"""One run: a registry, peers on a network of their own, one group, one post."""

from __future__ import annotations

import os
import secrets
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import measure, wiring
from .peers import GRACE, WATCH, Interrupted, OutOfBudget, Peer, Registry, Stopped, Watch, name_of

REPOSITORY = Path(__file__).resolve().parents[3]

# chat-cli's two sets of group timers, by the name `--group-commit` takes.
TIMERS = ("fast", "default")

# No preset holds this cluster id, and every node of a run is given it.
CLUSTER_ID = 77

# Between two rounds of asking every node for its state.
POLL = 0.5

# How long a node has to answer what its state is.
ANSWER = 10.0


@dataclass(frozen=True)
class Settings:
    """What a run is given."""

    chat_cli: Path
    peers: int
    out: Path
    batch: int = 10
    timers: str = "fast"
    max_message_kib: int = 512
    # Seconds each step may take: a peer's start, the wiring, a batch, the post.
    budget: float = 180.0
    registry: Path = REPOSITORY / ".github" / "scripts" / "mock-registry.py"


def run(settings: Settings, watch: Watch) -> dict:
    """Runs a group from its first peer to its post, ends every process, and returns the report.

    A signal `watch` takes before the processes are being ended interrupts the run.
    """
    settings.out.mkdir(parents=True)
    report = {
        "peers": settings.peers,
        "ok": False,
        "stopped": None,
        "timers": settings.timers,
        "batch": settings.batch,
        "cluster_id": CLUSTER_ID,
        "max_message_kib": settings.max_message_kib,
        "budget": settings.budget,
        "nodes": [],
        "batches": [],
        "post": None,
    }
    peers: List[Peer] = []
    registry: Optional[Registry] = None
    try:
        try:
            registry = Registry(settings.registry, settings.out, watch)
            registry.read_url(_deadline(settings))
            watch.processes.append(registry)
            _group(settings, registry, peers, report, watch)
        except Stopped as stopped:
            report["stopped"] = str(stopped)
        # A process that has ended has no last word, and the others still have theirs.
        watch.processes.clear()
        _last_words(peers, report)
    except Interrupted:
        pass
    finally:
        watch.close()
        _stop(peers, registry)
    if watch.signal is not None:
        report["stopped"] = f"interrupted by {signal.Signals(watch.signal).name}"
    post = report["post"]
    report["ok"] = report["stopped"] is None and post is not None and not post["unread"]
    return report


def _deadline(settings: Settings) -> float:
    return time.monotonic() + settings.budget


def _group(
    settings: Settings, registry: Registry, peers: List[Peer], report: dict, watch: Watch
) -> None:
    started_at = time.time()
    dialing = wiring.dials(settings.peers)
    env = dict(os.environ, RUST_LOG=measure.LOG_FILTER)
    for index, dialed in enumerate(dialing):
        name = name_of(index)
        command = [
            str(settings.chat_cli),
            "--headless",
            "--transport",
            "logos-delivery",
            "--name",
            name,
            "--data",
            str(settings.out / "data"),
            "--keys",
            str(settings.out / "keys"),
            "--registry-url",
            registry.url,
            "--group-commit",
            settings.timers,
            "--cluster-id",
            str(CLUSTER_ID),
            "--max-message-kib",
            str(settings.max_message_kib),
        ]
        for other in dialed:
            command += ["--static-node", peers[other].node_address]
        peer = Peer(name, command, settings.out, env, watch)
        peers.append(peer)
        report["nodes"].append({"name": name, "dials": [name_of(other) for other in dialed]})
        peer.read_ready(_deadline(settings))
        watch.processes.append(peer)
        report["nodes"][index]["ready_s"] = peer.ready_at - started_at
        report["nodes"][index]["start_s"] = peer.ready_at - peer.started_at

    _wire(settings, peers, wiring.links(dialing), report, watch)
    report["wired_s"] = time.time() - started_at

    creator = peers[0]
    convo = creator.run("/new sim", _deadline(settings))["convo"]
    seated = [creator]
    for joiners in measure.batches(peers[1:], settings.batch):
        report["batches"].append(_seat(settings, convo, seated, joiners))
        seated += joiners

    report["post"] = _post(settings, registry, convo, peers)
    unread = report["post"]["unread"]
    if unread:
        raise Stopped(f"the post was not read within the budget by: {', '.join(unread)}")


def _links(state: dict) -> dict:
    if state["peers"] is None or state["mesh_peers"] is None:
        raise Stopped("the delivery library does not tell a node's peers")
    return {"peers": state["peers"], "mesh_peers": state["mesh_peers"]}


def _wire(
    settings: Settings, peers: List[Peer], want: List[int], report: dict, watch: Watch
) -> None:
    """Waits until every node holds its links and a mesh gossipsub does not grow."""
    deadline = _deadline(settings)
    while True:
        short = []
        for node, peer, links in zip(report["nodes"], peers, want):
            node["wired"] = _links(peer.node(time.monotonic() + ANSWER))
            if node["wired"]["peers"] < links or node["wired"]["mesh_peers"] < min(
                links, wiring.MESH_LOW
            ):
                short.append(peer.name)
        if not short:
            return
        if time.monotonic() + POLL >= deadline:
            raise OutOfBudget(f"not wired within the budget: {', '.join(short)}")
        _pause(POLL, watch)


def _pause(seconds: float, watch: Watch) -> None:
    """Waits `seconds`, looking at `watch` every `WATCH` of them."""
    until = time.monotonic() + seconds
    while True:
        watch()
        left = until - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, WATCH))


def _seat(settings: Settings, convo: str, seated: List[Peer], joiners: List[Peer]) -> dict:
    """The creator adds `joiners` in one input. Returns once every member holds the new roster.

    A joiner holds it by a Welcome for a group of that size, and the creator sends a joiner one
    Welcome. So the Welcomes it published meanwhile are those of this roster, one a joiner.
    """
    deadline = _deadline(settings)
    creator = seated[0]
    members = len(seated) + len(joiners)
    addresses = " ".join(joiner.address for joiner in joiners)
    sent_at, _, result = creator.input(f"/add {addresses}", deadline)
    if result.get("ok") is not True:
        raise Stopped(f"{creator.name}: /add was refused: {result.get('error')}")

    joined_at = sent_at
    reached = []
    behind: List[OutOfBudget] = []
    for member in seated + joiners:
        try:
            if member in joiners:
                joined_at = max(joined_at, _roster_line(member, convo, deadline))
            reached.append(_reached(member, convo, members, deadline))
        except OutOfBudget as late:
            behind.append(late)
    if reached:
        # A member that does not hold the roster may hold its epoch all the same, by a commit
        # the others did not take.
        epoch = reached[0].epoch
        held = [measure.held(_log(member), convo, epoch) for member in seated + joiners]
        states = {state for state in held if state is not None}
        if len(states) > 1:
            raise Stopped(
                f"the group split: {len(held) - held.count(None)} of its {members} members "
                f"hold epoch {epoch}, in {len(states)} states"
            )
    if behind:
        raise behind[0]
    seated_at = max(record.at for record in reached)
    welcome_bytes = measure.welcomes(_log(creator), sent_at, seated_at)
    if len(welcome_bytes) != len(joiners):
        raise Stopped(
            f"{creator.name}: its log holds {len(welcome_bytes)} Welcomes "
            f"for {len(joiners)} joiners"
        )
    return {
        "joiners": len(joiners),
        "members": members,
        "joined_s": joined_at - sent_at,
        "seated_s": seated_at - sent_at,
        "welcome_bytes": welcome_bytes,
    }


def _log(member: Peer) -> str:
    """What `member` has logged: nothing, while it has not made its log."""
    try:
        return member.log.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _roster_line(member: Peer, convo: str, deadline: float) -> float:
    """Waits for the next line that says `member` joined `convo` or saw its members change."""
    read_at, _ = member.wait_for(
        "line of a new roster",
        lambda line: line.get("type") in ("joined", "members") and line.get("convo") == convo,
        deadline,
    )
    return read_at


def _reached(member: Peer, convo: str, members: int, deadline: float) -> measure.Epoch:
    """Waits until `member` holds `convo` with `members` members, and returns the record of it.

    A member writes the epoch record of a roster before the line that tells of it, so the log
    is read again after each such line.
    """
    while True:
        reached = measure.reached(_log(member), convo, members)
        if reached is not None:
            return reached
        _roster_line(member, convo, deadline)


def _post(settings: Settings, registry: Registry, convo: str, peers: List[Peer]) -> dict:
    """The last member seated posts, and every other member waits for the post."""
    deadline = _deadline(settings)
    poster = peers[-1]
    text = f"sim {secrets.token_hex(8)}"
    sent_at, _, result = poster.input(f"/send {convo} {text}", deadline)
    if result.get("ok") is not True:
        raise Stopped(f"{poster.name}: the post was refused: {result.get('error')}")

    read_at = []
    unread = []
    for reader in peers[:-1]:
        try:
            at, _ = reader.wait_for(
                "the post",
                lambda line: line.get("type") == "message"
                and line.get("convo") == convo
                and line.get("text") == text,
                deadline,
            )
        except OutOfBudget:
            unread.append(reader.name)
        else:
            read_at.append(at)

    asked_at = measure.lookups(
        registry.served(), poster.address, sent_at, max(read_at, default=sent_at)
    )
    post = measure.post_times(sent_at, read_at, asked_at)
    post.update(poster=poster.name, readers=len(peers) - 1, unread=unread)
    return post


def _last_words(peers: List[Peer], report: dict) -> None:
    """Asks every node still answering what it ends with."""
    for node, peer in zip(report["nodes"], peers):
        try:
            node["end"] = peer.node(time.monotonic() + ANSWER)
        except Stopped:
            continue
        del node["end"]["address"]


def _stop(peers: List[Peer], registry: Optional[Registry]) -> None:
    for peer in peers:
        peer.close()
    deadline = time.monotonic() + GRACE
    for peer in peers:
        peer.reap(deadline)
    if registry is not None:
        registry.stop()
