"""The command line: `sim run SCENARIO` and `sim sweep SCENARIO --runs K`."""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import signal
import sys
from pathlib import Path
from typing import Callable, Iterator, NoReturn, Optional, Sequence
from urllib.parse import unquote

from . import judge, scenario, table
from .peers import Watch
from .run import NoDirectory, Settings, run
from .sweep import Interrupts, sweep

SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs one command and returns the exit status.

    0 when every step was done and the report of the logs is as the scenario expects, in
    every run of a sweep. A command a signal interrupted returns 128 and the signal's number.
    """
    parser = _Parser(
        prog="sim",
        description="Starts headless chat-cli peers on a delivery network of their own, has "
        "them do the steps of a scenario, and holds the fork report of their logs against what "
        "the scenario expects.",
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
    args = parser.parse_args(argv)

    if not args.chat_cli.is_file():
        parser.error(f"--chat-cli: no such file: {args.chat_cli}")
    if not os.access(args.chat_cli, os.X_OK):
        parser.error(f"--chat-cli: not a file that can be run: {args.chat_cli}")
    if args.out.exists():
        parser.error(f"--out: {args.out} exists")
    if getattr(args, "runs", 1) < 1:
        parser.error("--runs: a sweep is 1 run or more")
    try:
        settings = Settings(
            chat_cli=args.chat_cli.resolve(),
            scenario=scenario.read(args.scenario),
            out=args.out,
            share=args.share,
        )
    except scenario.Refused as refused:
        parser.error(str(refused))
    last = settings.scenario.of_run(getattr(args, "runs", 1)).network
    if last is not None and last.seed > scenario.MOST_SEED:
        parser.error(
            f"--runs: run {args.runs} would have the seed {last.seed}, "
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
    command.add_argument(
        "--chat-cli", metavar="PATH", type=Path, required=True, help="the chat-cli binary to run"
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
