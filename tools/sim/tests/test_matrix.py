import contextlib
import io
import json
import shutil
import unittest
from pathlib import Path
from typing import Optional, Tuple

from sim import compare, matrix
from sim.cli import main
from sim.matrix import Run, Side

from tests import FIXTURES, Scratch
from tests.test_compare import SPLIT, STALLED, WHOLE

SCENARIO = "50-epochs-by-saro-waku-loss-1"
GOAL = "50-epochs-by-saro-waku-loss-{}"
# The goal on a GroupV3 group.
GROUPV3 = "50-epochs-by-saro-waku-loss-{}-groupv3"
# What the jobs of the pr suite uploaded at a revision: seeds 0 and 1 of three rates, as the
# probe at logos-chat fa5282a3 ran them, each `run.json` cut to what the matrix reads.
UPLOADED = FIXTURES / "matrix"

# The run of SPLIT, split at epoch 18 at 1% and seed 0, as it could come out at another revision.
LATER = dict(SPLIT, verdict="split at epoch 20")
SOONER = dict(SPLIT, verdict="split at epoch 12")
DREW = dict(SPLIT, conversation="7c3e9a01b2")
SLOWER = dict(SPLIT, virtual_s=SPLIT["virtual_s"] + 1)
CRASHED = dict(SPLIT, conversation=None, members_digest=None, verdict=None, at_tip=None)
# A run whose job could not build chat-sim, as `side` takes it.
UNBUILT = object()


def side(*outcomes: object) -> Side:
    """A side with a run of SCENARIO at 1% loss at each seed from 0 that has an outcome given,
    or that UNBUILT is given for."""
    runs = {
        (SCENARIO, seed): Run(seed + 1, 0.01, None if one is UNBUILT else dict(one, seed=seed))
        for seed, one in enumerate(outcomes)
        if one is not None
    }
    return Side(runs)


def verdict(head: Side, base: Optional[Side]) -> Tuple[str, dict]:
    made = matrix.matrix(head, base)
    return made["verdict"], made["counts"]


class Verdict(unittest.TestCase):
    """The verdict over a matrix's cells, one rule of its order a test."""

    def test_every_cell_as_at_the_base_is_the_same(self):
        said, counts = verdict(side(SPLIT, WHOLE, STALLED), side(SPLIT, WHOLE, STALLED))

        self.assertEqual(said, "same")
        self.assertEqual((counts["cells"], counts["compared"], counts["same"]), (3, 3, 3))

    def test_cells_better_and_none_worse_are_better(self):
        said, counts = verdict(side(LATER, WHOLE, SPLIT), side(SPLIT, WHOLE, SPLIT))

        self.assertEqual((said, counts["better"], counts["same"]), ("better", 1, 2))

    def test_two_cells_worse_and_none_better_are_worse(self):
        said, counts = verdict(side(SOONER, SOONER, SPLIT), side(SPLIT, SPLIT, SPLIT))

        self.assertEqual((said, counts["worse"]), ("worse", 2))

    def test_one_cell_worse_and_none_better_is_a_hint(self):
        said, counts = verdict(side(SOONER, SPLIT, SPLIT), side(SPLIT, SPLIT, SPLIT))

        self.assertEqual((said, counts["worse"]), ("hint", 1))

    def test_cells_better_and_cells_worse_are_mixed(self):
        said, counts = verdict(side(LATER, SOONER, SPLIT), side(SPLIT, SPLIT, SPLIT))

        self.assertEqual((said, counts["better"], counts["worse"]), ("mixed", 1, 1))

    def test_cells_that_differ_by_their_draws_alone_are_re_rolled(self):
        said, counts = verdict(side(DREW, DREW, SPLIT), side(SPLIT, SPLIT, SPLIT))

        self.assertEqual((said, counts["re-rolled"], counts["same"]), ("re-rolled", 2, 1))

    def test_a_head_whose_jobs_built_no_run_and_one_could_not_build_is_unbuilt(self):
        base = side(SPLIT, SPLIT)
        self.assertEqual(verdict(side(UNBUILT, UNBUILT), base)[0], "unbuilt")
        self.assertEqual(verdict(side(UNBUILT, None), base)[0], "unbuilt")

    def test_a_head_one_of_whose_jobs_could_not_build_has_no_outcome_in_that_cell(self):
        made = matrix.matrix(side(UNBUILT, SOONER), side(SPLIT, SPLIT))

        self.assertEqual(made["verdict"], "no-outcome")
        self.assertEqual((made["counts"]["no-outcome"], made["counts"]["worse"]), (1, 1))
        self.assertEqual(made["head"], {"runs": 1, "unbuilt": 1})
        self.assertEqual(
            [cell["unbuilt"] for cell in made["cells"]],
            [{"head": True, "base": False}, {"head": False, "base": False}],
        )

    def test_a_cell_with_no_run_at_the_head_or_one_with_no_verdict_has_no_outcome(self):
        for head in (side(SPLIT, None), side(SPLIT, CRASHED)):
            with self.subTest(head=head):
                said, counts = verdict(head, side(SPLIT, SPLIT))
                self.assertEqual(
                    (said, counts["no-outcome"], counts["compared"]), ("no-outcome", 1, 1)
                )

    def test_a_changed_cell_counts_as_the_same(self):
        said, counts = verdict(side(SLOWER, SPLIT), side(SPLIT, SPLIT))

        self.assertEqual(said, "same")
        self.assertEqual((counts["changed"], counts["same"], counts["compared"]), (1, 1, 2))

    def test_a_re_rolled_cell_leaves_the_verdict_to_the_cells_that_moved_beside_it(self):
        base = side(SPLIT, SPLIT, SPLIT)
        for head, said in (
            (side(DREW, SOONER, SPLIT), "hint"),
            (side(DREW, SOONER, SOONER), "worse"),
            (side(DREW, LATER, SPLIT), "better"),
            (side(DREW, SLOWER, SPLIT), "re-rolled"),
        ):
            with self.subTest(said=said):
                self.assertEqual(verdict(head, base)[0], said)

    def test_with_a_base_that_did_not_build_no_cell_has_a_change_and_the_heads_stand(self):
        made = matrix.matrix(side(SOONER, SPLIT), side(UNBUILT, UNBUILT))

        self.assertEqual(made["verdict"], "no-base")
        self.assertEqual([cell["change"] for cell in made["cells"]], [None, None])
        self.assertEqual([cell["epochs"]["head"] for cell in made["cells"]], [12, 18])
        self.assertEqual(made["base"], {"runs": 0, "unbuilt": 2})

    def test_a_cell_the_base_did_not_build_has_no_change_and_the_others_decide(self):
        made = matrix.matrix(side(SOONER, SOONER), side(UNBUILT, SPLIT))

        self.assertEqual(made["verdict"], "hint")
        self.assertEqual([cell["change"] for cell in made["cells"]], [None, "worse"])
        self.assertEqual(made["cells"][0]["unbuilt"], {"head": False, "base": True})

    def test_with_no_base_or_a_base_with_no_outcome_no_cell_has_a_change(self):
        for base in (None, Side({}), side(CRASHED, CRASHED)):
            with self.subTest(base=base):
                said, counts = verdict(side(SPLIT, SPLIT), base)
                self.assertEqual((said, counts["compared"]), ("no-base", 0))

    def test_what_went_wrong_at_the_head_is_said_before_what_is_missing_at_the_base(self):
        unbuilt = side(UNBUILT, UNBUILT)
        self.assertEqual(verdict(side(UNBUILT, UNBUILT), unbuilt)[0], "unbuilt")
        self.assertEqual(verdict(side(SPLIT, None), unbuilt)[0], "no-outcome")

    def test_a_head_with_no_run_and_no_base_has_no_outcome(self):
        said, counts = verdict(Side({}), None)

        self.assertEqual((said, counts["cells"]), ("no-outcome", 0))

    def test_each_group_s_cells_have_the_verdict_of_the_same_rule_over_them_alone(self):
        def both(groupv2: tuple, groupv3: tuple) -> Side:
            return Side(
                {
                    (name.format(1), seed): Run(seed + 1, 0.01, dict(one, seed=seed))
                    for name, outcomes in ((GOAL, groupv2), (GROUPV3, groupv3))
                    for seed, one in enumerate(outcomes)
                }
            )

        base = both((SPLIT, SPLIT), (SPLIT, SPLIT))

        made = matrix.matrix(both((LATER, SPLIT), (SOONER, SOONER)), base)

        self.assertEqual(made["verdict"], "mixed")
        self.assertEqual(
            [(one["group"], one["verdict"]) for one in made["groups"]],
            [("v2", "better"), ("v3", "worse")],
        )
        self.assertEqual(
            [(one["counts"]["better"], one["counts"]["worse"]) for one in made["groups"]],
            [(1, 0), (0, 2)],
        )
        alone = matrix.matrix(side(SPLIT), side(SPLIT))["groups"]
        self.assertEqual([(one["group"], one["verdict"]) for one in alone], [("v2", "same")])


class Cells(unittest.TestCase):
    def test_a_cell_holds_both_outcomes_its_change_and_each_sides_epochs_and_ending(self):
        (cell,) = matrix.matrix(side(SOONER), side(SPLIT))["cells"]

        self.assertEqual(
            cell,
            {
                "scenario": SCENARIO,
                "group": "v2",
                "seed": 0,
                "run": 1,
                "loss": 0.01,
                "rate": "1%",
                "head": SOONER,
                "base": SPLIT,
                "unbuilt": {"head": False, "base": False},
                "change": "worse",
                "epochs": {"head": 12, "base": 18},
                "ended": {"head": "split", "base": "split"},
                "moved": {"part": "epochs", "head": 12, "base": 18},
            },
        )

    def test_a_cell_better_or_worse_says_the_part_of_the_standing_that_moved(self):
        behind = dict(WHOLE, verdict="whole, members behind", ended="behind", members_off_trunk=1)
        for was, now, moved in (
            (WHOLE, behind, {"part": "ended", "head": "behind", "base": "whole"}),
            (
                SPLIT,
                dict(SPLIT, lines_off_trunk=8),
                {"part": "lines_off_trunk", "head": 8, "base": 7},
            ),
            (
                SPLIT,
                dict(SPLIT, members_off_trunk=17),
                {"part": "members_off_trunk", "head": 17, "base": 18},
            ),
            (SPLIT, SLOWER, None),
            (SPLIT, DREW, None),
        ):
            with self.subTest(moved=moved):
                (cell,) = matrix.matrix(side(now), side(was))["cells"]
                self.assertEqual(cell["moved"], moved)

    def test_the_epochs_without_a_fork_are_the_first_splits_or_else_the_trunks(self):
        cells = matrix.matrix(side(SPLIT, WHOLE, STALLED))["cells"]

        self.assertEqual([cell["epochs"]["head"] for cell in cells], [18, 50, 4])
        self.assertEqual([cell["ended"]["head"] for cell in cells], ["split", "whole", "stalled"])

    def test_a_cell_only_the_base_has_holds_no_outcome_at_the_head(self):
        cell = matrix.matrix(side(SPLIT, None), side(SPLIT, WHOLE))["cells"][1]

        self.assertEqual((cell["seed"], cell["run"], cell["head"]), (1, 2, None))
        self.assertEqual((cell["change"], cell["ended"]["head"]), (None, None))
        self.assertEqual(cell["epochs"], {"head": None, "base": 50})
        self.assertEqual(cell["unbuilt"], {"head": False, "base": False})

    def test_a_run_asked_for_that_no_side_has_is_a_cell_with_no_outcome_at_either(self):
        asked = [(SCENARIO, number) for number in (1, 2, 3)]

        made = matrix.matrix(side(SPLIT, WHOLE), side(SPLIT), asked)

        self.assertEqual(made["verdict"], "no-outcome")
        self.assertEqual((made["counts"]["cells"], made["counts"]["no-outcome"]), (3, 1))
        cell = made["cells"][2]
        self.assertEqual((cell["seed"], cell["run"], cell["rate"]), (2, 3, "1%"))
        self.assertEqual((cell["head"], cell["base"], cell["change"]), (None, None, None))
        self.assertEqual(made["cells"][1]["change"], None)

    def test_a_run_asked_of_a_scenario_there_is_no_file_of_is_refused(self):
        with self.assertRaises(compare.Unreadable) as refused:
            matrix.matrix(side(SPLIT), None, [("no-such-scenario", 1)])
        self.assertIn("the scenario no-such-scenario", str(refused.exception))

    def test_a_run_read_of_a_scenario_there_is_no_file_of_is_refused(self):
        runs = {("no-such-scenario", 0): Run(1, 0.01, dict(SPLIT, seed=0))}
        with self.assertRaises(compare.Unreadable) as refused:
            matrix.matrix(Side(runs), None)
        self.assertIn("the scenario no-such-scenario", str(refused.exception))

    def test_the_cells_are_in_the_order_of_their_loss_then_seed(self):
        runs = {
            (GOAL.format(rate), seed): Run(seed + 1, loss, dict(SPLIT, seed=seed))
            for rate, loss in (("5", 0.05), ("0.1", 0.001), ("1", 0.01))
            for seed in (1, 0)
        }

        cells = matrix.matrix(Side(runs))["cells"]

        self.assertEqual(
            [(cell["loss"], cell["seed"]) for cell in cells],
            [(0.001, 0), (0.001, 1), (0.01, 0), (0.01, 1), (0.05, 0), (0.05, 1)],
        )

    def test_a_cell_is_of_its_scenario_s_group_and_the_groups_are_in_the_order_of_theirs(self):
        runs = {
            (name.format(rate), seed): Run(seed + 1, loss, dict(SPLIT, seed=seed))
            for name in (GROUPV3, GOAL)
            for rate, loss in (("1", 0.01), ("0.1", 0.001))
            for seed in (0, 1)
        }

        made = matrix.matrix(Side(runs))

        self.assertEqual(
            [(cell["group"], cell["loss"], cell["seed"]) for cell in made["cells"]],
            [
                (group, loss, seed)
                for group in ("v2", "v3")
                for loss in (0.001, 0.01)
                for seed in (0, 1)
            ],
        )
        self.assertEqual(
            [(one["scenario"], one["group"]) for one in made["summary"]],
            [
                (GOAL.format("0.1"), "v2"),
                (GOAL.format(1), "v2"),
                (GROUPV3.format("0.1"), "v3"),
                (GROUPV3.format(1), "v3"),
            ],
        )


class Summary(unittest.TestCase):
    def test_without_a_base_each_scenario_says_how_its_runs_ended_and_their_epochs(self):
        behind = dict(WHOLE, verdict="whole, members behind", ended="behind", members_off_trunk=1)

        (summary,) = matrix.matrix(side(WHOLE, SPLIT, STALLED, behind, CRASHED))["summary"]

        self.assertEqual(
            summary,
            {
                "scenario": SCENARIO,
                "group": "v2",
                "loss": 0.01,
                "rate": "1%",
                "runs": 5,
                "no-outcome": 1,
                "whole": 1,
                "behind": 1,
                "split": 1,
                "stalled": 1,
                "epochs": {"lowest": 4, "median": 34, "highest": 50},
                "split_at": {"lowest": 18, "median": 18, "highest": 18},
                "lines_off_trunk": {"median": 0},
                "members_off_trunk": {"median": 1.5},
                "never_joined": {"median": 0.5},
                "seated": {"median": 51},
            },
        )
        self.assertIsInstance(summary["epochs"]["median"], int)

    def test_a_scenario_with_no_outcome_has_no_epochs(self):
        (summary,) = matrix.matrix(side(CRASHED))["summary"]

        for spread in ("epochs", "split_at"):
            self.assertEqual(summary[spread], dict.fromkeys(("lowest", "median", "highest")))
        for median in ("lines_off_trunk", "members_off_trunk", "never_joined", "seated"):
            self.assertEqual(summary[median], {"median": None})

    def test_a_run_asked_for_that_left_nothing_counts_as_a_run_with_no_outcome(self):
        asked = [(SCENARIO, number) for number in range(1, 5)]

        (summary,) = matrix.matrix(side(WHOLE, UNBUILT), None, asked)["summary"]

        self.assertEqual((summary["runs"], summary["no-outcome"], summary["whole"]), (4, 3, 1))

    def test_with_a_base_there_is_no_summary(self):
        self.assertIsNone(matrix.matrix(side(SPLIT), side(SPLIT))["summary"])


class Read(Scratch):
    """The matrix of what the jobs uploaded, laid out as `actions/download-artifact` writes each
    artifact into a directory of its own."""

    def uploaded(self, name: str) -> Path:
        """A copy of UPLOADED under the scratch directory."""
        copy = self.scratch / name
        shutil.copytree(UPLOADED, copy)
        return copy

    def edit(self, side: Path, rate: str, seed: int, **changed) -> None:
        name = GOAL.format(rate)
        file = side / f"sim-revision-{name}-{seed + 1}" / name / str(seed + 1) / "run.json"
        report = json.loads(file.read_text(encoding="utf-8"))
        report.update(changed)
        file.write_text(json.dumps(report), encoding="utf-8")

    def test_the_runs_are_read_at_any_depth_by_scenario_and_seed(self):
        read = matrix.read(UPLOADED)

        self.assertEqual(
            sorted(read.runs),
            [(GOAL.format(rate), seed) for rate in ("0.1", "1", "5") for seed in (0, 1)],
        )
        one = read.runs[(GOAL.format("5"), 1)]
        self.assertEqual((one.number, one.loss), (2, 0.05))
        self.assertEqual(one.outcome["verdict"], "split at epoch 3")

    def test_a_head_beside_a_base_that_split_later_once_and_drew_other_ids_once_is_a_hint(self):
        base = self.uploaded("base")
        self.edit(base, "0.1", 1, verdict="split at epoch 48")
        self.edit(base, "5", 1, conversation="7c3e9a01b2")

        made = matrix.matrix(matrix.read(UPLOADED), matrix.read(base))

        self.assertEqual(made["verdict"], "hint")
        self.assertEqual(
            [(cell["seed"], cell["change"], cell["epochs"]["head"]) for cell in made["cells"]],
            [
                (0, "same", 50),
                (1, "worse", 46),
                (0, "same", 18),
                (1, "same", 9),
                (0, "same", 8),
                (1, "re-rolled", 3),
            ],
        )
        self.assertEqual(made["cells"][1]["epochs"], {"head": 46, "base": 48})
        self.assertEqual(made["head"], {"unbuilt": False, "runs": 6})

    def unbuilt(self, side: Path, rate: str, number: int) -> None:
        """The artifact of the job of run `number` at `rate` as one that could not build."""
        name = GOAL.format(rate)
        job = side / f"sim-revision-{name}-{number}"
        shutil.rmtree(job, ignore_errors=True)
        marker = job / name / str(number) / "unbuilt"
        marker.parent.mkdir(parents=True)
        marker.write_text("chat-sim does not build at 212cb05f\n", encoding="utf-8")

    def test_a_job_that_could_not_build_leaves_its_runs_with_no_outcome_and_the_rest_stand(self):
        head = self.uploaded("head")
        self.unbuilt(head, "1", 2)

        read = matrix.read(head)

        self.assertEqual(read.runs[(SCENARIO, 1)], Run(2, 0.01, None))
        made = matrix.matrix(read, matrix.read(UPLOADED))
        self.assertEqual((made["verdict"], made["counts"]["compared"]), ("no-outcome", 5))
        self.assertEqual(made["head"], {"runs": 5, "unbuilt": 1})

    def test_a_head_whose_every_job_could_not_build_is_unbuilt(self):
        head = self.scratch / "head"
        for rate in ("0.1", "1", "5"):
            for number in (1, 2):
                self.unbuilt(head, rate, number)

        made = matrix.matrix(matrix.read(head), matrix.read(UPLOADED))

        self.assertEqual(made["verdict"], "unbuilt")
        self.assertEqual(made["head"], {"runs": 0, "unbuilt": 6})
        self.assertEqual([cell["seed"] for cell in made["cells"]], [0, 1] * 3)

    def test_a_run_it_cannot_read_or_one_at_a_seed_already_read_is_refused(self):
        head = self.uploaded("head")
        name = GOAL.format("1")
        first = head / f"sim-revision-{name}-1" / name / "1" / "run.json"
        for placed, said in (
            (head / "again" / name / "3" / "run.json", f"a second run of {name} at seed 0"),
            (head / "again" / name / "1" / "unbuilt", f"a second run of {name} at seed 0"),
            (head / name / "run.json", "not in the directory of a run"),
            (head / name / "unbuilt", "not in the directory of a run"),
        ):
            with self.subTest(said=said):
                placed.parent.mkdir(parents=True)
                shutil.copyfile(first, placed)
                with self.assertRaises(compare.Unreadable) as refused:
                    matrix.read(head)
                self.assertIn(said, str(refused.exception))
                shutil.rmtree(placed.parent)
        processes = dict(json.loads(first.read_text("utf-8")), rig=None)
        for text, said in ((json.dumps(processes), "not a run in one process"), ("{", "not JSON")):
            with self.subTest(said=said):
                first.write_text(text, encoding="utf-8")
                with self.assertRaises(compare.Unreadable) as refused:
                    matrix.read(head)
                self.assertIn(said, str(refused.exception))


class CommandLine(Scratch):
    def sim(self, *arguments: str) -> Tuple[int, str, str]:
        printed, complained = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(complained):
            try:
                status = main(["matrix", *arguments])
            except SystemExit as exit:
                status = exit.code
        return status, printed.getvalue(), complained.getvalue()

    def test_it_writes_the_matrix_as_json_to_standard_output_or_to_a_file(self):
        written = matrix.matrix(matrix.read(UPLOADED), matrix.read(UPLOADED))

        status, printed, _ = self.sim(str(UPLOADED), "--base", str(UPLOADED))
        self.assertEqual((status, json.loads(printed)), (0, written))
        file = self.scratch / "matrix.json"
        status, printed, _ = self.sim(str(UPLOADED), "--out", str(file))
        self.assertEqual((status, printed), (0, ""))
        self.assertEqual(json.loads(file.read_text("utf-8")), matrix.matrix(matrix.read(UPLOADED)))

    def test_it_makes_a_cell_of_each_run_asked_of_each_side_whether_or_not_a_side_has_it(self):
        head = self.scratch / "head"
        shutil.copytree(UPLOADED, head)
        shutil.rmtree(head / f"sim-revision-{SCENARIO}-2")
        asked = self.scratch / "asked.json"
        sweeps = [
            {"side": side, "scenario": GOAL.format(rate), "from": first, "runs": 1}
            for side in ("revision", "base")
            for rate in ("0.1", "1", "5")
            for first in (1, 2)
        ]
        asked.write_text(json.dumps(sweeps), encoding="utf-8")

        status, printed, _ = self.sim(str(head), "--base", str(head), "--asked", str(asked))

        made = json.loads(printed)
        self.assertEqual((status, made["verdict"], made["counts"]["cells"]), (0, "no-outcome", 6))
        cell = made["cells"][3]
        self.assertEqual((cell["scenario"], cell["seed"], cell["run"]), (SCENARIO, 1, 2))
        self.assertEqual((cell["head"], cell["base"]), (None, None))

    def test_it_refuses_sweeps_asked_for_that_are_not_a_list_of_them(self):
        asked = self.scratch / "asked.json"
        for said in ({}, [{"scenario": SCENARIO, "from": 0, "runs": 1}], [{"from": 1, "runs": 1}]):
            with self.subTest(said=said):
                asked.write_text(json.dumps(said), encoding="utf-8")
                status, printed, complained = self.sim(str(UPLOADED), "--asked", str(asked))
                self.assertEqual((status, printed), (2, ""))
                self.assertIn(f"{asked}: not a list of sweeps", complained)

    def test_it_refuses_a_side_that_is_no_directory_and_a_run_it_cannot_read(self):
        nowhere = self.scratch / "nowhere"
        status, printed, complained = self.sim(str(nowhere))
        self.assertEqual((status, printed), (2, ""))
        self.assertIn(f"HEAD: no such directory: {nowhere}", complained)
        _, _, complained = self.sim(str(UPLOADED), "--base", str(nowhere))
        self.assertIn(f"--base: no such directory: {nowhere}", complained)

        unread = self.scratch / "head" / SCENARIO / "1" / "run.json"
        unread.parent.mkdir(parents=True)
        unread.write_text("[]", encoding="utf-8")
        status, _, complained = self.sim(str(self.scratch / "head"))
        self.assertEqual(status, 2)
        self.assertIn(f"{unread}: not a run's report", complained)


if __name__ == "__main__":
    unittest.main()
