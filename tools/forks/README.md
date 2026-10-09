# forks

Do the members of a group still hold one group?

A GroupV2 conversation moves from epoch to epoch as its members apply commits. When two members
apply different commits at the same epoch the group forks: each side goes on, and neither can read
what the other sends. Nobody is told. Every installation writes a few trace records into its log
as it goes, and `forks` reads the logs the members share and says what they add up to: one group
or several, who is on which branch, who is behind, who was removed, and how much of the group the
logs cover.

The tool reads logs and nothing else: `report` reads the files it is given, and `serve` the logs
members send it. It needs Python 3.9 or newer and no package beyond the standard library.

## Getting a log with records in it

libchat writes its records at the `info` level, so a log holds them only when its writer lets the
target `libchat::groupv2trace` through at `info`.

The scale test runs a whole group in one process. With `LOG=info` its output holds a record for
every epoch each member reaches, and this is how the CI job captures and reads it:

```sh
LOG=info cargo test -p integration_tests_core --test test_group_v2_scale -- \
  --nocapture --test-threads=1 > scale.log 2>&1
python3 tools/forks report scale.log
```

`chat-cli` writes no log unless it is given `--log-file`, and then it filters at `warn` unless
`RUST_LOG` sets another filter. A member who runs it sets both:

```sh
RUST_LOG=warn,libchat::groupv2trace=info cargo run -p chat-cli -- --name saro --log-file saro.log
```

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
    on it: 19f6b0e3, 7b1e44d9, a3f91c07
  branch B: epoch 3, state e849c2aa9ab1f74d, 4 members
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

Each conversation in the logs gets its own block: the verdict, the coverage, every branch with its
newest epoch, the fingerprint of the state there, how many members that state holds and which
installations are on it, and then every installation with its account, the run it wrote in, the
newest epoch it reported and its position. The last line says what became of the lines that were
read.

`--json FILE` also writes the whole report as JSON, which holds more than the text does. See
[The JSON report](#the-json-report).

### Exit status

| Status | Meaning |
|---|---|
| 0 | Every conversation in the logs reads `whole`, and no line on a report target was malformed. |
| 1 | A conversation reads anything else, the logs hold no conversation, or a line on a report target was malformed. |
| 2 | The command line is wrong, or a log cannot be read. |

## The lines it reads

A log line is a stamp, a level, optional span prefixes, a target, a colon and a message. Two
writers produce the logs, and the tool reads both:

```text
2026-10-09T06:54:50.327025Z  INFO core.handle_frame{user_id=raya}: libchat::groupv2trace: left installation=7b1e44d9 convo=2a30d6a2c1 epoch=8
2026-10-08T16:02:11.004+02:00 INFO: libchat::groupv2trace: left installation=7b1e44d9 convo=2a30d6a2c1 epoch=8
```

The first is the default formatter of the `tracing` crate, with or without colour codes: the stamp
is UTC, the level is padded and has no colon, and the spans the event was written in come before
the target. The second is the log file of logos-chat-module: the stamp is local time, with or
without its offset from UTC, the level ends in a colon, and there are no spans.

A **record** is a line whose target is `libchat::groupv2trace` or `chat_module::report`. Its message is
the record's name and then `key=value` words, each separated by one space. The target is read at
its place in the line and never searched for, because a conversation id is text its creator chose
and other lines print it.

| Target | Record | Fields | What it says |
|---|---|---|---|
| `libchat::groupv2trace` | `run` | `installation` `account` | An installation started, acting for an account. |
| `libchat::groupv2trace` | `epoch` | `installation` `convo` `epoch` `state` `members` `roster` | The installation holds the conversation at this epoch, in this state, with these members. |
| `libchat::groupv2trace` | `left` | `installation` `convo` `epoch` | The commit that made this epoch removed the installation. |
| `libchat::groupv2trace` | `sent` | `installation` `convo` `frame` `epoch` | The installation sent a frame. |
| `libchat::groupv2trace` | `received` | `installation` `convo` `frame` `from` `epoch` | The installation read a frame another one sent. |
| `libchat::groupv2trace` | `unreadable` | `installation` `convo` `frame` `epoch` `why` | A frame arrived and could not be read. |
| `chat_module::report` | `delivery` | `state` | The delivery service of this run changed state. |

| Field | Written as |
|---|---|
| `installation`, `from` | The first four bytes of the installation's key: 8 hex characters. |
| `account` | The first four bytes of the account id, in hex. An id shorter than four bytes is written whole, and an empty one as nothing. |
| `convo` | The conversation id as one word. A byte outside `[0-9A-Za-z_-]` is written `%xx`, and the tool keeps the id as written. |
| `epoch`, `members` | A number. `members` is the length of the roster. |
| `state` | 16 hex characters: a fingerprint of the group state. Installations that hold the same state write the same fingerprint. |
| `roster` | The members, comma-separated, each `installation:account`. |
| `frame` | The id of a frame, in hex. |
| `why` | `epoch`, `decrypt` or `sender`. |
| `state` of `delivery` | `initialising`, `online`, `error` or `stopped`. |

`run`, `epoch` and `left` decide the verdict. The other four add detail, and logs without them
read the same.

No line stops the tool. The last line of a report counts what was read:

- **records**: the lines read as records.
- **without a stamp**: lines that do not start with a stamp, such as a backtrace or the output of
  a build. They are skipped.
- **malformed**: lines on a report target whose record lacks a field, repeats one, holds a
  value of the wrong shape, or gives a `members` that is not the length of its `roster`. They are
  skipped.
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
records of another run is that run at an earlier length, and only the longer is read. A final
mark on either, which the collector gives, counts for the one that is read: it is one run, and
its writer is gone.

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

A file does not say whether its writer is gone, so `report` marks no run final and never says
`ended`. The rule takes the final runs as an input, and the collector, which is told when a run
is final, hands them to it: see [Collecting logs](#collecting-logs).

A **kept line** is what the collector keeps of an installation in a conversation once the log of
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
are given.

```json
{
  "lines": {"lines": 69, "records": 42, "unstamped": 0, "malformed": 0, "unknown": 0},
  "conversations": [
    {
      "id": "b05d4417e9",
      "verdict": "split at epoch 3",
      "split_epoch": 3,
      "coverage": {"sharing": 4, "seats": 4},
      "branches": [
        {
          "label": "A",
          "since": 3,
          "epoch": 4,
          "state": "5a850890461cab7f",
          "members": 4,
          "installations": ["19f6b0e3", "7b1e44d9", "a3f91c07"]
        }
      ],
      "states": [
        {
          "epoch": 3,
          "state": "e849c2aa9ab1f74d",
          "branch": "B",
          "members": 4,
          "roster": ["19f6b0e3", "7b1e44d9", "a3f91c07", "e09a3f52"],
          "held_by": ["e09a3f52"],
          "after": [{"epoch": 2, "state": "763449d6cdefb10f"}]
        }
      ],
      "installations": [
        {
          "id": "e09a3f52",
          "account": "0d7a66f3",
          "runs": ["pax"],
          "position": "tip",
          "branch": "B",
          "epoch": 3,
          "state": "e849c2aa9ab1f74d",
          "history": [
            {
              "kind": "epoch",
              "epoch": 1,
              "state": "77797f82b14f233d",
              "at": {
                "run": "pax",
                "line": 7,
                "time": "2026-10-08T15:33:12.080+02:00",
                "has_offset": true
              }
            }
          ]
        }
      ],
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
            "has_offset": true
          },
          "cells": {
            "19f6b0e3": "read",
            "7b1e44d9": "sent",
            "a3f91c07": "read",
            "e09a3f52": "unreadable"
          },
          "why": {"e09a3f52": "decrypt"}
        }
      ],
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
                "has_offset": true
              }
            }
          ]
        }
      ]
    }
  ]
}
```

The lists above are cut to one entry each.

- `lines` is the count the text report ends with.
- `conversations` are in the order of their ids.
- `split_epoch` is the first epoch with two states, or `null`.
- `branches` are in the order of their names. `since` is the epoch of a branch's first state,
  and `epoch`, `state` and `members` describe its newest.
- `states` are every state reported, by epoch and then fingerprint. `branch` is `null` for a
  state on no branch, `held_by` lists the installations that reported it, and `after` lists the
  states that links lead to it from.
- `installations` are every seat, by id. `account` comes from the installation's `run` record
  or, without one, from a roster, and is `""` for an account of no bytes. `epoch` is the epoch of
  its last record, and `state` and `branch` are `null` when that record is a leave or the seat
  has no record. `history` holds its records in the order written, each of kind `epoch` or
  `left`, and a leave has no state. A seat that goes by a kept line has the line's `account`
  and `epoch`, no `state`, `branch` or history, and in `runs` only the runs that hold other
  records of its own.
- `at` is where a record was written: the run, the line in it, and the stamp as written.
  `has_offset` says whether the stamp carries its offset from UTC.
- `messages` has an entry for each frame a `sent` record names, in the order sent. `state` is the
  state its sender held at that epoch, or `null` when the sender wrote no `epoch` record for it.
  `cells` says what became of the post on each seat, and `why` gives the cause for each
  `unreadable`.

  | Cell | When |
  |---|---|
  | `sent` | The seat sent it. |
  | `read` | It wrote a `received` record for the frame. |
  | `unreadable` | It wrote an `unreadable` record for the frame. |
  | `not a member` | The roster of the state the post was sent in does not list it. |
  | `no log` | No shared log holds a record of it. |
  | `never got` | Its log is here and holds no record of the frame. |

- `unmatched_unreadable` counts the `unreadable` records that name a frame for which no shared
  log holds a `sent` record.
- `runs` are the runs the seats wrote in, by name, each with the changes of its delivery state.

## Collecting logs

`report` reads the logs someone gathered. `serve` gathers them: it takes the logs members send
it as they run, keeps them, and answers each conversation's report as JSON, drawn by the same
rule from what it holds when it is asked.

From the repository root:

```sh
python3 tools/forks serve --data forks-data --token-file forks-token
```

`forks-data` is the directory the logs are kept in, and is made when it is missing.
`forks-token` is a file that holds the token: 16 to 128 characters of `A-Z`, `a-z`, `0-9`, `_`
and `-`, with nothing but white space around them. The token is read from that file and from
nowhere else, so it is in no command line and no environment. The collector listens on
`127.0.0.1`, port 8750, unless `--bind ADDRESS` or `--port N` says otherwise, and runs until it
is interrupted. `--max-bytes N` is the most bytes of logs it holds at a time, and without it
there is no such limit.

It writes a log of its own to standard error, one line for each write:

```text
2026-10-09T17:28:42Z serving forks-data on http://127.0.0.1:8750/
2026-10-09T17:28:51Z run 18fae263f6ea83ec26079157cea3f019 opened
2026-10-09T17:28:51Z run 18fae263f6ea83ec26079157cea3f019 took 10066 bytes, holds 10066
```

A line also names each directory a start leaves alone, each run that goes at thirty days, and
what failed when the disk or a call did. That log never holds the token, a receipt, a run's name
or anything a member's log says, and when it cannot be written no call fails for it.

### The calls

On the collector a **run** is one stream of log bytes under an id the collector made. Its name is
a label its sender gave it, and two runs may carry one name.

| Call | Body | Answer |
|---|---|---|
| `POST /runs` | `{"name": NAME, "app": APP}`, each 1 to 64 characters | 201 `{"id": ID, "receipt": R}`: an empty run is open. |
| `PUT /runs/ID/log?offset=N` | The run's bytes from `N` on, at most 4 MiB, or none. | 200 `{"size": S, "final": F}` when `N` was the size held: the bytes are appended. 409 with the same object when it was not: nothing is appended. |
| `PUT /runs/ID/log?offset=N&final=1` | The last bytes, or none. | The same, and the run is final: its writer is gone. A final run answers 409 to more bytes, and 200 to its mark sent again. |
| `DELETE /runs/ID` | | 204: the run's log and its records are gone. |
| `GET /conversations` | | 200: every conversation the logs held name, the worst first. |
| `GET /conversations/CID` | | 200: the report of one conversation. |
| `GET /runs/ID/log?line=N` | | 200 `{"lines": [{"line": N, "text": "..."}]}`: line `N` of the run's log, the two before it and the three after. |

| Status | When |
|---|---|
| 400 | A body, an offset or a line is not what the call takes, or a body gives its length twice or beside a transfer coding. |
| 401 | The call does not carry the token. The answer says nothing else, whatever was asked for. |
| 403 | A write to a run does not carry that run's receipt. |
| 404 | The collector has no such run, no such conversation or no such call. |
| 411 | A `POST` or a `PUT` does not say the length of its body. |
| 413 | A body is longer than 4 MiB. |
| 507 | With `--max-bytes`, the bytes would take the logs held past it. Nothing is appended and nothing is marked, and the run still counts as heard from. Its sender loses nothing, and asks again later. |

Every answer with a body is JSON, and an error is `{"error": "..."}`. A `PUT` without bytes at
the size held changes no log, and it is how the collector knows it still hears from a run.
Records are read from whole lines: what follows the last line break of the bytes held waits for
the bytes that end it.

### The token and the receipt

**The token** goes on every call, in the header `Authorization: Bearer TOKEN`. Whoever holds it
may send logs, and read every report and every line of every log held. It does not hide what
crosses the network: the collector speaks plain HTTP, so off the loopback address the token and
the logs travel in the clear unless something in front of it adds TLS.

**The receipt** of a run is answered once, when the run is opened, and goes on every write to
that run, in the header `Forks-Receipt: R`: an append, the final mark and the delete. So a holder
of the token cannot add to, end or remove a run somebody else opened. It does not hide the run,
which the token alone reads. The collector keeps only a hash of a receipt, and cannot give a lost
one again.

### What the directory holds

Each run has a directory under `forks-data`, named after its id:

| File | What it holds |
|---|---|
| `ID/ID.log` | The run's log as it was received, byte for byte. |
| `ID/run.json` | The run's name and app, the hash of its receipt, whether it is final, and when the collector last heard from it. Once the log is gone, the lines kept of it. |

The records are held in memory, and a start reads them again from the logs: each log whole,
whatever its `run.json` says, so bytes appended just before a stop are not lost. `run.json` is
replaced in one step. A directory that does not hold a run as the collector writes one is left as
it is, and a line of the collector's log names it. The logs are files like any other, and
`report` reads them, naming each run by its id and marking none final:

```sh
python3 tools/forks report forks-data/*/*.log
```

### Thirty days

The collector hears from a run when it opens it and at every `PUT` it accepts, one without bytes
included. It hears from it at a `PUT` it answers 507 as well: the sender is there, and it is the
collector that cannot take the bytes. A run it has not heard from for thirty days goes: its log
and its records, and a call for it then answers 404.

Of a run that was not final nothing is left. Of a final run a line is kept for each installation and
conversation in its log, the [kept line](#positions), so the report goes on naming those
installations: one that goes by its line reads `removed` where its last record was a leave, or where
there is a newest roster at or above the line's epoch and none of those lists it, and `ended`
otherwise. `PUT` answers 404 for such a run, and `DELETE` with its receipt still answers 204 and
removes the lines. The lines of a conversation also go when the last log that names it goes.

A run the disk does not let go of stays as it is, log and records, and is tried again an hour
later, with a line in the collector's log each time.

### The collector's JSON

`GET /conversations/CID` answers the object that `--json` holds for a conversation, with two
differences. Wherever it names a run, it gives the run's id. And each entry of `runs` says more:

```json
{
  "id": "18fae263f6ea83ec26079157cea3f019",
  "name": "saro",
  "app": "logos-chat-ui 0.3.0",
  "final": false,
  "size": 10066,
  "heard": "2026-10-09T17:28:51Z",
  "delivery": []
}
```

`size` is how many bytes of the run's log are held, and `heard` is when the collector last heard
from it, in UTC. A conversation's id is text its creator chose, so in an address its `%` is
written `%25`.

`GET /conversations` answers `lines`, the count over every log held, and `conversations`, each
with its `id`, `verdict`, `split_epoch` and `coverage`, the `label` and newest `epoch` of each of
its `branches`, and `heard`, the latest of the runs its report lists. They come the worst first:
a split with two or more sides live, with one, with none, then `whole as far as seen`,
`whole, members behind`, `not enough data`, `no live member` and `whole`. Of two that read alike,
the one heard from last comes first.

A report is drawn when it is asked for and kept until a write changes it: a record of one of its
seats, a final mark on a run one of them wrote in, or a log that goes.

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
- The collector speaks plain HTTP, one request to a connection, and closes a connection that
  falls silent for 30 seconds. It serves the calls above and no page.
- A body the collector refuses for its length, or for saying none, is answered unread. A client
  that reads an answer only once it has sent its body finds the connection closed instead.
- The body of a call without the token is read and kept nowhere, for 30 seconds at most, and then
  the call is answered 401. The head of a request is read before the collector can know whether
  it carries the token: a peer without it holds a connection for as long as it sends one slowly
  and is never silent for 30 seconds.
- The collector syncs nothing to disk. After a power cut a log may be shorter than the size last
  answered, which its sender mends at its next `PUT`, and a `run.json` may be unreadable, which
  leaves its run out until the file is mended or the directory removed by hand.
- While a directory the collector left alone at its start holds a log, no kept line goes for want
  of a log that names its conversation: the log nobody read may be the one that does. The lines
  stay in their `run.json` until a start that finds no such directory.
- The collector holds every record in memory. Logs of 300 members, each 300 epochs of one
  conversation, 500 MB in all, took 20 seconds to read at a start and a second to draw the
  report of, in 160 MiB of memory.
- A directory is for one collector. Nothing stops a second from starting on it, and the two
  would each replace the other's `run.json`.
- `--max-bytes` counts the bytes of the logs, and not what `run.json` takes.

## Tests

From the repository root:

```sh
python3 -m unittest discover -s tools/forks
```

`tests/fixtures/histories` holds thirty histories of a group, each a directory of logs as its
members would share them, with the verdict and the positions the tool must give in `expect.json`.
`tests/fixtures/real` holds two logs of a real group, cut to their records and the lines around
them. The tests of the collector each start one on a loopback port the system picks, and call it
over HTTP.

## Layout

| File | What it does |
|---|---|
| `forks/records.py` | Reads log lines into records. |
| `forks/runs.py` | Turns files and directories into runs. |
| `forks/rule.py` | Decides the report from records. It reads no file. |
| `forks/text.py` | Writes the report as text. |
| `forks/collector.py` | Keeps the runs members send, and draws each conversation's report. |
| `forks/serve.py` | Serves a collector over HTTP. |
| `forks/cli.py` | The command line. |
