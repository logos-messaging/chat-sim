import unittest

from sim import table

PAGE = "http://127.0.0.1:8750/#2a30d6a2c1"
NETWORK = {"seed": 7, "loss": 0.05, "welcome_loss": 0, "delay_ms": 0}


def withheld(**changed) -> dict:
    """What a run says was withheld: here from pax, the candidate of raya, which was applied."""
    whole = {"from": "pax", "seat": 1, "app_id": "38306230", "minter": "raya", "applied": True}
    whole.update(changed)
    return whole


def report(**changed) -> dict:
    whole = {
        "members": 3,
        "ok": True,
        "stopped": None,
        "steps": 2,
        "steps_done": 2,
        "group": "v2",
        "timers": "fast",
        "preset": None,
        "max_message_kib": 512,
        "seed": None,
        "network": None,
        "wire": None,
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
                    "dials_ok": len(dials) + 3,
                    "dials_failed": len(dials),
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
        "holds": [],
        "conversation": "2a30d6a2c1",
        "withheld": None,
        "check": {"as_expected": True, "differences": []},
        "unchecked": None,
        "verdict": "whole",
        "at_tip": 3,
        "seated": 3,
        "grow": None,
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
            "node   ready   start  dials  wired  mesh  end  mesh  dialed  failed  refused  dropped"
            "  errors  exit\n"
            "saro  3.125s  3.125s      0     12    11   10     9       3       0        8        7"
            "       0     0\n"
            "raya  6.250s  3.000s      1     22    21   20    19       4       1       18       17"
            "       0     0\n"
            "pax   9.375s  2.875s      2     32    31   30    29       5       2       28       27"
            "       0     0\n"
            "\n"
            "batch  joiners  members  joined  seated  Welcome bytes\n"
            "1            2        3  0.750s  0.875s       2 x 7121\n"
            "\n"
            "post  from  read by  median   worst  delivered   worst  lookup\n"
            "1      pax   2 of 2  0.062s  0.125s     0.031s  0.062s  0.047s\n",
        )

    def test_a_run_in_containers_names_its_image_its_engine_and_its_wire(self):
        wire = {"seed": 5, "delay_ms": 40, "jitter_ms": 3, "loss": 0.015}
        rig = {"rig": "containers", "engine": "docker", "image": "logos-chat-sim:latest"}
        rendered = table.render(report(wire=wire, unremoved=None, **rig)).splitlines()

        self.assertEqual(
            rendered[:2],
            [
                "3 members, fast group timers, largest message 512 KiB, containers of "
                "logos-chat-sim:latest on docker, wire seed 5, delay 40 ms, jitter 3 ms, "
                "loss 0.015",
                "whole: 3 of 3 members at the tip",
            ],
        )
        by_member = dict(wire, delay_ms={"raya": 40})
        self.assertIn(", delay by member, ", table.render(report(wire=by_member, **rig)))
        unremoved = "docker rm ended with status 1: Error: no such container"
        rendered = table.render(report(unremoved=unremoved, **rig)).splitlines()
        self.assertEqual(rendered[3], f"not removed: {unremoved}")

    def test_a_run_with_a_network_model_names_its_seed_its_rates_and_the_frames_each_lost(self):
        modelled = report(network=NETWORK)
        for node, dropped in zip(
            modelled["nodes"], ({}, {"loss": 12, "cut": 1}, {"withheld": 1})
        ):
            node["network_dropped"] = dropped

        self.assertEqual(
            table.render(modelled).splitlines()[:9],
            [
                "3 members, fast group timers, largest message 512 KiB, network seed 7, "
                "loss 0.05, welcome loss 0",
                "whole: 3 of 3 members at the tip",
                "the report is as expected",
                "load average 1.50 at the start, 2.62 at the end",
                "",
                "node   ready   start  dials  wired  mesh  end  mesh  dialed  failed  refused  "
                "dropped  lost  errors  exit",
                "saro  3.125s  3.125s      0     12    11   10     9       3       0        8  "
                "      7     0       0     0",
                "raya  6.250s  3.000s      1     22    21   20    19       4       1       18  "
                "     17    13       0     0",
                "pax   9.375s  2.875s      2     32    31   30    29       5       2       28  "
                "     27     1       0     0",
            ],
        )
        self.assertEqual(
            table.render(modelled).splitlines()[9:], table.render(report()).splitlines()[9:]
        )

    def test_a_peer_of_a_network_model_that_did_not_end_has_no_count_of_frames_lost(self):
        nodes = [{"name": "saro", "dials": [], "ready_s": 3.125, "start_s": 3.125}]
        stopped = report(network=NETWORK, nodes=nodes, batches=[], posts=[])

        self.assertEqual(
            table.render(stopped).splitlines()[5:],
            [
                "node   ready   start  dials  wired  mesh  end  mesh  dialed  failed  refused  "
                "dropped  lost  errors  exit",
                "saro  3.125s  3.125s      0      -     -    -     -       -       -        -  "
                "      -     -       -     -",
            ],
        )

    def test_a_run_on_a_preset_names_it_and_the_links_each_node_no_longer_holds(self):
        fleet = report(preset="logos.test", max_message_kib=None)
        for node, unheld in zip(fleet["nodes"], (0, 14, None)):
            node["unheld"] = unheld

        self.assertEqual(
            table.render(fleet).splitlines()[:9],
            [
                "3 members, fast group timers, preset logos.test",
                "whole: 3 of 3 members at the tip",
                "the report is as expected",
                "load average 1.50 at the start, 2.62 at the end",
                "",
                "node   ready   start  dials  wired  mesh  end  mesh  dialed  failed  unheld  "
                "refused  dropped  errors  exit",
                "saro  3.125s  3.125s      0     12    11   10     9       3       0       0  "
                "      8        7       0     0",
                "raya  6.250s  3.000s      1     22    21   20    19       4       1      14  "
                "     18       17       0     0",
                "pax   9.375s  2.875s      2     32    31   30    29       5       2       -  "
                "     28       27       0     0",
            ],
        )
        self.assertEqual(
            table.render(fleet).splitlines()[9:], table.render(report()).splitlines()[9:]
        )

    def test_a_run_on_a_preset_with_a_network_model_names_both(self):
        both = report(preset="logos.dev", max_message_kib=None, network=NETWORK)

        self.assertEqual(
            table.render(both).splitlines()[0],
            "3 members, fast group timers, preset logos.dev, network seed 7, loss 0.05, "
            "welcome loss 0",
        )
        self.assertEqual(
            table.render(both).splitlines()[5].split()[8:],
            ["dialed", "failed", "unheld", "refused", "dropped", "lost", "errors", "exit"],
        )

    def test_a_run_of_one_member_says_so(self):
        self.assertEqual(
            table.render(report(members=1)).splitlines()[0],
            "1 member, fast group timers, largest message 512 KiB",
        )

    def test_each_hold_is_a_line_of_its_seconds_after_the_tables(self):
        holds = [{"seconds": 60.0, "nodes": []}, {"seconds": 0.5, "nodes": []}]

        self.assertEqual(
            table.render(report(holds=holds)).splitlines()[-4:],
            [
                "1      pax   2 of 2  0.062s  0.125s     0.031s  0.062s  0.047s",
                "",
                "hold 1: 60.000s",
                "hold 2: 0.500s",
            ],
        )

    def test_a_hold_on_a_preset_says_the_links_a_node_no_longer_held_a_minute(self):
        holds = [
            {"seconds": 300.0, "nodes": [], "rate": 0.4},
            {"seconds": 90.0, "nodes": [], "rate": None},
            {"seconds": 300.0, "nodes": [], "rate": -0.25},
        ]
        fleet = report(preset="logos.test", holds=holds, batches=[], posts=[])

        self.assertEqual(
            table.render(fleet).splitlines()[-4:],
            [
                "",
                "hold 1: 300.000s, unheld 0.40 a node a minute",
                "hold 2: 90.000s, unheld - a node a minute",
                "hold 3: 300.000s, unheld -0.25 a node a minute",
            ],
        )

    def test_a_rate_of_a_hold_that_rounds_to_nothing_has_no_sign(self):
        # One link not dialed, of ten nodes, in half an hour.
        holds = [{"seconds": 1800.0, "nodes": [], "rate": -1 / 300}]
        fleet = report(preset="logos.test", holds=holds, batches=[], posts=[])

        self.assertEqual(
            table.render(fleet).splitlines()[-1], "hold 1: 1800.000s, unheld 0.00 a node a minute"
        )

    def test_what_was_withheld_from_whom_is_told_after_the_verdict(self):
        text = table.render(
            report(network=NETWORK, withheld=withheld(), verdict="split at epoch 4")
        )

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
            table.render(
                report(network=NETWORK, withheld=withheld(minter="saro", applied=False))
            ),
        )

    def test_a_withheld_candidate_whose_app_id_is_no_peers_is_said_so(self):
        self.assertIn(
            "\nwithheld from pax: the candidate of no peer of the run, applied\n",
            table.render(report(network=NETWORK, withheld=withheld(minter=None))),
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
            "node   ready   start  dials  wired  mesh  end  mesh  dialed  failed  refused  dropped"
            "  errors  exit\n"
            "saro  3.125s  3.125s      0      -     -    -     -       -       -        -        -"
            "       -     -\n"
            "raya       -       -      1      -     -    -     -       -       -        -        -"
            "       -     -\n",
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

    def test_a_run_with_a_network_model_names_its_seed_after_what_stopped_it(self):
        self.assertEqual(
            table.row(7, 41.26, report(network=NETWORK)),
            "  7     41.3      3 of 3  whole                   as expected; network seed 7",
        )
        stopped = differing(
            "raya (7b1e44d9): never joined, expected tip",
            stopped="raya: no line of a new roster within the budget",
            network=dict(NETWORK, seed=2**64 - 1),
        )
        self.assertTrue(
            table.row(2, 9.04, stopped).endswith(
                "; stopped: raya: no line of a new roster within the budget; "
                f"network seed {2**64 - 1}"
            )
        )

    def test_a_run_on_a_wire_names_the_seed_of_its_wire_after_that_of_its_network_model(self):
        wire = {"seed": 70, "delay_ms": 0, "jitter_ms": 0, "loss": 0.5}

        self.assertEqual(
            table.row(7, 41.26, report(network=NETWORK, wire=wire)),
            "  7     41.3      3 of 3  whole                   "
            "as expected; network seed 7; wire seed 70",
        )

    def test_a_run_that_withheld_a_candidate_says_whose_and_whether_applied_before_its_page(self):
        told = report(network=NETWORK, withheld=withheld(), page=PAGE)

        self.assertTrue(
            table.row(1, 1.0, told).endswith(
                "  as expected; network seed 7; "
                f"withheld from pax: the candidate of raya, applied; {PAGE}"
            )
        )
        told["withheld"]["applied"] = False
        self.assertTrue(
            table.row(1, 1.0, dict(told, page=None)).endswith(
                "  as expected; network seed 7; "
                "withheld from pax: the candidate of raya, not applied"
            )
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
