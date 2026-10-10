"""The command line: `sim --chat-cli PATH --peers N --out DIR`."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import table
from .peers import Watch
from .run import TIMERS, Settings, run

SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs one group and returns the exit status: 0 when every member read the post.

    A run a signal interrupted returns 128 and the signal's number.
    """
    defaults = Settings(chat_cli=Path(), peers=0, out=Path())
    parser = argparse.ArgumentParser(
        prog="sim",
        description="Starts headless chat-cli peers on a delivery network of their own, forms "
        "one group of them and has one member post. Exits 0 when every member read the post, "
        "1 when the run stopped before that, 2 when its command line is refused, and 128 and "
        "the number of the signal that interrupted it.",
    )
    parser.add_argument(
        "--chat-cli", metavar="PATH", type=Path, required=True, help="the chat-cli binary to run"
    )
    parser.add_argument(
        "--peers", metavar="N", type=int, required=True, help="how many peers, 2 or more"
    )
    parser.add_argument(
        "--out", metavar="DIR", type=Path, required=True, help="the run's directory, which it makes"
    )
    parser.add_argument(
        "--batch",
        metavar="N",
        type=int,
        default=defaults.batch,
        help="how many members the creator adds at a time (default %(default)s)",
    )
    parser.add_argument(
        "--timers",
        choices=TIMERS,
        default=defaults.timers,
        help="the group timers every peer runs with (default %(default)s)",
    )
    parser.add_argument(
        "--max-message-kib",
        metavar="KIB",
        type=int,
        default=defaults.max_message_kib,
        help="the largest message a node takes (default %(default)s)",
    )
    parser.add_argument(
        "--budget",
        metavar="SECONDS",
        type=float,
        default=defaults.budget,
        help="how long each step may take (default %(default)s)",
    )
    args = parser.parse_args(argv)
    if args.peers < 2:
        parser.error("--peers: a group to post in needs 2 peers or more")
    if args.batch < 1:
        parser.error("--batch: a batch holds 1 member or more")
    if not args.chat_cli.is_file():
        parser.error(f"--chat-cli: no such file: {args.chat_cli}")
    if args.out.exists():
        parser.error(f"--out: {args.out} exists")

    watch = Watch()
    # The default action ends the tool where it stands, with its peers still running. A signal
    # the tool was started ignoring stays ignored.
    handled = [signum for signum in SIGNALS if signal.getsignal(signum) is not signal.SIG_IGN]
    before = [
        signal.signal(signum, lambda taken, _frame: watch.interrupt(taken)) for signum in handled
    ]
    try:
        report = run(
            Settings(
                chat_cli=args.chat_cli.resolve(),
                peers=args.peers,
                out=args.out,
                batch=args.batch,
                timers=args.timers,
                max_message_kib=args.max_message_kib,
                budget=args.budget,
            ),
            watch,
        )
        text = table.render(report)
        (args.out / "run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (args.out / "run.txt").write_text(text, encoding="utf-8")
        sys.stdout.write(text)
    finally:
        for signum, handler in zip(handled, before):
            signal.signal(signum, handler)
    if watch.signal is not None:
        return 128 + watch.signal
    return 0 if report["ok"] else 1
