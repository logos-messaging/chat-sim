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
from test_pages import edited

REVISION = "0123456789abcdef0123456789abcdef01234567"
BASE = "89abcdef0123456789abcdef0123456789abcdef"
CHAT_SIM = "fedcba9876543210fedcba9876543210fedcba98"
RUN_URL = "https://github.com/logos-messaging/chat-sim/actions/runs/42"
REPORT_URL = "https://logos-messaging.github.io/chat-sim/logos-chat/pr-316/"
# What the comment on a pull request links where REPORT_URL is its report's page: the trees of its
# runs there, the page, the graph over main above it, and the run.
LINKS = (
    f"[The trees]({REPORT_URL}#runs) · [the report]({REPORT_URL}), kept while the pull request is "
    "open · [the graph over main](https://logos-messaging.github.io/chat-sim/logos-chat/) · "
    f"[the run]({RUN_URL})"
)
# Where a pull request's pictures are read once they are pushed: the directory of its run on
# gh-pages, at the commit pushed.
PICTURES = (
    f"https://raw.githubusercontent.com/logos-messaging/chat-sim/{CHAT_SIM}/logos-chat/pr-316/"
)
ROOT = Path(__file__).resolve().parents[2]
# What the GroupV2 jobs of HELD_SUITES' pr suite uploaded at a revision, seeds 0 and 1 of its three
# rates, cut to what tools/sim matrix reads: 0.1% whole and split at epoch 46, 1% split at 18 and
# 9, 5% split at 8 and 3.
UPLOADED = ROOT / "tools" / "sim" / "tests" / "fixtures" / "matrix"
# The scenario UPLOADED holds runs of, a group of 51 that saro grows toward epoch 50, by the
# group's name in a scenario, and the rates and seeds the suites ran of it.
GROW_1_ADDER = {
    "v2": "grow-1-adder-groupv2-waku-loss-{}pct",
    "v3": "grow-1-adder-groupv3-waku-loss-{}pct",
}
# Two of UPLOADED's runs as a head that split sooner, by the run's rate and seed: 1% seed 1 at
# epoch 5, not 9, and 5% seed 0 at 4, not 8.
SOONER = {"1/1": {"first_fork": 5}, "5/0": {"first_fork": 4}}
HELD_SUITES = {
    "GROW_3_ADDERS": GROW_1_ADDER,
    "RATES": {"pr": {"0.1": 2, "1": 2, "5": 2}, "main": {"0": 20, "0.1": 20, "1": 20, "5": 20}},
}


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


def with_groupv3(runs: Path, into: Path) -> Path:
    """A copy of `runs` at `into` that holds each run of the scenario on GroupV2 again as its run
    on GroupV3 at its rate and seed, in an artifact of its own."""
    groupv3 = {
        GROW_1_ADDER["v2"].format(rate): GROW_1_ADDER["v3"].format(rate)
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
            "grow-1-adder-groupv2-waku-loss-1pct",
            "grow-1-adder-groupv3-waku-loss-1pct",
            "grow-3-adders-groupv2-waku-loss-0.1pct",
            "grow-3-adders-groupv2-waku-loss-1pct",
            "grow-3-adders-groupv3-waku-loss-1pct",
            "grow-3-adders-size-50-groupv2-waku-loss-0pct",
        )
        for name in held:
            write(self.expected / f"{name}.json", [outcome(0), outcome(1), outcome(2)])


class Scenarios(Fixture):
    def test_pr_is_three_seeds_of_grow_3_adders_on_each_group_at_each_of_its_lossy_rates(self):
        self.assertEqual(
            simulate.scenarios("pr", self.expected),
            [
                ("grow-3-adders-groupv2-waku-loss-0.5pct", 3),
                ("grow-3-adders-groupv2-waku-loss-1pct", 3),
                ("grow-3-adders-groupv2-waku-loss-5pct", 3),
                ("grow-3-adders-groupv3-waku-loss-0.5pct", 3),
                ("grow-3-adders-groupv3-waku-loss-1pct", 3),
                ("grow-3-adders-groupv3-waku-loss-5pct", 3),
            ],
        )

    def test_main_is_three_seeds_of_grow_3_adders_on_each_group_at_no_loss_and_twenty_at_a_loss(
        self,
    ):
        self.assertEqual(
            simulate.scenarios("main", self.expected),
            [
                ("grow-3-adders-groupv2-waku-loss-0pct", 3),
                ("grow-3-adders-groupv2-waku-loss-0.5pct", 20),
                ("grow-3-adders-groupv2-waku-loss-1pct", 20),
                ("grow-3-adders-groupv2-waku-loss-5pct", 20),
                ("grow-3-adders-groupv3-waku-loss-0pct", 3),
                ("grow-3-adders-groupv3-waku-loss-0.5pct", 20),
                ("grow-3-adders-groupv3-waku-loss-1pct", 20),
                ("grow-3-adders-groupv3-waku-loss-5pct", 20),
            ],
        )

    def test_long_is_seat_50_post_and_every_held_scenario_but_main_s_as_held(self):
        self.assertEqual(
            simulate.scenarios("long", self.expected),
            [
                ("seat-50-post-groupv2", 1),
                ("seat-50-post-groupv2-waku-loss-0pct", 1),
                ("grow-1-adder-groupv2-waku-loss-1pct", 3),
                ("grow-1-adder-groupv3-waku-loss-1pct", 3),
                ("grow-3-adders-groupv2-waku-loss-0.1pct", 3),
                ("grow-3-adders-size-50-groupv2-waku-loss-0pct", 3),
            ],
        )

    def test_long_lists_a_scenario_s_rates_from_the_lowest(self):
        for rate in ("0", "0.1", "5"):
            write(self.expected / f"grow-1-adder-groupv2-waku-loss-{rate}pct.json", [outcome(0)])
        names = [name for name, _ in simulate.scenarios("long", self.expected)]
        self.assertEqual(
            [name for name in names if name.startswith("grow-1-adder-groupv2")],
            [f"grow-1-adder-groupv2-waku-loss-{rate}pct" for rate in ("0", "0.1", "1", "5")],
        )

    def test_the_files_of_grow_3_adders_set_no_seed_so_that_seed_k_is_run_k_plus_1(self):
        for grow in simulate.GROW_3_ADDERS.values():
            for rate in simulate.RATES["main"]:
                name = grow.format(rate)
                file = ROOT / "tools/sim/scenarios" / f"{name}.json"
                self.assertNotIn("seed", json.loads(file.read_text("utf-8")), name)

    def test_each_group_s_grow_3_adders_creates_that_group_and_is_the_other_s_but_for_it(self):
        def read(name: str) -> dict:
            return json.loads((ROOT / "tools/sim/scenarios" / f"{name}.json").read_text("utf-8"))

        for rate in simulate.RATES["main"]:
            with self.subTest(rate=rate):
                groupv2 = read(simulate.GROW_3_ADDERS["v2"].format(rate))
                groupv3 = read(simulate.GROW_3_ADDERS["v3"].format(rate))
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
                (grow.format(rate), first, 1)
                for grow in simulate.GROW_3_ADDERS.values()
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

    def test_main_is_a_job_of_a_group_s_three_runs_at_no_loss_and_two_of_ten_a_loss_and_no_base(
        self,
    ):
        jobs = simulate.jobs("main", REVISION, "", simulate.scenarios("main", Path()))

        self.assertEqual(len(jobs), 14)
        self.assertEqual({job["side"] for job in jobs}, {"revision"})
        self.assertEqual(
            [(job["scenario"], job["from"], job["runs"]) for job in jobs],
            [
                (grow.format(rate), first, min(10, seeds - first + 1))
                for grow in simulate.GROW_3_ADDERS.values()
                for rate, seeds in (("0", 3), ("0.5", 20), ("1", 20), ("5", 20))
                for first in range(1, seeds + 1, 10)
            ],
        )
        self.assertEqual(
            [job["name"] for job in jobs[6:8]],
            ["head 0123456: GroupV2 5% seeds 10 to 19", "head 0123456: GroupV3 0% seeds 0 to 2"],
        )

    def test_long_is_a_job_a_scenario_a_side_that_does_its_runs_as_held(self):
        swept = [("seat-50-post-groupv2", 1), ("grow-3-adders-groupv2-waku-loss-1pct", 3)]
        jobs = simulate.jobs("long", REVISION, BASE, swept)

        self.assertEqual(
            [(job["side"], job["scenario"], job["from"], job["runs"]) for job in jobs],
            [
                ("revision", "seat-50-post-groupv2", 1, 1),
                ("revision", "grow-3-adders-groupv2-waku-loss-1pct", 1, 3),
                ("base", "seat-50-post-groupv2", 1, 1),
                ("base", "grow-3-adders-groupv2-waku-loss-1pct", 1, 3),
            ],
        )
        self.assertEqual(jobs[1]["name"], "head 0123456: grow-3-adders-groupv2-waku-loss-1pct")
        self.assertEqual(jobs[2]["name"], "base 89abcde: seat-50-post-groupv2")


class Widen(unittest.TestCase):
    """The run jobs of the rest of the seeds of each group of a pull request whose runs moved."""

    @staticmethod
    def matrix(*cells: "tuple[str, Optional[str]]") -> dict:
        """A matrix of cells each of its group and its change, and what else a matrix holds."""
        matrix = dict.fromkeys(simulate.MATRIX)
        matrix["cells"] = [{"group": group, "change": change} for group, change in cells]
        return matrix

    def widen(self, matrix: object, asked: object, *extra: str) -> "tuple[int, str, str]":
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        given = []
        for name, value in (("--matrix", matrix), ("--asked", asked)):
            file = Path(directory.name) / f"{name[2:]}.json"
            file.write_text(json.dumps(value), encoding="utf-8")
            given += [name, str(file)]
        return run("widen", *given, *extra)

    def test_a_group_a_run_of_which_moved_runs_seeds_3_to_19_of_each_rate_six_a_job_at_both(self):
        moved = self.matrix(("v2", "same"), ("v2", None), ("v3", "same"), ("v3", "moved"))
        jobs = simulate.widen(moved, REVISION, BASE)

        self.assertEqual(len(jobs), 18)
        self.assertEqual(
            {(job["side"], job["revision"]) for job in jobs[:9]}, {("revision", REVISION)}
        )
        self.assertEqual({(job["side"], job["revision"]) for job in jobs[9:]}, {("base", BASE)})
        self.assertEqual(
            [(job["scenario"], job["from"], job["runs"]) for job in jobs[:9]],
            [
                (simulate.GROW_3_ADDERS["v3"].format(rate), first, runs)
                for rate in ("0.5", "1", "5")
                for first, runs in ((4, 6), (10, 6), (16, 5))
            ],
        )
        self.assertEqual(
            [job["name"] for job in jobs[:3]],
            [
                "head 0123456: GroupV3 0.5% seeds 3 to 8",
                "head 0123456: GroupV3 0.5% seeds 9 to 14",
                "head 0123456: GroupV3 0.5% seeds 15 to 19",
            ],
        )
        self.assertEqual(jobs[17]["name"], "base 89abcde: GroupV3 5% seeds 15 to 19")

    def test_both_groups_moving_widens_each_groupv2_first(self):
        jobs = simulate.widen(self.matrix(("v3", "moved"), ("v2", "moved")), REVISION, BASE)

        self.assertEqual(len(jobs), 36)
        self.assertEqual(
            [job["name"] for job in jobs[8:10]],
            ["head 0123456: GroupV2 5% seeds 15 to 19", "head 0123456: GroupV3 0.5% seeds 3 to 8"],
        )

    def test_nothing_widens_where_no_cell_has_an_outcome_at_both_sides_that_differs(self):
        for cells in ((), (("v2", "same"), ("v3", "same")), (("v2", None), ("v3", None))):
            with self.subTest(cells=cells):
                self.assertEqual(simulate.widen(self.matrix(*cells), REVISION, BASE), [])

    def test_each_job_uploads_under_a_name_none_of_the_first_seeds_jobs_has(self):
        first = simulate.jobs("pr", REVISION, BASE, simulate.scenarios("pr", Path()))
        more = simulate.widen(self.matrix(("v2", "moved"), ("v3", "moved")), REVISION, BASE)

        def named(jobs: Sequence[dict]) -> set:
            return {(job["side"], job["scenario"], job["from"]) for job in jobs}

        self.assertEqual(len(named(first + more)), len(first + more))

    def test_prints_the_jobs_that_follow_and_every_job_for_github_output(self):
        asked = simulate.jobs("pr", REVISION, BASE, simulate.scenarios("pr", Path()))
        given = ("--revision", REVISION, "--base", BASE)
        for cells in ((("v3", "moved"),), (("v3", "same"),)):
            with self.subTest(cells=cells):
                matrix = self.matrix(*cells)
                status, out, err = self.widen(matrix, asked, *given)
                self.assertEqual(status, 0, err)
                more = simulate.widen(matrix, REVISION, BASE)
                self.assertEqual(
                    out.splitlines(),
                    [f"more={json.dumps(more)}", f"asked={json.dumps(asked + more)}"],
                )

    def test_refuses_a_revision_not_a_commit_jobs_not_as_the_plan_prints_them_and_no_matrix(self):
        asked = simulate.jobs("pr", REVISION, BASE, simulate.scenarios("pr", Path()))
        moved = self.matrix(("v3", "moved"))
        for matrix, given, extra, said in (
            (moved, asked, ("--revision", REVISION[:7], "--base", BASE), "--revision: '0123456'"),
            (moved, asked, ("--revision", REVISION, "--base", "main"), "--base: 'main'"),
            (moved, {"runs": 6}, ("--revision", REVISION, "--base", BASE), "not the run jobs"),
            ({"cells": []}, asked, ("--revision", REVISION, "--base", BASE), "not a matrix"),
        ):
            with self.subTest(said=said):
                status, out, err = self.widen(matrix, given, *extra)
                self.assertEqual((status, out), (2, ""))
                self.assertIn(said, err)


class Plan(Fixture):
    def setUp(self):
        super().setUp()
        held = mock.patch.multiple(simulate, **HELD_SUITES)
        held.start()
        self.addCleanup(held.stop)

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
                *handed, runs, scenarios = out.splitlines()
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


class History(unittest.TestCase):
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


class Measurement(History):
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


class Changed(History):
    """Whether HEAD changes a path chat-sim's CI holds its runs to, since a commit."""

    def test_a_commit_to_what_a_run_measures_since_the_base_changes_it(self):
        self.assertTrue(simulate.changed(self.tree, self.shas[1]))
        status, out, _ = run("changed", "--since", self.shas[1], "--tree", str(self.tree))
        self.assertEqual((status, out), (0, "changed=true\n"))

    def test_commits_to_the_guide_alone_change_nothing(self):
        since = self.shas[2]
        self.change("guide/d")
        self.change("guide/e")

        self.assertFalse(simulate.changed(self.tree, since))
        status, out, _ = run("changed", "--since", since, "--tree", str(self.tree))
        self.assertEqual((status, out), (0, "changed=false\n"))

    def test_a_commit_to_what_is_expected_or_to_ci_yml_changes_it(self):
        for path in ("tools/sim/expected/x.json", ".github/workflows/ci.yml"):
            with self.subTest(path=path):
                since = self.git("rev-parse", "HEAD")
                self.change(path)
                self.assertTrue(simulate.changed(self.tree, since))

    def test_a_commit_to_another_workflow_changes_nothing(self):
        since = self.shas[2]
        self.change(".github/workflows/simulate.yml")
        self.assertFalse(simulate.changed(self.tree, since))

    def test_no_commit_to_compare_from_changes_it(self):
        for since in ("", "0" * 40, REVISION):
            with self.subTest(since=since):
                self.assertTrue(simulate.changed(self.tree, since))
                status, out, _ = run("changed", "--since", since, "--tree", str(self.tree))
                self.assertEqual((status, out), (0, "changed=true\n"))

    def test_the_base_moving_what_a_run_measures_after_the_branch_left_it_changes_nothing(self):
        self.git("checkout", "-q", "-b", "base")
        self.change("crates/harness/d")
        base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-")
        self.change("guide/e")

        self.assertFalse(simulate.changed(self.tree, base))

    def test_a_since_that_is_not_a_commit_s_digits_is_refused(self):
        for since in ("master", self.shas[1][:7]):
            with self.subTest(since=since):
                status, out, err = run("changed", "--since", since, "--tree", str(self.tree))
                self.assertEqual((status, out), (2, ""))
                self.assertIn("not a commit's 40 hex digits", err)


class Title(unittest.TestCase):
    """The verdict of a matrix in a few words, from the counts and the results of each group's
    cells."""

    @staticmethod
    def judged(verdict: Optional[str], group: str = "v2", results=(), **counted) -> dict:
        counts = {"cells": 6, "compared": 6, "same": 6, "moved": 0, "no-outcome": 0, **counted}
        return {"group": group, "verdict": verdict, "counts": counts, "results": list(results)}

    @staticmethod
    def result(rate: str, said: str, later: int, earlier: int, p: float) -> dict:
        return {"rate": rate, "result": said, "later": later, "earlier": earlier, "p": p}

    def title(
        self, verdict: str, base: Optional[dict] = None, cells=(), results=(), **counted
    ) -> str:
        """The title of a matrix of GroupV2's cells alone, of `verdict`, the results and the
        counts given."""
        judged = self.judged(verdict, results=results, **counted)
        base = {"unbuilt": 0, "runs": 6} if base is None else base
        matrix = {**judged, "groups": [judged], "base": base, "cells": list(cells)}
        return simulate.title(matrix)

    def test_each_verdict_has_the_title_of_its_check(self):
        earlier = [
            self.result("0.5%", "within", 8, 5, 595 / 1024),
            self.result("1%", "earlier", 2, 15, 308 / 2**17),
            self.result("5%", "earlier", 1, 16, 36 / 2**17),
        ]
        later = [self.result("5%", "later", 17, 3, 2702 / 2**20)]
        for verdict, results, counted, said in (
            ("same", (), {}, "GroupV2 nothing moved in 6 runs"),
            ("within", (), {"moved": 2}, "GroupV2 moved, within chance"),
            ("earlier", earlier, {}, "GroupV2 forks earlier at 1%: 2 later, 15 earlier (p 0.0023)"),
            ("later", later, {}, "GroupV2 forks later at 5%: 17 later, 3 earlier (p 0.0026)"),
            ("unbuilt", (), {}, "does not build against this commit"),
            ("no-outcome", (), {"no-outcome": 1}, "GroupV2 no outcome in 1 of 6 runs"),
        ):
            with self.subTest(verdict=verdict):
                self.assertEqual(self.title(verdict, results=results, **counted), said)

    def test_a_p_below_a_thousandth_is_said_as_that(self):
        results = [self.result("1%", "earlier", 0, 20, 2 / 2**20)]

        self.assertEqual(
            self.title("earlier", results=results),
            "GroupV2 forks earlier at 1%: 0 later, 20 earlier (p < 0.001)",
        )

    def test_each_group_s_title_follows_its_name_in_the_order_of_the_groups(self):
        results = [self.result("1%", "earlier", 0, 8, 0.0078125)]
        groups = [self.judged("same"), self.judged("earlier", "v3", results)]
        union = self.judged("earlier", cells=12, compared=12)
        base = {"unbuilt": 0, "runs": 12}
        matrix = {**union, "groups": groups, "base": base, "cells": []}

        self.assertEqual(
            simulate.title(matrix),
            "GroupV2 nothing moved in 6 runs; GroupV3 forks earlier at 1%: 0 later, 8 earlier "
            "(p 0.0078)",
        )

    def test_no_base_says_why_there_is_no_comparison(self):
        def cells(group: str, unbuilt: bool) -> list:
            return [{"group": group, "unbuilt": {"head": False, "base": unbuilt}}]

        nothing = {"compared": 0, "same": 0}
        self.assertEqual(
            self.title("no-base", {"unbuilt": 6, "runs": 0}, cells("v2", True), **nothing),
            "does not build at the base, no comparison",
        )
        self.assertEqual(
            self.title("no-base", {"unbuilt": 0, "runs": 0}, cells("v2", False), **nothing),
            "no outcome at the base, no comparison",
        )
        self.assertEqual(
            simulate.title(
                {"verdict": None, "counts": {"compared": 0}, "groups": [], "base": None}
            ),
            "no base, no comparison",
        )
        groups = [self.judged("same"), self.judged("no-base", "v3", compared=4, same=4)]
        matrix = {
            **self.judged("no-base", cells=12, compared=10, same=10),
            "groups": groups,
            "base": {"unbuilt": 2, "runs": 10},
            "cells": cells("v2", False) + cells("v3", True),
        }
        self.assertEqual(
            simulate.title(matrix),
            "GroupV2 nothing moved in 6 runs; GroupV3 does not build at the base, no comparison",
        )


class Told(unittest.TestCase):
    """What a group's line says of one of its scenarios, by its rate."""

    def test_a_rate_that_did_not_move_says_its_seeds_and_one_that_did_its_sign_test(self):
        nothing = {"scenario": "a", "rate": "0.5%", "result": "same", "seeds": [0, 1, 2]}
        tested = {"later": 8, "earlier": 5, "zero": 7, "p": 595 / 1024}
        moved = {"scenario": "b", "rate": "1%", "result": "within", "seeds": [0], **tested}
        none = {"cells": []}

        self.assertEqual(simulate.told(none, nothing), "0.5%: nothing moved in seeds 0 to 2")
        self.assertEqual(simulate.told(none, moved), "1%: 8 later, 5 earlier, 7 tied (p 0.58)")

    def test_a_rate_says_how_many_runs_the_base_gave_no_outcome_in(self):
        nothing = {"scenario": "a", "rate": "0.5%", "result": "same", "seeds": [0, 1, 2]}
        cell = {"scenario": "a", "base": {}, "unbuilt": {"head": False, "base": False}}
        lost = dict(cell, base=None)
        for cells, said in (
            ([cell, lost, cell], "no outcome at the base in 1 of 3 runs"),
            (
                [cell, lost, dict(lost, unbuilt={"head": False, "base": True})],
                "does not build at the base in 2 of 3 runs",
            ),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    simulate.told({"cells": cells}, nothing),
                    f"0.5%: nothing moved in seeds 0 to 2, {said}",
                )


class Conclusion(unittest.TestCase):
    """The check's conclusion of a matrix's verdict."""

    def test_each_verdict_has_the_conclusion_of_its_check_and_its_own_once_required(self):
        table = (
            ("earlier", "failure", "failure"),
            ("later", "success", "success"),
            ("within", "neutral", "neutral"),
            ("same", "neutral", "neutral"),
            ("no-base", "neutral", "neutral"),
            ("unbuilt", "neutral", "failure"),
            ("no-outcome", "neutral", "failure"),
            (None, "neutral", "neutral"),
        )
        self.assertEqual({verdict for verdict, *_ in table}, set(simulate.CONCLUSIONS))
        for verdict, concluded, once_required in table:
            with self.subTest(verdict=verdict):
                self.assertEqual(simulate.conclusion(verdict, required=False), concluded)
                self.assertEqual(simulate.conclusion(verdict, required=True), once_required)

    def test_a_group_that_fails_fails_the_check_and_else_one_that_succeeds_succeeds_it(self):
        for verdicts, required, concluded in (
            (("later", "earlier"), False, "failure"),
            (("earlier", "later"), False, "failure"),
            (("later", "within"), False, "success"),
            (("same", "later"), False, "success"),
            (("same", "within"), False, "neutral"),
            (("later", "no-outcome"), False, "success"),
            (("later", "no-outcome"), True, "failure"),
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
        asked = simulate.jobs("pr", REVISION, BASE, simulate.scenarios("pr", Path()))
        status, out, err = self.running(asked, "--revision", REVISION, "--base", BASE)

        self.assertEqual(status, 0, err)
        self.assertEqual(
            json.loads(out),
            {
                "status": "in_progress",
                "details_url": RUN_URL,
                "output": {
                    "title": "18 runs in progress",
                    "summary": f"**18 runs in progress** at {commit(REVISION)} against its merge "
                    f"base {commit(BASE)} · [the run]({RUN_URL})",
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
        run changed as `edited` changes them, by its rate and seed, and `/v3` after them for
        GroupV3's run."""
        copy = self.root / name
        with_groupv3(UPLOADED, copy) if both else shutil.copytree(UPLOADED, copy)
        for cell, changed in edits.items():
            rate, seed, *group = cell.split("/")
            scenario = GROW_1_ADDER[group[0] if group else "v2"].format(rate)
            number = int(seed) + 1
            file = copy / f"sim-revision-{scenario}-{number}" / scenario / str(number) / "run.json"
            report = json.loads(file.read_text(encoding="utf-8"))
            edited(report, changed)
            file.write_text(json.dumps(report), encoding="utf-8")
        return copy

    def widened(
        self, side: Path, rate: str, group: str = "v2", seeds: Sequence[int] = range(2, 8)
    ) -> Path:
        """`side` with runs of `group` at `rate` at `seeds` too, each a copy of seed 0's at its own
        seed: by default seeds 2 to 7, eight seeds, as many as can fork earlier or later beyond
        chance."""
        scenario = GROW_1_ADDER[group].format(rate)
        first = side / f"sim-revision-{scenario}-1" / scenario / "1" / "run.json"
        report = json.loads(first.read_text(encoding="utf-8"))
        for seed in seeds:
            number = seed + 1
            copy = side / f"sim-revision-{scenario}-{number}" / scenario / str(number) / "run.json"
            copy.parent.mkdir(parents=True)
            copy.write_text(json.dumps(dict(report, seed=seed)), encoding="utf-8")
        return side

    def unbuilt(self, side: Path, rate: str, seed: int) -> None:
        """The artifact of the job of a run at `rate` and `seed` as one that could not build."""
        scenario, number = GROW_1_ADDER["v2"].format(rate), seed + 1
        job = side / f"sim-revision-{scenario}-{number}"
        shutil.rmtree(job, ignore_errors=True)
        (job / scenario / str(number)).mkdir(parents=True)
        (job / scenario / str(number) / "unbuilt").write_text("", encoding="utf-8")

    def matrix(self, head: Path, base: Optional[Path] = None, asked: Sequence[dict] = ()) -> Path:
        """The file tools/sim matrix writes of the runs under `head`, beside those under `base`,
        with a cell for each run of the jobs `asked`."""
        out = self.root / "matrix.json"
        command = [sys.executable, str(ROOT / "tools" / "sim"), "matrix", str(head)]
        command += ["--out", str(out)] + ([] if base is None else ["--base", str(base)])
        if asked:
            write(self.root / "asked.json", list(asked))
            command += ["--asked", str(self.root / "asked.json")]
        subprocess.run(command, check=True)
        return out

    def pr_jobs(self) -> List[dict]:
        """The run jobs of pr, as the suite ran the scenario UPLOADED holds the runs of."""
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

    def said(self, text: str) -> List[str]:
        """The lines of the comment's first paragraph, without the backslash that breaks each but
        the last: a line for each group, or the comment's one line."""
        return [line.removesuffix("\\") for line in text.split("\n\n", 1)[0].splitlines()]

    def written(self, suite: str, matrix: Path, *extra: str) -> "tuple[str, str]":
        """What `comment --out` prints for `suite` drawn from `matrix`, and the comment it
        writes."""
        out = self.root / "written.md"
        status, printed, err = self.comment(suite, matrix, "--out", str(out), *extra)
        self.assertEqual(status, 0, err)
        return printed, out.read_text(encoding="utf-8")

    def test_a_pr_whose_runs_all_came_out_as_at_the_base_says_nothing_moved_in_one_line(self):
        both = self.matrix(
            self.uploaded("head", both=True), self.uploaded("base", both=True), self.pr_jobs()
        )
        _, out, _ = self.comment("pr", both, "--base", BASE, "--report-url", REPORT_URL)

        self.assertEqual(
            out,
            f"⚪ **Nothing moved.** All 12 runs of GroupV2 and GroupV3 at {commit(REVISION)} "
            f"replayed its merge base {commit(BASE)} exactly. {LINKS}\n",
        )
        checked = self.check(both, out)
        self.assertEqual(
            (checked["conclusion"], checked["output"]),
            (
                "neutral",
                {
                    "title": "GroupV2 nothing moved in 6 runs; GroupV3 nothing moved in 6 runs",
                    "summary": out,
                },
            ),
        )
        _, out, _ = self.comment("pr", self.matrix(UPLOADED, UPLOADED), "--base", BASE)
        self.assertEqual(
            out,
            f"⚪ **Nothing moved.** All 6 runs of GroupV2 at {commit(REVISION)} replayed its "
            f"merge base {commit(BASE)} exactly. [The run]({RUN_URL})\n",
        )

    def test_a_pr_whose_runs_moved_says_so_by_group_and_rate_and_links_their_trees(self):
        matrix = self.matrix(self.uploaded("head", **SOONER), UPLOADED)
        _, out, _ = self.comment("pr", matrix, "--base", BASE, "--report-url", REPORT_URL)

        self.assertEqual(
            out,
            "🟡 **GroupV2: moved, within chance** · 0.1%: nothing moved in seeds 0 to 1 · 1%: 0 "
            "later, 1 earlier, 1 tied (p 1) · 5%: 0 later, 1 earlier, 1 tied (p 1)\n"
            "\n"
            f"{LINKS} · {commit(REVISION)} against its merge base {commit(BASE)}\n",
        )

    def test_a_pr_handed_where_its_pictures_are_shows_that_of_each_group_whose_runs_moved(self):
        drawn = "seeds 0 to 1: the head's tree over the base's, a run that moved outlined"

        def picture(group: str, called: str) -> str:
            return (
                '<picture><source media="(prefers-color-scheme: dark)" '
                f'srcset="{PICTURES}trees-{group}-dark.svg"><img alt="{called}\'s {drawn}" '
                f'src="{PICTURES}trees-{group}.svg"></picture>'
            )

        given = ("--base", BASE, "--report-url", REPORT_URL)
        base = self.uploaded("base", both=True)
        for name, sooner, pictures, called in (
            ("one", {"1/1/v3": {"first_fork": 5}}, [picture("v3", "GroupV3")], "GroupV3"),
            (
                "both",
                {"1/1": {"first_fork": 5}, "5/0/v3": {"first_fork": 4}},
                [picture("v2", "GroupV2"), picture("v3", "GroupV3")],
                "GroupV2 and GroupV3",
            ),
        ):
            with self.subTest(moved=name):
                matrix = self.matrix(self.uploaded(name, both=True, **sooner), base)
                _, without, _ = self.comment("pr", matrix, *given)
                _, out, _ = self.comment("pr", matrix, *given, "--pictures", PICTURES)
                lines, links = without.split("\n\n")
                self.assertEqual(
                    out.split("\n\n"), [lines, *pictures, f"{called}'s {drawn}. {links}"]
                )

    def test_a_pr_none_of_whose_runs_moved_shows_no_picture(self):
        both = (self.uploaded("head", both=True), self.uploaded("base", both=True))
        unbuilt = self.root / "unbuilt"
        self.unbuilt(unbuilt, "1", 0)
        for name, matrix, given in (
            ("nothing moved", self.matrix(*both, self.pr_jobs()), ("--base", BASE)),
            ("no base", self.matrix(UPLOADED), ()),
            ("unbuilt", self.matrix(unbuilt, UPLOADED), ("--base", BASE)),
        ):
            with self.subTest(run=name):
                _, without, _ = self.comment("pr", matrix, *given, "--report-url", REPORT_URL)
                _, out, _ = self.comment(
                    "pr", matrix, *given, "--report-url", REPORT_URL, "--pictures", PICTURES
                )
                self.assertEqual(out, without)

    def test_refuses_pictures_an_attribute_cannot_hold_the_address_of_or_of_another_suite(self):
        matrix = self.matrix(UPLOADED)
        refused = ("javascript:alert(1)", f"{PICTURES} x", f'{PICTURES}"', f"{PICTURES}'", "")
        # Without its last /, a picture's address would lose the directory's name.
        for url in (*refused, PICTURES.rstrip("/")):
            with self.subTest(url=url):
                status, out, err = self.comment("pr", matrix, "--pictures", url)
                self.assertEqual((status, out), (2, ""))
                self.assertIn("--pictures: ", err.splitlines()[-1])
        status, out, err = self.comment("main", matrix, "--pictures", PICTURES)
        self.assertEqual((status, out), (2, ""))
        self.assertIn("--pictures: given for pr, and only then", err.splitlines()[-1])

    def test_a_pr_on_both_groups_says_each_on_a_line_of_its_own_and_fails_on_either(self):
        sooner = {"1/0/v3": {"first_fork": 10}, "1/1/v3": {"first_fork": 5}}
        head = self.uploaded("head", both=True, **sooner)
        base = self.widened(self.uploaded("base", both=True), "1", "v3")
        matrix = self.matrix(self.widened(head, "1", "v3"), base, self.pr_jobs())
        _, out, _ = self.comment("pr", matrix, "--base", BASE)

        self.assertEqual(
            self.said(out),
            [
                "⚪ **GroupV2: nothing moved** · 0.1%: nothing moved in seeds 0 to 1 · 1%: nothing "
                "moved in seeds 0 to 1 · 5%: nothing moved in seeds 0 to 1",
                "🔴 **GroupV3: forks earlier** · 0.1%: nothing moved in seeds 0 to 1 · 1%: 0 "
                "later, 8 earlier, 0 tied (p 0.0078) · 5%: nothing moved in seeds 0 to 1",
            ],
        )
        checked = self.check(matrix, out)
        self.assertEqual(
            (checked["conclusion"], checked["output"]["title"]),
            (
                "failure",
                "GroupV2 nothing moved in 6 runs; GroupV3 forks earlier at 1%: 0 later, 8 "
                "earlier (p 0.0078)",
            ),
        )

    def test_a_pr_whose_runs_moved_is_judged_on_the_seeds_of_both_stages_of_its_moved_group(self):
        head = self.uploaded("head", both=True, **{"1/1/v3": {"first_fork": 5}})
        base = self.uploaded("base", both=True)
        first = self.pr_jobs()
        asked = self.root / "asked.json"
        write(asked, first)
        given = ("--asked", str(asked), "--revision", REVISION, "--base", BASE)
        with mock.patch.multiple(simulate, **HELD_SUITES):
            status, out, err = run("widen", "--matrix", str(self.matrix(head, base, first)), *given)
        self.assertEqual(status, 0, err)
        more, every = (json.loads(line.split("=", 1)[1]) for line in out.splitlines())
        self.assertEqual(every, first + more)
        self.assertEqual(
            {(job["side"], job["scenario"], job["from"]) for job in more},
            {
                (side, GROW_1_ADDER["v3"].format(rate), start)
                for side in ("revision", "base")
                for rate in ("0.1", "1", "5")
                for start in (3, 9, 15)
            },
        )
        for side in (head, base):
            for rate in ("0.1", "1", "5"):
                self.widened(side, rate, "v3", range(2, 20))
        for seed in range(2, 10):
            scenario, number = GROW_1_ADDER["v3"].format("1"), seed + 1
            file = head / f"sim-revision-{scenario}-{number}" / scenario / str(number) / "run.json"
            report = json.loads(file.read_text(encoding="utf-8"))
            edited(report, {"first_fork": 10})
            file.write_text(json.dumps(report), encoding="utf-8")
        matrix = self.matrix(head, base, every)
        _, out, _ = self.comment("pr", matrix, "--base", BASE)

        same = "nothing moved in seeds 0 to 1"
        self.assertEqual(
            self.said(out),
            [
                f"⚪ **GroupV2: nothing moved** · 0.1%: {same} · 1%: {same} · 5%: {same}",
                "🔴 **GroupV3: forks earlier** · 0.1%: nothing moved in seeds 0 to 19 · 1%: 0 "
                "later, 9 earlier, 11 tied (p 0.0039) · 5%: nothing moved in seeds 0 to 19",
            ],
        )
        checked = self.check(matrix, out)
        self.assertEqual(
            (checked["conclusion"], checked["output"]["title"]),
            (
                "failure",
                "GroupV2 nothing moved in 6 runs; GroupV3 forks earlier at 1%: 0 later, 9 "
                "earlier (p 0.0039)",
            ),
        )

    def test_runs_that_drew_other_ids_or_moved_at_the_same_first_fork_are_ties(self):
        ids = {"0": "7c3e9a01b2", "1": "b19d47e0c5"}
        drew = {
            f"{rate}/{seed}": {"conversation": drawn}
            for rate in ("1", "5")
            for seed, drawn in ids.items()
        }
        head = self.uploaded("head", **drew, **{"0.1/0": {"virtual_s": 6077.0}})
        matrix = self.matrix(head, UPLOADED)
        _, out, _ = self.comment("pr", matrix, "--base", BASE, "--report-url", REPORT_URL)

        self.assertEqual(
            out,
            "🟡 **GroupV2: moved, within chance** · 0.1%: 0 later, 0 earlier, 2 tied (p 1) · "
            "1%: 0 later, 0 earlier, 2 tied (p 1) · 5%: 0 later, 0 earlier, 2 tied (p 1)\n"
            "\n"
            f"{LINKS} · {commit(REVISION)} against its merge base {commit(BASE)}\n",
        )

    def test_a_rate_with_no_outcome_at_the_head_or_at_the_base_says_so(self):
        base = self.uploaded("base", both=True)
        unbuilt_base = self.uploaded("unbuilt-base", both=True)
        self.unbuilt(unbuilt_base, "1", 1)
        no_outcome = self.uploaded("no-outcome", both=True)
        shutil.rmtree(no_outcome / "sim-revision-grow-1-adder-groupv2-waku-loss-5pct-2")
        unbuilt = self.uploaded("unbuilt-head", both=True)
        self.unbuilt(unbuilt, "1", 1)
        sooner = self.uploaded("sooner", both=True, **{"5/0": {"first_fork": 4}})
        later = {"1/0": {"first_fork": 4}, "5/0": {"first_fork": 49}}
        mixed = self.uploaded("mixed", both=True, **later)
        shutil.rmtree(mixed / "sim-revision-grow-1-adder-groupv2-waku-loss-5pct-2")
        same = "nothing moved in seeds 0 to 1"
        moved = "0 later, 1 earlier, 1 tied (p 1)"
        missing = "no outcome in 1 of 2 runs"
        unbuilt_at_base = "does not build at the base"
        for head, against, verdict, rates in (
            (no_outcome, base, "⚫ **GroupV2: no outcome**", (same, same, missing)),
            (unbuilt, base, "⚫ **GroupV2: no outcome**", (same, missing, same)),
            (mixed, base, "⚫ **GroupV2: no outcome**", (same, moved, missing)),
            (
                sooner,
                unbuilt_base,
                "🟡 **GroupV2: moved, within chance**",
                (same, f"{same}, {unbuilt_at_base} in 1 of 2 runs", moved),
            ),
            (
                base,
                unbuilt_base,
                "⚪ **GroupV2: nothing moved**",
                (same, f"{same}, {unbuilt_at_base} in 1 of 2 runs", same),
            ),
        ):
            with self.subTest(head=head.name, base=against.name):
                matrix = self.matrix(head, against, self.pr_jobs())
                _, out, _ = self.comment("pr", matrix, "--base", BASE)
                said = verdict + "".join(
                    f" · {rate}: {one}" for rate, one in zip(("0.1%", "1%", "5%"), rates)
                )
                groupv3 = f"⚪ **GroupV3: nothing moved** · 0.1%: {same} · 1%: {same} · 5%: {same}"
                self.assertEqual(self.said(out), [said, groupv3])

    def test_a_pr_with_nothing_to_compare_has_the_verdict_line_and_the_links(self):
        _, out, _ = self.comment("pr", self.matrix(UPLOADED), "--report-url", REPORT_URL)

        self.assertEqual(
            out, f"**chat-sim: no base, no comparison** at {commit(REVISION)}\n\n{LINKS}\n"
        )
        base = self.root / "base"
        for rate in ("0.1", "1", "5"):
            for seed in (0, 1):
                self.unbuilt(base, rate, seed)
        _, out, _ = self.comment("pr", self.matrix(UPLOADED, base), "--base", BASE)
        self.assertEqual(
            out,
            f"**chat-sim: does not build at the base, no comparison** at {commit(REVISION)} "
            f"against its merge base {commit(BASE)}\n\n[The run]({RUN_URL})\n",
        )

    def test_without_the_report_s_page_the_comment_links_the_run_alone(self):
        matrix = self.matrix(self.uploaded("head", **SOONER), UPLOADED)
        _, out, _ = self.comment("pr", matrix, "--base", BASE)

        self.assertEqual(
            out.split("\n\n", 1)[1],
            f"[The run]({RUN_URL}) · {commit(REVISION)} against its merge base {commit(BASE)}\n",
        )

    def test_a_comment_written_to_a_file_is_what_it_prints_without_one(self):
        for suite, head, base, extra in (
            ("pr", self.uploaded("head", **SOONER), UPLOADED, ("--base", BASE)),
            ("main", UPLOADED, None, ()),
        ):
            with self.subTest(suite=suite):
                matrix = self.matrix(head, base)
                _, shown, _ = self.comment(suite, matrix, *extra, "--report-url", REPORT_URL)
                self.assertEqual(
                    self.written(suite, matrix, *extra, "--report-url", REPORT_URL), ("", shown)
                )

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
        shutil.rmtree(head / "sim-revision-grow-1-adder-groupv2-waku-loss-1pct-2")
        asked = [
            {"scenario": GROW_1_ADDER["v2"].format(rate), "from": 1, "runs": 2}
            for rate in ("0.1", "1", "5")
        ]
        _, out, _ = self.comment("main", self.matrix(head, None, asked))

        self.assertTrue(out.startswith("**chat-sim: no outcome in 1 of 6 runs** at "))
        self.assertEqual(
            self.rows(out)[1],
            "| GroupV2 | 1% | 2 | 0 | 0 | 1 | 0 | 18 to 18, median 18 | 18 to 18, median 18 | 7 "
            "| 18 of 51 | 1 |",
        )

    def test_a_pr_that_forks_earlier_fails_its_check_and_one_that_forks_later_succeeds(self):
        base = self.widened(self.uploaded("base"), "1")
        given = ("--base", BASE, "--report-url", REPORT_URL)
        for at, concluded, title in (
            ((10, 5), "failure", "GroupV2 forks earlier at 1%: 0 later, 8 earlier (p 0.0078)"),
            ((30, 30), "success", "GroupV2 forks later at 1%: 8 later, 0 earlier (p 0.0078)"),
        ):
            with self.subTest(concluded=concluded):
                moved = {f"1/{seed}": {"first_fork": one} for seed, one in enumerate(at)}
                head = self.uploaded(f"head-{concluded}", **moved)
                matrix = self.matrix(self.widened(head, "1"), base)
                _, comment, _ = self.comment("pr", matrix, *given)
                self.assertEqual(
                    self.check(matrix, comment, "--report-url", REPORT_URL),
                    {
                        "status": "completed",
                        "conclusion": concluded,
                        "details_url": REPORT_URL,
                        "output": {"title": title, "summary": comment},
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
        comment.write_text("⚪ **Nothing moved.** All 6 runs of GroupV2\n", encoding="utf-8")
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


class Comment(Fixture):
    """The comment on the long suite, from the text each run job uploaded in an artifact of its
    own."""

    def setUp(self):
        super().setUp()
        self.runs = self.root / "revision"
        self.base_runs = self.root / "base"
        self.names = [name for name, _ in simulate.scenarios("long", self.expected)]
        for name in self.names:
            count = 1 if name.startswith("seat-50-post") else 3
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
            self.swept(self.runs, "grow-3-adders-groupv2-waku-loss-0.1pct") / "outcomes.json",
            [outcome(0), outcome(1, "split at epoch 9"), outcome(2, "split at epoch 4")],
        )
        self.compared("grow-3-adders-groupv2-waku-loss-0.1pct", "1 of 3 runs came out as expected")
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
            rows["grow-3-adders-groupv2-waku-loss-0.1pct"],
            [
                "3",
                "whole · split at epoch 9 · split at epoch 4",
                "1 of 3 runs came out as expected",
            ],
        )
        self.assertEqual(rows["seat-50-post-groupv2"], ["1", "whole", "no outcome at the base"])
        self.assertIn(f"chat-sim `fedcba9` · [the run]({RUN_URL})", out)

    def test_without_a_base_holds_to_what_is_expected_and_says_where_nothing_is(self):
        self.compared(
            "grow-3-adders-size-50-groupv2-waku-loss-0pct", "3 of 3 runs came out as expected"
        )
        status, out, _ = self.comment()
        self.assertEqual(status, 0)
        self.assertIn("against what chat-sim holds in `tools/sim/expected`", out)
        self.assertIn("| Scenario | Runs | Came to | Against what is held |", out)
        rows = self.rows(out)
        self.assertEqual(rows["seat-50-post-groupv2"], ["1", "whole", "nothing held"])
        self.assertEqual(
            rows["grow-3-adders-size-50-groupv2-waku-loss-0pct"],
            ["3", "whole · whole · whole", "3 of 3 runs came out as expected"],
        )

    def test_a_scenario_with_no_outcome_says_so(self):
        (self.swept(self.runs, "seat-50-post-groupv2") / "outcomes.json").unlink()
        write(
            self.swept(self.runs, "grow-3-adders-groupv2-waku-loss-0.1pct") / "outcomes.json",
            [outcome(0), outcome(1, None)],
        )
        _, out, _ = self.comment(*self.with_base)
        rows = self.rows(out)
        self.assertEqual(rows["seat-50-post-groupv2"], ["1", "no outcome", "-"])
        self.assertEqual(rows["grow-3-adders-groupv2-waku-loss-0.1pct"][1], "whole · no verdict")

    def test_a_run_of_a_grow_step_with_no_verdict_says_how_its_lineage_ended(self):
        endings = (
            (50, 9, "split"),
            (4, None, "stalled"),
            (50, None, "whole"),
            (50, None, "behind"),
        )
        grown = [
            dict(outcome(seed, None), trunk_epoch=trunk, first_fork=fork, ended=ended)
            for seed, (trunk, fork, ended) in enumerate(endings)
        ]
        write(
            self.swept(self.runs, "grow-3-adders-groupv2-waku-loss-0.1pct") / "outcomes.json", grown
        )

        _, out, _ = self.comment(*self.with_base)

        self.assertEqual(
            self.rows(out)["grow-3-adders-groupv2-waku-loss-0.1pct"][1],
            "split at epoch 9 · stalled at epoch 4 · whole · whole, members behind",
        )

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
        self.assertEqual(
            rows["seat-50-post-groupv2"], ["1", "whole", "chat-sim does not build at the base"]
        )

    def test_refuses_a_base_without_its_runs_and_runs_without_a_base(self):
        for extra in (("--base", BASE), ("--base-runs", str(self.base_runs))):
            with self.subTest(extra=extra):
                status, out, err = self.comment(*extra)
                self.assertEqual(status, 2)
                self.assertEqual(out, "")
                self.assertIn("--base-runs", err)

    def test_shows_each_compare_folded_under_the_table(self):
        compared = self.compared(
            "grow-3-adders-groupv2-waku-loss-0.1pct", "3 of 3 runs came out as expected"
        )
        _, out, _ = self.comment()
        self.assertIn(
            "<details><summary><code>grow-3-adders-groupv2-waku-loss-0.1pct</code></summary>\n\n"
            f"```\n{compared}```\n\n</details>\n",
            out,
        )
        self.assertEqual(out.count("<details>"), 1)

    def test_written_to_a_file_prints_nothing(self):
        _, shown, _ = self.comment(*self.with_base)
        out = self.root / "comment.md"
        status, printed, err = self.comment(*self.with_base, "--out", str(out))

        self.assertEqual((status, printed), (0, ""), err)
        self.assertEqual(out.read_text(encoding="utf-8"), shown)


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

    def condition(self, name: str) -> str:
        """The if of the job `name`, folded over the lines under `if: >-`."""
        return re.search(r"\n    if: >-\n((?:      .*\n)+)", self.jobs[name])[1]

    def test_only_the_plan_and_post_jobs_mint_a_token_and_each_keeps_it_to_its_steps(self):
        self.assertEqual(
            list(self.jobs), ["plan", "run", "pair", "more", "comment", "publish", "post"]
        )
        minting = [name for name, job in self.jobs.items() if "create-github-app-token@" in job]
        keyed = [name for name, job in self.jobs.items() if "secrets." in job]
        self.assertEqual((minting, keyed), (["plan", "post"], ["plan", "post"]))
        for name in minting:
            with self.subTest(job=name):
                used = {
                    line.strip()
                    for line in self.jobs[name].splitlines()
                    if "steps.token.outputs.token" in line
                }
                self.assertEqual(used, {"GH_TOKEN: ${{ steps.token.outputs.token }}"})

    def test_a_pr_run_s_seeds_run_in_two_stages_each_a_call_of_sweep_yml(self):
        for name, runs in (("run", "needs.plan.outputs.runs"), ("more", "needs.pair.outputs.more")):
            with self.subTest(job=name):
                self.assertIn(
                    "\n    uses: ./.github/workflows/sweep.yml\n    with:\n"
                    f"      runs: ${{{{ {runs} }}}}\n",
                    self.jobs[name],
                )
        self.assertIn("\n    needs: plan\n", self.jobs["run"])

    def test_pair_plans_the_second_stage_from_the_first_s_runs_whatever_they_came_to(self):
        pair, condition = self.jobs["pair"], self.condition("pair")

        self.assertIn("\n    needs: [plan, run]\n", pair)
        for held in (
            "!cancelled() && needs.plan.result == 'success'",
            "needs.plan.outputs.suite == 'pr' && needs.plan.outputs.base != ''",
        ):
            self.assertIn(held, condition)
        self.assertIn(
            "\n      more: ${{ steps.widen.outputs.more }}\n"
            "      asked: ${{ steps.widen.outputs.asked }}\n",
            pair,
        )
        self.assertIn('--asked "$RUNNER_TEMP/asked.json" --out "$RUNNER_TEMP/matrix.json"', pair)
        self.assertIn("python3 .github/scripts/simulate.py widen ", pair)
        self.assertIn('\n            >> "$GITHUB_OUTPUT"\n', pair)

    def test_more_is_skipped_where_no_group_s_runs_moved_and_runs_after_a_failed_run_job(self):
        condition = self.condition("more")

        self.assertIn("\n    needs: pair\n", self.jobs["more"])
        self.assertIn("!cancelled() && needs.pair.result == 'success'", condition)
        self.assertIn("needs.pair.outputs.more != '[]'", condition)

    def test_the_comment_pairs_the_runs_of_both_stages_or_of_the_first_without_pair(self):
        comment = self.jobs["comment"]

        self.assertIn("\n    needs: [plan, run, pair, more]\n", comment)
        self.assertIn("\n    if: ${{ !cancelled() && needs.plan.result == 'success' }}\n", comment)
        self.assertIn("ASKED: ${{ needs.pair.outputs.asked || needs.plan.outputs.runs }}", comment)
        self.assertIn('printf \'%s\\n\' "$ASKED" > "$RUNNER_TEMP/asked.json"', comment)

    def test_the_comment_is_posted_after_publish_and_the_check_completed_whatever_it_came_to(self):
        post, condition = self.jobs["post"], self.condition("post")

        self.assertIn("\n    needs: [plan, comment, publish]\n", post)
        self.assertIn("!cancelled() && needs.comment.result == 'success'", condition)
        self.assertNotIn("needs.publish", condition)

    def test_post_writes_the_comment_again_with_its_pictures_at_the_commit_publish_pushed(self):
        publish, post = self.jobs["publish"], self.jobs["post"]

        self.assertIn("\n    outputs:\n      pushed: ${{ steps.push.outputs.pushed }}\n", publish)
        pushing = publish.split("\n      - name: Push to gh-pages\n        id: push\n", 1)[1]
        self.assertTrue(
            pushing.rstrip().endswith(
                "git -C site push origin gh-pages\n"
                '          echo "pushed=$(git -C site rev-parse HEAD)" >> "$GITHUB_OUTPUT"'
            )
        )
        before, written = post.split("\n      - name: Write the comment again, ", 1)
        step, after = written.split("\n      - name: ", 1)
        self.assertNotIn("comment.md", before)
        self.assertIn("\n        if: ${{ needs.publish.outputs.pushed != '' }}\n", step)
        # A step that fails leaves the comment as it was, for the steps that post it.
        self.assertIn("\n        continue-on-error: true\n", step)
        self.assertIn("PUSHED: ${{ needs.publish.outputs.pushed }}", step)
        self.assertIn(
            '--pictures "https://raw.githubusercontent.com/$GITHUB_REPOSITORY/$PUSHED/'
            'logos-chat/pr-$PR/"',
            step,
        )
        self.assertIn('--out "$RUNNER_TEMP/comment.md" || {', step)
        self.assertIn('--comment "$RUNNER_TEMP/comment.md"', after)

    def test_publish_keeps_the_scenario_files_of_the_tree_that_ran(self):
        publish = self.jobs["publish"]
        before, written = publish.split("pages.py pr ", 1)
        step = written.split("\n\n", 1)[0]

        self.assertIn("\n      - uses: actions/checkout@v4\n\n", before)
        self.assertIn("\n            --scenarios tools/sim/scenarios \\\n", step)


class SweepWorkflow(unittest.TestCase):
    """sweep.yml as it is written: the run jobs simulate.yml calls it for."""

    def setUp(self):
        self.sweep = (ROOT / ".github" / "workflows" / "sweep.yml").read_text(encoding="utf-8")

    def test_is_called_with_the_run_jobs_and_runs_a_job_each_of_its_own_env(self):
        self.assertIn(
            "\non:\n  workflow_call:\n    inputs:\n      runs:\n", self.sweep.split("\njobs:\n")[0]
        )
        self.assertIn("\n        run: ${{ fromJSON(inputs.runs) }}\n", self.sweep)
        self.assertIn("\nenv:\n  CARGO_TERM_COLOR: always\n", self.sweep)
        self.assertNotIn("secrets.", self.sweep)

    def test_each_job_uploads_under_its_side_scenario_and_first_run(self):
        self.assertIn(
            "\n          name: sim-${{ matrix.run.side }}-${{ matrix.run.scenario }}-"
            "${{ matrix.run.from }}\n",
            self.sweep,
        )

    def test_a_run_made_again_pins_logos_chat_in_each_job_s_own_lines(self):
        step = re.search(
            r"\n      - name: Pin logos-chat at the revision\n"
            r"        run: \|\n((?:          .+\n)+)",
            self.sweep,
        )[1]
        pin = [line.strip().replace("$REVISION", REVISION) for line in step.splitlines()]
        cell = {"scenario": "grow-3-adders-groupv2-waku-loss-0.1pct", "run": 2, "seed": 1}

        again = simulate.again(cell, REVISION, CHAT_SIM)

        self.assertEqual(again[1 : 1 + len(pin)], pin)
        self.assertEqual(len(pin), 3)


class CiWorkflow(unittest.TestCase):
    """ci.yml as it is written, a job by its name."""

    def setUp(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        parts = re.split(r"(?m)^  ([\w-]+):\n", workflow.split("\njobs:\n", 1)[1])
        self.jobs = dict(zip(parts[1::2], parts[2::2]))

    def test_the_sims_run_only_when_a_path_they_depend_on_changed(self):
        measured = self.jobs["measured"]

        self.assertIn("\n          fetch-depth: 0\n", measured)
        self.assertIn('simulate.py changed --since "$SINCE" >> "$GITHUB_OUTPUT"', measured)
        self.assertIn(
            "SINCE: ${{ github.event.pull_request.base.sha || "
            "!github.event.forced && github.event.before || '' }}",
            measured,
        )
        for name in ("sim-one-process", "list-expected"):
            with self.subTest(job=name):
                self.assertIn(
                    "\n    needs: measured\n"
                    "    if: ${{ needs.measured.outputs.changed == 'true' }}\n",
                    self.jobs[name],
                )
        self.assertIn("\n    needs: list-expected\n", self.jobs["sim-expected"])
        self.assertNotIn("\n    if: ", self.jobs["sim-expected"])

    def test_publish_is_skipped_with_the_sims(self):
        publish = self.jobs["publish"]
        condition = publish.split("\n    if: >-\n", 1)[1].split("\n    runs-on: ", 1)[0]

        self.assertIn("\n    needs: [measured, sim-one-process, sim-expected]\n", publish)
        self.assertIn("!cancelled() && needs.measured.outputs.changed == 'true' &&", condition)

    def test_lint_test_and_tools_run_whatever_changed(self):
        for name in ("lint", "test", "tools"):
            with self.subTest(job=name):
                self.assertNotIn("\n    needs: ", self.jobs[name])
                self.assertNotIn("\n    if: ", self.jobs[name])


if __name__ == "__main__":
    unittest.main()
