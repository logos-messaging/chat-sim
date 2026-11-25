---
title: From a moved run to your machine
description: Open a run that moved, read its trees and rounds, run both sides again on your own machine, hold one to the other, and read the log.
---

A run **moved** when the head's run of a seed came out otherwise than the base's run of the same
seed. Every run of a pull request's report links a page of its own. From there, the same two runs
can be made again on Linux, byte for byte, and taken apart.

## The run's page

The page of a run is `logos-chat/pr-N/head/<scenario>/<run>/` of the site, headed by its group,
rate and seed, such as `GroupV3 · 1% · seed 4`. A chip and a sentence say how the head's run came
out against the base's, such as:

```text
moved  The head first forked at epoch 7, 3 epochs earlier than the base's 10.
```

A run that moved at the same first fork says so, and counts neither earlier nor later. Then come:

| Part | Shows |
|---|---|
| The trees | both runs large, the head's over the base's, on one axis of epochs; each line labelled with its members, its epochs and the line it left; a mark at the first fork; who fell behind, at which epoch |
| The rounds | of each side, a row a round: its adders, the members they added, the adders refused the whole round, and the virtual seconds the round took |
| Run it yourself | what running the run again takes, and the commands for the head and for the base |
| The text | each side's `run.txt` and `run.json`, or the build's log where chat-sim did not build |
| The log | the command that downloads each side's `group.log` ([Into the log](#into-the-log)) |

Read the rounds of both sides from the top. The first round where the two tables differ is the
first the rounds show of the runs parting. The run does not say which adder added which member.

## A change, or a re-deal

A run that moved at one seed is not yet a finding. A member's network model decides each frame
from the seed, its name and the frames shown to it so far
([How a run works](/chat-sim/guide/understand/how-a-run-works/#the-same-run-and-when-it-is-not)).
A head that sends one frame more re-deals every later loss, and the run can move by chance alone.
The verdict over a rate's seeds tells chance from a shift
([Your pull request's result](/chat-sim/guide/track/pull-request/#the-verdict)).

The member table of each side's `run.txt` shows whether the frames differ. A member whose `arrived`
count differs between the two sides received another stream of frames, and from the first frame
that differs, its losses fall on other frames.

## Run it yourself

It needs Linux on x86-64, Nix with flakes, `jq` and `python3`. On Linux a run is the same every time
from its seed, and CI runs on x86-64. The page prints a block for each side. Run each from a
directory of its own, since each clones chat-sim:

```sh
mkdir -p ~/moved/head && cd ~/moved/head
git clone https://github.com/logos-messaging/chat-sim && cd chat-sim && git checkout <chat-sim>
pinned=$(jq -r '.nodes."logos-chat".locked.rev' flake.lock)
sed -i "s/$pinned/<revision>/g" flake.nix Cargo.toml
nix flake lock
nix develop -c cargo build --release -p harness --bin harness-run
python3 tools/sim sweep tools/sim/scenarios/<scenario>.json --from <run> --runs 1 --one-process target/release/harness-run --out tmp/<scenario>-seed-<seed>
```

| Line | Does |
|---|---|
| `git clone`, `git checkout` | takes chat-sim at the commit CI ran |
| `pinned=`, `sed`, `nix flake lock` | pins logos-chat at the side's commit, in the flake and in `Cargo.toml`, as CI's jobs do |
| `nix develop -c cargo build` | builds `harness-run` against that commit, with the toolchain logos-chat's flake gives |
| `sweep --from <run> --runs 1` | does run `<run>` of the scenario's sweep, with the seeds it has in CI |

Do the same for the base in `~/moved/base`, with the base's block. On CI's machines a run at a
pull request's rates takes from seconds to a few minutes of wall time, and the build longer.

## What a run prints

`sweep` prints a row for the run and writes it under `--out`, in a directory named for the run.
Its `run.txt` reads, cut to a few members and lines:

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

20 rounds, 42 joiners, 0 of the adders refused a whole round, the longest 180.000s

line   kind  from  forked at  epoch  members
0     trunk     -          -     11        7
1      main     0          3     13       10
3       sub     1          8     12        2
fell behind: mira at epoch 3, kes at epoch 4, peer11 at epoch 7, peer18 at epoch 7, peer19 at epoch 7, peer22 at epoch 7, raya at epoch 8, pax at epoch 8, peer29 at epoch 11, peer30 at epoch 11, peer12 at epoch 9, pax at epoch 10, peer7 at epoch 11, peer10 at epoch 11, peer13 at epoch 11, peer24 at epoch 10
behind at the end: pax, mira, peer7, peer10, peer12, peer13
never joined: peer31, peer32, peer42
```

- The head says how far the virtual clock went and in how much wall time, the seeds and the loss,
  and the load on the machine follows.
- The second line is how the grow ended. A grow run is not checked against an expectation.
- `member` counts the frames that `arrived` at each member's model, those it `passed`, those it
  still `held` at the end and those it `lost`, and the frames the member rejected (`errors`). A
  member never seated has none.
- `inbound error` lists each error the members' cores gave, in the core's words, with how many
  frames and members.
- The grow block counts the rounds and joiners, lists each line, and names who fell behind, who was
  behind at the end and who never joined.

`run.json` holds the same and more, for scripts
([Records](/chat-sim/guide/reference/records/#what-a-run-leaves)).

## The two sides side by side

`compare` holds a sweep to outcomes written before. Write the base's outcome, then hold the head's
run to it:

```sh
cd ~/moved/base/chat-sim && python3 tools/sim compare --write ~/moved/base.json tmp/<scenario>-seed-<seed>
cd ~/moved/head/chat-sim && python3 tools/sim compare ~/moved/base.json tmp/<scenario>-seed-<seed>
```

It prints the head's run as a row, `moved`, with how it ended and what its grow came to, and the
base's under it as `was`. An outcome holds the seeds, the ids the run drew, how far the grow got and
the seconds of the clock, so any of them differing makes the run move. `diff` of the two `run.txt`
files shows every table that differs.

## Into the log

Each run's directory also holds what the runner left: `plan.json`, what it was told; `group.log`,
every member's trace records; and `result.json`, what it measured. `tools/forks` reads the log and
says where the group parted:

```sh
python3 tools/forks report tmp/<scenario>-seed-<seed>/<run>/group.log
```

CI keeps each run's log as well. The site holds none: each run job uploads its runs' logs as an
artifact of the workflow run, `logs-<side>-<scenario>-<from>`, where the side is `revision` or
`base` and `<from>` is the number of the job's first run. GitHub keeps it 90 days. A job that could
not build chat-sim uploads none. The page of a run, of a point and of a change each give the
command that downloads the artifacts of a scenario, such as:

```sh
gh run download <run-id> --repo logos-messaging/chat-sim --dir logs --pattern 'logs-revision-grow-waku-loss-5-[0-9]*'
```

Each artifact holds the runs of one job, a directory a run by its number, as the sweep names it:
run 7 of ten is `logs/logs-revision-grow-waku-loss-5-1/grow-waku-loss-5/07/group.log`. Where the
command finds no artifact, [run the run again](#run-it-yourself): its directory holds the log.

A grow run's report is long. The tests of `tools/forks` hold a split among four members, whose
report reads in full:

```console
$ python3 tools/forks report tools/forks/tests/fixtures/histories/split-at-an-epoch/
conversation b05d4417e9
  split at epoch 3
  4 of 4 seats share a log

  branch A: epoch 4, state 5a850890461cab7f, 4 members
    first state at epoch 3, made by commit c41f9be27a6d03e5 by a3f91c07
    on it: 19f6b0e3, 7b1e44d9, a3f91c07
  branch B: epoch 3, state e849c2aa9ab1f74d, 4 members
    first state at epoch 3, made by commit 77b0a1c2d3e4f5a6 by 7b1e44d9
    on it: e09a3f52

  installation  account   name  epoch  position  branch
  19f6b0e3      d2184c8e  mira  4      tip       A
  7b1e44d9      91c0e3a7  raya  4      tip       A
  a3f91c07      5be2c0d1  saro  4      tip       A
  e09a3f52      0d7a66f3  pax   3      tip       B

lines: 69 read, 42 records, 0 without a stamp, 0 malformed, 0 unknown
```

Each branch gives its newest epoch, its state there and how many members that state holds. Under
it come the commit that made its first state, with the installation that committed it, and the
installations on the branch. The table lists every installation and the branch it is on.
[`tools/forks/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/tools/forks/README.md#running-it)
says what each line means.

An installation is the first 4 bytes of a member's signing key, and the member table of `run.txt`
gives each member's. `grep 'signer=<installation>' group.log` lists every record that member wrote,
in the order of the clock ([Records](/chat-sim/guide/reference/records/)).
