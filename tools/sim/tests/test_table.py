import unittest

from sim import table

PAGE = "http://127.0.0.1:8750/#2a30d6a2c1"


def report(**changed) -> dict:
    whole = {
        "members": 3,
        "ok": True,
        "stopped": None,
        "steps": 2,
        "steps_done": 2,
        "timers": "fast",
        "max_message_kib": 512,
        "load": {"start": [1.5, 1.25, 1.0], "end": [2.625, 2.25, 2.0]},
        "nodes": [
            {
                "name": name,
                "dials": dials,
                "ready_s": ready,
                "start_s": start,
                "wired": {"peers": wired, "mesh_peers": wired_mesh},
                "end": {
                    "peers": end,
                    "mesh_peers": end_mesh,
                    "publish_refused": refused,
                    "inbound_dropped": dropped,
                },
                "exit_status": 0,
                "exit_signal": None,
                "ended": "asked",
                "inbound_errors": {},
            }
            for name, dials, ready, start, wired, wired_mesh, end, end_mesh, refused, dropped in (
                ("saro", [], 3.125, 3.125, 12, 11, 10, 9, 8, 7),
                ("raya", ["saro"], 6.25, 3.0, 22, 21, 20, 19, 18, 17),
                ("pax", ["saro", "raya"], 9.375, 2.875, 32, 31, 30, 29, 28, 27),
            )
        ],
        "batches": [
            {
                "joiners": 2,
                "members": 3,
                "joined_s": 0.75,
                "seated_s": 0.875,
                "welcome_bytes": [7121, 7121],
            }
        ],
        "posts": [
            {
                "poster": "pax",
                "readers": 2,
                "unread": [],
                "read": {"count": 2, "median": 0.0625, "worst": 0.125},
                "delivered": {"count": 2, "median": 0.03125, "worst": 0.0625},
                "lookup_mean": 0.046875,
                "lookups": 2,
            }
        ],
        "conversation": "2a30d6a2c1",
        "check": {"as_expected": True, "differences": []},
        "unchecked": None,
        "verdict": "whole",
        "at_tip": 3,
        "seated": 3,
        "page": None,
        "unshared": [],
    }
    whole.update(changed)
    return whole


def differing(*differences: str, **changed) -> dict:
    check = {"as_expected": False, "differences": list(differences)}
    return report(ok=False, check=check, **changed)


class Render(unittest.TestCase):
    def test_a_whole_run(self):
        self.assertEqual(
            table.render(report()),
            "3 members, fast group timers, largest message 512 KiB\n"
            "whole: 3 of 3 members at the tip\n"
            "the report is as expected\n"
            "load average 1.50 at the start, 2.62 at the end\n"
            "\n"
            "node   ready   start  dials  wired  mesh  end  mesh  refused  dropped  errors  exit\n"
            "saro  3.125s  3.125s      0     12    11   10     9        8        7       0     0\n"
            "raya  6.250s  3.000s      1     22    21   20    19       18       17       0     0\n"
            "pax   9.375s  2.875s      2     32    31   30    29       28       27       0     0\n"
            "\n"
            "batch  joiners  members  joined  seated  Welcome bytes\n"
            "1            2        3  0.750s  0.875s       2 x 7121\n"
            "\n"
            "post  from  read by  median   worst  delivered   worst  lookup\n"
            "1      pax   2 of 2  0.062s  0.125s     0.031s  0.062s  0.047s\n",
        )

    def test_a_run_that_stopped_while_its_peers_started(self):
        nodes = [
            {"name": "saro", "dials": [], "ready_s": 3.125, "start_s": 3.125},
            {"name": "raya", "dials": ["saro"]},
        ]
        stopped = report(
            ok=False,
            stopped="raya: no ready line within the budget",
            steps_done=0,
            nodes=nodes,
            batches=[],
            posts=[],
            conversation=None,
            check=None,
            verdict=None,
            at_tip=None,
            load={"start": [1.5, 1.25, 1.0], "end": None},
        )

        self.assertEqual(
            table.render(stopped),
            "3 members, fast group timers, largest message 512 KiB\n"
            "stopped after 0 of 2 steps: raya: no ready line within the budget\n"
            "not checked: the run made no conversation\n"
            "load average 1.50 at the start\n"
            "\n"
            "node   ready   start  dials  wired  mesh  end  mesh  refused  dropped  errors  exit\n"
            "saro  3.125s  3.125s      0      -     -    -     -        -        -       -     -\n"
            "raya       -       -      1      -     -    -     -        -        -       -     -\n",
        )

    def test_a_run_that_stopped_is_told_with_what_its_check_found(self):
        text = table.render(
            differing(
                "raya (7b1e44d9): never joined, expected tip",
                "post 1 of pax (e09a3f52): not in the report",
                stopped="raya: no line of a new roster within the budget",
                steps_done=0,
                at_tip=2,
            )
        )

        self.assertIn(
            "\nstopped after 0 of 2 steps: raya: no line of a new roster within the budget\n"
            "whole: 2 of 3 members at the tip\n"
            "not as expected: raya (7b1e44d9): never joined, expected tip\n"
            "not as expected: post 1 of pax (e09a3f52): not in the report\n"
            "load average ",
            text,
        )

    def test_an_interrupted_run_is_not_checked(self):
        text = table.render(report(ok=False, stopped="interrupted by SIGINT", check=None))

        self.assertIn(
            "\nstopped after 2 of 2 steps: interrupted by SIGINT\n"
            "not checked: the run was interrupted\n",
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

    def test_a_check_that_was_not_made_is_told_after_the_verdict_and_before_the_page(self):
        text = table.render(
            report(ok=False, check=None, unchecked="forks check ended with signal 9", page=PAGE)
        )

        self.assertIn(
            "\nwhole: 3 of 3 members at the tip\n"
            "not checked: forks check ended with signal 9\n"
            f"shared: {PAGE}\n",
            text,
        )

    def test_a_shared_run_gives_the_page_of_its_conversation(self):
        self.assertIn(
            f"\nthe report is as expected\nshared: {PAGE}\nload average ",
            table.render(report(page=PAGE)),
        )

    def test_a_run_that_could_not_be_shared_says_why(self):
        unshared = ["forks: saro.log: 127.0.0.1:8750 did not answer", "forks: 2 runs not tried"]

        self.assertIn(
            "\nthe report is as expected\n"
            "not shared: forks: saro.log: 127.0.0.1:8750 did not answer\n"
            "not shared: forks: 2 runs not tried\n",
            table.render(report(unshared=unshared)),
        )

    def test_durations_are_named_each_with_its_milliseconds(self):
        timers = {"consensus_timeout": 2000, "voting_delay": 100}

        self.assertEqual(
            table.render(report(timers=timers)).splitlines()[0],
            "3 members, group timers consensus_timeout 2000 ms, voting_delay 100 ms, "
            "largest message 512 KiB",
        )
        self.assertEqual(
            table.render(report(timers={})).splitlines()[0],
            "3 members, default group timers, largest message 512 KiB",
        )

    def test_how_a_peer_ended_is_told_where_it_was_not_with_status_0_once_asked(self):
        whole = report()
        whole["nodes"][0].update(exit_status=3, ended="early")
        whole["nodes"][1].update(exit_status=None, exit_signal=11, ended="in its stop")
        whole["nodes"][2].update(exit_status=None, exit_signal=9, ended="killed")

        lines = table.render(whole).splitlines()[5:9]
        ends = [line.split("  ")[-1].strip() for line in lines]
        self.assertEqual(ends, ["exit", "3 early", "signal 11 in its stop", "signal 9 killed"])

    def test_the_lines_of_what_the_peers_could_not_process_are_counted_by_message(self):
        whole = report()
        whole["nodes"][0]["inbound_errors"] = {"wrong epoch": 4}
        whole["nodes"][1]["inbound_errors"] = {"wrong epoch": 1, "no such group": 12}

        text = table.render(whole)
        self.assertEqual(
            [line.split()[-2] for line in text.splitlines()[5:9]], ["errors", "4", "13", "0"]
        )
        self.assertIn(
            "\n\n"
            "inbound_error  lines  peers\n"
            "no such group     12      1\n"
            "wrong epoch        5      2\n"
            "\n"
            "batch ",
            text,
        )

    def test_a_post_some_did_not_read_names_how_many_did(self):
        post = dict(report()["posts"][0], unread=["saro"], delivered=None, lookup_mean=None)
        post["read"] = {"count": 1, "median": 0.0625, "worst": 0.0625}
        text = table.render(report(posts=[report()["posts"][0], post]))

        self.assertEqual(
            text.splitlines()[-3:],
            [
                "post  from  read by  median   worst  delivered   worst  lookup",
                "1      pax   2 of 2  0.062s  0.125s     0.031s  0.062s  0.047s",
                "2      pax   1 of 2  0.062s  0.062s          -       -       -",
            ],
        )

    def test_a_post_nobody_was_to_read(self):
        post = dict(
            report()["posts"][0], readers=0, read=None, delivered=None, lookup_mean=None, lookups=0
        )

        self.assertEqual(
            table.render(report(posts=[post])).splitlines()[-1],
            "1      pax   0 of 0       -      -          -      -       -",
        )

    def test_welcomes_of_several_sizes_show_the_smallest_and_the_largest(self):
        batch = dict(report()["batches"][0], welcome_bytes=[7200, 7121])

        self.assertIn("2 x 7121..7200\n", table.render(report(batches=[batch])))


class Row(unittest.TestCase):
    """A sweep's row for a run."""

    def test_the_head_names_what_a_row_says(self):
        self.assertEqual(table.head(), "run  seconds  at the tip  verdict                 check")

    def test_a_run_that_went_as_expected(self):
        self.assertEqual(
            table.row(7, 41.26, report()),
            "  7     41.3      3 of 3  whole                   as expected",
        )

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
            table.row(12, 301.0, one),
            " 12    301.0      3 of 3  forked at epoch 4       "
            "verdict: forked at epoch 4, expected whole",
        )
        self.assertEqual(
            table.row(12, 301.0, three),
            " 12    301.0      2 of 3  forked at epoch 4       "
            "verdict: forked at epoch 4, expected whole, and 2 more",
        )

    def test_a_run_whose_steps_stopped_says_what_stopped_them_and_what_its_check_found(self):
        stopped = differing(
            "raya (7b1e44d9): never joined, expected tip",
            stopped="raya: no line of a new roster within the budget",
            at_tip=2,
        )

        self.assertEqual(
            table.row(2, 9.04, stopped),
            "  2      9.0      2 of 3  whole                   "
            "raya (7b1e44d9): never joined, expected tip; "
            "stopped: raya: no line of a new roster within the budget",
        )

    def test_a_run_that_made_no_conversation(self):
        stopped = report(
            ok=False, stopped="raya ended with status 3", check=None, verdict=None, at_tip=None
        )

        self.assertEqual(
            table.row(1, 0.52, stopped),
            "  1      0.5           -  -                       "
            "not checked; stopped: raya ended with status 3",
        )

    def test_a_run_whose_check_was_not_made_says_how_the_check_ended(self):
        unchecked = report(ok=False, check=None, unchecked="forks check ended with signal 9")

        self.assertEqual(
            table.row(3, 7.25, unchecked),
            "  3      7.2      3 of 3  whole                   "
            "not checked: forks check ended with signal 9",
        )

    def test_a_shared_run_ends_with_the_page_of_its_conversation(self):
        self.assertTrue(table.row(1, 1.0, report(page=PAGE)).endswith(f"  as expected; {PAGE}"))
        unshared = report(unshared=["forks: saro.log: no answer", "forks: 2 runs not tried"])
        self.assertTrue(
            table.row(1, 1.0, unshared).endswith(
                "  as expected; not shared: forks: saro.log: no answer"
            )
        )


if __name__ == "__main__":
    unittest.main()
