import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from typing import List, Optional, Sequence

import pages
import simulate

REVISION = "0123456789abcdef0123456789abcdef01234567"
BASE = "89abcdef0123456789abcdef0123456789abcdef"
CHAT_SIM = "fedcba9876543210fedcba9876543210fedcba98"
RUN_URL = "https://github.com/logos-messaging/chat-sim/actions/runs/42"
REPORT_URL = "https://logos-messaging.github.io/chat-sim/logos-chat/pr-316/"
ROOT = Path(__file__).resolve().parents[2]
# What the GroupV2 jobs of HELD_SUITES' pr suite uploaded at a revision, seeds 0 and 1 of its three
# rates, cut to what tools/sim matrix reads: 0.1% whole and split at epoch 46, 1% split at 18 and
# 9, 5% split at 8 and 3.
UPLOADED = ROOT / "tools" / "sim" / "tests" / "fixtures" / "matrix"
# Main's point at logos-chat fa5282a3, the probe's twenty seeds of each rate, cut to what
# tools/sim matrix reads: its seeds 0 and 1 of the lossy rates are UPLOADED's runs.
POINT = ROOT / "tools" / "sim" / "tests" / "fixtures" / "point.json"
# The goal UPLOADED and POINT hold runs of, a group of 51 that saro grows toward epoch 50, by the
# group's name in a scenario, and the rates and seeds the suites ran of it.
HELD_GOALS = {
    "v2": "50-epochs-by-saro-waku-loss-{}",
    "v3": "50-epochs-by-saro-waku-loss-{}-groupv3",
}
# The lines and the members off the trunk at the end of each of UPLOADED's runs but 0.1% seed 0,
# which ended whole, and how many of those never joined, by its rate and seed, as a cell says them.
OFF = {
    "0.1/1": "1 line and 2 of 51 members off the trunk",
    "1/0": "7 lines and 18 of 51 members off the trunk, 1 of them never joined",
    "1/1": "11 lines and 21 of 51 members off the trunk",
    "5/0": "2 lines and 9 of 15 members off the trunk, 2 of them never joined",
    "5/1": "16 lines and 42 of 51 members off the trunk, 2 of them never joined",
}
# What the comment on a pull request says where main has no point to set the groups side by side.
NO_SIDES = (
    "No point of main at this chat-sim's measurement that can be read is among the base's hundred "
    "newest commits, so main's runs of the two groups are not set side by side."
)
HELD_SUITES = {
    "GOALS": HELD_GOALS,
    "RATES": {"pr": ("0.1", "1", "5"), "main": ("0", "0.1", "1", "5")},
    "SEEDS": {"pr": 2, "main": 20},
}
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


def with_groupv3(runs: Path, into: Path) -> Path:
    """A copy of `runs` at `into` that holds each run of the goal on GroupV2 again as the run of
    the goal on GroupV3 at its rate and seed, in an artifact of its own."""
    groupv3 = {
        HELD_GOALS["v2"].format(rate): HELD_GOALS["v3"].format(rate)
        for rate in HELD_SUITES["RATES"]["main"]
    }
    shutil.copytree(runs, into)
    for file in sorted([*runs.rglob("run.json"), *runs.rglob("unbuilt")]):
        number, name = file.parent.name, groupv3[file.parent.parent.name]
        copy = into / f"sim-revision-{name}-{number}" / name / number / file.name
        copy.parent.mkdir(parents=True)
        shutil.copyfile(file, copy)
    return into


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.expected = self.root / "expected"
        held = (
            "50-epochs-by-saro-waku-loss-1",
            "50-epochs-by-saro-waku-loss-1-groupv3",
            "grow-waku-loss-0.1",
            "grow-waku-loss-1",
            "grow-waku-loss-1-groupv3",
            "grow-waku-size-50",
        )
        for name in held:
            write(self.expected / f"{name}.json", [outcome(0), outcome(1), outcome(2)])


class Scenarios(Fixture):
    def test_pr_is_three_seeds_of_the_goal_on_each_group_at_each_of_its_three_lossy_rates(self):
        self.assertEqual(
            simulate.scenarios("pr", self.expected),
            [
                ("grow-waku-loss-0.5", 3),
                ("grow-waku-loss-1", 3),
                ("grow-waku-loss-5", 3),
                ("grow-waku-loss-0.5-groupv3", 3),
                ("grow-waku-loss-1-groupv3", 3),
                ("grow-waku-loss-5-groupv3", 3),
            ],
        )

    def test_main_is_twenty_seeds_of_the_goal_on_each_group_at_each_of_its_four_rates(self):
        self.assertEqual(
            simulate.scenarios("main", self.expected),
            [
                ("grow-waku-loss-0", 20),
                ("grow-waku-loss-0.5", 20),
                ("grow-waku-loss-1", 20),
                ("grow-waku-loss-5", 20),
                ("grow-waku-loss-0-groupv3", 20),
                ("grow-waku-loss-0.5-groupv3", 20),
                ("grow-waku-loss-1-groupv3", 20),
                ("grow-waku-loss-5-groupv3", 20),
            ],
        )

    def test_long_is_fifty_epochs_and_every_held_scenario_but_the_goals_as_held(self):
        self.assertEqual(
            simulate.scenarios("long", self.expected),
            [
                ("50-epochs", 1),
                ("50-epochs-waku", 1),
                ("50-epochs-by-saro-waku-loss-1", 3),
                ("50-epochs-by-saro-waku-loss-1-groupv3", 3),
                ("grow-waku-loss-0.1", 3),
                ("grow-waku-size-50", 3),
            ],
        )

    def test_the_goals_files_set_no_seed_so_that_seed_k_is_run_k_plus_1(self):
        for goal in simulate.GOALS.values():
            for rate in simulate.RATES["main"]:
                name = goal.format(rate)
                file = ROOT / "tools/sim/scenarios" / f"{name}.json"
                self.assertNotIn("seed", json.loads(file.read_text("utf-8")), name)

    def test_each_group_s_goal_creates_that_group_and_is_the_other_s_goal_but_for_it(self):
        def read(name: str) -> dict:
            return json.loads((ROOT / "tools/sim/scenarios" / f"{name}.json").read_text("utf-8"))

        for rate in simulate.RATES["main"]:
            with self.subTest(rate=rate):
                groupv2 = read(simulate.GOALS["v2"].format(rate))
                groupv3 = read(simulate.GOALS["v3"].format(rate))
                self.assertEqual((groupv2.get("group", "v2"), groupv3["group"]), ("v2", "v3"))
                self.assertEqual(groupv2, {k: v for k, v in groupv3.items() if k != "group"})


class Jobs(unittest.TestCase):
    def test_pr_is_a_job_a_run_at_the_revision_and_at_the_base(self):
        jobs = simulate.jobs("pr", REVISION, BASE, simulate.scenarios("pr", Path()))

        self.assertEqual(len(jobs), 36)
        self.assertEqual(
            {(job["side"], job["revision"]) for job in jobs[:18]}, {("revision", REVISION)}
        )
        self.assertEqual({(job["side"], job["revision"]) for job in jobs[18:]}, {("base", BASE)})
        self.assertEqual(
            [(job["scenario"], job["from"], job["runs"]) for job in jobs[:18]],
            [
                (goal.format(rate), first, 1)
                for goal in simulate.GOALS.values()
                for rate in ("0.5", "1", "5")
                for first in (1, 2, 3)
            ],
        )
        self.assertEqual(
            [job["name"] for job in jobs[:18]],
            [
                f"head 0123456: {group} {rate}% seed {seed}"
                for group in ("GroupV2", "GroupV3")
                for rate in ("0.5", "1", "5")
                for seed in (0, 1, 2)
            ],
        )
        self.assertEqual(jobs[33]["name"], "base 89abcde: GroupV3 5% seed 0")

    def test_main_is_two_jobs_of_ten_runs_a_group_s_rate_and_no_base(self):
        jobs = simulate.jobs("main", REVISION, "", simulate.scenarios("main", Path()))

        self.assertEqual(len(jobs), 16)
        self.assertEqual({job["side"] for job in jobs}, {"revision"})
        self.assertEqual(
            [(job["scenario"], job["from"], job["runs"]) for job in jobs],
            [
                (goal.format(rate), first, 10)
                for goal in simulate.GOALS.values()
                for rate in ("0", "0.5", "1", "5")
                for first in (1, 11)
            ],
        )
        self.assertEqual(
            [job["name"] for job in jobs[7:9]],
            ["head 0123456: GroupV2 5% seeds 10 to 19", "head 0123456: GroupV3 0% seeds 0 to 9"],
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
    them, of the suites as they ran the goal UPLOADED holds the runs of."""

    def setUp(self):
        super().setUp()
        held = mock.patch.multiple(simulate, **HELD_SUITES)
        held.start()
        self.addCleanup(held.stop)
        self.pages = self.root / "pages"
        # UPLOADED's runs on each group.
        self.uploaded = with_groupv3(UPLOADED, self.root / "uploaded")

    def published(self, runs: Optional[Path] = None, revision: str = BASE) -> List[str]:
        """What gh-pages lists under logos-chat/main/ once it holds main's point at `revision`
        and MEASUREMENT besides what it held, as pages.py point writes it of the runs under
        `runs`, else UPLOADED's on each group, a cell for each run main does."""
        runs = self.uploaded if runs is None else runs
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
            ("pr", ("--base", BASE, "--pr", "316"), 24),
            ("main", (), 16),
            ("long", ("--base", BASE), 12),
        ):
            with self.subTest(suite=suite):
                status, out, _ = self.plan(suite, "--revision", REVISION, *extra)
                self.assertEqual(status, 0)
                *handed, runs, scenarios, stood_in = out.splitlines()
                base = BASE if "--base" in extra else ""
                pr = "316" if "--pr" in extra else ""
                self.assertEqual(
                    handed,
                    [
                        f"suite={suite}",
                        f"revision={REVISION}",
                        f"base={base}",
                        f"pr={pr}",
                        "check_run=",
                    ],
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
        text = f"logos-chat/main/{BASE}/{MEASUREMENT}/{HELD_GOALS['v2'].format(1)}/1/run.txt"
        listed = self.listed(text, point(BASE), point(REVISION, EARLIER))

        planned = self.planned("pr", listed, "--base", BASE, "--pr", "316")

        self.assertEqual((planned["point"], planned["base"]), (point(BASE), BASE))
        self.assertEqual(len(planned["runs"]), 12)
        self.assertEqual(
            {(job["side"], job["revision"]) for job in planned["runs"]}, {("revision", REVISION)}
        )
        self.assertEqual(planned["warned"], "")

    def test_a_point_with_no_outcome_at_a_run_the_pr_asks_of_the_base_stands_in_for_nothing(self):
        scenario = HELD_GOALS["v2"].format(5)
        job = f"sim-revision-{scenario}-2"
        # The job of GroupV2 at 5%, seed 1, uploaded nothing, or could not build chat-sim.
        for unbuilt in (False, True):
            with self.subTest(unbuilt=unbuilt):
                runs = shutil.copytree(self.uploaded, self.root / f"runs-{unbuilt}")
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

                self.assertEqual((planned["point"], len(planned["runs"])), ("", 24))
                self.assertEqual({job["side"] for job in planned["runs"][12:]}, {"base"})
                self.assertEqual(
                    planned["warned"],
                    f"{self.NOT_STANDING}{point(BASE)} has no outcome at GroupV2 5% seed 1\n",
                )

    def test_a_point_with_no_outcome_at_a_group_s_runs_stands_in_for_nothing(self):
        listed = self.listed(*self.published(UPLOADED))

        planned = self.planned("pr", listed, "--base", BASE, "--pr", "316")

        self.assertEqual((planned["point"], len(planned["runs"])), ("", 24))
        missing = [f"GroupV3 {rate}% seed {seed}" for rate in ("0.1", "1", "5") for seed in (0, 1)]
        self.assertEqual(
            planned["warned"],
            f"{self.NOT_STANDING}{point(BASE)} has no outcome at {', '.join(missing)}\n",
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

                self.assertEqual((planned["point"], len(planned["runs"])), ("", 24))
                self.assertEqual(
                    planned["warned"], f"{self.NOT_STANDING}{point(BASE)} on gh-pages: {said}\n"
                )
        self.git("rm", "-q", point(BASE))
        self.committed()

        planned = self.planned("pr", listed, "--base", BASE)

        self.assertEqual((planned["point"], len(planned["runs"])), ("", 24))
        self.assertTrue(
            planned["warned"].startswith(f"{self.NOT_STANDING}{point(BASE)} on gh-pages: fatal: ")
        )

    def test_a_pr_whose_base_has_no_point_at_the_measurement_runs_at_the_base(self):
        for listed in (
            (point(BASE, EARLIER),),
            (point(REVISION),),
            (f"logos-chat/main/{BASE}/{MEASUREMENT}/{HELD_GOALS['v2'].format(1)}/1/run.txt",),
            (),
        ):
            with self.subTest(listed=listed):
                planned = self.planned("pr", self.listed(*listed), "--base", BASE)
                self.assertEqual((planned["point"], len(planned["runs"])), ("", 24))
                self.assertEqual({job["side"] for job in planned["runs"][12:]}, {"base"})

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

    def test_hands_on_the_check_run_of_a_pr_run_for_a_pull_request(self):
        status, out, _ = self.plan(
            "pr", "--revision", REVISION, "--base", BASE, "--pr", "316", "--check-run", "4242"
        )

        self.assertEqual(status, 0)
        self.assertIn("check_run=4242", out.splitlines())

    def test_refuses_a_check_run_that_is_no_id_or_not_of_a_pr_run_for_a_pull_request(self):
        pr = ("--revision", REVISION, "--pr", "316")
        for suite, extra, said in (
            ("pr", (*pr, "--check-run", "0"), "--check-run: '0' is not the id of a check run"),
            ("pr", (*pr, "--check-run", "42; rm"), "is not the id of a check run"),
            ("pr", ("--revision", REVISION, "--check-run", "42"), "given for pr with --pr"),
            ("main", (*pr, "--check-run", "42"), "--check-run: given for pr with --pr, and only"),
            ("long", (*pr, "--check-run", "42"), "--check-run: given for pr with --pr, and only"),
        ):
            with self.subTest(suite=suite, extra=extra):
                status, out, err = self.plan(suite, *extra)
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
    """The verdict of a matrix in a few words, from the counts of each group's cells."""

    @staticmethod
    def judged(verdict: str, group: str = "v2", **counted) -> dict:
        counts = dict.fromkeys(("better", "worse", "re-rolled", "below", "no-outcome"), 0)
        counts.update({"cells": 6, "compared": 6, **counted})
        return {"group": group, "verdict": verdict, "counts": counts}

    def title(
        self, verdict: str, base: Optional[dict] = None, spread: Optional[dict] = None, **counted
    ) -> str:
        """The title of a matrix of GroupV2's cells alone, of `verdict` and the counts given."""
        judged = self.judged(verdict, **counted)
        base = {"unbuilt": False, "runs": 6} if base is None else base
        matrix = {**judged, "groups": [judged], "base": base, "spread": spread}
        return simulate.title(matrix)

    def test_each_verdict_has_the_title_of_its_check(self):
        for verdict, counted, said in (
            ("same", {}, "GroupV2 same as its base, 6 of 6 runs"),
            ("same", {"compared": 5}, "GroupV2 same as its base, 5 of 6 runs"),
            ("better", {"better": 1}, "GroupV2 better in 1 of 6 runs"),
            ("worse", {"worse": 2}, "GroupV2 worse in 2 of 6 runs"),
            ("hint", {"worse": 1}, "GroupV2 worse in 1 of 6 runs, a hint"),
            ("mixed", {"better": 1, "worse": 1}, "GroupV2 mixed: 1 better, 1 worse of 6"),
            ("re-rolled", {"re-rolled": 6}, "GroupV2 the draws changed, no comparison"),
            ("unbuilt", {}, "does not build against this commit"),
            ("no-outcome", {"no-outcome": 1}, "GroupV2 no outcome in 1 of 6 runs"),
        ):
            with self.subTest(verdict=verdict, counted=counted):
                self.assertEqual(self.title(verdict, **counted), said)

    def test_each_group_s_title_follows_its_name_in_the_order_of_the_groups(self):
        groups = [self.judged("same"), self.judged("worse", "v3", worse=2, compared=6)]
        union = self.judged("worse", worse=2, cells=12, compared=12)
        base = {"unbuilt": False, "runs": 12}
        matrix = {**union, "groups": groups, "base": base, "spread": None}

        self.assertEqual(
            simulate.title(matrix),
            "GroupV2 same as its base, 6 of 6 runs; GroupV3 worse in 2 of 6 runs",
        )

    def test_re_rolled_beside_main_s_spread_says_how_many_fell_below_its_worst_seed(self):
        spread = {"logos_chat": BASE}
        for counted, said in (
            ({"re-rolled": 6, "below": 1}, "1 of 6 below main's worst seed"),
            ({"re-rolled": 2, "below": 0}, "0 of 6 below main's worst seed"),
        ):
            with self.subTest(counted=counted):
                self.assertEqual(
                    self.title("re-rolled", spread=spread, **counted),
                    f"GroupV2 the draws changed, no comparison; {said}",
                )
        self.assertEqual(
            self.title("hint", spread=spread, worse=1, below=1),
            "GroupV2 worse in 1 of 6 runs, a hint",
        )

    def test_no_base_says_why_there_is_no_comparison(self):
        no_base = {"unbuilt": False, "runs": 0}
        self.assertEqual(
            self.title("no-base", {"unbuilt": True, "runs": 0}),
            "does not build at the base, no comparison",
        )
        self.assertEqual(self.title("no-base", no_base), "no outcome at the base, no comparison")
        self.assertEqual(
            simulate.title({"verdict": "no-base", "counts": {}, "groups": [], "base": None}),
            "no base, no comparison",
        )


class Conclusion(unittest.TestCase):
    """The check's conclusion of a matrix's verdict."""

    def test_each_verdict_has_the_conclusion_of_its_check_and_its_own_once_required(self):
        table = (
            ("same", "neutral", "neutral"),
            ("better", "success", "success"),
            ("worse", "failure", "failure"),
            ("hint", "neutral", "neutral"),
            ("mixed", "neutral", "neutral"),
            ("re-rolled", "neutral", "neutral"),
            ("unbuilt", "neutral", "failure"),
            ("no-outcome", "neutral", "failure"),
            ("no-base", "neutral", "neutral"),
        )
        self.assertEqual({verdict for verdict, *_ in table}, set(simulate.CONCLUSIONS))
        for verdict, concluded, once_required in table:
            with self.subTest(verdict=verdict):
                self.assertEqual(simulate.conclusion(verdict, required=False), concluded)
                self.assertEqual(simulate.conclusion(verdict, required=True), once_required)

    def test_a_group_that_fails_fails_the_check_and_else_one_that_succeeds_succeeds_it(self):
        for verdicts, required, concluded in (
            (("better", "worse"), False, "failure"),
            (("worse", "better"), False, "failure"),
            (("better", "hint"), False, "success"),
            (("same", "better"), False, "success"),
            (("same", "mixed"), False, "neutral"),
            (("better", "no-outcome"), False, "success"),
            (("better", "no-outcome"), True, "failure"),
        ):
            with self.subTest(verdicts=verdicts, required=required):
                groups = [{"group": g, "verdict": v} for g, v in zip(("v2", "v3"), verdicts)]
                self.assertEqual(simulate.concluded({"groups": groups}, required), concluded)


class Running(unittest.TestCase):
    """The check's update while the runs go, from the run jobs the plan printed."""

    def running(self, asked: object, *extra: str) -> "tuple[int, str, str]":
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        file = Path(directory.name) / "asked.json"
        file.write_text(json.dumps(asked), encoding="utf-8")
        return run("running", "--asked", str(file), "--run-url", RUN_URL, *extra)

    def test_is_in_progress_with_the_run_as_its_details_and_the_head_s_runs_in_its_title(self):
        swept = simulate.scenarios("pr", Path())
        # The base's jobs, and none where main's point stands in for the base's runs.
        for run_at in (BASE, ""):
            with self.subTest(run_at=run_at):
                asked = simulate.jobs("pr", REVISION, run_at, swept)
                status, out, err = self.running(asked, "--revision", REVISION, "--base", BASE)

                self.assertEqual(status, 0, err)
                self.assertEqual(
                    json.loads(out),
                    {
                        "status": "in_progress",
                        "details_url": RUN_URL,
                        "output": {
                            "title": "18 runs in progress",
                            "summary": f"**18 runs in progress** at {commit(REVISION)} against "
                            f"its merge base {commit(BASE)} · [the run]({RUN_URL})",
                        },
                    },
                )

    def test_refuses_a_revision_not_a_commit_and_jobs_not_as_the_plan_prints_them(self):
        asked = simulate.jobs("pr", REVISION, BASE, simulate.scenarios("pr", Path()))
        for given, extra, said in (
            (asked, ("--revision", REVISION[:7]), "--revision: '0123456'"),
            (asked, ("--revision", REVISION, "--base", "main"), "--base: 'main'"),
            ({"runs": 6}, ("--revision", REVISION), "not the run jobs plan prints"),
            ([{"side": "head", "runs": 1}], ("--revision", REVISION), "not the run jobs"),
            ([{"side": "revision", "runs": "1"}], ("--revision", REVISION), "not the run jobs"),
        ):
            with self.subTest(extra=extra, given=given):
                status, out, err = self.running(given, *extra)
                self.assertEqual((status, out), (2, ""))
                self.assertIn(said, err)


class Drawn(unittest.TestCase):
    """The comment drawn from the matrix tools/sim matrix writes of the runs uploaded."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "expected").mkdir()

    def uploaded(self, name: str, both: bool = False, **edits: dict) -> Path:
        """A copy of UPLOADED, with `both` its runs on each group, with the fields `edits` gives a
        run changed, by its rate and seed, and `/v3` after them for GroupV3's run."""
        copy = self.root / name
        with_groupv3(UPLOADED, copy) if both else shutil.copytree(UPLOADED, copy)
        for cell, changed in edits.items():
            rate, seed, *group = cell.split("/")
            scenario = HELD_GOALS[group[0] if group else "v2"].format(rate)
            number = int(seed) + 1
            file = copy / f"sim-revision-{scenario}-{number}" / scenario / str(number) / "run.json"
            report = json.loads(file.read_text(encoding="utf-8"))
            report.update(changed)
            file.write_text(json.dumps(report), encoding="utf-8")
        return copy

    def unbuilt(self, side: Path, rate: str, seed: int) -> None:
        """The artifact of the job of a run at `rate` and `seed` as one that could not build."""
        scenario, number = HELD_GOALS["v2"].format(rate), seed + 1
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
        """The run jobs of pr, as the suite ran the goal UPLOADED holds the runs of."""
        with mock.patch.multiple(simulate, **HELD_SUITES):
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

    def check(self, matrix: Path, comment: str, *extra: str) -> dict:
        """The check's update `check` prints of `matrix`, and `comment` the comment on it."""
        written = self.root / "comment.md"
        written.write_text(comment, encoding="utf-8")
        given = ("--matrix", str(matrix), "--comment", str(written), "--run-url", RUN_URL)
        status, out, err = run("check", *given, *extra)
        self.assertEqual(status, 0, err)
        return json.loads(out)

    def rows(self, text: str) -> List[str]:
        return [line for line in text.splitlines() if line.startswith("| GroupV")]

    def sides(self, text: str) -> List[str]:
        """The rows of main's runs of the two groups side by side."""
        return [line for line in text.splitlines() if re.match(r"\| [0-9.]+% \| ", line)]

    def table_head(self, text: str) -> List[str]:
        """The header and the rule of the table of the cells."""
        lines = text.splitlines()
        at = next(number for number, line in enumerate(lines) if line.startswith("| Group |"))
        return lines[at : at + 2]

    def test_a_pr_two_cells_of_which_split_sooner_than_at_the_base_is_worse_in_2_of_6(self):
        head = self.uploaded(
            "head",
            **{"1/1": {"verdict": "split at epoch 5"}, "5/0": {"verdict": "split at epoch 4"}},
        )
        status, out, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", BASE)

        self.assertEqual(status, 0)
        first, blank, sides, gap, header, rule, *_ = out.splitlines()
        self.assertEqual(
            first,
            f"**chat-sim: GroupV2 worse in 2 of 6 runs** at {commit(REVISION)} against its merge "
            f"base {commit(BASE)} · [the run]({RUN_URL})",
        )
        self.assertEqual((blank, sides, gap), ("", NO_SIDES, ""))
        self.assertEqual(
            (header, rule), ("| Group | Loss | Seed 0 | Seed 1 |", "|---|---|---|---|")
        )
        self.assertEqual(
            self.rows(out),
            [
                f"| GroupV2 | 0.1% | 50 whole, same | split at 46, {OFF['0.1/1']}, same |",
                f"| GroupV2 | 1% | split at 18, {OFF['1/0']}, same "
                f"| split at 5, {OFF['1/1']}, was split at 9: worse |",
                f"| GroupV2 | 5% | split at 4, {OFF['5/0']}, was split at 8: worse "
                f"| split at 3, {OFF['5/1']}, same |",
            ],
        )
        self.assertIn(
            "\n\nA cell: how the run ended (whole: one group, every member that joined at its tip; "
            "behind: one group, some members behind; stalled: one group that stopped growing short "
            "of 50; split: it forked), at the epoch of its first fork, or else of the trunk (50 is "
            "the goal) · the lines and the members off the trunk at the end, of the members "
            "seated, where there are any (behind, on another line, or never joined), and how many "
            "of those never joined, where any did · where it is better or worse, what the base's "
            "run had of the first of these that moved · the change against the merge base at the "
            "same seed",
            out,
        )
        self.assertEqual((out.count("<details>"), out.count("<details open>")), (4, 2))
        self.assertIn(
            "\n<details open><summary>GroupV2 5% loss, seed 0: split at 4, "
            f"{OFF['5/0']}, was split at 8: worse</summary>",
            out,
        )
        fold = out[out.index("<details open><summary>GroupV2 1% loss, seed 1:") :]
        fold = fold[: fold.index("</details>")]
        self.assertEqual(
            fold.splitlines()[:4],
            [
                "<details open><summary>GroupV2 1% loss, seed 1: split at 5, "
                f"{OFF['1/1']}, was split at 9: worse</summary>",
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

    def test_a_pr_on_both_groups_says_each_group_s_verdict_and_rows_and_fails_on_either(self):
        head = self.uploaded(
            "head",
            both=True,
            **{
                "1/1/v3": {"verdict": "split at epoch 5"},
                "5/0/v3": {"verdict": "split at epoch 4"},
            },
        )
        matrix = self.matrix(head, self.uploaded("base", both=True), self.pr_jobs())
        _, out, _ = self.comment("pr", matrix, "--base", BASE)

        self.assertTrue(
            out.startswith(
                "**chat-sim: GroupV2 same as its base, 6 of 6 runs; GroupV3 worse in 2 of 6 "
                "runs** at "
            )
        )
        self.assertEqual(
            self.rows(out),
            [
                f"| GroupV2 | 0.1% | 50 whole, same | split at 46, {OFF['0.1/1']}, same |",
                f"| GroupV2 | 1% | split at 18, {OFF['1/0']}, same "
                f"| split at 9, {OFF['1/1']}, same |",
                f"| GroupV2 | 5% | split at 8, {OFF['5/0']}, same "
                f"| split at 3, {OFF['5/1']}, same |",
                f"| GroupV3 | 0.1% | 50 whole, same | split at 46, {OFF['0.1/1']}, same |",
                f"| GroupV3 | 1% | split at 18, {OFF['1/0']}, same "
                f"| split at 5, {OFF['1/1']}, was split at 9: worse |",
                f"| GroupV3 | 5% | split at 4, {OFF['5/0']}, was split at 8: worse "
                f"| split at 3, {OFF['5/1']}, same |",
            ],
        )
        self.assertEqual((out.count("<details>"), out.count("<details open>")), (10, 2))
        self.assertIn(
            "\n<details open><summary>GroupV3 5% loss, seed 0: split at 4, "
            f"{OFF['5/0']}, was split at 8: worse</summary>",
            out,
        )
        self.assertIn(
            "python3 tools/sim sweep "
            "tools/sim/scenarios/50-epochs-by-saro-waku-loss-1-groupv3.json --from 2 --runs 1 ",
            out,
        )
        checked = self.check(matrix, out)
        self.assertEqual(
            (checked["conclusion"], checked["output"]["title"]),
            ("failure", "GroupV2 same as its base, 6 of 6 runs; GroupV3 worse in 2 of 6 runs"),
        )

    def test_a_pr_against_main_s_point_at_its_base_names_it_and_shows_main_s_spread(self):
        head = self.uploaded(
            "head",
            **{"1/1": {"verdict": "split at epoch 5"}, "5/0": {"verdict": "split at epoch 4"}},
        )
        against_point = self.matrix(head, base_point=POINT, spread=POINT)
        _, out, _ = self.comment("pr", against_point, "--base", FA5282A)
        _, against_runs, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", FA5282A)

        first, blank, sides, *_ = out.splitlines()
        self.assertEqual(
            first,
            f"**chat-sim: GroupV2 worse in 2 of 6 runs** at {commit(REVISION)} against its merge "
            f"base {commit(FA5282A)}, from [main's run of it]({POINT_RUN}) · [the run]({RUN_URL})",
        )
        self.assertEqual(
            (blank, sides),
            ("", f"**GroupV2 · GroupV3**, main's 20 seeds of each rate at {commit(FA5282A)}"),
        )
        self.assertEqual(
            self.sides(out),
            [
                "| 0.1% | 15 of 20 · - | 18 to 46, median 36 · - | 1 · - | 2 of 51 · - | 0 · - |",
                "| 1% | 20 of 20 · - | 3 to 23, median 12.5 · - | 7 · - | 19 of 51 · - | 0.5 · - |",
                "| 5% | 19 of 20 · - | 3 to 18, median 6 · - | 7.5 · - | 32.5 of 44 · - | 2 · - |",
            ],
        )
        self.assertEqual(
            self.table_head(out),
            [
                f"| Group | Loss | Seed 0 | Seed 1 | main's 20 seeds at {commit(FA5282A)} |",
                "|---|---|---|---|---|",
            ],
        )
        self.assertEqual(
            self.rows(out),
            [
                f"| GroupV2 | 0.1% | 50 whole, same | split at 46, {OFF['0.1/1']}, same "
                "| 2 whole, 18 to 50 |",
                f"| GroupV2 | 1% | split at 18, {OFF['1/0']}, same "
                f"| split at 5, {OFF['1/1']}, was split at 9: worse | 0 whole, 3 to 23 |",
                f"| GroupV2 | 5% | split at 4, {OFF['5/0']}, was split at 8: worse "
                f"| split at 3, {OFF['5/1']}, same | 0 whole, 3 to 18 |",
            ],
        )
        self.assertEqual(
            [row.rsplit(" | ", 1)[0] + " |" for row in self.rows(out)], self.rows(against_runs)
        )
        folds = out.index("\n\nA cell: ")
        self.assertEqual(out[folds:], against_runs[against_runs.index("\n\nA cell: ") :])

    def test_main_s_runs_side_by_side_count_the_split_of_the_runs_with_an_outcome(self):
        matrix = self.matrix(self.uploaded("head"), base_point=POINT, spread=POINT)
        written = json.loads(matrix.read_text("utf-8"))
        rows = {one["rate"]: one for one in written["spread"]["summary"]}
        # Main's runs of 1% had no outcome, as where chat-sim did not build, and 15 of 5%'s none.
        none = {"lowest": None, "median": None, "highest": None}
        rows["1%"].update(
            {"no-outcome": 20, "whole": 0, "behind": 0, "split": 0, "stalled": 0, "epochs": none}
        )
        rows["1%"].update({"split_at": none, "lines_off_trunk": {"median": None}})
        rows["1%"].update({"members_off_trunk": {"median": None}, "seated": {"median": None}})
        rows["1%"].update({"never_joined": {"median": None}})
        rows["5%"].update({"no-outcome": 15, "split": 4, "behind": 1})
        matrix.write_text(json.dumps(written), encoding="utf-8")
        _, out, _ = self.comment("pr", matrix, "--base", FA5282A)

        self.assertEqual(
            self.sides(out)[1:],
            [
                "| 1% | - · - | - · - | - · - | - · - | - · - |",
                "| 5% | 4 of 5, 15 with no outcome · - | 3 to 18, median 6 · - | 7.5 · - "
                "| 32.5 of 44 · - | 2 · - |",
            ],
        )

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
                "**chat-sim: GroupV2 the draws changed, no comparison; 1 of 6 below main's worst "
                "seed** at "
            )
        )
        within = "re-rolled, within main's spread"
        self.assertEqual(
            self.rows(out),
            [
                f"| GroupV2 | 0.1% | 50 whole, {within} | split at 15, {OFF['0.1/1']}, re-rolled, "
                "below main's spread | 2 whole, 18 to 50 |",
                f"| GroupV2 | 1% | split at 11, {OFF['1/0']}, {within} "
                f"| split at 20, {OFF['1/1']}, {within} | 0 whole, 3 to 23 |",
                f"| GroupV2 | 5% | split at 6, {OFF['5/0']}, {within} "
                f"| split at 4, {OFF['5/1']}, {within} | 0 whole, 3 to 18 |",
            ],
        )
        self.assertIn(
            f"\n<details><summary>GroupV2 0.1% loss, seed 1: split at 15, {OFF['0.1/1']}, "
            "re-rolled, below main's spread</summary>\n",
            out,
        )

    def test_a_pr_whose_base_has_no_point_shows_the_spread_of_the_nearest_before_it(self):
        before = json.loads(POINT.read_text(encoding="utf-8"))
        before["logos_chat"] = CHAT_SIM
        spread = self.root / "spread.json"
        spread.write_text(json.dumps(before), encoding="utf-8")
        matrix = self.matrix(UPLOADED, UPLOADED, spread=spread)
        _, out, _ = self.comment("pr", matrix, "--base", BASE)

        first, _, sides, *_ = out.splitlines()
        self.assertEqual(
            first,
            f"**chat-sim: GroupV2 same as its base, 6 of 6 runs** at {commit(REVISION)} against "
            f"its merge base {commit(BASE)} · [the run]({RUN_URL})",
        )
        self.assertEqual(
            sides, f"**GroupV2 · GroupV3**, main's 20 seeds of each rate at {commit(CHAT_SIM)}"
        )
        self.assertEqual(
            self.table_head(out)[0],
            f"| Group | Loss | Seed 0 | Seed 1 | main's 20 seeds at {commit(CHAT_SIM)} |",
        )
        self.assertEqual(
            self.rows(out)[0],
            f"| GroupV2 | 0.1% | 50 whole, same | split at 46, {OFF['0.1/1']}, same "
            "| 2 whole, 18 to 50 |",
        )

    def test_without_main_s_point_the_column_is_left_out_and_the_groups_not_side_by_side(self):
        head = self.uploaded(
            "head", **{"0.1/1": {"conversation": "b19d47e0c5", "verdict": "split at epoch 15"}}
        )
        _, out, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", BASE)

        self.assertTrue(
            out.startswith("**chat-sim: GroupV2 the draws changed, no comparison** at ")
        )
        self.assertEqual(out.splitlines()[2], NO_SIDES)
        self.assertEqual(self.table_head(out)[0], "| Group | Loss | Seed 0 | Seed 1 |")
        self.assertEqual(
            self.rows(out)[0],
            f"| GroupV2 | 0.1% | 50 whole, same | split at 15, {OFF['0.1/1']}, re-rolled |",
        )
        self.assertEqual(self.sides(out), [])

    def test_a_pr_whose_cells_only_drew_other_ids_or_ran_otherwise_is_re_rolled(self):
        head = self.uploaded(
            "head", **{"5/1": {"conversation": "7c3e9a01b2"}, "1/0": {"virtual_s": 6077.0}}
        )
        _, out, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", BASE)

        self.assertTrue(
            out.startswith("**chat-sim: GroupV2 the draws changed, no comparison** at ")
        )
        self.assertEqual(
            self.rows(out)[1:],
            [
                f"| GroupV2 | 1% | split at 18, {OFF['1/0']}, changed "
                f"| split at 9, {OFF['1/1']}, same |",
                f"| GroupV2 | 5% | split at 8, {OFF['5/0']}, same "
                f"| split at 3, {OFF['5/1']}, re-rolled |",
            ],
        )

    def test_a_pr_cell_worse_at_the_same_epochs_names_what_moved(self):
        name = HELD_GOALS["v2"].format("0.1")
        file = UPLOADED / f"sim-revision-{name}-2" / name / "2" / "run.json"
        grow = json.loads(file.read_text(encoding="utf-8"))["grow"]
        grow["lines"][0]["members"].remove("raya")
        grow["lines"][1]["members"].append("raya")
        head = self.uploaded(
            "head", **{"0.1/0": {"verdict": "whole, members behind"}, "0.1/1": {"grow": grow}}
        )
        _, out, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", BASE)

        self.assertTrue(out.startswith("**chat-sim: GroupV2 worse in 2 of 6 runs** at "))
        self.assertEqual(
            self.rows(out)[0],
            "| GroupV2 | 0.1% | 50, behind, was 50 whole: worse "
            "| split at 46, 1 line and 3 of 51 members off the trunk, was 2 members off: worse |",
        )

    def test_a_pr_with_a_run_that_left_nothing_at_the_head_has_no_outcome_there(self):
        head = self.uploaded("head")
        shutil.rmtree(head / "sim-revision-50-epochs-by-saro-waku-loss-5-2")
        _, out, _ = self.comment("pr", self.matrix(head, UPLOADED), "--base", BASE)

        self.assertTrue(out.startswith("**chat-sim: GroupV2 no outcome in 1 of 6 runs** at "))
        self.assertEqual(
            self.rows(out)[2], f"| GroupV2 | 5% | split at 8, {OFF['5/0']}, same | no outcome |"
        )
        self.assertIn(f"- **head** {commit(REVISION)} · no outcome\n", out)

    def test_a_pr_run_whose_job_uploaded_nothing_at_either_side_has_no_outcome(self):
        head, base = self.uploaded("head", both=True), self.uploaded("base", both=True)
        for side in (head, base):
            shutil.rmtree(side / "sim-revision-50-epochs-by-saro-waku-loss-5-2")
        _, out, _ = self.comment("pr", self.matrix(head, base, self.pr_jobs()), "--base", BASE)

        self.assertTrue(
            out.startswith(
                "**chat-sim: GroupV2 no outcome in 1 of 6 runs; GroupV3 same as its base, 6 of 6 "
                "runs** at "
            )
        )
        self.assertEqual(
            self.rows(out)[2], f"| GroupV2 | 5% | split at 8, {OFF['5/0']}, same | no outcome |"
        )
        self.assertIn(f"- **base** {commit(BASE)} · no outcome\n", out)

    def test_a_pr_run_whose_job_could_not_build_at_the_head_has_no_outcome_there(self):
        head, base = self.uploaded("head", both=True), self.uploaded("base", both=True)
        self.unbuilt(head, "1", 1)
        _, out, _ = self.comment("pr", self.matrix(head, base, self.pr_jobs()), "--base", BASE)

        self.assertTrue(
            out.startswith(
                "**chat-sim: GroupV2 no outcome in 1 of 6 runs; GroupV3 same as its base, 6 of 6 "
                "runs** at "
            )
        )
        self.assertEqual(
            self.rows(out)[1],
            f"| GroupV2 | 1% | split at 18, {OFF['1/0']}, same | does not build |",
        )
        self.assertIn(f"- **head** {commit(REVISION)} · does not build\n", out)

    def test_a_pr_cell_the_base_could_not_build_says_so_and_the_others_are_compared(self):
        head = self.uploaded("head", both=True, **{"5/0": {"verdict": "split at epoch 4"}})
        base = self.uploaded("base", both=True)
        self.unbuilt(base, "1", 1)
        _, out, _ = self.comment("pr", self.matrix(head, base, self.pr_jobs()), "--base", BASE)

        self.assertTrue(
            out.startswith(
                "**chat-sim: GroupV2 worse in 1 of 6 runs, a hint; GroupV3 same as its base, 6 of "
                "6 runs** at "
            )
        )
        self.assertEqual(
            self.rows(out)[1:3],
            [
                f"| GroupV2 | 1% | split at 18, {OFF['1/0']}, same "
                f"| split at 9, {OFF['1/1']}, does not build at the base |",
                f"| GroupV2 | 5% | split at 4, {OFF['5/0']}, was split at 8: worse "
                f"| split at 3, {OFF['5/1']}, same |",
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

        first, blank, header, *_ = out.splitlines()
        self.assertEqual(
            first,
            f"**chat-sim: no base, no comparison** at {commit(REVISION)} · "
            f"[the report]({REPORT_URL}) · [the run]({RUN_URL})",
        )
        self.assertEqual((blank, header), ("", "| Group | Loss | Seed 0 | Seed 1 |"))
        self.assertNotIn(NO_SIDES, out)
        self.assertEqual(
            self.rows(out)[0], f"| GroupV2 | 0.1% | 50 whole | split at 46, {OFF['0.1/1']} |"
        )
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

    def test_main_is_a_row_a_group_s_rate_with_how_its_runs_ended_and_their_epochs(self):
        status, out, _ = self.comment("main", self.matrix(UPLOADED))

        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertEqual(
            lines[0], f"**chat-sim: 6 runs** at {commit(REVISION)} · [the run]({RUN_URL})"
        )
        self.assertEqual(
            lines[2],
            "| Group | Loss | Runs | Whole | Behind | Split | Stalled | Epochs without a fork "
            "| First fork | Lines off the trunk, median | Members off the trunk, median "
            "| Never joined, median |",
        )
        self.assertEqual(
            self.rows(out),
            [
                "| GroupV2 | 0.1% | 2 | 1 | 0 | 1 | 0 | 46 to 50, median 48 | 46 to 46, median 46 "
                "| 0.5 | 1 of 51 | 0 |",
                "| GroupV2 | 1% | 2 | 0 | 0 | 2 | 0 | 9 to 18, median 13.5 | 9 to 18, median 13.5 "
                "| 9 | 19.5 of 51 | 0.5 |",
                "| GroupV2 | 5% | 2 | 0 | 0 | 2 | 0 | 3 to 8, median 5.5 | 3 to 8, median 5.5 "
                "| 9 | 25.5 of 33 | 2 |",
            ],
        )
        self.assertTrue(
            lines[-1].startswith("A row: the runs of a group at a rate of loss, a seed each, ")
        )
        self.assertNotIn("<details>", out)

    def test_main_on_both_groups_is_a_row_a_group_s_rate_groupv2_s_first(self):
        _, out, _ = self.comment("main", self.matrix(self.uploaded("head", both=True)))

        self.assertTrue(out.startswith(f"**chat-sim: 12 runs** at {commit(REVISION)} · "))
        self.assertEqual(
            [row.split(" | ")[:3] for row in self.rows(out)],
            [
                ["| GroupV2", "0.1%", "2"],
                ["| GroupV2", "1%", "2"],
                ["| GroupV2", "5%", "2"],
                ["| GroupV3", "0.1%", "2"],
                ["| GroupV3", "1%", "2"],
                ["| GroupV3", "5%", "2"],
            ],
        )

    def test_main_with_a_job_that_uploaded_nothing_counts_its_runs_with_no_outcome(self):
        head = self.uploaded("head")
        shutil.rmtree(head / "sim-revision-50-epochs-by-saro-waku-loss-1-2")
        asked = [
            {"scenario": HELD_GOALS["v2"].format(rate), "from": 1, "runs": 2}
            for rate in ("0.1", "1", "5")
        ]
        _, out, _ = self.comment("main", self.matrix(head, None, asked))

        self.assertTrue(out.startswith("**chat-sim: no outcome in 1 of 6 runs** at "))
        self.assertEqual(
            self.rows(out)[1],
            "| GroupV2 | 1% | 2 | 0 | 0 | 1 | 0 | 18 to 18, median 18 | 18 to 18, median 18 | 7 "
            "| 18 of 51 | 1 |",
        )

    def test_a_pr_worse_in_2_of_6_fails_its_check_with_the_comment_and_the_report(self):
        head = self.uploaded(
            "head",
            **{"1/1": {"verdict": "split at epoch 5"}, "5/0": {"verdict": "split at epoch 4"}},
        )
        matrix = self.matrix(head, UPLOADED)
        given = ("--base", BASE, "--report-url", REPORT_URL)
        _, comment, _ = self.comment("pr", matrix, *given)

        self.assertEqual(
            self.check(matrix, comment, "--report-url", REPORT_URL),
            {
                "status": "completed",
                "conclusion": "failure",
                "details_url": REPORT_URL,
                "output": {"title": "GroupV2 worse in 2 of 6 runs", "summary": comment},
            },
        )
        self.assertEqual(self.check(matrix, comment)["details_url"], RUN_URL)

    def test_a_head_chat_sim_does_not_build_at_is_neutral_until_the_check_is_required(self):
        head = self.root / "head"
        self.unbuilt(head, "1", 0)
        matrix = self.matrix(head, UPLOADED)
        _, comment, _ = self.comment("pr", matrix, "--base", BASE)
        for extra, concluded in (
            ((), "neutral"),
            (("--required", "false"), "neutral"),
            (("--required", "true"), "failure"),
        ):
            with self.subTest(extra=extra):
                checked = self.check(matrix, comment, *extra)
                self.assertEqual(
                    (checked["conclusion"], checked["output"]["title"]),
                    (concluded, "does not build against this commit"),
                )

    def test_a_check_refuses_no_matrix_a_link_it_cannot_hold_and_a_required_not_true_or_false(self):
        matrix = self.matrix(UPLOADED, UPLOADED)
        unread, comment = self.root / "unread.json", self.root / "comment.md"
        unread.write_text("{}", encoding="utf-8")
        comment.write_text("**chat-sim: same as its base, 6 of 6 runs**\n", encoding="utf-8")
        for given, said in (
            ((unread,), f"--matrix: {unread} is not a matrix tools/sim matrix writes"),
            ((matrix, "--report-url", "javascript:alert(1)"), "--report-url: "),
            ((matrix, "--required", "True"), "--required: 'True' is neither true nor false"),
        ):
            with self.subTest(given=given):
                status, out, err = run(
                    "check",
                    "--matrix",
                    *map(str, given),
                    "--comment",
                    str(comment),
                    "--run-url",
                    RUN_URL,
                )
                self.assertEqual((status, out), (2, ""))
                self.assertIn(said, err)

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
            self.swept(self.runs, "grow-waku-loss-0.1") / "outcomes.json",
            [outcome(0), outcome(1, "split at epoch 9"), outcome(2, "split at epoch 4")],
        )
        self.compared("grow-waku-loss-0.1", "1 of 3 runs came out as expected: 2 worse")
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
            rows["grow-waku-loss-0.1"],
            [
                "3",
                "whole, split at epoch 9, split at epoch 4",
                "1 of 3 runs came out as expected: 2 worse",
            ],
        )
        self.assertEqual(rows["50-epochs"], ["1", "whole", "no outcome at the base"])
        self.assertIn(f"chat-sim `fedcba9` · [the run]({RUN_URL})", out)

    def test_without_a_base_holds_to_what_is_expected_and_says_where_nothing_is(self):
        self.compared("grow-waku-size-50", "3 of 3 runs came out as expected")
        status, out, _ = self.comment()
        self.assertEqual(status, 0)
        self.assertIn("against what chat-sim holds in `tools/sim/expected`", out)
        self.assertIn("| Scenario | Runs | Came to | Against what is held |", out)
        rows = self.rows(out)
        self.assertEqual(rows["50-epochs"], ["1", "whole", "nothing held"])
        self.assertEqual(
            rows["grow-waku-size-50"],
            ["3", "whole, whole, whole", "3 of 3 runs came out as expected"],
        )

    def test_a_scenario_with_no_outcome_says_so(self):
        (self.swept(self.runs, "50-epochs") / "outcomes.json").unlink()
        write(
            self.swept(self.runs, "grow-waku-loss-0.1") / "outcomes.json",
            [outcome(0), outcome(1, None)],
        )
        _, out, _ = self.comment(*self.with_base)
        rows = self.rows(out)
        self.assertEqual(rows["50-epochs"], ["1", "no outcome", "-"])
        self.assertEqual(rows["grow-waku-loss-0.1"][1], "whole, no verdict")

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

    def test_links_the_long_run_s_page_and_says_the_build_s_errors_are_on_it(self):
        report = f"https://logos-messaging.github.io/chat-sim/logos-chat/long/{REVISION}/{BASE}/"
        foot = f"chat-sim `fedcba9` · [the report]({report}) · [the run]({RUN_URL})\n"
        _, out, _ = self.comment("--report-url", report)
        self.assertIn(f"\n\n{foot}", out)
        self.unbuilt(self.runs)
        _, out, _ = self.comment("--report-url", report)
        self.assertIn(
            "\n\nchat-sim does not build at this revision, so no scenario ran: the build's "
            "errors are on the report page.\n\n",
            out,
        )
        self.assertTrue(out.endswith(foot))

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
        compared = self.compared("grow-waku-loss-0.1", "3 of 3 runs came out as expected")
        _, out, _ = self.comment()
        self.assertIn(
            "<details><summary><code>grow-waku-loss-0.1</code></summary>\n\n```\n"
            f"{compared}```\n\n</details>\n",
            out,
        )
        self.assertEqual(out.count("<details>"), 1)


class Post(unittest.TestCase):
    """Where the comment goes on a logos-chat pull request, from its comments as the API lists
    them."""

    LOGIN = "chat-sim[bot]"
    BODY = "**chat-sim: same as its base, 6 of 6 runs**\n"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "comment.md").write_text(self.BODY, encoding="utf-8")

    def post(self, pages: object) -> "tuple[int, str, str]":
        """What `post` says of the pull request's comments `pages`, a list of pages."""
        (self.root / "comments.json").write_text(json.dumps(pages), encoding="utf-8")
        return run(
            "post",
            "--comments",
            str(self.root / "comments.json"),
            "--login",
            self.LOGIN,
            "--pr",
            "316",
            "--comment",
            str(self.root / "comment.md"),
            "--out",
            str(self.root / "posted.json"),
        )

    def by(self, login: str, number: int) -> dict:
        return {"id": number, "user": {"login": login}, "body": f"a comment of {login}"}

    def test_edits_the_app_s_oldest_comment_on_whichever_page_it_is(self):
        pages = [[self.by("saro", 3), self.by("chat-sim", 5)], [self.by(self.LOGIN, 7)]]
        pages[1].append(self.by(self.LOGIN, 9))

        status, out, _ = self.post(pages)

        self.assertEqual(
            (status, out),
            (0, "method=PATCH\nendpoint=repos/logos-messaging/logos-chat/issues/comments/7\n"),
        )
        posted = json.loads((self.root / "posted.json").read_text(encoding="utf-8"))
        self.assertEqual(posted, {"body": self.BODY})

    def test_writes_a_new_comment_where_the_app_has_none(self):
        # The user of a comment of an account since deleted can be null.
        others = [self.by("saro", 3), self.by("chat-sim", 5), {"id": 4, "user": None}]
        for pages in ([[]], [others]):
            with self.subTest(pages=pages):
                status, out, _ = self.post(pages)

                self.assertEqual(
                    (status, out),
                    (
                        0,
                        "method=POST\n"
                        "endpoint=repos/logos-messaging/logos-chat/issues/316/comments\n",
                    ),
                )
                posted = json.loads((self.root / "posted.json").read_text(encoding="utf-8"))
                self.assertEqual(posted, {"body": self.BODY})

    def test_refuses_what_is_not_pages_of_comments_each_with_its_id(self):
        for pages in (
            {"message": "Not Found"},
            [self.by(self.LOGIN, 7)],
            [[{"id": "7"}]],
            [[{}]],
            [[{"id": 7, "user": self.LOGIN}]],
        ):
            with self.subTest(pages=pages):
                status, out, err = self.post(pages)
                self.assertEqual((status, out), (2, ""))
                self.assertIn("not pages of comments, each with its id", err)


class Workflow(unittest.TestCase):
    """simulate.yml as it is written, a job by its name."""

    def setUp(self):
        workflow = (ROOT / ".github" / "workflows" / "simulate.yml").read_text(encoding="utf-8")
        parts = re.split(r"(?m)^  (\w+):\n", workflow.split("\njobs:\n", 1)[1])
        self.jobs = dict(zip(parts[1::2], parts[2::2]))

    def test_only_the_plan_and_comment_jobs_mint_a_token_and_each_keeps_it_to_its_steps(self):
        self.assertEqual(list(self.jobs), ["plan", "run", "comment", "publish"])
        minting = [name for name, job in self.jobs.items() if "create-github-app-token@" in job]
        keyed = [name for name, job in self.jobs.items() if "secrets." in job]
        self.assertEqual((minting, keyed), (["plan", "comment"], ["plan", "comment"]))
        for name in minting:
            with self.subTest(job=name):
                used = {
                    line.strip()
                    for line in self.jobs[name].splitlines()
                    if "steps.token.outputs.token" in line
                }
                self.assertEqual(used, {"GH_TOKEN: ${{ steps.token.outputs.token }}"})


if __name__ == "__main__":
    unittest.main()
