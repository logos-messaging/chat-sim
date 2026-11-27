---
title: From a moved run to your machine
description: Open a run that moved, read its trees and rounds, make both sides again on your own machine, compare them, and read the log.
---

A run **moved** when the head's run of a seed gave another outcome than the base's run of the same
seed. Every run of a pull request's report links a page of its own. From there you can make both
runs again on Linux, byte for byte, and take them apart.

This page follows one run as its example: `GroupV3 · 1% loss · seed 4`. It is run 5 of its
scenario, `grow-3-adders-groupv3-waku-loss-1pct.json`: the runs of a scenario are numbered from 1,
and the seeds of `grow-3-adders` from 0.

## The run's page

The page of a run is `logos-chat/pr-N/head/<scenario>/<run>/` of chat-sim's site, with the run's
number as `<run>`. Its title names the group, the rate and the seed. A sentence says how the head's run differs from the base's:

```text
moved  The head first forked at epoch 7, 3 epochs earlier than the base's 10.
```

A run that moved at the same first fork says so, and counts neither earlier nor later. Below the
sentence:

- **The trees**: both runs on one axis of epochs, the head's over the base's. Each line is
  labelled with its members, its epochs and the line it left. A mark shows the first fork, and
  the page names who fell behind, and at which epoch.
- **The rounds**: for each side, one row per round. A row has the round's adders, the members
  they added, the adders whose add was refused for the whole round, and the virtual seconds the
  round took ([A group that grows](/chat-sim/guide/understand/scenarios/#a-group-that-grows)).
- **Run it yourself**: the commands that make each side's run again.
- **The text**: each side's `run.txt` and `run.json`, or the build's log when chat-sim did not
  build.
- **The log**: the command that downloads each side's `group.log`.

Read the rounds of both sides from the top. The first round where the two tables differ is the
earliest difference the rounds show. Two runs that moved can show none. The run does not say
which adder added which member.

A run of `main` has the same page, `logos-chat/main/<sha>/<measurement>/<scenario>/<run>/`, while
its point is among the newest eleven
([The page of a point](/chat-sim/guide/track/main/#the-page-of-a-point)). It has one side: one
tree, one table of rounds and one block of commands.

## A change, or chance

One seed that moved is not yet a finding. A head that sends one frame more shifts which frames
every member loses after it. That alone can move a run ([The same run, and when it is
not](/chat-sim/guide/understand/how-a-run-works/#the-same-run-and-when-it-is-not)). The verdict
over a rate's seeds says whether the runs that moved lean one way
([The verdict](/chat-sim/guide/track/pull-request/#the-verdict)).

## Run it yourself

You need Linux on x86-64, `git`, Nix with flakes, `jq` and `python3`, 3.9 or newer. On Linux a
run is the same every time from its seed, and CI runs on x86-64.

The page prints one block of commands for each side, with every value filled in. Run each block
in a directory of its own, since each clones chat-sim.

This is the head's block for the example. Its first line is added here, to make the directory.
`<chat-sim-commit>` stands for the chat-sim commit CI ran, and `<head-commit>` for the pull
request's head:

```sh
mkdir -p ~/moved/head && cd ~/moved/head
git clone https://github.com/logos-messaging/chat-sim && cd chat-sim && git checkout <chat-sim-commit>
pinned=$(jq -r '.nodes."logos-chat".locked.rev' flake.lock)
sed -i "s/$pinned/<head-commit>/g" flake.nix Cargo.toml
nix flake lock
nix develop -c cargo build --release -p harness --bin harness-run
python3 tools/sim sweep tools/sim/scenarios/grow-3-adders-groupv3-waku-loss-1pct.json --from 5 --runs 1 --one-process target/release/harness-run --out tmp/grow-3-adders-groupv3-waku-loss-1pct-seed-4
```

| Line | Does |
|---|---|
| `git clone`, `git checkout` | takes chat-sim at the commit CI ran |
| `pinned=`, `sed`, `nix flake lock` | pins logos-chat at the side's commit, in the flake and in `Cargo.toml`, as CI's jobs do |
| `nix develop -c cargo build` | builds `harness-run` against that commit, with the toolchain logos-chat's flake gives |
| `sweep --from 5 --runs 1` | makes run 5 of the scenario alone, which is seed 4 |
| `--out` | names the directory the run is written under, which must not exist yet |

Do the same for the base in `~/moved/base`. Its block differs only in the commit that `sed` pins.
On CI's machines a run at a pull request's rates takes from seconds to a few minutes, and the
build takes longer.

## What a run prints

`sweep` prints one row for the run and writes the run's directory under `--out`, here
`tmp/grow-3-adders-groupv3-waku-loss-1pct-seed-4/5/`
([What a run leaves](/chat-sim/guide/reference/records/#what-a-run-leaves)). A `run.txt` of
`grow-3-adders` starts like this. The sample is another run, seed 0 of
`grow-3-adders-groupv2-waku-loss-5pct.json`, cut to three members:

```text
200 members, default group timers, one process, 2712.459s on a virtual clock in 21.288s of wall time, seed 0, network seed 1, loss 0.05, welcome loss 0.05
5 main branches ran side by side: trunk at epoch 11, 5 main, 6 sub, 15 fell behind, 6 behind at the end, 3 never joined
not checked: a `grow` step expects no outcome
load average 2.36 at the start, 1.97 at the end

member   installation  arrived  passed  held  lost  errors
saro         f95e254c     2857    2698     4   155     423
raya         671cc18a     2949    2818     4   127     242
peer200      a9003b1b        0       0     0     0       0

inbound error                                                                 frames  members
DeMlsError(Mls(ProcessMessage(ValidationError(UnableToDecrypt(AeadError)))))   11060       37
```

- The first line gives the time on the virtual clock, the wall time, the two seeds and the loss.
  The seed the pages name is the first of the two
  ([Seeds in a sweep](/chat-sim/guide/reference/scenario-format/#seeds-in-a-sweep)).
- The second line says why the group stopped growing, and what it left. Its `5 main` counts the
  trunk with its four main branches.
  [A group that grows](/chat-sim/guide/understand/scenarios/#a-group-that-grows) reads such a
  line part by part.
- `not checked` says that a run of a growing group is checked against no expectation.
- The wall time and the load average are the machine's. They alone differ between two runs of
  one seed at one revision.
- The `member` table counts the frames sent to each member. Each one `arrived` at the member's
  [network model](/chat-sim/guide/understand/how-a-run-works/#one-process-a-virtual-clock), which
  `passed` it on, still `held` it at the end, or `lost` it. `errors` counts the frames the member
  rejected. A member that was never added has none. A member whose `arrived` count differs
  between the head and the base was sent another stream of frames.
- The `inbound error` table lists each error the members gave on a frame, with how many frames
  and how many members gave it.

Below them, the file counts the rounds. Then it lists each line, and who fell behind, was behind
at the end or never joined. `run.json` holds the same for scripts, and each round as well.

## The two sides side by side

`compare` checks a run against an outcome written before. Write the base's outcome, then check the
head's run against it:

```sh
cd ~/moved/base/chat-sim && python3 tools/sim compare --write ~/moved/base.json tmp/grow-3-adders-groupv3-waku-loss-1pct-seed-4
cd ~/moved/head/chat-sim && python3 tools/sim compare ~/moved/base.json tmp/grow-3-adders-groupv3-waku-loss-1pct-seed-4
```

It prints the head's run as a row marked `moved`, and the base's outcome under it as `was`. A
difference in any field of the outcome makes the run moved
([A run's outcome](/chat-sim/guide/understand/how-a-run-works/#a-runs-outcome) lists the fields).
`diff` of the two `run.txt` files shows every table that differs.

## Into the log

`group.log` holds every member's trace records
([The records](/chat-sim/guide/reference/records/#the-records)). A run you made has it in its
directory. A run of CI has it in an artifact of the workflow in chat-sim's repository, not on the
site.

Each job of CI uploads its runs' logs as the artifact `logs-<side>-<scenario>-<from>`. The side is
`revision` for the head and `base` for the base. `<from>` is the number of the job's first run.
GitHub keeps the artifact 90 days. A job that could not build chat-sim uploads none.

The page of a run and the page of a change print the command that downloads a scenario's
artifacts. So does the page of a point whose runs have no page of their own. The command needs
GitHub's `gh`. For the example:

```sh
gh run download <run-id> --repo logos-messaging/chat-sim --dir logs --pattern 'logs-revision-grow-3-adders-groupv3-waku-loss-1pct-[0-9]*'
```

Each artifact holds the runs of one job, one directory per run, named by the run's number. The
example's run 5 is in the artifact of the job that starts at run 4:
`logs/logs-revision-grow-3-adders-groupv3-waku-loss-1pct-4/grow-3-adders-groupv3-waku-loss-1pct/5/group.log`.
`ls -d logs/*/*/*/` lists the directory of each run that was downloaded.

When the command finds no artifact, [make the run again](#run-it-yourself): its directory holds
the log.

`tools/forks` reads a log and says where the group split:

```sh
python3 tools/forks report tmp/grow-3-adders-groupv3-waku-loss-1pct-seed-4/5/group.log
```

Its report gives the conversation's verdict, each branch with its newest epoch and state, and the
branch each installation is on.
[`tools/forks/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/tools/forks/README.md#running-it)
shows a report and explains each part, and [Commands](/chat-sim/guide/reference/commands/#forks)
lists the verdicts.

An installation is one member's client, named by the first 4 bytes of its signing key. The member
table of `run.txt` gives each member's. `grep 'signer=<installation>' group.log` lists every
record that member wrote, in the order of the clock.
