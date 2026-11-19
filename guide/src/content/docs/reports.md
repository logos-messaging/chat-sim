---
title: Reports
description: What CI runs on master and on each pull request, what the site's reports show, how to make a row again on your machine, what the site keeps of logos-chat's runs, the long run of each Sunday among them, the comment and the check on a logos-chat pull request, the pages of those runs, and the graph over its main.
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
(see [logos-chat's runs](#logos-chats-runs)), or when a run of `main` at the tip is queued or in
progress. A run of another suite does not hold it back: `simulate.yml` cancels a run only for a newer
one of the same suite at the same revision or pull request. A tip with a point at an older measurement
only is run again: what changed in chat-sim is the reason. A run of `main` at the tip that ended at the
measurement without its point holds the tip back too: the next would fail where it failed, so the
summary of the hourly run links it, and the tip runs again when it is started by hand.
A run of `main` handed a `pr` holds the tip back neither going nor ended: it ran at the pull request's
head and keeps no point, and a run started at the tip does not cancel it. Nor does a run of another
branch than chat-sim's default that ended: it keeps no point either.

Once each Sunday, in UTC, `measure-main.yml` starts the `long` suite at the tip as well: the two
scenarios of fifty epochs once each, and each scenario `tools/sim/expected/` holds but the goal's on
either group, as many runs as it holds outcomes, held to them. The first hourly run of the day that finds no run of `long`
handed no `pr` created that day, queued, in progress or ended, starts it, unless the tip has its long run
on the site at the measurement, `logos-chat/long/<tip>/<measurement>/long.json`, or a run of `long` is
queued or in progress. An hourly run that cannot list the runs created that day starts no `long`, and
`main` as it would. A run of `long` that failed is not started again that day: the summary of each later
hourly run links it, and it runs again when it is started by hand or on the next Sunday. A run of `long`
takes about three hours: its longest job sweeps `grow-waku-size-50.json`, whose three runs took 2 h 49
min on a CI run of master.

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
the runs of three of its suites under `logos-chat/` of the site: those of `main`, seeds 0 to 19 of each
`50-epochs-by-saro-waku-loss-` file at a commit of logos-chat's main, as a point of main, those of `pr`,
seeds 0 and 1 of the lossy ones, three a group, at a logos-chat pull request's head and at its base, and
those of
`long` at a commit of logos-chat's main, held to what is expected, as a long run. A pull request's run
keeps the matrix [`tools/sim matrix`](https://github.com/logos-messaging/chat-sim/blob/master/tools/sim/README.md#a-matrix-of-a-revisions-runs-beside-its-bases)
wrote of the runs, a point the matrix's cells and summary, and a long run what each scenario came to and
the output of each `compare`, and each keeps each run's `run.txt`, `report.txt` and `run.json`, or, of a
job that could not build chat-sim, the build's log, `build.log`, which the job uploads beside each run's
`unbuilt` marker. A run's `report.json`, ten times the rest, stays in the workflow run's artifacts. Each
has a page drawn from what it keeps (see [The pages of logos-chat's runs](#the-pages-of-logos-chats-runs)),
and the comment links it.

A `pr` run whose base has its point on `gh-pages` at the measurement runs nothing at the base: a point's
seeds 0 and 1 are runs 1 and 2 of its sweeps, the runs a pull request asks of the base, so the point's
cells stand in for the base's, outcomes and ids as the point holds them. A point at another measurement
never stands in, and nor does one that has no outcome at one of those runs, as where its job uploaded
nothing, or one the plan cannot read: the jobs at the base run them, and a warning on the run says why.
When a point stands in, the matrix names it as `base_point`, and the pull request's directory holds
no `base/`: the point's own directory holds those runs' text while it keeps it. The comment places the
head's runs among main's twenty seeds of each group's rate at the point at the base, or else at the
newest point
at the measurement of the base's hundred newest commits that logos-chat's commits API lists, passing over
one the plan cannot read, with a warning that says why. The matrix
holds that point as `spread`: a re-rolled cell says whether its epochs are within that point's runs or
below the lowest, and the title of a re-rolled verdict how many fell below. With no such point, the
comment has no such column.

| Path | Holds |
|---|---|
| `logos-chat/main/<sha>/<measurement>/point.json` | the point of the logos-chat commit `<sha>`, measured by the chat-sim commit `<measurement>` |
| `logos-chat/main/<sha>/<measurement>/<scenario>/<run>/` | the text of each of the point's runs, `<run>` its number in a sweep of its scenario |
| `logos-chat/main/<sha>/<measurement>/index.html` | the point's page |
| `logos-chat/long/<sha>/<measurement>/long.json` | the long run of the logos-chat commit `<sha>`, measured by the chat-sim commit `<measurement>` |
| `logos-chat/long/<sha>/<measurement>/<scenario>/compare.txt` | what `compare` printed of the scenario's runs, held to what `tools/sim/expected/` holds of it |
| `logos-chat/long/<sha>/<measurement>/<scenario>/<run>/` | the text of each of the long run's runs, `<run>` its number in the sweep of its scenario |
| `logos-chat/long/<sha>/<measurement>/index.html` | the long run's page |
| `logos-chat/index.html` | the graph over main, drawn again from every point's `point.json` each time a point or a long run is written (see [Over main](#over-main)) |
| `logos-chat/pr-N/matrix.json` | the matrix of the newest run for pull request N, the head's runs beside the base's, or beside main's point at the base |
| `logos-chat/pr-N/meta.json` | what ran: the pull request's number as `pr`, the `revision` and the `base`, `null` without one, chat-sim's commit as `chat_sim`, the `measurement`, the workflow run's `run_url` and `run_number`, and the `time` it was written, in UTC |
| `logos-chat/pr-N/head/<scenario>/<run>/`, `logos-chat/pr-N/base/<scenario>/<run>/` | the text of each run at the head and at the base; no `base/` when main's point stood in |
| `logos-chat/pr-N/index.html` | the report of the run |

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
| `cells` | each run's cell as the matrix holds it, but `below`, which only a pull request's matrix says: the scenario, its group, `v2` or `v3`, the seed, the run, the loss and its rate, the run's outcome as `compare --write` writes one, the epochs without a fork, how the run ended, and whether its job could not build chat-sim. A point has no base, so each field of a base is `null`, and `unbuilt.base` is `false` |
| `summary` | of each scenario, a group at a rate, its group, how many runs and how each ended, the lowest, median and highest epochs without a fork, and the median of the members off the trunk at the end |

A point that names no group, in its cells or its summary, holds GroupV2's runs alone, and is read and
drawn as GroupV2's.

A `long.json` holds what the long run's page draws:

| Field | Holds |
|---|---|
| `logos_chat`, `measurement`, `chat_sim`, `time`, `run_url`, `run_number` | as a `point.json` holds them |
| `scenarios` | each scenario of the suite, in the order it runs them: its name as `scenario`; as `compare`, its `verdict` by the status `compare` exited with, `as expected`, `not as expected` or `no outcome`, and the line it ended with as `counted`, or `null` where it did not run, as for a scenario chat-sim holds no outcome of; and its `runs`, each its number as `run`, its `seed`, that of the scenario's file plus the run's number less one, whether its job could not build chat-sim as `unbuilt`, and its `outcome` as `compare --write` writes one, or `null` |

The points are in the order of their workflow runs' numbers, which GitHub gives in the order the runs
were started and a re-run keeps. A point follows the point of the site of the highest run number below
its own but the one of its own commit and measurement, and is refused unless logos-chat's compare API
says its commit is ahead of that point's, or is that commit. A point, a long run and a pull request's
directory are those of the newest run to publish them, by run number: a publish from an older run is kept
out, and a re-run replaces what was there whole. Only a run of chat-sim's default branch keeps a point or
a long run, and none keeps one whose jobs read no run at the revision, built or not, as when logos-chat
has no such commit. A `long` run handed a base holds its runs to the base's, not to what is expected, and
a `main` or `long` run handed a `pr` ran at the pull request's head, not at a commit of main: neither is
kept.
A pull request's directory goes once the pull request is no longer open: the weekly run of Prune Pages
removes each `logos-chat/pr-N/` whose number logos-chat's pulls API does not list among its open pull
requests, and a dispatch of Prune Pages handed a `pr` removes that pull request's alone.

The newest thirty points by run number keep their runs' text: once a point is written, the runs' text of
each point older than those is removed, and its `point.json` stays. A point's text is about 10 MB, 5 MB
a group, and Pages holds a site of 1 GB. The newest fifteen long runs keep their text, their runs' and
their `compare` output, and each older one its `long.json` alone: a long run takes about 10.7 MB while it
keeps its text, 7.1 MB of it the three runs of `grow-waku-size-50` and 1 MB its page, so fifteen take
about half the room of the thirty points. Every publish is a commit on `gh-pages`, so the weekly run of Prune Pages also
rewrites the branch as one commit of its tree as it stands, and the text a point or a long run lets go
leaves the branch's history too.

## The comment and the check

A `pr` run of `simulate.yml` handed a `pr` posts its comment on that logos-chat pull request as
`chat-sim[bot]`, the GitHub App chat-sim: it edits the App's oldest comment there, or writes one where
the App has none, so a pull request holds one comment of chat-sim's. A `pr` run handed `check_run`, the
id of a check run the App made on logos-chat, moves that check to in progress once its runs are planned,
with the workflow run as its details and the runs at the head as its title, `12 runs in progress`, and
completes it once the comment is posted, with the comment as its summary and the pull request's report
page as its details, or the workflow run where the report has no address. Each group has a verdict of its
own, over its runs alone, and its own conclusion and title:

| The verdict | Conclusion | A group's title |
|---|---|---|
| `same` | neutral | same as its base, 6 of 6 runs |
| `better` | success | better in 1 of 6 runs |
| `worse` | failure | worse in 2 of 6 runs |
| `hint` | neutral | worse in 1 of 6 runs, a hint |
| `mixed` | neutral | mixed: 1 better, 1 worse of 6 |
| `re-rolled` | neutral | the draws changed, no comparison; 1 of 6 below main's worst seed |
| `unbuilt` | neutral, or failure once required | does not build against this commit |
| `no-outcome` | neutral, or failure once required | no outcome in 1 of 6 runs |
| `no-base` | neutral | no base, no comparison |

The check's title is each group's after the group's name, GroupV2's first:
`GroupV2 same as its base, 6 of 6 runs; GroupV3 worse in 2 of 6 runs`. Its conclusion is a failure where a
group's is, else a success where a group's is, else neutral, so that one group's runs standing higher do
not hide the other's standing lower. Where every group's verdict is `unbuilt`, or every group's
`no-base`, the title says it once.

`re-rolled`'s title says how many runs fell below main's worst seed where the run has main's spread to
place them among, and stops before the semicolon where it has none. `no-base`'s title is
`does not build at the base, no comparison` where chat-sim did not build at the base, and
`no outcome at the base, no comparison` where no run at the base has one.

With chat-sim's repository variable `CHAT_SIM_CHECK_REQUIRED` set to `true`, for when the check is made
required on logos-chat, a head chat-sim does not build at, or a run with no outcome at the head, fails it;
any value but `true` or `false` fails the comment job before the check completes. `no-base` stays neutral
either way: what it lacks is the base's, which the pull request cannot give. The comment and the check go
out before the publish job writes the run's page: until it and Pages have, their link to the report opens
the page of the pull request's previous run, or no page yet on its first. The plan job and the comment job
each mint a token of their own with `actions/create-github-app-token`, for logos-chat alone and the
permissions their steps need, from the repository variable `CHAT_SIM_APP_CLIENT_ID` and the repository
secret `CHAT_SIM_APP_PRIVATE_KEY`; the run jobs hold none, and no job hands its token to another. Without
the variable or the secret, each says so on the run and posts nothing, and the comment is in the run's
summary alone. A run that is cancelled leaves the check in progress, and so does one whose comment job
failed, until that job runs again.

## The pages of logos-chat's runs

The publish job draws the page of each run it keeps, `index.html` in its directory, from what it keeps
there. The comment links it: a `pr` run's for a pull request, a `main` run's point, and a `long` run's
page, where the publish job keeps one.

The report of a pull request's run, `logos-chat/pr-N/`:

| Part | Shows |
|---|---|
| The head | the pull request's number, the head and its merge base, chat-sim's commit, links to the pull request and to the workflow run, and when, in UTC |
| The verdict | the comment's verdict line, in its words; where main's point stood in for the base, its words link the point's page, or its `point.json` until the point has a page |
| The matrix | of each group, under its name, GroupV2 first: the head's cells and the base's side by side, a row a rate and a column a seed, coloured as [the newest point](#over-main) is; an outline marks a cell of the head that stands lower (`worse`) or higher (`better`) than the base's. Where the matrix holds a `spread`, main's runs of each rate beside them in the comment's words (`2 whole, 18 to 50`), and a strip for each rate placing the head's two runs among main's twenty seeds of that point by their epochs without a fork, or a line that says the point holds no runs of the group |
| The cells | a fold for each cell, named by its group, rate and seed, GroupV2's first and within a group open and first where it moved: of each side, the run's verdict and what it came to, the lines of its `grow` from `run.json` (each line's kind, the line it forked from, the epoch it forked at, the epoch it reached and its members), who fell behind and at which epoch, who was behind at the end and who never joined, and its `run.txt` and `report.txt`; then the command that runs the cell again, as the comment's fold has it. Where main's point stood in, the base's side is the point's run, with the point's page linked, and its text shown from the point's directory while the point keeps it |

Where chat-sim did not build at the head, the build's log takes the place of the matrix and the cells.
Every job built the same commit, so the page shows the first job's.

The page of a point, `logos-chat/main/<sha>/<measurement>/`:

| Part | Shows |
|---|---|
| The head | the logos-chat commit, the chat-sim commit that ran and the one the runs measure, the workflow run and when; the pull requests the point merged, and the point it follows, linking its page |
| The matrix | the point's cells, a heatmap a group under its name, a row a rate and a column a seed, as the newest point is drawn |
| The rates | of each group's rate, a row with the group and the loss: the runs and how they ended, the lowest, median and highest epochs without a fork, the median of the members off the trunk at the end, and the shortest and the longest wall time of a run, from `run.json` |
| The runs | a fold a group's rate, named by both: a row a run, with its seed, its epochs without a fork, how it ended, what its `grow` came to and its wall time; and a fold for each run, with its lines, its text and the command that runs it again |

Where a job of the point could not build chat-sim, the page shows the build's log once, and each run that
could not build links its own. A page runs no script and loads nothing from outside its directory, and
what a run, a pull request's title or an API says reaches it as text.

When a point past the newest thirty loses its runs' text, its page is drawn again from its
`point.json` alone, and so is the report of a pull request whose base it stood in for, so that no link
leads to text the site no longer holds: the command under each run makes it again. A point kept before
its page was drawn gets one the next time a point is written; until then a report it stood in for links
its `point.json`, and the report is drawn again with the point's page.

The page of a long run, `logos-chat/long/<sha>/<measurement>/`:

| Part | Shows |
|---|---|
| The head | the logos-chat commit, the chat-sim commit that ran and the one the runs measure, the workflow run and when |
| Held to what is expected | a row a scenario, as the comment has it: its runs, each run's verdict, and the line `compare` ended with, or `nothing held` where chat-sim holds no outcome of the scenario; each row links its section |
| A section for each scenario | `compare`'s verdict and its output; a row a run, with its seed, how it ended, what its `grow` came to and its wall time; and a fold for each run, with its lines, its text and the command that runs it again |

Where chat-sim did not build, the page shows the build's log once, and each run links its own. When a
long run past the newest fifteen loses its text, its page is drawn again from its `long.json` alone: the
verdicts, the rows and the commands stay, and no link leads to text the site no longer holds.

## Over main

[Over main](../../logos-chat/) draws the points of main, the oldest at the left, in the order of their
run numbers. The publish job draws it again from every `point.json` of the site once it has written a
point or a long run, and leaves out, with a warning, a `point.json` it cannot read. Above the charts it
links the page of the newest long run by run number, and draws nothing of it: the long suite measures
other things than the goal. It lives beside this guide and not
in it, since a publish of the guide replaces `guide/` whole. Every number it draws is one a `point.json`
holds. Each chart draws GroupV2 and GroupV3 apart, each under its name, GroupV2 first, and a group from
the first point that ran it. A rate is a group's loss, so a scenario renamed at the same loss goes on as
its line, and each rate of a group has a blue of its own, the heavier the loss the darker. A point that
names no group is GroupV2's.

| Part | Shows |
|---|---|
| Epochs without a fork | of each group, a panel a rate: of each point, the median of the epochs its runs went without a fork, the first split's or else the trunk's, as a line, and the band from the lowest run to the highest, from `summary`'s `epochs` |
| Runs whole at 50 epochs | of each group, a line a rate of how many runs ended whole, `summary`'s `whole`, toward the goal of every run, broken at a point where none of the rate's runs had an outcome (`no-outcome` is `runs`), as when chat-sim did not build; under the dates the axis shows, the pull requests the point merged |
| The newest point | the newest point's `cells`, a heatmap a group, a row a rate and a column a seed: a cell's colour is the epochs its run went without a fork, in bins of ten and 50 alone; a dot marks a run that ended whole, a ring one that ended `behind`, and the cell's hover text is the run's `came_to` |
| Members off the trunk at the end | of each group, a line a rate of the median of the members behind at the end, on a line not the trunk or never joined, `summary`'s `members_off_trunk`; a group's left out until a rate of it with loss has had every run whole at a point |
| The points | each point, the newest first: when it was written, its logos-chat commit, the pull requests it merged, each group's rate's runs whole, `-` where the point ran none of the group, what moved against the point before it, the chat-sim commit it measures, and links to its page and its workflow run |

Two marks tell a step that is not logos-chat's: a dashed line on a point that measures another chat-sim
commit than the point before it, and an open marker on a point whose `previous.rerolled` is `true`,
whose runs drew other ids than those of the point it follows, so that every seed's run is another run
and moved whether or not logos-chat did. What moved names, of each group's rate, after the group's name,
the runs whole and the median epochs without a fork where they changed, or that none of its runs had an outcome, and either mark. A
marker's hover text names the point, the pull requests it merged and the value. The page runs no script.

## The guide

This guide is built from `guide/` in the repository, with Astro Starlight. A push to master that changes
`guide/` publishes it under `guide/` of the site, and a pull request that changes it gets a build alone.
To read it as you change it:

```sh
cd guide
npm ci
npm run dev
```
