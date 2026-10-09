"""The command line: `forks report PATH...`."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Sequence

from . import rule, text
from .records import Counts
from .runs import InputError, find_runs, read_run


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs one command and returns the exit status."""
    parser = argparse.ArgumentParser(
        prog="forks",
        description="Reads the trace records in the logs of a GroupV2 group's members "
        "and says whether they still hold one group.",
    )
    commands = parser.add_subparsers(metavar="COMMAND", required=True)
    _add_report(commands)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (InputError, OSError) as error:
        print(f"forks: {error}", file=sys.stderr)
        return 2


def _add_report(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser(
        "report",
        help="say whether each conversation in some logs reads whole",
        description="Reads the trace records in logs and prints each conversation's report. "
        "Exits 0 when every conversation reads whole and no record was malformed, 1 when one "
        "does not, there is none or a record was malformed, 2 when the logs cannot be read.",
    )
    command.add_argument(
        "paths", metavar="PATH", nargs="+", type=Path, help="a log, or a directory of logs"
    )
    command.add_argument("--json", metavar="FILE", type=Path, help="also write the report as JSON")
    command.set_defaults(run=_report)


def _report(args: argparse.Namespace) -> int:
    readings = [read_run(run) for run in find_runs(args.paths)]
    conversations = rule.report(record for reading in readings for record in reading.records)
    counts = sum((reading.counts for reading in readings), Counts())
    _print(text.render(conversations, counts))
    if args.json:
        report = {"lines": asdict(counts), "conversations": [asdict(c) for c in conversations]}
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    whole = conversations and all(c.verdict == rule.WHOLE for c in conversations)
    # A verdict drawn without a record that could not be read is not a clean bill.
    return 0 if whole and not counts.malformed else 1


def _print(report: str) -> None:
    """Prints the report; a character the output's encoding cannot write is escaped."""
    encoding = getattr(sys.stdout, "encoding", None)
    if encoding:
        report = report.encode(encoding, "backslashreplace").decode(encoding)
    print(report)
