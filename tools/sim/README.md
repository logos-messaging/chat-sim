# sim

How does a group fare when every member runs a delivery node of its own?

`sim` reads a scenario, a file that says how many members there are, what they do and what the
group is expected to come to. It starts the repository's mock registry and a headless `chat-cli`
peer for each member, each with its embedded delivery node on a local network that holds the
run's nodes and no other. It links the nodes, has the peers do the scenario's steps, ends every
process it started, and has [`forks`](../forks/README.md) hold the report of the peers' logs
against what the scenario expects. A scenario that names a fleet preset has the nodes join that
fleet instead (see [On a fleet preset](#on-a-fleet-preset)). Given `--image`, it runs the
registry and each peer in a container of its own, on a network of containers whose wire can delay
and lose what each member sends (see [In containers](#in-containers)). Given `--one-process`, it
starts no peer and has a runner do the scenario with every member in one process, on a virtual
clock (see [In one process](#in-one-process)).

It needs Python 3.9 or newer and no package beyond the standard library, and a `chat-cli` built
with the delivery library (see [chat-cli](../../bin/chat-cli/README.md#building)). A run in
containers needs the image in its place, and Docker or Podman. A run in one process
needs the runner in its place.

## Running it

From the repository root:

```sh
python3 tools/sim run tools/sim/scenarios/three.json --chat-cli target/release/chat-cli --out tmp/three
```

```console
3 members, fast group timers, largest message 512 KiB
whole: 3 of 3 members at the tip
the report is as expected
load average 1.71 at the start, 1.71 at the end

node   ready   start  dials  wired  mesh  end  mesh  dialed  failed  refused  dropped  errors  exit
saro  0.095s  0.094s      0      2     2    2     2       0       0        0        0       0     0
raya  0.190s  0.095s      1      2     2    2     2       1       0        0        0       0     0
pax   0.287s  0.097s      2      2     2    2     2       2       0        0        0       0     0

batch  joiners  members  joined  seated  Welcome bytes
1            2        3  0.536s  0.535s       2 x 7121

post  from  read by  median   worst  delivered   worst  lookup
1      pax   2 of 2  0.005s  0.006s     0.004s  0.005s  0.000s
```

| Status | Meaning |
|---|---|
| 0 | Every step was done, and the report of the logs is as the scenario expects. |
| 1 | A step was not done, or the report differs from what the scenario expects. |
| 2 | The command line or the scenario is refused. No registry and no peer was started. |
| 128 + n | Signal n interrupted the run: SIGINT, SIGTERM or SIGHUP. It ended every process it started and wrote what it had measured. |

A step that does not come about ends the steps and not the run: a budget that runs out or a peer
that ends leaves the steps after it undone, and the logs are collected and checked all the same.
The run then says both, what stopped the steps and what the check found. Here three members
run the library's own timers, under which a batch takes a minute and a half, on a budget of 20
seconds:

```console
3 members, default group timers, largest message 512 KiB
stopped after 0 of 2 steps: saro: no line of a new roster within the budget
not enough data: 1 of 3 members at the tip
not as expected: verdict: not enough data, expected whole
not as expected: raya (e4cd360b): not in the report, expected tip
not as expected: pax (e879c7d6): not in the report, expected tip
not as expected: post 1 of pax (e879c7d6): not in the report
```

A run that made no conversation, or that a signal interrupted, is not checked. Neither is one
whose check was not made: when `forks check` ends without an answer, with a status of its own or
by a signal, the run says `not checked:` and how the check ended, and exits 1. A check that was
not made is never told as a difference of the report.

| Flag | Sets |
|------|------|
| `--chat-cli PATH` | The binary every peer runs. A path that is no file, or a file that cannot be run, is refused |
| `--one-process PATH` | The runner that does the scenario in one process, given in place of `--chat-cli`: a command line has one of `--chat-cli`, `--one-process` and `--image` (see [In one process](#in-one-process)). A path that is no file, or a file that cannot be run, is refused |
| `--image IMAGE` | The image the registry and each peer run in, each in a container of its own, given in place of `--chat-cli` (see [In containers](#in-containers)) |
| `--engine ENGINE` | The container engine that runs `--image`: `docker`, the default, or `podman`. One that is not on `PATH` is refused, and so is the flag without `--image` |
| `--out DIR` | The run's directory, which the run makes and which must not exist. One that cannot be made is refused |
| `--share ADDRESS` | The share address of a `forks` collector, `...?token=TOKEN`: once the run is checked, every peer's log is sent there with `forks share`, and the run gives the address of the conversation's page on the collector |

The share address holds the collector's token. The run hands it to `forks share` and writes it
nowhere: the page's address is given without it, and what `forks` complains of when the logs
could not be sent is kept with `TOKEN` where the token stood. Logs that could not be sent do not
change the status. `forks` is shown the address before anything starts, and one it does not take
is refused with its reason, since no run could share to it.

On Linux a run of many peers is best given a network namespace of its own, where its nodes
share ports and loopback with nothing else on the machine and cannot reach any other network:

```sh
unshare -rn sh -c 'ip link set lo up && exec "$@"' sim \
  python3 tools/sim run tools/sim/scenarios/fifty-sized.json --chat-cli target/release/chat-cli --out tmp/fifty
```

A scenario with `preset` is not started this way: its nodes have to reach the fleet (see
[On a fleet preset](#on-a-fleet-preset)). Containers keep a run apart as well, and on a Mac
too (see [In containers](#in-containers)).

## The scenario

A scenario is a JSON object. This one seats five members in two steps and has posts sent between
them:

```json
{
  "members": 5,
  "timers": "fast",
  "max_message_kib": 512,
  "budget": 180,
  "steps": [
    {"step": "seat", "to": 3},
    {"step": "post", "from": "raya"},
    {"step": "seat", "to": 5, "batch": 1},
    {"step": "post", "from": "all"}
  ]
}
```

| Field | Default | Holds |
|-------|---------|-------|
| `members` | | How many peers the run starts, 1 to 65534: every node listens on a TCP port of its own, and the registry on one more. They are named `saro`, `raya`, `pax`, `mira`, `kes` and `tomo`, written in lower case, and from the seventh on `peer7`, `peer8` and so on, in the order they start. `saro` creates the group. |
| `steps` | | What the members do, in order: one step or more. A later step is named by its place in the list, from 0: a refusal says `steps[2]`. |
| `timers` | `fast` | The group timers of every peer: `fast` or `default`, a set of chat-cli's `--group-commit`, or an object of durations in milliseconds by the names the group library gives them, each of which stands over that of the `default` set (see [chat-cli](../../bin/chat-cli/README.md#group-timers)). A duration is a whole number up to 2^64 - 1, the most its flag holds. |
| `max_message_kib` | `512` | The largest message a node takes, as chat-cli's `--max-message-kib`: 1 to 2^32 - 1, the most the flag holds. Refused with `preset`, which fixes the largest message. |
| `preset` | | The fleet preset every node joins, by its name, as chat-cli's `--preset`: `logos.test`. Without it the nodes are a network of the run's own (see [On a fleet preset](#on-a-fleet-preset)). A name that is no text, an empty one, or one that holds a character that cannot be printed, as a line break is, is refused. Which names there are is the delivery library's to say. |
| `budget` | `180` | How many seconds each wait may take: a peer's start, the wiring, a batch, a post. A number above 0. |
| `seed` | `0` | What a run in one process draws every random number from: the ids of the group and of its members, their keys, and all else the core draws (see [In one process](#in-one-process)). A whole number from 0 to 2^64 - 1. A peer that is a process draws from its OS, so in any other run it has no part. |
| `network` | | A model of a lossy network that every peer puts what it receives through (see [The network model](#the-network-model)). Without it no peer has one. |
| `wire` | | The delay, the jitter and the loss on what each member sends, in a run in containers (see [The wire](#the-wire)). Refused in a run without `--image`. |
| `expect` | | How the outcome differs from a whole group, as below. |

A step is one of six, and the last three are refused in a scenario without `network`:

| Step | Fields | Does |
|------|--------|------|
| `seat` | `to`, and `batch` (default `10`) | The creator adds the members not yet seated, in the order they started, until the group holds `to`, `batch` of them with one `/add`. It is done when the log of each member holds the epoch record of the new roster. `to` is above what is seated by then and at most `members`. |
| `post` | `from` (default `last`) | A member posts and the others seated wait for the post. `from` is the name of a member seated by then, `last` for the last one seated, or `all` for every member seated, one after another in the order they started. |
| `hold` | `seconds` | The run does nothing for `seconds`, a number above 0, and asks every node for its state before and after (see `holds[]` in [What it writes](#what-it-writes)). |
| `cut` | `between` | Two sides of the group are cut off from each other. `between` holds the two sides, each a list of the names of one or more members seated by then, and a member is named once. Each member of both sides is told to drop what the members of the other side publish to the group. No cut is made while one is in force. |
| `heal` | | The cut in force ends: each member of its two sides is told to drop nothing of the other side's any longer. |
| `withhold` | `from` | The member `from`, seated by then, is told to drop the next commit candidate that reaches it, which is one of the batch the next step seats (see [A withheld candidate](#a-withheld-candidate)). |

A member no step seats runs its node and is in no group.

A batch defaults to 10 so that a run shows a Welcome at several group sizes, and so that a batch
stays under the 50 proposals the group puts into one commit.

A scenario that says nothing of what to expect expects the group `whole`, every member the steps
seat at the `tip`, and every post `read` by every other member seated when it was sent. `expect`
says only how the outcome differs:

```json
"expect": {
  "verdict": "whole, members behind",
  "positions": {"pax": "behind"},
  "posts": {"pax": "unreadable"}
}
```

| Field of `expect` | Holds |
|-------------------|-------|
| `verdict` | The verdict of the fork report, in its own words, with the epoch where it names one: `split at epoch 3`. A text no report gives for a verdict is refused. |
| `positions` | For a member by its name, its position in the report: `tip`, `behind`, `lineage unknown`, `removed`, `never joined` or `no data`. A report has `ended` as well, and never for logs read from files, so it cannot be expected. |
| `posts` | For a member by its name, what becomes of each post another member sends while it is seated: `read`, `unreadable`, `no record`, `no log` or `not a member`. |

The run waits for a roster on the creator and on each member expected at the `tip`, and for a
post on each member expected to read it. The other waits are not made, so a scenario that expects
a member to fall behind is not stopped by it.

Every field is checked before anything starts, and a scenario that is refused is named with what
is wrong in it:

```console
$ python3 tools/sim run scenario.json --chat-cli target/release/chat-cli --out tmp/run
sim: error: scenario.json: `from` of steps[1] names mira, who is not seated by then
```

A field the scenario format does not have is refused as well. So is a field the file says
twice, a number above the most its field takes, and a file that is JSON to Python alone, with
`NaN` or `Infinity` in it.

What a duration may be is the library's to say: chat-cli refuses a set of timers the library
does not take, the first peer then ends at its start, and the run stops with the last line of
its standard error, which is the library's reason:

```console
stopped after 0 of 2 steps: saro ended with status 1: chat-cli: Invalid conversation config: freeze_duration must be non-zero
```

The same goes for a `preset` the delivery library does not know.

`scenarios/` holds the scenarios the repository keeps: `three.json`, which CI runs,
`50-epochs.json`, `50-epochs-waku.json` and `50-epochs-waku-loss.json`, fifty epochs as
logos-chat-module runs a group (see
[Fifty epochs as the module runs them](#fifty-epochs-as-the-module-runs-them)), `twenty.json`
and `fifty-sized.json`, two sizes runs have been measured at (see
[The floor on one machine](#the-floor-on-one-machine)), and `lost-candidate.json` and
`lossy.json`, five members on a network model (see [What a lost frame does](#what-a-lost-frame-does)).
`lossy-wire.json` has a wire, and CI runs it and `three.json` in containers (see
[In containers](#in-containers)). `fleet-five.json` needs the `logos.test` fleet and is no part
of CI (see [On a fleet preset](#on-a-fleet-preset)).

### The network model

`network` has every peer put what it receives through chat-cli's model of a lossy network (see
[chat-cli](../../bin/chat-cli/README.md#the-network-model)). The model stands between a peer's
node and its client: the nodes of a run hand each other every frame, and a frame a model drops
is one its node took.

```json
"network": {"seed": 1, "loss": 0.05, "delay_ms": {"pax": 40}}
```

| Field of `network` | Default | Holds |
|--------------------|---------|-------|
| `seed` | | What the model's drops follow from, with the name of the peer: a whole number from 0 to 2^64 - 1. |
| `loss` | `0` | The share of the frames a peer receives that its model loses, from 0 to 1. A Welcome is lost at `welcome_loss` instead. |
| `welcome_loss` | `0` | The share of the Welcomes a peer receives that its model loses, from 0 to 1. |
| `delay_ms` | `0` | How long a model holds each frame it lets through, in milliseconds: one whole number for every member, or an object of them by the names of members, in which a member that is not named has 0. A whole number is at most 2^64 - 1, the most its flag holds. |

What a rate or a delay may be beyond its form and its range is chat-cli's to refuse, as it is
with a duration of `timers`. A delay stands against the group timers, and one that the `fast`
set does not bear is taken all the same (see [A delay](#a-delay)).

### A withheld candidate

Every steward of a group makes a commit candidate in a round, and the group applies one of them.
A `withhold` step has a member lose the first candidate that reaches it in the round of the next
batch, and the run then comes to one of two outcomes. The group applied another candidate, the
member applied it as well, and the group is whole. Or the group applied the candidate the member
lost, and the member is alone in a state of its own. Which of the two a run comes to goes by
which candidate reached the member first, and no scenario picks it. So a scenario says both:

```json
"expect": {
  "applied": {
    "verdict": "split at epoch 4",
    "posts": {"pax": "unreadable"}
  }
}
```

`applied` of `expect` is the outcome of a run whose group applied the candidate withheld, with
the `verdict`, the `positions` and the `posts` of `expect`. What `expect` holds beside it is the
outcome of a run whose group did not. `applied` is refused in a scenario without a `withhold`
step.

At its end the run asks the member's model whether the group applied the candidate (the
`applied` of chat-cli's `/network`), and the report is held to the outcome that answer names and
to no other. The answer and the logs are two tellings, so a run that split while the answer says
not applied, or stayed whole while it says applied, differs from what is expected in its
verdict.

The model answers `applied` once the steward that made the candidate has broadcast a Welcome,
which a steward does for a commit of its own that it merged. One batch follows the step, so
that commit is the candidate withheld, and the answer stands for the group as long as its
stewards merge one commit. Where a delay had them merge two, the answer was `applied` while
the member was not alone, and the steps stopped at that batch (see [A delay](#a-delay)).

A `withhold` step stands for these two outcomes and no third, so it is refused where the
scenario would make more of them:

- a second `withhold` step, a `cut` step, or a `loss` or a `welcome_loss` above 0;
- a step right after it that is not a `seat` step or that seats more than one batch, or a
  second `seat` step after that one;
- a `post` step after it from the member itself, or from `all`;
- a position of the member in `expect` that is not `tip`, since the run waits on the member for
  the batch.

A round in which one member alone makes a candidate has a third outcome, which no scenario
says: the member that loses that candidate is left behind. The round that seats the third
member is such a round, and a `withhold` step before its batch is not refused: the steps of
its run stop at the budget (see
[A candidate that does not arrive](#a-candidate-that-does-not-arrive)).

## A sweep

```sh
python3 tools/sim sweep tools/sim/scenarios/three.json --runs 3 --chat-cli target/release/chat-cli --out tmp/sweep
```

A sweep does one scenario `--runs` times, one run after another, each in a directory of its own
under `--out`, named by the run's number. Each run has the scenario's `seed` after that of the
run before, so that run `k` has `seed + k - 1`, and a sweep whose last run would have a seed above
2^64 - 1 is refused. So does a scenario with a network model, with the seed of its model, and
one with a wire, with the seed of its wire. It prints a row as each run ends, and then how many
runs went as expected:

```console
run  seconds  at the tip  verdict                 check
  1      2.2      3 of 3  whole                   as expected
  2      2.2      3 of 3  whole                   as expected
  3      2.2      3 of 3  whole                   as expected
3 of 3 runs went as expected
```

| Column | Says |
|--------|------|
| `run` | The run's number, which names its directory. |
| `seconds` | How long the run took, from its start to its check. For a run in one process, how far its virtual clock went, and the line above the rows says so. |
| `at the tip` | How many of the members the steps seat the report has at the tip. |
| `verdict` | The verdict of the report. |
| `check` | `as expected`, or the first difference the check found and how many more there are, or `not checked`, with how `forks check` ended when it was run and gave no answer. Then, after `; stopped:`, what stopped the steps when something did. Then `seed` and the seed of a run in one process, `network seed` and the seed of its network model, `wire seed` and the seed of its wire, and what was withheld from whom and whether the group applied it, for a run that has them. Then the address of the conversation's page when the logs were shared, or after `not shared:` why they were not. |

It takes the flags of `run`, and `--share` sends the logs of every run. A sweep exits 0 when every
run went as expected, 1 when one did not, and 2 when its command line or the scenario is refused.
A sweep that SIGINT, SIGTERM or SIGHUP interrupts stops the run in hand, which gets no row,
keeps the rows of the runs before it, says how many runs it did, and exits 128 plus the signal's
number. A run that is already ending its processes when the signal comes is finished and keeps
its row, and no run follows it. `sweep.txt` in `--out` holds what the sweep printed, and
`sweep.json` the same. Both are written again as each row is printed, so a sweep that is cut
short has left the rows of the runs that ended:

| Field | Holds |
|-------|-------|
| `runs`, `done`, `as_expected` | How many runs were asked for, how many ended, and how many of those went as expected |
| `interrupted` | The name of the signal that interrupted the sweep, or `null` |
| `load` | The load average of the machine at the `start` and at the `end` of the sweep |
| `rows[]` | For each run that ended its `run` number and `directory`, whether it is `ok`, the `differences` its check found, or `null` when it was not checked, as `unchecked` how `forks check` ended when it gave no answer, what `stopped` its steps, the `verdict`, the members `at_tip` of those `seated`, its `seconds`, the `seed` of a run in one process, the `network_seed` of its network model, the `wire_seed` of its wire and what was `withheld` as its `run.json` holds it, each `null` for a run without one, and the `page` its logs were shared at |

## What a run does

1. It starts the mock registry on a port the system picks.
2. It starts the peers one after another. Each node is told which nodes to dial, all of them
   started before it, and the next peer starts once this one has written its `ready` line. A
   scenario with `network` starts each peer with a network model: the seed, the two rates and
   the delay of that member. A scenario with `preset` starts each peer on the preset, and no
   node is told a node to dial. A peer that ends before its `ready` line stops the run. One
   that ended with status 1, as chat-cli does when it cannot start, has the last line of its
   standard error for the reason (see [chat-cli](../../bin/chat-cli/README.md#how-it-ends)).
   Any other, as one that a signal ended, has the path of that file.
3. It asks every node for its state until each holds its links and has at least four of them, or
   all of them when it has fewer, in its gossip mesh. On a preset it asks until each node holds
   one link or more, and waits for no mesh.
4. The first peer creates a group, and the steps are done in order. A batch of a `seat` step is
   seated when the log of every member waited on holds the epoch record of the new roster, and
   the steps stop when the members' records of that epoch do not name one state. A `post` step
   waits for the `message` line of every member waited on. A `cut` step waits until each member
   it names has told its app id, which a peer does as it creates or joins the group, and then
   writes each member of both sides the app ids of the other side. A `withhold` step is written
   once the step before it is done and before the `/add` of the next batch. That batch is
   seated when the member has also written the line of the candidate its model dropped: none
   within the budget stops the steps, and so does one written before the `/add`, a candidate of
   an earlier round. The steps do not stop at that batch when the member alone holds a state of
   its own and every other member waited on holds one: the steps go on without the member, and
   no post is waited for on it. A `hold` step asks every node for its state, does nothing for
   its seconds, which no budget bounds, and asks every node again.
5. It asks every peer for the state of its network model, when the scenario has one. A peer
   abandons the frames its model holds when it ends, so a model that holds one is asked again
   every 0.5 s: until it holds none, or until the member's delay has passed since the steps
   were over, or the budget where that is shorter. It asks every node for its state again, ends
   the input of every peer, and waits for each to exit. One that has not exited after 30
   seconds is killed.
6. It writes what `forks check` is to hold the report against, has `forks report` draw the
   report of the logs and `forks check` check it, and sends the logs when it was given a share
   address.

Every wait is for a line or a record and ends when its budget does, and a run whose steps stop
early still goes through steps 5 and 6. A wait also ends when a peer or the registry has ended,
and the steps then stop with the name of the one that did. A node has 10 seconds to answer for
its state. A run a signal interrupts does not ask the nodes again and is not checked.

A signal that comes once the processes are being ended changes nothing, and neither does one
that comes after, while the report is drawn and checked and the logs are sent: the run goes on
to its end and exits with the status of what it found. `forks` runs in a session of its own, so
the interrupt of a terminal does not reach it either. The one wait left then is that of
`forks share`, which gives a collector 30 seconds to answer a request.

### The wiring

Up to six nodes all link to each other. From seven up every node has six links, the mesh degree
gossipsub aims for: the nodes stand in a ring, and each links to the next and the one before
along three strides. The strides are 1, `a` and `a * a` for the smallest `a` whose cube reaches
the number of nodes, pulled in on a small ring so that the three differ. A hundred nodes have
the strides 1, 5 and 25.

Whichever end of a link starts later dials it, so every dial is to a node that is up, and no
node is dialed by more than six. A node keeps up to 50 relay peers that dialed it.

A node of a local network is ready once it holds a link for each node it was told to dial, so a
peer starts in the time its node takes to start and to dial.

## What it writes

The run's directory holds, for each peer, `<name>.out` with the lines it wrote, `<name>.stderr`
and `<name>.log`, and for the registry `registry.requests` with a line for each request it
served. `data/` is what the peers stored, and `keys/` their accounts' and installations' keys,
which a run starts without, as its registry does. `expect.json` is what the report was held
against, as [`forks check`](../forks/README.md#checking-a-report) reads it, with each member's
installation id taken from its `ready` line. `report.txt` and `report.json` are the report
`forks report` drew of the logs. A run given `--share` holds `.forks-share.json` as well, the
file in which `forks share` remembers what it sent from a directory (see
[forks](../forks/README.md#what-it-remembers)): it holds the collector's address without the
token, and is there also when nothing could be sent. `run.txt` is what the run prints, and
`run.json` holds the same and more:

| Field | Holds |
|-------|-------|
| `members`, `timers`, `max_message_kib`, `budget`, `preset`, `cluster_id` | What the run was given. Of `preset` and `cluster_id` the one that does not apply is `null`, and so is `max_message_kib` on a preset |
| `network` | The `network` of the scenario with the seed this run had, or `null` |
| `wire` | The `wire` of the scenario with the seed this run had, or `null` |
| `ok` | Whether every step was done and the report is as expected |
| `stopped` | What stopped the steps, when something did |
| `steps`, `steps_done` | How many steps the scenario has, and how many were done |
| `load` | The load average of the machine at the `start` and at the `end` of the run: over 1, 5 and 15 minutes |
| `conversation` | The id of the group, once it was created |
| `check` | Whether the report is `as_expected`, and the `differences` as `forks check` printed them: `null` when the run was not checked |
| `unchecked` | How `forks check` ended when it was run and gave no answer: its status or the signal that ended it, and the last line it wrote to standard error. `null` when the check gave an answer or was not run |
| `withheld` | What a `withhold` step withheld: `from` whom, the `app_id` of the candidate's sender and the `seat` of the steward that made it, as `minter` the member that told that app id, and whether the group `applied` it, as the member's model answered at the end (see [A withheld candidate](#a-withheld-candidate)). `null` without the step, and when the member answered nothing or had withheld nothing |
| `held_to` | Which outcome the report was held to: `applied` or `not applied`, and `null` where `withheld` is, when it was held to `expect` |
| `verdict`, `at_tip`, `seated` | The report's verdict, how many of the members the steps seat it has at the tip, and how many the steps seat |
| `page` | The address of the conversation's page on the collector the logs were shared to |
| `unshared` | What `forks share` complained of, when the logs could not be sent |
| `nodes[].installation` | The id the fork report knows the peer by |
| `nodes[].app_id` | The app id the peer told as it created or joined the group, or `null` |
| `nodes[].ready_s` | When the peer wrote its `ready` line, in seconds from the run's start |
| `nodes[].start_s` | How long the peer took from its own start to that line |
| `nodes[].dials` | The nodes it was told to dial: none on a preset |
| `nodes[].wired` | Its `peers` and `mesh_peers` once every node held the links the run waits for |
| `nodes[].end` | Its `peers`, `mesh_peers`, `dials_ok`, `dials_failed`, `publish_refused` and `inbound_dropped` after the steps |
| `nodes[].unheld` | `dials_ok` less `peers` of `nodes[].end`: the connections the node opened that are not links it holds, or `null` when it did not say one of the two (see [On a fleet preset](#on-a-fleet-preset)). Only in a run with `preset` |
| `nodes[].exit_status`, `nodes[].exit_signal` | The status the peer exited with, or the signal that ended it |
| `nodes[].ended` | `asked` when it exited with status 0 once its input had ended, `in its stop` when it then exited with another status or by a signal, `killed` when it had not exited after 30 seconds, and `early` when it ended before the run asked it to |
| `nodes[].inbound_errors` | How many `inbound_error` lines the peer wrote, by their message |
| `nodes[].unchecked_senders` | How many frames the peer's log says it decrypted and could not read because the registry gave it no answer for the sender's account: its `unreadable` records with `why=sender`. They tell of the registry and not of the group |
| `nodes[].network` | What the peer's network model answered after the steps, as chat-cli's `/network` gives it. Only in a run with `network` |
| `nodes[].network_dropped` | How many `network_drop` lines the peer wrote, by their `why`. Only in a run with `network` |
| `wired_s` | When every node held the links the run waits for, in seconds from the run's start |
| `batches[]` | For each batch its `joiners`, the `members` the group then had, `joined_s` from the `/add` to the last joiner's `joined` line, `seated_s` from the `/add` to the last member's epoch record, and as `welcome_bytes` the bytes of each Welcome the creator published for it |
| `posts[].poster`, `posts[].readers` | Who sent the post, and how many members were waited on to read it |
| `posts[].read` | From the post's input to each reader's `message` line: `count`, `median` and `worst` |
| `posts[].delivered` | From the post's input to each reader's lookup reaching the registry |
| `posts[].lookup_mean` | The mean time a reader spent on that lookup |
| `posts[].lookups` | How many lookups of the poster the registry served while the post was read |
| `posts[].unread` | The members that did not read the post within the budget |
| `holds[]` | For each `hold` step that was done its `seconds`, and under `nodes[]` each node by its `name` with what it answered `before` the hold and `after` it, as `nodes[].end` holds an answer. In a run with `preset` each node has `unheld` as well: what `dials_ok` rose by from the one answer to the other, less what `peers` rose by. A link the node lost in the hold is counted in it whenever the node had opened it. The hold then has `rate`, the sum of them divided by the nodes and by the minutes of the hold. Either is `null` when a node did not say one of the two |

Times are in seconds, and `peers`, `mesh_peers`, `dials_ok`, `dials_failed`, `publish_refused`
and `inbound_dropped` are what chat-cli's `/node` answers: the table of the peers has the two
counts of dials under `dialed` and `failed`. It has the count of `inbound_error` lines under
`errors` and how the peer ended under `exit`, and a table of the messages of those lines follows
it when there are any. In a run with `network` the first line names the seed and the two rates,
the table of the peers has under `lost` how many frames each peer's model dropped, and a line
after the verdict says what a `withhold` step withheld:

```console
5 members, fast group timers, largest message 512 KiB, network seed 1, loss 0, welcome loss 0
split at epoch 4: 5 of 5 members at the tip
withheld from pax: the candidate of raya, applied
the report is as expected
load average 3.00 at the start, 2.92 at the end

node   ready   start  dials  wired  mesh  end  mesh  dialed  failed  refused  dropped  lost  errors  exit
saro  0.107s  0.107s      0      4     4    4     4       0       0        0        0     0       1     0
raya  0.226s  0.119s      1      4     4    4     4       1       0        0        0     0       1     0
pax   0.349s  0.122s      2      4     4    4     4       2       0        0        0     1       1     0
mira  0.468s  0.119s      3      4     4    4     4       3       0        0        0     0       1     0
kes   0.591s  0.123s      4      4     4    4     4       4       0        0        0     0       1     0
```

Each `hold` step has a line of its own after the tables, with its seconds: `hold 1: 60.000s`.

On a local network the two counts of a node that others dialed rise while its links stay as
they are: a node asks nodes it dialed to dial it back, and a dial made for that is counted and
leaves no link. So a run on a network of its own writes the counts and derives nothing of them.
Over a `hold` of 60 seconds, in a run of five members that all link to each other, the node
four others had dialed went from 0 made and 0 failed to 8 and 8, and in another such run to 7
and 7, and the node that had dialed the four others and was dialed by none stayed at 4 and 0.

A member that gets a post asks the registry for the account of its sender before it shows the
post. The registry stamps each request as it arrives, so the time up to the stamp is delivery and
the time after it is the lookup. A request does not say which member made it. When each reader
made one, `lookup_mean` is the mean read time less the mean time a request arrived; when they did
not, `delivered` and `lookup_mean` are `null`.

The bytes of a Welcome are those of the envelope the creator handed its node, which libchat
writes to its log. The node adds the content topic and a timestamp before it compares the
message with its limit.

## On a fleet preset

A scenario with `preset` has every node join that fleet, where a scenario without it makes a
network of the run's own. Its nodes have to reach the fleet, so the run is started outside any
network namespace:

```sh
python3 tools/sim run tools/sim/scenarios/fleet-five.json --chat-cli target/release/chat-cli --out tmp/fleet
```

`scenarios/fleet-five.json` seats five members one at a time on `logos.test` with the library's
own timers, has the last one post, and holds for five minutes. Its budget is 400 seconds. One
machine put no more than five nodes on that fleet with every link kept (see
[The nodes of one machine](#the-nodes-of-one-machine)).

What differs from a run on a network of its own:

- Each peer is started with chat-cli's `--preset`, and without `--cluster-id`, `--static-node`
  and `--max-message-kib`. The registry stays the run's own, on loopback, so a run leaves
  nothing on a shared service but the frames its group publishes.
- Nothing is wired: each node dials the entry nodes the preset names, of which `logos.test` has
  six. The run waits until every node holds one link or more, and for no mesh. A node that
  holds none within the budget stops the run, which names it.
- The first line names the preset in place of the largest message, and the table of the peers
  has `unheld` after `failed`: the dials a node made less the links it holds. A node of a
  preset dialed every link it held, as far as one node on `logos.test` showed, so that is how
  many connections it opened that are not links it holds. A link it lost is one. So is a
  connection that never became a link, since a dial is counted as made once its connection is
  open: a node of a local network told to dial a port that took the connection and closed it
  answered one dial made and no link. So is a dial back to a node that asked for one. A count
  below 0, or more links than the preset has entry nodes, says that the node held a link it
  did not dial, and the run writes both as they are.
- The line of a `hold` step ends with what `unheld` rose by over the hold, by the node and by
  the minute of the hold.

Here the scenario ran on 2026-10-12, on the machine of
[The floor on one machine](#the-floor-on-one-machine):

```console
5 members, default group timers, preset logos.test
whole: 5 of 5 members at the tip
the report is as expected
load average 0.60 at the start, 2.39 at the end

node    ready   start  dials  wired  mesh  end  mesh  dialed  failed  unheld  refused  dropped  errors                   exit
saro   9.499s  9.499s      0      6    18    6    21       6       0       0        0        0       0  signal 11 in its stop
raya  18.252s  8.753s      0      6     1    6     1       6       0       0        0        0       0                      0
pax   26.937s  8.685s      0      6     0    6     0       6       0       0        0        0       0                      0
mira  35.555s  8.619s      0      6     1    6     5       6       0       0        0        0       0                      0
kes   44.645s  9.090s      0      6     0    6     7       6       0       0        0        0       0                      0

batch  joiners  members   joined   seated  Welcome bytes
1            1        2  62.067s  62.066s       1 x 4487
2            1        3  71.229s  71.903s       1 x 5884
3            1        4  70.980s  73.318s       1 x 7291
4            1        5  64.550s  64.549s       1 x 8687

post  from  read by  median   worst  delivered   worst  lookup
1      kes   4 of 4  0.619s  0.950s     0.618s  0.949s  0.001s

hold 1: 300.000s, unheld 0.00 a node a minute
```

Each node held its six links when its peer was ready, about 9 s after its start, and had made
six dials and no more at the end of the hold. `mesh` is counted over every shard a node is on
(see [chat-cli](../../bin/chat-cli/README.md#headless-mode)), so it may be above the links, as
it is for the creator.

Of three runs of the scenario that day two went so. The third stopped at its fourth batch,
`split at epoch 4`: four members held the epoch in one state and one member alone in another,
and every node held six links when it was ready and at its end. So a group of five may split
on a fleet with nothing wrong in its links, and the run then exits 1 as any run that is not as
expected.

The delivery library hands a node of a preset, five minutes after its start, the frames its
topics held since then that it did not get while subscribed to them, which for a member are
its group's frames from before it joined. In the run that split, the second member wrote an
`unreadable` record of a frame of epoch 0 298 s after its first log line, 5 ms before it moved
to epoch 4, and the creator moved there 63 ms later, into another state.

A run on a preset has three limits:

- It cannot be made again: the fleet's other traffic, the state of its nodes and the route to it
  are not the run's.
- The nodes of a run share one address, and a fleet scores and limits peers by address, so the
  nodes of one machine are not as many people, and past five they do not stay linked (see
  [The nodes of one machine](#the-nodes-of-one-machine)).
- The largest message is the preset's, and the Welcome of a group past it does not arrive: the
  wait for the roster runs out, and the run says so.

The run does not look at where it is started. One that cannot reach the fleet, as in a network
namespace with loopback alone, waits its budget for a first link and names every node. Three
members on a budget of 20 seconds:

```console
3 members, fast group timers, preset logos.test
stopped after 0 of 2 steps: no link within the budget: saro, raya, pax
not checked: the run made no conversation
```

### The nodes of one machine

How many nodes can one machine put on `logos.test`? One run a row but for the third, on
2026-10-12, each with a `hold` of 300 seconds after its post where it got that far:

| Nodes | Seated | Links of a node at the end | Dials made by a node | `unheld` rose in the hold, by all | The group |
|---|---|---|---|---|---|
| 1 | | 6 | 6 | 0 | |
| 5 | in one batch | 6 | 6 or 7 | 2 | whole, seated in 62 s |
| 5 | one at a time, three runs | 6 | 6 or 7 | 3 and 0, and no hold in the third | two whole, 62 to 73 s a member; one split |
| 6 | in one batch | 4 to 6 | 87 to 92 | 402, 65 to 69 a node | whole, seated in 63 s |
| 9 | one at a time | 1 to 6 | 399 to 433 in 12 minutes | no hold | the second member seated after 232 s, the third not within 400 |

No dial failed in any of them. Up to five nodes each kept the six links it began with. With a
sixth, every node of the run lost links and dialed again for as long as the run went, 13 times
a minute over the hold; with nine, a node held as few as one link and the group did not get
past two members. The delivery library's own default is five peers to an address: when a peer
past that joins, a node closes its links to the peers of that address it has held longest. The
rows agree with a fleet that runs with it. So a run of more than five nodes from one address
measures that limit and not a group of that size, and at nine it had a fleet that others use
take 300 connections a minute.

## Fifty epochs as the module runs them

`50-epochs.json` and `50-epochs-waku.json` seat fifty-one members one at a time, and then the last
one posts. Each add is a commit the members have to agree on and opens an epoch of its own, and a
group splits at a commit or holds through it: fifty members seated ten at a time make five epochs,
and these make fifty. Both are on the library's own timers, as logos-chat-module runs a group: it
sets no group timers. `50-epochs.json` loses and holds no frame. `50-epochs-waku.json` loses none
either, and holds each frame a member receives for as long as a message of Waku relay took to reach
a node in a simulation the Waku team made
([waku-org/research#42](https://github.com/waku-org/research/issues/42)):

| Of the simulation | |
|---|---|
| Nodes | 1000 `nwaku` nodes in Shadow, a mesh of 6 |
| Each link | 100 ms, which the team measured as the average of the live network; 83 Mbit/s down and 38 Mbit/s up |
| Packets lost | none, and the 10 messages it published reached all 999 other nodes |
| RLN | 150 ms to make a proof, which it slept on the publisher, and 12 ms to check one on each node |
| 10 KB messages, the first left out as the team does | 8991 arrivals: 263 ms the fastest, 507.6 ms the mean, 627 ms 95 of 100, 673 ms the slowest |

The fifty-one delays are those arrivals' quantiles at the middles of 51 equal slices, 1/102,
3/102, ..., 101/102, from 284 to 644 ms
([`raw/latency_10kb.txt`](https://github.com/waku-org/research/blob/d7167bcedb1142e3d7462dcf24a30f65b1a3ef90/rln-delay-simulations/raw/latency_10kb.txt),
with the config
[`shadow.yaml`](https://github.com/waku-org/research/blob/d7167bcedb1142e3d7462dcf24a30f65b1a3ef90/rln-delay-simulations/shadow.yaml)
of the same commit). The member that starts `k`-th, from 0, has the delay of rank
`(19k + 25) mod 51` among them, from 0 for the shortest, so that saro, which creates the group,
has the median, 518 ms, and the order of the seats is not that of the delays. Of the published
runs these are the ones on the link latency of the live network: those of the paper that
followed ([ePrint 2024/1073](https://eprint.iacr.org/2024/1073)) took 150 ms a link. No measured
rate of loss on the live network was found, and the model's delay is one for every frame a
member receives, where on Waku it differs from sender to sender and from message to message.

`50-epochs-waku-loss.json` is `50-epochs-waku.json` with 0.05 of the frames each member receives
lost, Welcomes as well. The rate is assumed, as none measured on the live network was found. A
frame the model loses never comes to its member later (see
[What a lost frame does](#what-a-lost-frame-does)), so a run of it stops at the first epoch the
group does not come through, and that epoch is what it measures. CI runs the other two.

The model is chat-cli's in a run of processes or containers, and the runner's in one process,
so both files run in every rig. In one process a run of either is the same run whenever it is
made (see [In one process](#in-one-process)), and so is one of `50-epochs.json`.

## The timers of fifty members

`scenarios/fifty-sized.json` gives five of the group's durations. Each is sized from what a group of
fifty needed on the machine of [The floor on one machine](#the-floor-on-one-machine). The needs
were read from the logs of two runs of fifty whose peers also logged the group library's own
lines: one with a consensus timeout, a batch window and a freeze of 5 s each, and one with 20 s
each, which cut nothing short. Neither run can be made again with what the repository holds:
`sim` gives every peer a log filter that lets none of those lines through, and the script that
read them is not kept. The load average of the machine was 0.9 at the start of the first
and 17 at its end, and 9.4 and 22 for the second. The worst over the members and the five
batches of both runs:

| What has to come about | Worst | Read from the logs as |
|---|---|---|
| A proposal reaches a member | 3.9 s | From the creator's opening of it to the member's vote, less the voting delay |
| A vote is decided | 8.2 s | From the opening to a member's holding the outcome, less the voting delay |
| A batch is approved | 6.5 s | From a member's first outcome of a batch to its last |
| The commits are gathered | 3.2 s | From a member's entering the freeze to its choice of a commit |

A duration is twice what it has to cover, which is the margin, up to the next half second:

| Duration | ms | Is |
|---|---|---|
| `voting_delay` | 1000 | What the two runs ran with. Nothing has to cross within it, so it is not sized. |
| `consensus_timeout` | 17500 | The voting delay and twice the 8.2 s of a vote |
| `commit_batch_window` | 13500 | Twice the 6.5 s of a batch's approvals |
| `freeze_duration` | 6500 | Twice the 3.2 s of the commits |
| `proposal_expiration` | 26000 | The consensus timeout and twice the 3.9 s of a proposal's way, up to the next second, which is what the library counts it in |

`retry_window`, `backup_takeover_window` and `recovery_auto_commit_delay` stay the library's.
They time reelection, a backup steward's standing in and recovery, and the logs of the two runs
hold no line of any of the three.

The ladder is three runs of fifty at each rung, from the `fast` set up to the library's own:

| Rung | `consensus_timeout` | `commit_batch_window` | `freeze_duration` | `proposal_expiration` | As expected | Seconds a run |
|---|---|---|---|---|---|---|
| The `fast` set | 250 | 500 | 500 | 4000 | 0 of 3 | 195, 194, 197 |
| Half of what has to be covered | 5500 | 3500 | 2000 | 8000 | 0 of 3 | 38, 37, 38 |
| What has to be covered | 9500 | 7000 | 3500 | 14000 | 3 of 3 | 58, 61, 62 |
| Twice that, `fifty-sized.json` | 17500 | 13500 | 6500 | 26000 | 2 of 3 | 87, 92, 76 |
| The library's own | 30000 | 60000 | 30000 | 600000 | 1 of 3 | 255, 358, 464 |

The `fast` set has a voting delay of 50 ms, the library's own one of 10000 ms, and the rungs
between them one of 1000 ms. The load average was between 1.1 and 25 where a rung began and
ended. A run that did not go as expected stopped at a batch its members held in more than one
state, or took up to its budget for a batch that did not come about. Three runs are few: of
twenty more with the scenario's timers, sixteen went as expected (see
[The floor on one machine](#the-floor-on-one-machine)).

## The floor on one machine

How large a group forms on one machine, and how often? These are the numbers of one machine, of
16 cores and 15 GB, taken on 2026-10-11 with every run in a network namespace of its own. They
are not those of another machine, nor of a network. The machine did other work meanwhile, so the
load average over one minute stands beside each sweep, as it was when the sweep began and ended.

| Sweep | Scenario | As expected | Seconds of a run as expected | Load average |
|---|---|---|---|---|
| 20 runs of 20 members, the `fast` set | `twenty.json` | 20 of 20 | 6.8 to 8.9 | 4.9, 10.3 |
| 20 runs of 50 members, the sized timers, in two sweeps of 10 | `fifty-sized.json` | 16 of 20 | 88 to 109 | 10.3, 18.4 and 7.1, 20.3 |
| 3 runs of 50 members, the library's own timers | `fifty-sized.json` with `"timers": "default"` | 1 of 3 | 358 | 18.4, 1.1 |

The four runs of fifty with the sized timers that did not go as expected stopped at a batch that
3 to 10 of its members did not come to hold within the budget, and their report reads
`whole, members behind`. So did one of the two with the library's own timers, for one member.
The other stopped at a batch whose members held it in two states, and its report reads
`split at epoch 4`.

In the runs that went as expected, of which the library's own timers have one:

| | 20 members, `fast` | 50 members, sized | 50 members, the library's own |
|---|---|---|---|
| A batch seated, s | 0.9 to 2.0 | 14.1 to 21.8, and three times 30.8 to 33.2 | 60.7 to 73.0 |
| A post read, median of a run, s | 0.05 to 0.22 | 0.02 to 0.41 | 0.02 |
| A post read, the worst member, s | 0.43 | 0.54 | 0.03 |

In every run of these sweeps, as expected or not, every node held its six links once the run was
wired, and all six in its gossip mesh, but for four nodes of twenty and three of fifty, in two
runs each, that had five there. A peer took 0.09 to 0.24 s to start. Twenty peers were all ready
2.3 to 2.9 s after the run's start and wired after 3.4 to 4.1 s, fifty after 5.7 to 8.8 s and
7.2 to 9.8 s.

Every node held its six links at the end of its run as well. Of twenty, every node ended with
all six in its gossip mesh, but for four with five. Of fifty with the sized timers, the creator
ended 14 of the 16 runs that went as expected with fewer than four of its links in its mesh,
and two of them with none, where every other node ended with five or six, but for one with
four. With the library's own timers the creator ended the run that went as expected with six.

A run that went as expected may hold `unreadable` records of frames that are no posts, which
the check does not look at: 9 of the 20 runs of twenty hold 1 to 33 of them, and 4 of the 16 of
fifty with the sized timers 1 to 318.

## What a lost frame does

These are the numbers of the machine of [The floor on one machine](#the-floor-on-one-machine),
taken with a release build of chat-cli, every sweep in a network namespace of its own. The
sweeps of `lost-candidate.json` and of its copies and the 20 runs of `lossy.json` were taken on
2026-10-12, and the other runs of [A loss rate](#a-loss-rate) on 2026-10-11. The machine did
other work meanwhile, so the load average over one minute stands beside each sweep, as it was
when the sweep began and ended.

### A candidate that does not arrive

`scenarios/lost-candidate.json` seats five members one at a time with the `fast` set, has pax
lose a commit candidate of the round that seats the fifth, and has kes post. It was swept 10
times, and so was each of three copies of it that withhold from saro, from raya and from mira
instead, with the post unreadable on that member:

| Withheld from | As expected | The group applied it | It did not | The candidate was that of | Load average |
|---|---|---|---|---|---|
| saro | 10 of 10 | 4 | 6 | raya, 10 times | 1.7, 1.9 |
| raya | 10 of 10 | 5 | 5 | saro, 10 times | 1.9, 1.0 |
| pax | 10 of 10 | 2 | 8 | saro 5 times, raya 5 | 1.0, 0.6 |
| mira | 10 of 10 | 6 | 4 | raya 6 times, saro 4 | 0.6, 0.5 |

In each of the 40 runs the five peers wrote one `network_drop` line between them: the
member's, `withheld`, of a candidate that another member made. The report read
`split at epoch 4` in the 17 runs whose member answered that the group applied the candidate,
with the member alone on a branch of that epoch, and `whole` in the other 23. No run came to a
third outcome, and every candidate withheld was that of saro or of raya.

A run took 4.6 to 4.8 s. The batch after the step was seated in 1.06 to 1.10 s whichever way
the run went, and the three batches before it in 0.52 to 0.60 s each. Where the group applied
the candidate, the post was waited for on the three other members, and each of the five peers
wrote one `inbound_error` line, `demls error: MLS error: An error occurred during AEAD
decryption.`. Where it did not, the post was read by all four and no peer wrote one.

The seed does not pick the outcome: no rate is set, so no draw of the model loses a frame.
`lost-candidate.json` was swept 10 times more from seed 1, at a load average of 1.7 and 0.8.
All 10 runs went as expected, 7 with the candidate applied, and 5 of the 10 seeds came to one
outcome in this sweep and to the other in the first.

In the round that seats the fourth member, as in the one that seats the fifth, saro and raya
each make a candidate, and in the round that seats the third saro alone does. The runs that
show it were taken on 2026-10-12, at a load average of 0.2 to 0.6. A copy of the scenario for
four members, which withholds a candidate of the round that seats the fourth, was swept 5
times for each of saro, raya and pax. All 15 runs went as expected, 2 of each 5 with the
candidate applied, and the candidate was raya's but for the 5 withheld from raya, which were
saro's. A copy for three members, which withholds a candidate of the round that seats the
third, was swept 3 times for saro and 3 times for raya on a budget of 33 s, and no run went as
expected:

| Withheld from | What the member's model dropped | The report | What stopped the steps |
|---|---|---|---|
| saro | Nothing: no candidate reached saro | `whole`, 3 of 3 at the tip | No line of a candidate withheld within the budget |
| raya | The candidate of saro, which the model answered the group applied | `whole, members behind`, with raya at epoch 1 | No roster on raya within the budget |

### A loss rate

`scenarios/lossy.json` seats five members one at a time with the `fast` set, each peer losing
0.05 of the frames it receives, and has the last member post. The rate is the highest of four
at which a run still went as expected. Each was swept 6 times from seed 1, on a budget of 20 s:

| `loss` | `whole`, as expected | Split | `whole, members behind` | `whole`, not as expected | Load average |
|---|---|---|---|---|---|
| 0.02 | 4 | 1 | 0 | 1 | 2.6, 1.5 |
| 0.05 | 2 | 2 | 0 | 2 | 1.5, 0.6 |
| 0.1 | 0 | 2 | 0 | 4 | 0.6, 9.4 |
| 0.2 | 0 | 1 | 2 | 3 | 9.4, 30.2 |

Then `lossy.json` was swept 20 times, with `--share` to a collector started in the namespace
of the sweep. The load average was 0.5 and 0.1:

| Outcome | Runs | What the lines of the peers show |
|---|---|---|
| `whole`, as expected | 10 | A run took 4.3 to 4.8 s |
| `split at epoch 4` | 3 | A member lost a commit candidate and is alone on a branch of that epoch: mira once, and raya twice |
| `whole, members behind` | 1 | raya lost a frame of saro in the round that seats the fourth member, and stayed at epoch 2 |
| `whole`, a post that was not read | 3 | The member that did not read the post lost its frame |
| `whole`, a batch that did not come about | 3 | A frame of the round that seats the third member was lost, one of saro by raya twice and one of raya by saro once, and neither came to hold that roster |

The models dropped 61 of the 1464 frames that arrived, which is 0.042 of them. Each of the 10
rows that did not go as expected ends in the address of its page, and the report the collector
gives at that address has the verdict of the row.

Nothing a model lost came to its member later, so a wait that a lost frame leaves open runs to
the end of its budget. `lossy.json` has a budget of 33 s for that, in place of 180. It covers
once each window the `fast` set leaves at the library's own, 20 s for a backup steward, 5 s for
a round of recovery and 5 s before recovery commits, then a round of the `fast` set, 1.3 s, and
the 1.1 s of the slowest batch that came about in these runs. Four waits that were given 120 s
ran out all the same: two rosters on a member, one on the creator and one post.

A seed does not make a run with a loss rate again. Seeds 3 and 4 were each run twice at a loss
of 0.1, and neither came to one outcome twice.

### A delay

Five copies of `lost-candidate.json` hold a member's frames for a delay. The first has a delay
of 300 ms on pax, the member the candidate is withheld from, and a budget of 33 s. It was
swept 8 times at a load average of 0.1 and 1.1, and all 8 runs went as expected, 6 with the
candidate applied. In each of the 6 the model of pax held the frame of the post when the steps
were over, and had passed it on when it gave its last answer.

The other four have the delay on saro, the creator, and were swept 4 times each:

| Timers | Delay on saro | Budget | As expected | What the runs came to | Load average |
|---|---|---|---|---|---|
| The `fast` set | 100 ms | 33 s | 4 of 4 | The candidate was applied in 1 run | 1.1, 0.9 |
| The `fast` set | 200 ms | 33 s | 0 of 4 | No third member: saro and raya stayed at epoch 1 until the budget ended, and no model had dropped a frame or held one | 0.9, 1.8 |
| The `fast` set, but for a consensus timeout of 3000 ms and a proposal expiration of 8000 ms | 450 ms | 40 s | 4 of 4 | The candidate was applied in 2 runs | 1.8, 4.2 |
| The same | 550 ms | 40 s | 0 of 4 | In 3 runs the round that seats the fourth member left saro and mira in one state and raya and pax in another, before the step. In 1 run pax lost the candidate of raya and its model answered `applied`, while pax held the state of saro and kes, and raya and mira held another | 4.2, 2.8 |

Why the `fast` set seats no third member at a delay of 200 ms was not traced. The delay of
550 ms is longer than the freeze of 500 ms, and no frame was dropped in the 3 runs that split
before the step.

## In containers

```sh
nix build .#sim-image && ./result | docker load
python3 tools/sim run tools/sim/scenarios/three.json --image logos-chat-sim:latest --out tmp/three
```

`.#sim-image` is an image of Linux with `chat-cli` built with the delivery library, Python for the
registry, and `tc` for the wire. `./result` writes it for `docker load`, or for `podman load`.
`--image` stands in place of `--chat-cli`, and `--engine podman` has Podman run it in place of
Docker. `sweep` takes both flags as `run` does.

The run makes a network of its own, `sim-<id>`, that reaches nothing outside it. Its subnet is
the `/24` of `10.213.0.0/16` the id picks, or the next one the engine does not say is taken:
Docker takes a container's address only on a network it was given the subnet of. On it the
registry and then each peer run in a container of their own, the registry at the first address
after the network's gateway and each peer at the next, which its node listens on and is dialed
at (chat-cli's `--node-ip`). A container runs as the user who runs `sim`, with the run's
directory mounted at the path it has on the machine, so a run in containers writes what a run of
processes writes, where it writes it. The containers and the network carry the label
`logos-chat-sim=<id>`, and the run removes them once its processes ended, as it does when a
signal interrupts it. A run that was killed leaves them:

```sh
docker rm -f $(docker ps -aq --filter label=logos-chat-sim)
docker network rm $(docker network ls -q --filter label=logos-chat-sim)
```

```console
3 members, fast group timers, largest message 512 KiB, containers of logos-chat-sim:latest on podman
whole: 3 of 3 members at the tip
the report is as expected
load average 0.24 at the start, 0.24 at the end

node   ready   start  dials  wired  mesh  end  mesh  dialed  failed  refused  dropped  errors  exit
saro  0.362s  0.362s      0      2     2    2     2       0       0        0        0       0     0
raya  0.708s  0.346s      1      2     2    2     2       1       0        0        0       0     0
pax   1.054s  0.346s      2      2     2    2     2       2       0        0        0       0     0

batch  joiners  members  joined  seated  Welcome bytes
1            2        3  0.540s  0.540s       2 x 7121

post  from  read by  median   worst  delivered   worst  lookup
1      pax   2 of 2  0.009s  0.009s     0.008s  0.008s  0.001s
```

The engine ends a container with 128 and the number of the signal that ended what ran in it,
and the run tells a peer's container that ended so as the signal, as it tells a process. Beyond
what [What it writes](#what-it-writes) lists, `run.json` holds:

| Field | Holds |
|-------|-------|
| `rig` | `containers`. A run of processes has no such field |
| `engine`, `image` | What ran the containers, and what they ran |
| `nodes[].ip` | The address the peer's node listened on |
| `unremoved` | What the engine refused as the run removed its containers and its network, or `null`. `run.txt` says it after `not removed:` |

A scenario with `preset` is refused, since a network of containers reaches no fleet:

```console
sim: error: --image: the scenario has `preset`, and a network of containers reaches no fleet
```

On the machine of [The floor on one machine](#the-floor-on-one-machine), one run of
`fifty-sized.json` in Podman containers went as expected on 2026-10-14. A peer took 0.33 to 0.39 s to
start, and the fifty were wired after 19.4 s. A batch was seated in 14.5 to 19.7 s, and the
worst member read the post after 0.043 s. Nine peers ended by signal 11 in their stop.

### The wire

`wire` has every peer's container delay and lose what its node sends. Once a peer is ready, the
run has `tc` put `netem` on the interface of its container, with the wire's delay, jitter and
loss. A packet between two members is delayed by the wire of the one that sends it, so a round
trip has the delays of both.

```json
"wire": {"seed": 1, "delay_ms": 40, "jitter_ms": 10, "loss": 0.02}
```

| Field of `wire` | Default | Holds |
|-----------------|---------|-------|
| `seed` | | What the loss and the jitter of each member's interface follow from, with the member's name: a whole number from 0 to 2^64 - 1. |
| `delay_ms` | `0` | How long each packet a member sends is held, in milliseconds: one whole number for every member, or an object of them by the names of members, in which a member that is not named has 0. |
| `jitter_ms` | `0` | How much more or less than its delay a packet is held, in milliseconds. |
| `loss` | `0` | The share of the packets a member sends that are lost, from 0 to 1. |

The nodes link over TCP, which sends a lost packet again, so a loss on the wire makes a frame
late and not lost while its link holds. A frame the network model's `loss` takes is lost: the
model drops what a node hands its client. A scenario may have both. A `tc` that refuses, as one
does on a kernel without `netem`, stops the run with what it said.

Five members on that wire, `lossy-wire.json`, read each post in about 0.09 s, where the same
five on a bare wire read each in 0.003 to 0.012 s:

```console
5 members, fast group timers, largest message 512 KiB, containers of logos-chat-sim:latest on podman, wire seed 1, delay 40 ms, jitter 10 ms, loss 0.02
whole: 5 of 5 members at the tip
the report is as expected

post  from  read by  median   worst  delivered   worst  lookup
1     saro   4 of 4  0.094s  0.111s     0.093s  0.111s  0.001s
2     raya   4 of 4  0.092s  0.110s     0.092s  0.110s  0.001s
3      pax   4 of 4  0.096s  0.118s     0.095s  0.117s  0.001s
4     mira   4 of 4  0.091s  0.107s     0.090s  0.106s  0.001s
5      kes   4 of 4  0.087s  0.092s     0.087s  0.092s  0.001s
```

### On a Mac

An engine on a Mac runs its containers in a Linux virtual machine, and the image is one of
Linux, for the machine's architecture. It is built in a container of the `nixos/nix` image, from
the repository root:

```sh
docker run --rm -v "$PWD":/src -w /src nixos/nix sh -c "
  printf '[safe]\n\tdirectory = /src\n' > /root/.gitconfig &&
  nix --extra-experimental-features 'nix-command flakes' --accept-flake-config \
    build .#sim-image -o /tmp/image && /tmp/image" | docker load
```

Nix reads a checkout another user owns only when Git's `safe.directory` names it, and a mounted
checkout can be another user's to the container. No cache holds the delivery library for arm64
Linux, so on Apple silicon the first build builds it from its sources. The peers have the CPUs
and the memory the engine's settings give its virtual machine.

## In one process

```sh
python3 tools/sim run tools/sim/scenarios/three.json --one-process target/release/harness-run --out tmp/three
```

`--one-process` names `harness-run`, the runner of the core's integration tests (see
[A plan on a virtual clock](../../core/integration_tests_core/README.md#a-plan-on-a-virtual-clock)),
and stands in place of `--chat-cli`. The members are then clients in that one process, on a clock
the runner moves, and no registry and no peer is started. `sim` writes the scenario as the
runner's plan, runs the runner to its end, and holds what it left to the scenario as it holds a
run of processes: by the same expectation, with `forks report` and `forks check`, with the two
outcomes of a `withhold` step, and with `--share`. `sweep` takes the flag as `run` does.

```console
3 members, fast group timers, one process, 1.350s on a virtual clock in 0.016s of wall time, seed 0
whole: 3 of 3 members at the tip
the report is as expected
load average 1.48 at the start, 1.48 at the end

member  installation  errors
saro        02680b98       0
raya        fd24df45       0
pax         530d0b04       0

batch  joiners  members  seated
1            2        3  1.250s

post  from  read by  median   worst
1      pax   2 of 2  0.000s  0.000s
```

A command line with both flags or with neither is refused. So is a scenario with `preset`, since
one process joins no fleet:

```console
sim: error: --one-process: the scenario has `preset`, and one process joins no fleet
```

What differs from a run of processes:

- Time is virtual. The runner moves its clock a step of 50 ms at a time and waits for nothing,
  so a run takes the time its work takes and not the time its timers count. Every duration the run
  gives is of that clock, but for the wall time on the first line, and `budget` is seconds of
  that clock: what the waits of one batch or of one post may take together. The command waits
  for the runner as long as it runs. A `hold` step moves the clock on by its seconds, and no
  node is asked for its state.
- No delivery node is between the members: no link, no mesh and no gossip, and no need for a
  network namespace. A frame is lost or late where the network model makes it so and nowhere
  else. `max_message_kib` has nothing to act on, and the first line says so when the scenario
  sets one other than the default: `largest message 64 KiB not applied`.
- A member is not handed the frames it published itself. A peer's node hands them back, and
  the peer's model numbers the invites among them (see
  [chat-cli](../../bin/chat-cli/README.md#the-network-model)). So the `n`-th frame of a member
  is another frame here, and what the models count is not compared between the two.
- There is one log. `group.log` holds the trace records of every member, stamped by the virtual
  clock, which starts at 2026-01-01T00:00:00Z in every run. The report takes it for one run that
  holds every installation, and each member's installation id is taken from the runner's result.
  `report.txt` and a shared page name every installation `group`, after the log, and `run.txt`
  has each member's name beside its installation id.
- An `/add` or a post that the group refuses is tried again after each step of the clock, until
  the budget has run out. A run of processes stops at the refusal.
- The reason a member rejected a frame is in the words of the core's error,
  `DeMlsError(Mls(ProcessMessage(ValidationError(UnableToDecrypt(AeadError)))))`, where the
  `inbound_error` line of a peer has chat-cli's, `demls error: MLS error: An error occurred
  during AEAD decryption.`

The steps stop where the runner stops them, with its reason: a roster or a post that a member
waited on did not come to hold or to read within the budget, a batch left the group split, or no
candidate was withheld in the batch after a `withhold` step. The log is judged all the same. A
runner that ends with a status other than 0, or by a signal, stops the run with how it ended and
the last three lines it wrote to standard error, or that it said nothing there, and what it
left for a result is not read. A result that lacks a field the run reads of it, or holds
something else in its place, is not read either, and the run stops with the name of the field:
`the runner left a result that cannot be read: members is missing`. A run that read no result
is not checked. Here the library refuses a set of timers:

```console
3 members, group timers freeze_duration 0 ms, one process on a virtual clock
stopped after 0 of 2 steps: the runner ended with status 2: harness-run: tmp/run/plan.json: timers: Invalid conversation config: freeze_duration must be non-zero
not checked: no result of the runner was read
```

What a number of seconds may be beyond its form is the runner's to say, as a duration of
`timers` is the library's. `sim` takes any number above 0 for `budget` and for the `seconds` of
a `hold` step. The runner refuses a plan with 2^64 seconds or more for either, and one with a
hold of less than half a nanosecond, and the run stops in the same way, with
`budget_s is not a number of seconds, 0 or more` or
`steps[1].seconds is not a number of seconds above 0` for the runner's reason.

A signal that interrupts the command ends the runner, and the run is not checked.

A scenario and its seeds give one run, whenever it is made. The runner draws every random
number of the run from the scenario's `seed`, the ids of the group and of its members among
them, and which commit the group applies follows from those. The seed of `network` fixes what a
member's model does with the `n`-th frame to reach it. Two runs of one scenario write the same
`group.log`, and the same report but for the wall time and the load average (see
[The loss rate in one process](#the-loss-rate-in-one-process)). A change to the core that
changes a run shows as a difference between the logs of one scenario made before and after it.

### What a run in one process writes

The run's directory holds `plan.json`, the plan the runner was told, and `group.log` and
`result.json`, which the runner leaves (see
[What a run leaves](../../core/integration_tests_core/README.md#what-a-run-leaves)).
`runner.stderr` and `runner.out` are what the runner wrote to standard error and to standard
output. Beside them are `expect.json`, `report.txt`, `report.json`, `run.txt` and `run.json`, as
a run of processes has them, and `.forks-share.json` in a run given `--share`. `run.json` has no
`wired_s`, and differs in these fields:

| Field | Holds |
|-------|-------|
| `rig` | `one process`. A run of processes has no such field |
| `seed` | The scenario's `seed`, as this run had it. `null` in a run of processes |
| `virtual_s`, `wall_s` | How far the runner moved its clock, and how long it took on the machine. `null` when no result was read |
| `preset`, `cluster_id` | `null`, both |
| `max_message_kib` | The scenario's, which nothing applied |
| `nodes[]` | Each member as the runner's result has it: its `name`, `installation`, `account` and `app_id`, whether the steps `seated` it, and as `inbound_errors` how many frames it rejected, by the reason. With `network`, the state of its model as `network`, and as `network_dropped` how many frames the model dropped, by their `why`. Nothing a node answers is there, and nothing of how a peer started or ended |
| `batches[]` | For each batch its `joiners`, the `members` the group then had, `seated_s` on the virtual clock, from the first try of the add to the last roster, and `wall_s`, what the batch took on the machine. No `joined_s` and no `welcome_bytes`: the log holds no line of a Welcome |
| `posts[]` | For each post its `poster`, its `readers`, those that left it `unread`, and `read` on the virtual clock. Nothing of a registry |
| `holds[]` | The `seconds` of each `hold` step that was done, and nothing else |

The first line of `run.txt` says that the run was one process, how far the virtual clock went,
how long that took, and the run's seed. The table of the members has each one's installation id
and the count of the frames it rejected. With `network` it has how many frames `arrived` at the
member's model, and how many of them the model `passed`, `held` at the end and `lost`. Here the
fifth run of a sweep of `lost-candidate.json`:

```console
5 members, fast group timers, one process, 3.650s on a virtual clock in 0.052s of wall time, seed 4, network seed 5, loss 0, welcome loss 0
split at epoch 4: 5 of 5 members at the tip
withheld from pax: the candidate of saro, applied
the report is as expected
load average 1.11 at the start, 1.11 at the end

member  installation  arrived  passed  held  lost  errors
saro        52c324a5       20      20     0     0       1
raya        bd5a775e       22      22     0     0       1
pax         f61b4185       22      21     0     1       1
mira        b74e2dcb       15      15     0     0       1
kes         314c9dfc        4       4     0     0       1

inbound error                                                                 frames  members
DeMlsError(Mls(ProcessMessage(ValidationError(UnableToDecrypt(AeadError)))))       5        5

batch  joiners  members  seated
1            1        2  1.250s
2            1        3  0.550s
3            1        4  0.600s
4            1        5  1.050s

post  from  read by  median   worst
1      kes   3 of 3  0.000s  0.000s
```

The `seconds` of a sweep's row are those of the run's virtual clock, and the line above the rows
says so. `sweep.json` has under `seconds` what the run took on the machine, as it has for a run
of processes:

```console
run  seconds  at the tip  verdict                 check (one process: seconds of its virtual clock)
  1      1.4      3 of 3  whole                   as expected; seed 0
  2      1.4      3 of 3  whole                   as expected; seed 1
  3      1.4      3 of 3  whole                   as expected; seed 2
3 of 3 runs went as expected
```

### From three members to a hundred

These are the numbers of the machine of [The floor on one machine](#the-floor-on-one-machine),
taken on 2026-10-12 with a release build of `harness-run`, every run through `sim`. The load
average over one minute was between 0.6 and 1.2.

| Scenario | Runs | As expected | The virtual clock went, s | The runner took, s | The run took, s |
|---|---|---|---|---|---|
| `three.json` | 3 | 3 | 1.35 | 0.004 to 0.005 | 0.18 to 0.20 |
| `twenty.json` | 3 | 3 | 1.95 | 0.34 to 0.35 | 0.55 to 0.56 |
| A hundred members, the library's own timers | 2 | 2 | 770.9 | 70.54 and 70.89 | 70.96 and 71.31 |
| A hundred members, the `fast` set | 2 | 2 | 7.3 | 71.50 and 70.80 | 71.95 and 71.21 |

A run took what `sweep.json` has for it, from its start to its check, and a run of a hundred
members what `/usr/bin/time -v` gave for the command.

A hundred members are a scenario that `scenarios/` does not keep. It seats them ten at a time
and has the last one post, here with the library's own timers:

```json
{
  "members": 100,
  "timers": "default",
  "steps": [
    {"step": "seat", "to": 100, "batch": 10},
    {"step": "post"}
  ]
}
```

It is a size for one process and not for a network on one machine: of fifty peers with the
library's own timers, 1 run of 3 went as expected there and took 358 s, and no run of a hundred
peers is recorded here.

All four runs were `whole` with 100 of 100 members at the tip, and the 99 other members read
the post before the clock moved on. The timers set how far the clock goes, and not how long the
runner takes. With the library's own, the first batch was seated after 120.0 s of the clock, the
second after 70.0 s, the third after 90.0 s and each of the seven after that after 70.05 s. With
the `fast` set the first was seated after 1.25 s, the second after 0.55 s, the third after
0.75 s and each of the seven after that after 0.6 s. On the machine the first batch took 0.02 s
with either set, the fifth 3.5 to 3.9 s, and the last, which seats nine at ninety-one members,
19.8 to 20.1 s. The largest resident set `/usr/bin/time -v` gave for the command was 269604 to
270396 kB, which is 263 to 264 MiB.

A batch of ten took 0.3 s where it left twenty-one members and 15.7 to 15.9 s where it left
ninety-one, and over the eight batches from the one to the other its time grew as the size of
the group to the power of 2.7.

Three hundred members, seated the same way, were given 30 minutes with each set of timers, the
two runs at once, at a load average of 1.0 at the start and 2.1 at the end. Neither run was over
by then. Each was ended by a SIGTERM to `sim`, which ends the runner, so there is no result and
the run is not checked:

```console
300 members, fast group timers, one process on a virtual clock, seed 0
stopped after 0 of 2 steps: interrupted by SIGTERM
not checked: no result of the runner was read
```

What each got to is in the `group.log` it left, by `forks report`. With either set the log
held 24 batches: 241 installations, every one at the tip at epoch 24, and the verdict `whole`.
Its last record was stamped 1752.15 s after the start of the clock with the library's own
timers, and 16.30 s after it with the `fast` set. The largest resident set of the command was
2500172 and 2503736 kB, which is 2.4 GiB.

### The lost candidate in one process

`lost-candidate.json` and the three copies of
[A candidate that does not arrive](#a-candidate-that-does-not-arrive) were swept 10 times each,
at a load average of 0.7. The last column is what the sweeps of processes of that section came
to:

| Withheld from | As expected | The group applied it | It did not | The candidate was that of | With processes: applied, not |
|---|---|---|---|---|---|
| saro | 10 of 10 | 8 | 2 | raya, 10 times | 4, 6 |
| raya | 10 of 10 | 6 | 4 | saro, 10 times | 5, 5 |
| pax | 10 of 10 | 9 | 1 | saro, 10 times | 2, 8 |
| mira | 10 of 10 | 5 | 5 | saro, 10 times | 6, 4 |

In each of the 40 runs the models dropped one frame between them: the member's, `withheld`, a
candidate that another member made. The report read `split at epoch 4` in the 28 runs whose
member's model answered that the group applied the candidate, with that member alone on a branch
of that epoch, and `whole` in the other 12, where no member was alone. So one process comes to
the two outcomes a run of processes comes to, and to no third. Whose candidate reaches a member
first differs: here it was saro's in every run that does not withhold from saro, and with
processes pax lost saro's 5 times and raya's 5, and mira raya's 6 times and saro's 4.

A run moved the clock 3.6 to 3.7 s and took the runner 0.019 to 0.025 s. The batch after the
step was seated in 1.05 s of the clock whichever way the run went, the first batch in 1.25 s,
the second in 0.55 s and the third in 0.55 or 0.6 s. Where the group applied the candidate, the
post was waited for on the three other members and every member rejected one frame, as in the
run above, but for the member itself in the 14 such runs that withhold from saro or from raya,
which rejected two. Where it did not, the post was read by all four and no member rejected a
frame.

One more run of `lost-candidate.json` was given `--share`, to a collector started for it in a
network namespace. It printed `shared: http://127.0.0.1:41391/#f7d8ed6b83`, and the report the
collector gave for that conversation had the run's verdict, `whole`.

### The loss rate in one process

`lossy.json` was swept 20 times on 2026-10-16, over the network seeds 1 to 20, which the 20 runs
of [A loss rate](#a-loss-rate) had, and the seeds 0 to 19, and then once more. The load average
was 1.5. The last column is what those 20 runs of processes came to:

| Outcome | One process | With processes |
|---|---|---|
| `whole`, as expected | 11 | 10 |
| Split | 2, at epoch 3 | 3, at epoch 4 |
| `whole, members behind` | 4 | 1 |
| `whole`, a post that was not read | 2 | 3 |
| `whole`, a batch that did not come about | 1 | 3 |

The second sweep printed the same 20 rows, byte for byte, installation ids and all.

One process came to every outcome the runs of processes came to, and the counts need not agree.
One process gives the group under the model alone, with the frames arriving in the order the
runner hands them over. Processes give it over nodes that gossip, each of which also hands its
peer the frames that peer published. Twenty runs are few on either side.

The member alone on a branch had lost a commit candidate: pax in one run and raya in the other. The
runner stops the steps at a batch that leaves the group split, so those runs ended after 3.0 and
2.95 s of the clock. A member behind had lost a frame of about 3 KB, once a commit candidate and
else a group frame of that size, and stayed one epoch back: raya three times and pax once. The post
was not read by mira both times, which had lost one frame in one run and two in the other. In the
run whose batch did not come about, raya lost the second frame to reach it, and no member came to
hold the roster of three. A run as expected moved the clock 3.2 to 3.65 s, and one that waited out
its budget of 33 s moved it 34.3 to 36.15 s. No run took the runner more than 0.07 s. The models
dropped 52 of the 1305 frames that arrived, which is 0.040 of them.

### A delay in one process

`lost-candidate.json` with a delay of 300 ms on pax and a budget of 33 s, the first copy of
[A delay](#a-delay), was swept 20 times in one process at a load average of 0.2. All 20 went as
expected, 14 with the candidate applied. No step waits for the post on pax where pax is alone
on its branch, so the steps were over while the model of pax held the frame of the post. The
runner then lets its clock go on until no model holds a frame, for the largest delay of the
scenario at most and no longer than the budget, as a run of processes waits for each peer's
model before it asks its last answer (see [What a run does](#what-a-run-does)). The row of pax
has 0 under `held` in every run, and where the candidate was applied pax rejected one frame, as
each of the other four did. With processes all 8 runs of that file went as expected.

Three members with that delay on pax and a post of saro that the scenario does not wait for on
pax: pax read it in 5 runs of 5 in one process, and in 3 of 3 with processes.

## What it does not do

- It does not build `chat-cli`, the runner or the image, and it runs whatever it is given.
- It runs no fleet: a scenario with `preset` joins one that is there, and a run without it
  reaches no node outside the run.
- It does not put itself into a network namespace.
- It forms one group, and its steps seat members, send posts and set the rules of the network
  model: it removes nobody and restarts no peer.
- It checks a duration of `timers` for its name, its form and the most its flag holds: what
  the library takes is chat-cli's to refuse.
- It shapes no traffic between the nodes but on the wire of a run in containers. The network
  model is chat-cli's and drops what a node hands its client, and `sim` checks a rate and a
  delay for their form and their range alone.
- It does not make a run of processes or of containers again from its seeds: a seed gives the
  same draw to the frame a peer receives as its `n`-th, and which frame that is differs from run
  to run. Only a run in one process is made again.
- It does not look at what a report holds beyond the verdict, the positions and the posts (see
  [forks](../forks/README.md#checking-a-report)), so a run that went as expected may hold
  `unreadable` records of frames that are no posts.
- It starts peers one at a time.
- The registry is the mock one, so the lookup time is that of a registry on the same
  machine.

## Tests

```sh
python3 -m unittest discover -s tools/sim
```

They run the tool against `tests/standin.py`, a stand-in for chat-cli that needs no delivery
library and writes the trace records a peer writes, against the repository's mock registry, and
against `forks` as the repository has it.

The runs in containers are against `tests/standin_engine.py`, a stand-in for Docker and Podman
whose containers are processes on addresses of loopback, so those tests run on Linux alone.

The runs in one process are against `tests/standin_runner.py`, a stand-in for the runner that
runs no group and leaves the log and the result a runner leaves. `tests/fixtures/result.json` is
a result the runner left. What the stand-in leaves is held against its fields, and a run is
given it whole and with each field the run reads taken out in turn.
