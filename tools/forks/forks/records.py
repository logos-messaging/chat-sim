"""Reads log lines into trace records.

A line is a stamp, a level, optional span prefixes, a target, a colon and the message. A record is
a line whose target is a report target; its message is the record's name and then `key=value`
words. The target is read at its place in that structure and never searched for: a conversation
id is text its creator chose, other lines print it as it is, and it may hold a record's whole text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

_ANSI = re.compile(rb"\x1b\[[0-9;]*m")
_STAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]+))?(?P<offset>Z|[+-][0-9]{2}:[0-9]{2})?"
)
# tracing pads its level to five characters and writes no colon after it; chat-module writes one.
_LEVEL = re.compile(r" +[A-Z]+(?P<colon>:?) (?P<rest>.*)")
_SPAN_NAME = re.compile(r"[^ :{}]+")
_TARGET = re.compile(r"(?P<target>[^ ]+?): (?P<message>.*)")


@dataclass(frozen=True)
class Record:
    """One trace record and where it was written; a field its kind does not carry is unset."""

    run: str
    line: int
    time: str
    has_offset: bool
    kind: str
    installation: Optional[str] = None
    account: Optional[str] = None
    convo: Optional[str] = None
    epoch: Optional[int] = None
    state: Optional[str] = None
    members: Optional[int] = None
    roster: Tuple[Tuple[str, str], ...] = ()
    frame: Optional[str] = None
    sender: Optional[str] = None
    why: Optional[str] = None


@dataclass
class Counts:
    """How many lines were read, and how many of them were what."""

    lines: int = 0
    records: int = 0
    unstamped: int = 0
    malformed: int = 0
    unknown: int = 0

    def __add__(self, other: Counts) -> Counts:
        return Counts(
            self.lines + other.lines,
            self.records + other.records,
            self.unstamped + other.unstamped,
            self.malformed + other.malformed,
            self.unknown + other.unknown,
        )


@dataclass(frozen=True)
class Reading:
    """The records in some log bytes and a count of their lines."""

    records: List[Record]
    counts: Counts


class _UnknownRecord(Exception):
    pass


def _shaped(pattern: str) -> Callable[[str], str]:
    shape = re.compile(pattern)

    def parse(value: str) -> str:
        if not shape.fullmatch(value):
            raise ValueError(value)
        return value

    return parse


def _number(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise ValueError(value)
    return int(value)


def _roster(value: str) -> Tuple[Tuple[str, str], ...]:
    return tuple(_member(pair) for pair in value.split(","))


def _member(pair: str) -> Tuple[str, str]:
    installation, colon, account = pair.partition(":")
    if not colon:
        raise ValueError(pair)
    return _installation(installation), _account(account)


_installation = _shaped(r"[0-9a-f]{8}")
# An installation is the first four bytes of a key. An account is the first four bytes of what a
# member's credential holds, which may be fewer, or none.
_account = _shaped(r"(?:[0-9a-f]{2}){0,4}")
_convo = _shaped(r"(?:[0-9A-Za-z_-]|%[0-9a-f]{2})+")
_fingerprint = _shaped(r"[0-9a-f]{16}")
_frame = _shaped(r"[0-9a-f]+")
_placed = {"installation": _installation, "convo": _convo, "epoch": _number}

# Each target's records, and for each the fields it must carry.
_RECORDS: Dict[str, Dict[str, Dict[str, Callable]]] = {
    "libchat::groupv2trace": {
        "run": {"installation": _installation, "account": _account},
        "epoch": {**_placed, "state": _fingerprint, "members": _number, "roster": _roster},
        "left": _placed,
        "sent": {**_placed, "frame": _frame},
        "received": {**_placed, "frame": _frame, "from": _installation},
        "unreadable": {**_placed, "frame": _frame, "why": _shaped("epoch|decrypt|sender")},
    },
    "chat_module::report": {
        "delivery": {"state": _shaped("initialising|online|error|stopped")},
    },
}
# `from` is a Python keyword.
_ATTRIBUTE = {"from": "sender"}


def instant(time: str) -> Tuple[datetime, str]:
    """A stamp as a value that sorts by when it was written; without an offset it is read as UTC."""
    stamp = _STAMP.fullmatch(time)
    if not stamp:
        raise ValueError(time)
    *date, fraction, offset = stamp.groups()
    written = datetime(*map(int, date))
    if offset and offset != "Z":
        hours, minutes = int(offset[1:3]), int(offset[4:6])
        ahead = timedelta(hours=hours, minutes=minutes)
        written -= ahead if offset[0] == "+" else -ahead
    return written, fraction or ""


def read_lines(data: bytes, run: str) -> Reading:
    """Reads the lines of a run's log that end in a line break.

    A last line without one is still being written, or the copy was cut there: it is not read.
    """
    lines = data.split(b"\n")
    # What follows the last line break is no line.
    lines.pop()
    counts, records = Counts(lines=len(lines)), []
    for number, raw in enumerate(lines, 1):
        text = _ANSI.sub(b"", raw).decode("utf-8", "replace").rstrip("\r")
        stamp = _stamp(text)
        if stamp is None:
            counts.unstamped += 1
            continue
        found = _target_and_message(text[stamp.end() :])
        if found is None or found[0] not in _RECORDS:
            continue
        try:
            kind, fields = _fields(_RECORDS[found[0]], found[1])
        except _UnknownRecord:
            counts.unknown += 1
        except ValueError:
            counts.malformed += 1
        else:
            counts.records += 1
            has_offset = stamp["offset"] is not None
            records.append(Record(run, number, stamp.group(), has_offset, kind, **fields))
    return Reading(records, counts)


def _stamp(text: str) -> Optional[re.Match]:
    stamp = _STAMP.match(text)
    if not stamp or text[stamp.end() : stamp.end() + 1] not in ("", " "):
        return None
    try:
        instant(stamp.group())
    except (ValueError, OverflowError):
        return None
    return stamp


def _target_and_message(after_stamp: str) -> Optional[Tuple[str, str]]:
    level = _LEVEL.fullmatch(after_stamp)
    if not level:
        return None
    # Only tracing's own formatter writes spans, and chat-module's line has none.
    rest = level["rest"] if level["colon"] else _after_spans(level["rest"])
    found = _TARGET.fullmatch(rest)
    return (found["target"], found["message"]) if found else None


def _after_spans(rest: str) -> str:
    """What follows a line's span prefixes, each `name{fields}:` or `name:`, and their one space.

    A span's fields end at the first `}:`. No later one is tried: a later one may be text a peer
    chose, and trying each makes a line cost more than its length.
    """
    at = 0
    while True:
        name = _SPAN_NAME.match(rest, at)
        if not name:
            return rest
        end = name.end()
        if rest.startswith("{", end):
            close = rest.find("}:", end)
            if close < 0:
                return rest
            end = close + 2
        elif rest.startswith(":", end):
            end += 1
        else:
            return rest
        if rest.startswith(" ", end):
            return rest[end + 1 :]
        at = end


def _fields(kinds: Dict[str, Dict[str, Callable]], message: str) -> Tuple[str, dict]:
    kind, *words = message.split(" ")
    if kind not in kinds:
        raise _UnknownRecord(kind)
    written: Dict[str, str] = {}
    for word in words:
        key, equals, value = word.partition("=")
        if not equals or key in written:
            raise ValueError(word)
        written[key] = value
    fields = {}
    for key, parse in kinds[kind].items():
        if key not in written:
            raise ValueError(key)
        fields[_ATTRIBUTE.get(key, key)] = parse(written[key])
    if "roster" in fields and fields["members"] != len(fields["roster"]):
        # libchat counts the roster it writes, so another number is a cut or altered line.
        raise ValueError(written["members"])
    return kind, fields
