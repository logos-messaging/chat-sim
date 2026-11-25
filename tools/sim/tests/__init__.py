"""Tests of the sim tool, and what they share."""

import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from typing import Tuple

from sim.cli import SIGNALS

FIXTURES = Path(__file__).parent / "fixtures"
STANDIN_RUNNER = Path(__file__).parent / "standin_runner.py"
FORKS = Path(__file__).resolve().parents[2] / "forks"
TOOL = Path(__file__).resolve().parents[1]

# What a stand-in comes to when the group applied the candidate withheld in `withholding`.
APPLIED = {"verdict": "split at epoch 2"}


def said(members: int = 3, **changed) -> dict:
    """A scenario as a file says it: the creator seats every other member, and the last posts."""
    whole = {
        "members": members,
        "budget": 30,
        "steps": [{"step": "seat", "to": members}, {"step": "post"}],
    }
    whole.update(changed)
    return whole


def withholding(*after: dict, **changed) -> dict:
    """What makes `said` a scenario of four that withholds a candidate from pax.

    Three members are seated, and the candidate is one of the batch that seats mira. The steps
    `after` follow.
    """
    seat, withhold = {"step": "seat", "to": 3}, {"step": "withhold", "from": "pax"}
    steps = [seat, withhold, {"step": "seat", "to": 4}, *after]
    return dict(members=4, network={"seed": 7}, steps=steps, **changed)


def stand_in_runner(directory: Path, *faults: str) -> Path:
    """Writes into `directory` a program that is the stand-in for the runner with `faults`."""
    program = directory / "runner"
    command = [sys.executable, "-B", str(STANDIN_RUNNER)]
    for fault in faults:
        command += ["--fault", fault]
    program.write_text(f'#!/bin/sh\nexec {shlex.join(command)} "$@"\n', encoding="utf-8")
    program.chmod(0o755)
    return program


def forks_whose(command: str, does: str, directory: Path) -> Path:
    """Writes into `directory` a `forks` that runs the lines of `does` before `command`.

    It is the repository's own `forks` in everything else, and in `command` too once the lines
    are done.
    """
    program = directory / "forks"
    program.mkdir()
    (program / "__main__.py").write_text(
        "import os, runpy, signal, sys, time\n"
        f"if sys.argv[1] == {command!r}:\n"
        f"{textwrap.indent(does, '    ')}\n"
        f"runpy.run_path({str(FORKS)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    return program


class Scratch(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.out = self.scratch / "run"

    def scenario(self, **changed) -> Path:
        """A file that holds a scenario."""
        file = self.scratch / "scenario.json"
        file.write_text(json.dumps(said(**changed)), encoding="utf-8")
        return file

    def written(self) -> str:
        """Every file the command wrote, one after another."""
        files = sorted(file for file in self.out.rglob("*") if file.is_file())
        return "\n".join(file.read_text(encoding="utf-8", errors="replace") for file in files)


class Driven(Scratch):
    """The command as a process of its own."""

    def start(
        self, *arguments: str, runner: Path, ignoring: Tuple[int, ...] = (), tool: Path = TOOL
    ) -> subprocess.Popen:
        def dispositions() -> None:
            for signum in SIGNALS:
                signal.signal(signum, signal.SIG_IGN if signum in ignoring else signal.SIG_DFL)

        driver = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(tool),
                *arguments,
                "--one-process",
                str(runner),
                "--out",
                str(self.out),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=dispositions,
            # A session of its own, so that a test that fails can end whatever the tool left.
            start_new_session=True,
        )
        self.addCleanup(self.end, driver)
        return driver

    def end(self, driver: subprocess.Popen) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(driver.pid, signal.SIGKILL)
        driver.communicate()

    def wait_for(self, path: Path, text: str = "") -> None:
        until = time.monotonic() + 60
        while not (path.is_file() and text in path.read_text(encoding="utf-8")):
            if time.monotonic() > until:
                self.fail(f"{path} never held {text!r}")
            time.sleep(0.01)
