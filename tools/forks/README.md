# forks

Do the members of a group still hold one group?

A GroupV2 conversation moves from epoch to epoch as its members apply commits. When two members
apply different commits at the same epoch the group forks: each side goes on, and neither can read
what the other sends. Nobody is told. Every installation writes a few trace records into its log
as it goes, and `forks` reads the logs the members share and says what they add up to: one group
or several, who is on which branch, who is behind, who was removed, and how much of the group the
logs cover.

The tool reads logs and nothing else: `report` reads the files it is given, `serve` the logs
members send it, and `share` sends files to where `serve` runs. It needs Python 3.9 or newer and
no package beyond the standard library.

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
| 2 | The command line is wrong, a log cannot be read, or the report cannot be written. |

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
| `convo` | The conversation id as one word. A byte outside `[0-9A-Za-z_-]` is written `%xx`, and the tool keeps the id as written. An id holds at most 1597 bytes, which is what fits the 8000 bytes of address the page asks for a conversation by. |
| `epoch`, `members` | A number, at most 9007199254740991, the largest a browser reads from JSON exactly. `members` is the length of the roster. |
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
  value of the wrong shape or past its bound, or gives a `members` that is not the length of its
  `roster`. They are skipped.
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
          "installations": ["19f6b0e3", "7b1e44d9", "a3f91c07"]
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
          "row": 1
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

The lists above are cut to one entry each.

- `lines` is the count the text report ends with.
- `conversations` are in the order of their ids.
- `split_epoch` is the first epoch with two states, or `null`.
- `positions` are the positions an installation is in, in the order `tip`, `behind`,
  `lineage unknown`, `ended`, `removed`, `never joined`, `no data`, each with its installations
  and their `count`.
- `epochs` are the epochs a state or a leave names, in order.
- `branches` are in the order of their names. `since` is the epoch of a branch's first state,
  and `epoch`, `state` and `members` describe its newest.
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

- `lanes` are the installations under the headings they are read under, each heading with the
  `positions` of its installations, each once. First come the branches of a split, by name:
  `under` is `branch`, and `branch` names it. Then comes `one line` without a split, or
  `neither branch` with one, and last `not placed`. An installation goes by the last state it
  wrote, also when a leave follows it: under that state's branch when its `lineage` is `branch`,
  under `one line` or `neither branch` when it is `shared`, and under `not placed` when it is
  `side` or `unknown` or the installation wrote no state. Under a heading they come by position,
  in the order of `positions`, and then by id, and a heading with no installation is left out.
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
rule from what it holds when it is asked. [`share`](#sharing-logs) sends it the logs of a
directory, and [its page](#the-page) draws the reports in a browser.

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
| `GET /` | | 200: [the page](#the-page). |
| `GET /page/NAME` | | 200: the file `NAME` of the page, which is `index.html`, `report.css` or `report.js`. |
| `GET /?token=TOKEN` | | 303 to `./`, with a cookie that holds the token: a browser's way in. |

| Status | When |
|---|---|
| 400 | A body, an offset or a line is not what the call takes, or a body gives its length twice or beside a transfer coding. |
| 401 | The call does not carry the token. The answer says nothing else, whatever was asked for. |
| 403 | A write to a run does not carry that run's receipt. |
| 404 | The collector has no such run, no such conversation, no such file of the page or no such call. |
| 411 | A `POST` or a `PUT` does not say the length of its body. |
| 413 | A body is longer than 4 MiB. |
| 507 | With `--max-bytes`, the bytes would take the logs held past it. Nothing is appended and nothing is marked, and the run still counts as heard from. Its sender loses nothing, and asks again later. |

Every answer with a body is JSON, but for the page and its files, and an error is
`{"error": "..."}`. Every answer carries `Cache-Control: no-store`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and a
`Content-Security-Policy` that lets the page load from the collector alone, run no script or
style written into it, and lets no page show an answer in a frame. A `PUT` without bytes at the size held changes no log, and it is
how the collector knows it still hears from a run.
Records are read from whole lines: what follows the last line break of the bytes held waits for
the bytes that end it.

### The token and the receipt

**The token** goes on every call, in the header `Authorization: Bearer TOKEN`. Whoever holds it
may send logs, and read every report and every line of every log held. It does not hide what
crosses the network: the collector speaks plain HTTP, so off the loopback address the token and
the logs travel in the clear unless something in front of it adds TLS.

A browser sends no such header for an address a person opens, so it comes in by the share
address: `GET /?token=TOKEN`, the token being the whole query, answers 303 to `./` and sets the
cookie `forks_token=TOKEN; HttpOnly; SameSite=Lax; Max-Age=2592000`, with `; Secure` when the
request carries `X-Forwarded-Proto: https`. The cookie lasts the thirty days a log does. It
stands for the header on a `GET` and on nothing else: a write needs the header, so no page of
another site can have a browser write. It is `Lax` so that a share address opens from where it
was posted: a browser sends such a cookie when a link on another site is followed, and to
nothing that site loads by itself. A token in the query of any other call opens nothing. The
redirect is relative and the cookie names no path, so behind a proxy that serves the collector
under a path both stay under that path.

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

`GET /conversations/CID` answers the object that `--json` holds for a conversation, with three
differences. Wherever it names a run, it gives the run's id. It ends with `lines`, the count of
lines over the runs its report lists. And each entry of `runs` says more:

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
its `branches`, `heard`, the latest of the runs its report lists, and `unheard`, the seconds
from then to the answer. They come the worst first: a split with two or more sides live, with
one, with none, then `whole as far as seen`, `whole, members behind`, `not enough data`,
`no live member` and `whole`. Of two that read alike, the one heard from last comes first.

A report is drawn when it is asked for and kept until a write changes it: a record of one of its
seats, a final mark on a run one of them wrote in, or a log that goes.

### The page

The collector serves a page that draws its reports. A person opens the collector's share address
in a browser, the address `share` takes:

```text
http://127.0.0.1:8750/?token=5d1c0a9e7b3f42d8
```

The collector answers with [the cookie](#the-token-and-the-receipt) and sends the browser on to
the same address without the token, where the page stays. The page is the three files of
`forks/page`, served as they stand: it has no build step and loads nothing from another host.

| Part | What it shows |
|---|---|
| The list at the left | Every conversation a held log names, the worst first, with its verdict, how many of its seats share a log and how long ago the collector heard from a run of it. |
| The front page | The same conversations as a table, each also with the newest epoch of each of its branches, and under it the count of lines over every log held. |
| The title | The conversation's id, and how long ago the collector heard from a run of it. |
| The verdict | A conversation's verdict in the words of the report, how many of its seats share a log, and how many malformed lines were skipped when any was. |
| The tiles | How many seats share a log, how many branches there are and the first epoch with two states when there is a split, and how many installations are in each position, with their ids when they are three or fewer. |
| Lineage of states | Every reported state as a circle that holds how many installations reported it, in the column of its epoch and in its `row`, a line of states named for its newest: a branch, the states that only one branch's logs reach, the shared line, or the states of unknown lineage. A line is a link, a mark stands at the first epoch with two states, and each branch ends in its name, its newest epoch and how many installations are on it. |
| Installations | A row for each installation, under the headings of `lanes`, with a line for each run it wrote in: the run's name, what sent it and when the collector last heard from it. A cell for each epoch of its `cells`: every record it wrote there in the order written, each a state with the letter of its branch or a leave, or an epoch it has not reached, or an epoch whose roster lists a seat nobody shared a log of. Then its position, with an end mark where it is `removed`, `ended` or `never joined` unless a leave ends the lane, and the words `its log is gone` for a seat that goes by a kept line. The lanes open at the newest epoch. |
| Timeline | A row for each installation over the time from the first to the last record that names the conversation: a line for each of its runs, cut where the run reaches past that time, dotted where the run's `delivery` records say it was not online, with a mark where it reached each epoch. Times are UTC, and the date stands under the first stamp of each day. |
| The state each branch holds | The newest state of each branch: its epoch, its fingerprint, how many its roster lists, the installations on it and the seats its roster lists. |
| Log | The line of a record in its run's log with the lines around it, when a cell of the installations or a mark of the timeline is clicked. The lines stay open while the page reads again, and close when their record is drawn no more. |

The page reads `GET conversations`, and `GET conversations/CID` for the conversation its address
names after `#`, when it is opened and again every 30 seconds while its tab is visible. A read
of the 30 seconds is left out while the one before it is under way, and a read gives the
collector up when 20 seconds pass without a byte of its answer. It reads
`GET runs/ID/log?line=N` when a cell or a mark is opened. Every address it uses is relative, so
it works under whatever path a proxy serves the collector at.

The page decides nothing: every verdict, position, count, order and heading it shows is in the
collector's JSON. What a run's name, an account or a log line says reaches the page as text and
never as markup.

When the collector answers 401, the page says that the share address must be opened again. When
the collector holds no log that names the conversation shown, the page says so and links to the
front page. When the collector does not answer, the page says when it last did, in UTC, and what
it drew stays. It stays as well when the answer is not the collector's JSON, which the page says
it cannot read. A reader is left at the part they were reading when a report grows.

Every link, every cell that holds a record and every mark of the timeline can be reached and
opened from the keyboard, with an outline around the one that has the focus: Tab moves from part
to part, the arrow keys, Home and End move among the cells and among the marks, Enter or Space
opens the log lines, and Escape closes them. A read that draws the page anew leaves each table
the cell the Tab key stopped at in it. A conversation that is opened takes the focus at its
title, and closing the log lines gives it back to the mark that opened them. A position has its
icon and its word, and a branch its letter as well as its hue.

## Sharing logs

`share` sends logs to a collector from a terminal: every run in the paths it is given, once, and
then it ends.

From the repository root:

```sh
python3 tools/forks share --to 'http://127.0.0.1:8750/?token=5d1c0a9e7b3f42d8' logs/
```

`--to` takes the collector's share address: where its calls are made, and its token after
`?token=`. A `PATH` is what it is to `report`: a log, or a directory, which stands for every
`.log` file directly in it. The parts of a [run](#runs) go as one stream, in their order, and the
run goes by the name `report` gives it, cut to 64 characters, with `forks share` as its app. A
run is sent whole: a log named by itself while its directory holds another part of its run is
refused, since the name the run is remembered by would stand for one stream now, and for another
once the directory is named.

```console
$ python3 tools/forks share --to 'http://127.0.0.1:8750/?token=5d1c0a9e7b3f42d8' logs/
mira: sent 2414 bytes, 127.0.0.1:8750 holds 2414 of 2414
pax: sent 2236 bytes, 127.0.0.1:8750 holds 2236 of 2236
raya: sent 2132 bytes, 127.0.0.1:8750 holds 2132 of 2132
saro: sent 2150 bytes, 127.0.0.1:8750 holds 2150 of 2150
```

A line on standard output says of each run how many bytes this go sent, and how many the
collector holds of the bytes the disk held when the go began: the run is whole there when the
last two are one number. A run the collector gave no size of has no such line. What kept a run
from being whole is a line on standard error. A line that speaks of the collector names it by its
host, and the token is in nothing the command prints or writes. A line is written when its run is
done: a go whose output nobody reads any more ends there, with 2.

Run again, the command goes on with the same runs and sends what their logs gained. It keeps no
offset: of a run it sent before it asks the collector, with a request that holds no bytes at the
size the disk has, and a 409 says where to go on. A request holds at most 1 MiB of a run and is
given up after 30 seconds.

| Status | Meaning |
|---|---|
| 0 | The collector holds every run whole. |
| 1 | A run is not whole there. |
| 2 | The command line, the address or a path cannot be used, a directory's `.forks-share.json` cannot be read as its own or written, another `share` is sending from a directory, or nobody reads what the command prints. |

### The share address

Everything before `?` is the base, and each call is made under it, with a `/` added where the
base does not end in one. `share` refuses an address that is not `http` or `https`, or that holds
a fragment, a user or a password, no `token`, more than one, one spelled with an escape, or one
that is not 16 to 128 characters of `A-Z`, `a-z`, `0-9`, `_` and `-`, or whose host has a label
that is empty or longer than 63 characters. It quotes no address it refuses. Nor does `forks`
quote one that stands out of its place, as in `forks --to ADDRESS share logs/`: in what it says
of a command line, `ADDRESS` stands for each word that holds `token=`. An `https` address is
checked against the certificates the system trusts.

### What it remembers

`share` keeps one file in each directory it sends logs from, `.forks-share.json`:

```json
{
  "http://127.0.0.1:8750/": {
    "saro": {
      "id": "18fae263f6ea83ec26079157cea3f019",
      "receipt": "vxNFW7h5EQ4zl8_C7fSShRxUJ-j9NDWkC0GtzFkQEcI"
    }
  }
}
```

The file is cut to one run here. For each collector, under its base, and each run, under its
name, it holds the id the collector gave the run and the receipt the run's writes must carry,
and never the token: an id or a receipt that holds the token is not what the opening of a run
promises. With it the next go adds to the same runs, and without it every log is sent as a new
run beside the old one. It is replaced in one step, and only its owner can read it: beside the
token, a receipt is the right to add to a run, to end it and to delete it. It is written once
before a run is opened, so a directory no file can be made in has no run opened. A file that is
not as `share` writes it, one that names a collector or a run twice among them, is refused: the
go says so, sends nothing from that directory and leaves the file as it is.

A go holds each directory it sends from until it ends, by a lock of the system's on the directory
itself, which leaves no file behind. A second go at the same time says so and sends nothing from
that directory: the two would each open a run for a log, and each replace the other's file.

### What the collector answers

| Answer | What `share` does |
|---|---|
| 200 | It goes on with the next bytes, until the collector holds what the disk held. |
| 409 | It goes on from the size the answer gives. When that is more than the disk has, it stops the run and says so. So it does at the second 409 a run answers in one go: the run grows by another hand, or is final. |
| 404 | It drops the run's entry, opens the run again and sends it whole, in the same go. |
| 403 | It says that the run's receipt was refused, and goes on with the next run. |
| 413 | It says that the request was longer than is taken, and goes on with the next run. |
| 401 | It says that the token was refused, and stops. |
| 507 | It says that the collector holds as many bytes as it may, and stops. |
| Any other, one that does not say what its call promises, none in 30 seconds, or no connection | It says so in a line and stops, since no other run would fare better. A redirect is such an answer, and is not followed. |

None of them loses anything: the logs are on the disk, and the next go asks again.

### No run is marked final

A file does not say whether its writer is gone, so `share` marks no run final. An installation
whose logs all came this way never reads `ended`: one whose application has stopped stays live
where it stood, and a group the others took on from there reads `whole, members behind` where it
would read `whole`. That lasts while the collector holds the run, thirty days from the last go
that named its log.

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
  falls silent for 30 seconds.
- The cookie is `Secure` only when the request says `X-Forwarded-Proto: https`. Behind a proxy
  that adds TLS and not that header, a browser sends the cookie over plain HTTP as well.
- A browser keeps a cookie by host, whatever the port. A second collector on the host, with a
  token of its own, takes the place of the first one's cookie, and the first answers 401 until
  its share address is opened again. Whatever else answers on another port of the host is sent
  the cookie, and the token in it.
- Behind a proxy that serves the collector under a path, a page of any other application of that
  host is of the collector's origin: in the browser of someone who opened the share address it
  reads the reports and the logs. A collector whose logs the rest of a host must not read wants
  a host name of its own.
- Opened in a browser, a share address with a path must end the path in `/`: the redirect and
  every address of the page are relative, and after `http://HOST/forks?token=TOKEN` they lead to
  `http://HOST/`.
- The page shows what it read last, and reads only while its tab is visible: what it shows can be
  30 seconds behind the collector. Each read asks for the whole JSON of the conversation shown,
  25 MB for the conversation below, and over a link that does not carry it in 30 seconds one
  read follows another.
- The page shows the states, the installations and the runs of a conversation, and none of its
  messages.
- Every installation has a row among the installations and a row on the timeline, and none is
  folded away. The conversation of 300 members and 300 epochs below is 25 MB of JSON and 282,000
  elements: over four runs Firefox built and laid it out in 1.7 to 2.0 seconds once it held the
  JSON, and showed it 1.9 to 2.4 seconds after the conversation was asked for.
- The page gives the first three branches a hue each and any further branch one fill: there the
  letter alone tells the branches apart.
- The timeline places a record by its stamp, and a stamp without an offset is read as UTC: logs
  stamped in the local time of two zones are drawn that far apart.
- The timeline covers the time of the records that name the conversation. A run that wrote
  nothing within that time, as the run of a seat that never joined may, has a row and no line.
- The page asks for the typeface Public Sans and ships no font: where it is not installed, the
  browser uses the system's.
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
  conversation, 500 MB in all, took 20 seconds to read at a start and a second and a half to
  draw the report of, in 235 MiB of memory at the most and 155 MiB once it was drawn.
- A directory is for one collector. Nothing stops a second from starting on it, and the two
  would each replace the other's `run.json`.
- `--max-bytes` counts the bytes of the logs, and not what `run.json` takes.
- The share address is an argument of `share`, so its token is in the shell's history and, while
  the command runs, in the list of processes.
- `share` sends a log from a file, which it can read again from where the collector stopped, and
  refuses a pipe.
- `share` sends a run as far as the disk held it when the go began: what a log gains during a go
  is sent by the next, and a log its writer moved aside or cut during a go is sent no further in
  that go.
- A log written anew under the name of one that was sent is taken for that log, grown: `share`
  adds its bytes past the size the collector holds, and stops only when it is the shorter. To
  send it as a new run, take its entry out of `.forks-share.json`.
- A run whose opening the collector answered and `share` did not hear, did not live to write
  down or could not write down, as when the disk fills between the two writes of the file, stays
  on the collector, empty, until its thirty days are over.
- An entry stays in `.forks-share.json` when its log is gone from the directory.
- `share` holds a directory by `flock`, which Python has on Unix. Where Python has none, or the
  system gives no lock on a directory, it says so and sends nothing from the directory.
- `share` reads a log through a link as `report` does: the run is named after the file the link
  leads to, and `.forks-share.json` is kept in that file's directory, which must take it, and not
  in the directory of the link.
- A collector is remembered by its base as the address spells it. Under another spelling, such as
  `HTTP://` for `http://`, `localhost` for `127.0.0.1`, a port with a zero before it or a second
  `/` at the end, it is another collector to `share`, and every log is sent to it as a new run.
- A request of `share` holds up to 1 MiB and is given up after 30 seconds, so over a link that
  carries less than 34 KiB a second a run with 1 MiB or more to send does not arrive.
- `.forks-share.json` is not synced to disk: after a power cut it may be cut short, and is then
  refused until it is removed by hand.
- `share` calls the collector itself, and reads no proxy from the environment.

## Tests

From the repository root:

```sh
python3 -m unittest discover -s tools/forks
```

`tests/fixtures/histories` holds thirty histories of a group, each a directory of logs as its
members would share them, with the verdict and the positions the tool must give in `expect.json`.
`tests/fixtures/real` holds two logs of a real group, cut to their records and the lines around
them. The tests of the collector each start one on a loopback port the system picks, and call it
over HTTP. The tests of `share` send to such a collector, and to a scripted stand-in for the
answers a collector never gives. The tests of the page come in as a browser does, by the share
address and the cookie, and read the page's files for what they must not hold: no test runs a
browser.

## Layout

| File | What it does |
|---|---|
| `forks/records.py` | Reads log lines into records. |
| `forks/runs.py` | Turns files and directories into runs. |
| `forks/rule.py` | Decides the report from records. It reads no file. |
| `forks/text.py` | Writes the report as text. |
| `forks/collector.py` | Keeps the runs members send, and draws each conversation's report. |
| `forks/serve.py` | Serves a collector over HTTP. |
| `forks/page/` | The page `serve` answers: `index.html`, `report.css` and `report.js`. |
| `forks/share.py` | Sends the runs in some paths to a collector. |
| `forks/cli.py` | The command line. |
