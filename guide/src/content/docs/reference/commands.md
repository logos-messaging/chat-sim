---
title: Commands
description: The commands of tools/sim and tools/forks and harness-run at a glance, and where each is described in full.
---

Every command runs from the root of a chat-sim checkout. The `python3` ones need Python 3.9 or
newer and nothing beyond its standard library.

## sim

```sh
python3 tools/sim run SCENARIO --one-process PATH --out DIR
python3 tools/sim sweep SCENARIO --one-process PATH --out DIR --runs K [--from N]
python3 tools/sim compare [--write] EXPECTED OUT
python3 tools/sim matrix HEAD [--base DIR] [--asked FILE] [--out FILE]
nix run .#sim -- run SCENARIO --out DIR
```

| Command | Does |
|---|---|
| `run` | does the scenario once |
| `sweep` | does it `K` times, one after another, and prints one row per run; with `--from N`, runs `N` to `N+K-1` of a sweep |
| `compare` | checks each run of a sweep in `OUT` against the outcome `EXPECTED` holds for it; with `--write`, writes the sweep's outcomes to `EXPECTED` instead |
| `matrix` | pairs the runs under `HEAD` with those under `--base` by scenario and seed, gives each group its verdict, and writes the pairs and the verdicts as JSON: the matrix. `--asked FILE` lists the sweeps each side was asked for, so that a run nobody made counts as one with no outcome |

`--one-process PATH` is the runner, `harness-run`. `--out DIR` must not exist. The flake's app
`sim` builds `harness-run` from the checkout and adds `--one-process` to what follows `--`, so it
runs `run` and `sweep`. `compare` and `matrix` run with `python3`.

`run` and `sweep` exit 0 when every step was done and every run went as expected, and 1 when not.
They exit 2 when the command line or the scenario is refused. `compare` exits 0 when every run
matched its expected outcome and the sweep has a run for each.
[`tools/sim/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/tools/sim/README.md)
gives every flag, exit status and field.

## forks

```sh
python3 tools/forks report PATH... [--json FILE]
python3 tools/forks check --expect FILE PATH...
```

A `PATH` is a log, or a directory, which stands for every `.log` file directly in it. `report`
prints each conversation's verdict, its branches and where each installation stands. `check`
compares one conversation's report with an expectation. The verdict is the first of these that
holds:

| Verdict | When |
|---|---|
| `split at epoch N` | epoch N is the first with two states, and both follow one state at epoch N-1 |
| `split seen from epoch N` | epoch N is the first with two states, and the logs do not show one state at epoch N-1 that both follow |
| `no live member` | no installation is live |
| `not enough data` | no state was reported by two installations |
| `whole as far as seen` | a later epoch has a state that the logs do not link to the newest state of any live installation |
| `whole` | every live installation is at the tip |
| `whole, members behind` | a live installation is behind |

An installation is one member's client. It is live when its position is `tip`, `behind` or
`lineage unknown`. `report` exits 0 only when every conversation reads exactly `whole`: any other
verdict, `whole, members behind` among them, exits 1.
[`tools/forks/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/tools/forks/README.md#how-the-verdict-is-decided)
gives the rule in full, the exit statuses and the expectation's format.

## harness-run

```sh
cargo run --release -p harness --bin harness-run -- PLAN.json OUT_DIR
```

It runs every member of one group in one process on a virtual clock, and writes
`OUT_DIR/group.log` and `OUT_DIR/result.json`. `tools/sim` writes the plan from the scenario.
[`crates/harness/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/crates/harness/README.md)
gives the plan's format and what a run leaves.
