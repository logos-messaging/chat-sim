import contextlib
import copy
import html
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pages
import simulate

SHA = "0123456789abcdef0123456789abcdef01234567"
RUNS_URL = "https://github.com/logos-messaging/chat-sim/actions/runs"
RUN_URL = f"{RUNS_URL}/42"
COMMIT_URL = f"https://github.com/logos-messaging/chat-sim/commit/{SHA}"
# A logos-chat commit, the base it is compared with, and the chat-sim commit a run measures.
REVISION = "89abcdef0123456789abcdef0123456789abcdef"
BASE = "fedcba9876543210fedcba9876543210fedcba98"
MEASUREMENT = "76543210fedcba9876543210fedcba9876543210"
ROOT = Path(__file__).resolve().parents[2]
# What the GroupV2 jobs of a pr suite of HELD_GOALS' goal uploaded at a revision, seeds 0 and 1 of
# 0.1, 1 and 5% loss, each job's artifact in a directory of its own, cut to what tools/sim matrix
# reads.
UPLOADED = ROOT / "tools" / "sim" / "tests" / "fixtures" / "matrix"
GOAL = "50-epochs-by-saro-waku-loss-{}"
# The goal of each group, by the group's name in a scenario: UPLOADED holds GroupV2's runs of it,
# and the tests copy them as GroupV3's.
HELD_GOALS = {"v2": GOAL, "v3": "50-epochs-by-saro-waku-loss-{}-groupv3"}
# What a pull request's report page says each scenario of the runs it is given loses.
WELCOMED = (
    "A row is a rate of loss: each member loses that share of the frames it receives, at random, "
    "Welcomes among them."
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def edited(report: dict, changed: dict) -> None:
    """Gives `report`, a run.json, the fields `changed` gives it. Two are its grow's: `behind`,
    and `first_fork`, which has no line off the trunk fork before that epoch and the first one
    fork at it, or with `None` has every line off the trunk gone and its members on the trunk."""
    grown = {field: changed[field] for field in ("behind", "first_fork") if field in changed}
    # A copy, so that a grow two tests give their runs is not the one this changes below.
    given = copy.deepcopy({field: held for field, held in changed.items() if field not in grown})
    report.update(given)
    grow = report["grow"]
    if "behind" in grown:
        grow["behind"] = grown["behind"]
    if "first_fork" in grown:
        at, (trunk, *off) = grown["first_fork"], grow["lines"]
        if at is None:
            trunk["members"] += [member for line in off for member in line["members"]]
            grow.update(lines=[trunk], main=1)
        else:
            first = min(off, key=lambda line: line["forked_at"])
            for line in off:
                if line is first or line["forked_at"] < at:
                    line["forked_at"] = at


def reported(outcome: dict) -> dict:
    """`outcome` as one of an earlier measurement holds it, which holds no first fork and says
    how its run came out by the verdict `forks` gave it."""
    outcome["verdict"] = simulate.came_out(outcome)
    del outcome["first_fork"]
    return outcome


def unpaired(held: dict) -> dict:
    """`held`, a point, as one from before the cells held `d` holds it, whose cells hold `moved`,
    `null` in a point, in its place."""
    for cell in held["cells"]:
        del cell["d"]
        cell["moved"] = None
    return held


def unjoined(held: dict) -> dict:
    """`held`, a point, as one of an earlier measurement holds it, `unpaired`, whose outcomes are
    `reported` and whose outcomes and summaries do not count the members that never joined."""
    for cell in unpaired(held)["cells"]:
        for side in ("head", "base"):
            if cell.get(side) is not None:
                del reported(cell[side])["never_joined"]
    for one in held["summary"]:
        del one["never_joined"]
    return held


def unforked(held: dict) -> dict:
    """`held` as one of a measurement earlier still holds it, whose summaries say nothing of how
    the runs forked either."""
    for one in unjoined(held)["summary"]:
        for field in ("split_at", "lines_off_trunk", "seated"):
            del one[field]
    return held


class Parsed(HTMLParser):
    """What a page holds: tags, links, ids, text, and the text of each h1 and each row's cells."""

    def __init__(self, page: str):
        super().__init__()
        self.tags = set()
        self.links: List[str] = []
        self.ids = set()
        self.text = ""
        self.h1: List[str] = []
        self.heading = False
        self.rows: List[List[str]] = []
        self.cell = False
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.add(tag)
        if tag == "a":
            self.links.append(attrs["href"])
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "h1":
            self.h1.append("")
            self.heading = True
        if tag == "tr":
            self.rows.append([])
        if tag in ("td", "th"):
            self.rows[-1].append("")
            self.cell = True

    def handle_endtag(self, tag):
        if tag == "h1":
            self.heading = False
        if tag in ("td", "th"):
            self.cell = False

    def handle_data(self, data):
        self.text += data
        if self.heading:
            self.h1[-1] += data
        if self.cell:
            self.rows[-1][-1] += data


class Site(unittest.TestCase):
    """A site that starts empty, as a new gh-pages checkout does, and the artifacts of a run."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.site = self.scratch / "site"
        self.site.mkdir()
        self.artifacts = self.scratch / "artifacts"
        self.expected = self.scratch / "expected"
        self.expected.mkdir()

    def swept(self, *names: str) -> None:
        """Has CI sweep the scenarios `names`."""
        for name in names:
            write(self.expected / f"{name}.json", "[]\n")

    def sweep(self, name: str, status: Optional[str], runs: int = 2, text: str = "") -> None:
        """Uploads a sweep of `runs` runs of a `grow` step, which `forks` does not report on, whose
        compare exited `status`, or left none."""
        out = self.artifacts / name
        write(out / "sweep.txt", f"{runs} of {runs} runs went as expected\n")
        write(out / "compare.txt", f"compare of {name}\n")
        if status is not None:
            write(out / "compare.status", f"{status}\n")
        for run in range(1, runs + 1):
            write(out / str(run) / "run.txt", f"run {run} of {name}\n{text}")

    def ran(self, name: str) -> None:
        """Uploads a run of the job in one process."""
        write(self.artifacts / name / "run.txt", f"{name}: the report is as expected\n")
        write(self.artifacts / name / "report.txt", "conversation 4d54ad3236\n")

    def report(
        self,
        key: str = "master",
        title: str = "",
        time: str = "2026-10-22T07:30:15Z",
        run_number: int = 1,
    ) -> str:
        """Writes the report of run `run_number`, and returns what the command printed."""
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            status = pages.main(
                [
                    "report",
                    str(self.artifacts),
                    str(self.site),
                    "--expected",
                    str(self.expected),
                    "--key",
                    key,
                    f"--title={title}",
                    "--sha",
                    SHA,
                    "--commit-url",
                    COMMIT_URL,
                    "--run-url",
                    RUN_URL,
                    "--run-number",
                    str(run_number),
                    "--time",
                    time,
                ]
            )
        self.assertEqual(status, 0)
        return printed.getvalue()

    def index(self) -> List[str]:
        """Writes the index, and returns the lines it warns with."""
        warned = io.StringIO()
        with contextlib.redirect_stderr(warned):
            self.assertEqual(pages.main(["index", str(self.site)]), 0)
        return warned.getvalue().splitlines()

    def page(self, *parts: str) -> str:
        return self.site.joinpath(*parts).read_text(encoding="utf-8")

    def meta(self, key: str) -> dict:
        return json.loads(self.page("reports", key, "meta.json"))

    def tally(self, key: str) -> Tuple[int, int]:
        """How many sweeps came out as expected, of how many, as the report's meta.json says."""
        meta = self.meta(key)
        return meta["as_expected"], meta["sweeps"]

    def refused(self, *argv: str) -> str:
        """The last line the command complains with, as it exits 2."""
        complained = io.StringIO()
        with self.assertRaises(SystemExit) as refused:
            with contextlib.redirect_stderr(complained):
                pages.main(list(argv))
        self.assertEqual(refused.exception.code, 2)
        return complained.getvalue().splitlines()[-1]


class Report(Site):
    def test_a_sweep_comes_out_by_the_status_compare_exited_with(self):
        self.swept("grow-a", "grow-b", "grow-c", "grow-d", "grow-e")
        self.sweep("grow-a", "0")
        self.sweep("grow-b", "1")
        self.sweep("grow-c", None)
        self.sweep("grow-d", "2")
        self.sweep("grow-e", "signal 15")
        self.report()
        self.assertEqual(
            Parsed(self.page("reports", "master", "index.html")).rows,
            [
                ["Scenario", "Outcome"],
                ["grow-a", "as expected"],
                ["grow-b", "not as expected"],
                ["grow-c", "no outcome"],
                ["grow-d", "no outcome"],
                ["grow-e", "no outcome"],
            ],
        )
        self.assertEqual(self.tally("master"), (1, 5))

    def test_a_sweep_whose_job_uploaded_nothing_has_no_outcome(self):
        self.swept("grow-a", "grow-b")
        self.sweep("grow-a", "0")
        self.report()
        parsed = Parsed(self.page("reports", "master", "index.html"))
        self.assertEqual(parsed.rows[1:], [["grow-a", "as expected"], ["grow-b", "no outcome"]])
        self.assertIn("grow-b no outcome\nThe job uploaded nothing.", parsed.text)
        self.assertEqual(self.tally("master"), (1, 2))

    def test_a_run_that_uploaded_nothing_at_all_is_a_report_of_no_outcomes(self):
        self.swept("grow-a")
        self.report()
        parsed = Parsed(self.page("reports", "master", "index.html"))
        self.assertEqual(parsed.rows[1:], [["grow-a", "no outcome"]])
        self.assertEqual(parsed.text.count("The job uploaded nothing."), 1)
        nothing = "Nothing of this run was uploaded: it did not run, or wrote nothing."
        self.assertEqual(parsed.text.count(nothing), 2)

    def test_a_title_and_the_text_of_a_run_are_escaped(self):
        title = """--key=<script>alert("title")</script> & 'quoted'"""
        held = """<img src=x onerror="alert('run')"> & </pre>"""
        self.swept("grow-a")
        self.sweep("grow-a", "0", text=held)
        self.report("pr-7", title)
        self.index()
        for page in (("reports", "pr-7", "index.html"), ("reports", "index.html")):
            parsed = Parsed(self.page(*page))
            self.assertIn(f"#7 {title}", parsed.text)
            self.assertNotIn("<script", self.page(*page))
            self.assertNotIn("script", parsed.tags)
        parsed = Parsed(self.page("reports", "pr-7", "index.html"))
        self.assertIn(held, parsed.text)
        self.assertNotIn("img", parsed.tags)

    def test_a_pull_request_s_report_heads_with_what_ran_the_commit_the_run_and_when(self):
        self.swept("grow-a")
        self.sweep("grow-a", "0")
        self.report("pr-7", "Hold the group at fifty", time="2026-10-22T07:30:15Z")
        page = self.page("reports", "pr-7", "index.html")
        parsed = Parsed(page)
        self.assertEqual(parsed.h1, ["#7 Hold the group at fifty"])
        self.assertEqual(parsed.links[:4], ["../../", "../", COMMIT_URL, RUN_URL])
        self.assertIn("2026-10-22 07:30 UTC", parsed.text)
        self.assertIn("details", parsed.tags)
        self.assertNotIn("<details open", page)

    def test_every_file_the_page_shows_is_beside_it_and_every_anchor_has_its_section(self):
        self.swept("grow-a", "grow-b")
        self.sweep("grow-a", "1", runs=3)
        first, second = pages.FIFTY_EPOCHS
        self.ran(first)
        self.ran(second)
        (self.artifacts / second / "run.txt").unlink()
        self.report()
        out = self.site / "reports" / "master"
        parsed = Parsed(self.page("reports", "master", "index.html"))
        files = [link for link in parsed.links if link.endswith(".txt")]
        self.assertEqual(len(files), 2 + 3 + 2 + 1)
        for link in files:
            self.assertEqual((out / link).read_bytes(), (self.artifacts / link).read_bytes())
        anchors = {link[1:] for link in parsed.links if link.startswith("#")}
        self.assertEqual(anchors, {"held", "fifty-epochs", "grow-a", "grow-b"})
        self.assertLessEqual(anchors, parsed.ids)
        self.assertIn(f"{second}\nrun.txt was not uploaded", parsed.text)
        self.assertEqual(parsed.text.count("was not uploaded"), 1)

    def test_a_report_replaces_the_one_before_it_whole(self):
        self.swept("grow-a")
        stale = self.site / "reports" / "master" / "grow-a" / "9" / "run.txt"
        write(stale, "an earlier run\n")
        self.sweep("grow-a", "0")
        self.report()
        self.assertFalse(stale.exists())
        self.assertTrue((stale.parent.parent / "1" / "run.txt").is_file())

    def test_the_report_of_a_newer_run_is_kept_against_an_older_one(self):
        self.swept("grow-a")
        self.sweep("grow-a", "0")
        self.report(run_number=8)
        page = self.page("reports", "master", "index.html")
        self.sweep("grow-a", "1")
        printed = self.report(run_number=7)
        self.assertEqual(
            printed, "Kept reports/master/: it holds the report of run 8, newer than run 7.\n"
        )
        self.assertEqual(self.page("reports", "master", "index.html"), page)
        self.assertEqual(self.meta("master")["run_number"], 8)
        self.assertEqual(self.tally("master"), (1, 1))

    def test_the_report_of_an_older_run_is_replaced_by_a_newer_one(self):
        self.swept("grow-a")
        self.sweep("grow-a", "0")
        self.report(run_number=7)
        self.sweep("grow-a", "1")
        self.assertEqual(self.report(run_number=8), "")
        self.assertEqual(self.meta("master")["run_number"], 8)
        self.assertEqual(self.tally("master"), (0, 1))

    def test_a_re_run_replaces_the_report_of_its_run(self):
        self.swept("grow-a")
        self.sweep("grow-a", "1")
        self.report(run_number=8)
        self.sweep("grow-a", "0")
        self.assertEqual(self.report(run_number=8), "")
        self.assertEqual(self.tally("master"), (1, 1))

    def test_a_report_whose_meta_json_is_refused_is_replaced_whatever_its_run_number(self):
        self.swept("grow-a")
        self.sweep("grow-a", "0")
        self.report(run_number=8)
        meta = dict(self.meta("master"), run_url="javascript:alert(1)")
        write(self.site / "reports" / "master" / "meta.json", json.dumps(meta))
        self.report(run_number=7)
        self.assertEqual(self.meta("master")["run_number"], 7)
        self.assertEqual(self.meta("master")["run_url"], RUN_URL)

    def test_a_report_is_kept_only_against_an_older_run_of_its_own_key(self):
        self.swept("grow-a")
        self.sweep("grow-a", "0")
        self.report(run_number=9)
        self.assertEqual(self.report(key="pr-7", title="Hold the group", run_number=1), "")
        self.assertEqual(self.meta("pr-7")["run_number"], 1)

    def test_a_command_line_that_names_no_run_is_refused(self):
        self.swept("grow-a")
        given = ["report", str(self.artifacts), str(self.site), "--expected", str(self.expected)]
        given += ["--run-number", "7"]
        what = ["--sha", SHA, "--commit-url", COMMIT_URL, "--run-url", RUN_URL]
        when = ["--time", "2026-10-22T07:30:15Z"]
        self.assertIn("--key", self.refused(*given, "--key", "main", *what, *when))
        self.assertIn("--key", self.refused(*given, "--key", "pr-0", *what, *when))
        titled = ["--key", "master", "--title", "x"]
        self.assertIn("--title", self.refused(*given, *titled, *what, *when))
        self.assertIn("--time", self.refused(*given, "--key", "master", *what, "--time", "today"))
        numbered = ["--key", "master", "--run-number", "seven"]
        self.assertIn("--run-number", self.refused(*given, *numbered, *what, *when))
        bad = ["--sha", SHA, "--commit-url", "javascript:alert(1)", "--run-url", RUN_URL]
        self.assertIn("--commit-url", self.refused(*given, "--key", "master", *bad, *when))
        self.assertFalse((self.site / "reports").exists())


class Index(Site):
    def test_master_comes_first_then_the_pull_requests_highest_number_first(self):
        self.swept("grow-a")
        for key in ("pr-3", "pr-12", "pr-1", "master", "pr-100"):
            self.report(key, "" if key == "master" else f"title of {key}")
        self.index()
        self.assertEqual(
            [row[0] for row in Parsed(self.page("reports", "index.html")).rows[1:]],
            [
                "master",
                "#100 title of pr-100",
                "#12 title of pr-12",
                "#3 title of pr-3",
                "#1 title of pr-1",
            ],
        )

    def test_the_index_reads_back_what_report_wrote(self):
        self.swept("grow-a", "grow-b")
        self.sweep("grow-a", "0")
        self.sweep("grow-b", "1")
        self.report("pr-12", "Hold the group at fifty", time="2026-10-22T07:30:15Z")
        self.index()
        parsed = Parsed(self.page("reports", "index.html"))
        self.assertEqual(
            parsed.rows,
            [
                ["What ran", "Commit", "When (UTC)", "Sweeps", "", ""],
                [
                    "#12 Hold the group at fifty",
                    SHA[:7],
                    "2026-10-22 07:30",
                    "1 of 2 as expected",
                    "report",
                    "CI run",
                ],
            ],
        )
        self.assertEqual(parsed.links, ["../", COMMIT_URL, "pr-12/", RUN_URL])
        self.assertTrue((self.site / "reports" / "pr-12" / "index.html").is_file())

    def test_the_landing_page_holds_three_links_the_guide_the_reports_and_the_graph_over_main(self):
        self.index()
        parsed = Parsed(self.page("index.html"))
        self.assertEqual(parsed.links, ["guide/", "reports/", "logos-chat/"])
        self.assertIn("Guide", parsed.text)
        self.assertIn("Reports", parsed.text)
        self.assertIn("logos-chat over main", parsed.text)
        self.assertIn("chat-sim", parsed.text)

    def test_an_empty_site_gets_its_pages_and_nojekyll(self):
        self.index()
        listed = Parsed(self.page("reports", "index.html")).text
        self.assertIn("No CI run has published a report yet.", listed)
        self.assertTrue((self.site / ".nojekyll").is_file())

    def test_a_meta_json_that_report_does_not_write_is_refused(self):
        self.swept("grow-a")
        for key in ("master", "pr-5", "pr-3"):
            self.report(key, "" if key == "master" else f"title of {key}")
        meta = self.meta("pr-5")
        written = self.site / "reports" / "pr-5" / "meta.json"
        for field, value in (("run_url", "javascript:alert(1)"), ("number", 6), ("key", "master")):
            write(written, json.dumps(dict(meta, **{field: value})))
            warned = self.index()
            self.assertEqual(len(warned), 1)
            self.assertTrue(warned[0].startswith(f"::warning::{written}: "), warned[0])
            self.assertIn(field.split("_")[0], warned[0])
            listed = [row[0] for row in Parsed(self.page("reports", "index.html")).rows[1:]]
            self.assertEqual(listed, ["master", "#3 title of pr-3"])


def sha(number: int) -> str:
    """The `number`th of a run of made-up commits, each with a short form of its own."""
    return f"{number:07x}".ljust(40, "0")


def time(minute: int) -> str:
    """The minute of 2026-11-09 a run ended at, or of a day after it."""
    day, minute = divmod(minute, 24 * 60)
    return f"2026-11-{9 + day:02d}T{minute // 60:02d}:{minute % 60:02d}:00Z"


class LogosChat(unittest.TestCase):
    """A site that starts empty, and copies of what the run jobs of simulate.yml uploaded."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.site = self.scratch / "site"
        self.site.mkdir()
        self.made = 0

    def uploaded(self, groupv3: bool = False, **edits: dict) -> Path:
        """A copy of UPLOADED, with `groupv3` each of its runs again as GroupV3's, under the name
        of the GroupV3 goal at its rate; each run with its run.txt, report.txt and report.json
        beside its run.json, and the fields `edits` gives a run changed as `edited` changes them,
        by its rate and seed, or by its group, rate and seed."""
        self.made += 1
        copy = self.scratch / f"uploaded-{self.made}"
        shutil.copytree(UPLOADED, copy)
        if groupv3:
            for report in list(copy.rglob("run.json")):
                job, scenario, run = report.parts[-4:-1]
                name = HELD_GOALS["v3"].format(scenario.removeprefix(GOAL.format("")))
                shutil.copytree(report.parent, copy / job.replace(scenario, name) / name / run)
        for report in copy.rglob("run.json"):
            for name in ("run.txt", "report.txt", "report.json"):
                write(report.parent / name, f"{name} of {report.parent.relative_to(copy)}\n")
        for cell, changed in edits.items():
            report = json.loads(self.run_json(copy, cell).read_text(encoding="utf-8"))
            edited(report, changed)
            self.run_json(copy, cell).write_text(json.dumps(report), encoding="utf-8")
        return copy

    def run_json(self, side: Path, cell: str) -> Path:
        """The run.json of a cell under `side`, by its rate and seed, `1/0`, a cell of GroupV2's,
        or by its group, rate and seed, `v3/1/0`."""
        *named, rate, seed = cell.split("/")
        goal = HELD_GOALS[named[0]] if named else GOAL
        scenario, number = goal.format(rate), int(seed) + 1
        return side / f"sim-revision-{scenario}-{number}" / scenario / str(number) / "run.json"

    def seeded(self, side: Path, rate: str, seeds: Sequence[int], **edits: dict) -> Path:
        """`side` with runs of GroupV2 at `rate` at `seeds` too, each a copy of seed 0's at its own
        seed with its text, and the fields `edits` gives a run changed, by its rate and seed."""
        first = self.run_json(side, f"{rate}/0")
        for seed in seeds:
            copy = self.run_json(side, f"{rate}/{seed}")
            shutil.copytree(first.parent, copy.parent)
            report = dict(json.loads(first.read_text(encoding="utf-8")), seed=seed)
            edited(report, edits.get(f"{rate}/{seed}", {}))
            copy.write_text(json.dumps(report), encoding="utf-8")
        return side

    def matrix(
        self,
        head: Path,
        base: Optional[Path] = None,
        asked: Sequence[dict] = (),
    ) -> Path:
        """The file tools/sim matrix writes of the runs under `head`, beside those under `base`,
        with each run of the sweeps `asked` of each side a cell."""
        self.made += 1
        out = self.scratch / f"matrix-{self.made}.json"
        command = [sys.executable, str(ROOT / "tools" / "sim"), "matrix", str(head)]
        command += ["--out", str(out)] + ([] if base is None else ["--base", str(base)])
        if asked:
            write(out.with_suffix(".asked"), json.dumps(list(asked)))
            command += ["--asked", str(out.with_suffix(".asked"))]
        subprocess.run(command, check=True)
        return out

    def commits(self, *messages: str, status: str = "ahead") -> Path:
        """What logos-chat's compare API answers, with its status and a commit of each message,
        cut."""
        self.made += 1
        out = self.scratch / f"commits-{self.made}.json"
        given = [{"commit": {"message": one}} for one in messages]
        write(out, json.dumps({"status": status, "commits": given}))
        return out

    def ran(self, run_number: int, minute: int, run_url: str = RUN_URL) -> List[str]:
        return [
            "--chat-sim",
            SHA,
            "--run-url",
            run_url,
            "--run-number",
            str(run_number),
            "--time",
            time(minute),
        ]

    def run_pages(self, *argv: str) -> Tuple[int, str, str]:
        """The status a command exits with, and what it printed and warned with."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                status = pages.main(list(argv))
            except SystemExit as exit:
                status = exit.code
        return status, out.getvalue(), err.getvalue()

    def point(
        self,
        revision: str = REVISION,
        minute: int = 0,
        run_number: int = 1,
        measurement: str = MEASUREMENT,
        runs: Optional[Path] = None,
        matrix: Optional[Path] = None,
        commits: Optional[Path] = None,
        run_url: str = RUN_URL,
    ) -> str:
        """Writes the point of a run at `revision`, and returns what the command printed."""
        runs = self.uploaded() if runs is None else runs
        matrix = self.matrix(runs) if matrix is None else matrix
        given = [str(matrix), str(runs), str(self.site), "--revision", revision]
        given += ["--measurement", measurement, *self.ran(run_number, minute, run_url)]
        given += [] if commits is None else ["--commits", str(commits)]
        status, out, err = self.run_pages("point", *given)
        self.assertEqual(status, 0, err)
        return out

    def at(self, revision: str = REVISION, measurement: str = MEASUREMENT) -> Path:
        return self.site / "logos-chat" / "main" / revision / measurement

    def read(self, revision: str = REVISION, measurement: str = MEASUREMENT) -> dict:
        return json.loads((self.at(revision, measurement) / "point.json").read_text("utf-8"))

    def files(self, directory: Path) -> List[str]:
        return sorted(
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
        )


class Point(LogosChat):
    def test_a_point_holds_the_cells_and_the_summary_of_its_matrix_and_what_ran(self):
        runs = self.uploaded()
        matrix = self.matrix(runs)
        self.point(runs=runs, matrix=matrix, minute=90, run_number=7)
        written, matrix = self.read(), json.loads(matrix.read_text(encoding="utf-8"))
        self.assertEqual(
            list(written),
            ["logos_chat", "measurement", "chat_sim", "time", "run_url", "run_number"]
            + ["merged", "previous", "cells", "summary", "lines"],
        )
        self.assertEqual(
            [written[field] for field in ("logos_chat", "measurement", "chat_sim", "time")],
            [REVISION, MEASUREMENT, SHA, "2026-11-09T01:30:00Z"],
        )
        self.assertEqual((written["run_url"], written["run_number"]), (RUN_URL, 7))
        self.assertEqual(written["cells"], matrix["cells"])
        self.assertEqual(written["summary"], matrix["summary"])
        self.assertEqual(len(written["cells"]), 6)
        self.assertEqual(written["cells"][2]["head"]["conversation"], "def2b1dd40")
        self.assertEqual(pages.read_point(self.at() / "point.json"), written)
        self.assertEqual(pages.points(self.site), [(self.at(), written)])

    def test_a_point_of_both_groups_holds_each_group_s_cells_and_summary_in_one_point_json(self):
        runs = self.uploaded(groupv3=True)
        matrix = self.matrix(runs)
        self.point(runs=runs, matrix=matrix)
        written, matrix = self.read(), json.loads(matrix.read_text(encoding="utf-8"))
        self.assertEqual(written["cells"], matrix["cells"])
        self.assertEqual(written["summary"], matrix["summary"])
        self.assertEqual(
            [(cell["group"], cell["scenario"], cell["seed"]) for cell in written["cells"]],
            [
                (group, HELD_GOALS[group].format(rate), seed)
                for group in ("v2", "v3")
                for rate in ("0.1", "1", "5")
                for seed in (0, 1)
            ],
        )
        self.assertEqual([one["group"] for one in written["summary"]], ["v2"] * 3 + ["v3"] * 3)
        # Each run's three files of text and its page.
        self.assertEqual(len(self.files(self.at())), 2 + 12 * 4)
        self.assertEqual(pages.read_point(self.at() / "point.json"), written)
        self.assertEqual(pages.points(self.site), [(self.at(), written)])

    def test_a_point_holds_each_run_s_lines_by_its_directory_as_its_run_json_has_them(self):
        self.point(runs=self.uploaded(**{"1/1": {"grow": GROW}, "5/0": {"grow": LOOPED}}))
        lines = self.read()["lines"]
        held = [
            {
                "from": line["from"],
                "forked_at": line["forked_at"],
                "epoch": line["epoch"],
                "members": len(line["members"]),
            }
            for line in GROW["lines"]
        ]
        self.assertEqual((lines[f"{GOAL.format(1)}/2/"], len(held)), (held, 3))
        # Lines that do not come down from one trunk are not a tree, and are not kept.
        self.assertNotIn(f"{GOAL.format(5)}/1/", lines)
        self.assertLess(set(lines), {pages._run_href(cell) for cell in self.read()["cells"]})
        self.assertEqual(pages.read_point(self.at() / "point.json"), self.read())

    def test_a_point_json_from_before_a_point_held_its_runs_lines_is_read(self):
        self.point()
        earlier = {field: held for field, held in self.read().items() if field != "lines"}
        write(self.at() / "point.json", json.dumps(earlier))
        self.assertEqual(pages.read_point(self.at() / "point.json"), earlier)
        self.assertEqual(pages.points(self.site), [(self.at(), earlier)])

    def test_each_run_s_text_is_kept_by_its_number_and_its_report_json_is_not(self):
        runs = self.uploaded()
        # A lone artifact downloads flat, and a run of a sweep of ten or more is named 01.
        lone = runs / "sim-revision-50-epochs-by-saro-waku-loss-5-2" / GOAL.format(5)
        (lone / "2").rename(lone / "02")
        lone.rename(runs / GOAL.format(5))
        self.point(runs=runs)
        texts = [
            f"{GOAL.format(rate)}/{run}/{name}"
            for rate in ("0.1", "1", "5")
            for run in (1, 2)
            for name in ("index.html", "report.txt", "run.json", "run.txt")
        ]
        self.assertEqual(self.files(self.at()), sorted(["index.html", "point.json", *texts]))
        for name in ("run.txt", "report.txt", "run.json"):
            self.assertEqual(
                (self.at() / GOAL.format(5) / "2" / name).read_bytes(),
                (runs / GOAL.format(5) / "02" / name).read_bytes(),
            )
        # The point's page and its runs', and no other page of the site.
        self.assertEqual(len(list(self.site.rglob("*.html"))), 1 + 6)

    def test_a_point_json_that_point_does_not_write_is_refused(self):
        self.point()
        path = self.at() / "point.json"
        written = self.read()

        def cell(**changed) -> dict:
            return dict(written, cells=[dict(written["cells"][0], **changed)])

        head = written["cells"][0]["head"]
        for said, point in (
            ("measurement is missing", {k: v for k, v in written.items() if k != "measurement"}),
            ("verdict is not a field of it", dict(written, verdict="same")),
            ("run_number is not of its kind", dict(written, run_number=True)),
            ("run_url is not an http(s) address", dict(written, run_url="javascript:alert(1)")),
            ("time is not of the form", dict(written, time="today")),
            ("chat_sim is not a commit", dict(written, chat_sim="0123456")),
            ("do not name its directory", dict(written, logos_chat=BASE)),
            (
                "merged[0].number is not of its kind",
                dict(written, merged=[{"number": "315", "title": "x"}]),
            ),
            (
                "previous.rerolled is missing",
                dict(written, previous={"logos_chat": BASE, "measurement": MEASUREMENT}),
            ),
            (
                "cells[0].head.members_digest is missing",
                cell(head={k: v for k, v in head.items() if k != "members_digest"}),
            ),
            ("cells[0].head.conversation is not an id", cell(head=dict(head, conversation="<b>"))),
            (
                "cells[0].head.never_joined is not of its kind",
                cell(head=dict(head, never_joined="2")),
            ),
            (
                "summary[0].never_joined.median is not of its kind",
                dict(
                    written,
                    summary=[dict(written["summary"][0], never_joined={"median": "0.5"})],
                ),
            ),
            ("cells[0].group 'v4' is none of a scenario's", cell(group="v4")),
            (
                "summary[0].group 'GroupV3' is none of a scenario's",
                dict(written, summary=[dict(written["summary"][0], group="GroupV3")]),
            ),
            ("summary[0] is not an object", dict(written, summary=[5])),
            ("lines is not of its kind", dict(written, lines=[])),
            (
                "lines['nothing/1/'] is of no run of its cells",
                dict(written, lines={"nothing/1/": []}),
            ),
            (
                f"lines['{GOAL.format(1)}/1/'] is not the lines of a run, down from one trunk",
                dict(
                    written,
                    lines={f"{GOAL.format(1)}/1/": [{"from": None, "forked_at": None, "epoch": 3}]},
                ),
            ),
            ("cells[0] has a base", cell(base=head)),
            (
                "cells[0].ended.head is not how a run ended",
                cell(ended={"head": "forked", "base": None}),
            ),
            (
                "summary[0].epochs.median is not of its kind",
                dict(
                    written,
                    summary=[
                        dict(
                            written["summary"][0],
                            epochs={"lowest": 1, "median": "38.5", "highest": 50},
                        )
                    ],
                ),
            ),
        ):
            with self.subTest(said=said):
                write(path, json.dumps(point))
                with self.assertRaises(pages.Unreadable) as refused:
                    pages.read_point(path)
                self.assertTrue(str(refused.exception).startswith(f"{path}: "))
                self.assertIn(said, str(refused.exception))
        write(path, "{")
        with self.assertRaises(pages.Unreadable):
            pages.read_point(path)

    def test_the_first_point_follows_none_and_merged_no_pull_request(self):
        self.point()
        self.assertEqual((self.read()["previous"], self.read()["merged"]), (None, []))

    def previous(self, revision: str, run_number: int) -> Tuple[int, str, str]:
        given = ["previous", str(self.site), "--revision", revision, "--measurement", MEASUREMENT]
        return self.run_pages(*given, "--run-number", str(run_number))

    def test_a_point_follows_the_one_of_the_highest_run_number_below_its_own(self):
        self.point(sha(1), minute=1, run_number=10)
        self.point(sha(3), minute=2, run_number=12, commits=self.commits())
        self.assertEqual(self.read(sha(3))["previous"]["logos_chat"], sha(1))
        # Run 11, dispatched before run 12, finishes after it.
        self.point(sha(2), minute=3, run_number=11, commits=self.commits())
        self.assertEqual(self.read(sha(2))["previous"]["logos_chat"], sha(1))
        # A re-run of run 10, hours later, still follows none.
        self.point(sha(1), minute=900, run_number=10)
        self.assertIsNone(self.read(sha(1))["previous"])
        self.assertEqual(self.previous(sha(3), 12), (0, f"previous={sha(2)}\n", ""))
        self.assertEqual(self.previous(sha(4), 13), (0, f"previous={sha(3)}\n", ""))
        self.assertEqual(self.previous(sha(1), 10), (0, "previous=\n", ""))

    def test_a_point_at_a_new_measurement_follows_the_one_of_its_commit_at_the_old(self):
        self.point(minute=1, run_number=1)
        identical = self.commits(status="identical")
        self.point(minute=2, run_number=2, measurement=sha(7), commits=identical)
        self.assertEqual(
            self.read(measurement=sha(7))["previous"],
            {"logos_chat": REVISION, "measurement": MEASUREMENT, "rerolled": False},
        )
        self.assertEqual(self.read(measurement=sha(7))["merged"], [])

    def test_a_point_whose_commit_does_not_come_after_the_point_it_follows_is_refused(self):
        self.point(sha(2), minute=1, run_number=1)
        runs = self.uploaded()
        given = [str(self.matrix(runs)), str(runs), str(self.site), "--revision", sha(1)]
        given += ["--measurement", MEASUREMENT, *self.ran(2, 2)]
        for status in ("behind", "diverged"):
            with self.subTest(status=status):
                commits = self.commits(status=status)
                said, out, err = self.run_pages("point", *given, "--commits", str(commits))
                self.assertEqual((said, out), (2, ""))
                self.assertIn(f"the revision is {status!r} of the point it follows", err)
        self.assertFalse(self.at(sha(1)).exists())

    def test_a_point_whose_runs_drew_other_ids_than_the_point_it_follows_is_re_rolled(self):
        self.point(sha(1), minute=1, run_number=1)
        moved = self.uploaded(**{"1/1": {"first_fork": 5}})
        self.point(sha(2), minute=2, run_number=2, runs=moved, commits=self.commits())
        self.assertFalse(self.read(sha(2))["previous"]["rerolled"])
        drawn = self.uploaded(**{"1/1": {"conversation": "7c3e9a01b2"}})
        self.point(sha(3), minute=3, run_number=3, runs=drawn, commits=self.commits())
        self.assertTrue(self.read(sha(3))["previous"]["rerolled"])
        # The cell whose ids moved has no outcome here, and drew none.
        missing = self.uploaded()
        self.run_json(missing, "1/1").unlink()
        write(self.run_json(missing, "1/1").with_name("unbuilt"), "")
        self.point(sha(4), minute=4, run_number=4, runs=missing, commits=self.commits())
        self.assertFalse(self.read(sha(4))["previous"]["rerolled"])

    def test_the_pull_requests_merged_are_read_from_the_commits_since_the_point_it_follows(self):
        self.point(sha(1), minute=1, run_number=1)
        commits = self.commits(
            "feat(core): run GroupV3 content through SDS (#315)\n\n### refactor(core): move",
            "fix(build): re-run the build script",
            "Merge pull request #197 from logos-messaging/mch/fix/build-order\n\n"
            "fix(build): re-run the build script when the native lib appears",
            "force signature",
        )
        self.point(sha(2), minute=2, run_number=2, commits=commits)
        self.assertEqual(
            self.read(sha(2))["merged"],
            [
                {"number": 315, "title": "feat(core): run GroupV3 content through SDS"},
                {
                    "number": 197,
                    "title": "fix(build): re-run the build script when the native lib appears",
                },
            ],
        )

    def test_commits_are_given_when_the_site_holds_a_point_before_and_only_then(self):
        runs = self.uploaded()
        matrix = self.matrix(runs)
        given = [str(matrix), str(runs), str(self.site), "--measurement", MEASUREMENT]
        status, _, err = self.run_pages(
            "point", *given, "--revision", sha(1), *self.ran(1, 1), "--commits", str(self.commits())
        )
        self.assertEqual(status, 2)
        self.assertIn("--commits", err)
        self.assertFalse((self.site / "logos-chat").exists())
        self.point(sha(1), minute=1, runs=runs, matrix=matrix)
        status, _, err = self.run_pages("point", *given, "--revision", sha(2), *self.ran(2, 2))
        self.assertEqual(status, 2)
        self.assertIn("--commits", err)
        unread = self.scratch / "unread.json"
        write(unread, json.dumps({"commits": [{"sha": sha(2)}]}))
        status, _, err = self.run_pages(
            "point", *given, "--revision", sha(2), *self.ran(2, 2), "--commits", str(unread)
        )
        self.assertEqual(status, 2)
        self.assertIn("not an answer of the compare API", err)
        self.assertFalse(self.at(sha(2)).exists())

    def test_a_command_line_that_names_no_run_or_a_matrix_with_a_base_is_refused(self):
        runs = self.uploaded()
        matrix = self.matrix(runs)
        given = [str(runs), str(self.site), "--measurement", MEASUREMENT, *self.ran(1, 1)]
        named = [*given, "--revision", REVISION]
        for flag, argv in (
            ("--revision", [str(matrix), *given, "--revision", "212cb05f"]),
            ("--run-url", [str(matrix), *named, "--run-url", "file:///x"]),
            ("--time", [str(matrix), *named, "--time", "today"]),
            ("MATRIX", [str(self.matrix(runs, runs)), *named]),
            ("MATRIX", [str(runs / "nothing.json"), *named]),
        ):
            with self.subTest(flag=flag, argv=argv):
                status, _, err = self.run_pages("point", *argv)
                self.assertEqual(status, 2)
                self.assertIn(flag, err.splitlines()[-1])
        self.assertFalse((self.site / "logos-chat").exists())

    def test_a_matrix_that_read_no_run_is_refused_and_one_whose_runs_could_not_build_is_kept(self):
        # What a run job at a revision logos-chat does not have leaves: nothing at all.
        nothing = self.scratch / "nothing"
        nothing.mkdir()
        asked = [{"scenario": GOAL.format(rate), "from": 1, "runs": 2} for rate in ("1", "5")]
        given = [str(self.matrix(nothing, asked=asked)), str(nothing), str(self.site)]
        given += ["--revision", REVISION, "--measurement", MEASUREMENT, *self.ran(1, 1)]
        status, out, err = self.run_pages("point", *given)
        self.assertEqual((status, out), (2, ""))
        self.assertIn("MATRIX: no run at the revision was read", err.splitlines()[-1])
        self.assertFalse((self.site / "logos-chat").exists())
        unbuilt = self.uploaded()
        for report in list(unbuilt.rglob("run.json")):
            for name in ("run.json", "run.txt", "report.txt", "report.json"):
                report.with_name(name).unlink()
            write(report.with_name("unbuilt"), "chat-sim does not build\n")
        self.point(runs=unbuilt)
        self.assertEqual(self.files(self.at()), ["index.html", "point.json"])
        self.assertEqual([cell["unbuilt"]["head"] for cell in self.read()["cells"]], [True] * 6)

    def test_the_point_of_a_newer_run_is_kept_against_an_older_one(self):
        self.point(run_number=8)
        written = self.read()
        moved = self.uploaded(**{"1/1": {"first_fork": 5}})
        printed = self.point(run_number=7, minute=5, runs=moved)
        self.assertEqual(
            printed,
            f"Kept logos-chat/main/{REVISION}/{MEASUREMENT}/: it holds the point of run 8, "
            "newer than run 7.\n",
        )
        self.assertEqual(self.read(), written)

    def test_a_re_run_replaces_the_point_of_its_run_whole(self):
        self.point(run_number=8)
        stale = self.at() / GOAL.format(1) / "9" / "run.txt"
        write(stale, "an earlier run\n")
        moved = self.uploaded(**{"1/1": {"first_fork": 5}})
        self.assertEqual(self.point(run_number=8, minute=5, runs=moved), "")
        self.assertFalse(stale.exists())
        self.assertEqual(self.read()["cells"][3]["epochs"]["head"], 5)
        self.assertEqual(self.read()["time"], time(5))

    def test_a_point_whose_point_json_is_refused_is_replaced_whatever_its_run_number(self):
        self.point(run_number=8)
        write(self.at() / "point.json", json.dumps(dict(self.read(), run_url="javascript:x")))
        self.point(run_number=7)
        self.assertEqual((self.read()["run_number"], self.read()["run_url"]), (7, RUN_URL))

    def test_a_point_json_that_is_refused_is_left_out_and_warned_of(self):
        self.point(sha(1), minute=1)
        written = self.at(sha(1)) / "point.json"
        write(written, json.dumps(dict(self.read(sha(1)), run_number="1")))
        status, out, err = self.previous(sha(2), 2)
        self.assertEqual((status, out), (0, "previous=\n"))
        self.assertEqual(err.splitlines(), [f"::warning::{written}: run_number is not of its kind"])


class Window(LogosChat):
    def test_past_the_newest_eleven_points_by_run_number_each_loses_its_runs_text_alone(self):
        runs = self.uploaded()
        matrix = self.matrix(runs)
        # The commits run from high to low and the times from late to early as the run numbers
        # rise, so that neither the order of the directories nor that of time is the runs'.
        printed = []
        for number in range(1, 14):
            printed.append(
                self.point(
                    sha(100 - number),
                    minute=100 - number,
                    run_number=number,
                    runs=runs,
                    matrix=matrix,
                    commits=None if number == 1 else self.commits(),
                )
            )
            if number == 1:
                write(self.at(sha(99)) / "index.html", "a page drawn from point.json\n")
        self.assertEqual(printed[:11], [""] * 11)
        self.assertEqual(
            printed[11:],
            [
                f"Removed the runs' text of logos-chat/main/{sha(100 - number)}/{MEASUREMENT}/: "
                f"past the newest 11 points.\n"
                for number in (1, 2)
            ],
        )
        for number in range(1, 14):
            with self.subTest(number=number):
                point = self.at(sha(100 - number))
                held = self.files(point)
                if number == 1:
                    self.assertEqual(sorted(path.name for path in point.iterdir()), held)
                    self.assertEqual(held, ["index.html", "point.json"])
                else:
                    self.assertEqual(len(held), 2 if number == 2 else 2 + 6 * 4)
                self.assertIn("point.json", held)
                pages.read_point(point / "point.json")

    def test_a_point_from_before_points_held_lines_gets_them_before_its_text_goes(self):
        runs = self.uploaded(**{"1/1": {"grow": GROW}})
        matrix = self.matrix(runs)
        for number in range(1, 13):
            if number == 12:
                # Points 1 and 2 as a chat-sim from before a point held its runs' lines wrote them.
                for earlier in (1, 2):
                    held = self.read(sha(earlier))
                    del held["lines"]
                    write(self.at(sha(earlier)) / "point.json", json.dumps(held))
            self.point(
                sha(number),
                minute=number,
                run_number=number,
                runs=runs,
                matrix=matrix,
                commits=None if number == 1 else self.commits(),
            )
        newest = self.read(sha(12))["lines"]
        self.assertIn(f"{GOAL.format(1)}/2/", newest)
        # That command took point 1's text, past the newest eleven, and left point 2 its own.
        self.assertEqual(self.files(self.at(sha(1))), ["index.html", "point.json"])
        self.assertIn(f"{GOAL.format(1)}/2/run.json", self.files(self.at(sha(2))))
        self.assertEqual((self.read(sha(1))["lines"], self.read(sha(2))["lines"]), (newest, newest))

    def test_a_point_with_neither_its_runs_lines_nor_their_text_stays_as_it_is(self):
        self.point(sha(1), minute=1, run_number=1)
        held = self.read(sha(1))
        del held["lines"]
        write(self.at(sha(1)) / "point.json", json.dumps(held))
        for kept in self.at(sha(1)).glob("*/*/*"):
            kept.unlink()
        self.point(sha(2), minute=2, run_number=2, commits=self.commits())
        self.assertEqual(self.read(sha(1)), held)

    def test_each_point_command_draws_every_other_point_s_page_and_its_runs_pages_again(self):
        self.point(sha(1), minute=1, run_number=1)
        first = self.at(sha(1))
        # As an earlier chat-sim left the point: a page of its own shape, and no page a run.
        write(first / "index.html", "a page an earlier chat-sim drew\n")
        for page in first.glob("*/*/index.html"):
            page.unlink()
        self.point(sha(2), minute=2, run_number=2, commits=self.commits())
        again = (first / "index.html").read_text(encoding="utf-8")
        self.assertIn(f"<title>chat-sim: logos-chat main {sha(1)[:7]}</title>", again)
        self.assertEqual(len(list(first.glob("*/*/index.html"))), 6)

    def test_a_point_json_that_is_refused_is_left_alone(self):
        refused = self.site / "logos-chat" / "main" / sha(200) / MEASUREMENT
        write(refused / "point.json", "{}\n")
        write(refused / GOAL.format(1) / "1" / "run.txt", "a run\n")
        runs = self.uploaded()
        matrix = self.matrix(runs)
        for minute in range(12):
            self.point(
                sha(minute),
                minute=minute,
                run_number=minute + 1,
                runs=runs,
                matrix=matrix,
                commits=None if minute == 0 else self.commits(),
            )
        self.assertEqual(self.files(refused), [f"{GOAL.format(1)}/1/run.txt", "point.json"])
        self.assertEqual(self.files(self.at(sha(0))), ["index.html", "point.json"])


class PullRequest(LogosChat):
    def pr(
        self,
        run_number: int = 1,
        minute: int = 0,
        head: Optional[Path] = None,
        base: Optional[Path] = UPLOADED,
    ) -> str:
        """Writes the run of pull request 316 at REVISION against BASE, or against none, and
        returns what the command printed."""
        head = self.uploaded() if head is None else head
        given = [str(self.matrix(head, base)), str(head), str(self.site), "--pr", "316"]
        given += ["--revision", REVISION, "--measurement", MEASUREMENT]
        given += ["--scenarios", str(SCENARIOS)]
        if base is not None:
            given += ["--base", BASE, "--base-runs", str(base)]
        status, out, err = self.run_pages("pr", *given, *self.ran(run_number, minute))
        self.assertEqual(status, 0, err)
        return out

    def meta(self) -> dict:
        return json.loads((self.site / "logos-chat" / "pr-316" / "meta.json").read_text("utf-8"))

    def test_a_pull_request_s_run_holds_its_matrix_both_sides_text_and_its_meta(self):
        head = self.uploaded(**{"1/1": {"first_fork": 5}})
        self.pr(head=head, base=self.uploaded(), run_number=12, minute=581)
        out = self.site / "logos-chat" / "pr-316"
        texts = [
            f"{side}/{GOAL.format(rate)}/{run}/{name}"
            for side in ("base", "head")
            for rate in ("0.1", "1", "5")
            for run in (1, 2)
            for name in ("report.txt", "run.json", "run.txt")
        ]
        drawn = [
            f"head/{GOAL.format(rate)}/{run}/index.html"
            for rate in ("0.1", "1", "5")
            for run in (1, 2)
        ]
        scenarios = [f"scenarios/{GOAL.format(rate)}.json" for rate in ("0.1", "1", "5")]
        kept = ["index.html", "matrix.json", "meta.json", *texts, *drawn, *scenarios]
        self.assertEqual(self.files(out), sorted(kept))
        self.assertEqual(
            (out / "head" / GOAL.format(1) / "2" / "run.json").read_bytes(),
            self.run_json(head, "1/1").read_bytes(),
        )
        self.assertEqual(
            (out / scenarios[1]).read_bytes(), (SCENARIOS / f"{GOAL.format(1)}.json").read_bytes()
        )
        matrix = pages.read_matrix(out / "matrix.json")
        self.assertEqual(matrix["verdict"], "within")
        self.assertEqual(
            self.meta(),
            {
                "pr": 316,
                "revision": REVISION,
                "base": BASE,
                "chat_sim": SHA,
                "measurement": MEASUREMENT,
                "run_url": RUN_URL,
                "run_number": 12,
                "time": "2026-11-09T09:41:00Z",
            },
        )
        self.assertEqual(
            sorted(self.site.rglob("*.html")), sorted(out / page for page in ["index.html", *drawn])
        )

    def test_a_run_without_a_base_has_no_base_side(self):
        self.pr(base=None)
        out = self.site / "logos-chat" / "pr-316"
        self.assertFalse((out / "base").exists())
        self.assertIsNone(self.meta()["base"])
        self.assertIsNone(pages.read_matrix(out / "matrix.json")["verdict"])

    def test_a_matrix_not_as_tools_sim_writes_one_is_refused(self):
        path = self.matrix(self.uploaded(), self.uploaded())
        matrix = json.loads(path.read_text(encoding="utf-8"))
        (group,) = matrix["groups"]

        def results(**changed) -> dict:
            result = dict(group["results"][0], **changed)
            return dict(matrix, groups=[dict(group, results=[result])])

        for said, written in (
            ("base_point is not a field of it", dict(matrix, base_point=None)),
            ("spread is not a field of it", dict(matrix, spread=None)),
            (
                "cells[0].below is not a field of cells[0]",
                dict(matrix, cells=[dict(matrix["cells"][0], below=None)]),
            ),
            (
                "cells[0].moved is not a field of cells[0]",
                dict(matrix, cells=[dict(matrix["cells"][0], moved=None)]),
            ),
            ("verdict 'hint' is none of a matrix", dict(matrix, verdict="hint")),
            (
                "groups[0].results[0].result 'worse' is none of a matrix",
                results(result="worse"),
            ),
            ("groups[0].results[0].seeds is not a list of seeds", results(seeds=[0, "1"])),
            ("groups[0].results[0].p is not of its kind", results(p="0.58")),
            (
                "cells[0].group 'v1' is none of a scenario's",
                dict(matrix, cells=[dict(matrix["cells"][0], group="v1")]),
            ),
        ):
            with self.subTest(said=said):
                write(path, json.dumps(written))
                with self.assertRaises(pages.Unreadable) as refused:
                    pages.read_matrix(path)
                self.assertIn(said, str(refused.exception))

    def test_the_run_of_a_newer_run_is_kept_against_an_older_one(self):
        self.pr(run_number=8)
        printed = self.pr(run_number=7, base=None)
        self.assertEqual(printed, "Kept logos-chat/pr-316/: it holds run 8, newer than run 7.\n")
        self.assertEqual(self.meta()["base"], BASE)

    def test_a_newer_run_or_a_re_run_replaces_the_one_before_it_whole(self):
        self.pr(run_number=7)
        stale = self.site / "logos-chat" / "pr-316" / "base" / GOAL.format(1) / "1" / "run.json"
        self.assertTrue(stale.is_file())
        self.assertEqual(self.pr(run_number=8, base=None), "")
        self.assertFalse(stale.exists())
        self.assertEqual(self.pr(run_number=8, minute=3), "")
        self.assertEqual((self.meta()["run_number"], self.meta()["time"]), (8, time(3)))

    def test_a_meta_json_that_pr_does_not_write_is_refused_and_its_run_replaced(self):
        self.pr(run_number=8)
        path = self.site / "logos-chat" / "pr-316" / "meta.json"
        meta = self.meta()
        for said, written in (
            ("pr 317 does not name its directory", dict(meta, pr=317)),
            ("base is not a commit", dict(meta, base="fedcba9")),
            ("run_url is not an http(s) address", dict(meta, run_url="javascript:alert(1)")),
            ("title is not a field of it", dict(meta, title="Hold the group")),
            ("run_number is missing", {k: v for k, v in meta.items() if k != "run_number"}),
        ):
            with self.subTest(said=said):
                write(path, json.dumps(written))
                with self.assertRaises(pages.Unreadable) as refused:
                    pages.read_pr_meta(path)
                self.assertIn(said, str(refused.exception))
        self.assertEqual(self.pr(run_number=7), "")
        self.assertEqual(self.meta()["run_number"], 7)

    def test_a_command_line_that_does_not_say_its_base_as_its_matrix_does_is_refused(self):
        head = self.uploaded()
        with_base, alone = self.matrix(head, UPLOADED), self.matrix(head)
        given = [str(head), str(self.site), "--revision", REVISION, "--measurement", MEASUREMENT]
        given += [*self.ran(1, 1), "--scenarios", str(SCENARIOS)]
        said, runs = [*given, "--pr", "316"], ["--base-runs", str(UPLOADED)]
        for flag, argv in (
            ("--base-runs", [str(with_base), *said, "--base", BASE]),
            ("--base-runs", [str(alone), *said, *runs]),
            ("MATRIX", [str(with_base), *said]),
            ("MATRIX", [str(alone), *said, "--base", BASE, *runs]),
            ("--pr", [str(alone), *given, "--pr", "0"]),
            ("--base", [str(with_base), *said, "--base", "fedcba9", *runs]),
        ):
            with self.subTest(flag=flag, argv=argv):
                status, _, err = self.run_pages("pr", *argv)
                self.assertEqual(status, 2)
                self.assertIn(flag, err.splitlines()[-1])
        self.assertFalse((self.site / "logos-chat").exists())

    def test_scenarios_that_do_not_hold_each_scenario_the_matrix_ran_as_a_grow_are_refused(self):
        head = self.uploaded()
        given = [str(self.matrix(head)), str(head), str(self.site), "--pr", "316"]
        given += ["--revision", REVISION, "--measurement", MEASUREMENT, *self.ran(1, 1)]
        held, seated = self.scratch / "held", self.scratch / "seated"
        for scenarios in (held, seated):
            scenarios.mkdir()
            for rate in ("1", "5"):
                shutil.copy(SCENARIOS / f"{GOAL.format(rate)}.json", scenarios)
        missing, three = held / f"{GOAL.format('0.1')}.json", seated / f"{GOAL.format('0.1')}.json"
        shutil.copy(SCENARIOS / "three.json", three)
        for scenarios, said in (
            (self.scratch / "none", "--scenarios: no such directory"),
            (held, f"{missing}: no such file, and the matrix ran {GOAL.format('0.1')}"),
            (seated, f"{three} is not a scenario of a grow step alone"),
        ):
            with self.subTest(scenarios=scenarios):
                status, _, err = self.run_pages("pr", *given, "--scenarios", str(scenarios))
                self.assertEqual(status, 2)
                self.assertIn(said, err.splitlines()[-1])
        self.assertFalse((self.site / "logos-chat").exists())


# The chat-sim commit the fourth point of a history measures, the measurement having moved.
MOVED = "5d81c0a" * 5 + "5d81c"
SVG = "{http://www.w3.org/2000/svg}"
TITLE = """feat(core): hold <script>alert("merged")</script> & 'flush'"""
# The runs of the fixture with 1% seed 1 split at epoch 5, four epochs earlier than its 9.
EARLIER = {"1/1": {"first_fork": 5}}


class Trend(LogosChat):
    """The graph over main, drawn from a history of points of what the run jobs uploaded."""

    def history(self, points: int = 4) -> None:
        """Writes the first `points` of four: the fixture's runs; a commit that merged two pull
        requests, one titled with markup, whose runs are `EARLIER`; a commit whose runs are that
        commit's; and that commit again at another measurement, its runs the same."""
        made = [
            dict(revision=sha(1)),
            dict(
                revision=sha(2),
                runs=self.uploaded(**EARLIER),
                commits=self.commits(f"{TITLE} (#321)", "chore: bump the toolchain (#318)"),
            ),
            dict(
                revision=sha(3),
                runs=self.uploaded(**EARLIER),
                commits=self.commits("fix: a typo (#322)"),
            ),
            dict(
                revision=sha(3),
                measurement=MOVED,
                runs=self.uploaded(**EARLIER),
                commits=self.commits(status="identical"),
            ),
        ]
        for number, given in enumerate(made[:points], 1):
            self.point(minute=number, run_number=number, **given)

    def trend(self) -> List[str]:
        """Draws the graph, and returns the lines it warns with."""
        status, out, err = self.run_pages("trend", str(self.site))
        self.assertEqual((status, out), (0, ""), err)
        return err.splitlines()

    def page(self) -> str:
        return (self.site / "logos-chat" / "index.html").read_text(encoding="utf-8")

    def section(self, name: str, page: Optional[str] = None) -> str:
        """A section of the page, or of `page`, by its id."""
        drawn = self.page() if page is None else page
        return drawn.split(f'id="{name}"', 1)[1].split("</section>", 1)[0]

    def now(self) -> List[Tuple[str, str]]:
        """Each cell of the newest commit's numbers: its median and what is under it."""
        return re.findall(r"<td><b>([^<]*)</b>.*?<small>([^<]*)</small></td>", self.section("now"))

    def panels(
        self, page: Optional[str] = None, name: str = "commits"
    ) -> Dict[str, ElementTree.Element]:
        """The panels of a section, each by its title."""
        found = re.findall(
            r'<div class="panel">\n<p>([^<]*)</p>\n(<svg.*?</svg>)', self.section(name, page), re.S
        )
        return {title: ElementTree.fromstring(svg) for title, svg in found}

    def rows(self) -> List[List[str]]:
        """The rows of what changed, the newest commit first, each its cells' text."""
        return Parsed(self.section("changes")).rows[1:]

    def change(self, revision: str, measurement: str = MEASUREMENT) -> Path:
        return self.at(revision, measurement) / "change" / "index.html"

    def series(self, svg: ElementTree.Element, group: str) -> ElementTree.Element:
        return next(one for one in svg.iter(f"{SVG}g") if one.get("class") == group)

    def since(self) -> str:
        """The line under the newest commit's numbers, on what moved since the first commit."""
        return Parsed(self.page().split('<p class="since">', 1)[1].split("</p>", 1)[0]).text

    def none_built(self) -> Path:
        """What the run jobs of a commit chat-sim does not build against uploaded."""
        runs = self.uploaded()
        for report in list(runs.rglob("run.json")):
            report.unlink()
            write(report.with_name("unbuilt"), "chat-sim does not build\n")
        return runs

    def without(self, runs: Path, *cells: str) -> Path:
        """`runs` less the job of each of `cells`, a run by its rate and seed."""
        for cell in cells:
            shutil.rmtree(self.run_json(runs, cell).parents[2])
        return runs

    def renamed(self, runs: Path) -> dict:
        """`runs` and their matrix, as a point takes them, under scenarios no file names."""
        self.made += 1
        renamed = self.scratch / f"renamed-{self.made}"
        for report in runs.rglob("run.json"):
            job, scenario, run = report.parts[-4:-1]
            name = scenario.replace("50-epochs-by-saro", "fifty-by-saro")
            shutil.copytree(report.parent, renamed / job.replace(scenario, name) / name / run)
        # tools/sim matrix takes a cell's group from its scenario's file, which a renamed one does
        # not have: the matrix is the runs', its scenarios renamed.
        matrix = self.matrix(runs)
        text = matrix.read_text(encoding="utf-8")
        write(matrix, text.replace("50-epochs-by-saro", "fifty-by-saro"))
        return dict(runs=renamed, matrix=matrix)

    def test_the_page_holds_now_the_commits_and_what_changed_in_that_order(self):
        self.history()
        self.trend()
        page = self.page()
        at = [page.index(f'id="{name}"') for name in ("now", "commits", "changes")]
        self.assertEqual(at, sorted(at))
        parsed = Parsed(page)
        self.assertEqual(parsed.h1, ["Over main"])
        self.assertIn("lasts before it first forks. The goal is 50.", parsed.text)
        self.assertIn("../guide/track/main/#the-graph-over-main", parsed.links)

    def test_now_holds_each_rate_s_median_at_the_newest_commit_and_how_many_runs_gave_it(self):
        self.history()
        self.trend()
        self.assertEqual(self.now(), [("48", "2 runs"), ("11.5", "2 runs"), ("5.5", "2 runs")])
        heads = Parsed(self.section("now")).rows[0]
        self.assertEqual(heads, ["", "0.1% loss", "1% loss", "5% loss"])
        self.assertIn(f"main/{sha(3)}/{MOVED}/", Parsed(self.section("now")).links)
        self.assertNotIn("has no outcome", Parsed(self.section("now")).text)

    def test_now_says_how_many_members_an_epoch_of_each_group_joins_where_runs_did_not_fork(self):
        self.point()
        self.trend()
        self.assertIn("an epoch joins another number of members in each.", self.section("now"))
        # The run of each group that went 50 epochs without a fork seated 53 and 103.
        edits = {"0.1/0": {"seated": 53}, "v3/0.1/0": {"seated": 103}}
        self.point(runs=self.uploaded(groupv3=True, **edits))
        self.trend()
        said = (
            "in the runs that went without a fork, a GroupV2 epoch joins 1 member and a GroupV3 "
            "epoch joins 2 members."
        )
        self.assertIn(said, self.section("now"))

    def test_a_rate_no_run_of_which_forked_says_so_and_has_no_panel(self):
        self.point(runs=self.uploaded(**{"0.1/1": {"first_fork": None, "behind": []}}))
        self.trend()
        self.assertEqual(self.now()[0], ("50", "no fork in 2 of 2 runs"))
        self.assertEqual(list(self.panels()), ["1% loss", "5% loss"])
        self.assertIn("At 0.1% loss no run of these commits forked.", self.section("commits"))

    def test_a_commit_s_runs_get_the_verdict_tools_sim_matrix_gives_a_pull_request_s(self):
        base, head = self.uploaded(), self.uploaded(**EARLIER)
        self.point(sha(1), minute=1, run_number=1, runs=base)
        self.point(sha(2), minute=2, run_number=2, runs=head, commits=self.commits())
        self.trend()
        matrix = json.loads(self.matrix(head, base).read_text(encoding="utf-8"))
        (judged,) = matrix["groups"]
        moved = next(one for one in judged["results"] if one["result"] != "same")
        self.assertEqual((judged["verdict"], moved["rate"], moved["earlier"]), ("within", "1%", 1))
        newest, first = self.rows()
        self.assertIn(simulate.RESULTS[judged["verdict"]], newest[3])
        self.assertIn("1%13.5 → 11.5", newest[3])
        self.assertIn(
            f"{moved['earlier']} earlier · {moved['zero']} tied · {moved['later']} later", newest[3]
        )
        self.assertNotIn("0.1%", newest[3])
        self.assertEqual(first[3], "the first commit this test ran on")
        classes = re.findall(r'<tr class="(\w+)">', self.section("changes"))
        self.assertEqual(classes, ["moved", "quiet"])
        self.assertIn(f"main/{sha(2)}/{MEASUREMENT}/change/", Parsed(self.page()).links)
        self.assertTrue(self.change(sha(2)).is_file())

    def test_a_commit_that_changed_no_run_says_nothing_moved_and_has_no_page_of_a_change(self):
        self.history(3)
        self.trend()
        newest = self.rows()[0]
        self.assertEqual(newest[1:4], [sha(3)[:7], "#322 fix: a typo", "nothing moved"])
        self.assertFalse(self.change(sha(3)).exists())
        self.assertTrue(self.change(sha(2)).is_file())

    def test_a_marker_is_on_a_group_s_line_at_each_commit_that_changed_its_runs_alone(self):
        self.history(3)
        self.trend()
        for title, svg in self.panels().items():
            with self.subTest(panel=title):
                hits = [one for one in svg.iter(f"{SVG}rect") if one.get("class") == "hit"]
                commits = [float(one.get("x")) + float(one.get("width")) / 2 for one in hits]
                marked = [one.get("cx") for one in self.series(svg, "v2").iter(f"{SVG}circle")]
                self.assertEqual(len(commits), 3)
                self.assertEqual(list(map(float, marked)), [round(commits[1], 1)])

    def test_chat_sim_changing_under_main_is_one_commit_where_no_first_fork_moved(self):
        self.history()
        self.trend()
        self.assertEqual([row[1] for row in self.rows()], [sha(3)[:7], sha(2)[:7], sha(1)[:7]])
        self.assertEqual(self.rows()[0][3], "nothing moved")
        said = "chat-sim itself changed 1 time since, and each time no seed's first fork moved."
        self.assertIn(said, Parsed(self.page()).text)
        self.assertFalse((self.site / "logos-chat" / "earlier").exists())

    def test_a_chat_sim_change_that_moves_a_first_fork_starts_the_page_again_from_its_run(self):
        self.point(sha(1), minute=1, run_number=1)
        self.point(sha(2), minute=2, run_number=2, commits=self.commits("fix: flush (#321)"))
        again = dict(measurement=MOVED, runs=self.uploaded(**EARLIER))
        again["commits"] = self.commits(status="identical")
        self.point(sha(2), minute=3, run_number=3, **again)
        self.trend()
        # The commit's pull request is the one its point of the test before merged.
        (row,) = self.rows()
        self.assertEqual(row[2:4], ["#321 fix: flush", "the first commit this test ran on"])
        self.assertIn(f"This test has run since 2026-11-09, at chat-sim", Parsed(self.page()).text)
        self.assertIn("The 2 points before ran another test", Parsed(self.page()).text)
        earlier = Parsed((self.site / "logos-chat" / "earlier" / "index.html").read_text("utf-8"))
        self.assertEqual([row[1] for row in earlier.rows[1:]], [sha(2)[:7], sha(1)[:7]])
        self.assertIn("chat-sim changed the test since", earlier.text)
        self.assertIn(f"../main/{sha(1)}/{MEASUREMENT}/", earlier.links)
        self.assertEqual(earlier.rows[1][4], "6 of 6")

    def test_a_first_fork_that_moves_as_chat_sim_and_main_both_change_is_laid_to_neither(self):
        self.point(sha(1), minute=1, run_number=1)
        given = dict(measurement=MOVED, runs=self.uploaded(**EARLIER), commits=self.commits())
        self.point(sha(2), minute=2, run_number=2, **given)
        self.trend()
        self.assertEqual([row[1] for row in self.rows()], [sha(2)[:7]])
        said = "The 1 point before are not compared with these, since chat-sim and main changed"
        self.assertIn(said, Parsed(self.page()).text)
        self.assertNotIn("another test", self.page())
        earlier = Parsed((self.site / "logos-chat" / "earlier" / "index.html").read_text("utf-8"))
        self.assertIn("cannot tell which of the two changed the runs", earlier.text)
        self.assertNotIn("chat-sim changed the test since", earlier.text)

    def test_a_point_that_shares_no_run_with_the_one_before_at_another_measurement_starts_the_page(
        self,
    ):
        rates = ("0.1", "1", "5")
        first = self.without(self.uploaded(), *(f"{rate}/1" for rate in rates))
        second = self.without(self.uploaded(), *(f"{rate}/0" for rate in rates))
        self.point(sha(1), minute=1, run_number=1, runs=first)
        again = dict(measurement=MOVED, runs=second, commits=self.commits(status="identical"))
        self.point(sha(1), minute=2, run_number=2, **again)
        self.trend()
        self.assertEqual(len(self.rows()), 1)
        self.assertIn("The 1 point before ran another test", Parsed(self.page()).text)

    def test_the_earlier_points_page_goes_once_no_point_is_before_the_test(self):
        self.point(sha(1), minute=1, run_number=1)
        again = dict(measurement=MOVED, runs=self.uploaded(**EARLIER))
        again["commits"] = self.commits(status="identical")
        self.point(sha(1), minute=2, run_number=2, **again)
        self.trend()
        earlier = self.site / "logos-chat" / "earlier" / "index.html"
        self.assertTrue(earlier.is_file())
        write(self.at(sha(1)) / "point.json", "{}")
        self.assertEqual(len(self.trend()), 1)
        self.assertFalse(earlier.exists())

    def test_the_test_has_run_since_the_day_of_its_first_point(self):
        self.point(sha(1), minute=1, run_number=1)
        self.point(sha(2), minute=24 * 60 + 1, run_number=2, commits=self.commits())
        self.trend()
        self.assertIn("This test has run since 2026-11-09, at chat-sim", Parsed(self.page()).text)

    def test_a_point_with_no_outcome_ahead_of_the_test_s_first_at_its_measurement_is_of_the_test(
        self,
    ):
        # The second commit does not build at either measurement.
        self.point(sha(1), minute=1, run_number=1)
        lacking = dict(runs=self.none_built(), commits=self.commits())
        self.point(sha(2), minute=2, run_number=2, **lacking)
        again = dict(measurement=MOVED, commits=self.commits())
        self.point(sha(2), minute=3, run_number=3, runs=self.none_built(), **again)
        self.point(sha(3), minute=4, run_number=4, runs=self.uploaded(**EARLIER), **again)
        self.trend()
        self.assertEqual(
            [row[3] for row in self.rows()],
            ["the first commit this test ran on", "no run had an outcome"],
        )
        self.assertIn("The 2 points before are not compared with these", Parsed(self.page()).text)

    def test_the_points_with_no_outcome_before_any_that_has_one_are_commits_of_the_test(self):
        self.point(sha(1), minute=1, run_number=1, runs=self.none_built())
        again = dict(measurement=MOVED, commits=self.commits())
        self.point(sha(2), minute=2, run_number=2, **again)
        self.trend()
        self.assertEqual(
            [row[3] for row in self.rows()],
            ["the first commit this test ran on", "no run had an outcome"],
        )
        self.assertFalse((self.site / "logos-chat" / "earlier").exists())

    def test_since_the_first_commit_says_what_the_newest_s_runs_came_to_against_its(self):
        self.history(3)
        self.trend()
        self.assertEqual(
            self.since(),
            f"Since the first commit of this test, {sha(1)[:7]} on 2026-11-09: GroupV2 moved, "
            "within chance",
        )

    def test_since_the_first_commit_nothing_moved_where_no_commit_changed_a_run(self):
        # chat-sim's change retimes a run of the second commit, which no commit of main did.
        self.point(sha(1), minute=1, run_number=1)
        self.point(sha(2), minute=2, run_number=2, commits=self.commits())
        again = dict(measurement=MOVED, runs=self.uploaded(**{"1/1": {"virtual_s": 1.5}}))
        self.point(sha(2), minute=3, run_number=3, commits=self.commits(), **again)
        self.trend()
        self.assertTrue(self.since().endswith("GroupV2 nothing moved"), self.since())

    def test_a_group_that_forks_earlier_says_so_of_the_commit_and_since_the_first(self):
        more = range(2, 10)
        base = self.seeded(self.uploaded(), "1", more)
        sooner = {f"1/{seed}": {"first_fork": 5} for seed in more}
        head = self.seeded(self.uploaded(), "1", more, **sooner)
        self.point(sha(1), minute=1, run_number=1, runs=base)
        self.point(sha(2), minute=2, run_number=2, runs=head, commits=self.commits())
        self.trend()
        newest = self.rows()[0][3]
        self.assertIn("forks earlier", newest)
        self.assertIn("1%18 → 5", newest)
        self.assertIn("8 earlier · 2 tied · 0 later", newest)
        self.assertTrue(self.since().endswith("GroupV2 ▼ forks earlier"), self.since())

    def test_a_commit_at_a_measurement_the_commit_before_never_ran_at_is_not_compared(self):
        # Its 1% seed 1 took another time and first forked at the same epoch, so the test goes on.
        self.point(sha(1), minute=1, run_number=1)
        again = dict(measurement=MOVED, runs=self.uploaded(**{"1/1": {"virtual_s": 1.5}}))
        self.point(sha(2), minute=2, run_number=2, commits=self.commits(), **again)
        self.trend()
        newest, first = self.rows()
        self.assertEqual(newest[3], "not compared with the commit before")
        self.assertEqual(first[3], "the first commit this test ran on")
        self.assertTrue(self.since().endswith("GroupV2 moved, within chance"), self.since())

    def test_a_commit_is_compared_at_the_newest_measurement_it_shares_with_the_commit_before(self):
        # Its 1% seed 1 took another time at the first measurement alone.
        again = dict(measurement=MOVED, commits=self.commits(status="identical"))
        retimed = self.uploaded(**{"1/1": {"virtual_s": 1.5}})
        self.point(sha(1), minute=1, run_number=1)
        self.point(sha(1), minute=2, run_number=2, **again)
        self.point(sha(2), minute=3, run_number=3, runs=retimed, commits=self.commits())
        self.point(sha(2), minute=4, run_number=4, **again)
        self.trend()
        self.assertEqual(self.rows()[0][3], "nothing moved")

    def test_a_commit_after_one_with_no_outcome_is_compared_with_the_last_that_had_one(self):
        self.point(sha(1), minute=1, run_number=1)
        self.point(sha(2), minute=2, run_number=2, runs=self.none_built(), commits=self.commits())
        given = dict(runs=self.uploaded(**EARLIER), commits=self.commits())
        self.point(sha(3), minute=3, run_number=3, **given)
        self.trend()
        newest, lacking, _ = self.rows()
        self.assertIn("1%13.5 → 11.5", newest[3])
        self.assertEqual(lacking[3], "no run had an outcome")

    def test_a_commit_of_scenarios_since_renamed_is_compared_under_the_newest_point_s_names(self):
        self.point(sha(1), minute=1, run_number=1, **self.renamed(self.uploaded()))
        moved = dict(self.renamed(self.uploaded(**EARLIER)), commits=self.commits())
        self.point(sha(2), minute=2, run_number=2, **moved)
        again = dict(measurement=MOVED, runs=self.uploaded(**EARLIER))
        again["commits"] = self.commits(status="identical")
        self.point(sha(2), minute=3, run_number=3, **again)
        self.assertEqual(self.trend(), [])
        self.assertIn("1%13.5 → 11.5", self.rows()[0][3])

    def test_points_whose_outcomes_hold_no_first_fork_are_compared_as_the_newest_are(self):
        self.history(2)
        for number in (1, 2):
            held = unjoined(self.read(sha(number)))
            write(self.at(sha(number)) / "point.json", json.dumps(held))
        self.assertEqual(self.trend(), [])
        newest = self.rows()[0][3]
        self.assertIn("1%13.5 → 11.5", newest)
        self.assertIn("1 earlier · 1 tied · 0 later", newest)

    def test_a_newest_run_with_no_outcome_is_said_and_the_numbers_are_the_commit_before_s(self):
        self.point(sha(1), minute=1, run_number=1)
        self.point(sha(2), minute=2, run_number=2, runs=self.none_built(), commits=self.commits())
        self.trend()
        now = Parsed(self.section("now"))
        newest = f"The newest run, at main {sha(2)[:7]} on 2026-11-09 00:02 UTC, has no"
        self.assertIn(newest, now.text)
        self.assertIn(f"main/{sha(2)}/{MEASUREMENT}/", now.links)
        self.assertEqual(self.now()[0], ("48", "2 runs"))
        newest, first = self.rows()
        self.assertEqual(newest[1:4], [sha(2)[:7], "none", "no run had an outcome"])
        self.assertEqual(first[3], "the first commit this test ran on")

    def test_a_run_the_commit_before_has_no_outcome_of_is_counted_and_not_compared(self):
        lacking = self.uploaded()
        for seed in (0, 1):
            report = self.run_json(lacking, f"5/{seed}")
            report.unlink()
            write(report.with_name("unbuilt"), "chat-sim does not build\n")
        self.point(sha(1), minute=1, run_number=1, runs=lacking)
        self.point(sha(2), minute=2, run_number=2, commits=self.commits())
        self.trend()
        said = "nothing moved 2 of 6 runs had none to compare with"
        self.assertEqual(self.rows()[0][3], said)
        self.assertTrue(self.since().endswith(f"GroupV2 {said}"), self.since())
        self.assertEqual(len(self.now()), 3)
        self.assertFalse(self.change(sha(2)).exists())
        for svg in self.panels().values():
            self.assertEqual(list(self.series(svg, "v2").iter(f"{SVG}circle")), [])

    def test_a_group_the_commit_before_did_not_run_has_no_run_to_compare_with(self):
        self.point(sha(1), minute=1, run_number=1)
        given = dict(runs=self.uploaded(groupv3=True), commits=self.commits())
        self.point(sha(2), minute=2, run_number=2, **given)
        self.trend()
        self.assertEqual(self.rows()[0][3:5], ["nothing moved", "no run to compare with"])
        said = "GroupV2 nothing moved GroupV3 no run to compare with"
        self.assertTrue(self.since().endswith(said), self.since())

    def test_a_run_the_commit_before_could_not_build_is_said_on_the_page_of_the_change(self):
        lacking = self.uploaded()
        report = self.run_json(lacking, "5/0")
        report.unlink()
        write(report.with_name("unbuilt"), "chat-sim does not build\n")
        self.point(sha(1), minute=1, run_number=1, runs=lacking)
        given = dict(runs=self.uploaded(**EARLIER), commits=self.commits())
        self.point(sha(2), minute=2, run_number=2, **given)
        self.trend()
        self.assertIn("1 of 6 runs had none to compare with", self.rows()[0][3])
        text = Parsed(self.change(sha(2)).read_text(encoding="utf-8")).text
        said = "5%: nothing moved in seeds 0 to 1, does not build at the base in 1 of 2 runs"
        self.assertIn(said, text)
        self.assertIn("the base is the commit before", text)

    def test_a_run_the_commit_before_ran_and_the_commit_did_not_is_left_out(self):
        self.point(sha(1), minute=1, run_number=1)
        fewer = self.without(self.uploaded(), "5/1")
        self.point(sha(2), minute=2, run_number=2, runs=fewer, commits=self.commits())
        self.trend()
        self.assertEqual(self.rows()[0][3], "nothing moved")

    def test_a_commit_one_of_whose_runs_has_no_outcome_says_so(self):
        self.point(sha(1), minute=1, run_number=1)
        lacking = self.without(self.uploaded(), "5/1")
        asked = [{"scenario": GOAL.format(rate), "from": 1, "runs": 2} for rate in ("0.1", "5")]
        given = dict(runs=lacking, matrix=self.matrix(lacking, asked=asked))
        self.point(sha(2), minute=2, run_number=2, commits=self.commits(), **given)
        # The same run of the commit after has none either, and none before to hold it to.
        self.point(sha(3), minute=3, run_number=3, commits=self.commits(), **given)
        self.trend()
        self.assertEqual([row[3] for row in self.rows()[:2]], ["no outcome", "no outcome"])

    def test_a_marker_and_the_page_of_a_change_are_of_the_group_whose_runs_moved(self):
        self.point(sha(1), minute=1, run_number=1, runs=self.uploaded(groupv3=True))
        moved = self.uploaded(groupv3=True, **{"v3/1/1": {"first_fork": 5}})
        self.point(sha(2), minute=2, run_number=2, runs=moved, commits=self.commits())
        self.trend()
        for svg in self.panels().values():
            marked = {
                key: len(list(self.series(svg, key).iter(f"{SVG}circle"))) for key in ("v2", "v3")
            }
            self.assertEqual(marked, {"v2": 0, "v3": 1})
        page = self.change(sha(2)).read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'<section class="chart" id="(\w+)">', page), ["v3"])

    def test_the_page_of_a_change_goes_with_the_test_its_commit_ran(self):
        self.history(2)
        self.trend()
        self.assertTrue(self.change(sha(2)).is_file())
        again = dict(measurement=MOVED, commits=self.commits(status="identical"))
        self.point(sha(2), minute=3, run_number=3, **again)
        self.trend()
        self.assertFalse(self.change(sha(2)).parent.exists())

    def test_the_numbers_under_the_panels_have_a_row_a_commit_the_newest_first(self):
        self.history(3)
        self.trend()
        heads, *rows = Parsed(self.section("commits")).rows
        self.assertEqual(heads[1:], ["GroupV2 0.1% loss", "GroupV2 1% loss", "GroupV2 5% loss"])
        self.assertEqual([row[0] for row in rows], [sha(3)[:7], sha(2)[:7], sha(1)[:7]])
        self.assertEqual([row[2] for row in rows], ["11.5", "11.5", "13.5"])

    def test_each_group_has_its_row_of_numbers_and_its_line_in_each_panel(self):
        runs = self.uploaded(groupv3=True, **{"v3/1/1": {"first_fork": 5}})
        self.point(runs=runs)
        self.trend()
        names = [row[0] for row in Parsed(self.section("now")).rows[1:]]
        self.assertEqual([name[: len("GroupV2")] for name in names], ["GroupV2", "GroupV3"])
        medians = [median for median, _ in self.now()]
        self.assertEqual(medians, ["48", "13.5", "5.5", "48", "11.5", "5.5"])
        for svg in self.panels().values():
            ends = [text.text for text in svg.iter(f"{SVG}text") if text.get("class") == "label"]
            self.assertEqual(sorted(end.split()[0] for end in ends), ["GroupV2", "GroupV3"])

    def test_a_commit_s_hover_says_its_pull_requests_and_each_group_s_runs(self):
        self.history(2)
        self.trend()
        titles = [title.text for title in self.panels()["1% loss"].iter(f"{SVG}title")]
        self.assertEqual(
            titles[1].splitlines(),
            [
                f"main {sha(2)[:7]} · #321, #318 · 2026-11-09 00:02 UTC",
                "GroupV2: median 11.5, the middle half 8.25 to 14.75, 2 runs",
            ],
        )

    def test_a_pull_request_s_title_is_escaped(self):
        self.history(2)
        self.trend()
        for page in (self.page(), self.change(sha(2)).read_text(encoding="utf-8")):
            self.assertNotIn("<script>alert", page)
            self.assertIn(f"#321 {TITLE}", Parsed(page).text)

    def test_the_svgs_parse_as_xml_and_the_pages_as_html_and_run_no_script(self):
        self.history()
        self.trend()
        for page in (self.page(), self.change(sha(2)).read_text(encoding="utf-8")):
            drawn = re.findall(r"<svg.*?</svg>", page, re.S)
            self.assertTrue([ElementTree.fromstring(svg) for svg in drawn])
            self.assertNotIn("script", Parsed(page).tags)

    def test_a_single_point_is_drawn_as_a_line_as_wide_as_its_band_with_no_marker(self):
        self.history(1)
        self.trend()
        self.assertEqual(len(self.rows()), 1)
        self.assertNotIn('class="since"', self.page())
        for svg in self.panels().values():
            series = self.series(svg, "v2")
            self.assertEqual(list(series.iter(f"{SVG}circle")), [])
            (line,) = series.iter(f"{SVG}path")
            (x, y), (to, level) = re.findall(r"[ML]([0-9.]+) ([0-9.]+)", line.get("d"))[-2:]
            self.assertEqual((float(to) - float(x), level), (8.0, y))

    def test_a_point_json_that_is_refused_is_left_out_and_warned_of(self):
        self.history(2)
        broken = self.at(sha(2)) / "point.json"
        write(broken, "{}")
        warned = self.trend()
        self.assertEqual(len(warned), 1)
        self.assertIn(f"::warning::{broken}: ", warned[0])
        self.assertEqual([row[1] for row in self.rows()], [sha(1)[:7]])

    def test_a_site_with_no_point_says_so(self):
        self.trend()
        self.assertIn("No point of main has had an outcome yet.", self.page())
        self.assertEqual(re.findall(r"<svg", self.page()), [])

    def test_a_site_whose_points_have_no_outcome_says_so_and_calls_none_another_test_s(self):
        self.point(runs=self.none_built())
        self.trend()
        self.assertIn("No point of main has had an outcome yet.", self.page())
        self.assertNotIn("another test", self.page())
        self.assertFalse((self.site / "logos-chat" / "earlier").exists())

    def test_a_rate_whose_scenario_is_renamed_at_a_new_measurement_goes_on_as_its_line(self):
        self.point(sha(1), minute=1, run_number=1)
        given = dict(self.renamed(self.uploaded()), measurement=MOVED, commits=self.commits())
        self.point(sha(2), minute=2, run_number=2, **given)
        # No file names the renamed scenarios, so the newest commit is not held to the first.
        warned = self.trend()
        self.assertEqual(len(warned), 1)
        self.assertIn("fifty-by-saro-waku-loss-", warned[0])
        self.assertEqual(
            [row[3] for row in self.rows()],
            ["not compared with the commit before", "the first commit this test ran on"],
        )
        for svg in self.panels().values():
            line = self.series(svg, "v2").find(f"{SVG}path").get("d")
            self.assertEqual(len(line.split("L")), 2)

    def test_the_page_of_a_change_holds_each_seed_s_two_runs_and_the_round_they_part_in(self):
        base, head = self.uploaded(), self.uploaded(**EARLIER)
        joined = [
            {"adders": ["saro"], "joiners": [name], "refused": [], "settled_s": 1.5}
            for name in ("raya", "pax")
        ]
        retimed = [joined[0], dict(joined[1], settled_s=2.5)]
        # 1% seed 1 parts in a round both runs have, 5% seed 0 where the shorter run ends.
        held = {"1/1": (joined, retimed), "5/0": (joined, joined + [retimed[1]])}
        for cell, both in held.items():
            for side, rounds in zip((base, head), both):
                report = json.loads(self.run_json(side, cell).read_text(encoding="utf-8"))
                report["grow"]["rounds"] = rounds
                write(self.run_json(side, cell), json.dumps(report))
        self.point(sha(1), minute=1, run_number=1, runs=base, run_url=f"{RUNS_URL}/71")
        given = dict(runs=head, commits=self.commits("fix: flush (#321)"))
        self.point(sha(2), minute=2, run_number=2, run_url=f"{RUNS_URL}/72", **given)
        self.trend()
        page = self.change(sha(2)).read_text(encoding="utf-8")
        parsed = Parsed(page)
        self.assertEqual(parsed.h1, ["#321 fix: flush"])
        self.assertIn(f"main {sha(2)[:7]} against the commit before it, {sha(1)[:7]}", parsed.text)
        self.assertIn("1%: 0 later, 1 earlier, 1 tied (p 1)", parsed.text)
        (panel,) = self.panels(page, "v2").values()
        self.assertEqual(list(self.panels(page, "v2")), ["1%"])
        # The seed that moved earliest is the first row, its ring the commit before's 9 epochs.
        seeds = [one.text for one in panel.iter(f"{SVG}text") if one.get("text-anchor") == "end"]
        self.assertEqual(seeds, ["1", "0"])
        (ring,) = (one for one in panel.iter(f"{SVG}circle") if one.get("class") == "before")
        dot = next(one for one in panel.iter(f"{SVG}circle") if one.get("class") == "after")
        self.assertLess(float(dot.get("cx")), float(ring.get("cx")))
        self.assertIn("At 0.1% and 5% no seed's first fork moved.", page)
        rows = {tuple(row[:4]): row[4:6] for row in parsed.rows if len(row) == 8}
        self.assertEqual(rows[("1", "2", "9", "5")], ["4 earlier", "round 2"])
        self.assertEqual(rows[("0", "1", "18", "18")][0], "the same outcome")
        self.assertEqual(rows[("0", "1", "8", "8")], ["the same outcome", "round 3"])
        self.assertIn("two runs of the same outcome can part in a round", parsed.text)
        run = f"{GOAL.format('1')}/2/"
        self.assertIn(f"../{run}run.json", parsed.links)
        self.assertIn(f"../../../{sha(1)}/{MEASUREMENT}/{run}run.json", parsed.links)
        named = f"--pattern 'logs-revision-{GOAL.format('1')}-[0-9]*'"
        self.assertIn(
            f"gh run download 71 --repo logos-messaging/chat-sim --dir before {named}\n"
            f"gh run download 72 --repo logos-messaging/chat-sim --dir after {named}",
            parsed.text,
        )
        self.assertEqual([link for link in parsed.links if "group.log" in link], [])
        self.assertIn("GitHub keeps a run's logs 90 days.", parsed.text)
        self.assertLessEqual({"../", f"../../../{sha(1)}/{MEASUREMENT}/"}, set(parsed.links))
        self.assertIn("../../../../../guide/track/main/#the-page-of-a-change", parsed.links)
        self.assertIn("../../../../../guide/investigate/moved-run/#into-the-log", parsed.links)
        for link in parsed.links:
            if not link.startswith("http") and "/guide/" not in link:
                self.assertTrue((self.change(sha(2)).parent / link.split("#")[0]).exists(), link)

    def test_the_page_of_a_change_whose_runs_text_is_gone_links_none_and_names_no_round(self):
        self.history(2)
        for directory in (self.at(sha(1)), self.at(sha(2))):
            for kept in directory.glob("*/*/*"):
                if kept.name in pages.TEXTS:
                    kept.unlink()
        self.trend()
        parsed = Parsed(self.change(sha(2)).read_text(encoding="utf-8"))
        rows = [row for row in parsed.rows if len(row) == 8 and row[0] != "Seed"]
        self.assertEqual(len(rows), 6)
        self.assertEqual({tuple(row[5:]) for row in rows}, {("-", "-", "-")})
        self.assertIn("gh run download 42 ", parsed.text)


GROW = {
    "ended": "no member took part",
    "trunk_epoch": 14,
    "main": 3,
    "lines": [
        {
            "epoch": 14,
            "forked_at": None,
            "from": None,
            "kind": "trunk",
            "members": ["saro", "raya", "pax", "mira", "kes", "peer7", "peer9", "peer11"]
            + ["peer12", "peer13", "peer15"],
        },
        {"epoch": 8, "forked_at": 8, "from": 0, "kind": "main", "members": ["peer8"]},
        {"epoch": 11, "forked_at": 11, "from": 0, "kind": "main", "members": ["peer10"]},
    ],
    "fell_behind": [
        {"epoch": 5, "name": "mira"},
        {"epoch": 6, "name": "kes"},
        {"epoch": 6, "name": "peer7"},
        {"epoch": 7, "name": "peer8"},
        {"epoch": 8, "name": "peer9"},
        {"epoch": 10, "name": "peer10"},
        {"epoch": 13, "name": "saro"},
    ],
    "behind": ["saro", "mira", "kes", "peer7", "peer9"],
    "never_joined": ["tomo", "peer14"],
    "removed": [],
}
# A grow whose lines are listed out of the order they are drawn in: line 1 left line 2, listed after
# it, line 3 left the trunk the latest, and line 4 ended with no member.
TANGLED = dict(
    GROW,
    trunk_epoch=20,
    lines=[
        {
            "epoch": 20,
            "forked_at": None,
            "from": None,
            "kind": "trunk",
            "members": ["saro", "raya", "pax", "mira", "kes", "peer7", "peer9", "peer11", "peer12"],
        },
        {"epoch": 16, "forked_at": 12, "from": 2, "kind": "main", "members": ["peer3", "peer4"]},
        {
            "epoch": 14,
            "forked_at": 8,
            "from": 0,
            "kind": "main",
            "members": ["peer1", "peer2", "peer5", "peer6"],
        },
        {"epoch": 18, "forked_at": 15, "from": 0, "kind": "main", "members": ["peer8"]},
        {"epoch": 9, "forked_at": 9, "from": 2, "kind": "main", "members": []},
    ],
)
# A grow whose lines off the trunk each name the other as the line they left.
LOOPED = dict(
    GROW,
    lines=[
        GROW["lines"][0],
        {"epoch": 8, "forked_at": 8, "from": 2, "kind": "main", "members": ["peer8"]},
        {"epoch": 11, "forked_at": 11, "from": 1, "kind": "main", "members": ["peer10"]},
    ],
)
# A grow that no member took part in.
EMPTY = dict(
    GROW,
    lines=[{"epoch": 0, "forked_at": None, "from": None, "kind": "trunk", "members": []}],
    fell_behind=[],
    behind=[],
    never_joined=[],
)
# Rounds of a grow of drawn adders, as a run.json has them: two adders in the second, and one of
# them refused the whole third.
ROUNDS = [
    {"adders": ["saro"], "joiners": ["raya"], "refused": [], "settled_s": 0.55},
    {"adders": ["saro", "raya"], "joiners": ["pax", "mira"], "refused": [], "settled_s": 12.5},
    {"adders": ["raya", "pax"], "joiners": ["kes"], "refused": ["pax"], "settled_s": 180},
]
# The rows of the table of ROUNDS, its head first.
ROUNDED = [
    ["round", "adders", "joiners", "refused", "settled s"],
    ["1", "saro", "raya", "-", "0.550"],
    ["2", "saro, raya", "pax, mira", "-", "12.500"],
    ["3", "raya, pax", "kes", "pax", "180.000"],
]
# The title of each line of GROW's tree, its top row first.
GROWN = [
    "trunk: 11 members, epochs 0 to 14",
    "line 2, off the trunk: 1 member, epoch 11",
    "line 1, off the trunk: 1 member, epoch 8",
]
MARKUP = '<script>alert("run")</script> & </pre>'
# The log of a build of harness-run that failed, as the run job keeps it.
LOG = (
    "   Compiling harness v0.1.0 (/home/runner/work/chat-sim/chat-sim/crates/harness)\n"
    "error[E0061]: this method takes 3 arguments but 2 arguments were supplied\n"
    "   --> crates/harness/src/runner.rs:489:35\n"
    "489 |         harness.client_mut(0).group_add_signers(&convo, &joiners);\n"
    "error: could not compile `harness` (lib) due to 1 previous error\n"
)


class Describe(unittest.TestCase):
    """How the run of a scenario of a grow step tests what a pull request's report page says the
    scenarios test, as that page says it."""

    def test_each_scenario_a_suite_runs_is_said_and_one_of_any_other_step_refused(self):
        goals = {
            goal.format(rate)
            for goal in simulate.GOALS.values()
            for rates in simulate.RATES.values()
            for rate in rates
        }
        said = set()
        for path in sorted(SCENARIOS.glob("*.json")):
            scenario = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(scenario=path.stem):
                if [step["step"] for step in scenario["steps"]] != ["grow"]:
                    with self.assertRaisesRegex(pages.Unreadable, "not a scenario of a grow step"):
                        pages.describe(scenario)
                    continue
                told = pages.describe(scenario)
                self.assertTrue(all(one.endswith(".") for one in told))
                said.add(path.stem)
        self.assertLessEqual(goals, said)

    def test_a_grow_of_drawn_adders_says_who_adds_what_the_trunk_is_and_when_a_run_ends(self):
        grow = json.loads((SCENARIOS / "grow-waku-loss-1-groupv3.json").read_text("utf-8"))
        self.assertEqual(
            pages.describe(grow),
            [
                "A GroupV3 group, on timers of its own, whatever the file's timers say.",
                "saro creates the group, and the other 199 members wait to be added.",
                "Each round, the seed draws one to 3 of the members that can add, and each adds "
                "the next member at once, on whatever branch it is on.",
                "A member can add once it holds the group, while no member has gone past its "
                "state, unless it was refused a whole round on that state.",
                "The trunk starts at saro's first state and, at each fork, follows the state more "
                "members hold or have gone past.",
                "A run goes on past its first fork, and ends at the start of a round once the "
                "trunk reaches epoch 50, the trunk and the branches off it number 5 or more, all "
                "200 members have been added, or no member can add.",
                "Each member receives every frame late, by a delay of its own from 268 to 659 ms.",
                "A row is a rate of loss: each member loses that share of the frames it receives, "
                "at random, Welcomes among them.",
            ],
        )

    def test_a_grow_by_one_member_of_one_adder_or_of_a_size_says_so_with_its_timers(self):
        def grow(**step) -> dict:
            return {"members": 9, "steps": [{"step": "grow", "epochs": 7, "branches": 2, **step}]}

        self.assertEqual(
            pages.describe(grow(by="saro")),
            [
                "A GroupV2 group, on the harness's fast timers.",
                "saro creates the group, and the other 8 members wait to be added.",
                "Each round, saro alone adds the next member.",
                "saro can add while no member has gone past its state, unless it was refused a "
                "whole round on that state.",
                "The trunk is the line saro is on.",
                "A run goes on past its first fork, and ends at the start of a round once the "
                "trunk reaches epoch 7, the trunk and the branches off it number 2 or more, all 9 "
                "members have been added, or no member can add.",
            ],
        )
        self.assertEqual(
            pages.describe({**grow(adders=1), "timers": "default"})[:3],
            [
                "A GroupV2 group, on GroupV2's default timers.",
                "saro creates the group, and the other 8 members wait to be added.",
                "Each round, the seed draws one of the members that can add, and it adds the next "
                "member at once, on whatever branch it is on.",
            ],
        )
        sized = pages.describe({**grow(adders=2, size=4), "timers": {"voting_delay": 900}})
        self.assertEqual(
            sized[0], "A GroupV2 group, on GroupV2's default timers but voting_delay 900 ms."
        )
        self.assertEqual(
            sized[4],
            "An adder whose add would take the group above 4 members first removes the member "
            "added longest ago that does not add that round.",
        )
        self.assertEqual(
            pages.describe({**grow(adders=2), "group": "v3"})[0],
            "A GroupV3 group, on timers of its own.",
        )

    def test_how_late_frames_come_and_what_is_lost_of_them_and_of_welcomes_is_said_last(self):
        def said(**network) -> List[str]:
            grow = {"step": "grow", "epochs": 7, "branches": 2, "adders": 1}
            return pages.describe({"members": 3, "steps": [grow], "network": network})[-2:]

        self.assertEqual(
            said(seed=1, delay_ms={"saro": 40, "raya": 90}, loss=0.05),
            [
                "Each member receives every frame late, by a delay of its own from 0 to 90 ms.",
                "A row is a rate of loss: each member loses that share of the frames it receives, "
                "at random, but no Welcome.",
            ],
        )
        self.assertEqual(
            said(seed=1, delay_ms=40, loss=0.01, welcome_loss=0.5)[0],
            "Each member receives every frame 40 ms late.",
        )
        self.assertEqual(
            said(seed=1, delay_ms=40, loss=0.01, welcome_loss=0.5)[1],
            "A row is a rate of loss: each member loses that share of the frames it receives other "
            "than Welcomes, at random, and 50% of its Welcomes.",
        )
        quiet = said(seed=1, delay_ms={"saro": 0, "raya": 0, "pax": 0})
        self.assertNotIn("late", " ".join(quiet))
        self.assertTrue(quiet[-1].endswith("at random, Welcomes among them."))

    def test_a_scenario_whose_grow_it_cannot_say_is_refused(self):
        grow = {"step": "grow", "epochs": 7, "branches": 2, "adders": 1}
        for scenario in (
            [],
            {"members": 3},
            {"members": 3, "steps": [grow, {"step": "heal"}]},
            {"members": "3", "steps": [grow]},
            {"members": 3, "steps": [{**grow, "adders": 1.5}]},
            {"members": 3, "steps": [{**grow, "by": 1}]},
            {"members": 3, "steps": [grow], "timers": "slow"},
            {"members": 3, "steps": [grow], "network": {"delay_ms": [40]}},
            {"members": 3, "steps": [grow], "network": {"loss": "1%"}},
            {"members": 3, "steps": [grow], "network": []},
        ):
            with self.subTest(scenario=scenario), self.assertRaises(pages.Unreadable):
                pages.describe(scenario)


DOCS = ROOT / "guide" / "src" / "content" / "docs"
# What a link into the guide is in pages.py's source: the page's slug and the heading's id.
GUIDE_LINK = re.compile(r"guide/([a-z0-9/-]+)/#([a-z0-9-]+)")
# The links into the guide that the pages already on gh-pages were drawn with. A page that is not
# drawn again, such as a long run's past its window, keeps them.
PUBLISHED = (
    ("reports", "logos-chats-runs"),
    ("reports", "the-pages-of-logos-chats-runs"),
    ("simulations", "a-group-that-grows"),
)
# What github-slugger drops from a heading's text, of ASCII, to make the id Starlight gives it.
_DROPPED = re.compile(r"[\x00-\x1f!-,./:-@\[-\^`{-~]")


def heading_ids(slug: str) -> List[str]:
    """The id of each heading of the guide's page `slug`, as Starlight gives them: its text
    lowercased, the punctuation dropped and each space a hyphen, a repeat numbered from 1;
    nothing where the page is not there."""
    found = [DOCS / f"{slug}{suffix}" for suffix in (".md", ".mdx")]
    page = next((path for path in found if path.is_file()), None)
    if page is None:
        return []
    ids: List[str] = []
    seen: Dict[str, int] = {}
    fenced = False
    for line in page.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
        heading = None if fenced else re.match(r"#{1,6} (.+)", line)
        if heading is not None:
            said = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.group(1)).strip()
            anchor = _DROPPED.sub("", said.lower()).replace(" ", "-")
            ids.append(anchor if anchor not in seen else f"{anchor}-{seen[anchor]}")
            seen[anchor] = seen.get(anchor, 0) + 1
    return ids


class Guide(unittest.TestCase):
    """The headings of the guide that the site's pages link to."""

    def test_each_link_pages_writes_into_the_guide_lands_on_a_heading_of_its_page(self):
        source = (ROOT / ".github" / "scripts" / "pages.py").read_text(encoding="utf-8")
        written = GUIDE_LINK.findall(source)
        self.assertEqual(len(written), 7)
        for slug, anchor in written:
            with self.subTest(link=f"guide/{slug}/#{anchor}"):
                self.assertIn(anchor, heading_ids(slug))

    def test_each_link_into_the_guide_a_published_page_holds_lands_on_a_heading(self):
        for slug, anchor in PUBLISHED:
            with self.subTest(link=f"guide/{slug}/#{anchor}"):
                self.assertIn(anchor, heading_ids(slug))


class Pages(LogosChat):
    """The report page of a pull request's run and the page of each of its runs, and the page of
    main's point, as pr and point draw them from what they keep."""

    def moved(self, **edits: dict) -> Path:
        """The runs at a head whose 1% seed 1 split at epoch 5 and 5% seed 0 at epoch 4, sooner
        than the fixture's 9 and 8, and the fields `edits` gives a run changed."""
        sooner = {"1/1": {"first_fork": 5}, "5/0": {"first_fork": 4}}
        for cell, changed in edits.items():
            sooner[cell] = dict(sooner.get(cell, {}), **changed)
        return self.uploaded(**sooner)

    def pr(self, head: Path, matrix: Path, base: Optional[Path], revision: str = BASE) -> Path:
        """Writes the run of pull request 316 at REVISION against `revision`, its base, whose runs
        are under `base`, or against none where `base` is `None`, and returns its directory."""
        given = [str(matrix), str(head), str(self.site), "--pr", "316", "--revision", REVISION]
        given += ["--measurement", MEASUREMENT, "--scenarios", str(SCENARIOS)]
        if base is not None:
            given += ["--base", revision, "--base-runs", str(base)]
        status, _, err = self.run_pages("pr", *given, *self.ran(1, 0))
        self.assertEqual(status, 0, err)
        return self.site / "logos-chat" / "pr-316"

    def nothing(self) -> Path:
        """An empty directory of its own, as a side whose jobs uploaded nothing."""
        self.made += 1
        empty = self.scratch / f"nothing-{self.made}"
        empty.mkdir()
        return empty

    def unbuilt(self, side: Path, cell: str) -> None:
        """Makes the run of `cell` under `side` that of a job that could not build chat-sim: its
        marker and the build's log in place of its text."""
        run = self.run_json(side, cell).parent
        shutil.rmtree(run)
        write(run / "unbuilt", f"chat-sim does not build at {REVISION}\n")
        write(run / "build.log", LOG)

    def drawn(self, directory: Path) -> str:
        return (directory / "index.html").read_text(encoding="utf-8")

    def charts(self, directory: Path) -> Dict[str, ElementTree.Element]:
        """Each chart of the page of `directory`, by what it is labelled, parsed as XML."""
        found = re.findall(r'<svg[^>]* role="img".*?</svg>', self.drawn(directory), re.S)
        return {svg.get("aria-label"): svg for svg in map(ElementTree.fromstring, found)}

    def cells(self, svg: ElementTree.Element) -> List[ElementTree.Element]:
        return [group for group in svg.iter(f"{SVG}g") if group.get("class", "").startswith("cell")]

    def folds(self, directory: Path) -> List[Tuple[bool, str]]:
        """Each run's fold of the page of `directory`: whether it is open, and its summary."""
        fold = r'<details class="run"( open)?>\n<summary>(.*?)</summary>'
        found = re.findall(fold, self.drawn(directory))
        return [(opened == " open", Parsed(summary).text) for opened, summary in found]

    def verdicts(self, directory: Path) -> List[str]:
        """Each line of the report page's list of what each group's runs came to, in words."""
        listed = self.drawn(directory).split('<ul class="verdicts">', 1)[1].split("</ul>", 1)[0]
        return [Parsed(line).text for line in re.findall(r"<li>(.*?)</li>", listed)]

    def key(self, directory: Path) -> List[str]:
        """The words of each item of the key over the report page's trees."""
        runs = self.drawn(directory).split('<section class="chart" id="runs">', 1)[1]
        legend = runs.split('<p class="legend">', 1)[1].split("</p>", 1)[0]
        return re.findall(r"(?:</svg>|</span>)([^<]+)</span>", legend)

    def items(self, page: str) -> List[str]:
        """The text of each item of the lists of `page`."""
        return [html.unescape(one) for one in re.findall(r"<li>(.*?)</li>", page)]

    def about(self, page: str, group: str) -> Parsed:
        """The line under the name of `group` on a pull request's report `page`."""
        found = re.search(rf'<h2>{simulate.GROUPS[group]}</h2>\n<p class="about">(.*?)</p>', page)
        return Parsed(found[1])

    def forks(
        self, directory: Path, group: str = "GroupV2"
    ) -> List[Tuple[str, str, str, ElementTree.Element]]:
        """Each run among `group`'s trees on the report page of `directory`, by its row and then
        its column: its class, its link, what it says over its trees, and its trees, parsed as
        XML."""
        drawn = self.drawn(directory).split(f"<h2>{group}</h2>", 1)[1].split("<h2>", 1)[0]
        fork = r'<a class="([^"]*)" href="([^"]*)"><div class="said">(.*?)</div>(<svg.*?</svg>)</a>'
        return [
            (kind, href, Parsed(said).text, ElementTree.fromstring(svg))
            for kind, href, said, svg in re.findall(fork, drawn)
        ]

    def tiles(
        self, directory: Path, group: str = "GroupV2"
    ) -> List[Tuple[Optional[str], str, str, ElementTree.Element]]:
        """Each run among `group`'s trees on the page of the point in `directory`, in the page's
        order: the page it links, or `None`, what it says beside its tree, what its hover says,
        and its tree, parsed as XML."""
        runs = self.drawn(directory).split('<section class="chart" id="runs">', 1)[1]
        drawn = runs.split(f"<h3>{group}</h3>", 1)[1].split("<h3>", 1)[0]
        tile = (
            r'<(a|div) class="tile"(?: href="([^"]*)")? title="([^"]*)"><span class="at">(.*?)'
            r"</span>(<svg.*?</svg>)</\1>"
        )
        return [
            (
                href or None,
                Parsed(said.replace("<br>", " ")).text,
                html.unescape(hover),
                ElementTree.fromstring(svg),
            )
            for _, href, hover, said, svg in re.findall(tile, drawn)
        ]

    def lines(self, svg: ElementTree.Element, side: str) -> List[Tuple[str, str, str]]:
        """Each line of `side`'s tree in `svg`, its top row first: its title, its path and how wide
        it is drawn."""
        tree = svg.find(f".//{SVG}g[@class='tree {side}']")
        drawn = sorted(tree.findall(f"{SVG}g"), key=lambda line: float(line[2].get("cy")))
        return [(line[0].text, line[1].get("d"), line[1].get("stroke-width")) for line in drawn]

    def trees(self, svg: ElementTree.Element) -> List[str]:
        """The class of each tree in `svg`, the head's first."""
        return [one.get("class") for one in svg.iter(f"{SVG}g") if "tree" in one.get("class", "")]

    def rings(self, svg: ElementTree.Element) -> List[Tuple[str, str]]:
        """Each ring in `svg`: its title and its number."""
        return [
            (ring.find(f"{SVG}title").text, ring.find(f"{SVG}text").text)
            for ring in svg.iter(f"{SVG}g")
            if ring.get("class") == "ring"
        ]

    def stands_alone(self, directory: Path) -> Parsed:
        """The page of `directory`, held to what every page is: it parses as HTML and its SVGs as
        XML, it runs no script and loads nothing, its colours are its style block's alone, and
        each link of its own leads to a file or a page the site holds, the guide's aside."""
        self.assertEqual(self.run_pages("index", str(self.site))[0], 0)
        self.assertEqual(self.run_pages("trend", str(self.site))[0], 0)
        page = self.drawn(directory)
        parsed = Parsed(page)
        for svg in re.findall(r"<svg.*?</svg>", page, re.S):
            ElementTree.fromstring(svg)
        self.assertFalse({"script", "link", "img", "iframe", "object"} & parsed.tags)
        self.assertIsNone(re.search(r"<[^>]* on[a-z]+=", page))
        self.assertIsNone(re.search(r' (style|fill|stroke|color|src)="', page))
        for link in parsed.links:
            if link.startswith("https://") or "guide/" in link:
                continue
            if link.startswith("#"):
                self.assertIn(link[1:], parsed.ids)
                continue
            target = directory / link
            with self.subTest(link=link):
                self.assertTrue((target / "index.html" if link.endswith("/") else target).is_file())
        return parsed

    def test_the_report_page_says_what_ran_and_draws_each_run_s_trees_linked_to_its_page(self):
        base = self.uploaded()
        out = self.pr(self.moved(), self.matrix(self.moved(), base), base)

        parsed = self.stands_alone(out)
        self.assertEqual(parsed.h1, ["logos-chat #316"])
        self.assertIn(f"Ran at {REVISION[:7]} against its merge base {BASE[:7]}.", parsed.text)
        self.assertEqual(
            self.verdicts(out),
            [
                "GroupV2 moved, within chance · 0.1%: nothing moved in seeds 0 to 1 · 1%: 0 "
                "later, 1 earlier, 1 tied (p 1) · 5%: 0 later, 1 earlier, 1 tied (p 1)"
            ],
        )
        # The comment links the trees by it.
        self.assertIn("runs", parsed.ids)
        for link in (
            f"{pages.LOGOS_CHAT_URL}/commit/{REVISION}",
            f"{pages.LOGOS_CHAT_URL}/commit/{BASE}",
            f"{pages.CHAT_SIM_URL}/commit/{SHA}",
            f"{pages.LOGOS_CHAT_URL}/pull/316",
            RUN_URL,
        ):
            self.assertIn(link, parsed.links)
        self.assertEqual(
            self.key(out), ["the head on top", "the base under it", "never joined", "moved"]
        )
        page = self.drawn(out)
        heads = re.findall(r'<div class="rate"><div class="said"><b>(.*?)</b>', page)
        self.assertEqual(heads, ["0.1% loss", "1% loss", "5% loss"])
        self.assertEqual(re.findall(r'<div class="seed"><b>(.*?)</b>', page), ["seed 0", "seed 1"])
        matrix = pages.read_matrix(out / "matrix.json")
        forks = self.forks(out)
        self.assertEqual(
            [(href, svg.get("aria-label")) for _, href, _, svg in forks],
            [
                (simulate.run_page(cell), f"GroupV2 {cell['rate']}, seed {cell['seed']}")
                for cell in matrix["cells"]
            ],
        )
        self.assertEqual(
            [(kind, said) for kind, _, said, _ in forks],
            [
                ("fork", "whole at 50"),
                ("fork", "first fork 46"),
                ("fork", "first fork 18"),
                ("fork moved", "moved first fork 5, base 9"),
                ("fork moved", "moved first fork 4, base 8"),
                ("fork", "first fork 3"),
            ],
        )
        for cell, (*_, svg) in zip(matrix["cells"], forks):
            with self.subTest(run=simulate.run_page(cell)):
                for side in ("head", "base"):
                    self.assertIsNotNone(svg.find(f".//{SVG}g[@class='tree {side} sketch']"))
                self.stands_alone(out / simulate.run_page(cell))

    def test_a_rate_that_ran_more_seeds_has_trees_of_seeds_0_to_2_and_a_strip_of_every_seed(self):
        moved = {"1/3": {"first_fork": 10}, "1/4": {"first_fork": 30}}
        moved["1/5"] = {"conversation": "7c3e9a01b2"}
        head = self.seeded(self.uploaded(), "1", range(2, 8), **moved)
        base = self.seeded(self.uploaded(), "1", range(2, 8))
        self.unbuilt(base, "1/6")
        out = self.pr(head, self.matrix(head, base), base)

        self.stands_alone(out)
        page = self.drawn(out)
        self.assertEqual(
            re.findall(r'<div class="seed"><b>(.*?)</b>', page), ["seed 0", "seed 1", "seed 2"]
        )
        self.assertEqual(
            [href for _, href, _, _ in self.forks(out)],
            [
                f"head/{GOAL.format(rate)}/{run}/"
                for rate, runs in (("0.1", (1, 2)), ("1", (1, 2, 3)), ("5", (1, 2)))
                for run in runs
            ],
        )
        strip = (
            r'<div class="strip"><div class="rate"><div class="said">(.*?)</div></div>'
            r'<div><div class="ds">(.*?)</div><div class="said">(.*?)</div></div></div>'
        )
        ((seeds, ds, said),) = re.findall(strip, page)
        self.assertEqual(seeds, "seeds 0 to 7")
        tested = "1 later, 1 earlier, 5 tied (p 1), does not build at the base in 1 of 8 runs"
        self.assertEqual(said, tested)
        self.assertEqual(self.verdicts(out)[0].split(" · ")[2], f"1%: {tested}")
        d = r'<a class="([^"]*)" href="([^"]*)" title="([^"]*)"><span>([0-9]+)</span>([^<]*)</a>'
        self.assertEqual(
            [(kind, href, seed, shown) for kind, href, _, seed, shown in re.findall(d, ds)],
            [
                ("d", f"head/{GOAL.format(1)}/1/", "0", "0"),
                ("d", f"head/{GOAL.format(1)}/2/", "1", "0"),
                ("d", f"head/{GOAL.format(1)}/3/", "2", "0"),
                ("d moved earlier", f"head/{GOAL.format(1)}/4/", "3", "-8"),
                ("d moved later", f"head/{GOAL.format(1)}/5/", "4", "+12"),
                ("d moved", f"head/{GOAL.format(1)}/6/", "5", "0"),
                ("d none", f"head/{GOAL.format(1)}/7/", "6", "-"),
                ("d", f"head/{GOAL.format(1)}/8/", "7", "0"),
            ],
        )
        self.assertEqual(
            [html.unescape(title) for _, _, title, _, _ in re.findall(d, ds)][3::3],
            [
                "The head first forked at epoch 10, 8 epochs earlier than the base's 18.",
                "The head first forked at epoch 18. chat-sim does not build at the base, so the "
                "two are not compared.",
            ],
        )
        self.assertIn("A strip under a row holds every seed of the rate", page)
        for run in range(4, 9):
            self.stands_alone(out / f"head/{GOAL.format(1)}/{run}")

    def test_a_group_s_verdict_is_its_worst_rate_s_and_each_run_says_how_it_moved_or_why_not(
        self,
    ):
        base = self.uploaded()
        self.unbuilt(base, "0.1/0")
        head = self.uploaded(
            **{
                "0.1/1": {"virtual_s": 1.5},
                "1/1": {"first_fork": 30},
                "5/0": {"first_fork": 4},
                "5/1": {"conversation": "7c3e9a01b2"},
            }
        )
        self.unbuilt(head, "1/0")
        out = self.pr(head, self.matrix(head, base), base)
        self.stands_alone(out)
        self.assertEqual(
            self.verdicts(out),
            [
                "GroupV2 no outcome · 0.1%: 0 later, 0 earlier, 1 tied (p 1), does not build "
                "at the base in 1 of 2 runs · 1%: no outcome in 1 of 2 runs · 5%: 0 later, 1 "
                "earlier, 1 tied (p 1)"
            ],
        )

        forks = self.forks(out)
        self.assertEqual(
            [(kind, said) for kind, _, said, _ in forks],
            [
                ("fork unbuilt", "unbuilt whole at 50 · does not build at the base"),
                ("fork moved", "moved first fork 46, base 46"),
                ("fork unbuilt", "unbuilt does not build at the head"),
                ("fork moved", "moved first fork 30, base 9"),
                ("fork moved", "moved first fork 4, base 8"),
                ("fork moved", "moved first fork 3, base 3"),
            ],
        )
        notes = [
            [text.text for text in svg.iter(f"{SVG}text") if text.get("class") == "note"]
            for *_, svg in forks
        ]
        self.assertEqual(notes[0], ["does not build at the base"])
        self.assertEqual(notes[2], ["does not build at the head"])
        self.assertEqual(notes[5], [])
        both = ["tree head sketch", "tree base sketch"]
        self.assertEqual(
            [self.trees(svg) for *_, svg in forks],
            [["tree head sketch"], both, ["tree base sketch"], both, both, both],
        )
        matrix = pages.read_matrix(out / "matrix.json")
        ran = [
            Parsed(self.drawn(out / simulate.run_page(cell)).split('<p class="ran">')[1]).text
            for cell in matrix["cells"]
        ]
        self.assertEqual(
            [said.split("\n", 1)[0] for said in ran],
            [
                "not compared The head ended whole at epoch 50. chat-sim does not build at the "
                "base, so the two are not compared.",
                "moved The head first forked at epoch 46, and the base first forked at epoch 46. "
                "The runs differ, but not in the epoch of the first fork, so the run counts "
                "neither earlier nor later.",
                "unbuilt chat-sim does not build at the head, so nothing ran.",
                "moved The head first forked at epoch 30, 21 epochs later than the base's 9.",
                "moved The head first forked at epoch 4, 4 epochs earlier than the base's 8.",
                "moved The head first forked at epoch 3, and the base first forked at epoch 3. "
                "The runs differ, but not in the epoch of the first fork, so the run counts "
                "neither earlier nor later.",
            ],
        )
        unbuilt = self.stands_alone(out / f"head/{GOAL.format(1)}/1")
        self.assertEqual([link for link in unbuilt.links if link.endswith(".log")], ["build.log"])

    def test_a_tree_lays_its_lines_out_by_the_line_each_left_and_draws_them_as_wide_as_members(
        self,
    ):
        grown = {"5/0": {"grow": TANGLED}, "1/1": {"grow": LOOPED}}
        base, head = self.uploaded(**grown), self.uploaded(**grown)
        out = self.pr(head, self.matrix(head, base), base)

        self.stands_alone(out)
        self.assertEqual(
            self.key(out),
            ["the head on top", "the base under it", "1, 9 members", "never joined"],
        )
        forks = self.forks(out)
        tangled = forks[4][3]
        drawn = [
            ("trunk: 9 members, epochs 0 to 20", "M4.0 7.0H108.0", "4.60"),
            ("line 3, off the trunk: 1 member, epochs 15 to 18", "M82.0 7.0V14.0H97.6", "2.33"),
            ("line 2, off the trunk: 4 members, epochs 8 to 14", "M45.6 7.0V21.0H76.8", "3.47"),
            ("line 1, off line 2: 2 members, epochs 12 to 16", "M66.4 21.0V28.0H87.2", "2.80"),
            ("line 4, off line 2: 0 members, epoch 9", "M50.8 21.0V35.0H50.8", "1.20"),
        ]
        self.assertEqual(self.lines(tangled, "head"), drawn)
        self.assertEqual(
            [(title, width) for title, _, width in self.lines(tangled, "base")],
            [(title, width) for title, _, width in drawn],
        )
        self.assertEqual(self.rings(tangled), [("2 never joined", "2")] * 2)
        looped = forks[3][3]
        sketch = looped.find(f".//{SVG}g[@class='tree head sketch']")
        self.assertEqual(
            sketch.find(f"{SVG}title").text,
            "split at epoch 8, 2 lines off the trunk: no lineage kept",
        )
        self.assertEqual(sketch.findall(f"{SVG}path")[1].get("d"), "M45.6 7.0V14.0")
        self.assertEqual(self.rings(looped), [("2 never joined", "2")] * 2)
        large = self.charts(out / f"head/{GOAL.format(5)}/1")
        tree = large["GroupV2 5%, seed 0: the head's tree over the base's"]
        self.assertEqual(
            [title for title, _, _ in self.lines(tree, "head")], [title for title, _, _ in drawn]
        )

    def test_the_base_of_a_run_that_drew_other_ids_is_drawn_and_counted_in_the_widths(self):
        trunk = dict(GROW["lines"][0], members=[f"peer{n}" for n in range(30)])
        base = self.uploaded(**{"5/1": {"grow": dict(GROW, lines=[trunk, *GROW["lines"][1:]])}})
        head = self.uploaded(**{"5/1": {"grow": GROW, "conversation": "7c3e9a01b2"}})
        out = self.pr(head, self.matrix(head, base), base)

        self.stands_alone(out)
        self.assertEqual(self.key(out)[2], "1, 10, 30 members")
        self.assertEqual(self.trees(self.forks(out)[5][3]), ["tree head", "tree base"])

    def test_a_page_whose_trees_have_no_member_on_any_line_draws_each_line_thin(self):
        grown = {"5/0": {"grow": EMPTY}}
        base, head = self.uploaded(**grown), self.uploaded(**grown)
        out = self.pr(head, self.matrix(head, base), base)

        self.stands_alone(out)
        self.assertEqual(
            self.lines(self.forks(out)[4][3], "head"),
            [("trunk: 0 members, epoch 0", "M4.0 7.0H4.0", "1.20")],
        )
        self.assertEqual(self.key(out), ["the head on top", "the base under it", "never joined"])
        parsed = self.stands_alone(out / f"head/{GOAL.format(5)}/1")
        self.assertIn("0 on the trunk · 0 on 0 lines off it", parsed.text)

    def test_each_run_s_page_says_how_it_moved_and_draws_both_trees_large_with_its_command_and_text(
        self,
    ):
        base = self.uploaded(**{"5/0": {"grow": GROW}})
        out = self.pr(self.moved(), self.matrix(self.moved(), base), base)

        matrix = pages.read_matrix(out / "matrix.json")
        for cell in matrix["cells"]:
            run = simulate.run_page(cell)
            with self.subTest(run=run):
                parsed = self.stands_alone(out / run)
                self.assertEqual(
                    parsed.h1, [f"GroupV2 · {cell['rate']} loss · seed {cell['seed']}"]
                )
                self.assertIn("../../../", parsed.links)
                for side in (REVISION, BASE):
                    self.assertIn("\n".join(simulate.again(cell, side, SHA)), parsed.text)
                self.assertEqual(
                    [link for link in parsed.links if link.endswith((".txt", ".json"))],
                    [
                        f"{at}{name}"
                        for at in ("", f"../../../base/{cell['scenario']}/{cell['run']}/")
                        for name in ("run.txt", "report.txt", "run.json")
                    ],
                )
        run = out / f"head/{GOAL.format(5)}/1"
        page = self.drawn(run)
        ran = Parsed(page.split('<p class="ran">', 1)[1].split("</p>", 1)[0]).text
        self.assertEqual(
            ran, "moved The head first forked at epoch 4, 4 epochs earlier than the base's 8."
        )
        tree = self.charts(run)["GroupV2 5%, seed 0: the head's tree over the base's"]
        self.assertEqual([title for title, _, _ in self.lines(tree, "base")], GROWN)
        self.assertIsNotNone(tree.find(f".//{SVG}g[@class='tree head sketch']"))
        parsed = Parsed(page)
        for said in (
            "trunk 11 members, epochs 0 to 14",
            "1 member, epoch 11",
            "1 member, epoch 8",
            "first fork 4",
            "first fork 8",
            "the site keeps no lineage of this run: its trunk and its first fork, from how it "
            "came out",
            "mira at 5",
            "kes, peer7 at 6",
            "saro at 13",
            "15 seated · 11 on the trunk · 2 on 2 lines off it · 5 behind at the end · ",
        ):
            self.assertIn(said, parsed.text)
        self.assertEqual(self.rings(tree), [("2 never joined", "2")] * 2)

    def test_a_run_s_page_says_what_seated_counts_and_running_it_takes_and_tables_its_rounds(self):
        base = self.uploaded(**{"5/0": {"grow": dict(GROW, rounds=ROUNDS)}})
        out = self.pr(self.moved(), self.matrix(self.moved(), base), base)

        run = out / f"head/{GOAL.format(5)}/1"
        parsed = self.stands_alone(run)
        self.assertIn(
            "seated counts every member added, the creator included, and not removed: those on a "
            "line and those that never joined.",
            parsed.text,
        )
        self.assertIn(
            "It needs Linux on x86-64, Nix with flakes, jq and python3. Run each block from a "
            "directory of its own, since each clones chat-sim.",
            parsed.text,
        )
        rounds = Parsed(self.drawn(run).split("<h2>The rounds</h2>", 1)[1].split("<h2>", 1)[0])
        self.assertEqual(rounds.rows, ROUNDED)
        self.assertIn(f"the base {BASE[:7]}", rounds.text)
        self.assertNotIn("the head", rounds.text)
        self.assertIn("The run does not say which adder added which member.", rounds.text)
        self.assertNotIn("<h2>The rounds</h2>", self.drawn(out / f"head/{GOAL.format(5)}/2"))

    def test_a_run_of_both_groups_lists_each_group_s_verdict_and_draws_its_runs_apart(self):
        base = self.uploaded(groupv3=True)
        head = self.uploaded(
            groupv3=True,
            **{
                "v3/1/1": {"first_fork": 5},
                "v3/5/0": {"first_fork": 4},
            },
        )
        out = self.pr(head, self.matrix(head, base), base)

        self.stands_alone(out)
        self.assertEqual(
            self.verdicts(out),
            [
                "GroupV2 nothing moved · 0.1%: nothing moved in seeds 0 to 1 · 1%: nothing moved "
                "in seeds 0 to 1 · 5%: nothing moved in seeds 0 to 1",
                "GroupV3 moved, within chance · 0.1%: nothing moved in seeds 0 to 1 · 1%: 0 "
                "later, 1 earlier, 1 tied (p 1) · 5%: 0 later, 1 earlier, 1 tied (p 1)",
            ],
        )
        self.assertEqual(re.findall(r"<h2>(.*?)</h2>", self.drawn(out)), ["GroupV2", "GroupV3"])
        self.assertEqual({kind for kind, *_ in self.forks(out, "GroupV2")}, {"fork"})
        v3 = HELD_GOALS["v3"]
        self.assertEqual(
            [(kind, href) for kind, href, *_ in self.forks(out, "GroupV3") if kind != "fork"],
            [("fork moved", f"head/{v3.format(1)}/2/"), ("fork moved", f"head/{v3.format(5)}/1/")],
        )
        self.assertEqual(
            self.stands_alone(out / f"head/{v3.format(1)}/2").h1, ["GroupV3 · 1% loss · seed 1"]
        )

    def test_the_report_says_what_its_scenarios_test_and_how_and_links_each_one_s_file(self):
        base, head = self.uploaded(groupv3=True), self.uploaded(groupv3=True)
        out = self.pr(head, self.matrix(head, base), base)
        page = self.drawn(out)

        self.stands_alone(out)
        verdicts, scenarios, runs = (
            page.index('<ul class="verdicts">'),
            page.index('<h2 id="scenarios">'),
            page.index('id="runs"'),
        )
        self.assertLess(verdicts, scenarios)
        self.assertLess(scenarios, runs)
        section = page[scenarios:runs]
        self.assertEqual(
            re.findall(r'<p class="lead">(.*?)</p>', section),
            ["How many epochs a growing group of logos-chat clients lasts before it first forks."],
        )
        self.assertEqual(
            self.items(section),
            [
                "saro creates the group, and the other 50 members wait to be added.",
                "Each round, saro alone adds the next member.",
                "saro can add while no member has gone past its state, unless it was refused a "
                "whole round on that state.",
                "The trunk is the line saro is on.",
                "A run goes on past its first fork, and ends at the start of a round once the "
                "trunk reaches epoch 50, the trunk and the branches off it number 51 or more, all "
                "51 members have been added, or no member can add.",
                "Each member receives every frame late, by a delay of its own from 284 to 644 ms.",
                WELCOMED,
                "A column is a seed, the same at the head and at the base. At one revision a seed "
                "runs the same every time, so where a column's two trees differ, the pull request "
                "changed the run. The change may be only a re-deal of the losses: one frame "
                "more, anywhere, shifts which frames each member loses after it.",
            ],
        )
        self.assertIn("../../guide/understand/scenarios/#a-group-that-grows", Parsed(section).links)
        for group, timers in (
            ("v2", "A GroupV2 group, on GroupV2's default timers."),
            ("v3", "A GroupV3 group, on timers of its own, whatever the file's timers say."),
        ):
            names = [HELD_GOALS[group].format(rate) for rate in ("0.1", "1", "5")]
            with self.subTest(group=group):
                about = self.about(page, group)
                self.assertEqual(
                    about.text,
                    f"{timers} The scenario files: " + " · ".join(f"{one}.json" for one in names),
                )
                self.assertEqual(about.links, [f"scenarios/{one}.json" for one in names])

    def test_what_some_scenarios_alone_say_is_said_under_their_group_or_beside_their_file(self):
        base, head = self.uploaded(groupv3=True), self.uploaded(groupv3=True)
        out = self.pr(head, self.matrix(head, base), base)
        lone = out / "scenarios" / f"{GOAL.format(1)}.json"
        scenario = json.loads(lone.read_text(encoding="utf-8"))
        scenario["network"]["welcome_loss"] = 0
        lone.write_text(json.dumps(scenario), encoding="utf-8")
        pages.draw_pr(
            out, pages.read_matrix(out / "matrix.json"), pages.read_pr_meta(out / "meta.json")
        )
        page = self.drawn(out)

        self.assertNotIn(WELCOMED, self.items(page.split('id="runs"')[0]))
        unwelcomed = WELCOMED.replace("Welcomes among them", "but no Welcome")
        self.assertEqual(
            self.about(page, "v2").text,
            "A GroupV2 group, on GroupV2's default timers. The scenario files: "
            f"{GOAL.format('0.1')}.json ({WELCOMED}) · {GOAL.format(1)}.json ({unwelcomed}) · "
            f"{GOAL.format(5)}.json ({WELCOMED})",
        )
        v3 = HELD_GOALS["v3"]
        self.assertEqual(
            self.about(page, "v3").text,
            f"A GroupV3 group, on timers of its own, whatever the file's timers say. {WELCOMED} "
            "The scenario files: "
            + " · ".join(f"{v3.format(rate)}.json" for rate in ("0.1", "1", "5")),
        )

    def test_a_run_whose_scenario_copies_it_cannot_say_names_its_files_and_describes_none(self):
        for spoilt in ("kept none", "not a grow", "not JSON"):
            with self.subTest(spoilt=spoilt):
                out = self.pr(self.uploaded(), self.matrix(self.uploaded()), None)
                lone = out / "scenarios" / f"{GOAL.format(1)}.json"
                if spoilt == "kept none":
                    shutil.rmtree(out / "scenarios")
                elif spoilt == "not a grow":
                    shutil.copy(SCENARIOS / "three.json", lone)
                else:
                    lone.write_text("{", encoding="utf-8")
                pages.draw_pr(
                    out,
                    pages.read_matrix(out / "matrix.json"),
                    pages.read_pr_meta(out / "meta.json"),
                )
                page = self.drawn(out)

                self.assertNotIn('id="scenarios"', page)
                names = [GOAL.format(rate) for rate in ("0.1", "1", "5")]
                said = "The scenario files: " + " · ".join(f"{one}.json" for one in names)
                self.assertEqual(self.about(page, "v2").text, said)
                kept = [] if spoilt == "kept none" else [f"scenarios/{one}.json" for one in names]
                self.assertEqual(self.about(page, "v2").links, kept)
                self.stands_alone(out)

    def test_without_a_base_the_head_s_trees_stand_alone(self):
        alone = self.pr(self.moved(), self.matrix(self.moved()), None)
        self.stands_alone(alone)
        self.assertEqual(
            self.verdicts(alone), ["GroupV2 not compared · 6 runs, no base to compare with"]
        )
        self.assertEqual(self.key(alone), ["the head on top", "never joined"])
        self.assertEqual(
            re.findall(r'<text class="note"[^>]*>(head|base)</text>', self.drawn(alone)),
            ["head"] * 3,
        )
        forks = self.forks(alone)
        self.assertEqual([said for _, _, said, _ in forks][3], "first fork 5")
        self.assertEqual([self.trees(svg) for *_, svg in forks], [["tree head sketch"]] * 6)
        run = self.stands_alone(alone / f"head/{GOAL.format(1)}/2")
        ran = self.drawn(alone / f"head/{GOAL.format(1)}/2").split('<p class="ran">', 1)[1]
        self.assertEqual(
            Parsed(ran.split("</p>", 1)[0]).text,
            "not compared The head first forked at epoch 5. There is no base to compare.",
        )
        self.assertIn("no base", run.text)
        self.assertEqual([link for link in run.links if "base/" in link], [])
        cell = {"scenario": GOAL.format(1), "run": 2, "seed": 1}
        self.assertIn("\n".join(simulate.again(cell, REVISION, SHA)), run.text)
        self.assertNotIn("Run each block", run.text)
        self.assertEqual(run.text.count("git clone "), 1)

    def test_where_chat_sim_did_not_build_the_build_s_log_takes_the_place_of_the_cells(self):
        head = self.scratch / "unbuilt"
        for rate in ("0.1", "1", "5"):
            for number in (1, 2):
                scenario = GOAL.format(rate)
                run = head / f"sim-revision-{scenario}-{number}" / scenario / str(number)
                write(run / "unbuilt", f"chat-sim does not build at {REVISION}\n")
                write(run / "build.log", LOG)
        asked = [
            {"scenario": GOAL.format(rate), "from": 1, "runs": 2} for rate in ("0.1", "1", "5")
        ]
        base = self.uploaded()
        out = self.pr(head, self.matrix(head, base, asked), base)

        parsed = self.stands_alone(out)
        self.assertIn(
            f"chat-sim does not build against this commit at {REVISION[:7]} against its merge "
            f"base {BASE[:7]}",
            parsed.text,
        )
        self.assertIn("build", parsed.ids)
        self.assertFalse({"matrix", "cells", "runs"} & parsed.ids)
        self.assertIn(LOG, parsed.text)
        self.assertIn(f"head/{GOAL.format('0.1')}/1/build.log", parsed.links)
        self.assertEqual(
            [path for path in self.files(out) if path.startswith("head/")],
            sorted(
                f"head/{GOAL.format(rate)}/{number}/build.log"
                for rate in ("0.1", "1", "5")
                for number in (1, 2)
            ),
        )

    def test_every_text_of_a_run_is_escaped(self):
        fell = [dict(GROW["fell_behind"][0], name=MARKUP)]
        head = self.moved(**{"1/1": {"grow": dict(GROW, fell_behind=fell)}})
        for name in ("run.txt", "report.txt"):
            write(self.run_json(head, "1/1").with_name(name), f"{MARKUP}\n")
        base = self.uploaded()
        out = self.pr(head, self.matrix(head, base), base)

        run = out / f"head/{GOAL.format(1)}/2/index.html"
        for drawn in (out / "index.html", run):
            with self.subTest(drawn=drawn.relative_to(out)):
                self.assertNotIn("<script", drawn.read_text(encoding="utf-8"))
        # The run's page names who fell behind.
        self.assertIn(MARKUP, Parsed(run.read_text(encoding="utf-8")).text)

    def test_a_point_s_page_draws_its_cells_its_rates_and_each_run_as_a_tree_linked_to_its_page(
        self,
    ):
        self.point(sha(1), minute=1, run_number=1)
        timed = {"5/0": {"grow": GROW, "wall_s": 0.916510242}, "1/1": {"wall_s": 189.4}}
        merged = self.commits(f"{TITLE} (#321)", "chore: bump the toolchain (#318)")
        self.point(sha(2), minute=2, run_number=2, runs=self.uploaded(**timed), commits=merged)

        at, point = self.at(sha(2)), self.read(sha(2))
        parsed = self.stands_alone(at)
        self.assertEqual(parsed.h1, [f"logos-chat main {sha(2)[:7]}, 2026-11-09"])
        self.assertIn(f"#321 {TITLE}, #318 chore: bump the toolchain", parsed.text)
        self.assertIn(f"{pages.LOGOS_CHAT_URL}/pull/321", parsed.links)
        self.assertIn(f"../../{sha(1)}/{MEASUREMENT}/", parsed.links)
        self.assertNotIn("<script", self.drawn(at))
        cells = self.cells(self.charts(at)["The point's GroupV2 runs"])
        self.assertEqual(
            [cell.find(f"{SVG}title").text for cell in cells],
            [
                f"{cell['rate']}, seed {cell['seed']} · {cell['epochs']['head']} epochs without a "
                f"fork · {cell['head']['came_to']}"
                for cell in point["cells"]
            ],
        )
        rates = [row for row in parsed.rows if len(row) == 13 and row[1].endswith("%")]
        self.assertEqual(
            [row[:11] + row[12:] for row in rates],
            [
                ["GroupV2", "0.1%", "2", "1", "0", "1", "0", "0", "46", "48", "50", "-"],
                ["GroupV2", "1%", "2", "0", "0", "2", "0", "0", "9", "13.5", "18", "3 min 9 s"],
                ["GroupV2", "5%", "2", "0", "0", "2", "0", "0", "3", "5.5", "8", "0.9 s"],
            ],
        )
        page = self.drawn(at)
        self.assertIn('<div class="v2">\n<h3>GroupV2</h3>', page)
        self.assertEqual(
            re.findall(r'<p class="of"><b>(.*?)</b><span>(.*?)</span></p>', page),
            [
                (f"{rate}% loss", "2 runs, the fewest epochs without a fork on top")
                for rate in ("0.1", "1", "5")
            ],
        )
        self.assertEqual(
            re.findall(r'<div class="median">(.*?)</div>', page),
            ["median 48", "median 13.5", "median 5.5"],
        )
        # A column a rate, and in it the run that went the fewest epochs without a fork first.
        by_run = {pages._run_href(cell): cell for cell in point["cells"]}
        tiles = self.tiles(at)
        self.assertEqual(
            [(href, said) for href, said, _, _ in tiles],
            [
                (f"{GOAL.format('0.1')}/2/", "seed 1 first fork 46"),
                (f"{GOAL.format('0.1')}/1/", "seed 0 whole at 50"),
                (f"{GOAL.format(1)}/2/", "seed 1 first fork 9"),
                (f"{GOAL.format(1)}/1/", "seed 0 first fork 18"),
                (f"{GOAL.format(5)}/2/", "seed 1 first fork 3"),
                (f"{GOAL.format(5)}/1/", "seed 0 first fork 8"),
            ],
        )
        for href, _, hover, svg in tiles:
            with self.subTest(run=href):
                cell = by_run[href]
                self.assertEqual(hover, simulate.words(cell))
                job = f"GroupV2 {cell['rate']}, seed {cell['seed']}"
                self.assertEqual(svg.get("aria-label"), job)
                # The point keeps the lines of the one run whose run.json holds a tree's.
                kept = href == f"{GOAL.format(5)}/1/"
                self.assertEqual(self.trees(svg), ["tree head" if kept else "tree head sketch"])
        grown = tiles[-1][3]
        self.assertEqual([title for title, _, _ in self.lines(grown, "head")], GROWN)
        self.assertEqual(self.rings(grown), [("2 never joined", "2")])
        self.assertEqual(self.key(at), ["1, 10, 11 members", "never joined"])
        for said in (
            "A column holds a rate's runs, the fewest epochs without a fork on top",
            "or open a run for its labels, its rounds, its text and the commands that run it again",
            "A dashed tree is of a run whose lines the site does not keep",
        ):
            self.assertIn(said, parsed.text)
        # The commands and the text are on each run's page.
        self.assertNotIn("git clone", parsed.text)
        self.assertFalse([link for link in parsed.links if link.endswith((".txt", ".json"))])
        for cell in point["cells"]:
            with self.subTest(run=pages._run_href(cell)):
                run = self.stands_alone(at / pages._run_href(cell))
                self.assertEqual(run.h1, [f"GroupV2 · {cell['rate']} loss · seed {cell['seed']}"])
                self.assertEqual(
                    run.links[:3], ["../../../../../../", "../../../../../", "../../"]
                )
                self.assertIn("\n".join(simulate.again(cell, sha(2), SHA)), run.text)
                self.assertEqual(
                    [link for link in run.links if link.endswith((".txt", ".json"))],
                    ["run.txt", "report.txt", "run.json"],
                )

    def test_the_page_of_a_run_of_a_point_draws_its_tree_large_with_its_rounds_and_wall_time(
        self,
    ):
        grow = dict(GROW, rounds=ROUNDS)
        self.point(runs=self.uploaded(**{"5/0": {"grow": grow, "wall_s": 0.916510242}}))

        run = self.at() / GOAL.format(5) / "1"
        parsed, page = self.stands_alone(run), self.drawn(run)
        self.assertIn(
            f"<title>chat-sim: logos-chat main {REVISION[:7]}, GroupV2 5%, seed 0</title>", page
        )
        ran = Parsed(page.split('<p class="ran">', 1)[1].split("</p>", 1)[0]).text
        self.assertEqual(ran, "The run first forked at epoch 8.")
        self.assertIn("UTC · wall time 0.9 s", parsed.text)
        # The tree is in the group's hue.
        self.assertIn('<div class="chart wide v2">', page)
        tree = self.charts(run)["GroupV2 5%, seed 0: its tree"]
        self.assertEqual(self.trees(tree), ["tree head"])
        self.assertEqual([title for title, _, _ in self.lines(tree, "head")], GROWN)
        self.assertEqual(self.rings(tree), [("2 never joined", "2")])
        labels = [one.text for one in tree.iter(f"{SVG}text")]
        self.assertEqual(labels[labels.index("the goal") + 1 :][:2], ["main", REVISION[:7]])
        for said in (
            "trunk 11 members, epochs 0 to 14",
            "first fork 8",
            "mira at 5",
            "kes, peer7 at 6",
            "15 seated · 11 on the trunk · 2 on 2 lines off it · 5 behind at the end · ",
            "seated counts every member added, the creator included, and not removed",
            "It needs Linux on x86-64, Nix with flakes, jq and python3.",
        ):
            self.assertIn(said, parsed.text)
        self.assertNotIn("Run each block from a directory of its own", parsed.text)
        rounds = Parsed(page.split("<h2>The rounds</h2>", 1)[1].split("<h2>", 1)[0])
        self.assertEqual(rounds.rows, ROUNDED)
        self.assertIn("The run does not say which adder added which member.", rounds.text)
        # A run whose run.json holds no rounds, no wall time and no tree's lines.
        other = self.at() / GOAL.format(5) / "2"
        self.assertNotIn("<h2>The rounds</h2>", self.drawn(other))
        self.assertNotIn("wall time", self.drawn(other))
        sketched = self.charts(other)["GroupV2 5%, seed 1: its tree"]
        self.assertEqual(self.trees(sketched), ["tree head sketch"])
        self.assertIn("the site keeps no lineage of this run", self.stands_alone(other).text)

    def test_the_page_of_a_run_of_a_point_gives_the_command_that_downloads_its_log(self):
        self.point()
        for rate in ("0.1", "1", "5"):
            command = (
                "gh run download 42 --repo logos-messaging/chat-sim --dir logs --pattern "
                f"'logs-revision-{GOAL.format(rate)}-[0-9]*'"
            )
            for run in (1, 2):
                with self.subTest(rate=rate, run=run):
                    parsed = self.stands_alone(self.at() / GOAL.format(rate) / str(run))
                    self.assertEqual(parsed.text.count(command), 1)
                    self.assertIn(
                        "an artifact of the workflow run, which GitHub keeps 90 days. This "
                        "downloads its scenario's, and the run's log is group.log under run "
                        f"{run}.",
                        parsed.text,
                    )
                    self.assertIn(
                        "../../../../../../guide/investigate/moved-run/#into-the-log", parsed.links
                    )
        parsed = self.stands_alone(self.at())
        self.assertNotIn("gh run download", parsed.text)
        self.assertEqual([link for link in parsed.links if "group.log" in link], [])

    def test_a_run_of_a_point_that_did_not_build_has_no_tree_and_no_command_for_its_log(self):
        runs = self.uploaded()
        self.unbuilt(runs, "5/0")
        self.point(runs=runs)
        unbuilt = self.at() / GOAL.format(5) / "1"
        parsed = self.stands_alone(unbuilt)
        self.assertIn("chat-sim does not build at this commit, so nothing ran.", parsed.text)
        self.assertNotIn("gh run download", parsed.text)
        self.assertEqual(self.charts(unbuilt), {})
        self.assertEqual([link for link in parsed.links if link.endswith(".log")], ["build.log"])
        again = simulate.again(self.read()["cells"][4], REVISION, SHA)
        self.assertIn("\n".join(again), parsed.text)
        self.assertIn("gh run download", self.stands_alone(self.at() / GOAL.format(5) / "2").text)
        # On the point's page it has no tree: its seed is under its rate, linked to its page.
        self.assertIn(
            f'<p class="of muted">does not build: seed <a href="{GOAL.format(5)}/1/">0</a></p>',
            self.drawn(self.at()),
        )
        self.assertEqual(
            [said for _, said, _, _ in self.tiles(self.at())][4:], ["seed 1 first fork 3"]
        )
        self.assertEqual(
            re.findall(r'<div class="median">(.*?)</div>', self.drawn(self.at()))[2], "median 3"
        )

    def test_a_point_whose_run_is_not_a_workflow_run_s_has_no_command_for_its_logs(self):
        self.point(run_url="https://ci.example/runs/42")
        self.stands_alone(self.at())
        drawn = sorted(self.at().glob("*/*/index.html"))
        self.assertEqual(len(drawn), 6)
        for run in drawn:
            self.assertNotIn("gh run download", run.read_text(encoding="utf-8"))

    def test_a_run_s_page_gives_the_command_that_downloads_the_log_of_each_side_that_ran(self):
        scenario, at = GOAL.format(1), "--repo logos-messaging/chat-sim --dir logs"
        head = f"--pattern 'logs-revision-{scenario}-[0-9]*'"
        base = f"'logs-base-{scenario}-[0-9]*'"
        out = self.pr(self.moved(), self.matrix(self.moved(), self.uploaded()), self.uploaded())
        parsed = self.stands_alone(out / f"head/{scenario}/2")
        self.assertIn(f"gh run download 42 {at} {head} --pattern {base}\n", parsed.text)
        self.assertIn("the run's log is group.log under run 2 of each.", parsed.text)
        drawn = (out / f"head/{scenario}/2/index.html").read_text(encoding="utf-8")
        self.assertIn("<title>chat-sim: logos-chat #316, GroupV2 1%, seed 1</title>", drawn)
        self.assertIn("../../../../../guide/investigate/moved-run/#into-the-log", parsed.links)
        out = self.pr(self.moved(), self.matrix(self.moved()), None)
        parsed = self.stands_alone(out / f"head/{scenario}/2")
        self.assertIn(f"gh run download 42 {at} {head}\n", parsed.text)
        self.assertIn("the run's log is group.log under run 2.", parsed.text)
        self.assertIn("which GitHub keeps 90 days. This downloads the head's, and", parsed.text)
        # A side whose job could not build chat-sim uploaded no log.
        lacking = self.uploaded()
        self.unbuilt(lacking, "1/1")
        out = self.pr(self.moved(), self.matrix(self.moved(), lacking), lacking)
        parsed = self.stands_alone(out / f"head/{scenario}/2")
        self.assertIn(f"gh run download 42 {at} {head}\n", parsed.text)
        out = self.pr(lacking, self.matrix(lacking, self.uploaded()), self.uploaded())
        parsed = self.stands_alone(out / f"head/{scenario}/2")
        self.assertIn(f"gh run download 42 {at} --pattern {base}\n", parsed.text)
        self.assertIn("This downloads the base's, and", parsed.text)

    def test_the_graph_over_main_the_page_of_a_change_and_the_earlier_points_stand_alone(self):
        # A point of another test, then two commits of the test the graph draws, the second of
        # which moved a first fork.
        self.point(sha(1), minute=1, run_number=1)
        again = dict(measurement=MOVED, commits=self.commits())
        self.point(sha(1), minute=2, run_number=2, runs=self.uploaded(**EARLIER), **again)
        self.point(sha(2), minute=3, run_number=3, **again)
        logos_chat = self.site / "logos-chat"
        for directory in (logos_chat, logos_chat / "earlier", self.at(sha(2), MOVED) / "change"):
            with self.subTest(page=directory.relative_to(self.site)):
                self.stands_alone(directory)

    def test_a_point_of_both_groups_draws_each_group_s_heatmap_and_its_trees_in_its_hue(self):
        self.point(runs=self.uploaded(groupv3=True, **{"v3/1/1": {"first_fork": 5}}))

        parsed = self.stands_alone(self.at())
        matrix, runs = self.drawn(self.at()).split('<section class="chart" id="runs">', 1)
        self.assertEqual(re.findall(r"<h3>(.*?)</h3>", matrix), ["GroupV2", "GroupV3"])
        charts = self.charts(self.at())
        self.assertEqual(
            list(charts)[:2], ["The point's GroupV2 runs", "The point's GroupV3 runs"]
        )
        shown = [
            [cell.find(f"{SVG}text").text for cell in self.cells(svg)]
            for svg in list(charts.values())[:2]
        ]
        self.assertEqual(
            shown, [["50", "46", "18", "9", "8", "3"], ["50", "46", "18", "5", "8", "3"]]
        )
        named = [
            f"{group} {rate}" for group in ("GroupV2", "GroupV3") for rate in ("0.1%", "1%", "5%")
        ]
        rates = [row[:2] for row in parsed.rows if len(row) == 13 and row[1].endswith("%")]
        self.assertEqual([" ".join(row) for row in rates], named)
        self.assertEqual(
            re.findall(r'<div class="(v[23])">\n<h3>(.*?)</h3>', runs),
            [("v2", "GroupV2"), ("v3", "GroupV3")],
        )
        for key, called in simulate.GROUPS.items():
            with self.subTest(group=called):
                tiles = self.tiles(self.at(), called)
                self.assertEqual(
                    [href for href, *_ in tiles],
                    [
                        f"{HELD_GOALS[key].format(rate)}/{run}/"
                        for rate in ("0.1", "1", "5")
                        for run in (2, 1)
                    ],
                )
                self.assertEqual(
                    tiles[2][1], f"seed 1 first fork {5 if key == 'v3' else 9}"
                )
        v3 = self.at() / HELD_GOALS["v3"].format(1) / "2"
        self.assertEqual(self.stands_alone(v3).h1, ["GroupV3 · 1% loss · seed 1"])
        self.assertIn('<div class="chart wide v3">', self.drawn(v3))
        self.assertEqual(len(list(self.at().glob("*/*/index.html"))), 12)

    def test_a_point_json_of_groups_that_says_nothing_of_how_its_runs_forked_is_read_as_written(
        self,
    ):
        self.point(runs=self.uploaded(groupv3=True))
        path, written = self.at() / "point.json", unforked(self.read())
        write(path, json.dumps(written))

        point = pages.read_point(path)
        pages.draw_point(self.at(), point)

        self.assertEqual(point, written)
        # Each group's heatmap, and then each group's trees.
        self.assertEqual(
            re.findall(r"<h3>(.*?)</h3>", self.drawn(self.at())), ["GroupV2", "GroupV3"] * 2
        )
        self.stands_alone(self.at())
        status, _, err = self.run_pages("trend", str(self.site))
        self.assertEqual((status, err), (0, ""))

    def test_a_point_json_whose_outcomes_hold_no_first_fork_is_read_and_drawn_as_its_verdicts(
        self,
    ):
        self.point()
        path, written = self.at() / "point.json", unpaired(self.read())
        drawn = self.stands_alone(self.at()).text
        for cell in written["cells"]:
            reported(cell["head"])
        write(path, json.dumps(written))

        point = pages.read_point(path)
        pages.draw_point(self.at(), point)

        self.assertEqual(point, written)
        self.assertEqual(self.stands_alone(self.at()).text, drawn)
        self.assertIn("split at epoch 18, 7 lines off the trunk: no lineage kept", drawn)
        status, _, err = self.run_pages("trend", str(self.site))
        self.assertEqual((status, err), (0, ""))

    def test_a_point_json_whose_outcomes_count_no_member_that_never_joined_is_read_as_written(self):
        self.point()
        path, written = self.at() / "point.json", unjoined(self.read())
        write(path, json.dumps(written))

        point = pages.read_point(path)
        pages.draw_point(self.at(), point)

        self.assertEqual(point, written)
        self.stands_alone(self.at())
        tiles = self.tiles(self.at())
        hovers = [hover for _, _, hover, _ in tiles]
        self.assertIn("split at 8, 2 lines and 9 of 15 members off the trunk", hovers)
        self.assertFalse([hover for hover in hovers if "never joined" in hover])
        # Nor does a tree have the ring of them.
        self.assertEqual([self.rings(svg) for *_, svg in tiles], [[]] * 6)
        status, _, err = self.run_pages("trend", str(self.site))
        self.assertEqual((status, err), (0, ""))

    def test_a_point_json_that_names_no_group_is_read_as_written_and_drawn_as_group_v2_s(self):
        self.point()
        path, written = self.at() / "point.json", unforked(self.read())
        for one in written["cells"] + written["summary"]:
            del one["group"]
        write(path, json.dumps(written))

        point = pages.read_point(path)
        pages.draw_point(self.at(), point)

        self.assertEqual(point, written)
        self.assertEqual({pages.group(one) for one in point["cells"] + point["summary"]}, {"v2"})
        parsed = self.stands_alone(self.at())
        self.assertEqual(re.findall(r"<h3>(.*?)</h3>", self.drawn(self.at())), ["GroupV2"] * 2)
        self.assertEqual(len(self.cells(self.charts(self.at())["The point's GroupV2 runs"])), 6)
        self.assertEqual(len(self.tiles(self.at())), 6)
        rates = [row[:2] for row in parsed.rows if len(row) == 13 and row[1].endswith("%")]
        self.assertEqual(rates, [["GroupV2", rate] for rate in ("0.1%", "1%", "5%")])

    def test_a_point_that_did_not_build_shows_its_log_once_and_past_the_window_says_it_is_gone(
        self,
    ):
        unbuilt = self.scratch / "unbuilt"
        for rate in ("0.1", "1", "5"):
            for number in (1, 2):
                scenario = GOAL.format(rate)
                run = unbuilt / f"sim-revision-{scenario}-{number}" / scenario / str(number)
                write(run / "unbuilt", f"chat-sim does not build at {sha(1)}\n")
                write(run / "build.log", LOG)
        asked = [
            {"scenario": GOAL.format(rate), "from": 1, "runs": 2} for rate in ("0.1", "1", "5")
        ]
        matrix = self.matrix(unbuilt, None, asked)
        self.point(sha(1), minute=1, run_number=1, runs=unbuilt, matrix=matrix)

        first = self.at(sha(1))
        parsed = self.stands_alone(first)
        self.assertEqual(parsed.text.count(LOG), 1)
        each = [
            f"{GOAL.format(rate)}/{number}/build.log"
            for rate in ("0.1", "1", "5")
            for number in (1, 2)
        ]
        # The page shows the first job's log, and each run's page links its own.
        self.assertEqual([link for link in parsed.links if link.endswith("build.log")], each[:1])
        self.assertEqual(parsed.text.count("does not build: seeds 0, 1"), 3)
        self.assertEqual(self.tiles(first), [])
        self.assertNotIn("A tree is one run", parsed.text)
        for log in each:
            run = self.stands_alone(first / log.removesuffix("build.log"))
            self.assertEqual([link for link in run.links if link.endswith(".log")], ["build.log"])
        self.assertNotIn("was not uploaded", parsed.text)
        runs = self.uploaded()
        matrix = self.matrix(runs)
        for number in range(2, 13):
            given = dict(runs=runs, matrix=matrix, commits=self.commits())
            self.point(sha(number), minute=number, run_number=number, **given)

        self.assertEqual(self.files(first), ["index.html", "point.json"])
        parsed = self.stands_alone(first)
        self.assertNotIn(LOG, parsed.text)
        self.assertNotIn("was not uploaded", parsed.text)
        self.assertEqual(parsed.text.count("The site no longer keeps its log."), 1)
        self.assertEqual([link for link in parsed.links if link.endswith("build.log")], [])

    def test_past_the_window_a_point_s_page_is_drawn_again_without_its_runs_text(self):
        runs = self.uploaded(**{"5/0": {"grow": GROW}})
        matrix = self.matrix(runs)
        self.point(sha(1), minute=1, run_number=1, runs=runs, matrix=matrix)
        first = self.at(sha(1))
        self.assertIn(f"{GOAL.format(1)}/1/", Parsed(self.drawn(first)).links)

        for number in range(2, 13):
            given = dict(runs=runs, matrix=matrix, commits=self.commits())
            self.point(sha(number), minute=number, run_number=number, **given)

        self.assertEqual(self.files(first), ["index.html", "point.json"])
        parsed, page = self.stands_alone(first), self.drawn(first)
        self.assertFalse([link for link in parsed.links if link.endswith((".txt", ".json"))])
        self.assertIn(
            "The site keeps the runs' text and pages of the newest 11 points, and no longer this "
            "one's: the commands under its runs make one again.",
            parsed.text,
        )
        self.assertEqual(len(self.cells(self.charts(first)["The point's GroupV2 runs"])), 6)
        # Each run's tree is still there, the one the point keeps the lines of whole, and none
        # links a page.
        tiles = self.tiles(first)
        self.assertEqual([href for href, *_ in tiles], [None] * 6)
        self.assertEqual([title for title, _, _ in self.lines(tiles[-1][3], "head")], GROWN)
        self.assertEqual(self.trees(tiles[0][3]), ["tree head sketch"])
        self.assertNotIn("or open a run for its labels", parsed.text)
        folds = re.findall(r'<details>\n<summary>(.*?) <span class="muted">2 runs</span>', page)
        self.assertEqual(folds, ["GroupV2 0.1%", "GroupV2 1%", "GroupV2 5%"])
        point = self.read(sha(1))
        for cell in point["cells"]:
            again = "\n".join(simulate.again(cell, sha(1), SHA))
            self.assertEqual(parsed.text.count(again), 1 if cell["seed"] == 0 else 0)
        said = "For another seed, give --from the seed plus 1 and name the seed in --out."
        self.assertEqual(parsed.text.count(said), 3)
        for rate in ("0.1", "1", "5"):
            command = (
                "gh run download 42 --repo logos-messaging/chat-sim --dir logs --pattern "
                f"'logs-revision-{GOAL.format(rate)}-[0-9]*'"
            )
            self.assertEqual(parsed.text.count(command), 1)
        self.assertEqual(
            parsed.text.count("a run's group.log under its number, the seed plus 1:"), 3
        )
        self.assertIn("GitHub keeps it 90 days.", parsed.text)
        self.assertIn(f"{GOAL.format(1)}/1/", Parsed(self.drawn(self.at(sha(2)))).links)

    def test_past_the_window_a_point_that_did_not_build_has_no_command_for_its_logs(self):
        unbuilt = self.uploaded()
        for cell in ("0.1/0", "0.1/1", "1/0", "1/1", "5/0", "5/1"):
            self.unbuilt(unbuilt, cell)
        self.point(sha(1), minute=1, run_number=1, runs=unbuilt)
        for number in range(2, 13):
            self.point(sha(number), minute=number, run_number=number, commits=self.commits())

        parsed = self.stands_alone(self.at(sha(1)))
        self.assertNotIn("gh run download", parsed.text)
        self.assertNotIn("GitHub keeps it", parsed.text)
        self.assertEqual(parsed.text.count("Run seed 0 again"), 3)


EXPECTED = ROOT / "tools" / "sim" / "expected"
SCENARIOS = ROOT / "tools" / "sim" / "scenarios"
# The outcome of seed 0 of 50-epochs.json, on master's CI run 42121862497.
FIFTY = {
    "seed": 0,
    "network_seed": None,
    "conversation": "def2b1dd40",
    "members_digest": "1f228bfa64e58b9a",
    "verdict": "whole",
    "at_tip": 51,
    "seated": 51,
    "virtual_s": 3494.75,
    "trunk_epoch": None,
    "first_fork": None,
    "ended": None,
    "lines_off_trunk": None,
    "members_off_trunk": None,
    "never_joined": None,
    "came_to": "as expected",
    "stopped": None,
}
# What compare printed of 50-epochs-by-saro-waku-loss-5's two runs on master's CI run
# 44661545505, cut to its first row.
COMPARED = (
    "run  seed  change     verdict                 at the tip  seconds  came to\n"
    "  1     0  same       split at epoch 8           8 of 15   1961.2  no member took part: "
    "trunk at epoch 14, 3 main, 0 sub, 7 fell behind, 5 behind at the end, 2 never joined\n"
    "2 of 2 runs came out as expected\n"
)
# The row of 50-epochs-by-saro-waku-loss-5 on a long run's page, its runs as tools/sim/expected
# holds them.
LOSS_5 = [
    "50-epochs-by-saro-waku-loss-5",
    "2",
    "split at epoch 8 · split at epoch 3",
    "2 of 2 runs came out as expected",
]


class Long(LogosChat):
    """A run of the long suite at a commit of main, as long keeps it and draws its page."""

    # Pages' helpers, without its tests.
    drawn, folds, nothing = Pages.drawn, Pages.folds, Pages.nothing
    stands_alone = Pages.stands_alone

    def suite(self) -> List[Tuple[str, int]]:
        return simulate.scenarios("long", EXPECTED)

    def long_uploaded(self, unbuilt: bool = False) -> Tuple[Path, Path]:
        """What the run jobs of a long run uploaded, a job a scenario, each run with its run.txt,
        report.txt, run.json and report.json and each sweep its outcomes, those tools/sim/expected
        holds or fifty epochs whole; and what compare wrote of each scenario it holds. Where
        chat-sim did not build, each run's unbuilt marker and the build's log."""
        self.made += 1
        runs, compared = self.scratch / f"long-{self.made}", self.scratch / f"compared-{self.made}"
        compared.mkdir()
        for name, count in self.suite():
            sweep = runs / f"sim-revision-{name}-1" / name
            held = EXPECTED / f"{name}.json"
            outcomes = json.loads(held.read_text("utf-8")) if held.is_file() else [FIFTY] * count
            for number in range(1, count + 1):
                run = sweep / str(number)
                if unbuilt:
                    write(run / "unbuilt", f"chat-sim does not build at {REVISION}\n")
                    write(run / "build.log", LOG)
                    continue
                verdict = outcomes[number - 1]["verdict"]
                write(run / "run.json", json.dumps({"verdict": verdict, "wall_s": 60.5 * number}))
                for text in ("run.txt", "report.txt", "report.json"):
                    write(run / text, f"{text} of {name}/{number}\n")
            if unbuilt:
                continue
            write(sweep / "outcomes.json", json.dumps(outcomes))
            if held.is_file():
                write(compared / name / "compare.txt", COMPARED)
                write(compared / name / "compare.status", "0\n")
        return runs, compared

    def long(
        self,
        revision: str = REVISION,
        minute: int = 0,
        run_number: int = 1,
        uploaded: Optional[Tuple[Path, Path]] = None,
    ) -> str:
        """Writes the long run of a run at `revision`, and returns what the command printed."""
        runs, compared = self.long_uploaded() if uploaded is None else uploaded
        given = self.given(runs, compared, revision, minute, run_number)
        status, out, err = self.run_pages("long", *given)
        self.assertEqual(status, 0, err)
        return out

    def given(
        self, runs: Path, compared: Path, revision: str, minute: int, run_number: int
    ) -> List[str]:
        return [
            str(runs),
            str(self.site),
            "--compared",
            str(compared),
            "--expected",
            str(EXPECTED),
            "--scenarios",
            str(SCENARIOS),
            "--revision",
            revision,
            "--measurement",
            MEASUREMENT,
            *self.ran(run_number, minute),
        ]

    def held(self, revision: str = REVISION) -> Path:
        return self.site / "logos-chat" / "long" / revision / MEASUREMENT

    def test_a_long_run_keeps_what_each_scenario_came_to_its_compare_and_runs_text_alone(self):
        self.long()

        out = self.held()
        texts = [
            f"{name}/{number}/{text}"
            for name, count in self.suite()
            for number in range(1, count + 1)
            for text in ("report.txt", "run.json", "run.txt")
        ]
        compared = [
            f"{name}/compare.txt"
            for name, _ in self.suite()
            if (EXPECTED / f"{name}.json").is_file()
        ]
        self.assertEqual(self.files(out), sorted(["index.html", "long.json", *texts, *compared]))
        long = pages.read_long(out / "long.json")
        self.assertEqual(
            {key: value for key, value in long.items() if key != "scenarios"},
            {
                "logos_chat": REVISION,
                "measurement": MEASUREMENT,
                "chat_sim": SHA,
                "time": time(0),
                "run_url": RUN_URL,
                "run_number": 1,
            },
        )
        names = [name for name, _ in self.suite()]
        self.assertEqual([one["scenario"] for one in long["scenarios"]], names)
        loss = next(one for one in long["scenarios"] if one["scenario"] == LOSS_5[0])
        self.assertEqual(
            loss["compare"],
            {"verdict": pages.AS_EXPECTED, "counted": "2 of 2 runs came out as expected"},
        )
        held = json.loads((EXPECTED / "50-epochs-by-saro-waku-loss-5.json").read_text("utf-8"))
        self.assertEqual(
            loss["runs"],
            [
                {"run": n + 1, "seed": n, "unbuilt": False, "outcome": one}
                for n, one in enumerate(held)
            ],
        )
        self.assertEqual(long["scenarios"][0]["compare"], None)

    def test_the_page_says_what_ran_and_each_scenario_s_verdict_runs_and_command_to_run_again(
        self,
    ):
        runs, compared = self.long_uploaded()
        loss = runs / f"sim-revision-{LOSS_5[0]}-1" / LOSS_5[0]
        grow = dict(GROW, rounds=ROUNDS)
        write(loss / "1" / "run.json", json.dumps({"grow": grow, "wall_s": 189.4}))
        self.long(uploaded=(runs, compared))

        out = self.held()
        parsed = self.stands_alone(out)
        self.assertEqual(parsed.h1, [f"logos-chat main {REVISION[:7]}, the long suite, 2026-11-09"])
        for link in (
            f"{pages.LOGOS_CHAT_URL}/commit/{REVISION}",
            f"{pages.CHAT_SIM_URL}/commit/{SHA}",
            f"{pages.CHAT_SIM_URL}/commit/{MEASUREMENT}",
            RUN_URL,
            "../../../",
        ):
            self.assertIn(link, parsed.links)
        self.assertIn(LOSS_5, parsed.rows)
        self.assertIn(["50-epochs", "1", "whole", "nothing held"], parsed.rows)
        self.assertTrue({name for name, _ in self.suite()} <= parsed.ids)
        self.assertIn(COMPARED, parsed.text)
        self.assertIn("50-epochs-by-saro-waku-loss-5/compare.txt", parsed.links)
        held = json.loads((EXPECTED / "50-epochs-by-saro-waku-loss-5.json").read_text("utf-8"))
        self.assertIn(["0", "split at epoch 8", held[0]["came_to"], "3 min 9 s"], parsed.rows)
        self.assertIn(["1", "split at epoch 3", held[1]["came_to"], "2 min 1 s"], parsed.rows)
        self.assertIn("never joined: tomo, peer14", parsed.text)
        for row in ROUNDED:
            self.assertEqual(parsed.rows.count(row), 1)
        self.assertIn(
            '"As expected" means the runs came out as tools/sim/expected holds them, not that '
            "they came out well.",
            parsed.text,
        )
        self.assertIn("run.txt of 50-epochs-by-saro-waku-loss-5/1\n", parsed.text)
        self.assertIn("50-epochs-by-saro-waku-loss-5/1/run.json", parsed.links)
        self.assertNotIn("50-epochs-by-saro-waku-loss-5/1/report.json", parsed.links)
        for name, count in self.suite():
            for number in range(1, count + 1):
                cell = {"scenario": name, "run": number, "seed": number - 1}
                self.assertIn("\n".join(simulate.again(cell, REVISION, SHA)), parsed.text)
        self.assertEqual(len(self.folds(out)), sum(count for _, count in self.suite()))
        self.assertNotIn("build", parsed.ids)

    def test_a_run_with_no_outcome_has_its_seed_and_the_command_and_links_the_text_it_left(self):
        runs, compared = self.long_uploaded()
        # 50-epochs could not build, 50-epochs-waku's job uploaded nothing, grow-waku-loss-0.1's
        # compare --write wrote no outcomes, and grow-waku-loss-0.1-groupv3's sweep
        # stopped after run 2.
        fifty = runs / "sim-revision-50-epochs-1" / "50-epochs"
        shutil.rmtree(fifty)
        write(fifty / "1" / "unbuilt", f"chat-sim does not build at {REVISION}\n")
        write(fifty / "1" / "build.log", LOG)
        shutil.rmtree(runs / "sim-revision-50-epochs-waku-1")
        lost = runs / "sim-revision-grow-waku-loss-0.1-1" / "grow-waku-loss-0.1"
        (lost / "outcomes.json").unlink()
        shutil.rmtree(compared / "grow-waku-loss-0.1")
        stopped = runs / "sim-revision-grow-waku-loss-0.1-groupv3-1" / "grow-waku-loss-0.1-groupv3"
        shutil.rmtree(stopped / "3")
        two = json.loads((stopped / "outcomes.json").read_text("utf-8"))[:2]
        write(stopped / "outcomes.json", json.dumps(two))
        self.long(uploaded=(runs, compared))

        out = self.held()
        parsed = self.stands_alone(out)
        for name, count in self.suite():
            for number in range(1, count + 1):
                cell = {"scenario": name, "run": number, "seed": number - 1}
                with self.subTest(scenario=name, run=number):
                    again = simulate.again(cell, REVISION, SHA)
                    self.assertIn("\n".join(again), parsed.text)
        self.assertEqual(len(self.folds(out)), sum(count for _, count in self.suite()))
        self.assertIn((False, "seed 0 does not build"), self.folds(out))
        for row in (
            ["50-epochs", "1", "does not build", "-"],
            ["50-epochs-waku", "1", "no outcome", "-"],
            ["grow-waku-loss-0.1", "3", "no outcome", "-"],
            ["0", "no outcome", "-", "1 min"],
            ["2", "no outcome", "-", "-"],
        ):
            self.assertIn(row, parsed.rows)
        for text in ("run.txt", "report.txt", "run.json"):
            self.assertIn(f"grow-waku-loss-0.1/1/{text}", parsed.links)
        self.assertIn("run.txt of grow-waku-loss-0.1/1\n", parsed.text)

    def test_a_scenario_s_own_seed_is_that_of_its_first_run_and_an_outcome_of_another_is_refused(
        self,
    ):
        scenarios = self.scratch / "scenarios"
        shutil.copytree(SCENARIOS, scenarios)
        fifty = json.loads((scenarios / "50-epochs.json").read_text("utf-8"))
        write(scenarios / "50-epochs.json", json.dumps({**fifty, "seed": 7}))
        runs, compared = self.long_uploaded()
        sweep = runs / "sim-revision-50-epochs-1" / "50-epochs"
        write(sweep / "outcomes.json", json.dumps([{**FIFTY, "seed": 7}]))
        given = self.given(runs, compared, REVISION, 0, 1)
        given[given.index("--scenarios") + 1] = str(scenarios)
        status, _, err = self.run_pages("long", *given)
        self.assertEqual(status, 0, err)

        long = pages.read_long(self.held() / "long.json")
        self.assertEqual(long["scenarios"][0]["runs"][0]["seed"], 7)
        cell = {"scenario": "50-epochs", "run": 1, "seed": 7}
        again = simulate.again(cell, REVISION, SHA)
        self.assertIn("\n".join(again), Parsed(self.drawn(self.held())).text)
        write(sweep / "outcomes.json", json.dumps([FIFTY]))
        status, _, err = self.run_pages("long", *given)
        self.assertEqual(status, 2)
        self.assertIn("[0] is of seed 0, and run 1 of seed 7", err)
        write(scenarios / "50-epochs.json", json.dumps({**fifty, "seed": "7"}))
        status, _, err = self.run_pages("long", *given)
        self.assertEqual(status, 2)
        self.assertIn("50-epochs.json: its seed is not a whole number from 0", err)

    def test_every_text_of_a_run_and_of_compare_is_escaped(self):
        runs, compared = self.long_uploaded()
        loss = runs / f"sim-revision-{LOSS_5[0]}-1" / LOSS_5[0]
        for text in ("run.txt", "report.txt"):
            write(loss / "1" / text, f"{MARKUP}\n")
        outcomes = json.loads((loss / "outcomes.json").read_text("utf-8"))
        outcomes[0]["came_to"] = MARKUP
        write(loss / "outcomes.json", json.dumps(outcomes))
        write(compared / "50-epochs-by-saro-waku-loss-5" / "compare.txt", f"{MARKUP}\n{COMPARED}")
        self.long(uploaded=(runs, compared))

        page = self.drawn(self.held())
        self.assertNotIn("<script", page)
        self.assertNotIn("script", Parsed(page).tags)
        # Each text once, and what the run came to in its row and in its fold.
        self.assertEqual(Parsed(page).text.count(MARKUP), 5)

    def test_where_chat_sim_did_not_build_the_build_s_log_is_shown_once_and_each_run_links_its(
        self,
    ):
        self.long(uploaded=self.long_uploaded(unbuilt=True))

        out = self.held()
        parsed = self.stands_alone(out)
        self.assertEqual(parsed.text.count(LOG), 1)
        self.assertIn("build", parsed.ids)
        self.assertIn("Of the job that was to run 50-epochs: no scenario ran.", parsed.text)
        each = [
            f"{name}/{number}/build.log"
            for name, count in self.suite()
            for number in range(1, count + 1)
        ]
        self.assertEqual(self.files(out), sorted(["index.html", "long.json", *each]))
        logs = [link for link in parsed.links if link.endswith("build.log")]
        self.assertEqual(sorted(logs), sorted([each[0], *each]))
        self.assertIn([LOSS_5[0], "2", "does not build", "-"], parsed.rows)
        self.assertNotIn("was not uploaded", parsed.text)

    def test_runs_of_which_none_was_read_built_or_not_and_a_command_line_short_of_one_are_refused(
        self,
    ):
        empty, compared = self.nothing(), self.nothing()
        status, _, err = self.run_pages("long", *self.given(empty, compared, REVISION, 0, 1))
        self.assertEqual(status, 2)
        self.assertIn("RUNS: no run at the revision was read, and none could not be built", err)
        self.assertFalse((self.site / "logos-chat" / "long").exists())
        runs, compared = self.long_uploaded()
        given = self.given(runs, compared, REVISION, 0, 1)
        for flag in ("--compared", "--expected", "--scenarios"):
            with self.subTest(flag=flag):
                changed = list(given)
                changed[changed.index(flag) + 1] = str(self.scratch / "missing")
                status, _, err = self.run_pages("long", *changed)
                self.assertEqual(status, 2)
                self.assertIn(f"{flag}: no such directory", err)
        status, _, err = self.run_pages("long", *given[:-2])
        self.assertEqual(status, 2)
        self.assertIn("--time", err)

    def test_the_long_run_of_a_newer_run_is_kept_against_an_older_one_and_a_re_run_replaces(self):
        self.long(run_number=2, minute=2)
        self.assertEqual(
            self.long(run_number=1, minute=1),
            f"Kept logos-chat/long/{REVISION}/{MEASUREMENT}/: it holds the long run of run 2, "
            "newer than run 1.\n",
        )
        self.assertEqual(pages.read_long(self.held() / "long.json")["run_number"], 2)
        write(self.held() / "stale.txt", "left from before\n")
        self.long(run_number=2, minute=3)
        self.assertNotIn("stale.txt", self.files(self.held()))
        self.assertEqual(pages.read_long(self.held() / "long.json")["time"], time(3))
        self.long(run_number=3, minute=4)
        self.assertEqual(pages.read_long(self.held() / "long.json")["run_number"], 3)

    def test_a_long_json_that_long_does_not_write_is_refused_and_its_run_replaced(self):
        self.long()
        long = json.loads((self.held() / "long.json").read_text("utf-8"))

        def scenario(**changed) -> dict:
            return dict(long, scenarios=[dict(long["scenarios"][2], **changed)])

        def run(**changed) -> dict:
            one = long["scenarios"][2]
            return scenario(runs=[dict(one["runs"][0], **changed), *one["runs"][1:]])

        for refused, said in (
            ({**long, "run_number": "1"}, "run_number"),
            ({**long, "logos_chat": BASE}, "do not name its directory"),
            ({**long, "extra": 1}, "extra"),
            (scenario(scenario="../up"), "is not the name of a scenario"),
            (scenario(compare={"verdict": "fine", "counted": ""}), "is none of compare's"),
            (scenario(runs=[]), "runs is empty"),
            (run(run=2), "is not run 1"),
            (run(seed="0"), "seed is not of its kind"),
            (run(seed=1), "outcome is not of the run's seed"),
            (run(outcome={"seed": 0}), "outcome"),
            (dict(long, scenarios=long["scenarios"][2:3] * 2), "is a second of"),
        ):
            with self.subTest(said=said):
                write(self.held() / "long.json", json.dumps(refused))
                with self.assertRaisesRegex(pages.Unreadable, re.escape(said)):
                    pages.read_long(self.held() / "long.json")
        write(self.held() / "long.json", json.dumps({**long, "run_number": 9, "time": "now"}))
        _, _, err = self.run_pages("trend", str(self.site))
        self.assertIn("long.json: time is not of the form", err)
        self.long(run_number=1)
        self.assertEqual(pages.read_long(self.held() / "long.json")["time"], time(0))

    def test_a_long_json_whose_outcomes_count_no_member_that_never_joined_is_read_as_written(self):
        self.long()
        path = self.held() / "long.json"
        written = json.loads(path.read_text("utf-8"))
        outcomes = [run["outcome"] for one in written["scenarios"] for run in one["runs"]]
        for outcome in outcomes:
            if outcome is not None:
                del reported(outcome)["never_joined"]
        write(path, json.dumps(written))

        long = pages.read_long(path)
        pages.draw_long(self.held(), long)

        self.assertEqual(long, written)
        self.assertIn(LOSS_5, self.stands_alone(self.held()).rows)

    def test_past_the_newest_fifteen_long_runs_each_keeps_long_json_and_a_page_drawn_from_it(self):
        uploaded = self.long_uploaded()
        # The commits run from high to low and the times from late to early as the run numbers
        # rise, so that neither the order of the directories nor that of time is the runs'.
        printed = [
            self.long(sha(100 - number), minute=100 - number, run_number=number, uploaded=uploaded)
            for number in range(1, 18)
        ]
        self.assertEqual(printed[:15], [""] * 15)
        self.assertEqual(
            printed[15:],
            [
                f"Removed the text of logos-chat/long/{sha(100 - number)}/{MEASUREMENT}/: past "
                "the newest 15 long runs.\n"
                for number in (1, 2)
            ],
        )
        for number in (1, 2):
            out = self.held(sha(100 - number))
            held = sorted(path.name for path in out.iterdir())
            self.assertEqual(held, ["index.html", "long.json"])
            parsed = self.stands_alone(out)
            self.assertFalse([link for link in parsed.links if link.endswith((".txt", ".json"))])
            self.assertIn("The site keeps the text of the newest 15 long runs", parsed.text)
            self.assertNotIn(COMPARED, parsed.text)
            self.assertIn(LOSS_5, parsed.rows)
            cell = {"scenario": "50-epochs-by-saro-waku-loss-5", "run": 2, "seed": 1}
            self.assertIn("\n".join(simulate.again(cell, sha(100 - number), SHA)), parsed.text)
        self.assertIn("50-epochs-by-saro-waku-loss-5/compare.txt", self.files(self.held(sha(97))))

    def test_the_graph_over_main_links_the_newest_long_run_and_draws_nothing_of_it(self):
        self.point(sha(1), minute=1, run_number=1)
        self.assertEqual(self.run_pages("trend", str(self.site))[0], 0)
        alone = (self.site / "logos-chat" / "index.html").read_text("utf-8")
        self.assertNotIn("long/", alone)
        self.long(sha(3), minute=3, run_number=3)
        self.long(sha(2), minute=4, run_number=2)
        self.assertEqual(self.run_pages("trend", str(self.site))[0], 0)

        page = (self.site / "logos-chat" / "index.html").read_text("utf-8")
        links = [link for link in Parsed(page).links if link.startswith("long/")]
        self.assertEqual(links, [f"long/{sha(3)}/{MEASUREMENT}/"])
        said = (
            f"The newest long run, at main {sha(3)[:7]} on 2026-11-09 00:03 UTC: the two scenarios "
            "of fifty epochs and each scenario chat-sim holds to what is expected but the goal's"
        )
        self.assertIn(said, Parsed(page).text)
        self.assertEqual(page.count("<svg"), alone.count("<svg"))
        self.assertTrue((self.site / "logos-chat" / links[0] / "index.html").is_file())


class Closed(LogosChat):
    """The runs of logos-chat's pull requests that closed, as closed names them."""

    def held(self, *numbers: int) -> None:
        for number in numbers:
            write(self.site / "logos-chat" / f"pr-{number}" / "meta.json", "{}\n")

    def opened(self, text: str) -> Path:
        self.made += 1
        listed = self.scratch / f"open-{self.made}.txt"
        write(listed, text)
        return listed

    def closed(self, *argv: str) -> Tuple[int, str, str]:
        return self.run_pages("closed", str(self.site), *argv)

    def test_each_run_whose_pull_request_the_open_list_leaves_out_is_named_lowest_first(self):
        self.held(316, 4, 321, 300)
        (self.site / "logos-chat" / "pr-x").mkdir()
        write(self.site / "logos-chat" / "pr-7", "not a directory\n")
        status, out, _ = self.closed("--open", str(self.opened("321\n316\n")))
        self.assertEqual((status, out), (0, "logos-chat/pr-4\nlogos-chat/pr-300\n"))

    def test_with_no_pull_request_open_every_run_is_named(self):
        self.held(4, 300)
        status, out, _ = self.closed("--open", str(self.opened("")))
        self.assertEqual((status, out), (0, "logos-chat/pr-4\nlogos-chat/pr-300\n"))

    def test_a_pr_names_that_pull_request_s_run_alone_where_the_site_holds_it(self):
        self.held(4, 300)
        self.assertEqual(self.closed("--pr=300")[:2], (0, "logos-chat/pr-300\n"))
        self.assertEqual(self.closed("--pr=999")[:2], (0, ""))

    def test_a_site_without_a_run_of_a_pull_request_names_none(self):
        self.assertEqual(self.closed("--open", str(self.opened("316\n")))[:2], (0, ""))

    def test_a_pr_or_a_line_that_is_not_the_number_of_a_pull_request_is_refused(self):
        self.held(4, 300)
        for value in ("", "0", "05", "-3", "3 ", "1e3", "\u0663", "300;rm -rf ."):
            with self.subTest(pr=value):
                status, out, err = self.closed(f"--pr={value}")
                self.assertEqual((status, out), (2, ""))
                self.assertIn("--pr: ", err.splitlines()[-1])
        for text in ("316\nabc\n", "316\n\n321\n", " 316\n", "\u0663\n"):
            with self.subTest(text=text):
                status, out, err = self.closed("--open", str(self.opened(text)))
                self.assertEqual((status, out), (2, ""))
                self.assertIn("--open: ", err.splitlines()[-1])
        self.assertEqual(self.closed()[0], 2)
        self.assertEqual(self.closed("--pr=4", "--open", str(self.opened("")))[0], 2)
        held = self.files(self.site / "logos-chat")
        self.assertEqual(held, ["pr-300/meta.json", "pr-4/meta.json"])


if __name__ == "__main__":
    unittest.main()
