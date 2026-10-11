"""Keeps the logs members share and reports on them: the collector that `forks serve` serves.

A run is one stream of log bytes under an id the collector made. Its directory holds the log as
received, byte for byte, and `run.json` for what no log line says: the name and the application
its sender gave, a hash of its receipt, whether it is final, and when the collector last heard
from it. The index, the records in the complete lines of every log, is in memory, and a start
reads it from the two again.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from itertools import islice
from operator import attrgetter
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from . import rule
from .records import Counts, Record, line_text, read_complete_lines
from .rule import Kept

# A run the collector has not heard from for this many seconds goes: thirty days.
UNHEARD = 30 * 24 * 60 * 60
# A run the disk did not let go of is tried again this many seconds later: an hour, which is
# little beside the thirty days, and a line of the log each time.
RETRY = 60 * 60
# The lines the page draws behind a mark: two before it and three after.
_BEFORE, _AFTER = 2, 3
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_HASH = re.compile(r"[0-9a-f]{64}")
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
# The front page lists the worst first: a split by how many of its sides are live, then these.
_SPLIT_TAILS = ("", ", one side live", ", no side live")
_UNSPLIT = (
    "whole as far as seen",
    "whole, members behind",
    "not enough data",
    "no live member",
    rule.WHOLE,
)
_NO_LOG = "the collector has no log of this run"


class Refused(Exception):
    """A call the collector does not carry out: the status it answers, and what the answer says."""

    def __init__(self, status: int, **said: object) -> None:
        super().__init__(status)
        self.status, self.said = status, said


@dataclass
class Run:
    """A run: what `run.json` says of it, and what its log was read as."""

    id: str
    name: str
    app: str
    # A hash of the receipt. The receipt itself is answered once and not kept.
    receipt: str
    final: bool
    heard: int
    # The lines kept of a final run whose log is gone; unset while the log is held.
    kept: Optional[List[Kept]] = None
    size: int = 0
    # How many of the log's bytes are complete lines, all of them read.
    read: int = 0
    counts: Counts = field(default_factory=Counts)
    records: List[Record] = field(default_factory=list)


@dataclass(frozen=True)
class _Label:
    """What the collector says of a run beside what a report says of it."""

    name: str
    app: str
    size: int
    heard: int
    counts: Counts


@dataclass(frozen=True)
class _Drawn:
    """A conversation's report as it is answered, and the tick of the index it was drawn at."""

    at: int
    # The report as JSON, up to its runs: they are answered with what the collector says of each.
    head: bytes
    runs: List[dict]
    # For each fold of the report, the runs its installations wrote in, each once.
    folds: List[List[str]]
    listed: dict


def mint() -> Tuple[str, str]:
    """A new run's id and its receipt."""
    return secrets.token_hex(16), secrets.token_urlsafe(32)


def to_stderr(line: str) -> None:
    """Writes a line of the collector's own log."""
    # One write for a line, so that two threads' lines do not run into each other.
    sys.stderr.write(line + "\n")


class Collector:
    """The runs under one directory, and the report of every conversation their logs name."""

    def __init__(
        self,
        directory: Path,
        max_bytes: Optional[int] = None,
        clock: Callable[[], float] = time.time,
        mint: Callable[[], Tuple[str, str]] = mint,
        log: Callable[[str], None] = to_stderr,
    ) -> None:
        self._directory = directory
        self._max_bytes = max_bytes
        self._clock, self._mint, self._log = clock, mint, log
        # Held for every read and change of what follows. A report is drawn outside it.
        self._lock = threading.Lock()
        # Held while a report is drawn: who asks meanwhile waits for that one, and draws none.
        self._drawing = threading.Lock()
        self._runs: Dict[str, Run] = {}
        # For each conversation, the runs whose logs name it, and the installations its report
        # may name: those that wrote in it, those its rosters list, those of its kept lines.
        self._holds: Dict[str, Set[str]] = {}
        self._seats: Dict[str, Set[str]] = {}
        self._kept: Dict[str, List[Kept]] = {}
        # For each installation, the runs that hold a record it wrote.
        self._wrote: Dict[str, Set[str]] = {}
        self._rosters: Dict[tuple, tuple] = {}
        # A tick is a write. A report holds while it was drawn at or after the last tick at
        # which a seat of its conversation wrote, and the last at which a log that named it went.
        self._tick = 0
        self._moved: Dict[str, int] = {}
        self._cut: Dict[str, int] = {}
        self._reports: Dict[str, _Drawn] = {}
        # For a run the disk did not let go of, the time before which it is not tried again.
        self._retry: Dict[str, int] = {}
        # Whether a directory that was left out at the start holds a log: no log is then known
        # to be a conversation's last.
        self._unread = False
        self._load()

    def say(self, text: str) -> None:
        """Writes a line of the collector's own log, stamped by its clock.

        A log that cannot be written stops nothing: a call is answered for what it did.
        """
        try:
            self._log(f"{_stamp(int(self._clock()))} {text}")
        except OSError:
            pass

    def index(self) -> tuple:
        """What is held in memory, as a value: a start on this directory holds an equal one."""
        with self._lock:
            return self._runs, self._holds, self._seats, self._kept, self._wrote

    def open_run(self, name: str, app: str) -> Tuple[str, str]:
        """Opens an empty run, and answers its id and the receipt its writes must carry."""
        with self._lock:
            now = self._sweep()
            run_id, receipt = self._mint()
            run = Run(run_id, name, app, _hash(receipt), final=False, heard=now)
            (self._directory / run_id).mkdir()
            self._log_of(run).touch()
            self._save(run)
            self._runs[run_id] = run
            self.say(f"run {run_id} opened")
            return run_id, receipt

    def append(
        self, run_id: str, receipt: Optional[str], offset: int, data: bytes, final: bool
    ) -> dict:
        """Appends bytes to a run's log at the size held, and marks the run final when asked."""
        with self._lock:
            now = self._sweep()
            run = self._runs.get(run_id)
            if run is None or run.kept is not None:
                raise Refused(404, error=_NO_LOG)
            self._check(run, receipt)
            if offset != run.size or (run.final and data):
                raise Refused(409, size=run.size, final=run.final)
            if data and self._max_bytes is not None:
                held = sum(other.size for other in self._runs.values())
                if held + len(data) > self._max_bytes:
                    # The run's sender is there: it is the collector that cannot take the bytes.
                    run.heard = now
                    self._save(run)
                    self.say(f"run {run_id} could not take {len(data)} bytes, holds {run.size}")
                    raise Refused(507, error="the collector holds as many bytes as it may")
            self._write(run, data)
            new = self._read_on(run) if b"\n" in data else []
            marked = final and not run.final
            run.final = run.final or final
            # A mark, like a run's delivery state, is said in the report of every conversation
            # an installation of the run is in.
            said_of_all = marked or any(record.kind == "delivery" for record in new)
            self._move(_writers(run.records if said_of_all else new))
            run.heard = now
            self._save(run)
            self.say(
                f"run {run_id} took {len(data)} bytes, holds {run.size}"
                + (", final" if run.final else "")
            )
            return {"size": run.size, "final": run.final}

    def delete(self, run_id: str, receipt: Optional[str]) -> None:
        """Removes a run: its log and records, or the lines kept of it."""
        with self._lock:
            self._sweep()
            run = self._runs.get(run_id)
            if run is None:
                raise Refused(404, error="the collector has nothing of this run")
            self._check(run, receipt)
            self._let_go(run, [])
            self.say(f"run {run_id} deleted")
            self._reindex()

    def expire(self) -> None:
        """Lets go of every run not heard from for thirty days."""
        with self._lock:
            self._sweep()

    def conversations(self) -> dict:
        """Every conversation a held log names, the worst first, and what became of the lines."""
        with self._lock:
            now = self._sweep()
            convos = sorted(self._holds)
            counts = sum((run.counts for run in self._runs.values()), Counts())
        listed = []
        for found in map(self._report, convos):
            if found is not None:
                drawn, labels = found
                heard = max(label.heard for label in labels.values())
                # A run heard from while the reports were drawn was heard from now.
                unheard = max(0, now - heard)
                listed.append(dict(drawn.listed, heard=_stamp(heard), unheard=unheard))
        # Of two that read alike, the one heard from last comes first. A stamp sorts as its time.
        listed.sort(key=lambda entry: entry["heard"], reverse=True)
        listed.sort(key=lambda entry: _place(entry["verdict"]))
        return {"lines": asdict(counts), "conversations": listed}

    def conversation(self, convo: str) -> bytes:
        """A conversation's report as JSON: what `--json` holds for it, and more of its runs.

        Each run is answered with what the collector says of it, and their lines are counted.
        `heard` says of each fold how many runs its installations wrote in, how many of them are
        final, and when the collector heard from the first and the last heard of them.
        """
        found = self._report(convo)
        if found is None:
            raise Refused(404, error="no log the collector holds names this conversation")
        drawn, labels = found
        counts = sum((label.counts for label in labels.values()), Counts())
        runs = [
            {
                "id": run["name"],
                "name": labels[run["name"]].name,
                "app": labels[run["name"]].app,
                "final": run["final"],
                "size": labels[run["name"]].size,
                "heard": _stamp(labels[run["name"]].heard),
                "delivery": run["delivery"],
            }
            for run in drawn.runs
        ]
        final = {run["name"] for run in drawn.runs if run["final"]}
        heard = [
            {
                "runs": len(fold),
                "final": len(final.intersection(fold)),
                "first": _stamp(min(labels[run].heard for run in fold)) if fold else None,
                "last": _stamp(max(labels[run].heard for run in fold)) if fold else None,
            }
            for fold in drawn.folds
        ]
        lines = json.dumps(asdict(counts))
        tail = b', "heard": ' + json.dumps(heard).encode() + b', "lines": ' + lines.encode()
        return drawn.head + json.dumps(runs).encode() + tail + b"}"

    def lines_around(self, run_id: str, line: int) -> List[dict]:
        """The lines of a run's log around one of them, each with its number."""
        with self._lock:
            self._sweep()
            run = self._runs.get(run_id)
            if run is None or run.kept is not None:
                raise Refused(404, error=_NO_LOG)
            log = self._log_of(run)
            # A log holds no more lines than bytes.
            last = min(line + _AFTER, run.size)
        first = max(1, line - _BEFORE)
        if first > last:
            return []
        try:
            with open(log, "rb") as file:
                around = islice(enumerate(file, 1), first - 1, last)
                return [
                    {"line": number, "text": line_text(raw[:-1])}
                    for number, raw in around
                    # What follows the last line break is no line.
                    if raw.endswith(b"\n")
                ]
        except FileNotFoundError:
            raise Refused(404, error=_NO_LOG) from None

    def _report(self, convo: str) -> Optional[Tuple[_Drawn, Dict[str, _Label]]]:
        """A conversation's report, and what the collector says of each run in it.

        The report is drawn when asked for, and again only after a write that may have changed
        it. It is drawn from a copy of the index, outside the lock, so that it holds up no write.
        """
        with self._lock:
            self._sweep()
            if convo not in self._holds:
                return None
            found = self._fresh(convo)
        if found is not None:
            return found
        with self._drawing:
            with self._lock:
                if convo not in self._holds:
                    return None
                found = self._fresh(convo)
                if found is not None:
                    return found
                runs = self._runs_of(convo)
                at = self._tick
                records = [record for run in runs for record in run.records]
                final = {run.id for run in runs if run.final}
                kept = list(self._kept.get(convo, ()))
                labels = {run.id: _label(run) for run in runs}
            drawn = _draw(convo, at, records, final, kept)
            with self._lock:
                if convo in self._holds:
                    self._reports[convo] = drawn
        # Of the runs it was drawn from, the ones the report lists: an installation the index
        # files under a conversation may be no seat of it.
        return drawn, {run["name"]: labels[run["name"]] for run in drawn.runs}

    def _fresh(self, convo: str) -> Optional[Tuple[_Drawn, Dict[str, _Label]]]:
        """The report drawn last, when no write since may have changed it."""
        drawn = self._reports.get(convo)
        moved = max(self._moved.get(seat, 0) for seat in self._seats[convo])
        if drawn is None or drawn.at < max(moved, self._cut.get(convo, 0)):
            return None
        return drawn, {run["name"]: _label(self._runs[run["name"]]) for run in drawn.runs}

    def _runs_of(self, convo: str) -> List[Run]:
        """The runs a conversation's report is drawn from: every one a seat of it wrote in."""
        wrote = (self._wrote.get(seat, ()) for seat in self._seats[convo])
        return [self._runs[run] for run in sorted(set().union(*wrote))]

    def _sweep(self) -> int:
        """Lets go of every run not heard from for thirty days, and answers the time it is."""
        now = int(self._clock())
        unheard = [
            run
            for run in self._runs.values()
            if run.kept is None and now - run.heard >= UNHEARD
        ]
        went = False
        for run in unheard:
            if now < self._retry.get(run.id, now):
                continue
            lines = _lines_of(run) if run.final else []
            try:
                self._let_go(run, lines)
            except OSError as error:
                # The run stays as it is, and the others go all the same.
                self._retry[run.id] = now + RETRY
                self.say(f"run {run.id} unheard for 30 days, left as it is: {error}")
                continue
            went = True
            kept = f"its log is gone, lines kept: {len(lines)}" if lines else "gone"
            self.say(f"run {run.id} unheard for 30 days, {kept}")
        if went:
            self._reindex()
        return now

    def _check(self, run: Run, receipt: Optional[str]) -> None:
        if receipt is None or not hmac.compare_digest(_hash(receipt), run.receipt):
            raise Refused(403, error="a write to a run needs that run's receipt")

    def _write(self, run: Run, data: bytes) -> None:
        log = self._log_of(run)
        try:
            with open(log, "ab") as file:
                file.write(data)
        except OSError:
            # Nothing stays of bytes that could not all be written.
            os.truncate(log, run.size)
            raise
        run.size += len(data)

    def _read_on(self, run: Run) -> List[Record]:
        """Reads the complete lines a run's log holds past those already read."""
        with open(self._log_of(run), "rb") as file:
            file.seek(run.read)
            reading = read_complete_lines(file.read(), run.id, run.counts.lines + 1)
        run.read += reading.consumed
        run.counts += reading.counts
        new = [self._shared(record) for record in reading.records]
        run.records += new
        self._file(run, new)
        return new

    def _shared(self, record: Record) -> Record:
        """A record that holds the index's one copy of its roster."""
        if not record.roster:
            return record
        roster = self._rosters.setdefault(record.roster, record.roster)
        return record if roster is record.roster else replace(record, roster=roster)

    def _file(self, run: Run, records: Iterable[Record]) -> None:
        """Files records under the conversations they name and the installations that wrote them."""
        listed: Tuple[Optional[str], tuple] = (None, ())
        for record in records:
            if record.installation is None:
                continue
            self._wrote.setdefault(record.installation, set()).add(run.id)
            if record.convo is not None:
                self._holds.setdefault(record.convo, set()).add(run.id)
                seats = self._seats.setdefault(record.convo, set())
                seats.add(record.installation)
                # Most records list the members the one before listed, in the one shared roster.
                if record.convo != listed[0] or record.roster is not listed[1]:
                    seats.update(member for member, _ in record.roster)
                    listed = record.convo, record.roster

    def _move(self, installations: Iterable[str]) -> None:
        """Counts a write, and notes that these installations wrote in it."""
        self._tick += 1
        for installation in installations:
            self._moved[installation] = self._tick

    def _let_go(self, run: Run, lines: List[Kept]) -> None:
        """Removes a run's log and records, and the run itself unless lines are kept of it.

        What the disk refuses is raised before anything is forgotten: the run is as it was.
        """
        if lines:
            # The lines before the log goes: a stop between the two loses neither.
            self._save(replace(run, kept=lines))
            self._log_of(run).unlink(missing_ok=True)
        else:
            # The log first: a stop then leaves nothing a start would hold.
            self._log_of(run).unlink(missing_ok=True)
            shutil.rmtree(self._directory / run.id)
        self._move(_writers(run.records))
        named = {record.convo for record in run.records} | {line.convo for line in run.kept or ()}
        for convo in named:
            self._cut[convo] = self._tick
        if lines:
            run.kept, run.size, run.read, run.counts, run.records = lines, 0, 0, Counts(), []
        else:
            del self._runs[run.id]

    def _reindex(self) -> None:
        """Files every held record again, without what went."""
        self._holds, self._seats, self._wrote = {}, {}, {}
        shared = {}
        for run in self._runs.values():
            self._file(run, run.records)
            for record in run.records:
                shared[id(record.roster)] = record.roster
        self._rosters = {roster: roster for roster in shared.values() if roster}
        self._keep()
        self._reports = {
            convo: drawn for convo, drawn in self._reports.items() if convo in self._holds
        }

    def _keep(self) -> None:
        """Files the kept lines under their conversations.

        A conversation exists while a log names it, so its lines go with its last log.
        """
        self._kept = {}
        expired = [run for run in self._runs.values() if run.kept is not None]
        # In an order a start finds again, whatever order the runs were opened in.
        for run in sorted(expired, key=attrgetter("id")):
            lines = [line for line in run.kept if line.convo in self._holds]
            if lines != run.kept and not self._unread:
                self._drop_lines(run, lines)
            for line in lines:
                self._kept.setdefault(line.convo, []).append(line)
                self._seats[line.convo].add(line.installation)

    def _drop_lines(self, run: Run, lines: List[Kept]) -> None:
        """Keeps these of an expired run's lines, and none of the rest."""
        why = f"run {run.id}: a conversation's last log is gone"
        try:
            self._let_go(run, lines)
        except OSError as error:
            # The lines stay on the disk, and are tried again when the next run goes.
            self.say(f"{why}, left as it is: {error}")
        else:
            self.say(f"{why}, lines kept: {len(lines)}")

    def _load(self) -> None:
        """Reads every run under the directory: a log is read whole, whatever `run.json` says."""
        for directory in sorted(path for path in self._directory.iterdir() if path.is_dir()):
            run = _run_in(directory)
            if run is None:
                self._leave(directory)
            elif self._log_of(run).is_file():
                # Lines beside a log are a letting go that was cut short: the sweep repeats it.
                run.kept = None
                run.size = self._log_of(run).stat().st_size
                self._runs[run.id] = run
                self._read_on(run)
            elif run.kept:
                self._runs[run.id] = run
            elif not self._finish_removal(directory):
                self._leave(directory)
        self._keep()

    def _finish_removal(self, directory: Path) -> bool:
        """Removes what a removal cut short after the log went left: `run.json` and no more.

        A directory that holds anything else is not the collector's to remove.
        """
        try:
            if {path.name for path in directory.iterdir()} <= {"run.json", "run.new"}:
                shutil.rmtree(directory)
                self.say(f"run {directory.name}: a removal was cut short, gone")
                return True
        except OSError:
            pass
        return False

    def _leave(self, directory: Path) -> None:
        """Leaves a directory that holds no run as it is, and notes a log in it nobody read."""
        self.say(f"{directory.name} is not a run, left as it is")
        try:
            logs = [path for path in directory.iterdir() if path.suffix == ".log"]
        except OSError:
            # What cannot be listed may hold one.
            logs = [directory]
        self._unread = self._unread or bool(logs)

    def _save(self, run: Run) -> None:
        """Replaces a run's `run.json` in one step: a stop leaves the old one or the new."""
        said = {
            "name": run.name,
            "app": run.app,
            "receipt": run.receipt,
            "final": run.final,
            "heard": _stamp(run.heard),
        }
        if run.kept is not None:
            said["kept"] = [asdict(line) for line in run.kept]
        path = self._directory / run.id / "run.json"
        scratch = path.with_suffix(".new")
        scratch.write_text(json.dumps(said, indent=2) + "\n")
        os.replace(scratch, path)

    def _log_of(self, run: Run) -> Path:
        return self._directory / run.id / f"{run.id}.log"


def _run_in(directory: Path) -> Optional[Run]:
    """The run a directory holds, as its `run.json` says it; none when that is not one."""
    try:
        said = json.loads((directory / "run.json").read_text())
        heard = datetime.strptime(said["heard"], _STAMP).replace(tzinfo=timezone.utc)
        run = Run(
            directory.name,
            said["name"],
            said["app"],
            said["receipt"],
            said["final"],
            int(heard.timestamp()),
            [Kept(**line) for line in said.get("kept", ())],
        )
    except (OSError, ValueError, LookupError, TypeError, RecursionError):
        return None
    texts = all(isinstance(value, str) for value in (run.name, run.app, run.receipt))
    if not texts or not isinstance(run.final, bool) or not all(map(_is_a_line, run.kept)):
        return None
    return run if _ID.fullmatch(run.id) and _HASH.fullmatch(run.receipt) else None


def _is_a_line(line: Kept) -> bool:
    """Whether a kept line holds what the collector keeps in one: texts, a number, a yes or no."""
    texts = isinstance(line.convo, str) and isinstance(line.installation, str)
    account = line.account is None or isinstance(line.account, str)
    return texts and account and type(line.epoch) is int and type(line.left) is bool


def _draw(
    convo: str, at: int, records: List[Record], final: Set[str], kept: List[Kept]
) -> _Drawn:
    """Draws a conversation's report with the code that writes `--json`, ready to answer."""
    (conversation,) = rule.report(records, final, kept, only=[convo])
    tree = rule.as_json(conversation)
    runs = tree.pop("runs")
    wrote = {seat["id"]: seat["runs"] for seat in tree["installations"]}
    folds = [
        list(dict.fromkeys(run for seat in fold["installations"] for run in wrote[seat]))
        for fold in tree["folds"]
    ]
    listed = {key: tree[key] for key in ("id", "verdict", "split_epoch", "coverage")}
    listed["branches"] = [
        {"label": branch["label"], "epoch": branch["epoch"]} for branch in tree["branches"]
    ]
    head = json.dumps(tree)[:-1] + ', "runs": '
    return _Drawn(at, head.encode(), runs, folds, listed)


def _lines_of(run: Run) -> List[Kept]:
    """What is kept of a final run: where each installation ended in each conversation in it."""
    return [
        Kept(conversation.id, seat.id, seat.account, seat.epoch, seat.history[-1].kind == "left")
        for conversation in rule.report(run.records)
        for seat in conversation.installations
        if seat.history
    ]


def _writers(records: Iterable[Record]) -> Set[str]:
    return {record.installation for record in records if record.installation is not None}


def _label(run: Run) -> _Label:
    return _Label(run.name, run.app, run.size, run.heard, run.counts)


def _place(verdict: str) -> int:
    """A verdict's place on the front page, the worst first."""
    if verdict.startswith("split"):
        return max(place for place, tail in enumerate(_SPLIT_TAILS) if verdict.endswith(tail))
    return len(_SPLIT_TAILS) + _UNSPLIT.index(verdict)


def _hash(receipt: str) -> str:
    return hashlib.sha256(receipt.encode()).hexdigest()


def _stamp(seconds: int) -> str:
    """A time of the collector's clock as it is written, in UTC."""
    return datetime.fromtimestamp(seconds, timezone.utc).strftime(_STAMP)
