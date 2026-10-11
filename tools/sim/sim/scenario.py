"""A scenario: the file that says what a run does, and what its group is expected to come to."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Union

from .peers import index_of, name_of

# chat-cli's two sets of group timers, by the name `--group-commit` takes.
TIMER_SETS = ("fast", "default")

# The durations of the group's settings, by the names the library gives them.
DURATIONS = (
    "voting_delay",
    "consensus_timeout",
    "commit_batch_window",
    "freeze_duration",
    "retry_window",
    "backup_takeover_window",
    "proposal_expiration",
    "recovery_auto_commit_delay",
)

# The most each number may be. Every node listens on a TCP port of loopback, and the registry
# on one more. The others are the most chat-cli's flags hold.
MOST_MEMBERS = 65534
MOST_KIB = 2**32 - 1
MOST_MILLISECONDS = 2**64 - 1

# Who posts, where a step names no member: the last one seated, or every member in turn.
LAST = "last"
ALL = "all"

# The words of a fork report read from logs: where an installation stands, what became of a
# post on an installation that did not send it, and the verdict.
POSITIONS = ("tip", "behind", "lineage unknown", "removed", "never joined", "no data")
CELLS = ("read", "unreadable", "not a member", "no log", "no record")
_VERDICT = re.compile(
    r"whole(, members behind| as far as seen)?|not enough data|no live member"
    r"|split (at|seen from) epoch [0-9]+(, (no|one) side live)?"
)


class Refused(Exception):
    """Why a file is no scenario a run can do."""


@dataclass(frozen=True)
class Seat:
    """The creator adds members until the group holds `to`, `batch` of them with one `/add`."""

    to: int
    batch: int = 10


@dataclass(frozen=True)
class Post:
    """A member posts: the one named, the last one seated, or every member in turn."""

    sender: str = LAST


@dataclass(frozen=True)
class Expect:
    """How the outcome differs from a whole group at the tip in which every post was read.

    `positions` gives a member's position by its name. `posts` gives, by a member's name, what
    becomes of each post another member sends while it is seated.
    """

    verdict: str = "whole"
    positions: Dict[str, str] = field(default_factory=dict)
    posts: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    """What a run is told to do.

    `timers` names a set, or gives durations in milliseconds over the library's own. `budget`
    is the seconds each wait may take: a peer's start, the wiring, a batch, a post.
    """

    members: int
    steps: Tuple[Union[Seat, Post], ...]
    timers: Union[str, Dict[str, int]] = "fast"
    max_message_kib: int = 512
    budget: float = 180.0
    expect: Expect = Expect()

    @property
    def seated(self) -> int:
        """How many members the steps seat, the creator among them."""
        return max([step.to for step in self.steps if isinstance(step, Seat)], default=1)

    def posts(self) -> List[Tuple[int, int]]:
        """Each post of the steps in the order sent: who sends it, and how many are seated then."""
        seated, posts = 1, []
        for step in self.steps:
            if isinstance(step, Seat):
                seated = step.to
            else:
                posts += [(sender, seated) for sender in senders(step, seated)]
        return posts

    def expectation(self, conversation: str, installations: List[str]) -> dict:
        """What `forks check` is to hold the report of `conversation` against.

        `installations` are the ids the report knows the members by, in the order of the
        members. Each member the steps seat is expected at the tip, and each post read by
        every member seated when it was sent, but where `expect` says otherwise.
        """
        seats = range(self.seated)
        return {
            "conversation": conversation,
            "verdict": self.expect.verdict,
            "installations": {
                installations[seat]: {
                    "name": name_of(seat),
                    "position": self.expect.positions.get(name_of(seat), "tip"),
                }
                for seat in seats
            },
            "posts": [
                {
                    "sender": installations[sender],
                    "cells": {
                        installations[seat]: "sent"
                        if seat == sender
                        else self.expect.posts.get(name_of(seat), "read")
                        if seat < seated
                        else "not a member"
                        for seat in seats
                    },
                }
                for sender, seated in self.posts()
            ],
        }


def senders(step: Post, seated: int) -> List[int]:
    """The members that post at a step, in the order they do, with `seated` members seated."""
    if step.sender == ALL:
        return list(range(seated))
    if step.sender == LAST:
        return [seated - 1]
    return [index_of(step.sender)]


def read(path: Path) -> Scenario:
    """The scenario a file holds. Every field is checked, and a refusal names what is wrong."""
    try:
        said = json.loads(
            path.read_bytes(), object_pairs_hook=_said_once, parse_constant=_no_number
        )
    except OSError as error:
        raise Refused(str(error)) from None
    except (ValueError, RecursionError):
        raise Refused(f"{path} is not JSON") from None
    except Refused as why:
        raise Refused(f"{path}: {why}") from None
    try:
        return scenario(said)
    except Refused as why:
        raise Refused(f"{path}: {why}") from None


def _said_once(pairs: List[Tuple[str, object]]) -> Dict[str, object]:
    """An object of JSON none of whose fields is said twice: a reader keeps the last alone."""
    said: Dict[str, object] = {}
    for name, held in pairs:
        if name in said:
            raise Refused(f"`{name}` is said twice")
        said[name] = held
    return said


def _no_number(word: str) -> float:
    # Python reads `NaN` and `Infinity` as numbers, and JSON has neither.
    raise ValueError(word)


def scenario(said: object) -> Scenario:
    """The scenario an object of JSON holds."""
    whole = _fields(
        said,
        "the scenario",
        ("members", "steps"),
        ("members", "timers", "max_message_kib", "budget", "steps", "expect"),
    )
    members = _whole(whole, "members", "the scenario", least=2, most=MOST_MEMBERS)
    if not isinstance(whole["steps"], list) or not whole["steps"]:
        raise Refused("`steps` is not a list of one step or more")
    steps: List[Union[Seat, Post]] = []
    seated = 1
    for at, step in enumerate(whole["steps"]):
        steps.append(_step(step, f"steps[{at}]", seated, members))
        if isinstance(steps[-1], Seat):
            seated = steps[-1].to
    return Scenario(
        members=members,
        steps=tuple(steps),
        timers=_timers(whole.get("timers", Scenario.timers)),
        max_message_kib=_whole(
            whole,
            "max_message_kib",
            "the scenario",
            least=1,
            most=MOST_KIB,
            unsaid=Scenario.max_message_kib,
        ),
        budget=_budget(whole.get("budget", Scenario.budget)),
        expect=_expect(whole.get("expect", {}), seated),
    )


def _fields(said: object, what: str, needed: Tuple[str, ...], known: Tuple[str, ...]) -> dict:
    """`said` as the object `what` is: every field it needs, and no field that is not known."""
    if not isinstance(said, dict):
        raise Refused(f"{what} is not an object")
    for name in needed:
        if name not in said:
            raise Refused(f"{what} lacks `{name}`")
    for name in said:
        if name not in known:
            raise Refused(f"`{name}` is not a field of {what}")
    return said


def _whole(said: dict, name: str, what: str, least: int, most: int, unsaid: int = 0) -> int:
    """A field that is a whole number from `least` to `most`, or `unsaid` when it is left out."""
    number = said.get(name, unsaid)
    # A truth value is a number to Python and none to a reader.
    if type(number) is not int:
        raise Refused(f"`{name}` of {what} is not a whole number")
    if number < least:
        raise Refused(f"`{name}` of {what} is {number}, and {least} is the least")
    if number > most:
        raise Refused(f"`{name}` of {what} is {number}, and {most} is the most")
    return number


def _step(said: object, what: str, seated: int, members: int) -> Union[Seat, Post]:
    if not isinstance(said, dict) or said.get("step") not in ("seat", "post"):
        raise Refused(f"{what} is not an object whose `step` is `seat` or `post`")
    if said["step"] == "seat":
        step = _fields(said, what, ("step", "to"), ("step", "to", "batch"))
        to = _whole(step, "to", what, least=2, most=MOST_MEMBERS)
        if to > members:
            raise Refused(f"{what} seats {to} members, and the scenario has {members}")
        if to <= seated:
            raise Refused(f"{what} seats {to} members, and {seated} are seated by then")
        return Seat(to, _whole(step, "batch", what, 1, MOST_MEMBERS, unsaid=Seat.batch))
    step = _fields(said, what, ("step",), ("step", "from"))
    sender = step.get("from", Post.sender)
    if not isinstance(sender, str):
        raise Refused(f"`from` of {what} is not a name, `{LAST}` or `{ALL}`")
    if sender not in (LAST, ALL):
        _member(sender, f"`from` of {what}", seated, "is not seated by then")
    return Post(sender)


def _member(name: str, what: str, seated: int, unseated: str) -> None:
    """Refuses a name that is no member's, or that of a member not among the first `seated`."""
    index = index_of(name) if isinstance(name, str) else None
    if index is None:
        raise Refused(f"{what} names {name}, and no member has that name")
    if index >= seated:
        raise Refused(f"{what} names {name}, who {unseated}")


def _timers(said: object) -> Union[str, Dict[str, int]]:
    if isinstance(said, str):
        if said not in TIMER_SETS:
            raise Refused(f"`timers` names the set {said}, and the sets are fast and default")
        return said
    if not isinstance(said, dict):
        raise Refused("`timers` is not the name of a set or an object of durations")
    for name in said:
        if name not in DURATIONS:
            raise Refused(f"`{name}` in `timers` is not a duration of the group's settings")
    return {name: _whole(said, name, "`timers`", 0, MOST_MILLISECONDS) for name in said}


def _budget(said: object) -> float:
    if type(said) not in (int, float) or not said > 0:
        raise Refused("`budget` is not a number of seconds above 0")
    try:
        seconds = float(said)
    except OverflowError:
        seconds = math.inf
    if not math.isfinite(seconds):
        raise Refused("`budget` is more seconds than a wait can be told to last")
    return seconds


def _expect(said: object, seated: int) -> Expect:
    expect = _fields(said, "`expect`", (), ("verdict", "positions", "posts"))
    verdict = expect.get("verdict", Expect.verdict)
    if not isinstance(verdict, str):
        raise Refused("`verdict` of `expect` is not a text")
    if not _VERDICT.fullmatch(verdict):
        raise Refused(f"`verdict` of `expect` is `{verdict}`, which no fork report gives")
    held = {}
    for name, words in (("positions", POSITIONS), ("posts", CELLS)):
        held[name] = expect.get(name, {})
        if not isinstance(held[name], dict):
            raise Refused(f"`{name}` of `expect` is not an object")
        for member, word in held[name].items():
            _member(member, f"`{name}` of `expect`", seated, "is seated by no step")
            if word not in words:
                raise Refused(
                    f"`{name}` of `expect` gives {member} `{word}`, "
                    f"which is not among: {', '.join(words)}"
                )
    return Expect(verdict, dict(held["positions"]), dict(held["posts"]))
