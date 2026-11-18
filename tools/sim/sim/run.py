"""One run: a registry, peers on a network of their own or on a preset, and a scenario's steps.

The registry and the peers are processes of this machine, or each in a container of its own.
"""

from __future__ import annotations

import collections
import json
import os
import secrets
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from . import containers, judge, measure, one_process, table, wiring
from .peers import (
    GRACE,
    WATCH,
    Interrupted,
    OutOfBudget,
    Peer,
    Registry,
    Stopped,
    Watch,
    index_of,
    name_of,
)
from .scenario import Cut, Heal, Hold, Post, Scenario, Seat, senders

REPOSITORY = Path(__file__).resolve().parents[3]

# No preset holds this cluster id, and every node of a network of the run's own is given it.
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

    chat_cli: Optional[Path]
    scenario: Scenario
    out: Path
    # The share address of a collector the run's logs go to.
    share: Optional[str] = None
    # The runner that does the scenario in one process, where no peer runs `chat_cli`.
    one_process: Optional[Path] = None
    # The image whose chat-cli each peer runs in a container, where no peer runs `chat_cli`.
    image: Optional[str] = None
    # The container engine that runs the image: docker or podman.
    engine: str = containers.ENGINES[0]
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
        "group": scenario.group,
        "timers": scenario.timers,
        "preset": scenario.preset,
        "cluster_id": CLUSTER_ID if scenario.preset is None else None,
        "max_message_kib": scenario.max_message_kib if scenario.preset is None else None,
        "budget": scenario.budget,
        "seed": None,
        "network": None if scenario.network is None else asdict(scenario.network),
        "wire": None if scenario.wire is None else asdict(scenario.wire),
        "load": {"start": list(os.getloadavg()), "end": None},
        "nodes": [],
        "batches": [],
        "posts": [],
        "holds": [],
        "conversation": None,
        "withheld": None,
        "held_to": None,
        "check": None,
        "unchecked": None,
        "verdict": None,
        "at_tip": None,
        "seated": scenario.seated,
        "grow": None,
        "page": None,
        "unshared": [],
    }
    if settings.one_process is not None:
        one_process.run(settings, report, watch)
        installations = [node["installation"] for node in report["nodes"]]
        return _judged(settings, installations, report, watch)
    peers: List[Peer] = []
    registry: Optional[Registry] = None
    rig: Optional[containers.Rig] = None
    if settings.image is not None:
        rig = containers.Rig(settings.engine, settings.image, settings.out)
        report.update(rig=table.CONTAINERS, engine=settings.engine, image=settings.image)
        report["unremoved"] = None
    try:
        try:
            if rig is not None:
                rig.create(scenario.members)
            contain = None if rig is None else rig.registry
            registry = Registry(settings.registry, _directory(settings, rig), watch, contain)
            registry.read_url(_deadline(settings))
            watch.processes.append(registry)
            _group(settings, rig, registry, peers, report, watch)
        except Stopped as stopped:
            report["stopped"] = str(stopped)
        # A process that has ended has no last word, and the others still have theirs.
        watch.processes.clear()
        _last_words(scenario, peers, report, watch)
    except Interrupted:
        pass
    finally:
        watch.close()
        _stop(peers, registry, report)
        if rig is not None:
            report["unremoved"] = rig.remove()
    return _judged(settings, [peer.installation for peer in peers], report, watch)


def _directory(settings: Settings, rig: Optional[containers.Rig]) -> Path:
    """The run's directory as its processes are given it: the path a container mounts it at."""
    return settings.out if rig is None else rig.out


def _judged(settings: Settings, installations: List[str], report: dict, watch: Watch) -> dict:
    """Has the logs of a run that is over judged, and writes the report it returns.

    `installations` are the ids the logs know the members by, in the order of the members.
    A run a signal interrupted is not judged.
    """
    report["load"]["end"] = list(os.getloadavg())
    _withheld(settings.scenario, report)
    if watch.signal is not None:
        report["stopped"] = f"interrupted by {signal.Signals(watch.signal).name}"
    elif report["conversation"] is not None:
        judge.judge(settings, installations, report)
    check = report["check"]
    # A grow expects no outcome: it is as expected once it ends as it may.
    checked = report["grow"] is not None or (check is not None and check["as_expected"])
    report["ok"] = report["stopped"] is None and checked
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


def _delivery(scenario: Scenario) -> List[str]:
    """The flags that say which network a peer's node joins: the preset, or one of the run's own."""
    if scenario.preset is not None:
        # A name may begin with a dash, which chat-cli takes for a flag when it is a word of
        # its own.
        return [f"--preset={scenario.preset}"]
    return ["--cluster-id", str(CLUSTER_ID), "--max-message-kib", str(scenario.max_message_kib)]


def _network(scenario: Scenario, name: str) -> List[str]:
    """The flags that put what the peer `name` receives through the scenario's network model."""
    network = scenario.network
    if network is None:
        return []
    return [
        "--network-seed",
        str(network.seed),
        # A rate may be -0.0, which chat-cli takes for a flag when it is a word of its own.
        f"--network-loss={network.loss}",
        f"--network-welcome-loss={network.welcome_loss}",
        "--network-delay-ms",
        str(network.delay_of(name)),
    ]


def _group(
    settings: Settings,
    rig: Optional[containers.Rig],
    registry: Registry,
    peers: List[Peer],
    report: dict,
    watch: Watch,
) -> None:
    scenario = settings.scenario
    directory = _directory(settings, rig)
    started_at = time.time()
    if scenario.preset is None:
        dialing = wiring.dials(scenario.members)
    else:
        # Which nodes a node of a preset dials is the preset's to say.
        dialing = [[] for _ in range(scenario.members)]
    log = {"RUST_LOG": measure.LOG_FILTER}
    for index, dialed in enumerate(dialing):
        name = name_of(index)
        arguments = [
            "--transport",
            "logos-delivery",
            "--name",
            name,
            "--data",
            str(directory / "data"),
            "--keys",
            str(directory / "keys"),
            "--registry-url",
            registry.url,
            *_timers(scenario),
            *_delivery(scenario),
            *_network(scenario, name),
        ]
        for other in dialed:
            arguments += ["--static-node", peers[other].node_address]
        if rig is None:
            command, env = [str(settings.chat_cli), *arguments], dict(os.environ, **log)
        else:
            command, env = rig.member(name, index, arguments, log), dict(os.environ)
        peer = Peer(name, command, directory, env, watch, in_container=rig is not None)
        peers.append(peer)
        report["nodes"].append({"name": name, "dials": [name_of(other) for other in dialed]})
        peer.read_ready(_deadline(settings))
        watch.processes.append(peer)
        if rig is not None:
            report["nodes"][index]["ip"] = rig.address(index)
            if scenario.wire is not None:
                rig.shape(name, scenario.wire.delay_of(name), scenario.wire)
        report["nodes"][index]["installation"] = peer.installation
        report["nodes"][index]["ready_s"] = peer.ready_at - started_at
        report["nodes"][index]["start_s"] = peer.ready_at - peer.started_at

    if scenario.preset is None:
        _wire(settings, peers, wiring.links(dialing), report, watch)
    else:
        _link(settings, peers, report, watch)
    report["wired_s"] = time.time() - started_at

    creator = peers[0]
    convo = creator.run("/new sim", _deadline(settings))["convo"]
    report["conversation"] = convo
    seated = [creator]
    # The sides of the cut in force, the member whose rule is armed for the next batch, and
    # the members a withheld candidate left in a state of their own.
    cut: List[List[Peer]] = []
    armed: Optional[Peer] = None
    apart: List[Peer] = []
    for step in scenario.steps:
        if isinstance(step, Seat):
            for joiners in measure.batches(peers[len(seated) : step.to], step.batch):
                report["batches"].append(_seat(settings, convo, seated, joiners, armed, apart))
                seated += joiners
                armed = None
        elif isinstance(step, Post):
            for sender in senders(step, len(seated)):
                post = _post(settings, registry, convo, peers[sender], seated, apart)
                report["posts"].append(post)
                if post["unread"]:
                    unread = ", ".join(post["unread"])
                    raise Stopped(f"the post was not read within the budget by: {unread}")
        elif isinstance(step, Hold):
            report["holds"].append(_hold(settings, peers, step.seconds, watch))
        elif isinstance(step, Cut):
            cut = [[peers[index_of(name)] for name in side] for side in step.sides]
            _cut(settings, cut)
        elif isinstance(step, Heal):
            for member in cut[0] + cut[1]:
                member.run("/network heal", _deadline(settings))
        else:
            armed = peers[index_of(step.member)]
            armed.run("/network withhold candidate", _deadline(settings))
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


def _link(settings: Settings, peers: List[Peer], report: dict, watch: Watch) -> None:
    """Waits until every node of a preset holds a link.

    A mesh is not waited for: an idle node of a preset has none.
    """
    deadline = _deadline(settings)
    while True:
        alone = []
        for node, peer in zip(report["nodes"], peers):
            node["wired"] = _links(peer.node(time.monotonic() + ANSWER))
            if node["wired"]["peers"] < 1:
                alone.append(peer.name)
        if not alone:
            return
        if time.monotonic() + POLL >= deadline:
            raise OutOfBudget(f"no link within the budget: {', '.join(alone)}")
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


def _hold(settings: Settings, peers: List[Peer], seconds: float, watch: Watch) -> dict:
    """Asks every node its state, does nothing for `seconds`, and asks every node again.

    Returns the seconds and the two answers of each node. On a preset it tells of each node
    what `_unheld` rose by from the one answer to the other, and as the rate how much that is
    a node a minute.
    """
    before = [peer.node(time.monotonic() + ANSWER) for peer in peers]
    _pause(seconds, watch)
    after = [peer.node(time.monotonic() + ANSWER) for peer in peers]
    for answer in before + after:
        del answer["address"]
    nodes = [
        {"name": peer.name, "before": first, "after": second}
        for peer, first, second in zip(peers, before, after)
    ]
    hold = {"seconds": seconds, "nodes": nodes}
    if settings.scenario.preset is not None:
        for node in nodes:
            first, second = _unheld(node["before"]), _unheld(node["after"])
            node["unheld"] = None if None in (first, second) else second - first
        unheld = [node["unheld"] for node in nodes]
        hold["rate"] = None if None in unheld else sum(unheld) / (len(nodes) * seconds / 60)
    return hold


def _unheld(state: dict) -> Optional[int]:
    """The dials a node of a preset made less the links it holds, or `None` when it does not say.

    Every link of such a node is taken for one it dialed, so these are the connections it
    opened that are not links it holds: a link it lost, a connection that never became a
    link, a dial back to a node that asked for one. A link it did not dial lowers the count,
    which may then be below 0.
    """
    dialed, held = state.get("dials_ok"), state["peers"]
    return None if dialed is None or held is None else dialed - held


def _expected(members: List[Peer], said: Dict[str, str], word: str) -> List[Peer]:
    """The members a scenario expects `word` of: each one it says nothing else of."""
    return [member for member in members if said.get(member.name, word) == word]


def _cut(settings: Settings, sides: List[List[Peer]]) -> None:
    """Cuts each side off from the other: each member is given the app ids of the other side.

    A member tells its app id as it joins, so one that has not told it yet is waited for.
    """
    deadline = _deadline(settings)
    app_ids = [[member.read_app_id(deadline) for member in side] for side in sides]
    for side, others in zip(sides, reversed(app_ids)):
        for member in side:
            member.run(f"/network cut {' '.join(others)}", deadline)


def _seat(
    settings: Settings,
    convo: str,
    seated: List[Peer],
    joiners: List[Peer],
    withheld: Optional[Peer],
    apart: List[Peer],
) -> dict:
    """The creator adds `joiners` in one input. Returns once the members hold the new roster.

    A joiner holds it by a Welcome for a group of that size, and the creator sends a joiner one
    Welcome. So the Welcomes it published meanwhile are those of this roster, one a joiner.
    The members waited on are the creator, in whose log the Welcomes are counted, and each one
    the scenario expects at the tip.

    `withheld` is the member whose armed rule withholds a candidate of this batch, when there
    is one. Where the batch leaves it in a state of its own it is added to `apart`.
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
        if _alone(withheld, at_the_tip, held):
            apart.append(withheld)
        elif len(states) > 1:
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
    if withheld is not None:
        _withheld_line(withheld, sent_at, deadline)
    return {
        "joiners": len(joiners),
        "members": members,
        "joined_s": joined_at - sent_at,
        "seated_s": seated_at - sent_at,
        "welcome_bytes": welcome_bytes,
    }


def _alone(member: Optional[Peer], members: List[Peer], held: List[Optional[str]]) -> bool:
    """Whether `member` holds an epoch in a state of its own, and the other `members` in one.

    `held` is the state each of `members` holds the epoch in, or `None` where it holds none.
    That is the split a candidate withheld from `member` makes when the group applies it.
    """
    if member is None:
        return False
    own = held[members.index(member)]
    others = {
        state for other, state in zip(members, held) if other is not member and state is not None
    }
    return own is not None and len(others) == 1 and own not in others


def _withheld_line(member: Peer, added_at: float, deadline: float) -> None:
    """Waits for the line by which `member` says that its rule withheld a candidate.

    The rule takes the first candidate to arrive once it is armed. One it took before the
    `/add` was written at `added_at` was of an earlier batch, and none of this one was withheld.
    """
    read_at, _ = member.wait_for(
        "line of a withheld candidate",
        lambda line: line.get("type") == "network_drop" and line.get("why") == "withheld",
        deadline,
    )
    if read_at < added_at:
        raise Stopped(f"{member.name}: a candidate was withheld before the /add was written")


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
    settings: Settings,
    registry: Registry,
    convo: str,
    poster: Peer,
    seated: List[Peer],
    apart: List[Peer],
) -> dict:
    """`poster` posts, and each other member the scenario expects to read the post waits for it.

    A member in `apart` holds the group in a state of its own, and is not waited on.
    """
    deadline = _deadline(settings)
    text = f"sim {secrets.token_hex(8)}"
    sent_at, _, result = poster.input(f"/send {convo} {text}", deadline)
    if result.get("ok") is not True:
        raise Stopped(f"{poster.name}: the post was refused: {result.get('error')}")

    others = [member for member in seated if member is not poster and member not in apart]
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


def _last_words(scenario: Scenario, peers: List[Peer], report: dict, watch: Watch) -> None:
    """Asks every node still answering what it ends with."""
    over_at = time.monotonic()
    for node, peer in zip(report["nodes"], peers):
        try:
            if scenario.network is not None:
                # A frame held when the steps are over is due within the member's delay, and
                # no wait of a run is longer than its budget.
                delay = scenario.network.delay_of(peer.name) / 1000
                held_until = over_at + min(delay, scenario.budget)
                node["network"] = _network_at_rest(peer, held_until, watch)
            node["end"] = peer.node(time.monotonic() + ANSWER)
        except Stopped:
            continue
        del node["end"]["address"]
        if scenario.preset is not None:
            node["unheld"] = _unheld(node["end"])


def _network_at_rest(peer: Peer, held_until: float, watch: Watch) -> dict:
    """What the network model of `peer` answers once it holds no frame, or at `held_until`.

    A peer abandons the frames its model holds when it ends, and its log then lacks what
    became of them.
    """
    while True:
        network = peer.run("/network", time.monotonic() + ANSWER)["network"]
        if network["frames"]["held"] == 0 or time.monotonic() >= held_until:
            return network
        _pause(POLL, watch)


def _withheld(scenario: Scenario, report: dict) -> None:
    """Says what was withheld from a member, and which outcome the run is held to by it.

    Both go by what the member's network model last answered: whether the steward that minted
    the candidate has broadcast a Welcome since, which a steward does for a commit of its own
    that it merged. One batch follows the step, so that commit is the candidate, and the group
    applied it where its stewards merged one commit.
    """
    answers = {node["name"]: node.get("network") for node in report["nodes"]}
    withheld = (answers.get(scenario.withheld_from) or {}).get("withheld")
    if withheld is None:
        return
    minters = [node["name"] for node in report["nodes"] if node["app_id"] == withheld["app_id"]]
    report["withheld"] = {
        "from": scenario.withheld_from,
        "seat": withheld["seat"],
        "app_id": withheld["app_id"],
        "minter": minters[0] if minters and withheld["app_id"] is not None else None,
        "applied": withheld["applied"],
    }
    report["held_to"] = "applied" if withheld["applied"] else "not applied"


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
        node["app_id"] = peer.app_id
        if report["network"] is not None:
            node["network_dropped"] = _network_dropped(peer)
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


def _network_dropped(peer: Peer) -> Dict[str, int]:
    """How many `network_drop` lines a peer wrote, by their `why`."""
    counted = collections.Counter(
        str(line.get("why")) for line in peer.written() if line.get("type") == "network_drop"
    )
    return dict(counted.most_common())
