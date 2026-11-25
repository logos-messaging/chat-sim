"""The matrix of a revision's runs beside its base's: a cell for each scenario and seed, the
change of the cell, and the result of each group's runs at each rate of loss.

A side is what the jobs at one revision uploaded, read at any depth: each run's
`<scenario>/<run>/run.json`, and `<scenario>/<run>/unbuilt` for each run of a job that could not
build chat-sim. A cell is a scenario and a seed either side has a run of, or that is asked of
each side. A cell has no outcome at a side whose job could not build it, that has no run of it,
or whose run has no lineage of its `grow` step, as when its runner left no result. Its change is
what `compare` says of the base's outcome and the head's, where both have one, and its `d` how
many epochs later the head's run first forked than the base's, the trunk's epoch standing in for a
run that did not fork.

A scenario is of one group, the one its creator creates, GroupV2 or GroupV3, at one rate. The
result of its cells is the first of these that holds, and what moved is said only of the cells
with an outcome at both sides:

- `unbuilt`: the head does not build: no run at the head was read, and a job there could not
  build chat-sim.
- `no-outcome`: a cell has no outcome at the head.
- `no-base`: no cell has an outcome at the base.
- `same`: every cell with an outcome at both sides came out the same at both.
- `earlier` or `later`: the seeds whose run first forked earlier at the head, and those whose
  run first forked later, are apart beyond chance: an exact two-sided sign test on how many of
  each gives `ALPHA` or less. A cell whose runs moved at the same first fork counts on neither
  side.
- `within`: the cells moved, within chance.

A group's verdict is the worst of its scenarios' results, in the order of `WORST`, and the
matrix's the worst of its groups', so that one group's runs forking later cannot hide the
other's forking earlier. With no base, a result is `unbuilt`, `no-outcome` or else `None`. A
matrix of no cell has no outcome.
"""

from __future__ import annotations

import functools
import json
import math
import re
import statistics
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from . import compare, scenario, table

# How a run of a `grow` step can end, as `compare` says it.
ENDINGS = ("whole", "behind", "split", "stalled")
# The files of the scenarios a cell can be of, each its name and `.json`.
SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
# The p at or below which the sign test says the head's runs forked earlier or later than the
# base's beyond chance.
ALPHA = Fraction(1, 100)
# The results of a scenario's cells, the worst first.
WORST = ("unbuilt", "no-outcome", "earlier", "later", "within", "no-base", "same")
_CHANGES = ("same", "moved")
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
    could not be built, the cells, the count of each change, the verdict, the same counts and
    verdict of each group's cells as `groups` with the result of each of its scenarios, and with
    no base each scenario's summary. `asked` holds the runs asked of each side, each its scenario
    and its number in a sweep, so that a cell no side has a run of is there all the same.

    A cell holds its `scenario`, the `group` its scenario creates, `seed`, the `run` of a sweep
    it is, the `loss` of its network model and that loss in percent as its `rate`; the `head`'s
    outcome and the `base`'s, each `None` where the side has no outcome; whether each side's job
    could not build it, as `unbuilt`; the `change`, `None` where there is none; for each side the
    `epochs` without a fork and how the run `ended`; and `d`, the head's epochs less the base's,
    `None` where there is no change. The cells are in the order of their group, then loss, then
    scenario, then seed, and `groups` in the order of its group.

    A scenario's result holds its `scenario`, `rate` and the `seeds` of its cells; where a cell
    has a change, how many of those first forked `later` at the head than at the base, how
    many `earlier`, how many at the same epoch as `zero`, and the sign test's `p`, else each
    `None`; and the `result`.
    """
    sides = [head] + ([] if base is None else [base])
    where = {key: (run.number, run.loss) for side in sides for key, run in side.runs.items()}
    for name, number in asked:
        seed, loss = _drawn(name, number)
        where.setdefault((name, seed), (number, loss))
    cells = sorted((_cell(key, *where[key], head, base) for key in where), key=_order)
    groups: Dict[str, List[dict]] = {}
    for cell in cells:
        groups.setdefault(cell["group"], []).append(cell)
    unbuilt = _unbuilt_head(_side(head))
    judged = [_judged(group, its, unbuilt, base is not None) for group, its in groups.items()]
    return {
        "head": _side(head),
        "base": None if base is None else _side(base),
        "cells": cells,
        "counts": _counts(cells),
        "verdict": _worst(one["verdict"] for one in judged) if cells else "no-outcome",
        "groups": judged,
        "summary": _summary(cells) if base is None else None,
    }


def sign_test(later: int, earlier: int) -> Fraction:
    """The two-sided p of an exact sign test of `later` seeds against `earlier`: twice the chance
    that as few or fewer fall on the side with fewer, each seed a fair coin, at most 1; and 1 with
    no seed on either side."""
    seeds = later + earlier
    if not seeds:
        return Fraction(1)
    tail = sum(math.comb(seeds, one) for one in range(min(later, earlier) + 1))
    return min(Fraction(1), Fraction(2 * tail, 2**seeds))


def _judged(group: str, cells: List[dict], unbuilt: bool, based: bool) -> dict:
    scenarios: Dict[str, List[dict]] = {}
    for cell in cells:
        scenarios.setdefault(cell["scenario"], []).append(cell)
    results = [_result(its, unbuilt, based) for its in scenarios.values()]
    return {
        "group": group,
        "counts": _counts(cells),
        "verdict": _worst(one["result"] for one in results),
        "results": results,
    }


def _result(cells: List[dict], unbuilt: bool, based: bool) -> dict:
    """The result of one scenario's `cells`, of a head that does not build where `unbuilt`, beside
    a base where `based`, and the sign test of its cells with an outcome at both sides."""
    compared = [cell for cell in cells if cell["change"] is not None]
    tested = bool(compared)
    d = [cell["d"] for cell in compared]
    later, earlier = sum(one > 0 for one in d), sum(one < 0 for one in d)
    p = sign_test(later, earlier)
    if unbuilt:
        result: Optional[str] = "unbuilt"
    elif any(cell["head"] is None for cell in cells):
        result = "no-outcome"
    elif not based:
        result = None
    elif not tested:
        result = "no-base"
    elif all(cell["change"] == "same" for cell in compared):
        result = "same"
    elif p <= ALPHA:
        result = "earlier" if earlier > later else "later"
    else:
        result = "within"
    return {
        "scenario": cells[0]["scenario"],
        "rate": cells[0]["rate"],
        "seeds": [cell["seed"] for cell in cells],
        "later": later if tested else None,
        "earlier": earlier if tested else None,
        "zero": len(compared) - later - earlier if tested else None,
        "p": float(p) if tested else None,
        "result": result,
    }


def _worst(results: Iterable[Optional[str]]) -> Optional[str]:
    """The worst of `results` by `WORST`, `None` where none is any."""
    found = [one for one in results if one is not None]
    return min(found, key=WORST.index, default=None)


def _counts(cells: List[dict]) -> dict:
    return {
        "cells": len(cells),
        "compared": sum(cell["change"] is not None for cell in cells),
        **{change: sum(cell["change"] == change for cell in cells) for change in _CHANGES},
        "no-outcome": sum(cell["head"] is None for cell in cells),
    }


def _side(side: Side) -> dict:
    unbuilt = sum(run.outcome is None for run in side.runs.values())
    return {"runs": len(side.runs) - unbuilt, "unbuilt": unbuilt}


def _unbuilt_head(head: dict) -> bool:
    """Whether a head does not build: a job could not build chat-sim, and no run was read."""
    return bool(head["unbuilt"]) and not head["runs"]


def _cell(
    key: Tuple[str, int], number: int, loss: Optional[float], head: Side, base: Optional[Side]
) -> dict:
    now = head.runs.get(key)
    was = None if base is None else base.runs.get(key)
    head_outcome, base_outcome = _outcome(now), _outcome(was)
    change = None
    if None not in (head_outcome, base_outcome):
        change = compare.change(base_outcome, head_outcome)
    epochs = {"head": _unforked(head_outcome), "base": _unforked(base_outcome)}
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
        "epochs": epochs,
        "ended": {
            "head": None if head_outcome is None else head_outcome["ended"],
            "base": None if base_outcome is None else base_outcome["ended"],
        },
        "d": None if change is None else epochs["head"] - epochs["base"],
    }


def _outcome(run: Optional[Run]) -> Optional[dict]:
    """A run's outcome, or `None` where there is no run, it was not built or it has no lineage."""
    if run is None or run.outcome is None or run.outcome["ended"] is None:
        return None
    return run.outcome


def _unbuilt(run: Optional[Run]) -> bool:
    return run is not None and run.outcome is None


def _unforked(outcome: Optional[dict]) -> Optional[int]:
    return None if outcome is None else compare.unforked(outcome)


def _rate(loss: Optional[float]) -> Optional[str]:
    return None if loss is None else f"{loss * 100:g}%"


def _order(cell: dict) -> tuple:
    group = scenario.GROUPS.index(cell["group"])
    return (group, cell["loss"] is None, cell["loss"] or 0, cell["scenario"], cell["seed"])


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
