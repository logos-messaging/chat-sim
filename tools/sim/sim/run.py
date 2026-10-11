"""One run: a registry, peers on a network of their own, and the steps of a scenario."""

from __future__ import annotations

import collections
import json
import os
import secrets
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from . import judge, measure, table, wiring
from .peers import GRACE, WATCH, Interrupted, OutOfBudget, Peer, Registry, Stopped, Watch, name_of
from .scenario import Scenario, Seat, senders

REPOSITORY = Path(__file__).resolve().parents[3]

# No preset holds this cluster id, and every node of a run is given it.
CLUSTER_ID = 77

# Between two rounds of asking every node for its state.
POLL = 0.5

# How long a node has to answer what its state is.
ANSWER = 10.0

# How a peer ended: before the run asked it to, with status 0 once asked, with another status
# or by a signal once asked, or killed because it had not ended within the grace.
EARLY, ASKED, IN_ITS_STOP, KILLED = "early", "asked", "in its stop", "killed"


class NoDirectory(Exception):
    """Why the directory a run writes to could not be made."""


@dataclass(frozen=True)
class Settings:
    """What a run is given."""

    chat_cli: Path
    scenario: Scenario
    out: Path
    # The share address of a collector the run's logs go to.
    share: Optional[str] = None
    registry: Path = REPOSITORY / ".github" / "scripts" / "mock-registry.py"
    forks: Path = REPOSITORY / "tools" / "forks"


def run(settings: Settings, watch: Watch) -> dict:
    """Does a scenario's steps, ends every process, and has the logs judged.

    Returns the report, which it also writes into the run's directory as `run.json` and, as
    the tables a person reads, `run.txt`. A signal `watch` takes before the processes are being
    ended interrupts the run.
    """
    make(settings.out)
    scenario = settings.scenario
    report = {
        "members": scenario.members,
        "ok": False,
        "stopped": None,
        "steps": len(scenario.steps),
        "steps_done": 0,
        "timers": scenario.timers,
        "cluster_id": CLUSTER_ID,
        "max_message_kib": scenario.max_message_kib,
        "budget": scenario.budget,
        "load": {"start": list(os.getloadavg()), "end": None},
        "nodes": [],
        "batches": [],
        "posts": [],
        "conversation": None,
        "check": None,
        "unchecked": None,
        "verdict": None,
        "at_tip": None,
        "seated": scenario.seated,
        "page": None,
        "unshared": [],
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
        _stop(peers, registry, report)
    report["load"]["end"] = list(os.getloadavg())
    if watch.signal is not None:
        report["stopped"] = f"interrupted by {signal.Signals(watch.signal).name}"
    elif report["conversation"] is not None:
        judge.judge(settings, [peer.installation for peer in peers], report)
    check = report["check"]
    report["ok"] = report["stopped"] is None and check is not None and check["as_expected"]
    (settings.out / "run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (settings.out / "run.txt").write_text(table.render(report), encoding="utf-8")
    return report


def make(directory: Path) -> None:
    """Makes a directory that is not there yet, and those above it."""
    try:
        directory.mkdir(parents=True)
    except OSError as error:
        raise NoDirectory(str(error)) from None


def _deadline(settings: Settings) -> float:
    return time.monotonic() + settings.scenario.budget


def _timers(scenario: Scenario) -> List[str]:
    """The flags that give a peer the scenario's group timers."""
    if isinstance(scenario.timers, str):
        return ["--group-commit", scenario.timers]
    flags = ["--group-commit", "default"]
    for duration, milliseconds in scenario.timers.items():
        flags += [f"--group-{duration.replace('_', '-')}-ms", str(milliseconds)]
    return flags


def _group(
    settings: Settings, registry: Registry, peers: List[Peer], report: dict, watch: Watch
) -> None:
    scenario = settings.scenario
    started_at = time.time()
    dialing = wiring.dials(scenario.members)
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
            *_timers(scenario),
            "--cluster-id",
            str(CLUSTER_ID),
            "--max-message-kib",
            str(scenario.max_message_kib),
        ]
        for other in dialed:
            command += ["--static-node", peers[other].node_address]
        peer = Peer(name, command, settings.out, env, watch)
        peers.append(peer)
        report["nodes"].append({"name": name, "dials": [name_of(other) for other in dialed]})
        peer.read_ready(_deadline(settings))
        watch.processes.append(peer)
        report["nodes"][index]["installation"] = peer.installation
        report["nodes"][index]["ready_s"] = peer.ready_at - started_at
        report["nodes"][index]["start_s"] = peer.ready_at - peer.started_at

    _wire(settings, peers, wiring.links(dialing), report, watch)
    report["wired_s"] = time.time() - started_at

    creator = peers[0]
    convo = creator.run("/new sim", _deadline(settings))["convo"]
    report["conversation"] = convo
    seated = [creator]
    for step in scenario.steps:
        if isinstance(step, Seat):
            for joiners in measure.batches(peers[len(seated) : step.to], step.batch):
                report["batches"].append(_seat(settings, convo, seated, joiners))
                seated += joiners
        else:
            for sender in senders(step, len(seated)):
                post = _post(settings, registry, convo, peers[sender], seated)
                report["posts"].append(post)
                if post["unread"]:
                    unread = ", ".join(post["unread"])
                    raise Stopped(f"the post was not read within the budget by: {unread}")
        report["steps_done"] += 1


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


def _expected(members: List[Peer], said: Dict[str, str], word: str) -> List[Peer]:
    """The members a scenario expects `word` of: each one it says nothing else of."""
    return [member for member in members if said.get(member.name, word) == word]


def _seat(settings: Settings, convo: str, seated: List[Peer], joiners: List[Peer]) -> dict:
    """The creator adds `joiners` in one input. Returns once the members hold the new roster.

    A joiner holds it by a Welcome for a group of that size, and the creator sends a joiner one
    Welcome. So the Welcomes it published meanwhile are those of this roster, one a joiner.
    The members waited on are the creator, in whose log the Welcomes are counted, and each one
    the scenario expects at the tip.
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
    positions = settings.scenario.expect.positions
    at_the_tip = [creator] + _expected(seated[1:] + joiners, positions, "tip")
    for member in at_the_tip:
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
        held = [measure.held(_log(member), convo, epoch) for member in at_the_tip]
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


def _post(
    settings: Settings, registry: Registry, convo: str, poster: Peer, seated: List[Peer]
) -> dict:
    """`poster` posts, and each other member the scenario expects to read the post waits for it."""
    deadline = _deadline(settings)
    text = f"sim {secrets.token_hex(8)}"
    sent_at, _, result = poster.input(f"/send {convo} {text}", deadline)
    if result.get("ok") is not True:
        raise Stopped(f"{poster.name}: the post was refused: {result.get('error')}")

    others = [member for member in seated if member is not poster]
    readers = _expected(others, settings.scenario.expect.posts, "read")
    read_at = []
    unread = []
    for reader in readers:
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
    post.update(poster=poster.name, readers=len(readers), unread=unread)
    return post


def _last_words(peers: List[Peer], report: dict) -> None:
    """Asks every node still answering what it ends with."""
    for node, peer in zip(report["nodes"], peers):
        try:
            node["end"] = peer.node(time.monotonic() + ANSWER)
        except Stopped:
            continue
        del node["end"]["address"]


def _stop(peers: List[Peer], registry: Optional[Registry], report: dict) -> None:
    """Ends every process, and says of each peer how it ended and what it could not process."""
    early = [peer.ended() is not None for peer in peers]
    for peer in peers:
        peer.close()
    deadline = time.monotonic() + GRACE
    for node, peer, was_early in zip(report["nodes"], peers, early):
        killed = peer.reap(deadline)
        node.update(_ending(peer.ended(), was_early, killed))
        node["inbound_errors"] = _inbound_errors(peer)
        node["unchecked_senders"] = measure.unchecked(_log(peer))
    if registry is not None:
        registry.stop()


def _ending(status: int, early: bool, killed: bool) -> dict:
    """How a peer ended: its exit status, or the signal that ended it, and when."""
    if early:
        ended = EARLY
    elif killed:
        ended = KILLED
    else:
        ended = ASKED if status == 0 else IN_ITS_STOP
    return {
        "exit_status": status if status >= 0 else None,
        "exit_signal": -status if status < 0 else None,
        "ended": ended,
    }


def _inbound_errors(peer: Peer) -> Dict[str, int]:
    """How many `inbound_error` lines a peer wrote, by their message."""
    counted = collections.Counter(
        str(line.get("message")) for line in peer.written() if line.get("type") == "inbound_error"
    )
    return dict(counted.most_common())
