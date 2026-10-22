---
title: Reports
description: What CI runs on master and on each pull request, what the site's reports show, and how to make a row again on your machine.
---

CI runs on every push to master and on every pull request to master. Two of its jobs run simulations in
one process, and a third publishes what they came to beside this guide, under [reports](../../reports/):
a page for master, and one for each open pull request. A pull request's simulations run on its head
commit, not on its merge into master.

| Job | Runs |
|---|---|
| Sim in one process | `50-epochs.json` and `50-epochs-waku.json`, once each |
| Sim held to what is expected | for each file in `tools/sim/expected/`, a sweep of three seeds of the scenario of that name in `tools/sim/scenarios/`, held by `tools/sim compare` to the file |
| Publish the report to GitHub Pages | the report of the CI run, from the text the two jobs upload |

The report is published when a simulation job fails as well: that is the run whose report is wanted
most. The output of each `compare` is also in the summary of its job on the CI run.

## The list

`reports/` has a row for master, then one for each open pull request, the highest number first:

| Column | Says |
|---|---|
| What ran | `master`, or `#N` and the pull request's title |
| Commit | the commit that ran, by its short sha, linking to the commit |
| When (UTC) | when CI wrote the report |
| Sweeps | how many of the scenarios CI sweeps came out as expected, such as `6 of 6 as expected` or `5 of 6 as expected` |
| the last two, with no heading | `report`, a link to the report page, and `CI run`, a link to the CI run |

A row and its page are those of the newest CI run of master, or of the pull request, to publish a
report: newest by run number, which a re-run keeps. A push to a pull request cancels its older run if
that is still going. A pull request's row and page go when it closes, and a weekly sweep removes any
that a late run wrote again. A pull request from a fork gets neither: its token cannot push to the site.

## A report page

| Part | Shows |
|---|---|
| The head | what ran (`master`, or `#N` and the pull request's title), the commit, the CI run, and when, in UTC |
| Held to what is expected | a row for each scenario CI sweeps: `as expected` when `compare` exited 0, `not as expected` when it exited 1, and `no outcome` when it exited otherwise or did not run. Each row links to its section |
| A section for each scenario | the output of `compare`, the sweep's `sweep.txt`, and each run's `run.txt` and `report.txt`, in a fold for the run; or that the job uploaded nothing |
| Fifty epochs | the two runs of Sim in one process, `50-epochs.json` and `50-epochs-waku.json`, each held to what its scenario expects: each run's `run.txt` and `report.txt`; or, when nothing of a run was uploaded, that it did not run or wrote nothing |

Every file a page shows is also beside it, and linked, to open as it stands. A file the job did not
upload says so where it would stand. A page holds text alone: no `group.log`, nothing the runner wrote,
nothing large. It runs no script, and what a run or a pull request's title says reaches it as text.

[Simulations](../simulations/) explains what a `run.txt`, a `sweep.txt` and the rows of `compare` say, and
[Read the report](../read-the-report/) a `report.txt`.

## As expected

A run in one process is the same run every time from its seeds, on Linux, so a run that comes out other
than before was changed by a revision of logos-chat or of chat-sim. `tools/sim/expected/` holds, for each
scenario CI sweeps, what each of its three runs came to at the revision of logos-chat that chat-sim pins
in `Cargo.toml`: the seeds, the verdict, the members at the tip of those seated, the seconds of the
virtual clock, how the run came out and what stopped its steps. `compare` holds each run of the sweep to
the outcome expected of it, and names each change:

| Change | Means |
|---|---|
| `same` | the outcome is the one expected |
| `better` | no split where there was one, a split at a later epoch, whole where members were behind, or, at the same verdict, more members at the tip |
| `worse` | the reverse |
| `changed` | neither: the same verdict and members at the tip, in an outcome that differs all the same, such as in the seconds of its clock |

A scenario is as expected when every run is `same` and the sweep has a run for each outcome expected. A
change that moves how a run comes out has its outcomes written with
`python3 tools/sim compare --write EXPECTED OUT`, and the file's diff shows the change.

## Why a run repeats

On Linux, `harness-run` stands in for libc's `getrandom` with a generator seeded by the scenario's `seed`,
and for `clock_gettime` with the virtual clock, and runs openmls on one thread. Every random number of a
run, the ids of the group and its members among them, follows from the seed, and which commit the group
applies follows from the ids. The seed of `network` fixes what each member's model does with each frame.
Two runs of one scenario write the same `group.log` byte for byte. CI runs on Linux. On macOS the ids come
from the OS, and a run can come out otherwise from one time to the next.

## Make a row again

On Linux, at the commit of the row:

```sh
nix run .#groupv2-simulation -- sweep tools/sim/scenarios/grow-waku-loss-5.json --runs 3 --out tmp/grow-waku-loss-5
python3 tools/sim compare tools/sim/expected/grow-waku-loss-5.json tmp/grow-waku-loss-5
```

`compare` prints the rows of the page's section, and each run's `report.txt` is the page's. A run of fifty
epochs:

```sh
nix run .#groupv2-simulation -- run tools/sim/scenarios/50-epochs.json --out tmp/50-epochs
```

Its `report.txt` is the page's, and so is its `run.txt` but for the wall time and the load average in its
head. A sweep of `grow-waku-size-50.json` takes the runner over half an hour.

## The guide

This guide is built from `guide/` in the repository, with Astro Starlight. A push to master that changes
`guide/` publishes it under `guide/` of the site, and a pull request that changes it gets a build alone.
To read it as you change it:

```sh
cd guide
npm ci
npm run dev
```
