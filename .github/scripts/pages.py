#!/usr/bin/env python3
"""Writes the repository's Pages site from the text CI's sim jobs upload.

`report` writes the report of one CI run to `reports/KEY/` of the site, replacing what was
there unless that is the report of a run of a higher number: KEY is `master` for a push to
master and `pr-N` for pull request N. `index` writes the site's landing page, and
`reports/index.html` from the `meta.json` of each report; one it cannot read is left out, with
a warning.

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
from typing import Callable, Optional, Sequence
from urllib.parse import quote

# The scenarios of the job "Sim in one process", in the order it runs them.
FIFTY_EPOCHS = ("50-epochs", "50-epochs-waku")

AS_EXPECTED = "as expected"
NOT_AS_EXPECTED = "not as expected"
NO_OUTCOME = "no outcome"

_KEY = re.compile(r"master|pr-([1-9][0-9]*)")
_SHA = re.compile(r"[0-9a-f]{40}")
_URL = re.compile(r"https?://[^\s\"'<>]+")
_TIME = "%Y-%m-%dT%H:%M:%SZ"
_RUN = re.compile(r"[0-9]+")
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
    """A report's meta.json that cannot be read, and why."""


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
