"""Decides from trace records whether the members of each conversation still hold one group.

A state is an epoch and its fingerprint, with everyone who reported it. A link is two `epoch`
records one installation wrote one after the other, in one run, with no leave and no restart
between: proof that the second state came from the first. An epoch with two states is a split.
Behind, and where a split happened, are said only where links say them; who is live words a split
and decides the rest.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields
from datetime import datetime
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
# The order positions are listed in: the live ones first.
POSITIONS = (TIP, BEHIND, LINEAGE_UNKNOWN, ENDED, REMOVED, NEVER_JOINED, NO_DATA)

WHOLE = "whole"

# How a state stands among the branches.
ON_A_BRANCH = "branch"
SHARED = "shared"
ONE_SIDE = "side"
UNKNOWN = "unknown"

# The headings installations are listed under.
ONE_LINE = "one line"
NEITHER_BRANCH = "neither branch"
NOT_PLACED = "not placed"

# What a lane holds at an epoch.
WROTE = "wrote"
UNREACHED = "unreached"
LISTED = "listed"

# What a record of a post says became of it on the installation that wrote it.
_SAYS = {"sent": "sent", "received": "read", "unreadable": "unreadable"}
# The cells of the messages a page draws before it is asked for the rest: what a browser draws,
# each with its mark on the timeline, in about a tenth of a second. A row counts as a cell for
# each installation, on a page that starts folded as well, which draws a cell for each fold.
SHOWN_CELLS = 2000
# The installations a page draws a lane each for. With more it starts folded, a row for each
# heading and position. Firefox gives a window 1080 px high a page of 994 px, the box of the
# lanes takes 86% of it, and under the table's head of 38 px and one heading of 32 px that is
# room for 23 lanes of one run each, at 33 px.
LANES_IN_SIGHT = 23
# What a post can come to on an installation, in the order its counts are listed.
_FATES = ("sent", "read", "unreadable", "not a member", "no log", "no record")

_1970 = datetime(1970, 1, 1)

# A state: an epoch and its fingerprint.
Node = Tuple[int, str]
# The installation of a roster's entry.
_MEMBER = itemgetter(0)
# What a record says: every field but where it was written and the commit it names, which
# decides nothing.
_SAID = attrgetter(
    *(field.name for field in fields(Record) if field.name not in ("run", "line", "commit", "by"))
)


@dataclass(frozen=True)
class Mark:
    """Where and when a record was written.

    `seconds` is the stamp as it is compared: seconds since 1970 in UTC.
    """

    run: str
    line: int
    time: str
    has_offset: bool
    seconds: float


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
class Span:
    """A stretch of a run, in seconds since 1970 in UTC, and whether its delivery was online."""

    start: float
    end: float
    online: bool


@dataclass(frozen=True)
class Cell:
    """What a lane holds at an epoch.

    `steps` are the places in the installation's `history` of the records it wrote there, in
    the order it wrote them: none where it wrote nothing.
    """

    epoch: int
    kind: str
    steps: List[int]


@dataclass(frozen=True)
class Installation:
    """A seat of the conversation: where it stands, and the records it wrote to get there.

    `kept` says whether it goes by a line kept of a log that is gone. `listed` holds, for a
    seat that wrote nothing here, the epochs of the states whose roster lists it. `unreached`
    holds, for a seat that is behind, the epochs of the states links lead on to from its own,
    and `behind_by` how far the last of them is above it. `cells` are its lane, epoch by epoch.
    `spans` are the runs it wrote in, as stretches of time.
    """

    id: str
    account: Optional[str]
    runs: List[str]
    kept: bool
    position: str
    branch: Optional[str]
    epoch: Optional[int]
    state: Optional[str]
    listed: List[int]
    unreached: List[int]
    behind_by: Optional[int]
    history: List[Step]
    cells: List[Cell]
    spans: List[Span]


@dataclass(frozen=True)
class StateId:
    """An epoch and the fingerprint of a state at it."""

    epoch: int
    state: str


@dataclass(frozen=True)
class Commit:
    """A commit that installations named as the one that made a state, and who committed it.

    `holders` counts the installations that named the two together.
    """

    commit: str
    by: str
    holders: int


@dataclass(frozen=True)
class State:
    """A reported state: its branch if it is on one, and the states it came from.

    `lineage` says how it stands among the branches, and `toward` names the branch of a state
    that is one side's. `holders` counts the installations that reported it. `row` is the row of
    the lineage a page draws it in, counted from the top. `commits` are what its holders named as
    the commit that made it, the most named first.
    """

    epoch: int
    state: str
    branch: Optional[str]
    lineage: str
    toward: Optional[str]
    holders: int
    members: int
    roster: List[str]
    held_by: List[str]
    after: List[StateId]
    row: int
    commits: List[Commit]


@dataclass(frozen=True)
class Branch:
    """A line of states no other branch shares: where it starts, its newest state, who is on it.

    `first_commits` are the `commits` of its first state, or of every state it holds at `since`
    where two there lead on to one.
    """

    label: str
    since: int
    epoch: int
    state: str
    members: int
    installations: List[str]
    first_commits: List[Commit]


@dataclass(frozen=True)
class Coverage:
    """How many of the seats that are neither ended nor removed share a log."""

    sharing: int
    seats: int


@dataclass(frozen=True)
class Position:
    """A position some seat is in, and the seats in it."""

    position: str
    count: int
    installations: List[str]


@dataclass(frozen=True)
class Lanes:
    """A heading installations are listed under, and those under it in the order they are read.

    `positions` are the positions of those installations, each once.
    """

    under: str
    branch: Optional[str]
    positions: List[str]
    installations: List[str]


@dataclass(frozen=True)
class Range:
    """The lowest and the highest of a number among the installations of a fold."""

    low: int
    high: int


@dataclass(frozen=True)
class Held:
    """A state at an epoch, among the installations of a fold.

    `count` is how many of them wrote it there, and `first` and `last` are the stamps of the
    first and the last of those records, in seconds since 1970 in UTC.
    """

    state: str
    count: int
    first: float
    last: float


@dataclass(frozen=True)
class Stack:
    """What the lanes of a fold hold between them at an epoch.

    `wrote` counts the installations that wrote a record there: `states` says how many wrote
    each state and `left` how many a leave, and `several` counts those that are under more than
    one of them. `unreached` and `listed` count the lanes that hold that there.
    """

    epoch: int
    wrote: int
    states: List[Held]
    left: int
    several: int
    unreached: int
    listed: int


@dataclass(frozen=True)
class Fold:
    """The installations under one heading that are in one position, as one row.

    `kept` counts those that go by a kept line, and `logs` those that wrote in a log that is
    held. `epoch` and `behind_by` reach from the lowest to the highest that its installations
    say, and `listed_from` does for those without an epoch, by the first epoch a roster lists
    each at. `cells` are their lanes laid over one another, epoch by epoch.
    """

    under: str
    branch: Optional[str]
    position: str
    installations: List[str]
    kept: int
    logs: int
    epoch: Optional[Range]
    behind_by: Optional[Range]
    listed_from: Optional[Range]
    cells: List[Stack]


@dataclass(frozen=True)
class Fates:
    """What became of a post on the installations of a fold, as counts.

    `cells` counts them by what became of it, and `why` counts the unreadable by their cause.
    """

    cells: Dict[str, int]
    why: Dict[str, int]


@dataclass(frozen=True)
class Timeline:
    """The first and the last stamp of the records that name the conversation.

    Both are seconds since 1970 in UTC. A run reaches past them where it wrote anything else
    before or after: its `run` record, a `delivery` record or a record of another conversation.
    """

    start: float
    end: float


@dataclass(frozen=True)
class Message:
    """A post, and what became of it on every installation.

    `utc` is the stamp it was sent at, in UTC and cut to its second. `marks` holds, for each
    installation whose cell stands for a record it wrote, where that record was written. `folds`
    counts the cells of each fold of the conversation, in the order of its `folds`.
    """

    frame: str
    sender: str
    epoch: int
    state: Optional[str]
    at: Mark
    utc: str
    cells: Dict[str, str]
    why: Dict[str, str]
    marks: Dict[str, Mark]
    folds: List[Fates]


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
    """What the shared logs say about one conversation.

    `shown_messages` is how many of the last `messages` a page draws before it is asked for the
    rest. `folds` are the `lanes` by position, and `folded` says whether a page starts with a
    row for each of them in place of a lane for each installation.
    """

    id: str
    verdict: str
    split_epoch: Optional[int]
    coverage: Coverage
    positions: List[Position]
    epochs: List[int]
    branches: List[Branch]
    states: List[State]
    lanes: List[Lanes]
    folds: List[Fold]
    folded: bool
    installations: List[Installation]
    timeline: Timeline
    messages: List[Message]
    shown_messages: int
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
    """Whether two records differ in nothing but where they were written and the commit named."""
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
        # For a state, each commit and committer an installation named as what made it.
        self.named: Dict[Node, Set[Tuple[str, str, str]]] = defaultdict(set)
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
            if record.commit is not None:
                self.named[node].add((record.commit, record.by, installation))
            self.wrote[installation].append(node)
            if prev is not None and prev[0] < node[0]:
                self.links[prev].add(node)
                self.parents[node].add(prev)
            prev = self.newest[installation] = self.held[installation, record.epoch] = node

    def roster(self, node: Node) -> FrozenSet[str]:
        """The installations a state's roster lists."""
        return frozenset(map(_MEMBER, self.reported[node].roster))

    def commits(self, *nodes: Node) -> List[Commit]:
        """What the holders of states named as the commit that made each, the most named first.

        An installation counts once for a commit, at however many of the states it named it.
        """
        told = {naming for node in nodes for naming in self.named.get(node, ())}
        named = Counter((commit, by) for commit, by, _ in told)
        return [
            Commit(commit, by, holders)
            for (commit, by), holders in sorted(named.items(), key=lambda each: (-each[1], each[0]))
        ]

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

    def lineages(self, branches: List[Set[Node]]) -> Dict[Node, Tuple[str, Optional[str]]]:
        """How each state stands among the branches, and the branch a state of one side leads to.

        A state on a branch of a split is that branch's. Any other state is shared when links
        lead on from it to the newest state of two or more branches, one side's when they lead
        to that of one branch alone, and of unknown lineage when they lead to none. Without a
        split there is one branch and no side: its newest state and the states links lead on
        from to it are shared.
        """
        split = self.split_epoch() is not None
        on_a_branch = set().union(*branches) if split else set()
        tips = {_label(index): max(branch) for index, branch in enumerate(branches)}
        leading = {name: _reach(tip, self.parents) | {tip} for name, tip in tips.items()}
        found: Dict[Node, Tuple[str, Optional[str]]] = {}
        for node in self.reported:
            toward = [name for name, below in leading.items() if node in below]
            if node in on_a_branch:
                found[node] = ON_A_BRANCH, None
            elif split and len(toward) == 1:
                found[node] = ONE_SIDE, toward[0]
            else:
                found[node] = (SHARED if toward else UNKNOWN), None
        return found

    def rows(self, branches: List[Set[Node]]) -> Dict[Node, int]:
        """The row of the lineage each state is drawn in, counted from the top.

        A row is a line of states. Each branch has one, in the order of the branches: its newest
        state and the states links lead to it from, each by the latest it came from, back to a
        state an earlier row holds, which the row leaves. A state no such line reaches starts a
        row the same way, the newest first. A row stands under the one it leaves, nearer to it
        the later it leaves, so that a link to a row's first state crosses no row that leaves
        earlier.
        """
        line: Dict[Node, int] = {}
        # For each line, by when it was found: the state it leaves, or None for a first line.
        leaves: List[Optional[Node]] = []
        for node in [*map(max, branches), *sorted(self.reported, reverse=True)]:
            if node in line:
                continue
            while True:
                line[node] = len(leaves)
                came = self.parents.get(node, set())
                held = came & line.keys()
                if held or not came:
                    leaves.append(max(held, default=None))
                    break
                node = max(came)
        under: Dict[Optional[int], List[int]] = defaultdict(list)
        for index, left in enumerate(leaves):
            under[None if left is None else line[left]].append(index)
        order: List[int] = []
        todo = under[None][::-1]
        while todo:
            index = todo.pop()
            order.append(index)
            # The later a line leaves, the sooner it is taken; of two that leave at one epoch,
            # the one found first.
            nearest = sorted(under[index], key=lambda found: (-leaves[found][0], found))
            todo.extend(reversed(nearest))
        row = {index: at for at, index in enumerate(order)}
        return {node: row[index] for node, index in line.items()}

    def listed(self) -> Dict[str, List[int]]:
        """For each seat that wrote nothing here, the epochs of the states whose roster lists it."""
        listed: Dict[str, Set[int]] = defaultdict(set)
        for (epoch, _), record in self.reported.items():
            for member in map(_MEMBER, record.roster):
                if member not in self.left:
                    listed[member].add(epoch)
        return {seat: sorted(epochs) for seat, epochs in listed.items()}

    def unreached(self, seat: str) -> List[int]:
        """The epochs of the states links lead on to from a seat's newest state."""
        return sorted({epoch for epoch, _ in _reach(self.newest[seat], self.links)})


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
    lineages = lineage.lineages(branches)
    rows = lineage.rows(branches)
    listed = lineage.listed()
    counted = [position for position in positions.values() if position not in (ENDED, REMOVED)]
    installations = [
        _installation(
            seat, positions[seat], lineage, logs, label, kept.get(seat), listed.get(seat, [])
        )
        for seat in seats
    ]
    # Every `epoch` and `left` record is a step, stamped already.
    stamps = [step.at.seconds for seat in installations for step in seat.history]
    stamps += [
        _seconds(record.time)
        for record in logs.convos[convo]
        if record.kind not in ("epoch", "left")
    ]
    lanes = _lanes(positions, lineage, label, lineages)
    folds = _folds(lanes, installations)
    messages, unmatched = _messages(convo, seats, lineage, logs, folds)
    return Conversation(
        id=convo,
        verdict=lineage.verdict(positions),
        split_epoch=lineage.split_epoch(),
        coverage=Coverage(sum(position != NO_DATA for position in counted), len(counted)),
        positions=[
            Position(position, len(here), here)
            for position in POSITIONS
            for here in [[seat for seat in seats if positions[seat] == position]]
            if here
        ],
        epochs=sorted(
            {epoch for epoch, _ in lineage.reported}
            | {step.epoch for seat in installations for step in seat.history}
        ),
        branches=[_branch(_label(index), branch, lineage) for index, branch in enumerate(branches)],
        states=[
            _state(node, lineage, label, lineages[node], rows[node])
            for node in sorted(lineage.reported)
        ],
        lanes=lanes,
        folds=folds,
        folded=len(seats) > LANES_IN_SIGHT,
        installations=installations,
        timeline=Timeline(min(stamps), max(stamps)),
        messages=messages,
        shown_messages=min(len(messages), max(1, SHOWN_CELLS // len(seats))),
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
    return Mark(record.run, record.line, record.time, record.has_offset, _seconds(record.time))


def _seconds(time: str) -> float:
    """A stamp as seconds since 1970 in UTC; without an offset it is read as UTC."""
    written, fraction = instant(time)
    return (written - _1970).total_seconds() + float(f"0.{fraction or 0}")


def _branch(label: str, branch: Set[Node], lineage: _Lineage) -> Branch:
    newest = max(branch)
    since = min(branch)[0]
    return Branch(
        label=label,
        since=since,
        epoch=newest[0],
        state=newest[1],
        members=lineage.reported[newest].members,
        installations=lineage.on(branch),
        first_commits=lineage.commits(*(node for node in branch if node[0] == since)),
    )


def _state(
    node: Node,
    lineage: _Lineage,
    label: Dict[Node, str],
    stands: Tuple[str, Optional[str]],
    row: int,
) -> State:
    return State(
        epoch=node[0],
        state=node[1],
        branch=label.get(node),
        lineage=stands[0],
        toward=stands[1],
        holders=len(lineage.holders[node]),
        members=lineage.reported[node].members,
        roster=sorted(lineage.roster(node)),
        held_by=sorted(lineage.holders[node]),
        after=[StateId(*parent) for parent in sorted(lineage.parents.get(node, ()))],
        row=row,
        commits=lineage.commits(node),
    )


def _installation(
    seat: str,
    position: str,
    lineage: _Lineage,
    logs: _Logs,
    label: Dict[Node, str],
    kept: Optional[Kept],
    listed: List[int],
) -> Installation:
    node = lineage.newest.get(seat)
    history = lineage.steps.get(seat, [])
    account = logs.accounts.get(seat, lineage.accounts.get(seat))
    if kept and kept.account is not None:
        account = kept.account
    unreached = lineage.unreached(seat) if position == BEHIND else []
    # A seat whose log is here, or was, is not one nobody shared a log of.
    unshared = seat not in logs.written and kept is None
    return Installation(
        id=seat,
        account=account,
        runs=logs.runs_of(seat),
        kept=kept is not None,
        position=position,
        branch=label.get(node) if node else None,
        epoch=history[-1].epoch if history else kept.epoch if kept else None,
        state=node[1] if node else None,
        listed=listed,
        unreached=unreached,
        behind_by=unreached[-1] - node[0] if unreached else None,
        history=history,
        cells=_cells(history, unreached, listed if unshared else []),
        spans=_spans(seat, logs),
    )


def _cells(history: List[Step], unreached: List[int], listed: List[int]) -> List[Cell]:
    """A lane: what it holds at each epoch it holds something at, by epoch.

    What the installation wrote at an epoch stands there, whatever else is said of the epoch.
    An epoch links lead on to from its newest state is unreached, and an epoch whose roster
    lists a seat nobody shared a log of is listed.
    """
    wrote: Dict[int, List[int]] = defaultdict(list)
    for place, step in enumerate(history):
        wrote[step.epoch].append(place)
    held = {epoch: Cell(epoch, LISTED, []) for epoch in listed}
    held.update({epoch: Cell(epoch, UNREACHED, []) for epoch in unreached})
    held.update({epoch: Cell(epoch, WROTE, places) for epoch, places in wrote.items()})
    return [held[epoch] for epoch in sorted(held)]


def _lanes(
    positions: Dict[str, str],
    lineage: _Lineage,
    label: Dict[Node, str],
    lineages: Dict[Node, Tuple[str, Optional[str]]],
) -> List[Lanes]:
    """The installations under the headings they are read under, by position under each.

    An installation goes by the last state it wrote, also when a leave follows it: under that
    state's branch, under the shared states when it is one, and with those the logs place
    nowhere otherwise, as when it wrote no state.
    """
    split = lineage.split_epoch() is not None
    shared = (NEITHER_BRANCH if split else ONE_LINE), None
    nowhere = NOT_PLACED, None
    under: Dict[Tuple[str, Optional[str]], List[str]] = {
        (ON_A_BRANCH, name): [] for name in dict.fromkeys(label.values()) if split
    }
    under.update({shared: [], nowhere: []})
    for seat in sorted(positions, key=lambda seat: (POSITIONS.index(positions[seat]), seat)):
        wrote = lineage.wrote.get(seat)
        stands = lineages[wrote[-1]][0] if wrote else UNKNOWN
        if stands == ON_A_BRANCH:
            under[ON_A_BRANCH, label[wrote[-1]]].append(seat)
        else:
            under[shared if stands == SHARED else nowhere].append(seat)
    return [
        Lanes(heading, branch, list(dict.fromkeys(positions[seat] for seat in seats)), seats)
        for (heading, branch), seats in under.items()
        if seats
    ]


def _folds(lanes: List[Lanes], installations: List[Installation]) -> List[Fold]:
    """Each heading's installations by position, in the order they are read in."""
    seats = {seat.id: seat for seat in installations}
    return [
        _fold(lane, position, [seats[seat] for seat in lane.installations])
        for lane in lanes
        for position in lane.positions
    ]


def _fold(lane: Lanes, position: str, under: List[Installation]) -> Fold:
    seats = [seat for seat in under if seat.position == position]
    return Fold(
        under=lane.under,
        branch=lane.branch,
        position=position,
        installations=[seat.id for seat in seats],
        kept=sum(seat.kept for seat in seats),
        logs=sum(bool(seat.runs) for seat in seats),
        epoch=_range(seat.epoch for seat in seats),
        behind_by=_range(seat.behind_by for seat in seats),
        listed_from=_range(
            seat.listed[0] for seat in seats if seat.epoch is None and seat.listed
        ),
        cells=_stacks(seats),
    )


def _range(numbers: Iterable[Optional[int]]) -> Optional[Range]:
    """From the lowest to the highest of the numbers that are known, when one is."""
    known = [number for number in numbers if number is not None]
    return Range(min(known), max(known)) if known else None


def _stacks(seats: List[Installation]) -> List[Stack]:
    """Some installations' lanes laid over one another: what they hold at each epoch, by epoch.

    An installation counts once under each state it wrote at an epoch, however often it wrote
    it there, and once under a leave.
    """
    kinds: Dict[int, Counter] = defaultdict(Counter)
    # For each epoch and state, the stamps of its records, and how many installations wrote it.
    stamps: Dict[int, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    holders: Dict[int, Counter] = defaultdict(Counter)
    for seat in seats:
        for cell in seat.cells:
            steps = [seat.history[place] for place in cell.steps]
            # A leave holds no state, and stands here as none.
            wrote = {step.state for step in steps}
            kinds[cell.epoch][cell.kind] += 1
            kinds[cell.epoch]["left"] += None in wrote
            kinds[cell.epoch]["several"] += len(wrote) > 1
            holders[cell.epoch].update(wrote - {None})
            for step in steps:
                if step.state is not None:
                    stamps[cell.epoch][step.state].append(step.at.seconds)
    return [
        Stack(
            epoch=epoch,
            wrote=kinds[epoch][WROTE],
            states=[
                Held(state, holders[epoch][state], min(at), max(at))
                for state, at in sorted(stamps[epoch].items())
            ],
            left=kinds[epoch]["left"],
            several=kinds[epoch]["several"],
            unreached=kinds[epoch][UNREACHED],
            listed=kinds[epoch][LISTED],
        )
        for epoch in sorted(kinds)
    ]


def _spans(seat: str, logs: _Logs) -> List[Span]:
    """Each run an installation wrote in as stretches of time, cut where its delivery changed.

    A run reaches from the first to the last of the installation's records and the run's
    `delivery` records, in the order of their lines. Nothing says it was off before the first
    `delivery` record, so it is read as online until then.
    """
    runs: Dict[str, List[Record]] = defaultdict(list)
    for record in logs.written.get(seat, ()):
        runs[record.run].append(record)
    spans: List[Span] = []
    for run, own in runs.items():
        changes = logs.delivery.get(run, [])
        lines = sorted([own[0], own[-1], *changes[:1], *changes[-1:]], key=attrgetter("line"))
        at = _seconds(lines[0].time)
        # A clock that went back ends no run before it began.
        end = max(at, _seconds(lines[-1].time))
        online, before = True, len(spans)
        for change in changes:
            if (change.state == "online") != online:
                moment = min(max(at, _seconds(change.time)), end)
                if moment > at:
                    spans.append(Span(at, moment, online))
                at, online = moment, not online
        # A run of one stamp is still a run; a change at its last stamp begins no stretch.
        if end > at or len(spans) == before:
            spans.append(Span(at, end, online))
    return spans


def _run(name: str, final: bool, logs: _Logs) -> Run:
    changes = logs.delivery.get(name, ())
    return Run(name, final, [Delivery(change.state, _mark(change)) for change in changes])


def _messages(
    convo: str, seats: List[str], lineage: _Lineage, logs: _Logs, folds: List[Fold]
) -> Tuple[List[Message], int]:
    """A row for each post, and how many `unreadable` records name a frame no `sent` does.

    Of two records of one kind that an installation wrote for a frame, the one it wrote first
    counts. A frame two installations say they sent is the post of the earlier stamp.
    """
    records = logs.convos[convo]
    # For each kind and frame, the first such record of each installation, by installation.
    first: Dict[str, Dict[str, Dict[str, Record]]] = {kind: defaultdict(dict) for kind in _SAYS}
    for installation in sorted({record.installation for record in records}):
        for record in logs.written[installation]:
            if record.convo == convo and record.kind in _SAYS:
                first[record.kind][record.frame].setdefault(installation, record)
    posts = {
        frame: min(senders.values(), key=lambda record: instant(record.time))
        for frame, senders in first["sent"].items()
    }
    rows = []
    for post in sorted(posts.values(), key=lambda record: (instant(record.time), record.frame)):
        sent_in = lineage.held.get((post.installation, post.epoch))
        roster = lineage.roster(sent_in) if sent_in else None
        # Each installation's record of the post: the sender's own, else one that read it, else
        # one that could not.
        wrote = {
            **first["unreadable"][post.frame],
            **first["received"][post.frame],
            post.installation: post,
        }
        cells = {seat: _cell(seat, wrote.get(seat), roster, logs) for seat in seats}
        why = {seat: wrote[seat].why for seat in seats if cells[seat] == "unreadable"}
        rows.append(
            Message(
                frame=post.frame,
                sender=post.installation,
                epoch=post.epoch,
                state=sent_in[1] if sent_in else None,
                at=_mark(post),
                utc=f"{instant(post.time)[0].isoformat(timespec='seconds')}Z",
                cells=cells,
                why=why,
                marks={seat: _mark(wrote[seat]) for seat in seats if seat in wrote},
                folds=[_fates(fold.installations, cells, why) for fold in folds],
            )
        )
    unmatched = sum(record.kind == "unreadable" and record.frame not in posts for record in records)
    return rows, unmatched


def _fates(seats: List[str], cells: Dict[str, str], why: Dict[str, str]) -> Fates:
    """What became of a post on some installations, as counts in an order that does not vary."""
    became = Counter(cells[seat] for seat in seats)
    causes = Counter(why[seat] for seat in seats if seat in why)
    return Fates(
        cells={fate: became[fate] for fate in _FATES if became[fate]},
        why={cause: causes[cause] for cause in sorted(causes)},
    )


def _cell(
    seat: str, wrote: Optional[Record], roster: Optional[FrozenSet[str]], logs: _Logs
) -> str:
    """What became of a post on one installation, which wrote this record of it or none."""
    if wrote is not None:
        return _SAYS[wrote.kind]
    if roster is not None and seat not in roster:
        return "not a member"
    if seat not in logs.written:
        return "no log"
    return "no record"
