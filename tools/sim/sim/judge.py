"""What a run's logs come to: `forks` reads them, each of its commands as a process of its own."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    from .run import Settings


def judge(settings: Settings, installations: List[str], report: dict) -> None:
    """Writes the expectation, and has `forks` report on the logs and check them.

    `installations` are the ids of the members in the order of the members. The report gains
    `verdict` and `at_tip`, and `check` or why there is none as `unchecked`. Its `held_to` says
    which outcome is expected of a run that withheld a candidate. A run of a `grow` step is
    reported on and not checked, and each member the report knows is counted at the tip or not.
    """
    out: Path = settings.out
    convo = report["conversation"]
    applied = report["held_to"] == "applied"
    expectation = None
    if not settings.scenario.grows:
        expectation = settings.scenario.expectation(convo, installations, applied)
        held = json.dumps(expectation, indent=2) + "\n"
        (out / "expect.json").write_text(held, encoding="utf-8")

    _, drawn, _ = _forks(settings, "report", "--json", str(out / "report.json"), str(out))
    (out / "report.txt").write_text(drawn, encoding="utf-8")
    conversation = _reported(out / "report.json", convo)
    if conversation is not None:
        seats = {seat["id"]: seat["position"] for seat in conversation["installations"]}
        report["verdict"] = conversation["verdict"]
        counted = seats if expectation is None else expectation["installations"]
        report["at_tip"] = sum(seats.get(seat) == "tip" for seat in counted)

    if expectation is None:
        report["unchecked"] = "a `grow` step expects no outcome"
    else:
        report["check"], report["unchecked"] = _checked(
            *_forks(settings, "check", "--expect", str(out / "expect.json"), str(out))
        )


def _checked(status: int, said: str, complained: str) -> Tuple[Optional[dict], Optional[str]]:
    """What `forks check` found, or why it found nothing.

    It ends with status 0 for a report as expected and with 1 for one that differs, a line a
    difference. Anything else is a check that was not made, which is no difference of the report.
    """
    differences = said.splitlines()
    if status == 0 or (status == 1 and differences):
        return {"as_expected": status == 0, "differences": differences if status else []}, None
    return None, _ending("forks check", status, complained)


def _ending(command: str, status: int, complained: str) -> str:
    """How a command of `forks` ended, and the last line of what it complained of."""
    how = f"status {status}" if status >= 0 else f"signal {-status}"
    last = complained.splitlines()[-1:]
    return f"{command} ended with {how}" + (f": {last[0]}" if last else "")


def _forks(settings: Settings, *arguments: str) -> Tuple[int, str, str]:
    """Runs a command of `forks`: its exit status, what it printed and what it complained of."""
    done = subprocess.run(
        [sys.executable, "-B", str(settings.forks), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        # A terminal sends its interrupt to every process of a group, and the run that has its
        # processes ended is past being interrupted.
        start_new_session=True,
        text=True,
        errors="replace",
    )
    return done.returncode, done.stdout, done.stderr


def _reported(file: Path, convo: str) -> Optional[dict]:
    """The report of `convo` in what `forks report --json` wrote, when it wrote one."""
    try:
        conversations: Sequence[dict] = json.loads(file.read_bytes())["conversations"]
    except (OSError, ValueError):
        return None
    return next((held for held in conversations if held["id"] == convo), None)
