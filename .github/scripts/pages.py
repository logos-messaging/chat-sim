#!/usr/bin/env python3
"""Writes the repository's Pages site from the text CI's sim jobs upload, and keeps on it what
simulate.yml's runs of logos-chat came to.

`report` writes the report of one CI run to `reports/KEY/` of the site, replacing what was
there unless that is the report of a run of a higher number: KEY is `master` for a push to
master and `pr-N` for pull request N. `index` writes the site's landing page, and
`reports/index.html` from the `meta.json` of each report; one it cannot read is left out, with
a warning.

`point` keeps a point of logos-chat's main, a run of simulate.yml's main suite: its
`point.json`, drawn from the run's matrix, its runs' text and its page, in
`logos-chat/main/<sha>/<measurement>/`. What was there is replaced unless a run of a higher
number wrote it, and then only the newest eleven points keep their runs' text: the page of a
point that loses it is drawn again from its point.json. The points are in the order of their
runs' numbers, which GitHub gives in the order the runs were started and a re-run keeps;
`previous` names the point a new one follows. `pr` keeps the newest run of a logos-chat pull
request in `logos-chat/pr-N/` the same way: its matrix, both sides' runs' text, its `meta.json`
and its report page. `long` keeps a run of the long suite at a commit of main, held to what is
expected, in `logos-chat/long/<sha>/<measurement>/` the same way: its `long.json`, each
scenario's compare output, its runs' text and its page; the newest fifteen long runs keep their
text. `trend` draws the graph over main, `logos-chat/index.html`, from the `point.json` of every
point, and links the newest long run; one it cannot read is left out, with a warning. `closed`
names the runs of logos-chat's pull requests that are no longer open.

The pages run no script, and every text that comes from a run, from a pull request's title or
from an API is escaped.
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

import simulate

# The scenarios of the job "Sim in one process", in the order it runs them.
FIFTY_EPOCHS = ("50-epochs", "50-epochs-waku")

AS_EXPECTED = "as expected"
NOT_AS_EXPECTED = "not as expected"
NO_OUTCOME = "no outcome"

# Where the site keeps what simulate.yml's runs of logos-chat came to.
LOGOS_CHAT = "logos-chat"
# How many of the newest points of main keep their runs' text, in a site Pages holds to 1 GB. A
# point takes about 29.6 MB while it keeps its text: 26.7 MB of its 160 runs', 13.4 MB each
# group's, on master's CI run 44661545505 without report.json, and 2.9 MB of its page, which
# shows that text. Eleven take 326 MB. Every point keeps its point.json.
WINDOW = 11
# How many of the newest long runs keep their text, their runs' and compare's. A long run takes
# about 10 MB while it keeps its text: 7 MB of the three runs of grow-waku-size-50, 1 MB of the
# six of grow-waku-loss-0.1 and its `-groupv3`, 1 MB of the sixteen of the 50-epochs-by-saro
# scenarios, on master's CI run 44661545505 without report.json, and its page, 1 MB, which shows
# that text. Fifteen take 150 MB, so with the eleven points the two hold under half of Pages' 1 GB.
# Every long run keeps its long.json, and a page drawn from it.
LONG_WINDOW = 15
# The text of a run the site keeps, and the build's log of one whose job could not build
# chat-sim. Its report.json, ten times the rest, stays in the artifacts.
TEXTS = ("run.txt", "report.txt", "run.json", "build.log")
# How many rates of a group the graph over main tells apart, a hue of `_STYLE`'s ramp each.
HUES = 4
# The repositories whose commits and pull requests a point names.
LOGOS_CHAT_URL = "https://github.com/logos-messaging/logos-chat"
CHAT_SIM_URL = "https://github.com/logos-messaging/chat-sim"

_KEY = re.compile(r"master|pr-([1-9][0-9]*)")
_SHA = re.compile(r"[0-9a-f]{40}")
_URL = re.compile(r"https?://[^\s\"'<>]+")
_TIME = "%Y-%m-%dT%H:%M:%SZ"
_RUN = re.compile(r"[0-9]+")
_PR = re.compile(r"[1-9][0-9]*")
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
_COUNTS = {field: (int,) for field in ("cells", "compared", *_CHANGES, "below", "no-outcome")}
_MATRIX = {
    "head": _SIDE,
    "base": (dict, _NONE),
    "base_point": (dict, _NONE),
    "cells": (list,),
    "counts": _COUNTS,
    "verdict": (str,),
    "groups": (list,),
    "summary": (list, _NONE),
    "spread": (dict, _NONE),
}
# The counts and the verdict of a group's cells.
_GROUP = {"group": (str,), "counts": _COUNTS, "verdict": (str,)}
# The point of main a matrix names: the one that stood in for the base's runs, and its spread.
_NAMED = {"logos_chat": (str,), "measurement": (str,), "run_url": (str,)}
_SPREAD = {**_NAMED, "summary": (list,), "seeds": (list,)}
_SEED = {"scenario": (str,), "seed": (int,), "epochs": (int, _NONE), "ended": (str, _NONE)}
_CELL = {
    "scenario": (str,),
    "group": (str,),
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
# A point need not name the group of a cell or a scenario: the points that do not ran GroupV2's
# alone.
_UNGROUPED_CELL = {field: kind for field, kind in _POINT_CELL.items() if field != "group"}
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
    "group": (str,),
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
    "split_at": {
        "lowest": (int, _NONE),
        "median": (int, float, _NONE),
        "highest": (int, _NONE),
    },
    "lines_off_trunk": {"median": (int, float, _NONE)},
    "members_off_trunk": {"median": (int, float, _NONE)},
    "seated": {"median": (int, float, _NONE)},
}
# A point need not hold how its runs forked: the points that do not are of earlier measurements.
_FORKS = ("split_at", "lines_off_trunk", "seated")
_UNFORKED_SUMMARY = {field: kind for field, kind in _SUMMARY.items() if field not in _FORKS}
_UNGROUPED_SUMMARY = {field: kind for field, kind in _UNFORKED_SUMMARY.items() if field != "group"}
# What `write_point`, `write_pr` and `write_long` write.
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
_LONG = {
    "logos_chat": (str,),
    "measurement": (str,),
    "chat_sim": (str,),
    "time": (str,),
    "run_url": (str,),
    "run_number": (int,),
    "scenarios": (list,),
}
_LONG_SCENARIO = {"scenario": (str,), "compare": (dict, _NONE), "runs": (list,)}
_COMPARE = {"verdict": (str,), "counted": (str,)}
_LONG_RUN = {"run": (int,), "seed": (int,), "unbuilt": (bool,), "outcome": (dict, _NONE)}
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
# The report pages, beside the charts' classes.
_REPORT = """
.facts code{font-size:13px}
.verdict{font-size:1.1em}
.side{margin:.75rem 0 .25rem;color:var(--muted);font-size:13px}
.heats{display:flex;flex-wrap:wrap;align-items:flex-start;gap:0 3rem}
.sides{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,24rem),1fr));
gap:0 2.5rem}
.sides>div{min-width:0}
details{margin:.75rem 0}
details.run>summary{font-size:1.1em;font-weight:600}
summary span{font-weight:normal;margin-left:.4rem}
details.text>summary{font-size:13px;color:var(--muted)}
.who{font-size:14px;margin:.4rem 0}
.lines,.runs,.rates{font-size:14px}
.lines th,.lines td{padding:.15rem 1rem .15rem 0}
.n{text-align:right;font-variant-numeric:tabular-nums}
.rates th[colspan]{text-align:center}
.rates td,.runs td.n{white-space:nowrap}
.spread{margin-top:18px}
.spread th,.spread td{height:30px;padding:0 .75rem 0 0;border:0;font-size:13px;
vertical-align:middle;white-space:nowrap}
.spread th{font-weight:normal;color:var(--muted)}
.cell .outline{fill:none;stroke-width:2.5}
.worse .outline{stroke:var(--bad)}
.better .outline{stroke:var(--good)}
.tick{stroke:var(--line)}
.seed{fill:var(--muted);fill-opacity:.55}
.head{fill:var(--fg);stroke:var(--bg);stroke-width:2}
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
        "logos-chat/main/SHA/MEASUREMENT/ of SITE: point.json, each run's run.txt, report.txt "
        "and run.json, or build.log of a job that could not build chat-sim, and the point's "
        "page, index.html. What was there is replaced, unless it is the point of a run of a "
        f"higher number. Then removes the runs' text of each point past the newest {WINDOW}, by "
        "run number, and draws again the page of each point that loses it or has none, and the "
        "report of each pull request such a point stood in for. A matrix that read no run at "
        "the revision, built or not, is refused.",
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
        "logos-chat/pr-N/ of SITE: the matrix, each run's run.txt, report.txt and run.json, or "
        "build.log of a job that could not build chat-sim, at the revision under head/ and at "
        "the base under base/, meta.json, and the report page, index.html. What was there is "
        "replaced, unless it is a run of a higher number.",
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
    held = commands.add_parser(
        "long",
        help="keep a run of the long suite at a commit of logos-chat's main",
        description="Writes the run of the long suite, held to what is expected, to "
        "logos-chat/long/SHA/MEASUREMENT/ of SITE: long.json, which says what ran and what each "
        "scenario came to, each scenario's compare output as compare.txt, each run's run.txt, "
        "report.txt and run.json, or build.log of a job that could not build chat-sim, and the "
        "page, index.html. What was there is replaced, unless it is the long run of a run of a "
        f"higher number. Then removes the text of each long run past the newest {LONG_WINDOW}, by "
        "run number, and draws its page again. Runs of which none was read, built or not, are "
        "refused.",
    )
    _add_ran(held, runs="the run jobs uploaded", matrix=False)
    held.add_argument(
        "--compared",
        metavar="DIR",
        type=Path,
        required=True,
        help="each scenario's compare in <scenario>/, at any depth: its output, compare.txt, and "
        "the status it exited with, compare.status",
    )
    held.add_argument(
        "--expected",
        metavar="DIR",
        type=Path,
        required=True,
        help="tools/sim/expected: the outcomes chat-sim holds, a file a scenario",
    )
    held.add_argument(
        "--scenarios",
        metavar="DIR",
        type=Path,
        required=True,
        help="tools/sim/scenarios: a file a scenario, whose seed is that of run 1 of its sweep",
    )
    held.set_defaults(command=_long)
    trend = commands.add_parser(
        "trend",
        help="draw the graph over logos-chat's main",
        description="Writes logos-chat/index.html of SITE, the graph over logos-chat's main, "
        "from the point.json of each point, in the order of their runs' numbers, and a link to "
        "the long run of the highest run number. A point.json or a long.json it cannot read is "
        f"left out, with a warning; a group whose newest point has more than {HUES} rates is "
        "refused.",
    )
    trend.add_argument("site", metavar="SITE", type=Path, help="the site, as gh-pages holds it")
    trend.set_defaults(command=_trend)
    closed = commands.add_parser(
        "closed",
        help="name the runs of logos-chat's pull requests that are no longer open",
        description="Prints each logos-chat/pr-N of SITE whose pull request is no longer open, a "
        "line each, the lowest number first: with --open, each whose N the file does not list, "
        "and with --pr, that pull request's alone. A line of the file, or a pr, that is not the "
        "number of a pull request is refused.",
    )
    closed.add_argument("site", metavar="SITE", type=Path, help="the site, as gh-pages holds it")
    which = closed.add_mutually_exclusive_group(required=True)
    which.add_argument(
        "--open",
        metavar="FILE",
        type=Path,
        help="the numbers of logos-chat's open pull requests, one a line, as its pulls API "
        "lists them",
    )
    which.add_argument("--pr", metavar="N", help="the number of a pull request that closed")
    closed.set_defaults(command=_closed)
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
        f"<li>{_link('logos-chat/', 'logos-chat over main')}</li>\n"
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
    `logos-chat/main/<sha>/<measurement>/` of `site`, replacing what was there: point.json, each
    run's text under `runs` at any depth in `<scenario>/<run>/`, its run by number, and the
    point's page.

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
    point = read_point(out / "point.json")
    draw_point(out, point)
    return point


def write_pr(matrix: dict, runs: Path, base_runs: Optional[Path], site: Path, meta: dict) -> dict:
    """Writes the run of a logos-chat pull request to `logos-chat/pr-N/` of `site`, replacing
    what was there: `matrix` as matrix.json, each run's text under `runs` in
    `head/<scenario>/<run>/` and under `base_runs` in `base/<scenario>/<run>/`, at any depth and
    each run by number, `meta`, which says what ran, as meta.json, and the report page.

    Returns `meta`, as `read_pr_meta` reads it back.
    """
    out = site / LOGOS_CHAT / f"pr-{meta['pr']}"
    copies = _texts(runs, out / "head")
    if base_runs is not None:
        copies += _texts(base_runs, out / "base")
    _replace(out, copies)
    (out / "matrix.json").write_text(json.dumps(matrix, indent=1) + "\n", encoding="utf-8")
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    meta = read_pr_meta(out / "meta.json")
    draw_pr(out, matrix, meta)
    return meta


def write_long(
    runs: Path,
    compared: Path,
    site: Path,
    facts: dict,
    swept: Sequence[Tuple[str, int]],
    scenarios: Path,
) -> dict:
    """Writes the run of the long suite `facts` says ran to
    `logos-chat/long/<sha>/<measurement>/` of `site`, replacing what was there: long.json, each
    run's text under `runs` at any depth in `<scenario>/<run>/`, its run by number, each
    scenario's compare output under `compared` at any depth in `<scenario>/compare.txt`, and the
    page.

    `swept` names the suite's scenarios and how many runs of each it asked for, and `scenarios`
    holds their files. Runs of which none was read, built or not, are refused. Returns the long
    run, as `read_long` reads it back.
    """
    out = _long_at(site, facts["logos_chat"], facts["measurement"])
    copies = _texts(runs, out)
    held = []
    for name, count in swept:
        said = None
        written = next(iter(sorted(compared.glob(f"**/{name}/compare.txt"))), None)
        if written is not None:
            copies.append((written, out / name / "compare.txt"))
            lines = written.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            said = {"verdict": outcome(written.parent), "counted": lines[-1] if lines else ""}
        first = _first_seed(scenarios / f"{name}.json")
        held.append(
            {"scenario": name, "compare": said, "runs": _long_runs(runs, name, count, first)}
        )
    ran = [run for one in held for run in one["runs"]]
    if not any(run["outcome"] is not None or run["unbuilt"] for run in ran):
        raise Unreadable("RUNS: no run at the revision was read, and none could not be built")
    _replace(out, copies)
    long = {**facts, "scenarios": held}
    (out / "long.json").write_text(json.dumps(long, indent=1) + "\n", encoding="utf-8")
    long = read_long(out / "long.json")
    draw_long(out, long)
    return long


def draw_point(directory: Path, point: dict) -> None:
    """Writes the page of the point in `directory`, its index.html, from `point` and the runs'
    text `directory` keeps: what ran, the pull requests it merged and the point it follows, its
    cells as a heatmap a group, how the runs of each group's rate ended, and each run with the
    command that runs it again and, while its text is kept, the text, its lines and its wall time;
    where chat-sim did not build, the build's log, once. Every file it links is one `directory`
    holds."""
    logos_chat, measured, before = point["logos_chat"], point["measurement"], point["previous"]
    facts = {"revision": logos_chat, "base": "", "chat_sim": point["chat_sim"]}
    rows = _by_rate(point["cells"])
    merged = ", ".join(
        _link(f"{LOGOS_CHAT_URL}/pull/{one['number']}", f"#{one['number']}")
        + f" {_e(one['title'])}"
        for one in point["merged"]
    )
    follows = "It is the first point."
    if before is not None:
        at = f"../../{before['logos_chat']}/{before['measurement']}/"
        follows = (
            f"It follows the point of main {_link(at, before['logos_chat'][:7], code=True)} at "
            f"chat-sim {_code(before['measurement'])}"
        )
        follows += ", and its runs drew other ids: re-rolled." if before["rerolled"] else "."
    body = (
        f'<p class="muted">{_link("../../../../", "chat-sim")} / '
        f'{_link("../../../", "Over main")}</p>\n'
        f"<h1>logos-chat main {_e(logos_chat[:7])}, {_e(point['time'][:10])}</h1>\n"
        f'<p class="facts">main {_commit(LOGOS_CHAT_URL, logos_chat)} · chat-sim '
        f"{_commit(CHAT_SIM_URL, point['chat_sim'])} · the measurement "
        f"{_commit(CHAT_SIM_URL, measured)} · {_link(point['run_url'], 'the run')} · "
        f"{_when(point)} UTC</p>\n"
        f"<p>Pull requests merged since the point it follows: {merged or _NONE_MERGED}. "
        f"{follows}</p>\n"
    )
    texts = [_run_dir(directory, cell) / name for cell in point["cells"] for name in TEXTS]
    kept = any(path.is_file() for path in texts)
    if not kept:
        body += (
            f'<p class="muted">The site keeps the runs\' text of the newest {WINDOW} points, and '
            "no longer this one's: the command under each run makes it again.</p>\n"
        )
    body += (
        '<section class="chart" id="matrix">\n<h2>The matrix</h2>\n'
        + "".join(
            f"<h3>{_e(called)}</h3>\n" + _heatmap(_by_rate(cells), f"The point's {called} runs")
            for called, cells in _groups(point["cells"])
        )
        + _heat_legend()
        + '<p class="caption">A row a group\'s rate and a column a seed: a cell\'s colour is the '
        "epochs its run went without a fork; a dot marks a run that ended whole, a ring one that "
        "ended one group with members behind, and no mark one that split or stalled. Its hover "
        "text is how the run came out.</p>\n"
        "</section>\n"
    )
    unbuilt = next((cell for cell in point["cells"] if cell["unbuilt"]["head"]), None)
    if unbuilt is not None:
        body += _build(_job(unbuilt), _run_dir(directory, unbuilt), _run_href(unbuilt), kept)
    body += _rates_table(directory, point) + '<section id="runs">\n<h2>The runs</h2>\n'
    # The section above shows the build's log once; a run that could not build links its own.
    for name, cells in rows:
        listed = [cells[seed] for seed in sorted(cells)]
        named = f"{simulate.GROUPS[group(listed[0])]} {name}"
        body += (
            f'<details>\n<summary>{_e(named)} <span class="muted">{len(listed)} runs</span>'
            "</summary>\n"
            + _runs_table(directory, listed)
            + "".join(
                f'<details class="run">\n<summary>seed {cell["seed"]} <span class="muted">'
                f"{_e(simulate.words(cell, False))}</span></summary>\n"
                + _side(
                    cell,
                    "head",
                    None,
                    _run_dir(directory, cell),
                    _run_href(cell),
                    inline=not cell["unbuilt"]["head"],
                )
                + _again(cell, facts)
                + "</details>\n"
                for cell in listed
            )
            + "</details>\n"
        )
    body += (
        "</section>\n"
        f'<p class="muted">What the page shows: the guide\'s '
        f'{_link("../../../../guide/reports/#the-pages-of-logos-chats-runs", "Reports")}.</p>\n'
    )
    title = f"chat-sim: logos-chat main {logos_chat[:7]}"
    (directory / "index.html").write_text(_page(title, body, _CHARTS + _REPORT), encoding="utf-8")


def draw_pr(out: Path, matrix: dict, meta: dict) -> None:
    """Writes the report page of the run of a logos-chat pull request kept in `out`, its
    index.html, from `matrix` and `meta`, which `out` holds, and the runs' text it keeps: the
    verdict as the comment says it; of each group the head's cells and the base's as heatmaps,
    and where the matrix has main's spread, where the head's runs fall among main's; then of each
    group each cell's runs at both sides, with their text and lines, and the command that runs
    the cell again. Where chat-sim did not build at the head, the build's log takes the place of
    the cells, and where main's point stood in for the base's runs, the point's page, or its
    point.json until it has one, is linked in theirs, and its runs' text is shown from the point's
    directory while the point keeps it."""
    head, base, point = meta["revision"], meta["base"], matrix["base_point"]
    facts = {"revision": head, "base": base or "", "chat_sim": meta["chat_sim"]}
    against = simulate.compares(matrix)
    at = page = there = None
    if point is not None:
        at = f"../main/{point['logos_chat']}/{point['measurement']}/"
        there = _point_at(out.parent.parent, point["logos_chat"], point["measurement"])
        page = at if (there / "index.html").is_file() else at + "point.json"
    # As the comment has it: a head that did not build was held to no point.
    stood = None if matrix["verdict"] == "unbuilt" else point
    verdict = simulate.ran_at(facts, stood, _code, lambda words, _: _link(page, words))
    said = [
        f"head {_commit(LOGOS_CHAT_URL, head)}",
        "no base" if base is None else f"merge base {_commit(LOGOS_CHAT_URL, base)}",
        f"chat-sim {_commit(CHAT_SIM_URL, meta['chat_sim'])}",
        _link(f"{LOGOS_CHAT_URL}/pull/{meta['pr']}", "the pull request"),
        _link(meta["run_url"], "the run"),
        f"{_when(meta)} UTC",
    ]
    body = (
        f'<p class="muted">{_link("../../", "chat-sim")} / {_link("../", "Over main")}</p>\n'
        f"<h1>logos-chat #{meta['pr']}</h1>\n"
        f'<p class="facts">{" · ".join(said)}</p>\n'
        f'<p class="verdict"><b>{_e(simulate.headline(matrix))}</b> '
        f'<span class="muted">{verdict}</span></p>\n'
    )
    if matrix["verdict"] == "unbuilt":
        unbuilt = next((cell for cell in matrix["cells"] if cell["unbuilt"]["head"]), None)
        if unbuilt is not None:
            run = _run_href(unbuilt)
            body += _build(_job(unbuilt), out / "head" / run, f"head/{run}")
    else:
        drawn = strips = ""
        for called, cells in _groups(matrix["cells"]):
            rows = _by_rate(cells)
            heats = [
                f'<div>\n<p class="side">the head {_code(head)}</p>\n'
                + _heatmap(rows, f"The head's {called} runs", "head", against)
                + "</div>\n"
            ]
            if matrix["base"] is not None:
                named = f"the base {_code(base)}"
                if at is not None:
                    named = f"main's point at {_link(page, point['logos_chat'][:7], code=True)}"
                heats.append(
                    f'<div>\n<p class="side">{named}</p>\n'
                    + _heatmap(rows, f"The base's {called} runs", "base")
                    + "</div>\n"
                )
            if matrix["spread"] is not None:
                heats.append(_spread(matrix["spread"], rows))
            drawn += f'<h3>{_e(called)}</h3>\n<div class="heats">\n{"".join(heats)}</div>\n'
            strips += _strips(matrix["spread"], rows, against, called)
        changes = {cell["change"] for cell in matrix["cells"]}
        outlined = [
            (_cell_key(f"bin2 {change}", "outline"), f"{change} than the base")
            for change in ("worse", "better")
            if change in changes
        ]
        body += (
            '<section class="chart" id="matrix">\n<h2>The matrix</h2>\n'
            + drawn
            + _heat_legend(*outlined)
            + '<p class="caption">A row a group\'s rate and a column a seed: a cell\'s colour is '
            "the epochs its run went without a fork; a dot marks a run that ended whole, a ring "
            "one that ended one group with members behind, and no mark one that split or stalled. "
            "An outline marks a run of the head that stands lower or higher than the base's. A "
            "cell's hover text is how the run came out.</p>\n"
            + strips
            + "</section>\n"
            '<section id="cells">\n<h2>The cells</h2>\n'
        )
        groups = list(simulate.GROUPS)
        moved = sorted(
            matrix["cells"],
            key=lambda cell: (groups.index(group(cell)), cell["change"] not in ("better", "worse")),
        )
        for cell in moved:
            run = _run_href(cell)
            named = f"the head {_code(head)}"
            sides = _side(cell, "head", named, out / "head" / run, f"head/{run}")
            if matrix["base"] is not None and at is None:
                named = f"the base {_code(base)}"
                sides += _side(cell, "base", named, out / "base" / run, f"base/{run}")
            elif at is not None:
                named = f"main's point at {_link(page, point['logos_chat'][:7], code=True)}"
                kept = any((there / run / name).is_file() for name in TEXTS)
                sides += _side(cell, "base", named, there / run, at + run, inline=kept)
            kind = {"worse": "bad", "better": "good"}.get(cell["change"] or "", "muted")
            if cell["head"] is None:
                kind = "none"
            opened = " open" if cell["change"] in ("better", "worse") else ""
            called = simulate.GROUPS[group(cell)]
            body += (
                f'<details class="run"{opened}>\n<summary>{_e(called)} {_e(cell["rate"] or "-")} '
                f'loss, seed {cell["seed"]} <span class="{kind}">'
                f"{_e(simulate.words(cell, against))}</span></summary>\n"
                f'<div class="sides">\n{sides}</div>\n'
                + _again(cell, facts)
                + "</details>\n"
            )
        body += "</section>\n"
    body += (
        f'<p class="muted">What the page shows: the guide\'s '
        f'{_link("../../guide/reports/#the-pages-of-logos-chats-runs", "Reports")}.</p>\n'
    )
    title = f"chat-sim: logos-chat #{meta['pr']}"
    (out / "index.html").write_text(_page(title, body, _CHARTS + _REPORT), encoding="utf-8")


def draw_long(directory: Path, long: dict) -> None:
    """Writes the page of the long run in `directory`, its index.html, from `long` and the text
    `directory` keeps: what ran, a row a scenario with what its runs came to and what compare said
    of them, the build's log once where chat-sim did not build, then a section a scenario with
    compare's verdict and, while its text is kept, its output, its runs as a table, and each run
    with the command that runs it again and, while its text is kept, the text and its lines. Every
    file it links is one `directory` holds."""
    logos_chat = long["logos_chat"]
    facts = {"revision": logos_chat, "base": "", "chat_sim": long["chat_sim"]}
    kept = any(path.is_file() for path in _long_texts(directory, long))
    body = (
        f'<p class="muted">{_link("../../../../", "chat-sim")} / '
        f'{_link("../../../", "Over main")}</p>\n'
        f"<h1>logos-chat main {_e(logos_chat[:7])}, the long suite, {_e(long['time'][:10])}</h1>\n"
        f'<p class="facts">main {_commit(LOGOS_CHAT_URL, logos_chat)} · chat-sim '
        f"{_commit(CHAT_SIM_URL, long['chat_sim'])} · the measurement "
        f"{_commit(CHAT_SIM_URL, long['measurement'])} · {_link(long['run_url'], 'the run')} · "
        f"{_when(long)} UTC</p>\n"
        "<p>The two scenarios of fifty epochs, a run each, and each scenario "
        "<code>tools/sim/expected</code> holds but the goal's on either group, as many runs as it "
        "holds outcomes, held to them, at this commit of logos-chat's main. The graph over main "
        "draws none of it.</p>\n"
    )
    if not kept:
        body += (
            f'<p class="muted">The site keeps the text of the newest {LONG_WINDOW} long runs, and '
            "no longer this one's: the command under each run makes it again.</p>\n"
        )
    rows = "".join(
        f'<tr><td>{_link("#" + one["scenario"], one["scenario"])}</td>'
        f'<td class="n">{len(one["runs"])}</td><td>{_e(_long_came_to(one))}</td>'
        f'<td class="{_long_against(one)[0]}">{_e(_long_against(one)[1])}</td></tr>\n'
        for one in long["scenarios"]
    )
    body += (
        '<section id="held">\n<h2>Held to what is expected</h2>\n'
        '<div class="wide"><table class="runs">\n'
        '<thead><tr><th>Scenario</th><th class="n">Runs</th><th>Came to</th>'
        "<th>Against what is held</th></tr></thead>\n"
        f"<tbody>\n{rows}</tbody>\n</table></div>\n"
        '<p class="caption">Came to: each run\'s verdict. Against what is held: the count '
        "<code>tools/sim compare</code> ends with, each run of the sweep held to the outcome "
        "expected of it; nothing held where chat-sim holds no outcome of the scenario.</p>\n"
        "</section>\n"
    )
    unbuilt = next(
        ((one, run) for one in long["scenarios"] for run in one["runs"] if run["unbuilt"]), None
    )
    if unbuilt is not None:
        one, run = unbuilt
        at = f"{one['scenario']}/{run['run']}"
        body += _build(one["scenario"], directory / at, f"{at}/", kept)
    for one in long["scenarios"]:
        name = one["scenario"]
        kind, said = _long_against(one)
        verdict = f'<b class="{kind}">{_e(said)}</b>'
        if one["compare"] is not None:
            verdict = (
                f'<b class="{kind}">{_e(one["compare"]["verdict"])}</b> '
                f'<span class="muted">{_e(said)}</span>'
            )
        elif said == "-":
            verdict = f'<b class="none">{_e(_long_came_to(one))}</b>'
        body += (
            f'<section id="{_e(name)}">\n<h2>{_e(name)}</h2>\n'
            f'<p class="verdict">{verdict}</p>\n'
        )
        if (directory / name / "compare.txt").is_file():
            body += _shown(directory / name, f"{name}/", "compare.txt")
        body += _long_runs_table(directory, one)
        # The section above shows the build's log once; a run that could not build links its own.
        for run in one["runs"]:
            at, got = f"{name}/{run['run']}", run["outcome"]
            kind = "none" if got is None else "muted"
            body += (
                f'<details class="run">\n<summary>seed {run["seed"]} '
                f'<span class="{kind}">{_e(_long_ended(run))}</span></summary>\n'
            )
            cell = {"head": got, "unbuilt": {"head": run["unbuilt"]}}
            body += _side(cell, "head", None, directory / at, f"{at}/", inline=not run["unbuilt"])
            body += _again({"scenario": name, "run": run["run"], "seed": run["seed"]}, facts)
            body += "</details>\n"
        body += "</section>\n"
    body += (
        f'<p class="muted">What the page shows: the guide\'s '
        f'{_link("../../../../guide/reports/#the-pages-of-logos-chats-runs", "Reports")}.</p>\n'
    )
    title = f"chat-sim: logos-chat main {logos_chat[:7]}, the long suite"
    (directory / "index.html").write_text(_page(title, body, _CHARTS + _REPORT), encoding="utf-8")


def write_trend(site: Path) -> None:
    """Writes `logos-chat/index.html` of `site`, the graph over logos-chat's main, from each
    point `points` reads, the oldest at the left: of each group's rate the epochs without a fork
    and the runs whole, the newest point's cells, the members off the trunk of a group once a
    rate of it has had every run whole, and the list of the points, the newest first.

    Every number it draws is one a point.json holds. A point that measures another chat-sim
    commit than the point before it has a dashed line, and one re-rolled against the point it
    follows an open marker. The rates drawn of a group are those of its newest point, and more
    than `HUES` are refused. Of the long runs it links the one of the highest run number, and
    draws nothing.
    """
    found = [point for _, point in reversed(points(site))]
    groups = [(called, held, _rates(held, called)) for called, held in _apart(found)]
    reached = [(called, held, rates) for called, held, rates in groups if _reached(held)]
    body = (
        f'<p class="muted">{_link("../", "chat-sim")} / {_link("../guide/", "Guide")}</p>\n'
        "<h1>Over main</h1>\n"
        '<p class="lead">How many epochs a group of logos-chat clients lasts as one group while '
        f"it grows toward epoch {_TOP}, on GroupV2 and on GroupV3 at each rate of loss main runs, "
        "over logos-chat's main: a point for each of its commits that was measured, the oldest at "
        "the left.</p>\n"
    )
    newest = next(iter(longs(site)), None)
    if newest is not None:
        _, long = newest
        at = f"long/{long['logos_chat']}/{long['measurement']}/"
        body += (
            f"<p>{_link(at, 'The newest long run')}, at main {_code(long['logos_chat'])} on "
            f"{_when(long)} UTC: the two scenarios of fifty epochs and each scenario chat-sim "
            "holds to what is expected but the goal's on either group, run at main's tip each "
            "Sunday. The charts are of main's points alone, so nothing of them is drawn "
            "here.</p>\n"
        )
    if found:
        body += (
            _epochs_chart(found, groups)
            + _whole_chart(groups)
            + _newest_chart(found[-1], groups)
            + (_off_chart(reached) if reached else "")
            + _points_list(found, groups)
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
    return _read_all(sorted((site / LOGOS_CHAT / "main").glob("*/*/point.json")), read_point)


def longs(site: Path) -> List[Tuple[Path, dict]]:
    """Each long run `site` holds, its directory and its long.json, the newest first by run
    number. A long.json `read_long` refuses is left out, and a warning on standard error says
    why."""
    return _read_all(sorted((site / LOGOS_CHAT / "long").glob("*/*/long.json")), read_long)


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


def prune(found: Sequence[Tuple[Path, dict]], window: int = WINDOW) -> List[Path]:
    """Removes the text from each point or long run of `found` past the newest `window` by run
    number, its runs' and a long run's compare output, each `<scenario>/<run>/` and
    `<scenario>/` it empties with it, and returns the directories it removed text from. Each
    keeps its point.json or long.json and every other file."""
    pruned = []
    for directory, _ in sorted(found, key=_newest, reverse=True)[window:]:
        texts = [path for name in TEXTS for path in directory.glob(f"*/*/{name}") if path.is_file()]
        texts += [path for path in directory.glob("*/compare.txt") if path.is_file()]
        for path in texts:
            path.unlink()
        # The deepest first, so that a scenario's directory is emptied of its runs' before it is
        # looked at.
        emptied = {path.parent for path in texts} | {path.parent.parent for path in texts}
        for one in sorted(emptied - {directory}, key=lambda one: len(one.parts), reverse=True):
            if not any(one.iterdir()):
                one.rmdir()
        if texts:
            pruned.append(directory)
    return pruned


def pull_requests(site: Path) -> List[int]:
    """The numbers of the logos-chat pull requests whose run `site` keeps in
    `logos-chat/pr-N/`, the lowest first."""
    return sorted(
        int(directory.name.removeprefix("pr-"))
        for directory in (site / LOGOS_CHAT).glob("pr-*")
        if directory.is_dir() and _PR.fullmatch(directory.name.removeprefix("pr-"))
    )


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
        for number, one in enumerate(matrix["groups"]):
            where = f"groups[{number}]"
            _shaped(one, _GROUP, where)
            _group(one, where)
            if one["verdict"] not in _VERDICTS:
                raise Unreadable(f"{where}.verdict {one['verdict']!r} is none of a matrix")
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
            # A pull request's matrix holds main's point as it was, of any shape a point had.
            for number, one in enumerate(spread["summary"]):
                _summary(one, f"spread.summary[{number}]", _held_summary(one))
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
            shape = _POINT_CELL if "group" in cell else _UNGROUPED_CELL
            _cell(cell, f"cells[{number}]", alone=True, shape=shape)
        for number, one in enumerate(point["summary"]):
            _summary(one, f"summary[{number}]", _held_summary(one))
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


def read_long(path: Path) -> dict:
    """A long.json, refused unless it is one `write_long` writes, in the directory of its
    logos-chat commit and its measurement."""
    long = _json(path)
    try:
        _shaped(long, _LONG)
        _commits(long, "logos_chat", "measurement", "chat_sim")
        named = (path.parent.parent.name, path.parent.name)
        if (long["logos_chat"], long["measurement"]) != named:
            raise Unreadable("logos_chat and measurement do not name its directory")
        _ran(long)
        seen = set()
        for number, one in enumerate(long["scenarios"]):
            _long_scenario(one, f"scenarios[{number}]")
            if one["scenario"] in seen:
                raise Unreadable(f"scenarios[{number}] is a second of {one['scenario']}")
            seen.add(one["scenario"])
    except Unreadable as refused:
        raise Unreadable(f"{path}: {refused}") from None
    return long


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
    kept = [(out, point), *((at, one) for at, one in found if at != out)]
    pruned = prune(kept)
    for directory in pruned:
        print(
            f"Removed the runs' text of {directory.relative_to(args.site)}/: past the newest "
            f"{WINDOW} points."
        )
    # A point kept before its page was drawn has none, and the graph's list links it.
    drawn = [
        directory
        for directory, _ in kept
        if directory in pruned or not (directory / "index.html").is_file()
    ]
    for directory, one in kept:
        if directory in drawn:
            draw_point(directory, one)
    # A pull request's page links the page and the runs' text of main's point that stood in for
    # its base, where the point has them.
    for directory in sorted((args.site / LOGOS_CHAT).glob("pr-*")):
        try:
            ran = read_matrix(directory / "matrix.json")
            meta = read_pr_meta(directory / "meta.json")
        except Unreadable:
            continue
        at = ran["base_point"]
        if at and _point_at(args.site, at["logos_chat"], at["measurement"]) in drawn:
            draw_pr(directory, ran, meta)
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


def _long(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    _check_facts(parser, args)
    for flag, directory in (
        ("--compared", args.compared),
        ("--expected", args.expected),
        ("--scenarios", args.scenarios),
    ):
        if not directory.is_dir():
            parser.error(f"{flag}: no such directory: {directory}")
    found = longs(args.site)
    out = _long_at(args.site, args.revision, args.measurement)
    there = next((long for directory, long in found if directory == out), None)
    if there is not None and there["run_number"] > args.run_number:
        print(
            f"Kept {out.relative_to(args.site)}/: it holds the long run of run "
            f"{there['run_number']}, newer than run {args.run_number}."
        )
        return 0
    try:
        long = write_long(
            args.runs,
            args.compared,
            args.site,
            {
                "logos_chat": args.revision,
                "measurement": args.measurement,
                "chat_sim": args.chat_sim,
                "time": args.time,
                "run_url": args.run_url,
                "run_number": args.run_number,
            },
            simulate.scenarios("long", args.expected),
            args.scenarios,
        )
    except Unreadable as refused:
        parser.error(str(refused))
    kept = [(out, long), *((at, one) for at, one in found if at != out)]
    for directory in prune(kept, LONG_WINDOW):
        print(
            f"Removed the text of {directory.relative_to(args.site)}/: past the newest "
            f"{LONG_WINDOW} long runs."
        )
        draw_long(directory, next(one for at, one in kept if at == directory))
    return 0


def _trend(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        write_trend(args.site)
    except Unreadable as refused:
        parser.error(str(refused))
    return 0


def _closed(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    held = pull_requests(args.site)
    if args.pr is not None:
        if _PR.fullmatch(args.pr) is None:
            parser.error(f"--pr: {args.pr!r} is not the number of a pull request")
        gone = [number for number in held if number == int(args.pr)]
    else:
        try:
            listed = args.open.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            parser.error(f"--open: {error}")
        refused = next((line for line in listed if _PR.fullmatch(line) is None), None)
        if refused is not None:
            parser.error(f"--open: {refused!r} is not the number of a pull request")
        still = {int(line) for line in listed}
        gone = [number for number in held if number not in still]
    for number in gone:
        print(f"{LOGOS_CHAT}/pr-{number}")
    return 0


def _add_ran(command: argparse.ArgumentParser, runs: str, matrix: bool = True) -> None:
    """The arguments that say what a run of simulate.yml ran, and what it came to: with `matrix`,
    the matrix of its runs."""
    if matrix:
        command.add_argument(
            "matrix",
            metavar="MATRIX",
            type=Path,
            help="the matrix tools/sim matrix wrote of the runs",
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
    _check_facts(parser, args)
    try:
        return read_matrix(args.matrix)
    except Unreadable as refused:
        parser.error(f"MATRIX: {refused}")


def _check_facts(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuses what a command was handed of what a run ran unless each is of its form, and its
    runs are a directory."""
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


def _point_at(site: Path, logos_chat: str, measurement: str) -> Path:
    return site / LOGOS_CHAT / "main" / logos_chat / measurement


def _long_at(site: Path, logos_chat: str, measurement: str) -> Path:
    return site / LOGOS_CHAT / "long" / logos_chat / measurement


def _long_runs(runs: Path, name: str, count: int, first: int) -> List[dict]:
    """Runs 1 to `count` of the sweep of `name` under `runs`, at any depth, run 1 of seed `first`:
    each its seed, whether its job could not build chat-sim, and its outcome as `compare --write`
    wrote it in outcomes.json, or `None`. A job of the long suite sweeps its scenario whole from
    run 1, so the outcomes are its runs' in order; one of another seed than its run's is
    refused."""
    swept = next((path for path in sorted(runs.glob(f"**/{name}")) if path.is_dir()), runs / name)
    written = swept / "outcomes.json"
    outcomes = _json(written) if written.is_file() else []
    if not isinstance(outcomes, list):
        raise Unreadable(f"{written}: not a list of the outcomes compare --write writes")
    for number, one in enumerate(outcomes):
        try:
            _outcome(one, f"[{number}]")
        except Unreadable as refused:
            raise Unreadable(f"{written}: {refused}") from None
        if one["seed"] != first + number:
            raise Unreadable(
                f"{written}: [{number}] is of seed {one['seed']}, and run {number + 1} of seed "
                f"{first + number}"
            )
    numbered = {int(path.name): path for path in swept.glob("*") if _RUN.fullmatch(path.name)}
    return [
        {
            "run": number,
            "seed": first + number - 1,
            "unbuilt": number in numbered and (numbered[number] / "unbuilt").is_file(),
            "outcome": outcomes[number - 1] if number <= len(outcomes) else None,
        }
        for number in range(1, count + 1)
    ]


def _long_texts(directory: Path, long: dict) -> List[Path]:
    """Where the text of a long run would be: each scenario's compare output, and each run's."""
    return [
        path
        for one in long["scenarios"]
        for path in (
            directory / one["scenario"] / "compare.txt",
            *(
                directory / one["scenario"] / str(run["run"]) / name
                for run in one["runs"]
                for name in TEXTS
            ),
        )
    ]


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


def _read_all(paths: Sequence[Path], read: Callable[[Path], dict]) -> List[Tuple[Path, dict]]:
    """Each of `paths` as `read` reads it, and its directory, the newest first by run number. One
    `read` refuses is left out, and a warning on standard error says why."""
    found = []
    for path in paths:
        try:
            found.append((path.parent, read(path)))
        except Unreadable as refused:
            print(f"::warning::{refused}", file=sys.stderr)
    return sorted(found, key=_newest, reverse=True)


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
    _group(cell, where)
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


def _held_summary(one: object) -> dict:
    """The shape of a point's summary of a scenario: one from before the summaries held how the
    runs forked has none of it, and one from before they named their group has no group too."""
    if not isinstance(one, dict) or "split_at" in one:
        return _SUMMARY
    return _UNFORKED_SUMMARY if "group" in one else _UNGROUPED_SUMMARY


def _summary(one: object, where: str, shape: dict = _SUMMARY) -> None:
    """Refuses `one` unless it is a scenario's summary of `shape`."""
    _shaped(one, shape, where)
    if _SCENARIO.fullmatch(one["scenario"]) is None:
        raise Unreadable(f"{where}.scenario {one['scenario']!r} is not the name of a scenario")
    _group(one, where)


def _group(held: dict, where: str) -> None:
    if group(held) not in simulate.GROUPS:
        raise Unreadable(f"{where}.group {held['group']!r} is none of a scenario's")


def group(held: dict) -> str:
    """The group of a cell or a scenario's summary of a matrix or a point, or of a group's
    verdict: `v2` where a point names none."""
    return held.get("group", "v2")


def _named(point: dict, where: str) -> None:
    """Refuses a point a matrix names unless its commits and the address of its run are of their
    form."""
    _commits(point, "logos_chat", "measurement", where=f"{where}.")
    if _URL.fullmatch(point["run_url"]) is None:
        raise Unreadable(f"{where}.run_url is not an http(s) address")


def _long_scenario(one: object, where: str) -> None:
    """Refuses a scenario of a long run unless it is one `write_long` writes: its runs from 1,
    each of its seed with its outcome where it has one, and compare's verdict where it ran."""
    _shaped(one, _LONG_SCENARIO, where)
    if _SCENARIO.fullmatch(one["scenario"]) is None:
        raise Unreadable(f"{where}.scenario {one['scenario']!r} is not the name of a scenario")
    if one["compare"] is not None:
        _shaped(one["compare"], _COMPARE, f"{where}.compare")
        if one["compare"]["verdict"] not in _CLASS:
            raise Unreadable(f"{where}.compare.verdict is none of compare's")
    if not one["runs"]:
        raise Unreadable(f"{where}.runs is empty")
    for number, run in enumerate(one["runs"]):
        _shaped(run, _LONG_RUN, f"{where}.runs[{number}]")
        if run["run"] != number + 1:
            raise Unreadable(f"{where}.runs[{number}].run is not run {number + 1}")
        if run["outcome"] is not None:
            _outcome(run["outcome"], f"{where}.runs[{number}].outcome")
            if run["outcome"]["seed"] != run["seed"]:
                raise Unreadable(f"{where}.runs[{number}].outcome is not of the run's seed")


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
    """Refuses a point, a long run or a pull request's meta.json unless the address of its run
    and its time are of their form."""
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


def _apart(found: Sequence[dict]) -> List[Tuple[str, List[dict]]]:
    """Each group the points summarise, in the order of `simulate.GROUPS`: what the page calls
    it, and each point with the cells and the summary of that group alone, none where the point
    has no run of it."""
    held = {group(one) for point in found for one in point["summary"]}
    return [
        (
            called,
            [
                dict(
                    point,
                    cells=[cell for cell in point["cells"] if group(cell) == key],
                    summary=[one for one in point["summary"] if group(one) == key],
                )
                for point in found
            ],
        )
        for key, called in simulate.GROUPS.items()
        if key in held
    ]


def _rates(found: Sequence[dict], called: str) -> List[Tuple[object, str]]:
    """Each rate the newest of the points of the group `called` with a run of it summarises, the
    rates main runs now, as `_rate` keys it, in the order of its loss, and the name the page gives
    it; refused when there are more than `HUES`. A rate only older points ran is not drawn."""
    newest = next((point for point in reversed(found) if point["summary"]), {"summary": []})
    seen: Dict[object, dict] = {}
    for one in newest["summary"]:
        seen.setdefault(_rate(one), one)
    ordered = sorted(
        seen.values(), key=lambda one: (one["loss"] is None, one["loss"] or 0, one["scenario"])
    )
    if len(ordered) > HUES:
        raise Unreadable(
            f"the newest point holds {len(ordered)} rates of {called}, and the page tells {HUES} "
            "apart"
        )
    return [(_rate(one), one["rate"] or one["scenario"]) for one in ordered]


def _rate(held: dict) -> object:
    """The rate a summary or a cell is of, within its group: its loss, so that a scenario renamed
    at the same loss goes on as the same line, or its scenario where it has none."""
    return group(held), (held["scenario"] if held["loss"] is None else held["loss"])


def _epochs_chart(
    found: Sequence[dict], groups: Sequence[Tuple[str, List[dict], List[Tuple[object, str]]]]
) -> str:
    """The first chart: of each group of `groups`, as `write_trend` has them, a panel a rate, the
    median of the epochs without a fork as a line and the band from the lowest to the highest."""
    return (
        '<section class="chart" id="epochs">\n<h2>Epochs without a fork</h2>\n'
        + _legend(
            (_line_key(2), "the median of the runs"),
            (_band_key(), "the lowest run to the highest"),
            *_marks(found),
        )
        + "".join(
            f'<h3>{_e(called)}</h3>\n<div class="panels">\n'
            + "".join(_panel(held, hue, *rate, called) for hue, rate in enumerate(rates, 1))
            + "</div>\n"
            for called, held, rates in groups
        )
        + '<p class="caption">How many epochs each run went without a fork: the first split\'s, '
        "else the trunk's. It moves with every change for the better, where the runs whole below "
        "can show none yet. A dashed line marks a point that measures another chat-sim commit "
        "than the point before it, so that the step is chat-sim's, not logos-chat's; an open "
        "marker a point re-rolled, whose runs drew other ids than those of the point it follows, "
        "so that every seed's run is another run, and moved whether or not logos-chat did.</p>\n"
        "</section>\n"
    )


def _whole_chart(groups: Sequence[Tuple[str, List[dict], List[Tuple[object, str]]]]) -> str:
    """The second chart: of each group of `groups`, as `write_trend` has them, a line a rate of
    how many runs ended whole."""
    drawn = ""
    for called, held, rates in groups:
        runs = max((one["runs"] for point in held for one in point["summary"]), default=0)
        drawn += (
            f"<h3>{_e(called)} runs whole{_of_runs(held)}</h3>\n"
            + _legend(
                *((_line_key(hue), name) for hue, (_, name) in enumerate(rates, 1)),
                (_goal_key(), "the goal"),
                *_marks(held),
            )
            + _over(
                held,
                rates,
                _whole,
                _whole_said,
                top=runs,
                plot=240,
                label=f"{called} runs whole at {_TOP} epochs",
                goal=runs,
                merged=True,
                noted=True,
            )
        )
    return (
        '<section class="chart" id="whole">\n'
        f"<h2>Runs whole at {_TOP} epochs</h2>\n"
        + drawn
        + '<p class="caption">How many runs of each rate ended whole: one group, every member '
        f"that joined at its tip, at epoch {_TOP}. The goal is every line at the top. A line "
        "breaks at a point where none of the rate's runs had an outcome, as when chat-sim did not "
        "build. Under the dates the axis shows, the pull requests of logos-chat the point merged, "
        "which a marker's hover text names too.</p>\n"
        "</section>\n"
    )


def _newest_chart(
    point: dict, groups: Sequence[Tuple[str, List[dict], List[Tuple[object, str]]]]
) -> str:
    """The third chart: the cells of the newest point, a heatmap a group of `groups`, as
    `write_trend` has them, a row a rate and a column a seed."""
    drawn = ""
    for called, _, rates in groups:
        rows = []
        for rate, name in rates:
            cells = {cell["seed"]: cell for cell in point["cells"] if _rate(cell) == rate}
            if cells:
                rows.append((name, cells))
        if rows:
            label = f"The newest point's {called} runs"
            drawn += f"<h3>{_e(called)}</h3>\n" + _heatmap(rows, label)
    when = _when(point)
    return (
        '<section class="chart" id="newest">\n<h2>The newest point</h2>\n'
        f'<p class="caption">{when} UTC, main {_e(point["logos_chat"][:7])}: a row a group\'s rate '
        "and a column a seed.</p>\n"
        + drawn
        + _heat_legend()
        + '<p class="caption">A cell\'s colour is the epochs its run went without a fork; a dot '
        "marks a run that ended whole, a ring one that ended one group with members behind, and "
        "no mark one that split or stalled. Its hover text is how the run came out.</p>\n"
        "</section>\n"
    )


def _off_chart(groups: Sequence[Tuple[str, List[dict], List[Tuple[object, str]]]]) -> str:
    """The fourth chart: of each group of `groups`, as `write_trend` has them, a line a rate of
    the median of the members off the trunk at the end."""
    drawn = ""
    for called, held, rates in groups:
        medians = [
            one["members_off_trunk"]["median"]
            for point in held
            for one in point["summary"]
            if one["members_off_trunk"]["median"] is not None
        ]
        drawn += (
            f"<h3>{_e(called)}</h3>\n"
            + _legend(
                *((_line_key(hue), name) for hue, (_, name) in enumerate(rates, 1)), *_marks(held)
            )
            + _over(
                held,
                rates,
                lambda one: one["members_off_trunk"]["median"],
                lambda one: f"a median of {_number(one['members_off_trunk']['median'])} members "
                "off the trunk at the end",
                top=max(medians, default=0),
                plot=150,
                label=f"{called} members off the trunk at the end",
            )
        )
    return (
        '<section class="chart" id="off">\n<h2>Members off the trunk at the end</h2>\n'
        + drawn
        + '<p class="caption">Behind at the end, on a line not the trunk, or never joined: the '
        "median of each rate's runs. The other half of the goal, drawn of a group once a rate of "
        "it has every run whole.</p>\n"
        "</section>\n"
    )


def _points_list(
    found: Sequence[dict], groups: Sequence[Tuple[str, List[dict], List[Tuple[object, str]]]]
) -> str:
    """The list of the points, the newest first, with the runs whole of each group of `groups`, as
    `write_trend` has them."""
    rates = [(rate, f"{called} {name}") for called, _, its in groups for rate, name in its]
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
            f"<td>{_link(f'main/{logos_chat}/{measured}/', 'point')} · "
            f"{_link(point['run_url'], 'the run')}</td>"
            "</tr>\n"
        )
    wholes = "".join(
        f'<th colspan="{len(its)}">{_e(called)} runs whole{_of_runs(held)}</th>'
        for called, held, its in groups
    )
    heads = "".join(f"<th>{_e(name)}</th>" for _, _, its in groups for _, name in its)
    return (
        '<section id="points">\n<h2>The points</h2>\n'
        '<div class="wide"><table class="points">\n'
        "<thead><tr><th>When (UTC)</th><th>main</th><th>Pull requests merged</th>"
        f"{wholes}<th>What moved</th>"
        "<th>chat-sim</th><th></th></tr>\n"
        f"<tr><th></th><th></th><th></th>{heads}<th></th><th></th><th></th></tr></thead>\n"
        f"<tbody>\n{''.join(rows)}</tbody>\n"
        "</table></div>\n"
        '<p class="caption">What moved is against the point before it: the runs whole and median '
        "of epochs without a fork of each group's rate where they changed, or that none of its "
        "runs had an outcome. chat-sim is the commit the point measures.</p>\n"
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


def _panel(found: Sequence[dict], hue: int, rate: object, name: str, called: str) -> str:
    """A panel of the first chart: of one rate of the group `called`, the median of the epochs
    without a fork as a line, and the band from the lowest to the highest."""
    width, plot, left, right, above, below = 196, 120, 28, 12, 10, 22
    xs = _xs(len(found), left, width - left - right)
    reach = _reach(xs)
    held = [_rate_at(point, rate) for point in found]
    epochs = [None if one is None else one["epochs"] for one in held]
    top = max([_TOP] + [one["highest"] for one in epochs if one and one["highest"] is not None])
    bottom = above + plot

    def y(epoch: float) -> float:
        return bottom - epoch / top * plot

    drawn = [_svg(width, bottom + below, f"Epochs without a fork at {called} {name}")]
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


def _cell_drawn(
    cell: dict, name: str, x: float, y: float, side: str = "head", against: bool = False
) -> str:
    """A cell at `side`: its epochs without a fork in a bin's colour, a dot for a run that ended
    whole, a ring for one that ended behind, and how it came out as its title; with `against`, an
    outline on a run of the head better or worse than the base's."""
    outcome, epochs, ended = cell[side], cell["epochs"][side], cell["ended"][side]
    said = f"{name}, seed {cell['seed']}"
    if outcome is None:
        why = "chat-sim did not build" if cell["unbuilt"][side] else "no outcome"
        kind, shown, said = "empty", "-", f"{said} · {why}"
    else:
        kind = "empty" if epochs is None else f"bin{min(epochs // 10, len(_BINS) - 1)}"
        shown = "-" if epochs is None else str(epochs)
        if epochs is not None:
            said += f" · {epochs} epochs without a fork"
        said += f" · {outcome['came_to']}"
    mark = {"whole": "whole", "behind": "behind"}.get(ended or "")
    centre = x + (15 if mark else 18)
    moved = against and side == "head" and cell["change"] in ("better", "worse")
    if moved:
        kind, said = f"{kind} {cell['change']}", f"{said} · {cell['change']} than the base"
    drawn = (
        f'<g class="cell {kind}"><title>{_e(said)}</title>'
        f'<rect x="{x + 1:.1f}" y="{y + 1}" width="36" height="26" rx="4"/>'
        f'<text x="{centre:.1f}" y="{y + 18}" text-anchor="middle">{shown}</text>'
    )
    if moved:
        drawn += (
            f'<rect class="outline" x="{x + 1:.1f}" y="{y + 1}" width="36" height="26" rx="4"/>'
        )
    if mark == "whole":
        drawn += f'<circle class="whole" cx="{x + 29:.1f}" cy="{y + 14}" r="3"/>'
    if mark == "behind":
        drawn += f'<circle class="behind" cx="{x + 29:.1f}" cy="{y + 14}" r="2.5"/>'
    return drawn + "</g>"


def _groups(cells: Sequence[dict]) -> List[Tuple[str, List[dict]]]:
    """Each group of `cells`, in the order of `simulate.GROUPS`: what the page calls it, and its
    cells, in their order."""
    return [
        (called, [cell for cell in cells if group(cell) == key])
        for key, called in simulate.GROUPS.items()
        if any(group(cell) == key for cell in cells)
    ]


def _by_rate(cells: Sequence[dict]) -> List[Tuple[str, Dict[int, dict]]]:
    """The cells a row a group's rate, as `_rate` keys it, in their order, each row the name of
    its rate and its cells by seed."""
    rows: Dict[object, Tuple[str, Dict[int, dict]]] = {}
    for cell in cells:
        rows.setdefault(_rate(cell), (cell["rate"] or cell["scenario"], {}))[1][cell["seed"]] = cell
    return list(rows.values())


def _heatmap(
    rows: Sequence[Tuple[str, Dict[int, dict]]],
    label: str,
    side: str = "head",
    against: bool = False,
) -> str:
    """The cells of `rows` at `side` as a heatmap, a row a rate and a column a seed; with
    `against`, a run of the head that is better or worse than the base's outlined."""
    seeds = sorted({seed for _, cells in rows for seed in cells})
    left, size = 56, 38
    width, height = left + size * len(seeds), 18 + 30 * len(rows)
    drawn = [_svg(width, height, label)]
    drawn.append('<text class="axis small" x="0" y="11">seed</text>')
    for column, seed in enumerate(seeds):
        x = left + size * column + size / 2
        drawn.append(
            f'<text class="axis small" x="{x:.1f}" y="11" text-anchor="middle">{seed}</text>'
        )
    for row, (name, cells) in enumerate(rows):
        y = 18 + 30 * row
        drawn.append(f'<text class="label" x="0" y="{y + 18}">{_e(name)}</text>')
        for column, seed in enumerate(seeds):
            if seed in cells:
                drawn.append(
                    _cell_drawn(cells[seed], name, left + size * column, y, side, against)
                )
    drawn.append("</svg>")
    return "\n".join(drawn) + "\n"


def _heat_legend(*extra: Tuple[str, str]) -> str:
    """The legend of a heatmap: the bins of the epochs without a fork, the marks, and `extra`."""
    return _legend(
        *((_cell_key(f"bin{number}"), name) for number, name in enumerate(_BINS)),
        (_cell_key("bin5", "whole"), "whole"),
        (_cell_key("bin5", "behind"), "one group, members behind"),
        (_cell_key("empty"), "no outcome"),
        *extra,
    )


def _spread(spread: dict, rows: Sequence[Tuple[str, Dict[int, dict]]]) -> str:
    """The column beside the heatmaps of main's runs in `spread`, a row a rate of `rows`, as the
    comment's column has them."""
    listed = "".join(
        f"<tr><th>{_e(name)}</th>"
        f"<td>{_e(simulate.spread_of(spread, next(iter(cells.values()))['scenario']))}</td></tr>\n"
        for name, cells in rows
    )
    return (
        f'<div>\n<p class="side">main\'s {_e(simulate.spread_seeds(spread))} at '
        f"{_code(spread['logos_chat'])}</p>\n"
        f'<table class="spread"><tbody>\n{listed}</tbody></table>\n</div>\n'
    )


def _strips(
    spread: Optional[dict], rows: Sequence[Tuple[str, Dict[int, dict]]], against: bool, called: str
) -> str:
    """A strip a rate placing the head's runs of `rows`, of the group `called`, among the runs of
    main's point that `spread` holds, by the epochs without a fork; nothing without a spread, and
    a line that says so where it holds no run of the group."""
    if spread is None:
        return ""
    scenarios = {cell["scenario"] for _, cells in rows for cell in cells.values()}
    if not any(one["scenario"] in scenarios for one in spread["seeds"]):
        return (
            f'<p class="side">Main\'s point at {_code(spread["logos_chat"])} holds no '
            f"{_e(called)} runs to place the head's among.</p>\n"
        )
    drawn = [
        f'<p class="side">Where the head\'s {_e(called)} runs fall among main\'s '
        f"{_e(simulate.spread_seeds(spread))} at "
        f"{_code(spread['logos_chat'])}, by the epochs without a fork: main's runs small, stacked "
        "where they meet, and the head's large.</p>\n"
    ]
    for name, cells in rows:
        scenario = next(iter(cells.values()))["scenario"]
        mains = [
            one
            for one in spread["seeds"]
            if one["scenario"] == scenario and one["epochs"] is not None
        ]
        heads = [cells[seed] for seed in sorted(cells) if cells[seed]["epochs"]["head"] is not None]
        drawn.append(_strip(name, mains, heads, against, called))
    return "".join(drawn)


def _strip(
    name: str, mains: Sequence[dict], heads: Sequence[dict], against: bool, called: str
) -> str:
    """The strip of one rate of the group `called`: an axis of the epochs without a fork, main's
    runs on it as small marks and the head's as large ones, each with how it came out as its
    hover text."""
    left, plot, gap = 56, 440, 7
    top = max(
        [_TOP]
        + [one["epochs"] for one in mains]
        + [cell["epochs"]["head"] for cell in heads]
    )
    stacked: Dict[int, int] = {}
    for one in mains:
        stacked[one["epochs"]] = stacked.get(one["epochs"], 0) + 1
    axis = 12 + gap * (max(stacked.values(), default=1) - 1)

    def x(epochs: int) -> float:
        return left + epochs / top * plot

    drawn = [_svg(left + plot + 12, axis + 22, f"The head's runs at {called} {name} among main's")]
    drawn.append(f'<text class="label" x="0" y="{axis + 4}">{_e(name)}</text>')
    drawn.append(f'<line class="tick" x1="{left}" x2="{left + plot}" y1="{axis}" y2="{axis}"/>')
    for tick in range(0, top + 1, 10):
        drawn.append(
            f'<line class="tick" x1="{x(tick):.1f}" x2="{x(tick):.1f}" y1="{axis + 3}" '
            f'y2="{axis + 7}"/>'
        )
        drawn.append(
            f'<text class="axis small" x="{x(tick):.1f}" y="{axis + 18}" '
            f'text-anchor="middle">{tick}</text>'
        )
    seen: Dict[int, int] = {}
    for one in mains:
        seen[one["epochs"]] = seen.get(one["epochs"], 0) + 1
        said = f"main, seed {one['seed']} · {one['epochs']} epochs without a fork"
        if one["ended"] is not None:
            said += f", {one['ended']}"
        drawn.append(
            f"<g><title>{_e(said)}</title>"
            f'<circle class="seed" cx="{x(one["epochs"]):.1f}" '
            f'cy="{axis - gap * (seen[one["epochs"]] - 1)}" r="3.5"/></g>'
        )
    for cell in heads:
        said = f"the head, seed {cell['seed']} · {simulate.words(cell, against)}"
        drawn.append(
            f"<g><title>{_e(said)}</title>"
            f'<circle class="head" cx="{x(cell["epochs"]["head"]):.1f}" cy="{axis}" r="6"/></g>'
        )
    drawn.append("</svg>")
    return "\n".join(drawn) + "\n"


def _side(
    cell: dict, side: str, named: Optional[str], run: Path, href: str, inline: bool = True
) -> str:
    """A side's run of a cell, its text kept in `run` and linked at `href`: what it came to, its
    lines and who was off the trunk as its run.json has them, and its text, linked and, with
    `inline`, shown; or why it has no outcome, with what text it left or the build's log of a job
    that could not build chat-sim."""
    drawn = "<div>\n" + ("" if named is None else f'<p class="side">{named}</p>\n')
    outcome = cell[side]
    if outcome is None and not cell["unbuilt"][side]:
        drawn += "<p>no outcome</p>\n" + _files(run, href)
        if inline:
            drawn += "".join(_folded(run, name) for name in ("run.txt", "report.txt"))
        return drawn + "</div>\n"
    if outcome is None:
        shown = _shown(run, href, "build.log") if inline else _files(run, href)
        return drawn + "<p>does not build</p>\n" + shown + "</div>\n"
    drawn += f"<p>{_e(outcome['verdict'] or 'no verdict')} · {_e(outcome['came_to'])}</p>\n"
    if inline:
        drawn += _grown(_run_json(run / "run.json"))
    drawn += _files(run, href)
    if inline:
        drawn += "".join(_folded(run, name) for name in ("run.txt", "report.txt"))
    return drawn + "</div>\n"


def _grown(report: Optional[dict]) -> str:
    """The lines of a run's grow as a table, and who fell behind, who was behind at the end and
    who never joined, as `report`, its run.json, has them; nothing where it has no lines."""
    grow = report.get("grow") if report is not None else None
    lines = grow.get("lines") if isinstance(grow, dict) else None
    if not isinstance(lines, list) or not all(isinstance(line, dict) for line in lines):
        return ""
    rows = "".join(
        f'<tr><td class="n">{number}</td><td>{_e(_word(line.get("kind")))}</td>'
        + "".join(
            f'<td class="n">{_count(line.get(field))}</td>'
            for field in ("from", "forked_at", "epoch")
        )
        + f'<td class="n">{_count(_size(line.get("members")))}</td></tr>\n'
        for number, line in enumerate(lines)
    )
    drawn = (
        '<div class="wide"><table class="lines">\n'
        '<thead><tr><th class="n">line</th><th>kind</th><th class="n">from</th>'
        '<th class="n">forked at</th><th class="n">epoch</th><th class="n">members</th></tr>'
        f"</thead>\n<tbody>\n{rows}</tbody>\n</table></div>\n"
    )
    fell = [
        f"{one['name']} at epoch {_count(one.get('epoch'))}"
        for one in _listed(grow.get("fell_behind"))
        if isinstance(one, dict) and isinstance(one.get("name"), str)
    ]
    for said, names in (
        ("fell behind", fell),
        ("behind at the end", _names(grow.get("behind"))),
        ("never joined", _names(grow.get("never_joined"))),
    ):
        if names:
            drawn += f'<p class="who">{said}: {_e(", ".join(names))}</p>\n'
    return drawn


def _rates_table(directory: Path, point: dict) -> str:
    """How the runs of each group's rate of a point ended, their epochs without a fork, their
    members off the trunk, and their wall time while their run.json is kept."""
    rows = []
    for one in point["summary"]:
        walls = [
            _wall(_run_json(_run_dir(directory, cell) / "run.json"))
            for cell in point["cells"]
            if cell["scenario"] == one["scenario"]
        ]
        timed = [wall for wall in walls if wall is not None]
        took = "-"
        if timed:
            low, high = _seconds(min(timed)), _seconds(max(timed))
            took = low if low == high else f"{low} to {high}"
        epochs = one["epochs"]
        counted = [one["runs"], *(one[ended] for ended in _ENDINGS), one["no-outcome"]]
        counted += [epochs["lowest"], epochs["median"], epochs["highest"]]
        counted.append(one["members_off_trunk"]["median"])
        rows.append(
            f"<tr><td>{_e(simulate.GROUPS[group(one)])}</td>"
            f"<td>{_e(one['rate'] or one['scenario'])}</td>"
            + "".join(f'<td class="n">{_number(value)}</td>' for value in counted)
            + f"<td>{took}</td></tr>\n"
        )
    return (
        '<section id="rates">\n<h2>The rates</h2>\n'
        '<div class="wide"><table class="rates">\n<thead><tr><th rowspan="2">Group</th>'
        '<th rowspan="2">Loss</th>'
        + "".join(
            f'<th rowspan="2" class="n">{said}</th>'
            for said in ("Runs", "Whole", "Behind", "Split", "Stalled", "No outcome")
        )
        + '<th colspan="3">Epochs without a fork</th>'
        '<th rowspan="2" class="n">Off the trunk at the end, median</th>'
        "<th rowspan=\"2\">A run's wall time</th></tr>\n"
        '<tr><th class="n">lowest</th><th class="n">median</th><th class="n">highest</th></tr>'
        f"</thead>\n<tbody>\n{''.join(rows)}</tbody>\n</table></div>\n"
        '<p class="caption">Whole: one group, every member that joined at its tip, at epoch 50; '
        "behind: one group, some members behind; stalled: one group that stopped growing short of "
        "50; split: it forked. Off the trunk: behind at the end, on a line not the trunk, or never "
        "joined. A run's wall time is the runner's, while its run.json is kept.</p>\n"
        "</section>\n"
    )


def _runs_table(directory: Path, cells: Sequence[dict]) -> str:
    """A row for each run of a rate of a point: its seed, its epochs without a fork, how it
    ended, what its grow came to, and its wall time while its run.json is kept."""
    rows = []
    for cell in cells:
        outcome = cell["head"]
        ended = cell["ended"]["head"] or ("-" if outcome else simulate.words(cell, False))
        wall = _wall(_run_json(_run_dir(directory, cell) / "run.json"))
        rows.append(
            f'<tr><td class="n">{cell["seed"]}</td>'
            f'<td class="n">{_number(cell["epochs"]["head"])}</td><td>{_e(ended)}</td>'
            f"<td>{_e('-' if outcome is None else outcome['came_to'])}</td>"
            f'<td class="n">{"-" if wall is None else _seconds(wall)}</td></tr>\n'
        )
    return (
        '<div class="wide"><table class="runs">\n'
        '<thead><tr><th class="n">seed</th><th class="n">epochs without a fork</th>'
        "<th>how it ended</th><th>what the grow came to</th><th class=\"n\">wall time</th></tr>"
        f"</thead>\n<tbody>\n{''.join(rows)}</tbody>\n</table></div>\n"
    )


def _build(job: str, run: Path, href: str, kept: bool = True) -> str:
    """The section of the build's log of a job that could not build chat-sim, the job that was
    to run what `job` says, its log kept in `run` and linked at `href`; without `kept`, of a run
    whose text the site no longer keeps, it says so in the log's place."""
    log = '<p class="muted">The site no longer keeps its log.</p>\n'
    if kept:
        log = _shown(run, href, "build.log")
    return (
        '<section id="build">\n<h2>The build\'s log</h2>\n'
        f"<p>Of the job that was to run {_e(job)}: no scenario ran.</p>\n" + log + "</section>\n"
    )


def _job(cell: dict) -> str:
    """What the job of `cell`'s run was to run: its group and its rate, or its scenario, and its
    seed."""
    return f"{simulate.GROUPS[group(cell)]} {cell['rate'] or cell['scenario']}, seed {cell['seed']}"


def _long_runs_table(directory: Path, one: dict) -> str:
    """A row for each run of a scenario of a long run: its seed, how it ended, what its grow
    came to, and its wall time while its run.json is kept."""
    rows = []
    for run in one["runs"]:
        got = run["outcome"]
        wall = _wall(_run_json(directory / one["scenario"] / str(run["run"]) / "run.json"))
        rows.append(
            f'<tr><td class="n">{_number(run["seed"])}</td>'
            f"<td>{_e(_long_ended(run))}</td>"
            f"<td>{_e('-' if got is None else got['came_to'])}</td>"
            f'<td class="n">{"-" if wall is None else _seconds(wall)}</td></tr>\n'
        )
    return (
        '<div class="wide"><table class="runs">\n'
        '<thead><tr><th class="n">seed</th><th>how it ended</th><th>what the grow came to</th>'
        '<th class="n">wall time</th></tr>'
        f"</thead>\n<tbody>\n{''.join(rows)}</tbody>\n</table></div>\n"
    )


def _long_ended(run: dict) -> str:
    """How a run of a long run ended: its verdict, or why it has no outcome."""
    if run["outcome"] is not None:
        return run["outcome"]["verdict"] or "no verdict"
    return "does not build" if run["unbuilt"] else "no outcome"


def _long_came_to(one: dict) -> str:
    """What the runs of a scenario of a long run came to, as the comment says it, or that chat-sim
    does not build where none has an outcome and a job could not build it."""
    now = _long_outcomes(one)
    if now is None and any(run["unbuilt"] for run in one["runs"]):
        return "does not build"
    return simulate.came_to(now)


def _long_against(one: dict) -> Tuple[str, str]:
    """What compare said of the runs of a scenario of a long run, as the comment says it, and its
    class."""
    said = one["compare"]
    counted = None if said is None else said["counted"]
    kind = "muted" if said is None else _CLASS[said["verdict"]]
    return kind, simulate.compare_count(_long_outcomes(one), counted, "nothing held")


def _long_outcomes(one: dict) -> Optional[List[dict]]:
    """The outcomes of the runs of a scenario of a long run, in order, or `None` where none has
    one."""
    return [run["outcome"] for run in one["runs"] if run["outcome"] is not None] or None


def _first_seed(path: Path) -> int:
    """The seed of run 1 of a sweep of the scenario in `path`: its `seed`, 0 where it has none.
    Run k of a sweep has that seed plus k - 1."""
    scenario = _json(path)
    seed = scenario.get("seed", 0) if isinstance(scenario, dict) else None
    if type(seed) is not int or seed < 0:
        raise Unreadable(f"{path}: its seed is not a whole number from 0")
    return seed


def _again(cell: dict, facts: dict) -> str:
    """The command that runs a cell again, as the comment's fold has it."""
    return (
        '<p class="side">Run it again:</p>\n'
        f"<pre>{_e(chr(10).join(simulate.again(cell, facts)))}</pre>\n"
    )


def _files(run: Path, href: str) -> str:
    """A link to each of a run's text files `run` keeps, or nothing where it keeps none."""
    kept = [name for name in TEXTS if (run / name).is_file()]
    if not kept:
        return ""
    return '<p class="file">' + " · ".join(_link(href + name, name) for name in kept) + "</p>\n"


def _shown(run: Path, href: str, name: str) -> str:
    """A text file of a run as the page shows it: a link to it, and its text."""
    if not (run / name).is_file():
        return f'<p class="file">{_e(name)} <span class="muted">was not uploaded</span></p>\n'
    text = _e((run / name).read_text(encoding="utf-8", errors="replace"))
    return f'<p class="file">{_link(href + name, name)}</p>\n<pre>{text}</pre>\n'


def _folded(run: Path, name: str) -> str:
    """A text file of a run in a fold, or nothing where `run` does not keep it."""
    if not (run / name).is_file():
        return ""
    text = _e((run / name).read_text(encoding="utf-8", errors="replace"))
    return f'<details class="text">\n<summary>{_e(name)}</summary>\n<pre>{text}</pre>\n</details>\n'


def _run_json(path: Path) -> Optional[dict]:
    """A run's run.json, or `None` where it is not kept or is not an object."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return report if isinstance(report, dict) else None


def _wall(report: Optional[dict]) -> Optional[float]:
    """The seconds of wall time a run took, as its run.json says, or `None`."""
    wall = None if report is None else report.get("wall_s")
    return wall if isinstance(wall, (int, float)) and not isinstance(wall, bool) else None


def _run_dir(directory: Path, cell: dict) -> Path:
    return directory / cell["scenario"] / str(cell["run"])


def _run_href(cell: dict) -> str:
    return f"{cell['scenario']}/{cell['run']}/"


def _listed(value: object) -> list:
    return value if isinstance(value, list) else []


def _names(value: object) -> List[str]:
    return [one for one in _listed(value) if isinstance(one, str)]


def _word(value: object) -> str:
    return value if isinstance(value, str) else "-"


def _count(value: object) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else "-"


def _size(value: object) -> Optional[int]:
    return len(value) if isinstance(value, list) else None


def _seconds(value: float) -> str:
    """A wall time as the page shows it: 0.4 s, 45 s, or 3 min 9 s."""
    if value < 1:
        return f"{value:.1f} s"
    if round(value) < 60:
        return f"{round(value)} s"
    minutes, seconds = divmod(round(value), 60)
    return f"{minutes} min {seconds} s" if seconds else f"{minutes} min"


def _code(sha: str) -> str:
    return f"<code>{_e(sha[:7])}</code>"


def _commit(repository: str, sha: str) -> str:
    """A link to a commit of `repository`, by its short sha."""
    return _link(f"{repository}/commit/{sha}", sha[:7], code=True)


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
    """What a point changed against the point before it: of each group's rate the runs whole and
    the median of the epochs without a fork, where either moved, or why none of its runs had an
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
    if mark == "outline":
        drawn += '<rect class="outline" x="1" y="1" width="20" height="14" rx="3"/>'
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
