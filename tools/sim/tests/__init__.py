"""Tests of the sim tool, and what they share."""

import shlex
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
STANDIN = Path(__file__).parent / "standin.py"


def stand_in(directory: Path, *faults: str) -> Path:
    """Writes into `directory` a program that is the stand-in for chat-cli with `faults`."""
    program = directory / "chat-cli"
    command = [sys.executable, "-B", str(STANDIN)]
    for fault in faults:
        command += ["--fault", fault]
    program.write_text(f'#!/bin/sh\nexec {shlex.join(command)} "$@"\n', encoding="utf-8")
    program.chmod(0o755)
    return program
