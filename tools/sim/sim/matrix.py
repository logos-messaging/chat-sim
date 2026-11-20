"""The matrix of a revision's runs beside its base's: a cell for each scenario and seed, the
change of the cell, and the verdict over the cells and over each group's.

A side is what the jobs at one revision uploaded, read at any depth: each run's
`<scenario>/<run>/run.json`, and `<scenario>/<run>/unbuilt` for each run of a job that could not
build chat-sim. A cell is a scenario and a seed either side has a run of, or that is asked of
each side. A cell has no outcome at a side whose job could not build it, that has no run of it,
or whose run has no verdict, as when its runner left no result. Its change is what `compare` says
of the base's outcome and the head's, where both have one.

A point of main, what its `point.json` keeps of the matrix of main's runs at a logos-chat commit,
can stand in for the base's runs: a cell's outcome at the base is then the point's at its scenario
and seed, as written, ids and all, so that its change and the verdict are what they would be
against the base's own runs, and a cell the point has no outcome for has none at the base. A point
holds every seed main runs, so its runs make no cell of their own. A point can also be the spread
the head's runs are placed among: the matrix keeps of it each scenario's summary and each run's
epochs without a fork and how it ended, at the scenarios of the cells, says of each cell whether
the head's epochs are below the lowest of the point's runs of its scenario, and counts the
re-rolled cells that are.

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

A cell is of the group its scenario's creator creates, GroupV2 or GroupV3, and the verdict over a
group's cells is the same rule over them alone, so that one group's cells better cannot hide the
other's worse.
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
_SHA = re.compile(r"[0-9a-f]{40}")
_URL = re.compile(r"https?://[^\s\"'<>]+")
_HEX = re.compile(r"[0-9a-f]+")
# What a point holds of a cell's outcome at the head and of a scenario's summary: each field's
# kinds, or a shape of its own.
_NONE = type(None)
_OUTCOME = {
    "seed": (int,),
    "network_seed": (int, _NONE),
    "conversation": (str, _NONE),
    "members_digest": (str, _NONE),
    "verdict": (str, _NONE),
    "at_tip": (int, _NONE),
    "seated": (int, _NONE),
    "virtual_s": (int, float, _NONE),
    "trunk_epoch": (int, _NONE),
    "ended": (str, _NONE),
    "lines_off_trunk": (int, _NONE),
    "members_off_trunk": (int, _NONE),
    "never_joined": (int, _NONE),
    "came_to": (str,),
    "stopped": (str, _NONE),
}
_SUMMARY = {
    "scenario": (str,),
    "group": (str,),
    "loss": (int, float, _NONE),
    "rate": (str, _NONE),
    "runs": (int,),
    "no-outcome": (int,),
    **{ended: (int,) for ended in ENDINGS},
    "epochs": {"lowest": (int, _NONE), "median": (int, float, _NONE), "highest": (int, _NONE)},
    "split_at": {"lowest": (int, _NONE), "median": (int, float, _NONE), "highest": (int, _NONE)},
    "lines_off_trunk": {"median": (int, float, _NONE)},
    "members_off_trunk": {"median": (int, float, _NONE)},
    "never_joined": {"median": (int, float, _NONE)},
    "seated": {"median": (int, float, _NONE)},
}


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


@dataclass(frozen=True)
class Point:
    """A point of main: its logos-chat commit, the chat-sim commit it measures, the address of its
    run, its cells, each with its scenario, seed, run, loss, outcome at the head and whether its
    job could not build it, and its summary of each scenario."""

    logos_chat: str
    measurement: str
    run_url: str
    cells: List[dict]
    summary: List[dict]


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


def point(file: Path) -> Point:
    """The point of main the `point.json` in `file` holds: its commits, the address of its run,
    each cell's scenario, seed, run, loss, outcome at the head as written and whether its job
    could not build it, and its summary of each scenario as written."""
    held = _json(file)
    if not (
        isinstance(held, dict)
        and all(_SHA.fullmatch(str(held.get(key))) for key in ("logos_chat", "measurement"))
        and _URL.fullmatch(str(held.get("run_url")))
        and isinstance(held.get("cells"), list)
        and isinstance(held.get("summary"), list)
    ):
        raise compare.Unreadable(
            f"{file}: not a point of main, with its two commits, its run's address, its cells "
            "and its summary"
        )
    seen = set()
    for number, cell in enumerate(held["cells"]):
        if not _point_cell(cell):
            raise compare.Unreadable(
                f"{file}: cells[{number}] is not a cell of a point: its scenario, seed, run, loss, "
                "outcome at the head and whether it was built there"
            )
        if (cell["scenario"], cell["seed"]) in seen:
            raise compare.Unreadable(
                f"{file}: cells[{number}] is a second cell of {cell['scenario']} at seed "
                f"{cell['seed']}"
            )
        seen.add((cell["scenario"], cell["seed"]))
    for number, one in enumerate(held["summary"]):
        if not _shaped(one, _SUMMARY):
            raise compare.Unreadable(
                f"{file}: summary[{number}] is not a scenario's summary as `matrix` writes one"
            )
    return Point(
        held["logos_chat"], held["measurement"], held["run_url"], held["cells"], held["summary"]
    )


def matrix(
    head: Side,
    base: Union[Side, Point, None] = None,
    asked: Sequence[Tuple[str, int]] = (),
    spread: Optional[Point] = None,
) -> dict:
    """The matrix of `head` beside `base`: of each side how many runs were read and how many
    could not be built, the point of main that stood in for the base's runs, the cells, the count
    of each change and of the re-rolled cells `below` the spread, the verdict, the same counts and
    verdict of each group's cells as `groups`, with no base each scenario's summary, and what it
    holds of `spread`. `asked` holds the runs asked of each side, each its scenario and its number
    in a sweep, so that a cell no side has a run of is there all the same.

    `base` is the base's runs, or a point of main that stands in for them. `spread` is a point of
    main the head's runs are placed among: of it the matrix holds its commits and the address of
    its run, and at the scenarios of the cells its `summary` and as `seeds` each of its runs'
    `scenario`, `seed`, `epochs` without a fork and how it `ended`.

    A cell holds its `scenario`, the `group` its scenario creates, `seed`, the `run` of a sweep
    it is, the `loss` of its network model and that loss in percent as its `rate`; the `head`'s
    outcome and the `base`'s, each `None` where the side has no outcome; whether each side's job
    could not build it, as `unbuilt`; the `change`, `None` where there is none; for each side the
    `epochs` without a fork and how the run `ended`; where it is better or worse, the `part` of
    the standing in which it `moved` and each side's value of it, else `moved` is `None`; and
    whether the head's epochs are `below` the lowest of the spread's runs of its scenario, `None`
    without a spread, a lowest or epochs at the head. The cells are in the order of their group,
    then loss, then scenario, then seed, and `groups` in the order of its group.
    """
    # A point's runs are main's twenty seeds a scenario, of which a pull request runs three.
    sides = [head] + ([base] if isinstance(base, Side) else [])
    where = {key: (run.number, run.loss) for side in sides for key, run in side.runs.items()}
    for name, number in asked:
        seed, loss = _drawn(name, number)
        where.setdefault((name, seed), (number, loss))
    runs = base
    if isinstance(base, Point):
        runs = Side({key: run for key, run in _runs(base).runs.items() if key in where})
    summarised = [] if spread is None else spread.summary
    lowest = {one["scenario"]: one["epochs"]["lowest"] for one in summarised}
    cells = sorted(
        (_cell(key, *where[key], head, runs, lowest.get(key[0])) for key in where), key=_order
    )
    groups: Dict[str, List[dict]] = {}
    for cell in cells:
        groups.setdefault(cell["group"], []).append(cell)
    counts = _counts(cells)
    return {
        "head": _side(head),
        "base": None if runs is None else _side(runs),
        "base_point": _named(base) if isinstance(base, Point) else None,
        "cells": cells,
        "counts": counts,
        "verdict": _verdict(_side(head), counts),
        "groups": [_judged(group, its, head) for group, its in groups.items()],
        "summary": _summary(cells) if runs is None else None,
        "spread": None if spread is None else _spread(spread, cells),
    }


def _judged(group: str, cells: List[dict], head: Side) -> dict:
    counts = _counts(cells)
    return {"group": group, "counts": counts, "verdict": _verdict(_side(head), counts)}


def _counts(cells: List[dict]) -> dict:
    return {
        "cells": len(cells),
        "compared": sum(cell["change"] is not None for cell in cells),
        **{change: sum(cell["change"] == change for cell in cells) for change in _CHANGES},
        "below": sum(cell["change"] == "re-rolled" and cell["below"] is True for cell in cells),
        "no-outcome": sum(cell["head"] is None for cell in cells),
    }


def _side(side: Side) -> dict:
    unbuilt = sum(run.outcome is None for run in side.runs.values())
    return {"runs": len(side.runs) - unbuilt, "unbuilt": unbuilt}


def _cell(
    key: Tuple[str, int],
    number: int,
    loss: Optional[float],
    head: Side,
    base: Optional[Side],
    lowest: Optional[int],
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
    epochs = _unforked(head_outcome)
    return {
        "scenario": key[0],
        "group": _scenario(key[0]).group,
        "seed": key[1],
        "run": number,
        "loss": loss,
        "rate": _rate(loss),
        "head": head_outcome,
        "base": base_outcome,
        "unbuilt": {"head": _unbuilt(now), "base": _unbuilt(was)},
        "change": change,
        "epochs": {
            "head": epochs,
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
        "below": None if None in (epochs, lowest) else epochs < lowest,
    }


def _runs(point: Point) -> Side:
    """The runs of a point as a side: each cell with an outcome at the head, or whose job could
    not build it."""
    return Side(
        {
            (cell["scenario"], cell["seed"]): Run(cell["run"], cell["loss"], cell["head"])
            for cell in point.cells
            if cell["head"] is not None or cell["unbuilt"]["head"]
        }
    )


def _named(point: Point) -> dict:
    return {
        "logos_chat": point.logos_chat,
        "measurement": point.measurement,
        "run_url": point.run_url,
    }


def _spread(point: Point, cells: List[dict]) -> dict:
    names = {cell["scenario"] for cell in cells}
    return {
        **_named(point),
        "summary": [one for one in point.summary if one["scenario"] in names],
        "seeds": [
            {
                "scenario": cell["scenario"],
                "seed": cell["seed"],
                "epochs": _unforked(cell["head"]),
                "ended": None if cell["head"] is None else cell["head"]["ended"],
            }
            for cell in point.cells
            if cell["scenario"] in names
        ],
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
    group = scenario.GROUPS.index(cell["group"])
    return (group, cell["loss"] is None, cell["loss"] or 0, cell["scenario"], cell["seed"])


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
    """For each scenario in the order of its cells: its group, how many runs, how each ended, the
    lowest, median and highest epochs without a fork, the same of the epoch of the first fork of
    the runs that split, and the medians of the lines off the trunk at the end, of the members off
    it, as `compare` counts them: behind, on a line not the trunk, or never joined, of those that
    never joined, and of the members seated."""
    scenarios: Dict[str, List[dict]] = {}
    for cell in cells:
        scenarios.setdefault(cell["scenario"], []).append(cell)
    summary = []
    for name, its in scenarios.items():
        outcomes = [cell["head"] for cell in its if cell["head"] is not None]
        epochs = [cell["epochs"]["head"] for cell in its if cell["epochs"]["head"] is not None]
        split = [cell["epochs"]["head"] for cell in its if cell["ended"]["head"] == "split"]
        lines = [one["lines_off_trunk"] for one in outcomes if one["lines_off_trunk"] is not None]
        off = [one["members_off_trunk"] for one in outcomes if one["members_off_trunk"] is not None]
        never = [one["never_joined"] for one in outcomes if one["never_joined"] is not None]
        seated = [one["seated"] for one in outcomes if one["seated"] is not None]
        summary.append(
            {
                "scenario": name,
                "group": its[0]["group"],
                "loss": its[0]["loss"],
                "rate": its[0]["rate"],
                "runs": len(its),
                "no-outcome": len(its) - len(outcomes),
                **{ended: sum(one["ended"] == ended for one in outcomes) for ended in ENDINGS},
                "epochs": _extent(epochs),
                "split_at": _extent(split),
                "lines_off_trunk": {"median": _median(lines)},
                "members_off_trunk": {"median": _median(off)},
                "never_joined": {"median": _median(never)},
                "seated": {"median": _median(seated)},
            }
        )
    return summary


def _extent(values: List[int]) -> dict:
    return {
        "lowest": min(values, default=None),
        "median": _median(values),
        "highest": max(values, default=None),
    }


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


def _point_cell(cell: object) -> bool:
    return (
        isinstance(cell, dict)
        and isinstance(cell.get("scenario"), str)
        and type(cell.get("seed")) is int
        and type(cell.get("run")) is int
        and cell["run"] >= 1
        and "loss" in cell
        and (cell["loss"] is None or type(cell["loss"]) in (int, float))
        and "head" in cell
        and (cell["head"] is None or _outcome_of(cell["head"]))
        and isinstance(cell.get("unbuilt"), dict)
        and type(cell["unbuilt"].get("head")) is bool
    )


def _outcome_of(outcome: object) -> bool:
    return (
        _shaped(outcome, _OUTCOME)
        and outcome["ended"] in (None, *ENDINGS)
        and all(
            outcome[field] is None or _HEX.fullmatch(outcome[field])
            for field in ("conversation", "members_digest")
        )
    )


def _shaped(value: object, shape: dict) -> bool:
    """Whether `value` is an object of the fields of `shape` alone, each of the kinds `shape`
    gives it, or of a shape of its own; a bool is not an int."""
    return (
        isinstance(value, dict)
        and value.keys() == shape.keys()
        and all(
            _shaped(value[field], kind) if isinstance(kind, dict) else type(value[field]) in kind
            for field, kind in shape.items()
        )
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
