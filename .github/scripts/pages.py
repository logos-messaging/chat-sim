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
and its `meta.json`. `trend` draws the graph over main, `logos-chat/index.html`, from the
`point.json` of every point; one it cannot read is left out, with a warning.

The pages run no script, and every text that comes from a run or from a pull request's title
is escaped.
"""

from __future__ import annotations

import argparse
import html
import json
import math
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
# How many rates the graph over main tells apart, a hue of `_STYLE`'s ramp each.
HUES = 4
# The repositories whose commits and pull requests a point names.
LOGOS_CHAT_URL = "https://github.com/logos-messaging/logos-chat"
CHAT_SIM_URL = "https://github.com/logos-messaging/chat-sim"

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
# The epoch the goal's runs grow toward, the top of the scale of epochs without a fork, and the
# bins of a cell's epochs: ten epochs each, and the top alone.
_TOP = 50
_BINS = ("0 to 9", "10 to 19", "20 to 29", "30 to 39", "40 to 49", "50")
_NONE_MERGED = '<span class="muted">none</span>'
# The width of a chart of the graph over main, the room a label of its axis of time takes, and
# about the width of a character of its text.
_WIDTH = 820
_ROOM = 44
_CHAR = 6.2

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
    "base_point": (dict, _NONE),
    "cells": (list,),
    "counts": {
        field: (int,) for field in ("cells", "compared", *_CHANGES, "below", "no-outcome")
    },
    "verdict": (str,),
    "summary": (list, _NONE),
    "spread": (dict, _NONE),
}
# The point of main a matrix names: the one that stood in for the base's runs, and its spread.
_NAMED = {"logos_chat": (str,), "measurement": (str,), "run_url": (str,)}
_SPREAD = {**_NAMED, "summary": (list,), "seeds": (list,)}
_SEED = {"scenario": (str,), "seed": (int,), "epochs": (int, _NONE), "ended": (str, _NONE)}
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
    "below": (bool, _NONE),
}
# A point keeps a cell of main's matrix but whether it is below a spread, which only a pull
# request's matrix has.
_POINT_CELL = {field: kind for field, kind in _CELL.items() if field != "below"}
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

# The rates are one blue ramp, the heavier the loss the darker, and so are the six bins of a
# cell's epochs without a fork; each bin's ink is the light or the dark one.
_STYLE = """
:root{color-scheme:light dark;--fg:#1f2328;--muted:#59636e;--bg:#fff;--box:#f6f8fa;
--line:#d1d9e0;--link:#0969da;--good:#1a7f37;--bad:#cf222e;--none:#9a6700;--grid:#e6e9ed;
--band:rgba(57,135,229,.18);--rate-1:#86b6ef;--rate-2:#3987e5;--rate-3:#1c5cab;
--rate-4:#0d366b;--w0:#cde2fb;--w1:#9ec5f4;--w2:#6da7ec;--w3:#3987e5;--w4:#1c5cab;
--w5:#0d366b;--w-ink-light:#1f2328;--w-ink-dark:#fff}
@media (prefers-color-scheme:dark){:root{--fg:#f0f6fc;--muted:#9198a1;--bg:#0d1117;
--box:#151b23;--line:#3d444d;--link:#4493f8;--good:#3fb950;--bad:#f85149;--none:#d29922;
--grid:#21262d;--band:rgba(109,167,236,.22);--rate-1:#b7d3f6;--rate-2:#6da7ec;
--rate-3:#2a78d6;--rate-4:#184f95;--w0:#184f95;--w1:#256abf;--w2:#3987e5;--w3:#6da7ec;
--w4:#9ec5f4;--w5:#cde2fb;--w-ink-light:#fff;--w-ink-dark:#0d1117}}
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
# The graph over main, in the colours of `_STYLE`.
_CHARTS = """
.lead{font-size:16px}
.chart svg{display:block;max-width:100%;height:auto}
.chart text{font-size:11px;font-variant-numeric:tabular-nums}
.caption{color:var(--muted);font-size:13px;margin:.4rem 0 0;max-width:52rem}
.legend{display:flex;flex-wrap:wrap;align-items:center;gap:.3rem 1.1rem;margin:0 0 .5rem;
font-size:12px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:.4rem}
.legend svg{display:inline-block}
.panels{display:flex;flex-wrap:wrap;gap:12px}
.panel p{display:flex;align-items:center;gap:.4rem;margin:0 0 2px 28px;font-size:13px}
.grid{stroke:var(--grid)}
.axis{fill:var(--muted)}
.chart .small{font-size:10px}
.label{fill:var(--fg)}
.note{fill:var(--muted)}
.band{fill:var(--band)}
.measure{stroke:var(--muted);stroke-dasharray:3 3}
.leader{stroke:var(--line)}
.hit{fill:none;pointer-events:all}
.s1{--hue:var(--rate-1)}
.s2{--hue:var(--rate-2)}
.s3{--hue:var(--rate-3)}
.s4{--hue:var(--rate-4)}
.series{fill:none;stroke:var(--hue);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.marker,.key{fill:var(--hue)}
.marker{stroke:var(--bg);stroke-width:2}
.marker.open{fill:var(--bg);stroke:var(--hue)}
.cell rect{fill:var(--cell)}
.chart .cell text{fill:currentColor;font-size:12px}
.cell .whole{fill:currentColor}
.cell .behind{fill:none;stroke:currentColor;stroke-width:1.5}
.bin0{--cell:var(--w0);color:var(--w-ink-light)}
.bin1{--cell:var(--w1);color:var(--w-ink-light)}
.bin2{--cell:var(--w2);color:var(--w-ink-light)}
.bin3{--cell:var(--w3);color:var(--w-ink-dark)}
.bin4{--cell:var(--w4);color:var(--w-ink-dark)}
.bin5{--cell:var(--w5);color:var(--w-ink-dark)}
.empty{--cell:var(--box);color:var(--muted)}
.empty rect{stroke:var(--line);stroke-dasharray:3 2}
.chart svg+.legend{margin-top:.5rem}
.points td{white-space:nowrap;font-variant-numeric:tabular-nums}
.points th[colspan]{text-align:center}
.points .merged{white-space:normal;min-width:14rem}
.points .moved{white-space:normal;font-size:13px;color:var(--muted);min-width:12rem}
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
    trend = commands.add_parser(
        "trend",
        help="draw the graph over logos-chat's main",
        description="Writes logos-chat/index.html of SITE, the graph over logos-chat's main, "
        "from the point.json of each point, in the order of their runs' numbers. A point.json it "
        f"cannot read is left out, with a warning; points of more than {HUES} rates are refused.",
    )
    trend.add_argument("site", metavar="SITE", type=Path, help="the site, as gh-pages holds it")
    trend.set_defaults(command=_trend)
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
        "cells": [{field: cell[field] for field in _POINT_CELL} for cell in matrix["cells"]],
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


def write_trend(site: Path) -> None:
    """Writes `logos-chat/index.html` of `site`, the graph over logos-chat's main, from each
    point `points` reads, the oldest at the left: of each rate the epochs without a fork and the
    runs whole, the newest point's cells, the members off the trunk once a rate has had every run
    whole, and the list of the points, the newest first.

    Every number it draws is one a point.json holds. A point that measures another chat-sim
    commit than the point before it has a dashed line, and one re-rolled against the point it
    follows an open marker. Points of more than `HUES` rates are refused.
    """
    found = [point for _, point in reversed(points(site))]
    rates = _rates(found)
    body = (
        f'<p class="muted">{_link("../", "chat-sim")} / {_link("../guide/", "Guide")}</p>\n'
        "<h1>Over main</h1>\n"
        '<p class="lead">How many epochs a group of logos-chat clients lasts as one group while '
        f"it grows toward epoch {_TOP}, at each rate of loss, over logos-chat's main: a point for "
        "each of its commits that was measured, the oldest at the left.</p>\n"
    )
    if found:
        body += (
            _epochs_chart(found, rates)
            + _whole_chart(found, rates)
            + _newest_chart(found[-1], rates)
            + (_off_chart(found, rates) if _reached(found) else "")
            + _points_list(found, rates)
        )
    else:
        body += "<p>No point of main has been kept yet.</p>\n"
    body += (
        f'<p class="muted">What a point holds: the guide\'s '
        f'{_link("../guide/reports/#logos-chats-runs", "Reports")}.</p>\n'
    )
    out = site / LOGOS_CHAT / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page("chat-sim: over main", body, _CHARTS), encoding="utf-8")


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
        if matrix["base_point"] is not None:
            if matrix["base"] is None:
                raise Unreadable("base_point is there without a base")
            _shaped(matrix["base_point"], _NAMED, "base_point")
            _named(matrix["base_point"], "base_point")
        for number, cell in enumerate(matrix["cells"]):
            _cell(cell, f"cells[{number}]", alone=matrix["base"] is None)
        for number, one in enumerate(matrix["summary"] or ()):
            _summary(one, f"summary[{number}]")
        if matrix["spread"] is not None:
            spread = matrix["spread"]
            _shaped(spread, _SPREAD, "spread")
            _named(spread, "spread")
            for number, one in enumerate(spread["summary"]):
                _summary(one, f"spread.summary[{number}]")
            for number, one in enumerate(spread["seeds"]):
                _seed(one, f"spread.seeds[{number}]")
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
            _cell(cell, f"cells[{number}]", alone=True, shape=_POINT_CELL)
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


def _trend(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        write_trend(args.site)
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


def _cell(cell: object, where: str, alone: bool, shape: dict = _CELL) -> None:
    """Refuses `cell` unless it is a cell of a matrix, of the fields of `shape`; with `alone`, of
    one without a base."""
    _shaped(cell, shape, where)
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


def _named(point: dict, where: str) -> None:
    """Refuses a point a matrix names unless its commits and the address of its run are of their
    form."""
    _commits(point, "logos_chat", "measurement", where=f"{where}.")
    if _URL.fullmatch(point["run_url"]) is None:
        raise Unreadable(f"{where}.run_url is not an http(s) address")


def _seed(one: object, where: str) -> None:
    _shaped(one, _SEED, where)
    if _SCENARIO.fullmatch(one["scenario"]) is None:
        raise Unreadable(f"{where}.scenario {one['scenario']!r} is not the name of a scenario")
    if one["ended"] not in (None, *_ENDINGS):
        raise Unreadable(f"{where}.ended is not how a run ended")


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


def _rates(found: Sequence[dict]) -> List[Tuple[object, str]]:
    """Each rate the points summarise, as `_rate` keys it, in the order of its loss, and the
    name the page gives it; refused when there are more than `HUES`."""
    seen: Dict[object, dict] = {}
    for point in found:
        for one in point["summary"]:
            seen.setdefault(_rate(one), one)
    ordered = sorted(
        seen.values(), key=lambda one: (one["loss"] is None, one["loss"] or 0, one["scenario"])
    )
    if len(ordered) > HUES:
        raise Unreadable(f"the points hold {len(ordered)} rates, and the page tells {HUES} apart")
    return [(_rate(one), one["rate"] or one["scenario"]) for one in ordered]


def _rate(held: dict) -> object:
    """The rate a summary or a cell is of: its loss, so that a scenario renamed at the same loss
    goes on as the same line, or its scenario where it has none."""
    return held["scenario"] if held["loss"] is None else held["loss"]


def _epochs_chart(found: Sequence[dict], rates: Sequence[Tuple[object, str]]) -> str:
    """The first chart: a panel a rate, the median of the epochs without a fork as a line and
    the band from the lowest to the highest."""
    return (
        '<section class="chart" id="epochs">\n<h2>Epochs without a fork</h2>\n'
        + _legend(
            (_line_key(2), "the median of the runs"),
            (_band_key(), "the lowest run to the highest"),
            *_marks(found),
        )
        + '<div class="panels">\n'
        + "".join(_panel(found, hue, *rate) for hue, rate in enumerate(rates, 1))
        + "</div>\n"
        '<p class="caption">How many epochs each run went without a fork: the first split\'s, '
        "else the trunk's. It moves with every change for the better, where the runs whole below "
        "can show none yet. A dashed line marks a point that measures another chat-sim commit "
        "than the point before it, so that the step is chat-sim's, not logos-chat's; an open "
        "marker a point re-rolled, whose runs drew other ids than those of the point it follows, "
        "so that every seed's run is another run, and moved whether or not logos-chat did.</p>\n"
        "</section>\n"
    )


def _whole_chart(found: Sequence[dict], rates: Sequence[Tuple[object, str]]) -> str:
    """The second chart: a line a rate of how many runs ended whole."""
    runs = max((one["runs"] for point in found for one in point["summary"]), default=0)
    return (
        '<section class="chart" id="whole">\n'
        f"<h2>Runs whole at {_TOP} epochs{_of_runs(found)}</h2>\n"
        + _legend(
            *((_line_key(hue), name) for hue, (_, name) in enumerate(rates, 1)),
            (_goal_key(), "the goal"),
            *_marks(found),
        )
        + _over(
            found,
            rates,
            _whole,
            _whole_said,
            top=runs,
            plot=240,
            label=f"Runs whole at {_TOP} epochs",
            goal=runs,
            merged=True,
            noted=True,
        )
        + '<p class="caption">How many runs of each rate ended whole: one group, every member in '
        f"it, at epoch {_TOP}. The goal is every line at the top. A line breaks at a point where "
        "none of the rate's runs had an outcome, as when chat-sim did not build. Under the dates "
        "the axis shows, the pull requests of logos-chat the point merged, which a marker's hover "
        "text names too.</p>\n"
        "</section>\n"
    )


def _newest_chart(point: dict, rates: Sequence[Tuple[object, str]]) -> str:
    """The third chart: the cells of the newest point, a row a rate and a column a seed."""
    rows = []
    for rate, name in rates:
        cells = {cell["seed"]: cell for cell in point["cells"] if _rate(cell) == rate}
        if cells:
            rows.append((name, cells))
    seeds = sorted({seed for _, cells in rows for seed in cells})
    side, size = 56, 38
    width, height = side + size * len(seeds), 18 + 30 * len(rows)
    drawn = [_svg(width, height, "The newest point's runs")]
    drawn.append('<text class="axis small" x="0" y="11">seed</text>')
    for column, seed in enumerate(seeds):
        x = side + size * column + size / 2
        drawn.append(
            f'<text class="axis small" x="{x:.1f}" y="11" text-anchor="middle">{seed}</text>'
        )
    for row, (name, cells) in enumerate(rows):
        y = 18 + 30 * row
        drawn.append(f'<text class="label" x="0" y="{y + 18}">{_e(name)}</text>')
        for column, seed in enumerate(seeds):
            if seed in cells:
                drawn.append(_cell_drawn(cells[seed], name, side + size * column, y))
    drawn.append("</svg>")
    when = _when(point)
    return (
        '<section class="chart" id="newest">\n<h2>The newest point</h2>\n'
        f'<p class="caption">{when} UTC, main {_e(point["logos_chat"][:7])}: a row a rate and a '
        "column a seed.</p>\n"
        + "\n".join(drawn)
        + "\n"
        + _legend(
            *((_cell_key(f"bin{number}"), name) for number, name in enumerate(_BINS)),
            (_cell_key("bin5", "whole"), "whole"),
            (_cell_key("bin5", "behind"), "one group, members behind"),
            (_cell_key("empty"), "no outcome"),
        )
        + '<p class="caption">A cell\'s colour is the epochs its run went without a fork; a dot '
        "marks a run that ended whole, a ring one that ended one group with members behind, and "
        "no mark one that split or stalled. Its hover text is how the run came out.</p>\n"
        "</section>\n"
    )


def _off_chart(found: Sequence[dict], rates: Sequence[Tuple[object, str]]) -> str:
    """The fourth chart: a line a rate of the median of the members off the trunk at the end."""
    medians = [
        one["members_off_trunk"]["median"]
        for point in found
        for one in point["summary"]
        if one["members_off_trunk"]["median"] is not None
    ]
    return (
        '<section class="chart" id="off">\n<h2>Members off the trunk at the end</h2>\n'
        + _legend(
            *((_line_key(hue), name) for hue, (_, name) in enumerate(rates, 1)), *_marks(found)
        )
        + _over(
            found,
            rates,
            lambda one: one["members_off_trunk"]["median"],
            lambda one: f"a median of {_number(one['members_off_trunk']['median'])} members off "
            "the trunk at the end",
            top=max(medians, default=0),
            plot=150,
            label="Members off the trunk at the end",
        )
        + '<p class="caption">Behind at the end, on a line not the trunk, or never joined: the '
        "median of each rate's runs. The other half of the goal, once a rate has every run "
        "whole.</p>\n"
        "</section>\n"
    )


def _points_list(found: Sequence[dict], rates: Sequence[Tuple[object, str]]) -> str:
    """The list of the points, the newest first."""
    rows = []
    for number in reversed(range(len(found))):
        point = found[number]
        logos_chat, measured = point["logos_chat"], point["measurement"]
        merged = "<br>".join(
            _link(f"{LOGOS_CHAT_URL}/pull/{one['number']}", f"#{one['number']}")
            + f" {_e(one['title'])}"
            for one in point["merged"]
        )
        whole = "".join(
            f"<td>{_number(None if one is None else _whole(one))}</td>"
            for one in (_rate_at(point, rate) for rate, _ in rates)
        )
        tag = ' <span class="muted">moved</span>' if _moved(found, number) else ""
        rows.append(
            "<tr>"
            f"<td>{_when(point)}</td>"
            f"<td>{_link(f'{LOGOS_CHAT_URL}/commit/{logos_chat}', logos_chat[:7], code=True)}</td>"
            f'<td class="merged">{merged or _NONE_MERGED}</td>'
            f"{whole}"
            f'<td class="moved">{_e(_what_moved(found, number, rates))}</td>'
            f"<td>{_link(f'{CHAT_SIM_URL}/commit/{measured}', measured[:7], code=True)}{tag}</td>"
            f"<td>{_link(f'main/{logos_chat}/{measured}/point.json', 'point')} · "
            f"{_link(point['run_url'], 'the run')}</td>"
            "</tr>\n"
        )
    heads = "".join(f"<th>{_e(name)}</th>" for _, name in rates)
    return (
        '<section id="points">\n<h2>The points</h2>\n'
        '<div class="wide"><table class="points">\n'
        "<thead><tr><th>When (UTC)</th><th>main</th><th>Pull requests merged</th>"
        f'<th colspan="{len(rates)}">Runs whole{_of_runs(found)}</th><th>What moved</th>'
        "<th>chat-sim</th><th></th></tr>\n"
        f"<tr><th></th><th></th><th></th>{heads}<th></th><th></th><th></th></tr></thead>\n"
        f"<tbody>\n{''.join(rows)}</tbody>\n"
        "</table></div>\n"
        '<p class="caption">What moved is against the point before it: each rate\'s runs whole '
        "and median of epochs without a fork where they changed, or that none of its runs had an "
        "outcome. chat-sim is the commit the point measures.</p>\n"
        "</section>\n"
    )


def _over(
    found: Sequence[dict],
    rates: Sequence[Tuple[object, str]],
    value: Callable[[dict], Optional[float]],
    hover: Callable[[dict], str],
    top: float,
    plot: int,
    label: str,
    goal: Optional[int] = None,
    merged: bool = False,
    noted: bool = False,
) -> str:
    """A chart over the points: a line a rate of what `value` reads of the rate's summary at each
    point, a marker on each with `hover` in its title, and the rate and its newest value at the
    end of its line, on a scale from 0 to `top`. With `goal`, a dashed line at it; with `merged`,
    the pull requests each point merged under its date; with `noted`, the commit chat-sim moved
    to over each dashed line of a point at another measurement."""
    left, right, above, below = 36, 96, 28 if noted else 14, 44 if merged else 28
    xs = _xs(len(found), left, _WIDTH - left - right)
    reach = _reach(xs)
    ticks = _ticks(top)
    bottom = above + plot
    height = bottom + below

    def y(held: float) -> float:
        return bottom - held / ticks[-1] * plot

    drawn = [_svg(_WIDTH, height, label)]
    for tick in ticks:
        drawn.append(
            f'<line class="grid" x1="{left}" x2="{_WIDTH - right}" y1="{y(tick):.1f}" '
            f'y2="{y(tick):.1f}"/>'
        )
        drawn.append(
            f'<text class="axis" x="{left - 8}" y="{y(tick) + 4:.1f}" text-anchor="end">'
            f"{tick}</text>"
        )
    every = _every(xs)
    for number in range(len(found))[::-1][::every]:
        drawn.append(_date(found[number], xs[number], bottom + 16, "middle"))
        if merged:
            room = every * (xs[1] - xs[0]) if len(xs) > 1 else _WIDTH
            drawn.append(
                f'<text class="axis small" x="{xs[number]:.1f}" y="{bottom + 30}" '
                f'text-anchor="middle">{_e(_merged(found[number], room))}</text>'
            )
    for number in range(len(found)):
        if _moved(found, number):
            drawn.append(_measure(xs[number], above, bottom))
    if goal is not None:
        drawn.append(
            f'<line class="measure" x1="{left}" x2="{_WIDTH - right + 10}" y1="{y(goal):.1f}" '
            f'y2="{y(goal):.1f}"/>'
        )
        drawn.append(
            f'<text class="note" x="{left + 14}" y="{y(goal) - 8:.1f}">the goal</text>'
        )
    if noted:
        # The goal is the top of the scale, so its label is in the notes' row.
        taken = 0 if goal is None else left + 14 + len("the goal") * _CHAR + 8
        drawn += _notes(found, xs, above - 10, taken)
    ends = []
    for hue, (rate, name) in enumerate(rates, 1):
        held = [_rate_at(point, rate) for point in found]
        values = [None if one is None else value(one) for one in held]
        drawn.append(f'<g class="s{hue}">')
        line = _path(xs, values, y)
        if line:
            drawn.append(f'<path class="series" d="{line}"/>')
        for number, one in enumerate(held):
            if values[number] is not None:
                said = f"{_said(found, number)} · {name}: {hover(one)}"
                opened = _rerolled(found[number])
                drawn.append(_marker(xs[number], y(values[number]), said, opened, reach))
        drawn.append("</g>")
        last = next((v for v in reversed(values) if v is not None), None)
        if last is not None:
            ends.append({"hue": hue, "name": name, "value": last, "y": y(last), "at": y(last)})
    drawn += _ends(ends, xs[-1], bottom + 4)
    drawn.append("</svg>")
    return "\n".join(drawn) + "\n"


def _panel(found: Sequence[dict], hue: int, rate: object, name: str) -> str:
    """A panel of the first chart: of one rate, the median of the epochs without a fork as a
    line, and the band from the lowest to the highest."""
    width, plot, left, right, above, below = 196, 120, 28, 12, 10, 22
    xs = _xs(len(found), left, width - left - right)
    reach = _reach(xs)
    held = [_rate_at(point, rate) for point in found]
    epochs = [None if one is None else one["epochs"] for one in held]
    top = max([_TOP] + [one["highest"] for one in epochs if one and one["highest"] is not None])
    bottom = above + plot

    def y(epoch: float) -> float:
        return bottom - epoch / top * plot

    drawn = [_svg(width, bottom + below, f"Epochs without a fork at {name}")]
    for tick in (0, _TOP // 2, _TOP):
        drawn.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y(tick):.1f}" '
            f'y2="{y(tick):.1f}"/>'
        )
        drawn.append(
            f'<text class="axis small" x="{left - 6}" y="{y(tick) + 4:.1f}" '
            f'text-anchor="end">{tick}</text>'
        )
    if len(found) > 1:
        drawn.append(_date(found[0], xs[0], bottom + 15, "start", "axis small"))
    newest = "end" if len(found) > 1 else "middle"
    drawn.append(_date(found[-1], xs[-1], bottom + 15, newest, "axis small"))
    for number in range(len(found)):
        if _moved(found, number):
            drawn.append(_measure(xs[number], above, bottom))
    spans = [
        None if one is None or None in (one["lowest"], one["highest"]) else one for one in epochs
    ]
    for run in _runs(spans):
        highs = [(xs[number], y(spans[number]["highest"])) for number in run]
        lows = [(xs[number], y(spans[number]["lowest"])) for number in reversed(run)]
        if len(run) == 1:
            (x, high), (_, low) = highs[0], lows[0]
            highs, lows = [(x - 4, high), (x + 4, high)], [(x + 4, low), (x - 4, low)]
        corners = " ".join(f"{x:.1f},{at:.1f}" for x, at in highs + lows)
        drawn.append(f'<polygon class="band" points="{corners}"/>')
    medians = [None if one is None else one["median"] for one in epochs]
    drawn.append(f'<g class="s{hue}">')
    line = _path(xs, medians, y)
    if line:
        drawn.append(f'<path class="series" d="{line}"/>')
    for number, one in enumerate(epochs):
        if medians[number] is not None:
            said = (
                f"{_said(found, number)} · {name}: median {_number(one['median'])}, lowest "
                f"{_number(one['lowest'])}, highest {_number(one['highest'])}"
            )
            opened = _rerolled(found[number])
            drawn.append(_marker(xs[number], y(medians[number]), said, opened, reach))
    drawn.append("</g>\n</svg>")
    titled = f"<p>{_rate_key(hue)}{_e(name)}</p>"
    return f'<div class="panel">\n{titled}\n' + "\n".join(drawn) + "\n</div>\n"


def _cell_drawn(cell: dict, name: str, x: float, y: float) -> str:
    """A cell of the newest point: its epochs without a fork in a bin's colour, a dot for a run
    that ended whole, a ring for one that ended behind, and how it came out as its title."""
    outcome, epochs, ended = cell["head"], cell["epochs"]["head"], cell["ended"]["head"]
    said = f"{name}, seed {cell['seed']}"
    if outcome is None:
        why = "chat-sim did not build" if cell["unbuilt"]["head"] else "no outcome"
        kind, shown, said = "empty", "-", f"{said} · {why}"
    else:
        kind = "empty" if epochs is None else f"bin{min(epochs // 10, len(_BINS) - 1)}"
        shown = "-" if epochs is None else str(epochs)
        if epochs is not None:
            said += f" · {epochs} epochs without a fork"
        said += f" · {outcome['came_to']}"
    mark = {"whole": "whole", "behind": "behind"}.get(ended or "")
    centre = x + (15 if mark else 18)
    drawn = (
        f'<g class="cell {kind}"><title>{_e(said)}</title>'
        f'<rect x="{x + 1:.1f}" y="{y + 1}" width="36" height="26" rx="4"/>'
        f'<text x="{centre:.1f}" y="{y + 18}" text-anchor="middle">{shown}</text>'
    )
    if mark == "whole":
        drawn += f'<circle class="whole" cx="{x + 29:.1f}" cy="{y + 14}" r="3"/>'
    if mark == "behind":
        drawn += f'<circle class="behind" cx="{x + 29:.1f}" cy="{y + 14}" r="2.5"/>'
    return drawn + "</g>"


def _ends(ends: List[dict], x: float, lowest: float) -> List[str]:
    """The rate and its newest value at the end of each line, pushed apart where two meet."""
    gap = 14
    ends.sort(key=lambda one: one["y"])
    for before, one in zip(ends, ends[1:]):
        one["at"] = max(one["at"], before["at"] + gap)
    if ends and ends[-1]["at"] > lowest:
        ends[-1]["at"] = lowest
        for one, after in zip(ends[-2::-1], ends[::-1]):
            one["at"] = min(one["at"], after["at"] - gap)
    drawn = []
    for one in ends:
        drawn.append(f'<g class="s{one["hue"]}">')
        if abs(one["at"] - one["y"]) > 1:
            drawn.append(
                f'<line class="leader" x1="{x + 7:.1f}" y1="{one["y"]:.1f}" x2="{x + 17:.1f}" '
                f'y2="{one["at"]:.1f}"/>'
            )
        drawn.append(
            f'<rect class="key" x="{x + 18:.1f}" y="{one["at"] - 1.5:.1f}" width="10" height="3" '
            'rx="1.5"/>'
        )
        drawn.append(
            f'<text class="label" x="{x + 32:.1f}" y="{one["at"] + 4:.1f}">'
            f"{_e(one['name'])} {_number(one['value'])}</text>"
        )
        drawn.append("</g>")
    return drawn


def _what_moved(found: Sequence[dict], number: int, rates: Sequence[Tuple[object, str]]) -> str:
    """What a point changed against the point before it: of each rate the runs whole and the
    median of the epochs without a fork, where either moved, or why none of its runs had an
    outcome where the point before had one; and the two marks."""
    if number == 0:
        return "the first point"
    point, before = found[number], found[number - 1]
    said = []
    for rate, name in rates:
        now, was = _rate_at(point, rate), _rate_at(before, rate)
        if now is None or was is None:
            continue
        if _whole(now) is None:
            if _whole(was) is not None:
                said.append(f"{name} {_why(point, rate)}")
            continue
        moved = []
        if _whole(now) != _whole(was):
            moved.append(f"whole {_number(_whole(was))} to {_number(_whole(now))}")
        if now["epochs"]["median"] != was["epochs"]["median"]:
            moved.append(
                f"median {_number(was['epochs']['median'])} to {_number(now['epochs']['median'])}"
            )
        if moved:
            said.append(f"{name} {', '.join(moved)}")
    if _rerolled(point):
        said.append("re-rolled")
    if _moved(found, number):
        said.append("chat-sim moved")
    return " · ".join(said) or "nothing"


def _said(found: Sequence[dict], number: int) -> str:
    """A point as a marker's title names it."""
    point = found[number]
    merged = ", ".join(f"#{one['number']}" for one in point["merged"]) or "none"
    said = f"{_when(point)} UTC · main {point['logos_chat'][:7]} · merged {merged}"
    if _moved(found, number):
        said += f" · chat-sim moved to {point['measurement'][:7]}"
    if _rerolled(point):
        said += " · re-rolled: the ids differ from the previous point's"
    return said


def _rate_at(point: dict, rate: object) -> Optional[dict]:
    """The summary of a rate at a point, or `None` where the point has no run of it."""
    return next((one for one in point["summary"] if _rate(one) == rate), None)


def _whole(one: dict) -> Optional[int]:
    """The runs of a rate's summary that ended whole, or `None` where none had an outcome."""
    return None if one["no-outcome"] == one["runs"] else one["whole"]


def _whole_said(one: dict) -> str:
    said = f"{one['whole']} of {one['runs']} whole"
    if one["no-outcome"]:
        said += f", {one['no-outcome']} with no outcome"
    return said


def _why(point: dict, rate: object) -> str:
    """Why none of a rate's runs at a point had an outcome, as its cells say."""
    cells = [cell for cell in point["cells"] if _rate(cell) == rate]
    if cells and all(cell["unbuilt"]["head"] for cell in cells):
        return "chat-sim did not build"
    return "no outcome"


def _moved(found: Sequence[dict], number: int) -> bool:
    """Whether a point measures another chat-sim commit than the point before it."""
    return number > 0 and found[number]["measurement"] != found[number - 1]["measurement"]


def _rerolled(point: dict) -> bool:
    return point["previous"] is not None and point["previous"]["rerolled"]


def _reached(found: Sequence[dict]) -> bool:
    """Whether a rate with loss has had every run whole at a point: its line of runs whole at the
    top. Without loss the line is at the top from the first point."""
    return any(
        one["loss"] and one["runs"] and one["whole"] == one["runs"]
        for point in found
        for one in point["summary"]
    )


def _of_runs(found: Sequence[dict]) -> str:
    """`, of N` where every rate of every point has N runs, else nothing."""
    runs = {one["runs"] for point in found for one in point["summary"]}
    return f", of {runs.pop()}" if len(runs) == 1 else ""


def _merged(point: dict, room: float) -> str:
    """The pull requests a point merged as its date's second line, cut to the first and how many
    more where they would take more than `room`."""
    numbers = [f"#{one['number']}" for one in point["merged"]]
    said = ", ".join(numbers)
    # About the width of a character of the axis's small text.
    if len(said) * 5.6 > room * 0.95 and len(numbers) > 1:
        said = f"{numbers[0]} +{len(numbers) - 1}"
    return said


def _xs(count: int, left: float, width: float) -> List[float]:
    if count == 1:
        return [left + width / 2]
    return [left + number * width / (count - 1) for number in range(count)]


def _every(xs: Sequence[float]) -> int:
    """How many points one date of the axis of time stands for, so that dates keep `_ROOM`
    apart."""
    if len(xs) < 2:
        return 1
    return max(1, math.ceil(_ROOM / (xs[1] - xs[0])))


def _reach(xs: Sequence[float]) -> float:
    """How far from a marker its hover reaches: 10, or half the way to the next point's, so
    that a marker drawn later does not take the hover of the one before it."""
    return 10 if len(xs) < 2 else min(10, (xs[1] - xs[0]) / 2)


def _notes(found: Sequence[dict], xs: Sequence[float], y: float, taken: float) -> List[str]:
    """The commit chat-sim moved to, over the dashed line of each point at another measurement,
    left out where it would start before `taken`, run into the note before it or past the
    chart, the line and the markers' hover text saying it all the same."""
    drawn = []
    for number in range(len(found)):
        if not _moved(found, number):
            continue
        said = f"chat-sim moved: {found[number]['measurement'][:7]}"
        start = xs[number] - len(said) * _CHAR / 2
        if start < taken or start + len(said) * _CHAR > _WIDTH:
            continue
        taken = start + len(said) * _CHAR + 8
        drawn.append(
            f'<text class="note" x="{xs[number]:.1f}" y="{y}" text-anchor="middle">'
            f"{_e(said)}</text>"
        )
    return drawn


def _ticks(top: float) -> List[int]:
    """The ticks of a scale from 0 to `top` or just past it: at most six, a step of 1, 2 or 5
    times a power of ten apart."""
    top, step = max(top, 1), 1
    while math.ceil(top / step) > 5:
        step = step * 5 // 2 if str(step)[0] == "2" else step * 2
    return list(range(0, math.ceil(top / step) * step + 1, step))


def _path(xs: Sequence[float], values: Sequence[Optional[float]], y: Callable) -> str:
    """The path of a line through the values, broken where a point has none."""
    drawn, pen = [], "M"
    for x, value in zip(xs, values):
        if value is None:
            pen = "M"
            continue
        drawn.append(f"{pen}{x:.1f} {y(value):.1f}")
        pen = "L"
    return " ".join(drawn)


def _runs(values: Sequence[Optional[object]]) -> List[List[int]]:
    """The numbers of the values in runs of consecutive ones that are not `None`."""
    found: List[List[int]] = []
    for number, value in enumerate(values):
        if value is None:
            continue
        if found and found[-1][-1] == number - 1:
            found[-1].append(number)
        else:
            found.append([number])
    return found


def _svg(width: float, height: float, label: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="{_e(label)}">'
    )


def _marker(x: float, y: float, said: str, opened: bool, reach: float) -> str:
    """A point on a line, its title `said` in hover that reaches `reach` from it, open where it
    was re-rolled."""
    return (
        f"<g><title>{_e(said)}</title>"
        f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="{reach:.1f}"/>'
        f'<circle class="marker{" open" if opened else ""}" cx="{x:.1f}" cy="{y:.1f}" '
        f'r="{5 if opened else 4}"/></g>'
    )


def _measure(x: float, above: float, bottom: float) -> str:
    return f'<line class="measure" x1="{x:.1f}" x2="{x:.1f}" y1="{above}" y2="{bottom}"/>'


def _date(point: dict, x: float, y: float, anchor: str, kind: str = "axis") -> str:
    return (
        f'<text class="{kind}" x="{x:.1f}" y="{y}" text-anchor="{anchor}">'
        f"{_e(point['time'][5:10])}</text>"
    )


def _legend(*items: Tuple[str, str]) -> str:
    """A legend: each item a key drawn and the words it stands for."""
    return (
        '<p class="legend">'
        + "".join(f"<span>{key}{_e(said)}</span>" for key, said in items)
        + "</p>\n"
    )


def _marks(found: Sequence[dict]) -> List[Tuple[str, str]]:
    """The keys of the two marks, each where a point has it."""
    marks = []
    if any(_moved(found, number) for number in range(len(found))):
        marks.append((_moved_key(), "the measurement moved: another chat-sim commit"))
    if any(_rerolled(point) for point in found):
        marks.append((_rerolled_key(), "re-rolled: the ids differ from the previous point's"))
    return marks


def _line_key(hue: int) -> str:
    return (
        f'<svg class="s{hue}" width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">'
        '<line class="series" x1="1" x2="21" y1="5" y2="5"/>'
        '<circle class="key" cx="11" cy="5" r="4"/></svg>'
    )


def _rate_key(hue: int) -> str:
    return (
        f'<svg class="s{hue}" width="14" height="6" viewBox="0 0 14 6" aria-hidden="true">'
        '<rect class="key" x="0" y="1.5" width="14" height="3" rx="1.5"/></svg>'
    )


def _band_key() -> str:
    return (
        '<svg width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">'
        '<rect class="band" x="1" y="1" width="20" height="8"/></svg>'
    )


def _goal_key() -> str:
    return (
        '<svg width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">'
        '<line class="measure" x1="1" x2="21" y1="5" y2="5"/></svg>'
    )


def _moved_key() -> str:
    return (
        '<svg width="22" height="14" viewBox="0 0 22 14" aria-hidden="true">'
        '<line class="measure" x1="11" x2="11" y1="0" y2="14"/></svg>'
    )


def _rerolled_key() -> str:
    return (
        '<svg class="s2" width="22" height="12" viewBox="0 0 22 12" aria-hidden="true">'
        '<circle class="marker open" cx="11" cy="6" r="4.5"/></svg>'
    )


def _cell_key(kind: str, mark: str = "") -> str:
    drawn = (
        f'<svg class="cell {kind}" width="22" height="16" viewBox="0 0 22 16" aria-hidden="true">'
        '<rect x="0" y="0" width="22" height="16" rx="3"/>'
    )
    if mark == "whole":
        drawn += '<circle class="whole" cx="11" cy="8" r="3"/>'
    if mark == "behind":
        drawn += '<circle class="behind" cx="11" cy="8" r="2.5"/>'
    return drawn + "</svg>"


def _number(value: Optional[float]) -> str:
    """A count or a median as the page shows it: 6, 38.5, or `-` for none."""
    if value is None:
        return "-"
    return f"{value:g}" if isinstance(value, float) else str(value)


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


def _page(title: str, body: str, style: str = "") -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>{_e(title)}</title>\n"
        f"<style>{_STYLE}{style}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body}"
        "</body>\n"
        "</html>\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
