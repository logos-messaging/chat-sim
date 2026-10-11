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
| `expect` | | How the outcome differs from a whole group, as below. |

A step is one of two:

| Step | Fields | Does |
|------|--------|------|
| `seat` | `to`, and `batch` (default `10`) | The creator adds the members not yet seated, in the order they started, until the group holds `to`, `batch` of them with one `/add`. It is done when the log of each member holds the epoch record of the new roster. `to` is above what is seated by then and at most `members`. |
| `post` | `from` (default `last`) | A member posts and the others seated wait for the post. `from` is the name of a member seated by then, `last` for the last one seated, or `all` for every member seated, one after another in the order they started. |

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

`scenarios/` holds the scenarios the repository keeps: `three.json`, which CI runs, and
`twenty.json` and `fifty.json`, the two larger sizes runs have been measured at (see
[The floor on one machine](#the-floor-on-one-machine)).

## A sweep

```sh
python3 tools/sim sweep tools/sim/scenarios/three.json --runs 3 --chat-cli target/release/chat-cli --out tmp/sweep
```

A sweep does one scenario `--runs` times, one run after another, each in a directory of its own
under `--out`, named by the run's number. It prints a row as each run ends, and then how many
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
| `check` | `as expected`, or the first difference the check found and how many more there are, or `not checked`, with how `forks check` ended when it was run and gave no answer. Then, after `; stopped:`, what stopped the steps when something did. Then the address of the conversation's page when the logs were shared, or after `not shared:` why they were not. |

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
| `rows[]` | For each run that ended its `run` number and `directory`, whether it is `ok`, the `differences` its check found, or `null` when it was not checked, as `unchecked` how `forks check` ended when it gave no answer, what `stopped` its steps, the `verdict`, the members `at_tip` of those `seated`, its `seconds`, and the `page` its logs were shared at |

## What a run does

1. It starts the mock registry on a port the system picks.
2. It starts the peers one after another. Each node is told which nodes to dial, all of them
   started before it, and the next peer starts once this one has written its `ready` line.
3. It asks every node for its state until each holds its links and has at least four of them, or
   all of them when it has fewer, in its gossip mesh.
4. The first peer creates a group, and the steps are done in order. A batch of a `seat` step is
   seated when the log of every member waited on holds the epoch record of the new roster, and
   the steps stop when the members' records of that epoch do not name one state. A `post` step
   waits for the `message` line of every member waited on.
5. It asks every node for its state again, ends the input of every peer, and waits for each to
   exit. One that has not exited after 30 seconds is killed.
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
| `ok` | Whether every step was done and the report is as expected |
| `stopped` | What stopped the steps, when something did |
| `steps`, `steps_done` | How many steps the scenario has, and how many were done |
| `load` | The load average of the machine at the `start` and at the `end` of the run: over 1, 5 and 15 minutes |
| `conversation` | The id of the group, once it was created |
| `check` | Whether the report is `as_expected`, and the `differences` as `forks check` printed them: `null` when the run was not checked |
| `unchecked` | How `forks check` ended when it was run and gave no answer: its status or the signal that ended it, and the last line it wrote to standard error. `null` when the check gave an answer or was not run |
| `verdict`, `at_tip`, `seated` | The report's verdict, how many of the members the steps seat it has at the tip, and how many the steps seat |
| `page` | The address of the conversation's page on the collector the logs were shared to |
| `unshared` | What `forks share` complained of, when the logs could not be sent |
| `nodes[].installation` | The id the fork report knows the peer by |
| `nodes[].ready_s` | When the peer wrote its `ready` line, in seconds from the run's start |
| `nodes[].start_s` | How long the peer took from its own start to that line |
| `nodes[].dials` | The nodes it was told to dial |
| `nodes[].wired` | Its `peers` and `mesh_peers` once every node was linked |
| `nodes[].end` | Its `peers`, `mesh_peers`, `publish_refused` and `inbound_dropped` after the steps |
| `nodes[].exit_status`, `nodes[].exit_signal` | The status the peer exited with, or the signal that ended it |
| `nodes[].ended` | `asked` when it exited with status 0 once its input had ended, `in its stop` when it then exited with another status or by a signal, `killed` when it had not exited after 30 seconds, and `early` when it ended before the run asked it to |
| `nodes[].inbound_errors` | How many `inbound_error` lines the peer wrote, by their message |
| `nodes[].unchecked_senders` | How many frames the peer's log says it decrypted and could not read because the registry gave it no answer for the sender's account: its `unreadable` records with `why=sender`. They tell of the registry and not of the group |
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
it when there are any.

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

## What it does not do

- It does not build `chat-cli`, and it runs whatever binary it is given.
- It runs no preset network and reaches no node outside the run.
- It does not put itself into a network namespace.
- It forms one group, and its steps seat members and send posts: it removes nobody and restarts
  no peer.
- It checks a duration of `timers` for its name, its form and the most its flag holds: what
  the library takes is chat-cli's to refuse.
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
