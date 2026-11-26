---
title: logos-chat main over time
description: How chat-sim measures each commit of logos-chat's main, and how to read the graph over main, the page of a change, the page of a point and the long run of each Sunday.
---

Each push to logos-chat's `main` runs chat-sim at the pushed commit. It runs
[the goal](/chat-sim/guide/understand/scenarios/#the-goal) on both kinds of group: seeds 0 to 2 at
no loss, and seeds 0 to 19 at 0.5, 1 and 5% loss.

The runs of one commit make a **point** of `main`.
[The graph over main](/chat-sim/logos-chat/) draws the points. Every hour chat-sim also looks at
`main`'s tip, and runs it when it has no point yet. A tip whose run ended without a point is not
run again until a person has looked into it.

## The graph over main

The page has three sections:

| Section | Shows |
|---|---|
| Now | the newest commit that has an outcome: for each group and rate of loss, the median epochs before the first fork, and a meter toward the goal of 50 |
| Over main's commits | one panel per rate of loss at which a run forked: the median as a line, the middle half of the runs as a band, and a marker on each commit where a run moved |
| What changed | one row per commit, the newest first: each group's verdict against the commit before |

The epochs before the first fork are the number a run is measured by. The page of a point calls
them its epochs without a fork
([A run's outcome](/chat-sim/guide/understand/how-a-run-works/#a-runs-outcome)).

A commit gets its verdict as a pull request does
([The verdict](/chat-sim/guide/track/pull-request/#the-verdict)). The commit before it stands in
for the base: the runs of the two commits are paired seed by seed. A run **moved** when its
outcome differs from the outcome of the same seed at the commit before.

- A commit where a run moved links [the page of its change](#the-page-of-a-change).
- A run with no outcome at the commit before has none to compare with. The row says how many
  such runs there are.
- A point with no outcome has a row and no numbers.
- Only the rates of loss that `main`'s newest commit ran are compared.
- At no loss a commit runs three seeds, and three seeds cannot go beyond chance. So that rate
  can read "moved, within chance", and never "forks earlier" or "forks later".

Under the numbers of Now, one line compares the newest commit with the first commit the page
draws. A group reads "nothing moved" when every commit since the first had each run of the group
compared with the commit before, and changed none. Otherwise it gets the verdict of the newest
commit's runs against the first commit's. So a drift over several commits shows there, even when
no single commit shows it.

The page tracks each group against its own past. It does not rank GroupV2 against GroupV3: one
epoch adds a different number of members in each. Where both groups have runs that went without a
fork, the page says how many.

## The measurement

chat-sim changes too, and a change to it can move a run. So each point is tied to a
**measurement**. The measurement is a chat-sim commit. It is the newest commit, up to the one
that ran, that changes a file a run depends on
([CONTRIBUTING.md](https://github.com/logos-messaging/chat-sim/blob/master/CONTRIBUTING.md#the-paths-a-run-depends-on)
lists the files). It is the `<measurement>` in a point's address. A commit to this guide changes
no such file, so it leaves the measurement as it is.

In full, a point is the runs of one logos-chat commit at one measurement. A commit is compared
with the commit before it only at a measurement both ran at.

When the measurement changes, chat-sim runs `main`'s tip again within the hour, at the new
measurement. The tip then has two points, one at each measurement, and they show what chat-sim's
change did.

chat-sim runs only the tip again. So when `main` gets a new commit first, the old tip has no point
at the new measurement. The page shows one of three things:

| The page shows | When |
|---|---|
| under its sections, "chat-sim itself changed 1 time since, and each time no seed's first fork moved" | the tip ran again, and no seed's first fork differs between its two points. The graph goes on. |
| a graph that starts again at the new point, and a link to [the earlier points](/chat-sim/logos-chat/earlier/) | the tip ran again, and a seed's first fork differs between its two points |
| a row that reads "not compared with the commit before" | `main` got a new commit before the old tip ran again. Between the two commits, logos-chat and chat-sim both changed, and no run tells one change from the other. |

When a first fork moved across such a row, the graph starts again there, and says that chat-sim
and `main` changed together.

## The page of a change

A commit where a run moved has a page that compares it with the commit before,
`logos-chat/main/<sha>/<measurement>/change/`:

- **The verdicts.** One line per group, in the words of a pull request's comment.
- **Seed by seed.** For each group with a run that moved, one panel per rate at which a seed's
  first fork moved, with one row per seed. A ring marks the seed's epochs before the first fork
  at the commit before, and a dot marks them at the commit.
- **Open a pair.** One fold per rate. It draws each seed as two trees, the run after over the
  run before, from the seed whose first fork moved earliest to the one whose moved latest. A tree
  links its run's page while the site keeps one. Under the trees is one row per seed, with its
  epochs at both commits, how far its first fork moved, the round where its two runs first
  differ, and each run's text. Under the rows are the commands that download both commits' logs.

The round where two runs first differ is the first round whose row differs between them
([The run's page](/chat-sim/guide/investigate/moved-run/#the-runs-page) shows the rows). The rounds
are not part of a run's outcome. So two runs with the same outcome can differ in a round, and two
runs that moved can show `no round`.

## The page of a point

`logos-chat/main/<sha>/<measurement>/` is the page of a point. It names the logos-chat commit,
the chat-sim commit that ran, the measurement and the pull requests merged since the point before
it. The measurement is the chat-sim commit that ran, or an older one. Then the page shows a
heatmap of the runs, how the runs of each group and rate ended, and each run as a tree.

A tree reads as it does on a pull request's report
([The report page](/chat-sim/guide/track/pull-request/#the-report-page)), and is in its group's
colour. Each rate is a column. Its runs go from the fewest epochs without a fork at the top to
the most, and a dashed line marks their median, the number the graph over main draws. A rate
where no run forked is a row above the columns.

A tree links the page of its run, `<scenario>/<run>/` under the point. That page draws the tree
large, with each line labelled and the members that fell behind. It also has the run's rounds,
its text, the commands that make it again
([Run it yourself](/chat-sim/guide/investigate/moved-run/#run-it-yourself)) and the command that
downloads its log ([Into the log](/chat-sim/guide/investigate/moved-run/#into-the-log)).

The site keeps the runs' pages and text of the newest eleven points
([What the site keeps](/chat-sim/guide/reference/records/#what-the-site-keeps)). An older point
keeps its trees, which then link no page. Under them, each rate has the commands that run a seed
again and the command that downloads the rate's logs. A dashed tree is a run whose lines the
point does not hold: it draws the trunk, and a mark where the run first forked.

## The long runs

Once each Sunday, in UTC, chat-sim runs every scenario that a point does not run, but `three`, at
`main`'s tip ([The scenarios](/chat-sim/guide/understand/scenarios/) says which). The page of a long run,
`logos-chat/long/<sha>/<measurement>/`, has one row per scenario: how its runs ended, and whether
they were as expected.

"As expected" means the runs gave the outcomes that `tools/sim/expected/` records
([CONTRIBUTING.md](https://github.com/logos-messaging/chat-sim/blob/master/CONTRIBUTING.md#what-is-expected)),
not that they ended well. The graph over main links the newest long run and draws none of it.
