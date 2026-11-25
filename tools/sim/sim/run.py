"""One run: a runner does a scenario's steps in one process, and the log it leaves is judged."""

from __future__ import annotations

import json
import os
import signal
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

from . import judge, one_process, table
from .peers import Watch
from .scenario import Scenario

REPOSITORY = Path(__file__).resolve().parents[3]


class NoDirectory(Exception):
    """Why the directory a run writes to could not be made."""


@dataclass(frozen=True)
class Settings:
    """What a run is given."""

    scenario: Scenario
    out: Path
    # The runner that does the scenario in one process.
    one_process: Path
    forks: Path = REPOSITORY / "tools" / "forks"


def run(settings: Settings, watch: Watch) -> dict:
    """Does a scenario's steps, ends the runner, and has its log judged.

    Returns the report, which it also writes into the run's directory as `run.json` and, as
    the tables a person reads, `run.txt`. A signal `watch` takes before the runner is being
    ended interrupts the run.
    """
    make(settings.out)
    scenario = settings.scenario
    report = {
        "members": scenario.members,
        "ok": False,
        "stopped": None,
        "steps": len(scenario.steps),
        "steps_done": 0,
        "group": scenario.group,
        "timers": scenario.timers,
        "budget": scenario.budget,
        "seed": None,
        "network": None if scenario.network is None else asdict(scenario.network),
        "load": {"start": list(os.getloadavg()), "end": None},
        "nodes": [],
        "batches": [],
        "posts": [],
        "holds": [],
        "conversation": None,
        "withheld": None,
        "held_to": None,
        "check": None,
        "unchecked": None,
        "verdict": None,
        "at_tip": None,
        "seated": scenario.seated,
        "grow": None,
    }
    one_process.run(settings, report, watch)
    installations = [node["installation"] for node in report["nodes"]]
    return _judged(settings, installations, report, watch)


def _judged(settings: Settings, installations: List[str], report: dict, watch: Watch) -> dict:
    """Has the logs of a run that is over judged, and writes the report it returns.

    `installations` are the ids the logs know the members by, in the order of the members.
    A run a signal interrupted is not judged, nor is a run of a `grow` step, whose lineage says
    how it came out.
    """
    report["load"]["end"] = list(os.getloadavg())
    _withheld(settings.scenario, report)
    if watch.signal is not None:
        report["stopped"] = f"interrupted by {signal.Signals(watch.signal).name}"
    elif report["conversation"] is not None and settings.scenario.grows:
        report["unchecked"] = "a `grow` step expects no outcome"
    elif report["conversation"] is not None:
        judge.judge(settings, installations, report)
    check = report["check"]
    # A grow expects no outcome: it is as expected once it ends as it may.
    checked = report["grow"] is not None or (check is not None and check["as_expected"])
    report["ok"] = report["stopped"] is None and checked
    (settings.out / "run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (settings.out / "run.txt").write_text(table.render(report), encoding="utf-8")
    return report


def make(directory: Path) -> None:
    """Makes a directory that is not there yet, and those above it."""
    try:
        directory.mkdir(parents=True)
    except OSError as error:
        raise NoDirectory(str(error)) from None


def _withheld(scenario: Scenario, report: dict) -> None:
    """Says what was withheld from a member, and which outcome the run is held to by it.

    Both go by what the member's network model last answered: whether the steward that minted
    the candidate has broadcast a Welcome since, which a steward does for a commit of its own
    that it merged. One batch follows the step, so that commit is the candidate, and the group
    applied it where its stewards merged one commit.
    """
    answers = {node["name"]: node.get("network") for node in report["nodes"]}
    withheld = (answers.get(scenario.withheld_from) or {}).get("withheld")
    if withheld is None:
        return
    minters = [node["name"] for node in report["nodes"] if node["app_id"] == withheld["app_id"]]
    report["withheld"] = {
        "from": scenario.withheld_from,
        "seat": withheld["seat"],
        "app_id": withheld["app_id"],
        "minter": minters[0] if minters and withheld["app_id"] is not None else None,
        "applied": withheld["applied"],
    }
    report["held_to"] = "applied" if withheld["applied"] else "not applied"
