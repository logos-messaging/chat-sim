---
title: Commands
description: Every command of tools/sim and tools/forks, with its flags and exit status, the flake's app, and harness-run.
---

Every command runs from the root of a chat-sim checkout, with Python 3.9 or newer and nothing
beyond its standard library.

## sim

```sh
python3 tools/sim run SCENARIO --one-process PATH --out DIR
python3 tools/sim sweep SCENARIO --one-process PATH --out DIR --runs K [--from N]
python3 tools/sim compare [--write] EXPECTED OUT
python3 tools/sim matrix HEAD [--base DIR] [--asked FILE] [--out FILE]
nix run .#groupv2-simulation -- run SCENARIO --out DIR
```

| Command | Does |
|---|---|
| `run` | does the scenario once; a run that is not a grow has its log's fork report held to `expect` |
| `sweep` | does it `K` times, one after another, a row a run; with `--from N`, runs `N` to `N+K-1` of a sweep |
| `compare` | holds each run of a sweep in `OUT` to the outcome `EXPECTED` holds for it; with `--write`, writes the sweep's outcomes to `EXPECTED` instead |
| `matrix` | pairs the runs under `HEAD` with those under `--base` by scenario and seed, and writes the matrix as JSON |

`--one-process PATH` is the runner, `harness-run`. `--out DIR` must not exist. The flake's app
`groupv2-simulation` builds `harness-run` from the checkout and adds `--one-process` to what follows
`--`, so it runs `run` and `sweep`. `compare` and `matrix` run with `python3`.

| Exit | `run`, `sweep` | `compare` | `matrix` |
|---|---|---|---|
| 0 | every step was done, and every run went as expected | every run came out as expected, and the sweep has a run for each outcome | it wrote the matrix |
| 1 | a step was not done, or a report differs | a run did not, or the sweep and the file hold different numbers of runs | |
| 2 | the command line or the scenario is refused; nothing started | the sweep or `EXPECTED` cannot be read, or a run was not in one process | a run or `--asked` cannot be read |
| 128 + n | signal n interrupted it | | |

A grow goes as expected when nothing stopped its step: it expects no outcome.

### compare

An outcome is what a run came to, but its wall time: its seeds, the ids it drew for the group and
its members, the seconds of the clock, how it came out and what stopped it, and of a grow its
trunk's epoch, its first fork, how it ended, and the lines and members off the trunk. A run is
`same` where every field is as expected, and `moved` where any differs. A moved run prints the
outcome expected under it, as `was`.

### matrix

`matrix` reads each `<scenario>/<run>/run.json` under a directory, at any depth, and an `unbuilt`
marker for each run of a job that could not build chat-sim. A cell is a scenario and a seed. Its
`change` is `compare`'s, where both sides have an outcome, and its `d` the head's epochs without a
fork less the base's. Each group's rates get a result by the sign test, and each group the worst of
its rates' ([Your pull request's result](/chat-sim/guide/track/pull-request/#the-verdict)). Without
`--base`, a result is `unbuilt`, `no-outcome` or none, and the matrix holds each scenario's summary.
`--asked` names a JSON list of the sweeps asked of each side, so a run neither side uploaded is a
cell all the same.
[`tools/sim/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/tools/sim/README.md#a-matrix-of-a-revisions-runs-beside-its-bases)
gives every field.

## forks

```sh
python3 tools/forks report PATH... [--json FILE]
python3 tools/forks check --expect FILE PATH...
```

A `PATH` is a log, or a directory, which stands for every `.log` file directly in it. `report`
prints each conversation's report: its verdict, how many seats share a log, each branch with its
epoch, state and members, and each installation with its position. `--json` also writes it as
JSON. `check` holds one conversation's report to an expectation, and prints a line for each
difference.

| Exit | `report` | `check` |
|---|---|---|
| 0 | every conversation reads `whole`, and nothing is malformed | as expected |
| 1 | anything else | a difference, or a malformed record |
| 2 | the command line is wrong, a log cannot be read, or the JSON cannot be written | the command line is wrong, a log cannot be read, or the file is no expectation |

The verdict is the first of these that holds:

| Verdict | When |
|---|---|
| `split at epoch N` | epoch N is the first with two states, and both follow one state at epoch N-1 |
| `split seen from epoch N` | epoch N is the first with two states, and the logs show no one state at N-1 they follow |
| `no live member` | no installation is live |
| `not enough data` | no state was reported by two installations |
| `whole as far as seen` | a later state follows from no live installation's newest |
| `whole` | every live installation is at the tip |
| `whole, members behind` | a live installation is behind |

An installation is at the `tip`, `behind`, of `lineage unknown`, `removed`, `never joined` or of
`no data`, and live in the first three. Only `whole` is a clean bill.
[`tools/forks/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/tools/forks/README.md#how-the-verdict-is-decided)
gives the rule in full, and the expectation's format.

## harness-run

```sh
cargo run --release -p harness --bin harness-run -- PLAN.json OUT_DIR
```

It runs every member of one group in one process on a virtual clock, and writes
`OUT_DIR/group.log` and `OUT_DIR/result.json`. It exits 0 for a run, steps that stopped included,
and 2, with one line on standard error, when the plan cannot be read, the directory cannot be
written, or the arguments are not those two. `tools/sim` writes the plan from the scenario.
[`crates/harness/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/crates/harness/README.md)
gives the plan's format and what a run leaves.
