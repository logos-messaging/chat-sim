#!/usr/bin/env python3
"""Writes the repository's Pages site from the text CI's sim jobs upload, and keeps on it what
simulate.yml's runs of logos-chat came to.

`report` writes the report of one CI run to `reports/KEY/` of the site, replacing what was
there unless that is the report of a run of a higher number: KEY is `master` for a push to
master and `pr-N` for pull request N. `index` writes the site's landing page, and
`reports/index.html` from the `meta.json` of each report; one it cannot read is left out, with
a warning.

`point` keeps a point of logos-chat's main, a run of simulate.yml's main suite: its
`point.json`, drawn from the run's matrix, and its runs' text, in
`logos-chat/main/<sha>/<measurement>/`. What was there is replaced unless a run of a higher
number wrote it, and then only the newest thirty points keep their runs' text. The points are in
the order of their runs' numbers, which GitHub gives in the order the runs were started and a
re-run keeps; `previous` names the point a new one follows. `pr` keeps the newest run of a
logos-chat pull request in `logos-chat/pr-N/` the same way: its matrix, both sides' runs' text
and its `meta.json`.

The pages run no script, and every text that comes from a run or from a pull request's title
is escaped.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

# The scenarios of the job "Sim in one process", in the order it runs them.
FIFTY_EPOCHS = ("50-epochs", "50-epochs-waku")

AS_EXPECTED = "as expected"
NOT_AS_EXPECTED = "not as expected"
NO_OUTCOME = "no outcome"

# Where the site keeps what simulate.yml's runs of logos-chat came to.
LOGOS_CHAT = "logos-chat"
# How many of the newest points of main keep their runs' text, about 5 MB a point, in a site
# Pages holds to 1 GB. Every point keeps its point.json.
WINDOW = 30
# The text of a run the site keeps. Its report.json, ten times the rest, stays in the artifacts.
TEXTS = ("run.txt", "report.txt", "run.json")

_KEY = re.compile(r"master|pr-([1-9][0-9]*)")
_SHA = re.compile(r"[0-9a-f]{40}")
_URL = re.compile(r"https?://[^\s\"'<>]+")
_TIME = "%Y-%m-%dT%H:%M:%SZ"
_RUN = re.compile(r"[0-9]+")
_HEX = re.compile(r"[0-9a-f]+")
_SCENARIO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# How a commit of logos-chat's main names the pull request it merged: a squash merge's title
# ends with its number, and a merge commit's starts with it, the pull request's title below.
_SQUASHED = re.compile(r"(.+) \(#([1-9][0-9]*)\)")
_MERGE = re.compile(r"Merge pull request #([1-9][0-9]*) from \S+")
# What logos-chat's compare API says of a revision that comes after the point it follows, or is
# its commit at another measurement.
_FOLLOWS = ("ahead", "identical")
_FIELDS = {
    "key": str,
    "name": str,
    "title": str,
    "number": (int, type(None)),
    "sha": str,
    "commit_url": str,
    "run_url": str,
    "run_number": int,
    "time": str,
    "as_expected": int,
    "sweeps": int,
}
_CLASS = {AS_EXPECTED: "good", NOT_AS_EXPECTED: "bad", NO_OUTCOME: "none"}

# What `tools/sim matrix` writes, as the field of each kind, or a shape of its own.
_NONE = type(None)
_ENDINGS = ("whole", "behind", "split", "stalled")
_CHANGES = ("same", "changed", "better", "worse", "re-rolled")
_STANDING = ("epochs", "ended", "lines_off_trunk", "members_off_trunk")
_VERDICTS = (
    "unbuilt",
    "no-outcome",
    "no-base",
    "mixed",
    "better",
    "worse",
    "hint",
    "re-rolled",
    "same",
)
_SIDE = {"runs": (int,), "unbuilt": (int,)}
_MATRIX = {
    "head": _SIDE,
    "base": (dict, _NONE),
    "cells": (list,),
    "counts": {field: (int,) for field in ("cells", "compared", *_CHANGES, "no-outcome")},
    "verdict": (str,),
    "summary": (list, _NONE),
}
_CELL = {
    "scenario": (str,),
    "seed": (int,),
    "run": (int,),
    "loss": (int, float, _NONE),
    "rate": (str, _NONE),
    "head": (dict, _NONE),
    "base": (dict, _NONE),
    "unbuilt": {"head": (bool,), "base": (bool,)},
    "change": (str, _NONE),
    "epochs": {"head": (int, _NONE), "base": (int, _NONE)},
    "ended": {"head": (str, _NONE), "base": (str, _NONE)},
    "moved": (dict, _NONE),
}
_OUTCOME = {
    "seed": (int,),
    "network_seed": (int, _NONE),
    "conversation": (str, _NONE),
    "members_digest": (str, _NONE),
    "verdict": (str, _NONE),
    "at_tip": (int, _NONE),
    "seated": (int, _NONE),
    "virtual_s": (int, float, _NONE),
    "trunk_epoch": (int, _NONE),
    "ended": (str, _NONE),
    "lines_off_trunk": (int, _NONE),
    "members_off_trunk": (int, _NONE),
    "came_to": (str,),
    "stopped": (str, _NONE),
}
_MOVED = {"part": (str,), "head": (int, str), "base": (int, str)}
_SUMMARY = {
    "scenario": (str,),
    "loss": (int, float, _NONE),
    "rate": (str, _NONE),
    "runs": (int,),
    "no-outcome": (int,),
    **{ended: (int,) for ended in _ENDINGS},
    "epochs": {
        "lowest": (int, _NONE),
        "median": (int, float, _NONE),
        "highest": (int, _NONE),
    },
    "members_off_trunk": {"median": (int, float, _NONE)},
}
# What `write_point` and `write_pr` write.
_POINT = {
    "logos_chat": (str,),
    "measurement": (str,),
    "chat_sim": (str,),
    "time": (str,),
    "run_url": (str,),
    "run_number": (int,),
    "merged": (list,),
    "previous": (dict, _NONE),
    "cells": (list,),
    "summary": (list,),
}
_MERGED = {"number": (int,), "title": (str,)}
_PREVIOUS = {"logos_chat": (str,), "measurement": (str,), "rerolled": (bool,)}
_PR_META = {
    "pr": (int,),
    "revision": (str,),
    "base": (str, _NONE),
    "chat_sim": (str,),
    "measurement": (str,),
    "run_url": (str,),
    "run_number": (int,),
    "time": (str,),
}

_STYLE = """
:root{color-scheme:light dark;--fg:#1f2328;--muted:#59636e;--bg:#fff;--box:#f6f8fa;
--line:#d1d9e0;--link:#0969da;--good:#1a7f37;--bad:#cf222e;--none:#9a6700}
@media (prefers-color-scheme:dark){:root{--fg:#f0f6fc;--muted:#9198a1;--bg:#0d1117;
--box:#151b23;--line:#3d444d;--link:#4493f8;--good:#3fb950;--bad:#f85149;--none:#d29922}}
body{max-width:72rem;margin:0 auto;padding:1.5rem 1rem 3rem;color:var(--fg);
background:var(--bg);font:15px/1.5 system-ui,sans-serif}
a{color:var(--link)}
h1{overflow-wrap:anywhere}
h2{margin-top:2.5rem}
h3{margin-top:2rem}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{margin:.25rem 0 1rem;padding:.75rem;overflow-x:auto;font-size:12px;line-height:1.45;
background:var(--box);border:1px solid var(--line);border-radius:6px}
.wide{overflow-x:auto}
table{border-collapse:collapse}
th,td{padding:.3rem 1.25rem .3rem 0;border-bottom:1px solid var(--line);text-align:left;
vertical-align:top}
summary{cursor:pointer}
.file{margin:.75rem 0 0}
.muted{color:var(--muted)}
.good{color:var(--good)}
.bad{color:var(--bad)}
.none{color:var(--none)}
"""


class Unreadable(Exception):
    """A file of the site, or one a command is handed, that cannot be read, and why."""


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs one command and returns the exit status: 0, or 2 when its input is refused."""
    parser = argparse.ArgumentParser(
        prog="pages.py",
        description="Writes the repository's Pages site from the text CI's sim jobs upload.",
    )
    commands = parser.add_subparsers(metavar="COMMAND", required=True)
    report = commands.add_parser(
        "report",
        help="write the report of one CI run",
        description="Writes the report of one CI run to reports/KEY/ of SITE: a page, the text "
        "files it shows, and meta.json, which index reads. What was there is replaced, unless "
        "it is the report of a run of a higher number.",
    )
    report.add_argument(
        "artifacts",
        metavar="ARTIFACTS",
        type=Path,
        help="the text the sim jobs uploaded: a directory for each scenario they ran, named "
        "after it. One that is not there holds nothing",
    )
    report.add_argument("site", metavar="SITE", type=Path, help="the site, as gh-pages holds it")
    report.add_argument(
        "--expected",
        metavar="DIR",
        type=Path,
        required=True,
        help="tools/sim/expected: the outcomes of the scenarios CI sweeps, a file a scenario",
    )
    report.add_argument("--key", required=True, help="master, or pr-N for pull request N")
    report.add_argument("--title", default="", help="the title of the pull request")
    report.add_argument("--sha", required=True, help="the commit the run tested")
    report.add_argument("--commit-url", metavar="URL", required=True, help="the commit's page")
    report.add_argument("--run-url", metavar="URL", required=True, help="the CI run's page")
    report.add_argument(
        "--run-number",
        metavar="N",
        type=int,
        required=True,
        help="the CI run's number, which a re-run keeps",
    )
    report.add_argument("--time", required=True, help="when it ran, as 2026-10-22T07:30:00Z")
    report.set_defaults(command=_report)
    index = commands.add_parser(
        "index",
        help="write the landing page and the list of the reports",
        description="Writes index.html and reports/index.html of SITE, from the meta.json of "
        "each report, and makes sure SITE holds .nojekyll. A meta.json it cannot read is left "
        "out, with a warning.",
    )
    index.add_argument("site", metavar="SITE", type=Path, help="the site, as gh-pages holds it")
    index.set_defaults(command=_index)
    point = commands.add_parser(
        "point",
        help="keep a point of logos-chat's main",
        description="Writes the point of a run of the main suite to "
        "logos-chat/main/SHA/MEASUREMENT/ of SITE: point.json and each run's run.txt, "
        "report.txt and run.json. What was there is replaced, unless it is the point of a run "
        "of a higher number. Then removes the runs' text of each point past the newest "
        f"{WINDOW}, by run number. A matrix that read no run at the revision, built or not, is "
        "refused.",
    )
    _add_ran(point, runs="the run jobs uploaded")
    point.add_argument(
        "--commits",
        metavar="FILE",
        type=Path,
        help="what logos-chat's compare API answers for the point the site holds before this "
        "one and the revision: given when the site holds one, and only then. Refused unless "
        "it says the revision is ahead of that point, or its commit",
    )
    point.set_defaults(command=_point)
    before = commands.add_parser(
        "previous",
        help="name the point a new one follows",
        description="Prints, as a line for $GITHUB_OUTPUT, the logos-chat commit of the point "
        "of SITE of the highest run number below the run's but the one at the revision and the "
        "measurement, or nothing when there is none.",
    )
    before.add_argument("site", metavar="SITE", type=Path, help="the site, as gh-pages holds it")
    before.add_argument("--revision", required=True, help="the logos-chat commit of the point")
    before.add_argument(
        "--measurement", metavar="SHA", required=True, help="the chat-sim commit it measures"
    )
    before.add_argument(
        "--run-number",
        metavar="N",
        type=int,
        required=True,
        help="the workflow run's number, which a re-run keeps",
    )
    before.set_defaults(command=_previous)
    pr = commands.add_parser(
        "pr",
        help="keep the run of a logos-chat pull request",
        description="Writes the run of the pr suite for a logos-chat pull request to "
        "logos-chat/pr-N/ of SITE: the matrix, each run's run.txt, report.txt and run.json "
        "at the revision under head/ and at the base under base/, and meta.json. What was "
        "there is replaced, unless it is a run of a higher number.",
    )
    _add_ran(pr, runs="the run jobs at the revision uploaded")
    pr.add_argument("--pr", metavar="N", type=int, required=True, help="the pull request")
    pr.add_argument("--base", default="", help="the logos-chat commit it was compared with")
    pr.add_argument(
        "--base-runs",
        metavar="DIR",
        type=Path,
        help="what the run jobs at the base uploaded, given with --base and only with it",
    )
    pr.set_defaults(command=_pr)
    args = parser.parse_args(argv)
    return args.command(parser, args)


def outcome(sweep: Path) -> str:
    """What `tools/sim compare` said of the sweep in `sweep`, by the status it exited with."""
    try:
        status = (sweep / "compare.status").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return NO_OUTCOME
    return {"0": AS_EXPECTED, "1": NOT_AS_EXPECTED}.get(status, NO_OUTCOME)


def write_report(artifacts: Path, site: Path, swept: Sequence[str], meta: dict) -> dict:
    """Writes the report of one run to `reports/<key>/` of `site`, replacing what was there.

    `swept` names the scenarios CI sweeps, and `meta` says what ran. Returns `meta` with how
    many of the sweeps came out as expected, as the report's meta.json holds it.
    """
    out = site / "reports" / meta["key"]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    def show(*parts: str) -> str:
        """A file of the artifacts as the page shows it: a link to its copy, and its text."""
        relative = Path(*parts)
        source = artifacts / relative
        if not source.is_file():
            missing = '<span class="muted">was not uploaded</span>'
            return f'<p class="file">{_e(relative.name)} {missing}</p>\n'
        copy = out / relative
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, copy)
        text = _e(source.read_text(encoding="utf-8", errors="replace"))
        link = _link(relative.as_posix(), relative.name)
        return f'<p class="file">{link}</p>\n<pre>{text}</pre>\n'

    outcomes = [(name, outcome(artifacts / name)) for name in swept]
    held = sum(said == AS_EXPECTED for _, said in outcomes)
    meta = dict(meta, as_expected=held, sweeps=len(swept))
    rows = "".join(
        f'<tr><td>{_link("#" + name, name)}</td><td class="{_CLASS[said]}">{said}</td></tr>\n'
        for name, said in outcomes
    )
    body = (
        f'<p class="muted">{_link("../../", "chat-sim")} / {_link("../", "Reports")}</p>\n'
        f"<h1>{_e(_heading(meta))}</h1>\n"
        f"<p>{_facts(meta)}</p>\n"
        f'<p>{_link("#held", "Held to what is expected")}: {_tally(meta)} · '
        f'{_link("#fifty-epochs", "Fifty epochs")}</p>\n'
        '<h2 id="held">Held to what is expected</h2>\n'
        "<p>Each scenario CI sweeps, held by <code>tools/sim compare</code> to the outcomes "
        "<code>tools/sim/expected/</code> holds for its runs.</p>\n"
        '<div class="wide"><table>\n'
        "<thead><tr><th>Scenario</th><th>Outcome</th></tr></thead>\n"
        f"<tbody>\n{rows}</tbody>\n"
        "</table></div>\n"
        + "".join(_sweep(artifacts / name, name, said, show) for name, said in outcomes)
        + '<h2 id="fifty-epochs">Fifty epochs</h2>\n'
        '<p>The runs of the job "Sim in one process", each held to what its scenario '
        "expects.</p>\n" + "".join(_fifty(artifacts / name, name, show) for name in FIFTY_EPOCHS)
    )
    page = _page(f"chat-sim: {_heading(meta)}", body)
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def write_index(site: Path) -> None:
    """Writes the landing page and `reports/index.html` of `site`, and its `.nojekyll`.

    The list has master first and then the pull requests, the highest number first. A report
    whose meta.json `read_meta` refuses is left out, and a warning on standard error says why.
    """
    reports = site / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    metas = []
    for path in sorted(reports.glob("*/meta.json")):
        try:
            metas.append(read_meta(path))
        except Unreadable as refused:
            print(f"::warning::{refused}", file=sys.stderr)
    metas.sort(key=lambda meta: (meta["number"] is not None, -(meta["number"] or 0)))
    rows = "".join(
        "<tr>"
        f"<td>{_e(_heading(meta))}</td>"
        f"<td>{_link(meta['commit_url'], meta['sha'][:7], code=True)}</td>"
        f"<td>{_when(meta)}</td>"
        f'<td class="{"good" if meta["as_expected"] == meta["sweeps"] else "bad"}">'
        f"{_tally(meta)}</td>"
        f"<td>{_link(meta['key'] + '/', 'report')}</td>"
        f"<td>{_link(meta['run_url'], 'CI run')}</td>"
        "</tr>\n"
        for meta in metas
    )
    listed = (
        '<div class="wide"><table>\n'
        "<thead><tr><th>What ran</th><th>Commit</th><th>When (UTC)</th><th>Sweeps</th>"
        "<th></th><th></th></tr></thead>\n"
        f"<tbody>\n{rows}</tbody>\n"
        "</table></div>\n"
        if metas
        else "<p>No CI run has published a report yet.</p>\n"
    )
    body = (
        f'<p class="muted">{_link("../", "chat-sim")}</p>\n'
        "<h1>Reports</h1>\n"
        "<p>What CI's simulations came to in the newest run on master and on each open pull "
        "request.</p>\n" + listed
    )
    (reports / "index.html").write_text(_page("chat-sim: reports", body), encoding="utf-8")
    body = (
        "<h1>chat-sim</h1>\n"
        "<p>Simulations of logos-chat groups: does a group of logos-chat clients still hold one "
        "group when the network delays and loses what its members send?</p>\n"
        "<ul>\n"
        f"<li>{_link('guide/', 'Guide')}</li>\n"
        f"<li>{_link('reports/', 'Reports')}</li>\n"
        "</ul>\n"
    )
    (site / "index.html").write_text(_page("chat-sim", body), encoding="utf-8")
    (site / ".nojekyll").touch()


def read_meta(path: Path) -> dict:
    """The meta.json of a report, refused unless it is one `write_report` writes."""
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise Unreadable(f"{path}: {error}") from None
    if not isinstance(meta, dict):
        raise Unreadable(f"{path}: not an object")
    for field, kind in _FIELDS.items():
        value = meta.get(field)
        if isinstance(value, bool) or not isinstance(value, kind):
            raise Unreadable(f"{path}: {field} is missing, or not of its kind")
    key = _KEY.fullmatch(meta["key"])
    if key is None or meta["key"] != path.parent.name:
        raise Unreadable(f"{path}: key {meta['key']!r} does not name its directory")
    if meta["number"] != (None if key[1] is None else int(key[1])):
        raise Unreadable(f"{path}: number {meta['number']} is not that of {meta['key']}")
    if _SHA.fullmatch(meta["sha"]) is None:
        raise Unreadable(f"{path}: sha is not a commit")
    for field in ("commit_url", "run_url"):
        if _URL.fullmatch(meta[field]) is None:
            raise Unreadable(f"{path}: {field} is not an http(s) address")
    try:
        datetime.strptime(meta["time"], _TIME)
    except ValueError:
        raise Unreadable(f"{path}: time is not of the form 2026-10-22T07:30:00Z") from None
    return meta


def write_point(
    matrix: dict, runs: Path, site: Path, facts: dict, merged: List[dict], before: Optional[dict]
) -> dict:
    """Writes the point of `matrix`, a matrix of a run of main, to
    `logos-chat/main/<sha>/<measurement>/` of `site`, replacing what was there: point.json, and
    each run's text under `runs` at any depth in `<scenario>/<run>/`, its run by number.

    `facts` says what ran, `merged` the pull requests of logos-chat merged since `before`, the
    point it follows, or `None`. Returns the point, as `read_point` reads it back.
    """
    out = _point_at(site, facts["logos_chat"], facts["measurement"])
    copies = _texts(runs, out)
    point = {
        **facts,
        "merged": merged,
        "previous": None
        if before is None
        else {
            "logos_chat": before["logos_chat"],
            "measurement": before["measurement"],
            "rerolled": rerolled(before, matrix),
        },
        "cells": matrix["cells"],
        "summary": matrix["summary"],
    }
    _replace(out, copies)
    (out / "point.json").write_text(json.dumps(point, indent=1) + "\n", encoding="utf-8")
    return read_point(out / "point.json")


def write_pr(matrix: dict, runs: Path, base_runs: Optional[Path], site: Path, meta: dict) -> dict:
    """Writes the run of a logos-chat pull request to `logos-chat/pr-N/` of `site`, replacing
    what was there: `matrix` as matrix.json, each run's text under `runs` in
    `head/<scenario>/<run>/` and under `base_runs` in `base/<scenario>/<run>/`, at any depth and
    each run by number, and `meta`, which says what ran, as meta.json.

    Returns `meta`, as `read_pr_meta` reads it back.
    """
    out = site / LOGOS_CHAT / f"pr-{meta['pr']}"
    copies = _texts(runs, out / "head")
    if base_runs is not None:
        copies += _texts(base_runs, out / "base")
    _replace(out, copies)
    (out / "matrix.json").write_text(json.dumps(matrix, indent=1) + "\n", encoding="utf-8")
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return read_pr_meta(out / "meta.json")


def points(site: Path) -> List[Tuple[Path, dict]]:
    """Each point of logos-chat's main `site` holds, its directory and its point.json, the
    newest first by run number. A point.json `read_point` refuses is left out, and a warning on
    standard error says why."""
    found = []
    for path in sorted((site / LOGOS_CHAT / "main").glob("*/*/point.json")):
        try:
            found.append((path.parent, read_point(path)))
        except Unreadable as refused:
            print(f"::warning::{refused}", file=sys.stderr)
    return sorted(found, key=_newest, reverse=True)


def previous(
    found: Sequence[Tuple[Path, dict]], revision: str, measurement: str, run_number: int
) -> Optional[dict]:
    """The point a new one at `revision` and `measurement`, of run `run_number`, follows: of
    `found`, the one of the highest run number below `run_number` but one at both, or `None`."""
    for _, point in sorted(found, key=_newest, reverse=True):
        at = (point["logos_chat"], point["measurement"])
        if point["run_number"] < run_number and at != (revision, measurement):
            return point
    return None


def prune(found: Sequence[Tuple[Path, dict]]) -> List[Path]:
    """Removes the runs' text from each point of `found` past the newest `WINDOW` by run
    number, each `<scenario>/<run>/` it empties with it, and returns the directories it removed
    text from. A point keeps its point.json and every other file."""
    pruned = []
    for directory, _ in sorted(found, key=_newest, reverse=True)[WINDOW:]:
        texts = [path for name in TEXTS for path in directory.glob(f"*/*/{name}") if path.is_file()]
        for path in texts:
            path.unlink()
        for emptied in (
            *{path.parent for path in texts},
            *{path.parent.parent for path in texts},
        ):
            if not any(emptied.iterdir()):
                emptied.rmdir()
        if texts:
            pruned.append(directory)
    return pruned


def rerolled(was: dict, now: dict) -> bool:
    """Whether a cell of `now` drew other ids than the cell of `was` at its scenario and seed,
    the group's or the members', where both drew ids: a re-roll, as `tools/sim compare` tells
    one."""
    held = {(cell["scenario"], cell["seed"]): _ids(cell) for cell in was["cells"]}
    for cell in now["cells"]:
        before, drawn = held.get((cell["scenario"], cell["seed"])), _ids(cell)
        if None not in (before, drawn) and before != drawn:
            return True
    return False


def merged_by(path: Path) -> List[dict]:
    """The pull requests merged by the commits that the answer of logos-chat's compare API in
    `path` holds, each its number and title, in the order of their commits. A commit whose title
    does not name a pull request the way a squash merge's or a merge commit's does merged none.
    An answer that does not say the revision is ahead of the point it follows, or that point's
    commit, is refused."""
    answer = _json(path)
    commits = answer.get("commits") if isinstance(answer, dict) else None
    if not isinstance(commits, list) or not all(
        isinstance(one, dict)
        and isinstance(one.get("commit"), dict)
        and isinstance(one["commit"].get("message"), str)
        for one in commits
    ):
        raise Unreadable(f"{path}: not an answer of the compare API, each commit with its message")
    if answer.get("status") not in _FOLLOWS:
        raise Unreadable(
            f"{path}: the revision is {answer.get('status')!r} of the point it follows, neither "
            "ahead of it nor its commit"
        )
    found: Dict[int, dict] = {}
    for one in commits:
        lines = one["commit"]["message"].splitlines() or [""]
        squashed, merge = _SQUASHED.fullmatch(lines[0]), _MERGE.fullmatch(lines[0])
        if squashed is not None:
            number, title = int(squashed[2]), squashed[1]
        elif merge is not None:
            number, title = int(merge[1]), next((line for line in lines[1:] if line.strip()), "")
        else:
            continue
        found.setdefault(number, {"number": number, "title": title.strip()})
    return list(found.values())


def read_matrix(path: Path) -> dict:
    """A matrix of `tools/sim matrix`, refused unless it is one."""
    matrix = _json(path)
    try:
        _shaped(matrix, _MATRIX)
        if matrix["base"] is not None:
            _shaped(matrix["base"], _SIDE, "base")
        if matrix["verdict"] not in _VERDICTS:
            raise Unreadable(f"verdict {matrix['verdict']!r} is none of a matrix")
        if (matrix["summary"] is None) != (matrix["base"] is not None):
            raise Unreadable("summary is there without a base, and only without one")
        for number, cell in enumerate(matrix["cells"]):
            _cell(cell, f"cells[{number}]", alone=matrix["base"] is None)
        for number, one in enumerate(matrix["summary"] or ()):
            _summary(one, f"summary[{number}]")
    except Unreadable as refused:
        raise Unreadable(f"{path}: {refused}") from None
    return matrix


def read_point(path: Path) -> dict:
    """A point.json, refused unless it is one `write_point` writes, in the directory of its
    logos-chat commit and its measurement."""
    point = _json(path)
    try:
        _shaped(point, _POINT)
        _commits(point, "logos_chat", "measurement", "chat_sim")
        named = (path.parent.parent.name, path.parent.name)
        if (point["logos_chat"], point["measurement"]) != named:
            raise Unreadable("logos_chat and measurement do not name its directory")
        _ran(point)
        for number, one in enumerate(point["merged"]):
            _shaped(one, _MERGED, f"merged[{number}]")
            if one["number"] < 1:
                raise Unreadable(f"merged[{number}].number is not that of a pull request")
        if point["previous"] is not None:
            _shaped(point["previous"], _PREVIOUS, "previous")
            _commits(point["previous"], "logos_chat", "measurement", where="previous.")
        for number, cell in enumerate(point["cells"]):
            _cell(cell, f"cells[{number}]", alone=True)
        for number, one in enumerate(point["summary"]):
            _summary(one, f"summary[{number}]")
    except Unreadable as refused:
        raise Unreadable(f"{path}: {refused}") from None
    return point


def read_pr_meta(path: Path) -> dict:
    """The meta.json of a logos-chat pull request's run, refused unless it is one `write_pr`
    writes, in the directory of its pull request."""
    meta = _json(path)
    try:
        _shaped(meta, _PR_META)
        if path.parent.name != f"pr-{meta['pr']}" or meta["pr"] < 1:
            raise Unreadable(f"pr {meta['pr']} does not name its directory")
        _commits(meta, "revision", "chat_sim", "measurement")
        if meta["base"] is not None:
            _commits(meta, "base")
        _ran(meta)
    except Unreadable as refused:
        raise Unreadable(f"{path}: {refused}") from None
    return meta


def _report(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    key = _KEY.fullmatch(args.key)
    if key is None:
        parser.error(f"--key: {args.key!r} is neither master nor pr-N")
    if key[1] is None and args.title:
        parser.error("--title: master has no title")
    if _SHA.fullmatch(args.sha) is None:
        parser.error(f"--sha: {args.sha!r} is not a commit")
    for flag, url in (("--commit-url", args.commit_url), ("--run-url", args.run_url)):
        if _URL.fullmatch(url) is None:
            parser.error(f"{flag}: {url!r} is not an http(s) address")
    try:
        datetime.strptime(args.time, _TIME)
    except ValueError:
        parser.error(f"--time: {args.time!r} is not of the form 2026-10-22T07:30:00Z")
    if not args.expected.is_dir():
        parser.error(f"--expected: no such directory: {args.expected}")
    try:
        there = read_meta(args.site / "reports" / args.key / "meta.json")["run_number"]
    except Unreadable:
        there = None
    if there is not None and there > args.run_number:
        print(
            f"Kept reports/{args.key}/: it holds the report of run {there}, newer than run "
            f"{args.run_number}."
        )
        return 0
    number = None if key[1] is None else int(key[1])
    write_report(
        args.artifacts,
        args.site,
        sorted(path.stem for path in args.expected.glob("*.json")),
        {
            "key": args.key,
            "name": "master" if number is None else f"#{number}",
            "title": args.title,
            "number": number,
            "sha": args.sha,
            "commit_url": args.commit_url,
            "run_url": args.run_url,
            "run_number": args.run_number,
            "time": args.time,
        },
    )
    return 0


def _index(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    write_index(args.site)
    return 0


def _point(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    matrix = _check_ran(parser, args)
    if matrix["base"] is not None:
        parser.error("MATRIX: a point of main is of a matrix without a base")
    # What a run job leaves when it fails before its build, as at a revision logos-chat does not
    # have: kept, it would stand as the revision's point, and the point a later one follows.
    if matrix["head"] == {"runs": 0, "unbuilt": 0}:
        parser.error("MATRIX: no run at the revision was read, and none could not be built")
    found = points(args.site)
    out = _point_at(args.site, args.revision, args.measurement)
    there = next((point for directory, point in found if directory == out), None)
    if there is not None and there["run_number"] > args.run_number:
        print(
            f"Kept {out.relative_to(args.site)}/: it holds the point of run "
            f"{there['run_number']}, newer than run {args.run_number}."
        )
        return 0
    before = previous(found, args.revision, args.measurement, args.run_number)
    if (before is None) != (args.commits is None):
        parser.error("--commits: given when the site holds a point before this one, and only then")
    try:
        prs = [] if args.commits is None else merged_by(args.commits)
        point = write_point(
            matrix,
            args.runs,
            args.site,
            {
                "logos_chat": args.revision,
                "measurement": args.measurement,
                "chat_sim": args.chat_sim,
                "time": args.time,
                "run_url": args.run_url,
                "run_number": args.run_number,
            },
            prs,
            before,
        )
    except Unreadable as refused:
        parser.error(str(refused))
    for directory in prune([(out, point), *((at, one) for at, one in found if at != out)]):
        print(
            f"Removed the runs' text of {directory.relative_to(args.site)}/: past the newest "
            f"{WINDOW} points."
        )
    return 0


def _previous(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    for flag, sha in (("--revision", args.revision), ("--measurement", args.measurement)):
        if _SHA.fullmatch(sha) is None:
            parser.error(f"{flag}: {sha!r} is not a commit's 40 hex digits")
    before = previous(points(args.site), args.revision, args.measurement, args.run_number)
    print(f"previous={'' if before is None else before['logos_chat']}")
    return 0


def _pr(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if args.pr < 1:
        parser.error(f"--pr: {args.pr} is not the number of a pull request")
    if args.base and _SHA.fullmatch(args.base) is None:
        parser.error(f"--base: {args.base!r} is not a commit's 40 hex digits")
    if (args.base_runs is None) != (not args.base):
        parser.error("--base-runs: given with --base, and only with it")
    if args.base_runs is not None and not args.base_runs.is_dir():
        parser.error(f"--base-runs: no such directory: {args.base_runs}")
    matrix = _check_ran(parser, args)
    if (matrix["base"] is None) != (not args.base):
        parser.error("MATRIX: a matrix with a base is given with --base, and only with it")
    out = args.site / LOGOS_CHAT / f"pr-{args.pr}"
    try:
        there = read_pr_meta(out / "meta.json")["run_number"]
    except Unreadable:
        there = None
    if there is not None and there > args.run_number:
        print(
            f"Kept {out.relative_to(args.site)}/: it holds run {there}, newer than run "
            f"{args.run_number}."
        )
        return 0
    try:
        write_pr(
            matrix,
            args.runs,
            args.base_runs,
            args.site,
            {
                "pr": args.pr,
                "revision": args.revision,
                "base": args.base or None,
                "chat_sim": args.chat_sim,
                "measurement": args.measurement,
                "run_url": args.run_url,
                "run_number": args.run_number,
                "time": args.time,
            },
        )
    except Unreadable as refused:
        parser.error(str(refused))
    return 0


def _add_ran(command: argparse.ArgumentParser, runs: str) -> None:
    """The arguments that say what a run of simulate.yml ran, and what it came to."""
    command.add_argument(
        "matrix", metavar="MATRIX", type=Path, help="the matrix tools/sim matrix wrote of the runs"
    )
    command.add_argument(
        "runs",
        metavar="RUNS",
        type=Path,
        help=f"what {runs}: each run in <scenario>/<run>/, at any depth",
    )
    command.add_argument("site", metavar="SITE", type=Path, help="the site, as gh-pages holds it")
    command.add_argument("--revision", required=True, help="the logos-chat commit that ran")
    command.add_argument(
        "--measurement", metavar="SHA", required=True, help="the chat-sim commit the runs measure"
    )
    command.add_argument("--chat-sim", metavar="SHA", required=True, help="chat-sim's commit")
    command.add_argument("--run-url", metavar="URL", required=True, help="the workflow run's page")
    command.add_argument(
        "--run-number",
        metavar="N",
        type=int,
        required=True,
        help="the workflow run's number, which a re-run keeps",
    )
    command.add_argument("--time", required=True, help="when it ran, as 2026-10-22T07:30:00Z")


def _check_ran(parser: argparse.ArgumentParser, args: argparse.Namespace) -> dict:
    """The matrix of a run, once what the command was handed of it is checked."""
    for flag, sha in (
        ("--revision", args.revision),
        ("--measurement", args.measurement),
        ("--chat-sim", args.chat_sim),
    ):
        if _SHA.fullmatch(sha) is None:
            parser.error(f"{flag}: {sha!r} is not a commit's 40 hex digits")
    if _URL.fullmatch(args.run_url) is None:
        parser.error(f"--run-url: {args.run_url!r} is not an http(s) address")
    try:
        datetime.strptime(args.time, _TIME)
    except ValueError:
        parser.error(f"--time: {args.time!r} is not of the form 2026-10-22T07:30:00Z")
    if not args.runs.is_dir():
        parser.error(f"RUNS: no such directory: {args.runs}")
    try:
        return read_matrix(args.matrix)
    except Unreadable as refused:
        parser.error(f"MATRIX: {refused}")


def _point_at(site: Path, logos_chat: str, measurement: str) -> Path:
    return site / LOGOS_CHAT / "main" / logos_chat / measurement


def _texts(runs: Path, out: Path) -> List[Tuple[Path, Path]]:
    """Each run's text under `runs`, at any depth, and where it goes under `out`:
    `<scenario>/<run>/`, its run by number."""
    copies: Dict[Path, Path] = {}
    for name in TEXTS:
        for source in sorted(path for path in runs.rglob(name) if path.is_file()):
            run, scenario = source.parent.name, source.parent.parent.name
            if _RUN.fullmatch(run) is None or _SCENARIO.fullmatch(scenario) is None:
                raise Unreadable(f"{source}: not in the directory of a run, <scenario>/<run>")
            copy = out / scenario / str(int(run)) / name
            if copy in copies:
                raise Unreadable(f"{source}: a second {name} of run {int(run)} of {scenario}")
            copies[copy] = source
    return [(source, copy) for copy, source in copies.items()]


def _replace(out: Path, copies: Sequence[Tuple[Path, Path]]) -> None:
    """Empties `out`, and copies each file of `copies` to where it goes."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for source, copy in copies:
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, copy)


def _ids(cell: dict) -> Optional[Tuple[str, str]]:
    """The ids the run of a cell drew at the head, the group's and the members' digest, or
    `None` where it drew none."""
    outcome = cell["head"]
    if outcome is None or None in (outcome["conversation"], outcome["members_digest"]):
        return None
    return outcome["conversation"], outcome["members_digest"]


def _newest(found: Tuple[Path, dict]) -> tuple:
    directory, point = found
    return point["run_number"], str(directory)


def _shaped(value: object, shape: dict, where: str = "") -> None:
    """Refuses `value` unless it is an object of the fields of `shape` alone, each of the kinds
    `shape` gives it, or of a shape of its own."""
    if not isinstance(value, dict):
        raise Unreadable(f"{where or 'it'} is not an object")
    unknown = sorted(value.keys() - shape.keys())
    if unknown:
        raise Unreadable(f"{_at(where, unknown[0])} is not a field of {where or 'it'}")
    for field, kind in shape.items():
        if field not in value:
            raise Unreadable(f"{_at(where, field)} is missing")
        if isinstance(kind, dict):
            _shaped(value[field], kind, _at(where, field))
        elif not isinstance(value[field], kind) or (
            isinstance(value[field], bool) and bool not in kind
        ):
            raise Unreadable(f"{_at(where, field)} is not of its kind")


def _at(where: str, field: str) -> str:
    return f"{where}.{field}" if where else field


def _cell(cell: object, where: str, alone: bool) -> None:
    """Refuses `cell` unless it is a cell of a matrix; with `alone`, of one without a base."""
    _shaped(cell, _CELL, where)
    if _SCENARIO.fullmatch(cell["scenario"]) is None:
        raise Unreadable(f"{where}.scenario {cell['scenario']!r} is not the name of a scenario")
    if cell["run"] < 1:
        raise Unreadable(f"{where}.run {cell['run']} is not that of a run")
    for side in ("head", "base"):
        if cell[side] is not None:
            _outcome(cell[side], f"{where}.{side}")
        if cell["ended"][side] not in (None, *_ENDINGS):
            raise Unreadable(f"{where}.ended.{side} is not how a run ended")
    if cell["change"] not in (None, *_CHANGES):
        raise Unreadable(f"{where}.change {cell['change']!r} is none of compare's")
    if cell["moved"] is not None:
        _shaped(cell["moved"], _MOVED, f"{where}.moved")
        if cell["moved"]["part"] not in _STANDING:
            raise Unreadable(f"{where}.moved.part is no part of where a run stands")
    based = (cell["base"], cell["epochs"]["base"], cell["ended"]["base"], cell["change"])
    if alone and (based != (None,) * 4 or cell["unbuilt"]["base"] or cell["moved"] is not None):
        raise Unreadable(f"{where} has a base")


def _outcome(outcome: object, where: str) -> None:
    _shaped(outcome, _OUTCOME, where)
    for field in ("conversation", "members_digest"):
        if outcome[field] is not None and _HEX.fullmatch(outcome[field]) is None:
            raise Unreadable(f"{where}.{field} is not an id")
    if outcome["ended"] not in (None, *_ENDINGS):
        raise Unreadable(f"{where}.ended is not how a run ended")


def _summary(one: object, where: str) -> None:
    _shaped(one, _SUMMARY, where)
    if _SCENARIO.fullmatch(one["scenario"]) is None:
        raise Unreadable(f"{where}.scenario {one['scenario']!r} is not the name of a scenario")


def _commits(held: dict, *fields: str, where: str = "") -> None:
    for field in fields:
        if _SHA.fullmatch(held[field]) is None:
            raise Unreadable(f"{where}{field} is not a commit")


def _ran(held: dict) -> None:
    """Refuses a point or a pull request's meta.json unless the address of its run and its time
    are of their form."""
    if _URL.fullmatch(held["run_url"]) is None:
        raise Unreadable("run_url is not an http(s) address")
    try:
        datetime.strptime(held["time"], _TIME)
    except ValueError:
        raise Unreadable("time is not of the form 2026-10-22T07:30:00Z") from None


def _json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise Unreadable(f"{path}: {error}") from None


def _sweep(sweep: Path, name: str, said: str, show: Callable[..., str]) -> str:
    """The section of one sweep: compare's output, sweep.txt, and each run's two files."""
    head = f'<h3 id="{_e(name)}">{_e(name)} <span class="{_CLASS[said]}">{said}</span></h3>\n'
    if not sweep.is_dir():
        return head + "<p>The job uploaded nothing.</p>\n"
    runs = sorted(
        (path.name for path in sweep.iterdir() if path.is_dir() and _RUN.fullmatch(path.name)),
        key=int,
    )
    return (
        head
        + show(name, "compare.txt")
        + show(name, "sweep.txt")
        + "".join(
            f"<details>\n<summary>Run {run}</summary>\n"
            + show(name, run, "run.txt")
            + show(name, run, "report.txt")
            + "</details>\n"
            for run in runs
        )
    )


def _fifty(run: Path, name: str, show: Callable[..., str]) -> str:
    """The section of one run of fifty epochs: its run.txt and report.txt."""
    head = f'<h3 id="{_e(name)}">{_e(name)}</h3>\n'
    if not run.is_dir():
        return head + "<p>Nothing of this run was uploaded: it did not run, or wrote nothing.</p>\n"
    return head + show(name, "run.txt") + show(name, "report.txt")


def _heading(meta: dict) -> str:
    return " ".join(part for part in (meta["name"], meta["title"]) if part)


def _facts(meta: dict) -> str:
    return (
        f"Commit {_link(meta['commit_url'], meta['sha'][:7], code=True)} · "
        f"{_link(meta['run_url'], 'CI run')} · {_when(meta)} UTC"
    )


def _tally(meta: dict) -> str:
    return f"{meta['as_expected']} of {meta['sweeps']} as expected"


def _when(meta: dict) -> str:
    return datetime.strptime(meta["time"], _TIME).strftime("%Y-%m-%d %H:%M")


def _link(href: str, text: str, code: bool = False) -> str:
    """A link to `href`: an address as it is, a path of the site with each part quoted."""
    if _URL.fullmatch(href) is None:
        href = quote(href, safe="/#")
    shown = f"<code>{_e(text)}</code>" if code else _e(text)
    return f'<a href="{_e(href)}">{shown}</a>'


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>{_e(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body}"
        "</body>\n"
        "</html>\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
