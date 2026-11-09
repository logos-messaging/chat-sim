"""The matrix of a revision's runs beside its base's: a cell for each scenario and seed, the
change of the cell, and the verdict over the cells.

A side is what the jobs at one revision uploaded, read at any depth: each run's
`<scenario>/<run>/run.json`, and `<scenario>/<run>/unbuilt` for each run of a job that could not
build chat-sim. A cell is a scenario and a seed either side has a run of, or that is asked of
each side. A cell has no outcome at a side whose job could not build it, that has no run of it,
or whose run has no verdict, as when its runner left no result. Its change is what `compare` says
of the base's outcome and the head's, where both have one.

The verdict is the first of these that holds, so that what went wrong at the head is said before
what is missing at the base, and that before what moved:

- `unbuilt`: a job at the head could not build chat-sim, and no run at the head was read.
- `no-outcome`: a cell has no outcome at the head.
- `no-base`: no cell has a change.
- `mixed`: cells are better and cells are worse.
- `better`: cells are better, and none worse.
- `worse`: two or more cells are worse, and none better.
- `hint`: one cell is worse, and none better.
- `re-rolled`: cells are re-rolled, and none better or worse.
- `same`: every cell with a change is the same or changed.

A cell that is `changed`, which differs from the base's but stands where it did with the same
ids, counts as the same, since the verdict says where the runs stand. A re-rolled cell is neither
better nor worse, so the cells better or worse beside it decide the verdict, and it is
`re-rolled` only when no other cell moved. With no base, a base that did not build, or a base
with no outcome for any cell, no cell has a change and the head's cells stand alone: `no-base`.
A cell the base has no outcome for has no change, and the cells that have one decide.
"""

from __future__ import annotations

import functools
import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from . import compare, scenario, table

# How a run of a `grow` step can end, as `compare` says it.
ENDINGS = ("whole", "behind", "split", "stalled")
# The files of the scenarios a cell can be of, each its name and `.json`.
SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
_CHANGES = ("same", "changed", "better", "worse", "re-rolled")
_RUN = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class Run:
    """A run of a side: its number in its sweep, the loss of its network model, and its outcome,
    `None` for a run whose job could not build chat-sim."""

    number: int
    loss: Optional[float]
    outcome: Optional[dict]


@dataclass(frozen=True)
class Side:
    """What the jobs at one revision uploaded: each run by its scenario and seed."""

    runs: Dict[Tuple[str, int], Run]


def read(directory: Path) -> Side:
    """The side whose runs and `unbuilt` markers are under `directory`, at any depth."""
    runs: Dict[Tuple[str, int], Run] = {}
    markers = (marker for marker in directory.rglob("unbuilt") if marker.is_file())
    for file in sorted([*directory.rglob("run.json"), *markers]):
        number, name = file.parent.name, file.parent.parent.name
        if _RUN.fullmatch(number) is None:
            raise compare.Unreadable(f"{file}: not in the directory of a run, <scenario>/<run>")
        if file.name == "unbuilt":
            seed, loss = _drawn(name, int(number))
            key, run = (name, seed), Run(int(number), loss, None)
        else:
            report = _report(file)
            if report.get("rig") != table.ONE_PROCESS:
                raise compare.Unreadable(f"{file}: not a run in one process, whose runs repeat")
            outcome = compare.outcome(report)
            loss = None if report["network"] is None else report["network"]["loss"]
            key, run = (name, outcome["seed"]), Run(int(number), loss, outcome)
        if key in runs:
            raise compare.Unreadable(f"{file}: a second run of {name} at seed {key[1]}")
        runs[key] = run
    return Side(runs)


def asked(file: Path) -> List[Tuple[str, int]]:
    """The runs that the JSON list of sweeps in `file` asks of each side, each its scenario and
    its number in a sweep. A sweep holds its `scenario`, the name of a file of `SCENARIOS`
    without `.json`, the run it is `from` and how many `runs`, and any other field besides."""
    sweeps = _json(file)
    if not isinstance(sweeps, list) or not all(_sweep(one) for one in sweeps):
        raise compare.Unreadable(f"{file}: not a list of sweeps, each its scenario, from and runs")
    return [
        (one["scenario"], number)
        for one in sweeps
        for number in range(one["from"], one["from"] + one["runs"])
    ]


def matrix(head: Side, base: Optional[Side] = None, asked: Sequence[Tuple[str, int]] = ()) -> dict:
    """The matrix of `head` beside `base`: of each side how many runs were read and how many
    could not be built, the cells, the count of each change, the verdict, and with no base each
    scenario's summary. `asked` holds the runs asked of each side, each its scenario and its
    number in a sweep, so that a cell no side has a run of is there all the same.

    A cell holds its `scenario`, `seed`, the `run` of a sweep it is, the `loss` of its network
    model and that loss in percent as its `rate`; the `head`'s outcome and the `base`'s, each
    `None` where the side has no outcome; whether each side's job could not build it, as
    `unbuilt`; the `change`, `None` where there is none; for each side the `epochs` without a
    fork and how the run `ended`; and where it is better or worse, the `part` of the standing in
    which it `moved` and each side's value of it, else `moved` is `None`. The cells are in the
    order of their loss, then scenario, then seed.
    """
    sides = [head] + ([] if base is None else [base])
    where = {key: (run.number, run.loss) for side in sides for key, run in side.runs.items()}
    for name, number in asked:
        seed, loss = _drawn(name, number)
        where.setdefault((name, seed), (number, loss))
    cells = sorted((_cell(key, *where[key], head, base) for key in where), key=_order)
    counts = {
        "cells": len(cells),
        "compared": sum(cell["change"] is not None for cell in cells),
        **{change: sum(cell["change"] == change for cell in cells) for change in _CHANGES},
        "no-outcome": sum(cell["head"] is None for cell in cells),
    }
    return {
        "head": _side(head),
        "base": None if base is None else _side(base),
        "cells": cells,
        "counts": counts,
        "verdict": _verdict(_side(head), counts),
        "summary": _summary(cells) if base is None else None,
    }


def _side(side: Side) -> dict:
    unbuilt = sum(run.outcome is None for run in side.runs.values())
    return {"runs": len(side.runs) - unbuilt, "unbuilt": unbuilt}


def _cell(
    key: Tuple[str, int], number: int, loss: Optional[float], head: Side, base: Optional[Side]
) -> dict:
    now = head.runs.get(key)
    was = None if base is None else base.runs.get(key)
    head_outcome, base_outcome = _outcome(now), _outcome(was)
    change = None
    if None not in (head_outcome, base_outcome):
        change = compare.change(base_outcome, head_outcome)
    part = None
    if change in ("better", "worse"):
        part = compare.apart(base_outcome, head_outcome)
    return {
        "scenario": key[0],
        "seed": key[1],
        "run": number,
        "loss": loss,
        "rate": _rate(loss),
        "head": head_outcome,
        "base": base_outcome,
        "unbuilt": {"head": _unbuilt(now), "base": _unbuilt(was)},
        "change": change,
        "epochs": {
            "head": _unforked(head_outcome),
            "base": _unforked(base_outcome),
        },
        "ended": {
            "head": None if head_outcome is None else head_outcome["ended"],
            "base": None if base_outcome is None else base_outcome["ended"],
        },
        "moved": None
        if part is None
        else {
            "part": part,
            "head": _value(head_outcome, part),
            "base": _value(base_outcome, part),
        },
    }


def _outcome(run: Optional[Run]) -> Optional[dict]:
    """A run's outcome, or `None` where there is no run, it was not built or it has no verdict."""
    if run is None or run.outcome is None or run.outcome["verdict"] is None:
        return None
    return run.outcome


def _unbuilt(run: Optional[Run]) -> bool:
    return run is not None and run.outcome is None


def _unforked(outcome: Optional[dict]) -> Optional[int]:
    return None if outcome is None else compare.unforked(outcome)


def _value(outcome: dict, part: str) -> Union[int, str]:
    """A run's value of a part of `compare.STANDING`: the epochs without a fork, else its field."""
    return compare.unforked(outcome) if part == "epochs" else outcome[part]


def _rate(loss: Optional[float]) -> Optional[str]:
    return None if loss is None else f"{loss * 100:g}%"


def _order(cell: dict) -> tuple:
    return (cell["loss"] is None, cell["loss"] or 0, cell["scenario"], cell["seed"])


def _verdict(head: dict, counts: dict) -> str:
    better, worse = counts["better"], counts["worse"]
    if head["unbuilt"] and not head["runs"]:
        return "unbuilt"
    if counts["no-outcome"] or not counts["cells"]:
        return "no-outcome"
    if not counts["compared"]:
        return "no-base"
    if better and worse:
        return "mixed"
    if better:
        return "better"
    if worse:
        return "worse" if worse > 1 else "hint"
    if counts["re-rolled"]:
        return "re-rolled"
    return "same"


def _summary(cells: List[dict]) -> List[dict]:
    """For each scenario in the order of its cells: how many runs, how each ended, the lowest,
    median and highest epochs without a fork, and the median of the members off the trunk at the
    end, as `compare` counts them: behind, on a line not the trunk, or never joined."""
    scenarios: Dict[str, List[dict]] = {}
    for cell in cells:
        scenarios.setdefault(cell["scenario"], []).append(cell)
    summary = []
    for name, its in scenarios.items():
        outcomes = [cell["head"] for cell in its if cell["head"] is not None]
        epochs = [cell["epochs"]["head"] for cell in its if cell["epochs"]["head"] is not None]
        off = [one["members_off_trunk"] for one in outcomes if one["members_off_trunk"] is not None]
        summary.append(
            {
                "scenario": name,
                "loss": its[0]["loss"],
                "rate": its[0]["rate"],
                "runs": len(its),
                "no-outcome": len(its) - len(outcomes),
                **{ended: sum(one["ended"] == ended for one in outcomes) for ended in ENDINGS},
                "epochs": {
                    "lowest": min(epochs, default=None),
                    "median": _median(epochs),
                    "highest": max(epochs, default=None),
                },
                "members_off_trunk": {"median": _median(off)},
            }
        )
    return summary


def _median(values: List[int]) -> Optional[Union[int, float]]:
    if not values:
        return None
    middle = statistics.median(values)
    # The median of an even count is a float even when it is whole: 6.0 is written 6.
    return int(middle) if middle == int(middle) else middle


@functools.lru_cache(maxsize=None)
def _scenario(name: str) -> scenario.Scenario:
    try:
        return scenario.read(SCENARIOS / f"{name}.json")
    except scenario.Refused as refused:
        raise compare.Unreadable(f"the scenario {name}: {refused}") from None


def _drawn(name: str, number: int) -> Tuple[int, Optional[float]]:
    """The seed of run `number` of a sweep of the scenario `name`, and its network model's loss."""
    drawn = _scenario(name).of_run(number)
    return drawn.seed, None if drawn.network is None else drawn.network.loss


def _sweep(said: object) -> bool:
    return (
        isinstance(said, dict)
        and isinstance(said.get("scenario"), str)
        and all(type(said.get(key)) is int and said[key] >= 1 for key in ("from", "runs"))
    )


def _report(file: Path) -> dict:
    report = _json(file)
    if not isinstance(report, dict):
        raise compare.Unreadable(f"{file}: not a run's report")
    return report


def _json(file: Path) -> object:
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except OSError as error:
        raise compare.Unreadable(f"{file}: {error.strerror}") from None
    except ValueError as error:
        raise compare.Unreadable(f"{file}: not JSON: {error}") from None
