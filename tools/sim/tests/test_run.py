import contextlib
import dataclasses
import functools
import io
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Iterator, List, Tuple
from unittest import mock

from sim import scenario
from sim.cli import SIGNALS, main
from sim.peers import Peer, Watch
from sim.run import CLUSTER_ID, Settings, run

from tests import forks_whose, stand_in, standin

TOOL = Path(__file__).resolve().parents[1]


def given(arguments: List[str], flag: str) -> List[str]:
    """Each value `flag` has in the arguments a peer was started with."""
    return [value for before, value in zip(arguments, arguments[1:]) if before == flag]


def said(members: int = 3, **changed) -> dict:
    """A scenario as a file says it: the creator seats every other member, and the last posts."""
    whole = {
        "members": members,
        "budget": 30,
        "steps": [{"step": "seat", "to": members}, {"step": "post"}],
    }
    whole.update(changed)
    return whole


def withholding(*after: dict, **changed) -> dict:
    """What makes `said` a scenario of four that withholds a candidate from pax.

    Three members are seated, and the candidate is one of the batch that seats mira. The steps
    `after` follow.
    """
    seat, withhold = {"step": "seat", "to": 3}, {"step": "withhold", "from": "pax"}
    steps = [seat, withhold, {"step": "seat", "to": 4}, *after]
    return dict(members=4, network={"seed": 7}, steps=steps, **changed)


# What a stand-in comes to when the group applied the candidate withheld in `withholding`.
APPLIED = {"verdict": "split at epoch 2"}


class Scratch(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.out = self.scratch / "run"

    def scenario(self, **changed) -> Path:
        """A file that holds a scenario."""
        file = self.scratch / "scenario.json"
        file.write_text(json.dumps(said(**changed)), encoding="utf-8")
        return file

    def arguments(self, name: str) -> List[str]:
        """What the stand-in for the peer `name` was started with."""
        kept = (self.out / "data" / f"{name}.json").read_text(encoding="utf-8")
        return json.loads(kept)["arguments"]

    def lines(self, name: str) -> List[dict]:
        kept = (self.out / f"{name}.out").read_text(encoding="utf-8")
        return [json.loads(line) for line in kept.splitlines()]

    def written(self) -> str:
        """Every file the command wrote, one after another."""
        files = sorted(file for file in self.out.rglob("*") if file.is_file())
        return "\n".join(file.read_text(encoding="utf-8", errors="replace") for file in files)


@contextlib.contextmanager
def noting(method: str) -> Iterator[List[Tuple[float, float, tuple]]]:
    """Notes each call of a method of a peer while entered: when it began and ended, with what."""
    noted: List[Tuple[float, float, tuple]] = []
    unnoted = getattr(Peer, method)

    def call(peer: Peer, *arguments):
        began = time.monotonic()
        try:
            return unnoted(peer, *arguments)
        finally:
            noted.append((began, time.monotonic(), arguments))

    with mock.patch.object(Peer, method, call):
        yield noted


@contextlib.contextmanager
def inputs() -> Iterator[List[Tuple[str, str, float]]]:
    """Notes each input written while entered, in order: its peer, its text, and when."""
    noted: List[Tuple[str, str, float]] = []
    unnoted = Peer.input

    def write(peer: Peer, text: str, deadline: float):
        noted.append((peer.name, text, time.monotonic()))
        return unnoted(peer, text, deadline)

    with mock.patch.object(Peer, "input", write):
        yield noted


def commands(written: List[Tuple[str, str, float]]) -> List[Tuple[str, str]]:
    """Who was written which command, but for a node and a network model asked their state.

    A command of the network model is whole, and any other is its first word.
    """
    return [
        (name, text if text.startswith("/network ") else text.split(" ")[0])
        for name, text, _ in written
        if text not in ("/node", "/network")
    ]


def member(report: dict, name: str) -> str:
    """A member as a difference names it."""
    (node,) = [node for node in report["nodes"] if node["name"] == name]
    return f"{name} ({node['installation']})"


class Running(Scratch):
    """`run` against stand-ins for chat-cli: what it reports, and that it leaves no process."""

    def run_of(self, *faults: str, **changed) -> dict:
        settings = Settings(
            chat_cli=stand_in(self.scratch, *faults),
            scenario=scenario.scenario(said(**changed)),
            out=self.out,
        )
        return self.report_of(settings)

    def report_of(self, settings: Settings) -> dict:
        report = run(settings, Watch())
        # Each process a run starts is a child of this one, and one that was reaped is none.
        with self.assertRaises(ChildProcessError, msg="the run left a process"):
            os.waitpid(-1, os.WNOHANG)
        kept = json.loads((self.out / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(kept, json.loads(json.dumps(report)))
        return report

    def stop_of(self, *faults: str, **changed) -> str:
        report = self.run_of(*faults, **changed)
        self.assertFalse(report["ok"])
        return report["stopped"]

    def test_three_peers_form_a_group_and_read_one_post(self):
        report = self.run_of()

        self.assertTrue(report["ok"])
        self.assertIsNone(report["stopped"])
        self.assertEqual((report["steps_done"], report["steps"]), (2, 2))
        self.assertEqual(
            [(node["name"], node["dials"]) for node in report["nodes"]],
            [("saro", []), ("raya", ["saro"]), ("pax", ["saro", "raya"])],
        )
        for node in report["nodes"]:
            self.assertEqual(node["wired"], {"peers": 2, "mesh_peers": 2}, node["name"])
            self.assertEqual(
                node["end"],
                {
                    "peers": 2,
                    "mesh_peers": 2,
                    "dials_ok": len(node["dials"]),
                    "dials_failed": 0,
                    "publish_refused": 0,
                    "inbound_dropped": 0,
                },
                node["name"],
            )
        (batch,) = report["batches"]
        self.assertEqual((batch["joiners"], batch["members"]), (2, 3))
        self.assertEqual(batch["welcome_bytes"], [3 * standin.WELCOME] * 2)
        (post,) = report["posts"]
        self.assertEqual((post["poster"], post["readers"], post["unread"]), ("pax", 2, []))
        self.assertEqual((post["read"]["count"], post["lookups"]), (2, 2))

    def test_the_report_of_the_logs_is_held_against_what_the_scenario_expects(self):
        report = self.run_of()

        self.assertEqual(report["check"], {"as_expected": True, "differences": []})
        self.assertEqual((report["verdict"], report["at_tip"], report["seated"]), ("whole", 3, 3))
        expected = json.loads((self.out / "expect.json").read_text(encoding="utf-8"))
        self.assertEqual(expected["conversation"], report["conversation"])
        self.assertEqual(
            expected["installations"],
            {
                node["installation"]: {"name": node["name"], "position": "tip"}
                for node in report["nodes"]
            },
        )
        # A peer's id is the one its log bears: the start of the key its first line gives.
        for node in report["nodes"]:
            self.assertEqual(node["installation"], self.lines(node["name"])[0]["installation"][:8])
            log = (self.out / f"{node['name']}.log").read_text(encoding="utf-8")
            self.assertIn(f"signer={node['installation']} ", log)
        drawn = json.loads((self.out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([held["id"] for held in drawn["conversations"]], [report["conversation"]])
        self.assertIn("\n  whole\n", (self.out / "report.txt").read_text(encoding="utf-8"))

    def test_a_run_writes_the_load_average_at_its_start_and_at_its_end(self):
        with mock.patch("os.getloadavg", side_effect=[(1.5, 1.25, 1.0), (2.5, 2.25, 2.0)]):
            report = self.run_of()

        self.assertEqual(report["load"], {"start": [1.5, 1.25, 1.0], "end": [2.5, 2.25, 2.0]})

    def test_each_peer_is_given_the_network_and_the_nodes_it_dials(self):
        report = self.run_of(timers="default", max_message_kib=64)

        node = {name: self.lines(name)[0]["node_address"] for name in ("saro", "raya", "pax")}
        self.assertEqual(given(self.arguments("saro"), "--static-node"), [])
        self.assertEqual(given(self.arguments("raya"), "--static-node"), [node["saro"]])
        self.assertEqual(
            given(self.arguments("pax"), "--static-node"), [node["saro"], node["raya"]]
        )
        for name in node:
            arguments = self.arguments(name)
            self.assertEqual(given(arguments, "--name"), [name])
            self.assertEqual(given(arguments, "--cluster-id"), [str(CLUSTER_ID)])
            self.assertEqual(given(arguments, "--group-commit"), ["default"])
            self.assertEqual(given(arguments, "--max-message-kib"), ["64"])
            self.assertEqual(given(arguments, "--log-file"), [str(self.out / f"{name}.log")])
            self.assertFalse([word for word in arguments if word.endswith("-ms")])
            self.assertFalse([word for word in arguments if word.startswith("--preset")])
        self.assertEqual((report["timers"], report["max_message_kib"]), ("default", 64))
        self.assertEqual((report["preset"], report["cluster_id"]), (None, CLUSTER_ID))

    def test_each_peer_of_a_preset_is_given_its_name_and_no_network_of_the_runs_own(self):
        report = self.run_of(preset="logos.test", timers="default")

        self.assertTrue(report["ok"], report["stopped"])
        local = ("--cluster-id", "--max-message-kib", "--static-node")
        for name in ("saro", "raya", "pax"):
            arguments = self.arguments(name)
            self.assertIn("--preset=logos.test", arguments)
            self.assertFalse([word for word in arguments if word.startswith(local)], name)
            self.assertEqual(given(arguments, "--group-commit"), ["default"])
            (registry,) = given(arguments, "--registry-url")
            self.assertTrue(registry.startswith("http://127.0.0.1:"), registry)
        self.assertEqual(
            (report["preset"], report["cluster_id"], report["max_message_kib"]),
            ("logos.test", None, None),
        )
        self.assertEqual([node["dials"] for node in report["nodes"]], [[], [], []])

    def test_a_preset_whose_name_starts_with_a_dash_is_given_in_one_word_with_its_flag(self):
        # A word of its own that starts with a dash is a flag to chat-cli.
        self.run_of(preset="-x")

        self.assertIn("--preset=-x", self.arguments("saro"))
        self.assertNotIn("-x", self.arguments("saro"))

    def test_the_timers_are_the_fast_ones_unless_the_scenario_says_otherwise(self):
        report = self.run_of()

        self.assertEqual(report["timers"], "fast")
        self.assertEqual(given(self.arguments("pax"), "--group-commit"), ["fast"])

    def test_durations_are_given_each_by_its_flag_over_the_default_set(self):
        timers = {"consensus_timeout": 20000, "recovery_auto_commit_delay": 0}
        report = self.run_of(timers=timers)

        self.assertEqual(report["timers"], timers)
        for name in ("saro", "raya", "pax"):
            arguments = self.arguments(name)
            self.assertEqual(given(arguments, "--group-commit"), ["default"])
            self.assertEqual(given(arguments, "--group-consensus-timeout-ms"), ["20000"])
            self.assertEqual(given(arguments, "--group-recovery-auto-commit-delay-ms"), ["0"])
            self.assertEqual(len([word for word in arguments if word.endswith("-ms")]), 2)

    def test_the_run_keeps_the_lines_and_the_standard_error_of_each_peer(self):
        self.run_of()

        for name in ("saro", "raya", "pax"):
            lines = self.lines(name)
            self.assertEqual((lines[0]["type"], lines[0]["name"]), ("ready", name))
            self.assertIn("result", [line["type"] for line in lines])
            self.assertTrue((self.out / f"{name}.stderr").is_file())
        joined = {"type": "joined", "convo": mock.ANY, "kind": "group", "app_id": mock.ANY}
        self.assertIn(joined, self.lines("pax"))

    def test_seven_peers_are_a_ring_with_chords_seated_a_batch_at_a_time(self):
        steps = [{"step": "seat", "to": 7, "batch": 3}, {"step": "post"}]
        report = self.run_of(members=7, steps=steps)

        self.assertTrue(report["ok"], report["stopped"])
        self.assertEqual(report["nodes"][6]["name"], "peer7")
        self.assertEqual(len(given(self.arguments("peer7"), "--static-node")), 6)
        for node in report["nodes"]:
            self.assertEqual(node["wired"], {"peers": 6, "mesh_peers": 6}, node["name"])
        self.assertEqual(
            [(batch["joiners"], batch["members"]) for batch in report["batches"]], [(3, 4), (3, 7)]
        )
        self.assertEqual(
            [batch["welcome_bytes"] for batch in report["batches"]],
            [[4 * standin.WELCOME] * 3, [7 * standin.WELCOME] * 3],
        )
        (post,) = report["posts"]
        self.assertEqual((post["poster"], post["read"]["count"]), ("peer7", 6))

    def test_the_steps_are_done_in_their_order_by_the_members_seated_by_then(self):
        steps = [
            {"step": "post"},
            {"step": "seat", "to": 2},
            {"step": "post", "from": "saro"},
            {"step": "seat", "to": 4, "batch": 1},
            {"step": "post", "from": "all"},
        ]
        report = self.run_of(members=5, steps=steps)

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual((report["steps_done"], report["seated"], report["at_tip"]), (5, 4, 4))
        self.assertEqual(
            [(batch["joiners"], batch["members"]) for batch in report["batches"]],
            [(1, 2), (1, 3), (1, 4)],
        )
        self.assertEqual(
            [(post["poster"], post["readers"]) for post in report["posts"]],
            [("saro", 0), ("saro", 1), ("saro", 3), ("raya", 3), ("pax", 3), ("mira", 3)],
        )
        # The member no step seats runs, and is in no group.
        self.assertEqual(self.lines("kes")[0]["type"], "ready")
        self.assertNotIn("joined", [line["type"] for line in self.lines("kes")])

    def test_a_time_runs_from_the_input_to_the_line_or_the_record_it_waits_for(self):
        report = self.run_of("slow")

        self.assertTrue(report["ok"], report["stopped"])
        for number, node in enumerate(report["nodes"], 1):
            self.assertTrue(standin.START <= node["start_s"] < standin.START + 5, node)
            self.assertTrue(0 <= node["ready_s"] - number * standin.START < 5, node)
        self.assertGreaterEqual(report["wired_s"], report["nodes"][-1]["ready_s"])
        (batch,) = report["batches"]
        for took in (batch["joined_s"], batch["seated_s"]):
            self.assertTrue(standin.ROSTER <= took < standin.ROSTER + 5, batch)
        (post,) = report["posts"]
        read = standin.DELIVERY + standin.LOOKUP
        self.assertTrue(read <= post["read"]["median"] <= post["read"]["worst"] < read + 5, post)
        self.assertTrue(0 <= post["delivered"]["median"] - standin.DELIVERY < 5, post)
        self.assertTrue(standin.LOOKUP <= post["lookup_mean"] < standin.LOOKUP + 5, post)

    def test_a_peer_that_writes_no_ready_line_stops_the_run_at_its_start(self):
        report = self.run_of("silent=raya", budget=2)

        self.assertEqual(report["stopped"], "raya: no ready line within the budget")
        self.assertFalse(report["ok"])
        self.assertEqual([node["name"] for node in report["nodes"]], ["saro", "raya"])
        self.assertIn("end", report["nodes"][0])
        self.assertNotIn("ready_s", report["nodes"][1])

    def test_a_run_that_made_no_conversation_is_not_checked(self):
        report = self.run_of("silent=raya", budget=2)

        self.assertIsNone(report["conversation"])
        self.assertIsNone(report["check"])
        self.assertEqual((report["verdict"], report["at_tip"]), (None, None))
        self.assertEqual(report["steps_done"], 0)
        self.assertFalse((self.out / "expect.json").exists())

    def test_a_peer_that_ends_at_its_start_is_named_with_its_status(self):
        self.assertEqual(
            self.stop_of("dies-at-start=raya"),
            f"raya ended with status {standin.STATUS}: see {self.out / 'raya.stderr'}",
        )

    def test_a_peer_that_ends_before_its_ready_line_is_named_with_its_last_complaint(self):
        report = self.run_of(preset=standin.NO_PRESET)

        self.assertEqual(
            report["stopped"],
            "saro ended with status 1: chat-cli: failed to start the delivery node: "
            f"{standin.UNKNOWN}: {standin.NO_PRESET}",
        )
        self.assertEqual([node["name"] for node in report["nodes"]], ["saro"])
        self.assertEqual((report["ok"], report["steps_done"], report["check"]), (False, 0, None))

    def test_a_line_that_is_not_json_stops_the_run_with_a_report(self):
        self.assertEqual(
            self.stop_of("stray-line=raya"), "raya: a line that is no JSON object: starting"
        )

    def test_a_chat_cli_that_is_no_program_stops_the_run_and_the_report_is_written(self):
        chat_cli = self.scratch / "chat-cli"
        chat_cli.write_bytes(b"\x00 no program")
        chat_cli.chmod(0o755)
        settings = Settings(chat_cli=chat_cli, scenario=scenario.scenario(said()), out=self.out)
        report = self.report_of(settings)

        self.assertRegex(report["stopped"], "^saro could not be started: .*Exec format error")
        self.assertEqual((report["ok"], report["nodes"], report["check"]), (False, [], None))
        self.assertIn("\nnot checked: the run made no conversation\n", self.written())

    def test_a_registry_that_ends_at_its_start_stops_the_run_before_any_peer(self):
        registry = self.scratch / "registry.py"
        registry.write_text("import sys\nsys.exit(4)\n", encoding="utf-8")
        settings = Settings(
            chat_cli=stand_in(self.scratch),
            scenario=scenario.scenario(said()),
            out=self.out,
            registry=registry,
        )
        report = self.report_of(settings)

        self.assertEqual(
            report["stopped"],
            f"the registry ended with status 4: see {self.out / 'registry.stderr'}",
        )
        self.assertEqual(report["nodes"], [])

    def test_the_wiring_is_waited_for_until_its_budget_and_no_longer(self):
        budget = 2
        with noting("input") as inputs:
            self.stop_of("unlinked", budget=budget)

        # Nothing but its state is asked of a node that is not wired.
        asked = [began for began, _, _ in inputs]
        self.assertTrue(budget / 2 < asked[-1] - asked[0] < 2 * budget, asked[-1] - asked[0])

    def test_a_batch_is_waited_for_until_its_budget_and_no_longer(self):
        budget = 2
        with noting("input") as inputs:
            self.stop_of("mute=raya", budget=budget)

        added = [text.split(" ", 1)[0] for _, _, (text, _) in inputs].index("/add")
        # What a node ends with is asked of it once the batch is given up.
        waited = inputs[added + 1][0] - inputs[added][0]
        self.assertTrue(budget <= waited < 2 * budget, waited)

    def test_nodes_that_dial_nobody_are_not_wired_and_are_named(self):
        self.assertEqual(
            self.stop_of("unlinked", budget=2), "not wired within the budget: saro, raya, pax"
        )

    def test_a_link_short_of_six_is_not_wired(self):
        self.assertEqual(
            self.stop_of("drops-a-dial=peer7", members=7, budget=2),
            "not wired within the budget: saro, peer7",
        )

    def test_a_mesh_of_three_is_not_wired(self):
        self.assertEqual(
            self.stop_of("mesh-of-three", members=7, budget=2),
            "not wired within the budget: saro, raya, pax, mira, kes, tomo, peer7",
        )

    def test_a_mesh_of_four_is_wired(self):
        report = self.run_of("mesh-of-four", members=7)

        self.assertTrue(report["ok"], report["stopped"])
        for node in report["nodes"]:
            self.assertEqual(node["wired"], {"peers": 6, "mesh_peers": 4}, node["name"])

    def test_a_run_on_a_preset_waits_for_a_first_link_of_every_node_and_for_no_mesh(self):
        report = self.run_of("links-late=raya", preset="logos.test")

        self.assertTrue(report["ok"], report["stopped"])
        for node in report["nodes"]:
            self.assertEqual(
                node["wired"], {"peers": standin.ENTRY_NODES, "mesh_peers": 0}, node["name"]
            )
        # Raya holds no link until `LINK` after its start.
        self.assertGreater(report["wired_s"], standin.LINK)

    def test_a_node_of_a_preset_with_no_link_within_the_budget_stops_the_run_and_is_named(self):
        self.assertEqual(
            self.stop_of("links-never=saro", "links-never=pax", preset="logos.test", budget=2),
            "no link within the budget: saro, pax",
        )

    def test_a_first_link_is_waited_for_until_its_budget_and_no_longer(self):
        budget = 2
        with noting("input") as inputs:
            self.stop_of("links-never", preset="logos.test", budget=budget)

        # Nothing but its state is asked of a node that holds no link.
        asked = [began for began, _, _ in inputs]
        self.assertTrue(budget / 2 < asked[-1] - asked[0] < 2 * budget, asked[-1] - asked[0])

    def test_a_network_model_and_its_steps_run_on_a_preset(self):
        report = self.run_of(**withholding(preset="logos.test"))

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual((report["withheld"]["from"], report["held_to"]), ("pax", "not applied"))
        for name in ("saro", "pax"):
            self.assertIn("--preset=logos.test", self.arguments(name))
            self.assertEqual(given(self.arguments(name), "--network-seed"), ["7"])

    def test_a_hold_asks_every_node_its_state_before_it_and_after_and_keeps_both(self):
        hold = {"step": "hold", "seconds": 0.5}
        with inputs() as written:
            report = self.run_of(steps=[{"step": "seat", "to": 3}, hold, {"step": "post"}])

        self.assertTrue(report["ok"], report["stopped"])
        self.assertEqual((report["steps_done"], report["steps"]), (3, 3))
        (held,) = report["holds"]
        self.assertEqual(held["seconds"], 0.5)
        # Each node dialed the nodes started before it.
        for dialed, (node, name) in enumerate(zip(held["nodes"], ("saro", "raya", "pax"))):
            state = {
                "peers": 2,
                "mesh_peers": 2,
                "dials_ok": dialed,
                "dials_failed": 0,
                "publish_refused": 0,
                "inbound_dropped": 0,
            }
            self.assertEqual(node, {"name": name, "before": state, "after": state})
        asked = [text.split(" ")[0] for _, text, _ in written]
        add, post = asked.index("/add"), asked.index("/send")
        self.assertEqual(
            [(name, text) for name, text, _ in written[add + 1 : post]],
            [(name, "/node") for name in ("saro", "raya", "pax")] * 2,
        )
        self.assertGreaterEqual(written[add + 4][2] - written[add + 3][2], 0.5)

    def test_a_hold_on_a_network_of_the_runs_own_derives_nothing_of_the_dials(self):
        steps = [{"step": "seat", "to": 3}, {"step": "hold", "seconds": 0.5}]
        report = self.run_of(steps=steps)

        self.assertEqual(sorted(report["holds"][0]), ["nodes", "seconds"])
        for node in report["holds"][0]["nodes"] + report["nodes"]:
            self.assertNotIn("unheld", node)

    def test_a_hold_lasts_its_seconds_though_they_are_more_than_the_budget(self):
        budget, seconds = 2, 2.5
        steps = [{"step": "hold", "seconds": seconds}, {"step": "seat", "to": 3}]
        with inputs() as written:
            report = self.run_of(budget=budget, steps=steps)

        self.assertTrue(report["ok"], report["stopped"])
        created = [text.split(" ")[0] for _, text, _ in written].index("/new")
        self.assertGreaterEqual(written[created + 4][2] - written[created + 3][2], seconds)

    def test_a_hold_on_a_preset_tells_the_links_each_node_no_longer_holds_and_their_rate(self):
        steps = [{"step": "seat", "to": 3}, {"step": "hold", "seconds": 0.5}]
        report = self.run_of("redials=raya", preset="logos.test", steps=steps)

        self.assertTrue(report["ok"], report["stopped"])
        (held,) = report["holds"]
        raya = held["nodes"][1]
        # Each answer of raya's node has one dial more than the one before, and its six links.
        self.assertEqual((raya["before"]["dials_ok"], raya["after"]["dials_ok"]), (7, 8))
        self.assertEqual((raya["before"]["peers"], raya["after"]["peers"]), (6, 6))
        self.assertEqual([node["unheld"] for node in held["nodes"]], [0, 1, 0])
        # One link, of three nodes, in half a second.
        self.assertAlmostEqual(held["rate"], 40.0)
        self.assertEqual([node["unheld"] for node in report["nodes"]], [0, 3, 0])

    def test_a_link_lost_in_a_hold_and_not_dialed_again_is_one_no_longer_held(self):
        steps = [{"step": "seat", "to": 3}, {"step": "hold", "seconds": 0.5}]
        report = self.run_of("drops-a-link=raya", preset="logos.test", steps=steps)

        self.assertTrue(report["ok"], report["stopped"])
        (held,) = report["holds"]
        raya = held["nodes"][1]
        # Each answer of raya's node has one link fewer than the one before, and its six dials.
        self.assertEqual((raya["before"]["peers"], raya["after"]["peers"]), (5, 4))
        self.assertEqual((raya["before"]["dials_ok"], raya["after"]["dials_ok"]), (6, 6))
        self.assertEqual([node["unheld"] for node in held["nodes"]], [0, 1, 0])
        self.assertEqual([node["unheld"] for node in report["nodes"]], [0, 3, 0])

    def test_a_node_that_does_not_tell_its_dials_has_no_count_of_links_no_longer_held(self):
        steps = [{"step": "seat", "to": 3}, {"step": "hold", "seconds": 0.5}]
        report = self.run_of("tells-no-dials=pax", preset="logos.test", steps=steps)

        self.assertTrue(report["ok"], report["stopped"])
        (held,) = report["holds"]
        self.assertEqual([node["unheld"] for node in held["nodes"]], [0, 0, None])
        self.assertIsNone(held["rate"])
        self.assertEqual([node["unheld"] for node in report["nodes"]], [0, 0, None])

    def test_what_a_run_on_a_preset_prints_names_it_the_dials_and_the_rate_of_its_hold(self):
        steps = [{"step": "seat", "to": 3}, {"step": "post"}, {"step": "hold", "seconds": 0.5}]
        self.run_of("redials=raya", preset="logos.test", steps=steps)

        printed = (self.out / "run.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(printed[0], "3 members, fast group timers, preset logos.test")
        self.assertEqual(
            printed[5].split()[3:],
            "dials wired mesh end mesh dialed failed unheld refused dropped errors exit".split(),
        )
        self.assertEqual(
            [line.split()[3:11] for line in printed[6:9]],
            [
                ["0", "6", "0", "6", "0", "6", "0", "0"],
                ["0", "6", "0", "6", "0", "9", "0", "3"],
                ["0", "6", "0", "6", "0", "6", "0", "0"],
            ],
        )
        self.assertEqual(printed[-1], "hold 1: 0.500s, unheld 40.00 a node a minute")

    def test_what_a_run_on_a_network_of_its_own_prints_names_the_dials_of_each_node(self):
        steps = [{"step": "seat", "to": 3}, {"step": "post"}, {"step": "hold", "seconds": 0.5}]
        self.run_of(steps=steps)

        printed = (self.out / "run.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(printed[0], "3 members, fast group timers, largest message 512 KiB")
        self.assertEqual(
            printed[5].split()[3:],
            "dials wired mesh end mesh dialed failed refused dropped errors exit".split(),
        )
        self.assertEqual(
            [line.split()[3:10] for line in printed[6:9]],
            [
                ["0", "2", "2", "2", "2", "0", "0"],
                ["1", "2", "2", "2", "2", "1", "0"],
                ["2", "2", "2", "2", "2", "2", "0"],
            ],
        )
        self.assertEqual(printed[-1], "hold 1: 0.500s")

    def test_a_scenario_of_one_member_runs_its_creator_alone(self):
        expect = {"verdict": "not enough data"}
        report = self.run_of(members=1, steps=[{"step": "post"}], expect=expect)

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual([node["name"] for node in report["nodes"]], ["saro"])
        self.assertEqual(report["nodes"][0]["wired"], {"peers": 0, "mesh_peers": 0})

    def test_an_add_the_creator_refuses_stops_the_run(self):
        self.assertEqual(
            self.stop_of("refuses-add=saro"),
            "saro: /add was refused: The stand-in refuses to add anybody.",
        )

    def test_a_budget_that_runs_out_ends_the_steps_and_the_logs_are_still_checked(self):
        report = self.run_of("mute=raya", budget=2)

        self.assertFalse(report["ok"])
        self.assertEqual(report["stopped"], "raya: no line of a new roster within the budget")
        self.assertEqual((report["steps_done"], report["batches"], report["posts"]), (0, [], []))
        self.assertEqual((report["verdict"], report["at_tip"], report["seated"]), ("whole", 2, 3))
        self.assertEqual(
            report["check"],
            {
                "as_expected": False,
                "differences": [
                    f"{member(report, 'raya')}: never joined, expected tip",
                    f"post 1 of {member(report, 'pax')}: not in the report",
                ],
            },
        )

    def test_a_member_the_scenario_expects_elsewhere_than_the_tip_is_not_waited_on(self):
        expect = {"positions": {"raya": "never joined"}, "posts": {"raya": "no record"}}
        report = self.run_of("mute=raya", expect=expect)

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual((report["steps_done"], report["at_tip"], report["seated"]), (2, 2, 3))
        (post,) = report["posts"]
        self.assertEqual((post["readers"], post["unread"]), (1, []))

    def test_members_that_hold_one_roster_in_two_states_are_a_split(self):
        report = self.run_of("split=pax")

        self.assertEqual(
            report["stopped"], "the group split: 3 of its 3 members hold epoch 1, in 2 states"
        )
        self.assertEqual(report["verdict"], "split seen from epoch 1")
        self.assertEqual(
            report["check"]["differences"][0], "verdict: split seen from epoch 1, expected whole"
        )

    def test_a_split_is_told_also_when_another_member_is_behind(self):
        self.assertEqual(
            self.stop_of("split=pax", "mute=raya", budget=2),
            "the group split: 2 of its 3 members hold epoch 1, in 2 states",
        )

    def test_a_member_that_holds_the_epoch_on_a_roster_of_its_own_is_a_split(self):
        self.assertEqual(
            self.stop_of("short-roster=raya", budget=2),
            "the group split: 3 of its 3 members hold epoch 1, in 2 states",
        )

    def test_a_member_that_ends_while_another_is_waited_on_is_named_at_once(self):
        started = time.monotonic()
        report = self.run_of("mute=raya", "dies-at-roster=pax", budget=20)

        self.assertEqual(
            report["stopped"],
            f"pax ended with status {standin.STATUS}: see {self.out / 'pax.stderr'}",
        )
        self.assertLess(time.monotonic() - started, 10)
        # The peers that are still there are asked what they end with.
        self.assertEqual(["end" in node for node in report["nodes"]], [True, True, False])

    def test_a_creator_that_logs_a_welcome_too_few_stops_the_run(self):
        self.assertEqual(
            self.stop_of("forgets-a-welcome=saro"), "saro: its log holds 1 Welcomes for 2 joiners"
        )

    def test_steps_that_stopped_are_not_ok_though_the_report_is_as_expected(self):
        report = self.run_of("forgets-a-welcome=saro", steps=[{"step": "seat", "to": 3}])

        self.assertEqual(report["stopped"], "saro: its log holds 1 Welcomes for 2 joiners")
        self.assertEqual(report["check"], {"as_expected": True, "differences": []})
        self.assertFalse(report["ok"])

    def test_a_post_the_poster_refuses_stops_the_run(self):
        self.assertEqual(
            self.stop_of("refuses-post=pax"),
            "pax: the post was refused: The stand-in refuses to send anything.",
        )

    def test_a_member_that_does_not_read_the_post_is_named_and_the_run_is_not_ok(self):
        report = self.run_of("deaf=raya", budget=2)

        self.assertFalse(report["ok"])
        self.assertEqual(report["stopped"], "the post was not read within the budget by: raya")
        (post,) = report["posts"]
        self.assertEqual(post["unread"], ["raya"])
        self.assertEqual((post["readers"], post["read"]["count"]), (2, 1))
        self.assertEqual((report["steps_done"], report["at_tip"]), (1, 3))
        self.assertEqual(
            report["check"]["differences"],
            [
                f"post 1 of {member(report, 'pax')} on {member(report, 'raya')}: "
                "no record, expected read"
            ],
        )

    def test_the_lines_of_what_a_peer_could_not_process_are_counted_by_their_message(self):
        steps = [
            {"step": "seat", "to": 3},
            {"step": "post", "from": "saro"},
            {"step": "post", "from": "pax"},
        ]
        report = self.run_of("garbles=raya", budget=2, steps=steps)

        self.assertEqual(
            [node["inbound_errors"] for node in report["nodes"]], [{}, {standin.GARBLED: 1}, {}]
        )
        self.assertEqual(
            report["check"]["differences"],
            [
                f"post 1 of {member(report, 'saro')} on {member(report, 'raya')}: "
                "unreadable, expected read",
                f"post 1 of {member(report, 'pax')}: not in the report",
            ],
        )

    def test_each_message_of_what_a_peer_could_not_process_has_a_count_of_its_own(self):
        report = self.run_of(
            "grumbles=raya",
            "garbles=raya",
            "grumbles=saro",
            steps=[{"step": "seat", "to": 3}, {"step": "post"}, {"step": "post", "from": "pax"}],
            expect={"posts": {"raya": "unreadable"}},
        )

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual(
            [node["inbound_errors"] for node in report["nodes"]],
            [{standin.GRUMBLED: 2}, {standin.GRUMBLED: 2, standin.GARBLED: 2}, {}],
        )

    def test_a_post_unread_for_its_senders_account_is_counted_and_said_in_words(self):
        report = self.run_of("unchecked=raya", budget=2)

        self.assertEqual(report["stopped"], "the post was not read within the budget by: raya")
        self.assertEqual([node["unchecked_senders"] for node in report["nodes"]], [0, 1, 0])
        self.assertEqual(
            report["check"]["differences"],
            [
                f"post 1 of {member(report, 'pax')} on {member(report, 'raya')}: "
                "unreadable since the sender's account could not be checked, expected read"
            ],
        )

    def test_a_post_the_scenario_expects_a_member_not_to_read_is_not_waited_for_on_it(self):
        report = self.run_of("garbles=raya", expect={"posts": {"raya": "unreadable"}})

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual(report["nodes"][1]["inbound_errors"], {standin.GARBLED: 1})
        (post,) = report["posts"]
        self.assertEqual((post["readers"], post["unread"]), (1, []))

    def unchecked_of(self, forks: Path) -> str:
        """What a run says of its check when `forks` is the tool that makes it."""
        settings = Settings(
            chat_cli=stand_in(self.scratch),
            scenario=scenario.scenario(said()),
            out=self.out,
            forks=forks,
        )
        report = self.report_of(settings)
        self.assertEqual((report["ok"], report["stopped"], report["check"]), (False, None, None))
        self.assertIn(f"\nnot checked: {report['unchecked']}\n", self.written())
        return report["unchecked"]

    def test_a_check_that_broke_is_a_run_not_checked_and_no_difference_of_its_report(self):
        broke = forks_whose("check", "raise RuntimeError('the check broke')", self.scratch)

        self.assertEqual(
            self.unchecked_of(broke),
            "forks check ended with status 1: RuntimeError: the check broke",
        )

    def test_a_check_that_could_not_read_what_it_was_given_is_a_run_not_checked(self):
        refused = forks_whose(
            "check", "print('forks: no log in the run', file=sys.stderr)\nsys.exit(2)", self.scratch
        )

        self.assertEqual(
            self.unchecked_of(refused), "forks check ended with status 2: forks: no log in the run"
        )

    def test_a_check_a_signal_ended_is_a_run_not_checked(self):
        killed = forks_whose("check", "os.kill(os.getpid(), signal.SIGKILL)", self.scratch)

        self.assertEqual(self.unchecked_of(killed), "forks check ended with signal 9")

    def test_a_check_that_said_nothing_is_a_run_not_checked(self):
        silent = forks_whose("check", "sys.exit(1)", self.scratch)

        self.assertEqual(self.unchecked_of(silent), "forks check ended with status 1")

    def test_a_fork_tool_that_is_not_there_is_a_run_not_checked(self):
        unchecked = self.unchecked_of(self.scratch / "nowhere")

        self.assertRegex(unchecked, "^forks check ended with status 2: .*nowhere")

    def test_a_peer_that_ends_with_its_input_ended_as_it_was_asked(self):
        report = self.run_of()

        for node in report["nodes"]:
            self.assertEqual(
                (node["exit_status"], node["exit_signal"], node["ended"]), (0, None, "asked"), node
            )

    def test_a_peer_that_ends_with_another_status_once_asked_died_in_its_stop(self):
        report = self.run_of("dies-at-the-end=pax")

        self.assertTrue(report["ok"], report["stopped"])
        self.assertEqual(
            [(node["exit_status"], node["ended"]) for node in report["nodes"]],
            [(0, "asked"), (0, "asked"), (standin.STATUS, "in its stop")],
        )

    def test_a_peer_that_ended_before_it_was_asked_ended_early(self):
        report = self.run_of("dies-at-roster=pax")

        self.assertEqual(
            [(node["exit_status"], node["ended"]) for node in report["nodes"]],
            [(0, "asked"), (0, "asked"), (standin.STATUS, "early")],
        )
        self.assertEqual(
            report["check"]["differences"][0],
            f"{member(report, 'pax')}: never joined, expected tip",
        )

    def test_a_peer_that_does_not_end_with_its_input_is_killed(self):
        with mock.patch("sim.run.GRACE", 0.2), noting("reap") as reaped:
            report = self.run_of("lingers=pax")

        self.assertTrue(report["ok"], report["stopped"])
        # The peer would run on for `LINGER` once its input ended.
        self.assertTrue((self.out / "data" / "pax.eof").is_file())
        self.assertLess(reaped[-1][1] - reaped[0][0], standin.LINGER)
        pax = report["nodes"][2]
        self.assertEqual(
            (pax["exit_status"], pax["exit_signal"], pax["ended"]),
            (None, signal.SIGKILL, "killed"),
        )

    def told(self, name: str) -> str:
        """The app id the peer `name` told of itself."""
        (app_id,) = [
            line["app_id"]
            for line in self.lines(name)
            if line["type"] in ("result", "joined") and "app_id" in line
        ]
        return app_id

    def expected(self) -> dict:
        """What the run had `forks check` hold the report against."""
        return json.loads((self.out / "expect.json").read_text(encoding="utf-8"))

    def test_a_scenario_without_a_network_model_gives_no_flag_and_no_input_of_one(self):
        with inputs() as written:
            report = self.run_of()

        self.assertTrue(report["ok"], report["stopped"])
        for name in ("saro", "raya", "pax"):
            self.assertFalse(
                [word for word in self.arguments(name) if word.startswith("--network")]
            )
        self.assertFalse([text for _, text, _ in written if text.startswith("/network")])
        self.assertEqual(
            (report["network"], report["withheld"], report["held_to"]), (None, None, None)
        )
        for node in report["nodes"]:
            self.assertNotIn("network", node)
            self.assertNotIn("network_dropped", node)

    def test_each_peer_is_given_the_seed_the_rates_and_its_own_delay(self):
        network = {"seed": 7, "loss": 0.25, "welcome_loss": 1, "delay_ms": {"raya": 40}}
        report = self.run_of(network=network)

        self.assertTrue(report["ok"], report["stopped"])
        self.assertEqual(report["network"], network)
        for name, delay in (("saro", "0"), ("raya", "40"), ("pax", "0")):
            arguments = self.arguments(name)
            self.assertEqual(given(arguments, "--network-seed"), ["7"])
            self.assertIn("--network-loss=0.25", arguments)
            self.assertIn("--network-welcome-loss=1", arguments)
            self.assertEqual(given(arguments, "--network-delay-ms"), [delay])

    def test_a_network_model_that_says_its_seed_alone_is_given_with_no_loss_and_no_delay(self):
        report = self.run_of(network={"seed": 0})

        self.assertEqual(
            report["network"], {"seed": 0, "loss": 0, "welcome_loss": 0, "delay_ms": 0}
        )
        for name in ("saro", "raya", "pax"):
            arguments = self.arguments(name)
            self.assertEqual(
                [given(arguments, f"--network-{flag}") for flag in ("seed", "delay-ms")],
                [["0"], ["0"]],
            )
            self.assertIn("--network-loss=0", arguments)
            self.assertIn("--network-welcome-loss=0", arguments)

    def test_one_delay_is_given_to_every_peer(self):
        self.run_of(network={"seed": 7, "delay_ms": 25})

        for name in ("saro", "raya", "pax"):
            self.assertEqual(given(self.arguments(name), "--network-delay-ms"), ["25"])

    def test_a_rate_below_zero_in_its_sign_alone_is_given_in_one_word_with_its_flag(self):
        # A word of its own that starts with a minus sign is a flag to chat-cli.
        report = self.run_of(network={"seed": 7, "loss": -0.0, "welcome_loss": -0.0})

        self.assertTrue(report["ok"], report["stopped"])
        for name in ("saro", "raya", "pax"):
            arguments = self.arguments(name)
            self.assertIn("--network-loss=-0.0", arguments)
            self.assertIn("--network-welcome-loss=-0.0", arguments)
            self.assertNotIn("-0.0", arguments)

    def test_a_peer_is_asked_again_until_its_network_model_holds_no_frame(self):
        # Pax cannot read the post, so nobody waits for it there, and its model holds the
        # post for half the time between two askings: the first finds it held, the second
        # passed on.
        with inputs() as written:
            report = self.run_of(
                "garbles=pax",
                steps=[{"step": "seat", "to": 3}, {"step": "post", "from": "raya"}],
                network={"seed": 7, "delay_ms": {"pax": 250}},
                expect={"posts": {"pax": "unreadable"}},
            )

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual([node["network"]["frames"]["held"] for node in report["nodes"]], [0] * 3)
        asked = [name for name, text, _ in written if text == "/network"]
        self.assertEqual((asked.count("saro"), asked.count("raya")), (1, 1))
        self.assertGreater(asked.count("pax"), 1)

    def test_a_frame_held_for_longer_than_the_budget_is_not_waited_for(self):
        # Ten seconds, of which the run waits two.
        report = self.run_of(
            steps=[{"step": "seat", "to": 3}, {"step": "post", "from": "raya"}],
            network={"seed": 7, "delay_ms": {"pax": 10_000}},
            expect={"posts": {"pax": "no record"}},
            budget=2,
        )

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual([node["network"]["frames"]["held"] for node in report["nodes"]], [0, 0, 1])

    def test_a_run_keeps_each_peers_app_id_and_what_its_network_model_last_answered(self):
        with inputs() as written:
            report = self.run_of(members=4, steps=[{"step": "seat", "to": 3}], network={"seed": 7})

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        seated, unseated = report["nodes"][:3], report["nodes"][3]
        for node in seated:
            self.assertEqual(node["app_id"], self.told(node["name"]))
            self.assertEqual(node["network"]["own"], [node["app_id"]])
        # The member no step seats told no app id, and has a network model like any other.
        self.assertIsNone(unseated["app_id"])
        self.assertEqual(len({node["app_id"] for node in seated}), 3)
        for node in report["nodes"]:
            self.assertEqual((node["network"]["seed"], node["network"]["cut"]), (7, []))
            self.assertEqual(node["network_dropped"], {})
        # Each is asked once, with its last words.
        last_words = [(name, text) for name, text, _ in written][-8:]
        self.assertEqual(
            last_words,
            [
                (name, text)
                for name in ("saro", "raya", "pax", "mira")
                for text in ("/network", "/node")
            ],
        )
        self.assertEqual([text for _, text, _ in written].count("/network"), 4)

    def test_a_cut_gives_each_side_the_app_ids_of_the_other_and_a_heal_clears_them(self):
        steps = [
            {"step": "seat", "to": 3},
            {"step": "cut", "between": [["saro", "raya"], ["pax"]]},
            {"step": "post", "from": "saro"},
            {"step": "heal"},
            {"step": "post", "from": "pax"},
        ]
        with inputs() as written:
            report = self.run_of(
                steps=steps, network={"seed": 7}, expect={"posts": {"pax": "no record"}}
            )

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        saro, raya, pax = (self.told(name) for name in ("saro", "raya", "pax"))
        self.assertEqual(
            commands(written),
            [
                ("saro", "/new"),
                ("saro", "/add"),
                ("saro", f"/network cut {pax}"),
                ("raya", f"/network cut {pax}"),
                ("pax", f"/network cut {saro} {raya}"),
                ("saro", "/send"),
                ("saro", "/network heal"),
                ("raya", "/network heal"),
                ("pax", "/network heal"),
                ("pax", "/send"),
            ],
        )
        # The post of saro did not cross the cut, and that of pax found it healed.
        self.assertEqual(
            [node["network_dropped"] for node in report["nodes"]], [{}, {}, {"cut": 1}]
        )
        self.assertEqual([node["network"]["cut"] for node in report["nodes"]], [[], [], []])
        self.assertEqual([post["readers"] for post in report["posts"]], [1, 2])

    def test_a_cut_left_in_force_is_in_what_each_network_model_last_answered(self):
        steps = [
            {"step": "seat", "to": 3},
            {"step": "cut", "between": [["raya"], ["pax", "saro"]]},
        ]
        report = self.run_of(steps=steps, network={"seed": 7})

        saro, raya, pax = (self.told(name) for name in ("saro", "raya", "pax"))
        self.assertEqual(
            [node["network"]["cut"] for node in report["nodes"]],
            [[raya], sorted([pax, saro]), [raya]],
        )
        self.assertEqual((report["steps_done"], report["stopped"]), (2, None))

    def test_a_cut_waits_for_a_member_that_has_yet_to_tell_its_app_id(self):
        steps = [{"step": "seat", "to": 3}, {"step": "cut", "between": [["saro"], ["pax"]]}]
        # Pax takes `ROSTER` to hold a roster, and a member expected behind is not waited on
        # while it is seated.
        with inputs() as written:
            report = self.run_of(
                "slow=pax",
                steps=steps,
                network={"seed": 7},
                expect={"positions": {"pax": "behind"}},
            )

        self.assertEqual((report["steps_done"], report["stopped"]), (2, None))
        at = {text.split(" ")[0]: at for _, text, at in written if text != "/network"}
        self.assertGreaterEqual(at["/network"] - at["/add"], standin.ROSTER)
        self.assertIn(("saro", f"/network cut {self.told('pax')}"), commands(written))

    def test_a_cut_of_a_member_that_never_tells_its_app_id_stops_the_run(self):
        steps = [{"step": "seat", "to": 3}, {"step": "cut", "between": [["saro"], ["pax"]]}]
        with inputs() as written:
            report = self.run_of(
                "tells-no-app-id=pax", steps=steps, network={"seed": 7}, budget=2
            )

        self.assertEqual(report["stopped"], "pax: no app id within the budget")
        self.assertEqual((report["ok"], report["steps_done"]), (False, 1))
        self.assertIsNone(report["nodes"][2]["app_id"])
        # Nobody is cut off from a side whose app ids are not all known.
        self.assertFalse([text for _, text, _ in written if text.startswith("/network cut")])

    def test_a_candidate_is_withheld_once_the_step_before_is_done_and_before_the_next_add(self):
        with inputs() as written:
            report = self.run_of("slow=raya", **withholding({"step": "post", "from": "mira"}))

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual(
            commands(written),
            [
                ("saro", "/new"),
                ("saro", "/add"),
                ("pax", "/network withhold candidate"),
                ("saro", "/add"),
                ("mira", "/send"),
            ],
        )
        # Raya takes `ROSTER` to hold the roster of the batch before.
        added, armed = [at for _, text, at in written if text[:5] in ("/add ", "/netw")][:2]
        self.assertGreaterEqual(armed - added, standin.ROSTER)

    def test_a_run_whose_group_did_not_apply_the_withheld_candidate_is_held_to_expect(self):
        post = {"step": "post", "from": "mira"}
        report = self.run_of(**withholding(post, expect={"applied": APPLIED}))

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        self.assertEqual(report["held_to"], "not applied")
        self.assertEqual(
            report["withheld"],
            {
                "from": "pax",
                "seat": 0,
                "app_id": self.told("saro"),
                "minter": "saro",
                "applied": False,
            },
        )
        self.assertEqual((report["verdict"], self.expected()["verdict"]), ("whole", "whole"))
        self.assertEqual(
            [node["network_dropped"] for node in report["nodes"]], [{}, {}, {"withheld": 1}, {}]
        )
        self.assertEqual(report["posts"][0]["readers"], 3)

    def test_a_run_whose_group_applied_the_withheld_candidate_is_held_to_what_applied_says(self):
        post = {"step": "post", "from": "mira"}
        report = self.run_of(
            "lone=pax", "says-applied=pax", **withholding(post, expect={"applied": APPLIED})
        )

        # The split of the member alone does not stop the steps.
        self.assertEqual((report["stopped"], report["steps_done"]), (None, 4))
        self.assertTrue(report["ok"], report["check"])
        self.assertEqual(report["held_to"], "applied")
        self.assertEqual(
            (report["withheld"]["from"], report["withheld"]["applied"]), ("pax", True)
        )
        self.assertEqual(
            (report["verdict"], self.expected()["verdict"]),
            ("split at epoch 2", "split at epoch 2"),
        )
        self.assertEqual(report["at_tip"], 4)

    def test_a_post_is_not_waited_for_on_the_member_a_withheld_candidate_left_alone(self):
        applied = dict(APPLIED, posts={"pax": "no record"})
        report = self.run_of(
            "lone=pax",
            "says-applied=pax",
            "deaf=pax",
            **withholding({"step": "post", "from": "mira"}, expect={"applied": applied}, budget=3),
        )

        self.assertTrue(report["ok"], (report["stopped"], report["check"]))
        (post,) = report["posts"]
        self.assertEqual((post["readers"], post["unread"]), (2, []))

    def test_a_split_the_member_does_not_say_was_applied_is_one_difference_of_the_verdict(self):
        report = self.run_of("lone=pax", **withholding(expect={"applied": APPLIED}))

        self.assertEqual((report["held_to"], report["withheld"]["applied"]), ("not applied", False))
        self.assertEqual((report["ok"], report["stopped"]), (False, None))
        self.assertEqual(
            report["check"]["differences"], ["verdict: split at epoch 2, expected whole"]
        )

    def test_a_whole_group_the_member_says_applied_it_is_one_difference_of_the_verdict(self):
        report = self.run_of("says-applied=pax", **withholding(expect={"applied": APPLIED}))

        self.assertEqual((report["held_to"], report["withheld"]["applied"]), ("applied", True))
        self.assertEqual((report["ok"], report["stopped"]), (False, None))
        self.assertEqual(
            report["check"]["differences"], ["verdict: whole, expected split at epoch 2"]
        )

    def test_a_member_that_withholds_no_candidate_stops_the_run(self):
        post = {"step": "post", "from": "mira"}
        report = self.run_of("withholds-nothing=pax", **withholding(post, budget=2))

        self.assertEqual(
            report["stopped"], "pax: no line of a withheld candidate within the budget"
        )
        self.assertEqual((report["ok"], report["steps_done"]), (False, 2))
        self.assertEqual((report["withheld"], report["held_to"]), (None, None))
        self.assertEqual(self.expected()["verdict"], "whole")
        self.assertTrue(report["nodes"][2]["network"]["armed"])

    def test_a_candidate_withheld_before_the_add_was_written_stops_the_run(self):
        post = {"step": "post", "from": "mira"}
        report = self.run_of("withholds-early=pax", **withholding(post))

        self.assertEqual(
            report["stopped"], "pax: a candidate was withheld before the /add was written"
        )
        self.assertEqual((report["ok"], report["steps_done"]), (False, 2))
        self.assertEqual(
            (report["withheld"]["from"], report["withheld"]["minter"]), ("pax", "saro")
        )

    def test_a_split_that_leaves_another_member_alone_stops_the_steps(self):
        self.assertEqual(
            self.stop_of("split=mira", **withholding({"step": "post", "from": "mira"})),
            "the group split: 4 of its 4 members hold epoch 2, in 2 states",
        )

    def test_a_split_in_three_states_stops_the_steps_though_the_member_is_alone(self):
        self.assertEqual(
            self.stop_of(
                "lone=pax", "split=mira", **withholding({"step": "post", "from": "mira"})
            ),
            "the group split: 4 of its 4 members hold epoch 2, in 3 states",
        )

    def test_a_member_that_holds_no_state_of_the_epoch_is_no_side_of_a_split(self):
        # Mira never holds the roster, and the others but pax hold it in one state.
        self.assertEqual(
            self.stop_of(
                "lone=pax", "mute=mira", **withholding({"step": "post", "from": "saro"}, budget=2)
            ),
            "mira: no line of a new roster within the budget",
        )

    def test_a_candidate_stamped_with_no_app_id_has_no_minter(self):
        # Saro withholds a candidate it gives no app id for, and kes, whom no step seats,
        # told none either.
        steps = [
            {"step": "seat", "to": 3},
            {"step": "withhold", "from": "saro"},
            {"step": "seat", "to": 4},
        ]
        report = self.run_of("withholds-early=saro", members=5, network={"seed": 7}, steps=steps)

        self.assertIsNone(report["nodes"][4]["app_id"])
        self.assertEqual(
            report["withheld"],
            {"from": "saro", "seat": 0, "app_id": None, "minter": None, "applied": False},
        )


class CommandLine(Scratch):
    """The command, run in this process."""

    def sim(self, *arguments: str, faults: Tuple[str, ...] = ()) -> Tuple[int, str]:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            chat_cli = stand_in(self.scratch, *faults)
            status = main([*arguments, "--chat-cli", str(chat_cli), "--out", str(self.out)])
        return status, printed.getvalue()

    def refused(self, *arguments: str) -> str:
        """What the command says of a command line it refuses."""
        complained = io.StringIO()
        with self.assertRaises(SystemExit) as refused:
            with contextlib.redirect_stderr(complained):
                self.sim(*arguments)
        self.assertEqual(refused.exception.code, 2, arguments)
        return complained.getvalue().splitlines()[-1]

    def report(self) -> dict:
        return json.loads((self.out / "run.json").read_text(encoding="utf-8"))

    def test_a_run_that_did_every_step_and_is_as_expected_is_status_0(self):
        status, printed = self.sim("run", str(self.scenario()))

        self.assertEqual(status, 0)
        self.assertIn("\nwhole: 3 of 3 members at the tip\nthe report is as expected\n", printed)
        self.assertEqual(printed, (self.out / "run.txt").read_text(encoding="utf-8"))
        self.assertTrue(self.report()["ok"])

    def test_a_run_that_stopped_is_status_1_and_still_writes_what_it_measured(self):
        status, printed = self.sim("run", str(self.scenario(budget=2)), faults=("deaf=raya",))

        self.assertEqual(status, 1)
        self.assertIn(
            "\nstopped after 1 of 2 steps: the post was not read within the budget by: raya\n",
            printed,
        )
        self.assertIn("\nnot as expected: post 1 of pax (", printed)
        self.assertEqual(printed, (self.out / "run.txt").read_text(encoding="utf-8"))
        report = self.report()
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["batches"]), 1)

    def test_a_run_whose_report_is_not_as_expected_is_status_1(self):
        expect = {"verdict": "whole, members behind"}
        status, printed = self.sim("run", str(self.scenario(expect=expect)))

        self.assertEqual(status, 1)
        self.assertNotIn("stopped", printed)
        self.assertIn(
            "\nnot as expected: verdict: whole, expected whole, members behind\n", printed
        )
        self.assertEqual((self.report()["stopped"], self.report()["steps_done"]), (None, 2))

    def test_a_scenario_it_refuses_is_status_2_and_named(self):
        scenario_file = self.scenario(peers=3)

        self.assertEqual(
            self.refused("run", str(scenario_file)),
            f"sim: error: {scenario_file}: `peers` is not a field of the scenario",
        )
        self.assertEqual(
            self.refused("sweep", str(scenario_file), "--runs", "2"),
            f"sim: error: {scenario_file}: `peers` is not a field of the scenario",
        )
        self.assertFalse(self.out.exists())

    def test_a_command_line_it_refuses_is_status_2_and_starts_nothing(self):
        scenario_file = str(self.scenario())
        self.assertEqual(
            self.refused("sweep", scenario_file, "--runs", "0"),
            "sim: error: --runs: a sweep is 1 run or more",
        )
        self.assertIn("--runs", self.refused("sweep", scenario_file))
        self.assertIn("--runs", self.refused("run", scenario_file, "--runs", "2"))
        self.assertIn("--peers", self.refused("run", scenario_file, "--peers", "3"))
        self.assertIn("COMMAND", self.refused())
        self.assertIn("nowhere.json", self.refused("run", str(self.scratch / "nowhere.json")))
        self.assertFalse(self.out.exists())

        self.out.mkdir()
        self.assertEqual(
            self.refused("run", scenario_file), f"sim: error: --out: {self.out} exists"
        )
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_sweep_whose_last_run_would_have_a_seed_above_the_most_is_refused(self):
        seeded = str(self.scenario(seed=2**64 - 2))
        self.assertEqual(
            self.refused("sweep", seeded, "--runs", "3"),
            f"sim: error: --runs: run 3 would have the seed {2**64}, and {2**64 - 1} is the most",
        )
        scenario_file = str(self.scenario(network={"seed": 2**64 - 2}))

        self.assertEqual(
            self.refused("sweep", scenario_file, "--runs", "3"),
            f"sim: error: --runs: run 3 would have the network seed {2**64}, "
            f"and {2**64 - 1} is the most",
        )
        self.assertFalse(self.out.exists())
        status, printed = self.sim("sweep", scenario_file, "--runs", "2")
        self.assertEqual(status, 0, printed)
        self.assertIn(f"as expected; network seed {2**64 - 1}\n", printed)

    def test_a_chat_cli_that_is_no_file_is_refused(self):
        complained = io.StringIO()
        with self.assertRaises(SystemExit) as refused, contextlib.redirect_stderr(complained):
            main(["run", str(self.scenario()), "--chat-cli", "nowhere", "--out", str(self.out)])

        self.assertEqual(refused.exception.code, 2)
        self.assertIn("--chat-cli: no such file: nowhere", complained.getvalue())

    def test_a_chat_cli_that_cannot_be_run_is_refused_and_nothing_is_started(self):
        chat_cli = self.scratch / "not-to-run"
        chat_cli.write_text("#!/bin/sh\n", encoding="utf-8")
        chat_cli.chmod(0o644)
        complained = io.StringIO()
        for command in (["run"], ["sweep", "--runs", "2"]):
            given = [str(self.scenario()), "--chat-cli", str(chat_cli), "--out", str(self.out)]
            with self.assertRaises(SystemExit) as refused, contextlib.redirect_stderr(complained):
                main([*command, *given])

            self.assertEqual(refused.exception.code, 2)
            self.assertEqual(
                complained.getvalue().splitlines()[-1],
                f"sim: error: --chat-cli: not a file that can be run: {chat_cli}",
            )
            self.assertFalse(self.out.exists())

    def test_an_out_that_cannot_be_made_is_refused_and_nothing_is_started(self):
        in_the_way = self.scratch / "a-file"
        in_the_way.write_text("", encoding="utf-8")
        self.out = in_the_way / "run"

        for command in (["run"], ["sweep", "--runs", "2"]):
            refusal = self.refused(*command, str(self.scenario()))

            self.assertRegex(refusal, "^sim: error: --out: .*Not a directory")
            self.assertIn(str(self.out), refusal)
            # A process the command started and did not reap would be a child of this one.
            with self.assertRaises(ChildProcessError):
                os.waitpid(-1, os.WNOHANG)

    def test_a_run_whose_check_was_not_made_is_status_1_and_says_how_the_check_ended(self):
        broke = forks_whose("check", "raise RuntimeError('the check broke')", self.scratch)
        with mock.patch("sim.cli.Settings", functools.partial(Settings, forks=broke)):
            status, printed = self.sim("run", str(self.scenario()))

        self.assertEqual(status, 1)
        self.assertIn(
            "\nwhole: 3 of 3 members at the tip\n"
            "not checked: forks check ended with status 1: RuntimeError: the check broke\n",
            printed,
        )

    def test_the_signal_handlers_of_the_caller_are_put_back(self):
        before = [signal.getsignal(signum) for signum in SIGNALS]
        self.sim("run", str(self.scenario()))

        self.assertEqual([signal.getsignal(signum) for signum in SIGNALS], before)


class Driven(Scratch):
    """The command as a process of its own."""

    def start(
        self, *arguments: str, chat_cli: Path, ignoring: Tuple[int, ...] = (), tool: Path = TOOL
    ) -> subprocess.Popen:
        def dispositions() -> None:
            for signum in SIGNALS:
                signal.signal(signum, signal.SIG_IGN if signum in ignoring else signal.SIG_DFL)

        driver = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(tool),
                *arguments,
                "--chat-cli",
                str(chat_cli),
                "--out",
                str(self.out),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=dispositions,
            # A session of its own, so that a test that fails can end whatever the tool left.
            start_new_session=True,
        )
        self.addCleanup(self.end, driver)
        return driver

    def end(self, driver: subprocess.Popen) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(driver.pid, signal.SIGKILL)
        driver.communicate()

    def wait_for(self, path: Path, text: str = "") -> None:
        until = time.monotonic() + 60
        while not (path.is_file() and text in path.read_text(encoding="utf-8")):
            if time.monotonic() > until:
                self.fail(f"{path} never held {text!r}")
            time.sleep(0.01)

    def left(self, run: Path) -> List[str]:
        """The processes of the run in a directory that are still there."""
        names = []
        for kept in sorted((run / "data").glob("*.json")):
            with contextlib.suppress(ProcessLookupError):
                os.kill(json.loads(kept.read_text(encoding="utf-8"))["pid"], 0)
                names.append(kept.stem)
        url = (run / "registry.out").read_text(encoding="utf-8").strip()
        with contextlib.suppress(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", int(url.rsplit(":", 1)[1])), timeout=30).close()
            names.append("the registry")
        return names


class Signals(Driven):
    """A run sent a signal while its batch waits on a member."""

    def run_of(self, *faults: str, ignoring: Tuple[int, ...] = ()) -> subprocess.Popen:
        return self.start(
            "run",
            str(self.scenario(budget=120)),
            chat_cli=stand_in(self.scratch, *faults),
            ignoring=ignoring,
        )

    def interrupted(self, driver: subprocess.Popen, signum: signal.Signals) -> None:
        printed, errors = driver.communicate(timeout=60)

        self.assertEqual(driver.returncode, 128 + signum)
        self.assertEqual(errors, "")
        self.assertEqual(self.left(self.out), [])
        report = json.loads((self.out / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(report["stopped"], f"interrupted by {signum.name}")
        self.assertFalse(report["ok"])
        self.assertIsNone(report["check"])
        for node in report["nodes"]:
            self.assertEqual(node["wired"], {"peers": 2, "mesh_peers": 2}, node["name"])
        self.assertEqual(len(report["nodes"]), 3)
        self.assertIn(
            f"\nstopped after 0 of 2 steps: interrupted by {signum.name}\n"
            "not checked: the run was interrupted\n",
            printed,
        )
        self.assertEqual(printed, (self.out / "run.txt").read_text(encoding="utf-8"))

    def interrupt(self, signum: signal.Signals) -> None:
        driver = self.run_of("mute=raya")
        self.wait_for(self.out / "saro.log", "Publish WelcomeMessage")
        driver.send_signal(signum)

        self.interrupted(driver, signum)

    def test_an_interrupt_ends_every_process_and_writes_the_report(self):
        self.interrupt(signal.SIGINT)

    def test_a_termination_ends_every_process_and_writes_the_report(self):
        self.interrupt(signal.SIGTERM)

    def test_a_hangup_ends_every_process_and_writes_the_report(self):
        self.interrupt(signal.SIGHUP)

    def test_a_second_signal_while_the_processes_are_ended_changes_nothing(self):
        driver = self.run_of("mute=raya", "lingers=pax")
        self.wait_for(self.out / "saro.log", "Publish WelcomeMessage")
        driver.send_signal(signal.SIGTERM)
        self.wait_for(self.out / "data" / "pax.eof")
        driver.send_signal(signal.SIGINT)

        self.interrupted(driver, signal.SIGTERM)

    def test_a_signal_while_the_processes_of_a_whole_run_are_ended_changes_nothing(self):
        driver = self.run_of("lingers=pax")
        self.wait_for(self.out / "data" / "pax.eof")
        driver.send_signal(signal.SIGTERM)
        printed, _ = driver.communicate(timeout=60)

        self.assertEqual(driver.returncode, 0)
        self.assertIn("\nthe report is as expected\n", printed)
        self.assertEqual(self.left(self.out), [])

    def test_an_interrupt_from_the_terminal_does_not_reach_the_check_of_a_run_past_its_stop(self):
        checking = self.scratch / "checking"
        waits = forks_whose(
            "check", f"open({str(checking)!r}, 'w').close()\ntime.sleep(2)", self.scratch
        )
        # The command, with that `forks` for its own.
        tool = self.scratch / "sim.py"
        tool.write_text(
            "import functools, sys\n"
            "from pathlib import Path\n"
            "from unittest import mock\n"
            f"sys.path.insert(0, {str(TOOL)!r})\n"
            "from sim import cli, run\n"
            f"settings = functools.partial(run.Settings, forks=Path({str(waits)!r}))\n"
            "with mock.patch('sim.cli.Settings', settings):\n"
            "    sys.exit(cli.main())\n",
            encoding="utf-8",
        )
        driver = self.start(
            "run", str(self.scenario()), chat_cli=stand_in(self.scratch), tool=tool
        )
        self.wait_for(checking)
        # A terminal sends its interrupt to every process of the group in its foreground.
        os.killpg(driver.pid, signal.SIGINT)
        printed, errors = driver.communicate(timeout=60)

        self.assertEqual((driver.returncode, errors), (0, ""))
        self.assertIn("\nthe report is as expected\n", printed)

    def test_a_signal_the_command_was_started_ignoring_stays_ignored(self):
        driver = self.run_of("slow", ignoring=(signal.SIGINT,))
        self.wait_for(self.out / "saro.log", "Publish WelcomeMessage")
        driver.send_signal(signal.SIGINT)
        printed, _ = driver.communicate(timeout=60)

        self.assertEqual(driver.returncode, 0)
        self.assertIn("\nthe report is as expected\n", printed)
        self.assertEqual(self.left(self.out), [])


if __name__ == "__main__":
    unittest.main()
