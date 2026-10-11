"""Tests of the sim tool, and what they share."""

import shlex
import sys
import textwrap
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
STANDIN = Path(__file__).parent / "standin.py"
FORKS = Path(__file__).resolve().parents[2] / "forks"


def stand_in(directory: Path, *faults: str) -> Path:
    """Writes into `directory` a program that is the stand-in for chat-cli with `faults`."""
    program = directory / "chat-cli"
    command = [sys.executable, "-B", str(STANDIN)]
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
