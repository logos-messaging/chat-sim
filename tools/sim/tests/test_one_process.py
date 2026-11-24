import contextlib
import copy
import io
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import List, Tuple

from sim import one_process, scenario, table
from sim.cli import main
from sim.peers import Watch
from sim.run import REPOSITORY, Settings, run
from sim.sweep import Interrupts, sweep

from tests import FIXTURES, STANDIN_RUNNER, stand_in, stand_in_runner, standin_runner
from tests.test_run import APPLIED, Driven, Scratch, said, withholding
from tests.test_sweep import cells
from tests.test_table import NETWORK
from tests.test_table import report as whole_run

TOOL = Path(__file__).resolve().parents[1]

# The second a plan's clock starts at, where a test names one.
START = 1791817445

# What the stand-in comes to when the group applied the candidate withheld in `withholding`.
ALONE = dict(APPLIED, posts={"pax": "unreadable"})

# A step that grows the group to the third epoch, two adders a round at most.
GROW = {"step": "grow", "epochs": 3, "adders": 2, "branches": 5}

# A grow that came to a main branch and a sub-branch off it, and left pax behind.
GROWN = {
    "ended": "2 main branches ran side by side",
    "trunk_epoch": 7,
    "main": 2,
    "lines": [
        {"kind": "trunk", "from": None, "forked_at": None, "epoch": 7, "members": ["saro", "raya"]},
        {"kind": "main", "from": 0, "forked_at": 3, "epoch": 12, "members": ["kes"]},
        {"kind": "sub", "from": 1, "forked_at": 5, "epoch": 5, "members": ["pax"]},
    ],
    "fell_behind": [{"name": "pax", "epoch": 3}, {"name": "pax", "epoch": 5}],
    "behind": ["pax"],
    "never_joined": ["mira"],
    "removed": [],
    "rounds": [
        {"adders": ["saro"], "joiners": ["raya"], "refused": [], "settled_s": 0.5},
        {"adders": ["saro", "raya"], "joiners": ["pax"], "refused": ["raya"], "settled_s": 180},
    ],
}

# What stops a run whose runner a signal ended.
KILLED = "the runner ended with signal 9: it said nothing on standard error"

# Each field of the kept result that a run reads. Pax is the member a candidate was withheld from.
READ = (
    "stopped",
    "steps_done",
    "conversation",
    "virtual_s",
    "wall_s",
    "members",
    "batches",
    "posts",
    "members[1].name",
    "members[1].installation",
    "members[1].app_id",
    "members[1].inbound_errors",
    "members[1].network",
    "members[1].dropped",
    "members[1].network.withheld",
    "members[1].network.frames",
    "members[1].network.frames.arrived",
    "members[1].network.frames.passed",
    "members[1].network.frames.held",
    "members[2].network.withheld.app_id",
    "members[2].network.withheld.seat",
    "members[2].network.withheld.applied",
    "members[2].dropped[0].why",
    "batches[0].joiners",
    "batches[0].members",
    "batches[0].seated_s",
    "posts[0].poster",
    "posts[0].readers",
    "posts[0].unread",
    "posts[0].read_s",
)


def plan_of(**changed) -> dict:
    """The plan of a scenario as a file says it."""
    return one_process.plan(scenario.scenario(said(**changed)))


def in_one_process(**changed) -> dict:
    """What a run of three in one process reports: saro seats two, and pax posts."""
    ids = (("saro", "a3f91c07", "7361726f"), ("raya", "7b1e44d9", "72617961"))
    nodes = [
        {
            "name": name,
            "installation": installation,
            "account": account,
            "seated": True,
            "app_id": installation + "00",
            "inbound_errors": {},
        }
        for name, installation, account in (*ids, ("pax", "e09a3f52", "706178"))
    ]
    post = {
        "poster": "pax",
        "readers": 2,
        "unread": [],
        "read": {"count": 2, "median": 0.0, "worst": 0.05},
    }
    whole = whole_run(
        rig=table.ONE_PROCESS,
        cluster_id=None,
        seed=1,
        virtual_s=1.35,
        wall_s=0.0625,
        nodes=nodes,
        batches=[{"joiners": 2, "members": 3, "seated_s": 1.25, "wall_s": 0.046875}],
        posts=[post],
    )
    whole.update(changed)
    return whole


def no_result(**changed) -> dict:
    """What a run reports whose runner left no result."""
    return in_one_process(
        ok=False,
        steps_done=0,
        virtual_s=None,
        wall_s=None,
        nodes=[],
        batches=[],
        posts=[],
        conversation=None,
        check=None,
        verdict=None,
        at_tip=None,
        **changed,
    )


def shape(held: object) -> object:
    """What a value of JSON is made of: the fields of each object, and the kind of the rest."""
    if isinstance(held, dict):
        return {name: shape(inner) for name, inner in held.items()}
    if isinstance(held, list):
        return [shape(inner) for inner in held]
    return type(held).__name__


def led_to(held: object, path: str) -> Tuple[object, object]:
    """Where `path` leads in a value of JSON: what holds its last field, and that field's name."""
    *before, last = [int(name) if name.isdigit() else name for name in re.findall(r"\w+", path)]
    for name in before:
        held = held[name]
    return held, last


def kinds(held: object) -> object:
    """The shape of a value of JSON with each list down to its first item."""
    if isinstance(held, dict):
        return {name: kinds(inner) for name, inner in held.items()}
    if isinstance(held, list):
        return [kinds(inner) for inner in held[:1]]
    return type(held).__name__


def runner_leaving(directory: Path, result: object, status: int = 0) -> Path:
    """Writes into `directory` a program that leaves `result` for its result, and no log."""
    left = directory / "left.json"
    left.write_text(json.dumps(result), encoding="utf-8")
    program = directory / "runner"
    program.write_text(
        f'#!/bin/sh\ncp {shlex.quote(str(left))} "$2/result.json"\nexit {status}\n',
        encoding="utf-8",
    )
    program.chmod(0o755)
    return program


class Planning(unittest.TestCase):
    """The plan a scenario comes to."""

    def test_a_plan_names_the_members_and_holds_what_the_scenario_says_of_the_run(self):
        self.assertEqual(
            plan_of(members=7, timers="default", budget=2.5),
            {
                "members": ["saro", "raya", "pax", "mira", "kes", "tomo", "peer7"],
                "timers": "default",
                "budget_s": 2.5,
                "start": one_process.START,
                "seed": 0,
                "unwaited": {"rosters": [], "posts": []},
                "steps": [
                    {"step": "seat", "to": 7, "batch": 10},
                    {"step": "post", "from": ["peer7"]},
                ],
            },
        )

    def test_a_groupv3_group_is_told_and_a_groupv2_one_left_to_the_runner(self):
        self.assertEqual(plan_of(group="v3")["group"], "v3")
        self.assertNotIn("group", plan_of(group="v2"))

    def test_durations_are_told_as_the_scenario_has_them(self):
        timers = {"consensus_timeout": 20000, "recovery_auto_commit_delay": 0}

        self.assertEqual(plan_of(timers=timers)["timers"], timers)
        self.assertEqual(plan_of()["timers"], "fast")

    def test_the_largest_message_is_no_part_of_a_plan(self):
        self.assertEqual(plan_of(max_message_kib=64), plan_of())

    def test_a_member_no_step_seats_is_a_member_of_the_plan(self):
        plan = plan_of(members=5, steps=[{"step": "seat", "to": 3}])

        self.assertEqual(plan["members"], ["saro", "raya", "pax", "mira", "kes"])
        self.assertEqual(plan["steps"], [{"step": "seat", "to": 3, "batch": 10}])

    def test_each_step_is_told_in_its_place_and_a_cut_by_its_sides(self):
        steps = [
            {"step": "seat", "to": 3, "batch": 1},
            {"step": "hold", "seconds": 0.5},
            {"step": "cut", "between": [["saro", "raya"], ["pax"]]},
            {"step": "post", "from": "saro"},
            {"step": "heal"},
        ]

        self.assertEqual(
            plan_of(steps=steps, network={"seed": 7})["steps"],
            [
                {"step": "seat", "to": 3, "batch": 1},
                {"step": "hold", "seconds": 0.5},
                {"step": "cut", "sides": [["saro", "raya"], ["pax"]]},
                {"step": "post", "from": ["saro"]},
                {"step": "heal"},
            ],
        )

    def test_a_grow_step_is_told_with_its_fields(self):
        self.assertEqual(plan_of(members=5, steps=[GROW])["steps"], [GROW])
        by = {"step": "grow", "epochs": 3, "by": "saro", "branches": 5}
        self.assertEqual(plan_of(members=5, steps=[by])["steps"], [by])
        sized = dict(GROW, size=4)
        self.assertEqual(plan_of(members=5, steps=[sized])["steps"], [sized])

    def test_a_candidate_is_withheld_from_the_member_the_step_names(self):
        self.assertEqual(
            plan_of(**withholding({"step": "post", "from": "mira"}))["steps"],
            [
                {"step": "seat", "to": 3, "batch": 10},
                {"step": "withhold", "from": "pax"},
                {"step": "seat", "to": 4, "batch": 10},
                {"step": "post", "from": ["mira"]},
            ],
        )

    def test_the_last_and_all_are_the_members_seated_by_then(self):
        steps = [
            {"step": "post"},
            {"step": "seat", "to": 2},
            {"step": "post", "from": "all"},
            {"step": "seat", "to": 4, "batch": 1},
            {"step": "post"},
            {"step": "post", "from": "all"},
        ]
        plan = plan_of(members=5, steps=steps)

        self.assertEqual(len(plan["steps"]), len(steps))
        self.assertEqual(
            [step["from"] for step in plan["steps"] if step["step"] == "post"],
            [["saro"], ["saro", "raya"], ["mira"], ["saro", "raya", "pax", "mira"]],
        )

    def test_a_network_model_is_told_with_the_delay_of_every_member(self):
        network = {"seed": 7, "loss": 0.25, "welcome_loss": 1, "delay_ms": {"raya": 40}}

        self.assertEqual(
            plan_of(network=network)["network"],
            {
                "seed": 7,
                "loss": 0.25,
                "welcome_loss": 1,
                "delay_ms": {"saro": 0, "raya": 40, "pax": 0},
            },
        )
        self.assertEqual(
            plan_of(network={"seed": 0, "delay_ms": 25})["network"],
            {
                "seed": 0,
                "loss": 0,
                "welcome_loss": 0,
                "delay_ms": {"saro": 25, "raya": 25, "pax": 25},
            },
        )

    def test_no_roster_is_waited_on_for_a_member_expected_elsewhere_than_at_the_tip(self):
        positions = {"saro": "behind", "raya": "tip", "pax": "never joined"}

        # The creator is waited on wherever the scenario expects it.
        self.assertEqual(
            plan_of(expect={"positions": positions})["unwaited"], {"rosters": ["pax"], "posts": []}
        )

    def test_no_post_is_waited_on_for_a_member_expected_not_to_read_it(self):
        posts = {"pax": "no record", "raya": "read", "saro": "unreadable"}

        self.assertEqual(
            plan_of(expect={"posts": posts})["unwaited"],
            {"rosters": [], "posts": ["saro", "pax"]},
        )

    def test_what_is_expected_of_a_candidate_applied_leaves_no_member_unwaited(self):
        plan = plan_of(**withholding(expect={"applied": ALONE}))

        self.assertEqual(plan["unwaited"], {"rosters": [], "posts": []})


class Running(Scratch):
    """`run` against a stand-in for the runner: what it reports, and that it leaves no process."""

    def run_of(self, *faults: str, **changed) -> dict:
        settings = Settings(
            chat_cli=None,
            scenario=scenario.scenario(said(**changed)),
            out=self.out,
            one_process=stand_in_runner(self.scratch, *faults),
        )
        return self.report_of(settings)

    def report_of(self, settings: Settings) -> dict:
        report = run(settings, Watch())
        # The runner and each command of `forks` is a child of this process until it is reaped.
        with self.assertRaises(ChildProcessError, msg="the run left a process"):
            os.waitpid(-1, os.WNOHANG)
        kept = json.loads((self.out / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(kept, json.loads(json.dumps(report)))
        return report

    def left(self, name: str) -> dict:
        """What a file of JSON in the run's directory holds."""
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def test_three_members_form_a_group_in_one_process_and_read_one_post(self):
        report = self.run_of()

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertIsNone(report["stopped"])
        self.assertEqual((report["steps_done"], report["steps"]), (2, 2))
        self.assertEqual(report["rig"], "one process")
        self.assertEqual(report["group"], "v2")
        self.assertEqual(report["virtual_s"], standin_runner.SEATED)
        self.assertGreater(report["wall_s"], 0)
        self.assertEqual(
            [(node["name"], node["seated"]) for node in report["nodes"]],
            [("saro", True), ("raya", True), ("pax", True)],
        )
        (batch,) = report["batches"]
        self.assertEqual(
            (batch["joiners"], batch["members"], batch["seated_s"]), (2, 3, standin_runner.SEATED)
        )
        (post,) = report["posts"]
        self.assertEqual((post["poster"], post["readers"], post["unread"]), ("pax", 2, []))

    def test_a_grow_is_reported_on_and_not_checked_and_counts_the_members_it_seated(self):
        report = self.run_of(members=5, steps=[GROW])

        self.assertTrue(report["ok"], report["stopped"])
        self.assertEqual(
            (report["check"], report["unchecked"]), (None, "a `grow` step expects no outcome")
        )
        self.assertFalse((self.out / "expect.json").exists())
        self.assertTrue((self.out / "report.txt").exists())
        self.assertEqual((report["verdict"], report["at_tip"], report["seated"]), ("whole", 4, 4))
        self.assertEqual(report["grow"]["ended"], "the trunk reached epoch 3")
        self.assertEqual(report["batches"], [])
        self.assertIn("\nnot checked: a `grow` step expects no outcome\n", table.render(report))

    def test_a_grow_that_seats_every_member_of_the_plan_ends_there(self):
        report = self.run_of(members=3, steps=[dict(GROW, epochs=50)])

        self.assertEqual(report["grow"]["ended"], "every member of the plan was seated")
        self.assertEqual((report["at_tip"], report["seated"]), (3, 3))

    def test_the_runner_is_told_the_plan_the_run_keeps_with_its_seed_on_a_clock_from_2026(self):
        report = self.run_of(members=4, timers="default", seed=5, network={"seed": 7})

        plan = self.left("plan.json")
        held = scenario.scenario(said(members=4, timers="default", seed=5, network={"seed": 7}))
        self.assertEqual(plan, one_process.plan(held))
        self.assertEqual((plan["start"], plan["seed"], report["seed"]), (1_767_225_600, 5, 5))
        # The stamps of the log are those of the plan's clock.
        log = (self.out / "group.log").read_text(encoding="utf-8")
        self.assertTrue(
            log.startswith("2026-01-01T00:00:00.000000Z TRACE libchat::mls_trace: run "), log
        )

    def test_a_run_in_one_process_starts_no_registry_and_no_peer(self):
        self.run_of()

        self.assertEqual(
            sorted(file.name for file in self.out.iterdir()),
            [
                "expect.json",
                "group.log",
                "plan.json",
                "report.json",
                "report.txt",
                "result.json",
                "run.json",
                "run.txt",
                "runner.out",
                "runner.stderr",
            ],
        )

    def test_the_report_of_the_one_log_is_held_against_what_the_scenario_expects(self):
        report = self.run_of()

        self.assertEqual(report["check"], {"as_expected": True, "differences": []})
        self.assertEqual((report["verdict"], report["at_tip"], report["seated"]), ("whole", 3, 3))
        result, expected = self.left("result.json"), self.left("expect.json")
        self.assertEqual(expected["conversation"], result["conversation"])
        self.assertEqual(report["conversation"], result["conversation"])
        # A member's id is the one the runner's result gives it, which its records bear.
        self.assertEqual(
            expected["installations"],
            {
                member["installation"]: {"name": member["name"], "position": "tip"}
                for member in result["members"]
            },
        )
        drawn = self.left("report.json")
        self.assertEqual([held["id"] for held in drawn["conversations"]], [report["conversation"]])
        self.assertIn("\n  whole\n", (self.out / "report.txt").read_text(encoding="utf-8"))

    def test_a_node_is_a_member_of_the_result_with_its_drops_counted_by_their_why(self):
        steps = [
            {"step": "seat", "to": 3},
            {"step": "cut", "between": [["saro", "raya"], ["pax"]]},
            {"step": "post", "from": "saro"},
            {"step": "post", "from": "raya"},
            {"step": "heal"},
            {"step": "post", "from": "pax"},
        ]
        report = self.run_of(
            steps=steps, network={"seed": 7}, expect={"posts": {"pax": "no record"}}
        )

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        members = self.left("result.json")["members"]
        self.assertEqual([len(member["dropped"]) for member in members], [0, 0, 2])
        for node, member in zip(report["nodes"], members):
            self.assertEqual(node["network_dropped"], {"cut": 2} if member["dropped"] else {})
            del node["network_dropped"], member["dropped"]
            self.assertEqual(node, member)
        self.assertEqual([post["readers"] for post in report["posts"]], [1, 1, 2])

    def test_a_node_holds_nothing_that_only_a_peer_has(self):
        report = self.run_of()

        for node in report["nodes"]:
            self.assertEqual(
                sorted(node),
                ["account", "app_id", "inbound_errors", "installation", "name", "seated"],
            )
        self.assertNotIn("wired_s", report)
        self.assertEqual((report["preset"], report["cluster_id"]), (None, None))

    def test_a_post_holds_who_read_it_and_when_and_nothing_of_a_registry(self):
        report = self.run_of()

        self.assertEqual(
            report["posts"],
            [
                {
                    "poster": "pax",
                    "readers": 2,
                    "unread": [],
                    "read": {"count": 2, "median": 0.0, "worst": 0.0},
                }
            ],
        )

    def test_a_batch_is_told_as_the_runner_told_it_with_no_welcome(self):
        report = self.run_of(members=5, steps=[{"step": "seat", "to": 5, "batch": 2}])

        self.assertEqual(report["batches"], self.left("result.json")["batches"])
        self.assertEqual(
            [sorted(batch) for batch in report["batches"]],
            [["joiners", "members", "seated_s", "wall_s"]] * 2,
        )

    def test_each_hold_that_was_done_is_kept_with_its_seconds(self):
        steps = [{"step": "hold", "seconds": 2.5}, {"step": "seat", "to": 3}]
        report = self.run_of(steps=[*steps, {"step": "hold", "seconds": 60}])

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual(report["holds"], [{"seconds": 2.5}, {"seconds": 60.0}])
        self.assertEqual(report["virtual_s"], 2.5 + standin_runner.SEATED + 60)

    def test_a_hold_after_the_step_that_stopped_is_not_kept(self):
        steps = [{"step": "hold", "seconds": 2.5}, {"step": "seat", "to": 3}]
        report = self.run_of("behind=raya", steps=[*steps, {"step": "hold", "seconds": 60}])

        self.assertEqual((report["steps_done"], report["holds"]), (1, [{"seconds": 2.5}]))

    def test_steps_the_runner_stopped_are_told_with_its_reason_and_the_log_is_still_checked(self):
        report = self.run_of("behind=raya")

        self.assertFalse(report["ok"])
        self.assertEqual(
            report["stopped"],
            "the roster of 3 members was not held within the budget by: raya",
        )
        self.assertEqual((report["steps_done"], report["batches"], report["posts"]), (0, [], []))
        self.assertEqual((report["verdict"], report["at_tip"], report["seated"]), ("whole", 2, 3))
        raya, pax = (
            f"{node['name']} ({node['installation']})" for node in report["nodes"][1:]
        )
        self.assertEqual(
            report["check"],
            {
                "as_expected": False,
                "differences": [
                    f"{raya}: never joined, expected tip",
                    f"post 1 of {pax}: not in the report",
                ],
            },
        )

    def test_a_post_a_member_waited_on_did_not_read_stops_the_steps_and_names_the_member(self):
        steps = [
            {"step": "seat", "to": 3},
            {"step": "cut", "between": [["saro"], ["pax"]]},
            {"step": "post", "from": "saro"},
            {"step": "post", "from": "raya"},
        ]
        report = self.run_of(steps=steps, network={"seed": 7})

        self.assertEqual(
            (report["stopped"], report["steps_done"]),
            ("the post of saro was not read within the budget by: pax", 2),
        )
        (post,) = report["posts"]
        self.assertEqual((post["readers"], post["unread"]), (2, ["pax"]))
        self.assertEqual(post["read"], {"count": 1, "median": 0.0, "worst": 0.0})
        saro, raya, pax = (f"{node['name']} ({node['installation']})" for node in report["nodes"])
        self.assertEqual(
            report["check"]["differences"],
            [
                f"post 1 of {saro} on {pax}: no record, expected read",
                f"post 1 of {raya}: not in the report",
            ],
        )

    def test_a_member_the_scenario_expects_elsewhere_than_the_tip_is_not_waited_on(self):
        expect = {"positions": {"raya": "never joined"}, "posts": {"raya": "no record"}}
        report = self.run_of("behind=raya", expect=expect)

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual((report["steps_done"], report["at_tip"], report["seated"]), (2, 2, 3))
        self.assertEqual(
            self.left("plan.json")["unwaited"], {"rosters": ["raya"], "posts": ["raya"]}
        )
        (post,) = report["posts"]
        self.assertEqual((post["readers"], post["unread"]), (1, []))

    def test_a_run_whose_group_did_not_apply_the_withheld_candidate_is_held_to_expect(self):
        post = {"step": "post", "from": "mira"}
        report = self.run_of(**withholding(post, expect={"applied": ALONE}))

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual(report["held_to"], "not applied")
        self.assertEqual(
            report["withheld"],
            {
                "from": "pax",
                "seat": 0,
                "app_id": report["nodes"][0]["app_id"],
                "minter": "saro",
                "applied": False,
            },
        )
        self.assertEqual(
            (report["verdict"], self.left("expect.json")["verdict"]), ("whole", "whole")
        )
        self.assertEqual(
            [node["network_dropped"] for node in report["nodes"]], [{}, {}, {"withheld": 1}, {}]
        )
        self.assertEqual(report["posts"][0]["readers"], 3)

    def test_a_run_whose_group_applied_the_withheld_candidate_is_held_to_what_applied_says(self):
        post = {"step": "post", "from": "mira"}
        report = self.run_of("applied", **withholding(post, expect={"applied": ALONE}))

        # The split of the member alone does not stop the steps.
        self.assertEqual((report["stopped"], report["steps_done"]), (None, 4))
        self.assertTrue(report["ok"], report["check"])
        self.assertEqual(report["held_to"], "applied")
        self.assertEqual(
            (report["withheld"]["from"], report["withheld"]["applied"]), ("pax", True)
        )
        self.assertEqual(
            (report["verdict"], self.left("expect.json")["verdict"]),
            ("split at epoch 2", "split at epoch 2"),
        )
        self.assertEqual(report["at_tip"], 4)
        self.assertEqual(report["posts"][0]["readers"], 2)
        self.assertEqual(report["nodes"][2]["inbound_errors"], {standin_runner.GARBLED: 1})

    def test_a_split_the_scenario_says_nothing_of_is_one_difference_of_the_verdict(self):
        report = self.run_of("applied", **withholding())

        self.assertEqual((report["held_to"], report["stopped"]), ("applied", None))
        self.assertFalse(report["ok"])
        self.assertEqual(
            report["check"]["differences"], ["verdict: split at epoch 2, expected whole"]
        )

    def stop_of(self, *faults: str) -> str:
        """What stopped a run whose runner left no result, which is then not checked."""
        report = self.run_of(*faults)
        self.assertEqual((report["ok"], report["steps_done"], report["check"]), (False, 0, None))
        self.assertEqual((report["conversation"], report["nodes"]), (None, []))
        self.assertEqual((report["virtual_s"], report["wall_s"]), (None, None))
        self.assertFalse((self.out / "expect.json").exists())
        self.assertIn("\nnot checked: no result of the runner was read\n", self.written())
        return report["stopped"]

    def test_a_runner_that_refuses_its_plan_is_named_with_its_status_and_its_complaint(self):
        self.assertEqual(
            self.stop_of("refused"),
            f"the runner ended with status 2: harness-run: {self.out / 'plan.json'}: "
            f"{standin_runner.REFUSAL}",
        )

    def test_a_runner_that_ended_is_named_with_the_last_three_lines_it_complained_of(self):
        self.assertEqual(len(standin_runner.PANIC), 5)
        self.assertEqual(
            self.stop_of("panics"),
            f"the runner ended with status {standin_runner.STATUS}: "
            "  left: 1 /  right: 2 / "
            "note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace",
        )

    def test_a_runner_a_signal_ended_is_named_with_the_signal_and_has_left_no_result(self):
        self.assertEqual(self.stop_of("killed"), KILLED)
        self.assertEqual((self.out / "result.json").read_bytes(), b"")
        self.assertIn(" libchat::mls_trace: run ", (self.out / "group.log").read_text("utf-8"))

    def test_a_runner_that_ends_as_it_should_and_leaves_no_result_stops_the_run(self):
        self.assertEqual(
            self.stop_of("no-result"), "the runner ended with status 0 and left no result"
        )
        self.assertIn(" libchat::mls_trace: epoch ", (self.out / "group.log").read_text("utf-8"))

    def test_a_runner_that_is_no_program_stops_the_run_and_the_report_is_written(self):
        runner = self.scratch / "runner"
        runner.write_bytes(b"\x00 no program")
        runner.chmod(0o755)
        settings = Settings(
            chat_cli=None, scenario=scenario.scenario(said()), out=self.out, one_process=runner
        )
        report = self.report_of(settings)

        self.assertRegex(
            report["stopped"], "^the runner could not be started: .*Exec format error"
        )
        self.assertEqual((report["ok"], report["nodes"], report["check"]), (False, [], None))
        self.assertIn("\nnot checked: no result of the runner was read\n", self.written())

    def test_a_run_of_a_groupv3_group_reports_its_group(self):
        report = self.run_of(group="v3")

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual(report["group"], "v3")
        self.assertEqual(self.left("plan.json")["group"], "v3")

    def test_what_a_run_in_one_process_prints_is_its_report_as_text(self):
        report = self.run_of(network={"seed": 7})

        printed = (self.out / "run.txt").read_text(encoding="utf-8")
        self.assertEqual(printed, table.render(report))
        lines = printed.splitlines()
        self.assertRegex(
            lines[0],
            r"^3 members, fast group timers, one process, 0\.500s on a virtual clock "
            r"in 0\.\d{3}s of wall time, seed 0, network seed 7, loss 0, welcome loss 0$",
        )
        self.assertEqual(
            lines[1:3], ["whole: 3 of 3 members at the tip", "the report is as expected"]
        )
        self.assertEqual(
            lines[5].split(), "member installation arrived passed held lost errors".split()
        )
        self.assertEqual(
            [line.split()[0] for line in lines[6:9]], ["saro", "raya", "pax"]
        )


class TheResult(Scratch):
    """A result the runner left, kept as a fixture: five members with a network model.

    A cut between saro and pax has pax lose the post of saro. Then a candidate is withheld from
    pax in the batch that seats kes, and the group applies it.
    """

    PLAN = {
        "members": ["saro", "raya", "pax", "mira", "kes"],
        "timers": "fast",
        "budget_s": 30,
        "start": START,
        "network": {"seed": 7, "loss": 0, "welcome_loss": 0, "delay_ms": {"mira": 40}},
        "unwaited": {"rosters": [], "posts": ["pax"]},
        "steps": [
            {"step": "seat", "to": 4, "batch": 1},
            {"step": "cut", "sides": [["saro"], ["pax"]]},
            {"step": "post", "from": ["saro"]},
            {"step": "heal"},
            {"step": "withhold", "from": "pax"},
            {"step": "seat", "to": 5, "batch": 1},
            {"step": "post", "from": ["kes"]},
        ],
    }

    def setUp(self):
        super().setUp()
        self.result = json.loads((FIXTURES / "result.json").read_text(encoding="utf-8"))

    def test_the_stand_in_leaves_a_result_of_the_fields_the_runner_leaves(self):
        plan = self.scratch / "plan.json"
        plan.write_text(json.dumps(self.PLAN), encoding="utf-8")
        subprocess.run(
            [sys.executable, "-B", str(STANDIN_RUNNER), "--fault", "applied", plan, self.out],
            check=True,
        )
        left = json.loads((self.out / "result.json").read_text(encoding="utf-8"))

        # Every member of the kept run rejected a frame, and of the stand-in's the one alone.
        for member in self.result["members"] + left["members"]:
            member["inbound_errors"] = {}
        self.assertEqual(shape(left), shape(self.result))
        for held in (left, self.result):
            self.assertEqual((held["steps_done"], held["stopped"]), (7, None))
            self.assertEqual(held["members"][2]["network"]["withheld"]["applied"], True)

    def test_the_stand_in_leaves_a_grow_of_the_fields_the_runner_leaves(self):
        kept = json.loads((FIXTURES / "grow_result.json").read_text(encoding="utf-8"))
        plan = self.scratch / "plan.json"
        unwaited = {"rosters": [], "posts": []}
        plan.write_text(json.dumps(dict(self.PLAN, unwaited=unwaited, steps=[GROW])), "utf-8")
        subprocess.run([sys.executable, "-B", str(STANDIN_RUNNER), plan, self.out], check=True)
        left = json.loads((self.out / "result.json").read_text(encoding="utf-8"))

        # The kept members rejected frames and published under the conversation's app id, and
        # the stand-in's do neither.
        for member in kept["members"] + left["members"]:
            member["inbound_errors"], member["network"]["own"] = {}, []
        self.assertEqual(kinds(left), kinds(kept))
        self.assertEqual(left["grow"]["ended"], kept["grow"]["ended"])

    def report_leaving(self, result: object, status: int = 0, **changed) -> dict:
        """What a run of five with a network model reports whose runner leaves `result`."""
        scratch = Path(tempfile.mkdtemp(dir=self.scratch))
        settings = Settings(
            chat_cli=None,
            scenario=scenario.scenario(said(members=5, network={"seed": 7}, **changed)),
            out=scratch / "run",
            one_process=runner_leaving(scratch, result, status),
        )
        report = run(settings, Watch())
        kept = json.loads((settings.out / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(kept, json.loads(json.dumps(report)))
        self.assertEqual((settings.out / "run.txt").read_text("utf-8"), table.render(report))
        return report

    def stop_leaving(self, result: object, status: int = 0, **changed) -> str:
        """What stopped a run that read nothing of the `result` its runner left."""
        report = self.report_leaving(result, status, **changed)
        self.assertEqual((report["ok"], report["steps_done"], report["check"]), (False, 0, None))
        self.assertEqual((report["conversation"], report["nodes"]), (None, []))
        self.assertEqual((report["virtual_s"], report["wall_s"]), (None, None))
        self.assertIn("\nnot checked: no result of the runner was read\n", table.render(report))
        return report["stopped"]

    def test_a_run_reads_the_kept_result(self):
        report = self.report_leaving(self.result)

        self.assertIsNone(report["stopped"])
        self.assertEqual(
            (report["steps_done"], report["conversation"]), (7, self.result["conversation"])
        )
        self.assertEqual(
            [node["name"] for node in report["nodes"]], ["saro", "raya", "pax", "mira", "kes"]
        )
        self.assertEqual(report["nodes"][2]["network_dropped"], {"cut": 1, "withheld": 1})
        self.assertEqual((len(report["batches"]), len(report["posts"])), (4, 2))

    def test_a_result_without_a_field_a_run_reads_stops_the_run_and_names_the_field(self):
        for path in READ:
            with self.subTest(path):
                result = copy.deepcopy(self.result)
                holder, name = led_to(result, path)
                del holder[name]

                self.assertEqual(
                    self.stop_leaving(result),
                    f"the runner left a result that cannot be read: {path} is missing",
                )

    def test_a_run_reads_the_kept_grow_and_counts_the_members_seated(self):
        kept = json.loads((FIXTURES / "grow_result.json").read_text(encoding="utf-8"))

        report = self.report_leaving(kept, steps=[GROW])

        self.assertEqual((report["stopped"], report["grow"]), (None, kept["grow"]))
        self.assertEqual(report["seated"], 5)

    def test_a_grow_of_a_size_counts_no_member_it_removed_as_seated(self):
        kept = json.loads((FIXTURES / "grow_result.json").read_text(encoding="utf-8"))
        kept["grow"]["removed"] = ["saro", "raya"]

        self.assertEqual(self.report_leaving(kept, steps=[GROW])["seated"], 3)

    def test_a_grow_without_a_field_a_run_reads_stops_the_run_and_names_the_field(self):
        kept = json.loads((FIXTURES / "grow_result.json").read_text(encoding="utf-8"))
        for path in (
            "grow",
            "grow.ended",
            "grow.lines[0].from",
            "grow.lines[0].members",
            "grow.rounds[2].settled_s",
            "grow.never_joined",
            "grow.removed",
            "members[1].seated",
        ):
            with self.subTest(path):
                result = copy.deepcopy(kept)
                holder, name = led_to(result, path)
                del holder[name]

                self.assertEqual(
                    self.stop_leaving(result, steps=[GROW]),
                    f"the runner left a result that cannot be read: {path} is missing",
                )

    def test_a_result_with_a_field_that_is_not_what_a_run_reads_there_stops_the_run(self):
        for path, held, amiss in (
            ("stopped", 7, "stopped is not what a result holds"),
            ("steps_done", "seven", "steps_done is not what a result holds"),
            ("steps_done", True, "steps_done is not what a result holds"),
            ("conversation", None, "conversation is not what a result holds"),
            ("wall_s", "long", "wall_s is not what a result holds"),
            ("posts", {}, "posts is not a list"),
            ("members[1]", "raya", "members[1] is not an object"),
            ("posts[0].read_s[1]", "soon", "posts[0].read_s[1] is not what a result holds"),
            ("members[2].inbound_errors", [], "members[2].inbound_errors is not an object"),
            (
                "members[2].inbound_errors",
                {"garbled": "once"},
                "members[2].inbound_errors.garbled is not what a result holds",
            ),
            (
                "members[2].network.withheld",
                "saro",
                "members[2].network.withheld is not what a result holds",
            ),
            (
                "members[2].network.withheld.applied",
                1,
                "members[2].network.withheld.applied is not what a result holds",
            ),
        ):
            with self.subTest(path, held=held):
                result = copy.deepcopy(self.result)
                holder, name = led_to(result, path)
                holder[name] = held

                self.assertEqual(
                    self.stop_leaving(result),
                    f"the runner left a result that cannot be read: {amiss}",
                )

    def test_a_result_whose_members_are_not_those_of_the_plan_in_its_order_stops_the_run(self):
        members = self.result["members"]
        for others in (members[:4], members[::-1], []):
            with self.subTest(len(others)):
                self.assertEqual(
                    self.stop_leaving(dict(self.result, members=others)),
                    "the runner left a result that cannot be read: "
                    "members are not those of the plan, in its order",
                )

    def test_a_result_of_a_runner_that_ended_with_a_status_is_not_read(self):
        self.assertEqual(
            self.stop_leaving(self.result, status=3),
            "the runner ended with status 3: it said nothing on standard error",
        )

    def test_what_is_no_object_of_json_is_no_result(self):
        for left in ([], None, 7, "whole"):
            with self.subTest(left):
                self.assertEqual(
                    self.stop_leaving(left), "the runner ended with status 0 and left no result"
                )


class Render(unittest.TestCase):
    """The report of a run in one process as text."""

    def test_a_whole_run(self):
        self.assertEqual(
            table.render(in_one_process()),
            "3 members, fast group timers, one process, 1.350s on a virtual clock in 0.062s "
            "of wall time, seed 1\n"
            "whole: 3 of 3 members at the tip\n"
            "the report is as expected\n"
            "load average 1.50 at the start, 2.62 at the end\n"
            "\n"
            "member  installation  errors\n"
            "saro        a3f91c07       0\n"
            "raya        7b1e44d9       0\n"
            "pax         e09a3f52       0\n"
            "\n"
            "batch  joiners  members  seated\n"
            "1            2        3  1.250s\n"
            "\n"
            "post  from  read by  median   worst\n"
            "1      pax   2 of 2  0.000s  0.050s\n",
        )

    def test_a_run_with_a_network_model_names_it_and_what_became_of_each_members_frames(self):
        modelled = in_one_process(network=NETWORK)
        counts = ((21, 21, 0, {}), (22, 9, 1, {"loss": 11, "cut": 1}), (4, 3, 0, {"withheld": 1}))
        for node, (arrived, passed, held, dropped) in zip(modelled["nodes"], counts):
            node["network"] = {"frames": {"arrived": arrived, "passed": passed, "held": held}}
            node["network_dropped"] = dropped

        self.assertEqual(
            table.render(modelled).splitlines()[:9],
            [
                "3 members, fast group timers, one process, 1.350s on a virtual clock in 0.062s "
                "of wall time, seed 1, network seed 7, loss 0.05, welcome loss 0",
                "whole: 3 of 3 members at the tip",
                "the report is as expected",
                "load average 1.50 at the start, 2.62 at the end",
                "",
                "member  installation  arrived  passed  held  lost  errors",
                "saro        a3f91c07       21      21     0     0       0",
                "raya        7b1e44d9       22       9     1    12       0",
                "pax         e09a3f52        4       3     0     1       0",
            ],
        )
        self.assertEqual(
            table.render(modelled).splitlines()[9:], table.render(in_one_process()).splitlines()[9:]
        )

    def test_a_run_of_a_groupv3_group_names_it_in_place_of_the_timers(self):
        self.assertEqual(
            table.render(in_one_process(group="v3")).splitlines()[0],
            "3 members, GroupV3, one process, 1.350s on a virtual clock in 0.062s of wall time, "
            "seed 1",
        )

    def test_a_largest_message_that_is_not_the_default_is_said_not_to_apply(self):
        self.assertEqual(
            table.render(in_one_process(max_message_kib=64)).splitlines()[0],
            "3 members, fast group timers, one process, 1.350s on a virtual clock in 0.062s "
            "of wall time, seed 1, largest message 64 KiB not applied",
        )
        self.assertIn(
            ", largest message 1024 KiB not applied\n",
            table.render(in_one_process(max_message_kib=1024)),
        )
        self.assertNotIn("largest message", table.render(in_one_process()))

    def test_a_run_whose_runner_left_no_result_has_no_time_and_is_not_checked(self):
        stopped = no_result(stopped=KILLED)

        self.assertEqual(
            table.render(stopped),
            "3 members, fast group timers, one process on a virtual clock, seed 1\n"
            f"stopped after 0 of 2 steps: {KILLED}\n"
            "not checked: no result of the runner was read\n"
            "load average 1.50 at the start, 2.62 at the end\n"
            "\n"
            "member  installation  errors\n",
        )

    def test_a_run_that_was_interrupted_once_its_runner_had_ended_says_so(self):
        text = table.render(in_one_process(ok=False, stopped="interrupted by SIGINT", check=None))

        self.assertIn(
            "\nstopped after 2 of 2 steps: interrupted by SIGINT\n"
            "not checked: the run was interrupted\n",
            text,
        )

    def test_the_frames_the_members_rejected_are_counted_by_their_reason(self):
        whole = in_one_process()
        whole["nodes"][0]["inbound_errors"] = {"AeadError": 4}
        whole["nodes"][1]["inbound_errors"] = {"AeadError": 1, "WrongEpoch": 12}

        text = table.render(whole)
        self.assertEqual(
            [line.split()[-1] for line in text.splitlines()[5:9]], ["errors", "4", "13", "0"]
        )
        self.assertIn(
            "\n\n"
            "inbound error  frames  members\n"
            "WrongEpoch         12        1\n"
            "AeadError           5        2\n"
            "\n"
            "batch ",
            text,
        )

    def test_a_post_some_did_not_read_names_how_many_did(self):
        post = dict(in_one_process()["posts"][0], unread=["saro"])
        post["read"] = {"count": 1, "median": 0.05, "worst": 0.05}
        unread = dict(post, readers=1, read=None)

        self.assertEqual(
            table.render(in_one_process(posts=[post, unread])).splitlines()[-3:],
            [
                "post  from  read by  median   worst",
                "1      pax   1 of 2  0.050s  0.050s",
                "2      pax   0 of 1       -       -",
            ],
        )

    def test_a_grow_says_how_it_ended_and_each_line_after_the_tables(self):
        grown = in_one_process(grow=GROWN, check=None, unchecked="a `grow` step expects no outcome")

        self.assertTrue(
            table.render(grown).endswith(
                "1      pax   2 of 2  0.000s  0.050s\n"
                "\n"
                "2 main branches ran side by side: trunk at epoch 7, 2 main, 1 sub, 1 fell behind, "
                "1 behind at the end, 1 never joined\n"
                "2 rounds, 2 joiners, 1 of the adders refused a whole round, the longest 180.000s\n"
                "\n"
                "line   kind  from  forked at  epoch  members\n"
                "0     trunk     -          -      7        2\n"
                "1      main     0          3     12        1\n"
                "2       sub     1          5      5        1\n"
                "fell behind: pax at epoch 3, pax at epoch 5\n"
                "behind at the end: pax\n"
                "never joined: mira\n"
            ),
            table.render(grown),
        )

    def test_a_grow_of_a_size_says_how_many_members_it_removed(self):
        grown = in_one_process(grow=dict(GROWN, removed=["saro", "raya"]), check=None)

        self.assertIn(", 1 never joined, 2 removed\n", table.render(grown))

    def test_each_hold_is_a_line_of_its_seconds_after_the_tables(self):
        holds = [{"seconds": 60.0}, {"seconds": 0.5}]

        self.assertEqual(
            table.render(in_one_process(holds=holds)).splitlines()[-3:],
            ["", "hold 1: 60.000s", "hold 2: 0.500s"],
        )


class Row(unittest.TestCase):
    """A sweep's row for a run in one process."""

    def test_the_head_says_that_the_runs_are_one_process_each_and_their_seconds_virtual(self):
        self.assertEqual(
            table.head(one_process=True),
            "run  seconds  at the tip  verdict                 "
            "check (one process: seconds of its virtual clock)",
        )

    def test_a_row_gives_the_seconds_of_the_runs_virtual_clock(self):
        self.assertEqual(
            table.row(7, 41.26, in_one_process(network=NETWORK)),
            "  7      1.4      3 of 3  whole                   as expected; seed 1; network seed 7",
        )

    def test_the_row_of_a_grow_says_how_it_ended_in_place_of_a_check(self):
        grown = in_one_process(grow=GROWN, check=None, verdict="split at epoch 3", at_tip=2)

        self.assertEqual(
            table.row(2, 41.26, grown),
            "  2      1.4      2 of 3  split at epoch 3        2 main branches ran side by side: "
            "trunk at epoch 7, 2 main, 1 sub, 1 fell behind, 1 behind at the end, "
            "1 never joined; seed 1",
        )

    def test_the_row_of_a_run_whose_runner_left_no_result_has_no_seconds(self):
        stopped = no_result(stopped=KILLED)

        self.assertEqual(
            table.row(1, 0.52, stopped),
            "  1        -           -  -                       "
            f"not checked; stopped: {KILLED}; seed 1",
        )


class Sweeping(Scratch):
    """`sweep` against a stand-in for the runner."""

    def sweep_of(self, runs: int, *faults: str, **changed) -> dict:
        self.said: List[str] = []
        settings = Settings(
            chat_cli=None,
            scenario=scenario.scenario(said(**changed)),
            out=self.out,
            one_process=stand_in_runner(self.scratch, *faults),
        )
        return sweep(settings, runs, Interrupts(), self.said.append)

    def test_each_run_is_told_the_seeds_after_those_of_the_run_before_and_its_row_names_them(self):
        report = self.sweep_of(3, seed=4, network={"seed": 7})

        runs = ((1, 4, 7), (2, 5, 8), (3, 6, 9))
        for number, seed, network_seed in runs:
            run_of = self.out / str(number)
            plan = json.loads((run_of / "plan.json").read_text("utf-8"))
            self.assertEqual((plan["seed"], plan["network"]["seed"]), (seed, network_seed))
            kept = json.loads((run_of / "run.json").read_text("utf-8"))
            self.assertEqual(
                (kept["rig"], kept["seed"], kept["network"]["seed"]),
                ("one process", seed, network_seed),
            )
        self.assertEqual([row["seed"] for row in report["rows"]], [4, 5, 6])
        self.assertEqual([row["network_seed"] for row in report["rows"]], [7, 8, 9])
        self.assertEqual(self.said[0], table.head(one_process=True))
        self.assertEqual(
            [cells(row) for row in self.said[1:4]],
            [
                (str(number), "3 of 3", "whole", f"as expected; seed {seed}; network seed {seeded}")
                for number, seed, seeded in runs
            ],
        )
        self.assertEqual(self.said[4:], ["3 of 3 runs went as expected"])

    def test_a_row_has_the_virtual_seconds_of_its_run_and_the_json_those_it_took(self):
        steps = [{"step": "seat", "to": 3}, {"step": "hold", "seconds": 3600}]
        report = self.sweep_of(1, steps=steps)

        self.assertEqual(self.said[0], table.head(one_process=True))
        self.assertEqual(self.said[1].split()[:2], ["1", f"{3600 + standin_runner.SEATED:.1f}"])
        self.assertTrue(0 < report["rows"][0]["seconds"] < 600, report["rows"])
        self.assertEqual(json.loads((self.out / "sweep.json").read_text("utf-8")), report)
        self.assertEqual((self.out / "sweep.txt").read_text("utf-8").splitlines(), self.said)

    def test_a_run_whose_steps_the_runner_stopped_has_a_row_that_says_so(self):
        report = self.sweep_of(2, "behind=raya")

        stopped = "the roster of 3 members was not held within the budget by: raya"
        for row in self.said[1:3]:
            self.assertRegex(
                cells(row)[3],
                r"^raya \([0-9a-f]{8}\): never joined, expected tip, and 1 more; "
                f"stopped: {stopped}; seed [01]$",
            )
        self.assertEqual(self.said[3:], ["0 of 2 runs went as expected"])
        self.assertEqual([row["stopped"] for row in report["rows"]], [stopped] * 2)


class CommandLine(Scratch):
    """The command, run in this process."""

    def sim(self, *arguments: str, faults: Tuple[str, ...] = ()) -> Tuple[int, str]:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            runner = stand_in_runner(self.scratch, *faults)
            status = main([*arguments, "--one-process", str(runner), "--out", str(self.out)])
        return status, printed.getvalue()

    def refused(self, *arguments: str) -> str:
        """What the command says of a command line it refuses."""
        complained = io.StringIO()
        with self.assertRaises(SystemExit) as refused:
            with contextlib.redirect_stderr(complained):
                main([*arguments, "--out", str(self.out)])
        self.assertEqual(refused.exception.code, 2, arguments)
        self.assertFalse(self.out.exists())
        return complained.getvalue().splitlines()[-1]

    def test_a_run_in_one_process_that_is_as_expected_is_status_0(self):
        status, printed = self.sim("run", str(self.scenario()))

        self.assertEqual(status, 0)
        self.assertIn("\nwhole: 3 of 3 members at the tip\nthe report is as expected\n", printed)
        self.assertEqual(printed, (self.out / "run.txt").read_text(encoding="utf-8"))

    def test_a_run_in_one_process_whose_steps_stopped_is_status_1(self):
        status, printed = self.sim("run", str(self.scenario()), faults=("behind=raya",))

        self.assertEqual(status, 1)
        self.assertIn(
            "\nstopped after 0 of 2 steps: "
            "the roster of 3 members was not held within the budget by: raya\n",
            printed,
        )

    def test_a_run_whose_runner_left_no_result_is_status_1(self):
        status, printed = self.sim("run", str(self.scenario()), faults=("killed",))

        self.assertEqual(status, 1)
        self.assertIn("\nnot checked: no result of the runner was read\n", printed)

    def test_a_sweep_in_one_process_says_so_above_its_rows(self):
        status, printed = self.sim("sweep", str(self.scenario()), "--runs", "2")

        self.assertEqual(status, 0)
        self.assertEqual(printed.splitlines()[0], table.head(one_process=True))
        self.assertTrue(printed.endswith("\n2 of 2 runs went as expected\n"))
        self.assertEqual(printed, (self.out / "sweep.txt").read_text("utf-8"))

    def test_a_chat_cli_and_a_runner_together_are_refused_and_so_is_neither(self):
        chat_cli, runner = str(stand_in(self.scratch)), str(stand_in_runner(self.scratch))
        for command in (["run"], ["sweep", "--runs", "2"]):
            given = [*command, str(self.scenario())]
            self.assertEqual(
                self.refused(*given, "--chat-cli", chat_cli, "--one-process", runner),
                f"sim {command[0]}: error: argument --one-process: "
                "not allowed with argument --chat-cli",
            )
            self.assertEqual(
                self.refused(*given),
                f"sim {command[0]}: error: "
                "one of the arguments --chat-cli --one-process --image is required",
            )

    def test_a_runner_that_is_no_file_or_cannot_be_run_is_refused(self):
        not_to_run = self.scratch / "not-to-run"
        not_to_run.write_text("#!/bin/sh\n", encoding="utf-8")
        not_to_run.chmod(0o644)
        for command in (["run"], ["sweep", "--runs", "2"]):
            given = [*command, str(self.scenario()), "--one-process"]
            self.assertEqual(
                self.refused(*given, "nowhere"), "sim: error: --one-process: no such file: nowhere"
            )
            self.assertEqual(
                self.refused(*given, str(not_to_run)),
                f"sim: error: --one-process: not a file that can be run: {not_to_run}",
            )

    def test_a_runner_named_without_a_directory_is_the_file_of_that_name(self):
        stand_in_runner(self.scratch)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            status = main(
                ["run", str(self.scenario()), "--one-process", "runner", "--out", str(self.out)]
            )

        self.assertEqual(status, 0, printed.getvalue())
        self.assertIn("\nthe report is as expected\n", printed.getvalue())

    def test_a_grow_is_refused_but_in_one_process(self):
        chat_cli = str(stand_in(self.scratch))
        self.assertEqual(
            self.refused("run", str(self.scenario(steps=[GROW])), "--chat-cli", chat_cli),
            "sim: error: the scenario has a `grow` step, and only --one-process does one",
        )

    def test_a_groupv3_group_is_refused_but_in_one_process(self):
        chat_cli = str(stand_in(self.scratch))
        self.assertEqual(
            self.refused("run", str(self.scenario(group="v3")), "--chat-cli", chat_cli),
            "sim: error: the scenario's `group` is `v3`, and only --one-process seats a GroupV3 "
            "group",
        )

    def test_a_scenario_on_a_preset_is_refused_in_one_process_and_nothing_is_started(self):
        scenario_file = str(self.scenario(preset="logos.test"))
        runner = str(stand_in_runner(self.scratch))
        for command in (["run"], ["sweep", "--runs", "2"]):
            self.assertEqual(
                self.refused(*command, scenario_file, "--one-process", runner),
                "sim: error: --one-process: the scenario has `preset`, "
                "and one process joins no fleet",
            )
            # A process the command started and did not reap would be a child of this one.
            with self.assertRaises(ChildProcessError):
                os.waitpid(-1, os.WNOHANG)


class Signals(Driven):
    """A run in one process sent a signal while its runner runs."""

    def test_a_signal_ends_the_runner_and_the_run_is_not_checked(self):
        runner = stand_in_runner(self.scratch, "waits")
        driver = subprocess.Popen(
            [sys.executable, "-B", str(TOOL), "run", str(self.scenario())]
            + ["--one-process", str(runner), "--out", str(self.out)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # A session of its own, so that a test that fails can end whatever the tool left.
            start_new_session=True,
        )
        self.addCleanup(self.end, driver)
        self.wait_for(self.out / "runner.out", "\n")
        started = time.monotonic()
        driver.send_signal(signal.SIGTERM)
        printed, errors = driver.communicate(timeout=60)

        self.assertEqual((driver.returncode, errors), (128 + signal.SIGTERM, ""))
        self.assertLess(time.monotonic() - started, standin_runner.WAIT / 2)
        with self.assertRaises(ProcessLookupError):
            os.kill(int((self.out / "runner.out").read_text(encoding="utf-8")), 0)
        report = json.loads((self.out / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(report["stopped"], "interrupted by SIGTERM")
        self.assertEqual((report["ok"], report["check"], report["nodes"]), (False, None, []))
        self.assertIn(
            "\nstopped after 0 of 2 steps: interrupted by SIGTERM\n"
            "not checked: no result of the runner was read\n",
            printed,
        )
        self.assertEqual(printed, (self.out / "run.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
