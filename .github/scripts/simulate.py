#!/usr/bin/env python3
"""Plans the jobs of simulate.yml and writes the comment on what they came to.

simulate.yml runs chat-sim at a logos-chat revision it is handed, and at a base revision too when
it is handed one, in one of three suites. `pr` and `main` run the goal, a group of 51 that saro
grows toward epoch 50, at rates of loss: `pr` seeds 0 and 1 of the three lossy rates, a job a run,
at the revision and at the base; `main` seeds 0 to 19 of the four rates, two jobs of ten a rate,
and no base. `long` runs the two scenarios of fifty epochs and every other one `tools/sim/expected`
holds but the goal's, a job a scenario a side. `plan` checks what it was handed and prints the run
jobs, and `comment` writes the comment in Markdown: for `pr` and `main` from the matrix
`tools/sim matrix` wrote of the runs, and for `long` a row for each scenario with what
`tools/sim compare` said of its runs against the base's, or against what `tools/sim/expected`
holds when there is no base. `measurement` prints the chat-sim commit a run measures.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# The goal's scenario at a rate of loss in percent. Its files set no seed, so that run k of a
# sweep has seed k - 1.
GOAL = "50-epochs-by-saro-waku-loss-{}"
RATES = {"pr": ("0.1", "1", "5"), "main": ("0", "0.1", "1", "5")}
# How many seeds of each rate a suite runs, from seed 0, and how many of them a job does.
SEEDS = {"pr": 2, "main": 20}
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
MATRIX = {"head", "base", "cells", "counts", "verdict", "summary"}

_SHA = re.compile(r"[0-9a-f]{40}")
_PR = re.compile(r"[1-9][0-9]*")

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
_ENDED = (
    "whole: one group, everyone in it; behind: one group, some members behind; stalled: one "
    "group that stopped growing short of 50; split: it forked"
)
_PR_FOOT = (
    "A cell: how many epochs the group stayed one group (50 is the goal) · how the run ended "
    f"({_ENDED}) · the change against the merge base at the same seed (changed: the run differs "
    "but is no better or worse; re-rolled: the run drew other ids than the base's, so the two "
    "are not compared). One cell moving can be the loss pattern shifting under your change; "
    "main's 20 seeds after the merge are the measurement. This check blocks nothing yet. Run a "
    "cell again with the command in its fold."
)
_OFF = {"lines_off_trunk": "line", "members_off_trunk": "member"}
_MAIN_FOOT = (
    f"A row: the runs at a rate of loss, a seed each, by how each ended ({_ENDED}), and how "
    "many epochs the group stayed one group, from the lowest seed to the highest (50 is the "
    "goal)."
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
    plan.set_defaults(command=_plan)
    comment = commands.add_parser(
        "comment",
        help="write the comment on what the run jobs came to",
        description="Writes the comment in Markdown. For pr and main it is drawn from --matrix: "
        "for pr the verdict, a row a rate and a cell a seed, and a fold for each cell with its "
        "runs and the command that runs it again; for main a row a rate with how its runs "
        "ended. For long it is a row for each scenario from the outcomes.json the run jobs "
        "uploaded under RUNS and the compare.txt written beside it, and the output of each "
        "compare.",
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
    args = parser.parse_args(argv)
    return args.command(parser, args)


def scenarios(suite: str, expected: Path) -> List[Tuple[str, int]]:
    """The scenarios of `suite` and how many runs of each it does.

    For `pr` and `main` the goal's at the suite's rates, as many as its seeds. For `long` the two
    of fifty epochs and every scenario `expected` holds but the goal's, as many as `expected`
    holds for each, and one when it holds none.
    """
    if suite in RATES:
        return [(GOAL.format(rate), SEEDS[suite]) for rate in RATES[suite]]
    goal = {GOAL.format(rate) for rate in RATES["main"]}
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


def title(matrix: dict) -> str:
    """The verdict of `matrix` in a few words: how many of its runs moved, and which way."""
    counts, verdict = matrix["counts"], matrix["verdict"]
    if verdict != "no-base":
        return _TITLES[verdict].format(c=counts)
    if matrix["base"] is None:
        return "no base, no comparison"
    if matrix["base"]["unbuilt"]:
        return "does not build at the base, no comparison"
    return "no outcome at the base, no comparison"


def render_pr(matrix: dict, facts: dict) -> str:
    """The comment on a pull request: the verdict, a row a rate and a cell a seed, what a cell
    says, and under it a fold for each cell with its runs and the command that runs it again."""
    if matrix["verdict"] == "unbuilt":
        return _unbuilt_comment(facts)
    cells = matrix["cells"]
    seeds = sorted({cell["seed"] for cell in cells})
    against = matrix["base"] is not None and matrix["counts"]["compared"] > 0
    lines = [
        _verdict_line(f"chat-sim: {title(matrix)}", facts),
        "",
        "| Loss | " + " | ".join(f"Seed {seed}" for seed in seeds) + " |",
        "|---" * (len(seeds) + 1) + "|",
    ]
    for rate, row in _rows(cells):
        said = {cell["seed"]: _said(cell, against) for cell in row}
        lines.append(f"| {rate} | " + " | ".join(said.get(seed, "-") for seed in seeds) + " |")
    lines += ["", _PR_FOOT]
    for cell in cells:
        opened = " open" if cell["change"] in ("better", "worse") else ""
        lines += [
            "",
            f"<details{opened}><summary>{_rate(cell['rate'])} loss, seed {cell['seed']}: "
            f"{_said(cell, against)}</summary>",
            "",
            f"- **head** {_commit(facts['revision'])} · {_ran(cell, 'head')}",
        ]
        if facts["base"]:
            lines.append(f"- **base** {_commit(facts['base'])} · {_ran(cell, 'base')}")
        lines += ["", "```sh", *_again(cell, facts), "```", "", "</details>"]
    return "".join(f"{line}\n" for line in lines)


def render_main(matrix: dict, facts: dict) -> str:
    """The comment on a point of main: a row a rate with how its runs ended, and the lowest,
    median and highest epochs without a fork."""
    if matrix["verdict"] == "unbuilt":
        return _unbuilt_comment(facts)
    said = f"chat-sim: {matrix['counts']['cells']} runs"
    if matrix["verdict"] == "no-outcome":
        said = f"chat-sim: {title(matrix)}"
    lines = [
        _verdict_line(said, facts),
        "",
        "| Loss | Runs | Whole | Behind | Split | Stalled | Epochs without a fork |",
        "|---|---|---|---|---|---|---|",
    ]
    for one in matrix["summary"]:
        epochs = one["epochs"]
        reached = "-"
        if epochs["median"] is not None:
            reached = f"{epochs['lowest']} to {epochs['highest']}, median {epochs['median']}"
        counted = " | ".join(str(one[ended]) for ended in ("whole", "behind", "split", "stalled"))
        lines.append(f"| {_rate(one['rate'])} | {one['runs']} | {counted} | {reached} |")
    lines += ["", _MAIN_FOOT]
    return "".join(f"{line}\n" for line in lines)


def render(
    swept: Sequence[Tuple[str, int]], runs: Path, base_runs: Optional[Path], facts: dict
) -> str:
    """The comment on the long suite: a head, a row for each scenario, and the output of each
    compare.

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
        swept_at = _swept(runs, name)
        now = _outcomes(swept_at / "outcomes.json")
        compared = _text(swept_at / "compare.txt")
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
    swept = scenarios(args.suite, args.expected)
    for name in ("suite", "revision", "base", "pr"):
        print(f"{name}={getattr(args, name)}")
    print(f"runs={json.dumps(jobs(args.suite, args.revision, args.base, swept))}")
    print(f"scenarios={' '.join(name for name, _ in swept)}")
    return 0


def _comment(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    _check_handed(parser, args)
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
    written = render_pr(matrix, facts) if args.suite == "pr" else render_main(matrix, facts)
    sys.stdout.write(written)
    return 0


def _measurement(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    measured = measurement(args.tree)
    if measured is None:
        parser.error(f"--tree: {args.tree} is a shallow clone, or no commit touches {MEASURED}")
    print(f"measurement={measured}")
    return 0


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
    """What a job runs, as its name says: a scenario of `long`, else a rate and its seeds."""
    if suite == "long":
        return name
    rate = f"{name.removeprefix(GOAL.format(''))}%"
    if count == 1:
        return f"{rate} seed {first - 1}"
    return f"{rate} seeds {first - 1} to {first + count - 2}"


def _verdict_line(said: str, facts: dict) -> str:
    line = f"**{said}** at {_commit(facts['revision'])}"
    if facts["base"]:
        line += f" against its merge base {_commit(facts['base'])}"
    if facts["report_url"]:
        line += f" · [the report]({facts['report_url']})"
    return line + f" · [the run]({facts['run_url']})"


def _unbuilt_comment(facts: dict) -> str:
    lines = [
        _verdict_line("chat-sim does not build against this commit", facts),
        "",
        "No scenario ran: the build's errors are in the run.",
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


def _said(cell: dict, against: bool) -> str:
    """A cell in a few words: the head's epochs without a fork and how it ended, then its
    change, with what moved where it is better or worse; and with `against`, why a cell that
    has an outcome at the head has no change."""
    if cell["head"] is None:
        return _missing(cell, "head")
    epochs, ended = cell["epochs"]["head"], cell["ended"]["head"]
    said = f"{epochs} whole" if ended == "whole" else f"{epochs}, {ended}"
    change, moved = cell["change"], cell["moved"]
    if moved is not None:
        return f"{said}{_moved(moved)}: {change}"
    if change is not None:
        return f"{said}, {change}"
    return f"{said}, {_missing(cell, 'base')} at the base" if against else said


def _moved(moved: dict) -> str:
    """What moved in a cell: the base's epochs or ending, or the head's lines or members off the
    trunk and the base's."""
    part, now, was = moved["part"], moved["head"], moved["base"]
    if part in ("epochs", "ended"):
        return f", was {was}"
    return f", {now} {_OFF[part]}{'' if now == 1 else 's'} off the trunk, was {was}"


def _ran(cell: dict, side: str) -> str:
    """What a side's run of a cell came to: its verdict and how its grow ended."""
    outcome = cell[side]
    if outcome is None:
        return _missing(cell, side)
    return html.escape(f"{outcome['verdict']} · {outcome['came_to']}", quote=False)


def _missing(cell: dict, side: str) -> str:
    """Why a side of a cell has no outcome."""
    return "does not build" if cell["unbuilt"][side] else "no outcome"


def _again(cell: dict, facts: dict) -> List[str]:
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
