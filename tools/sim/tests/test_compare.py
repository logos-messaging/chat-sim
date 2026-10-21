import contextlib
import io
import json
import unittest
from typing import Tuple

from sim import compare
from sim.cli import main

from tests import stand_in_runner
from tests.test_run import Scratch


def outcome(**changed) -> dict:
    """The outcome of a run that came to a split at epoch 7, seven of nine at the tip."""
    whole = {
        "seed": 0,
        "network_seed": 1,
        "verdict": "split at epoch 7",
        "at_tip": 7,
        "seated": 9,
        "virtual_s": 120.5,
        "came_to": "5 main branches ran side by side",
        "stopped": None,
    }
    whole.update(changed)
    return whole


class Change(unittest.TestCase):
    def test_an_outcome_as_it_was_is_the_same(self):
        self.assertEqual(compare.change(outcome(), outcome()), "same")

    def test_a_split_later_is_better_and_one_sooner_worse(self):
        self.assertEqual(compare.change(outcome(), outcome(verdict="split at epoch 12")), "better")
        self.assertEqual(compare.change(outcome(), outcome(verdict="split at epoch 3")), "worse")

    def test_no_split_is_better_than_any_and_whole_better_than_members_behind(self):
        behind = outcome(verdict="whole, members behind")
        self.assertEqual(compare.change(outcome(), behind), "better")
        self.assertEqual(compare.change(behind, outcome(verdict="whole")), "better")
        self.assertEqual(compare.change(outcome(), outcome(verdict=None)), "worse")

    def test_at_one_verdict_more_members_at_the_tip_is_better(self):
        self.assertEqual(compare.change(outcome(), outcome(at_tip=8)), "better")
        self.assertEqual(compare.change(outcome(), outcome(at_tip=6)), "worse")

    def test_an_outcome_that_stands_where_it_did_and_differs_is_changed(self):
        self.assertEqual(compare.change(outcome(), outcome(virtual_s=121.0)), "changed")


class CommandLine(Scratch):
    """A sweep of two runs in one process, held to the outcomes written of it."""

    def setUp(self):
        super().setUp()
        self.expected = self.scratch / "expected.json"
        runner = stand_in_runner(self.scratch)
        with contextlib.redirect_stdout(io.StringIO()):
            given = ["--runs", "2", "--one-process", str(runner), "--out", str(self.out)]
            self.assertEqual(main(["sweep", str(self.scenario()), *given]), 0)

    def sim(self, *arguments: str) -> Tuple[int, str]:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            status = main(["compare", str(self.expected), str(self.out), *arguments])
        return status, printed.getvalue()

    def refused(self) -> str:
        """What the command says when it refuses to compare."""
        complained = io.StringIO()
        with self.assertRaises(SystemExit) as refused:
            with contextlib.redirect_stderr(complained):
                main(["compare", str(self.expected), str(self.out)])
        self.assertEqual(refused.exception.code, 2)
        return complained.getvalue().splitlines()[-1]

    def written(self) -> list:
        return json.loads(self.expected.read_text(encoding="utf-8"))

    def test_a_sweep_held_to_the_outcomes_written_of_it_is_status_0(self):
        self.assertEqual(self.sim("--write"), (0, ""))
        status, printed = self.sim()

        self.assertEqual(status, 0)
        rows = printed.splitlines()
        self.assertEqual(
            [row.split()[:3] for row in rows[1:3]], [["1", "0", "same"], ["2", "1", "same"]]
        )
        self.assertEqual(rows[3:], ["2 of 2 runs came out as expected"])
        self.assertEqual([one["seed"] for one in self.written()], [0, 1])

    def test_a_run_that_came_out_otherwise_is_status_1_and_shows_what_was_expected(self):
        self.sim("--write")
        changed = self.written()
        changed[1]["at_tip"] -= 1
        self.expected.write_text(json.dumps(changed), encoding="utf-8")

        status, printed = self.sim()

        self.assertEqual(status, 1)
        rows = printed.splitlines()
        self.assertEqual(rows[2].split()[:3], ["2", "1", "better"])
        self.assertEqual(rows[3].split()[0], "was")
        self.assertEqual(rows[4:], ["1 of 2 runs came out as expected: 1 better"])

    def test_a_sweep_short_of_the_runs_expected_is_status_1(self):
        self.sim("--write")
        self.expected.write_text(json.dumps(self.written() * 2), encoding="utf-8")

        status, printed = self.sim()

        self.assertEqual(status, 1)
        self.assertTrue(printed.endswith("; 4 runs were expected, and the sweep has 2\n"))

    def test_a_run_not_in_one_process_is_refused(self):
        run = self.out / "2" / "run.json"
        report = json.loads(run.read_text(encoding="utf-8"))
        del report["rig"]
        run.write_text(json.dumps(report), encoding="utf-8")

        refused = self.refused()

        self.assertTrue(refused.endswith(": run 2 was not in one process, whose runs repeat"))

    def test_a_file_of_no_outcomes_is_refused(self):
        self.expected.write_text(json.dumps([{"verdict": "whole"}]), encoding="utf-8")

        refused = self.refused()

        self.assertTrue(refused.endswith(": not a list of the outcomes `compare --write` writes"))
