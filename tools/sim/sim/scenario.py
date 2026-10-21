"""A scenario: the file that says what a run does, and what its group is expected to come to."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

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

# The most each number may be. Every node listens on a TCP port of its own, and the registry
# on one more. The others are the most chat-cli's flags hold.
MOST_MEMBERS = 65534
MOST_KIB = 2**32 - 1
MOST_MILLISECONDS = 2**64 - 1
MOST_SEED = 2**64 - 1

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
class Hold:
    """The run stands still for `seconds`, and every node is asked its state before and after."""

    seconds: float


@dataclass(frozen=True)
class Cut:
    """The members of each side are cut off from the frames of the other side's members."""

    sides: Tuple[Tuple[str, ...], Tuple[str, ...]]


@dataclass(frozen=True)
class Heal:
    """The cut in force ends."""


@dataclass(frozen=True)
class Withhold:
    """The first commit candidate of the next batch to reach a member is withheld from it."""

    member: str


@dataclass(frozen=True)
class Grow:
    """Members add new ones in rounds, until the trunk holds `epochs` or `branches` main branches
    run side by side, the trunk among them: one to `adders` a round on any branch, or with `by`
    that member alone, one a round, the line it holds the trunk. One of the two is `None`. With
    `size`, an adder whose add would take the group above it first removes the member seated
    longest ago."""

    epochs: int
    adders: Optional[int]
    branches: int
    by: Optional[str] = None
    size: Optional[int] = None


Step = Union[Seat, Post, Hold, Cut, Heal, Withhold, Grow]

# The steps by the name a file gives them.
STEPS = ("seat", "post", "hold", "cut", "heal", "withhold", "grow")


@dataclass(frozen=True)
class Network:
    """The model of a lossy network that what every peer receives goes through.

    `loss` is the share of the frames a peer loses, and `welcome_loss` that of the Welcomes.
    `delay_ms` is how long each frame is held: one duration for every member, or one by the
    name of a member, where a member it does not name has none.
    """

    seed: int
    loss: Union[int, float] = 0
    welcome_loss: Union[int, float] = 0
    delay_ms: Union[int, Dict[str, int]] = 0

    def delay_of(self, name: str) -> int:
        """How many milliseconds the frames of the member `name` are held."""
        if isinstance(self.delay_ms, dict):
            return self.delay_ms.get(name, 0)
        return self.delay_ms


@dataclass(frozen=True)
class Wire:
    """The conditions on the wire of a network of containers, on what each member's node sends.

    Each member's interface delays each packet it sends by `delay_ms`, give or take `jitter_ms`,
    and loses `loss` of them, drawn from `seed` and the member's name. TCP sends a lost packet
    again, so a frame arrives late and not lost while its link holds. `delay_ms` is one duration
    for every member, or one by the name of a member, where a member it does not name has none.
    """

    seed: int
    delay_ms: Union[int, Dict[str, int]] = 0
    jitter_ms: int = 0
    loss: Union[int, float] = 0

    def delay_of(self, name: str) -> int:
        """How many milliseconds the packets the member `name` sends are delayed."""
        if isinstance(self.delay_ms, dict):
            return self.delay_ms.get(name, 0)
        return self.delay_ms


@dataclass(frozen=True)
class Expect:
    """How the outcome differs from a whole group at the tip in which every post was read.

    `positions` gives a member's position by its name. `posts` gives, by a member's name, what
    becomes of each post another member sends while it is seated. `applied` is the outcome when
    the group applied the candidate a `withhold` step withheld, and the rest is then the
    outcome when it did not.
    """

    verdict: str = "whole"
    positions: Dict[str, str] = field(default_factory=dict)
    posts: Dict[str, str] = field(default_factory=dict)
    applied: Optional[Expect] = None


@dataclass(frozen=True)
class Scenario:
    """What a run is told to do.

    `timers` names a set, or gives durations in milliseconds over the library's own. `budget`
    is the seconds each wait may take: a peer's start, the wiring, a batch, a post. `preset`
    names the fleet preset every node joins, in place of a network of the run's own with
    `max_message_kib` for its largest message. `seed` is what a run in one process draws every
    random number from; a peer that is a process draws from its OS.
    """

    members: int
    steps: Tuple[Step, ...]
    timers: Union[str, Dict[str, int]] = "fast"
    max_message_kib: int = 512
    preset: Optional[str] = None
    budget: float = 180.0
    seed: int = 0
    network: Optional[Network] = None
    wire: Optional[Wire] = None
    expect: Expect = Expect()

    @property
    def seated(self) -> int:
        """How many members the steps seat, the creator among them."""
        return max([step.to for step in self.steps if isinstance(step, Seat)], default=1)

    @property
    def grows(self) -> bool:
        """Whether the scenario is a `grow` step, which seats as many as its run comes to."""
        return isinstance(self.steps[0], Grow)

    @property
    def withheld_from(self) -> Optional[str]:
        """The member a step withholds a candidate from, when one does."""
        return next((step.member for step in self.steps if isinstance(step, Withhold)), None)

    def of_run(self, number: int) -> Scenario:
        """The scenario as run `number` of a sweep does it, from 1: each has the next seeds."""
        network, wire = self.network, self.wire
        if network is not None:
            network = replace(network, seed=network.seed + number - 1)
        if wire is not None:
            wire = replace(wire, seed=wire.seed + number - 1)
        return replace(self, seed=self.seed + number - 1, network=network, wire=wire)

    def posts(self) -> List[Tuple[int, int]]:
        """Each post of the steps in the order sent: who sends it, and how many are seated then."""
        seated, posts = 1, []
        for step in self.steps:
            if isinstance(step, Seat):
                seated = step.to
            elif isinstance(step, Post):
                posts += [(sender, seated) for sender in senders(step, seated)]
        return posts

    def expectation(
        self, conversation: str, installations: List[str], applied: bool = False
    ) -> dict:
        """What `forks check` is to hold the report of `conversation` against.

        `installations` are the ids the report knows the members by, in the order of the
        members. Each member the steps seat is expected at the tip, and each post read by
        every member seated when it was sent, but where `expect` says otherwise. `applied`
        says that the group applied the candidate a step withheld: what is expected is then
        what `applied` of `expect` says.
        """
        expect = (self.expect.applied or Expect()) if applied else self.expect
        seats = range(self.seated)
        return {
            "conversation": conversation,
            "verdict": expect.verdict,
            "installations": {
                installations[seat]: {
                    "name": name_of(seat),
                    "position": expect.positions.get(name_of(seat), "tip"),
                }
                for seat in seats
            },
            "posts": [
                {
                    "sender": installations[sender],
                    "cells": {
                        installations[seat]: "sent"
                        if seat == sender
                        else expect.posts.get(name_of(seat), "read")
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
        (
            "members",
            "timers",
            "max_message_kib",
            "preset",
            "budget",
            "seed",
            "network",
            "wire",
            "steps",
            "expect",
        ),
    )
    members = _whole(whole, "members", "the scenario", least=1, most=MOST_MEMBERS)
    if not isinstance(whole["steps"], list) or not whole["steps"]:
        raise Refused("`steps` is not a list of one step or more")
    network = _network(whole["network"], members) if "network" in whole else None
    wire = _wire(whole["wire"], members) if "wire" in whole else None
    steps: List[Step] = []
    seated = 1
    for at, step in enumerate(whole["steps"]):
        steps.append(_step(step, f"steps[{at}]", seated, members, network is not None))
        if isinstance(steps[-1], Seat):
            seated = steps[-1].to
    grown = [at for at, step in enumerate(steps) if isinstance(step, Grow)]
    if grown and len(steps) > 1:
        raise Refused(f"steps[{grown[0]}] is a `grow` step, and a `grow` step is the only step")
    if grown and "expect" in whole:
        raise Refused("`expect` is said with a `grow` step, which expects no outcome")
    expect = _expect(whole.get("expect", {}), seated)
    _cuts(steps)
    _two_outcomes(steps, network, expect)
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
        preset=_preset(whole),
        budget=_seconds(whole.get("budget", Scenario.budget), "`budget`"),
        seed=_whole(whole, "seed", "the scenario", least=0, most=MOST_SEED),
        network=network,
        wire=wire,
        expect=expect,
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


def _step(said: object, what: str, seated: int, members: int, modelled: bool) -> Step:
    if not isinstance(said, dict) or said.get("step") not in STEPS:
        raise Refused(
            f"{what} is not an object whose `step` is "
            "`seat`, `post`, `hold`, `cut`, `heal`, `withhold` or `grow`"
        )
    if said["step"] not in ("seat", "post", "hold", "grow") and not modelled:
        raise Refused(f"{what} is a `{said['step']}` step, and the scenario has no `network`")
    if said["step"] == "seat":
        step = _fields(said, what, ("step", "to"), ("step", "to", "batch"))
        to = _whole(step, "to", what, least=2, most=MOST_MEMBERS)
        if to > members:
            raise Refused(f"{what} seats {to} members, and the scenario has {members}")
        if to <= seated:
            raise Refused(f"{what} seats {to} members, and {seated} are seated by then")
        return Seat(to, _whole(step, "batch", what, 1, MOST_MEMBERS, unsaid=Seat.batch))
    if said["step"] == "hold":
        step = _fields(said, what, ("step", "seconds"), ("step", "seconds"))
        return Hold(_seconds(step["seconds"], f"`seconds` of {what}"))
    if said["step"] == "cut":
        step = _fields(said, what, ("step", "between"), ("step", "between"))
        return Cut(_sides(step["between"], what, seated))
    if said["step"] == "heal":
        _fields(said, what, ("step",), ("step",))
        return Heal()
    if said["step"] == "withhold":
        member = _fields(said, what, ("step", "from"), ("step", "from"))["from"]
        if not isinstance(member, str):
            raise Refused(f"`from` of {what} is not a name")
        _member(member, f"`from` of {what}", seated, "is not seated by then")
        return Withhold(member)
    if said["step"] == "grow":
        named = ("step", "epochs", "adders", "by", "branches", "size")
        step = _fields(said, what, ("step", "epochs", "branches"), named)
        if members < 2:
            raise Refused(f"{what} is a `grow` step, and the scenario has 1 member")
        epochs = _whole(step, "epochs", what, least=1, most=MOST_MEMBERS)
        # The trunk alone is one main branch.
        branches = _whole(step, "branches", what, least=2, most=MOST_MEMBERS)
        # An adder removes a member other than itself.
        size = None if "size" not in step else _whole(step, "size", what, 2, MOST_MEMBERS)
        if "adders" in step and "by" in step:
            raise Refused(f"{what} says `adders` and `by`, and a `grow` step says one or the other")
        if "by" not in step:
            if "adders" not in step:
                raise Refused(f"{what} lacks `adders` or `by`")
            adders = _whole(step, "adders", what, least=1, most=MOST_MEMBERS)
            if size is not None and size < 2 * adders:
                raise Refused(
                    f"`size` of {what} is below twice `adders`, and each adder of a group at its "
                    "size removes a member that adds none"
                )
            return Grow(epochs, adders, branches, size=size)
        if not isinstance(step["by"], str):
            raise Refused(f"`by` of {what} is not a name")
        _member(step["by"], f"`by` of {what}", seated, "is not seated when a `grow` step begins")
        return Grow(epochs, None, branches, by=step["by"], size=size)
    step = _fields(said, what, ("step",), ("step", "from"))
    sender = step.get("from", Post.sender)
    if not isinstance(sender, str):
        raise Refused(f"`from` of {what} is not a name, `{LAST}` or `{ALL}`")
    if sender not in (LAST, ALL):
        _member(sender, f"`from` of {what}", seated, "is not seated by then")
    return Post(sender)


def _sides(said: object, what: str, seated: int) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """The two sides of a cut: each one member or more seated by then, and none named twice."""
    if (
        not isinstance(said, list)
        or len(said) != 2
        or not all(isinstance(side, list) and side for side in said)
        or not all(isinstance(name, str) for side in said for name in side)
    ):
        raise Refused(f"`between` of {what} is not two lists of one name or more")
    named = [name for side in said for name in side]
    for name in named:
        _member(name, f"`between` of {what}", seated, "is not seated by then")
        if named.count(name) > 1:
            raise Refused(f"`between` of {what} names {name} twice")
    return tuple(said[0]), tuple(said[1])


def _cuts(steps: Sequence[Step]) -> None:
    """Refuses a cut where one is in force, and a heal where none is."""
    cut = False
    for at, step in enumerate(steps):
        if isinstance(step, Cut) and cut:
            raise Refused(f"steps[{at}] cuts the group, and a cut is in force by then")
        if isinstance(step, Heal) and not cut:
            raise Refused(f"steps[{at}] heals a cut, and no cut is in force by then")
        if isinstance(step, (Cut, Heal)):
            cut = isinstance(step, Cut)


def _two_outcomes(steps: Sequence[Step], network: Optional[Network], expect: Expect) -> None:
    """Refuses a `withhold` step where a run could come to more than its two outcomes.

    The two are that the group applied the candidate withheld, and that it did not: the next
    batch is the one the candidate is of, and nothing after it adds a way for the group to part.
    """
    withholds = [at for at, step in enumerate(steps) if isinstance(step, Withhold)]
    if not withholds:
        if expect.applied is not None:
            raise Refused("`expect` holds `applied`, and no step is a `withhold` step")
        return
    at, member = withholds[0], steps[withholds[0]].member
    what = f"steps[{at}] is a `withhold` step"
    if len(withholds) > 1:
        raise Refused(f"steps[{withholds[1]}] is a second `withhold` step, and a run has one")
    batch = steps[at + 1] if at + 1 < len(steps) else None
    if not isinstance(batch, Seat):
        raise Refused(f"{what}, and the step after it is not a `seat` step")
    seated = max([step.to for step in steps[:at] if isinstance(step, Seat)], default=1)
    if batch.to - seated > batch.batch:
        raise Refused(f"{what}, and steps[{at + 1}] seats more than one batch")
    for later, step in enumerate(steps[at + 2 :], at + 2):
        if isinstance(step, Seat):
            raise Refused(f"{what}, and steps[{later}] is a second `seat` step after it")
        if isinstance(step, Post) and index_of(member) in senders(step, batch.to):
            raise Refused(f"{what}, and steps[{later}] has {member} post, whom it withholds from")
    if any(isinstance(step, Cut) for step in steps):
        raise Refused(f"{what}, and the scenario has a `cut` step")
    for rate in ("loss", "welcome_loss"):
        if getattr(network, rate) > 0:
            raise Refused(f"{what}, and `{rate}` of `network` is above 0")
    position = expect.positions.get(member, "tip")
    if position != "tip":
        raise Refused(
            f"{what}, and `positions` of `expect` gives {member} `{position}`, "
            "whom it withholds from and the run then waits on"
        )


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


def _preset(said: dict) -> Optional[str]:
    """The fleet preset a scenario names, or `None` when it names none.

    Which names there are is the delivery library's to say.
    """
    if "preset" not in said:
        return None
    if not isinstance(said["preset"], str) or not said["preset"]:
        raise Refused("`preset` is not the name of a preset")
    # The name is a word of every peer's command line and stands in the first line a run prints.
    if not said["preset"].isprintable():
        raise Refused("`preset` holds a character that cannot be printed")
    if "max_message_kib" in said:
        raise Refused(
            "`max_message_kib` is said with `preset`, and a preset fixes the largest message"
        )
    return said["preset"]


def _network(said: object, members: int) -> Network:
    network = _fields(said, "`network`", ("seed",), ("seed", "loss", "welcome_loss", "delay_ms"))
    return Network(
        seed=_whole(network, "seed", "`network`", least=0, most=MOST_SEED),
        loss=_rate(network, "loss", "`network`"),
        welcome_loss=_rate(network, "welcome_loss", "`network`"),
        delay_ms=_delay(network, "`network`", members),
    )


def _wire(said: object, members: int) -> Wire:
    wire = _fields(said, "`wire`", ("seed",), ("seed", "delay_ms", "jitter_ms", "loss"))
    return Wire(
        seed=_whole(wire, "seed", "`wire`", least=0, most=MOST_SEED),
        delay_ms=_delay(wire, "`wire`", members),
        jitter_ms=_whole(wire, "jitter_ms", "`wire`", 0, MOST_MILLISECONDS),
        loss=_rate(wire, "loss", "`wire`"),
    )


def _delay(said: dict, what: str, members: int) -> Union[int, Dict[str, int]]:
    """`delay_ms` of `what`: one duration, or an object of them by the names of members."""
    delay = said.get("delay_ms", 0)
    if not isinstance(delay, dict):
        return _whole(said, "delay_ms", what, 0, MOST_MILLISECONDS)
    for name in delay:
        _member(name, f"`delay_ms` of {what}", members, "is not among the members")
    return {
        name: _whole(delay, name, f"`delay_ms` of {what}", 0, MOST_MILLISECONDS) for name in delay
    }


def _rate(said: dict, name: str, what: str) -> Union[int, float]:
    """A field of `what` that is a number from 0 to 1, or 0 when it is left out."""
    rate = said.get(name, 0)
    # A truth value is a number to Python and none to a reader.
    if type(rate) not in (int, float) or not 0 <= rate <= 1:
        raise Refused(f"`{name}` of {what} is not a number from 0 to 1")
    return rate


def _seconds(said: object, what: str) -> float:
    """`said` as the seconds `what` lasts: a budget, or a hold."""
    if type(said) not in (int, float) or not said > 0:
        raise Refused(f"{what} is not a number of seconds above 0")
    try:
        seconds = float(said)
    except OverflowError:
        seconds = math.inf
    if not math.isfinite(seconds):
        raise Refused(f"{what} is more seconds than a wait can be told to last")
    return seconds


def _expect(said: object, seated: int, what: str = "`expect`") -> Expect:
    known = ("verdict", "positions", "posts")
    expect = _fields(said, what, (), known + ("applied",) if what == "`expect`" else known)
    verdict = expect.get("verdict", Expect.verdict)
    if not isinstance(verdict, str):
        raise Refused(f"`verdict` of {what} is not a text")
    if not _VERDICT.fullmatch(verdict):
        raise Refused(f"`verdict` of {what} is `{verdict}`, which no fork report gives")
    held = {}
    for name, words in (("positions", POSITIONS), ("posts", CELLS)):
        held[name] = expect.get(name, {})
        if not isinstance(held[name], dict):
            raise Refused(f"`{name}` of {what} is not an object")
        for member, word in held[name].items():
            _member(member, f"`{name}` of {what}", seated, "is seated by no step")
            if word not in words:
                raise Refused(
                    f"`{name}` of {what} gives {member} `{word}`, "
                    f"which is not among: {', '.join(words)}"
                )
    applied = None
    if "applied" in expect:
        applied = _expect(expect["applied"], seated, "`applied` of `expect`")
    return Expect(verdict, dict(held["positions"]), dict(held["posts"]), applied)
