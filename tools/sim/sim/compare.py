"""Holds the runs of a sweep in one process to the outcomes expected of them.

A run in one process is the same run every time from its seeds, so a run that comes out other
than expected was changed by the revision its runner was built from.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

from . import table

_SPLIT = re.compile(r"split at epoch (\d+)")
# How the runner says a `grow` step ended at the epoch it grew toward, which a run records
# nowhere else: each other ending is short of it.
_REACHED = re.compile(r"the trunk reached epoch \d+")
_LINE = "{:>3}  {:>4}  {:<9}  {:<22}  {:>10}  {:>7}  {}"
# A file of outcomes with the keys of a sweep's row alone is one to write again.
_ROW = {"seed", "network_seed", "verdict", "at_tip", "seated", "virtual_s", "came_to", "stopped"}
_KEYS = _ROW | {
    "conversation",
    "members_digest",
    "trunk_epoch",
    "first_fork",
    "ended",
    "lines_off_trunk",
    "members_off_trunk",
    "never_joined",
}
# The parts of where a run of a `grow` step stands, in the order they rank it: the epochs
# without a fork, whether it ended whole, then the lines and the members off the trunk.
STANDING = ("epochs", "ended", "lines_off_trunk", "members_off_trunk")


class Unreadable(Exception):
    """A sweep, a run or a file of expected outcomes that cannot be read, and why."""


def outcomes(out: Path) -> List[dict]:
    """The outcome of each run of the sweep in one process written under `out`, in row order."""
    found = []
    for row in _read(out / "sweep.json")["rows"]:
        report = _read(out / row["directory"] / "run.json")
        if report.get("rig") != table.ONE_PROCESS:
            raise Unreadable(f"{out}: run {row['run']} was not in one process, whose runs repeat")
        found.append(outcome(report))
    return found


def outcome(report: dict) -> dict:
    """What a run in one process came to: all its row in a sweep says but its wall time, the ids
    it drew, and how far a `grow` step got.

    The ids are the group's, `conversation`, and as `members_digest` a digest of the members' in
    member order, both `None` for a run that read no result of its runner. Of a `grow` step it
    holds what its lineage says: `trunk_epoch`; `first_fork`, the lowest epoch a line forked at,
    `None` where none did; how it `ended`: `split` where a line forked, `stalled` where the trunk
    stopped short of the epoch the step grew toward, `behind` where members were behind at the
    end, else `whole`; `lines_off_trunk`, the lines whose kind is not `trunk`;
    `members_off_trunk`, the members behind at the end, on a line not the trunk, or never
    joined, each once; and `never_joined`, how many of those never joined. Those six are `None`
    for a run without a `grow` step, and `verdict` and `at_tip`, which `forks` gives, are `None`
    for a run with one.
    """
    grows = report["grow"] is not None
    return {
        "seed": report["seed"],
        "network_seed": None if report["network"] is None else report["network"]["seed"],
        "conversation": report["conversation"],
        "members_digest": _digest([node["installation"] for node in report["nodes"]]),
        "verdict": None if grows else report["verdict"],
        "at_tip": None if grows else report["at_tip"],
        "seated": report["seated"],
        "virtual_s": report["virtual_s"],
        **_grown(report),
        "came_to": table.came_to(report),
        "stopped": report["stopped"],
    }


def expected(file: Path) -> List[dict]:
    """The outcomes `file` holds, one a run."""
    held = _read(file)
    if isinstance(held, list) and held and all(
        isinstance(one, dict) and one.keys() == _ROW for one in held
    ):
        raise Unreadable(
            f"{file}: outcomes without the ids the runs drew or how far a grow got: "
            "write them again with `compare --write`"
        )
    if not isinstance(held, list) or any(
        not isinstance(one, dict) or one.keys() != _KEYS for one in held
    ):
        raise Unreadable(f"{file}: not a list of the outcomes `compare --write` writes")
    return held


def held_runs(directory: Path) -> List[Tuple[str, int]]:
    """Each scenario `directory` holds a file of expected outcomes of, by the file's name without
    `.json`, and how many runs the file holds."""
    names = sorted(path.stem for path in directory.glob("*.json"))
    return [(name, len(expected(directory / f"{name}.json"))) for name in names]


def change(was: dict, now: dict) -> str:
    """`same`; `re-rolled` when both runs drew ids and the group's or the members' differ;
    `better` or `worse` by where the run stands; or `changed` when it stands where it did.

    The ids follow from the seed, and a run that drew others says nothing of the group against
    the one expected. A run that read no result drew none, and stands lowest.
    """
    if now == was:
        return "same"
    ids = [(was[key], now[key]) for key in ("conversation", "members_digest")]
    if all(None not in pair for pair in ids) and any(held != drawn for held, drawn in ids):
        return "re-rolled"
    if _standing(now) > _standing(was):
        return "better"
    if _standing(now) < _standing(was):
        return "worse"
    return "changed"


def unforked(outcome: dict) -> Optional[int]:
    """How many epochs a run of a `grow` step went without a fork: the epoch of its first fork,
    else the trunk's; `None` for a run without a `grow` step."""
    if outcome["ended"] is None:
        return None
    return outcome["trunk_epoch"] if outcome["first_fork"] is None else outcome["first_fork"]


def apart(was: dict, now: dict) -> Optional[str]:
    """The first part of `STANDING` in which two runs of a `grow` step stand apart; `None` where
    they stand alike, or either is not such a run."""
    if any(one["ended"] is None for one in (was, now)):
        return None
    for part, held, drawn in zip(STANDING, _standing(was)[1:], _standing(now)[1:]):
        if held != drawn:
            return part
    return None


def render(was: List[dict], now: List[dict]) -> Tuple[str, bool]:
    """A row for each run, under it the outcome expected where it differs, then a count.

    Also whether every run came out as expected, and the sweep has a run for each expected.
    """
    lines = [_LINE.format("run", "seed", "change", "verdict", "at the tip", "seconds", "came to")]
    changes = []
    for number, (expected_one, got) in enumerate(zip(was, now), start=1):
        changes.append(change(expected_one, got))
        lines.append(_row(str(number), str(got["seed"]), changes[-1], got))
        if changes[-1] != "same":
            lines.append(_row("", "", "was", expected_one))
    held = changes.count("same")
    counted = f"{held} of {len(changes)} runs came out as expected"
    moved = [
        f"{changes.count(kind)} {kind}" for kind in ("better", "worse", "re-rolled", "changed")
    ]
    moved = [said for said in moved if not said.startswith("0 ")]
    if moved:
        counted += f": {', '.join(moved)}"
    if len(was) != len(now):
        counted += f"; {len(was)} runs were expected, and the sweep has {len(now)}"
    lines.append(counted)
    return "".join(f"{line}\n" for line in lines), held == len(was) == len(now)


def _row(number: str, seed: str, said: str, outcome: dict) -> str:
    at_tip = "-" if outcome["at_tip"] is None else f"{outcome['at_tip']} of {outcome['seated']}"
    seconds = "-" if outcome["virtual_s"] is None else f"{outcome['virtual_s']:.1f}"
    came_to = outcome["came_to"]
    if outcome["stopped"] is not None:
        came_to += f"; stopped: {outcome['stopped']}"
    return _LINE.format(number, seed, said, _verdict(outcome), at_tip, seconds, came_to)


def _verdict(outcome: dict) -> str:
    """A run's verdict, or how a run of a `grow` step ended, in the words of a verdict."""
    ended = outcome["ended"]
    if outcome["verdict"] is not None or ended is None:
        return outcome["verdict"] or "-"
    if ended == "split":
        return f"split at epoch {outcome['first_fork']}"
    if ended == "stalled":
        return f"stalled at epoch {outcome['trunk_epoch']}"
    return "whole" if ended == "whole" else "whole, members behind"


def _digest(installations: List[str]) -> Optional[str]:
    """The first 16 hex digits of the SHA-256 of `installations`, each followed by a newline,
    in UTF-8; `None` for no member."""
    if not installations:
        return None
    lines = "".join(f"{installation}\n" for installation in installations)
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()[:16]


def _grown(report: dict) -> dict:
    """The six keys of an outcome that say how far a `grow` step got, `None` without one."""
    grow = report["grow"]
    if grow is None:
        return dict.fromkeys(
            (
                "trunk_epoch",
                "first_fork",
                "ended",
                "lines_off_trunk",
                "members_off_trunk",
                "never_joined",
            )
        )
    forked = [line["forked_at"] for line in grow["lines"] if line["forked_at"] is not None]
    first_fork = min(forked, default=None)
    off = [line for line in grow["lines"] if line["kind"] != "trunk"]
    never = set(grow["never_joined"])
    members = {*grow["behind"], *never}
    members.update(member for line in off for member in line["members"])
    return {
        "trunk_epoch": grow["trunk_epoch"],
        "first_fork": first_fork,
        "ended": _ended(grow, first_fork),
        "lines_off_trunk": len(off),
        "members_off_trunk": len(members),
        "never_joined": len(never),
    }


def _ended(grow: dict, first_fork: Optional[int]) -> str:
    if first_fork is not None:
        return "split"
    if not _REACHED.fullmatch(grow["ended"]):
        return "stalled"
    return "behind" if grow["behind"] else "whole"


def _standing(outcome: dict) -> Tuple[int, ...]:
    """Where a run stands, the higher the better, a run with neither a verdict nor the ending of
    a `grow` step lowest.

    A run of a `grow` step: the epochs without a fork, the first fork's or else the trunk's; at
    the same, whole at the end over any other ending; then the fewer lines off the trunk, and the
    fewer members off it. Any other: a split, the later the better, then any other verdict, then
    whole; and at each, the more members at the tip, the better.
    """
    verdict = outcome["verdict"]
    split = None if verdict is None else _SPLIT.match(verdict)
    if outcome["ended"] is not None:
        return (
            1,
            unforked(outcome),
            outcome["ended"] == "whole",
            -outcome["lines_off_trunk"],
            -outcome["members_off_trunk"],
        )
    if verdict is None:
        rank = (0, 0)
    elif split is not None:
        rank = (1, int(split[1]))
    elif verdict == "whole":
        rank = (3, 0)
    else:
        rank = (2, 0)
    return (*rank, outcome["at_tip"] or 0)


def _read(file: Path) -> object:
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except OSError as error:
        raise Unreadable(f"{file}: {error.strerror}") from None
    except ValueError as error:
        raise Unreadable(f"{file}: not JSON: {error}") from None
