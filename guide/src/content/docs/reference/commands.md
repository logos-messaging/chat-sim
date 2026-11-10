---
title: Commands
description: Every subcommand of forks and sim, with its flags and exit status, the collector's HTTP calls, and harness-run.
---

Every command runs from the root of a chat-sim checkout. A `PATH` is a log, or a directory, which stands
for every `.log` file directly in it.

## forks

```sh
python3 tools/forks report PATH... [--json FILE]
python3 tools/forks check --expect FILE PATH...
python3 tools/forks serve --data DIR --token-file FILE [--bind ADDRESS] [--port N] [--max-bytes N]
python3 tools/forks share --to ADDRESS PATH...
```

| Command | Does |
|---|---|
| `report` | prints each conversation's report; `--json` also writes it as JSON |
| `check` | holds one conversation's report against an expectation |
| `serve` | the collector: keeps the runs sent to it in `DIR`, serves the page and JSON |
| `share` | sends every run in the paths to a collector, each from where the collector says it stopped |

| Flag | Default | Holds |
|---|---|---|
| `--token-file` | | the token: 16 to 128 characters of `A-Z a-z 0-9 _ -` |
| `--bind` | `127.0.0.1` | the address to listen on |
| `--port` | `8750` | the port; `0` lets the system pick |
| `--max-bytes` | no limit | the most bytes of logs to hold at a time |
| `--to` | | the share address, ending in `?token=TOKEN` |

| Exit | report | check | share |
|---|---|---|---|
| 0 | every conversation `whole`, nothing malformed | as expected | the collector holds every run whole |
| 1 | anything else | a difference, or a malformed record | it does not |
| 2 | the command line is wrong, a log cannot be read, or the JSON cannot be written | the command line is wrong, a log cannot be read, or the file is no expectation | the command line, the address or a path cannot be used |

### An expectation for check

```json
{
  "conversation": "b05d4417e9",
  "verdict": "split at epoch 3",
  "installations": {
    "a3f91c07": {"name": "saro", "position": "tip"},
    "7b1e44d9": {"name": "raya", "position": "tip"},
    "19f6b0e3": {"name": "mira", "position": "tip"},
    "e09a3f52": {"name": "pax", "position": "tip"}
  },
  "posts": [
    {"sender": "a3f91c07", "cells": {"7b1e44d9": "read", "19f6b0e3": "read", "e09a3f52": "read"}},
    {"sender": "7b1e44d9", "cells": {"a3f91c07": "read", "e09a3f52": "unreadable"}},
    {"sender": "e09a3f52", "cells": {"a3f91c07": "unreadable"}}
  ]
}
```

`installations` names every installation in the report. `posts` is optional; when given, it names every
post, in each sender's order, with the cells that matter. A cell is `sent`, `read`, `unreadable`,
`not a member`, `no log` or `no record`. On a difference, `check` prints one line for each:

```text
verdict: split at epoch 3, expected whole
post 1 of raya (7b1e44d9) on pax (e09a3f52): unreadable, expected read
```

A simulation writes its `expect.json` in this format.

### The collector's calls

Each call carries `Authorization: Bearer TOKEN`; a browser's `GET` may carry the cookie instead. A write
to a run also carries the receipt the collector answered when the run was opened, in `Forks-Receipt`.

| Call | Answer |
|---|---|
| `GET /?token=TOKEN` | a redirect to the page, with a cookie that holds the token |
| `GET /conversations` | every conversation, the worst first |
| `GET /conversations/ID` | one conversation's report, as `report --json` writes it, with each run under the collector's id |
| `GET /runs/ID/log?line=N` | line N of a run's log, two before and three after |
| `POST /runs`, `PUT /runs/ID/log?offset=N`, `DELETE /runs/ID` | open a run, append to it, and delete it; `share` uses the first two |

[`tools/forks/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/tools/forks/README.md#the-calls)
gives every call with its statuses.

## sim

```sh
python3 tools/sim run SCENARIO RIG --out DIR [--share ADDRESS]
python3 tools/sim sweep SCENARIO RIG --out DIR --runs K [--from N] [--share ADDRESS]
python3 tools/sim compare [--write] EXPECTED OUT
python3 tools/sim matrix HEAD [--base DIR | --base-point FILE] [--spread FILE] [--asked FILE] [--out FILE]
nix run .#groupv2-simulation -- run SCENARIO --out DIR [--share ADDRESS]
```

`run` runs the scenario once; `sweep` runs it K times, one after another, and with `--from N` those are
runs N to N+K-1 of a sweep, each with the seeds it has in a sweep from run 1; `compare` holds a sweep in one
process to the outcomes in `EXPECTED` and says of each run `same`, `better`, `worse`, `re-rolled` or
`changed`, or with `--write` writes the sweep's outcomes there. `matrix` pairs the runs in one process under
`HEAD` with those under `--base` by scenario and seed, at any depth, and writes as JSON a cell for each with
its change, a cell too for each run the sweeps in `--asked` ask of each side, and the verdict over the
cells: `same`, `better`, `worse`, `hint` (one cell worse), `mixed`, `re-rolled`, `no-base`, `no-outcome`
or `unbuilt`; without a base, each scenario's summary. `--base-point` names, in place of `--base`, the
`point.json` of main's point at the base, whose runs stand in for the base's at the same scenario and seed;
`--spread` names that of a point whose runs the head's are placed among, and the matrix holds of it the
summary and each run of the cells' scenarios, of each cell whether the head's epochs are below the lowest
of the point's, and how many re-rolled cells are.
[`tools/sim/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/tools/sim/README.md#a-matrix-of-a-revisions-runs-beside-its-bases)
gives its fields and what decides the verdict. `RIG` is
`--one-process PATH`, the path of `harness-run`. The flake's app `groupv2-simulation` builds the runner
and adds that flag to what follows `--`, so it runs `run` and `sweep`, and `compare` and `matrix` run with
`python3`.
`--chat-cli PATH` and `--image IMAGE [--engine docker|podman]`
run chat-cli, which is on the `local-network` branch. `DIR` must not exist.

| Exit | run, sweep | compare | matrix |
|---|---|---|---|
| 0 | every run went as expected | every run came out as expected, and the sweep has a run for each outcome expected | it wrote the matrix |
| 1 | a run did not | a run did not, or the runs and the outcomes expected differ in number | |
| 2 | the command line or the scenario is refused | the sweep or `EXPECTED` cannot be read, `EXPECTED` holds outcomes without the ids and the fields of a `grow` step, or a run was not in one process | a directory is not there, `--asked` or a point cannot be read, `--base-point` is given with `--base`, or a run cannot be read, was not in one process, or is a second run of a scenario at one seed |
| 128 + n | signal n interrupted it | | |

## harness-run

```sh
cargo run --release -p harness --bin harness-run -- PLAN.json OUT_DIR
```

Runs every member of one group in one process on a virtual clock, and writes `OUT_DIR/group.log` and
`OUT_DIR/result.json`. It exits 0 for a run, steps that stopped included, and 2, with one line on standard
error, when the plan cannot be read, the directory cannot be written, or the arguments are not those two.
`sim --one-process` writes its plan from the scenario;
[`crates/harness/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/crates/harness/README.md)
gives the plan's format and what a run leaves.
