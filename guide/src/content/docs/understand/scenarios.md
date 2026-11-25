---
title: The scenarios
description: What each scenario does, how a group grows round by round, and why the scenarios are shaped as they are.
---

A scenario is a JSON file in `tools/sim/scenarios/`. It says how many members there are, what they
do, and what the network does to them
([Scenario format](/chat-sim/guide/reference/scenario-format/)).

| Scenario | Does | Run by |
|---|---|---|
| `grow-waku-loss-*` | **the goal**: a group grows toward epoch 50, up to three members adding at once, at the rate of loss in its name | pull requests at 0.5, 1 and 5%; `main` at those and 0%; the long run at 0.1% |
| `50-epochs-by-saro-waku-loss-*` | a group grows toward epoch 50, saro alone adding | the long run |
| `grow-waku-size-50` | a group kept at 50 members grows toward epoch 1000, at no loss | the long run |
| `50-epochs`, `50-epochs-waku` | saro adds 50 members one at a time, then the last one posts | the long run |
| `three` | three members and one post, on fast timers | you, to try the tools |

Each `grow-waku-loss-` and `50-epochs-by-saro-waku-loss-` file has a `-groupv3` copy, which runs
the same on a GroupV3 group. chat-sim's own CI runs every scenario but `three`
([chat-sim's own reports](/chat-sim/guide/reports/)).

Every GroupV2 scenario that CI runs sets logos-chat's own timers, as logos-chat-module runs a
group. A GroupV3 group keeps timers of its own, whatever the file says.

## A group that grows

A `grow` step is the only step of its scenario. One member, saro, creates the group. The others
are added in **rounds**.

A member is **behind** when other members have applied a commit that it has not: they have moved
on from the state it still holds. That is not a fork: the member applied no other commit in its
place. A member that is behind is not picked and not waited on.

A round goes like this:

1. The step picks between one and `adders` of the members that are not behind. The scenario's
   `seed` fixes the picks.
2. Each picked member, an **adder**, adds one new member. They all add at once, each on the
   line it is on, the trunk or a branch.
3. The round ends when every new member has joined, and every member that was not behind at its
   start holds the newest state of its line. It also ends when the budget runs out: 180 s of the
   virtual clock, unless the scenario sets another.

An adder's own client can refuse the add. The adder then tries again until the round ends. An
adder refused for a whole round is not picked again until it holds another state.

The states the members hold make lines:

| Line | Is |
|---|---|
| the **trunk** | the line that starts at the group's first state |
| a **main branch** | a branch off the trunk |
| a **sub-branch** | a branch off another branch |

When two or more states follow one state, the group forked there, and its members divided
between them. The state that more members took continues the line. Each other state starts a
branch.

The step ends at the start of a round when one of these is true:

- the trunk has reached `epochs`
- the trunk and its main branches together number `branches` or more
- every member of the scenario has been added
- no member can be picked

[`crates/harness/README.md`](https://github.com/logos-messaging/chat-sim/blob/master/crates/harness/README.md#a-group-that-grows)
has the rule in full.

Here is how one run of `grow-waku-loss-0.5.json` ended, as its `run.txt` prints it:

```text
5 main branches ran side by side: trunk at epoch 9, 5 main, 0 sub, 2 fell behind, 0 behind at the end, 1 never joined
10 rounds, 19 joiners, 0 of the adders refused a whole round, the longest 180.000s

line   kind  from  forked at  epoch  members
0     trunk     -          -      9       15
1      main     0          6      6        1
2      main     0          5      5        1
3      main     0          8      8        1
4      main     0          9      9        1
fell behind: peer8 at epoch 4, peer13 at epoch 8
never joined: peer20
```

- The line that starts `5 main branches` says why the step ended, and what it left. It counts
  the trunk in: `5 main` is the trunk with its four main branches, which reaches the scenario's
  `branches` of 5.
- `2 fell behind` counts the members that fell behind in some round. The line under the table
  names them, each with the epoch it held then. `0 behind at the end` says that none was still
  behind when the step ended.
- `1 never joined` counts the members that were added and never got into the group. The last
  line names it.
- The line that starts `10 rounds` counts the rounds, the members added (`joiners`) and the
  adders refused for a whole round. The longest round took the whole budget.
- The table has one row per line: its kind, the line it left, the epoch at which it left, the
  last epoch it reached, and the members it ended with.
- The first fork is at epoch 5, where line 2 left the trunk. So the run went 5 epochs without a
  fork.

## The goal

The `grow-waku-loss-` scenarios ask one question: how many epochs does a growing group last before
it first forks? The goal is 50, and the guide calls these scenarios **the goal** after it. Each
part of the file has a reason:

- **Up to three adders a round, on any line.** Two adders can commit for one epoch, which a
  GroupV3 group needs to fork. Members on a branch keep adding, as members of a real group would.
- **Toward epoch 50.** A run that never forks goes 50 epochs without a fork, which is the goal.
- **`branches` of 5.** A run goes on past its first fork, and its tree shows every line that
  follows. It ends once the trunk and four main branches run side by side: `branches` counts the
  trunk. That keeps a run that forks early short.
- **200 members.** Each add takes a member that is not in the group yet, and the step ends when
  none is left. So the scenario needs a member for each add on the way to epoch 50.
- **The same loss for Welcomes.** Each member loses that share of the frames it receives, Welcomes
  among them. A new member whose Welcome is lost never joins.
- **Waku's delays.** Each member receives every frame late, by a delay of its own
  ([Waku's delays](#wakus-delays)).

The rates of loss are chosen for what a run can show:

- **0%.** No frame is lost, so a fork is the code's doing alone. It is the slowest rate to run, so
  `main` runs it on three seeds and a pull request not at all.
- **0.5, 1 and 5%.** GroupV3 forks on each of seeds 0 to 2, so a change in when it forks can show.
- **0.1%.** Two of those three seeds stay whole. For logos-chat, only the long run runs it.

At the revision chat-sim pins, about half of the members added to a GroupV3 group never join, even
at no loss. When several members add in one round, one of the adds is applied, and that round
runs until its budget runs out
([The two groups](/chat-sim/guide/understand/how-a-run-works/#the-two-groups)).

## Waku's delays

The delays come from the relay simulation the Waku team published as `rln-delay-simulations` in
[logos-messaging/research](https://github.com/logos-messaging/research/tree/d7167bcedb1142e3d7462dcf24a30f65b1a3ef90/rln-delay-simulations)
([#42](https://github.com/logos-messaging/research/issues/42)). It ran 1000 `nwaku` nodes in the
network simulator Shadow, each with 6 peers in its mesh and 100 ms on each link
([`shadow.yaml`](https://github.com/logos-messaging/research/blob/d7167bcedb1142e3d7462dcf24a30f65b1a3ef90/rln-delay-simulations/shadow.yaml)),
and lost no packet. Its 10 KB messages gave 8991 arrivals, from 263 to 673 ms, with a median of
518 ms ([`raw/latency_10kb.txt`](https://github.com/logos-messaging/research/blob/d7167bcedb1142e3d7462dcf24a30f65b1a3ef90/rln-delay-simulations/raw/latency_10kb.txt)).

A scenario gives each member one quantile of those arrivals, in a fixed order, and gives saro the
median. The goal's 200 members get 268 to 659 ms. The model has two limits:

- A member's delay is the same for every frame it receives. On Waku it changes from message to
  message.
- No measured rate of loss on the live network was found, so the rates of loss are assumed.

## The other scenarios

- **`50-epochs-by-saro-waku-loss-`**: saro alone adds, one member a round, and `branches` is 51,
  so a run goes on past any fork. With one adder there is no race between adders, so what splits a
  GroupV2 group is what the network loses. A GroupV3 group cannot fork with one committer: a
  member that misses a commit falls behind on the same line.
- **`grow-waku-size-50.json`**: the group stays at 50 members, since each add past 50 first
  removes the member added longest ago that is not adding in that round. The run goes toward
  epoch 1000 and ends at its first main branch (`branches` is 2). It runs on GroupV2 alone, since
  a GroupV3 group removes no member.
- **`50-epochs.json` and `50-epochs-waku.json`**: saro adds 50 members one at a time, and the
  last one posts. The second runs on Waku's delays. `tools/forks` reads the log and must find the
  group `whole`.
- **`three.json`**: three members and one post. It runs in under a second, to try the tools on.
