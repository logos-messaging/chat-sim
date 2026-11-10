#!/usr/bin/env python3
"""Says whether simulate.yml's main suite, and its long suite, are to run at logos-chat main's
tip, and why.

A point of main is `logos-chat/main/<revision>/<measurement>/point.json` on gh-pages: the main
suite's runs at a logos-chat commit, with chat-sim at the commit they measure (`simulate.py
measurement`). The tip is to run when it has no point at the current measurement, no Simulate
run of main at it is queued or in progress, and no run of main at it ended at a chat-sim that
holds the measurement; simulate.yml's run-name titles a run `Simulate <suite> at <revision>`,
then ` for logos-chat#<N>` when it is handed a pull request. A tip with a point at an older
measurement only runs again: the change to chat-sim is the reason.
A run of main handed a pull request holds the tip back neither going nor ended: it ran at the pull
request's head and writes no point, and its concurrency group is the pull request's, which a
dispatch at the tip does not cancel.
The long suite runs once each Sunday, UTC, at the tip: at the first hourly tick of the day that
finds no Simulate run of long handed no pull request created that day, going or ended, unless the
tip has its long run, `logos-chat/long/<revision>/<measurement>/long.json`, at the current
measurement, or a run of long is queued or in progress, or the runs created that day were not
listed. A run of one suite
never holds the other back: simulate.yml's concurrency group names the suite, so neither cancels
the other.
Prints, as lines for $GITHUB_OUTPUT, whether to dispatch main, the revision and whether to
dispatch long, and appends a sentence for each that says why to a summary.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Collection, Iterable, List, Optional, Sequence, Tuple

LOGOS_CHAT = "https://github.com/logos-messaging/logos-chat"
CHAT_SIM = "https://github.com/logos-messaging/chat-sim"
# The suite that measures main, the one that runs the held scenarios each Sunday, and the title
# simulate.yml's run-name gives a run.
SUITE = "main"
LONG = "long"
TITLE = "Simulate {suite} at {revision}"

_SHA = re.compile(r"[0-9a-f]{40}")
_POINT = re.compile(rf"logos-chat/{SUITE}/([0-9a-f]{{40}})/([0-9a-f]{{40}})/point\.json")
_TIME = "%Y-%m-%dT%H:%M:%SZ"
_SUNDAY = 6


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Says whether to dispatch and returns the exit status: 0, or 2 when its input is refused."""
    parser = argparse.ArgumentParser(
        prog="measure.py",
        description="Prints, as lines for $GITHUB_OUTPUT, whether simulate.yml's main suite is "
        "to run at logos-chat main's tip, the revision it runs at, and whether its long suite "
        "is, and appends a sentence for each that says why to --summary. The tip runs main when "
        "it has no point at --measurement, no run of main at it handed no pull request is going, "
        "and none ended at a chat-sim that holds --measurement. It runs long when --now is a "
        "Sunday in UTC, --created lists no run of long handed no pull request created that day, "
        "none is going, and the tip has no long run at --measurement.",
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
        help="the paths of the files under logos-chat/main/ and logos-chat/long/ on gh-pages, "
        "one a line",
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
        "--created",
        metavar="FILE",
        type=Path,
        help="the Simulate runs created on --now's day, every one, as a JSON list each with its "
        "displayTitle, its createdAt as 2026-11-08T00:17:03Z, and its url; without it, long does "
        "not run",
    )
    parser.add_argument(
        "--now",
        metavar="TIME",
        required=True,
        help="the tick's time in UTC, as 2026-11-08T00:17:00Z",
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
        help="the file to append the sentences to",
    )
    args = parser.parse_args(argv)
    for name in ("tip", "measurement"):
        if _SHA.fullmatch(getattr(args, name)) is None:
            parser.error(f"--{name}: {getattr(args, name)!r} is not a commit's 40 hex digits")
    try:
        now = datetime.strptime(args.now, _TIME)
    except ValueError:
        parser.error(f"--now: {args.now!r} is not of the form 2026-11-08T00:17:00Z")
    points, going = _lines(parser, "--points", args.points), _lines(parser, "--going", args.going)
    ended = _runs(parser, "--ended", args.ended, ("displayTitle", "headSha", "url"))
    created = None
    if args.created is not None:
        created = _runs(parser, "--created", args.created, ("displayTitle", "createdAt", "url"))
        for one in created:
            try:
                datetime.strptime(one["createdAt"], _TIME)
            except ValueError:
                parser.error(
                    f"--created: {args.created}: createdAt {one['createdAt']!r} is not a time"
                )

    def holds(sha: str) -> bool:
        ancestor = ["git", "-C", str(args.tree), "merge-base", "--is-ancestor", args.measurement]
        return subprocess.run([*ancestor, sha], capture_output=True).returncode == 0

    dispatch, why = decide(args.tip, args.measurement, points, going, ended, holds)
    long, why_long = decide_long(args.tip, args.measurement, now, points, going, created)
    with args.summary.open("a", encoding="utf-8") as summary:
        summary.write(f"{why}\n{why_long}\n")
    print(f"dispatch={str(dispatch).lower()}")
    print(f"revision={args.tip}")
    print(f"long={str(long).lower()}")
    return 0


def point(revision: str, measurement: str) -> str:
    """Where on gh-pages the point of `revision` at `measurement` is."""
    return f"logos-chat/{SUITE}/{revision}/{measurement}/point.json"


def long_run(revision: str, measurement: str) -> str:
    """Where on gh-pages the long run of `revision` at `measurement` is."""
    return f"logos-chat/{LONG}/{revision}/{measurement}/long.json"


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
    `measurement` never runs again; without one, it runs unless a run of main at it handed no
    pull request is going, or one ended at a chat-sim that holds `measurement`.
    """
    at = f"logos-chat main's tip {_commit(LOGOS_CHAT, tip)}"
    measured = _chat_sim(measurement)
    if point(tip, measurement) in points:
        return False, f"{at} has its point at {measured}: nothing is dispatched."
    # A dispatch cancels a run of main at the tip handed no pull request, the concurrency group
    # it joins. One handed a pull request writes no point, and its group is the pull request's.
    main_at_tip = re.compile(TITLE.format(suite=SUITE, revision=re.escape(tip)))
    if any(main_at_tip.fullmatch(one) for one in going):
        return False, (
            f"{at} has no point at {measured}, and a run of {SUITE} at it is queued or in "
            "progress: nothing is dispatched."
        )
    # Such a run wrote no point, and the next would fail where it failed, every hour: a person
    # looks first, and dispatches the tip once the cause is fixed.
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


def decide_long(
    tip: str,
    measurement: str,
    now: datetime,
    points: Collection[str],
    going: Iterable[str],
    created: Optional[Iterable[dict]],
) -> Tuple[bool, str]:
    """Whether the long suite is to run at `tip` at `now`, the tick's time in UTC, and a sentence
    that says why.

    `points` are paths on gh-pages, `going` the titles of the Simulate runs queued or in
    progress, and `created` the Simulate runs created on `now`'s day, each its `displayTitle`,
    `createdAt` and `url`, or `None` where they were not listed; one of another day is left out.
    The long suite runs on a Sunday alone, and once: a run of it handed no pull request created
    that day, going or ended, however it ended, holds it back until the next, and so do runs not
    listed. Nor does it run when the tip has its long run at `measurement`, nothing having moved
    since, or a run of it, at any revision or pull request, is going.
    """
    at = f"logos-chat main's tip {_commit(LOGOS_CHAT, tip)}"
    measured = _chat_sim(measurement)
    if now.weekday() != _SUNDAY:
        return False, (
            f"The {LONG} suite runs on Sundays, UTC, and {now:%Y-%m-%d} is a {now:%A}: nothing is "
            "dispatched."
        )
    if created is None:
        return False, (
            f"The Simulate runs created today were not listed, so whether the {LONG} suite ran "
            "today is not known: nothing is dispatched."
        )
    # One handed a pull request ran at its head, which the site keeps no long run of.
    of_main = re.compile(TITLE.format(suite=LONG, revision=r"\S+"))
    today = [
        one["url"]
        for one in created
        if of_main.fullmatch(one["displayTitle"])
        and datetime.strptime(one["createdAt"], _TIME).date() == now.date()
    ]
    if today:
        return False, (
            f"A run of the {LONG} suite was created today, {today[0]}: nothing is dispatched "
            "until next Sunday."
        )
    if long_run(tip, measurement) in points:
        return False, f"{at} has its {LONG} run at {measured}: nothing is dispatched."
    long_at = re.compile(TITLE.format(suite=LONG, revision=r"\S+") + r"( for logos-chat#.*)?")
    if any(long_at.fullmatch(one) for one in going):
        return False, f"A run of the {LONG} suite is queued or in progress: nothing is dispatched."
    return True, (
        f"It is Sunday, and {at} has no {LONG} run at {measured}: the {LONG} suite is to run at it."
    )


def _lines(parser: argparse.ArgumentParser, flag: str, file: Path) -> List[str]:
    try:
        return file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        parser.error(f"{flag}: {file}: {error}")


def _runs(
    parser: argparse.ArgumentParser, flag: str, file: Path, fields: Sequence[str]
) -> List[dict]:
    try:
        runs = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        parser.error(f"{flag}: {file}: {error}")
    if not isinstance(runs, list) or not all(
        isinstance(one, dict) and all(isinstance(one.get(key), str) for key in fields)
        for one in runs
    ):
        parser.error(f"{flag}: {file}: not a list of runs, each its {', '.join(fields)}")
    return runs


def _chat_sim(sha: str) -> str:
    return f"chat-sim {_commit(CHAT_SIM, sha)}"


def _commit(repository: str, sha: str) -> str:
    return f"[`{sha[:7]}`]({repository}/commit/{sha})"


if __name__ == "__main__":
    raise SystemExit(main())
