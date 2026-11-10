import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional, Sequence

import measure

TIP = "0123456789abcdef0123456789abcdef01234567"
BEFORE = "89abcdef0123456789abcdef0123456789abcdef"
MEASUREMENT = "fedcba9876543210fedcba9876543210fedcba98"
OLDER = "76543210fedcba9876543210fedcba9876543210"
ROOT = Path(__file__).resolve().parents[2]


def commit(repository: str, sha: str) -> str:
    return f"[`{sha[:7]}`](https://github.com/logos-messaging/{repository}/commit/{sha})"


AT = f"logos-chat main's tip {commit('logos-chat', TIP)}"
MEASURED = f"chat-sim {commit('chat-sim', MEASUREMENT)}"


def files(revision: str, measurement: str) -> List[str]:
    """What a point of `revision` at `measurement` leaves under logos-chat/main/ on gh-pages."""
    directory = f"logos-chat/main/{revision}/{measurement}"
    return [
        f"{directory}/index.html",
        f"{directory}/point.json",
        f"{directory}/50-epochs-by-saro-waku-loss-1/01/run.txt",
    ]


def run(*argv: str) -> "tuple[int, str, str]":
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = measure.main(argv)
        except SystemExit as exit:
            status = exit.code
    return status, out.getvalue(), err.getvalue()


class Handed(unittest.TestCase):
    """What the decision is handed, as files in a scratch directory."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.summary = self.root / "summary.md"
        self.summary.write_text("before\n", encoding="utf-8")

    def decide(
        self,
        points: Sequence[str] = (),
        going: Sequence[str] = (),
        tip: str = TIP,
        measurement: str = MEASUREMENT,
        ended: Sequence[dict] = (),
        tree: Path = ROOT,
    ) -> "tuple[int, str, str]":
        for name, lines in (("points", points), ("going", going)):
            (self.root / name).write_text("".join(f"{line}\n" for line in lines), "utf-8")
        (self.root / "ended.json").write_text(json.dumps(list(ended)), "utf-8")
        return run(
            "--tip",
            tip,
            "--measurement",
            measurement,
            "--points",
            str(self.root / "points"),
            "--going",
            str(self.root / "going"),
            "--ended",
            str(self.root / "ended.json"),
            "--tree",
            str(tree),
            "--summary",
            str(self.summary),
        )

    def said(self) -> str:
        """What was appended to the summary."""
        return self.summary.read_text(encoding="utf-8").removeprefix("before\n")


class Decide(Handed):
    """The decision on the paths under logos-chat/main/ on gh-pages and the Simulate runs going."""

    def test_a_tip_with_no_point_is_dispatched_and_the_summary_says_why(self):
        status, out, _ = self.decide(files(BEFORE, MEASUREMENT), [f"Simulate main at {BEFORE}"])

        self.assertEqual((status, out), (0, f"dispatch=true\nrevision={TIP}\n"))
        self.assertEqual(
            self.said(),
            f"{AT} has no point at {MEASURED} or at any other: the main suite is to run at it.\n",
        )

    def test_a_tip_with_its_point_at_this_measurement_is_never_dispatched(self):
        for going in ((), [f"Simulate main at {TIP}"]):
            with self.subTest(going=going):
                self.summary.write_text("before\n", encoding="utf-8")
                status, out, _ = self.decide(files(TIP, MEASUREMENT), going)

                self.assertEqual((status, out), (0, f"dispatch=false\nrevision={TIP}\n"))
                self.assertEqual(
                    self.said(), f"{AT} has its point at {MEASURED}: nothing is dispatched.\n"
                )

    def test_a_tip_with_a_run_at_it_of_any_suite_queued_or_in_progress_is_not_dispatched(self):
        for title in (
            f"Simulate main at {TIP}",
            f"Simulate long at {TIP}",
            f"Simulate pr at {TIP}",
        ):
            with self.subTest(title=title):
                self.summary.write_text("before\n", encoding="utf-8")
                _, out, _ = self.decide(files(BEFORE, MEASUREMENT), ["Simulate", title])

                self.assertEqual(out, f"dispatch=false\nrevision={TIP}\n")
                self.assertEqual(
                    self.said(),
                    f"{AT} has no point at {MEASURED}, and a run at it is queued or in progress: "
                    "nothing is dispatched.\n",
                )

    def test_a_run_at_the_tip_for_a_pull_request_does_not_hold_it_back(self):
        # It writes no point, and simulate.yml's concurrency group is the pull request's: a
        # dispatch at the tip cancels nothing.
        for title in (
            f"Simulate main at {TIP} for logos-chat#316",
            f"Simulate pr at {TIP} for logos-chat#316",
        ):
            with self.subTest(title=title):
                _, out, _ = self.decide(files(BEFORE, MEASUREMENT), [title])

                self.assertEqual(out, f"dispatch=true\nrevision={TIP}\n")

    def test_a_run_at_another_revision_does_not_hold_the_tip_back(self):
        going = [
            f"Simulate main at {BEFORE}",
            f"Simulate main at {TIP[:7]}",
            f"Simulate long at {BEFORE}",
            f"Simulate pr at {BEFORE} for logos-chat#316",
            "Simulate",
        ]
        _, out, _ = self.decide(going=going)

        self.assertEqual(out, f"dispatch=true\nrevision={TIP}\n")

    def test_a_tip_measured_at_an_older_measurement_only_is_dispatched_again(self):
        status, out, _ = self.decide(files(TIP, OLDER) + files(BEFORE, MEASUREMENT))

        self.assertEqual((status, out), (0, f"dispatch=true\nrevision={TIP}\n"))
        self.assertEqual(
            self.said(),
            f"{AT} has a point at chat-sim {commit('chat-sim', OLDER)} only, and runs "
            f"measure {MEASURED} now: the main suite is to run at it again.\n",
        )

    def test_a_directory_of_the_tip_without_its_point_json_holds_no_point(self):
        written = [path for path in files(TIP, MEASUREMENT) if not path.endswith("/point.json")]
        _, out, _ = self.decide(written)

        self.assertEqual(out, f"dispatch=true\nrevision={TIP}\n")
        self.assertIn(" or at any other: ", self.said())

    def test_a_gh_pages_with_no_logos_chat_has_no_points(self):
        _, out, _ = self.decide()

        self.assertEqual(out, f"dispatch=true\nrevision={TIP}\n")

    def test_refuses_what_is_not_a_full_commit_and_writes_nothing(self):
        for given, said in (
            ({"tip": TIP[:7]}, "--tip"),
            ({"tip": TIP.upper()}, "--tip"),
            ({"tip": ""}, "--tip"),
            ({"tip": f"{TIP}\nrevision={BEFORE}"}, "--tip"),
            ({"measurement": MEASUREMENT[:7]}, "--measurement"),
        ):
            with self.subTest(given=given):
                status, out, err = self.decide(files(TIP, MEASUREMENT), **given)

                self.assertEqual((status, out), (2, ""))
                self.assertIn(f"{said}: ", err)
                self.assertIn("is not a commit's 40 hex digits", err)
                self.assertEqual(self.said(), "")

    def test_refuses_a_list_it_cannot_read(self):
        missing = str(self.root / "missing")
        status, out, err = run(
            "--tip",
            TIP,
            "--measurement",
            MEASUREMENT,
            "--points",
            missing,
            "--going",
            missing,
            "--ended",
            missing,
            "--summary",
            str(self.summary),
        )

        self.assertEqual((status, out), (2, ""))
        self.assertIn(f"--points: {missing}: ", err)
        self.assertEqual(self.said(), "")

    def test_refuses_runs_that_ended_it_cannot_read(self):
        for ended in ({"displayTitle": "Simulate"}, [{"displayTitle": "Simulate", "url": "u"}]):
            with self.subTest(ended=ended):
                (self.root / "ended.json").write_text(json.dumps(ended), "utf-8")
                status, out, err = run(
                    "--tip",
                    TIP,
                    "--measurement",
                    MEASUREMENT,
                    "--points",
                    str(self.summary),
                    "--going",
                    str(self.summary),
                    "--ended",
                    str(self.root / "ended.json"),
                    "--summary",
                    str(self.summary),
                )

                self.assertEqual((status, out), (2, ""))
                self.assertIn("--ended: ", err)
                self.assertIn("not a list of runs, each its displayTitle, headSha, url", err)


class Ended(Handed):
    """A chat-sim of two commits, the second the measurement, and Simulate runs that ended."""

    URL = "https://github.com/logos-messaging/chat-sim/actions/runs/7"

    def setUp(self):
        super().setUp()
        self.tree = self.root / "chat-sim"
        self.git("init", "-q", str(self.tree), tree=self.root)
        self.before, self.measured = self.change("tools/sim/sim/a"), self.change("crates/b")

    def git(self, *argv: str, tree: Optional[Path] = None) -> str:
        unsigned = ("-c", "commit.gpgsign=false", "-c", "user.name=Saro", "-c", "user.email=s@o")
        return subprocess.run(
            ["git", "-C", str(tree or self.tree), *unsigned, *argv],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def change(self, path: str) -> str:
        file = self.tree / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(path, encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-qm", path)
        return self.git("rev-parse", "HEAD")

    def ended(self, title: str, at: str) -> "tuple[int, str, str]":
        ran = [{"displayTitle": title, "headSha": at, "url": self.URL}]
        return self.decide(measurement=self.measured, ended=ran, tree=self.tree)

    def test_a_run_of_main_at_the_tip_that_ended_at_this_measurement_holds_it_back(self):
        later = self.change("guide/c")
        for at in (self.measured, later):
            with self.subTest(at=at):
                self.summary.write_text("before\n", encoding="utf-8")
                status, out, _ = self.ended(f"Simulate main at {TIP}", at)

                self.assertEqual((status, out), (0, f"dispatch=false\nrevision={TIP}\n"))
                self.assertEqual(
                    self.said(),
                    f"{AT} has no point at chat-sim {commit('chat-sim', self.measured)}, though "
                    f"its main suite ran at it at that measurement and ended: {self.URL}. Nothing "
                    "is dispatched until a person dispatches it again.\n",
                )

    def test_a_run_that_ended_before_the_measurement_or_off_its_history_does_not(self):
        for at in (self.before, "0" * 40):
            with self.subTest(at=at):
                _, out, _ = self.ended(f"Simulate main at {TIP}", at)

                self.assertEqual(out, f"dispatch=true\nrevision={TIP}\n")

    def test_a_run_of_main_at_the_tip_for_a_pull_request_that_ended_does_not(self):
        # It wrote no point, so it would hold the tip back for good.
        _, out, _ = self.ended(f"Simulate main at {TIP} for logos-chat#316", self.measured)

        self.assertEqual(out, f"dispatch=true\nrevision={TIP}\n")

    def test_a_run_of_another_suite_or_revision_that_ended_does_not(self):
        for title in (
            f"Simulate pr at {TIP}",
            f"Simulate long at {TIP}",
            f"Simulate main at {BEFORE}",
        ):
            with self.subTest(title=title):
                _, out, _ = self.ended(title, self.measured)

                self.assertEqual(out, f"dispatch=true\nrevision={TIP}\n")


class Title(unittest.TestCase):
    def test_is_the_one_simulate_yml_gives_a_run_with_or_without_a_pull_request(self):
        workflow = (ROOT / ".github" / "workflows" / "simulate.yml").read_text(encoding="utf-8")
        run_name = next(
            line.removeprefix("run-name: ")
            for line in workflow.splitlines()
            if line.startswith("run-name: ")
        )

        self.assertEqual(
            run_name,
            measure.TITLE.format(suite="${{ inputs.suite }}", revision="${{ inputs.revision }}")
            + "${{ inputs.pr != '' && format(' for logos-chat#{0}', inputs.pr) || '' }}",
        )


class Publish(unittest.TestCase):
    def test_simulate_yml_keeps_a_point_and_links_its_page_for_a_main_run_handed_no_pr_alone(self):
        workflow = (ROOT / ".github" / "workflows" / "simulate.yml").read_text(encoding="utf-8")
        publish = workflow.split("\n  publish:\n", 1)[1].split("\n    runs-on: ", 1)[0]
        report = workflow.split("\n      - name: Read the report page's address\n", 1)[1]
        report = report.split("\n        continue-on-error: ", 1)[0]

        main = "needs.plan.outputs.suite == 'main'"
        for condition in (publish, report):
            self.assertEqual(condition.count(main), 1)
            self.assertIn(f"{main} && needs.plan.outputs.pr == ''", condition)


class Branch(unittest.TestCase):
    def test_measure_main_yml_lists_the_runs_of_the_default_branch_alone_that_ended(self):
        workflow = (ROOT / ".github" / "workflows" / "measure-main.yml").read_text(encoding="utf-8")
        step = workflow.split("\n      - name: List the Simulate runs that ended\n", 1)[1]
        step = step.split("\n      - name: ", 1)[0]

        # A run of another branch keeps no point: ended, it would hold the tip back for good.
        self.assertIn("BRANCH: ${{ github.event.repository.default_branch }}", step)
        self.assertIn('--branch "$BRANCH"', step)


if __name__ == "__main__":
    unittest.main()
