---
title: logos-chat main over time
description: How chat-sim measures each commit of logos-chat's main, what a point holds, how to read the graph over main and the page of a change, and the long run of each Sunday.
---

Each push to logos-chat's `main` has its workflow start chat-sim's `main` suite at the pushed
commit. Every hour, at 17 minutes past, chat-sim's `measure-main.yml` starts it at `main`'s tip as
well, when the tip has no point at the current measurement, no run of it is going, and none ended at
that measurement without its point. The suite runs the goal on both groups, seeds 0 to 2 at no loss
and seeds 0 to 19 at 0.5, 1 and 5%, ten runs a job, and has no base. What it keeps is a **point** of
`main`: `logos-chat/main/<sha>/<measurement>/` of the site, with its `point.json` and its page.

## The measurement

A point is of a logos-chat commit and a chat-sim commit, the measurement: the newest chat-sim
commit that touches a path a run depends on. Those paths are `crates/`, `Cargo.toml`, `Cargo.lock`,
`.cargo/`, `rust-toolchain.toml`, `flake.nix`, `flake.lock`, `tools/forks/__main__.py`,
`tools/forks/forks/`, `tools/sim/__main__.py`, `tools/sim/sim/` and `tools/sim/scenarios/`.

A chat-sim commit that touches one of them moves the measurement. `main`'s tip then gets a point
at the new measurement within the hour, beside the one before it. A commit to the guide or to the
pages moves nothing. Only a run of chat-sim's default branch keeps a point.

## The graph over main

[Over main](/chat-sim/logos-chat/) answers three questions, in this order.

| Section | Shows |
|---|---|
| Now | of the newest commit, each group's epochs before the first fork at each rate of loss: the median of the rate's runs, as a number and a meter toward the goal of 50 |
| Over main's commits | a panel a rate of loss, both groups in each: the median as a line and the middle half of the runs as a band, with a marker on each commit found to change a run of the group |
| What changed | a row a commit, the newest first: what each group's runs came to against the commit before it, and a link to the commit's runs |

A commit of `main` is compared with the commit before it as a pull request is with its base
([The verdict](/chat-sim/guide/track/pull-request/#the-verdict)): seed by seed, both at one
measurement, by the same sign test, at the rates the newest commit ran. "Nothing moved" says every
seed with an outcome at both commits came out the same. "Moved, within chance" says runs differ,
with no lean to earlier or later beyond chance. A run the commit before has no outcome of is not
compared, and the row says how many there are. A commit found to change a run links
[the page of its change](#the-page-of-a-change).

Under the numbers, one line holds the newest commit to the first commit of the test. A group says
"nothing moved" where each commit since had every run of it compared and changed none. Any other
group is tested on each seed's epochs at the two commits, so a drift over several commits shows
there though no single commit shows it.

The page draws one test. A chat-sim commit that moves the measurement has `main`'s tip run again.
Where every seed's first fork stays at its epoch, the page goes on and counts the change under the
table. Where a seed's first fork moves, the page takes the test as changed: it starts again from
that run, and lists the points before it as [the earlier points](/chat-sim/logos-chat/earlier/). A
point with no outcome has a row and no numbers.

Where `main` has a new commit by the time it runs again, chat-sim and `main` changed together, and
no run holds one change apart from the other. That commit's row says it is not compared with the
commit before. Where a first fork moved as well, the page starts again there, and says of the
earlier points that it cannot tell which of the two changed the runs.

The page tracks each group against its own past. It does not rank GroupV2 against GroupV3: an
epoch joins more members in one than in the other, and the page says how many.

## The page of a change

A commit found to change a run has a page, `logos-chat/main/<sha>/<measurement>/change/`, for the
commit against the commit before it.

| Part | Shows |
|---|---|
| The verdicts | a line a group, in the words of a pull request's comment |
| Seed by seed | of each group a run of which moved, a panel a rate: a row a seed, with its epochs before the first fork at the commit before as a ring and at the commit as a dot |
| Open a pair | a row a seed: its run's number, its epochs at both commits, how far its first fork moved, the round its two runs part in, and each run's text; under them, the commands that download both commits' logs |

The round the two runs part in is the first whose adders, joiners, refused adders or virtual
seconds differ in the two `run.json`. A run's rounds and its outcome are two records of it: two
runs of the same outcome can part in a round, and two that differ can part in none.

The site keeps each run's text while its point is among the newest eleven, and no log
([Into the log](/chat-sim/guide/investigate/moved-run/#into-the-log)). GitHub keeps a run's logs
90 days. Where a command finds none, the page of each commit's point has the commands that run a
run again.

## The page of a point

The page of a point names the logos-chat commit, chat-sim's commit and the measurement, the pull
requests merged since the point it follows, and that point. A heatmap of its runs follows, then a
row for each group's rate with how its runs ended, then a fold a rate with each run. Each run has
its lines, its rounds, its text, the commands that run it again ([From a moved run to your
machine](/chat-sim/guide/investigate/moved-run/#run-it-yourself)) and the command that downloads
its log ([Into the log](/chat-sim/guide/investigate/moved-run/#into-the-log)). A run whose job
could not build chat-sim has no log.

The newest eleven points keep their runs' text. An older point keeps its `point.json`, and its page
is drawn again from it alone, so the commands stay.

## The long runs

Once each Sunday, in UTC, `measure-main.yml` starts the `long` suite at `main`'s tip. It runs the
two scenarios of fifty epochs once each, and each other scenario `tools/sim/expected/` holds, but
the goal at `main`'s rates, as many runs as the file holds. It holds each sweep to that file. Its
page, `logos-chat/long/<sha>/<measurement>/`, has a row a scenario with what its runs came to and
what `compare` said. "As expected" means the runs came out as `tools/sim/expected/` holds them, not
that they came out well. The graph over main links the newest long run and draws none of it. The
newest fifteen long runs keep their text.
