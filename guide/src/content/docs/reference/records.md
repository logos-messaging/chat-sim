---
title: Records
description: Each record logos-chat writes on the log target libchat::groupv2trace, with its fields, at the revision chat-sim pins.
---

A record is one log line at `info` on the target `libchat::groupv2trace`: a name, then `key=value` words.
This page is what logos-chat writes at the revision chat-sim pins in `Cargo.toml`.

```text
2026-01-01T00:00:00.500000Z  INFO libchat::groupv2trace: epoch installation=02680b98 convo=6fbcb48dfa epoch=1 state=d773ea9e00babd94 members=3 roster=02680b98:7361726f,fd24df45:72617961,530d0b04:706178
```

| Record | Written when | Fields |
|---|---|---|
| `run` | a chat core is built for an installation | `installation` `account` |
| `epoch` | the core comes to hold the conversation, by creating or joining it, and each time its epoch moves on | `installation` `convo` `epoch` `state` `members` `roster` |
| `left` | a commit removed this installation | `installation` `convo` `epoch` |
| `sent` | it published a post of its own | `installation` `convo` `frame` `epoch` |
| `received` | it read another member's post | `installation` `convo` `frame` `from` `epoch` |
| `unreadable` | a frame of the group reached it and could not be read: a post, a vote or any other encrypted frame | `installation` `convo` `frame` `epoch` `why` |

| Field | Holds |
|---|---|
| `installation` | the first 4 bytes of the installation's public signing key, in hex |
| `account` | the first 4 bytes of the account id, in hex; an id shorter than that is written whole, as `706178` is |
| `convo` | the conversation id, with a byte outside `[0-9A-Za-z_-]` written `%xx` |
| `epoch` | the MLS epoch: the one held, in `epoch`; the one the removing commit made, in `left`; the one the frame was sent at, as its header names it, in `sent`, `received` and `unreadable` |
| `state` | 8 bytes of BLAKE2b over a fixed prefix and the epoch authenticator, in hex |
| `members` | how many members the roster holds |
| `roster` | the members as `installation:account` pairs, comma-separated |
| `frame` | 8 bytes of BLAKE2b over a fixed prefix and the frame's bytes, in hex: the same at the sender and every reader |
| `from` | the sender's installation |
| `why` | `epoch` (of another epoch than the one held), `decrypt` (of the epoch held, and failed to decrypt), or `sender` (decrypted, and the sender's account could not be checked) |

`sent` and `received` are for posts alone. A frame that is no MLS message of the group, such as a
welcome, writes nothing, and nor does an installation's own frame coming back to it. A post that reaches
an installation a second time is `unreadable` (`decrypt`) then: the key that opened it is spent.

`run`, `epoch` and `left` decide the verdict; the others draw the page's posts and timeline. In the
logos-chat tree the records are written in `core/conversations/src/groupv2trace.rs`, from `core.rs`
(`run`) and `conversation/group_v2.rs` (the rest).

`forks` reads two things this revision does not write: `commit` and `by` on an `epoch` record, the commit
applied to reach the epoch and the installation that committed it, and a `delivery` record on the target
`chat_module::report`, the state of an app's delivery service.
