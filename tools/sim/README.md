# sim

How does one group fare when every member runs a delivery node of its own?

`sim` starts the repository's mock registry and N headless `chat-cli` peers, each with its
embedded delivery node on a local network that holds the run's nodes and no other. It links the
nodes, has the first peer create a group and add the others a batch at a time, has the last
member added post once, and waits until every other member has read the post. Then it ends every
process it started and writes what it measured.

It needs Python 3.9 or newer and no package beyond the standard library, and a `chat-cli` built
with the delivery library (see [chat-cli](../../bin/chat-cli/README.md#building)).

## Running it

From the repository root:

```sh
python3 tools/sim --chat-cli target/release/chat-cli --peers 7 --out tmp/seven
```

```console
7 peers, fast group timers, batches of 10, largest message 512 KiB
every member read the post

node     ready   start  dials  wired  mesh  end  mesh  refused  dropped
saro    3.115s  3.115s      0      6     6    6     6        0        0
raya    6.229s  3.113s      1      6     6    6     6        0        0
pax     9.356s  3.127s      2      6     6    6     6        0        0
mira   12.495s  3.139s      3      6     6    6     6        0        0
kes    15.628s  3.133s      4      6     6    6     6        0        0
tomo   18.758s  3.130s      5      6     6    6     6        0        0
peer7  21.883s  3.125s      6      6     6    6     6        0        0

batch  joiners  members  joined  seated  Welcome bytes
1            6        7  0.653s  0.647s      6 x 17672

post from peer7: read by 6 of 6, median 0.009s, worst 0.019s
delivered, by when the lookup reached the registry, median 0.009s, worst 0.018s
registry lookup: mean 0.001s
```

It exits 0 when every member read the post, 1 when the run stopped before that, and 2 when its
command line is refused. A run that SIGINT, SIGTERM or SIGHUP interrupts ends every process it
started, writes what it had measured, and exits 128 plus the signal's number.

| Flag | Default | Sets |
|------|---------|------|
| `--chat-cli PATH` | | The binary every peer runs |
| `--peers N` | | How many peers, 2 or more |
| `--out DIR` | | The run's directory, which the run makes and which must not exist |
| `--batch N` | `10` | How many members the creator adds with one `/add` |
| `--timers fast\|default` | `fast` | The group timers of every peer, as chat-cli's `--group-commit` |
| `--max-message-kib KIB` | `512` | The largest message a node takes, as chat-cli's `--max-message-kib` |
| `--budget SECONDS` | `180` | How long each step may take: a peer's start, the wiring, a batch, the post |

The timers default to `fast` because every frame of a run crosses loopback, which is the case
chat-cli's fast timers are for, and because under `default` the group waits out a 60 second
window before it commits a batch. `--timers default` measures the timers a real network runs
with, and needs a budget that covers that window. The fast timers leave a vote 250 ms to arrive,
which a machine running a few dozen peers may not give it, so a larger run wants `default`.

A batch defaults to 10 so that a run shows a Welcome at several group sizes, and so that a
batch stays under the 50 proposals the group puts into one commit.

On Linux a run of many peers is best given a network namespace of its own, where its nodes
share ports and loopback with nothing else on the machine and cannot reach any other network:

```sh
unshare -rn sh -c 'ip link set lo up && exec "$@"' sim \
  python3 tools/sim --chat-cli target/release/chat-cli --peers 50 --timers default --out tmp/fifty
```

## What a run does

1. It starts the mock registry on a port the system picks.
2. It starts the peers one after another. Each node is told which nodes to dial, all of them
   started before it, and the next peer starts once this one has written its `ready` line.
3. It asks every node for its state until each holds its links and has at least four of them, or
   all of them when it has fewer, in its gossip mesh.
4. The first peer creates a group and adds the others a batch at a time. A batch is seated when
   the log of every member holds the epoch record of the new roster, and the run stops when
   the members' records of that epoch do not name one state.
5. The last member added posts, and the run waits for the `message` line of every other member.
6. It asks every node for its state again, ends the input of every peer, and waits for each to
   exit. One that has not exited after 30 seconds is killed.

Every wait is for a line or a record and ends when its budget does, and a run that stops early
still goes through step 6. A wait also ends when a peer or the registry has ended, and the run
then stops with the name of the one that did. A node has 10 seconds to answer for its state. A
run a signal interrupts does not ask the nodes again, and a signal that comes while the
processes are being ended changes nothing.

### The wiring

Up to six nodes all link to each other. From seven up every node has six links, the mesh degree
gossipsub aims for: the nodes stand in a ring, and each links to the next and the one before
along three strides. The strides are 1, `a` and `a * a` for the smallest `a` whose cube reaches
the number of nodes, pulled in on a small ring so that the three differ. A hundred nodes have
the strides 1, 5 and 25.

Whichever end of a link starts later dials it, so every dial is to a node that is up, and no
node is dialed by more than six. A node keeps up to 50 relay peers that dialed it.

## What it writes

The run's directory holds, for each peer, `<name>.out` with the lines it wrote, `<name>.stderr`
and `<name>.log`, and for the registry `registry.requests` with a line for each request it
served. `data/` is what the peers stored, and `keys/` their accounts' and installations' keys,
which a run starts without, as its registry does. `run.txt` is the table the run prints, and
`run.json` holds the same and more:

| Field | Holds |
|-------|-------|
| `peers`, `timers`, `batch`, `max_message_kib`, `budget`, `cluster_id` | What the run was given |
| `ok` | Whether every member read the post |
| `stopped` | Why the run stopped before every member read the post, when it did |
| `nodes[].ready_s` | When the peer wrote its `ready` line, in seconds from the run's start |
| `nodes[].start_s` | How long the peer took from its own start to that line |
| `nodes[].dials` | The nodes it was told to dial |
| `nodes[].wired` | Its `peers` and `mesh_peers` once every node was linked |
| `nodes[].end` | Its `peers`, `mesh_peers`, `publish_refused` and `inbound_dropped` after the post |
| `wired_s` | When every node was linked, in seconds from the run's start |
| `batches[]` | For each batch its `joiners`, the `members` the group then had, `joined_s` from the `/add` to the last joiner's `joined` line, `seated_s` from the `/add` to the last member's epoch record, and as `welcome_bytes` the bytes of each Welcome the creator published for it |
| `post.read` | From the post's input to each reader's `message` line: `count`, `median` and `worst` |
| `post.delivered` | From the post's input to each reader's lookup reaching the registry |
| `post.lookup_mean` | The mean time a reader spent on that lookup |
| `post.lookups` | How many lookups of the poster the registry served while the post was read |
| `post.unread` | The members that did not read the post within the budget |

Times are in seconds, and `peers`, `mesh_peers`, `publish_refused` and `inbound_dropped` are what
chat-cli's `/node` answers.

A member that gets a post asks the registry for the account of its sender before it shows the
post. The registry stamps each request as it arrives, so the time up to the stamp is delivery and
the time after it is the lookup. A request does not say which member made it. When each reader
made one, `lookup_mean` is the mean read time less the mean time a request arrived; when they did
not, `delivered` and `lookup_mean` are `null`.

The bytes of a Welcome are those of the envelope the creator handed its node, which libchat
writes to its log. The node adds the content topic and a timestamp before it compares the
message with its limit.

## What it does not do

- It does not build `chat-cli`, and it runs whatever binary it is given.
- It runs no preset network and reaches no node outside the run.
- It does not put itself into a network namespace.
- It forms one group and reads one post: it removes nobody, sends no second post and restarts
  no peer.
- It tells a split only by the states the members hold at the epoch of a batch's roster and by
  a post left unread. The logs are in the run's directory for [`forks`](../forks/README.md) to
  read.
- It starts peers one at a time, so a run of N peers takes N starts before it measures anything.
- The registry is the mock one, so the lookup time is that of a registry on the same
  machine.

## Tests

```sh
python3 -m unittest discover -s tools/sim
```

They run the tool against `tests/standin.py`, a stand-in for chat-cli that needs no delivery
library, and against the repository's mock registry.
