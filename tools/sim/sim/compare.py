"""Holds the runs of a sweep in one process to the outcomes expected of them.

A run in one process is the same run every time from its seeds, so a run that comes out other
than expected was changed by the revision its runner was built from.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Tuple

from . import table

_SPLIT = re.compile(r"split at epoch (\d+)")
_LINE = "{:>3}  {:>4}  {:<7}  {:<22}  {:>10}  {:>7}  {}"
_KEYS = {"seed", "network_seed", "verdict", "at_tip", "seated", "virtual_s", "came_to", "stopped"}


class Unreadable(Exception):
    """A sweep or a file of expected outcomes that cannot be read, and why."""


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
    """What a run in one process came to: all its row in a sweep says, but its wall time."""
    return {
        "seed": report["seed"],
        "network_seed": None if report["network"] is None else report["network"]["seed"],
        "verdict": report["verdict"],
        "at_tip": report["at_tip"],
        "seated": report["seated"],
        "virtual_s": report["virtual_s"],
        "came_to": table.came_to(report),
        "stopped": report["stopped"],
    }


def expected(file: Path) -> List[dict]:
    """The outcomes `file` holds, one a run."""
    held = _read(file)
    if not isinstance(held, list) or any(
        not isinstance(one, dict) or one.keys() != _KEYS for one in held
    ):
        raise Unreadable(f"{file}: not a list of the outcomes `compare --write` writes")
    return held


def change(was: dict, now: dict) -> str:
    """`same`, `better` or `worse` by how whole the group came out, or `changed` when neither."""
    if now == was:
        return "same"
    if _standing(now) > _standing(was):
        return "better"
    if _standing(now) < _standing(was):
        return "worse"
    return "changed"


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
    moved = [f"{changes.count(kind)} {kind}" for kind in ("better", "worse", "changed")]
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
    return _LINE.format(number, seed, said, outcome["verdict"] or "-", at_tip, seconds, came_to)


def _standing(outcome: dict) -> Tuple[int, int, int]:
    """No verdict, then a split, the later the better, then any other verdict, then whole; and
    at each, the more members at the tip, the better."""
    verdict = outcome["verdict"]
    split = None if verdict is None else _SPLIT.match(verdict)
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
