#!/usr/bin/env python3
"""Plans the jobs of simulate.yml and writes the comment on what they came to.

simulate.yml runs chat-sim at a logos-chat revision it is handed, and at a base revision too when
it is handed one, in one of three suites. `pr` and `main` run the goal, a group grown toward epoch
50 by up to three members of any branch a round, on GroupV2 and on GroupV3 at rates of loss: `pr`
seeds 0 to 2 of the three lossy rates, a job a run, at the revision and at the base; `main` seeds 0
to 19 of the four rates, two jobs of ten a rate, and no base. `long` runs the two scenarios of
fifty epochs and every other one `tools/sim/expected` holds but the goal's, a job a scenario a
side. `plan` checks what it was handed and prints the run jobs: for `pr` none at the base when
main's point at the base at the current measurement is on gh-pages with an outcome at each run the
base's jobs would do, whose runs stand in for the base's. `spread` names main's point at the base,
or else the nearest before it, that can be read, whose runs the head's are placed among. `comment`
writes the comment in Markdown: for `pr` and `main` from the matrix `tools/sim matrix` wrote of the
runs, each group's apart, for `pr` after main's runs of the two groups side by side, and for `long`
a row for each scenario with what `tools/sim compare` said of its runs against the base's, or
against what `tools/sim/expected` holds when there is no base. `measurement` prints the chat-sim
commit a run measures. `running` writes the update that moves a check run on logos-chat to in
progress while the runs go, and `check` the one that completes it with the groups' verdicts; `post`
says where the comment goes on a logos-chat pull request, an edit of the App's comment there or a
new one, and writes it for GitHub's API.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Collection, Dict, List, Optional, Sequence, Tuple

import measure

# The goal's scenario of each group at a rate of loss in percent, by the group's name in a
# scenario: two or more adders a round can commit for one epoch, which a GroupV3 group needs to
# fork. Its files set no seed, so that run k of a sweep has seed k - 1.
GOALS = {"v2": "grow-waku-loss-{}", "v3": "grow-waku-loss-{}-groupv3"}
# What the comment calls each group.
GROUPS = {"v2": "GroupV2", "v3": "GroupV3"}
# GroupV3 forks at each of `pr`'s rates on seeds 0 to 2, where at 0.1% two of them stay whole.
RATES = {"pr": ("0.5", "1", "5"), "main": ("0", "0.5", "1", "5")}
# How many seeds of each rate a suite runs, from seed 0, and how many of them a job does.
SEEDS = {"pr": 3, "main": 20}
A_JOB = {"pr": 1, "main": 10}
FIFTY_EPOCHS = ("50-epochs", "50-epochs-waku")
SUITES = ("pr", "main", "long")
LOGOS_CHAT = "https://github.com/logos-messaging/logos-chat"
CHAT_SIM = "https://github.com/logos-messaging/chat-sim"
# What a run measures: a commit under these paths can move how a run comes out, where one to the
# guide or to the pages cannot. The build reads the files at the root besides crates/, and
# tools/sim judges each run's logs with tools/forks.
MEASURED = (
    "crates/",
    "Cargo.toml",
    "Cargo.lock",
    ".cargo/",
    "rust-toolchain.toml",
    "flake.nix",
    "flake.lock",
    "tools/forks/forks/",
    "tools/sim/sim/",
    "tools/sim/scenarios/",
)
# What a matrix of `tools/sim matrix` holds.
MATRIX = {"head", "base", "base_point", "cells", "counts", "verdict", "groups", "summary", "spread"}

_SHA = re.compile(r"[0-9a-f]{40}")
_PR = re.compile(r"[1-9][0-9]*")
_CHECK_RUN = re.compile(r"[1-9][0-9]*")
# logos-chat in GitHub's API, where the comment and the check are.
ON_LOGOS_CHAT = "repos/logos-messaging/logos-chat"
# The check's conclusion of each verdict: success where the head's runs stand higher and none
# lower, failure where two or more stand lower and none higher, and neutral where they say neither.
CONCLUSIONS = {
    "better": "success",
    "worse": "failure",
    "same": "neutral",
    "hint": "neutral",
    "mixed": "neutral",
    "re-rolled": "neutral",
    "no-base": "neutral",
    "unbuilt": "neutral",
    "no-outcome": "neutral",
}
# The verdicts that fail the check once it is required: what went wrong at the head.
_REQUIRED = ("unbuilt", "no-outcome")
# An address a Markdown link can hold as it is.
_URL = re.compile(r"https?://[^\s()<>]+")
# What the verdict line says of a revision chat-sim does not build at.
_UNBUILT = "chat-sim does not build against this commit"

# The title of each verdict but `no-base`, from the counts of the matrix's cells.
_TITLES = {
    "same": "same as its base, {c[compared]} of {c[cells]} runs",
    "better": "better in {c[better]} of {c[cells]} runs",
    "worse": "worse in {c[worse]} of {c[cells]} runs",
    "hint": "worse in 1 of {c[cells]} runs, a hint",
    "mixed": "mixed: {c[better]} better, {c[worse]} worse of {c[cells]}",
    "re-rolled": "the draws changed, no comparison",
    "unbuilt": "does not build against this commit",
    "no-outcome": "no outcome in {c[no-outcome]} of {c[cells]} runs",
}
# What the title of `re-rolled` adds where the matrix places the head's runs among main's.
_BELOW = "; {c[below]} of {c[cells]} below main's worst seed"
_ENDED = (
    "whole: one group, every member that joined at its tip; behind: one group, some members "
    "behind; stalled: one group that stopped growing short of 50; split: it forked"
)
# Of the members a run seated, those `compare` counts off the trunk at the end.
_OFF_TRUNK = "behind, on another line, or never joined"
_PR_FOOT = (
    f"A cell: how the run ended ({_ENDED}), at the epoch of its first fork, or else of the trunk "
    "(50 is the goal) · the lines and the members off the trunk at the end, of the members "
    f"seated, where there are any ({_OFF_TRUNK}), and how many of those never joined, where any "
    "did · where it is better or worse, what the base's run had of the first of these that moved "
    "· the change against the merge base at the same seed (changed: the run differs but is no "
    "better or worse; re-rolled: the run drew other ids than the base's, so the two are not "
    "compared). One cell moving can be the loss pattern shifting under your change; main's 20 "
    "seeds after the merge are the measurement. This check blocks nothing yet. Run a cell again "
    "with the command in its fold."
)
_SIDES_FOOT = (
    "A row: main's runs of a rate, a seed each, GroupV2's · GroupV3's: how many of those with an "
    "outcome split; the epoch of the first fork of the runs that split, from the lowest to the "
    "highest; and the median of the lines off the trunk at the end, the median of the members "
    f"off it ({_OFF_TRUNK}) out of the median of the members seated, and the median of those "
    "that never joined. A seed fixes the numbers each member's frames are drawn against, and the "
    "groups send other frames, so seed 0 of one group is no twin of seed 0 of the other: the "
    "groups compare by their spreads."
)
_NO_SIDES = [
    "No point of main at this chat-sim's measurement that can be read is among the base's hundred "
    "newest commits, so main's runs of the two groups are not set side by side."
]
_OFF = {"lines_off_trunk": "line", "members_off_trunk": "member"}
_MAIN_FOOT = (
    f"A row: the runs of a group at a rate of loss, a seed each, by how each ended ({_ENDED}); "
    "how many epochs the group stayed one group, from the lowest seed to the highest (50 is the "
    "goal); the epoch of the first fork of the runs that split, from the lowest to the highest; "
    "and the median of the lines off the trunk at the end, the median of the members off it "
    f"({_OFF_TRUNK}) out of the median of the members seated, and the median of those that "
    "never joined."
)


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
        "$GITHUB_OUTPUT, what it was handed, the run jobs as a JSON array, the suite's "
        "scenarios, and where on gh-pages the point of main is whose runs stand in for the "
        "base's: for pr, main's point at the base at --measurement when --points lists it and "
        "its point.json on --pages has an outcome at each run the base's jobs would do, and "
        "then no run job is at the base. A point that has none at one of them, or that cannot be "
        "read, stands in for nothing, and a warning on standard error says why.",
    )
    _add_handed(plan)
    plan.add_argument(
        "--check-run",
        default="",
        help="the id of the check run on logos-chat the run completes, or nothing: for pr, "
        "given with --pr, and only then",
    )
    plan.add_argument(
        "--measurement",
        metavar="SHA",
        help="the chat-sim commit a run measures, given with --points and --pages, and only "
        "with them",
    )
    plan.add_argument(
        "--points",
        metavar="FILE",
        type=Path,
        help="the paths of the files under logos-chat/main/ on gh-pages, one a line",
    )
    plan.add_argument(
        "--pages",
        metavar="REF",
        help="gh-pages, as --tree names it, whose files --points lists: main's point at the base "
        "is read from it",
    )
    plan.add_argument(
        "--tree", metavar="DIR", type=Path, default=Path(), help="the checkout (default: .)"
    )
    plan.set_defaults(command=_plan)
    spread = commands.add_parser(
        "spread",
        help="name the point of main nearest before a base",
        description="Prints, as a line for $GITHUB_OUTPUT, where on gh-pages the point at "
        "--measurement of the newest commit of --history with one is, or nothing when none "
        "has one. A point on --pages that cannot be read is passed over, and a warning on "
        "standard error says why.",
    )
    spread.add_argument(
        "--measurement", metavar="SHA", required=True, help="the chat-sim commit a run measures"
    )
    spread.add_argument(
        "--points",
        metavar="FILE",
        type=Path,
        required=True,
        help="the paths of the files under logos-chat/main/ on gh-pages, one a line",
    )
    spread.add_argument(
        "--history",
        metavar="FILE",
        type=Path,
        required=True,
        help="the base's history, newest first, as logos-chat's commits API lists it from the "
        "base",
    )
    spread.add_argument(
        "--pages",
        metavar="REF",
        required=True,
        help="gh-pages, as --tree names it, whose files --points lists: the point named is read "
        "from it",
    )
    spread.add_argument(
        "--tree", metavar="DIR", type=Path, default=Path(), help="the checkout (default: .)"
    )
    spread.set_defaults(command=_spread)
    comment = commands.add_parser(
        "comment",
        help="write the comment on what the run jobs came to",
        description="Writes the comment in Markdown. For pr and main it is drawn from --matrix: "
        "for pr the verdict of each group, main's runs of the two groups side by side a row a "
        "rate, a row a group's rate and a cell a seed, and a fold for each cell with its runs and "
        "the command that runs it again; for main a row a group's rate with how its runs ended "
        "and forked. For long it is a row for each scenario from the "
        "outcomes.json the run jobs uploaded under RUNS and the compare.txt written beside it, "
        "and the output of each compare.",
    )
    comment.add_argument(
        "runs",
        metavar="RUNS",
        type=Path,
        nargs="?",
        help="for long, what the run jobs at the revision uploaded, a directory for each "
        "scenario at any depth, with the output of its compare in compare.txt",
    )
    _add_handed(comment)
    comment.add_argument(
        "--base-runs",
        metavar="DIR",
        type=Path,
        help="for long, what the run jobs at the base uploaded, given with --base and only "
        "with it",
    )
    comment.add_argument(
        "--matrix",
        metavar="FILE",
        type=Path,
        help="for pr and main, the matrix tools/sim matrix wrote of the runs",
    )
    comment.add_argument("--chat-sim", metavar="SHA", required=True, help="chat-sim's commit")
    comment.add_argument("--run-url", metavar="URL", required=True, help="the workflow run's page")
    comment.add_argument("--report-url", metavar="URL", help="the page of the run's report")
    comment.set_defaults(command=_comment)
    measured = commands.add_parser(
        "measurement",
        help="print the chat-sim commit a run measures",
        description="Prints, as a line for $GITHUB_OUTPUT, the newest commit of the tree that "
        "touches what a run measures. Refuses a shallow clone.",
    )
    measured.add_argument(
        "--tree", metavar="DIR", type=Path, default=Path(), help="the checkout (default: .)"
    )
    measured.set_defaults(command=_measurement)
    running = commands.add_parser(
        "running",
        help="print the update that moves a check run to in progress",
        description="Prints, as JSON for GitHub's API, the update of a check run on logos-chat "
        "while the run jobs go: in progress, --run-url as its details, and as its title how "
        "many runs the jobs of --asked do at the revision.",
    )
    running.add_argument(
        "--asked",
        metavar="FILE",
        type=Path,
        required=True,
        help="the run jobs as a JSON array, as plan prints them",
    )
    running.add_argument("--revision", required=True, help="the logos-chat commit the runs are at")
    running.add_argument("--base", default="", help="the logos-chat commit to compare with")
    running.add_argument("--run-url", metavar="URL", required=True, help="the workflow run's page")
    running.set_defaults(command=_running)
    check = commands.add_parser(
        "check",
        help="print the update that completes a check run with the verdict",
        description="Prints, as JSON for GitHub's API, the update of a check run on logos-chat "
        "once the run jobs came to --matrix: completed, with the conclusion of its verdict and "
        "its title, --comment as its summary, and --report-url as its details, or --run-url "
        "without it. better is a success, worse a failure, and every other verdict neutral; "
        "with --required true, unbuilt and no-outcome are failures too.",
    )
    check.add_argument(
        "--matrix",
        metavar="FILE",
        type=Path,
        required=True,
        help="the matrix tools/sim matrix wrote of the runs",
    )
    check.add_argument(
        "--comment", metavar="FILE", type=Path, required=True, help="the comment on the runs"
    )
    check.add_argument("--run-url", metavar="URL", required=True, help="the workflow run's page")
    check.add_argument("--report-url", metavar="URL", help="the page of the run's report")
    check.add_argument(
        "--required",
        default="",
        help="whether the check is required, true or false, or nothing for false: once it is, a "
        "revision chat-sim does not build at, or a run with no outcome at it, fails it",
    )
    check.set_defaults(command=_check)
    post = commands.add_parser(
        "post",
        help="say where the comment goes on a logos-chat pull request, and write it",
        description="Prints, as lines for $GITHUB_OUTPUT, the method and the endpoint of "
        "GitHub's API that put --comment on logos-chat's pull request --pr: an edit of the "
        "oldest comment of --login among --comments, or else a new comment. Writes what is sent "
        "to --out.",
    )
    post.add_argument(
        "--comments",
        metavar="FILE",
        type=Path,
        required=True,
        help="the pull request's comments, a JSON array of pages each an array of comments, "
        "as gh api --paginate --slurp lists them",
    )
    post.add_argument("--login", required=True, help="the App's login, its slug and [bot]")
    post.add_argument("--pr", required=True, help="the logos-chat pull request")
    post.add_argument(
        "--comment", metavar="FILE", type=Path, required=True, help="the comment on the runs"
    )
    post.add_argument(
        "--out", metavar="FILE", type=Path, required=True, help="where to write what is sent"
    )
    post.set_defaults(command=_post)
    args = parser.parse_args(argv)
    return args.command(parser, args)


def scenarios(suite: str, expected: Path) -> List[Tuple[str, int]]:
    """The scenarios of `suite` and how many runs of each it does.

    For `pr` and `main` the goal's of each group at the suite's rates, as many as its seeds. For
    `long` the two of fifty epochs and every scenario `expected` holds but the goal's, as many as
    `expected` holds for each, and one when it holds none.
    """
    if suite in RATES:
        return [
            (goal.format(rate), SEEDS[suite]) for goal in GOALS.values() for rate in RATES[suite]
        ]
    goal = {goal.format(rate) for goal in GOALS.values() for rate in RATES["main"]}
    held = sorted(path.stem for path in expected.glob("*.json") if path.stem not in goal)
    names = FIFTY_EPOCHS + tuple(held)
    return [(name, _held_runs(expected / f"{name}.json") or 1) for name in names]


def measurement(tree: Path) -> Optional[str]:
    """The newest commit of `tree` that touches `MEASURED`, or `None` in a shallow clone."""
    shallow = _git(tree, "rev-parse", "--is-shallow-repository")
    # A shallow clone's oldest commit seems to add every file, so it would be named for any path.
    if shallow != "false":
        return None
    return _git(tree, "log", "-1", "--format=%H", "--", *MEASURED) or None


def jobs(suite: str, revision: str, base: str, swept: Sequence[Tuple[str, int]]) -> List[dict]:
    """The run jobs, each runs `from` to `from` + `runs` - 1 of a side's sweep of a scenario: a
    run a job for `pr`, ten for `main`, and the whole sweep for `long`."""
    sides = [("revision", "head", revision)] + ([("base", "base", base)] if base else [])
    found = []
    for side, said, sha in sides:
        for name, runs in swept:
            size = A_JOB.get(suite, runs)
            for first in range(1, runs + 1, size):
                count = min(size, runs - first + 1)
                found.append(
                    {
                        "side": side,
                        "revision": sha,
                        "scenario": name,
                        "from": first,
                        "runs": count,
                        "name": f"{said} {sha[:7]}: {_part(suite, name, first, count)}",
                    }
                )
    return found


def stands_in(suite: str, base: str, measurement: str, points: Collection[str]) -> Optional[str]:
    """Where on gh-pages main's point at `base` and `measurement` is, when `points` lists it and
    it can stand in for the base's runs of `suite`, or `None`. Only a point at the current
    measurement can, and only for `pr`, whose seeds are main's first three."""
    if suite != "pr" or not base or measure.point(base, measurement) not in points:
        return None
    return measure.point(base, measurement)


def without_outcome(point: dict, asked: Sequence[dict]) -> List[Tuple[str, int]]:
    """The runs of the jobs `asked` that `point`, a point.json, has no outcome for, each its
    scenario and its number in a sweep, in the order of the jobs."""
    had = {(cell["scenario"], cell["run"]) for cell in point["cells"] if cell["head"] is not None}
    return [
        (job["scenario"], number)
        for job in asked
        for number in range(job["from"], job["from"] + job["runs"])
        if (job["scenario"], number) not in had
    ]


def nearest(history: Sequence[str], measurement: str, points: Collection[str]) -> Optional[str]:
    """Where on gh-pages the point at `measurement` of the newest commit of `history` with one
    is, `history` newest first, or `None` when none has one."""
    at = (measure.point(sha, measurement) for sha in history)
    return next((path for path in at if path in points), None)


def title(matrix: dict) -> str:
    """The verdict of each group of `matrix` in a few words, after the group's name: how many of
    its runs moved, and which way; and where they drew other ids beside main's spread, how many
    fell below main's worst seed. A verdict every group has, of a head chat-sim does not build at
    or of no base to compare with, is said once."""
    if matrix["verdict"] in ("unbuilt", "no-base"):
        return _title(matrix, matrix)
    return "; ".join(f"{GROUPS[one['group']]} {_title(matrix, one)}" for one in matrix["groups"])


def _title(matrix: dict, judged: dict) -> str:
    """The verdict of `judged`, the matrix or one of its groups, in a few words."""
    counts, verdict = judged["counts"], judged["verdict"]
    if verdict == "re-rolled" and matrix["spread"] is not None:
        return (_TITLES[verdict] + _BELOW).format(c=counts)
    if verdict != "no-base":
        return _TITLES[verdict].format(c=counts)
    if matrix["base"] is None:
        return "no base, no comparison"
    if matrix["base"]["unbuilt"]:
        return "does not build at the base, no comparison"
    return "no outcome at the base, no comparison"


def headline(matrix: dict) -> str:
    """What the verdict line of a run says of `matrix`: chat-sim, and its verdict in a few
    words."""
    if matrix["verdict"] == "unbuilt":
        return _UNBUILT
    return f"chat-sim: {title(matrix)}"


def compares(matrix: dict) -> bool:
    """Whether `matrix` holds the head's runs to a base's: it has a base, and a cell of it was
    compared."""
    return matrix["base"] is not None and matrix["counts"]["compared"] > 0


def concluded(matrix: dict, required: bool) -> str:
    """The check's conclusion of `matrix`: a failure where the verdict of a group's runs fails it,
    else a success where one's succeeds, else neutral, so that one group's runs standing higher
    cannot hide the other's standing lower."""
    said = {conclusion(one["verdict"], required) for one in matrix["groups"]}
    return next((one for one in ("failure", "success") if one in said), "neutral")


def conclusion(verdict: str, required: bool) -> str:
    """The check's conclusion of `verdict`; with `required`, a head chat-sim does not build at,
    or a run with no outcome at the head, fails it. `no-base` stays neutral: what it lacks is the
    base's, which the pull request cannot give."""
    if required and verdict in _REQUIRED:
        return "failure"
    return CONCLUSIONS[verdict]


def running(asked: Sequence[dict], facts: dict) -> dict:
    """The check's update while the runs of the jobs `asked` go: in progress, the workflow run as
    its details, and how many runs the head does as its title."""
    runs = sum(job["runs"] for job in asked if job["side"] == "revision")
    said = f"{runs} runs in progress"
    return {
        "status": "in_progress",
        "details_url": facts["run_url"],
        "output": {"title": said, "summary": _verdict_line(said, facts)},
    }


def completed(matrix: dict, comment: str, facts: dict, required: bool) -> dict:
    """The check's update once the runs came to `matrix`: completed, with the conclusion of its
    groups' verdicts, its title, `comment` as its summary, and the report page as its details, or
    the workflow run without one."""
    return {
        "status": "completed",
        "conclusion": concluded(matrix, required),
        "details_url": facts["report_url"] or facts["run_url"],
        "output": {"title": title(matrix), "summary": comment},
    }


def post_to(pages: Sequence[Sequence[dict]], login: str, pr: str) -> Tuple[str, str]:
    """Where the comment goes on logos-chat's pull request `pr`, as a method and an endpoint of
    GitHub's API: an edit of the oldest comment of `login` among `pages`, the pull request's
    comments a page at a time as the API lists them, oldest first; or else a new comment."""
    mine = (
        one for page in pages for one in page if (one.get("user") or {}).get("login") == login
    )
    found = next(mine, None)
    if found is None:
        return "POST", f"{ON_LOGOS_CHAT}/issues/{pr}/comments"
    return "PATCH", f"{ON_LOGOS_CHAT}/issues/comments/{found['id']}"


def render_pr(matrix: dict, facts: dict) -> str:
    """The comment on a pull request: the verdict of each group; main's runs of the two groups
    side by side where the matrix has its spread, or else, against a base, why not; a row a
    group's rate and a cell a seed, beside them main's spread; what a cell says, and under it a
    fold for each cell with its runs and the command that runs it again."""
    if matrix["verdict"] == "unbuilt":
        return _unbuilt_comment(facts)
    cells, spread = matrix["cells"], matrix["spread"]
    seeds = sorted({cell["seed"] for cell in cells})
    against = compares(matrix)
    columns = [f"Seed {seed}" for seed in seeds]
    if spread is not None:
        columns.append(_spread_head(spread))
    lines = [_verdict_line(headline(matrix), facts, matrix["base_point"])]
    if spread is not None:
        lines += ["", *sides(spread)]
    elif facts["base"]:
        lines += ["", *_NO_SIDES]
    lines += [
        "",
        "| Group | Loss | " + " | ".join(columns) + " |",
        "|---" * (len(columns) + 2) + "|",
    ]
    for rate, row in _rows(cells):
        said = {cell["seed"]: words(cell, against) for cell in row}
        shown = [said.get(seed, "-") for seed in seeds]
        if spread is not None:
            shown.append(spread_of(spread, row[0]["scenario"]))
        lines.append(f"| {GROUPS[row[0]['group']]} | {rate} | " + " | ".join(shown) + " |")
    lines += ["", _PR_FOOT]
    for cell in cells:
        opened = " open" if cell["change"] in ("better", "worse") else ""
        lines += [
            "",
            f"<details{opened}><summary>{GROUPS[cell['group']]} {_rate(cell['rate'])} loss, seed "
            f"{cell['seed']}: "
            f"{words(cell, against)}</summary>",
            "",
            f"- **head** {_commit(facts['revision'])} · {_ran(cell, 'head')}",
        ]
        if facts["base"]:
            lines.append(f"- **base** {_commit(facts['base'])} · {_ran(cell, 'base')}")
        lines += ["", "```sh", *again(cell, facts), "```", "", "</details>"]
    return "".join(f"{line}\n" for line in lines)


def render_main(matrix: dict, facts: dict) -> str:
    """The comment on a point of main: a row a group's rate with how its runs ended, the
    lowest, median and highest epochs without a fork and of the first fork, and the medians of
    the lines and of the members off the trunk, and of those that never joined."""
    if matrix["verdict"] == "unbuilt":
        return _unbuilt_comment(facts)
    said = f"chat-sim: {matrix['counts']['cells']} runs"
    if matrix["verdict"] == "no-outcome":
        said = f"chat-sim: {_title(matrix, matrix)}"
    lines = [
        _verdict_line(said, facts),
        "",
        "| Group | Loss | Runs | Whole | Behind | Split | Stalled | Epochs without a fork | "
        "First fork | Lines off the trunk, median | Members off the trunk, median | "
        "Never joined, median |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for one in matrix["summary"]:
        reached = _extent(one["epochs"])
        counted = " | ".join(str(one[ended]) for ended in ("whole", "behind", "split", "stalled"))
        named = f"{GROUPS[one['group']]} | {_rate(one['rate'])}"
        forked = " | ".join(
            said(one) for said in (_first_fork, _lines_off, _members_off, _never_joined)
        )
        lines.append(f"| {named} | {one['runs']} | {counted} | {reached} | {forked} |")
    lines += ["", _MAIN_FOOT]
    return "".join(f"{line}\n" for line in lines)


def sides(spread: dict) -> List[str]:
    """Main's runs in `spread` of the two groups side by side, as lines of Markdown: a row a rate
    of how many split, the epochs of the first fork, the medians off the trunk and of those that
    never joined, each GroupV2's, then GroupV3's, and what a row says."""
    rates: Dict[object, Dict[str, dict]] = {}
    for one in spread["summary"]:
        rates.setdefault(one["loss"], {})[one["group"]] = one
    lines = [
        f"**{' · '.join(GROUPS.values())}**, main's {spread_seeds(spread)} of each rate at "
        f"{_commit(spread['logos_chat'])}",
        "",
        "| Loss | Split | First fork | Lines off the trunk, median | "
        "Members off the trunk, median | Never joined, median |",
        "|---|---|---|---|---|---|",
    ]
    for loss in sorted(rates, key=lambda loss: (loss is None, loss or 0)):
        held = [rates[loss].get(group) for group in GROUPS]
        rate = next(one for one in held if one is not None)["rate"]
        shown = [
            " · ".join("-" if one is None else said(one) for one in held)
            for said in (_split, _first_fork, _lines_off, _members_off, _never_joined)
        ]
        lines.append(f"| {_rate(rate)} | " + " | ".join(shown) + " |")
    return lines + ["", _SIDES_FOOT]


def render(
    swept: Sequence[Tuple[str, int]], runs: Path, base_runs: Optional[Path], facts: dict
) -> str:
    """The comment on the long suite: a head, a row for each scenario, the links, and the output
    of each compare.

    A run job that could not build chat-sim leaves `unbuilt` in each of its scenarios'
    directories: with it at the revision no scenario ran, and the comment says so instead, and
    where the build's errors are.
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
    report = f" · [the report]({facts['report_url']})" if facts["report_url"] else ""
    foot = f"chat-sim `{facts['chat_sim'][:7]}`{report} · [the run]({facts['run_url']})"
    if _unbuilt(runs):
        where = "on the report page" if facts["report_url"] else "in the run"
        said = (
            "chat-sim does not build at this revision, so no scenario ran: the build's errors "
            f"are {where}."
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
        swept_at = _swept(runs, name)
        now = _outcomes(swept_at / "outcomes.json")
        compared = _text(swept_at / "compare.txt")
        said = compare_count(now, compared, missing)
        lines.append(f"| `{name}` | {count} | {came_to(now)} | {said} |")
        if compared is not None:
            folded.append((name, compared))
    lines += ["", foot]
    for name, text in folded:
        lines += ["", f"<details><summary><code>{name}</code></summary>", "", "```"]
        lines += [text.rstrip("\n"), "```", "", "</details>"]
    return "".join(f"{line}\n" for line in lines)


def _add_handed(command: argparse.ArgumentParser) -> None:
    command.add_argument("--suite", required=True, choices=SUITES, help="pr, main or long")
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
    if args.base and args.suite == "main":
        parser.error("--base: the main suite runs at the revision alone")
    if args.pr and _PR.fullmatch(args.pr) is None:
        parser.error(f"--pr: {args.pr!r} is not the number of a pull request")
    if not args.expected.is_dir():
        parser.error(f"--expected: no such directory: {args.expected}")


def _plan(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    _check_handed(parser, args)
    if args.check_run and _CHECK_RUN.fullmatch(args.check_run) is None:
        parser.error(f"--check-run: {args.check_run!r} is not the id of a check run")
    if args.check_run and (args.suite != "pr" or not args.pr):
        parser.error("--check-run: given for pr with --pr, and only then")
    if not ((args.points is None) == (args.measurement is None) == (args.pages is None)):
        parser.error("--points: given with --measurement and --pages, and only with them")
    point = None
    if args.points is not None:
        _check_measurement(parser, args.measurement)
        listed = set(_lines(parser, "--points", args.points))
        point = stands_in(args.suite, args.base, args.measurement, listed)
    swept = scenarios(args.suite, args.expected)
    if point is not None:
        planned = jobs(args.suite, args.revision, args.base, swept)
        at_base = [job for job in planned if job["side"] == "base"]
        why = _not_standing(args.tree, args.pages, point, args.suite, at_base)
        if why is not None:
            print(f"::warning::{why}", file=sys.stderr)
            point = None
    for name in ("suite", "revision", "base", "pr", "check_run"):
        print(f"{name}={getattr(args, name)}")
    run_at = "" if point else args.base
    print(f"runs={json.dumps(jobs(args.suite, args.revision, run_at, swept))}")
    print(f"scenarios={' '.join(name for name, _ in swept)}")
    print(f"point={point or ''}")
    return 0


def _not_standing(
    tree: Path, ref: str, where: str, suite: str, asked: Sequence[dict]
) -> Optional[str]:
    """Why main's point at `where` on `ref` of `tree` does not stand in for the runs the jobs
    `asked` of `suite` do at the base: it cannot be read as pages.py reads a point, or it has no
    outcome at one of them. `None` when it stands in."""
    said = "main's point at the base does not stand in for the base's runs, so its jobs run them"
    point, why = _read_point(tree, ref, where)
    if point is None:
        return f"{said}: {why}"
    missing = [_part(suite, name, number, 1) for name, number in without_outcome(point, asked)]
    if missing:
        return f"{said}: {where} has no outcome at {', '.join(missing)}"
    return None


def _read_point(tree: Path, ref: str, where: str) -> Tuple[Optional[dict], str]:
    """Main's point at `where` on `ref` of `tree` as pages.py reads a point, and `""`; or `None`,
    and where the point is and why it cannot be read."""
    # pages.py imports this module.
    import pages

    read = subprocess.run(
        ["git", "-C", str(tree), "cat-file", "blob", f"{ref}:{where}"], capture_output=True
    )
    if read.returncode != 0:
        # git's lines as one: a warning is a line.
        failed = " ".join(read.stderr.decode(errors="replace").split())
        return None, f"{where} on {ref}: {failed}"
    with tempfile.TemporaryDirectory() as scratch:
        # pages.py reads a point in the directory of its logos-chat commit and its measurement.
        file = Path(scratch, where)
        file.parent.mkdir(parents=True)
        file.write_bytes(read.stdout)
        try:
            return pages.read_point(file), ""
        except pages.Unreadable as refused:
            return None, f"{where} on {ref}: {str(refused).removeprefix(f'{file}: ')}"


def _spread(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    _check_measurement(parser, args.measurement)
    listed = set(_lines(parser, "--points", args.points))
    try:
        history = json.loads(args.history.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        parser.error(f"--history: {args.history}: {error}")
    if not isinstance(history, list) or not all(
        isinstance(one, dict) and _SHA.fullmatch(str(one.get("sha"))) for one in history
    ):
        parser.error(f"--history: {args.history}: not a list of commits, each with its sha")
    shas = [one["sha"] for one in history]
    found = nearest(shas, args.measurement, listed)
    while found is not None:
        point, why = _read_point(args.tree, args.pages, found)
        if point is not None:
            break
        print(
            f"::warning::the head's runs are not placed among main's point: {why}", file=sys.stderr
        )
        listed.discard(found)
        found = nearest(shas, args.measurement, listed)
    print(f"spread={found or ''}")
    return 0


def _comment(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    _check_handed(parser, args)
    if args.report_url is not None and _URL.fullmatch(args.report_url) is None:
        parser.error(f"--report-url: {args.report_url!r} is not an http(s) address")
    facts = {
        "suite": args.suite,
        "revision": args.revision,
        "base": args.base,
        "pr": args.pr,
        "chat_sim": args.chat_sim,
        "run_url": args.run_url,
        "report_url": args.report_url,
    }
    if args.suite == "long":
        if args.runs is None or args.matrix is not None:
            parser.error("RUNS: the long suite is written from its runs, and not from --matrix")
        if (args.base_runs is None) != (not args.base):
            parser.error("--base-runs: given with --base, and only with it")
        swept = scenarios(args.suite, args.expected)
        sys.stdout.write(render(swept, args.runs, args.base_runs, facts))
        return 0
    if args.matrix is None or args.runs is not None or args.base_runs is not None:
        parser.error(f"--matrix: the {args.suite} suite is written from its matrix alone")
    matrix = _matrix(args.matrix)
    if matrix is None:
        parser.error(f"--matrix: {args.matrix} is not a matrix tools/sim matrix writes")
    if (matrix["base"] is None) != (not args.base):
        parser.error("--matrix: a matrix with a base is given with --base, and only with it")
    if matrix["base_point"] is not None and matrix["base_point"]["logos_chat"] != args.base:
        parser.error("--matrix: main's point that stood in for the base is not at --base")
    written = render_pr(matrix, facts) if args.suite == "pr" else render_main(matrix, facts)
    sys.stdout.write(written)
    return 0


def _measurement(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    measured = measurement(args.tree)
    if measured is None:
        parser.error(f"--tree: {args.tree} is a shallow clone, or no commit touches {MEASURED}")
    print(f"measurement={measured}")
    return 0


def _running(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if _SHA.fullmatch(args.revision) is None:
        parser.error(f"--revision: {args.revision!r} is not a commit's 40 hex digits")
    if args.base and _SHA.fullmatch(args.base) is None:
        parser.error(f"--base: {args.base!r} is not a commit's 40 hex digits")
    try:
        asked = json.loads(_read(parser, "--asked", args.asked))
    except ValueError as error:
        parser.error(f"--asked: {args.asked}: {error}")
    if not isinstance(asked, list) or not all(
        isinstance(job, dict)
        and job.get("side") in ("revision", "base")
        and _count(job.get("runs"))
        for job in asked
    ):
        parser.error(f"--asked: {args.asked}: not the run jobs plan prints")
    facts = {
        "revision": args.revision,
        "base": args.base,
        "run_url": args.run_url,
        "report_url": None,
    }
    print(json.dumps(running(asked, facts)))
    return 0


def _check(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if args.report_url is not None and _URL.fullmatch(args.report_url) is None:
        parser.error(f"--report-url: {args.report_url!r} is not an http(s) address")
    if args.required not in ("", "true", "false"):
        parser.error(f"--required: {args.required!r} is neither true nor false")
    matrix = _matrix(args.matrix)
    if matrix is None or matrix["verdict"] not in CONCLUSIONS:
        parser.error(f"--matrix: {args.matrix} is not a matrix tools/sim matrix writes")
    comment = _read(parser, "--comment", args.comment)
    facts = {"run_url": args.run_url, "report_url": args.report_url}
    print(json.dumps(completed(matrix, comment, facts, args.required == "true")))
    return 0


def _post(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if _PR.fullmatch(args.pr) is None:
        parser.error(f"--pr: {args.pr!r} is not the number of a pull request")
    try:
        pages = json.loads(_read(parser, "--comments", args.comments))
    except ValueError as error:
        parser.error(f"--comments: {args.comments}: {error}")
    if not isinstance(pages, list) or not all(
        isinstance(page, list)
        and all(
            isinstance(one, dict)
            and _count(one.get("id"))
            and isinstance(one.get("user"), (dict, type(None)))
            for one in page
        )
        for page in pages
    ):
        parser.error(f"--comments: {args.comments}: not pages of comments, each with its id")
    body = _read(parser, "--comment", args.comment)
    method, endpoint = post_to(pages, args.login, args.pr)
    args.out.write_text(json.dumps({"body": body}), encoding="utf-8")
    print(f"method={method}")
    print(f"endpoint={endpoint}")
    return 0


def _count(value: object) -> bool:
    """Whether `value` is a whole number above zero, as JSON gives one."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _check_measurement(parser: argparse.ArgumentParser, sha: str) -> None:
    if _SHA.fullmatch(sha) is None:
        parser.error(f"--measurement: {sha!r} is not a commit's 40 hex digits")


def _lines(parser: argparse.ArgumentParser, flag: str, file: Path) -> List[str]:
    return _read(parser, flag, file).splitlines()


def _read(parser: argparse.ArgumentParser, flag: str, file: Path) -> str:
    try:
        return file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        parser.error(f"{flag}: {file}: {error}")


def _git(tree: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-C", str(tree), *argv], check=True, capture_output=True, text=True
    ).stdout.strip()


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


def _matrix(file: Path) -> Optional[dict]:
    """The matrix `file` holds, or `None` when it holds none."""
    try:
        matrix = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return matrix if isinstance(matrix, dict) and matrix.keys() == MATRIX else None


def _part(suite: str, name: str, first: int, count: int) -> str:
    """What a job runs, as its name says: a scenario of `long`, else a group, a rate and its
    seeds."""
    if suite == "long":
        return name
    rate = next(
        f"{GROUPS[group]} {rate}%"
        for group, goal in GOALS.items()
        for rate in RATES["main"]
        if goal.format(rate) == name
    )
    if count == 1:
        return f"{rate} seed {first - 1}"
    return f"{rate} seeds {first - 1} to {first + count - 2}"


def ran_at(
    facts: dict,
    point: Optional[dict],
    commit: Callable[[str], str],
    link: Callable[[str, dict], str],
) -> str:
    """What the verdict line says after its verdict: the commit that ran and the merge base it
    was held to, and the point of main whose runs stood in for the base's where one did, each
    commit as `commit` draws it and the words that name the point as `link` draws them."""
    said = f"at {commit(facts['revision'])}"
    if facts["base"]:
        said += f" against its merge base {commit(facts['base'])}"
    if point is not None:
        named = link("main's run of it", point)
        said += f", from {named}"
    return said


def _verdict_line(said: str, facts: dict, point: Optional[dict] = None) -> str:
    """The verdict line: what was said, of which commit against which, the point of main whose
    runs stood in for the base's where one did, and the links."""
    run = ran_at(facts, point, _commit, lambda words, at: f"[{words}]({at['run_url']})")
    line = f"**{said}** {run}"
    if facts["report_url"]:
        line += f" · [the report]({facts['report_url']})"
    return line + f" · [the run]({facts['run_url']})"


def _unbuilt_comment(facts: dict) -> str:
    where = "on the report page" if facts["report_url"] else "in the run"
    lines = [
        _verdict_line(_UNBUILT, facts),
        "",
        f"No scenario ran: the build's errors are {where}.",
    ]
    return "".join(f"{line}\n" for line in lines)


def _rows(cells: List[dict]) -> List[Tuple[str, List[dict]]]:
    """The cells a row a scenario, in their order, each row by its rate of loss."""
    rows: List[Tuple[str, List[dict]]] = []
    for cell in cells:
        if not rows or rows[-1][1][0]["scenario"] != cell["scenario"]:
            rows.append((_rate(cell["rate"]), []))
        rows[-1][1].append(cell)
    return rows


def words(cell: dict, against: bool) -> str:
    """A cell in a few words: how the head's run ended, at the epoch of its first fork or else of
    the trunk, and the lines and the members off the trunk where there are any and how many of
    those never joined where any did, then its change, with what the base's run had of what moved
    where it is better or worse, and where it is re-rolled beside main's spread whether the head's
    epochs are within it or below it; and with `against`, why a cell that has an outcome at the
    head has no change."""
    if cell["head"] is None:
        return _missing(cell, "head")
    said = _ended_at(cell["epochs"]["head"], cell["ended"]["head"])
    off = _off_trunk(cell["head"])
    if off:
        said += f", {off}"
    change, moved = cell["change"], cell["moved"]
    if moved is not None:
        return f"{said}, was {_moved(cell)}: {change}"
    if change == "re-rolled" and cell["below"] is not None:
        return f"{said}, {change}, {'below' if cell['below'] else 'within'} main's spread"
    if change is not None:
        return f"{said}, {change}"
    return f"{said}, {_missing(cell, 'base')} at the base" if against else said


def spread_seeds(spread: dict) -> str:
    """How many seeds of a rate main ran in `spread`: `20 seeds`, or `seeds` where its rates
    ran other counts."""
    runs = {one["runs"] for one in spread["summary"]}
    return f"{runs.pop()} seeds" if len(runs) == 1 else "seeds"


def _extent(spread: dict) -> str:
    """The lowest, the highest and the median of a summary's values, or `-` without any."""
    if spread["median"] is None:
        return "-"
    return f"{spread['lowest']} to {spread['highest']}, median {spread['median']}"


def _split(one: dict) -> str:
    """How many of a summary's runs with an outcome split, and how many had none, or `-` where
    none had one."""
    without = one["no-outcome"]
    if without == one["runs"]:
        return "-"
    said = f"{one['split']} of {one['runs'] - without}"
    return said if not without else f"{said}, {without} with no outcome"


def _first_fork(one: dict) -> str:
    return _extent(one["split_at"])


def _lines_off(one: dict) -> str:
    median = one["lines_off_trunk"]["median"]
    return "-" if median is None else str(median)


def _members_off(one: dict) -> str:
    """The median of a summary's members off the trunk, of the median of those seated."""
    median, seated = one["members_off_trunk"]["median"], one["seated"]["median"]
    if median is None:
        return "-"
    return str(median) if seated is None else f"{median} of {seated}"


def _never_joined(one: dict) -> str:
    median = one["never_joined"]["median"]
    return "-" if median is None else str(median)


def _spread_head(spread: dict) -> str:
    """The head of the column of main's spread: how many seeds of a rate main ran, and where."""
    return f"main's {spread_seeds(spread)} at {_commit(spread['logos_chat'])}"


def spread_of(spread: dict, scenario: str) -> str:
    """Main's runs of a scenario in a few words: how many ended whole, and the lowest and the
    highest epochs without a fork."""
    one = next((one for one in spread["summary"] if one["scenario"] == scenario), None)
    if one is None or one["epochs"]["lowest"] is None:
        return "-"
    return f"{one['whole']} whole, {one['epochs']['lowest']} to {one['epochs']['highest']}"


def _ended_at(epochs: int, ended: str) -> str:
    """How a run ended, at the epoch of its first fork or else of the trunk."""
    if ended == "split":
        return f"split at {epochs}"
    return f"{epochs} whole" if ended == "whole" else f"{epochs}, {ended}"


def _off_trunk(outcome: dict) -> str:
    """The lines and the members off the trunk at the end of a run, of the members seated, and
    how many of those never joined, or nothing where there are none. An outcome of an earlier
    measurement does not count those that never joined."""
    lines, members = outcome["lines_off_trunk"], outcome["members_off_trunk"]
    said = []
    if lines:
        said.append(f"{lines} line{'' if lines == 1 else 's'}")
    if members:
        said.append(f"{members} of {outcome['seated']} members")
    if not said:
        return ""
    off = f"{' and '.join(said)} off the trunk"
    never = outcome.get("never_joined")
    return f"{off}, {never} of them never joined" if never else off


def _moved(cell: dict) -> str:
    """What the base's run of a cell had of the part that moved: how it ended and where, or its
    lines or members off the trunk."""
    part, was = cell["moved"]["part"], cell["moved"]["base"]
    if part in ("epochs", "ended"):
        return _ended_at(cell["epochs"]["base"], cell["ended"]["base"])
    return f"{was} {_OFF[part]}{'' if was == 1 else 's'} off"


def _ran(cell: dict, side: str) -> str:
    """What a side's run of a cell came to: its verdict and how its grow ended."""
    outcome = cell[side]
    if outcome is None:
        return _missing(cell, side)
    return html.escape(f"{outcome['verdict']} · {outcome['came_to']}", quote=False)


def _missing(cell: dict, side: str) -> str:
    """Why a side of a cell has no outcome."""
    return "does not build" if cell["unbuilt"][side] else "no outcome"


def again(cell: dict, facts: dict) -> List[str]:
    """The commands that run a cell again, at the head or at the base."""
    pins = f"# move both pins to {facts['revision']}"
    if facts["base"]:
        pins += f", or to {facts['base']} for the base"
    scenario = cell["scenario"]
    return [
        f"git clone {CHAT_SIM} && cd chat-sim && git checkout {facts['chat_sim']}",
        f"{pins}, then",
        "nix develop -c cargo build --release -p harness --bin harness-run",
        f"python3 tools/sim sweep tools/sim/scenarios/{scenario}.json --from {cell['run']} "
        f"--runs 1 --one-process target/release/harness-run --out tmp/{scenario}-seed-"
        f"{cell['seed']}",
    ]


def _rate(rate: Optional[str]) -> str:
    return "-" if rate is None else rate


def came_to(now: Optional[List[dict]]) -> str:
    """What the runs of a sweep came to, `now` their outcomes: each run's verdict, or `no outcome`
    without them."""
    if now is None:
        return "no outcome"
    return ", ".join(one.get("verdict") or "no verdict" for one in now)


def compare_count(now: Optional[List[dict]], compared: Optional[str], missing: str) -> str:
    """The count compare ends its output with, or why there is no compare."""
    if now is None:
        return "-"
    if compared is None or not compared.strip():
        return missing
    return compared.strip().splitlines()[-1]


def _unbuilt(runs: Optional[Path]) -> bool:
    return runs is not None and any(runs.rglob("unbuilt"))


def _swept(runs: Path, name: str) -> Path:
    """The directory of the sweep of `name` under `runs`, at any depth."""
    return next(iter(sorted(runs.glob(f"**/{name}"))), runs / name)


def _text(file: Path) -> Optional[str]:
    try:
        return file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _commit(sha: str) -> str:
    return f"[`{sha[:7]}`]({LOGOS_CHAT}/commit/{sha})"


if __name__ == "__main__":
    raise SystemExit(main())
