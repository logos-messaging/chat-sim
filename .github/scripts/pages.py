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
request in `logos-chat/pr-N/` the same way: its matrix, both sides' runs' text, its `meta.json`,
the scenario files it ran, its report page and a page for each run. `long` keeps a run of the long
suite at a commit of main, held to what is expected, in `logos-chat/long/<sha>/<measurement>/` the
same way: its `long.json`, each scenario's compare output, its runs' text and its page; the newest
fifteen long runs keep their text. `trend` draws the graph over main, `logos-chat/index.html`, from
the `point.json` of every point, and links the newest long run; one it cannot read is left out,
with a warning. `closed` names the runs of logos-chat's pull requests that are no longer open.

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
# Where a pull request's run keeps a copy of each scenario file it ran, which its page describes
# and links.
SCENARIOS = "scenarios"
# The member that creates a run's group: tools/sim's first peer.
_CREATOR = "saro"
# What the scenarios of a pull request's run test, each a grow; and how a column of its trees runs.
_TESTS = "How many epochs a growing group of logos-chat clients lasts before it first forks."
_SEEDS = (
    "A column is a seed, the same at the head and at the base. At one revision a seed runs the "
    "same every time, so where a column's two trees differ, the pull request changed the run. "
    "The change may be only a re-deal of the losses: one frame more, anywhere, shifts which "
    "frames each member loses after it."
)
# What running a run again takes: on Linux alone a run is the same every time from its seed, and
# CI runs on x86-64.
_NEEDS = "Linux on x86-64, Nix with flakes, jq and python3"
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
_CHANGES = ("same", "moved")
_RESULTS = ("unbuilt", "no-outcome", "earlier", "later", "within", "no-base", "same")
# The mark of a result that has a direction, beside its word.
_ARROWS = {"earlier": "▼ ", "later": "▲ "}
# The chip of a group's runs missing at a side, which has the colour of a run that did not build.
_MISSING = {"no-outcome": "unbuilt", "no-base": "unbuilt"}
_SIDE = {"runs": (int,), "unbuilt": (int,)}
_COUNTS = {field: (int,) for field in ("cells", "compared", *_CHANGES, "no-outcome")}
_MATRIX = {
    "head": _SIDE,
    "base": (dict, _NONE),
    "cells": (list,),
    "counts": _COUNTS,
    "verdict": (str, _NONE),
    "groups": (list,),
    "summary": (list, _NONE),
}
# The counts and the verdict of a group's cells, and the result of each of its scenarios.
_GROUP = {"group": (str,), "counts": _COUNTS, "verdict": (str, _NONE), "results": (list,)}
_RESULT = {
    "scenario": (str,),
    "rate": (str, _NONE),
    "seeds": (list,),
    "later": (int, _NONE),
    "earlier": (int, _NONE),
    "zero": (int, _NONE),
    "p": (float, _NONE),
    "result": (str, _NONE),
}
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
    "d": (int, _NONE),
}
# A point's cell need not hold `d`: the cells that do not, of earlier measurements, hold `moved`
# in its place, `null` in a point. Nor need it name the group of a cell or a scenario: the points
# that do not ran GroupV2's alone.
_MOVED_CELL = {**{field: kind for field, kind in _CELL.items() if field != "d"}, "moved": (_NONE,)}
_UNGROUPED_CELL = {field: kind for field, kind in _MOVED_CELL.items() if field != "group"}
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
    "first_fork": (int, _NONE),
    "ended": (str, _NONE),
    "lines_off_trunk": (int, _NONE),
    "members_off_trunk": (int, _NONE),
    "never_joined": (int, _NONE),
    "came_to": (str,),
    "stopped": (str, _NONE),
}
# An outcome a point or a long run holds need not hold the epoch of its first fork, nor count the
# members that never joined: the outcomes that do not are of earlier measurements.
_UNFORKED_OUTCOME = {field: kind for field, kind in _OUTCOME.items() if field != "first_fork"}
_UNJOINED_OUTCOME = {
    field: kind for field, kind in _UNFORKED_OUTCOME.items() if field != "never_joined"
}
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
    "never_joined": {"median": (int, float, _NONE)},
    "seated": {"median": (int, float, _NONE)},
}
# A point need not count the members that never joined, nor hold how its runs forked: the points
# that do not are of earlier measurements.
_UNJOINED_SUMMARY = {field: kind for field, kind in _SUMMARY.items() if field != "never_joined"}
_FORKS = ("split_at", "lines_off_trunk", "seated")
_UNFORKED_SUMMARY = {
    field: kind for field, kind in _UNJOINED_SUMMARY.items() if field not in _FORKS
}
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

# The colours of the pages, each its property's name and its value in the light theme and in the
# dark. The rates are one blue ramp, the heavier the loss the darker, and so are the six bins of a
# cell's epochs without a fork; each bin's ink is the light or the dark one.
_COLOURS = {
    "fg": ("#1f2328", "#f0f6fc"),
    "muted": ("#59636e", "#9198a1"),
    "bg": ("#fff", "#0d1117"),
    "box": ("#f6f8fa", "#151b23"),
    "line": ("#d1d9e0", "#3d444d"),
    "link": ("#0969da", "#4493f8"),
    "good": ("#1a7f37", "#3fb950"),
    "bad": ("#cf222e", "#f85149"),
    "none": ("#9a6700", "#d29922"),
    "good-bg": ("#dafbe1", "#12261e"),
    "bad-bg": ("#ffebe9", "#2d1416"),
    "none-bg": ("#fff8c5", "#2a2111"),
    "neutral-bg": ("#eaeef2", "#1c2128"),
    "grid": ("#e6e9ed", "#21262d"),
    "band": ("rgba(57,135,229,.18)", "rgba(109,167,236,.22)"),
    "rate-1": ("#86b6ef", "#b7d3f6"),
    "rate-2": ("#3987e5", "#6da7ec"),
    "rate-3": ("#1c5cab", "#2a78d6"),
    "rate-4": ("#0d366b", "#184f95"),
    "w0": ("#cde2fb", "#184f95"),
    "w1": ("#9ec5f4", "#256abf"),
    "w2": ("#6da7ec", "#3987e5"),
    "w3": ("#3987e5", "#6da7ec"),
    "w4": ("#1c5cab", "#9ec5f4"),
    "w5": ("#0d366b", "#cde2fb"),
    "w-ink-light": ("#1f2328", "#fff"),
    "w-ink-dark": ("#fff", "#0d1117"),
}
_STYLE = (
    "\n:root{color-scheme:light dark;"
    + ";".join(f"--{name}:{light}" for name, (light, _) in _COLOURS.items())
    + "}\n@media (prefers-color-scheme:dark){:root{"
    + ";".join(f"--{name}:{dark}" for name, (_, dark) in _COLOURS.items())
    + "}}\n"
    """body{max-width:72rem;margin:0 auto;padding:1.5rem 1rem 3rem;color:var(--fg);
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
)
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
details{margin:.75rem 0}
details.run>summary{font-size:1.1em;font-weight:600}
summary span{font-weight:normal;margin-left:.4rem}
details.text>summary{font-size:13px;color:var(--muted)}
.who{font-size:14px;margin:.4rem 0}
.lines,.runs,.rates,.rounds{font-size:14px}
.lines th,.lines td,.rounds th,.rounds td{padding:.15rem 1rem .15rem 0}
.n{text-align:right;font-variant-numeric:tabular-nums}
.rates th[colspan]{text-align:center}
.rates td,.runs td.n{white-space:nowrap}
"""
# The trees of a pull request's runs, on its report page and its runs' pages: the head's in the
# blue of the rates and the base's muted, a sketch dashed, with the ring of the members that never
# joined.
_TREES = """
.tree path{fill:none;stroke-linejoin:round;stroke-linecap:round}
.tree.head path{stroke:var(--rate-2)}
.tree.head circle{fill:var(--rate-2)}
.tree.base path{stroke:var(--muted)}
.tree.base circle{fill:var(--muted)}
.tree.sketch path{stroke-dasharray:3 3}
.epoch{stroke:var(--grid)}
.strong{font-weight:600}
.ring rect{fill:var(--bg);stroke:var(--line)}
.ring circle{fill:none;stroke:var(--muted);stroke-width:1.25}
.ring text{fill:var(--muted);font-size:10px}
.fell circle{fill:var(--bg);stroke:var(--muted);stroke-width:1.5}
.mark{stroke:var(--fg)}
.rule{stroke:var(--line)}
"""
# The report page of a pull request's run and the page of each of its runs, beside `_TREES`: a
# change's word always beside its colour.
_GRID = """
.verdicts{margin:1rem 0 0;padding:0;list-style:none;font-size:16px}
.verdicts li{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 6px}
.verdicts li>b{min-width:5rem}
.verdicts .muted{font-size:14px}
.chip{padding:1px 8px;border-radius:2em;font-size:12px;font-weight:600;white-space:nowrap;
background:var(--neutral-bg);color:var(--muted)}
.chip.moved,.chip.within{color:var(--fg)}
.chip.later{background:var(--good-bg);color:var(--good)}
.chip.earlier{background:var(--bad-bg);color:var(--bad)}
.chip.unbuilt{background:var(--none-bg);color:var(--none)}
#runs>.legend{margin:1rem 0 0;padding:10px 12px;border:1px solid var(--line);border-radius:6px}
.forks{display:flex;flex-direction:column;gap:10px;width:max-content}
.forks>div{display:flex;align-items:flex-start;gap:10px}
.forks .rate{width:116px}
.forks .seed{width:306px;padding:0 9px;font-size:12px;color:var(--muted)}
.forks .seed b{color:var(--fg)}
.forks .vacant{width:324px}
.ds{display:flex;gap:4px;margin:0 0 4px}
.d{display:flex;flex-direction:column;align-items:center;min-width:26px;padding:1px 4px 2px;
border:1px solid var(--line);border-radius:4px;font-size:12px;font-weight:600;
font-variant-numeric:tabular-nums;color:var(--fg);text-decoration:none}
.d span{font-size:10px;font-weight:400;color:var(--muted)}
.d:hover{border-color:var(--link)}
.d.moved{border-color:var(--fg)}
.d.later{background:var(--good-bg);color:var(--good)}
.d.earlier{background:var(--bad-bg);color:var(--bad)}
.d.none{background:var(--none-bg);color:var(--none)}
.chart .forks svg{display:block;max-width:none}
.fork{padding:6px 8px 8px;border:1px solid var(--line);border-radius:6px;color:var(--fg);
text-decoration:none}
.fork:hover{border-color:var(--link)}
.fork.moved{border-color:var(--fg);box-shadow:0 0 0 1px var(--fg)}
.fork.unbuilt{border-color:var(--none);border-style:dashed}
.said{display:flex;align-items:center;gap:6px;height:20px;margin:0 0 2px;font-size:12px;
color:var(--muted);white-space:nowrap}
.said b{color:var(--fg)}
.rate .said{font-size:13px}
.outlined{padding:0 6px;border:1px solid var(--fg);border-radius:4px;
box-shadow:0 0 0 1px var(--fg)}
.ran{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:.75rem 0 .25rem;
font-size:16px}
.reading{max-width:52rem;color:var(--muted)}
.how{max-width:52rem;margin:.25rem 0 .5rem;padding-left:1.25rem}
.about{max-width:52rem;margin:0 0 1rem}
#scenarios+.lead{max-width:52rem}
.about a{white-space:nowrap}
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
        "logos-chat/main/SHA/MEASUREMENT/ of SITE: point.json, each run's run.txt, run.json and "
        "report.txt where it has one, or build.log of a job that could not build chat-sim, and "
        "the point's page, index.html. What was there is replaced, unless it is the point of a "
        "run of a higher number. Then removes the runs' text of each point past the newest "
        f"{WINDOW}, by run number, and draws again the page of each point that loses it or has "
        "none. A matrix that read no run at the revision, built or not, is refused.",
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
        "logos-chat/pr-N/ of SITE: the matrix, each run's run.txt, run.json and report.txt where "
        "it has one, or build.log of a job that could not build chat-sim, at the revision under "
        "head/ and at the base under base/, meta.json, each scenario file it ran under "
        "scenarios/, the report page, index.html, and the page of each run, index.html in its "
        "directory under head/. What was there is replaced, unless it is a run of a higher "
        "number.",
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
    pr.add_argument(
        "--scenarios",
        metavar="DIR",
        type=Path,
        required=True,
        help="tools/sim/scenarios: a file a scenario, each the matrix ran kept and described",
    )
    pr.set_defaults(command=_pr)
    held = commands.add_parser(
        "long",
        help="keep a run of the long suite at a commit of logos-chat's main",
        description="Writes the run of the long suite, held to what is expected, to "
        "logos-chat/long/SHA/MEASUREMENT/ of SITE: long.json, which says what ran and what each "
        "scenario came to, each scenario's compare output as compare.txt, each run's run.txt, "
        "run.json and report.txt where it has one, or build.log of a job that could not build "
        "chat-sim, and the page, index.html. What was there is replaced, unless it is the long "
        "run of a run of a higher number. Then removes the text of each long run past the newest "
        f"{LONG_WINDOW}, by run number, and draws its page again. Runs of which none was read, "
        "built or not, are refused.",
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
        "cells": matrix["cells"],
        "summary": matrix["summary"],
    }
    _replace(out, copies)
    (out / "point.json").write_text(json.dumps(point, indent=1) + "\n", encoding="utf-8")
    point = read_point(out / "point.json")
    draw_point(out, point)
    return point


def write_pr(
    matrix: dict, runs: Path, base_runs: Optional[Path], scenarios: Path, site: Path, meta: dict
) -> dict:
    """Writes the run of a logos-chat pull request to `logos-chat/pr-N/` of `site`, replacing
    what was there: `matrix` as matrix.json, each run's text under `runs` in
    `head/<scenario>/<run>/` and under `base_runs` in `base/<scenario>/<run>/`, at any depth and
    each run by number, the file in `scenarios` of each scenario the matrix ran in
    `scenarios/`, `meta`, which says what ran, as meta.json, the report page and its runs'
    pages.

    Returns `meta`, as `read_pr_meta` reads it back.
    """
    out = site / LOGOS_CHAT / f"pr-{meta['pr']}"
    copies = _texts(runs, out / "head")
    if base_runs is not None:
        copies += _texts(base_runs, out / "base")
    for name in sorted({cell["scenario"] for cell in matrix["cells"]}):
        source = scenarios / f"{name}.json"
        if not source.is_file():
            raise Unreadable(f"{source}: no such file, and the matrix ran {name}")
        _grow_of(_json(source), str(source))
        copies.append((source, out / SCENARIOS / source.name))
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
    commands that run it again and, while its text is kept, the text, its lines and rounds and
    its wall time; where chat-sim did not build, the build's log, once. Every file it links is
    one `directory` holds."""
    logos_chat, measured, before = point["logos_chat"], point["measurement"], point["previous"]
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
                f"{_e(simulate.words(cell))}</span></summary>\n"
                + _side(
                    cell,
                    "head",
                    _run_dir(directory, cell),
                    _run_href(cell),
                    inline=not cell["unbuilt"]["head"],
                )
                + _again(cell, logos_chat, point["chat_sim"])
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
    """Writes the report page of the run of a logos-chat pull request kept in `out`, its index.html,
    from `matrix` and `meta`, which `out` holds, and the runs' text it keeps, and the page of each
    run: what each group's runs came to against the base's, a line a group; what the scenarios test
    and how, from the copies of their files `out` keeps; then of each group each scenario's file,
    linked to its copy where `out` keeps one, and the tree of each run of seeds 0 to 2, a row a rate
    and a column a seed, the head's over the base's, each linked to the run's page, under a rate
    that ran more seeds a strip of them. Where chat-sim did not build at the head, the build's log
    takes the place of the scenarios and the runs."""
    head, base = meta["revision"], meta["base"]
    facts = {"revision": head, "base": base or "", "chat_sim": meta["chat_sim"]}
    verdict = simulate.ran_at(facts, _code)
    said = [
        f"head {_commit(LOGOS_CHAT_URL, head)}",
        "no base" if base is None else f"merge base {_commit(LOGOS_CHAT_URL, base)}",
        f"chat-sim {_commit(CHAT_SIM_URL, meta['chat_sim'])}",
        _link(f"{LOGOS_CHAT_URL}/pull/{meta['pr']}", "the pull request"),
        _link(meta["run_url"], "the run"),
        f"{_when(meta)} UTC",
    ]
    body = (
        f'<p class="muted">{_link("../../", "chat-sim")} / {_link("../", "logos-chat")} / '
        f"#{meta['pr']}</p>\n"
        f"<h1>logos-chat #{meta['pr']}</h1>\n"
        f'<p class="facts">{" · ".join(said)}</p>\n'
    )
    if matrix["verdict"] == "unbuilt":
        body += (
            f'<p class="verdict"><b>{_e(simulate.headline(matrix))}</b> '
            f'<span class="muted">{verdict}</span></p>\n'
        )
        unbuilt = next((cell for cell in matrix["cells"] if cell["unbuilt"]["head"]), None)
        if unbuilt is not None:
            run = _run_href(unbuilt)
            body += _build(_job(unbuilt), out / "head" / run, f"head/{run}")
    else:
        drawn = {_run_href(cell): _trees(out, matrix, cell) for cell in matrix["cells"]}
        treed = [drawn[_run_href(cell)] for cell in matrix["cells"] if _treed(cell)]
        most = _most([tree for both in treed for tree in both])
        width = _widths(most, 1.2, 4.6)
        told = _told(out, matrix["cells"])
        every = _shared(list(told.values()))
        body += (
            f'<p class="muted">Ran {verdict}.</p>\n'
            + _verdicts(matrix)
            + _scenarios(told, every)
            + '<section class="chart" id="runs">\n'
            + _forks_key(matrix, most, width)
        )
        for key, called in simulate.GROUPS.items():
            cells = [cell for cell in matrix["cells"] if group(cell) == key]
            if cells:
                body += (
                    f"<h2>{_e(called)}</h2>\n"
                    + _about(out, cells, told, every)
                    + _forks(matrix, cells, drawn, width)
                )
        strips = ""
        if not all(_treed(cell) for cell in matrix["cells"]):
            strips = (
                " A strip under a row holds every seed of the rate, where a run of its group moved "
                "and the rate ran more: of each seed, how many epochs later the head's run first "
                "forked than the base's, the trunk's epoch standing in for a run that did not fork, "
                "linked to the run; under them, what the seeds came to."
            )
        body += (
            f'<p class="reading">A tree is one run, left to right by epoch from 0 to {_TOP}, the '
            "goal. The top line is the trunk. Each line below left the line it drops from at that "
            "epoch and stops at its dot. The thicker a line, the more members it ended with. The "
            "ring counts the members that never joined. Hover a line for its numbers, or open a "
            f"run for its labels and the command that runs it again.{strips}</p>\n</section>\n"
        )
        for cell in matrix["cells"]:
            draw_run(out, matrix, meta, cell, drawn[_run_href(cell)])
    body += (
        f'<p class="muted">What the page shows: the guide\'s '
        f'{_link("../../guide/reports/#the-pages-of-logos-chats-runs", "Reports")}.</p>\n'
    )
    title = f"chat-sim: logos-chat #{meta['pr']}"
    style = _CHARTS + _REPORT + _TREES + _GRID
    (out / "index.html").write_text(_page(title, body, style), encoding="utf-8")


def draw_run(
    out: Path,
    matrix: dict,
    meta: dict,
    cell: dict,
    trees: Tuple[Optional[dict], Optional[dict]],
) -> None:
    """Writes the page of `cell`'s run of the pull request kept in `out`, index.html in its
    directory under `head/`: what the head's run came to against the base's, in a sentence; both
    sides' trees, `trees` their lineages, large on one axis of epochs, each line labelled, with
    who fell behind and the members of each; each side's rounds; what running it again takes and
    the commands that run it again at each side; and each side's text the site keeps."""
    head, base = meta["revision"], meta["base"]
    named = _job(cell)
    chip, said = _run_said(matrix, cell)
    up = "../../../"
    body = (
        f'<p class="muted">{_link(up + "../../", "chat-sim")} / '
        f'{_link(up + "../", "logos-chat")} / {_link(up, "#" + str(meta["pr"]))} / '
        f"{_e(named)}</p>\n"
        f"<h1>{_e(_run_heading(cell))}</h1>\n"
        f'<p class="ran">{chip} <span>{_e(said)}</span></p>\n'
        f'<p class="facts">head {_commit(LOGOS_CHAT_URL, head)} · '
        + ("no base" if base is None else f"merge base {_commit(LOGOS_CHAT_URL, base)}")
        + f" · chat-sim {_commit(CHAT_SIM_URL, meta['chat_sim'])}</p>\n"
        f'<div class="chart wide">\n{_run_trees(matrix, meta, cell, trees)}</div>\n'
        '<p class="reading">seated counts every member added, the creator included, and not '
        "removed: those on a line and those that never joined.</p>\n"
    )
    run, based = out / simulate.run_page(cell), _base_run(out, matrix, cell)
    sides = [("head", head, run)] + ([] if based is None else [("base", base, based[0])])
    rounds = ""
    for side, sha, at in sides:
        table = _rounds(_run_json(at / "run.json"))
        if table:
            rounds += f'<p class="side">the {side} {_code(sha)}</p>\n{table}'
    if rounds:
        body += (
            f"<h2>The rounds</h2>\n{rounds}"
            '<p class="caption">Each round\'s adders, the members they added, the adders refused '
            "the whole round, and the virtual seconds the round took. The run does not say which "
            "adder added which member.</p>\n"
        )
    apart = " Run each block from a directory of its own, since each clones chat-sim."
    body += (
        "<h2>Run it yourself</h2>\n"
        f'<p class="reading">It needs {_NEEDS}.{apart if based else ""}</p>\n'
        + "".join(
            f'<p class="side">the {side} {_code(sha)}</p>\n'
            f"<pre>{_e(chr(10).join(simulate.again(cell, sha, meta['chat_sim'])))}</pre>\n"
            for side, sha, _ in sides
        )
        + "<h2>The text</h2>\n"
    )
    body += f'<p class="side">the head {_code(head)}</p>\n' + (
        _files(run, "") or '<p class="muted">The run left no text.</p>\n'
    )
    if base is not None:
        body += f'<p class="side">the base {_code(base)}</p>\n'
    if based is not None:
        body += _files(based[0], up + based[1]) or '<p class="muted">The run left no text.</p>\n'
    run.mkdir(parents=True, exist_ok=True)
    title = f"chat-sim: logos-chat #{meta['pr']}, {named}"
    style = _CHARTS + _REPORT + _TREES + _GRID
    (run / "index.html").write_text(_page(title, body, style), encoding="utf-8")


def describe(scenario: object) -> List[str]:
    """How the run of a scenario file of a `grow` step alone, as tools/sim takes it, tests what
    `_TESTS` says, a sentence each, as a pull request's report page says it, where a row of
    trees is headed by its rate of loss: its group and timers, who adds whom, what the trunk is,
    when a run ends, and what its network does to the frames each member receives. Refuses a
    scenario of any other step, or one whose fields it reads are not of their kinds."""
    grow = _grow_of(scenario)
    members, network = scenario["members"], scenario.get("network", {})
    said = [
        _group_said(scenario),
        f"{_CREATOR} creates the group, and the other {members - 1} members wait to be added.",
        *_adds(grow),
    ]
    if "size" in grow:
        said.append(
            f"An adder whose add would take the group above {grow['size']} members first removes "
            "the member added longest ago that does not add that round."
        )
    if "by" in grow:
        said.append(f"The trunk is the line {_CREATOR} is on.")
    else:
        said.append(
            f"The trunk starts at {_CREATOR}'s first state and, at each fork, follows the state "
            "more members hold or have gone past."
        )
    said.append(
        f"A run goes on past its first fork, and ends at the start of a round once the trunk "
        f"reaches epoch {grow['epochs']}, the trunk and the branches off it number "
        f"{grow['branches']} or more, all {members} members have been added, or no member can "
        "add."
    )
    late = _late(network.get("delay_ms", 0), members)
    return said + ([late] if late else []) + ([_lost(network)] if "network" in scenario else [])


def _grow_of(scenario: object, where: str = "") -> dict:
    """The grow step of `scenario`, a scenario of that step alone whose fields `describe` reads
    are of their kinds: refuses any other."""
    steps = scenario.get("steps") if isinstance(scenario, dict) else None
    grow = steps[0] if isinstance(steps, list) and len(steps) == 1 else None
    if not isinstance(grow, dict) or grow.get("step") != "grow":
        raise Unreadable(f"{where or 'it'} is not a scenario of a grow step alone")
    network = scenario.get("network", {})
    network = network if isinstance(network, dict) else {"delay_ms": None}
    delay, timers = network.get("delay_ms", 0), scenario.get("timers", "fast")
    whole = [scenario.get("members"), grow.get("epochs"), grow.get("branches")]
    whole += [grow.get("size", 0), grow.get("adders", 1)]
    whole += list(delay.values()) if isinstance(delay, dict) else [delay]
    whole += list(timers.values()) if isinstance(timers, dict) else []
    shares = [network.get("loss", 0), network.get("welcome_loss", 0)]
    if (
        any(type(one) is not int for one in whole)
        or any(type(one) not in (int, float) for one in shares)
        or type(grow.get("by", "")) is not str
        or not isinstance(timers, dict) and timers not in ("fast", "default")
    ):
        raise Unreadable(f"{where or 'it'}: a field of its grow is not of its kind")
    return grow


def _adds(grow: dict) -> List[str]:
    """Who adds a member each round of a `grow` step, and when one can."""
    if "by" in grow:
        return [
            f"Each round, {grow['by']} alone adds the next member.",
            f"{grow['by']} can add while no member has gone past its state, unless it was "
            "refused a whole round on that state.",
        ]
    drawn = (
        "one of the members that can add, and it adds"
        if grow["adders"] == 1
        else f"one to {grow['adders']} of the members that can add, and each adds"
    )
    return [
        f"Each round, the seed draws {drawn} the next member at once, on whatever branch it is on.",
        "A member can add once it holds the group, while no member has gone past its state, "
        "unless it was refused a whole round on that state.",
    ]


def _group_said(scenario: dict) -> str:
    """The kind of group a scenario's creator creates, and the timers it runs on: a GroupV3
    group's own, whatever the file says."""
    if group(scenario) == "v3":
        if "timers" in scenario:
            return "A GroupV3 group, on timers of its own, whatever the file's timers say."
        return "A GroupV3 group, on timers of its own."
    timers = scenario.get("timers", "fast")
    if timers == "default":
        return "A GroupV2 group, on GroupV2's default timers."
    if timers == "fast":
        return "A GroupV2 group, on the harness's fast timers."
    given = ", ".join(f"{name} {milliseconds} ms" for name, milliseconds in timers.items())
    return f"A GroupV2 group, on GroupV2's default timers but {given}."


def _late(delay: object, members: int) -> str:
    """How late each member receives every frame, `delay` one duration for every member or one by
    name, where a member it does not name has none: nothing where none is late."""
    each = [delay]
    if isinstance(delay, dict):
        each = list(delay.values()) + [0] * (len(delay) < members)
    least, most = min(each), max(each)
    if not most:
        return ""
    if least == most:
        return f"Each member receives every frame {most} ms late."
    return (
        f"Each member receives every frame late, by a delay of its own from {least} to {most} ms."
    )


def _lost(network: dict) -> str:
    """What a network loses of the frames each member receives, at the rate a row is headed by,
    and of its Welcomes, which are lost at their own rate."""
    loss, welcomes = network.get("loss", 0), network.get("welcome_loss", 0)
    row = "A row is a rate of loss: each member loses that share of the frames it receives"
    if welcomes == loss:
        return f"{row}, at random, Welcomes among them."
    if not welcomes:
        return f"{row}, at random, but no Welcome."
    return f"{row} other than Welcomes, at random, and {_share(welcomes)} of its Welcomes."


def _share(fraction: float) -> str:
    return f"{fraction * 100:g}%"


def _told(out: Path, cells: Sequence[dict]) -> Dict[str, List[str]]:
    """What `describe` says of the scenario of each of `cells`, by name, from the copy of its file
    `out` keeps: nothing where a copy is missing or one it cannot say, as of a run kept before
    the copies were."""
    told: Dict[str, List[str]] = {}
    for name in dict.fromkeys(cell["scenario"] for cell in cells):
        try:
            told[name] = describe(_json(out / SCENARIOS / f"{name}.json"))
        except Unreadable:
            return {}
    return told


def _shared(said: Sequence[List[str]]) -> List[str]:
    """The sentences each of `said` holds, in the order of the first: none of none."""
    return [one for one in said[0] if all(one in other for other in said[1:])] if said else []


def _scenarios(told: Dict[str, List[str]], every: List[str]) -> str:
    """What the scenarios of a pull request's run test and how, `told` what `describe` says of
    each by name: what they test, the sentences `every` one of them holds, how a column's seed
    runs, and where the guide says how a grow runs. Nothing without them."""
    if not told:
        return ""
    guide = _link("../../guide/simulations/#a-group-that-grows", "A group that grows")
    return (
        f'<h2 id="scenarios">The scenarios</h2>\n<p class="lead">{_e(_TESTS)}</p>\n'
        + '<ul class="how">\n'
        + "".join(f"<li>{_e(one)}</li>\n" for one in [*every, _SEEDS])
        + "</ul>\n"
        + f"<p class=\"muted\">How the files' grow step runs: the guide's {guide}.</p>\n"
    )


def _about(out: Path, cells: Sequence[dict], told: Dict[str, List[str]], every: List[str]) -> str:
    """The line under a group's name on a pull request's report page: what `describe` says of
    each of its scenarios, `told`, that not `every` scenario of the page has, then each
    scenario's file, linked to the copy `out` keeps where it keeps one, with what its own
    sentences say."""
    names = list(dict.fromkeys(cell["scenario"] for cell in cells))
    theirs = [told[name] for name in names if name in told]
    ours = [one for one in _shared(theirs) if one not in every]
    files = []
    for name in names:
        own = [one for one in told.get(name, []) if one not in every and one not in ours]
        copy, shown = f"{SCENARIOS}/{name}.json", f"<code>{_e(name)}.json</code>"
        if (out / copy).is_file():
            shown = _link(copy, f"{name}.json", code=True)
        files.append(shown + (f" ({_e(' '.join(own))})" if own else ""))
    said = "".join(f"{_e(one)} " for one in ours)
    return f'<p class="about">{said}The scenario files: {" · ".join(files)}</p>\n'


def draw_long(directory: Path, long: dict) -> None:
    """Writes the page of the long run in `directory`, its index.html, from `long` and the text
    `directory` keeps: what ran, a row a scenario with what its runs came to and what compare said
    of them, the build's log once where chat-sim did not build, then a section a scenario with
    compare's verdict and, while its text is kept, its output, its runs as a table, and each run
    with the commands that run it again and, while its text is kept, the text and its lines and
    rounds. Every file it links is one `directory` holds."""
    logos_chat = long["logos_chat"]
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
        "expected of it; nothing held where chat-sim holds no outcome of the scenario. "
        '"As expected" means the runs came out as <code>tools/sim/expected</code> holds them, not '
        "that they came out well.</p>\n"
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
            body += _side(cell, "head", directory / at, f"{at}/", inline=not run["unbuilt"])
            body += _again(
                {"scenario": name, "run": run["run"], "seed": run["seed"]},
                logos_chat,
                long["chat_sim"],
            )
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
        if matrix["verdict"] not in (None, *_RESULTS):
            raise Unreadable(f"verdict {matrix['verdict']!r} is none of a matrix")
        for number, one in enumerate(matrix["groups"]):
            where = f"groups[{number}]"
            _shaped(one, _GROUP, where)
            _group(one, where)
            if one["verdict"] not in (None, *_RESULTS):
                raise Unreadable(f"{where}.verdict {one['verdict']!r} is none of a matrix")
            for counted, result in enumerate(one["results"]):
                _result(result, f"{where}.results[{counted}]")
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
            _cell(cell, f"cells[{number}]", alone=True, shape=_held_cell(cell))
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
    if not args.scenarios.is_dir():
        parser.error(f"--scenarios: no such directory: {args.scenarios}")
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
            args.scenarios,
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
            _outcome(cell[side], f"{where}.{side}", _held_outcome(cell[side]))
        if cell["ended"][side] not in (None, *_ENDINGS):
            raise Unreadable(f"{where}.ended.{side} is not how a run ended")
    if cell["change"] not in (None, *_CHANGES):
        raise Unreadable(f"{where}.change {cell['change']!r} is none of compare's")
    based = (cell["base"], cell["epochs"]["base"], cell["ended"]["base"], cell["change"])
    if alone and (based != (None,) * 4 or cell["unbuilt"]["base"] or cell.get("d") is not None):
        raise Unreadable(f"{where} has a base")


def _held_cell(cell: object) -> dict:
    """The shape of a cell a point holds: one from before the cells held `d` holds `moved`, and
    one from before they named their group names none too."""
    if not isinstance(cell, dict) or "d" in cell:
        return _CELL
    return _MOVED_CELL if "group" in cell else _UNGROUPED_CELL


def _result(result: object, where: str) -> None:
    """Refuses `result` unless it is the result of a scenario's cells of a matrix."""
    _shaped(result, _RESULT, where)
    if not all(type(seed) is int for seed in result["seeds"]):
        raise Unreadable(f"{where}.seeds is not a list of seeds")
    if result["result"] not in (None, *_RESULTS):
        raise Unreadable(f"{where}.result {result['result']!r} is none of a matrix")


def _outcome(outcome: object, where: str, shape: dict = _OUTCOME) -> None:
    """Refuses `outcome` unless it is a run's outcome of `shape`."""
    _shaped(outcome, shape, where)
    for field in ("conversation", "members_digest"):
        if outcome[field] is not None and _HEX.fullmatch(outcome[field]) is None:
            raise Unreadable(f"{where}.{field} is not an id")
    if outcome["ended"] not in (None, *_ENDINGS):
        raise Unreadable(f"{where}.ended is not how a run ended")


def _held_outcome(outcome: object) -> dict:
    """The shape of an outcome a point or a long run holds: one from before the outcomes held the
    epoch of the first fork has no such epoch, and one from before they counted the members that
    never joined has no such count either."""
    if not isinstance(outcome, dict) or "first_fork" in outcome:
        return _OUTCOME
    return _UNFORKED_OUTCOME if "never_joined" in outcome else _UNJOINED_OUTCOME


def _held_summary(one: object) -> dict:
    """The shape of a point's summary of a scenario: one from before the summaries counted the
    members that never joined has no such count, one from before they held how the runs forked
    has none of that either, and one from before they named their group has no group too."""
    if not isinstance(one, dict) or "never_joined" in one:
        return _SUMMARY
    if "split_at" in one:
        return _UNJOINED_SUMMARY
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
            _outcome(
                run["outcome"], f"{where}.runs[{number}].outcome", _held_outcome(run["outcome"])
            )
            if run["outcome"]["seed"] != run["seed"]:
                raise Unreadable(f"{where}.runs[{number}].outcome is not of the run's seed")


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
    """The section of one sweep: compare's output, sweep.txt, and each run's run.txt and its
    report.txt where it has one."""
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
            + (show(name, run, "report.txt") if (sweep / run / "report.txt").is_file() else "")
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
    the share of its runs that ended whole."""
    drawn = ""
    for called, held, rates in groups:
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
                _whole_share,
                _whole_said,
                top=100,
                plot=240,
                label=f"{called} runs whole at {_TOP} epochs",
                goal=100,
                merged=True,
                noted=True,
                shown=_percent,
            )
        )
    return (
        '<section class="chart" id="whole">\n'
        f"<h2>Runs whole at {_TOP} epochs</h2>\n"
        + drawn
        + '<p class="caption">The share of each rate\'s runs that ended whole: one group, every '
        f"member that joined at its tip, at epoch {_TOP}. A share puts a rate of 3 runs and one "
        "of 20 on one scale. The goal is every line at the top. A line "
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
    shown: Optional[Callable[[float], str]] = None,
) -> str:
    """A chart over the points: a line a rate of what `value` reads of the rate's summary at each
    point, a marker on each with `hover` in its title, and the rate and its newest value at the
    end of its line, on a scale from 0 to `top`, its ticks and values as `shown` writes them. With
    `goal`, a dashed line at it; with `merged`, the pull requests each point merged under its
    date; with `noted`, the commit chat-sim moved to over each dashed line of a point at another
    measurement. Without `shown` they are written as `_number` writes them."""
    shown = shown or _number
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
            f"{shown(tick)}</text>"
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
    drawn += _ends(ends, xs[-1], bottom + 4, shown)
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


def _cell_drawn(cell: dict, name: str, x: float, y: float) -> str:
    """A cell: its epochs without a fork in a bin's colour, a dot for a run that ended whole, a
    ring for one that ended behind, and how it came out as its title."""
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


def _heatmap(rows: Sequence[Tuple[str, Dict[int, dict]]], label: str) -> str:
    """The cells of `rows` as a heatmap, a row a rate and a column a seed."""
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
                drawn.append(_cell_drawn(cells[seed], name, left + size * column, y))
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


def _verdicts(matrix: dict) -> str:
    """What each group's runs came to against the base's, a line a group, as the comment's line
    on them has it: the chip of its verdict, then what each of its scenarios came to, by its
    rate; or of runs with no base, how many."""
    listed = ""
    for one in matrix["groups"]:
        verdict = one["verdict"]
        said = [f"<b>{_e(simulate.GROUPS[group(one)])}</b>"]
        if verdict is None:
            said.append(_chip("same", "not compared"))
            told = f"{one['counts']['cells']} runs, no base to compare with"
        else:
            shown = _ARROWS.get(verdict, "") + simulate.RESULTS[verdict]
            said.append(_chip(_MISSING.get(verdict, verdict), shown))
            told = " · ".join(simulate.told(matrix, result) for result in one["results"])
        said.append(f'<span class="muted">· {_e(told)}</span>')
        listed += f"<li>{' '.join(said)}</li>\n"
    return f'<ul class="verdicts">\n{listed}</ul>\n'


def _chip(kind: str, said: Optional[str] = None) -> str:
    """The chip of a change or a result, its word beside its colour: `said`, or `kind` itself."""
    shown = kind if said is None else said
    return f'<span class="chip {kind.replace("-", "")}">{_e(shown)}</span>'


def _forks_key(matrix: dict, most: int, width: Callable[[int], float]) -> str:
    """The legend of the trees: the head's over the base's, how wide `width` draws a line of so
    many members up to `most`, the ring of those that never joined, and the outline of a run that
    moved where one did."""

    def tree(side: str) -> str:
        return (
            '<svg width="46" height="16" viewBox="0 0 46 16" aria-hidden="true">'
            f'<g class="tree {side}"><path d="M2 4H42" stroke-width="3.4"/>'
            '<path d="M18 4V12H30" stroke-width="1.8"/><circle cx="42" cy="4" r="2.5"/>'
            '<circle cx="30" cy="12" r="1.8"/></g></svg>'
        )

    keys = [(tree("head"), "the head on top")]
    if matrix["base"] is not None:
        keys.append((tree("base"), "the base under it"))
    if most:
        shown = sorted({1, min(10, most), most})
        lines = "".join(
            f'<path d="M{4 + 30 * number} 8H{24 + 30 * number}" stroke-width="{width(one):.2f}"/>'
            for number, one in enumerate(shown)
        )
        drawn = (
            f'<svg width="{30 * len(shown)}" height="16" viewBox="0 0 {30 * len(shown)} 16" '
            f'aria-hidden="true"><g class="tree base">{lines}</g></svg>'
        )
        keys.append((drawn, f"{', '.join(map(str, shown))} member{'' if most == 1 else 's'}"))
    ring = f'<svg width="34" height="16" viewBox="0 0 34 16" aria-hidden="true">{_ring(1, 8, "n")}'
    keys.append((ring + "</svg>", "never joined"))
    if any(cell["change"] == "moved" for cell in matrix["cells"]):
        keys.append(('<span class="outlined">outline</span>', "moved"))
    return _legend(*keys)


def _forks(
    matrix: dict, cells: Sequence[dict], trees: Dict[str, tuple], width: Callable[[int], float]
) -> str:
    """The trees of a group's `cells` of seeds 0 to 2, `trees` their lineages by run, a row a rate
    and a column a seed: each row as tall as its most lines, headed by its rate, and followed by
    the strip of every seed of the rate where it ran more."""
    pitch, pad, between = 7, 7, 8
    rows = _by_rate(cells)
    seeds = sorted({seed for _, by_seed in rows for seed, cell in by_seed.items() if _treed(cell)})
    based = matrix["base"] is not None
    ticks = (
        '<svg width="306" height="14" viewBox="0 0 306 14" aria-hidden="true">'
        f"{_epoch_ticks(_along(4, 264), 11)}</svg>"
    )
    heads = "".join(f'<div class="seed"><b>seed {seed}</b>{ticks}</div>' for seed in seeds)
    drawn = [f'<div><div class="rate"></div>{heads}</div>\n']
    for _, by_seed in rows:
        first = next(iter(by_seed.values()))
        count = max(
            max(_rows_of(trees[_run_href(cell)][0]), _rows_of(trees[_run_href(cell)][1]))
            for cell in by_seed.values()
            if _treed(cell)
        )
        tall = pad + (count - 1) * pitch + pad
        tops = (pad, tall + between + pad)
        height = tall * 2 + between if based else tall
        row = [_rate_head(matrix, first, tops, height)]
        for seed in seeds:
            if seed not in by_seed:
                row.append('<div class="vacant"></div>')
                continue
            cell = by_seed[seed]
            row.append(_fork(matrix, cell, trees[_run_href(cell)], width, tops, height))
        drawn.append(f"<div>{''.join(row)}</div>\n")
        if not all(_treed(cell) for cell in by_seed.values()):
            drawn.append(_strip(matrix, [cell for _, cell in sorted(by_seed.items())]))
    return f'<div class="wide"><div class="forks">\n{"".join(drawn)}</div></div>\n'


def _treed(cell: dict) -> bool:
    """Whether a pull request's report page draws `cell`'s runs as trees: seeds 0 to 2, those
    every pull request runs of each rate."""
    return cell["seed"] < min(simulate.RATES["pr"].values())


def _strip(matrix: dict, cells: Sequence[dict]) -> str:
    """The strip of a rate that ran more seeds than have trees, `cells` all its cells: each seed's
    `d`, linked to its run's page, and under them what the seeds came to, as the comment says it."""
    result = next(
        one
        for judged in matrix["groups"]
        for one in judged["results"]
        if one["scenario"] == cells[0]["scenario"]
    )
    seeds = "".join(_d(matrix, cell) for cell in cells)
    return (
        f'<div class="strip"><div class="rate"><div class="said">seeds {cells[0]["seed"]} to '
        f'{cells[-1]["seed"]}</div></div><div><div class="ds">{seeds}</div>'
        f'<div class="said">{_e(simulate.result_words(matrix, result))}</div></div></div>\n'
    )


def _d(matrix: dict, cell: dict) -> str:
    """A seed of a strip, a link to its run's page: the seed over its `d`, `-` where it has none,
    in the colour of the way the head's run moved, outlined where it moved as a tree is; its
    page's sentence on hover."""
    d, kind = cell["d"], "d"
    if cell["change"] == "moved":
        kind += " moved"
    if d is None:
        kind += " none"
    elif d:
        kind += " later" if d > 0 else " earlier"
    shown = "-" if d is None else f"{d:+d}" if d else "0"
    return (
        f'<a class="{kind}" href="{_e(quote(simulate.run_page(cell)))}" '
        f'title="{_e(_run_said(matrix, cell)[1])}"><span>{cell["seed"]}</span>{shown}</a>'
    )


def _rate_head(matrix: dict, cell: dict, tops: Tuple[float, float], height: float) -> str:
    """The head of a row of trees, `cell` one of its runs: its rate, and the head's label and the
    base's beside their trees."""
    drawn = f'<text class="note" x="0" y="{tops[0] + 4}">head</text>'
    if matrix["base"] is not None:
        drawn += f'<text class="note" x="0" y="{tops[1] + 4}">base</text>'
    return (
        f'<div class="rate"><div class="said"><b>{_e(_loss(cell))}</b></div>'
        f'<svg width="116" height="{height}" viewBox="0 0 116 {height}">{drawn}</svg></div>'
    )


def _fork(
    matrix: dict,
    cell: dict,
    trees: Tuple[Optional[dict], Optional[dict]],
    width: Callable[[int], float],
    tops: Tuple[float, float],
    height: float,
) -> str:
    """A run's place in its row, a link to its page: what moved in a few words, over the head's
    tree and the base's, `trees` their lineages, at `tops`, each with its ring; outlined where it
    moved, dashed where a side did not build."""
    x = _along(4, 264)
    kind = "fork"
    if cell["change"] == "moved":
        kind += " moved"
    elif any(cell["unbuilt"].values()):
        kind += " unbuilt"
    drawn = [
        _svg(306, height, _job(cell)),
        _epoch_lines(x, 0, height),
    ]
    sides = [("head", trees[0], tops[0])]
    if matrix["base"] is not None:
        sides.append(("base", trees[1], tops[1]))
    for side, lineage, top in sides:
        why = _unshown(cell, side)
        if why is not None:
            drawn.append(f'<text class="note" x="{x(0):.1f}" y="{top + 4}">{_e(why)}</text>')
            continue
        if lineage is None:
            drawn.append(_sketch(cell, side, x, top, 7))
        else:
            drawn.append(_tree(lineage, side, x, top, 7, width, 1.8))
        never = _never(cell, side, lineage)
        if never:
            drawn.append(_ring(272, top, never))
    return (
        f'<a class="{kind}" href="{_e(quote(simulate.run_page(cell)))}">'
        f'<div class="said">{_fork_said(matrix, cell)}</div>{"".join(drawn)}</svg></a>'
    )


def _fork_said(matrix: dict, cell: dict) -> str:
    """What the head of a run's tree says: where the head's run first forked, or how its trunk
    ended; with the chip of a run that moved, what the base's had; with that of one not compared
    side by side, why."""
    if cell["head"] is None:
        unbuilt = _chip("unbuilt") + " " if cell["unbuilt"]["head"] else ""
        return f"{unbuilt}<span>{_unshown(cell, 'head')}</span>"
    bold = "<b>{}</b>"
    if cell["change"] == "moved":
        return _chip("moved") + f" <span>{_against(cell, bold)}</span>"
    reach = _first_fork(cell, "head", bold)
    if matrix["base"] is not None and cell["base"] is None:
        unbuilt = _chip("unbuilt") + " " if cell["unbuilt"]["base"] else ""
        return f"{unbuilt}<span>{reach} · {_unshown(cell, 'base')}</span>"
    return f"<span>{reach}</span>"


def _first_fork(cell: dict, side: str, strong: str) -> str:
    """Where a side's run of `cell` first forked, or how and where its trunk ended, the epoch in
    `strong`, a format; its verdict where it has no epoch."""
    epochs, ended = cell["epochs"][side], cell["ended"][side]
    if epochs is None:
        return _e(cell[side]["verdict"] or "no verdict")
    shown = strong.format(epochs)
    return f"first fork {shown}" if ended == "split" else f"{ended or 'ended'} at {shown}"


def _against(cell: dict, strong: str) -> str:
    """A run of `cell` that moved against the base's in a few words: where the head's first forked
    and the base's did, or at the same epoch how each ended where that differs, the head's epoch
    in `strong`, a format."""
    reach, epochs = _first_fork(cell, "head", strong), cell["epochs"]["base"]
    if cell["d"] == 0 and cell["ended"]["head"] != cell["ended"]["base"]:
        return f"{reach}, base {cell['ended']['base'] or 'ended'} at {epochs}"
    return f"{reach}, base {epochs}"


def _run_said(matrix: dict, cell: dict) -> Tuple[str, str]:
    """What the page of `cell`'s run says first: the chip of its change, and a sentence of how the
    head's run came out against the base's."""
    change = cell["change"]
    if cell["head"] is None:
        if cell["unbuilt"]["head"]:
            return _chip("unbuilt"), "chat-sim does not build at the head, so nothing ran."
        return _chip("unbuilt", "no outcome"), "The head's run came to no outcome."
    reached = _forked(cell, "head")
    if change == "same":
        return _chip("same"), f"The head {reached}, as the base's run did."
    if change is None and matrix["base"] is None:
        return _chip("same", "not compared"), f"The head {reached}. There is no base to compare."
    if change is None:
        why = "chat-sim does not build at the base"
        if not cell["unbuilt"]["base"]:
            why = "The base's run came to no outcome"
        chip = _chip("unbuilt" if cell["unbuilt"]["base"] else "same", "not compared")
        return chip, f"The head {reached}. {why}, so the two are not compared."
    return _chip(change), _moved_said(cell)


def _moved_said(cell: dict) -> str:
    """How a run of `cell` that moved came out against the base's, in a sentence: how much sooner
    or later the head's first forked, or that it first forked at the base's epoch."""
    d, was = cell["d"], cell["epochs"]["base"]
    said = f"The head {_forked(cell, 'head')}"
    if d:
        later = "earlier" if d < 0 else "later"
        return f"{said}, {_plural(abs(d), 'epoch')} {later} than the base's {was}."
    return (
        f"{said}, and the base {_forked(cell, 'base')}. The runs differ, but not in the epoch "
        "of the first fork, so the run counts neither earlier nor later."
    )


def _forked(cell: dict, side: str) -> str:
    """How a side's run of `cell` came out in a few words: the epoch it first forked at, or how
    and where its trunk ended."""
    epochs, ended = cell["epochs"][side], cell["ended"][side]
    if epochs is None:
        return f"came to {cell[side]['verdict'] or 'no verdict'}"
    if ended == "split":
        return f"first forked at epoch {epochs}"
    if ended == "stalled":
        return f"stalled at epoch {epochs}"
    return f"ended {ended or 'one group'} at epoch {epochs}"


def _run_trees(
    matrix: dict, meta: dict, cell: dict, trees: Tuple[Optional[dict], Optional[dict]]
) -> str:
    """Both sides' runs of `cell` large on one axis of epochs, the head's over the base's, `trees`
    their lineages: each line labelled with its members, its epochs and the line it left, a mark
    where the run first forked, who fell behind and when, and the members of each. Its text is
    placed by an estimate of its width, since no browser measures it here."""
    wide, left, right, pitch = 1118, 92, 852, 28
    x = _along(left, right)
    width = _widths(_most(trees), 2, 9)
    goal = f'<text class="note" x="{x(_TOP) + 14:.1f}" y="14">the goal</text>'
    front = [_epoch_ticks(x, 14), goal]
    y = 34
    sides = [("head", meta["revision"], trees[0])]
    if matrix["base"] is not None:
        sides.append(("base", meta["base"], trees[1]))
    for number, (side, sha, lineage) in enumerate(sides):
        if number:
            front.append(f'<line class="rule" x1="0" x2="{wide}" y1="{y + 12}" y2="{y + 12}"/>')
            y += 24
        top = y + 46
        front.append(f'<text class="label strong" x="0" y="{top + 4}">{side}</text>')
        front.append(f'<text class="note" x="0" y="{top + 20}">{_e(sha[:7])}</text>')
        why = _unshown(cell, side)
        if why is not None:
            front.append(f'<text class="note" x="{left}" y="{top + 4}">{_e(why)}</text>')
            y = top + 30
            continue
        epochs = cell["epochs"][side]
        if cell["ended"][side] == "split" and epochs is not None:
            front.append(
                f'<line class="mark" x1="{x(epochs):.1f}" x2="{x(epochs):.1f}" y1="{top - 8}" '
                f'y2="{top - 22}"/><text class="note" x="{x(epochs) + 5:.1f}" y="{top - 13}">'
                f'first fork <tspan class="label strong">{epochs}</tspan></text>'
            )
        rows = 2
        if lineage is not None:
            front.append(_tree(lineage, side, x, top, pitch, width, 3.5, _labelled))
            rows = len(lineage["rows"])
        else:
            trunk = cell[side]["trunk_epoch"]
            end = x(epochs if trunk is None else trunk) if (trunk, epochs) != (None, None) else left
            front.append(_sketch(cell, side, x, top, pitch))
            front.append(
                f'<text class="note" x="{end + 8:.1f}" y="{top + 4}">the site keeps no lineage '
                "of this run: its trunk and its first fork, from how it came out</text>"
            )
        below = top + (rows - 1) * pitch + 34
        front.append(f'<text class="note" x="0" y="{below + 4}">fell behind</text>')
        lanes = _fell(front, lineage, x, left, below)
        below += max(lanes, 1) * 18 + 10
        front.append(f'<text class="note" x="0" y="{below + 4}">members</text>')
        front.append(_members(cell, side, lineage, left, below))
        y = below + 8
    height = y + 10
    back = [_epoch_lines(x, 20, height)]
    label = f"{_job(cell)}: the head's tree over the base's"
    return _svg(wide, height, label) + "".join(back + front) + "</svg>\n"


def _labelled(line: dict, end: float, y: float) -> str:
    """The label beside the tip of a line of a run's large tree, `end` the tip's right: its
    members, its epochs, and the line it left where that is not the trunk."""
    trunk = line["parent"] is None
    said = _span(line) + ("" if trunk or line["parent"] == 0 else f", off line {line['from']}")
    return (
        f'<text class="note" x="{end + 6:.1f}" y="{y + 4:.1f}">'
        + ('<tspan class="label strong">trunk </tspan>' if trunk else "")
        + f'<tspan class="label strong">{_plural(line["members"], "member")}</tspan>, {said}</text>'
    )


def _fell(
    drawn: List[str], lineage: Optional[dict], x: Callable[[float], float], left: float, y: float
) -> int:
    """Adds to `drawn` who of a run's members fell behind at each epoch, as `lineage` has them, a
    ring and their names at the epoch, in as many lanes from `y` down as keeps their names apart;
    that nobody did, or that the site keeps no lineage. Returns the lanes it took."""
    if lineage is None or not lineage["fell"]:
        said = "the site keeps no lineage" if lineage is None else "nobody"
        drawn.append(f'<text class="note" x="{left}" y="{y + 4}">{said}</text>')
        return 1
    at: Dict[int, List[str]] = {}
    for name, epoch in lineage["fell"]:
        at.setdefault(epoch, []).append(name)
    lanes: List[float] = []
    for epoch in sorted(at):
        names = ", ".join(at[epoch])
        said = f"{names} at {epoch}"
        lane = next((number for number, end in enumerate(lanes) if end < x(epoch) - 6), len(lanes))
        if lane == len(lanes):
            lanes.append(0)
        lanes[lane] = x(epoch) + 8 + _width(said)
        down = y + lane * 18
        drawn.append(
            f'<g class="fell"><title>{_e(names)} fell behind at epoch {epoch}</title>'
            f'<circle cx="{x(epoch):.1f}" cy="{down}" r="4"/>'
            f'<text class="note" x="{x(epoch) + 8:.1f}" y="{down + 4}">{_e(said)}</text></g>'
        )
    return len(lanes)


def _members(cell: dict, side: str, lineage: Optional[dict], left: float, y: float) -> str:
    """The members of a side's run of `cell` in a line from `left`: how many were seated, on the
    trunk, on the lines off it and behind at the end, as `lineage` has them, or off the trunk as
    the run's outcome counts them, and how many never joined, in a ring."""
    outcome = cell[side]
    seated = "" if outcome["seated"] is None else f"{outcome['seated']} seated"
    if lineage is not None:
        trunk, off = lineage["rows"][0], lineage["rows"][1:]
        rest = f" · {trunk['members']} on the trunk · {sum(one['members'] for one in off)} on "
        rest += f"{_plural(len(off), 'line')} off it"
        if lineage["behind"]:
            rest += f" · {len(lineage['behind'])} behind at the end"
    elif outcome["members_off_trunk"] is not None:
        rest = f" · {outcome['members_off_trunk']} off the trunk"
    else:
        rest = ""
    never = _never(cell, side, lineage)
    if never is not None:
        rest += " · "
    if not seated:
        rest = rest.removeprefix(" · ")
    drawn = (
        f'<text class="note" x="{left}" y="{y + 4}"><tspan class="label strong">{seated}</tspan>'
        f"{_e(rest)}</text>"
    )
    after = left + _width(seated) * 1.08 + _width(rest) + 4
    if never:
        drawn += _ring(after, y, never)
        after += _ring_width(never) + 5
    if never is not None:
        said = "never joined" if never else "none never joined"
        drawn += f'<text class="note" x="{after:.1f}" y="{y + 4}">{said}</text>'
    return drawn


def _trees(out: Path, matrix: dict, cell: dict) -> Tuple[Optional[dict], Optional[dict]]:
    """The lineages of `cell`'s runs of the pull request kept in `out`, at the head and at the
    base, from the run.json of each the site keeps, each `None` where it keeps none that can be
    drawn."""
    based = _base_run(out, matrix, cell)
    head = _lineage(_run_json(out / simulate.run_page(cell) / "run.json"))
    return head, None if based is None else _lineage(_run_json(based[0] / "run.json"))


def _base_run(out: Path, matrix: dict, cell: dict) -> Optional[Tuple[Path, str]]:
    """Where the base's run of `cell` of the pull request kept in `out` keeps its text, under
    `base/`, and its address from the report page; `None` without a base."""
    if matrix["base"] is None:
        return None
    run = _run_href(cell)
    return out / "base" / run, f"base/{run}"


def _lineage(report: Optional[dict]) -> Optional[dict]:
    """The lineage of a run's grow as `report`, its run.json, holds it, a row a line: the trunk
    first, then the lines that left each line, the latest to leave first, each followed by those
    that left it, so that no line's drop crosses another; who fell behind and at which epoch, who
    was behind at the end and who never joined. `None` where a line lacks the line it left, the
    epoch it left at, its tip's epoch or its members, or does not come down from the trunk, as in
    a run.json of an earlier measurement."""
    grow = report.get("grow") if report is not None else None
    lines = grow.get("lines") if isinstance(grow, dict) else None
    if not isinstance(lines, list):
        return None
    left: Dict[Optional[int], List[int]] = {}
    for number, line in enumerate(lines):
        if not isinstance(line, dict) or not isinstance(line.get("members"), list):
            return None
        source, at, epoch = line.get("from"), line.get("forked_at"), line.get("epoch")
        if not _integer(epoch) or (source is None) != (at is None):
            return None
        if source is not None and not (
            _integer(source) and _integer(at) and 0 <= source < len(lines) and source != number
        ):
            return None
        left.setdefault(source, []).append(number)
    if len(left.get(None, [])) != 1:
        return None
    rows: List[dict] = []

    def visit(number: int, parent: Optional[int]) -> None:
        line, row = lines[number], len(rows)
        rows.append(
            {
                "line": number,
                "from": line["from"],
                "forked_at": line["forked_at"],
                "epoch": line["epoch"],
                "members": len(line["members"]),
                "row": row,
                "parent": parent,
            }
        )
        for child in sorted(
            left.get(number, []), key=lambda one: (lines[one]["forked_at"], one), reverse=True
        ):
            visit(child, row)

    visit(left[None][0], None)
    if len(rows) != len(lines):
        return None
    fell = [
        (one["name"], one["epoch"])
        for one in _listed(grow.get("fell_behind"))
        if isinstance(one, dict) and isinstance(one.get("name"), str) and _integer(one.get("epoch"))
    ]
    behind, never = _names(grow.get("behind")), _names(grow.get("never_joined"))
    return {"rows": rows, "fell": fell, "behind": behind, "never": never}


def _most(lineages: Sequence[Optional[dict]]) -> int:
    """The most members any line of `lineages` ended with, 0 where none has any."""
    return max((line["members"] for one in lineages if one for line in one["rows"]), default=0)


def _widths(most: int, thin: float, thick: float) -> Callable[[int], float]:
    """How wide a line of so many members is drawn: from `thin`, for none, to `thick`, for `most`,
    by the square root; `thin` for any where `most` is 0."""
    return lambda members: thin + (thick - thin) * math.sqrt(members / most) if most else thin


def _along(left: float, right: float) -> Callable[[float], float]:
    """Where an epoch is on an axis from `left`, epoch 0, to `right`, the goal's, which an epoch
    past the goal stops at."""
    return lambda epoch: left + (right - left) * min(epoch, _TOP) / _TOP


def _tree(
    lineage: dict,
    side: str,
    x: Callable[[float], float],
    top: float,
    pitch: float,
    width: Callable[[int], float],
    dot: float,
    label: Optional[Callable[[dict, float, float], str]] = None,
) -> str:
    """A side's lineage as a tree, its trunk at `top` and its rows `pitch` apart: each line from
    the epoch it left the line above at, down to its row and on to its tip, as wide as `width`
    makes its members, with a dot at its tip of a radius of at least `dot` and what it is as its
    title; with `label`, what `label` writes beside each tip."""
    drawn, labels = [f'<g class="tree {side}">'], []
    for line in reversed(lineage["rows"]):
        y, wide, tip = top + line["row"] * pitch, width(line["members"]), x(line["epoch"])
        path = f"M{x(0):.1f} {y:.1f}H{tip:.1f}"
        if line["parent"] is not None:
            above = top + line["parent"] * pitch
            path = f"M{x(line['forked_at']):.1f} {above:.1f}V{y:.1f}H{tip:.1f}"
        radius = max(dot, wide / 2 + 0.75)
        drawn.append(
            f"<g><title>{_e(_line_said(line))}</title>"
            f'<path d="{path}" stroke-width="{wide:.2f}"/>'
            f'<circle cx="{tip:.1f}" cy="{y:.1f}" r="{radius:.2f}"/></g>'
        )
        if label is not None:
            labels.append(label(line, tip + radius, y))
    return "".join(drawn) + "</g>" + "".join(labels)


def _line_said(line: dict) -> str:
    """A line of a lineage in words, as its title says it: which line it is and which it left, its
    members and its epochs."""
    members = _plural(line["members"], "member")
    if line["parent"] is None:
        return f"trunk: {members}, {_span(line)}"
    left = "the trunk" if line["parent"] == 0 else f"line {line['from']}"
    return f"line {line['line']}, off {left}: {members}, {_span(line)}"


def _span(line: dict) -> str:
    """The epochs a line of a lineage held: from 0 on the trunk, or else from the epoch it left at,
    to its tip's."""
    start = 0 if line["parent"] is None else line["forked_at"]
    return f"epoch {start}" if start == line["epoch"] else f"epochs {start} to {line['epoch']}"


def _sketch(cell: dict, side: str, x: Callable[[float], float], top: float, pitch: float) -> str:
    """A side's run of `cell` whose lineage the site does not keep, sketched from how it came out:
    its trunk dashed to the trunk's epoch, and a mark down from the epoch it first forked at."""
    outcome, epochs = cell[side], cell["epochs"][side]
    trunk = epochs if outcome["trunk_epoch"] is None else outcome["trunk_epoch"]
    said = simulate.came_out(outcome)
    if outcome["lines_off_trunk"]:
        said += f", {_plural(outcome['lines_off_trunk'], 'line')} off the trunk"
    drawn = f'<g class="tree {side} sketch"><title>{_e(said)}: no lineage kept</title>'
    if trunk is not None:
        drawn += f'<path d="M{x(0):.1f} {top:.1f}H{x(trunk):.1f}" stroke-width="1.5"/>'
    if cell["ended"][side] == "split" and epochs is not None:
        drawn += f'<path d="M{x(epochs):.1f} {top:.1f}V{top + pitch:.1f}" stroke-width="1.5"/>'
    return drawn + "</g>"


def _ring(x: float, y: float, count: object) -> str:
    """How many members never joined, `count`, as a ring and the number in a pill, from `x` and
    about `y`."""
    return (
        f'<g class="ring"><title>{_e(str(count))} never joined</title>'
        f'<rect x="{x:.1f}" y="{y - 7:.1f}" width="{_ring_width(count)}" height="14" rx="7"/>'
        f'<circle cx="{x + 7.5:.1f}" cy="{y:.1f}" r="3"/>'
        f'<text x="{x + 14:.1f}" y="{y + 3.5:.1f}">{_e(str(count))}</text></g>'
    )


def _ring_width(count: object) -> int:
    return 20 + 6 * len(str(count))


def _epoch_lines(x: Callable[[float], float], top: float, bottom: float) -> str:
    return "".join(
        f'<line class="epoch" x1="{x(epoch):.1f}" x2="{x(epoch):.1f}" y1="{top}" y2="{bottom}"/>'
        for epoch in range(0, _TOP + 1, 10)
    )


def _epoch_ticks(x: Callable[[float], float], y: float) -> str:
    return "".join(
        f'<text class="axis small" x="{x(epoch):.1f}" y="{y}" text-anchor="middle">{epoch}</text>'
        for epoch in range(0, _TOP + 1, 10)
    )


def _never(cell: dict, side: str, lineage: Optional[dict]) -> Optional[int]:
    """How many members of a side's run of `cell` never joined: as its lineage names them, else as
    its outcome counts them, which one of an earlier measurement does not."""
    if lineage is not None:
        return len(lineage["never"])
    return None if cell[side] is None else cell[side].get("never_joined")


def _unshown(cell: dict, side: str) -> Optional[str]:
    """Why a side's run of `cell` has no tree: a run without an outcome did not build or came to
    none. `None` where it has one."""
    if cell[side] is None:
        return f"{'does not build' if cell['unbuilt'][side] else 'no outcome'} at the {side}"
    return None


def _rows_of(lineage: Optional[dict]) -> int:
    return 1 if lineage is None else len(lineage["rows"])


def _width(text: str, size: float = 11) -> float:
    """About how wide `text` is set at `size` pixels in a sans-serif face, no browser measuring it:
    spaces and narrow letters, wide letters, capitals and the rest each a share of the size, a
    little over Noto Sans's."""
    return size * sum(
        0.3
        if one in " .,:;'·iljftrI"
        else 0.9
        if one in "mwMW"
        else 0.68
        if one.isupper()
        else 0.58
        for one in text
    )


def _side(cell: dict, side: str, run: Path, href: str, inline: bool = True) -> str:
    """A side's run of a cell, its text kept in `run` and linked at `href`: what it came to, its
    lines, who was off the trunk and its rounds as its run.json has them, and its text, linked
    and, with `inline`, shown; or why it has no outcome, with what text it left or the build's
    log of a job that could not build chat-sim."""
    drawn = "<div>\n"
    outcome = cell[side]
    if outcome is None and not cell["unbuilt"][side]:
        drawn += "<p>no outcome</p>\n" + _files(run, href)
        if inline:
            drawn += "".join(_folded(run, name) for name in ("run.txt", "report.txt"))
        return drawn + "</div>\n"
    if outcome is None:
        shown = _shown(run, href, "build.log") if inline else _files(run, href)
        return drawn + "<p>does not build</p>\n" + shown + "</div>\n"
    drawn += f"<p>{_e(simulate.came_out(outcome))} · {_e(outcome['came_to'])}</p>\n"
    if inline:
        drawn += _grown(_run_json(run / "run.json"))
    drawn += _files(run, href)
    if inline:
        drawn += "".join(_folded(run, name) for name in ("run.txt", "report.txt"))
    return drawn + "</div>\n"


def _grown(report: Optional[dict]) -> str:
    """The lines of a run's grow as a table, who fell behind, who was behind at the end and who
    never joined, and its rounds, as `report`, its run.json, has them; nothing where it has no
    lines."""
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
    return drawn + _rounds(report)


def _rounds(report: Optional[dict]) -> str:
    """The rounds of a run's grow as a table, from 1: the names of its adders, of the members
    they added and of the adders refused the whole round, and the virtual seconds it took, as
    `report`, its run.json, has them; nothing where it has no rounds."""
    grow = report.get("grow") if report is not None else None
    rounds = _listed(grow.get("rounds")) if isinstance(grow, dict) else []
    if not rounds or not all(isinstance(held, dict) for held in rounds):
        return ""
    rows = "".join(
        f'<tr><td class="n">{number}</td>'
        + "".join(
            f"<td>{_e(', '.join(_names(held.get(field))) or '-')}</td>"
            for field in ("adders", "joiners", "refused")
        )
        + f'<td class="n">{_settled(held.get("settled_s"))}</td></tr>\n'
        for number, held in enumerate(rounds, 1)
    )
    return (
        '<div class="wide"><table class="rounds">\n'
        '<thead><tr><th class="n">round</th><th>adders</th><th>joiners</th><th>refused</th>'
        f'<th class="n">settled s</th></tr></thead>\n<tbody>\n{rows}</tbody>\n</table></div>\n'
    )


def _settled(value: object) -> str:
    """A round's virtual seconds as run.txt says them, to the millisecond, or `-` for none."""
    timed = isinstance(value, (int, float)) and not isinstance(value, bool)
    return f"{value:.3f}" if timed else "-"


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
        ended = cell["ended"]["head"] or ("-" if outcome else simulate.words(cell))
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
    """How a run of a long run ended: as `simulate.came_out` says it, or why it has no outcome."""
    if run["outcome"] is not None:
        return simulate.came_out(run["outcome"])
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


def _again(cell: dict, revision: str, chat_sim: str) -> str:
    """The commands that run a cell again at `revision` of logos-chat, from chat-sim at
    `chat_sim`, under a line that says so and what it takes."""
    return (
        f'<p class="side">Run it again ({_NEEDS}):</p>\n'
        f"<pre>{_e(chr(10).join(simulate.again(cell, revision, chat_sim)))}</pre>\n"
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


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _plural(count: object, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _loss(cell: dict) -> str:
    """The rate of loss of a cell's runs, as a row of its trees heads it, or its scenario where
    it has none."""
    return cell["scenario"] if cell["rate"] is None else f"{cell['rate']} loss"


def _run_heading(cell: dict) -> str:
    """A cell's run by its group, its rate of loss and its seed, as its page heads it."""
    return f"{simulate.GROUPS[group(cell)]} · {_loss(cell)} · seed {cell['seed']}"


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


def _ends(ends: List[dict], x: float, lowest: float, shown: Callable[[float], str]) -> List[str]:
    """The rate and its newest value at the end of each line, the value as `shown` writes it,
    pushed apart where two meet."""
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
            f"{_e(one['name'])} {shown(one['value'])}</text>"
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
        if _whole_share(now) != _whole_share(was):
            counts = [_number(_whole(one)) for one in (was, now)]
            if now["runs"] != was["runs"]:
                counts = [f"{count} of {one['runs']}" for count, one in zip(counts, (was, now))]
            moved.append(f"whole {counts[0]} to {counts[1]}")
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


def _whole_share(one: dict) -> Optional[float]:
    """The share of a rate's runs that ended whole in percent, or `None` where none had an
    outcome."""
    whole = _whole(one)
    return None if whole is None else 100 * whole / one["runs"]


def _percent(value: float) -> str:
    return f"{value:.0f}%"


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
