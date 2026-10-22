//! What a payload is, read as its receiver first reads it.

use std::error::Error;
use std::fmt;
use std::str::FromStr;

use chat_proto::logoschat::convos::group_v2::{GroupV2Frame, group_v2_frame};
use chat_proto::logoschat::encryption::{EncryptedPayload, encrypted_payload};
use chat_proto::logoschat::envelope::EnvelopeV1;
use chat_proto::logoschat::inbox::{InboxV2Frame, inbox_v2_frame};
use de_mls::protos::de_mls::messages::v1::{AppMessage, app_message};
use prost::Message;

/// The app id a conversation stamps on the frames it publishes: one byte or
/// more, written as their lowercase hex.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct AppId(Vec<u8>);

/// What is no app id: no bytes, or text that is no hex of whole bytes.
#[derive(Debug, PartialEq, Eq)]
pub struct NotAnAppId;

impl fmt::Display for NotAnAppId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("an app id is the hex of one byte or more")
    }
}

impl Error for NotAnAppId {}

impl TryFrom<Vec<u8>> for AppId {
    type Error = NotAnAppId;

    fn try_from(bytes: Vec<u8>) -> Result<Self, NotAnAppId> {
        if bytes.is_empty() {
            return Err(NotAnAppId);
        }
        Ok(Self(bytes))
    }
}

impl FromStr for AppId {
    type Err = NotAnAppId;

    fn from_str(hex: &str) -> Result<Self, NotAnAppId> {
        let digit = |c: &u8| char::from(*c).to_digit(16).ok_or(NotAnAppId);
        let (pairs, odd) = hex.as_bytes().as_chunks::<2>();
        if !odd.is_empty() {
            return Err(NotAnAppId);
        }
        let bytes = pairs
            .iter()
            .map(|[high, low]| Ok(((digit(high)? << 4) | digit(low)?) as u8))
            .collect::<Result<Vec<u8>, NotAnAppId>>()?;
        Self::try_from(bytes)
    }
}

impl fmt::Display for AppId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.iter().try_for_each(|byte| write!(f, "{byte:02x}"))
    }
}

/// What a frame carries.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// What seats a joiner: the invite to its inbox, or the copy a steward
    /// broadcasts to the group. A double ratchet payload has the bytes of
    /// such an invite, and is read as one.
    Welcome,
    /// A steward's commit candidate. `seat` is the leaf of the steward that
    /// minted it, when the candidate names it in four bytes.
    Candidate { seat: Option<u32> },
    /// Any other frame of a group.
    Group,
    /// Anything else, bytes that are no envelope among them.
    Other,
}

/// A payload as [`read`] takes it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    pub kind: Kind,
    /// The app id of the conversation that published it. A frame that is
    /// none of a group has no sender, and neither has one stamped with no
    /// app id.
    pub sender: Option<AppId>,
}

/// What a payload is, read as its receiver first reads it. Never fails.
pub fn read(payload: &[u8]) -> Frame {
    let Ok(envelope) = EnvelopeV1::decode(payload) else {
        return OTHER;
    };
    // The group form first: its bytes decode as an inbox frame too.
    group_frame(&envelope.payload)
        .or_else(|| invite(&envelope.payload))
        .unwrap_or(OTHER)
}

const OTHER: Frame = Frame {
    kind: Kind::Other,
    sender: None,
};

/// The frame of a group an envelope carries.
fn group_frame(payload: &[u8]) -> Option<Frame> {
    let encrypted = EncryptedPayload::decode(payload).ok()?;
    let encrypted_payload::Encryption::Plaintext(plaintext) = encrypted.encryption? else {
        return None;
    };
    let frame = GroupV2Frame::decode(plaintext.payload.as_ref()).ok()?;
    let group_v2_frame::Payload::DeMlsWrapper(inner) = frame.payload? else {
        return None;
    };
    // A candidate and a welcome travel in the clear, and every other frame of
    // a group is a message MLS encrypted.
    let carried = AppMessage::decode(inner.as_ref()).ok();
    let kind = match carried.and_then(|message| message.payload) {
        Some(app_message::Payload::CommitCandidate(candidate)) => Kind::Candidate {
            seat: seat(&candidate.steward_member_id),
        },
        Some(app_message::Payload::MemberWelcome(_)) => Kind::Welcome,
        _ => Kind::Group,
    };
    Some(Frame {
        kind,
        sender: AppId::try_from(frame.sender_app_id.to_vec()).ok(),
    })
}

/// A member's leaf, which the group library writes as four bytes, the most
/// significant first.
fn seat(member_id: &[u8]) -> Option<u32> {
    member_id.try_into().ok().map(u32::from_be_bytes)
}

/// The invite to a group an envelope carries to an inbox.
fn invite(payload: &[u8]) -> Option<Frame> {
    let inbox_v2_frame::Payload::GroupV2(_) = InboxV2Frame::decode(payload).ok()?.payload? else {
        return None;
    };
    Some(Frame {
        kind: Kind::Welcome,
        sender: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use chat_proto::logoschat::encryption::{Doubleratchet, InboxHandshakeV1, Plaintext};
    use de_mls::protos::de_mls::messages::v1::{
        CommitCandidate, ConversationSyncRequest, MemberWelcome,
    };

    const SARO: &[u8] = b"5a904c11e7";

    fn app_id(bytes: &[u8]) -> Option<AppId> {
        Some(AppId::try_from(bytes.to_vec()).unwrap())
    }

    fn envelope(payload: Vec<u8>) -> Vec<u8> {
        let envelope = EnvelopeV1 {
            conversation_hint: "weekend".into(),
            salt: 0,
            payload: payload.into(),
        };
        envelope.encode_to_vec()
    }

    /// `frame` as a group publishes it.
    fn published(frame: GroupV2Frame) -> Vec<u8> {
        let plaintext = Plaintext {
            payload: frame.encode_to_vec().into(),
        };
        let encrypted = EncryptedPayload {
            encryption: Some(encrypted_payload::Encryption::Plaintext(plaintext)),
        };
        envelope(encrypted.encode_to_vec())
    }

    /// The frame of a group that `sender` stamped and that wraps `inner`.
    fn group(sender: &[u8], inner: Vec<u8>) -> Vec<u8> {
        published(GroupV2Frame {
            payload: Some(group_v2_frame::Payload::DeMlsWrapper(inner)),
            sender_app_id: sender.to_vec().into(),
        })
    }

    fn in_the_clear(payload: app_message::Payload) -> Vec<u8> {
        let message = AppMessage {
            payload: Some(payload),
        };
        message.encode_to_vec()
    }

    fn candidate(minter: &[u8]) -> Vec<u8> {
        in_the_clear(app_message::Payload::CommitCandidate(CommitCandidate {
            commit_message: vec![7; 40],
            steward_member_id: minter.to_vec(),
            ..Default::default()
        }))
    }

    fn welcome() -> MemberWelcome {
        MemberWelcome {
            welcome_bytes: vec![9; 40],
            ..Default::default()
        }
    }

    /// The head of an MLS message: version 1, a private message.
    fn encrypted_by_mls() -> Vec<u8> {
        vec![0, 1, 0, 2, 5, 5, 5, 5]
    }

    #[test]
    fn a_commit_candidate_names_its_sender_and_the_seat_of_its_minter() {
        let frame = read(&group(SARO, candidate(&[0, 0, 1, 2])));

        assert_eq!(frame.kind, Kind::Candidate { seat: Some(258) });
        assert_eq!(frame.sender, app_id(SARO));
    }

    #[test]
    fn a_candidate_whose_minter_id_is_not_four_bytes_has_no_seat() {
        for minter in [&[][..], &[1, 2, 3], &[0, 0, 0, 0, 1]] {
            let frame = read(&group(SARO, candidate(minter)));

            assert_eq!(frame.kind, Kind::Candidate { seat: None }, "{minter:?}");
            assert_eq!(frame.sender, app_id(SARO));
        }
    }

    #[test]
    fn a_welcome_a_steward_broadcasts_has_its_sender() {
        let broadcast = in_the_clear(app_message::Payload::MemberWelcome(welcome()));

        let frame = read(&group(SARO, broadcast));

        assert_eq!(frame.kind, Kind::Welcome);
        assert_eq!(frame.sender, app_id(SARO));
    }

    #[test]
    fn a_welcome_sent_to_an_inbox_has_no_sender() {
        let invite = InboxV2Frame {
            payload: Some(inbox_v2_frame::Payload::GroupV2(
                welcome().encode_to_vec().into(),
            )),
        };

        let frame = read(&envelope(invite.encode_to_vec()));

        assert_eq!(frame.kind, Kind::Welcome);
        assert_eq!(frame.sender, None);
    }

    #[test]
    fn any_other_frame_of_a_group_is_a_group_frame() {
        let request = app_message::Payload::ConversationSyncRequest(ConversationSyncRequest {});
        for inner in [encrypted_by_mls(), in_the_clear(request), vec![]] {
            let frame = read(&group(SARO, inner.clone()));

            assert_eq!(frame.kind, Kind::Group, "{inner:?}");
            assert_eq!(frame.sender, app_id(SARO));
        }
    }

    #[test]
    fn a_frame_stamped_with_no_app_id_has_no_sender() {
        let frame = read(&group(b"", candidate(&[0, 0, 0, 1])));

        assert_eq!(frame.kind, Kind::Candidate { seat: Some(1) });
        assert_eq!(frame.sender, None);
    }

    #[test]
    fn a_group_frame_is_not_taken_for_an_invite() {
        let payload = group(SARO, encrypted_by_mls());
        let carried = EnvelopeV1::decode(payload.as_slice()).unwrap().payload;
        let as_invite = InboxV2Frame::decode(carried.as_ref()).unwrap();
        assert!(as_invite.payload.is_some(), "it decodes as an inbox frame");

        assert_eq!(read(&payload).kind, Kind::Group);
    }

    #[test]
    fn a_double_ratchet_payload_is_taken_for_an_invite() {
        // Its field has the number and the wire type of an invite's.
        let ratchet = EncryptedPayload {
            encryption: Some(encrypted_payload::Encryption::Doubleratchet(
                Doubleratchet {
                    ciphertext: vec![9; 40].into(),
                    ..Default::default()
                },
            )),
        };

        let frame = read(&envelope(ratchet.encode_to_vec()));

        assert_eq!(frame.kind, Kind::Welcome);
        assert_eq!(frame.sender, None);
    }

    #[test]
    fn anything_else_is_other() {
        // The invite to a direct chat, whose bytes are those of an envelope
        // with plaintext that is no frame of a group.
        let direct_invite = Plaintext {
            payload: encrypted_by_mls().into(),
        };
        let direct_invite = EncryptedPayload {
            encryption: Some(encrypted_payload::Encryption::Plaintext(direct_invite)),
        };
        let direct_invite = direct_invite.encode_to_vec();
        assert!(matches!(
            InboxV2Frame::decode(direct_invite.as_slice())
                .unwrap()
                .payload,
            Some(inbox_v2_frame::Payload::GroupV1(_))
        ));
        let bare_commit = published(GroupV2Frame {
            payload: Some(group_v2_frame::Payload::MlsCommitMessage(encrypted_by_mls())),
            sender_app_id: SARO.to_vec().into(),
        });
        // A handshake, in a field that neither a frame of a group nor an
        // invite has.
        let handshake = EncryptedPayload {
            encryption: Some(encrypted_payload::Encryption::InboxHandshake(
                InboxHandshakeV1::default(),
            )),
        };

        for payload in [
            vec![],
            vec![0xff; 16],
            b"no envelope".to_vec(),
            envelope(vec![]),
            envelope(vec![0xff; 16]),
            envelope(direct_invite),
            bare_commit,
            envelope(handshake.encode_to_vec()),
        ] {
            assert_eq!(read(&payload), OTHER, "{payload:?}");
        }
    }

    #[test]
    fn an_app_id_is_written_as_the_lowercase_hex_of_its_bytes() {
        let id = AppId::try_from(vec![0x00, 0x0a, 0xff]).unwrap();

        assert_eq!(id.to_string(), "000aff");
        assert_eq!("000aff".parse(), Ok(id.clone()));
        assert_eq!("000AFF".parse(), Ok(id));
    }

    #[test]
    fn what_is_no_app_id_is_refused() {
        assert_eq!(AppId::try_from(vec![]), Err(NotAnAppId));
        for text in ["", "a", "abc", "zz", "0x12", "+f", "12 ", "é"] {
            assert_eq!(text.parse::<AppId>(), Err(NotAnAppId), "{text:?}");
        }
    }
}
