---
title: logos-chat main over time
description: How chat-sim measures each commit of logos-chat's main, what a point holds, how to read the graph over main, and the long run of each Sunday.
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

[Over main](/chat-sim/logos-chat/) draws every point, the oldest at the left, GroupV2 and GroupV3
apart. Each rate of loss has a blue of its own, the heavier the loss the darker.

| Chart | Shows |
|---|---|
| Epochs without a fork | of each rate, the median of its runs and the band from the lowest run to the highest |
| Runs whole at 50 epochs | of each rate, the share of its runs that ended one group with every member that joined at its tip; under the dates, the pull requests each point merged |
| The newest point | a cell a run, a row a rate and a column a seed, coloured by its epochs without a fork; a dot marks a run that ended whole, a ring one that ended with members behind |
| Members off the trunk at the end | of each rate, the median of the members behind, on another line or never joined; drawn of a group once a rate of it with loss has had every run whole |
| The points | each point, the newest first: its commit, the pull requests it merged, each rate's runs whole, what moved against the point before it, and the measurement |

Two marks tell a step that is not logos-chat's. A dashed line marks a point at another measurement
than the point before it. An open marker marks a point whose runs drew other ids than the point
before it, so every seed's run is another run.

## The page of a point

The page of a point names the logos-chat commit, chat-sim's commit and the measurement, the pull
requests merged since the point it follows, and that point. A heatmap of its runs follows, then a
row for each group's rate with how its runs ended, then a fold a rate with each run. Each run has
its lines, its rounds, its text and the commands that run it again ([From a moved run to your
machine](/chat-sim/guide/investigate/moved-run/#run-it-yourself)).

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
