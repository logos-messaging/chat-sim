import contextlib
import io
import json
import unittest
from typing import Tuple

from sim import compare
from sim.cli import main

from tests import FIXTURES, stand_in_runner
from tests.test_run import Scratch


def outcome(**changed) -> dict:
    """The outcome of a run without a `grow` step that came to a split at epoch 7, seven of nine
    at the tip."""
    whole = {
        "seed": 0,
        "network_seed": 1,
        "conversation": "2a30d6a2c1",
        "members_digest": "5e3b9f0c27d41a68",
        "verdict": "split at epoch 7",
        "at_tip": 7,
        "seated": 9,
        "virtual_s": 120.5,
        "trunk_epoch": None,
        "ended": None,
        "lines_off_trunk": None,
        "members_off_trunk": None,
        "came_to": "verdict: split at epoch 7, expected whole",
        "stopped": None,
    }
    whole.update(changed)
    return whole


def run(name: str) -> dict:
    """A run of a `grow` step that CI made, its `run.json` cut to what `compare` reads."""
    return json.loads((FIXTURES / f"grow_{name}.json").read_text(encoding="utf-8"))


# Three runs of saro alone growing a group of 51 toward epoch 50. At no loss and seed 0 the group
# stayed whole; at 1% and seed 0, the same ids drawn, it split at epoch 18 and the trunk went on to
# 50; at 5% and seed 16 it never forked, and the trunk stopped at epoch 4 when no member took part.
WHOLE, SPLIT, STALLED = (compare.outcome(run(name)) for name in ("whole", "split", "stalled"))


# What an outcome says of a `grow` step: the trunk's epoch, how it ended, and the lines and the
# members off the trunk.
GROWN = ("trunk_epoch", "ended", "lines_off_trunk", "members_off_trunk")


def grown(one: dict) -> list:
    return [one[key] for key in GROWN]


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

    def test_a_run_that_drew_other_ids_is_re_rolled_and_neither_better_nor_worse(self):
        whole = outcome(verdict="whole", at_tip=9)
        for drawn in ({"conversation": "9c1d04e7b2"}, {"members_digest": "d07a6e21f95b3c48"}):
            self.assertEqual(compare.change(outcome(), dict(whole, **drawn)), "re-rolled")

    def test_a_run_that_read_no_result_drew_no_ids_and_is_worse(self):
        crashed = outcome(conversation=None, members_digest=None, verdict=None, at_tip=None)
        self.assertEqual(compare.change(outcome(), crashed), "worse")
        self.assertEqual(compare.change(crashed, outcome()), "better")
        grow_crashed = dict(crashed, **dict.fromkeys(GROWN))
        self.assertEqual(compare.change(WHOLE, grow_crashed), "worse")


class Outcome(unittest.TestCase):
    def test_a_run_that_stayed_whole_ended_whole_with_no_one_off_the_trunk(self):
        self.assertEqual(grown(WHOLE), [50, "whole", 0, 0])

    def test_a_run_that_split_counts_the_lines_and_members_off_its_trunk(self):
        # Seven main branches, nine members on them, eight behind on the trunk, tomo never joined.
        self.assertEqual(grown(SPLIT), [50, "split", 7, 18])

    def test_a_member_behind_on_a_branch_is_off_the_trunk_once(self):
        report = run("split")
        report["grow"]["behind"].append(report["grow"]["lines"][1]["members"][0])

        self.assertEqual(compare.outcome(report)["members_off_trunk"], 18)

    def test_a_run_that_never_forked_and_whose_trunk_stopped_short_stalled(self):
        self.assertEqual(grown(STALLED), [4, "stalled", 0, 2])

    def test_whether_the_trunk_got_where_the_step_grew_is_the_runners_ending(self):
        reached, short = run("stalled"), run("whole")
        reached["grow"]["ended"] = "the trunk reached epoch 4"
        short["grow"]["ended"] = "every member of the plan was seated"

        self.assertEqual(compare.outcome(reached)["ended"], "behind")
        self.assertEqual(compare.outcome(short)["ended"], "stalled")

    def test_a_run_without_a_grow_step_has_none_of_its_four(self):
        report = dict(run("whole"), grow=None, check={"as_expected": True, "differences": []})

        said = compare.outcome(report)

        self.assertEqual(grown(said), [None, None, None, None])
        self.assertEqual(said["came_to"], "as expected")

    def test_the_members_ids_come_to_one_digest_in_member_order(self):
        report = run("whole")
        self.assertEqual(WHOLE["members_digest"], "1f228bfa64e58b9a")
        report["nodes"][:2] = reversed(report["nodes"][:2])

        self.assertNotEqual(compare.outcome(report)["members_digest"], WHOLE["members_digest"])


class GrowStanding(unittest.TestCase):
    """Two outcomes of one run of a `grow` step, ordered by how far the trunk got without a fork."""

    def test_more_epochs_without_a_fork_is_better_whatever_else_moved(self):
        self.assertEqual(compare.change(SPLIT, WHOLE), "better")
        self.assertEqual(compare.change(WHOLE, SPLIT), "worse")
        later = dict(SPLIT, verdict="split at epoch 19", lines_off_trunk=20, members_off_trunk=40)
        self.assertEqual(compare.change(SPLIT, later), "better")

    def test_a_trunk_that_stalled_stands_by_its_epoch_as_a_split_does(self):
        sooner = dict(STALLED, verdict="split at epoch 3", ended="split")
        later = dict(STALLED, verdict="split at epoch 5", ended="split")
        self.assertEqual(compare.change(sooner, STALLED), "better")
        self.assertEqual(compare.change(later, STALLED), "worse")
        self.assertEqual(compare.change(STALLED, dict(STALLED, trunk_epoch=9)), "better")

    def test_at_the_same_epochs_whole_is_better_than_behind_with_fewer_off_the_trunk(self):
        whole = dict(WHOLE, members_off_trunk=2)
        behind = dict(WHOLE, verdict="whole, members behind", ended="behind", members_off_trunk=1)
        self.assertEqual(compare.change(behind, whole), "better")
        self.assertEqual(compare.change(whole, behind), "worse")

    def test_then_fewer_lines_off_the_trunk_is_better_with_more_members_off_it(self):
        fewer = dict(SPLIT, lines_off_trunk=6, members_off_trunk=30)
        self.assertEqual(compare.change(SPLIT, fewer), "better")
        self.assertEqual(compare.change(fewer, SPLIT), "worse")

    def test_then_fewer_members_off_the_trunk_is_better(self):
        self.assertEqual(compare.change(SPLIT, dict(SPLIT, members_off_trunk=17)), "better")
        self.assertEqual(compare.change(SPLIT, dict(SPLIT, members_off_trunk=19)), "worse")

    def test_members_at_the_tip_move_no_grow_run(self):
        self.assertEqual(compare.change(SPLIT, dict(SPLIT, at_tip=50)), "changed")

    def test_a_run_with_no_verdict_is_worse_than_any(self):
        self.assertEqual(compare.change(STALLED, dict(STALLED, verdict=None)), "worse")

    def test_a_run_whose_group_or_members_drew_other_ids_is_re_rolled(self):
        self.assertEqual(
            compare.change(SPLIT, dict(WHOLE, conversation=STALLED["conversation"])), "re-rolled"
        )
        self.assertEqual(
            compare.change(SPLIT, dict(WHOLE, members_digest=STALLED["members_digest"])),
            "re-rolled",
        )

    def test_a_re_rolled_run_is_counted_and_its_row_keeps_the_columns(self):
        rerolled = dict(SPLIT, conversation=STALLED["conversation"])

        printed, held = compare.render([SPLIT, SPLIT], [rerolled, WHOLE])

        rows = printed.splitlines()
        self.assertFalse(held)
        self.assertEqual(rows[1].split()[:3], ["1", "0", "re-rolled"])
        self.assertEqual(rows[1].index("split at epoch 18"), rows[0].index("verdict"))
        self.assertEqual(rows[-1], "0 of 2 runs came out as expected: 1 better, 1 re-rolled")


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

    def test_a_file_of_outcomes_without_the_ids_and_the_grow_says_to_write_it_again(self):
        self.sim("--write")
        older = self.written()
        for one in older:
            for key in ("conversation", "members_digest", *GROWN):
                del one[key]
        self.expected.write_text(json.dumps(older), encoding="utf-8")

        refused = self.refused()

        self.assertTrue(refused.endswith(": write them again with `compare --write`"))

    def test_a_file_of_no_outcomes_is_refused(self):
        self.expected.write_text(json.dumps([{"verdict": "whole"}]), encoding="utf-8")

        refused = self.refused()

        self.assertTrue(refused.endswith(": not a list of the outcomes `compare --write` writes"))
