#!/usr/bin/env python3
"""Says whether simulate.yml's main suite is to run at logos-chat main's tip, and why.

A point of main is `logos-chat/main/<revision>/<measurement>/point.json` on gh-pages: the main
suite's runs at a logos-chat commit, with chat-sim at the commit they measure (`simulate.py
measurement`). The tip is to run when it has no point at the current measurement, no Simulate
run at it, of any suite, is queued or in progress, and no run of main at it ended at a chat-sim
that holds the measurement; simulate.yml's run-name titles a run `Simulate <suite> at
<revision>`, then ` for logos-chat#<N>` when it is handed a pull request. A tip with a point at an
older measurement only runs again: the change to chat-sim is the reason.
A run handed a pull request holds the tip back neither going nor ended: it ran at the pull
request's head and writes no point, and its concurrency group is the pull request's, which a
dispatch at the tip does not cancel.
Prints, as lines for $GITHUB_OUTPUT, whether to dispatch and the revision, and appends a sentence
that says why to a summary.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Callable, Collection, Iterable, List, Optional, Sequence, Tuple

LOGOS_CHAT = "https://github.com/logos-messaging/logos-chat"
CHAT_SIM = "https://github.com/logos-messaging/chat-sim"
# The suite that measures main, and the title simulate.yml's run-name gives a run.
SUITE = "main"
TITLE = "Simulate {suite} at {revision}"

_SHA = re.compile(r"[0-9a-f]{40}")
_POINT = re.compile(rf"logos-chat/{SUITE}/([0-9a-f]{{40}})/([0-9a-f]{{40}})/point\.json")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Says whether to dispatch and returns the exit status: 0, or 2 when its input is refused."""
    parser = argparse.ArgumentParser(
        prog="measure.py",
        description="Prints, as lines for $GITHUB_OUTPUT, whether simulate.yml's main suite is "
        "to run at logos-chat main's tip and the revision it runs at, and appends a sentence "
        "that says why to --summary. The tip runs when it has no point at --measurement, no run "
        "at it handed no pull request is going, and no such run of main ended at a chat-sim that "
        "holds --measurement.",
    )
    parser.add_argument(
        "--tip", metavar="SHA", required=True, help="logos-chat main's tip, its 40 hex digits"
    )
    parser.add_argument(
        "--measurement",
        metavar="SHA",
        required=True,
        help="the chat-sim commit a run measures, as simulate.py measurement names it",
    )
    parser.add_argument(
        "--points",
        metavar="FILE",
        type=Path,
        required=True,
        help="the paths of the files under logos-chat/main/ on gh-pages, one a line",
    )
    parser.add_argument(
        "--going",
        metavar="FILE",
        type=Path,
        required=True,
        help="the titles of the Simulate runs queued or in progress, one a line",
    )
    parser.add_argument(
        "--ended",
        metavar="FILE",
        type=Path,
        required=True,
        help="the Simulate runs of the default branch that ended, as gh run list --json "
        "displayTitle,headSha,url writes",
    )
    parser.add_argument(
        "--tree",
        metavar="DIR",
        type=Path,
        default=Path(),
        help="chat-sim's checkout, whose history says which runs ran with --measurement",
    )
    parser.add_argument(
        "--summary",
        metavar="FILE",
        type=Path,
        required=True,
        help="the file to append the sentence to",
    )
    args = parser.parse_args(argv)
    for name in ("tip", "measurement"):
        if _SHA.fullmatch(getattr(args, name)) is None:
            parser.error(f"--{name}: {getattr(args, name)!r} is not a commit's 40 hex digits")
    points, going = _lines(parser, "--points", args.points), _lines(parser, "--going", args.going)
    ended = _ended(parser, args.ended)

    def holds(sha: str) -> bool:
        ancestor = ["git", "-C", str(args.tree), "merge-base", "--is-ancestor", args.measurement]
        return subprocess.run([*ancestor, sha], capture_output=True).returncode == 0

    dispatch, why = decide(args.tip, args.measurement, points, going, ended, holds)
    with args.summary.open("a", encoding="utf-8") as summary:
        summary.write(f"{why}\n")
    print(f"dispatch={str(dispatch).lower()}")
    print(f"revision={args.tip}")
    return 0


def point(revision: str, measurement: str) -> str:
    """Where on gh-pages the point of `revision` at `measurement` is."""
    return f"logos-chat/{SUITE}/{revision}/{measurement}/point.json"


def decide(
    tip: str,
    measurement: str,
    points: Collection[str],
    going: Iterable[str],
    ended: Iterable[dict] = (),
    holds: Callable[[str], bool] = lambda sha: False,
) -> Tuple[bool, str]:
    """Whether the main suite is to run at `tip`, and a sentence that says why.

    `points` are paths on gh-pages, `going` the titles of the Simulate runs queued or in
    progress, `ended` the Simulate runs that ended, each its `displayTitle`, `headSha` and `url`,
    and `holds` whether a chat-sim commit holds `measurement`. A tip with its point at
    `measurement` never runs again; without one, it runs unless a run at it handed no pull
    request, of any suite, is going, or such a run of main ended at a chat-sim that holds
    `measurement`.
    """
    at = f"logos-chat main's tip {_commit(LOGOS_CHAT, tip)}"
    measured = _chat_sim(measurement)
    if point(tip, measurement) in points:
        return False, f"{at} has its point at {measured}: nothing is dispatched."
    # A dispatch cancels any run at the tip handed no pull request, the concurrency group it
    # joins. One handed a pull request writes no point, and its group is the pull request's.
    at_tip = re.compile(TITLE.format(suite=r"\S+", revision=re.escape(tip)))
    if any(at_tip.fullmatch(one) for one in going):
        return False, (
            f"{at} has no point at {measured}, and a run at it is queued or in progress: "
            "nothing is dispatched."
        )
    # Such a run wrote no point, and the next would fail where it failed, every hour: a person
    # looks first, and dispatches the tip once the cause is fixed.
    main_at_tip = re.compile(TITLE.format(suite=SUITE, revision=re.escape(tip)))
    tried = [
        one["url"]
        for one in ended
        if main_at_tip.fullmatch(one["displayTitle"]) and holds(one["headSha"])
    ]
    if tried:
        return False, (
            f"{at} has no point at {measured}, though its {SUITE} suite ran at it at that "
            f"measurement and ended: {tried[0]}. Nothing is dispatched until a person dispatches "
            "it again."
        )
    older = sorted(
        found[2] for found in map(_POINT.fullmatch, points) if found and found[1] == tip
    )
    if older:
        return True, (
            f"{at} has a point at {', '.join(map(_chat_sim, older))} only, and runs measure "
            f"{measured} now: the {SUITE} suite is to run at it again."
        )
    return True, (
        f"{at} has no point at {measured} or at any other: the {SUITE} suite is to run at it."
    )


def _lines(parser: argparse.ArgumentParser, flag: str, file: Path) -> List[str]:
    try:
        return file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        parser.error(f"{flag}: {file}: {error}")


def _ended(parser: argparse.ArgumentParser, file: Path) -> List[dict]:
    try:
        ended = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        parser.error(f"--ended: {file}: {error}")
    fields = ("displayTitle", "headSha", "url")
    if not isinstance(ended, list) or not all(
        isinstance(one, dict) and all(isinstance(one.get(key), str) for key in fields)
        for one in ended
    ):
        parser.error(f"--ended: {file}: not a list of runs, each its {', '.join(fields)}")
    return ended


def _chat_sim(sha: str) -> str:
    return f"chat-sim {_commit(CHAT_SIM, sha)}"


def _commit(repository: str, sha: str) -> str:
    return f"[`{sha[:7]}`]({repository}/commit/{sha})"


if __name__ == "__main__":
    raise SystemExit(main())
