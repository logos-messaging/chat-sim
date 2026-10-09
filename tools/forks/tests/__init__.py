"""Tests of the forks tool, and what they share."""

import contextlib
import io
import json
import tempfile
from pathlib import Path
from typing import List, NamedTuple

from forks.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


class Reported(NamedTuple):
    """What `forks report --json` did: its exit status, what it printed, and the file it wrote."""

    status: int
    text: str
    written: str

    @property
    def conversations(self) -> List[dict]:
        return json.loads(self.written)["conversations"]

    @property
    def conversation(self) -> dict:
        (conversation,) = self.conversations
        return conversation

    @property
    def lines(self) -> dict:
        return json.loads(self.written)["lines"]


def run_report(*paths: Path) -> Reported:
    """Runs `forks report --json FILE PATH...` the way the command line does."""
    printed = io.StringIO()
    with tempfile.TemporaryDirectory() as scratch:
        written = Path(scratch) / "report.json"
        with contextlib.redirect_stdout(printed):
            status = main(["report", "--json", str(written), *map(str, paths)])
        return Reported(status, printed.getvalue(), written.read_text())
