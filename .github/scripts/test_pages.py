import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import pages

SHA = "0123456789abcdef0123456789abcdef01234567"
RUN_URL = "https://github.com/logos-messaging/chat-sim/actions/runs/42"
COMMIT_URL = f"https://github.com/logos-messaging/chat-sim/commit/{SHA}"
# A logos-chat commit, the base it is compared with, and the chat-sim commit a run measures.
REVISION = "89abcdef0123456789abcdef0123456789abcdef"
BASE = "fedcba9876543210fedcba9876543210fedcba98"
MEASUREMENT = "76543210fedcba9876543210fedcba9876543210"
ROOT = Path(__file__).resolve().parents[2]
# What the jobs of the pr suite uploaded at a revision, seeds 0 and 1 of 0.1, 1 and 5% loss, each
# job's artifact in a directory of its own, cut to what tools/sim matrix reads.
UPLOADED = ROOT / "tools" / "sim" / "tests" / "fixtures" / "matrix"
GOAL = "50-epochs-by-saro-waku-loss-{}"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
        """Uploads a sweep of `runs` runs, whose compare exited `status`, or left none."""
        out = self.artifacts / name
        write(out / "sweep.txt", f"{runs} of {runs} runs went as expected\n")
        write(out / "compare.txt", f"compare of {name}\n")
        if status is not None:
            write(out / "compare.status", f"{status}\n")
        for run in range(1, runs + 1):
            write(out / str(run) / "run.txt", f"run {run} of {name}\n{text}")
            write(out / str(run) / "report.txt", "conversation 2a30d6a2c1\n")

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
        self.assertEqual(len(files), 2 + 3 * 2 + 2 + 1)
        for link in files:
            self.assertEqual((out / link).read_bytes(), (self.artifacts / link).read_bytes())
        anchors = {link[1:] for link in parsed.links if link.startswith("#")}
        self.assertEqual(anchors, {"held", "fifty-epochs", "grow-a", "grow-b"})
        self.assertLessEqual(anchors, parsed.ids)
        self.assertIn(f"{second}\nrun.txt was not uploaded", parsed.text)

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

    def test_the_landing_page_holds_two_links_the_guide_and_the_reports(self):
        self.index()
        parsed = Parsed(self.page("index.html"))
        self.assertEqual(parsed.links, ["guide/", "reports/"])
        self.assertIn("Guide", parsed.text)
        self.assertIn("Reports", parsed.text)
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
    """The `number`th of a run of made-up commits."""
    return f"{number:040x}"


def time(minute: int) -> str:
    return f"2026-11-09T{minute // 60:02d}:{minute % 60:02d}:00Z"


class LogosChat(unittest.TestCase):
    """A site that starts empty, and copies of what the run jobs of simulate.yml uploaded."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.site = self.scratch / "site"
        self.site.mkdir()
        self.made = 0

    def uploaded(self, **edits: dict) -> Path:
        """A copy of UPLOADED, each run with its run.txt, report.txt and report.json beside its
        run.json, and the fields `edits` gives a run changed, by its rate and seed."""
        self.made += 1
        copy = self.scratch / f"uploaded-{self.made}"
        shutil.copytree(UPLOADED, copy)
        for report in copy.rglob("run.json"):
            for name in ("run.txt", "report.txt", "report.json"):
                write(report.parent / name, f"{name} of {report.parent.relative_to(copy)}\n")
        for cell, changed in edits.items():
            report = json.loads(self.run_json(copy, cell).read_text(encoding="utf-8"))
            report.update(changed)
            self.run_json(copy, cell).write_text(json.dumps(report), encoding="utf-8")
        return copy

    def run_json(self, side: Path, cell: str) -> Path:
        rate, seed = cell.split("/")
        scenario, number = GOAL.format(rate), int(seed) + 1
        return side / f"sim-revision-{scenario}-{number}" / scenario / str(number) / "run.json"

    def matrix(self, head: Path, base: Optional[Path] = None, asked: Sequence[dict] = ()) -> Path:
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

    def ran(self, run_number: int, minute: int) -> List[str]:
        return [
            "--chat-sim",
            SHA,
            "--run-url",
            RUN_URL,
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
    ) -> str:
        """Writes the point of a run at `revision`, and returns what the command printed."""
        runs = self.uploaded() if runs is None else runs
        matrix = self.matrix(runs) if matrix is None else matrix
        given = [str(matrix), str(runs), str(self.site), "--revision", revision]
        given += ["--measurement", measurement, *self.ran(run_number, minute)]
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
            + ["merged", "previous", "cells", "summary"],
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
            for name in ("report.txt", "run.json", "run.txt")
        ]
        self.assertEqual(self.files(self.at()), sorted(["point.json", *texts]))
        for name in ("run.txt", "report.txt", "run.json"):
            self.assertEqual(
                (self.at() / GOAL.format(5) / "2" / name).read_bytes(),
                (runs / GOAL.format(5) / "02" / name).read_bytes(),
            )
        self.assertEqual(list(self.site.rglob("*.html")), [])

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
        moved = self.uploaded(**{"1/1": {"verdict": "split at epoch 5"}})
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
        self.assertEqual(self.files(self.at()), ["point.json"])
        self.assertEqual([cell["unbuilt"]["head"] for cell in self.read()["cells"]], [True] * 6)

    def test_the_point_of_a_newer_run_is_kept_against_an_older_one(self):
        self.point(run_number=8)
        written = self.read()
        moved = self.uploaded(**{"1/1": {"verdict": "split at epoch 5"}})
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
        moved = self.uploaded(**{"1/1": {"verdict": "split at epoch 5"}})
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
    def test_past_the_newest_thirty_points_by_run_number_each_loses_its_runs_text_alone(self):
        runs = self.uploaded()
        matrix = self.matrix(runs)
        # The commits run from high to low and the times from late to early as the run numbers
        # rise, so that neither the order of the directories nor that of time is the runs'.
        printed = []
        for number in range(1, 33):
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
        self.assertEqual(printed[:30], [""] * 30)
        self.assertEqual(
            printed[30:],
            [
                f"Removed the runs' text of logos-chat/main/{sha(100 - number)}/{MEASUREMENT}/: "
                f"past the newest 30 points.\n"
                for number in (1, 2)
            ],
        )
        for number in range(1, 33):
            with self.subTest(number=number):
                point = self.at(sha(100 - number))
                held = self.files(point)
                if number == 1:
                    self.assertEqual(sorted(path.name for path in point.iterdir()), held)
                    self.assertEqual(held, ["index.html", "point.json"])
                else:
                    self.assertEqual(len(held), 1 if number == 2 else 19)
                self.assertIn("point.json", held)
                pages.read_point(point / "point.json")

    def test_a_point_json_that_is_refused_is_left_alone(self):
        refused = self.site / "logos-chat" / "main" / sha(200) / MEASUREMENT
        write(refused / "point.json", "{}\n")
        write(refused / GOAL.format(1) / "1" / "run.txt", "a run\n")
        runs = self.uploaded()
        matrix = self.matrix(runs)
        for minute in range(31):
            self.point(
                sha(minute),
                minute=minute,
                run_number=minute + 1,
                runs=runs,
                matrix=matrix,
                commits=None if minute == 0 else self.commits(),
            )
        self.assertEqual(self.files(refused), [f"{GOAL.format(1)}/1/run.txt", "point.json"])
        self.assertEqual(len(self.files(self.at(sha(0)))), 1)


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
        if base is not None:
            given += ["--base", BASE, "--base-runs", str(base)]
        status, out, err = self.run_pages("pr", *given, *self.ran(run_number, minute))
        self.assertEqual(status, 0, err)
        return out

    def meta(self) -> dict:
        return json.loads((self.site / "logos-chat" / "pr-316" / "meta.json").read_text("utf-8"))

    def test_a_pull_request_s_run_holds_its_matrix_both_sides_text_and_its_meta(self):
        head = self.uploaded(**{"1/1": {"verdict": "split at epoch 5"}})
        self.pr(head=head, base=self.uploaded(), run_number=12, minute=581)
        out = self.site / "logos-chat" / "pr-316"
        texts = [
            f"{side}/{GOAL.format(rate)}/{run}/{name}"
            for side in ("base", "head")
            for rate in ("0.1", "1", "5")
            for run in (1, 2)
            for name in ("report.txt", "run.json", "run.txt")
        ]
        self.assertEqual(self.files(out), sorted(["matrix.json", "meta.json", *texts]))
        self.assertEqual(
            (out / "head" / GOAL.format(1) / "2" / "run.json").read_bytes(),
            self.run_json(head, "1/1").read_bytes(),
        )
        matrix = pages.read_matrix(out / "matrix.json")
        self.assertEqual(matrix["verdict"], "hint")
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
        self.assertEqual(list(self.site.rglob("*.html")), [])

    def test_a_run_without_a_base_has_no_base_side(self):
        self.pr(base=None)
        out = self.site / "logos-chat" / "pr-316"
        self.assertFalse((out / "base").exists())
        self.assertIsNone(self.meta()["base"])
        self.assertEqual(pages.read_matrix(out / "matrix.json")["verdict"], "no-base")

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
        given += self.ran(1, 1)
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


if __name__ == "__main__":
    unittest.main()
