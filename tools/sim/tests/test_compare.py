import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from typing import Tuple

from sim import compare
from sim.cli import main

from tests import FIXTURES, Scratch, stand_in_runner


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
        "first_fork": None,
        "ended": None,
        "lines_off_trunk": None,
        "members_off_trunk": None,
        "never_joined": None,
        "came_to": "verdict: split at epoch 7, expected whole",
        "stopped": None,
    }
    whole.update(changed)
    return whole


def run(name: str) -> dict:
    """A run of a `grow` step that CI made, its `run.json` cut to what `compare` reads."""
    return json.loads((FIXTURES / f"grow_{name}.json").read_text(encoding="utf-8"))


# Three runs of saro alone growing a group of 51 toward epoch 50. At no loss and seed 0 the group
# stayed whole; at 1% and seed 0, the same ids drawn, a line first forked at epoch 18 and the trunk
# went on to 50; at 5% and seed 16 it never forked, and the trunk stopped at epoch 4 when no member
# took part.
WHOLE, SPLIT, STALLED = (compare.outcome(run(name)) for name in ("whole", "split", "stalled"))


# What an outcome says of a `grow` step: the trunk's epoch, the epoch of the first fork, how it
# ended, the lines and the members off the trunk, and how many of those never joined.
GROWN = (
    "trunk_epoch",
    "first_fork",
    "ended",
    "lines_off_trunk",
    "members_off_trunk",
    "never_joined",
)


def grown(one: dict) -> list:
    return [one[key] for key in GROWN]


class Change(unittest.TestCase):
    def test_an_outcome_as_it_was_is_the_same(self):
        self.assertEqual(compare.change(outcome(), outcome()), "same")
        self.assertEqual(compare.change(SPLIT, dict(SPLIT)), "same")

    def test_an_outcome_that_differs_in_any_field_moved(self):
        for changed in (
            {"verdict": "split at epoch 12"},
            {"at_tip": 8},
            {"virtual_s": 121.0},
            {"conversation": "9c1d04e7b2"},
            {"conversation": None, "members_digest": None, "verdict": None, "at_tip": None},
        ):
            with self.subTest(changed=changed):
                self.assertEqual(compare.change(outcome(), outcome(**changed)), "moved")
        self.assertEqual(compare.change(SPLIT, WHOLE), "moved")
        self.assertEqual(compare.change(WHOLE, dict(WHOLE, **dict.fromkeys(GROWN))), "moved")


class Outcome(unittest.TestCase):
    def test_a_run_that_stayed_whole_ended_whole_with_no_one_off_the_trunk(self):
        self.assertEqual(grown(WHOLE), [50, None, "whole", 0, 0, 0])

    def test_a_run_that_split_counts_the_lines_and_members_off_its_trunk(self):
        # Seven main branches, nine members on them, eight behind on the trunk, tomo never joined.
        self.assertEqual(grown(SPLIT), [50, 18, "split", 7, 18, 1])

    def test_the_first_fork_is_the_lowest_epoch_a_line_forked_at(self):
        report = run("split")
        report["grow"]["lines"][4]["forked_at"] = 12

        self.assertEqual(compare.outcome(report)["first_fork"], 12)
        self.assertEqual(compare.unforked(compare.outcome(report)), 12)
        self.assertEqual([compare.unforked(one) for one in (WHOLE, STALLED)], [50, 4])

    def test_a_run_of_a_grow_step_has_no_verdict_and_no_members_at_the_tip_whatever_forks_said(
        self,
    ):
        reported = dict(run("split"), verdict="split at epoch 18", at_tip=42)

        said = compare.outcome(reported)

        self.assertEqual((said["verdict"], said["at_tip"]), (None, None))
        self.assertEqual(said, SPLIT)

    def test_a_member_behind_on_a_branch_is_off_the_trunk_once(self):
        report = run("split")
        report["grow"]["behind"].append(report["grow"]["lines"][1]["members"][0])

        self.assertEqual(compare.outcome(report)["members_off_trunk"], 18)

    def test_a_run_that_never_forked_and_whose_trunk_stopped_short_stalled(self):
        self.assertEqual(grown(STALLED), [4, None, "stalled", 0, 2, 1])

    def test_whether_the_trunk_got_where_the_step_grew_is_the_runners_ending(self):
        reached, short = run("stalled"), run("whole")
        reached["grow"]["ended"] = "the trunk reached epoch 4"
        short["grow"]["ended"] = "every member of the plan was seated"

        self.assertEqual(compare.outcome(reached)["ended"], "behind")
        self.assertEqual(compare.outcome(short)["ended"], "stalled")

    def test_a_run_that_reached_the_epoch_unforked_is_behind_while_members_are_behind(self):
        report = run("whole")
        report["grow"]["behind"] = ["raya"]

        self.assertEqual(compare.outcome(report)["ended"], "behind")

    def test_a_run_without_a_grow_step_has_none_of_its_six_and_the_verdict_forks_gave(self):
        checked = {"as_expected": True, "differences": []}
        report = dict(run("whole"), grow=None, check=checked, verdict="whole", at_tip=51)

        said = compare.outcome(report)

        self.assertEqual(grown(said), [None] * 6)
        self.assertEqual((said["verdict"], said["at_tip"]), ("whole", 51))
        self.assertEqual(said["came_to"], "as expected")

    def test_a_row_says_how_a_run_of_a_grow_step_ended_where_a_verdict_stands(self):
        behind = dict(WHOLE, ended="behind", members_off_trunk=1)
        runs = [SPLIT, WHOLE, behind, STALLED]

        rows = compare.render(runs, runs)[0].splitlines()

        at = rows[0].index("verdict")
        self.assertEqual(
            [row[at:].split("  ")[0] for row in rows[1:5]],
            ["split at epoch 18", "whole", "whole, members behind", "stalled at epoch 4"],
        )

    def test_the_members_ids_come_to_one_digest_in_member_order(self):
        report = run("whole")
        self.assertEqual(WHOLE["members_digest"], "1f228bfa64e58b9a")
        report["nodes"][:2] = reversed(report["nodes"][:2])

        self.assertNotEqual(compare.outcome(report)["members_digest"], WHOLE["members_digest"])


class Words(unittest.TestCase):
    def test_the_comment_words_how_a_grow_run_ended_as_compare_does(self):
        path = Path(__file__).resolve().parents[3] / ".github" / "scripts" / "simulate.py"
        spec = importlib.util.spec_from_file_location("simulate", path)
        simulate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(simulate)
        for ended in ("split", "stalled", "whole", "behind"):
            outcome = {"verdict": None, "ended": ended, "first_fork": 7, "trunk_epoch": 23}
            with self.subTest(ended=ended):
                self.assertEqual(simulate.came_out(outcome), compare._verdict(outcome))


class Render(unittest.TestCase):
    def test_a_run_that_moved_is_counted_and_its_row_keeps_the_columns(self):
        rerolled = dict(SPLIT, conversation=STALLED["conversation"])

        printed, held = compare.render([SPLIT, SPLIT], [rerolled, WHOLE])

        rows = printed.splitlines()
        self.assertFalse(held)
        self.assertEqual(rows[1].split()[:3], ["1", "0", "moved"])
        self.assertEqual(rows[1].index("split at epoch 18"), rows[0].index("verdict"))
        self.assertEqual(rows[2].split()[0], "was")
        self.assertEqual(rows[-1], "0 of 2 runs came out as expected")


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
        self.assertEqual(rows[2].split()[:3], ["2", "1", "moved"])
        self.assertEqual(rows[3].split()[0], "was")
        self.assertEqual(rows[4:], ["1 of 2 runs came out as expected"])

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


class HeldRuns(Scratch):
    """A directory of files of expected outcomes, one a scenario."""

    def hold(self, name: str, held: str) -> None:
        (self.scratch / f"{name}.json").write_text(held, encoding="utf-8")

    def test_each_scenario_holds_as_many_runs_as_its_file_has_outcomes(self):
        self.hold("grow-waku-loss-5", json.dumps([WHOLE, SPLIT, STALLED]))
        self.hold("50-epochs-by-saro-waku-loss-1", json.dumps([WHOLE, SPLIT]))

        self.assertEqual(
            compare.held_runs(self.scratch),
            [("50-epochs-by-saro-waku-loss-1", 2), ("grow-waku-loss-5", 3)],
        )

    def test_a_file_that_cannot_be_read_is_refused_by_its_name(self):
        self.hold("grow-waku-loss-5", json.dumps([WHOLE, SPLIT, STALLED]))
        self.hold("grow-waku-loss-1", "<<<<<<< HEAD")

        with self.assertRaises(compare.Unreadable) as refused:
            compare.held_runs(self.scratch)

        self.assertTrue(
            str(refused.exception).startswith(f"{self.scratch / 'grow-waku-loss-1.json'}: not JSON")
        )
