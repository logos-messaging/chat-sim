# sim

How does a group fare when every member runs a delivery node of its own?

`sim` reads a scenario, a file that says how many members there are, what they do and what the
group is expected to come to. It starts the repository's mock registry and a headless `chat-cli`
peer for each member, each with its embedded delivery node on a local network that holds the
run's nodes and no other. It links the nodes, has the peers do the scenario's steps, ends every
process it started, and has [`forks`](../forks/README.md) hold the report of the peers' logs
against what the scenario expects.

It needs Python 3.9 or newer and no package beyond the standard library, and a `chat-cli` built
with the delivery library (see [chat-cli](../../bin/chat-cli/README.md#building)).

## Running it

From the repository root:

```sh
python3 tools/sim run tools/sim/scenarios/three.json --chat-cli target/release/chat-cli --out tmp/three
```

```console
3 members, fast group timers, largest message 512 KiB
whole: 3 of 3 members at the tip
the report is as expected
load average 0.74 at the start, 0.74 at the end

node   ready   start  dials  wired  mesh  end  mesh  refused  dropped  errors  exit
saro  0.125s  0.125s      0      2     2    2     2        0        0       0     0
raya  0.245s  0.120s      1      2     2    2     2        0        0       0     0
pax   0.368s  0.122s      2      2     2    2     2        0        0       0     0

batch  joiners  members  joined  seated  Welcome bytes
1            2        3  0.537s  0.537s       2 x 7121

post  from  read by  median   worst  delivered   worst  lookup
1      pax   2 of 2  0.006s  0.006s     0.005s  0.005s  0.001s
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
  python3 tools/sim run tools/sim/scenarios/fifty.json --chat-cli target/release/chat-cli --out tmp/fifty
```

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
| `members` | | How many peers the run starts, 2 to 65534: every node listens on a TCP port of loopback, and the registry on one more. They are named `saro`, `raya`, `pax`, `mira`, `kes` and `tomo`, written in lower case, and from the seventh on `peer7`, `peer8` and so on, in the order they start. `saro` creates the group. |
| `steps` | | What the members do, in order: one step or more. A later step is named by its place in the list, from 0: a refusal says `steps[2]`. |
| `timers` | `fast` | The group timers of every peer: `fast` or `default`, a set of chat-cli's `--group-commit`, or an object of durations in milliseconds by the names the group library gives them, each of which stands over that of the `default` set (see [chat-cli](../../bin/chat-cli/README.md#group-timers)). A duration is a whole number up to 2^64 - 1, the most its flag holds. |
| `max_message_kib` | `512` | The largest message a node takes, as chat-cli's `--max-message-kib`: 1 to 2^32 - 1, the most the flag holds. |
| `budget` | `180` | How many seconds each wait may take: a peer's start, the wiring, a batch, a post. A number above 0. |
| `network` | | A model of a lossy network that every peer puts what it receives through (see [The network model](#the-network-model)). Without it no peer has one. |
| `expect` | | How the outcome differs from a whole group, as below. |

A step is one of five, and the last three are refused in a scenario without `network`:

| Step | Fields | Does |
|------|--------|------|
| `seat` | `to`, and `batch` (default `10`) | The creator adds the members not yet seated, in the order they started, until the group holds `to`, `batch` of them with one `/add`. It is done when the log of each member holds the epoch record of the new roster. `to` is above what is seated by then and at most `members`. |
| `post` | `from` (default `last`) | A member posts and the others seated wait for the post. `from` is the name of a member seated by then, `last` for the last one seated, or `all` for every member seated, one after another in the order they started. |
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
does not take, the first peer then ends at its start, and its standard error holds the library's
reason.

`scenarios/` holds the scenarios the repository keeps: `three.json`, which CI runs,
`twenty.json` and `fifty.json`, the two larger sizes runs have been measured at (see
[The floor on one machine](#the-floor-on-one-machine)), and `lost-candidate.json` and
`lossy.json`, the two with a network model (see [What a lost frame does](#what-a-lost-frame-does)).

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
under `--out`, named by the run's number. A scenario with a network model gives each run the seed
after that of the run before, so that run `k` has `seed + k - 1`, and a sweep whose last run
would have a seed above 2^64 - 1 is refused. It prints a row as each run ends, and then how many
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
| `seconds` | How long the run took, from its start to its check. |
| `at the tip` | How many of the members the steps seat the report has at the tip. |
| `verdict` | The verdict of the report. |
| `check` | `as expected`, or the first difference the check found and how many more there are, or `not checked`, with how `forks check` ended when it was run and gave no answer. Then, after `; stopped:`, what stopped the steps when something did. Then `seed` and the seed of the run's network model, and what was withheld from whom and whether the group applied it, for a run that has them. Then the address of the conversation's page when the logs were shared, or after `not shared:` why they were not. |

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
| `rows[]` | For each run that ended its `run` number and `directory`, whether it is `ok`, the `differences` its check found, or `null` when it was not checked, as `unchecked` how `forks check` ended when it gave no answer, what `stopped` its steps, the `verdict`, the members `at_tip` of those `seated`, its `seconds`, the `seed` of its network model and what was `withheld` as its `run.json` holds it, each `null` for a run without one, and the `page` its logs were shared at |

## What a run does

1. It starts the mock registry on a port the system picks.
2. It starts the peers one after another. Each node is told which nodes to dial, all of them
   started before it, and the next peer starts once this one has written its `ready` line. A
   scenario with `network` starts each peer with a network model: the seed, the two rates and
   the delay of that member.
3. It asks every node for its state until each holds its links and has at least four of them, or
   all of them when it has fewer, in its gossip mesh.
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
   no post is waited for on it.
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
| `members`, `timers`, `max_message_kib`, `budget`, `cluster_id` | What the run was given |
| `network` | The `network` of the scenario with the seed this run had, or `null` |
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
| `nodes[].dials` | The nodes it was told to dial |
| `nodes[].wired` | Its `peers` and `mesh_peers` once every node was linked |
| `nodes[].end` | Its `peers`, `mesh_peers`, `publish_refused` and `inbound_dropped` after the steps |
| `nodes[].exit_status`, `nodes[].exit_signal` | The status the peer exited with, or the signal that ended it |
| `nodes[].ended` | `asked` when it exited with status 0 once its input had ended, `in its stop` when it then exited with another status or by a signal, `killed` when it had not exited after 30 seconds, and `early` when it ended before the run asked it to |
| `nodes[].inbound_errors` | How many `inbound_error` lines the peer wrote, by their message |
| `nodes[].unchecked_senders` | How many frames the peer's log says it decrypted and could not read because the registry gave it no answer for the sender's account: its `unreadable` records with `why=sender`. They tell of the registry and not of the group |
| `nodes[].network` | What the peer's network model answered after the steps, as chat-cli's `/network` gives it. Only in a run with `network` |
| `nodes[].network_dropped` | How many `network_drop` lines the peer wrote, by their `why`. Only in a run with `network` |
| `wired_s` | When every node was linked, in seconds from the run's start |
| `batches[]` | For each batch its `joiners`, the `members` the group then had, `joined_s` from the `/add` to the last joiner's `joined` line, `seated_s` from the `/add` to the last member's epoch record, and as `welcome_bytes` the bytes of each Welcome the creator published for it |
| `posts[].poster`, `posts[].readers` | Who sent the post, and how many members were waited on to read it |
| `posts[].read` | From the post's input to each reader's `message` line: `count`, `median` and `worst` |
| `posts[].delivered` | From the post's input to each reader's lookup reaching the registry |
| `posts[].lookup_mean` | The mean time a reader spent on that lookup |
| `posts[].lookups` | How many lookups of the poster the registry served while the post was read |
| `posts[].unread` | The members that did not read the post within the budget |

Times are in seconds, and `peers`, `mesh_peers`, `publish_refused` and `inbound_dropped` are what
chat-cli's `/node` answers. The table of the peers has the count of `inbound_error` lines under
`errors` and how the peer ended under `exit`, and a table of the messages of those lines follows
it when there are any. In a run with `network` the first line names the seed and the two rates,
the table of the peers has under `lost` how many frames each peer's model dropped, and a line
after the verdict says what a `withhold` step withheld:

```console
5 members, fast group timers, largest message 512 KiB, network seed 2, loss 0, welcome loss 0
split at epoch 4: 5 of 5 members at the tip
withheld from pax: the candidate of saro, applied
the report is as expected
load average 1.03 at the start, 0.95 at the end

node   ready   start  dials  wired  mesh  end  mesh  refused  dropped  lost  errors  exit
saro  0.122s  0.122s      0      4     4    4     4        0        0     0       1     0
raya  0.240s  0.118s      1      4     4    4     4        0        0     0       1     0
pax   0.360s  0.119s      2      4     4    4     4        0        0     1       1     0
mira  0.484s  0.125s      3      4     4    4     4        0        0     0       1     0
kes   0.614s  0.129s      4      4     4    4     4        0        0     0       1     0
```

A member that gets a post asks the registry for the account of its sender before it shows the
post. The registry stamps each request as it arrives, so the time up to the stamp is delivery and
the time after it is the lookup. A request does not say which member made it. When each reader
made one, `lookup_mean` is the mean read time less the mean time a request arrived; when they did
not, `delivered` and `lookup_mean` are `null`.

The bytes of a Welcome are those of the envelope the creator handed its node, which libchat
writes to its log. The node adds the content topic and a timestamp before it compares the
message with its limit.

## The timers of fifty members

`scenarios/fifty.json` gives five of the group's durations. Each is sized from what a group of
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
| Twice that, `fifty.json` | 17500 | 13500 | 6500 | 26000 | 2 of 3 | 87, 92, 76 |
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
| 20 runs of 50 members, the sized timers, in two sweeps of 10 | `fifty.json` | 16 of 20 | 88 to 109 | 10.3, 18.4 and 7.1, 20.3 |
| 3 runs of 50 members, the library's own timers | `fifty.json` with `"timers": "default"` | 1 of 3 | 358 | 18.4, 1.1 |

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

## What it does not do

- It does not build `chat-cli`, and it runs whatever binary it is given.
- It runs no preset network and reaches no node outside the run.
- It does not put itself into a network namespace.
- It forms one group, and its steps seat members, send posts and set the rules of the network
  model: it removes nobody and restarts no peer.
- It checks a duration of `timers` for its name, its form and the most its flag holds: what
  the library takes is chat-cli's to refuse.
- It shapes no traffic between the nodes. The network model is chat-cli's and drops what a
  node hands its client, and `sim` checks a rate and a delay for their form and their range
  alone.
- It does not make a run with a loss rate again from its seed: the seed gives the same draw to
  the frame a peer receives as its `n`-th, and which frame that is differs from run to run.
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
