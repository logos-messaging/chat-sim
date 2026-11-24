"""The command line: `sim run SCENARIO`, `sim sweep SCENARIO --runs K [--from N]`,
`sim compare EXPECTED OUT` and `sim matrix HEAD [--base BASE | --base-point FILE] [--spread FILE]
[--asked FILE]`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

from . import compare, matrix, scenario, table
from .peers import Watch
from .run import NoDirectory, Settings, run
from .sweep import Interrupts, sweep

SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs one command and returns the exit status.

    0 when every step was done and the report of the logs is as the scenario expects, in
    every run of a sweep, for `compare` when every run came out as expected, and for `matrix`
    when it wrote the matrix. A command a signal interrupted returns 128 and the signal's
    number.
    """
    parser = argparse.ArgumentParser(
        prog="sim",
        description="Has a runner do the steps of a scenario with its members as clients in one "
        "process on a virtual clock, and holds the fork report of its log against what the "
        "scenario expects.",
    )
    commands = parser.add_subparsers(metavar="COMMAND", required=True)
    one = commands.add_parser(
        "run",
        help="do a scenario once",
        description="Does a scenario's steps once and checks the report of the logs. Exits 0 "
        "when every step was done and the report is as expected, 1 when not, 2 when the "
        "command line or the scenario is refused, and 128 and the number of the signal that "
        "interrupted it.",
    )
    _add_arguments(one)
    one.set_defaults(run=_run)
    many = commands.add_parser(
        "sweep",
        help="do a scenario several times, a row a run",
        description="Does a scenario several times, one run after another, and prints a row "
        "for each. With --from N the runs are runs N to N+K-1 of a sweep, each with the seeds it "
        "has in a sweep from run 1. Exits 0 when every run went as expected, 1 when one did not, "
        "2 when the command line or the scenario is refused, and 128 and the number of the "
        "signal that interrupted it.",
    )
    _add_arguments(many)
    many.add_argument("--runs", metavar="K", type=int, required=True, help="how many runs")
    many.add_argument(
        "--from",
        dest="first",
        metavar="N",
        type=int,
        default=1,
        help="the number of the first run (default: 1)",
    )
    many.set_defaults(run=_sweep)
    held = commands.add_parser(
        "compare",
        help="hold a sweep in one process to the outcomes expected of it",
        description="Holds each run of a sweep in one process to the outcome EXPECTED holds for "
        "it, and prints a row for each. Exits 0 when every run came out as expected, 1 when one "
        "did not, and 2 when the sweep or EXPECTED cannot be read. With --write it writes the "
        "outcomes of the sweep to EXPECTED instead.",
    )
    held.add_argument(
        "expected", metavar="EXPECTED", type=Path, help="the file of the outcomes, one a run"
    )
    held.add_argument(
        "out", metavar="OUT", type=Path, help="the directory a sweep in one process wrote"
    )
    held.add_argument(
        "--write", action="store_true", help="write the outcomes of the sweep to EXPECTED"
    )
    held.set_defaults(run=_compare)
    paired = commands.add_parser(
        "matrix",
        help="pair a revision's runs with its base's, and say what moved",
        description="Reads the runs in one process the jobs at a revision uploaded under HEAD, "
        "and those at its base under --base or the point of main in --base-point, pairs them "
        "by scenario and seed, and writes as JSON a cell for each pair with its change, the "
        "verdict over the cells, and without a base each scenario's summary. A run --asked "
        "asks of each side is a cell whether or not a side has it. With --spread, each cell "
        "says whether the head's epochs are below the lowest of that point's runs of its "
        "scenario. Exits 0 when it wrote the matrix, and 2 when a run, a point or --asked "
        "cannot be read.",
    )
    paired.add_argument(
        "head", metavar="HEAD", type=Path, help="what the jobs at the revision uploaded"
    )
    based = paired.add_mutually_exclusive_group()
    based.add_argument(
        "--base", metavar="DIR", type=Path, help="what the jobs at the base uploaded"
    )
    based.add_argument(
        "--base-point",
        metavar="FILE",
        type=Path,
        help="the point.json of main's point at the base, whose runs stand in for the base's",
    )
    paired.add_argument(
        "--spread",
        metavar="FILE",
        type=Path,
        help="the point.json of main's point whose runs the head's are placed among",
    )
    paired.add_argument(
        "--asked",
        metavar="FILE",
        type=Path,
        help="the sweeps asked of each side, as a JSON list: each with its scenario, the run it "
        "is from and how many runs",
    )
    paired.add_argument(
        "--out", metavar="FILE", type=Path, help="the file to write, in place of standard output"
    )
    paired.set_defaults(run=_matrix)
    args = parser.parse_args(argv)
    if args.run is _compare:
        return _compare(parser, args)
    if args.run is _matrix:
        return _matrix(parser, args)

    if not args.one_process.is_file():
        parser.error(f"--one-process: no such file: {args.one_process}")
    if not os.access(args.one_process, os.X_OK):
        parser.error(f"--one-process: not a file that can be run: {args.one_process}")
    if args.out.exists():
        parser.error(f"--out: {args.out} exists")
    if getattr(args, "runs", 1) < 1:
        parser.error("--runs: a sweep is 1 run or more")
    if getattr(args, "first", 1) < 1:
        parser.error("--from: a sweep's first run is run 1")
    try:
        settings = Settings(
            scenario=scenario.read(args.scenario),
            out=args.out,
            one_process=args.one_process.resolve(),
        )
    except scenario.Refused as refused:
        parser.error(str(refused))
    number = getattr(args, "first", 1) + getattr(args, "runs", 1) - 1
    last = settings.scenario.of_run(number)
    seeds = ((last, "the seed"), (last.network, "the network seed"))
    for seeded, said in seeds:
        if seeded is not None and seeded.seed > scenario.MOST_SEED:
            parser.error(
                f"--runs: run {number} would have {said} {seeded.seed}, "
                f"and {scenario.MOST_SEED} is the most"
            )
    try:
        return args.run(args, settings)
    except NoDirectory as refused:
        parser.error(f"--out: {refused}")


def _add_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "scenario", metavar="SCENARIO", type=Path, help="the file that says what a run does"
    )
    command.add_argument(
        "--one-process",
        metavar="PATH",
        type=Path,
        required=True,
        help="the runner that does the scenario in one process",
    )
    command.add_argument(
        "--out", metavar="DIR", type=Path, required=True, help="the directory to make and write to"
    )


def _run(args: argparse.Namespace, settings: Settings) -> int:
    watch = Watch()
    with _handling(watch.interrupt):
        report = run(settings, watch)
        sys.stdout.write(table.render(report))
    if watch.signal is not None:
        return 128 + watch.signal
    return 0 if report["ok"] else 1


def _sweep(args: argparse.Namespace, settings: Settings) -> int:
    interrupts = Interrupts()
    with _handling(interrupts.take):
        report = sweep(
            settings, args.runs, interrupts, lambda line: print(line, flush=True), args.first
        )
    if interrupts.signal is not None:
        return 128 + interrupts.signal
    return 0 if report["as_expected"] == args.runs else 1


def _compare(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        now = compare.outcomes(args.out)
        if args.write:
            args.expected.write_text(json.dumps(now, indent=2) + "\n", encoding="utf-8")
            return 0
        was = compare.expected(args.expected)
    except compare.Unreadable as refused:
        parser.error(str(refused))
    printed, held = compare.render(was, now)
    sys.stdout.write(printed)
    return 0 if held else 1


def _matrix(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    for flag, directory in (("HEAD", args.head), ("--base", args.base)):
        if directory is not None and not directory.is_dir():
            parser.error(f"{flag}: no such directory: {directory}")
    try:
        base = None
        if args.base is not None:
            base = matrix.read(args.base)
        elif args.base_point is not None:
            base = matrix.point(args.base_point)
        written = matrix.matrix(
            matrix.read(args.head),
            base,
            () if args.asked is None else matrix.asked(args.asked),
            None if args.spread is None else matrix.point(args.spread),
        )
    except compare.Unreadable as refused:
        parser.error(str(refused))
    text = json.dumps(written, indent=2) + "\n"
    if args.out is None:
        sys.stdout.write(text)
    else:
        args.out.write_text(text, encoding="utf-8")
    return 0


@contextlib.contextmanager
def _handling(take: Callable[[int], None]) -> Iterator[None]:
    """Hands each signal of `SIGNALS` to `take` for as long as it is entered.

    The default action ends the tool where it stands, with its runner still running. A signal
    the tool was started ignoring stays ignored.
    """
    handled = [signum for signum in SIGNALS if signal.getsignal(signum) is not signal.SIG_IGN]
    before = [signal.signal(signum, lambda taken, _frame: take(taken)) for signum in handled]
    try:
        yield
    finally:
        for signum, handler in zip(handled, before):
            signal.signal(signum, handler)
