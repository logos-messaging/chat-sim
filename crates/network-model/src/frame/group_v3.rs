//! The frames on a GroupV3 group's address, read as far as telling them
//! apart takes.
//!
//! libchat keeps GroupV3's wire to itself, so the messages here mirror the
//! fields of it that are read, by their numbers, and a field that holds a
//! message of its own is kept as its bytes. An envelope on a group's address
//! carries an `EncryptedSdsMessage`: the SDS header of a message and its
//! content, a `GroupV3Packet`, which an SDS sync has none of. A beacon or a
//! sealed frame is sent outside SDS: its header is empty and names no
//! sender, so a member's own comes back to it as any other frame does.

use prost::{Message, Oneof};

use super::{AppId, Frame, Kind};

#[derive(Clone, PartialEq, Message)]
pub(super) struct EncryptedSdsMessage {
    #[prost(bytes = "vec", tag = "1")]
    pub encrypted_sds_header: Vec<u8>,
    #[prost(bytes = "vec", tag = "2")]
    pub payload: Vec<u8>,
}

/// The fields of the SDS header that name a message. `sender_id` is the hex
/// of the sender's signer key.
#[derive(Clone, PartialEq, Message)]
pub(super) struct SdsHeader {
    #[prost(string, tag = "1")]
    pub sender_id: String,
    #[prost(string, tag = "2")]
    pub message_id: String,
    #[prost(string, tag = "3")]
    pub channel_id: String,
}

#[derive(Clone, PartialEq, Message)]
pub(super) struct GroupV3Packet {
    #[prost(bytes = "vec", tag = "1")]
    pub encrypted_header: Vec<u8>,
    #[prost(oneof = "EncodedFrame", tags = "2, 3")]
    pub encoded_frame: Option<EncodedFrame>,
}

#[derive(Clone, PartialEq, Oneof)]
pub(super) enum EncodedFrame {
    #[prost(bytes = "vec", tag = "2")]
    MlsMessage(Vec<u8>),
    #[prost(bytes = "vec", tag = "3")]
    External(Vec<u8>),
}

#[derive(Clone, PartialEq, Message)]
pub(super) struct GroupV3Header {
    #[prost(bytes = "vec", tag = "1")]
    pub epoch_data: Vec<u8>,
}

#[derive(Clone, PartialEq, Message)]
pub(super) struct GroupV3External {
    #[prost(oneof = "ExternalKind", tags = "1, 2")]
    pub kind: Option<ExternalKind>,
}

#[derive(Clone, PartialEq, Oneof)]
pub(super) enum ExternalKind {
    #[prost(bytes = "vec", tag = "1")]
    Beacon(Vec<u8>),
    #[prost(bytes = "vec", tag = "2")]
    Sealed(Vec<u8>),
}

/// The frame of a GroupV3 group an envelope carries. Every one is a frame of
/// the group, and one sent through SDS names its sender.
pub(super) fn frame(payload: &[u8]) -> Option<Frame> {
    let message = EncryptedSdsMessage::decode(payload).ok()?;
    if message.encrypted_sds_header.is_empty() {
        let Some(EncodedFrame::External(external)) = encoded_frame(&message.payload) else {
            return None;
        };
        GroupV3External::decode(external.as_slice()).ok()?.kind?;
        return Some(Frame {
            kind: Kind::Group,
            sender: None,
        });
    }
    let header = SdsHeader::decode(message.encrypted_sds_header.as_slice()).ok()?;
    // SDS names the message and the channel of each, a sync among them.
    if header.message_id.is_empty() || header.channel_id.is_empty() {
        return None;
    }
    // A sync carries no packet, and SDS carries no external frame.
    if !message.payload.is_empty()
        && !matches!(
            encoded_frame(&message.payload),
            Some(EncodedFrame::MlsMessage(_))
        )
    {
        return None;
    }
    Some(Frame {
        kind: Kind::Group,
        sender: header.sender_id.parse::<AppId>().ok(),
    })
}

/// What a packet carries, when `packet` and its header decode.
fn encoded_frame(packet: &[u8]) -> Option<EncodedFrame> {
    let packet = GroupV3Packet::decode(packet).ok()?;
    GroupV3Header::decode(packet.encrypted_header.as_slice()).ok()?;
    packet.encoded_frame
}
