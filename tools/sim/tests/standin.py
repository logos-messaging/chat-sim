"""A stand-in for a headless chat-cli, for the tests that run the tool.

It takes the command line the tool gives a peer and writes the lines a peer writes, and into its
log the trace records a peer writes. Its node is a socket on loopback, on `--node-ip` when it
is given one: it dials the nodes it is told to dial and passes a frame on to every link but the
one the frame came by, so a roster or a post reaches the peers the wiring links and no other.
Into the store it is given it writes `<name>.json`, with its process id and its arguments.

Started with `--network-seed` it has a network model, of which it keeps the cut, the rule that
withholds a commit candidate and the delay of a post: it loses no frame at a rate and holds no
roster. A post it holds when it ends is abandoned, as a peer abandons a frame it holds. A
roster stands for the commit candidates of its batch. So a peer whose rule is armed writes the
line of a candidate withheld when a roster arrives, and holds that roster all the same, as a
peer does by the candidate of another steward.

Started with `--preset` its node is on a fleet: it says that it holds a link to each of the
six entry nodes of the preset, each of them one it dialed, and that it has no mesh. The fleet
is the stand-ins on the preset that share its store: it dials every one started before it, so
a frame reaches every peer of the run. The preset `NO_PRESET` ends it at its start as the
delivery library ends chat-cli on a name it does not know: with status 1 and the reason as the
last line of its standard error.

    standin.py [--fault NAME[=PEER]]... <what the tool gives chat-cli>

A fault is what the peer named does wrong, or every peer when it names none.
"""

import argparse
import http.client
import json
import os
import secrets
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

FAULTS = {
    "silent": "writes no ready line",
    "dies-at-start": "ends before its ready line",
    "stray-line": "writes a line that is not JSON before its ready line",
    "unlinked": "dials no node",
    "drops-a-dial": "dials every node it is told to but the first",
    "mesh-of-three": "has no more than three of its links in its mesh",
    "mesh-of-four": "has no more than four of its links in its mesh",
    "links-late": "holds no link of a preset until LINK after its start",
    "links-never": "holds no link of a preset",
    "redials": "loses a link of a preset and dials it again between two answers of its node",
    "drops-a-link": "loses a link of a preset between two answers of its node and dials none",
    "tells-no-dials": "does not say how many dials its node made",
    "refuses-add": "refuses an /add",
    "forgets-a-welcome": "logs one Welcome fewer than it added members",
    "tells-no-app-id": "writes no app id on its `joined` line",
    "mute": "takes a roster and says nothing of it",
    "split": "holds a roster in a state of its own",
    "short-roster": "holds a roster without its last member, in a state of its own",
    "dies-at-roster": "ends when a roster reaches it",
    "withholds-nothing": "arms its rule and withholds no candidate",
    "withholds-early": "writes the line of a candidate withheld as its rule is armed",
    "lone": "holds the roster a candidate was withheld from it at in a state of its own",
    "says-applied": "says that the group applied the candidate withheld from it",
    "refuses-post": "refuses a /send",
    "deaf": "shows no post",
    "garbles": "cannot read a post: it shows none, and writes an `inbound_error` line of it",
    "unchecked": "gets no answer for the sender of a post: it shows none",
    "grumbles": "writes an `inbound_error` line of each post, whatever else it does with it",
    "dies-at-the-end": "ends with a status of its own once its input ended",
    "signalled-at-the-end": "ends by SIGNAL once its input ended",
    "lingers": "runs on for LINGER seconds after its input ended, having written `<name>.eof`",
    "slow": "takes START to start, ROSTER to hold a roster, DELIVERY to get a post and LOOKUP "
    "to look its sender up",
}

# The status a peer ends with when a fault has it end.
STATUS = 3
# The signal a peer with `signalled-at-the-end` ends by: one that ends it with no core.
SIGNAL = signal.SIGUSR1
LINGER = 1.5
START, ROSTER, DELIVERY, LOOKUP = 0.3, 0.4, 0.2, 0.3
LINK = 1.0

# The bytes of a Welcome, for each member of the group it seats.
WELCOME = 1000

MESH = {"mesh-of-three": 3, "mesh-of-four": 4}

# The nodes of a preset that a node dials at its start.
ENTRY_NODES = 6

# The preset the stand-in ends on, and what the delivery library says of one it does not know.
NO_PRESET = "nosuch.preset"
UNKNOWN = "Invalid --preset value passed"

# The durations of the group's settings, as chat-cli's flags name them.
DURATIONS = (
    "voting-delay",
    "consensus-timeout",
    "commit-batch-window",
    "freeze-duration",
    "retry-window",
    "backup-takeover-window",
    "proposal-expiration",
    "recovery-auto-commit-delay",
)
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# What a peer that cannot read a post says of it, and what one that grumbles says of any.
GARBLED = "An error occurred during AEAD decryption"
GRUMBLED = "The frame is of an epoch the group has left"


def installation_of(address: str) -> str:
    """The key of the installation at an address, which every stand-in can work out."""
    return address[::-1]


def seat_of(address: str) -> str:
    """A member as a roster lists it: its installation and its account, four bytes of each."""
    return f"{installation_of(address)[:8]}:{address[:8]}"


class Peer:
    def __init__(self, args: argparse.Namespace):
        self.name = args.name
        named = (flag.partition("=") for flag in args.fault)
        self.faults = {fault for fault, _, peer in named if peer in ("", args.name)}
        self.address = secrets.token_hex(32)
        self.installation = installation_of(self.address)
        self.log_file = args.log_file
        self.registry = urlsplit(args.registry_url)
        self.rosters = {}
        self.epochs = {}
        self.active = None
        # What it stamps on the frames it publishes, and what the roster it holds was stamped.
        self.app_id = secrets.token_hex(10)
        self.minter = None
        self.network = None
        if args.network_seed is not None:
            self.network = {
                "seed": args.network_seed,
                "loss": args.network_loss or 0.0,
                "welcome_loss": args.network_welcome_loss or 0.0,
                "delay_ms": args.network_delay_ms or 0,
                "cut": [],
                "own": [self.app_id],
                "withheld": None,
                "armed": False,
                "frames": {
                    "arrived": 0,
                    "passed": 0,
                    "held": 0,
                    "dropped": {"loss": 0, "welcome_loss": 0, "cut": 0, "withheld": 0},
                },
            }
        self.inputs = 0
        self.preset = args.preset
        self.started = time.monotonic()
        # The dials that came up, and how often the node was asked its state.
        self.dialed = 0
        self.asked = 0
        # One input or one frame at a time.
        self.lock = threading.RLock()
        self.links = []
        self.seen = set()
        ip = args.node_ip or "127.0.0.1"
        self.server = socket.create_server((ip, 0))
        peer_id = "".join(secrets.choice(BASE58) for _ in range(46))
        self.node_address = f"/ip4/{ip}/tcp/{self.server.getsockname()[1]}/p2p/{peer_id}"
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self) -> None:
        while True:
            link, _ = self.server.accept()
            self.link(link)

    def dial(self, node_address: str) -> None:
        _, _, ip, _, port, *_ = node_address.split("/")
        self.link(socket.create_connection((ip, int(port))))
        self.dialed += 1

    def join(self, fleet: Path) -> None:
        """Dials every stand-in that `fleet` lists, and adds this one to the list."""
        if fleet.exists():
            for node_address in fleet.read_text(encoding="utf-8").split():
                self.dial(node_address)
        with open(fleet, "a", encoding="utf-8") as listed:
            listed.write(f"{self.node_address}\n")

    def entry_links(self) -> tuple:
        """What the node of a preset says of its links, which are those to the entry nodes.

        How many it holds, how many of them are in its mesh, and how many dials it made.
        """
        self.asked += 1
        late = "links-late" in self.faults and time.monotonic() < self.started + LINK
        if late or "links-never" in self.faults:
            return 0, 0, 0
        again = self.asked - 1 if "redials" in self.faults else 0
        gone = self.asked - 1 if "drops-a-link" in self.faults else 0
        return ENTRY_NODES - gone, 0, ENTRY_NODES + again

    def link(self, link: socket.socket) -> None:
        link.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with self.lock:
            self.links.append(link)
        threading.Thread(target=self.read, args=(link,), daemon=True).start()

    def read(self, link: socket.socket) -> None:
        try:
            for raw in link.makefile("rb"):
                self.take(json.loads(raw), link)
        except OSError:
            pass
        with self.lock:
            self.links.remove(link)

    def publish(self, **frame) -> None:
        frame.setdefault("id", secrets.token_hex(8))
        self.take(frame, None)

    def take(self, frame: dict, source) -> None:
        with self.lock:
            if frame["id"] in self.seen:
                return
            self.seen.add(frame["id"])
            raw = json.dumps(frame).encode("utf-8") + b"\n"
            for link in self.links:
                if link is not source:
                    try:
                        link.sendall(raw)
                    except OSError:
                        pass
            if source is None:
                return
            if frame["kind"] == "roster" and self.address in frame["members"]:
                why = self.arrive(frame, len(raw))
                if why != "cut":
                    self.hold(frame, why == "withheld" and "lone" in self.faults)
            elif frame["kind"] == "post" and frame["convo"] in self.rosters:
                if self.arrive(frame, len(raw)) is None:
                    self.pass_on(frame)

    def arrive(self, frame: dict, size: int):
        """Puts a frame through the network model: why the model drops it, or `None`."""
        if self.network is None:
            return None
        counted = self.network["frames"]
        counted["arrived"] += 1
        candidate = frame["kind"] == "roster"
        if frame["app_id"] in self.network["cut"]:
            counted["dropped"]["cut"] += 1
            self.drop("cut", "candidate" if candidate else "group", size, frame["app_id"])
            return "cut"
        if candidate and self.network["armed"] and "withholds-nothing" not in self.faults:
            self.withhold(frame["app_id"], size)
            return "withheld"
        counted["held" if self.holds(frame) else "passed"] += 1
        return None

    def holds(self, frame: dict) -> bool:
        """Whether the network model holds a frame for its delay."""
        return self.network is not None and frame["kind"] == "post" and self.network["delay_ms"] > 0

    def pass_on(self, post: dict) -> None:
        """Shows a post, once its delay is over where the network model holds it."""
        if not self.holds(post):
            self.show(post)
            return
        # Not waited for when the peer ends: the post is then abandoned.
        held = threading.Timer(self.network["delay_ms"] / 1000, self.release, [post])
        held.daemon = True
        held.start()

    def release(self, post: dict) -> None:
        """The delay of a post is over."""
        with self.lock:
            self.show(post)
            counted = self.network["frames"]
            counted["held"] -= 1
            counted["passed"] += 1

    def withhold(self, app_id, size: int) -> None:
        """The rule takes a candidate, and is spent."""
        self.network["armed"] = False
        self.network["withheld"] = {
            "app_id": app_id,
            "seat": 0,
            "applied": "says-applied" in self.faults,
        }
        self.network["frames"]["dropped"]["withheld"] += 1
        self.drop("withheld", "candidate", size, app_id, seat=0)

    def drop(self, why: str, kind: str, size: int, app_id, **more) -> None:
        sender = {} if app_id is None else {"app_id": app_id}
        arrived = self.network["frames"]["arrived"]
        self.write(type="network_drop", n=arrived, why=why, kind=kind, bytes=size, **sender, **more)

    def hold(self, roster: dict, alone: bool = False) -> None:
        if "dies-at-roster" in self.faults:
            os._exit(STATUS)
        if "mute" in self.faults:
            return
        if "slow" in self.faults:
            time.sleep(ROSTER)
        convo = roster["convo"]
        joined = convo not in self.rosters
        short = "short-roster" in self.faults
        self.rosters[convo] = roster["members"][:-1] if short else roster["members"]
        self.epochs[convo] = roster["epoch"]
        self.minter = roster["app_id"]
        own = short or alone or "split" in self.faults
        state = secrets.token_hex(8) if own else roster["state"]
        self.record(convo, roster["epoch"], state)
        if joined and "tells-no-app-id" in self.faults:
            self.write(type="joined", convo=convo, kind="group")
        elif joined:
            self.write(type="joined", convo=convo, kind="group", app_id=self.app_id)
        else:
            self.write(type="members", convo=convo)

    def show(self, post: dict) -> None:
        if "deaf" in self.faults:
            return
        frame = f"convo={post['convo']} frame={post['id']}"
        if "grumbles" in self.faults:
            self.write(type="inbound_error", message=GRUMBLED)
        if "garbles" in self.faults:
            self.report("unreadable", f"{frame} epoch={post['epoch']} why=decrypt")
            self.write(type="inbound_error", message=GARBLED)
            return
        if "unchecked" in self.faults:
            self.report("unreadable", f"{frame} epoch={post['epoch']} why=sender")
            return
        if "slow" in self.faults:
            time.sleep(DELIVERY)
        self.ask("GET", f"/v1/account/{post['from']}")
        if "slow" in self.faults:
            time.sleep(LOOKUP)
        sender = installation_of(post["from"])[:8]
        self.report("received", f"{frame} from={sender} epoch={post['epoch']}")
        self.write(type="message", convo=post["convo"], text=post["text"], **{"from": post["from"]})

    def ask(self, method: str, path: str, body=None) -> None:
        registry = http.client.HTTPConnection(
            self.registry.hostname, self.registry.port, timeout=10
        )
        registry.request(method, path, body)
        registry.getresponse().read()
        registry.close()

    def write(self, **line) -> None:
        with self.lock:
            sys.stdout.write(json.dumps(line) + "\n")
            sys.stdout.flush()

    def log(self, target: str, text: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with open(self.log_file, "a", encoding="utf-8") as log:
            log.write(f"{stamp}  INFO {target}: {text}\n")

    def report(self, record: str, fields: str) -> None:
        self.log("libchat::groupv2trace", f"{record} installation={self.installation[:8]} {fields}")

    def record(self, convo: str, epoch: int, state: str) -> None:
        members = self.rosters[convo]
        self.report(
            "epoch",
            f"convo={convo} epoch={epoch} state={state} members={len(members)} "
            f"roster={','.join(map(seat_of, members))}",
        )

    def result(self, **fields) -> None:
        self.write(type="result", input=self.inputs, ok="error" not in fields, **fields)

    def input(self, text: str) -> None:
        self.inputs += 1
        command, _, rest = text.partition(" ")
        if command == "/node":
            links = len(self.links)
            mesh = min([links] + [most for fault, most in MESH.items() if fault in self.faults])
            dialed, failed = self.dialed, 0
            if self.preset is not None:
                links, mesh, dialed = self.entry_links()
            if "tells-no-dials" in self.faults:
                dialed = failed = None
            self.result(
                node={
                    "address": self.node_address,
                    "peers": links,
                    "mesh_peers": mesh,
                    "dials_ok": dialed,
                    "dials_failed": failed,
                    "publish_refused": 0,
                    "inbound_dropped": 0,
                }
            )
        elif command == "/new":
            self.active = secrets.token_hex(5)
            self.rosters[self.active] = [self.address]
            self.epochs[self.active] = 0
            self.record(self.active, 0, secrets.token_hex(8))
            self.result(text=f"Created {rest}", convo=self.active, app_id=self.app_id)
        elif command == "/add" and "refuses-add" in self.faults:
            self.result(error="The stand-in refuses to add anybody.")
        elif command == "/add":
            self.result(text="Added")
            self.add(rest.split())
        elif command == "/send" and "refuses-post" in self.faults:
            self.result(error="The stand-in refuses to send anything.")
        elif command == "/send":
            convo, _, post = rest.partition(" ")
            frame, epoch = secrets.token_hex(8), self.epochs.get(convo, 0)
            self.report("sent", f"convo={convo} frame={frame} epoch={epoch}")
            self.result(text="Message sent", convo=convo)
            self.publish(
                kind="post",
                id=frame,
                convo=convo,
                text=post,
                epoch=epoch,
                app_id=self.app_id,
                **{"from": self.address},
            )
        elif command == "/network":
            self.model(rest.split())
        else:
            self.result(error=f"Unknown command: {command}")

    def model(self, words: list) -> None:
        """Answers `/network`: sets the network model as the words say, and tells its state."""
        network = self.network
        if network is None:
            self.result(error="The network model is off. Start with --network-seed to turn it on.")
        elif not words:
            self.result(text="Network model shown", network=network)
        elif words[0] == "cut" and words[1:]:
            network["cut"] = sorted(words[1:])
            self.result(text="Cut set", network=network)
        elif words == ["heal"]:
            network["cut"] = []
            self.result(text="Cut healed", network=network)
        elif words == ["withhold", "candidate"]:
            network["armed"] = True
            if "withholds-early" in self.faults:
                network["frames"]["arrived"] += 1
                self.withhold(self.minter, 0)
            self.result(text="The next commit candidate is withheld", network=network)
        else:
            self.result(error="Usage: /network [cut <app id>... | heal | withhold candidate]")

    def add(self, joiners: list) -> None:
        convo = self.active
        members = self.rosters[convo] + joiners
        self.rosters[convo] = members
        self.epochs[convo] += 1
        epoch, state = self.epochs[convo], secrets.token_hex(8)
        self.record(convo, epoch, state)
        for _ in joiners[1:] if "forgets-a-welcome" in self.faults else joiners:
            self.log("libchat::inbox_v2", f"Publish WelcomeMessage bytes={WELCOME * len(members)}")
        self.publish(
            kind="roster",
            convo=convo,
            epoch=epoch,
            state=state,
            members=members,
            app_id=self.app_id,
        )
        self.write(type="members", convo=convo)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fault", action="append", default=[], metavar="NAME[=PEER]")
    parser.add_argument("--transport", choices=["logos-delivery"], required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--keys", type=Path, required=True)
    parser.add_argument("--registry-url", required=True)
    parser.add_argument("--group-commit", choices=["fast", "default"], required=True)
    for duration in DURATIONS:
        parser.add_argument(f"--group-{duration}-ms", type=int)
    parser.add_argument("--preset")
    parser.add_argument("--cluster-id", type=int)
    parser.add_argument("--node-ip")
    parser.add_argument("--max-message-kib", type=int)
    parser.add_argument("--network-seed", type=int)
    parser.add_argument("--network-loss", type=float)
    parser.add_argument("--network-welcome-loss", type=float)
    parser.add_argument("--network-delay-ms", type=int)
    parser.add_argument("--static-node", action="append", default=[])
    parser.add_argument("--log-file", required=True)
    args = parser.parse_args()
    for flag in args.fault:
        if flag.partition("=")[0] not in FAULTS:
            parser.error(f"--fault: no such fault: {flag}")
    rules = (args.network_loss, args.network_welcome_loss, args.network_delay_ms)
    if args.network_seed is None and rules != (None, None, None):
        parser.error("a flag of the network model needs --network-seed")
    local = (
        ("--cluster-id", args.cluster_id),
        ("--max-message-kib", args.max_message_kib),
        ("--static-node", args.static_node or None),
        ("--node-ip", args.node_ip),
    )
    for flag, given in local:
        if args.preset is not None and given is not None:
            parser.error(f"the argument '--preset' cannot be used with '{flag}'")

    args.data.mkdir(parents=True, exist_ok=True)
    (args.data / f"{args.name}.json").write_text(
        json.dumps({"pid": os.getpid(), "arguments": sys.argv[1:]}), encoding="utf-8"
    )
    if args.preset == NO_PRESET:
        print(f"Starting logos-delivery node (preset={args.preset})...", file=sys.stderr)
        sys.exit(f"chat-cli: failed to start the delivery node: {UNKNOWN}: {args.preset}")
    peer = Peer(args)
    peer.report("run", f"account={peer.address[:8]}")
    if "dies-at-start" in peer.faults:
        sys.exit(STATUS)
    if "slow" in peer.faults:
        time.sleep(START)
    if "stray-line" in peer.faults:
        print("starting", flush=True)
    dialed = args.static_node[1:] if "drops-a-dial" in peer.faults else args.static_node
    for node_address in [] if "unlinked" in peer.faults else dialed:
        peer.dial(node_address)
    if args.preset is not None:
        peer.join(args.data / f"{args.preset}.fleet")
    peer.ask("POST", f"/v1/account/{peer.address}", b"{}")
    if "silent" not in peer.faults:
        peer.write(
            type="ready",
            name=peer.name,
            address=peer.address,
            installation=peer.installation,
            node_address=peer.node_address,
        )
    for raw in sys.stdin.buffer:
        with peer.lock:
            peer.input(raw.decode("utf-8").rstrip("\n"))
    if "lingers" in peer.faults:
        (args.data / f"{args.name}.eof").write_text("", encoding="utf-8")
        time.sleep(LINGER)
    if "dies-at-the-end" in peer.faults:
        sys.exit(STATUS)
    if "signalled-at-the-end" in peer.faults:
        os.kill(os.getpid(), SIGNAL)


if __name__ == "__main__":
    main()
