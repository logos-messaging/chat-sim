# chat-sim

Simulations of [logos-chat](https://github.com/logos-messaging/logos-chat) groups: does a group of
logos-chat clients still hold one group when the network delays and loses what its members send?

A scenario says how many members there are, what they do, what the network does to them, and what
the group is expected to come to. [`tools/sim`](tools/sim/README.md) runs it with every member in
one process on a virtual clock, where on Linux a run is the same every time from its seed.
[`tools/forks`](tools/forks/README.md) then reads the members' logs and says whether they still
hold one group.

`tools/sim` can also run a `chat-cli` process for each member, with a delivery node of its own on a
local network, or each of those in a container on a lossy wire. chat-cli builds on logos-chat changes
that the revision run here does not hold, so it lives on the
[`local-network`](https://github.com/logos-messaging/chat-sim/tree/local-network) branch, pinned to
logos-chat's `feat/local-network`.

CI publishes the [guide](https://logos-messaging.github.io/chat-sim/guide/) to the
repository's [Pages site](https://logos-messaging.github.io/chat-sim/), with a report of its
simulations for master and for each open pull request, the runs of logos-chat that
`simulate.yml` keeps, and the [graph over logos-chat's main](https://logos-messaging.github.io/chat-sim/logos-chat/)
drawn from the runs of main.

## Layout

| Path | What it is |
|------|------------|
| [`crates/harness`](crates/harness/README.md) | A group of logos-chat clients in one process on a virtual clock, and `harness-run`, which runs a plan on it |
| [`crates/network-model`](crates/network-model/src/lib.rs) | The model of a lossy network the runner puts between a client and what it receives |
| [`tools/sim`](tools/sim/README.md) | Runs a scenario, and sweeps one over seeds and rates |
| [`tools/forks`](tools/forks/README.md) | Reads the logs of a group's members and says whether the group forked |
| [`guide`](guide) | The guide, a Starlight site: how a run works, how to run one, and how to read what it finds |
| `.github/scripts/measure.py` | Says whether `simulate.yml`'s main suite is to run at logos-chat main's tip, from the points on `gh-pages` and the runs going, and on Sundays whether its long suite is |
| `.github/scripts/mock-registry.py` | The KeyPackage registry the peers of a run share |
| `.github/scripts/pages.py` | Writes the Pages site: the report of a CI run from the text its sim jobs upload, the list of the reports, and the landing page; and keeps the points of logos-chat's main, its long runs and the runs of its pull requests that `simulate.yml` makes, draws the page of each, draws the graph over main from the points, and names the runs of logos-chat's pull requests that are no longer open |
| `.github/scripts/simulate.py` | Plans the jobs of `simulate.yml`, names the point of main a `pr` run is placed among, writes the comment on what they came to, says where it goes on a logos-chat pull request, and writes the updates of the check run there |

## The logos-chat it runs

The simulation runs logos-chat at one revision, pinned twice: [`Cargo.toml`](Cargo.toml) pins
logos-chat's crates, and [`flake.nix`](flake.nix) takes the toolchain from logos-chat's flake, so it
is the store path logos-chat's CI pushes to the Logos cache. CI checks that the two name the same
revision. To move it, change the revision in both, then run
`nix flake lock`; cargo updates `Cargo.lock` on the next build.

`de-mls` and `chat-proto` are spelled in `Cargo.toml` as logos-chat spells them, and move with it.

The workflow `simulate.yml` runs master at another logos-chat revision, started by hand with the
revision, the suite, a base revision to compare with or none, the number of the logos-chat pull
request the revision is of or none, and the id of a check run on it or none. The `pr` suite, for a
logos-chat pull request, runs seeds 0 to 2 of `grow-waku-loss-0.5`, `-1` and `-5`
on GroupV2, and of their `-groupv3` copies on GroupV3, a job a run, at the revision and at the
base; `main` runs seeds 0 to 19 of those and of `-0` and `-0-groupv3`, with no base; `long` runs
`50-epochs`, `50-epochs-waku` and every other scenario held in `tools/sim/expected` but those
eight. A `pr` run whose base has its point of main on `gh-pages` at
the chat-sim commit a run measures, with an outcome at each run it asks of the base, runs nothing
at the base: main's runs of seeds 0 to 2 are the
base's. The run's summary holds the comment: for `pr` the verdict `tools/sim matrix` says of each
group, main's runs of the two groups side by side, a cell a group's rate and seed, and of each such
rate main's twenty seeds at the point at the
base or else the nearest before it of the base's hundred newest commits, for `main` how the runs of
each group at each rate ended, and for
`long` what each scenario's runs came to and which moved from the base's, or from those
`tools/sim/expected` holds when there is no base. Its publish job keeps a `main` run handed no
`pr` on the Pages
site as a point of logos-chat's main, with the matrix's cells and summary, a `pr` run under the
number of its pull request, with the matrix, and a `long` run with no base as a long run of
logos-chat's main, with what each scenario came to and the output of each `compare`, each with its
runs' text, or the build's log where chat-sim did not build, and a page drawn from them, which the
comment links; the guide's
[Reports](https://logos-messaging.github.io/chat-sim/guide/reports/#logos-chats-runs) says what
they hold and how long. The weekly run of `prune-pages.yml` removes the run of each logos-chat pull
request that is no longer open, and a dispatch of it handed a `pr` removes that one's.

A `pr` run handed a `pr` also posts its comment on that pull request as the GitHub App chat-sim,
`chat-sim[bot]`: it edits the App's comment there, or writes one. A `pr` run handed `check_run`,
the id of a check run the App made on logos-chat, moves that check to in progress once its runs are
planned and completes it with the groups' verdicts: a failure when either group's runs are worse in
two or more and better in none, else a success when either group's are better, and neutral
otherwise, but that a revision chat-sim does not build at,
or a run with no outcome there, fails it when the repository variable `CHAT_SIM_CHECK_REQUIRED` is
`true`. The plan job and the comment job each mint a token of their own, for logos-chat alone, from
the repository variable `CHAT_SIM_APP_CLIENT_ID` and secret `CHAT_SIM_APP_PRIVATE_KEY`; without
them, nothing is posted and the comment is in the run's summary alone. The guide's
[Reports](https://logos-messaging.github.io/chat-sim/guide/reports/#the-comment-and-the-check) gives
each verdict's conclusion and title.

`measure-main.yml` starts it as well: every hour, it dispatches `main` at logos-chat main's tip,
unless the tip has its point on `gh-pages` at the chat-sim commit a run measures (the newest that
touches `crates/`, `Cargo.toml`, `Cargo.lock`, `.cargo/`, `rust-toolchain.toml`, `flake.nix`,
`flake.lock`, `tools/forks/forks/`, `tools/sim/sim/` or `tools/sim/scenarios/`), a run of
`main` at the tip handed no `pr` is queued or in progress, or one of chat-sim's default branch
ended at that commit without its point, which a
person looks into before starting it again by hand. Once each Sunday, in UTC, it dispatches `long`
at the tip as well, unless a run of `long` handed no `pr` was created that day, one is queued or in
progress, or the tip has its long run on `gh-pages` at that commit. A run of one suite holds back no
other: the concurrency group of `simulate.yml` names the suite.

## Running it

With Nix, one command builds what a run needs and runs a scenario:

```sh
nix run .#groupv2-simulation -- run tools/sim/scenarios/50-epochs.json --out tmp/50-epochs
```

The dev shell has the toolchain for cargo:

```sh
nix develop
cargo build --release --workspace
python3 tools/sim run tools/sim/scenarios/50-epochs.json --one-process target/release/harness-run --out tmp/50-epochs
```

[`tools/sim`](tools/sim/README.md#running-it) has the other ways to run a scenario and what a run
reports.

## License

Licensed under either of [Apache License, Version 2.0](LICENSE-APACHE) or [MIT license](LICENSE-MIT)
at your option.
