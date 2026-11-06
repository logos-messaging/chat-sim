import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional

import simulate

REVISION = "0123456789abcdef0123456789abcdef01234567"
BASE = "89abcdef0123456789abcdef0123456789abcdef"
CHAT_SIM = "fedcba9876543210fedcba9876543210fedcba98"
RUN_URL = "https://github.com/logos-messaging/chat-sim/actions/runs/42"


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


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.expected = self.root / "expected"
        held = ("grow-waku-loss-0.5", "grow-waku-loss-1", "grow-waku-loss-5", "grow-waku-size-50")
        for name in held:
            write(self.expected / f"{name}.json", [outcome(0), outcome(1), outcome(2)])


class Scenarios(Fixture):
    def test_quick_sweeps_each_held_scenario_as_many_times_as_held_and_the_rest_once(self):
        self.assertEqual(
            simulate.scenarios("quick", self.expected),
            [
                ("50-epochs", 1),
                ("50-epochs-waku", 1),
                ("grow-waku-loss-0.5", 3),
                ("grow-waku-loss-1", 3),
                ("grow-waku-loss-5", 3),
            ],
        )

    def test_long_is_fifty_epochs_and_every_held_scenario(self):
        self.assertEqual(
            [name for name, _ in simulate.scenarios("long", self.expected)],
            [
                "50-epochs",
                "50-epochs-waku",
                "grow-waku-loss-0.5",
                "grow-waku-loss-1",
                "grow-waku-loss-5",
                "grow-waku-size-50",
            ],
        )


class Jobs(unittest.TestCase):
    SWEPT = [("50-epochs", 1), ("grow-waku-loss-1", 3)]

    def test_quick_is_one_job_a_side(self):
        jobs = simulate.jobs("quick", REVISION, BASE, self.SWEPT)
        self.assertEqual(
            [(job["side"], job["revision"]) for job in jobs],
            [("revision", REVISION), ("base", BASE)],
        )
        self.assertEqual({job["scenarios"] for job in jobs}, {"50-epochs=1 grow-waku-loss-1=3"})
        self.assertEqual(jobs[0]["name"], "revision 0123456: the quick suite")

    def test_long_is_a_job_a_scenario_and_no_base_is_one_side(self):
        jobs = simulate.jobs("long", REVISION, "", self.SWEPT)
        self.assertEqual([job["scenarios"] for job in jobs], ["50-epochs=1", "grow-waku-loss-1=3"])
        self.assertEqual({job["side"] for job in jobs}, {"revision"})
        self.assertEqual(jobs[1]["name"], "revision 0123456: grow-waku-loss-1")


class Plan(Fixture):
    def plan(self, *extra: str) -> "tuple[int, str, str]":
        return run("plan", "--suite", "quick", "--expected", str(self.expected), *extra)

    def test_prints_what_it_was_handed_the_jobs_and_the_scenarios_for_github_output(self):
        status, out, _ = self.plan("--revision", REVISION, "--base", BASE, "--pr", "316")
        self.assertEqual(status, 0)
        *handed, runs, scenarios = out.splitlines()
        self.assertEqual(handed, ["suite=quick", f"revision={REVISION}", f"base={BASE}", "pr=316"])
        self.assertEqual(len(json.loads(runs.removeprefix("runs="))), 2)
        self.assertEqual(
            scenarios,
            "scenarios=50-epochs 50-epochs-waku grow-waku-loss-0.5 grow-waku-loss-1 "
            "grow-waku-loss-5",
        )

    def test_refuses_what_is_not_a_full_commit_or_a_pull_request(self):
        for extra, said in (
            (("--revision", REVISION[:7]), "--revision"),
            (("--revision", REVISION.upper()), "--revision"),
            (("--revision", REVISION, "--base", "main"), "--base"),
            (("--revision", REVISION, "--base", REVISION), "--base: the revision itself"),
            (("--revision", REVISION, "--pr", "0"), "--pr"),
            (("--revision", REVISION, "--pr", "1; rm"), "--pr"),
        ):
            with self.subTest(extra=extra):
                status, out, err = self.plan(*extra)
                self.assertEqual(status, 2)
                self.assertEqual(out, "")
                self.assertIn(said, err)


class Comment(Fixture):
    def setUp(self):
        super().setUp()
        self.runs = self.root / "runs"
        self.base_runs = self.root / "base"
        for name in simulate.QUICK:
            count = 1 if name.startswith("50-epochs") else 3
            write(self.runs / name / "outcomes.json", [outcome(seed) for seed in range(count)])
            write(self.base_runs / name / "outcomes.json", [outcome(seed) for seed in range(count)])
        self.with_base = ("--base", BASE, "--base-runs", str(self.base_runs))

    def unbuilt(self, runs: Path) -> None:
        for name in simulate.QUICK:
            (runs / name / "outcomes.json").unlink()
            (runs / name / "unbuilt").write_text("", encoding="utf-8")

    def compared(self, name: str, count: str) -> str:
        text = f"run  seed  change\n{count}\n"
        (self.runs / name / "compare.txt").write_text(text, encoding="utf-8")
        return text

    def comment(self, *extra: str) -> "tuple[int, str, str]":
        return run(
            "comment",
            str(self.runs),
            "--suite",
            "quick",
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
            self.runs / "grow-waku-loss-1" / "outcomes.json",
            [outcome(0), outcome(1, "split at epoch 9"), outcome(2, "split at epoch 4")],
        )
        self.compared("grow-waku-loss-1", "1 of 3 runs came out as expected: 2 worse")
        status, out, _ = self.comment(*self.with_base, "--pr", "316")
        self.assertEqual(status, 0)
        self.assertIn(
            "at logos-chat [`0123456`](https://github.com/logos-messaging/logos-chat/commit/"
            f"{REVISION}) ([#316](https://github.com/logos-messaging/logos-chat/pull/316)), "
            "against the base [`89abcde`]",
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
        (self.runs / "50-epochs" / "outcomes.json").unlink()
        write(self.runs / "grow-waku-loss-1" / "outcomes.json", [outcome(0), outcome(1, None)])
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
