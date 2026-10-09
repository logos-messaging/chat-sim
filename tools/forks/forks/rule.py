"""Decides from trace records whether the members of each conversation still hold one group.

A state is an epoch and its fingerprint, with everyone who reported it. A link is two `epoch`
records one installation wrote one after the other, in one run, with no leave and no restart
between: proof that the second state came from the first. An epoch with two states is a split.
Behind, and where a split happened, are said only where links say them; who is live words a split
and decides the rest.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, fields
from functools import cached_property
from operator import attrgetter, itemgetter
from typing import Collection, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

from .records import Record, instant

TIP = "tip"
BEHIND = "behind"
LINEAGE_UNKNOWN = "lineage unknown"
REMOVED = "removed"
ENDED = "ended"
NEVER_JOINED = "never joined"
NO_DATA = "no data"
_LIVE = (TIP, BEHIND, LINEAGE_UNKNOWN)

WHOLE = "whole"

# A state: an epoch and its fingerprint.
Node = Tuple[int, str]
# The installation of a roster's entry.
_MEMBER = itemgetter(0)
# What a record says: every field but where it was written.
_SAID = attrgetter(*(field.name for field in fields(Record) if field.name not in ("run", "line")))


@dataclass(frozen=True)
class Mark:
    """Where and when a record was written."""

    run: str
    line: int
    time: str
    has_offset: bool


@dataclass(frozen=True)
class Kept:
    """What is kept of an installation in a conversation once the log it wrote is gone.

    `epoch` is that of its last record there, and `left` says whether that record was a leave.
    """

    convo: str
    installation: str
    account: Optional[str]
    epoch: int
    left: bool


@dataclass(frozen=True)
class Step:
    """An `epoch` or a leave an installation wrote; a leave holds no state."""

    kind: str
    epoch: int
    state: Optional[str]
    at: Mark


@dataclass(frozen=True)
class Installation:
    """A seat of the conversation: where it stands, and the records it wrote to get there."""

    id: str
    account: Optional[str]
    runs: List[str]
    position: str
    branch: Optional[str]
    epoch: Optional[int]
    state: Optional[str]
    history: List[Step]


@dataclass(frozen=True)
class StateId:
    """An epoch and the fingerprint of a state at it."""

    epoch: int
    state: str


@dataclass(frozen=True)
class State:
    """A reported state: its branch if it is on one, and the states it came from."""

    epoch: int
    state: str
    branch: Optional[str]
    members: int
    roster: List[str]
    held_by: List[str]
    after: List[StateId]


@dataclass(frozen=True)
class Branch:
    """A line of states no other branch shares: where it starts, its newest state, who is on it."""

    label: str
    since: int
    epoch: int
    state: str
    members: int
    installations: List[str]


@dataclass(frozen=True)
class Coverage:
    """How many of the seats that are neither ended nor removed share a log."""

    sharing: int
    seats: int


@dataclass(frozen=True)
class Message:
    """A post, and what became of it on every installation."""

    frame: str
    sender: str
    epoch: int
    state: Optional[str]
    at: Mark
    cells: Dict[str, str]
    why: Dict[str, str]


@dataclass(frozen=True)
class Delivery:
    """A change of a run's delivery state."""

    state: str
    at: Mark


@dataclass(frozen=True)
class Run:
    """A run the conversation's installations wrote in."""

    name: str
    final: bool
    delivery: List[Delivery]


@dataclass(frozen=True)
class Conversation:
    """What the shared logs say about one conversation."""

    id: str
    verdict: str
    split_epoch: Optional[int]
    coverage: Coverage
    branches: List[Branch]
    states: List[State]
    installations: List[Installation]
    messages: List[Message]
    unmatched_unreadable: int
    runs: List[Run]


def report(
    records: Iterable[Record],
    final: Collection[str] = (),
    kept: Iterable[Kept] = (),
    only: Optional[Collection[str]] = None,
) -> List[Conversation]:
    """The report of every conversation the records name, by its id.

    `final` names the runs whose writer is gone. `kept` holds the lines of installations whose
    log is gone. `only` names the conversations to report, when that is not every one.
    """
    logs = _Logs(records)
    lines: Dict[str, Dict[str, Kept]] = defaultdict(dict)
    # Of two lines for one installation, the one that ended later.
    for line in sorted(kept, key=attrgetter("epoch")):
        lines[line.convo][line.installation] = line
    gone = logs.final(final)
    return [
        _conversation(convo, logs, gone, lines.get(convo, {}))
        for convo in sorted(logs.convos)
        if only is None or convo in only
    ]


def as_json(conversation: Conversation) -> dict:
    """A conversation's report as the object `--json` holds for it."""
    return asdict(conversation)


class _Logs:
    """Every run's records, grouped the ways a conversation's report asks for them."""

    def __init__(self, records: Iterable[Record]) -> None:
        written: Dict[str, List[Record]] = defaultdict(list)
        self.accounts: Dict[str, str] = {}
        self.delivery: Dict[str, List[Record]] = defaultdict(list)
        self.convos: Dict[str, List[Record]] = defaultdict(list)
        # For a run, the runs that are it at an earlier length.
        self.earlier: Dict[str, Set[str]] = defaultdict(set)
        for record in sorted(records, key=_place):
            if record.kind == "delivery":
                self.delivery[record.run].append(record)
            else:
                written[record.installation].append(record)
        self.written = {
            installation: _as_written(own, self.earlier) for installation, own in written.items()
        }
        # A run that is among the logs at two lengths adds its records once.
        read = (record for own in self.written.values() for record in own)
        for record in sorted(read, key=_place):
            if record.kind == "run":
                self.accounts.setdefault(record.installation, record.account)
            else:
                self.convos[record.convo].append(record)

    def runs_of(self, installation: str) -> List[str]:
        """The runs an installation wrote in, in the order it wrote in them."""
        return list(dict.fromkeys(record.run for record in self.written.get(installation, ())))

    def final(self, marked: Collection[str]) -> FrozenSet[str]:
        """The runs whose writer is gone: the marked, and those a marked one is a length of."""
        marked = frozenset(marked)
        return marked | {run for run, earlier in self.earlier.items() if earlier & marked}


def _place(record: Record) -> Tuple[str, int]:
    return record.run, record.line


def _as_written(own: List[Record], earlier: Dict[str, Set[str]]) -> List[Record]:
    """One installation's records as it wrote them: its runs by first stamp, each run by line.

    A run whose records are the first records of another run is that run at an earlier length,
    a copy taken while it grew, and only the longer is read: `earlier` is told which runs a
    longer one stands for. Runs that open at one stamp go by what their records say and never by
    their names, which say nothing of when they were written.
    """
    runs: Dict[str, List[Record]] = defaultdict(list)
    for record in own:
        runs[record.run].append(record)
    longer = {
        run: [other for other, theirs in runs.items() if _earlier_length(records, theirs)]
        for run, records in runs.items()
    }
    read = [records for run, records in runs.items() if not longer[run]]
    for run, others in longer.items():
        for other in others:
            earlier[other].add(run)
    read.sort(key=lambda records: (instant(records[0].time), list(map(_sortable, records))))
    return [record for records in read for record in records]


def _earlier_length(records: List[Record], other: List[Record]) -> bool:
    """Whether a run's records are the first records of another run.

    Of two runs with the same records, the one whose name sorts later is.
    """
    if (len(records), other[0].run) >= (len(other), records[0].run):
        return False
    return all(_says_the_same(mine, theirs) for mine, theirs in zip(records, other))


def _says_the_same(one: Record, other: Record) -> bool:
    """Whether two records differ in nothing but where they were written."""
    # The stamps alone tell most pairs of runs apart, at a fraction of the cost.
    return one.time == other.time and _SAID(one) == _SAID(other)


def _sortable(record: Record) -> Tuple:
    """What a record says, as a value that sorts: an unset field comes before a set one."""
    return tuple((value is not None, value) for value in _SAID(record))


class _Lineage:
    """One conversation's states and the links between them, read from each writer's records."""

    def __init__(self, convo: str, logs: _Logs) -> None:
        self.reported: Dict[Node, Record] = {}
        self.holders: Dict[Node, Set[str]] = defaultdict(set)
        self.links: Dict[Node, Set[Node]] = defaultdict(set)
        self.parents: Dict[Node, Set[Node]] = defaultdict(set)
        self.accounts: Dict[str, str] = {}
        self.steps: Dict[str, List[Step]] = defaultdict(list)
        self.wrote: Dict[str, List[Node]] = defaultdict(list)
        self.held: Dict[Tuple[str, int], Node] = {}
        self.newest: Dict[str, Node] = {}
        # Whether an installation's last record is a leave; one that wrote neither kind is not here.
        self.left: Dict[str, bool] = {}
        for installation in sorted({record.installation for record in logs.convos[convo]}):
            self._follow(installation, convo, logs.written[installation])

    def _follow(self, installation: str, convo: str, written: List[Record]) -> None:
        prev: Optional[Node] = None
        run = None
        for record in written:
            if record.kind == "run" or record.run != run:
                # A GroupV2 conversation is not reloaded, so a restart begins a new chain.
                prev, run = None, record.run
            if record.convo != convo or record.kind not in ("epoch", "left"):
                continue
            # A roster without its writer is read as a leave.
            leaving = record.kind == "left" or installation not in map(_MEMBER, record.roster)
            self.left[installation] = leaving
            self.steps[installation].append(
                Step("left", record.epoch, None, _mark(record))
                if leaving
                else Step("epoch", record.epoch, record.state, _mark(record))
            )
            if leaving:
                prev = None
                self.newest.pop(installation, None)
                continue
            node = (record.epoch, record.state)
            if node not in self.reported:
                self.reported[node] = record
                for member, account in record.roster:
                    self.accounts.setdefault(member, account)
            self.holders[node].add(installation)
            self.wrote[installation].append(node)
            if prev is not None and prev[0] < node[0]:
                self.links[prev].add(node)
                self.parents[node].add(prev)
            prev = self.newest[installation] = self.held[installation, record.epoch] = node

    def roster(self, node: Node) -> FrozenSet[str]:
        """The installations a state's roster lists."""
        return frozenset(map(_MEMBER, self.reported[node].roster))

    def split_epoch(self) -> Optional[int]:
        """The first epoch that holds two states."""
        return min(self._split_epochs, default=None)

    @cached_property
    def _split_epochs(self) -> FrozenSet[int]:
        """The epochs that hold two states."""
        epochs = sorted(epoch for epoch, _ in self.reported)
        return frozenset(
            epoch for epoch, following in zip(epochs, epochs[1:]) if epoch == following
        )

    def position(self, seat: str, shares_a_log: bool, ended: bool, kept: Optional[Kept]) -> str:
        """Where a seat stands. The order of the checks is the rule."""
        if seat not in self.left:
            if kept is not None:
                return self._by_its_line(seat, kept)
            if self._off_every_roster(seat, self._newest_rosters):
                return REMOVED
            return NEVER_JOINED if shares_a_log else NO_DATA
        if self.left[seat]:
            return REMOVED
        node = self.newest[seat]
        later = _reach(node, self.links)
        if self._off_every_roster(seat, self._newest_of(later)):
            return REMOVED
        if ended:
            return ENDED
        if later:
            return BEHIND
        if any(other[0] > node[0] and not self._apart(node, other) for other in self.reported):
            return LINEAGE_UNKNOWN
        return TIP

    def _by_its_line(self, seat: str, line: Kept) -> str:
        """Where a seat that goes by its kept line stands."""
        # A roster older than the line cannot show the seat removed.
        ahead = [node for node in self._newest_rosters if node[0] >= line.epoch]
        return REMOVED if line.left or self._off_every_roster(seat, ahead) else ENDED

    @cached_property
    def _newest_rosters(self) -> List[Node]:
        """The states that hold the newest roster of a line.

        Nothing was seen to follow such a state, and it was not passed.
        """
        return [node for node in self._newest_of(self.reported) if not self._passed(node)]

    def _passed(self, node: Node) -> bool:
        """Whether an installation that reported a state moved on from it, though no link says so.

        A state on a side of a split the logs show is never passed: only links say what came
        after it. Any other is passed once an installation that reported it wrote a state at a
        higher epoch afterwards, as it does in a later run or after a leave.
        """
        if self._on_a_side(node):
            return False
        return any(
            later[0] > node[0]
            for holder in self.holders[node]
            for later in self.wrote[holder][self.wrote[holder].index(node) + 1 :]
        )

    def _on_a_side(self, node: Node) -> bool:
        """Whether some reported state is set apart from this one.

        One is exactly when this state's line crosses an epoch that holds two states: the other
        state there is on its own line.
        """
        return any(epoch in self._split_epochs for epoch, _ in self._line(node))

    def _newest_of(self, nodes: Iterable[Node]) -> List[Node]:
        """The states among `nodes` that nothing was seen to follow."""
        return [node for node in nodes if not self.links.get(node)]

    def _off_every_roster(self, seat: str, nodes: List[Node]) -> bool:
        """Whether the roster of each of these states, and there is one, lacks the seat."""
        return bool(nodes) and all(seat not in self.roster(node) for node in nodes)

    def _apart(self, one: Node, other: Node) -> bool:
        """Whether two states are known to be on different branches."""
        mine = defaultdict(set)
        for node in self._line(one):
            mine[node[0]].add(node)
        return any(mine.get(node[0], set()) - {node} for node in self._line(other))

    def _line(self, node: Node) -> Set[Node]:
        return {node} | _reach(node, self.parents) | _reach(node, self.links)

    def verdict(self, positions: Dict[str, str]) -> str:
        """Whether the group is whole, in the words the report uses."""
        live = [seat for seat, position in positions.items() if position in _LIVE]
        split = self.split_epoch()
        if split is not None:
            return self._split(split, live)
        if not live:
            return "no live member"
        if not any(len(holders) > 1 for holders in self.holders.values()):
            return "not enough data"
        live_at = {self.newest[seat] for seat in live}
        if any(self._under_an_unlinked_state(node) for node in live_at):
            return "whole as far as seen"
        if all(positions[seat] == TIP for seat in live):
            return WHOLE
        return "whole, members behind"

    def _under_an_unlinked_state(self, node: Node) -> bool:
        """Whether a later epoch holds a state that links from this one do not reach."""
        later = _reach(node, self.links)
        return any(other[0] > node[0] and other not in later for other in self.reported)

    def _split(self, split: int, live: List[str]) -> str:
        # Below the split is on no side, and a live state with a live one after it is on that
        # one's side. Of the rest, two from which links lead on to one state are one line and
        # count once, and two that no link ties to one line count as two.
        held = {self.newest[seat] for seat in live if self.newest[seat][0] >= split}
        later = {node: _reach(node, self.links) for node in held}
        sides: Set[Node] = set()
        # In the order of the states, so that the same logs give the same count.
        for node in sorted(node for node in held if not later[node] & held):
            if not any(later[node] & later[side] for side in sides):
                sides.add(node)
        came_from = [
            {parent for parent in self.parents.get(node, ()) if parent[0] == split - 1}
            for node in self.reported
            if node[0] == split
        ]
        # "at" where the logs show the state both sides left, otherwise where it was first seen.
        where = "at" if all(came_from) and set.intersection(*came_from) else "seen from"
        tail = {0: ", no side live", 1: ", one side live"}.get(len(sides), "")
        return f"split {where} epoch {split}{tail}"

    def branches(self) -> List[Set[Node]]:
        """The states by lineage, the branch more installations are on first.

        A state is on no branch when it is below the first split, or when the states links lead
        on to from it are not one line.
        """
        split = self.split_epoch()
        if split is None:
            return [set(self.reported)] if self.reported else []
        loose = {
            node
            for node in self.reported
            if node[0] >= split and self._leads_on_in_one_line(node)
        }
        branches = []
        while loose:
            branch, todo = set(), [loose.pop()]
            while todo:
                node = todo.pop()
                branch.add(node)
                near = (self.links.get(node, set()) | self.parents.get(node, set())) & loose
                loose -= near
                todo.extend(near)
            branches.append(branch)
        return sorted(branches, key=self._rank)

    def _rank(self, branch: Set[Node]) -> Tuple[int, str, int]:
        """More installations first, then by the fingerprint of the branch's first state."""
        epoch, fingerprint = min(branch)
        return -len(self.on(branch)), fingerprint, epoch

    def _leads_on_in_one_line(self, node: Node) -> bool:
        """Whether the states links lead on to from a state are one line.

        By epoch, each follows the one before. A link leads to a later epoch, so the one before
        reaches the next by one link or not at all.
        """
        later = sorted(_reach(node, self.links))
        return all(after in self.links.get(before, ()) for before, after in zip(later, later[1:]))

    def on(self, branch: Set[Node]) -> List[str]:
        """The installations whose newest state is on a branch."""
        return sorted(seat for seat, node in self.newest.items() if node in branch)


def _reach(start: Node, edges: Dict[Node, Set[Node]]) -> Set[Node]:
    seen: Set[Node] = set()
    todo = [start]
    while todo:
        for node in edges.get(todo.pop(), ()):
            if node not in seen:
                seen.add(node)
                todo.append(node)
    return seen


def _conversation(
    convo: str, logs: _Logs, final: FrozenSet[str], kept: Mapping[str, Kept]
) -> Conversation:
    lineage = _Lineage(convo, logs)
    # A line yields only to an `epoch` or a `left` record its installation wrote here.
    kept = {seat: line for seat, line in kept.items() if seat not in lineage.left}
    seats = sorted(
        {record.installation for record in logs.convos[convo]}
        | {member for node in lineage.reported for member in lineage.roster(node)}
        | set(kept)
    )
    positions = {
        seat: lineage.position(
            seat,
            shares_a_log=seat in logs.written,
            ended=all(run in final for run in logs.runs_of(seat)),
            kept=kept.get(seat),
        )
        for seat in seats
    }
    branches = lineage.branches()
    label = {node: _label(index) for index, branch in enumerate(branches) for node in branch}
    counted = [position for position in positions.values() if position not in (ENDED, REMOVED)]
    messages, unmatched = _messages(logs.convos[convo], seats, lineage, logs)
    return Conversation(
        id=convo,
        verdict=lineage.verdict(positions),
        split_epoch=lineage.split_epoch(),
        coverage=Coverage(sum(position != NO_DATA for position in counted), len(counted)),
        branches=[_branch(_label(index), branch, lineage) for index, branch in enumerate(branches)],
        states=[_state(node, lineage, label) for node in sorted(lineage.reported)],
        installations=[
            _installation(seat, positions[seat], lineage, logs, label, kept.get(seat))
            for seat in seats
        ],
        messages=messages,
        unmatched_unreadable=unmatched,
        runs=[
            _run(run, run in final, logs)
            for run in sorted({run for seat in seats for run in logs.runs_of(seat)})
        ],
    )


def _label(index: int) -> str:
    """A to Z, then AA, AB and on."""
    label = ""
    index += 1
    while index:
        index, letter = divmod(index - 1, 26)
        label = chr(ord("A") + letter) + label
    return label


def _mark(record: Record) -> Mark:
    return Mark(record.run, record.line, record.time, record.has_offset)


def _branch(label: str, branch: Set[Node], lineage: _Lineage) -> Branch:
    newest = max(branch)
    return Branch(
        label=label,
        since=min(branch)[0],
        epoch=newest[0],
        state=newest[1],
        members=lineage.reported[newest].members,
        installations=lineage.on(branch),
    )


def _state(node: Node, lineage: _Lineage, label: Dict[Node, str]) -> State:
    return State(
        epoch=node[0],
        state=node[1],
        branch=label.get(node),
        members=lineage.reported[node].members,
        roster=sorted(lineage.roster(node)),
        held_by=sorted(lineage.holders[node]),
        after=[StateId(*parent) for parent in sorted(lineage.parents.get(node, ()))],
    )


def _installation(
    seat: str,
    position: str,
    lineage: _Lineage,
    logs: _Logs,
    label: Dict[Node, str],
    kept: Optional[Kept],
) -> Installation:
    node = lineage.newest.get(seat)
    history = lineage.steps.get(seat, [])
    account = logs.accounts.get(seat, lineage.accounts.get(seat))
    if kept and kept.account is not None:
        account = kept.account
    return Installation(
        id=seat,
        account=account,
        runs=logs.runs_of(seat),
        position=position,
        branch=label.get(node) if node else None,
        epoch=history[-1].epoch if history else kept.epoch if kept else None,
        state=node[1] if node else None,
        history=history,
    )


def _run(name: str, final: bool, logs: _Logs) -> Run:
    changes = logs.delivery.get(name, ())
    return Run(name, final, [Delivery(change.state, _mark(change)) for change in changes])


def _messages(
    records: List[Record], seats: List[str], lineage: _Lineage, logs: _Logs
) -> Tuple[List[Message], int]:
    """A row for each post, and how many `unreadable` records name a frame no `sent` does."""
    posts: Dict[str, Record] = {}
    read: Dict[str, Set[str]] = defaultdict(set)
    unreadable: Dict[str, Dict[str, str]] = defaultdict(dict)
    for record in records:
        if record.kind == "sent":
            posts.setdefault(record.frame, record)
        elif record.kind == "received":
            read[record.frame].add(record.installation)
        elif record.kind == "unreadable":
            unreadable[record.frame].setdefault(record.installation, record.why)
    rows = []
    for post in sorted(posts.values(), key=lambda record: (instant(record.time), record.frame)):
        sent_in = lineage.held.get((post.installation, post.epoch))
        roster = lineage.roster(sent_in) if sent_in else None
        cells = {
            seat: _cell(seat, post, read[post.frame], unreadable[post.frame], roster, logs)
            for seat in seats
        }
        why = {seat: unreadable[post.frame][seat] for seat in seats if cells[seat] == "unreadable"}
        rows.append(
            Message(
                frame=post.frame,
                sender=post.installation,
                epoch=post.epoch,
                state=sent_in[1] if sent_in else None,
                at=_mark(post),
                cells=cells,
                why=why,
            )
        )
    unmatched = sum(record.kind == "unreadable" and record.frame not in posts for record in records)
    return rows, unmatched


def _cell(
    seat: str,
    post: Record,
    read: Set[str],
    unreadable: Dict[str, str],
    roster: Optional[FrozenSet[str]],
    logs: _Logs,
) -> str:
    """What became of a post on one installation."""
    if seat == post.installation:
        return "sent"
    if seat in read:
        return "read"
    if seat in unreadable:
        return "unreadable"
    if roster is not None and seat not in roster:
        return "not a member"
    if seat not in logs.written:
        return "no log"
    return "never got"
