"""The command line: `sim run SCENARIO`, `sim sweep SCENARIO --runs K` and
`sim compare EXPECTED OUT`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import sys
from pathlib import Path
from typing import Callable, Iterator, NoReturn, Optional, Sequence
from urllib.parse import unquote

from . import compare, containers, judge, scenario, table
from .peers import Watch
from .run import NoDirectory, Settings, run
from .sweep import Interrupts, sweep

SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs one command and returns the exit status.

    0 when every step was done and the report of the logs is as the scenario expects, in
    every run of a sweep, and for `compare` when every run came out as expected. A command a
    signal interrupted returns 128 and the signal's number.
    """
    parser = _Parser(
        prog="sim",
        description="Starts headless chat-cli peers on a delivery network of their own or on a "
        "fleet preset, has them do the steps of a scenario, and holds the fork report of their "
        "logs against what the scenario expects. With --image each peer and the registry run "
        "in a container of their own, on a network of containers. With --one-process the "
        "members are clients in one process on a virtual clock, and no peer is started.",
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
        "for each. Exits 0 when every run went as expected, 1 when one did not, 2 when the "
        "command line or the scenario is refused, and 128 and the number of the signal that "
        "interrupted it.",
    )
    _add_arguments(many)
    many.add_argument("--runs", metavar="K", type=int, required=True, help="how many runs")
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
    args = parser.parse_args(argv)
    if args.run is _compare:
        return _compare(parser, args)

    engine = args.engine or containers.ENGINES[0]
    if args.image is None:
        if args.engine is not None:
            parser.error("--engine: it runs --image, and none is given")
        flag, program = "--chat-cli", args.chat_cli
        if args.one_process is not None:
            flag, program = "--one-process", args.one_process
        if not program.is_file():
            parser.error(f"{flag}: no such file: {program}")
        if not os.access(program, os.X_OK):
            parser.error(f"{flag}: not a file that can be run: {program}")
    elif shutil.which(engine) is None:
        parser.error(f"--engine: {engine} is not on PATH")
    if args.out.exists():
        parser.error(f"--out: {args.out} exists")
    if getattr(args, "runs", 1) < 1:
        parser.error("--runs: a sweep is 1 run or more")
    try:
        settings = Settings(
            chat_cli=None if args.chat_cli is None else args.chat_cli.resolve(),
            scenario=scenario.read(args.scenario),
            out=args.out,
            share=args.share,
            one_process=None if args.one_process is None else args.one_process.resolve(),
            image=args.image,
            engine=engine,
        )
    except scenario.Refused as refused:
        parser.error(str(refused))
    if settings.one_process is not None and settings.scenario.preset is not None:
        parser.error("--one-process: the scenario has `preset`, and one process joins no fleet")
    if settings.image is not None and settings.scenario.preset is not None:
        parser.error(
            "--image: the scenario has `preset`, and a network of containers reaches no fleet"
        )
    if settings.one_process is None and settings.scenario.grows:
        parser.error("the scenario has a `grow` step, and only --one-process does one")
    if settings.image is None and settings.scenario.wire is not None:
        parser.error("the scenario has `wire`, and only --image puts conditions on the wire")
    last = settings.scenario.of_run(getattr(args, "runs", 1))
    seeds = ((last, "the seed"), (last.network, "the network seed"), (last.wire, "the wire seed"))
    for seeded, said in seeds:
        if seeded is not None and seeded.seed > scenario.MOST_SEED:
            parser.error(
                f"--runs: run {args.runs} would have {said} {seeded.seed}, "
                f"and {scenario.MOST_SEED} is the most"
            )
    if settings.share is not None:
        refusal = judge.unreadable(settings, settings.share)
        if refusal is not None:
            parser.error(f"--share: {refusal}")
    try:
        return args.run(args, settings)
    except NoDirectory as refused:
        parser.error(f"--out: {refused}")


def _add_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "scenario", metavar="SCENARIO", type=Path, help="the file that says what a run does"
    )
    rig = command.add_mutually_exclusive_group(required=True)
    rig.add_argument("--chat-cli", metavar="PATH", type=Path, help="the chat-cli binary to run")
    rig.add_argument(
        "--one-process",
        metavar="PATH",
        type=Path,
        help="the runner that does the scenario in one process, in place of --chat-cli",
    )
    rig.add_argument(
        "--image",
        metavar="IMAGE",
        help="the image whose chat-cli each peer runs in a container, in place of --chat-cli",
    )
    command.add_argument(
        "--engine",
        choices=containers.ENGINES,
        help=f"the container engine that runs --image (default: {containers.ENGINES[0]})",
    )
    command.add_argument(
        "--out", metavar="DIR", type=Path, required=True, help="the directory to make and write to"
    )
    command.add_argument(
        "--share",
        metavar="ADDRESS",
        type=_share_address,
        help="the share address of a collector to send every peer's log to: ...?token=TOKEN",
    )


def _share_address(address: str) -> str:
    """A share address as it was given. A refusal never quotes it: it holds a token."""
    try:
        judge.collector(address)
    except judge.NoAddress as refused:
        raise argparse.ArgumentTypeError(str(refused)) from None
    return address


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
        report = sweep(settings, args.runs, interrupts, lambda line: print(line, flush=True))
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


@contextlib.contextmanager
def _handling(take: Callable[[int], None]) -> Iterator[None]:
    """Hands each signal of `SIGNALS` to `take` for as long as it is entered.

    The default action ends the tool where it stands, with its peers still running. A signal
    the tool was started ignoring stays ignored.
    """
    handled = [signum for signum in SIGNALS if signal.getsignal(signum) is not signal.SIG_IGN]
    before = [signal.signal(signum, lambda taken, _frame: take(taken)) for signum in handled]
    try:
        yield
    finally:
        for signum, handler in zip(handled, before):
            signal.signal(signum, handler)


class _Parser(argparse.ArgumentParser):
    """A parser that quotes no token in what it refuses, wherever on the command line it stood."""

    def error(self, message: str) -> NoReturn:
        # A share address out of its place is quoted like any other word, and its token with it.
        super().error(
            re.sub(
                r"\S+",
                lambda word: "ADDRESS" if "token=" in unquote(word[0]) else word[0],
                message,
            )
        )
