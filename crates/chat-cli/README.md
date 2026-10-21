# chat-cli

logos-chat's chat client without a terminal, which a program drives over pipes, with a model of
a lossy network on what it receives. `tools/sim` runs each member of a group as one.

## Building

`chat-cli` links the native [logos-delivery](https://github.com/logos-messaging/logos-delivery)
library, as logos-chat's flake builds it. The dev shell fetches it and sets
`LOGOS_DELIVERY_LIB_DIR` for you:

```bash
nix develop
cargo build --release -p chat-cli
```

Or build the library yourself and point `LOGOS_DELIVERY_LIB_DIR` at it:

```bash
nix build .#logos-delivery
LOGOS_DELIVERY_LIB_DIR=./result/lib cargo build --release -p chat-cli
```

The binary lands at `target/release/chat-cli`.

## Transports

Both transports are compiled into the binary and selected at runtime via `--transport`:

| Value (`--transport`) | Description |
|-----------------------|-------------|
| `logos-delivery` (default) | Embedded Waku node on the logos.test network |
| `file` | Shared directory; no network needed — great for local testing |

## Registry

The client publishes its MLS KeyPackage on startup to the
[keypackage-registry](https://github.com/logos-messaging/chat-store) that `--registry-url` names,
so other clients can fetch it by `account_id`. Without the flag it keeps an in-memory registry that
only it can see. `.github/scripts/mock-registry.py` takes those calls and keeps them in memory,
which is what the tests and `tools/sim` run against:

```bash
python3 .github/scripts/mock-registry.py &
cargo run -p chat-cli -- --name saro --transport file --log-file saro.log \
  --registry-url http://127.0.0.1:18080
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--transport <kind>` | `logos-delivery` | Transport to use (`logos-delivery` or `file`) |
| `--data <dir>` | `tmp/chat-cli-data` | Data directory (chat state and default SQLite path) |
| `--keys <dir>` | `chat-cli-keys` | Where account and installation keys are kept, unencrypted, apart from `--data`: `--name saro` is installation 1 of the account `saro`, and `saro:2` its installation 2. The identity on `--transport logos-delivery` |
| `--db <path>` | `<data>/<name>.db` | SQLite file for persistent identity on `--transport file` |
| `--preset <name>` | `logos.test` | logos-delivery network preset |
| `--port <n>` | `60000` | TCP port for the embedded logos-delivery node |
| `--cluster-id <n>` | *(unset)* | Start the node on a local network of this cluster id instead of a preset: it listens on `--node-ip` alone and dials the nodes `--static-node` names. Cannot be given with `--preset`, and 1 and 2 are refused, since the library reads them as presets |
| `--static-node <multiaddr>` | *(none)* | A node of the local network to dial at start, as its `ready` line gives it: `/ip4/<address>/tcp/<port>/p2p/<peer id>`, and anything else is refused. Repeat the flag for each node. Needs `--cluster-id`, and cannot be given with `--preset` |
| `--node-ip <ipv4>` | `127.0.0.1` | The address the node of a local network listens on and is dialed at: loopback, or one of its own, as a container has on a network of containers. `0.0.0.0` is refused. Needs `--cluster-id`, and cannot be given with `--preset` |
| `--max-message-kib <n>` | `512` | The largest message the node of a local network takes, in KiB. Needs `--cluster-id`, and cannot be given with `--preset` |
| `--group-commit <set>` | `auto` | The set of group timers: `fast` commits a membership change in about a second, `default` is the library's own set, and `auto` is `fast` on `--transport file` and `default` otherwise |
| `--group-<duration>-ms <n>` | *(the set's)* | One duration of the group's settings in milliseconds, over the set `--group-commit` picks (see [Group timers](#group-timers)) |
| `--registry-url <url>` | *(unset)* | Use the HTTP-backed [keypackage-registry](https://github.com/logos-messaging/chat-store) at this URL instead of the in-memory registry |
| `--log-file <path>` | *(required)* | The log, a file that does not exist yet (see [The log](#the-log)) |
| `--network-seed <n>` | *(unset)* | Put what this client receives through a model of a lossy network, whose drops follow from this seed and `--name` (see [The network model](#the-network-model)) |
| `--network-loss <rate>` | `0` | The share of the frames received that the model loses, from 0 to 1. A welcome is lost at `--network-welcome-loss` instead. Needs `--network-seed` |
| `--network-welcome-loss <rate>` | `0` | The share of the welcomes received that the model loses, from 0 to 1. Needs `--network-seed` |
| `--network-delay-ms <n>` | `0` | How long the model holds each frame it lets through, in milliseconds. Needs `--network-seed` |

### Group timers

Each duration of the group's settings has a flag, named as the library names the duration. One
that is given stands over that of the set `--group-commit` picks, and the others stay the set's.

| Flag | `default` | `fast` | How long |
|------|-----------|--------|----------|
| `--group-voting-delay-ms` | 10000 | 50 | A member has to vote on a proposal before its vote is cast for it |
| `--group-consensus-timeout-ms` | 30000 | 250 | A vote waits for two thirds of the members before the silent ones are counted |
| `--group-commit-batch-window-ms` | 60000 | 500 | Approved proposals are gathered before every steward makes a commit of them |
| `--group-freeze-duration-ms` | 30000 | 500 | The stewards' commits are gathered before one is chosen |
| `--group-retry-window-ms` | 5000 | 5000 | A round of reelection or of recovery lasts |
| `--group-backup-takeover-window-ms` | 20000 | 20000 | A backup steward waits for a silent steward before it stands in for it |
| `--group-proposal-expiration-ms` | 600000 | 4000 | A proposal stays open before it expires unvoted |
| `--group-recovery-auto-commit-delay-ms` | 5000 | 5000 | Recovery waits for a commit made by hand before every member makes one |

The proposal expiration is counted in whole seconds: 1999 is one second, and under 1000 it is
none, with which every `/add` is refused.

The library refuses a voting delay that is not below the consensus timeout, a proposal
expiration that is not above it, and a freeze of 0. chat-cli then ends before it starts anything,
with status 1 and the library's reason on standard error:

```console
$ chat-cli --name saro --transport file --log-file saro.log --group-voting-delay-ms 250
chat-cli: Invalid conversation config: voting_delay must be < consensus_timeout
```

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/account` | Show your account address and your installation |
| `/dm <address>` | Start a direct (1:1) chat |
| `/new <name> [address...]` | Create a named group chat (optionally inviting members) |
| `/add <address>...` | Add people to the active group |
| `/members` | List members of the active conversation |
| `/send <name\|id> <text>` | Send a message to a chat, whichever chat is active |
| `/chats` | List all established chats |
| `/switch <user>` | Switch active chat |
| `/delete <user>` | Delete a chat session |
| `/status` | Show identity and connection info |
| `/node` | Show the delivery node's address, its peers, its dials and what it lost |
| `/network [cut <app id>...\|heal\|withhold candidate]` | Show or set the network model, in a run started with `--network-seed` (see [The network model](#the-network-model)) |
| `/clear` | Clear current chat's message history |
| `/quit` | Exit |

## Driving it

A program drives the client over pipes: it takes one input from each line of standard input, and
writes one JSON object on each line of standard output.

```bash
cargo run -p chat-cli -- --name saro --transport file --log-file saro.log
```

An input is a command, or text for the active chat. `/send <id> <text>` posts to a conversation
by its id, whichever chat is active.

A line ends at a line feed, and a carriage return before it is not part of the input. What
follows the last line feed when standard input ends is not run.

Only an input moves the active chat: `/new` and `/dm` to the conversation they make, `/switch` to
the one it names, and `/delete` off the one it deletes. A `joined` line does not.

### The lines it writes

Standard output holds these lines and nothing else. Each is written whole and has a `type`, and
text that came from the network is a JSON string like any other. U+0085, U+2028 and U+2029 are
written as `\u` escapes, so a line holds no character that some reader takes for a line break.

`ready` is the first line, once the client is up. `/account` shows `address` and `installation`.
`node_address` is the multiaddress another delivery node dials this client's node at, and the
`file` transport, which runs no node, leaves it out. On a local network it is on the node's
`--node-ip`. On a preset it is the first address the node announces, which on a machine without
a network is `/ip4/0.0.0.0/...` and no address to dial. The node of a local network is up once
it holds a link for each node `--static-node` names, however often the flag names it, or three
seconds after it started when they do not come up; the node of a preset is given three seconds
to find its peers.

```json
{"type":"ready","name":"saro","address":"ca4353e659298fd183925f79dffdb313bed92c01b05debb74a901f89ffbe3974","installation":"36a216ef2596c18b03611ed1f7f9d16e2bb377502d65920ea60e75674cce8fba"}
```

`result` answers one input line, and every input line gets exactly one. `input` is the number of
the line, from 1, and `ok` says whether it worked.

```json
{"type":"result","input":1,"ok":true,"text":"Group created (f2509018).","convo":"f2509018dc","app_id":"32656166656666613864"}
{"type":"result","input":4,"ok":true,"text":"2 member(s)","convo":"f2509018dc","output":["── Members (2) ──","  • ca4353e659298fd1… (you)","  • f423b86be4123144…"]}
{"type":"result","input":5,"ok":false,"error":"Unknown command: /nope. Type /help for commands."}
```

| Field | Holds |
|-------|-------|
| `text` | The command's reply |
| `convo` | The id of the conversation the input made or acted on |
| `app_id` | The app id this client stamps on the frames it publishes to the group, on the result of `/new`: the lowercase hex of its bytes |
| `output` | The lines the command wrote |
| `node` | The delivery node's state, on the result of `/node` |
| `network` | The network model's state, on the result of `/network` (see [The network model](#the-network-model)) |
| `error` | Why the input failed, when `ok` is `false` |

`/node` asks the delivery node for its state, and on the `file` transport it fails.

```json
{"type":"result","input":5,"ok":true,"text":"Node state shown","output":["── Delivery Node ──","Address: /ip4/127.0.0.1/tcp/42235/p2p/16Uiu2HAkzigAq63oZ6y5cRLifPQGESKsBnukP3iutxhaEqPwYn2m","Peers: 6","Mesh peers: 6","Dials made: 6","Dials failed: 0","Publishes refused: 0","Received frames dropped: 0"],"node":{"address":"/ip4/127.0.0.1/tcp/42235/p2p/16Uiu2HAkzigAq63oZ6y5cRLifPQGESKsBnukP3iutxhaEqPwYn2m","peers":6,"mesh_peers":6,"dials_ok":6,"dials_failed":0,"publish_refused":0,"inbound_dropped":0}}
```

| Field of `node` | Holds |
|-----------------|-------|
| `address` | The multiaddress another node dials this one at |
| `peers` | How many peers the node holds a connection to, or `null` when the library's metrics do not say |
| `mesh_peers` | How many of them are in its gossip mesh, as of its last heartbeat and over every shard it is on, or `null` when the library's metrics do not say |
| `dials_ok` | How many connections the node opened since it started, or `null` when the library's metrics do not say |
| `dials_failed` | How many connections the node could not open since it started, or `null` when the library's metrics do not say |
| `publish_refused` | How many publishes the node refused since it started |
| `inbound_dropped` | How many received frames were dropped since the node started because the client's inbound queue was full |

The two counts of dials are of every connection the node opened or could not open, and not of
its `--static-node` dials alone: on a local network those of a node that another node dialed
rise while its `peers` stay as they are.

The other lines are events, written as they happen, between results.

`joined`: someone added this client to a conversation. `kind` is `group` or `dm`. `app_id` is the
app id this client stamps on the frames it publishes to the conversation, and a `dm`, which
stamps none, leaves it out.

```json
{"type":"joined","convo":"f2509018dc","kind":"group","app_id":"38643731383162396433"}
```

`members`: the membership of a conversation changed.

```json
{"type":"members","convo":"f2509018dc"}
```

`message`: a message was read. `from` is the address of the account that sent it.

```json
{"type":"message","convo":"f2509018dc","from":"ca4353e659298fd183925f79dffdb313bed92c01b05debb74a901f89ffbe3974","text":"hello from saro"}
```

`inbound_error`: something arrived that the client could not process.

```json
{"type":"inbound_error","message":"Failed to decode payload: failed to decode Protobuf message: invalid tag value: 0"}
```

`network_drop`: the network model dropped a frame (see [The network model](#the-network-model)).
`n` numbers the frames that arrived, from 1, `why` is the rule that dropped the frame, `kind` what
the frame is and `bytes` its length. `app_id` is the app id of its sender, left out for a frame
that has none, and `seat` the seat in the group of the steward that minted a commit candidate.

```json
{"type":"network_drop","n":6,"why":"cut","kind":"group","bytes":546,"app_id":"32656166656666613864"}
{"type":"network_drop","n":9,"why":"withheld","kind":"candidate","bytes":3092,"app_id":"32656166656666613864","seat":0}
```

A message from a sender the client could not check is dropped with a warning in the log, and has
no line. A publish the node refuses and a received frame the full inbound queue drops have no
line either: each is a warning in the log with the bytes of its payload and the reason, and is
counted in what `/node` answers. The input that made a refused publish is answered `ok` all the
same, unless the node did not take the publish at the call, which fails the input.

### The network model

`--network-seed <n>` puts a model of a lossy network between the transport and the client. Each
frame the transport hands over arrives at the model, which drops it or holds it for
`--network-delay-ms` and then hands it to the client, in the order the frames arrived. What the
client publishes goes to the transport as it does without the model. Without the flag there is
no model: no frame is dropped, and `/network` is refused.

```bash
cargo run -p chat-cli -- --name raya --transport file --log-file raya.log \
  --network-seed 7 --network-loss 0.2
```

The model reads a frame as the client first reads it, which gives the frame its `kind`:

| `kind` | The frame is |
|--------|--------------|
| `welcome` | What seats a joiner: the invite sent to its inbox, which has no sender, or the copy a steward broadcasts to the group. A double ratchet payload, which this client does not send, has the bytes of such an invite and is read as one |
| `candidate` | A commit candidate a steward minted |
| `group` | Any other frame of a group, a post among them |
| `other` | Anything else. The frames of a direct chat are among them, and its invite |

The sender of a frame of a group is the app id its publisher stamped on it. Each member of a
group has an app id of its own there, which the result of its `/new` or its `joined` line gives.

A frame that arrives is held to these rules in this order, and the first that drops it is the
`why` of its `network_drop` line:

| `why` | Drops |
|-------|-------|
| `cut` | A frame whose sender is in the cut |
| `loss` | A frame that is no welcome, at the rate of `--network-loss` |
| `welcome_loss` | A welcome, at the rate of `--network-welcome-loss` |
| `withheld` | The first commit candidate the rules above let through once the rule is armed |

A welcome has a rate of its own because each is sent once, and a joiner that loses its welcome
never joins.

Frame `n` is lost when the `n`-th number of a sequence that follows from the seed and `--name`
is below its rate, whatever the other rules make of the frame. So two runs with one seed and one
name lose the same frames as long as the same frames arrive in the same order, a frame lost at
one rate is lost at every higher rate, and a cut does not change which other frames are lost.

A transport hands a client the frames it published itself: the `file` transport does, and so
did the embedded node on a local network of two nodes, where 10 posts a client made had 10
frames arrive at it. The model learns an app id of this client's own from the first frame the
client publishes under it, and never drops a frame stamped with it: the frame has no number, is
in no count, and is held for the delay like any other. A frame this client published without an
app id, a post to a direct chat or an invite to another's inbox, is numbered and counted like a
frame of anyone else.

| Input | Does |
|-------|------|
| `/network` | Answers the rules and the counts |
| `/network cut <app id>...` | Cuts this client off from these senders and from no others: the cut takes the place of the one before it. Refused without an app id, and for a word that is not the hex of whole bytes |
| `/network heal` | Empties the cut |
| `/network withhold candidate` | Arms the rule that drops the next commit candidate to arrive. It withholds one candidate in a run: arming it again is refused, before it has withheld its candidate and after |

Each is answered with the model's state as `network`:

```json
{"type":"result","input":1,"ok":true,"text":"Cut set","network":{"seed":7,"loss":0.0,"welcome_loss":0.0,"delay_ms":0,"cut":["32656166656666613864"],"own":["38643731383162396433"],"withheld":null,"armed":false,"frames":{"arrived":5,"passed":5,"held":0,"dropped":{"loss":0,"welcome_loss":0,"cut":0,"withheld":0}}}}
{"type":"result","input":4,"ok":true,"text":"Network model shown","network":{"seed":7,"loss":0.0,"welcome_loss":0.0,"delay_ms":0,"cut":[],"own":["38643731383162396433"],"withheld":{"app_id":"32656166656666613864","seat":0,"applied":true},"armed":false,"frames":{"arrived":11,"passed":9,"held":0,"dropped":{"loss":0,"welcome_loss":0,"cut":1,"withheld":1}}}}
```

| Field of `network` | Holds |
|--------------------|-------|
| `seed`, `loss`, `welcome_loss`, `delay_ms` | What the model was started with |
| `cut` | The app ids this client is cut off from |
| `own` | The app ids this client has published frames under |
| `armed` | Whether the next commit candidate to arrive is withheld |
| `withheld` | `null` until a candidate is withheld. Then the `app_id` of its sender and the `seat` of the steward that minted it, as its `network_drop` line gave them, and `applied`: whether that sender has broadcast a welcome since |
| `frames` | How many frames `arrived`, and what became of them: `passed` to the client, `held` for the delay, or `dropped`, by `why`. The three add up to `arrived` |

`applied` turns `true` when a welcome that the sender of the withheld candidate broadcast
arrives and no rule drops it. A steward broadcasts the welcome of its own commit as it merges
it. So in the round of the withheld candidate `true` says that its sender merged that
candidate, and the group applied it where every steward merged the same commit. A welcome its
sender broadcasts in a later round turns it `true` as well, whatever became of the candidate.

A `network_drop` line is written as its frame arrives, and each drop that a result's `network`
counts has its line before that result. A frame still held when the run ends is abandoned,
without a line.

### How it ends

It ends with status 0 on `/quit`, when standard input ends, and at the first line it writes
after the reader of standard output has gone away. When it cannot start it ends with status 1,
and the last line of standard error says why. On the `file` transport that is the only line; the
notices of a `logos-delivery` node starting go to standard error too, before it. A command line
that clap refuses ends with status 2 and clap's usage text on standard error.

A run locks its database, and a second one started on it is refused.

### The log

`--log-file` refuses a path that exists, so a run never writes over the log of another. The log
holds warnings and the trace records that
[`tools/forks`](../../tools/forks/README.md) reads, unless `RUST_LOG` sets another filter. An
empty `RUST_LOG` sets none. A run that fails with nothing written to its log removes it, so the
same start can be made again.

## Storage

All data lives under `tmp/chat-cli-data/` by default (override with `--data`):

| Path | Contents |
|------|----------|
| `<name>.db` | SQLite — identity keys, ratchet state, chat metadata (encrypted) |
| `<name>.db.lock` | Locked while a run is on the database |
| `<name>_state.json` | Chat state — message history, active chat |
| `transport/<name>/` | Inbox directory watched for incoming messages (file transport only) |

The SQLite database can be inspected with *DB Browser for SQLite*: password `chat-cli`, cipher `SQLCipher 4 defaults`.

## Architecture

```
crates/chat-cli/
├── src/
│   ├── main.rs           entry point, CLI arg parsing, runtime transport dispatch
│   ├── app.rs            application state and command handling
│   ├── headless.rs       inputs on stdin, JSON lines on stdout
│   ├── utils.rs          shared helpers
│   ├── transport.rs      module declarations
│   └── transport/
│       ├── file.rs       file-based transport
│       └── modelled.rs   a transport behind the network model
└── tests/headless.rs     drives the built binary over pipes
```
