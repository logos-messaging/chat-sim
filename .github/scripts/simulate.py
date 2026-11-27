#!/usr/bin/env python3
"""Plans the jobs of simulate.yml and writes the comment on what they came to.

simulate.yml runs chat-sim at a logos-chat revision it is handed, and at a base revision too when
it is handed one, in one of three suites. `pr` and `main` run `grow-3-adders`, a group grown toward
epoch 50 by up to three members of any branch a round, on GroupV2 and on GroupV3 at rates of loss:
`pr` seeds 0 to 2 of the three lossy rates, a job a run, at the revision and at the base, and of a
group a run of which came out otherwise at the two, seeds 3 to 19 of each of its rates as well, six
a job; `main` seeds 0 to 2 at 0% and 0 to 19 at the rest, ten a job, and no base. `long` runs the
two `seat-50-post` scenarios and every other one `tools/sim/expected` holds but `main`'s, a job
a scenario a side. `plan` checks what it was handed and prints the run jobs, and `widen` the run
jobs of seeds 3 to 19 from the matrix of seeds 0 to 2. `comment` writes the comment in Markdown:
for `pr` and `main` from the matrix `tools/sim matrix` wrote of the runs, each group's apart, for
`pr` with a picture of the trees of each group whose runs moved, once handed where the pictures
are, and links to the trees of the runs on the pull request's page and to the graph over main, and
for `long` a row for each scenario with what `tools/sim compare` said of its runs against the
base's, or against what `tools/sim/expected` holds when there is no base. `measurement` prints the
chat-sim commit a run measures, and `changed` whether a commit changes a path chat-sim's own CI
holds its runs to against its merge base with another. `running` writes the update that moves a
check run on logos-chat to in progress while the runs go, and `check` the one that completes it
with the groups' verdicts; and `post` says where the comment goes on a logos-chat pull request, an
edit of the App's comment there or a new one, and writes it for GitHub's API.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin

# The scenario `pr` and `main` run, of each group at a rate of loss in percent, by the group's name
# in a scenario: two or more adders a round can commit for one epoch, which a GroupV3 group needs
# to fork. Its files set no seed, so that run k of a sweep has seed k - 1.
GROW_3_ADDERS = {
    "v2": "grow-3-adders-groupv2-waku-loss-{}pct",
    "v3": "grow-3-adders-groupv3-waku-loss-{}pct",
}
# What the comment calls each group.
GROUPS = {"v2": "GroupV2", "v3": "GroupV3"}
# How many seeds of each rate a suite runs, from seed 0. GroupV3 forks at each of `pr`'s rates on
# seeds 0 to 2, where at 0.1% two of them stay whole. `main` runs three of 0%, by far its slowest
# rate, at which no run of main's has split.
RATES = {"pr": {"0.5": 3, "1": 3, "5": 3}, "main": {"0": 3, "0.5": 20, "1": 20, "5": 20}}
# How many runs a job does. `widen` is the rest of main's seeds of each rate of a `pr` group whose
# runs moved, at both sides.
A_JOB = {"pr": 1, "main": 10, "widen": 6}
SEAT_50_POST = ("seat-50-post-groupv2", "seat-50-post-groupv2-waku-loss-0pct")
SUITES = ("pr", "main", "long")
LOGOS_CHAT = "https://github.com/logos-messaging/logos-chat"
CHAT_SIM = "https://github.com/logos-messaging/chat-sim"
# What a run measures: a commit under these paths can move how a run comes out, where one to the
# guide or to the pages cannot. The build reads the files at the root besides crates/, and
# tools/sim judges a run without a `grow` step by its logs with tools/forks.
MEASURED = (
    "crates/",
    "Cargo.toml",
    "Cargo.lock",
    ".cargo/",
    "rust-toolchain.toml",
    "flake.nix",
    "flake.lock",
    "tools/forks/__main__.py",
    "tools/forks/forks/",
    "tools/sim/__main__.py",
    "tools/sim/sim/",
    "tools/sim/scenarios/",
)
# What chat-sim's own CI holds its runs to besides what a run measures: the outcomes it expects,
# and the workflow that runs them.
HELD = (*MEASURED, "tools/sim/expected/", ".github/workflows/ci.yml")
# What a matrix of `tools/sim matrix` holds.
MATRIX = {"head", "base", "cells", "counts", "verdict", "groups", "summary"}
# What a group's runs at the head came to against the base's, each result of `tools/sim matrix` as
# a group's line words it.
RESULTS = {
    "same": "nothing moved",
    "within": "moved, within chance",
    "earlier": "forks earlier",
    "later": "forks later",
    "unbuilt": "does not build",
    "no-outcome": "no outcome",
    "no-base": "nothing to compare at the base",
}

_SHA = re.compile(r"[0-9a-f]{40}")
_PR = re.compile(r"[1-9][0-9]*")
_CHECK_RUN = re.compile(r"[1-9][0-9]*")
# A number in a scenario's name: how many add, a size, a group's version or a rate of loss.
_NUMBER = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
# logos-chat in GitHub's API, where the comment and the check are.
ON_LOGOS_CHAT = "repos/logos-messaging/logos-chat"
# The check's conclusion of each verdict: failure where the head's runs fork earlier beyond
# chance, success where they fork later, and neutral otherwise, as for a matrix without a base,
# which has no verdict.
CONCLUSIONS = {
    "earlier": "failure",
    "later": "success",
    "within": "neutral",
    "same": "neutral",
    "no-base": "neutral",
    "unbuilt": "neutral",
    "no-outcome": "neutral",
    None: "neutral",
}
# The verdicts that fail the check once it is required: what went wrong at the head.
_REQUIRED = ("unbuilt", "no-outcome")
# An address a Markdown link can hold as it is, and an attribute of the comment's HTML.
_URL = re.compile(r"https?://[^\s()<>\"']+")
# What the verdict line says of a revision chat-sim does not build at.
_UNBUILT = "chat-sim does not build against this commit"

# The title of each verdict that says no more than the counts of the matrix's cells.
_TITLES = {
    "same": "nothing moved in {c[cells]} runs",
    "within": "moved, within chance",
    "unbuilt": "does not build against this commit",
    "no-outcome": "no outcome in {c[no-outcome]} of {c[cells]} runs",
}
_ENDED = (
    "whole: one group, every member that joined at its tip; behind: one group, some members "
    "behind; stalled: one group that stopped growing short of 50; split: it forked"
)
# Of the members a run seated, those `compare` counts off the trunk at the end.
_OFF_TRUNK = "behind, on another line, or never joined"
# The mark of each of `RESULTS` that leads a group's line of the comment on a pull request: a
# missing run, at the head or at the base, is black.
_MARKS = {
    "same": "⚪",
    "within": "🟡",
    "earlier": "🔴",
    "later": "🟢",
    "unbuilt": "⚫",
    "no-outcome": "⚫",
    "no-base": "⚫",
}
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
        "$GITHUB_OUTPUT, what it was handed, the run jobs as a JSON array and the suite's "
        "scenarios.",
    )
    _add_handed(plan)
    plan.add_argument(
        "--check-run",
        default="",
        help="the id of the check run on logos-chat the run completes, or nothing: for pr, "
        "given with --pr, and only then",
    )
    plan.set_defaults(command=_plan)
    widened = commands.add_parser(
        "widen",
        help="print the run jobs of the rest of the seeds of each group of pr whose runs moved",
        description="Prints, as lines for $GITHUB_OUTPUT, the run jobs that follow those of "
        "--asked as a JSON array, none where no run moved: of each group a run of which in "
        "--matrix came out otherwise at the revision than at the base, seeds 3 to 19 of each of "
        "pr's rates, at both, six a job; then the jobs of --asked and those together.",
    )
    widened.add_argument(
        "--matrix",
        metavar="FILE",
        type=Path,
        required=True,
        help="the matrix tools/sim matrix wrote of the runs of --asked",
    )
    widened.add_argument(
        "--asked",
        metavar="FILE",
        type=Path,
        required=True,
        help="the run jobs as a JSON array, as plan prints them",
    )
    widened.add_argument("--revision", required=True, help="the logos-chat commit the runs are at")
    widened.add_argument("--base", required=True, help="the logos-chat commit to compare with")
    widened.set_defaults(command=_widen)
    comment = commands.add_parser(
        "comment",
        help="write the comment on what the run jobs came to",
        description="Writes the comment in Markdown. For pr and main it is drawn from --matrix: "
        "for pr a line that says nothing moved, or else a line for each group with its verdict "
        "and what its runs at each rate came to, with --pictures the picture of the trees of "
        "each group whose runs moved, then links to the trees of the runs on the report's page, "
        "to the report, kept while the pull request is open, to the graph over main and to the "
        "run; for main a row a group's rate with how its runs ended and forked. "
        "For long it is a row for each scenario from the outcomes.json the run jobs uploaded "
        "under RUNS and the compare.txt written beside it, and the output of each compare. With "
        "--out it is written there.",
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
    comment.add_argument(
        "--pictures",
        metavar="URL",
        help="for pr, the directory the pictures of the report's trees are read at, once they "
        "are published: its address, ending in /",
    )
    comment.add_argument(
        "--out",
        metavar="FILE",
        type=Path,
        help="where to write the comment, instead of standard output",
    )
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
    held = commands.add_parser(
        "changed",
        help="say whether a commit changes a path chat-sim's CI holds its runs to",
        description="Prints, as a line for $GITHUB_OUTPUT, true when the tree's HEAD changes "
        "what a run measures, tools/sim/expected or ci.yml against its merge base with --since, "
        "or when --since is empty or no commit of the tree; else false.",
    )
    held.add_argument(
        "--since",
        metavar="SHA",
        required=True,
        help="the commit to compare from, a pull request's base or the commit before a push: "
        "nothing or forty zeros for none",
    )
    held.add_argument(
        "--tree", metavar="DIR", type=Path, default=Path(), help="the checkout (default: .)"
    )
    held.set_defaults(command=_changed)
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
        "without it. earlier is a failure, later a success, and every other verdict neutral; "
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

    For `pr` and `main` `grow-3-adders` of each group at the suite's rates, as many as its seeds.
    For `long` the two of `seat-50-post` and every scenario `expected` holds but `main`'s, as many
    as `expected` holds for each, and one when it holds none.
    """
    if suite in RATES:
        return [
            (grow.format(rate), seeds)
            for grow in GROW_3_ADDERS.values()
            for rate, seeds in RATES[suite].items()
        ]
    grown = {grow.format(rate) for grow in GROW_3_ADDERS.values() for rate in RATES["main"]}
    held = in_order(path.stem for path in expected.glob("*.json") if path.stem not in grown)
    names = SEAT_50_POST + tuple(held)
    return [(name, _held_runs(expected / f"{name}.json") or 1) for name in names]


def in_order(names: Iterable[str]) -> List[str]:
    """`names` of scenarios sorted with each number in a name read as a number, so that a
    scenario at 0pct comes before the one at 0.1pct."""

    def numbered(name: str) -> List[object]:
        return [float(part) if at % 2 else part for at, part in enumerate(_NUMBER.split(name))]

    return sorted(names, key=numbered)


def measurement(tree: Path) -> Optional[str]:
    """The newest commit of `tree` that touches `MEASURED`, or `None` in a shallow clone."""
    shallow = _git(tree, "rev-parse", "--is-shallow-repository")
    # A shallow clone's oldest commit seems to add every file, so it would be named for any path.
    if shallow != "false":
        return None
    return _git(tree, "log", "-1", "--format=%H", "--", *MEASURED) or None


def changed(tree: Path, since: str) -> bool:
    """Whether HEAD of `tree` changes a path of `HELD` against its merge base with `since`, or
    `since` is empty or no commit of `tree`."""
    if not since:
        return True
    found = subprocess.run(
        ["git", "-C", str(tree), "merge-base", since, "HEAD"], capture_output=True, text=True
    )
    if found.returncode != 0:
        return True
    base = found.stdout.strip()
    # diff-tree reads trees alone, so a clone without blobs serves it.
    diff = subprocess.run(
        ["git", "-C", str(tree), "diff-tree", "-r", "--quiet", base, "HEAD", "--", *HELD],
        capture_output=True,
        text=True,
    )
    if diff.returncode not in (0, 1):
        diff.check_returncode()
    return diff.returncode == 1


def jobs(
    suite: str, revision: str, base: str, swept: Sequence[Tuple[str, int]], first: int = 1
) -> List[dict]:
    """The run jobs, each runs `from` to `from` + `runs` - 1 of a side's sweep of a scenario, of
    runs `first` to the last `swept` gives it: a run a job for `pr`, ten for `main`, six for
    `widen`, and the whole sweep for `long`."""
    sides = [("revision", "head", revision)] + ([("base", "base", base)] if base else [])
    found = []
    for side, said, sha in sides:
        for name, last in swept:
            size = A_JOB.get(suite, last - first + 1)
            for start in range(first, last + 1, size):
                count = min(size, last - start + 1)
                found.append(
                    {
                        "side": side,
                        "revision": sha,
                        "scenario": name,
                        "from": start,
                        "runs": count,
                        "name": f"{said} {sha[:7]}: {_part(suite, name, start, count)}",
                    }
                )
    return found


def widen(matrix: dict, revision: str, base: str) -> List[dict]:
    """The run jobs that follow those of a pull request's first seeds, which came to `matrix`: of
    each group a cell of which has an outcome at both sides and the two differ, the rest of main's
    seeds of each of `pr`'s rates, at `revision` and at `base`; none where no cell does. Any field
    counts, as nothing moved only where every outcome is the same."""
    moved = {cell["group"] for cell in matrix["cells"] if cell["change"] == "moved"}
    found = []
    for group, grow in GROW_3_ADDERS.items():
        if group not in moved:
            continue
        for rate, seeds in RATES["pr"].items():
            swept = [(grow.format(rate), RATES["main"][rate])]
            found += jobs("widen", revision, base, swept, seeds + 1)
    # Every job at the revision first, as plan prints them.
    return sorted(found, key=lambda job: job["side"] == "base")


def title(matrix: dict) -> str:
    """The verdict of each group of `matrix` in a few words, after the group's name: whether its
    runs moved, and which way. A verdict of a head chat-sim does not build at, or of runs with
    nothing to compare, is said once."""
    if matrix["verdict"] == "unbuilt" or not compares(matrix):
        return _title(matrix, matrix)
    return "; ".join(f"{GROUPS[one['group']]} {_title(matrix, one)}" for one in matrix["groups"])


def _title(matrix: dict, judged: dict) -> str:
    """The verdict of `judged`, the matrix or one of its groups, in a few words: of a group that
    forks earlier or later, at its first rate that does."""
    counts, verdict = judged["counts"], judged["verdict"]
    if verdict in _TITLES:
        return _TITLES[verdict].format(c=counts)
    if verdict in ("earlier", "later"):
        one = next(one for one in judged["results"] if one["result"] == verdict)
        tested = f"{one['later']} later, {one['earlier']} earlier (p {_p(one['p'])})"
        return f"forks {verdict} at {_rate(one['rate'])}: {tested}"
    if matrix["base"] is None:
        return "no base, no comparison"
    cells = matrix["cells"]
    if judged is not matrix:
        cells = [cell for cell in cells if cell["group"] == judged["group"]]
    if any(cell["unbuilt"]["base"] for cell in cells):
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


def told(matrix: dict, result: dict) -> str:
    """What a group's line says of one of its scenarios, by its rate and its `result` in
    `matrix`: of one whose runs all came out as at the base, the seeds they ran; of one whose runs
    moved, how many first forked later, earlier and at the same epoch, and the sign test's p; after
    either, how many runs the base gave no outcome in; of any other, why there is nothing to
    compare."""
    return f"{_rate(result['rate'])}: {result_words(matrix, result)}"


def result_words(matrix: dict, result: dict) -> str:
    """What `told` says of a scenario after its rate."""
    said = result["result"]
    cells = [cell for cell in matrix["cells"] if cell["scenario"] == result["scenario"]]
    if said == "same":
        return f"nothing moved in {_seeds(result['seeds'])}{_base_gap(cells)}"
    if said in ("within", "earlier", "later"):
        later, earlier, zero = result["later"], result["earlier"], result["zero"]
        tested = f"{later} later, {earlier} earlier, {zero} tied (p {_p(result['p'])})"
        return tested + _base_gap(cells)
    if said == "no-outcome":
        missing = sum(cell["head"] is None for cell in cells)
        return f"no outcome in {missing} of {len(cells)} runs"
    if said == "no-base":
        if any(cell["unbuilt"]["base"] for cell in cells):
            return "does not build at the base"
        return "no outcome at the base"
    return RESULTS[said] if said is not None else "not compared"


def _base_gap(cells: Sequence[dict]) -> str:
    """How many of `cells` the base gave no outcome in, which the sign test leaves out, after a
    comma; nothing where it gave every one."""
    missing = [cell for cell in cells if cell["base"] is None]
    if not missing:
        return ""
    if any(cell["unbuilt"]["base"] for cell in missing):
        return f", does not build at the base in {len(missing)} of {len(cells)} runs"
    return f", no outcome at the base in {len(missing)} of {len(cells)} runs"


def run_page(cell: dict) -> str:
    """Where the page of `cell`'s run is, under its pull request's report."""
    return f"head/{cell['scenario']}/{cell['run']}/"


def treed(cell: dict) -> bool:
    """Whether `cell` is of a seed every pull request runs of each rate, seeds 0 to 2: those its
    report draws a row of trees of, and its comment pictures."""
    return cell["seed"] < min(RATES["pr"].values())


def pictured(matrix: dict) -> List[str]:
    """The groups of `matrix` a pull request's report keeps a picture of, for the comment: those a
    run of which moved, among the runs a picture draws."""
    moved = {cell["group"] for cell in matrix["cells"] if treed(cell) and cell["change"] == "moved"}
    return [one["group"] for one in matrix["groups"] if one["group"] in moved]


def picture(group: str, dark: bool) -> str:
    """The file under a pull request's report that holds the picture of `group`'s trees, in the
    colours of the dark theme or of the light."""
    return f"trees-{group}{'-dark' if dark else ''}.svg"


def concluded(matrix: dict, required: bool) -> str:
    """The check's conclusion of `matrix`: a failure where the verdict of a group's runs fails it,
    else a success where one's succeeds, else neutral, so that one group's runs forking later
    cannot hide the other's forking earlier."""
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
    """The comment on a pull request. Where every run was compared and came out the same as the
    base's, one line that says so and the links. Else a line for each group, its verdict and what
    each of its scenarios came to; the picture of each group whose runs moved, where `facts` says
    where the pictures are; and the links. A head chat-sim does not build at has the verdict line
    alone, and runs with nothing to compare have it and the links."""
    if matrix["verdict"] == "unbuilt":
        return _unbuilt_comment(facts)
    if not compares(matrix):
        return f"**{headline(matrix)}** {ran_at(facts, _commit)}\n\n{_links(facts)}\n"
    cells, head, base = matrix["cells"], _commit(facts["revision"]), _merge_base(facts)
    if matrix["verdict"] == "same" and matrix["counts"]["compared"] == len(cells):
        groups = " and ".join(GROUPS[one["group"]] for one in matrix["groups"])
        return (
            f"{_MARKS['same']} **Nothing moved.** All {len(cells)} runs of {groups} at {head} "
            f"replayed {base} exactly. {_links(facts)}\n"
        )
    said = [_group_line(matrix, one) for one in matrix["groups"]]
    # A backslash breaks the line wherever the comment is rendered, the check's summary and the
    # run's summary too, where the end of a line alone does not.
    lines = [f"{line}\\" for line in said[:-1]] + said[-1:]
    shown = pictured(matrix) if facts["pictures"] else []
    for one in shown:
        lines += ["", _picture(matrix, one, facts["pictures"])]
    drawn = f"{_pictures_said(matrix, shown)}. " if shown else ""
    lines += ["", f"{drawn}{_links(facts)} · {head} against {base}"]
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
    swept = scenarios(args.suite, args.expected)
    for name in ("suite", "revision", "base", "pr", "check_run"):
        print(f"{name}={getattr(args, name)}")
    print(f"runs={json.dumps(jobs(args.suite, args.revision, args.base, swept))}")
    print(f"scenarios={' '.join(name for name, _ in swept)}")
    return 0


def _widen(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    for flag, sha in (("--revision", args.revision), ("--base", args.base)):
        if _SHA.fullmatch(sha) is None:
            parser.error(f"{flag}: {sha!r} is not a commit's 40 hex digits")
    asked = _asked(parser, args.asked)
    matrix = _matrix(args.matrix)
    if matrix is None:
        parser.error(f"--matrix: {args.matrix} is not a matrix tools/sim matrix writes")
    more = widen(matrix, args.revision, args.base)
    print(f"more={json.dumps(more)}")
    print(f"asked={json.dumps(asked + more)}")
    return 0


def _comment(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    _check_handed(parser, args)
    if args.report_url is not None and _URL.fullmatch(args.report_url) is None:
        parser.error(f"--report-url: {args.report_url!r} is not an http(s) address")
    if args.pictures is not None and _URL.fullmatch(args.pictures) is None:
        parser.error(f"--pictures: {args.pictures!r} is not an http(s) address")
    if args.pictures is not None and not args.pictures.endswith("/"):
        parser.error(f"--pictures: {args.pictures!r} is not a directory's address, ending in /")
    if args.pictures is not None and args.suite != "pr":
        parser.error("--pictures: given for pr, and only then")
    facts = {
        "suite": args.suite,
        "revision": args.revision,
        "base": args.base,
        "pr": args.pr,
        "chat_sim": args.chat_sim,
        "run_url": args.run_url,
        "report_url": args.report_url,
        "pictures": args.pictures,
    }
    if args.suite == "long":
        if args.runs is None or args.matrix is not None:
            parser.error("RUNS: the long suite is written from its runs, and not from --matrix")
        if (args.base_runs is None) != (not args.base):
            parser.error("--base-runs: given with --base, and only with it")
        swept = scenarios(args.suite, args.expected)
        return _written(render(swept, args.runs, args.base_runs, facts), args.out)
    if args.matrix is None or args.runs is not None or args.base_runs is not None:
        parser.error(f"--matrix: the {args.suite} suite is written from its matrix alone")
    matrix = _matrix(args.matrix)
    if matrix is None:
        parser.error(f"--matrix: {args.matrix} is not a matrix tools/sim matrix writes")
    if (matrix["base"] is None) != (not args.base):
        parser.error("--matrix: a matrix with a base is given with --base, and only with it")
    if args.suite == "main":
        return _written(render_main(matrix, facts), args.out)
    return _written(render_pr(matrix, facts), args.out)


def _written(comment: str, out: Optional[Path]) -> int:
    """Writes `comment` to `out`, or without `out` to standard output."""
    if out is None:
        sys.stdout.write(comment)
    else:
        out.write_text(comment, encoding="utf-8")
    return 0


def _measurement(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    measured = measurement(args.tree)
    if measured is None:
        parser.error(f"--tree: {args.tree} is a shallow clone, or no commit touches {MEASURED}")
    print(f"measurement={measured}")
    return 0


def _changed(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if args.since and _SHA.fullmatch(args.since) is None:
        parser.error(f"--since: {args.since!r} is not a commit's 40 hex digits")
    print(f"changed={str(changed(args.tree, args.since)).lower()}")
    return 0


def _running(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if _SHA.fullmatch(args.revision) is None:
        parser.error(f"--revision: {args.revision!r} is not a commit's 40 hex digits")
    if args.base and _SHA.fullmatch(args.base) is None:
        parser.error(f"--base: {args.base!r} is not a commit's 40 hex digits")
    asked = _asked(parser, args.asked)
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


def _asked(parser: argparse.ArgumentParser, file: Path) -> List[dict]:
    """The run jobs `file` holds as plan prints them, each with its side and how many runs."""
    try:
        asked = json.loads(_read(parser, "--asked", file))
    except ValueError as error:
        parser.error(f"--asked: {file}: {error}")
    if not isinstance(asked, list) or not all(
        isinstance(job, dict)
        and job.get("side") in ("revision", "base")
        and _count(job.get("runs"))
        for job in asked
    ):
        parser.error(f"--asked: {file}: not the run jobs plan prints")
    return asked


def _count(value: object) -> bool:
    """Whether `value` is a whole number above zero, as JSON gives one."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


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
        for group, grow in GROW_3_ADDERS.items()
        for rate in RATES["main"]
        if grow.format(rate) == name
    )
    if count == 1:
        return f"{rate} seed {first - 1}"
    return f"{rate} seeds {first - 1} to {first + count - 2}"


def ran_at(facts: dict, commit: Callable[[str], str]) -> str:
    """What the verdict line says after its verdict: the commit that ran and the merge base it
    was held to, each as `commit` draws it."""
    said = f"at {commit(facts['revision'])}"
    if facts["base"]:
        said += f" against its merge base {commit(facts['base'])}"
    return said


def _verdict_line(said: str, facts: dict) -> str:
    """The verdict line: what was said, of which commit against which, and the links."""
    line = f"**{said}** {ran_at(facts, _commit)}"
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


def _group_line(matrix: dict, group: dict) -> str:
    """The line of the comment on a pull request on the runs of `group`, one of `matrix`'s: the
    mark of its verdict and the verdict in words, then what each of its scenarios came to, by its
    rate."""
    verdict = group["verdict"]
    line = f"{_MARKS[verdict]} **{GROUPS[group['group']]}: {RESULTS[verdict]}**"
    return line + "".join(f" · {told(matrix, one)}" for one in group["results"])


def _picture(matrix: dict, group: str, at: str) -> str:
    """The picture of `group`'s trees as the comment on a pull request shows it, its files read
    under `at`: the dark theme's file to a reader in the dark theme, and what it draws as the
    words that stand for it."""
    light, dark = (urljoin(at, picture(group, theme)) for theme in (False, True))
    return (
        f'<picture><source media="(prefers-color-scheme: dark)" srcset="{dark}">'
        f'<img alt="{_pictures_said(matrix, [group])}" src="{light}"></picture>'
    )


def _pictures_said(matrix: dict, groups: Sequence[str]) -> str:
    """What the pictures of `groups` draw, in words: which seeds of `matrix`, and how."""
    named = " and ".join(GROUPS[one] for one in groups)
    drawn = [cell["seed"] for cell in matrix["cells"] if cell["group"] in groups and treed(cell)]
    return f"{named}'s {_seeds(drawn)}: the head's tree over the base's, a run that moved outlined"


def _merge_base(facts: dict) -> str:
    """The merge base the head's runs were held to, as the comment on a pull request names it."""
    return f"its merge base {_commit(facts['base'])}"


def _links(facts: dict) -> str:
    """The links of the comment on a pull request: where the report has a page, the trees of its
    runs there, the page, which the site keeps while the pull request is open, and the graph over
    main, the page above it; and the run."""
    report = facts["report_url"]
    if not report:
        return f"[The run]({facts['run_url']})"
    return (
        f"[The trees]({report}#runs) · [the report]({report}), kept while the pull request is "
        f"open · [the graph over main]({urljoin(report, '../')}) · [the run]({facts['run_url']})"
    )


def words(cell: dict) -> str:
    """A cell in a few words: how the head's run ended, at the epoch of its first fork or else of
    the trunk, and the lines and the members off the trunk where there are any and how many of
    those never joined where any did; or why it has no outcome at the head."""
    if cell["head"] is None:
        return _missing(cell, "head")
    said = _ended_at(cell["epochs"]["head"], cell["ended"]["head"])
    off = _off_trunk(cell["head"])
    return f"{said}, {off}" if off else said


def _extent(spread: dict) -> str:
    """The lowest, the highest and the median of a summary's values, or `-` without any."""
    if spread["median"] is None:
        return "-"
    return f"{spread['lowest']} to {spread['highest']}, median {spread['median']}"


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


def _missing(cell: dict, side: str) -> str:
    """Why a side of a cell has no outcome."""
    return "does not build" if cell["unbuilt"][side] else "no outcome"


def again(cell: dict, revision: str, chat_sim: str) -> List[str]:
    """The commands that run a cell again at `revision` of logos-chat, from chat-sim at
    `chat_sim`: its checkout, the lines of sweep.yml's step that pin logos-chat, the build, and
    the cell's run of its sweep."""
    scenario = cell["scenario"]
    return [
        f"git clone {CHAT_SIM} && cd chat-sim && git checkout {chat_sim}",
        "pinned=$(jq -r '.nodes.\"logos-chat\".locked.rev' flake.lock)",
        f'sed -i "s/$pinned/{revision}/g" flake.nix Cargo.toml',
        "nix flake lock",
        "nix develop -c cargo build --release -p harness --bin harness-run",
        f"python3 tools/sim sweep tools/sim/scenarios/{scenario}.json --from {cell['run']} "
        f"--runs 1 --one-process target/release/harness-run --out tmp/{scenario}-seed-"
        f"{cell['seed']}",
    ]


def _rate(rate: Optional[str]) -> str:
    return "-" if rate is None else rate


def _seeds(seeds: Sequence[int]) -> str:
    """Seeds by the lowest and the highest of them."""
    return f"seeds {min(seeds)} to {max(seeds)}"


def _p(p: float) -> str:
    """A sign test's p to two figures, and below a thousandth as that."""
    return "< 0.001" if p < 0.001 else f"{p:.2g}"


def came_to(now: Optional[List[dict]]) -> str:
    """What the runs of a sweep came to, `now` their outcomes: how each came out, apart by ` · `
    since a run can come out in words that hold a comma, or `no outcome` without them."""
    if now is None:
        return "no outcome"
    return " · ".join(came_out(one) for one in now)


def came_out(outcome: dict) -> str:
    """How a run came out, in the words of a verdict: its verdict, or how its lineage says a run
    of a `grow` step with none ended, split at its first fork, stalled at the trunk's epoch,
    whole, or whole with members behind."""
    verdict, ended = outcome.get("verdict"), outcome.get("ended")
    if verdict is not None or ended is None:
        return verdict or "no verdict"
    if ended == "split":
        return f"split at epoch {outcome['first_fork']}"
    if ended == "stalled":
        return f"stalled at epoch {outcome['trunk_epoch']}"
    return "whole" if ended == "whole" else "whole, members behind"


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
