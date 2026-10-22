import contextlib
import io
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Tuple

import pages

SHA = "0123456789abcdef0123456789abcdef01234567"
RUN_URL = "https://github.com/logos-messaging/chat-sim/actions/runs/42"
COMMIT_URL = f"https://github.com/logos-messaging/chat-sim/commit/{SHA}"


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


if __name__ == "__main__":
    unittest.main()
