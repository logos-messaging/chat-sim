import contextlib
import io
import json
import shutil
import unittest
from fractions import Fraction
from pathlib import Path
from typing import Optional, Tuple

from sim import compare, matrix, scenario
from sim.cli import main
from sim.matrix import Run, Side

from tests import FIXTURES, Scratch
from tests.test_compare import GROWN, SPLIT, STALLED, WHOLE

SCENARIO = "50-epochs-by-saro-waku-loss-1"
GOAL = "50-epochs-by-saro-waku-loss-{}"
# The goal on a GroupV3 group.
GROUPV3 = "50-epochs-by-saro-waku-loss-{}-groupv3"
# What the jobs of the pr suite uploaded at a revision: seeds 0 and 1 of three rates, as the
# probe at logos-chat fa5282a3 ran them, each `run.json` cut to what the matrix reads.
UPLOADED = FIXTURES / "matrix"

# The run of SPLIT, first forked at epoch 18 at 1% and seed 0, as it could come out at another
# revision.
LATER = dict(SPLIT, first_fork=20)
SOONER = dict(SPLIT, first_fork=12)
DREW = dict(SPLIT, conversation="7c3e9a01b2")
SLOWER = dict(SPLIT, virtual_s=SPLIT["virtual_s"] + 1)
CRASHED = dict(SPLIT, conversation=None, members_digest=None, **dict.fromkeys(GROWN))
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


def sides(**scenarios: tuple) -> Side:
    """A side with a run at each seed from 0 of each scenario given by name, of each outcome
    given."""
    runs = {}
    for name, outcomes in scenarios.items():
        loss = scenario.read(matrix.SCENARIOS / f"{name}.json").network.loss
        for seed, one in enumerate(outcomes):
            runs[(name, seed)] = Run(seed + 1, loss, dict(one, seed=seed))
    return Side(runs)


def result(head: Side, base: Optional[Side]) -> dict:
    """The result of the one scenario of `head` beside `base`."""
    ((one,),) = [group["results"] for group in matrix.matrix(head, base)["groups"]]
    return one


class SignTest(unittest.TestCase):
    """The exact two-sided sign test of the seeds whose run first forked later at the head
    against those whose run first forked earlier."""

    def test_three_seeds_never_reach_alpha_as_their_least_p_is_a_quarter(self):
        tested = [matrix.sign_test(later, 3 - later) for later in range(4)]

        self.assertEqual(min(tested), Fraction(1, 4))
        self.assertEqual(tested, [Fraction(1, 4), 1, 1, Fraction(1, 4)])
        self.assertTrue(all(p > matrix.ALPHA for p in tested))

    def test_at_twenty_seeds_17_against_3_is_beyond_chance_and_16_against_4_is_not(self):
        for later, earlier, p, beyond in ((17, 3, 0.0026, True), (16, 4, 0.0118, False)):
            for tested in (matrix.sign_test(later, earlier), matrix.sign_test(earlier, later)):
                with self.subTest(later=later, earlier=earlier):
                    self.assertEqual(round(float(tested), 4), p)
                    self.assertEqual(tested <= matrix.ALPHA, beyond)

    def test_the_three_rates_of_logos_chat_349_are_within_chance(self):
        for later, earlier, p, exact in (
            (8, 5, 0.581, Fraction(595, 1024)),
            (3, 7, 0.344, Fraction(11, 32)),
            (4, 6, 0.754, Fraction(193, 256)),
        ):
            with self.subTest(later=later, earlier=earlier):
                tested = matrix.sign_test(later, earlier)
                self.assertEqual(tested, exact)
                self.assertEqual(round(float(tested), 3), p)

    def test_as_many_seeds_each_way_or_none_either_way_is_1(self):
        self.assertEqual(matrix.sign_test(5, 5), 1)
        self.assertEqual(matrix.sign_test(0, 0), 1)
        self.assertIsInstance(matrix.sign_test(0, 0), Fraction)


class Result(unittest.TestCase):
    """The result of a scenario's cells and the verdict over a group's, one rule of their order a
    test."""

    def test_every_cell_as_at_the_base_is_the_same(self):
        said = result(side(SPLIT, WHOLE, STALLED), side(SPLIT, WHOLE, STALLED))

        self.assertEqual(
            said,
            {
                "scenario": SCENARIO,
                "rate": "1%",
                "seeds": [0, 1, 2],
                "later": 0,
                "earlier": 0,
                "zero": 3,
                "p": 1.0,
                "result": "same",
            },
        )

    def test_seeds_that_forked_earlier_beyond_chance_are_earlier_and_later_later(self):
        base = side(*[SPLIT] * 20)
        for head, said in (
            (side(*[SOONER] * 17, *[LATER] * 3), "earlier"),
            (side(*[LATER] * 17, *[SOONER] * 3), "later"),
        ):
            with self.subTest(said=said):
                one = result(head, base)
                self.assertEqual(one["result"], said)
                self.assertEqual(round(one["p"], 4), 0.0026)
                self.assertEqual(one["later"] + one["earlier"], 20)

    def test_seeds_apart_within_chance_are_within(self):
        for head, base, p in (
            (side(*[SOONER] * 16, *[LATER] * 4), side(*[SPLIT] * 20), 0.0118),
            (side(SOONER, SOONER, SOONER), side(SPLIT, SPLIT, SPLIT), 0.25),
        ):
            with self.subTest(p=p):
                one = result(head, base)
                self.assertEqual((one["result"], round(one["p"], 4)), ("within", p))

    def test_a_cell_that_moved_at_the_same_first_fork_counts_on_neither_side(self):
        one = result(side(DREW, SLOWER, SOONER), side(SPLIT, SPLIT, SPLIT))

        self.assertEqual([one[key] for key in ("later", "earlier", "zero")], [0, 1, 2])
        self.assertEqual(one["result"], "within")
        self.assertEqual(result(side(DREW, SLOWER), side(SPLIT, SPLIT))["result"], "within")

    def test_a_run_that_drew_other_ids_moved_and_its_epochs_count(self):
        drew_sooner = dict(SOONER, conversation=DREW["conversation"])
        made = matrix.matrix(side(*[drew_sooner] * 8), side(*[SPLIT] * 8))

        self.assertEqual({cell["change"] for cell in made["cells"]}, {"moved"})
        self.assertEqual({cell["d"] for cell in made["cells"]}, {-6})
        self.assertEqual(made["verdict"], "earlier")

    def test_a_head_whose_jobs_built_no_run_and_one_could_not_build_is_unbuilt(self):
        base = side(SPLIT, SPLIT)
        self.assertEqual(result(side(UNBUILT, UNBUILT), base)["result"], "unbuilt")
        self.assertEqual(result(side(UNBUILT, None), base)["result"], "unbuilt")
        self.assertEqual(result(side(UNBUILT, UNBUILT), side(UNBUILT))["result"], "unbuilt")

    def test_a_head_one_of_whose_jobs_could_not_build_has_no_outcome_in_that_cell(self):
        made = matrix.matrix(side(UNBUILT, SOONER), side(SPLIT, SPLIT))

        self.assertEqual(made["verdict"], "no-outcome")
        self.assertEqual((made["counts"]["no-outcome"], made["counts"]["moved"]), (1, 1))
        self.assertEqual(made["head"], {"runs": 1, "unbuilt": 1})
        self.assertEqual(
            [cell["unbuilt"] for cell in made["cells"]],
            [{"head": True, "base": False}, {"head": False, "base": False}],
        )
        (one,) = made["groups"][0]["results"]
        self.assertEqual([one[key] for key in ("later", "earlier", "zero", "p")], [0, 1, 0, 1.0])

    def test_a_cell_with_no_run_at_the_head_or_one_that_read_no_result_has_no_outcome(self):
        for head in (side(SPLIT, None), side(SPLIT, CRASHED)):
            with self.subTest(head=head):
                made = matrix.matrix(head, side(SPLIT, SPLIT))
                counts = made["counts"]
                self.assertEqual(
                    (made["verdict"], counts["no-outcome"], counts["compared"]),
                    ("no-outcome", 1, 1),
                )

    def test_a_cell_with_no_outcome_at_the_base_is_left_out_of_the_sign_test(self):
        made = matrix.matrix(side(SOONER, SOONER), side(UNBUILT, SPLIT))

        self.assertEqual(made["verdict"], "within")
        self.assertEqual([cell["change"] for cell in made["cells"]], [None, "moved"])
        self.assertEqual(made["cells"][0]["unbuilt"], {"head": False, "base": True})
        (one,) = made["groups"][0]["results"]
        self.assertEqual(
            [one[key] for key in ("seeds", "later", "earlier", "zero")], [[0, 1], 0, 1, 0]
        )

    def test_a_cell_with_no_outcome_at_the_head_is_said_before_one_at_the_base(self):
        for base in (side(UNBUILT, SPLIT), side(UNBUILT, UNBUILT)):
            with self.subTest(base=base):
                made = matrix.matrix(side(None, SOONER), base)
                self.assertEqual(made["verdict"], "no-outcome")

    def test_a_base_that_did_not_build_or_has_no_outcome_is_no_base_and_the_heads_stand(self):
        for base in (side(UNBUILT, UNBUILT), Side({}), side(CRASHED, CRASHED)):
            with self.subTest(base=base):
                made = matrix.matrix(side(SOONER, SPLIT), base)
                self.assertEqual((made["verdict"], made["counts"]["compared"]), ("no-base", 0))
                self.assertEqual([cell["change"] for cell in made["cells"]], [None, None])
                self.assertEqual([cell["epochs"]["head"] for cell in made["cells"]], [12, 18])
        made = matrix.matrix(side(SPLIT), side(UNBUILT))
        self.assertEqual(made["base"], {"runs": 0, "unbuilt": 1})

    def test_with_no_base_a_head_has_no_verdict_but_what_went_wrong_at_it(self):
        for head, said in (
            (side(SPLIT, SPLIT), None),
            (side(SPLIT, CRASHED), "no-outcome"),
            (side(UNBUILT, UNBUILT), "unbuilt"),
        ):
            with self.subTest(said=said):
                self.assertEqual(result(head, None)["result"], said)

    def test_a_head_with_no_run_and_no_base_has_no_outcome(self):
        made = matrix.matrix(Side({}), None)

        self.assertEqual(
            (made["verdict"], made["counts"]["cells"], made["groups"]), ("no-outcome", 0, [])
        )

    def test_a_group_s_verdict_is_the_worst_of_its_rates_and_the_matrix_s_of_its_groups(self):
        names = (GOAL.format(1), GOAL.format(5), GROUPV3.format(1))
        base = sides(**{name: (SPLIT,) * 8 for name in names})
        some_sooner = (SOONER, *[SPLIT] * 7)
        for runs, results, verdicts, worst in (
            (
                ((SOONER,) * 8, (SPLIT,) * 8, some_sooner),
                ["earlier", "same", "within"],
                ["earlier", "within"],
                "earlier",
            ),
            (
                ((SPLIT,) * 8, some_sooner, (LATER,) * 8),
                ["same", "within", "later"],
                ["within", "later"],
                "later",
            ),
        ):
            with self.subTest(results=results):
                made = matrix.matrix(sides(**dict(zip(names, runs))), base)
                self.assertEqual(
                    [one["result"] for group in made["groups"] for one in group["results"]],
                    results,
                )
                self.assertEqual([group["verdict"] for group in made["groups"]], verdicts)
                self.assertEqual(made["verdict"], worst)

    def test_the_worst_result_is_what_went_wrong_at_the_head_then_what_moved_then_the_base(self):
        self.assertEqual(
            matrix.WORST,
            ("unbuilt", "no-outcome", "earlier", "later", "within", "no-base", "same"),
        )
        some_sooner = (SOONER, *[SPLIT] * 7)
        for at_1, at_5, base_at_5, worst in (
            ((CRASHED, *[SPLIT] * 7), (SOONER,) * 8, (SPLIT,) * 8, "no-outcome"),
            ((LATER,) * 8, (SOONER,) * 8, (SPLIT,) * 8, "earlier"),
            ((LATER,) * 8, some_sooner, (SPLIT,) * 8, "later"),
            (some_sooner, (SPLIT,) * 8, (SPLIT,) * 7, "within"),
            ((SPLIT,) * 8, (SPLIT,) * 8, (), "no-base"),
        ):
            with self.subTest(worst=worst):
                head = sides(**{GOAL.format(1): at_1, GOAL.format(5): at_5})
                base = sides(**{GOAL.format(1): (SPLIT,) * 8, GOAL.format(5): base_at_5})
                self.assertEqual(matrix.matrix(head, base)["verdict"], worst)


class Cells(unittest.TestCase):
    def test_a_cell_holds_both_outcomes_its_change_each_sides_epochs_and_ending_and_d(self):
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
                "change": "moved",
                "epochs": {"head": 12, "base": 18},
                "ended": {"head": "split", "base": "split"},
                "d": -6,
            },
        )

    def test_d_is_the_head_s_epochs_without_a_fork_less_the_base_s(self):
        behind = dict(WHOLE, ended="behind", members_off_trunk=1)
        for was, now, change, d in (
            (SPLIT, LATER, "moved", 2),
            (WHOLE, SPLIT, "moved", -32),
            (SPLIT, STALLED, "moved", -14),
            (WHOLE, behind, "moved", 0),
            (SPLIT, DREW, "moved", 0),
            (SPLIT, SPLIT, "same", 0),
        ):
            with self.subTest(now=now, was=was):
                (cell,) = matrix.matrix(side(now), side(was))["cells"]
                self.assertEqual((cell["change"], cell["d"]), (change, d))

    def test_the_epochs_without_a_fork_are_the_first_splits_or_else_the_trunks(self):
        cells = matrix.matrix(side(SPLIT, WHOLE, STALLED))["cells"]

        self.assertEqual([cell["epochs"]["head"] for cell in cells], [18, 50, 4])
        self.assertEqual([cell["ended"]["head"] for cell in cells], ["split", "whole", "stalled"])

    def test_a_cell_only_the_base_has_holds_no_outcome_at_the_head(self):
        cell = matrix.matrix(side(SPLIT, None), side(SPLIT, WHOLE))["cells"][1]

        self.assertEqual((cell["seed"], cell["run"], cell["head"]), (1, 2, None))
        self.assertEqual((cell["change"], cell["ended"]["head"], cell["d"]), (None, None, None))
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
        behind = dict(WHOLE, ended="behind", members_off_trunk=1)

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

    def run_json(self, side: Path, rate: str, seed: int) -> Path:
        name = GOAL.format(rate)
        return side / f"sim-revision-{name}-{seed + 1}" / name / str(seed + 1) / "run.json"

    def edit(self, side: Path, rate: str, seed: int, **changed) -> None:
        file = self.run_json(side, rate, seed)
        report = json.loads(file.read_text(encoding="utf-8"))
        report.update(changed)
        file.write_text(json.dumps(report), encoding="utf-8")

    def fork(self, side: Path, rate: str, seed: int, at: int) -> None:
        """Has the first line off the trunk of a run fork at epoch `at`."""
        grow = json.loads(self.run_json(side, rate, seed).read_text(encoding="utf-8"))["grow"]
        grow["lines"][1]["forked_at"] = at
        self.edit(side, rate, seed, grow=grow)

    def test_the_runs_are_read_at_any_depth_by_scenario_and_seed(self):
        read = matrix.read(UPLOADED)

        self.assertEqual(
            sorted(read.runs),
            [(GOAL.format(rate), seed) for rate in ("0.1", "1", "5") for seed in (0, 1)],
        )
        one = read.runs[(GOAL.format("5"), 1)]
        self.assertEqual((one.number, one.loss), (2, 0.05))
        self.assertEqual((one.outcome["first_fork"], one.outcome["ended"]), (3, "split"))

    def test_a_head_beside_a_base_that_split_later_once_and_drew_other_ids_once_is_within(self):
        base = self.uploaded("base")
        self.fork(base, "0.1", 1, 48)
        self.edit(base, "5", 1, conversation="7c3e9a01b2")

        made = matrix.matrix(matrix.read(UPLOADED), matrix.read(base))

        self.assertEqual(made["verdict"], "within")
        self.assertEqual(
            [
                (cell["seed"], cell["change"], cell["epochs"]["head"], cell["d"])
                for cell in made["cells"]
            ],
            [
                (0, "same", 50, 0),
                (1, "moved", 46, -2),
                (0, "same", 18, 0),
                (1, "same", 9, 0),
                (0, "same", 8, 0),
                (1, "moved", 3, 0),
            ],
        )
        self.assertEqual(made["cells"][1]["epochs"], {"head": 46, "base": 48})
        self.assertEqual(
            [
                (one["rate"], one["result"], one["later"], one["earlier"], one["zero"])
                for one in made["groups"][0]["results"]
            ],
            [("0.1%", "within", 0, 1, 1), ("1%", "same", 0, 0, 2), ("5%", "within", 0, 0, 2)],
        )
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
