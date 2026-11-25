---
title: The scenarios
description: What each scenario CI runs does, how a grow step runs, and why the scenarios are shaped as they are.
---

A scenario is a JSON file in `tools/sim/scenarios/`: how many members, what they do, and what the
network does to them ([Scenario format](/chat-sim/guide/reference/scenario-format/)). The creator
is `saro`, then come `raya`, `pax`, `mira`, `kes`, `tomo`, and `peer7` on. Each scenario CI runs
has a job:

| Scenario | Does | Run by |
|---|---|---|
| `grow-waku-loss-*.json` | the goal: 200 members on Waku's delays, up to three adders a round, toward epoch 50, at a rate of loss the name gives in percent | logos-chat's pull requests at 0.5, 1 and 5%, and its main at those and 0%; chat-sim's CI at each; the long run at 0.1% |
| `50-epochs-by-saro-waku-loss-*.json` | 51 members, saro alone adding one a round, toward epoch 50 | chat-sim's CI, and logos-chat main's long run |
| `grow-waku-size-50.json` | a group held at 50 members toward epoch 1000, at no loss | chat-sim's CI, and the long run |
| `50-epochs.json`, `50-epochs-waku.json` | 51 members seated one at a time, then one post | chat-sim's CI, and the long run |
| `three.json` | 3 members seated, one post, on fast timers | your first run |

Each `grow-waku-loss-` and `50-epochs-by-saro-waku-loss-` file has a `-groupv3` copy, the same on
a GroupV3 group. Every other scenario CI runs sets the library's own timers
(`"timers": "default"`), as logos-chat-module runs a group. A GroupV3 group keeps timers of its
own, whatever the file says.

## A group that grows

A `grow` step is the scenario's only step. Each round, it draws one to `adders` members, and each
adds the next member at once, on whatever branch it holds. With `by` in place of `adders`, that
member alone adds, one a round. A round ends when each member added holds the group and no member
that was level with its branch at the start is behind it, or when the budget runs out.

A member is **behind** when it holds a state another member has gone past. It is then waited on
no more and drawn no more until it catches up. An adder refused all round is drawn no more until
it holds another state. The draws come from the scenario's `seed`.

The states the members hold make lines. The creator's first state is on the **trunk**. Where a
state has two or more after it, the one more members hold or have gone past carries the line on,
and each other starts a branch. A branch off the trunk is a **main branch**, and one off any other
branch a **sub-branch**. With `by`, the adder's own line is the trunk.

The step ends at the start of a round once the trunk holds `epochs`, the trunk and the main
branches number `branches` or more, every member has been seated, or no member can be drawn. With
`size`, an adder whose add would take the group above `size` first removes the member seated
longest ago. Here is how one run of `grow-waku-loss-0.5.json` ended, as its `run.txt` prints it:

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

Its first fork is at epoch 5, where line 2 left the trunk, so it went 5 epochs without a fork. The
run ended when the trunk and four main branches made five.

## The goal

The `grow-waku-loss-` scenarios ask how many epochs a growing group lasts before it first forks.
Each part of the file has a reason:

- **Up to three adders a round, on any branch.** Two adders can commit for one epoch, which a
  GroupV3 group needs to fork. Members on a branch keep adding, as members of a real group would.
- **Toward epoch 50.** A run that never forks goes 50 epochs without a fork: the goal.
- **Five branches.** A run goes on past its first fork, and ends once the trunk and four main
  branches run side by side. That bounds a run that forks early.
- **200 members.** The step ends once every member is seated, so there is a member for each add
  on the way to epoch 50.
- **The same loss for Welcomes.** Each member loses that share of the frames it receives, Welcomes
  among them. A joiner whose Welcome is lost never joins.
- **Waku's delays.** Each member receives every frame late, by a delay of its own (below).

The rates are chosen for what a run can show. At 0% a fork is the code's alone, and it is the
slowest rate to run, so main runs it on three seeds and a pull request not at all. At 0.5, 1 and
5%, GroupV3 forks on each of seeds 0 to 2, so a change in when it forks can show. At 0.1% two of
those seeds stay whole.

At the revision chat-sim pins, about half of the members added to a GroupV3 group never join, even
at no loss: of a round's adds, one is applied ([How a run
works](/chat-sim/guide/understand/how-a-run-works/#the-two-groups)).

## Waku's delays

The delays come from the relay simulation the Waku team published as `rln-delay-simulations` in
[logos-messaging/research](https://github.com/logos-messaging/research/tree/d7167bcedb1142e3d7462dcf24a30f65b1a3ef90/rln-delay-simulations)
([#42](https://github.com/logos-messaging/research/issues/42)). It ran 1000 `nwaku` nodes in Shadow,
each with 6 peers in its mesh and 100 ms on each link
([`shadow.yaml`](https://github.com/logos-messaging/research/blob/d7167bcedb1142e3d7462dcf24a30f65b1a3ef90/rln-delay-simulations/shadow.yaml)),
and lost no packet. Its 10 KB messages gave 8991 arrivals, from 263 to 673 ms, with a median of
518 ms ([`raw/latency_10kb.txt`](https://github.com/logos-messaging/research/blob/d7167bcedb1142e3d7462dcf24a30f65b1a3ef90/rln-delay-simulations/raw/latency_10kb.txt)).

A scenario gives each member one of those arrivals' quantiles, in a fixed order, and gives saro the
median. Its 200 members get 268 to 659 ms, the 51 of a fifty-epochs scenario 284 to 644 ms, and the
3001 of `grow-waku-size-50.json` 263 to 673 ms. A member's delay is the same for every frame it
receives, where on Waku it changes from message to message. No measured rate of loss on the live
network was found, so the rates of loss are assumed.

## The other scenarios

- **`50-epochs-by-saro-waku-loss-`** has saro alone add, one member a round, with `branches` at 51,
  so a run goes on past any fork. One adder takes the race between adders out of a run, so what
  parts a GroupV2 group is what the network loses. With one committer a GroupV3 group cannot fork:
  a member that misses a commit falls behind on the same line.
- **`grow-waku-size-50.json`** keeps the group at 50 members: each add past 50 first removes the
  member seated longest ago. It runs toward epoch 1000 and ends at its first main branch
  (`branches` 2). It runs on GroupV2 alone, since a GroupV3 group removes no member. It is the
  longest job of the long run.
- **`50-epochs.json` and `50-epochs-waku.json`** seat 51 members, saro first and then 50 more one
  at a time, and have the last one post, the second on Waku's delays. Each epoch after the first
  takes 70 s: 10 s to vote and the 60 s window. Their logs go to `tools/forks`, which must find the
  group `whole`.
- **`three.json`** seats three members and has one post: a run of under a second, to try the
  tools on.

`tools/sim/expected/` holds what each run of the files chat-sim's CI sweeps came to at the
revision of logos-chat that chat-sim pins
([CONTRIBUTING.md](https://github.com/logos-messaging/chat-sim/blob/master/CONTRIBUTING.md)).
