import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional, Sequence

import pages
import simulate

REVISION = "0123456789abcdef0123456789abcdef01234567"
BASE = "89abcdef0123456789abcdef0123456789abcdef"
CHAT_SIM = "fedcba9876543210fedcba9876543210fedcba98"
RUN_URL = "https://github.com/logos-messaging/chat-sim/actions/runs/42"
REPORT_URL = "https://logos-messaging.github.io/chat-sim/logos-chat/pr-316/"
ROOT = Path(__file__).resolve().parents[2]
# What the jobs of the pr suite uploaded at a revision, seeds 0 and 1 of its three rates, cut to
# what tools/sim matrix reads: 0.1% whole and split at epoch 46, 1% split at 18 and 9, 5% split
# at 8 and 3.
UPLOADED = ROOT / "tools" / "sim" / "tests" / "fixtures" / "matrix"
# Main's point at logos-chat fa5282a3, the probe's twenty seeds of each rate, cut to what
# tools/sim matrix reads: its seeds 0 and 1 of the lossy rates are UPLOADED's runs.
POINT = ROOT / "tools" / "sim" / "tests" / "fixtures" / "point.json"
FA5282A = "fa5282a34405b6975424174858d85e4f261d420a"
POINT_RUN = "https://github.com/logos-messaging/chat-sim/actions/runs/42460360938"
# The chat-sim commit a run measures, and another.
MEASUREMENT = "76543210fedcba9876543210fedcba9876543210"
EARLIER = "00112233445566778899aabbccddeeff00112233"


def outcome(seed: int, verdict: Optional[str] = "whole") -> dict:
    return {
        "seed": seed,
        "network_seed": seed + 1,
        "verdict": verdict,
        "at_tip": 26,
        "seated": 26,
        "virtual_s": 1295.059,
        "came_to": "1 main branch",
        "stopped": None,
    }


def write(path: Path, outcomes: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(outcomes), encoding="utf-8")


def run(*argv: str) -> "tuple[int, str, str]":
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = simulate.main(argv)
        except SystemExit as exit:
            status = exit.code
    return status, out.getvalue(), err.getvalue()


def commit(sha: str) -> str:
    return f"[`{sha[:7]}`](https://github.com/logos-messaging/logos-chat/commit/{sha})"


def point(sha: str, measurement: str = MEASUREMENT) -> str:
    """Where on gh-pages main's point at `sha` and `measurement` is."""
    return f"logos-chat/main/{sha}/{measurement}/point.json"


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.expected = self.root / "expected"
        held = (
            "50-epochs-by-saro-waku-loss-1",
            "grow-waku-loss-0.5",
            "grow-waku-loss-1",
            "grow-waku-loss-5",
            "grow-waku-size-50",
        )
        for name in held:
            write(self.expected / f"{name}.json", [outcome(0), outcome(1), outcome(2)])


class Scenarios(Fixture):
    def test_pr_is_two_seeds_of_the_goal_at_each_of_its_three_lossy_rates(self):
        self.assertEqual(
            simulate.scenarios("pr", self.expected),
            [
                ("50-epochs-by-saro-waku-loss-0.1", 2),
                ("50-epochs-by-saro-waku-loss-1", 2),
                ("50-epochs-by-saro-waku-loss-5", 2),
            ],
        )

    def test_main_is_twenty_seeds_of_the_goal_at_each_of_its_four_rates(self):
        self.assertEqual(
            simulate.scenarios("main", self.expected),
            [
                ("50-epochs-by-saro-waku-loss-0", 20),
                ("50-epochs-by-saro-waku-loss-0.1", 20),
                ("50-epochs-by-saro-waku-loss-1", 20),
                ("50-epochs-by-saro-waku-loss-5", 20),
            ],
        )

    def test_long_is_fifty_epochs_and_every_held_scenario_but_the_goals_as_held(self):
        self.assertEqual(
            simulate.scenarios("long", self.expected),
            [
                ("50-epochs", 1),
                ("50-epochs-waku", 1),
                ("grow-waku-loss-0.5", 3),
                ("grow-waku-loss-1", 3),
                ("grow-waku-loss-5", 3),
                ("grow-waku-size-50", 3),
            ],
        )

    def test_the_goals_files_set_no_seed_so_that_seed_k_is_run_k_plus_1(self):
        for rate in simulate.RATES["main"]:
            name = simulate.GOAL.format(rate)
            held = json.loads((ROOT / "tools/sim/scenarios" / f"{name}.json").read_text("utf-8"))
            self.assertNotIn("seed", held, name)


class Jobs(unittest.TestCase):
    def test_pr_is_a_job_a_run_at_the_revision_and_at_the_base(self):
        jobs = simulate.jobs("pr", REVISION, BASE, simulate.scenarios("pr", Path()))

        self.assertEqual(len(jobs), 12)
        self.assertEqual(
            {(job["side"], job["revision"]) for job in jobs[:6]}, {("revision", REVISION)}
        )
        self.assertEqual({(job["side"], job["revision"]) for job in jobs[6:]}, {("base", BASE)})
        self.assertEqual(
            [(job["scenario"], job["from"], job["runs"]) for job in jobs[:6]],
            [
                (simulate.GOAL.format(rate), first, 1)
                for rate in ("0.1", "1", "5")
                for first in (1, 2)
            ],
        )
        self.assertEqual(
            [job["name"] for job in jobs[:6]],
            [f"head 0123456: {rate}% seed {seed}" for rate in ("0.1", "1", "5") for seed in (0, 1)],
        )
        self.assertEqual(jobs[10]["name"], "base 89abcde: 5% seed 0")

    def test_main_is_two_jobs_of_ten_runs_a_rate_and_no_base(self):
        jobs = simulate.jobs("main", REVISION, "", simulate.scenarios("main", Path()))

        self.assertEqual(len(jobs), 8)
        self.assertEqual({job["side"] for job in jobs}, {"revision"})
        self.assertEqual(
            [(job["scenario"], job["from"], job["runs"]) for job in jobs],
            [
                (simulate.GOAL.format(rate), first, 10)
                for rate in ("0", "0.1", "1", "5")
                for first in (1, 11)
            ],
        )
        self.assertEqual(
            [job["name"] for job in jobs[:2]],
            ["head 0123456: 0% seeds 0 to 9", "head 0123456: 0% seeds 10 to 19"],
        )

    def test_long_is_a_job_a_scenario_a_side_that_does_its_runs_as_held(self):
        swept = [("50-epochs", 1), ("grow-waku-loss-1", 3)]
        jobs = simulate.jobs("long", REVISION, BASE, swept)

        self.assertEqual(
            [(job["side"], job["scenario"], job["from"], job["runs"]) for job in jobs],
            [
                ("revision", "50-epochs", 1, 1),
                ("revision", "grow-waku-loss-1", 1, 3),
                ("base", "50-epochs", 1, 1),
                ("base", "grow-waku-loss-1", 1, 3),
            ],
        )
        self.assertEqual(jobs[1]["name"], "head 0123456: grow-waku-loss-1")
        self.assertEqual(jobs[2]["name"], "base 89abcde: 50-epochs")


class GhPages(Fixture):
    """A checkout, `pages`, whose branch gh-pages holds main's points as pages.py point writes
    them."""

    def setUp(self):
        super().setUp()
        self.pages = self.root / "pages"

    def published(self, runs: Path = UPLOADED, revision: str = BASE) -> List[str]:
        """What gh-pages lists under logos-chat/main/ once it holds main's point at `revision`
        and MEASUREMENT besides what it held, as pages.py point writes it of the runs under
        `runs`, a cell for each run main does."""
        asked, matrix = self.root / "asked.json", self.root / "matrix.json"
        write(asked, simulate.jobs("main", revision, "", simulate.scenarios("main", self.expected)))
        command = [sys.executable, str(ROOT / "tools" / "sim"), "matrix", str(runs)]
        subprocess.run(command + ["--asked", str(asked), "--out", str(matrix)], check=True)
        given = [str(matrix), str(runs), str(self.pages), "--revision", revision]
        given += ["--measurement", MEASUREMENT, "--chat-sim", CHAT_SIM, "--run-url", RUN_URL]
        given += ["--run-number", "1", "--time", "2026-11-09T00:17:00Z"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pages.main(["point", *given]), 0)
        if not (self.pages / ".git").is_dir():
            self.git("init", "-q", "-b", "gh-pages")
        self.committed()
        listed = self.git("ls-tree", "-r", "--name-only", "gh-pages", "--", "logos-chat/main")
        return listed.splitlines()

    def git(self, *argv: str) -> str:
        unsigned = ("-c", "commit.gpgsign=false", "-c", "user.name=Saro", "-c", "user.email=s@o")
        return subprocess.run(
            ["git", "-C", str(self.pages), *unsigned, *argv],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def committed(self) -> None:
        """Commits self.pages as it stands to gh-pages."""
        self.git("add", "-A")
        self.git("commit", "-qm", "Publish")


class Plan(GhPages):
    # What the plan warns with of main's point at the base that does not stand in, before why.
    NOT_STANDING = (
        "::warning::main's point at the base does not stand in for the base's runs, so its jobs "
        "run them: "
    )

    def plan(self, suite: str, *extra: str) -> "tuple[int, str, str]":
        return run("plan", "--suite", suite, "--expected", str(self.expected), *extra)

    def test_prints_what_it_was_handed_the_jobs_and_the_scenarios_for_github_output(self):
        for suite, extra, count in (
            ("pr", ("--base", BASE, "--pr", "316"), 12),
            ("main", (), 8),
            ("long", ("--base", BASE), 12),
        ):
            with self.subTest(suite=suite):
                status, out, _ = self.plan(suite, "--revision", REVISION, *extra)
                self.assertEqual(status, 0)
                *handed, runs, scenarios, stood_in = out.splitlines()
                base = BASE if "--base" in extra else ""
                pr = "316" if "--pr" in extra else ""
                self.assertEqual(
                    handed, [f"suite={suite}", f"revision={REVISION}", f"base={base}", f"pr={pr}"]
                )
                self.assertEqual(len(json.loads(runs.removeprefix("runs="))), count)
                names = [name for name, _ in simulate.scenarios(suite, self.expected)]
                self.assertEqual(scenarios, f"scenarios={' '.join(names)}")
                self.assertEqual(stood_in, "point=")

    def listed(self, *paths: str) -> Path:
        """What the plan job lists of gh-pages under logos-chat/main/, a path a line."""
        file = self.root / "points"
        file.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
        return file

    def planned(self, suite: str, listed: Path, *extra: str) -> dict:
        """What a plan of `suite` handed `listed` prints, by name, its run jobs read, and what
        it warned with as `warned`."""
        given = ["--revision", REVISION, "--measurement", MEASUREMENT, "--points", str(listed)]
        given += ["--pages", "gh-pages", "--tree", str(self.pages)]
        status, out, err = self.plan(suite, *given, *extra)
        self.assertEqual(status, 0, err)
        printed = dict(line.split("=", 1) for line in out.splitlines())
        return dict(printed, runs=json.loads(printed["runs"]), warned=err)

    def test_a_pr_whose_base_has_main_s_point_at_the_measurement_runs_nothing_at_the_base(self):
        self.published()
        text = f"logos-chat/main/{BASE}/{MEASUREMENT}/{simulate.GOAL.format(1)}/1/run.txt"
        listed = self.listed(text, point(BASE), point(REVISION, EARLIER))

        planned = self.planned("pr", listed, "--base", BASE, "--pr", "316")

        self.assertEqual((planned["point"], planned["base"]), (point(BASE), BASE))
        self.assertEqual(len(planned["runs"]), 6)
        self.assertEqual(
            {(job["side"], job["revision"]) for job in planned["runs"]}, {("revision", REVISION)}
        )
        self.assertEqual(planned["warned"], "")

    def test_a_point_with_no_outcome_at_a_run_the_pr_asks_of_the_base_stands_in_for_nothing(self):
        scenario = simulate.GOAL.format(5)
        job = f"sim-revision-{scenario}-2"
        # The job of 5%, seed 1, uploaded nothing, or could not build chat-sim.
        for unbuilt in (False, True):
            with self.subTest(unbuilt=unbuilt):
                runs = self.root / f"runs-{unbuilt}"
                shutil.copytree(UPLOADED, runs)
                shutil.rmtree(runs / job)
                if unbuilt:
                    (runs / job / scenario / "2").mkdir(parents=True)
                    (runs / job / scenario / "2" / "unbuilt").write_text("", encoding="utf-8")
                shutil.rmtree(self.pages, ignore_errors=True)
                listed = self.listed(*self.published(runs))
                held = json.loads((self.pages / point(BASE)).read_text(encoding="utf-8"))
                cell = next(
                    one for one in held["cells"] if (one["scenario"], one["run"]) == (scenario, 2)
                )
                self.assertEqual((cell["head"], cell["unbuilt"]["head"]), (None, unbuilt))

                planned = self.planned("pr", listed, "--base", BASE, "--pr", "316")

                self.assertEqual((planned["point"], len(planned["runs"])), ("", 12))
                self.assertEqual({job["side"] for job in planned["runs"][6:]}, {"base"})
                self.assertEqual(
                    planned["warned"],
                    f"{self.NOT_STANDING}{point(BASE)} has no outcome at 5% seed 1\n",
                )

    def test_a_point_that_cannot_be_read_stands_in_for_nothing(self):
        listed = self.listed(*self.published())
        held = self.pages / point(BASE)
        written = json.loads(held.read_text(encoding="utf-8"))
        for text, said in (
            (json.dumps(dict(written, run_number="1")), "run_number is not of its kind"),
            ("not a point\n", "Expecting value: line 1 column 1 (char 0)"),
        ):
            with self.subTest(said=said):
                held.write_text(text, encoding="utf-8")
                self.committed()

                planned = self.planned("pr", listed, "--base", BASE)

                self.assertEqual((planned["point"], len(planned["runs"])), ("", 12))
                self.assertEqual(
                    planned["warned"], f"{self.NOT_STANDING}{point(BASE)} on gh-pages: {said}\n"
                )
        self.git("rm", "-q", point(BASE))
        self.committed()

        planned = self.planned("pr", listed, "--base", BASE)

        self.assertEqual((planned["point"], len(planned["runs"])), ("", 12))
        self.assertTrue(
            planned["warned"].startswith(f"{self.NOT_STANDING}{point(BASE)} on gh-pages: fatal: ")
        )

    def test_a_pr_whose_base_has_no_point_at_the_measurement_runs_at_the_base(self):
        for listed in (
            (point(BASE, EARLIER),),
            (point(REVISION),),
            (f"logos-chat/main/{BASE}/{MEASUREMENT}/{simulate.GOAL.format(1)}/1/run.txt",),
            (),
        ):
            with self.subTest(listed=listed):
                planned = self.planned("pr", self.listed(*listed), "--base", BASE)
                self.assertEqual((planned["point"], len(planned["runs"])), ("", 12))
                self.assertEqual({job["side"] for job in planned["runs"][6:]}, {"base"})

    def test_a_point_stands_in_for_the_base_of_pr_alone(self):
        planned = self.planned("long", self.listed(point(BASE)), "--base", BASE)

        self.assertEqual((planned["point"], len(planned["runs"])), ("", 12))
        self.assertEqual(self.planned("pr", self.listed(point(BASE)))["point"], "")

    def test_refuses_points_without_a_measurement_or_pages_and_a_measurement_not_a_commit(self):
        listed = self.listed(point(BASE))
        given = ("--measurement", MEASUREMENT, "--points", str(listed))
        ref = ("--pages", "gh-pages")
        for extra, said in (
            (("--points", str(listed)), "--points: given with --measurement"),
            (("--measurement", MEASUREMENT), "--points: given with --measurement"),
            (given, "--points: given with --measurement and --pages"),
            (ref, "--points: given with --measurement and --pages"),
            (
                ("--measurement", "7654321", "--points", str(listed), *ref),
                "--measurement: '7654321'",
            ),
            (
                ("--measurement", MEASUREMENT, "--points", str(self.root / "nowhere"), *ref),
                "--points: ",
            ),
        ):
            with self.subTest(extra=extra):
                status, out, err = self.plan("pr", "--revision", REVISION, "--base", BASE, *extra)
                self.assertEqual((status, out), (2, ""))
                self.assertIn(said, err)

    def test_refuses_what_is_not_a_full_commit_or_a_pull_request_and_a_base_for_main(self):
        for suite, extra, said in (
            ("pr", ("--revision", REVISION[:7]), "--revision"),
            ("pr", ("--revision", REVISION.upper()), "--revision"),
            ("pr", ("--revision", REVISION, "--base", "main"), "--base"),
            ("pr", ("--revision", REVISION, "--base", REVISION), "--base: the revision itself"),
            ("pr", ("--revision", REVISION, "--pr", "0"), "--pr"),
            ("pr", ("--revision", REVISION, "--pr", "1; rm"), "--pr"),
            ("quick", ("--revision", REVISION), "--suite"),
            (
                "main",
                ("--revision", REVISION, "--base", BASE),
                "--base: the main suite runs at the revision alone",
            ),
        ):
            with self.subTest(extra=extra):
                status, out, err = self.plan(suite, *extra)
                self.assertEqual(status, 2)
                self.assertEqual(out, "")
                self.assertIn(said, err)


class Spread(GhPages):
    """The point of main nearest before a base, from the base's history."""

    def spread(self, history: object, *listed: str) -> "tuple[int, str, str]":
        """What `spread` says of `history`, as logos-chat's commits API lists it, cut, and of the
        points `listed` on gh-pages."""
        (self.root / "history.json").write_text(json.dumps(history), encoding="utf-8")
        (self.root / "points").write_text("".join(f"{one}\n" for one in listed), "utf-8")
        return run(
            "spread",
            "--measurement",
            MEASUREMENT,
            "--points",
            str(self.root / "points"),
            "--history",
            str(self.root / "history.json"),
            "--pages",
            "gh-pages",
            "--tree",
            str(self.pages),
        )

    def history(self, *shas: str) -> List[dict]:
        return [{"sha": sha, "commit": {"message": f"commit {sha[:7]}"}} for sha in shas]

    def test_is_the_newest_commit_of_the_base_s_history_with_a_point_at_the_measurement(self):
        older, oldest = FA5282A, CHAT_SIM
        history = self.history(BASE, REVISION, older, oldest)
        self.published(revision=older)
        self.published(revision=BASE)

        status, out, _ = self.spread(
            history, point(REVISION, EARLIER), point(oldest), point(older), point(BASE, EARLIER)
        )

        self.assertEqual((status, out), (0, f"spread={point(older)}\n"))
        _, out, _ = self.spread(history, point(BASE), point(older))
        self.assertEqual(out, f"spread={point(BASE)}\n")

    def test_is_nothing_when_no_commit_of_the_history_has_a_point_at_the_measurement(self):
        status, out, _ = self.spread(self.history(BASE, REVISION), point(BASE, EARLIER))

        self.assertEqual((status, out), (0, "spread=\n"))

    def test_passes_over_a_point_that_cannot_be_read_and_says_why(self):
        self.published(revision=FA5282A)
        listed = self.published(revision=BASE)
        history = self.history(BASE, REVISION, FA5282A)
        passed = "::warning::the head's runs are not placed among main's point: "
        said = "on gh-pages: Expecting value: line 1 column 1 (char 0)"
        (self.pages / point(BASE)).write_text("not a point\n", encoding="utf-8")
        self.committed()

        status, out, err = self.spread(history, *listed)

        self.assertEqual((status, out), (0, f"spread={point(FA5282A)}\n"))
        self.assertEqual(err, f"{passed}{point(BASE)} {said}\n")
        (self.pages / point(FA5282A)).write_text("not a point\n", encoding="utf-8")
        self.committed()

        status, out, err = self.spread(history, *listed)

        self.assertEqual((status, out), (0, "spread=\n"))
        self.assertEqual(err, f"{passed}{point(BASE)} {said}\n{passed}{point(FA5282A)} {said}\n")

    def test_refuses_a_history_that_is_not_a_list_of_commits(self):
        for history in ({"message": "Not Found"}, [{"sha": "main"}], [{"commit": {}}]):
            with self.subTest(history=history):
                status, out, err = self.spread(history)
                self.assertEqual((status, out), (2, ""))
                self.assertIn("not a list of commits, each with its sha", err)


class Measurement(unittest.TestCase):
    """A history of three commits: to crates/, then to the guide, then to tools/forks/forks/."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tree = Path(directory.name) / "tree"
        self.git("init", "-q", str(self.tree), tree=Path(directory.name))
        self.shas = [
            self.change(path) for path in ("crates/harness/a", "guide/b", "tools/forks/forks/c")
        ]

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

    def test_is_the_newest_commit_to_what_a_run_measures(self):
        self.assertEqual(simulate.measurement(self.tree), self.shas[2])
        self.change("guide/d")
        status, out, _ = run("measurement", "--tree", str(self.tree))
        self.assertEqual((status, out), (0, f"measurement={self.shas[2]}\n"))

    def test_a_commit_to_the_guide_moves_no_measurement(self):
        self.git("reset", "-q", "--hard", self.shas[1])
        self.assertEqual(simulate.measurement(self.tree), self.shas[0])

    def test_a_commit_to_a_build_file_at_the_root_moves_it_and_one_to_the_readme_does_not(self):
        for path in (
            "Cargo.toml",
            "Cargo.lock",
            ".cargo/config.toml",
            "rust-toolchain.toml",
            "flake.nix",
            "flake.lock",
        ):
            with self.subTest(path=path):
                moved = self.change(path)
                self.assertEqual(simulate.measurement(self.tree), moved)
        self.change("README.md")
        self.assertEqual(simulate.measurement(self.tree), moved)

    def test_a_shallow_clone_is_refused(self):
        shallow = self.tree.parent / "shallow"
        self.git("clone", "-q", "--depth", "1", f"file://{self.tree}", str(shallow))

        status, out, err = run("measurement", "--tree", str(shallow))

        self.assertEqual((status, out), (2, ""))
        self.assertIn("shallow clone", err)


class Title(unittest.TestCase):
    """The verdict of a matrix in a few words, from the counts of its cells."""

    def title(
        self, verdict: str, base: Optional[dict] = None, spread: Optional[dict] = None, **counted
    ) -> str:
        counts = dict.fromkeys(("better", "worse", "re-rolled", "below", "no-outcome"), 0)
        counts.update({"cells": 6, "compared": 6, **counted})
        base = {"unbuilt": False, "runs": 6} if base is None else base
        matrix = {"verdict": verdict, "counts": counts, "base": base, "spread": spread}
        return simulate.title(matrix)

    def test_each_verdict_has_the_title_of_its_check(self):
        for verdict, counted, said in (
            ("same", {}, "same as its base, 6 of 6 runs"),
            ("same", {"compared": 5}, "same as its base, 5 of 6 runs"),
            ("better", {"better": 1}, "better in 1 of 6 runs"),
            ("worse", {"worse": 2}, "worse in 2 of 6 runs"),
            ("hint", {"worse": 1}, "worse in 1 of 6 runs, a hint"),
            ("mixed", {"better": 1, "worse": 1}, "mixed: 1 better, 1 worse of 6"),
            ("re-rolled", {"re-rolled": 6}, "the draws changed, no comparison"),
            ("unbuilt", {}, "does not build against this commit"),
            ("no-outcome", {"no-outcome": 1}, "no outcome in 1 of 6 runs"),
        ):
            with self.subTest(verdict=verdict, counted=counted):
                self.assertEqual(self.title(verdict, **counted), said)

    def test_re_rolled_beside_main_s_spread_says_how_many_fell_below_its_worst_seed(self):
        spread = {"logos_chat": BASE}
        for counted, said in (
            ({"re-rolled": 6, "below": 1}, "1 of 6 below main's worst seed"),
            ({"re-rolled": 2, "below": 0}, "0 of 6 below main's worst seed"),
        ):
            with self.subTest(counted=counted):
                self.assertEqual(
                    self.title("re-rolled", spread=spread, **counted),
                    f"the draws changed, no comparison; {said}",
                )
        self.assertEqual(
            self.title("hint", spread=spread, worse=1, below=1), "worse in 1 of 6 runs, a hint"
        )

    def test_no_base_says_why_there_is_no_comparison(self):
        no_base = {"unbuilt": False, "runs": 0}
        self.assertEqual(
            self.title("no-base", {"unbuilt": True, "runs": 0}),
            "does not build at the base, no comparison",
        )
        self.assertEqual(self.title("no-base", no_base), "no outcome at the base, no comparison")
        self.assertEqual(
            simulate.title({"verdict": "no-base", "counts": {}, "base": None}),
            "no base, no comparison",
        )


class Drawn(unittest.TestCase):
    """The comment drawn from the matrix tools/sim matrix writes of the runs uploaded."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "expected").mkdir()

    def uploaded(self, name: str, **edits: dict) -> Path:
        """A copy of UPLOADED with the fields `edits` gives a run changed, by its rate and seed."""
        copy = self.root / name
        shutil.copytree(UPLOADED, copy)
        for cell, changed in edits.items():
            rate, seed = cell.split("/")
            scenario, number = simulate.GOAL.format(rate), int(seed) + 1
            file = copy / f"sim-revision-{scenario}-{number}" / scenario / str(number) / "run.json"
            report = json.loads(file.read_text(encoding="utf-8"))
            report.update(changed)
            file.write_text(json.dumps(report), encoding="utf-8")
        return copy

    def unbuilt(self, side: Path, rate: str, seed: int) -> None:
        """The artifact of the job of a run at `rate` and `seed` as one that could not build."""
        scenario, number = simulate.GOAL.format(rate), seed + 1
        job = side / f"sim-revision-{scenario}-{number}"
        shutil.rmtree(job, ignore_errors=True)
        (job / scenario / str(number)).mkdir(parents=True)
        (job / scenario / str(number) / "unbuilt").write_text("", encoding="utf-8")

    def matrix(
        self,
        head: Path,
        base: Optional[Path] = None,
        asked: Sequence[dict] = (),
        base_point: Optional[Path] = None,
        spread: Optional[Path] = None,
    ) -> Path:
        """The file tools/sim matrix writes of the runs under `head`, beside those under `base`
        or main's point in `base_point`, with a cell for each run of the jobs `asked`, and the
        head's runs placed among those of the point in `spread`."""
        out = self.root / "matrix.json"
        command = [sys.executable, str(ROOT / "tools" / "sim"), "matrix", str(head)]
        command += ["--out", str(out)] + ([] if base is None else ["--base", str(base)])
        if base_point is not None:
            command += ["--base-point", str(base_point)]
        if spread is not None:
            command += ["--spread", str(spread)]
        if asked:
            write(self.root / "asked.json", list(asked))
            command += ["--asked", str(self.root / "asked.json")]
        subprocess.run(command, check=True)
        return out

    def pr_jobs(self) -> List[dict]:
        return simulate.jobs("pr", REVISION, BASE, simulate.scenarios("pr", Path()))

    def comment_of(self, suite: str, matrix: Path) -> List[str]:
        """What `comment` is handed for `suite` drawn from `matrix`, but a base."""
        return [
            "--matrix",
            str(matrix),
            "--suite",
            suite,
            "--revision",
            REVISION,
            "--expected",
            str(self.root / "expected"),
            "--chat-sim",
            CHAT_SIM,
            "--run-url",
            RUN_URL,
        ]

    def comment(self, suite: str, matrix: Path, *extra: str) -> "tuple[int, str, str]":
        return run("comment", *self.comment_of(suite, matrix), *extra)

    def rows(self, text: str) -> List[str]:
        return [line for line in text.splitlines() if line.startswith("| ") and "%" in line]

    def test_a_pr_two_cells_of_which_split_sooner_than_at_the_base_is_worse_in_2_of_6(self):
        head = self.uploaded(
            "head",
            **{"1/1": {"verdict": "split at epoch 5"}, "5/0": {"verdict": "split at epoch 4"}},
        )
        status, out, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", BASE)

        self.assertEqual(status, 0)
        first, blank, header, rule, *_ = out.splitlines()
        self.assertEqual(
            first,
            f"**chat-sim: worse in 2 of 6 runs** at {commit(REVISION)} against its merge base "
            f"{commit(BASE)} · [the run]({RUN_URL})",
        )
        self.assertEqual((blank, header, rule), ("", "| Loss | Seed 0 | Seed 1 |", "|---|---|---|"))
        self.assertEqual(
            self.rows(out),
            [
                "| 0.1% | 50 whole, same | 46, split, same |",
                "| 1% | 18, split, same | 5, split, was 9: worse |",
                "| 5% | 4, split, was 8: worse | 3, split, same |",
            ],
        )
        self.assertIn(
            "\n\nA cell: how many epochs the group stayed one group (50 is the goal) · how the run "
            "ended (whole: one group, everyone in it; behind: one group, some members behind; "
            "stalled: one group that stopped growing short of 50; split: it forked) · the change "
            "against the merge base at the same seed",
            out,
        )
        self.assertEqual((out.count("<details>"), out.count("<details open>")), (4, 2))
        self.assertIn(
            "\n<details open><summary>5% loss, seed 0: 4, split, was 8: worse</summary>", out
        )
        fold = out[out.index("<details open><summary>1% loss, seed 1:") :]
        fold = fold[: fold.index("</details>")]
        self.assertEqual(
            fold.splitlines()[:4],
            [
                "<details open><summary>1% loss, seed 1: 5, split, was 9: worse</summary>",
                "",
                f"- **head** {commit(REVISION)} · split at epoch 5 · the trunk reached epoch 50: "
                "trunk at epoch 50, 12 main, 0 sub, 18 fell behind, 9 behind at the end, 0 never "
                "joined",
                f"- **base** {commit(BASE)} · split at epoch 9 · the trunk reached epoch 50: "
                "trunk at epoch 50, 12 main, 0 sub, 18 fell behind, 9 behind at the end, 0 never "
                "joined",
            ],
        )
        self.assertIn(
            "\n```sh\n"
            "git clone https://github.com/logos-messaging/chat-sim && cd chat-sim && git checkout "
            f"{CHAT_SIM}\n"
            f"# move both pins to {REVISION}, or to {BASE} for the base, then\n"
            "nix develop -c cargo build --release -p harness --bin harness-run\n"
            "python3 tools/sim sweep tools/sim/scenarios/50-epochs-by-saro-waku-loss-1.json "
            "--from 2 --runs 1 --one-process target/release/harness-run --out "
            "tmp/50-epochs-by-saro-waku-loss-1-seed-1\n```\n",
            fold,
        )

    def test_a_pr_against_main_s_point_at_its_base_names_it_and_shows_main_s_spread(self):
        head = self.uploaded(
            "head",
            **{"1/1": {"verdict": "split at epoch 5"}, "5/0": {"verdict": "split at epoch 4"}},
        )
        against_point = self.matrix(head, base_point=POINT, spread=POINT)
        _, out, _ = self.comment("pr", against_point, "--base", FA5282A)
        _, against_runs, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", FA5282A)

        first, blank, header, rule, *_ = out.splitlines()
        self.assertEqual(
            first,
            f"**chat-sim: worse in 2 of 6 runs** at {commit(REVISION)} against its merge base "
            f"{commit(FA5282A)}, from [main's run of it]({POINT_RUN}) · [the run]({RUN_URL})",
        )
        self.assertEqual(
            header, f"| Loss | Seed 0 | Seed 1 | main's 20 seeds at {commit(FA5282A)} |"
        )
        self.assertEqual(rule, "|---|---|---|---|")
        self.assertEqual(
            self.rows(out),
            [
                "| 0.1% | 50 whole, same | 46, split, same | 2 whole, 18 to 50 |",
                "| 1% | 18, split, same | 5, split, was 9: worse | 0 whole, 3 to 23 |",
                "| 5% | 4, split, was 8: worse | 3, split, same | 0 whole, 3 to 18 |",
            ],
        )
        self.assertEqual(
            [row.rsplit(" | ", 1)[0] + " |" for row in self.rows(out)], self.rows(against_runs)
        )
        folds = out.index("\n\nA cell: ")
        self.assertEqual(out[folds:], against_runs[against_runs.index("\n\nA cell: ") :])

    def test_a_re_rolled_cell_says_whether_its_epochs_are_within_main_s_spread_or_below_it(self):
        # The sheet's re-rolled state: every head run drew other ids, one pair a seed.
        seeds = {"0": "7c3e9a01b2", "1": "b19d47e0c5"}
        drawn = {"0.1/0": 50, "0.1/1": 15, "1/0": 11, "1/1": 20, "5/0": 6, "5/1": 4}
        head = self.uploaded(
            "head",
            **{
                cell: {"conversation": seeds[cell.split("/")[1]]}
                | ({} if epochs == 50 else {"verdict": f"split at epoch {epochs}"})
                for cell, epochs in drawn.items()
            },
        )
        _, out, _ = self.comment(
            "pr", self.matrix(head, base_point=POINT, spread=POINT), "--base", FA5282A
        )

        self.assertTrue(
            out.startswith(
                "**chat-sim: the draws changed, no comparison; 1 of 6 below main's worst seed** at "
            )
        )
        within = "re-rolled, within main's spread"
        self.assertEqual(
            self.rows(out),
            [
                f"| 0.1% | 50 whole, {within} | 15, split, re-rolled, below main's spread "
                "| 2 whole, 18 to 50 |",
                f"| 1% | 11, split, {within} | 20, split, {within} | 0 whole, 3 to 23 |",
                f"| 5% | 6, split, {within} | 4, split, {within} | 0 whole, 3 to 18 |",
            ],
        )
        self.assertIn(
            "\n<details><summary>0.1% loss, seed 1: 15, split, re-rolled, below main's "
            "spread</summary>\n",
            out,
        )

    def test_a_pr_whose_base_has_no_point_shows_the_spread_of_the_nearest_before_it(self):
        before = json.loads(POINT.read_text(encoding="utf-8"))
        before["logos_chat"] = CHAT_SIM
        spread = self.root / "spread.json"
        spread.write_text(json.dumps(before), encoding="utf-8")
        matrix = self.matrix(UPLOADED, UPLOADED, spread=spread)
        _, out, _ = self.comment("pr", matrix, "--base", BASE)

        first, _, header, *_ = out.splitlines()
        self.assertEqual(
            first,
            f"**chat-sim: same as its base, 6 of 6 runs** at {commit(REVISION)} against its merge "
            f"base {commit(BASE)} · [the run]({RUN_URL})",
        )
        self.assertEqual(
            header, f"| Loss | Seed 0 | Seed 1 | main's 20 seeds at {commit(CHAT_SIM)} |"
        )
        self.assertEqual(
            self.rows(out)[0], "| 0.1% | 50 whole, same | 46, split, same | 2 whole, 18 to 50 |"
        )

    def test_without_main_s_point_the_column_and_the_note_are_left_out(self):
        head = self.uploaded(
            "head", **{"0.1/1": {"conversation": "b19d47e0c5", "verdict": "split at epoch 15"}}
        )
        _, out, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", BASE)

        self.assertTrue(out.startswith("**chat-sim: the draws changed, no comparison** at "))
        self.assertEqual(out.splitlines()[2], "| Loss | Seed 0 | Seed 1 |")
        self.assertEqual(self.rows(out)[0], "| 0.1% | 50 whole, same | 15, split, re-rolled |")
        self.assertNotIn("main's", out.split("\n\nA cell: ")[0])

    def test_a_pr_whose_cells_only_drew_other_ids_or_ran_otherwise_is_re_rolled(self):
        head = self.uploaded(
            "head", **{"5/1": {"conversation": "7c3e9a01b2"}, "1/0": {"virtual_s": 6077.0}}
        )
        _, out, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", BASE)

        self.assertTrue(out.startswith("**chat-sim: the draws changed, no comparison** at "))
        self.assertEqual(
            self.rows(out)[1:],
            [
                "| 1% | 18, split, changed | 9, split, same |",
                "| 5% | 8, split, same | 3, split, re-rolled |",
            ],
        )

    def test_a_pr_cell_worse_at_the_same_epochs_names_what_moved(self):
        name = simulate.GOAL.format("0.1")
        file = UPLOADED / f"sim-revision-{name}-2" / name / "2" / "run.json"
        grow = json.loads(file.read_text(encoding="utf-8"))["grow"]
        grow["lines"][0]["members"].remove("raya")
        grow["lines"][1]["members"].append("raya")
        head = self.uploaded(
            "head", **{"0.1/0": {"verdict": "whole, members behind"}, "0.1/1": {"grow": grow}}
        )
        _, out, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", BASE)

        self.assertTrue(out.startswith("**chat-sim: worse in 2 of 6 runs** at "))
        self.assertEqual(
            self.rows(out)[0],
            "| 0.1% | 50, behind, was whole: worse "
            "| 46, split, 3 members off the trunk, was 2: worse |",
        )

    def test_a_pr_with_a_run_that_left_nothing_at_the_head_has_no_outcome_there(self):
        head = self.uploaded("head")
        shutil.rmtree(head / "sim-revision-50-epochs-by-saro-waku-loss-5-2")
        _, out, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", BASE)

        self.assertTrue(out.startswith("**chat-sim: no outcome in 1 of 6 runs** at "))
        self.assertEqual(self.rows(out)[2], "| 5% | 8, split, same | no outcome |")
        self.assertIn(f"- **head** {commit(REVISION)} · no outcome\n", out)

    def test_a_pr_run_whose_job_uploaded_nothing_at_either_side_has_no_outcome(self):
        head, base = self.uploaded("head"), self.uploaded("base")
        for side in (head, base):
            shutil.rmtree(side / "sim-revision-50-epochs-by-saro-waku-loss-5-2")
        _, out, _ = self.comment("pr", self.matrix(head, base, self.pr_jobs()), "--base", BASE)

        self.assertTrue(out.startswith("**chat-sim: no outcome in 1 of 6 runs** at "))
        self.assertEqual(self.rows(out)[2], "| 5% | 8, split, same | no outcome |")
        self.assertIn(f"- **base** {commit(BASE)} · no outcome\n", out)

    def test_a_pr_run_whose_job_could_not_build_at_the_head_has_no_outcome_there(self):
        head = self.uploaded("head")
        self.unbuilt(head, "1", 1)
        _, out, _ = self.comment("pr", self.matrix(head, UPLOADED, self.pr_jobs()), "--base", BASE)

        self.assertTrue(out.startswith("**chat-sim: no outcome in 1 of 6 runs** at "))
        self.assertEqual(self.rows(out)[1], "| 1% | 18, split, same | does not build |")
        self.assertIn(f"- **head** {commit(REVISION)} · does not build\n", out)

    def test_a_pr_cell_the_base_could_not_build_says_so_and_the_others_are_compared(self):
        head = self.uploaded("head", **{"5/0": {"verdict": "split at epoch 4"}})
        base = self.uploaded("base")
        self.unbuilt(base, "1", 1)
        _, out, _ = self.comment("pr", self.matrix(head, base, self.pr_jobs()), "--base", BASE)

        self.assertTrue(out.startswith("**chat-sim: worse in 1 of 6 runs, a hint** at "))
        self.assertEqual(
            self.rows(out)[1:],
            [
                "| 1% | 18, split, same | 9, split, does not build at the base |",
                "| 5% | 4, split, was 8: worse | 3, split, same |",
            ],
        )
        self.assertIn(f"- **base** {commit(BASE)} · does not build\n", out)

    def test_a_runs_words_are_escaped_in_its_fold(self):
        head = self.uploaded("head", **{"1/1": {"verdict": "split at epoch 9 </details>"}})
        _, out, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", BASE)

        self.assertIn(f"- **head** {commit(REVISION)} · split at epoch 9 &lt;/details&gt; · ", out)
        self.assertEqual(out.count("</details>"), 6)

    def test_a_pr_without_a_base_shows_the_heads_cells_and_links_the_report_when_given(self):
        _, out, _ = self.comment("pr", self.matrix(UPLOADED), "--report-url", REPORT_URL)

        self.assertEqual(
            out.splitlines()[0],
            f"**chat-sim: no base, no comparison** at {commit(REVISION)} · "
            f"[the report]({REPORT_URL}) · [the run]({RUN_URL})",
        )
        self.assertEqual(self.rows(out)[0], "| 0.1% | 50 whole | 46, split |")
        self.assertNotIn("- **base**", out)
        self.assertIn(f"# move both pins to {REVISION}, then\n", out)

    def test_a_head_chat_sim_does_not_build_at_ran_nothing(self):
        head = self.root / "head"
        self.unbuilt(head, "1", 0)
        for suite, base, extra in (("pr", UPLOADED, ("--base", BASE)), ("main", None, ())):
            with self.subTest(suite=suite):
                status, out, _ = self.comment(suite, self.matrix(head, base), *extra)
                self.assertEqual(status, 0)
                against = f" against its merge base {commit(BASE)}" if base else ""
                self.assertEqual(
                    out,
                    f"**chat-sim does not build against this commit** at {commit(REVISION)}"
                    f"{against} · [the run]({RUN_URL})\n\n"
                    "No scenario ran: the build's errors are in the run.\n",
                )

    def test_a_head_chat_sim_does_not_build_at_points_to_the_report_page_when_given(self):
        head = self.root / "head"
        self.unbuilt(head, "1", 0)
        given = ("--base", BASE, "--report-url", REPORT_URL)
        _, out, _ = self.comment("pr", self.matrix(head, UPLOADED), *given)

        self.assertEqual(
            out,
            f"**chat-sim does not build against this commit** at {commit(REVISION)} against its "
            f"merge base {commit(BASE)} · [the report]({REPORT_URL}) · [the run]({RUN_URL})\n\n"
            "No scenario ran: the build's errors are on the report page.\n",
        )

    def test_main_links_its_point_s_page_when_given(self):
        page = f"https://logos-messaging.github.io/chat-sim/logos-chat/main/{REVISION}/{CHAT_SIM}/"
        _, out, _ = self.comment("main", self.matrix(UPLOADED), "--report-url", page)

        self.assertEqual(
            out.splitlines()[0],
            f"**chat-sim: 6 runs** at {commit(REVISION)} · [the report]({page}) · "
            f"[the run]({RUN_URL})",
        )

    def test_refuses_a_report_url_a_markdown_link_cannot_hold_as_it_is(self):
        matrix = self.matrix(UPLOADED)
        for url in ("javascript:alert(1)", "https://example.org/a b", f"{REPORT_URL})[x](y", ""):
            with self.subTest(url=url):
                status, out, err = self.comment("pr", matrix, "--report-url", url)
                self.assertEqual((status, out), (2, ""))
                self.assertIn("--report-url: ", err.splitlines()[-1])

    def test_main_is_a_row_a_rate_with_how_its_runs_ended_and_their_epochs(self):
        status, out, _ = self.comment("main", self.matrix(UPLOADED))

        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertEqual(
            lines[0], f"**chat-sim: 6 runs** at {commit(REVISION)} · [the run]({RUN_URL})"
        )
        self.assertEqual(
            lines[2], "| Loss | Runs | Whole | Behind | Split | Stalled | Epochs without a fork |"
        )
        self.assertEqual(
            self.rows(out),
            [
                "| 0.1% | 2 | 1 | 0 | 1 | 0 | 46 to 50, median 48 |",
                "| 1% | 2 | 0 | 0 | 2 | 0 | 9 to 18, median 13.5 |",
                "| 5% | 2 | 0 | 0 | 2 | 0 | 3 to 8, median 5.5 |",
            ],
        )
        self.assertTrue(lines[-1].startswith("A row: the runs at a rate of loss, a seed each, "))
        self.assertNotIn("<details>", out)

    def test_main_with_a_job_that_uploaded_nothing_counts_its_runs_with_no_outcome(self):
        head = self.uploaded("head")
        shutil.rmtree(head / "sim-revision-50-epochs-by-saro-waku-loss-1-2")
        asked = [
            {"scenario": simulate.GOAL.format(rate), "from": 1, "runs": 2}
            for rate in ("0.1", "1", "5")
        ]
        _, out, _ = self.comment("main", self.matrix(head, None, asked))

        self.assertTrue(out.startswith("**chat-sim: no outcome in 1 of 6 runs** at "))
        self.assertEqual(self.rows(out)[1], "| 1% | 2 | 0 | 0 | 1 | 0 | 18 to 18, median 18 |")

    def test_refuses_a_matrix_for_long_and_runs_or_no_matrix_for_pr_and_main(self):
        matrix = self.matrix(UPLOADED, UPLOADED)
        unread = self.root / "unread.json"
        unread.write_text("{}", encoding="utf-8")
        for suite, given, said in (
            ("long", ("--base", BASE), "RUNS: the long suite is written from its runs"),
            ("main", ("--base-runs", str(self.root)), "--matrix: the main suite"),
            ("pr", (), "--matrix: a matrix with a base is given with --base"),
        ):
            with self.subTest(suite=suite, given=given):
                status, out, err = self.comment(suite, matrix, *given)
                self.assertEqual((status, out), (2, ""))
                self.assertIn(said, err)
        status, out, err = run(
            "comment", str(self.root), *self.comment_of("pr", matrix), "--base", BASE
        )
        self.assertEqual((status, out), (2, ""))
        self.assertIn("--matrix: the pr suite is written from its matrix alone", err)
        status, _, err = self.comment("pr", unread)
        self.assertEqual(status, 2)
        self.assertIn(f"--matrix: {unread} is not a matrix tools/sim matrix writes", err)
        against_point = self.matrix(UPLOADED, base_point=POINT)
        status, out, err = self.comment("pr", against_point, "--base", BASE)
        self.assertEqual((status, out), (2, ""))
        self.assertIn("--matrix: main's point that stood in for the base is not at --base", err)


class Comment(Fixture):
    """The comment on the long suite, from the text each run job uploaded in an artifact of its
    own."""

    def setUp(self):
        super().setUp()
        self.runs = self.root / "revision"
        self.base_runs = self.root / "base"
        self.names = [name for name, _ in simulate.scenarios("long", self.expected)]
        for name in self.names:
            count = 1 if name.startswith("50-epochs") else 3
            write(
                self.swept(self.runs, name) / "outcomes.json",
                [outcome(seed) for seed in range(count)],
            )
            write(
                self.swept(self.base_runs, name) / "outcomes.json",
                [outcome(seed) for seed in range(count)],
            )
        self.with_base = ("--base", BASE, "--base-runs", str(self.base_runs))

    def swept(self, runs: Path, name: str) -> Path:
        """Where a scenario's sweep is under what the run jobs of a side uploaded."""
        return runs / f"sim-{runs.name}-{name}-1" / name

    def unbuilt(self, runs: Path) -> None:
        for name in self.names:
            (self.swept(runs, name) / "outcomes.json").unlink()
            (self.swept(runs, name) / "1").mkdir()
            (self.swept(runs, name) / "1" / "unbuilt").write_text("", encoding="utf-8")

    def compared(self, name: str, count: str) -> str:
        text = f"run  seed  change\n{count}\n"
        (self.swept(self.runs, name) / "compare.txt").write_text(text, encoding="utf-8")
        return text

    def comment(self, *extra: str) -> "tuple[int, str, str]":
        return run(
            "comment",
            str(self.runs),
            "--suite",
            "long",
            "--revision",
            REVISION,
            "--expected",
            str(self.expected),
            "--chat-sim",
            CHAT_SIM,
            "--run-url",
            RUN_URL,
            *extra,
        )

    def rows(self, text: str) -> dict:
        """Each scenario's row, by its name, as the cells after it."""
        found = {}
        for line in text.splitlines():
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if line.startswith("| `"):
                found[cells[0].strip("`")] = cells[1:]
        return found

    def test_against_the_base_says_what_compare_counted(self):
        write(
            self.swept(self.runs, "grow-waku-loss-1") / "outcomes.json",
            [outcome(0), outcome(1, "split at epoch 9"), outcome(2, "split at epoch 4")],
        )
        self.compared("grow-waku-loss-1", "1 of 3 runs came out as expected: 2 worse")
        status, out, _ = self.comment(*self.with_base, "--pr", "316")
        self.assertEqual(status, 0)
        self.assertIn(
            "**chat-sim, the long suite** at logos-chat [`0123456`](https://github.com/"
            f"logos-messaging/logos-chat/commit/{REVISION}) ([#316](https://github.com/"
            "logos-messaging/logos-chat/pull/316)), against the base [`89abcde`]",
            out,
        )
        self.assertIn("| Scenario | Runs | Came to | Against the base |", out)
        rows = self.rows(out)
        self.assertEqual(
            rows["grow-waku-loss-1"],
            [
                "3",
                "whole, split at epoch 9, split at epoch 4",
                "1 of 3 runs came out as expected: 2 worse",
            ],
        )
        self.assertEqual(rows["50-epochs"], ["1", "whole", "no outcome at the base"])
        self.assertIn(f"chat-sim `fedcba9` · [the run]({RUN_URL})", out)

    def test_without_a_base_holds_to_what_is_expected_and_says_where_nothing_is(self):
        self.compared("grow-waku-loss-5", "3 of 3 runs came out as expected")
        status, out, _ = self.comment()
        self.assertEqual(status, 0)
        self.assertIn("against what chat-sim holds in `tools/sim/expected`", out)
        self.assertIn("| Scenario | Runs | Came to | Against what is held |", out)
        rows = self.rows(out)
        self.assertEqual(rows["50-epochs"], ["1", "whole", "nothing held"])
        self.assertEqual(
            rows["grow-waku-loss-5"],
            ["3", "whole, whole, whole", "3 of 3 runs came out as expected"],
        )

    def test_a_scenario_with_no_outcome_says_so(self):
        (self.swept(self.runs, "50-epochs") / "outcomes.json").unlink()
        write(
            self.swept(self.runs, "grow-waku-loss-1") / "outcomes.json",
            [outcome(0), outcome(1, None)],
        )
        _, out, _ = self.comment(*self.with_base)
        rows = self.rows(out)
        self.assertEqual(rows["50-epochs"], ["1", "no outcome", "-"])
        self.assertEqual(rows["grow-waku-loss-1"][1], "whole, no verdict")

    def test_a_revision_chat_sim_does_not_build_at_ran_nothing(self):
        self.unbuilt(self.runs)
        status, out, _ = self.comment(*self.with_base, "--pr", "316")
        self.assertEqual(status, 0)
        self.assertIn(
            "\n\nchat-sim does not build at this revision, so no scenario ran: the build's "
            "errors are in the run.\n\n",
            out,
        )
        self.assertNotIn("|", out)
        self.assertTrue(out.endswith(f"chat-sim `fedcba9` · [the run]({RUN_URL})\n"))

    def test_a_base_chat_sim_does_not_build_at_is_no_base_to_hold_to(self):
        self.unbuilt(self.base_runs)
        _, out, _ = self.comment(*self.with_base)
        rows = self.rows(out)
        self.assertEqual(rows["50-epochs"], ["1", "whole", "chat-sim does not build at the base"])

    def test_refuses_a_base_without_its_runs_and_runs_without_a_base(self):
        for extra in (("--base", BASE), ("--base-runs", str(self.base_runs))):
            with self.subTest(extra=extra):
                status, out, err = self.comment(*extra)
                self.assertEqual(status, 2)
                self.assertEqual(out, "")
                self.assertIn("--base-runs", err)

    def test_shows_each_compare_folded_under_the_table(self):
        compared = self.compared("grow-waku-loss-1", "3 of 3 runs came out as expected")
        _, out, _ = self.comment()
        self.assertIn(
            "<details><summary><code>grow-waku-loss-1</code></summary>\n\n```\n"
            f"{compared}```\n\n</details>\n",
            out,
        )
        self.assertEqual(out.count("<details>"), 1)


if __name__ == "__main__":
    unittest.main()
