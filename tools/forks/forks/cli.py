"""The command line: `forks report PATH...`, `forks serve --data DIR --token-file FILE` and
`forks share --to ADDRESS PATH...`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn, Optional, Sequence
from urllib.parse import unquote

from . import rule, share, text
from .collector import Collector
from .records import Counts
from .runs import InputError, find_runs, read_run
from .serve import TOKEN, Server


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs one command and returns the exit status."""
    parser = _Parser(
        prog="forks",
        description="Reads the trace records in the logs of a GroupV2 group's members "
        "and says whether they still hold one group.",
    )
    commands = parser.add_subparsers(metavar="COMMAND", required=True)
    _add_report(commands)
    _add_serve(commands)
    _add_share(commands)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (InputError, OSError) as error:
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


def _add_serve(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser(
        "serve",
        help="collect the logs members share, and report on them over HTTP",
        description="Keeps the logs members send it under a directory, answers each "
        "conversation's report as JSON, and serves a page that draws the reports at its own "
        "address. Every call must carry the token.",
    )
    command.add_argument(
        "--data", metavar="DIR", type=Path, required=True, help="the directory to keep the runs in"
    )
    command.add_argument(
        "--token-file",
        metavar="FILE",
        type=_token_in,
        required=True,
        dest="token",
        help="a file that holds the token: 16 to 128 characters of A-Z, a-z, 0-9, _ and -",
    )
    command.add_argument(
        "--bind", metavar="ADDRESS", default="127.0.0.1", help="the address to listen on"
    )
    command.add_argument(
        "--port", metavar="N", type=_port, default=8750, help="the port to listen on"
    )
    command.add_argument(
        "--max-bytes", metavar="N", type=int, help="the most bytes of logs to hold at a time"
    )
    command.set_defaults(run=_serve)


def _token_in(file: str) -> str:
    """The token a file holds. It is read here, and never from the command line itself."""
    try:
        token = Path(file).read_text().strip()
    except OSError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    if not TOKEN.fullmatch(token):
        raise argparse.ArgumentTypeError(f"{file} does not hold a token")
    return token


def _port(text: str) -> int:
    """A port a socket can listen on; at 0 the system picks one."""
    if not text.isdecimal() or int(text) > 65535:
        raise argparse.ArgumentTypeError(f"{text} is not a port")
    return int(text)


def _serve(args: argparse.Namespace) -> int:
    args.data.mkdir(parents=True, exist_ok=True)
    collector = Collector(args.data, args.max_bytes)
    with Server((args.bind, args.port), collector, args.token) as server:
        collector.say(f"serving {args.data} on {_address(*server.server_address[:2])}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def _address(host: str, port: int) -> str:
    """The address the collector's calls are made under."""
    return f"http://[{host}]:{port}/" if ":" in host else f"http://{host}:{port}/"


def _add_share(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser(
        "share",
        help="send the logs in some paths to a collector, once",
        description="Sends every run in the paths to the collector a share address names, each "
        "from where the collector's answer says it stopped, and exits. Exits 0 when the "
        "collector then holds every run whole, 1 when it does not, 2 when a path or the address "
        "cannot be used.",
    )
    command.add_argument(
        "--to",
        metavar="ADDRESS",
        type=_collector_at,
        required=True,
        help="the collector's share address, which ends in its token: ...?token=TOKEN",
    )
    command.add_argument(
        "paths", metavar="PATH", nargs="+", type=Path, help="a log, or a directory of logs"
    )
    command.set_defaults(run=_share)


def _collector_at(address: str) -> share.Address:
    """The collector a share address names. A refusal never quotes it: it holds the token."""
    try:
        return share.address_of(address)
    except share.NoAddress as refused:
        raise argparse.ArgumentTypeError(str(refused)) from None


def _share(args: argparse.Namespace) -> int:
    return share.send(args.to, args.paths, _print, _complain)


def _complain(problem: str) -> None:
    print(f"forks: {_tokenless(problem)}", file=sys.stderr)


class _Parser(argparse.ArgumentParser):
    """A parser that quotes no token in what it refuses, wherever on the command line it stood."""

    def error(self, message: str) -> NoReturn:
        super().error(_tokenless(message))


def _tokenless(said: str) -> str:
    """What is said of a command line, with `ADDRESS` for each word of it that holds `token=`.

    A share address out of its place is quoted like any other word, and its token with it.
    """
    return re.sub(r"\S+", lambda word: "ADDRESS" if "token=" in unquote(word[0]) else word[0], said)


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
