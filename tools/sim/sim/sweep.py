"""A sweep: one scenario run several times over, one after another, a row a run."""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import time
from typing import Callable, Optional

from . import table
from .peers import Watch
from .run import Settings, make, run


class Interrupts:
    """The signal that asked a sweep to end: it interrupts the run in hand, and no run follows."""

    def __init__(self) -> None:
        self.signal: Optional[int] = None
        self._watch: Optional[Watch] = None

    def take(self, signum: int) -> None:
        """Takes a signal the sweep was sent: the first one counts."""
        if self.signal is None:
            self.signal = signum
        if self._watch is not None:
            self._watch.interrupt(signum)

    def watch(self) -> Watch:
        """What the next run's waits look at."""
        self._watch = Watch()
        return self._watch


def sweep(
    settings: Settings, runs: int, interrupts: Interrupts, say: Callable[[str], None]
) -> dict:
    """Runs a scenario `runs` times, each in a directory of its own under `settings.out`.

    A scenario with a network model gives each run the seed after that of the run before.

    Says the head of the rows, a row as each run ends, and how many went as expected. Returns
    the same, which it also writes under `settings.out` as `sweep.json` and `sweep.txt`, as
    each row is said and once more at the end.
    """
    make(settings.out)
    said = []

    def tell(line: str) -> None:
        said.append(line)
        say(line)

    def write() -> None:
        for name, held in (
            ("sweep.json", json.dumps(report, indent=2) + "\n"),
            ("sweep.txt", "".join(f"{line}\n" for line in said)),
        ):
            (settings.out / name).write_text(held, encoding="utf-8")

    report = {
        "runs": runs,
        "done": 0,
        "as_expected": 0,
        "interrupted": None,
        "load": {"start": list(os.getloadavg()), "end": None},
        "rows": [],
    }
    tell(table.head())
    for number in range(1, runs + 1):
        # The watch is made before the look: a signal taken from here on is in one of the two.
        watch = interrupts.watch()
        if interrupts.signal is not None:
            break
        directory = f"{number:0{len(str(runs))}d}"
        started = time.monotonic()
        scenario = settings.scenario.of_run(number)
        one = run(
            dataclasses.replace(settings, out=settings.out / directory, scenario=scenario), watch
        )
        if watch.signal is not None:
            break
        seconds = time.monotonic() - started
        report["done"] += 1
        report["as_expected"] += one["ok"]
        report["rows"].append(
            {
                "run": number,
                "directory": directory,
                "ok": one["ok"],
                "differences": None if one["check"] is None else one["check"]["differences"],
                "unchecked": one["unchecked"],
                "stopped": one["stopped"],
                "verdict": one["verdict"],
                "at_tip": one["at_tip"],
                "seated": one["seated"],
                "seconds": seconds,
                "seed": None if one["network"] is None else one["network"]["seed"],
                "withheld": one["withheld"],
                "page": one["page"],
            }
        )
        tell(table.row(number, seconds, one))
        write()
    report["load"]["end"] = list(os.getloadavg())
    counted = f"{report['as_expected']} of {report['done']} runs went as expected"
    if interrupts.signal is not None:
        report["interrupted"] = signal.Signals(interrupts.signal).name
        counted += f": interrupted by {report['interrupted']} after {report['done']} of {runs}"
    tell(counted)
    write()
    return report
