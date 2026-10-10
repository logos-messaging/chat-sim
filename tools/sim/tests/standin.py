"""A stand-in for a headless chat-cli, for the tests that run the tool.

It takes the command line the tool gives a peer and writes the lines a peer writes. Its node is a
socket on loopback: it dials the nodes it is told to dial and passes a frame on to every link but
the one the frame came by, so a roster or a post reaches the peers the wiring links and no other.
Into the store it is given it writes `<name>.json`, with its process id and its arguments.

    standin.py [--fault NAME[=PEER]]... <what the tool gives chat-cli>

A fault is what the peer named does wrong, or every peer when it names none.
"""

import argparse
import http.client
import json
import os
import secrets
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
    "refuses-add": "refuses an /add",
    "forgets-a-welcome": "logs one Welcome fewer than it added members",
    "mute": "takes a roster and says nothing of it",
    "split": "holds a roster in a state of its own",
    "short-roster": "holds a roster without its last member, in a state of its own",
    "dies-at-roster": "ends when a roster reaches it",
    "refuses-post": "refuses a /send",
    "deaf": "shows no post",
    "lingers": "runs on for LINGER seconds after its input ended, having written `<name>.eof`",
    "slow": "takes START to start, ROSTER to hold a roster, DELIVERY to get a post and LOOKUP "
    "to look its sender up",
}

# The status a peer ends with when a fault has it end.
STATUS = 3
LINGER = 1.5
START, ROSTER, DELIVERY, LOOKUP = 0.3, 0.4, 0.2, 0.3

# The bytes of a Welcome, for each member of the group it seats.
WELCOME = 1000

MESH = {"mesh-of-three": 3, "mesh-of-four": 4}
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class Peer:
    def __init__(self, args: argparse.Namespace):
        self.name = args.name
        named = (flag.partition("=") for flag in args.fault)
        self.faults = {fault for fault, _, peer in named if peer in ("", args.name)}
        self.address = secrets.token_hex(32)
        self.installation = secrets.token_hex(4)
        self.log_file = args.log_file
        self.registry = urlsplit(args.registry_url)
        self.rosters = {}
        self.epochs = {}
        self.active = None
        self.inputs = 0
        # One input or one frame at a time.
        self.lock = threading.RLock()
        self.links = []
        self.seen = set()
        self.server = socket.create_server(("127.0.0.1", 0))
        peer_id = "".join(secrets.choice(BASE58) for _ in range(46))
        self.node_address = f"/ip4/127.0.0.1/tcp/{self.server.getsockname()[1]}/p2p/{peer_id}"
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self) -> None:
        while True:
            link, _ = self.server.accept()
            self.link(link)

    def dial(self, node_address: str) -> None:
        self.link(socket.create_connection(("127.0.0.1", int(node_address.split("/")[4]))))

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
        self.take(dict(frame, id=secrets.token_hex(8)), None)

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
                self.hold(frame)
            elif frame["kind"] == "post" and frame["convo"] in self.rosters:
                self.show(frame)

    def hold(self, roster: dict) -> None:
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
        state = secrets.token_hex(8) if short or "split" in self.faults else roster["state"]
        self.record(convo, roster["epoch"], state)
        if joined:
            self.write(type="joined", convo=convo, kind="group")
        else:
            self.write(type="members", convo=convo)

    def show(self, post: dict) -> None:
        if "deaf" in self.faults:
            return
        if "slow" in self.faults:
            time.sleep(DELIVERY)
        self.ask("GET", f"/v1/account/{post['from']}")
        if "slow" in self.faults:
            time.sleep(LOOKUP)
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

    def record(self, convo: str, epoch: int, state: str) -> None:
        self.log(
            "libchat::groupv2trace",
            f"epoch installation={self.installation} convo={convo} epoch={epoch} state={state} "
            f"members={len(self.rosters[convo])}",
        )

    def result(self, **fields) -> None:
        self.write(type="result", input=self.inputs, ok="error" not in fields, **fields)

    def input(self, text: str) -> None:
        self.inputs += 1
        command, _, rest = text.partition(" ")
        if command == "/node":
            links = len(self.links)
            mesh = min([links] + [most for fault, most in MESH.items() if fault in self.faults])
            self.result(
                node={
                    "address": self.node_address,
                    "peers": links,
                    "mesh_peers": mesh,
                    "publish_refused": 0,
                    "inbound_dropped": 0,
                }
            )
        elif command == "/new":
            self.active = secrets.token_hex(5)
            self.rosters[self.active] = [self.address]
            self.epochs[self.active] = 0
            self.record(self.active, 0, secrets.token_hex(8))
            self.result(text=f"Created {rest}", convo=self.active)
        elif command == "/add" and "refuses-add" in self.faults:
            self.result(error="The stand-in refuses to add anybody.")
        elif command == "/add":
            self.result(text="Added")
            self.add(rest.split())
        elif command == "/send" and "refuses-post" in self.faults:
            self.result(error="The stand-in refuses to send anything.")
        elif command == "/send":
            convo, _, post = rest.partition(" ")
            self.result(text="Message sent", convo=convo)
            self.publish(kind="post", convo=convo, text=post, **{"from": self.address})
        else:
            self.result(error=f"Unknown command: {command}")

    def add(self, joiners: list) -> None:
        convo = self.active
        members = self.rosters[convo] + joiners
        self.rosters[convo] = members
        self.epochs[convo] += 1
        epoch, state = self.epochs[convo], secrets.token_hex(8)
        self.record(convo, epoch, state)
        for _ in joiners[1:] if "forgets-a-welcome" in self.faults else joiners:
            self.log("libchat::inbox_v2", f"Publish WelcomeMessage bytes={WELCOME * len(members)}")
        self.publish(kind="roster", convo=convo, epoch=epoch, state=state, members=members)
        self.write(type="members", convo=convo)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fault", action="append", default=[], metavar="NAME[=PEER]")
    parser.add_argument("--headless", action="store_true", required=True)
    parser.add_argument("--transport", choices=["logos-delivery"], required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--keys", type=Path, required=True)
    parser.add_argument("--registry-url", required=True)
    parser.add_argument("--group-commit", choices=["fast", "default"], required=True)
    parser.add_argument("--cluster-id", type=int, required=True)
    parser.add_argument("--max-message-kib", type=int, required=True)
    parser.add_argument("--static-node", action="append", default=[])
    parser.add_argument("--log-file", required=True)
    args = parser.parse_args()
    for flag in args.fault:
        if flag.partition("=")[0] not in FAULTS:
            parser.error(f"--fault: no such fault: {flag}")

    args.data.mkdir(parents=True, exist_ok=True)
    (args.data / f"{args.name}.json").write_text(
        json.dumps({"pid": os.getpid(), "arguments": sys.argv[1:]}), encoding="utf-8"
    )
    peer = Peer(args)
    if "dies-at-start" in peer.faults:
        sys.exit(STATUS)
    if "slow" in peer.faults:
        time.sleep(START)
    if "stray-line" in peer.faults:
        print("starting", flush=True)
    dialed = args.static_node[1:] if "drops-a-dial" in peer.faults else args.static_node
    for node_address in [] if "unlinked" in peer.faults else dialed:
        peer.dial(node_address)
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


if __name__ == "__main__":
    main()
