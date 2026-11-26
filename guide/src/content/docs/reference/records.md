---
title: Records and files
description: The files a run leaves, what the site keeps of them and for how long, and each record logos-chat writes into a run's log at the revision chat-sim pins.
---

## What a run leaves

`tools/sim run` and each run of a sweep write a directory of their own:

| File | Holds |
|---|---|
| `run.txt` | the run's outcome, as tables to read |
| `run.json` | the same and more, for scripts |
| `group.log` | every member's trace records, stamped with the virtual clock |
| `plan.json` | the plan the runner was given, written from the scenario |
| `result.json` | what the runner measured: each member, batch and post, and of a grow its lineage and rounds |
| `runner.out`, `runner.stderr` | what the runner printed |
| `report.txt`, `report.json`, `expect.json` | for a run that is not a grow: what `tools/forks` reports of `group.log`, and the expectation it was checked against |

A sweep also writes `sweep.txt` and `sweep.json`, with one row per run.
[`tools/sim/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/tools/sim/README.md#what-a-run-in-one-process-writes)
gives every field of `run.json`, and
[`crates/harness/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/crates/harness/README.md#what-a-run-leaves)
those of `result.json`.

## What the site keeps

chat-sim's site keeps a run's text and no log. A run's text is its `run.txt` and `run.json`, with
`report.txt` when it has one, or `build.log` when chat-sim did not build.

| Runs of | The site keeps their text |
|---|---|
| a pull request | while the pull request is open |
| a point of `main` | while the point is among the newest eleven |
| a long run | while it is among the newest fifteen |

A run of a point has its page as long as its text. An older point keeps its `point.json`, and
its page is drawn from that alone: each rate has the commands that run a seed again, and a run
whose lines the point holds stays a tree.

A run's log, `group.log`, is an artifact of its workflow run, which GitHub keeps 90 days
([Into the log](/chat-sim/guide/investigate/moved-run/#into-the-log)).

Beside the runs' text, the site keeps these files for scripts:

| File | Holds |
|---|---|
| `logos-chat/pr-N/matrix.json` | the matrix of a pull request's newest run, as `tools/sim matrix` writes it |
| `logos-chat/pr-N/meta.json` | what ran: the pull request, the head and the base, the chat-sim commit that ran and the measurement, the workflow run, and when |
| `logos-chat/pr-N/scenarios/` | a copy of each scenario file that ran |
| `logos-chat/main/<sha>/<measurement>/point.json` | a point of main: what ran, the pull requests merged since the point before it, each run's outcome, each rate's summary, and each run's lines |
| `logos-chat/long/<sha>/<measurement>/long.json` | a long run: what ran, and for each scenario what `compare` said and each run's outcome |

In `point.json`, `lines` maps a run's directory, `<scenario>/<run>/`, to the lines of its tree,
in the run's order. A line has the line it left as `from`, the epoch it left at as `forked_at`,
its tip's `epoch`, and how many `members` it ended with. The trunk left no line, so its `from` and
`forked_at` are `null`.

A run whose text holds no lines, as one that did not build, has no entry. A point has no `lines`
when the site dropped its runs' text before points held them.

## The records

A record is one line of `group.log`, at `trace` on the target `libchat::mls_trace`: a name, then
`key=value` words. This is what logos-chat writes at the revision chat-sim pins in `Cargo.toml`.

```text
2026-01-01T00:00:00.500000Z TRACE groupv2.wakeup{user_id=saro signer=f95e254c convo=f190ef36ae epoch=0}: libchat::mls_trace: epoch epoch=1 state=d7dfaece05f67d08 members=3 signers=f95e254c:7361726f,671cc18a:72617961,c1f311f2:706178
```

Every record but `run` is written in the span of the group's op that wrote it, and takes `signer`
and `convo` from the span. The span's `user_id` is the member's name. The span's own `epoch` is
the one the op began at, not the record's: in the line above, a `wakeup` that began at epoch 0
moved the group to epoch 1.

The ops are `new`, `new_from_welcome`, `send_content`, `handle_frame`, `wakeup` and `add_member`,
each as `groupv2.` and as `groupv3.`, and `groupv2.remove_member`.

| Record | Written when | Fields |
|---|---|---|
| `run` | logos-chat's core is built for an installation | `signer` `participant` |
| `epoch` | the installation first holds the conversation, by creating or joining it, and each time its epoch moves on | `epoch` `state` `members` `signers` |
| `left` | a commit removed this installation | `epoch` |
| `sent` | it published a post of its own | `frame` `epoch` |
| `received` | it read another member's post | `frame` `from` `epoch` |
| `unreadable` | a frame of the group reached it and could not be read: a post, a vote or any other encrypted frame | `frame` `epoch` `why` |

| Field | Holds |
|---|---|
| `signer` | the first 4 bytes of the installation's public signing key, in hex: what the report calls the installation |
| `participant` | the first 4 bytes of the account id, in hex; an id shorter than that is written whole, as `706178` is |
| `convo` | the conversation id, with a byte outside `[0-9A-Za-z_-]` written `%xx` |
| `epoch` | the MLS epoch. In an `epoch` record it is the one held. In `left` it is the one the removing commit made. In `sent`, `received` and `unreadable` it is the one the frame was sent at, as its header names it |
| `state` | 8 bytes of BLAKE2b over a fixed prefix and the epoch authenticator, in hex |
| `members` | how many members `signers` holds |
| `signers` | the members as `signer:participant` pairs, comma-separated |
| `frame` | 32 bytes of BLAKE2b over a fixed prefix and the frame's bytes, in hex: the same at the sender and every reader |
| `from` | the sender's `signer` |
| `why` | `epoch` (of another epoch than the one held), `decrypt` (of the epoch held, and failed to decrypt), or `sender` (decrypted, and the sender's account could not be checked) |

`run`, `epoch` and `left` decide the verdict `tools/forks` gives. The others say what became of
each post:

- `sent` and `received` are for posts alone.
- A frame that is no MLS message of the group, such as a welcome, writes nothing. Nor does an
  installation's own frame coming back to it.
- A post that reaches an installation a second time is `unreadable` (`decrypt`) then: the key that
  opened it is spent.

In the logos-chat tree the records are written in
`core/conversations/src/conversation/mls_trace/record.rs`, from `core.rs` (`run`),
`conversation/group_v2/trace.rs` and `conversation/group_v3/trace.rs` (the rest).

`tools/forks` reads two things this revision does not write: `commit` and `by` on an `epoch`
record, and a `delivery` record
([`tools/forks/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/tools/forks/README.md#the-lines-it-reads)).
