import unittest

from sim import table

NETWORK = {"seed": 7, "loss": 0.05, "welcome_loss": 0, "delay_ms": 0}


def withheld(**changed) -> dict:
    """What a run says was withheld: here from pax, the candidate of raya, which was applied."""
    whole = {"from": "pax", "seat": 1, "app_id": "38306230", "minter": "raya", "applied": True}
    whole.update(changed)
    return whole


def report(**changed) -> dict:
    """What a run of three reports: saro seats two, and pax posts."""
    ids = (("saro", "a3f91c07", "7361726f"), ("raya", "7b1e44d9", "72617961"))
    whole = {
        "members": 3,
        "ok": True,
        "stopped": None,
        "steps": 2,
        "steps_done": 2,
        "group": "v2",
        "timers": "fast",
        "seed": 1,
        "network": None,
        "load": {"start": [1.5, 1.25, 1.0], "end": [2.625, 2.25, 2.0]},
        "nodes": [
            {
                "name": name,
                "installation": installation,
                "account": account,
                "seated": True,
                "app_id": installation + "00",
                "inbound_errors": {},
            }
            for name, installation, account in (*ids, ("pax", "e09a3f52", "706178"))
        ],
        "batches": [{"joiners": 2, "members": 3, "seated_s": 1.25, "wall_s": 0.046875}],
        "posts": [
            {
                "poster": "pax",
                "readers": 2,
                "unread": [],
                "read": {"count": 2, "median": 0.0, "worst": 0.05},
            }
        ],
        "holds": [],
        "conversation": "2a30d6a2c1",
        "withheld": None,
        "check": {"as_expected": True, "differences": []},
        "unchecked": None,
        "verdict": "whole",
        "at_tip": 3,
        "seated": 3,
        "grow": None,
        "rig": table.ONE_PROCESS,
        "virtual_s": 1.35,
        "wall_s": 0.0625,
    }
    whole.update(changed)
    return whole


def modelled(**changed) -> dict:
    """What `report` reports with a network model, through which no member lost a frame."""
    whole = report(network=NETWORK, **changed)
    for node in whole["nodes"]:
        node["network"] = {"frames": {"arrived": 4, "passed": 4, "held": 0}}
        node["network_dropped"] = {}
    return whole


def differing(*differences: str, **changed) -> dict:
    check = {"as_expected": False, "differences": list(differences)}
    return report(ok=False, check=check, **changed)


class Render(unittest.TestCase):
    def test_a_run_of_one_member_says_so(self):
        self.assertEqual(
            table.render(report(members=1)).splitlines()[0],
            "1 member, fast group timers, one process, 1.350s on a virtual clock in 0.062s of "
            "wall time, seed 1",
        )

    def test_what_was_withheld_from_whom_is_told_after_the_verdict(self):
        text = table.render(modelled(withheld=withheld(), verdict="split at epoch 4"))

        self.assertIn(
            "\nsplit at epoch 4: 3 of 3 members at the tip\n"
            "withheld from pax: the candidate of raya, applied\n"
            "the report is as expected\n",
            text,
        )
        self.assertIn(
            "\nwhole: 3 of 3 members at the tip\n"
            "withheld from pax: the candidate of saro, not applied\n"
            "the report is as expected\n",
            table.render(modelled(withheld=withheld(minter="saro", applied=False))),
        )

    def test_a_withheld_candidate_whose_app_id_is_no_members_is_said_so(self):
        self.assertIn(
            "\nwithheld from pax: the candidate of no member of the run, applied\n",
            table.render(modelled(withheld=withheld(minter=None))),
        )

    def test_a_run_that_stopped_is_told_with_what_its_check_found(self):
        text = table.render(
            differing(
                "raya (7b1e44d9): never joined, expected tip",
                "post 1 of pax (e09a3f52): not in the report",
                stopped="the roster of 3 members was not held within the budget by: raya",
                steps_done=0,
                at_tip=2,
            )
        )

        self.assertIn(
            "\nstopped after 0 of 2 steps: "
            "the roster of 3 members was not held within the budget by: raya\n"
            "whole: 2 of 3 members at the tip\n"
            "not as expected: raya (7b1e44d9): never joined, expected tip\n"
            "not as expected: post 1 of pax (e09a3f52): not in the report\n"
            "load average ",
            text,
        )

    def test_logs_that_no_report_could_be_drawn_of_are_not_checked(self):
        unchecked = "forks check ended with status 2: forks: no log in /run"
        text = table.render(
            report(ok=False, check=None, unchecked=unchecked, verdict=None, at_tip=None)
        )

        self.assertIn(
            f"\nno report: 0 of 3 members at the tip\nnot checked: {unchecked}\nload average ",
            text,
        )

    def test_a_check_that_was_not_made_is_told_after_the_verdict(self):
        text = table.render(
            report(ok=False, check=None, unchecked="forks check ended with signal 9")
        )

        self.assertIn(
            "\nwhole: 3 of 3 members at the tip\n"
            "not checked: forks check ended with signal 9\n"
            "load average ",
            text,
        )

    def test_durations_are_named_each_with_its_milliseconds(self):
        timers = {"consensus_timeout": 2000, "voting_delay": 100}

        self.assertEqual(
            table.render(report(timers=timers)).splitlines()[0],
            "3 members, group timers consensus_timeout 2000 ms, voting_delay 100 ms, "
            "one process, 1.350s on a virtual clock in 0.062s of wall time, seed 1",
        )
        self.assertEqual(
            table.render(report(timers={})).splitlines()[0],
            "3 members, default group timers, one process, 1.350s on a virtual clock in 0.062s "
            "of wall time, seed 1",
        )


class Row(unittest.TestCase):
    """A sweep's row for a run."""

    def test_a_run_that_differs_says_the_first_difference_and_how_many_more(self):
        one = differing("verdict: forked at epoch 4, expected whole", verdict="forked at epoch 4")
        three = differing(
            "verdict: forked at epoch 4, expected whole",
            "pax (e09a3f52): behind, expected tip",
            "post 1 of pax (e09a3f52): not in the report",
            verdict="forked at epoch 4",
            at_tip=2,
        )

        self.assertEqual(
            table.row(12, one),
            " 12      1.4      3 of 3  forked at epoch 4       "
            "verdict: forked at epoch 4, expected whole; seed 1",
        )
        self.assertEqual(
            table.row(12, three),
            " 12      1.4      2 of 3  forked at epoch 4       "
            "verdict: forked at epoch 4, expected whole, and 2 more; seed 1",
        )

    def test_a_run_whose_steps_stopped_says_what_stopped_them_and_what_its_check_found(self):
        stopped = differing(
            "raya (7b1e44d9): never joined, expected tip",
            stopped="the roster of 3 members was not held within the budget by: raya",
            at_tip=2,
        )

        self.assertEqual(
            table.row(2, stopped),
            "  2      1.4      2 of 3  whole                   "
            "raya (7b1e44d9): never joined, expected tip; "
            "stopped: the roster of 3 members was not held within the budget by: raya; seed 1",
        )

    def test_a_run_whose_check_was_not_made_says_how_the_check_ended(self):
        unchecked = report(ok=False, check=None, unchecked="forks check ended with signal 9")

        self.assertEqual(
            table.row(3, unchecked),
            "  3      1.4      3 of 3  whole                   "
            "not checked: forks check ended with signal 9; seed 1",
        )

    def test_a_run_with_a_network_model_names_its_seed_after_what_stopped_it(self):
        stopped = differing(
            "raya (7b1e44d9): never joined, expected tip",
            stopped="the roster of 3 members was not held within the budget by: raya",
            network=dict(NETWORK, seed=2**64 - 1),
        )
        self.assertTrue(
            table.row(2, stopped).endswith(
                "; stopped: the roster of 3 members was not held within the budget by: raya; "
                f"seed 1; network seed {2**64 - 1}"
            )
        )

    def test_a_run_that_withheld_a_candidate_says_whose_and_whether_applied(self):
        told = report(network=NETWORK, withheld=withheld())

        self.assertTrue(
            table.row(1, told).endswith(
                "  as expected; seed 1; network seed 7; "
                "withheld from pax: the candidate of raya, applied"
            )
        )
        told["withheld"]["applied"] = False
        self.assertTrue(
            table.row(1, told).endswith(
                "  as expected; seed 1; network seed 7; "
                "withheld from pax: the candidate of raya, not applied"
            )
        )


if __name__ == "__main__":
    unittest.main()
