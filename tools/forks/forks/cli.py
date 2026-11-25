"""The command line: `forks report PATH...` and `forks check --expect FILE PATH...`."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Sequence

from . import check, rule, text
from .records import Counts
from .runs import InputError, find_runs, read_run


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs one command and returns the exit status."""
    parser = argparse.ArgumentParser(
        prog="forks",
        description="Reads the trace records in the logs of a group's members "
        "and says whether they still hold one group.",
    )
    commands = parser.add_subparsers(metavar="COMMAND", required=True)
    _add_report(commands)
    _add_check(commands)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (InputError, check.NoExpectation, OSError) as error:
        _complain(str(error))
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
        report = {
            "lines": asdict(counts),
            "conversations": [rule.as_json(conversation) for conversation in conversations],
        }
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    whole = conversations and all(c.verdict == rule.WHOLE for c in conversations)
    # A verdict drawn without a record that could not be read is not a clean bill.
    return 0 if whole and not counts.malformed else 1


def _add_check(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser(
        "check",
        help="say whether one conversation in some logs reads as a file expects",
        description="Reads the trace records in logs and holds one conversation's report "
        "against an expectation: its verdict, the position of each installation and what "
        "became of each post. Exits 0 when the report is as expected, 1 when it differs or a "
        "record was malformed, 2 when the logs or the expectation cannot be used.",
    )
    command.add_argument(
        "--expect",
        metavar="FILE",
        type=Path,
        required=True,
        help="the expectation: a conversation, and what its report must hold",
    )
    command.add_argument(
        "paths", metavar="PATH", nargs="+", type=Path, help="a log, or a directory of logs"
    )
    command.set_defaults(run=_check)


def _check(args: argparse.Namespace) -> int:
    expectation = check.read(args.expect)
    readings = [read_run(run) for run in find_runs(args.paths)]
    records = (record for reading in readings for record in reading.records)
    reported = rule.report(records, only=[expectation.conversation])
    found = check.differences(expectation, reported[0] if reported else None)
    malformed = sum(reading.counts.malformed for reading in readings)
    if malformed:
        # A report drawn without a record that could not be read is not the one expected.
        found.append(
            "1 malformed line was skipped: the report was drawn without it"
            if malformed == 1
            else f"{malformed} malformed lines were skipped: the report was drawn without them"
        )
    _print("\n".join(found) or f"conversation {expectation.conversation}: as expected")
    return 1 if found else 0


def _complain(problem: str) -> None:
    print(f"forks: {problem}", file=sys.stderr)


def _print(report: str) -> None:
    """Prints the report at once; a character the output's encoding cannot write is escaped."""
    encoding = getattr(sys.stdout, "encoding", None)
    if encoding:
        report = report.encode(encoding, "backslashreplace").decode(encoding)
    try:
        print(report, flush=True)
    except BrokenPipeError:
        # Nobody reads the output any more. Closed, it is not written once more as the
        # interpreter exits, which would end the command with a status of the interpreter's.
        with contextlib.suppress(OSError):
            sys.stdout.close()
        raise
