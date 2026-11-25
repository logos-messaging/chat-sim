# forks

Do the members of a group still hold one group?

A conversation of GroupV2 or GroupV3 moves from epoch to epoch as its members apply commits. When
two members apply different commits at the same epoch the group forks: each side goes on, and
neither can read what the other sends. Nobody is told. Every installation writes a few trace records
into its log as it goes, and `forks` reads the logs the members share and says what they add up to:
one group or several, who is on which branch, who is behind, who was removed, and how much of the
group the logs cover.

The tool reads logs and nothing else: `report` reads the files it is given, and `check` holds what
they add up to against a file that says what was expected. It needs Python 3.9 or newer and no
package beyond the standard library.

## Getting a log with records in it

libchat writes its records at the `trace` level, so a log holds them only when its writer lets the
target `libchat::mls_trace` through at `trace`.

`harness-run` runs a whole group in one process, and its `group.log` holds a record for every
epoch each member reaches (see
[What a run leaves](../../crates/harness/README.md#what-a-run-leaves)):

```sh
python3 tools/forks report OUT_DIR/group.log
```

`chat-cli`, which runs a member as a process of its own on the
[`local-network`](https://github.com/logos-messaging/chat-sim/tree/local-network) branch, writes them
into the log `--log-file` names. `tools/sim` has the tool read the `group.log` of each
run that is not a grow.

logos-chat-module, the Logos module that wraps libchat, writes a log file of its own, in a line
shape of its own. The tool reads both shapes: see [The lines it reads](#the-lines-it-reads).

## Running it

From the repository root:

```sh
python3 tools/forks report PATH...
```

A `PATH` is a log file, or a directory, which stands for every `.log` file directly in it. Give
it the logs of as many members as you have. A log may also come through a pipe, as `/dev/stdin`.

```console
$ python3 tools/forks report logs/
conversation b05d4417e9
  split at epoch 3
  4 of 4 seats share a log

  branch A: epoch 4, state 5a850890461cab7f, 4 members
    first state at epoch 3, made by commit c41f9be27a6d03e5 by a3f91c07
    on it: 19f6b0e3, 7b1e44d9, a3f91c07
  branch B: epoch 3, state e849c2aa9ab1f74d, 4 members
    first state at epoch 3, made by commit 77b0a1c2d3e4f5a6 by 7b1e44d9
    on it: e09a3f52

  installation  account   name  epoch  position  branch
  19f6b0e3      d2184c8e  mira  4      tip       A
  7b1e44d9      91c0e3a7  raya  4      tip       A
  a3f91c07      5be2c0d1  saro  4      tip       A
  e09a3f52      0d7a66f3  pax   3      tip       B

lines: 69 read, 42 records, 0 without a stamp, 0 malformed, 0 unknown
```

Here `logs/` holds `saro.log`, `raya.log`, `pax.log` and `mira.log`. Saro, Raya and Mira went on
to epoch 4 together. Pax applied a different commit at epoch 3 and is alone on the other branch.
The three applied one that Saro committed, and Pax one that Raya committed.

Each conversation in the logs gets its own block: the verdict, the coverage, every branch with its
newest epoch, the fingerprint of the state there, how many members that state holds and which
installations are on it, and then every installation with its account, the run it wrote in, the
newest epoch it reported and its position. The last line says what became of the lines that were
read.

Where a conversation has more than one branch, a branch whose first state names a commit says under
its own line the epoch of that state, the commit that made it and the installation that committed
it, as the two above do. The first state is the first the logs hold of the branch: where a split is
only seen from an epoch, it is not where the branch began. Where the installations that reported
that state name more than one commit, the line says each and how many of them name it:

```text
    first state at epoch 3, made by commits c41f9be27a6d03e5 by a3f91c07 (2 name it) and
      5e0d7a2b91c6f483 by a3f91c07 (1 names it)
```

`--json FILE` also writes the whole report as JSON, which holds more than the text does. See
[The JSON report](#the-json-report).

### Exit status

| Status | Meaning |
|---|---|
| 0 | Every conversation in the logs reads `whole`, and no line on a report target was malformed. |
| 1 | A conversation reads anything else, the logs hold no conversation, or a line on a report target was malformed. |
| 2 | The command line is wrong, a log cannot be read, or the report cannot be written. |

## The lines it reads

A log line is a stamp, a level, optional span prefixes, a target, a colon and a message. Two
writers produce the logs, and the tool reads both:

```text
2026-10-09T06:54:50.327025Z TRACE core.handle_frame{user_id=raya}:groupv2.handle_frame{user_id=raya signer=7b1e44d9 convo=2a30d6a2c1 epoch=7}: libchat::mls_trace: left epoch=8
2026-10-08T16:02:11.004+02:00 TRACE: groupv2.handle_frame{user_id=raya signer=7b1e44d9 convo=2a30d6a2c1 epoch=7}: libchat::mls_trace: left epoch=8
```

The first is the default formatter of the `tracing` crate, with or without colour codes: the stamp
is UTC, and the level is padded and has no colon. The second is the log file of
logos-chat-module: the stamp is local time, with or without its offset from UTC, and the level
ends in a colon. In both, the spans the event was written in come before the target, the
outermost first, each `name{fields}:` or `name:`.

A **record** is a line whose target is `libchat::mls_trace` or `chat_module::report`. Its message is
the record's name and then `key=value` words, each separated by one space. The target is read at
its place in the line and never searched for, because a conversation id is text its creator chose
and other lines print it.

A record of a conversation takes `signer` and `convo` from the span of the group's op that wrote
it, and where two spans name one, from the innermost. The span's `epoch` is the one the op began
at, and the tool does not read it: the record's own `epoch` is the one it is of.

| Target | Record | Fields | What it says |
|---|---|---|---|
| `libchat::mls_trace` | `run` | `signer` `participant` | An installation started, acting for an account. |
| `libchat::mls_trace` | `epoch` | `signer` `convo` from its span, `epoch` `state` `members` `signers`, and `commit` `by` together or neither | The installation holds the conversation at this epoch, in this state, with these members. `commit` and `by` say the commit it applied to reach the epoch, and the installation that committed it. |
| `libchat::mls_trace` | `left` | `signer` `convo` from its span, `epoch` | The commit that made this epoch removed the installation. |
| `libchat::mls_trace` | `sent` | `signer` `convo` from its span, `frame` `epoch` | The installation published a post of its own. |
| `libchat::mls_trace` | `received` | `signer` `convo` from its span, `frame` `from` `epoch` | The installation read a post another one sent. |
| `libchat::mls_trace` | `unreadable` | `signer` `convo` from its span, `frame` `epoch` `why` | A frame of the group arrived and could not be read: a post, a vote or any other encrypted frame. |
| `chat_module::report` | `delivery` | `state` | The delivery service of this run changed state. |

| Field | Written as |
|---|---|
| `signer`, `from`, `by` | The first four bytes of the key the installation signs with: 8 hex characters. The report calls it the installation's id. |
| `participant` | The first four bytes of the account id, in hex. An id shorter than four bytes is written whole, and an empty one as nothing. |
| `convo` | The conversation id as one word. A byte outside `[0-9A-Za-z_-]` is written `%xx`, and the tool keeps the id as written. An id holds at most 1597 bytes, so that an address that asks for a conversation by its id, each byte written `%xx` taking five, fits the 8000 bytes RFC 9110 asks every server to take. |
| `epoch`, `members` | A number, at most 9007199254740991, the largest a browser reads from JSON exactly. `members` is the length of `signers`. The `epoch` of an `unreadable` record is the one the frame's header names, which nothing has checked: any number below 2^64. |
| `state` | 16 hex characters: a fingerprint of the group state. Installations that hold the same state write the same fingerprint. |
| `signers` | The members, comma-separated, each `signer:participant`. |
| `commit` | 16 hex characters: the first eight bytes of the SHA-256 of a commit's bytes. |
| `frame` | The id of a frame: 64 hex characters, 32 bytes of a hash of the frame as it travels. The tool reads a shorter one and no longer one. |
| `why` | `epoch`, `decrypt` or `sender`. |
| `state` of `delivery` | `initialising`, `online`, `error` or `stopped`. |

`run`, `epoch` and `left` decide the verdict. The other four add detail, and logs without them
read the same.

An `epoch` record carries `commit` and `by` together or not at all. It carries neither where its
installation applied no commit to reach the epoch, as when it joined there from a welcome, and an
older libchat writes neither on any record. The two decide nothing: but for the commits it
lists, a report is the same with them and without. On any other record they are fields the tool
does not know.

No line stops the tool. The last line of a report counts what was read:

- **records**: the lines read as records.
- **without a stamp**: lines that do not start with a stamp, such as a backtrace or the output of
  a build. They are skipped.
- **malformed**: lines on a report target whose record lacks a field, repeats one, holds a
  value of the wrong shape or past its bound, gives a `members` that is not the length of its
  `signers`, or carries one of `commit` and `by` without the other. A record lacks `signer` or
  `convo` when no span names it, and repeats one when a span names it twice or the record writes
  one its span names. They are skipped.
- **unknown**: lines on a report target whose record name the tool does not know. They are
  skipped. A field the tool does not know is ignored, and the record is read.

A verdict drawn without a record that was written is not one to trust. So when a line was
malformed, the report says so after the conversations and exits 1, whatever the verdicts:

```text
2 malformed lines were skipped: every verdict above was drawn without them
```

An unknown record is only counted: a newer libchat may write a record this tool does not know.

A last line without a line break is not read and not counted. Its writer has not finished it, or
the copy was cut there.

### Runs

A **run** is one stream of log lines, and the tool names it after its file, without the
extension: `saro.log` is the run `saro`. When a writer moves its file aside as it grows, the
parts are one run: `NAME.001.log`, `NAME.002.log` and so on, in that order, and then `NAME.log`,
the file still being written. A part's number has exactly three digits, which is what
logos-chat-module takes when it reads the names of its own parts: `NAME.1000.log` and
`NAME.20261009.log` are runs of their own. Two runs with one name cannot be told apart, so the
tool refuses them, and it refuses two files that are the same part of one run, such as `saro.log`
and `saro.txt`. A byte of a file's name that is not UTF-8 is written `\xNN` in the run's name,
and a character the output's encoding cannot write is printed as an escape, such as `\xe9` or
`\u65e5`.

One run may hold many installations, as the log of a test that builds a whole group in one
process does. One installation may write in many runs, one for each time the application
started, and they are put in order by the stamp of the first record it wrote in each. Runs that
open at the same stamp are put in order by what their records say, the first record that differs
deciding, and never by their names. A stamp is kept as it was written, and one without an offset
is read as UTC when stamps are compared.

The same run may be among the logs twice, at two lengths: a copy taken while it grew, and the
file as it is now. For one installation, a run whose records, with their stamps, are the first
records of another run is that run at an earlier length, and only the longer is read. Where the
rule is told that either is final, the one that is read is final: it is one run, and its writer
is gone.

## How the verdict is decided

A **state** is an epoch and the fingerprint an installation reported at it.

A **link** is two `epoch` records one installation wrote one after the other, the second at a
later epoch, in one run, with no `left` and no `run` record of its own between them. An
installation moves from a state only to the state that follows it, so a link is evidence that the
second state came from the first. A `run` record means the application started again, and what it
held before is not evidence of what it holds now, so a new chain begins there.

A **split** is an epoch with two states.

A **leave** is a `left` record, or an `epoch` record whose roster does not list its writer.

An installation **stands at** its newest state: the state of its last `epoch` record, when no
leave follows it.

The **line** of a state is the state itself, the states that links lead to it from, and the
states that links lead on to from it. Two states are **set apart** when their lines hold
different states at one epoch: the logs show a split between them, or after one of them.

A **newest roster** is the roster of a state that nothing was seen to follow, no link leading on
from it, unless the state was **passed**. A state that some reported state is set apart from is
never passed: it is on a side of a split the logs show, and only links say what came after it.
Any other state is passed once an installation that reported it wrote a state at a higher epoch
afterwards, as it does in a later run or after a leave.

The **seats** of a conversation are the installations that wrote a record in it, the
installations its rosters list, and the installations a [line is kept](#positions) of in it. An
installation that wrote only a `run` record, is on no roster and has no line kept of it is not
part of the conversation.

### Positions

Every seat has one position. The checks run in this order, and the first that holds decides.

| Position | When |
|---|---|
| `removed` | The seat wrote no `epoch` and no `left` record, and a line is kept of it. The line says its last record was a leave, or there is a newest roster at or above the line's epoch and none of those lists the seat. |
| `ended` | It wrote no `epoch` and no `left` record, and a line is kept of it: the log of its final run was shared, and is gone. |
| `removed` | It wrote no `epoch` and no `left` record, no line is kept of it, there is a newest roster, and no newest roster lists it. |
| `never joined` | It wrote no `epoch` and no `left` record, no line is kept of it, and a shared log holds a record of its own, of any kind. Its log is here, and it never held the group. |
| `no data` | It wrote no `epoch` and no `left` record, no line is kept of it, and no shared log holds a record of its own. Nobody shared its log. |
| `removed` | Its last record is a leave. Or links lead on from its newest state, and no roster at the end of them lists it: it was removed by a commit it never received. |
| `ended` | Every run it wrote in is final: the writer is gone and the run will not grow. |
| `behind` | Links lead from its newest state to a later one. |
| `lineage unknown` | A later state exists, and no link says whether that state follows this one or stands on another branch. |
| `tip` | Nothing newer is known on its line of states. |

A leave does not hide the roster of the state before it: it is a newest roster by the same test
as any other. A state on a side of a split the logs show still counts wherever its one reporter
joined again, on the other side or back on its own: such a state is never passed.

A file does not say whether its writer is gone, so `report` and `check` mark no run final and
never say `ended`. The rule takes the final runs and the kept lines as inputs, and neither command
hands it any: the collector that does is on the
[`local-network`](https://github.com/logos-messaging/chat-sim/tree/local-network) branch.

A **kept line** is what a collector keeps of an installation in a conversation once the log of
its final run is gone: the installation, its account, the epoch of its last record there, and
whether that record was a leave. The line says that the installation was here and how it ended,
and nothing of what it held. A seat that goes by its line has that epoch and no state, is on no
branch, and brings the report no state, no roster and no link. It is `removed` when its last
record was a leave, or when there is a newest roster at or above the line's epoch and none of
those lists it, and `ended` otherwise: a roster older than the line cannot show the seat removed.
A line yields only to an `epoch` or a `left` record: an installation that wrote one in the
conversation, in a shared log, goes by its records there, and one whose shared logs hold only
other records of its own, such as its `run` record or the records of another conversation, goes
by its line.

An installation is **live** when it is at the `tip`, `behind`, or of `lineage unknown`.

### Verdicts

Every conversation has one verdict. As with positions, the checks run in this order, and the
first that holds decides.

| Verdict | When |
|---|---|
| `split at epoch N` | Epoch N is the first with two states, and links lead to every one of them from one state at epoch N-1. |
| `split seen from epoch N` | Epoch N is the first with two states, and the logs do not show one state at epoch N-1 that they all came from. The split happened at epoch N or earlier. |
| `no live member` | No installation is live. |
| `not enough data` | No state was reported by two installations. One log alone cannot show a split. |
| `whole as far as seen` | A later epoch holds a state that no link leads to from the newest state of a live installation, whatever its position. |
| `whole` | Every live installation is at the tip. |
| `whole, members behind` | A live installation is behind. |

A split verdict says how many sides of the split hold a live installation. The sides are counted
among the states live installations stand at, at or above the first split epoch: each of them
that no other of them follows by links is a side, and two of those from which links lead on to a
common state are one line and count once. So a live installation behind another on one linked
line is on that one's side, two live installations behind one later state that nobody live stands
at are on one side, and two live states that no link ties to one line count as two, since
`one side live` is said only where the logs show it. The verdict ends in `, no side live` when
there is no such side and in `, one side live` when there is one, and has no tail when there are
two or more.

Only `whole` is a clean bill. `whole as far as seen` and `not enough data` say that the logs do
not prove a split, and do not prove its absence either.

### Branches

Without a split, the conversation has one branch, and every state is on it.

With a split, the states at and above the first split epoch are grouped along their links, and
each group is a branch. The states below the first split are on no branch, and neither is a state
from which links lead on to states that are not one line: sorted by epoch, each must be reached
by links from the one before. So two states linked from one state, neither following the other,
are two branches, as the verdict counts two live ones as two sides.

Branches are named `A`, `B`, `C` and on: the branch more installations are on comes first, and of
two with as many, the one whose first state has the lower fingerprint. A name says nothing about
which branch is the right one. An installation is on the branch its newest state is on, and on
none when that state is on none.

### Coverage

`4 of 4 seats share a log` counts the seats that are neither `removed` nor `ended`, and among
them those whose position is not `no data`. The fewer seats share a log, the less a `whole` is
worth.

## The JSON report

`--json FILE` writes one object. The same logs give the same bytes, in whatever order the paths
are given. `row`, `folds`, `folded` and `shown_messages` lay out a page drawn from the report, as
the collector on the
[`local-network`](https://github.com/logos-messaging/chat-sim/tree/local-network) branch draws one.

```json
{
  "lines": {"lines": 69, "records": 42, "unstamped": 0, "malformed": 0, "unknown": 0},
  "conversations": [
    {
      "id": "b05d4417e9",
      "verdict": "split at epoch 3",
      "split_epoch": 3,
      "coverage": {"sharing": 4, "seats": 4},
      "positions": [
        {
          "position": "tip",
          "count": 4,
          "installations": ["19f6b0e3", "7b1e44d9", "a3f91c07", "e09a3f52"]
        }
      ],
      "epochs": [1, 2, 3, 4],
      "branches": [
        {
          "label": "A",
          "since": 3,
          "epoch": 4,
          "state": "5a850890461cab7f",
          "members": 4,
          "installations": ["19f6b0e3", "7b1e44d9", "a3f91c07"],
          "first_commits": [{"commit": "c41f9be27a6d03e5", "by": "a3f91c07", "holders": 3}]
        }
      ],
      "states": [
        {
          "epoch": 3,
          "state": "e849c2aa9ab1f74d",
          "branch": "B",
          "lineage": "branch",
          "toward": null,
          "holders": 1,
          "members": 4,
          "roster": ["19f6b0e3", "7b1e44d9", "a3f91c07", "e09a3f52"],
          "held_by": ["e09a3f52"],
          "after": [{"epoch": 2, "state": "763449d6cdefb10f"}],
          "row": 1,
          "commits": [{"commit": "77b0a1c2d3e4f5a6", "by": "7b1e44d9", "holders": 1}]
        }
      ],
      "lanes": [
        {
          "under": "branch",
          "branch": "B",
          "positions": ["tip"],
          "installations": ["e09a3f52"]
        }
      ],
      "folds": [
        {
          "under": "branch",
          "branch": "B",
          "position": "tip",
          "installations": ["e09a3f52"],
          "kept": 0,
          "logs": 1,
          "epoch": {"low": 3, "high": 3},
          "behind_by": null,
          "listed_from": null,
          "cells": [
            {
              "epoch": 1,
              "wrote": 1,
              "states": [
                {
                  "state": "77797f82b14f233d",
                  "count": 1,
                  "first": 1791466392.08,
                  "last": 1791466392.08
                }
              ],
              "left": 0,
              "several": 0,
              "unreached": 0,
              "listed": 0
            }
          ]
        }
      ],
      "folded": false,
      "installations": [
        {
          "id": "e09a3f52",
          "account": "0d7a66f3",
          "runs": ["pax"],
          "kept": false,
          "position": "tip",
          "branch": "B",
          "epoch": 3,
          "state": "e849c2aa9ab1f74d",
          "listed": [],
          "unreached": [],
          "behind_by": null,
          "history": [
            {
              "kind": "epoch",
              "epoch": 1,
              "state": "77797f82b14f233d",
              "at": {
                "run": "pax",
                "line": 7,
                "time": "2026-10-08T15:33:12.080+02:00",
                "has_offset": true,
                "seconds": 1791466392.08
              }
            }
          ],
          "cells": [{"epoch": 1, "kind": "wrote", "steps": [0]}],
          "spans": [{"start": 1791466339.014, "end": 1791466339.017, "online": true}]
        }
      ],
      "timeline": {"start": 1791466306.08, "end": 1791466732.412},
      "messages": [
        {
          "frame": "e3b94d10",
          "sender": "7b1e44d9",
          "epoch": 3,
          "state": "897d8438305ac6d2",
          "at": {
            "run": "raya",
            "line": 12,
            "time": "2026-10-08T14:37:00.601+01:00",
            "has_offset": true,
            "seconds": 1791466620.601
          },
          "utc": "2026-10-08T13:37:00Z",
          "cells": {
            "19f6b0e3": "read",
            "7b1e44d9": "sent",
            "a3f91c07": "read",
            "e09a3f52": "unreadable"
          },
          "why": {"e09a3f52": "decrypt"},
          "marks": {
            "e09a3f52": {
              "run": "pax",
              "line": 16,
              "time": "2026-10-08T15:37:01.201+02:00",
              "has_offset": true,
              "seconds": 1791466621.201
            }
          },
          "folds": [{"cells": {"unreadable": 1}, "why": {"decrypt": 1}}]
        }
      ],
      "shown_messages": 3,
      "unmatched_unreadable": 1,
      "runs": [
        {
          "name": "pax",
          "final": false,
          "delivery": [
            {
              "state": "online",
              "at": {
                "run": "pax",
                "line": 5,
                "time": "2026-10-08T15:32:20.077+02:00",
                "has_offset": true,
                "seconds": 1791466340.077
              }
            }
          ]
        }
      ]
    }
  ]
}
```

The lists above, and `marks`, are cut to one entry each.

- `lines` is the count the text report ends with.
- `conversations` are in the order of their ids.
- `split_epoch` is the first epoch with two states, or `null`.
- `positions` are the positions an installation is in, in the order `tip`, `behind`,
  `lineage unknown`, `ended`, `removed`, `never joined`, `no data`, each with its installations
  and their `count`.
- `epochs` are the epochs a state or a leave names, in order.
- `branches` are in the order of their names. `since` is the epoch of a branch's first state,
  and `epoch`, `state` and `members` describe its newest. `first_commits` are the `commits` of
  its first state. Where a branch holds more than one state at `since`, as when two there lead
  on to one state, they are those of each, and an installation counts once for a commit and who
  committed it.
- `states` are every state reported, by epoch and then fingerprint. `branch` is `null` for a
  state on no branch, `held_by` lists the installations that reported it and `holders` counts
  them, and `after` lists the states that links lead to it from. `lineage` says how the state
  stands among the branches, by the newest state of each:

  | `lineage` | When |
  |---|---|
  | `branch` | There is a split, and the state is on a branch. |
  | `side` | There is a split, the state is on no branch, and links lead on from it to the newest state of one branch alone, which `toward` names. |
  | `shared` | There is a split, the state is on no branch, and links lead on from it to the newest states of two or more branches. Or there is no split, and it is the newest state or links lead on from it to the newest. |
  | `unknown` | Links lead on from it to the newest state of no branch. |

  `row` is the row of the lineage a page draws the state in, counted from the top. A row is a
  line of states. Each branch has one, in the order of `branches`: its newest state and the
  states links lead to it from, each by the latest it came from, back to a state an earlier row
  holds, which the row leaves. A state no such line reaches starts a row the same way, the
  newest first. A row stands under the one it leaves, nearer to it the later it leaves, so that
  a link to a row's first state crosses no row that leaves earlier.

  `commits` are the commits that the installations which reported the state named as the one
  they applied to reach it: each with the installation that committed it in `by`, and in
  `holders` how many of them named the two together. The most named comes first, and then they
  go by `commit` and by `by`. The list is empty where no record of the state names a commit, and
  holds more than one where its installations applied it under two hashes or their logs
  disagree. An installation counts once for a commit and who committed it, however often it
  reported the state.

- `lanes` are the installations under the headings they are read under, each heading with the
  `positions` of its installations, each once. First come the branches of a split, by name:
  `under` is `branch`, and `branch` names it. Then comes `one line` without a split, or
  `neither branch` with one, and last `not placed`. An installation goes by the last state it
  wrote, also when a leave follows it: under that state's branch when its `lineage` is `branch`,
  under `one line` or `neither branch` when it is `shared`, and under `not placed` when it is
  `side` or `unknown` or the installation wrote no state. Under a heading they come by position,
  in the order of `positions`, and then by id, and a heading with no installation is left out.
- `folds` are the `lanes` by position: under each heading a fold for each of its `positions`, in
  their order, with the installations of the heading that are in that position, as `lanes` lists
  them. `kept` counts those that go by a kept line, and `logs` those that wrote in a log that is
  held. `epoch` and `behind_by` reach from the lowest to the highest that its installations say,
  and `listed_from` does so for those without an epoch, by the first epoch of the `listed` of
  each: each of the three is `null` where none of them says one. `cells` are their lanes laid
  over one another, one for each epoch a lane of the fold holds something at. There `wrote`,
  `unreached` and `listed` count the lanes whose cell is of that kind, `states` says for each
  state one of them wrote at the epoch, by fingerprint, how many of them wrote it and the first
  and the last stamp of those records, in seconds since 1970 in UTC, and `left` counts those
  that wrote a leave. An installation counts once under each state it wrote at an epoch, however
  often it wrote it, and once under a leave. `several` counts those that are under more than one
  of them: where it is not 0, the counts of `states` and `left` come to more than `wrote`.
- `folded` says whether a page starts with a row for each fold in place of a row for each
  installation. It is `true` for more than 23 installations: 23 lanes of one run each are what
  the box of the installations shows at once in a window 1080 pixels high, and a lane has a
  line for each run of its installation.
- `installations` are every seat, by id. `account` comes from the installation's `run` record
  or, without one, from a roster, and is `""` for an account of no bytes. `epoch` is the epoch of
  its last record, and `state` and `branch` are `null` when that record is a leave or the seat
  has no record. `history` holds its records in the order written, each of kind `epoch` or
  `left`, and a leave has no state. A seat that goes by a kept line says so in `kept`: it has
  the line's `account` and `epoch`, no `state`, `branch` or history, and in `runs` only the runs
  that hold other records of its own. `listed` holds, for a seat that wrote no `epoch` and no
  `left` record, the epochs of the states whose roster lists it, and is empty for any other.
  `unreached` holds, for a seat that is `behind`, the epochs of the states that links lead on to
  from its newest, and `behind_by` says how many epochs the last of them is above its own: they
  are empty and `null` for any other. `cells` are its lane, one for each epoch the lane holds
  something at, in the order of the epochs. A cell of kind `wrote` holds in `steps` the places
  in `history` of the records the installation wrote at that epoch, in the order written,
  whatever else is said of the epoch. A cell of kind `unreached` is an epoch of `unreached`, and
  a cell of kind `listed` an epoch of `listed` for a seat nobody shared a log of: one that wrote
  in no log held and goes by no kept line. `steps` is empty in both. `spans` are the runs it
  wrote in as stretches of time, in seconds since 1970 in UTC: each run from the first to the
  last of the installation's records in it and the run's `delivery` records, cut where the
  delivery state turns to `online` or away from it, each stretch with whether it was `online`.
  A run is read as online until its first `delivery` record.
- `timeline` is the first and the last second among the stamps of the records that name the
  conversation. A run of `spans` reaches past it where the run wrote anything else before or
  after: its `run` record, a `delivery` record or a record of another conversation.
- `at` is where a record was written: the run, the line in it, and the stamp as written.
  `has_offset` says whether the stamp carries its offset from UTC, and `seconds` is the stamp as
  seconds since 1970 in UTC, a stamp without an offset read as UTC.
- `messages` has an entry for each frame a `sent` record names, in the order sent, whether or not
  anyone read it. `utc` is the stamp of its `sent` record in UTC, cut to its second and never rounded. `state` is
  the state its sender held at that epoch, or `null` when the sender wrote no `epoch` record for
  it. `cells` says what became of the post on each seat, by the first row of the table below
  that holds for the seat, and `why` gives the cause for each `unreadable`. `marks` says where
  the record a cell stands for was written, as `at` says it of the post, for each seat whose
  cell is `sent`, `read` or `unreadable`, and holds no other seat: the sender's is `at`. `folds`
  counts the cells of each fold of the conversation, in the order of its `folds`: `cells` how
  many of the fold's installations each thing became of the post on, in the order of the table
  below and without what became of it on none, and `why` how many of the unreadable gave each
  cause, by the name of the cause.

  | Cell | When |
  |---|---|
  | `sent` | The seat sent it. |
  | `read` | It wrote a `received` record for the frame. |
  | `unreadable` | It wrote an `unreadable` record for the frame. |
  | `not a member` | The roster of the state the post was sent in does not list it. |
  | `no log` | No shared log holds a record of it. |
  | `no record` | Its log is shared and holds no record of the frame. |

  A seat that wrote a `received` and an `unreadable` record for one frame read it. Of two
  records of one kind that it wrote for one frame, the one it wrote first counts, its runs read
  by their first stamps, as for `history`, and never by their names: the cause and the mark are
  that record's, and of two `sent` records the epoch and the stamp of the post are. A frame two
  installations wrote a `sent` record for is the post of the one whose stamp is earlier, and at
  one stamp of the one whose id sorts first: the other's `sent` record is left out, and its cell
  goes by what else it wrote of the frame.
- `shown_messages` is how many of the last entries of `messages` a page draws before it is asked
  for the rest: every post while their cells are 2000 or fewer, and else the rows that hold 2000
  cells between them, rounded down to a whole row and never less than one. 2000 cells, each with
  its mark on a timeline, are what a browser draws in about a tenth of a second. A row counts
  here as a cell for each installation, whether or not the page starts folded: a folded page
  draws a cell for each fold in it, and so fewer cells than the count is made for.
- `unmatched_unreadable` counts the `unreadable` records that name a frame for which no shared
  log holds a `sent` record: records and not frames, so a frame an installation reported twice,
  or two installations reported, is counted twice. A vote or any other encrypted frame of the
  group is counted here as much as a post whose sender shares no log, so an installation an
  epoch behind adds one for every such frame the group sends.
- `runs` are the runs the seats wrote in, by name, each with the changes of its delivery state.

## Checking a report

`check` holds the report of one conversation against a file that says what was expected of it,
and says each way the two differ:

```sh
python3 tools/forks check --expect FILE PATH...
```

The paths are read as `report` reads them. The file is JSON. This one expects the group of
[Running it](#running-it) whole, with a member the logs never name and without Mira:

```json
{
  "conversation": "b05d4417e9",
  "verdict": "whole",
  "installations": {
    "a3f91c07": {"name": "saro", "position": "tip"},
    "7b1e44d9": {"name": "raya", "position": "tip"},
    "e09a3f52": {"name": "pax", "position": "tip"},
    "b54a9d10": {"name": "kes", "position": "tip"}
  },
  "posts": [
    {"sender": "a3f91c07", "cells": {"7b1e44d9": "read", "e09a3f52": "read"}},
    {"sender": "7b1e44d9", "cells": {"a3f91c07": "read", "e09a3f52": "read"}},
    {"sender": "a3f91c07", "cells": {"7b1e44d9": "read"}}
  ]
}
```

| Field | What it holds |
|---|---|
| `conversation` | The id of the conversation to check. The logs may hold others, which are not looked at. |
| `verdict` | The [verdict](#verdicts) the report must give, whole, with the epoch where it names one. |
| `installations` | Every installation the report must hold, by the eight hex characters a log names it by. An installation the report holds and the file does not is a difference, and so is one the file holds and the report does not. |
| `position` | Of an installation: its [position](#positions) in the report. |
| `name` | Of an installation, and optional: what a difference calls it, before its id. The check knows nothing of where the name comes from. |
| `posts` | Optional. The posts the report must hold, each known by its sender and its place among that sender's posts in the file, since a log holds nothing of what a post said: the second entry of a sender is the second post it sent. A sender's posts are counted in the order its log holds their `sent` records, run after run, so two it wrote within one stamp keep their order. A file without `posts` says nothing of posts and they are not looked at; with `posts`, a post the report holds and the file does not is a difference, so an empty list expects none. |
| `sender` | Of a post: the installation that sent it, one of `installations`. |
| `cells` | Of a post: what became of it on an installation, by the words of `cells` in [the JSON report](#the-json-report): `sent`, `read`, `unreadable`, `not a member`, `no log` or `no record`. An installation it leaves out is not held to anything for that post. |

It prints one line when the report is as expected, and otherwise a line for each difference, with
what the report says before what was expected:

```console
$ python3 tools/forks check --expect expect.json logs/
verdict: split at epoch 3, expected whole
kes (b54a9d10): not in the report, expected tip
19f6b0e3: tip, not expected
post 1 of raya (7b1e44d9) on pax (e09a3f52): unreadable, expected read
post 2 of saro (a3f91c07): not in the report
post 1 of pax (e09a3f52): in the report, not expected
```

A cell the file names and the report lacks reads `not in the report` where its word would stand,
as the position of Kes does here, and a conversation no log names reads
`conversation b05d4417e9: not in the logs`. A line on a report target that was malformed is a
difference too, since the report was drawn without it.

A post an installation holds `unreadable` with `sender` for its cause reads
`unreadable since the sender's account could not be checked`. The post decrypted there, so what
kept it from being read is the lookup of the account and not the state of the group.

A post whose `sent` record is in no log given is in no report, so it is not counted: each later
post of its sender is then held against the entry before its own, and the lines name the wrong
posts. Nothing in a log says which post of the file a record is of.

The check looks at the verdict, the positions and the cells of the posts, and at nothing else
the report holds. It does not look at `unmatched_unreadable`, the `unreadable` records of frames
no log given says it sent, which a vote or a commit an installation could not read adds to as
much as a post does: a report that holds such records can be as expected. Nor does it look at
why a cell is `unreadable`, but to word `sender` in a difference.

| Status | Meaning |
|---|---|
| 0 | The report is as the file expects: `conversation b05d4417e9: as expected`. |
| 1 | The report differs, or a line on a report target was malformed. |
| 2 | The command line is wrong, a log cannot be read, or the file is no expectation: it is not JSON, says a field twice, lacks a field, holds one it does not know or one of another kind than the table gives, names an installation by anything but eight hex characters in lower case, gives a `sender` or an installation of `cells` that is not among `installations`, gives a position or a word of `cells` the report never says, or holds a `name`, a `verdict` or a `conversation` with a character that is not printed, a line break among them. Where the refusal quotes the file, such a character is escaped. |

## Limits

- In the line shape of `tracing`, a span without fields and a one-word target look alike: each is
  a word and a colon. A line whose target is one word, and whose message is a report target, a
  colon and a record, is read as that record.
- An installation's runs are put in order by the stamp of its first record in each, and a stamp
  without an offset is read as UTC. Two runs of one installation, one stamped with its offset and
  one without, can be read in the wrong order.
- `never joined` is said before `ended`: an installation whose shared logs hold a record of its
  own, and no `epoch` and no `left` record, reads `ended` only by a kept line, even when every
  run it wrote in is final.
- A directory is read one level deep, and a run is named after its file. Logs kept as one
  directory for each member, under one file name, are runs of one name and are refused.
- `NAME.NNN.log` is always read as part NNN of the run `NAME`, also when it is a second copy of
  `NAME.log` that someone kept under that name.
- A run's log given as two files that are not its numbered parts is read as two runs, and the
  link across the cut is lost.
- A seat stays `no data` while a state that lists it has nothing linked on from it and was not
  passed, though a later state that nothing links to lacks it.
- The tail of a split verdict also counts a live installation whose state is on no branch: one a
  later split follows, or one whose later states are not one line.
- A post is known by its frame, its sender and its stamp: the records hold nothing of what it
  said, and the report says nothing of it.
- A `received` record that names a frame no shared log holds a `sent` record of gives no entry
  of `messages` and is counted nowhere. An `unreadable` one is counted in `unmatched_unreadable`.
- `no record` says that a seat's shared logs hold no record of the frame, and no more than
  that: the frame may never have arrived, or the log that would hold it was not shared. It
  stands as well for a seat whose every shared run began after the post was sent, or ended
  before it. Where the sender wrote no `epoch` record for the epoch it sent in, no roster is
  known for the post, and a seat that was no member then reads `no record`, or `no log`, and
  never `not a member`.
- An `unreadable` record takes no key to cause: anyone who can publish to a conversation can
  make every member write one, with whatever epoch the frame's header names. So the records of
  a post show what the logs hold of it and decide nothing: the verdict is drawn from the
  `epoch` records alone.
- A frame's id is of its bytes as they travel. Whoever relays a frame can encode it another
  way, and it is then read under an id no `sent` record carries.

## Tests

From the repository root:

```sh
python3 -m unittest discover -s tools/forks
```

`tests/fixtures/histories` holds thirty histories of a group, each a directory of logs as its
members would share them, with the verdict and the positions the tool must give in `expect.json`.
The tests of `check` hold each of those histories against an expectation made of its
`expect.json`, and against ones that differ from it.
`tests/fixtures/real` holds two logs of a real group, cut to their records and the lines around
them.

## Layout

| File | What it does |
|---|---|
| `forks/records.py` | Reads log lines into records. |
| `forks/runs.py` | Turns files and directories into runs. |
| `forks/rule.py` | Decides the report from records. It reads no file. |
| `forks/text.py` | Writes the report as text. |
| `forks/check.py` | Holds a conversation's report against an expectation. |
| `forks/cli.py` | The command line. |
