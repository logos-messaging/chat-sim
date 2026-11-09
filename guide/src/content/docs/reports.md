---
title: Reports
description: What CI runs on master and on each pull request, what the site's reports show, how to make a row again on your machine, and what the site keeps of logos-chat's runs.
---

CI runs on every push to master and on every pull request to master. Two of its jobs run simulations in
one process, and a third publishes what they came to beside this guide, under [reports](../../reports/):
a page for master, and one for each open pull request. A pull request's simulations run on its head
commit, not on its merge into master.

| Job | Runs |
|---|---|
| Sim in one process | `50-epochs.json` and `50-epochs-waku.json`, once each |
| Sim held to what is expected | for each file in `tools/sim/expected/`, a sweep of the scenario of that name in `tools/sim/scenarios/`, as many runs as the file holds outcomes, held by `tools/sim compare` to the file |
| Publish the report to GitHub Pages | the report of the CI run, from the text the two jobs upload |

The report is published when a simulation job fails as well: that is the run whose report is wanted
most. The output of each `compare` is also in the summary of its job on the CI run.

Every hour, `measure-main.yml` starts `simulate.yml`'s `main` suite at logos-chat main's tip: seeds 0 to
19 of each `50-epochs-by-saro-waku-loss-` scenario. It starts nothing when the tip already has its point
on the site, `logos-chat/main/<tip>/<measurement>/point.json`, at the chat-sim commit a run measures
(see [logos-chat's runs](#logos-chats-runs)), or when a run at the tip, of any suite, is queued or in
progress. A tip with a point at an older measurement only is run again: what changed in chat-sim is the
reason. A run of `main` at the tip that ended at the measurement without its point holds the tip back
too: the next would fail where it failed, so the summary of the hourly run links it, and the tip runs
again when it is started by hand.
A run handed a `pr` holds the tip back neither going nor ended: it ran at the pull request's head and
keeps no point, and a run started at the tip does not cancel it. Nor does a run of another branch than
chat-sim's default that ended: it keeps no point either.

## The list

`reports/` has a row for master, then one for each open pull request, the highest number first:

| Column | Says |
|---|---|
| What ran | `master`, or `#N` and the pull request's title |
| Commit | the commit that ran, by its short sha, linking to the commit |
| When (UTC) | when CI wrote the report |
| Sweeps | how many of the scenarios CI sweeps came out as expected, such as `10 of 10 as expected` or `9 of 10 as expected` |
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
scenario CI sweeps, what each of its runs came to at the revision of logos-chat that chat-sim pins in
`Cargo.toml`: the seeds, the ids of the group and of its members, the verdict, the members at the tip of
those seated, the seconds of the virtual clock, how the run came out and what stopped its steps, and of a
`grow` step how far the trunk got and how many ended off it. CI sweeps each scenario as many runs as its
file holds, from seed 0: two of each `50-epochs-by-saro-waku-loss-` file, three of each `grow-waku-` one.
`compare` holds each run of the sweep to the outcome expected of it, and names each change by where the
run stands against it, in the order [Simulations](../simulations/#held-to-what-is-expected) gives:

| Change | Means |
|---|---|
| `same` | the outcome is the one expected |
| `better` | the run stands higher than the one expected |
| `worse` | the run stands lower |
| `re-rolled` | both runs drew ids, and the run drew others than the one expected, the group's or the members', so it is another run of that seed and neither better nor worse; a run that read no result drew none and is `worse` |
| `changed` | neither: the run stands where the one expected did, in an outcome that differs all the same, such as in the seconds of its clock |

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

`--runs` is as many as the scenario's file in `tools/sim/expected/` holds outcomes: three here, two for a
`50-epochs-by-saro-waku-loss-` one.

`compare` prints the rows of the page's section, and each run's `report.txt` is the page's. A run of fifty
epochs:

```sh
nix run .#groupv2-simulation -- run tools/sim/scenarios/50-epochs.json --out tmp/50-epochs
```

Its `report.txt` is the page's, and so is its `run.txt` but for the wall time and the load average in its
head. A sweep of `grow-waku-size-50.json` takes the runner over half an hour.

## logos-chat's runs

The workflow `simulate.yml` runs chat-sim at a logos-chat revision it is handed, and its publish job keeps
the runs of two of its suites under `logos-chat/` of the site: those of `main`, seeds 0 to 19 of each
`50-epochs-by-saro-waku-loss-` file at a commit of logos-chat's main, as a point of main, and those of
`pr`, seeds 0 and 1 of the three lossy ones at a logos-chat pull request's head and at its base. A pull
request's run keeps the matrix [`tools/sim matrix`](https://github.com/logos-messaging/chat-sim/blob/master/tools/sim/README.md#a-matrix-of-a-revisions-runs-beside-its-bases)
wrote of the runs and a point the matrix's cells and summary, and each keeps each run's `run.txt`,
`report.txt` and `run.json`. A run's `report.json`, ten times the rest, stays in the workflow run's
artifacts.

| Path | Holds |
|---|---|
| `logos-chat/main/<sha>/<measurement>/point.json` | the point of the logos-chat commit `<sha>`, measured by the chat-sim commit `<measurement>` |
| `logos-chat/main/<sha>/<measurement>/<scenario>/<run>/` | the text of each of the point's runs, `<run>` its number in a sweep of its scenario |
| `logos-chat/pr-N/matrix.json` | the matrix of the newest run for pull request N, the head's runs beside the base's |
| `logos-chat/pr-N/meta.json` | what ran: the pull request's number as `pr`, the `revision` and the `base`, `null` without one, chat-sim's commit as `chat_sim`, the `measurement`, the workflow run's `run_url` and `run_number`, and the `time` it was written, in UTC |
| `logos-chat/pr-N/head/<scenario>/<run>/`, `logos-chat/pr-N/base/<scenario>/<run>/` | the text of each run at the head and at the base |

The measurement is the newest chat-sim commit that touches what a run measures: `crates/`,
`Cargo.toml`, `Cargo.lock`, `.cargo/`, `rust-toolchain.toml`, `flake.nix`, `flake.lock`,
`tools/forks/forks/`, `tools/sim/sim/` or `tools/sim/scenarios/`. A commit to the guide or to the pages
moves no point, and one that moves the measurement gives a logos-chat commit a point of its own beside the
one before.

A `point.json` holds what a page of the point draws:

| Field | Holds |
|---|---|
| `logos_chat`, `measurement`, `chat_sim` | the logos-chat commit, the chat-sim commit the runs measure, and the chat-sim commit that ran them |
| `time`, `run_url`, `run_number` | when the point was written, in UTC, and the workflow run's page and number |
| `merged` | the logos-chat pull requests merged since the point it follows, each its `number` and `title`, from the commits logos-chat's compare API gives between the two: a squash merge's commit is titled `<title> (#N)`, a merge commit's `Merge pull request #N from <branch>` above the title |
| `previous` | the point it follows, its `logos_chat` and `measurement`, and as `rerolled` whether a run drew other ids than that point's run of its scenario and seed, the group's or the members'; `null` for the first point |
| `cells` | each run's cell as the matrix holds it: the scenario, the seed, the run, the loss and its rate, the run's outcome as `compare --write` writes one, the epochs without a fork, how the run ended, and whether its job could not build chat-sim. A point has no base, so each field of a base is `null`, and `unbuilt.base` is `false` |
| `summary` | of each rate, how many runs and how each ended, the lowest, median and highest epochs without a fork, and the median of the members off the trunk at the end |

The points are in the order of their workflow runs' numbers, which GitHub gives in the order the runs
were started and a re-run keeps. A point follows the point of the site of the highest run number below
its own but the one of its own commit and measurement, and is refused unless logos-chat's compare API
says its commit is ahead of that point's, or is that commit. A point and a pull request's directory are
those of the newest run to publish them, by run number: a publish from an older run is kept out, and a
re-run replaces what was there whole. Only a run of chat-sim's default branch keeps a point, and none
keeps one whose jobs read no run at the revision, built or not, as when logos-chat has no such commit.
A `main` run handed a `pr` ran at the pull request's head, not at a commit of main: it is not kept.

The newest thirty points by run number keep their runs' text: once a point is written, the runs' text of
each point older than those is removed, and its `point.json` stays. A point's text is about 5 MB, and
Pages holds a site of 1 GB. Every publish is a commit on `gh-pages`, so the weekly run of Prune Pages also
rewrites the branch as one commit of its tree as it stands, and the text a point lets go leaves the
branch's history too.

## The guide

This guide is built from `guide/` in the repository, with Astro Starlight. A push to master that changes
`guide/` publishes it under `guide/` of the site, and a pull request that changes it gets a build alone.
To read it as you change it:

```sh
cd guide
npm ci
npm run dev
```
