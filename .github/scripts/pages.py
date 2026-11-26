#!/usr/bin/env python3
"""Writes the repository's Pages site from the text CI's sim jobs upload, and keeps on it what
simulate.yml's runs of logos-chat came to.

`report` writes the report of one CI run to `reports/KEY/` of the site, replacing what was
there unless that is the report of a run of a higher number: KEY is `master` for a push to
master and `pr-N` for pull request N. `index` writes the site's landing page, and
`reports/index.html` from the `meta.json` of each report; one it cannot read is left out, with
a warning.

`point` keeps a point of logos-chat's main, a run of simulate.yml's main suite: its
`point.json`, drawn from the run's matrix and its runs' lines, its runs' text, its page and a
page for each run, in `logos-chat/main/<sha>/<measurement>/`. What was there is replaced unless
a run of a higher number wrote it, and then only the newest eleven points keep their runs' text
and their runs' pages. A point from before a point.json held its runs' lines is given them
while it keeps its text. Every other point's page is then drawn again from its point.json, so
that each is as this script draws it. The points are in the order of their
runs' numbers, which GitHub gives in the order the runs were started and a re-run keeps;
`previous` names the point a new one follows. `pr` keeps the newest run of a logos-chat pull
request in `logos-chat/pr-N/` the same way: its matrix, both sides' runs' text, its `meta.json`,
the scenario files it ran, its report page and a page for each run. `long` keeps a run of the long
suite at a commit of main, held to what is expected, in `logos-chat/long/<sha>/<measurement>/` the
same way: its `long.json`, each scenario's compare output, its runs' text and its page; the newest
fifteen long runs keep their text. `trend` draws the graph over main, `logos-chat/index.html`, from
the `point.json` of every point, and links the newest long run; one it cannot read is left out,
with a warning. With it `trend` draws the page of each commit whose runs moved against the commit
before it, `change/` in its point's directory, and the list of the points from before the test
the graph draws, `logos-chat/earlier/`. `closed` names the runs of logos-chat's pull requests that
are no longer open.

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
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import quote

import simulate

# tools/sim's matrix gives a pull request's runs their verdict beside its base's, and the page over
# main has it give a commit's runs theirs beside those of the commit before.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "sim"))
from sim import compare as sim_compare  # noqa: E402
from sim import matrix as sim_matrix  # noqa: E402

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
# The artifacts that hold the logs of a scenario's runs at a side of a workflow run, as
# sweep.yml names each job's, by the glob `gh run download` takes. The site keeps no log.
LOGS = "logs-{side}-{scenario}-[0-9]*"
# How many days GitHub keeps them, as sweep.yml asks of it.
LOGS_KEPT = 90
# Where the guide says what a run's log holds, from the site's root.
_INTO_THE_LOG = "guide/investigate/moved-run/#into-the-log"
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
# The repositories whose commits and pull requests a point names.
LOGOS_CHAT_URL = "https://github.com/logos-messaging/logos-chat"
CHAT_SIM_URL = "https://github.com/logos-messaging/chat-sim"

_KEY = re.compile(r"master|pr-([1-9][0-9]*)")
_SHA = re.compile(r"[0-9a-f]{40}")
_URL = re.compile(r"https?://[^\s\"'<>]+")
# A workflow run's address: its repository and its number.
_WORKFLOW_RUN = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/actions/runs/([0-9]+)")
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
# How a small tree is read, as a page says it first under its trees.
_READING = (
    f"A tree is one run, left to right by epoch from 0 to {_TOP}, the goal. The top line is the "
    "trunk. Each line below left the line it drops from at that epoch and stops at its dot. The "
    "thicker a line, the more members it ended with. The ring counts the members that never "
    "joined."
)
# What a dashed tree is, where a page draws one.
_SKETCHED = (
    " A dashed tree is of a run whose lines the site does not keep: its trunk, and a mark where "
    "it first forked."
)
# What a run's large tree counts as seated, under it.
_SEATED = (
    '<p class="reading">seated counts every member added, the creator included, and not '
    "removed: those on a line and those that never joined.</p>\n"
)
_NONE_MERGED = '<span class="muted">none</span>'
# The room a label of the axis of commits takes in a panel of the graph over main.
_ROOM = 44

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
    "lines": (dict,),
}
# A point need not hold its runs' lines: the points that do not are from before a point kept them.
_UNLINED_POINT = {field: kind for field, kind in _POINT.items() if field != "lines"}
# What a point keeps of a line of a run: the line it left and the epoch it left at, neither of
# the trunk, its tip's epoch, and how many members it ended with.
_LINE = {"from", "forked_at", "epoch", "members"}
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
    "rate-2": ("#3987e5", "#6da7ec"),
    "v2": ("#2a78d6", "#3987e5"),
    "v3": ("#eb6834", "#d95926"),
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
# The charts, in the colours of `_STYLE`.
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
.leader{stroke:var(--line)}
.hit{fill:none;pointer-events:all}
.series{fill:none;stroke:var(--hue);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.marker,.key{fill:var(--hue)}
.marker{stroke:var(--bg);stroke-width:2}
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
"""
# The graph over main, the page of a change and the list of the earlier points, beside the charts'
# classes: a group's marks are in its hue, and a result's word is always beside its colour.
_OVER = """
.v2{--hue:var(--v2)}
.v3{--hue:var(--v3)}
.now{margin:.75rem 0 0}
.now th{font-size:13px;color:var(--muted)}
.now th,.now td{padding:.5rem 2.5rem .5rem 0}
.now td b{display:block;font-size:28px;line-height:1.15;font-variant-numeric:tabular-nums}
.now td small{display:block;font-size:12px;color:var(--muted)}
.now .name{font-weight:600;white-space:nowrap;vertical-align:middle}
.dot{display:inline-block;width:10px;height:10px;margin-right:8px;border-radius:50%;
background:var(--hue)}
.typical{display:block;width:150px;margin:5px 0 3px;border-radius:4px}
.typical svg{display:block}
a.typical:hover{background:var(--box)}
.since{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:1rem 0 0}
.trend .band{fill:var(--hue);opacity:.14}
.trend .goal{stroke:var(--muted);stroke-width:1.5;stroke-dasharray:5 4}
.wash{fill:var(--muted);opacity:.18}
.any{fill:var(--muted)}
.results{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px;max-width:52rem;
margin:.5rem 0 1rem;font-size:13px;color:var(--muted)}
.changes td{font-variant-numeric:tabular-nums}
.changes .when,.changes .pr a,.changes td:last-child{white-space:nowrap}
.changes .pr{min-width:12rem}
.changes .quiet td{color:var(--muted)}
.changes .rates{display:grid;grid-template-columns:repeat(3,max-content);align-items:center;
gap:2px 12px;margin:6px 0 4px;font-size:13px}
.tally{display:inline-flex;align-items:center;gap:6px;color:var(--muted)}
.tally .earlier{fill:var(--bad)}
.tally .tied{fill:var(--line)}
.tally .later{fill:var(--good)}
details .forks{margin:.5rem 0 1rem}
.pairs{font-size:14px}
.pairs th,.pairs td{padding:.15rem 1.25rem .15rem 0;white-space:nowrap}
.dumb .before{fill:var(--bg);stroke:var(--hue);stroke-width:2}
.dumb .after{fill:var(--hue)}
.dumb .link{stroke:var(--hue);stroke-width:2;stroke-linecap:round;opacity:.45}
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
.reading{max-width:52rem;color:var(--muted)}
"""
# The trees of a run: a pull request's head in the blue of the rates and its base muted, a run of
# main, under its group's class, in the group's hue; a sketch dashed, with the ring of the members
# that never joined.
_TREES = """
.tree path{fill:none;stroke-linejoin:round;stroke-linecap:round}
.tree.head path{stroke:var(--rate-2)}
.tree.head circle{fill:var(--rate-2)}
.v2 .tree.head path{stroke:var(--v2)}
.v2 .tree.head circle{fill:var(--v2)}
.v3 .tree.head path{stroke:var(--v3)}
.v3 .tree.head circle{fill:var(--v3)}
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
# The runs of a point of main as trees, beside `_TREES`: a column a rate and a tile a run.
_FOREST = """
.of{margin:0 0 4px;font-size:13px}
.of span{margin-left:.4rem;font-size:12px;color:var(--muted)}
.chart .ticks{margin:0 0 2px 85px}
.forest,.row{display:flex;flex-wrap:wrap;align-items:flex-start;gap:0 16px;margin:0 0 12px}
.forest>div{width:362px}
.tile{box-sizing:border-box;display:flex;align-items:center;gap:6px;width:362px;margin:0 0 4px;
padding:2px 6px;border:1px solid var(--line);border-radius:6px;color:var(--fg);
text-decoration:none}
a.tile:hover{border-color:var(--link)}
.tile .at{flex:none;width:72px;font-size:11px;line-height:1.3;color:var(--muted);
white-space:nowrap}
.tile .at b{color:var(--fg)}
.chart .tile svg{max-width:none}
.median{display:flex;align-items:center;gap:8px;margin:1px 0 5px;font-size:11px;
color:var(--muted);white-space:nowrap}
.median::before,.median::after{content:"";flex:1;border-top:1px dashed var(--muted)}
"""
# The report page of a pull request's run and the page of each of its runs, beside `_TREES`: a
# change's word always beside its colour.
_GRID = """
.verdicts{margin:1rem 0 0;padding:0;list-style:none;font-size:16px}
.verdicts li{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 6px}
.verdicts li>b{min-width:5rem}
.verdicts .muted{flex:1 1 20rem;font-size:14px}
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
.forks>details{margin:0}
.forks>details>summary{margin-left:126px;font-size:12px;color:var(--link)}
.forks>details>div{display:flex;align-items:flex-start;gap:10px;margin-top:10px}
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
a.fork:hover{border-color:var(--link)}
.fork a:hover .hit{fill:var(--link);fill-opacity:.08}
.fork.moved{border-color:var(--fg);box-shadow:0 0 0 1px var(--fg)}
.fork.unbuilt{border-color:var(--none);border-style:dashed}
.said{display:flex;align-items:center;gap:6px;height:20px;margin:0 0 2px;font-size:12px;
color:var(--muted);white-space:nowrap}
.said b{color:var(--fg)}
.fork .said{max-width:306px}
.fork .said>span:not(.chip){min-width:0;overflow:hidden;text-overflow:ellipsis}
.rate .said{font-size:13px}
.outlined{padding:0 6px;border:1px solid var(--fg);border-radius:4px;
box-shadow:0 0 0 1px var(--fg)}
.ran{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:.75rem 0 .25rem;
font-size:16px}
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
        "logos-chat/main/SHA/MEASUREMENT/ of SITE: point.json, which holds the matrix's cells "
        "and each run's lines, each run's run.txt, run.json and report.txt where it has one, or "
        "build.log of a job that could not build chat-sim, with the run's page beside them, "
        "and the point's page, index.html. What was there is replaced, unless it is the point "
        "of a run of a higher number. Then gives each point that holds no lines those of the "
        "runs it keeps a run.json of, removes the runs' text and pages of each point past the "
        f"newest {WINDOW}, by run number, and draws every other point's page again. A matrix "
        "that read no run at the revision, built or not, is refused.",
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
        "left out, with a warning. Writes logos-chat/earlier/index.html too, the list of the "
        "points from before the test the page draws, and the page of each change it links.",
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
    point's page. The point.json holds each run's lines, as `_lines` reads them, so that the
    run's tree outlasts its text.

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
    point["lines"] = _lines(out, matrix["cells"])
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
    cells as a heatmap a group, how the runs of each group's rate ended, and each run as its
    tree, from the lines `point` keeps. While `directory` keeps the runs' text, each tree links
    its run's page, which this writes too; once it does not, each rate has the commands that run
    a run of it again and that download its logs. Where chat-sim did not build, the build's log,
    once. Every file it links is one `directory` holds."""
    logos_chat, measured, before = point["logos_chat"], point["measurement"], point["previous"]
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
            f'<p class="muted">The site keeps the runs\' text and pages of the newest {WINDOW} '
            "points, and no longer this one's: the commands under its runs make one again.</p>\n"
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
    body += (
        _rates_table(directory, point)
        + '<section class="chart" id="runs">\n<h2>The runs</h2>\n'
        + _forest(point, kept)
        + ("" if kept else _reruns(point))
        + "</section>\n"
    )
    shows = _link("../../../../guide/track/main/#the-page-of-a-point", "The page of a point")
    body += f'<p class="muted">What the page shows: the guide\'s {shows}.</p>\n'
    title = f"chat-sim: logos-chat main {logos_chat[:7]}"
    style = _CHARTS + _REPORT + _TREES + _FOREST
    (directory / "index.html").write_text(_page(title, body, style), encoding="utf-8")
    if kept:
        for cell in point["cells"]:
            draw_point_run(directory, point, cell)


def draw_point_run(directory: Path, point: dict, cell: dict) -> None:
    """Writes the page of `cell`'s run of the point in `directory`, index.html beside the run's
    text: how the run came out, in a sentence; its tree large in its group's hue, each line
    labelled, with who fell behind and its members, from its run.json; its rounds; what running
    it again takes and the commands that do; its text; and the command that downloads its log."""
    sha, run = point["logos_chat"], _run_dir(directory, cell)
    report = _run_json(run / "run.json")
    named, up = _job(cell), "../../"
    if cell["head"] is not None:
        said = f"The run {_forked(cell, 'head')}."
    elif cell["unbuilt"]["head"]:
        said = "chat-sim does not build at this commit, so nothing ran."
    else:
        said = "The run came to no outcome."
    wall = _wall(report)
    body = (
        f'<p class="muted">{_link(up + "../../../../", "chat-sim")} / '
        f'{_link(up + "../../../", "Over main")} / {_link(up, sha[:7], code=True)} / '
        f"{_e(named)}</p>\n"
        f"<h1>{_e(_run_heading(cell))}</h1>\n"
        f'<p class="ran"><span>{_e(said)}</span></p>\n'
        f'<p class="facts">main {_commit(LOGOS_CHAT_URL, sha)} · chat-sim '
        f"{_commit(CHAT_SIM_URL, point['chat_sim'])} · the measurement "
        f"{_commit(CHAT_SIM_URL, point['measurement'])} · {_link(point['run_url'], 'the run')} · "
        f"{_when(point)} UTC"
        + ("" if wall is None else f" · wall time {_seconds(wall)}")
        + "</p>\n"
    )
    if cell["head"] is not None:
        large = _run_trees(cell, [("head", "main", sha, _lineage(report))])
        body += f'<div class="chart wide {group(cell)}">\n{large}</div>\n' + _SEATED
    body += (
        _rounds_said(_rounds(report))
        + "<h2>Run it yourself</h2>\n"
        f'<p class="reading">It needs {_NEEDS}.</p>\n'
        f"<pre>{_e(chr(10).join(simulate.again(cell, sha, point['chat_sim'])))}</pre>\n"
        "<h2>The text</h2>\n"
        + (_files(run, "") or '<p class="muted">The run left no text.</p>\n')
    )
    # A job that could not build chat-sim uploaded no log.
    uploaded = [] if cell["unbuilt"]["head"] else ["revision"]
    body += _log_said(point["run_url"], cell, uploaded, "its scenario's", up + "../../../../")
    run.mkdir(parents=True, exist_ok=True)
    title = f"chat-sim: logos-chat main {sha[:7]}, {named}"
    style = _CHARTS + _REPORT + _TREES + _GRID
    (run / "index.html").write_text(_page(title, body, style), encoding="utf-8")


def draw_pr(out: Path, matrix: dict, meta: dict) -> None:
    """Writes the report page of the run of a logos-chat pull request kept in `out`, its index.html,
    from `matrix` and `meta`, which `out` holds, and the runs' text it keeps, and the page of each
    run: what each group's runs came to against the base's, a line a group; what the scenarios test
    and how, from the copies of their files `out` keeps; then of each group each scenario's file,
    linked to its copy where `out` keeps one, and the tree of each run of seeds 0 to 2, a row a rate
    and a column a seed, the head's over the base's, each linked to the run's page, under a rate
    that ran more seeds a strip of them and a fold of the trees of the others. Where chat-sim did
    not build at the head, the build's log takes the place of the scenarios and the runs."""
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
        most = _most([tree for both in drawn.values() for tree in both])
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
                "linked to the run; under them, what the seeds came to. The fold under a strip "
                "draws the rate's other seeds as the row above draws its three."
            )
        body += (
            f'<p class="reading">{_READING} Hover a line for its numbers, or open a run for its '
            f"labels and the command that runs it again.{strips}</p>\n</section>\n"
        )
        for cell in matrix["cells"]:
            draw_run(out, matrix, meta, cell, drawn[_run_href(cell)])
    body += (
        f'<p class="muted">What the page shows: the guide\'s '
        f'{_link("../../guide/track/pull-request/#the-report-page", "The report page")}.</p>\n'
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
    the commands that run it again at each side; each side's text the site keeps; and the
    command that downloads each side's log."""
    head, base = meta["revision"], meta["base"]
    named = _job(cell)
    chip, said = _run_said(matrix, cell)
    up = "../../../"
    large = [("head", "head", head, trees[0])]
    if matrix["base"] is not None:
        large.append(("base", "base", base, trees[1]))
    body = (
        f'<p class="muted">{_link(up + "../../", "chat-sim")} / '
        f'{_link(up + "../", "logos-chat")} / {_link(up, "#" + str(meta["pr"]))} / '
        f"{_e(named)}</p>\n"
        f"<h1>{_e(_run_heading(cell))}</h1>\n"
        f'<p class="ran">{chip} <span>{_e(said)}</span></p>\n'
        f'<p class="facts">head {_commit(LOGOS_CHAT_URL, head)} · '
        + ("no base" if base is None else f"merge base {_commit(LOGOS_CHAT_URL, base)}")
        + f" · chat-sim {_commit(CHAT_SIM_URL, meta['chat_sim'])}</p>\n"
        f'<div class="chart wide">\n{_run_trees(cell, large)}</div>\n' + _SEATED
    )
    run, based = out / simulate.run_page(cell), _base_run(out, matrix, cell)
    sides = [("head", head, run)] + ([] if based is None else [("base", base, based[0])])
    rounds = ""
    for side, sha, at in sides:
        table = _rounds(_run_json(at / "run.json"))
        if table:
            rounds += f'<p class="side">the {side} {_code(sha)}</p>\n{table}'
    apart = " Run each block from a directory of its own, since each clones chat-sim."
    body += (
        _rounds_said(rounds)
        + "<h2>Run it yourself</h2>\n"
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
    # A job that could not build chat-sim uploaded no log.
    built = [side for side, _, _ in sides if not cell["unbuilt"][side]]
    uploaded = ["revision" if side == "head" else side for side in built]
    whose = "both sides'" if len(built) == 2 else f"the {built[0]}'s" if built else ""
    body += _log_said(meta["run_url"], cell, uploaded, whose, up + "../../")
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
    guide = _link("../../guide/understand/scenarios/#a-group-that-grows", "A group that grows")
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
        f'{_link("../../../../guide/track/main/#the-long-runs", "The long runs")}.</p>\n'
    )
    title = f"chat-sim: logos-chat main {logos_chat[:7]}, the long suite"
    (directory / "index.html").write_text(_page(title, body, _CHARTS + _REPORT), encoding="utf-8")


def write_trend(site: Path) -> None:
    """Writes `logos-chat/index.html` of `site`, the graph over logos-chat's main, from each point
    `points` reads: of the commits `history` gives, the newest's epochs before the first fork of
    each group at each rate of loss with the tree of the rate's run nearest it, the same over the
    commits as a panel a rate, and what each commit changed against the commit before it, the
    newest first. Under them it links the points
    before, which it lists in `logos-chat/earlier/index.html`, and the long run of the highest
    run number. It writes the page of each commit's change that moved a run, as `draw_change`
    does, and removes the page of any other change.

    Every number it draws is one a point.json holds, or the median or a quartile of some.
    """
    found = [point for _, point in reversed(points(site))]
    commits, earlier = history(found)
    ran = [commit for commit in commits if commit["point"] is not None]
    body = (
        f'<p class="muted">{_link("../", "chat-sim")} / {_link("../guide/", "Guide")}</p>\n'
        "<h1>Over main</h1>\n"
        f'<p class="lead">{_e(_TESTS)} <b>The goal is {_TOP}.</b></p>\n'
    )
    changes = [draw_change(site, commit) for commit in ran if _has_change(commit)]
    for page in (site / LOGOS_CHAT / "main").glob("*/*/change/index.html"):
        if page not in changes:
            page.unlink()
            page.parent.rmdir()
    if ran:
        body += _now(site, commits, ran) + _over_commits(ran) + _what_changed(commits)
    else:
        body += "<p>No point of main has had an outcome yet.</p>\n"
    body += _foot(site, commits, earlier)
    draw_earlier(site, commits, earlier)
    out = site / LOGOS_CHAT / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    style = _CHARTS + _REPORT + _TREES + _GRID + _OVER
    out.write_text(_page("chat-sim: over main", body, style), encoding="utf-8")


def draw_change(site: Path, commit: dict) -> Path:
    """Writes the page of the change of `commit`, one of `history` with a step, to `change/` in
    the directory of the step's point, and returns it: what each group's runs came to against
    those of the commit before it, as a pull request's page says it; of each group a run of
    which moved, a chart a rate of each seed's epochs before the first fork at both commits; and
    of each seed its two runs as trees, from the lines their points keep, each a link to its
    run's page where the site has one, the round the two part in and each run's text, while the
    site keeps it, with the commands that download both runs' logs."""
    step = commit["step"]
    now, was, matrix = step["now"], step["was"], step["matrix"]
    out = _point_at(site, now["logos_chat"], now["measurement"])
    before = _point_at(site, was["logos_chat"], was["measurement"])
    up, there = "../../../", f"../../../{was['logos_chat']}/{was['measurement']}/"
    title = _merged_links(commit) or f"main {_code(now['logos_chat'])}"
    body = (
        f'<p class="muted">{_link(up + "../../", "chat-sim")} / '
        f'{_link(up + "../", "Over main")} / {_code(now["logos_chat"])}</p>\n'
        f"<h1>{title}</h1>\n"
        f'<p class="facts">main {_commit(LOGOS_CHAT_URL, now["logos_chat"])} against the commit '
        f"before it, {_commit(LOGOS_CHAT_URL, was['logos_chat'])} · chat-sim "
        f"{_commit(CHAT_SIM_URL, now['measurement'])} at both · "
        f"{_link(now['run_url'], 'the run')} · {_when(now)} UTC</p>\n"
        + _verdicts(matrix)
        + '<p class="reading">A seed is compared where it has an outcome at both commits, and '
        "the base is the commit before. Tied says its first fork came at the same epoch at "
        "both, in two runs that came out differently.</p>\n"
    )
    ran = {(_rate(cell), cell["seed"]): cell for cell in now["cells"]}
    based = {(_rate(cell), cell["seed"]): cell for cell in was["cells"]}
    # Of each seed compared, its run after and its run before: the lineage and the page of each.
    trees = {
        pair: (
            (_kept(now, ran[pair]), _run_page(out, ran[pair], "../")),
            (_kept(was, based[pair]), _run_page(before, based[pair], there)),
        )
        for pair in (
            (_rate(cell), cell["seed"]) for cell in matrix["cells"] if cell["change"] is not None
        )
    }
    width = _widths(_most([lineage for both in trees.values() for lineage, _ in both]), 1.2, 4.6)
    for key, called in simulate.GROUPS.items():
        judged = _judged(matrix, key)
        if judged is None or not judged["counts"]["moved"]:
            continue
        rows: Dict[str, List[dict]] = {}
        for cell in matrix["cells"]:
            if group(cell) == key and cell["change"] is not None:
                rows.setdefault(cell["scenario"], []).append(cell)
        charts, pairs, still, opened = "", "", [], False
        for cells in rows.values():
            said = cells[0]["rate"] or cells[0]["scenario"]
            moved = any(cell["d"] for cell in cells)
            if moved:
                charts += _dumbbells(cells, key, f"{called} at {said}", said)
            else:
                still.append(said)
            pairs += (
                f"<details{' open' if moved and not opened else ''}>\n<summary>{_e(said)} "
                f'<span class="muted">{_plural(len(cells), "seed")}</span></summary>\n'
                + _pair_trees(cells, trees, width)
                + _pairs(cells, ran, based, out, before)
                + _pair_logs(cells[0], ran, based, now["run_url"], was["run_url"])
                + "</details>\n"
            )
            opened = opened or moved
        unmoved = f" At {_e(' and '.join(still))} no seed's first fork moved." if still else ""
        drawn = [one for pair, both in trees.items() if pair[0][0] == key for one in both]
        tiles = (
            "A tile is one seed, from the seed whose first fork moved earliest to the one whose "
            "moved latest: the tree of its run after over the tree of its run before, and over "
            f"them the epochs before its first fork at both. {_READING}"
        )
        if any(page is not None for _, page in drawn):
            tiles += " A tree opens its run's page, while the site keeps one."
        if any(lineage is None for lineage, _ in drawn):
            tiles += _SKETCHED
        body += (
            f'<section class="chart {key}" id="{key}">\n<h2>{_e(called)}, seed by seed</h2>\n'
            + _legend(
                (_before_key(key), f"before, at {was['logos_chat'][:7]}"),
                (_after_key(key), f"after, at {now['logos_chat'][:7]}"),
            )
            + f'<div class="panels">\n{charts}</div>\n'
            f'<p class="caption">Epochs before the first fork, a row a seed, from the seed whose '
            "first fork moved earliest to the one whose moved latest. Further right is later, "
            f"which is better. A dot alone is a tied seed.{unmoved}</p>\n"
            f"<h3>Open a pair</h3>\n{pairs}"
            f'<p class="caption">{tiles}</p>\n'
            '<p class="caption">A round is one step of the test: the members the seed draws '
            "each add one. The two runs of a seed have the same rounds up to the one named. A "
            "run's rounds and its outcome are two records of it: two runs of the same outcome "
            "can part in a round, and two that differ can part in none.</p>\n"
            f'<p class="caption">The site keeps the runs\' text and pages of the newest '
            f"{WINDOW} points and no log. GitHub keeps a run's logs {LOGS_KEPT} days. Where a "
            "command finds none, the commands that run a run again are on the run's page, or on "
            f"its point's where the run has none: {_link('../', 'the runs after')}, "
            f"{_link(there, 'the runs before')}. What to read in a log is in the guide's "
            f"{_link(up + '../../' + _INTO_THE_LOG, 'Into the log')}.</p>\n"
            "</section>\n"
        )
    body += (
        f'<p class="muted">What the page shows: the guide\'s '
        f'{_link(up + "../../guide/track/main/#the-page-of-a-change", "The page of a change")}.'
        "</p>\n"
    )
    page = out / "change" / "index.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    named = f"chat-sim: logos-chat main {now['logos_chat'][:7]} against {was['logos_chat'][:7]}"
    style = _CHARTS + _REPORT + _TREES + _GRID + _OVER
    page.write_text(_page(named, body, style), encoding="utf-8")
    return page


def draw_earlier(site: Path, commits: Sequence[dict], earlier: Sequence[dict]) -> None:
    """Writes `logos-chat/earlier/index.html` of `site`, the list of the points `earlier`, those
    before the test of `commits`, which the graph over main draws, the newest first; removes it
    where there is none."""
    out = site / LOGOS_CHAT / "earlier" / "index.html"
    if not earlier:
        out.unlink(missing_ok=True)
        return
    rows = ""
    for point in reversed(earlier):
        logos_chat, measured = point["logos_chat"], point["measurement"]
        merged = "<br>".join(
            _link(f"{LOGOS_CHAT_URL}/pull/{one['number']}", f"#{one['number']}")
            + f" {_e(one['title'])}"
            for one in point["merged"]
        )
        ran = sum(cell["head"] is not None for cell in point["cells"])
        rows += (
            f'<tr><td class="when">{_when(point)}</td>'
            f"<td>{_commit(LOGOS_CHAT_URL, logos_chat)}</td>"
            f'<td class="pr">{merged or _NONE_MERGED}</td>'
            f"<td>{_commit(CHAT_SIM_URL, measured)}</td>"
            f'<td class="n">{ran} of {len(point["cells"])}</td>'
            f"<td>{_link(f'../main/{logos_chat}/{measured}/', 'point')} · "
            f"{_link(point['run_url'], 'the run')}</td></tr>\n"
        )
    why = (
        "chat-sim changed the test since, so their runs do not compare seed by seed with the "
        "runs there."
    )
    if not _cut_by_chat_sim(commits, earlier):
        why = (
            "chat-sim and main changed together after the newest of them, so the page cannot "
            "tell which of the two changed the runs, and does not compare them with the runs "
            "there."
        )
    body = (
        f'<p class="muted">{_link("../../", "chat-sim")} / {_link("../", "Over main")}</p>\n'
        "<h1>The earlier points</h1>\n"
        f'<p class="lead">The points of main from before the test {_link("../", "Over main")} '
        f"draws. {why} Each keeps its page.</p>\n"
        '<div class="wide"><table class="changes">\n'
        "<thead><tr><th>Measured (UTC)</th><th>Commit</th><th>Pull requests merged</th>"
        '<th>chat-sim</th><th class="n">Runs with an outcome</th><th></th></tr></thead>\n'
        f"<tbody>\n{rows}</tbody>\n</table></div>\n"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    style = _CHARTS + _REPORT + _OVER
    out.write_text(_page("chat-sim: the earlier points", body, style), encoding="utf-8")


def history(found: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    """The commits of main the graph over main draws, the oldest first, and the points before
    them, of `found`, the points with the oldest first; neither where no point has an outcome.

    The commits are those of one test: back from the newest point with an outcome, each point
    that `_continues` into the point after it, and ahead of the first of them each point with no
    outcome at its measurement. Where every point with an outcome is of the test, so is every
    point. A seed whose first fork moved between two measurements is taken as the test having
    changed, and the points before as another test's.

    A commit holds its `logos_chat`, its `points`, the `point` the page draws of it, the newest
    with an outcome, `None` where none has one, the pull requests `merged` by each point at it,
    those before the test among them, and its `step`: the two points of it and of the commit
    before it that are at one measurement, as `now` and `was`, and the `matrix` of the first's
    runs beside the second's, as `compared` gives it. The step is `None` for the first commit,
    for one with no outcome, for one no measurement of which the commit before ran, and for one
    `compared` cannot read.
    """
    measured = [number for number, point in enumerate(found) if _unforked(point)]
    if not measured:
        return [], []
    while len(measured) > 1 and _continues(found[measured[-2]], found[measured[-1]]):
        measured.pop()
    first = measured[-1] if len(measured) > 1 else 0
    while first and not _unforked(found[first - 1]) and _at_one(found[first - 1], found[first]):
        first -= 1
    commits: List[dict] = []
    for point in found[first:]:
        if not commits or commits[-1]["logos_chat"] != point["logos_chat"]:
            commits.append({"logos_chat": point["logos_chat"], "points": []})
        commits[-1]["points"].append(point)
    named = _named(next(point for point in reversed(found) if _unforked(point)))
    before: List[dict] = []
    for commit in commits:
        ran = [point for point in commit["points"] if _unforked(point)]
        merged: Dict[int, dict] = {}
        for point in found:
            if point["logos_chat"] == commit["logos_chat"]:
                for one in point["merged"]:
                    merged.setdefault(one["number"], one)
        commit.update(point=ran[-1] if ran else None, merged=list(merged.values()), step=None)
        if ran and before:
            commit["step"] = _step(ran, before, named)
        before = ran or before
    return commits, list(found[:first])


def compared(now: dict, was: dict, named: Dict[object, str]) -> dict:
    """The matrix `tools/sim matrix` gives a pull request's runs beside its base's, of the runs
    of the point `now` beside those of the point `was`, so that a commit of main is tested as a
    pull request is. A cell goes under the scenario `named` gives its rate, and one of a rate
    `named` does not hold is left out, as is a run of `was` that `now` did not run. Refused where
    tools/sim cannot read a scenario's file."""
    (head, asked), (base, _) = _as_side(now, named), _as_side(was, named)
    ran = {(named[_rate(cell)], cell["seed"]) for cell in now["cells"] if _rate(cell) in named}
    base = sim_matrix.Side({key: run for key, run in base.runs.items() if key in ran})
    try:
        return sim_matrix.matrix(head, base, asked)
    except sim_compare.Unreadable as refused:
        raise Unreadable(str(refused)) from None


def since(commits: Sequence[dict]) -> Dict[str, Tuple[str, str]]:
    """What each group's runs at the newest commit of `commits` came to against those at the
    first, by the group, as `_standing` gives it: `same` where each commit after the first has a
    step that compared every run of the group and changed none, else what the newest's runs came
    to beside the first's, which pairs the two commits' epochs by seed. Nothing with fewer than
    two commits that ran, or where `compared` cannot read them."""
    ran = [commit for commit in commits if commit["point"] is not None]
    if len(ran) < 2:
        return {}
    try:
        matrix = compared(ran[-1]["point"], ran[0]["point"], _named(ran[-1]["point"]))
    except Unreadable as refused:
        print(f"::warning::{refused}", file=sys.stderr)
        return {}
    steps = [commit["step"] for commit in ran[1:]]
    found = {}
    for one in matrix["groups"]:
        held = [None if step is None else _judged(step["matrix"], group(one)) for step in steps]
        unchanged = all(its is not None and _standing(its) == ("same", "") for its in held)
        found[group(one)] = ("same", "") if unchanged else _standing(one)
    return found


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
    number, its runs' with the page of each and a long run's compare output, each
    `<scenario>/<run>/` and `<scenario>/` it empties with it, and returns the directories it
    removed text from. Each keeps its point.json or long.json and every other file."""
    pruned = []
    for directory, _ in sorted(found, key=_newest, reverse=True)[window:]:
        texts = [
            path
            for name in (*TEXTS, "index.html")
            for path in directory.glob(f"*/*/{name}")
            if path.is_file()
        ]
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


def keep_lines(directory: Path, point: dict) -> dict:
    """Writes into the point.json in `directory` its runs' lines, as `_lines` reads them, where
    `point`, what it holds, has none and `directory` still keeps a run.json to read them from,
    and returns the point as it then is: a point from before a point kept them holds them once
    its runs' text goes."""
    if "lines" in point:
        return point
    lines = _lines(directory, point["cells"])
    if not lines:
        return point
    point = {**point, "lines": lines}
    (directory / "point.json").write_text(json.dumps(point, indent=1) + "\n", encoding="utf-8")
    return point


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
        _shaped(point, _held_point(point))
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
        runs = {_run_href(cell) for cell in point["cells"]}
        for run, lines in point.get("lines", {}).items():
            if run not in runs:
                raise Unreadable(f"lines[{run!r}] is of no run of its cells")
            if _rows(lines) is None:
                raise Unreadable(f"lines[{run!r}] is not the lines of a run, down from one trunk")
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
    # Before the text goes, so that a point leaving the window leaves with its lines.
    kept = [(out, point), *((at, keep_lines(at, one)) for at, one in found if at != out)]
    pruned = prune(kept)
    for directory in pruned:
        print(
            f"Removed the runs' text of {directory.relative_to(args.site)}/: past the newest "
            f"{WINDOW} points."
        )
    # Every other point's page again, and this one's where its text went at once: a page an
    # earlier chat-sim drew then takes the shape this one draws, and its runs get their pages.
    for directory, one in kept:
        if directory != out or directory in pruned:
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
    """Each run's text file under `runs`, at any depth, and where it goes under `out`:
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


def _held_point(point: object) -> dict:
    """The shape of a point.json: one from before the points held their runs' lines has none."""
    return _POINT if not isinstance(point, dict) or "lines" in point else _UNLINED_POINT


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


def _rate(held: dict) -> object:
    """The rate a summary or a cell is of, within its group: its loss, so that a scenario renamed
    at the same loss goes on as the same line, or its scenario where it has none."""
    return group(held), (held["scenario"] if held["loss"] is None else held["loss"])


def _now(site: Path, commits: Sequence[dict], ran: Sequence[dict]) -> str:
    """The first section: of the newest commit that ran, each group's epochs before the first
    fork at each rate of loss, as a number over the tree of the rate's run nearest it, from the
    lines the point keeps and a link to the run's page where `site` has one, and what its runs
    came to against the first commit's."""
    newest, point = ran[-1], ran[-1]["point"]
    spreads, losses = _spreads(point), _losses(point)
    at = f"main/{point['logos_chat']}/{point['measurement']}/"
    directory = _point_at(site, point["logos_chat"], point["measurement"])
    measured = [cell for cell in point["cells"] if cell["epochs"]["head"] is not None]
    typical = {
        rate: min(
            (cell for cell in measured if _rate(cell) == rate),
            key=lambda cell: (abs(cell["epochs"]["head"] - one["median"]), cell["seed"]),
        )
        for rate, one in spreads.items()
    }
    trees = {
        rate: (_kept(point, cell), _run_page(directory, cell, at))
        for rate, cell in typical.items()
    }
    width = _widths(_most([lineage for lineage, _ in trees.values()]), 1.2, 3.4)
    rows = ""
    for key, called in _held_groups(point):
        cells = ""
        for loss, _ in losses:
            one = spreads.get((key, loss))
            if one is None:
                cells += '<td class="muted">-</td>'
                continue
            runs = _plural(one["runs"], "run")
            if one["unforked"] == one["runs"]:
                runs = f"no fork in {one['runs']} of {runs}"
            cell = typical[(key, loss)]
            cells += (
                f"<td><b>{_number(one['median'])}</b>"
                f"{_typical(cell, *trees[(key, loss)], width)}"
                f"<small>{runs} · seed {cell['seed']} drawn</small></td>"
            )
        rows += (
            f'<tr class="{key}"><td class="name"><span class="dot"></span>{_e(called)}</td>'
            f"{cells}</tr>\n"
        )
    heads = "".join(f"<th>{_e(said)}</th>" for _, said in losses)
    drawn = (
        '<section id="now">\n<h2>Now</h2>\n'
        f'<p class="facts">main {_commit(LOGOS_CHAT_URL, point["logos_chat"])} · '
        f"{_merged_links(newest) or 'no pull request'} · {_when(point)} UTC · "
        f"{_link(at, 'its runs')}</p>\n"
    )
    last = commits[-1]["points"][-1]
    if last is not point:
        gone = f"main/{last['logos_chat']}/{last['measurement']}/"
        drawn += (
            f"<p>{_link(gone, 'The newest run')}, at main {_code(last['logos_chat'])} on "
            f"{_when(last)} UTC, has no outcome. The numbers below are of the newest commit "
            "that has one.</p>\n"
        )
    drawn += f'<table class="now">\n<thead><tr><th></th>{heads}</tr></thead>\n<tbody>\n{rows}'
    drawn += "</tbody>\n</table>\n"
    held = since(ran)
    if held:
        first = ran[0]["point"]
        chips = " ".join(
            f"<b>{_e(called)}</b> {_stood(*held[key])}"
            for key, called in _held_groups(point)
            if key in held
        )
        drawn += (
            f'<p class="since">Since the first commit of this test, '
            f"{_commit(LOGOS_CHAT_URL, first['logos_chat'])} on {_e(first['time'][:10])}: "
            f"{chips}</p>\n"
        )
    tree = (
        " Under it, the tree of the run nearest that median, by epoch from 0 to the goal's line: "
        "the top line is the trunk, and each line below left the line it drops from at that epoch."
    )
    if any(page is not None for _, page in trees.values()):
        tree += " A tree opens its run's page."
    if any(lineage is None for lineage, _ in trees.values()):
        tree += _SKETCHED
    return (
        drawn
        + '<p class="caption">Large: the epochs before the first fork, the median of the '
        f"rate's runs.{tree} A run that never forks counts {_TOP}. The page tracks each group "
        f"against its own past and does not rank the two: {_e(_seats(point))} Loss is the share "
        "of the frames sent to a member that it never receives.</p>\n</section>\n"
    )


def _typical(
    cell: dict, lineage: Optional[dict], page: Optional[str], width: Callable[[int], float]
) -> str:
    """The tree of `cell`'s run small, as Now draws it under its rate's number: `lineage` its
    lines, or its sketch without them, to a line at the goal, with the run in a few words on
    hover; a link to `page`, the run's, where it has one."""
    x, pad, pitch = _along(3, 147), 6, 6
    height = pad * 2 + (_rows_of(lineage) - 1) * pitch
    if lineage is None:
        tree = _sketch(cell, "head", x, pad, pitch)
    else:
        tree = _tree(lineage, "head", x, pad, pitch, width, 1.5)
    drawn = (
        _svg(150, height, f"The tree of {_job(cell)}")
        + f'<line class="epoch" x1="{x(_TOP):.1f}" x2="{x(_TOP):.1f}" y1="0" y2="{height}"/>'
        + f"{tree}</svg>"
    )
    said = _e(f"{_job(cell)}: {simulate.words(cell)}")
    if page is None:
        return f'<span class="typical" title="{said}">{drawn}</span>'
    return f'<a class="typical" href="{_e(quote(page))}" title="{said}">{drawn}</a>'


def _seats(point: dict) -> str:
    """What makes an epoch of one group more than one of the other, in a sentence: the members
    each group's runs of a point joined an epoch of the trunk, the median of the runs that went
    without a fork, or that an epoch joins another number of members in each."""
    said = []
    for key, called in _held_groups(point):
        joined = [
            (cell["head"]["seated"] - cell["head"]["never_joined"] - 1) / cell["epochs"]["head"]
            for cell in point["cells"]
            if group(cell) == key
            and cell["ended"]["head"] not in (None, "split")
            and cell["epochs"]["head"]
            and _integer(cell["head"]["seated"])
            and _integer(cell["head"].get("never_joined"))
        ]
        if joined:
            members = _whole_number(round(statistics.median(joined), 1))
            plural = "" if members == 1 else "s"
            said.append(f"a {called} epoch joins {_number(members)} member{plural}")
    if len(said) < 2:
        return "an epoch joins another number of members in each."
    return f"in the runs that went without a fork, {' and '.join(said)}."


def _over_commits(ran: Sequence[dict]) -> str:
    """The second section: a panel a rate of loss at which a run of a commit forked, and under
    them the medians as a table."""
    newest = ran[-1]["point"]
    groups, spreads = _held_groups(newest), [_spreads(commit["point"]) for commit in ran]
    forked, whole = [], []
    for loss, said in _losses(newest):
        held = [one.get((key, loss)) for one in spreads for key, _ in groups]
        runs = [one for one in held if one is not None]
        (whole if all(one["unforked"] == one["runs"] for one in runs) else forked).append(
            (loss, said)
        )
    panels = "".join(_trend_panel(ran, spreads, groups, loss, said) for loss, said in forked)
    rows = ""
    for commit, held in zip(reversed(ran), reversed(spreads)):
        rows += f"<tr><td>{_code(commit['logos_chat'])}</td>" + "".join(
            f'<td class="n">{_number(_median_at(held, key, loss))}</td>'
            for key, _ in groups
            for loss, _ in forked
        ) + "</tr>\n"
    heads = "".join(
        f'<th class="n">{_e(called)} {_e(said)}</th>' for _, called in groups for _, said in forked
    )
    never = ""
    if whole:
        never = f" At {_e(' and '.join(said for _, said in whole))} no run of these commits forked."
    return (
        '<section class="chart trend" id="commits">\n<h2>Over main\'s commits</h2>\n'
        + _legend(
            *((_line_key(key), called) for key, called in groups),
            (_band_key(), "the middle half of the runs"),
            (_goal_key(), f"the goal, {_TOP} epochs"),
            (_changed_key(), "a commit found to change a run of the group"),
        )
        + f'<div class="panels">\n{panels}</div>\n'
        '<p class="caption">Epochs before the first fork, the median of the rate\'s runs. A '
        "marker is on each commit found to change a run of the group, as What changed has it. "
        f"Hover a commit for its numbers.{never}</p>\n"
        "<details>\n<summary>The numbers</summary>\n"
        f'<div class="wide"><table class="rates">\n<thead><tr><th>commit</th>{heads}</tr></thead>\n'
        f"<tbody>\n{rows}</tbody>\n</table></div>\n</details>\n"
        "</section>\n"
    )


def _median_at(spreads: Dict[object, dict], key: str, loss: object) -> Optional[float]:
    one = spreads.get((key, loss))
    return None if one is None else one["median"]


def _trend_panel(
    ran: Sequence[dict],
    spreads: Sequence[Dict[object, dict]],
    groups: Sequence[Tuple[str, str]],
    loss: object,
    said: str,
) -> str:
    """A panel of the graph over the commits `ran`, at one rate of loss: of each group the median
    of the epochs before the first fork as a line and the middle half of its runs as a band, a
    marker on each commit found to change a run of the group, and the group and its newest median at
    the end of its line. `spreads` holds each commit's, as `_spreads` gives them."""
    width, plot, left, right, above, below = 360, 180, 30, 104, 10, 34
    xs = _xs(len(ran), left, width - left - right)
    held = {key: [one.get((key, loss)) for one in spreads] for key, _ in groups}
    top = max([_TOP] + [one["high"] for its in held.values() for one in its if one is not None])
    bottom = above + plot

    def y(epoch: float) -> float:
        return bottom - epoch / top * plot

    drawn = [_svg(width, bottom + below, f"Epochs before the first fork at {said}")]
    for tick in range(0, _TOP + 1, 10):
        drawn.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y(tick):.1f}" '
            f'y2="{y(tick):.1f}"/>'
        )
        drawn.append(
            f'<text class="axis small" x="{left - 6}" y="{y(tick) + 4:.1f}" '
            f'text-anchor="end">{tick}</text>'
        )
    drawn.append(
        f'<line class="goal" x1="{left}" x2="{width - right}" y1="{y(_TOP):.1f}" '
        f'y2="{y(_TOP):.1f}"/>'
    )
    for number in range(len(ran))[::-1][:: _every(xs)]:
        drawn.append(
            f'<text class="axis small" x="{xs[number]:.1f}" y="{bottom + 15}" '
            f'text-anchor="middle">{_e(_ticked(ran[number]))}</text>'
        )
    ends = []
    for key, called in groups:
        its = held[key]
        drawn.append(f'<g class="{key}">')
        for run in _runs(its):
            highs = [(xs[number], y(its[number]["high"])) for number in run]
            lows = [(xs[number], y(its[number]["low"])) for number in reversed(run)]
            if len(run) == 1:
                (x, high), (_, low) = highs[0], lows[0]
                highs, lows = [(x - 4, high), (x + 4, high)], [(x + 4, low), (x - 4, low)]
            corners = " ".join(f"{x:.1f},{at:.1f}" for x, at in highs + lows)
            drawn.append(f'<polygon class="band" points="{corners}"/>')
        medians = [None if one is None else one["median"] for one in its]
        # A commit with none beside it has its median as wide as its band: a marker is a change.
        alone = [
            f"M{xs[run[0]] - 4:.1f} {y(medians[run[0]]):.1f} "
            f"L{xs[run[0]] + 4:.1f} {y(medians[run[0]]):.1f}"
            for run in _runs(its)
            if len(run) == 1
        ]
        line = " ".join([_path(xs, medians, y)] + alone).strip()
        if line:
            drawn.append(f'<path class="series" d="{line}"/>')
        for number, commit in enumerate(ran):
            judged = None if commit["step"] is None else _judged(commit["step"]["matrix"], key)
            moved = judged is not None and judged["counts"]["moved"]
            if medians[number] is not None and moved:
                drawn.append(
                    f'<circle class="marker" cx="{xs[number]:.1f}" '
                    f'cy="{y(medians[number]):.1f}" r="4"/>'
                )
        drawn.append("</g>")
        last = next((one for one in reversed(medians) if one is not None), None)
        if last is not None:
            ends.append({"kind": key, "name": called, "value": last, "y": y(last), "at": y(last)})
    drawn += _ends(ends, xs[-1], bottom + 4)
    half = (xs[1] - xs[0]) / 2 if len(xs) > 1 else (width - left - right) / 2
    for number, commit in enumerate(ran):
        lines = [_commit_said(commit)] + [
            f"{called}: {_spread_said(held[key][number])}" for key, called in groups
        ]
        drawn.append(
            f"<g><title>{_e(chr(10).join(lines))}</title>"
            f'<rect class="hit" x="{xs[number] - half:.1f}" y="{above}" width="{2 * half:.1f}" '
            f'height="{plot}"/></g>'
        )
    drawn.append("</svg>")
    return f'<div class="panel">\n<p>{_e(said)}</p>\n' + "\n".join(drawn) + "\n</div>\n"


def _spread_said(one: Optional[dict]) -> str:
    if one is None:
        return "no outcome"
    return (
        f"median {_number(one['median'])}, the middle half {_number(one['low'])} to "
        f"{_number(one['high'])}, {_plural(one['runs'], 'run')}"
    )


def _commit_said(commit: dict) -> str:
    """A commit that ran as a hover names it."""
    point = commit["point"]
    merged = ", ".join(f"#{one['number']}" for one in commit["merged"]) or "no pull request"
    return f"main {point['logos_chat'][:7]} · {merged} · {_when(point)} UTC"


def _ticked(commit: dict) -> str:
    """A commit under its tick of the axis: the pull request it merged, with how many more, or
    its short sha where it merged none."""
    numbers = [f"#{one['number']}" for one in commit["merged"]]
    if not numbers:
        return commit["logos_chat"][:7]
    return numbers[0] if len(numbers) == 1 else f"{numbers[0]} +{len(numbers) - 1}"


def _merged_links(commit: dict) -> str:
    return ", ".join(
        _link(f"{LOGOS_CHAT_URL}/pull/{one['number']}", f"#{one['number']}")
        + f" {_e(one['title'])}"
        for one in commit["merged"]
    )


def _what_changed(commits: Sequence[dict]) -> str:
    """The third section: a row a commit, the newest first, with what each group's runs came to
    against the commit before it."""
    groups = _held_groups(next(c["point"] for c in reversed(commits) if c["point"] is not None))
    rows, first = "", True
    for commit in commits:
        point, step = commit["point"], commit["step"]
        shown = point or commit["points"][-1]
        at = f"main/{shown['logos_chat']}/{shown['measurement']}/"
        cells = (
            f'<td class="when">{_when(commit["points"][0])}</td>'
            f"<td>{_commit(LOGOS_CHAT_URL, commit['logos_chat'])}</td>"
            f'<td class="pr">{_merged_links(commit) or _NONE_MERGED}</td>'
        )
        moved = False
        if point is None:
            cells += f'<td colspan="{len(groups)}">no run had an outcome</td>'
        elif first:
            cells += f'<td colspan="{len(groups)}">the first commit this test ran on</td>'
        elif step is None:
            cells += f'<td colspan="{len(groups)}">not compared with the commit before</td>'
        else:
            moved = step["matrix"]["verdict"] != "same"
            cells += "".join(f"<td>{_step_cell(step, key)}</td>" for key, _ in groups)
        first = first and point is None
        rows = (
            f'<tr class="{"moved" if moved else "quiet"}">{cells}'
            f'<td>{_link(at, "its runs")}</td></tr>\n' + rows
        )
    heads = "".join(f"<th>{_e(called)}</th>" for _, called in groups)
    return (
        '<section id="changes">\n<h2>What changed</h2>\n'
        '<p class="caption">Each commit is compared with the commit before it, seed by seed, '
        "at the rates the newest commit ran: does the first fork come earlier, later or at the "
        "same epoch?</p>\n"
        + _results_key()
        + '<div class="wide"><table class="changes">\n'
        f"<thead><tr><th>Measured (UTC)</th><th>Commit</th><th>Pull request</th>{heads}"
        f"<th></th></tr></thead>\n<tbody>\n{rows}</tbody>\n</table></div>\n"
        "</section>\n"
    )


def _step_cell(step: dict, key: str) -> str:
    """What a group's runs came to at a step, in its cell of the commit's row: its standing,
    and where a run of it moved, of each rate one moved at its medians before and after and how
    its seeds moved, and a link to the page of the change."""
    matrix = step["matrix"]
    at = f"main/{step['now']['logos_chat']}/{step['now']['measurement']}/change/"
    one = _judged(matrix, key)
    if one is None:
        return '<span class="muted">-</span>'
    drawn = _stood(*_standing(one))
    if one["counts"]["moved"]:
        rates = ""
        for result in one["results"]:
            cells = [
                cell
                for cell in matrix["cells"]
                if cell["scenario"] == result["scenario"] and cell["change"] is not None
            ]
            if not any(cell["change"] == "moved" for cell in cells):
                continue
            was = statistics.median(cell["epochs"]["base"] for cell in cells)
            now = statistics.median(cell["epochs"]["head"] for cell in cells)
            rates += (
                f"<span>{_e(result['rate'] or result['scenario'])}</span>"
                f"<span>{_number(_whole_number(was))} → {_number(_whole_number(now))}</span>"
                f"{_moved_seeds(result)}"
            )
        drawn += (
            f'<div class="rates">{rates}</div>'
            f'{_link(at, "compare the runs before and after")}'
        )
    return drawn


def _stood(verdict: str, alone: str) -> str:
    """A group's standing, as `_standing` gives it: the chip of its verdict, and after it how
    many of its runs had none to compare with."""
    return _result_chip(verdict) + (f' <span class="muted">{_e(alone)}</span>' if alone else "")


def _result_chip(verdict: str) -> str:
    """The chip of a verdict, as a pull request's page has it, but for the one that names a
    base, which a commit of main has none of."""
    said = "no run to compare with" if verdict == "no-base" else simulate.RESULTS[verdict]
    return _chip(_MISSING.get(verdict, verdict), _ARROWS.get(verdict, "") + said)


def _moved_seeds(result: dict) -> str:
    """How the seeds of a scenario's result moved: a bar of those that first forked earlier,
    at the same epoch and later, and the three counts in words."""
    counts = (("earlier", result["earlier"]), ("tied", result["zero"]), ("later", result["later"]))
    total, width, x, bars = sum(count for _, count in counts), 72, 0.0, ""
    for kind, count in counts:
        if count:
            long = width * count / total
            # 2 px of the surface between two segments.
            bars += (
                f'<rect class="{kind}" x="{x:.1f}" y="0" width="{max(long - 2, 1):.1f}" '
                'height="8"/>'
            )
            x += long
    said = " · ".join(f"{count} {kind}" for kind, count in counts)
    return (
        f'<span class="tally"><svg width="{width}" height="8" viewBox="0 0 {width} 8" '
        f'aria-hidden="true">{bars}</svg>{said}</span>'
    )


def _results_key() -> str:
    """What each result of a comparison says, each chip beside its meaning."""
    return (
        '<div class="results">'
        f"<span>{_result_chip('same')}</span><span>every seed with an outcome at both "
        "commits came out the same: the same ids, the same virtual time, the same forks</span>"
        f"<span>{_result_chip('within')}</span><span>runs differ, with no lean to earlier or "
        "later beyond chance. A change can shift which frames every run loses and leave the "
        "forking as it was.</span>"
        f"<span>{_result_chip('earlier')} {_result_chip('later')}</span><span>most seeds moved "
        "one way, further than chance gives 1 time in "
        f"{sim_matrix.ALPHA.denominator // sim_matrix.ALPHA.numerator}</span>"
        "</div>\n"
    )


def _foot(site: Path, commits: Sequence[dict], earlier: Sequence[dict]) -> str:
    """Under the sections: since when the test has run and how often chat-sim changed under it,
    where the points before it are, the newest long run, and the guide's section on the page."""
    said = ""
    held = [point for commit in commits for point in commit["points"] if _unforked(point)]
    if held:
        changed = sum(a["measurement"] != b["measurement"] for a, b in zip(held, held[1:]))
        said = (
            f"This test has run since {_e(held[0]['time'][:10])}, at chat-sim "
            f"{_commit(CHAT_SIM_URL, held[0]['measurement'])}. "
        )
        if changed:
            said += (
                f"chat-sim itself changed {_plural(changed, 'time')} since, and each time no "
                "seed's first fork moved. "
            )
        said += (
            "A change of chat-sim that moves a seed's first fork starts the page again from "
            "that run. "
        )
    if earlier:
        before = f"The {_plural(len(earlier), 'point')} before"
        if _cut_by_chat_sim(commits, earlier):
            said += f"{before} ran another test: "
        else:
            said += (
                f"{before} are not compared with these, since chat-sim and main changed "
                "together after them: "
            )
        said += f"{_link('earlier/', 'the earlier points')}."
    drawn = f'<p class="caption">{said}</p>\n' if said else ""
    newest = next(iter(longs(site)), None)
    if newest is not None:
        _, long = newest
        at = f"long/{long['logos_chat']}/{long['measurement']}/"
        drawn += (
            f'<p class="caption">{_link(at, "The newest long run")}, at main '
            f"{_code(long['logos_chat'])} on {_when(long)} UTC: the two scenarios of fifty epochs "
            "and each scenario chat-sim holds to what is expected but the goal's on either "
            "group, run at main's tip each Sunday. Nothing of it is drawn here.</p>\n"
        )
    return drawn + (
        f'<p class="muted">What the page shows: the guide\'s '
        f'{_link("../guide/track/main/#the-graph-over-main", "The graph over main")}.</p>\n'
    )


def _step(ran: Sequence[dict], before: Sequence[dict], named: Dict[object, str]) -> Optional[dict]:
    """The step of a commit whose points with an outcome are `ran` from the commit whose are
    `before`: the newest point of `ran` at a measurement a point of `before` is at, that point,
    and the matrix of the two; `None` where no two are at one measurement or `compared` cannot
    read them, which a warning on standard error says."""
    held = {point["measurement"]: point for point in before}
    now = next((point for point in reversed(ran) if point["measurement"] in held), None)
    if now is None:
        return None
    was = held[now["measurement"]]
    try:
        return {"now": now, "was": was, "matrix": compared(now, was, named)}
    except Unreadable as refused:
        print(f"::warning::{refused}", file=sys.stderr)
        return None


def _cut_by_chat_sim(commits: Sequence[dict], earlier: Sequence[dict]) -> bool:
    """Whether chat-sim alone changed between the points `earlier` and the test of `commits`
    that follows them: the newest of them with an outcome is at the commit the test began at."""
    last = next(point for point in reversed(earlier) if _unforked(point))
    first = next(commit for commit in commits if commit["point"] is not None)
    return last["logos_chat"] == first["logos_chat"]


def _has_change(commit: dict) -> bool:
    """Whether a commit of `history` has a page of its change: a run moved at its step."""
    step = commit["step"]
    return step is not None and step["matrix"]["counts"]["moved"] > 0


def _at_one(before: dict, now: dict) -> bool:
    return before["measurement"] == now["measurement"]


def _continues(before: dict, now: dict) -> bool:
    """Whether the point `now` measures what `before`, the point before it, measures: the two
    are at one measurement, or they ran a seed of a rate both and each such seed first forked at
    the same epoch in both."""
    if _at_one(before, now):
        return True
    was, held = _unforked(before), _unforked(now)
    both = was.keys() & held.keys()
    return bool(both) and all(was[key] == held[key] for key in both)


def _unforked(point: dict) -> Dict[tuple, int]:
    """The epochs without a fork of each run of a point that has an outcome, by its rate, as
    `_rate` keys it, and its seed."""
    return {
        (_rate(cell), cell["seed"]): cell["epochs"]["head"]
        for cell in point["cells"]
        if cell["epochs"]["head"] is not None
    }


def _named(point: dict) -> Dict[object, str]:
    """The scenario of each rate of a point, as `_rate` keys it."""
    return {_rate(cell): cell["scenario"] for cell in point["cells"]}


def _as_side(
    point: dict, named: Dict[object, str]
) -> Tuple[sim_matrix.Side, List[Tuple[str, int]]]:
    """A point's runs as a side of `tools/sim matrix`, each under the scenario `named` gives its
    rate, and the runs it has no outcome of that were built, each its scenario and its number, as
    the matrix is asked for them."""
    runs: Dict[Tuple[str, int], sim_matrix.Run] = {}
    asked = []
    for cell in point["cells"]:
        name = named.get(_rate(cell))
        if name is None:
            continue
        if cell["head"] is None and not cell["unbuilt"]["head"]:
            asked.append((name, cell["run"]))
        else:
            runs[(name, cell["seed"])] = sim_matrix.Run(cell["run"], cell["loss"], _reshaped(cell))
    return sim_matrix.Side(runs), asked


def _reshaped(cell: dict) -> Optional[dict]:
    """The outcome of a cell's run as the newest measurement writes one: one from before the
    outcomes held the epoch of the first fork has it from the cell's epochs without a fork."""
    outcome = cell["head"]
    if outcome is None or "first_fork" in outcome:
        return outcome
    forked = cell["epochs"]["head"] if outcome["ended"] == "split" else None
    return {"never_joined": None, **outcome, "first_fork": forked}


def _judged(matrix: dict, key: str) -> Optional[dict]:
    """What a matrix came to for the group `key`, `None` where it has no cell of it."""
    return next((one for one in matrix["groups"] if group(one) == key), None)


def _standing(judged: dict) -> Tuple[str, str]:
    """What a group's runs came to against another commit's, as a matrix judged them: the
    verdict of its rates that had a run to compare with, and how many of its runs had none, in
    words; the group's own verdict and no words where each run had one, or no rate did."""
    counts = judged["counts"]
    runs = counts["cells"] - counts["no-outcome"]
    alone = runs - counts["compared"]
    compared = [one["result"] for one in judged["results"] if one["result"] != "no-base"]
    if not alone or not compared:
        return judged["verdict"], ""
    said = f"{alone} of {_plural(runs, 'run')} had none to compare with"
    return min(compared, key=sim_matrix.WORST.index), said


def _spread(values: Sequence[int]) -> Optional[dict]:
    """How the epochs without a fork of a rate's runs spread: how many `runs`, their `median`,
    the `low` and `high` ends of their middle half, and how many went `unforked` to the goal;
    `None` of no run."""
    if not values:
        return None
    low, median, high = (
        statistics.quantiles(values, n=4, method="inclusive") if len(values) > 1 else values * 3
    )
    return {
        "runs": len(values),
        "median": _whole_number(median),
        "low": _whole_number(low),
        "high": _whole_number(high),
        "unforked": sum(one >= _TOP for one in values),
    }


def _whole_number(value: float) -> Union[int, float]:
    return int(value) if value == int(value) else value


def _spreads(point: dict) -> Dict[object, dict]:
    """The spread of each rate of a point with an outcome, by the rate as `_rate` keys it."""
    held: Dict[object, List[int]] = {}
    for (rate, _), epochs in sorted(_unforked(point).items(), key=lambda one: one[0][1]):
        held.setdefault(rate, []).append(epochs)
    return {rate: _spread(values) for rate, values in held.items()}


def _losses(point: dict) -> List[Tuple[object, str]]:
    """Each rate of loss a point ran, of either group, in the order of its loss, and what the
    page calls it; a scenario with no network model after them, by its name."""
    seen: Dict[object, Tuple[tuple, str]] = {}
    for cell in point["cells"]:
        loss = cell["scenario"] if cell["loss"] is None else cell["loss"]
        said = cell["scenario"] if cell["loss"] is None else _lost_at(cell)
        seen.setdefault(loss, ((cell["loss"] is None, cell["loss"] or 0, cell["scenario"]), said))
    return [(loss, said) for loss, (_, said) in sorted(seen.items(), key=lambda one: one[1][0])]


def _lost_at(cell: dict) -> str:
    return "no loss" if not cell["loss"] else f"{cell['rate']} loss"


def _held_groups(point: dict) -> List[Tuple[str, str]]:
    """Each group a point ran, in the order of `simulate.GROUPS`: its key and what the page
    calls it."""
    held = {group(cell) for cell in point["cells"]}
    return [(key, called) for key, called in simulate.GROUPS.items() if key in held]


def _dumbbells(cells: Sequence[dict], key: str, label: str, said: str) -> str:
    """A panel of the page of a change: of one rate, a row a seed with its epochs before the
    first fork at the commit before as a ring and at the commit as a dot, joined, the seed whose
    first fork moved earliest at the top."""
    width, left, right, above, pitch = 360, 34, 14, 22, 13
    ordered = sorted(cells, key=lambda cell: (cell["d"], cell["seed"]))
    bottom = above + pitch * len(ordered)

    def x(epoch: float) -> float:
        return left + epoch / _TOP * (width - left - right)

    drawn = [_svg(width, bottom + 6, f"Epochs before the first fork at both commits, {label}")]
    drawn.append('<text class="axis small" x="0" y="11">seed</text>')
    for tick in range(0, _TOP + 1, 10):
        drawn.append(
            f'<line class="grid" x1="{x(tick):.1f}" x2="{x(tick):.1f}" y1="{above - 4}" '
            f'y2="{bottom}"/>'
        )
        drawn.append(
            f'<text class="axis small" x="{x(tick):.1f}" y="11" text-anchor="middle">{tick}</text>'
        )
    drawn.append(f'<g class="dumb {key}">')
    for row, cell in enumerate(ordered):
        y = above + pitch * row + pitch / 2
        was, now = cell["epochs"]["base"], cell["epochs"]["head"]
        told = f"seed {cell['seed']}: {_pair_said(cell)}"
        drawn.append(
            f"<g><title>{_e(told)}</title>"
            f'<rect class="hit" x="0" y="{y - pitch / 2:.1f}" width="{width}" height="{pitch}"/>'
            f'<text class="axis small" x="{left - 12}" y="{y + 3.5:.1f}" text-anchor="end">'
            f"{cell['seed']}</text>"
        )
        if was != now:
            drawn.append(
                f'<line class="link" x1="{x(was):.1f}" x2="{x(now):.1f}" y1="{y:.1f}" '
                f'y2="{y:.1f}"/>'
                f'<circle class="before" cx="{x(was):.1f}" cy="{y:.1f}" r="3.5"/>'
            )
        drawn.append(f'<circle class="after" cx="{x(now):.1f}" cy="{y:.1f}" r="4"/></g>')
    drawn.append("</g>\n</svg>")
    return f'<div class="panel">\n<p>{_e(said)}</p>\n' + "\n".join(drawn) + "\n</div>\n"


def _pair_said(cell: dict) -> str:
    """A seed's two runs in words: its epochs before the first fork at both commits, and how
    far it moved."""
    was, now = cell["epochs"]["base"], cell["epochs"]["head"]
    return f"{was} before, {now} after, {_moved_by(cell)}"


def _moved_by(cell: dict) -> str:
    d = cell["d"]
    if not d:
        return "the same outcome" if cell["change"] == "same" else "tied"
    return f"{abs(d)} {'later' if d > 0 else 'earlier'}"


def _pairs(
    cells: Sequence[dict],
    ran: Dict[tuple, dict],
    based: Dict[tuple, dict],
    out: Path,
    before: Path,
) -> str:
    """The seeds of one rate as a table, in the order of their seeds: each seed's run by number,
    its epochs before the first fork at both commits, how far it moved, the round its two runs
    part in, and each run's text, that of the commit in `out` and that of the commit before in
    `before`. `ran` and `based` hold each point's own cell of a rate and seed, which names its
    run's directory."""
    rows = ""
    for cell in sorted(cells, key=lambda cell: cell["seed"]):
        key = (_rate(cell), cell["seed"])
        now, was = _run_dir(out, ran[key]), _run_dir(before, based[key])
        there = f"../../../{before.parent.name}/{before.name}/{_run_href(based[key])}"
        rows += (
            f'<tr><td class="n">{cell["seed"]}</td><td class="n">{ran[key]["run"]}</td>'
            f'<td class="n">{cell["epochs"]["base"]}</td>'
            f'<td class="n">{cell["epochs"]["head"]}</td>'
            f"<td>{_moved_by(cell)}</td>"
            f"<td>{_parted(_run_json(was / 'run.json'), _run_json(now / 'run.json'))}</td>"
            f"<td>{_file_links(was, there) or '-'}</td>"
            f"<td>{_file_links(now, '../' + _run_href(ran[key])) or '-'}</td></tr>\n"
        )
    return (
        '<div class="wide"><table class="pairs">\n'
        '<thead><tr><th class="n">Seed</th><th class="n">Run</th><th class="n">Before</th>'
        '<th class="n">After</th><th>Moved</th><th>The runs part in</th><th>The run before</th>'
        f"<th>The run after</th></tr></thead>\n<tbody>\n{rows}</tbody>\n</table></div>\n"
    )


def _pair_trees(
    cells: Sequence[dict],
    trees: Dict[tuple, Sequence[Tuple[Optional[dict], Optional[str]]]],
    width: Callable[[int], float],
) -> str:
    """The seeds of one rate as trees, three a row, from the seed whose first fork moved earliest
    to the one whose moved latest: of each, where its two runs first forked and how far that
    moved, over the tree of its run after and that of its run before. `trees` holds of each
    seed's two runs, by its rate and its seed, the lineage and the address of the run's page,
    which the tree links where there is one. A row is as tall as its most lines."""
    x = _along(4, 264)
    ordered = sorted(cells, key=lambda cell: (cell["d"], cell["seed"]))
    rows = ""
    for first in range(0, len(ordered), 3):
        row = [(cell, trees[(_rate(cell), cell["seed"])]) for cell in ordered[first : first + 3]]
        tops, tall, height = _tiers([[lineage for lineage, _ in both] for _, both in row], True)
        tiles = _row_head("", ("after", "before"), tops, height)
        for cell, both in row:
            label = f"{_job(cell)}: the tree of its run after over that of its run before"
            drawn = _svg(306, height, label) + _epoch_lines(x, 0, height)
            for side, top, (lineage, page) in zip(("head", "base"), tops, both):
                tree = _small(cell, side, lineage, x, top, 7, width, 272)
                if page is not None:
                    tree = (
                        f'<a href="{_e(quote(page))}"><rect class="hit" x="0" '
                        f'y="{top - tops[0]}" width="306" height="{tall}"/>{tree}</a>'
                    )
                drawn += tree
            d = cell["d"]
            chip = _chip("later" if d > 0 else "earlier" if d < 0 else "same", _moved_by(cell))
            tiles += (
                f'<div class="fork"><div class="said"><b>seed {cell["seed"]}</b> '
                f"<span>{_pair_forked(cell)}</span> {chip}</div>{drawn}</svg></div>"
            )
        rows += f"<div>{tiles}</div>\n"
    return f'<div class="wide"><div class="forks">\n{rows}</div></div>\n'


def _pair_forked(cell: dict) -> str:
    """Where a seed's run before first forked, or how and where its trunk ended, and then its run
    after: its epoch alone where it ended as the run before did."""
    before, after = _first_fork(cell, "base", "{}"), _first_fork(cell, "head", "<b>{}</b>")
    if cell["ended"]["base"] == cell["ended"]["head"]:
        after = f"<b>{cell['epochs']['head']}</b>"
    return f"{before} → {after}"


def _run_page(directory: Path, cell: dict, href: str) -> Optional[str]:
    """The address of the page of `cell`'s run of the point in `directory`, from `href`, the
    point's; `None` where the run has no page."""
    if (_run_dir(directory, cell) / "index.html").is_file():
        return href + _run_href(cell)
    return None


def _pair_logs(
    cell: dict, ran: Dict[tuple, dict], based: Dict[tuple, dict], now_url: str, was_url: str
) -> str:
    """The commands that download the logs of both commits' runs of the rate of `cell`, those
    of the commit before into `before` and those of the commit into `after`, from the workflow
    runs at `was_url` and `now_url`; nothing where either is not a workflow run's address."""
    key = (_rate(cell), cell["seed"])
    commands = [
        _logs(was_url, based[key]["scenario"], ("revision",), "before"),
        _logs(now_url, ran[key]["scenario"], ("revision",), "after"),
    ]
    if not all(commands):
        return ""
    return (
        '<p class="side">The logs of both commits\' runs, a seed\'s <code>group.log</code> under '
        f"its run's number:</p>\n<pre>{_e(chr(10).join(commands))}</pre>\n"
    )


def _parted(was: Optional[dict], now: Optional[dict]) -> str:
    """The round two runs of a seed part in, counted from 1, as their run.json have their
    rounds: the first that differs in its adders, the members they added, the adders refused or
    its virtual seconds, or the round after the last of the shorter run; `-` where a run.json is
    not kept or has no rounds."""
    rounds = []
    for report in (was, now):
        grow = report.get("grow") if report is not None else None
        held = _listed(grow.get("rounds")) if isinstance(grow, dict) else []
        if not held or not all(isinstance(one, dict) for one in held):
            return "-"
        rounds.append(
            [
                tuple(one.get(field) for field in ("adders", "joiners", "refused", "settled_s"))
                for one in held
            ]
        )
    theirs, ours = rounds
    for number, (before, after) in enumerate(zip(theirs, ours), 1):
        if before != after:
            return f"round {number}"
    if len(theirs) != len(ours):
        return f"round {min(len(theirs), len(ours)) + 1}"
    return "no round"


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
    keys += _lines_key(most, width)
    if any(cell["change"] == "moved" for cell in matrix["cells"]):
        keys.append(('<span class="outlined">outline</span>', "moved"))
    return _legend(*keys)


def _lines_key(most: int, width: Callable[[int], float]) -> List[Tuple[str, str]]:
    """The keys of the lines of small trees: how wide `width` draws a line of so many members up
    to `most`, where a tree has a line, and the ring of the members that never joined."""
    keys = []
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
    return [*keys, (ring + "</svg>", "never joined")]


def _forks(
    matrix: dict, cells: Sequence[dict], trees: Dict[str, tuple], width: Callable[[int], float]
) -> str:
    """The trees of a group's `cells` of seeds 0 to 2, `trees` their lineages by run, a row a rate
    and a column a seed: each row as tall as its most lines, headed by its rate, and followed,
    where the rate ran more, by the strip of every seed of it and the fold of the others' trees."""
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
        shown = [cell for cell in by_seed.values() if _treed(cell)]
        tops, _, height = _tiers([trees[_run_href(cell)] for cell in shown], based)
        sides = ("head", "base") if based else ("head",)
        row = [_row_head(f"<b>{_e(_loss(first))}</b>", sides, tops, height)]
        for seed in seeds:
            if seed not in by_seed:
                row.append('<div class="vacant"></div>')
                continue
            cell = by_seed[seed]
            row.append(_fork(matrix, cell, trees[_run_href(cell)], width, tops, height))
        drawn.append(f"<div>{''.join(row)}</div>\n")
        if len(shown) < len(by_seed):
            every = [cell for _, cell in sorted(by_seed.items())]
            rest = [cell for cell in every if not _treed(cell)]
            drawn.append(_strip(matrix, every) + _more(matrix, rest, trees, width))
    return f'<div class="wide"><div class="forks">\n{"".join(drawn)}</div></div>\n'


def _tiers(
    trees: Sequence[Sequence[Optional[dict]]], both: bool
) -> Tuple[Tuple[float, float], float, float]:
    """How a row of small trees is laid out, `trees` the lineages of each of its tiles, a tree's
    rows 7 apart: where a tile has the trunk of its first tree and of the one under it, how tall
    the band of one tree is, by the most lines of any, and how tall the tile's drawing, of
    `both` trees or of the first alone."""
    pad, pitch, between = 7, 7, 8
    count = max(_rows_of(tree) for tile in trees for tree in tile)
    tall = pad + (count - 1) * pitch + pad
    return (pad, tall + between + pad), tall, tall * 2 + between if both else tall


def _more(
    matrix: dict, cells: Sequence[dict], trees: Dict[str, tuple], width: Callable[[int], float]
) -> str:
    """The fold under a strip: the trees of `cells`, the seeds of a rate past those its row
    draws, three a row and each under its seed, drawn as the row draws its own."""
    based = matrix["base"] is not None
    sides = ("head", "base") if based else ("head",)
    rows = ""
    for first in range(0, len(cells), 3):
        row = cells[first : first + 3]
        tops, _, height = _tiers([trees[_run_href(cell)] for cell in row], based)
        tiles = "".join(
            _fork(matrix, cell, trees[_run_href(cell)], width, tops, height, seeded=True)
            for cell in row
        )
        rows += f"<div>{_row_head('', sides, tops, height)}{tiles}</div>\n"
    return (
        f"<details>\n<summary>seeds {cells[0]['seed']} to {cells[-1]['seed']} as trees"
        f"</summary>\n{rows}</details>\n"
    )


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


def _row_head(said: str, sides: Sequence[str], tops: Tuple[float, float], height: float) -> str:
    """The head of a row of trees: `said`, what the row is, and what each of `sides` is called,
    beside its tree at `tops`."""
    drawn = "".join(
        f'<text class="note" x="0" y="{top + 4}">{side}</text>' for side, top in zip(sides, tops)
    )
    return (
        f'<div class="rate"><div class="said">{said}</div>'
        f'<svg width="116" height="{height}" viewBox="0 0 116 {height}">{drawn}</svg></div>'
    )


def _fork(
    matrix: dict,
    cell: dict,
    trees: Tuple[Optional[dict], Optional[dict]],
    width: Callable[[int], float],
    tops: Tuple[float, float],
    height: float,
    seeded: bool = False,
) -> str:
    """A run's place in its row, a link to its page: what moved in a few words, after its seed
    where `seeded`, over the head's tree and the base's, `trees` their lineages, at `tops`, each
    with its ring; outlined where it moved, dashed where a side did not build."""
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
        drawn.append(_small(cell, side, lineage, x, top, 7, width, 272))
    said = _fork_said(matrix, cell)
    if seeded:
        said = f"<b>seed {cell['seed']}</b> {said}"
    return (
        f'<a class="{kind}" href="{_e(quote(simulate.run_page(cell)))}">'
        f'<div class="said">{said}</div>{"".join(drawn)}</svg></a>'
    )


def _small(
    cell: dict,
    side: str,
    lineage: Optional[dict],
    x: Callable[[float], float],
    top: float,
    pitch: float,
    width: Callable[[int], float],
    ring: float,
) -> str:
    """A side's run of `cell` small, its trunk at `top`: its tree, `lineage` its lines, or its
    sketch without them, and at `ring` the ring of the members that never joined; why it has no
    tree where it has no outcome."""
    why = _unshown(cell, side)
    if why is not None:
        return f'<text class="note" x="{x(0):.1f}" y="{top + 4}">{_e(why)}</text>'
    if lineage is None:
        drawn = _sketch(cell, side, x, top, pitch)
    else:
        drawn = _tree(lineage, side, x, top, pitch, width, 1.8)
    never = _never(cell, side, lineage)
    return drawn + (_ring(ring, top, never) if never else "")


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


def _run_trees(cell: dict, sides: Sequence[Tuple[str, str, str, Optional[dict]]]) -> str:
    """The runs of `cell` large on one axis of epochs, one under another: of each of `sides`, the
    side of the cell it is, what the chart calls it, its commit and its lineage. Each line is
    labelled with its members, its epochs and the line it left, with a mark where the run first
    forked, who fell behind and when, and the members of each. Its text is placed by an estimate
    of its width, since no browser measures it here."""
    wide, left, right, pitch = 1118, 92, 852, 28
    x = _along(left, right)
    width = _widths(_most([lineage for *_, lineage in sides]), 2, 9)
    goal = f'<text class="note" x="{x(_TOP) + 14:.1f}" y="14">the goal</text>'
    front = [_epoch_ticks(x, 14), goal]
    y = 34
    for number, (side, called, sha, lineage) in enumerate(sides):
        if number:
            front.append(f'<line class="rule" x1="0" x2="{wide}" y1="{y + 12}" y2="{y + 12}"/>')
            y += 24
        top = y + 46
        front.append(f'<text class="label strong" x="0" y="{top + 4}">{called}</text>')
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
    drawn = "the head's tree over the base's" if len(sides) > 1 else "its tree"
    return _svg(wide, height, f"{_job(cell)}: {drawn}") + "".join(back + front) + "</svg>\n"


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
    """The lineage of a run's grow as `report`, its run.json, holds it: its lines as the rows
    `_rows` lays out, who fell behind and at which epoch, who was behind at the end and who never
    joined. `None` where `_rows` lays out none, as of a run.json of an earlier measurement."""
    rows = _rows(_run_lines(report))
    if rows is None:
        return None
    grow = report["grow"]
    fell = [
        (one["name"], one["epoch"])
        for one in _listed(grow.get("fell_behind"))
        if isinstance(one, dict) and isinstance(one.get("name"), str) and _integer(one.get("epoch"))
    ]
    behind, never = _names(grow.get("behind")), _names(grow.get("never_joined"))
    return {"rows": rows, "fell": fell, "behind": behind, "never": never}


def _kept(point: dict, cell: dict) -> Optional[dict]:
    """The lineage of `cell`'s run as `point` keeps it: its rows alone, which name no member.
    `None` where the point keeps no lines of the run."""
    lines = point.get("lines", {}).get(_run_href(cell))
    return None if lines is None else {"rows": _rows(lines)}


def _lines(directory: Path, cells: Sequence[dict]) -> Dict[str, List[dict]]:
    """The lines of each run of `cells` whose run.json `directory` keeps, by the run's directory
    there, as a point keeps them; a run whose lines `_rows` cannot lay out has none."""
    held = {}
    for cell in cells:
        lines = _run_lines(_run_json(_run_dir(directory, cell) / "run.json"))
        if _rows(lines) is not None:
            held[_run_href(cell)] = lines
    return held


def _run_lines(report: Optional[dict]) -> Optional[List[dict]]:
    """The lines of a run's grow as a point keeps them, from `report`, its run.json, in the run's
    order: of each the fields of `_LINE`. `None` where it has no lines, each with its members."""
    grow = report.get("grow") if report is not None else None
    lines = grow.get("lines") if isinstance(grow, dict) else None
    if not isinstance(lines, list):
        return None
    held = []
    for line in lines:
        if not isinstance(line, dict) or not isinstance(line.get("members"), list):
            return None
        held.append(
            {
                "from": line.get("from"),
                "forked_at": line.get("forked_at"),
                "epoch": line.get("epoch"),
                "members": len(line["members"]),
            }
        )
    return held


def _rows(lines: object) -> Optional[List[dict]]:
    """The rows of the tree of a run's `lines`, as a point keeps them, a row a line: the trunk
    first, then the lines that left each line, the latest to leave first, each followed by those
    that left it, so that no line's drop crosses another. `None` where a line is not of `_LINE`'s
    fields, lacks the line it left, the epoch it left at or its tip's epoch, or does not come
    down from the trunk."""
    if not isinstance(lines, list):
        return None
    left: Dict[Optional[int], List[int]] = {}
    for number, line in enumerate(lines):
        if not isinstance(line, dict) or line.keys() != _LINE:
            return None
        source, at = line["from"], line["forked_at"]
        if not (_integer(line["epoch"]) and _integer(line["members"])):
            return None
        if (source is None) != (at is None):
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
        row = len(rows)
        rows.append({"line": number, **lines[number], "row": row, "parent": parent})
        for child in sorted(
            left.get(number, []), key=lambda one: (lines[one]["forked_at"], one), reverse=True
        ):
            visit(child, row)

    visit(left[None][0], None)
    return rows if len(rows) == len(lines) else None


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
    """How many members of a side's run of `cell` never joined: as its lineage names them, else,
    without one or with one that names nobody, as its outcome counts them, which one of an earlier
    measurement does not."""
    if lineage is not None and "never" in lineage:
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


def _rounds_said(tables: str) -> str:
    """The section of a run's rounds, `tables` what `_rounds` draws of them, with how a round is
    read; nothing without a table."""
    if not tables:
        return ""
    return (
        f"<h2>The rounds</h2>\n{tables}"
        '<p class="caption">Each round\'s adders, the members they added, the adders refused '
        "the whole round, and the virtual seconds the round took. The run does not say which "
        "adder added which member.</p>\n"
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


def _forest(point: dict, linked: bool) -> str:
    """The runs of a point as trees, from the lines the point keeps, under how they are read and
    their legend, where a run has an outcome to draw: of each group, in its hue, a row for each
    rate none of whose runs forked, then a column a rate, its runs from the fewest epochs without
    a fork down, with a rule at their median. A run without an outcome is named under its rate.
    Where `linked`, a run links its page."""
    lineages = {_run_href(cell): _kept(point, cell) for cell in point["cells"]}
    most = _most(list(lineages.values()))
    width = _widths(most, 1.2, 4.6)
    x = _along(4, 226)
    ticks = (
        '<svg class="ticks" width="270" height="12" viewBox="0 0 270 12" aria-hidden="true">'
        f"{_epoch_ticks(x, 10)}</svg>\n"
    )
    spreads, losses = _spreads(point), _losses(point)
    drawn = ""
    for key, called in _held_groups(point):
        rows = columns = ""
        for loss, said in losses:
            cells = [cell for cell in point["cells"] if _rate(cell) == (key, loss)]
            if not cells:
                continue
            ran = sorted(
                (cell for cell in cells if cell["head"] is not None),
                key=lambda cell: (
                    cell["epochs"]["head"] is None,
                    cell["epochs"]["head"],
                    cell["seed"],
                ),
            )
            tiles = [_tile(cell, lineages[_run_href(cell)], x, width, linked) for cell in ran]
            one = spreads.get((key, loss))
            forked = one is not None and one["unforked"] < one["runs"]
            count = _plural(len(cells), "run")
            if forked:
                count += ", the fewest epochs without a fork on top"
            elif one is not None:
                count += ", none forked"
            head = f'<p class="of"><b>{_e(said)}</b><span>{count}</span></p>\n'
            lacking = _outcomeless(cells, linked)
            if forked:
                half = one["runs"] // 2
                columns += (
                    f"<div>\n{head}{ticks}{''.join(tiles[:half])}"
                    f'<div class="median">median {_number(one["median"])}</div>\n'
                    f"{''.join(tiles[half:])}{lacking}</div>\n"
                )
                continue
            rows += head
            if tiles:
                rows += f'{ticks}<div class="row">\n{"".join(tiles)}</div>\n'
            rows += lacking
        drawn += f'<div class="{key}">\n<h3>{_e(called)}</h3>\n{rows}'
        if columns:
            drawn += f'<div class="forest">\n{columns}</div>\n'
        drawn += "</div>\n"
    if all(cell["head"] is None for cell in point["cells"]):
        return drawn
    said = (
        f"{_READING} A column holds a rate's runs, the fewest epochs without a fork on top, and "
        "its dashed line is at their median, the number the graph over main draws. Hover a line "
        "for its numbers"
    )
    if linked:
        said += (
            ", or open a run for its labels, its rounds, its text and the commands that run it "
            "again and download its log"
        )
    said += "."
    sketched = (cell for cell in point["cells"] if lineages[_run_href(cell)] is None)
    if any(cell["head"] is not None for cell in sketched):
        said += _SKETCHED
    return f'<p class="reading">{said}</p>\n' + _legend(*_lines_key(most, width)) + drawn


def _tile(
    cell: dict,
    lineage: Optional[dict],
    x: Callable[[float], float],
    width: Callable[[int], float],
    linked: bool,
) -> str:
    """A run of a point among its rate's: its seed and where it first forked, beside its tree,
    `lineage` its lines, with its ring; the run in a few words on hover, and, where `linked`, a
    link to its page."""
    pad, pitch = 8, 7
    height = pad * 2 + (_rows_of(lineage) - 1) * pitch
    tag, href = ("a", f' href="{_e(quote(_run_href(cell)))}"') if linked else ("div", "")
    return (
        f'<{tag} class="tile"{href} title="{_e(simulate.words(cell))}">'
        f'<span class="at">seed {cell["seed"]}<br>{_first_fork(cell, "head", "<b>{}</b>")}</span>'
        f"{_svg(270, height, _job(cell))}{_epoch_lines(x, 0, height)}"
        f"{_small(cell, 'head', lineage, x, pad, pitch, width, 234)}</svg></{tag}>\n"
    )


def _outcomeless(cells: Sequence[dict], linked: bool) -> str:
    """The runs of `cells` that have no outcome in a line, by why they have none, each seed a
    link to its run's page where `linked`; nothing where each has one."""
    said = []
    for why, unbuilt in (("does not build", True), ("no outcome", False)):
        seeds = [
            _link(_run_href(cell), str(cell["seed"])) if linked else str(cell["seed"])
            for cell in sorted(cells, key=lambda cell: cell["seed"])
            if cell["head"] is None and cell["unbuilt"]["head"] == unbuilt
        ]
        if seeds:
            said.append(f"{why}: seed{'s' if len(seeds) > 1 else ''} {', '.join(seeds)}")
    return f'<p class="of muted">{" · ".join(said)}</p>\n' if said else ""


def _reruns(point: dict) -> str:
    """Under the trees of a point whose runs have no page: a fold a rate with the commands that
    run its first seed again, how they run another seed, and the command that downloads the logs
    of its runs, where a job of them built chat-sim and so uploaded any."""
    drawn = (
        "<h3>Run one yourself</h3>\n"
        '<p class="reading">A fold a rate holds the commands that run its first seed again, and '
        "says how they run another."
    )
    built = any(not cell["unbuilt"]["head"] for cell in point["cells"])
    if built and _WORKFLOW_RUN.fullmatch(point["run_url"]):
        drawn += (
            " Under them is the command that downloads the logs of the rate's runs: the site "
            "keeps none, each job uploads its runs' as an artifact of the workflow run, and "
            f"GitHub keeps it {LOGS_KEPT} days."
        )
    drawn += "</p>\n"
    for name, by_seed in _by_rate(point["cells"]):
        cells = [by_seed[seed] for seed in sorted(by_seed)]
        first = cells[0]
        numbered = _numbered(first["run"] - first["seed"])
        again = "\n".join(simulate.again(first, point["logos_chat"], point["chat_sim"]))
        drawn += (
            f"<details>\n<summary>{_e(simulate.GROUPS[group(first)])} {_e(name)} "
            f'<span class="muted">{_plural(len(cells), "run")}</span></summary>\n'
            f'<p class="side">Run seed {first["seed"]} again ({_NEEDS}):</p>\n'
            f"<pre>{_e(again)}</pre>\n"
            f'<p class="side">For another seed, give <code>--from</code> {numbered} and name '
            "the seed in <code>--out</code>.</p>\n"
        )
        logs = _logs(point["run_url"], first["scenario"], ("revision",), "logs")
        if logs and not all(cell["unbuilt"]["head"] for cell in cells):
            drawn += (
                '<p class="side">The logs of its runs, a run\'s <code>group.log</code> under its '
                f"number, {numbered}:</p>\n<pre>{_e(logs)}</pre>\n"
            )
        drawn += "</details>\n"
    return drawn


def _numbered(offset: int) -> str:
    """The number of a run of a sweep in words, by its seed, the two `offset` apart."""
    if not offset:
        return "the seed"
    return f"the seed plus {offset}" if offset > 0 else f"the seed less {-offset}"


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
    links = _file_links(run, href)
    return f'<p class="file">{links}</p>\n' if links else ""


def _file_links(run: Path, href: str) -> str:
    return " · ".join(_link(href + name, name) for name in TEXTS if (run / name).is_file())


def _logs(run_url: str, scenario: str, sides: Sequence[str], into: str) -> str:
    """The command that downloads into `into` the logs of the runs of `scenario` at `sides` of
    the workflow run at `run_url`, an artifact a job; nothing where `run_url` is not a workflow
    run's address."""
    found = _WORKFLOW_RUN.fullmatch(run_url)
    if found is None:
        return ""
    named = (LOGS.format(side=side, scenario=scenario) for side in sides)
    patterns = " ".join(f"--pattern '{name}'" for name in named)
    return f"gh run download {found[2]} --repo {found[1]} --dir {into} {patterns}"


def _log_said(run_url: str, cell: dict, sides: Sequence[str], whose: str, site: str) -> str:
    """The section of where the log of `cell`'s run is: that the site keeps none, and the command
    that downloads `whose`, the logs of the run's scenario at `sides` of the workflow run at
    `run_url`, with a link to what to read in one, in the guide of the site at `site`. Nothing
    without a side, or where `run_url` is not a workflow run's address."""
    logs = _logs(run_url, cell["scenario"], sides, "logs") if sides else ""
    if not logs:
        return ""
    return (
        "<h2>The log</h2>\n"
        '<p class="reading">The site keeps no log: each job uploads its runs\' as an artifact '
        f"of the workflow run, which GitHub keeps {LOGS_KEPT} days. This downloads {whose}, "
        f"and the run's log is <code>group.log</code> under run {cell['run']}"
        f"{' of each' if len(sides) == 2 else ''}. What to read in it: the guide's "
        f"{_link(site + _INTO_THE_LOG, 'Into the log')}.</p>\n"
        f"<pre>{_e(logs)}</pre>\n"
    )


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


def _ends(ends: List[dict], x: float, lowest: float) -> List[str]:
    """The group and its newest median at the end of each line, pushed apart where two meet."""
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
        drawn.append(f'<g class="{one["kind"]}">')
        if abs(one["at"] - one["y"]) > 1:
            drawn.append(
                f'<line class="leader" x1="{x + 4:.1f}" y1="{one["y"]:.1f}" x2="{x + 9:.1f}" '
                f'y2="{one["at"]:.1f}"/>'
            )
        drawn.append(
            f'<rect class="key" x="{x + 10:.1f}" y="{one["at"] - 1.5:.1f}" width="10" height="3" '
            'rx="1.5"/>'
        )
        drawn.append(
            f'<text class="label" x="{x + 24:.1f}" y="{one["at"] + 4:.1f}">'
            f"{_e(one['name'])} {_number(one['value'])}</text>"
        )
        drawn.append("</g>")
    return drawn


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


def _legend(*items: Tuple[str, str]) -> str:
    """A legend: each item a key drawn and the words it stands for."""
    return (
        '<p class="legend">'
        + "".join(f"<span>{key}{_e(said)}</span>" for key, said in items)
        + "</p>\n"
    )


def _line_key(kind: str) -> str:
    return (
        f'<svg class="{kind}" width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">'
        '<line class="series" x1="1" x2="21" y1="5" y2="5"/></svg>'
    )


def _band_key() -> str:
    return (
        '<svg width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">'
        '<rect class="wash" x="1" y="1" width="20" height="8"/></svg>'
    )


def _goal_key() -> str:
    return (
        '<svg class="trend" width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">'
        '<line class="goal" x1="1" x2="21" y1="5" y2="5"/></svg>'
    )


def _changed_key() -> str:
    return (
        '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">'
        '<circle class="any" cx="6" cy="6" r="4"/></svg>'
    )


def _before_key(kind: str) -> str:
    return (
        f'<svg class="dumb {kind}" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">'
        '<circle class="before" cx="6" cy="6" r="3.5"/></svg>'
    )


def _after_key(kind: str) -> str:
    return (
        f'<svg class="dumb {kind}" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">'
        '<circle class="after" cx="6" cy="6" r="4"/></svg>'
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
