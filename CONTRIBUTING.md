# Contributing to chat-sim

## What CI runs

`ci.yml` runs on every push to master and on every pull request to master:

| Job | Runs |
|---|---|
| Lint | that `flake.lock` and `Cargo.lock` pin one revision of logos-chat, `cargo fmt --check`, and `cargo clippy` with warnings as errors |
| Test | `cargo test --release --workspace` in the dev shell, on Linux and macOS |
| Tools | the tests of `tools/forks`, `tools/sim` and `.github/scripts` |
| Ask whether a path the sims depend on changed | `simulate.py changed`, against the pull request's base or the commit before the push |
| Sim in one process | `seat-50-post-groupv2.json` and `seat-50-post-groupv2-waku-loss-0pct.json`, each held to what its scenario expects |
| Sim held to what is expected | for each file in `tools/sim/expected/`, a sweep of the scenario of that name, held by `tools/sim compare` to the file |
| Publish the report to GitHub Pages | what the two sim jobs came to, under `reports/` of the site |

The sim jobs and the publish run only when a held path changed, or when the check has no commit to
compare with, as after a force push. The publish never runs for a pull request from a fork. The
guide's [chat-sim's own reports](https://logos-messaging.github.io/chat-sim/guide/reports/) says
what a report holds and how long it is kept. `guide.yml` builds the guide when `guide/` changes, and
publishes it from master.

## The paths a run depends on

`MEASURED` in `.github/scripts/simulate.py` lists what a run's outcome can depend on: `crates/`,
`Cargo.toml`, `Cargo.lock`, `.cargo/`, `rust-toolchain.toml`, `flake.nix`, `flake.lock`,
`tools/forks/__main__.py`, `tools/forks/forks/`, `tools/sim/__main__.py`, `tools/sim/sim/` and
`tools/sim/scenarios/`. The newest commit that touches one is the measurement. Merged, it gives the
tip of logos-chat's main a new point of its own, beside the one before.

`HELD` adds `tools/sim/expected/` and `.github/workflows/ci.yml`: a change to any held path runs
chat-sim's own sims. The suites, `GROW_3_ADDERS` and `RATES` in `simulate.py`, are in neither, so
a change to them moves no measurement.

## A scenario's name

A file in `tools/sim/scenarios/` is named for its steps, its group and its network, such as
`grow-3-adders-groupv3-waku-loss-0.5pct.json`. The guide's
[The scenarios](https://logos-messaging.github.io/chat-sim/guide/understand/scenarios/) reads a
name part by part. A test of `tools/sim` writes each file's name from what the file says, and fails
on any other name. A scenario of steps that no file has yet gets its name in that test.

## What is expected

`tools/sim/expected/<scenario>.json` holds the outcome of each run CI sweeps of that scenario, at
the revision of logos-chat that `Cargo.toml` pins: two runs of each `grow-1-adder` file, three of
each `grow-3-adders` one. A change that moves how a run comes out, such as a new pin, has its
outcomes written again, and the file's diff shows what moved:

```sh
nix run .#groupv2-simulation -- sweep tools/sim/scenarios/grow-3-adders-groupv2-waku-loss-5pct.json --runs 3 --out tmp/grow-3-adders-groupv2-waku-loss-5pct
python3 tools/sim compare --write tools/sim/expected/grow-3-adders-groupv2-waku-loss-5pct.json tmp/grow-3-adders-groupv2-waku-loss-5pct
```

`--runs` is as many as the file holds. Without `--write`, `compare` holds the sweep to the file and
prints a row a run. A run is the same on Linux whenever it is made, so run the sweeps there.

## Tests

From the repository root:

```sh
python3 -B -m unittest discover -s tools/forks
python3 -B -m unittest discover -s tools/sim
python3 -B -m unittest discover -s .github/scripts
nix develop -c cargo test --release --workspace
```

The tools need Python 3.9 or newer and nothing beyond its standard library. The tests of `tools/sim`
run a stand-in for `harness-run`, so they build nothing.

## The guide

The guide is a Starlight site in `guide/`. It needs Node 22.23.4 or newer:

```sh
cd guide
npm ci
npm run dev
```

Every link between the guide's pages is written with its base, `/chat-sim/guide/`, such as
`/chat-sim/guide/track/main/#the-long-runs`. `npm run build` checks each with
`starlight-links-validator`, and fails on a page or a heading that is not there. The pages
`.github/scripts/pages.py` draws link headings of the guide too, and `test_pages.py` holds each of
those links to a heading, so renaming one fails the Tools job.
