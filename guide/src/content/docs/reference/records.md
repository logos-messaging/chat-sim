---
title: Records
description: Each record logos-chat writes on the log target libchat::mls_trace, with its fields, at the revision chat-sim pins.
---

A record is one log line at `trace` on the target `libchat::mls_trace`: a name, then `key=value` words.
Every record but `run` is written in the span of the op that wrote it, a GroupV2 op (`groupv2.new`,
`groupv2.new_from_welcome`, `groupv2.send_content`, `groupv2.handle_frame`, `groupv2.wakeup`,
`groupv2.add_member` or `groupv2.remove_member`) or a GroupV3 one (`groupv3.new`,
`groupv3.new_from_welcome`, `groupv3.send_content`, `groupv3.handle_frame`, `groupv3.wakeup` or
`groupv3.add_member`), and takes `signer` and `convo` from it. The span's own `epoch` is the one the op
began at. This page is what logos-chat writes at the revision chat-sim pins in `Cargo.toml`.

```text
2026-01-01T00:00:00.500000Z TRACE groupv2.wakeup{user_id=saro signer=f95e254c convo=f190ef36ae epoch=0}: libchat::mls_trace: epoch epoch=1 state=d7dfaece05f67d08 members=3 signers=f95e254c:7361726f,671cc18a:72617961,c1f311f2:706178
```

| Record | Written when | Fields |
|---|---|---|
| `run` | a chat core is built for an installation | `signer` `participant` |
| `epoch` | the core comes to hold the conversation, by creating or joining it, and each time its epoch moves on | `signer` `convo` from the span, `epoch` `state` `members` `signers` |
| `left` | a commit removed this installation | `signer` `convo` from the span, `epoch` |
| `sent` | it published a post of its own | `signer` `convo` from the span, `frame` `epoch` |
| `received` | it read another member's post | `signer` `convo` from the span, `frame` `from` `epoch` |
| `unreadable` | a frame of the group reached it and could not be read: a post, a vote or any other encrypted frame | `signer` `convo` from the span, `frame` `epoch` `why` |

| Field | Holds |
|---|---|
| `signer` | the first 4 bytes of the installation's public signing key, in hex: what the report calls the installation |
| `participant` | the first 4 bytes of the account id, in hex; an id shorter than that is written whole, as `706178` is |
| `convo` | the conversation id, with a byte outside `[0-9A-Za-z_-]` written `%xx` |
| `epoch` | the MLS epoch: the one held, in `epoch`; the one the removing commit made, in `left`; the one the frame was sent at, as its header names it, in `sent`, `received` and `unreadable` |
| `state` | 8 bytes of BLAKE2b over a fixed prefix and the epoch authenticator, in hex |
| `members` | how many members `signers` holds |
| `signers` | the members as `signer:participant` pairs, comma-separated |
| `frame` | 32 bytes of BLAKE2b over a fixed prefix and the frame's bytes, in hex: the same at the sender and every reader |
| `from` | the sender's `signer` |
| `why` | `epoch` (of another epoch than the one held), `decrypt` (of the epoch held, and failed to decrypt), or `sender` (decrypted, and the sender's account could not be checked) |

`sent` and `received` are for posts alone. A frame that is no MLS message of the group, such as a
welcome, writes nothing, and nor does an installation's own frame coming back to it. A post that reaches
an installation a second time is `unreadable` (`decrypt`) then: the key that opened it is spent.

`run`, `epoch` and `left` decide the verdict; the others draw the page's posts and timeline. In the
logos-chat tree the records are written in `core/conversations/src/conversation/mls_trace/record.rs`, from
`core.rs` (`run`), `conversation/group_v2/trace.rs` and `conversation/group_v3/trace.rs` (the rest).

`forks` reads two things this revision does not write: `commit` and `by` on an `epoch` record, the commit
applied to reach the epoch and the installation that committed it, and a `delivery` record on the target
`chat_module::report`, the state of an app's delivery service.
