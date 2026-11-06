#!/usr/bin/env python3
"""Plans the jobs of simulate.yml and writes the comment on what they came to.

simulate.yml runs chat-sim at a logos-chat revision it is handed, and at a base revision too when
it is handed one. `plan` checks what it was handed and prints the run jobs, and `comment` writes
in Markdown a row for each scenario of the suite: what its runs came to at the revision, and what
`tools/sim compare` said of them against the base's, or against what `tools/sim/expected` holds
when there is no base.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# The quick suite, at most 20 minutes for a logos-chat pull request; the long one is every
# scenario CI holds to what is expected, and the two of fifty epochs.
QUICK = (
    "50-epochs",
    "50-epochs-waku",
    "grow-waku-loss-0.5",
    "grow-waku-loss-1",
    "grow-waku-loss-5",
)
FIFTY_EPOCHS = ("50-epochs", "50-epochs-waku")
SUITES = ("quick", "long")
LOGOS_CHAT = "https://github.com/logos-messaging/logos-chat"

_SHA = re.compile(r"[0-9a-f]{40}")
_PR = re.compile(r"[1-9][0-9]*")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs one command and returns the exit status: 0, or 2 when its input is refused."""
    parser = argparse.ArgumentParser(
        prog="simulate.py",
        description="Plans the jobs of simulate.yml and writes the comment on what they came to.",
    )
    commands = parser.add_subparsers(metavar="COMMAND", required=True)
    plan = commands.add_parser(
        "plan",
        help="check what simulate.yml was handed and print its run jobs",
        description="Checks what simulate.yml was handed and prints, as lines for "
        "$GITHUB_OUTPUT, what it was handed, the run jobs as a JSON array and the suite's "
        "scenarios.",
    )
    _add_handed(plan)
    plan.set_defaults(command=_plan)
    comment = commands.add_parser(
        "comment",
        help="write the comment on what the run jobs came to",
        description="Writes, in Markdown, a row for each scenario of the suite from the "
        "outcomes.json the run jobs uploaded and the compare.txt written beside it, and the "
        "output of each compare.",
    )
    comment.add_argument(
        "runs",
        metavar="RUNS",
        type=Path,
        help="what the run jobs at the revision uploaded, a directory for each scenario, with "
        "the output of its compare in compare.txt",
    )
    _add_handed(comment)
    comment.add_argument(
        "--base-runs",
        metavar="DIR",
        type=Path,
        help="what the run jobs at the base uploaded, given with --base and only with it",
    )
    comment.add_argument("--chat-sim", metavar="SHA", required=True, help="chat-sim's commit")
    comment.add_argument("--run-url", metavar="URL", required=True, help="the workflow run's page")
    comment.set_defaults(command=_comment)
    args = parser.parse_args(argv)
    return args.command(parser, args)


def scenarios(suite: str, expected: Path) -> List[Tuple[str, int]]:
    """The scenarios of `suite` and how many runs each sweeps: as many as `expected` holds for
    it, and one when it holds none."""
    held = sorted(path.stem for path in expected.glob("*.json"))
    names = QUICK if suite == "quick" else FIFTY_EPOCHS + tuple(held)
    return [(name, _held_runs(expected / f"{name}.json") or 1) for name in names]


def jobs(suite: str, revision: str, base: str, swept: Sequence[Tuple[str, int]]) -> List[dict]:
    """The run jobs: the quick suite in one job a side, the long one a job a scenario a side."""
    sides = [("revision", revision)] + ([("base", base)] if base else [])
    chunks = [swept] if suite == "quick" else [[one] for one in swept]
    return [
        {
            "side": side,
            "revision": sha,
            "scenarios": " ".join(f"{name}={runs}" for name, runs in chunk),
            "name": f"{side} {sha[:7]}: "
            + (f"the {suite} suite" if len(chunk) > 1 else chunk[0][0]),
        }
        for side, sha in sides
        for chunk in chunks
    ]


def render(
    swept: Sequence[Tuple[str, int]], runs: Path, base_runs: Optional[Path], facts: dict
) -> str:
    """The comment: a head, a row for each scenario, and the output of each compare.

    A run job that could not build chat-sim leaves `unbuilt` in each of its scenarios'
    directories: with it at the revision no scenario ran, and the comment says so instead.
    """
    against = (
        f"the base {_commit(facts['base'])}"
        if facts["base"]
        else "what chat-sim holds in `tools/sim/expected`"
    )
    pr = f" ([#{facts['pr']}]({LOGOS_CHAT}/pull/{facts['pr']}))" if facts["pr"] else ""
    head = (
        f"**chat-sim, the {facts['suite']} suite** at logos-chat {_commit(facts['revision'])}"
        f"{pr}, against {against}"
    )
    foot = f"chat-sim `{facts['chat_sim'][:7]}` · [the run]({facts['run_url']})"
    if _unbuilt(runs):
        said = (
            "chat-sim does not build at this revision, so no scenario ran: the build's errors "
            "are in the run."
        )
        return "".join(f"{line}\n" for line in (head, "", said, "", foot))
    lines = [
        head,
        "",
        "| Scenario | Runs | Came to | Against the base |"
        if facts["base"]
        else "| Scenario | Runs | Came to | Against what is held |",
        "|---|---|---|---|",
    ]
    missing = "nothing held"
    if facts["base"]:
        missing = "no outcome at the base"
        if _unbuilt(base_runs):
            missing = "chat-sim does not build at the base"
    folded = []
    for name, count in swept:
        now = _outcomes(runs / name / "outcomes.json")
        compared = _text(runs / name / "compare.txt")
        said = _against(now, compared, missing)
        lines.append(f"| `{name}` | {count} | {_came_to(now)} | {said} |")
        if compared is not None:
            folded.append((name, compared))
    lines += ["", foot]
    for name, text in folded:
        lines += ["", f"<details><summary><code>{name}</code></summary>", "", "```"]
        lines += [text.rstrip("\n"), "```", "", "</details>"]
    return "".join(f"{line}\n" for line in lines)


def _add_handed(command: argparse.ArgumentParser) -> None:
    command.add_argument("--suite", required=True, choices=SUITES, help="quick or long")
    command.add_argument("--revision", required=True, help="the logos-chat commit to run")
    command.add_argument("--base", default="", help="the logos-chat commit to compare with")
    command.add_argument("--pr", default="", help="the logos-chat pull request, or nothing")
    command.add_argument(
        "--expected",
        metavar="DIR",
        type=Path,
        required=True,
        help="tools/sim/expected: the outcomes chat-sim holds, a file a scenario",
    )


def _check_handed(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if _SHA.fullmatch(args.revision) is None:
        parser.error(f"--revision: {args.revision!r} is not a commit's 40 hex digits")
    if args.base and _SHA.fullmatch(args.base) is None:
        parser.error(f"--base: {args.base!r} is not a commit's 40 hex digits")
    if args.base == args.revision:
        parser.error("--base: the revision itself")
    if args.pr and _PR.fullmatch(args.pr) is None:
        parser.error(f"--pr: {args.pr!r} is not the number of a pull request")
    if not args.expected.is_dir():
        parser.error(f"--expected: no such directory: {args.expected}")


def _plan(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    _check_handed(parser, args)
    swept = scenarios(args.suite, args.expected)
    for name in ("suite", "revision", "base", "pr"):
        print(f"{name}={getattr(args, name)}")
    print(f"runs={json.dumps(jobs(args.suite, args.revision, args.base, swept))}")
    print(f"scenarios={' '.join(name for name, _ in swept)}")
    return 0


def _comment(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    _check_handed(parser, args)
    if (args.base_runs is None) != (not args.base):
        parser.error("--base-runs: given with --base, and only with it")
    facts = {
        "suite": args.suite,
        "revision": args.revision,
        "base": args.base,
        "pr": args.pr,
        "chat_sim": args.chat_sim,
        "run_url": args.run_url,
    }
    swept = scenarios(args.suite, args.expected)
    sys.stdout.write(render(swept, args.runs, args.base_runs, facts))
    return 0


def _held_runs(file: Path) -> int:
    held = _outcomes(file)
    return 0 if held is None else len(held)


def _outcomes(file: Path) -> Optional[List[dict]]:
    """The outcomes `tools/sim compare --write` writes, or `None` when `file` holds none."""
    try:
        outcomes = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(outcomes, list) or not all(isinstance(one, dict) for one in outcomes):
        return None
    return outcomes


def _came_to(now: Optional[List[dict]]) -> str:
    if now is None:
        return "no outcome"
    return ", ".join(one.get("verdict") or "no verdict" for one in now)


def _against(now: Optional[List[dict]], compared: Optional[str], missing: str) -> str:
    """The count compare ends its output with, or why there is no compare."""
    if now is None:
        return "-"
    if compared is None or not compared.strip():
        return missing
    return compared.strip().splitlines()[-1]


def _unbuilt(runs: Optional[Path]) -> bool:
    return runs is not None and any(runs.glob("*/unbuilt"))


def _text(file: Path) -> Optional[str]:
    try:
        return file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _commit(sha: str) -> str:
    return f"[`{sha[:7]}`]({LOGOS_CHAT}/commit/{sha})"


if __name__ == "__main__":
    raise SystemExit(main())
