import contextlib
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
from typing import List, Tuple
from unittest import mock

from sim.cli import SIGNALS, main
from sim.peers import Watch
from sim.run import CLUSTER_ID, Settings, run

from tests import stand_in, standin

TOOL = Path(__file__).resolve().parents[1]


def given(arguments: List[str], flag: str) -> List[str]:
    """Each value `flag` has in the arguments a peer was started with."""
    return [value for before, value in zip(arguments, arguments[1:]) if before == flag]


class Scratch(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.out = self.scratch / "run"

    def arguments(self, name: str) -> List[str]:
        """What the stand-in for the peer `name` was started with."""
        kept = (self.out / "data" / f"{name}.json").read_text(encoding="utf-8")
        return json.loads(kept)["arguments"]

    def lines(self, name: str) -> List[dict]:
        kept = (self.out / f"{name}.out").read_text(encoding="utf-8")
        return [json.loads(line) for line in kept.splitlines()]


class Running(Scratch):
    """`run` against stand-ins for chat-cli: what it reports, and that it leaves no process."""

    def run_of(self, *faults: str, peers: int = 3, **settings) -> dict:
        settings.setdefault("budget", 30.0)
        report = run(
            Settings(
                chat_cli=stand_in(self.scratch, *faults), peers=peers, out=self.out, **settings
            ),
            Watch(),
        )
        # Each process a run starts is a child of this one, and one that was reaped is none.
        with self.assertRaises(ChildProcessError, msg="the run left a process"):
            os.waitpid(-1, os.WNOHANG)
        return report

    def stop_of(self, *faults: str, **settings) -> str:
        report = self.run_of(*faults, **settings)
        self.assertFalse(report["ok"])
        return report["stopped"]

    def test_three_peers_form_a_group_and_read_one_post(self):
        report = self.run_of()

        self.assertTrue(report["ok"])
        self.assertIsNone(report["stopped"])
        self.assertEqual(
            [(node["name"], node["dials"]) for node in report["nodes"]],
            [("saro", []), ("raya", ["saro"]), ("pax", ["saro", "raya"])],
        )
        for node in report["nodes"]:
            self.assertEqual(node["wired"], {"peers": 2, "mesh_peers": 2}, node["name"])
            self.assertEqual(
                node["end"],
                {"peers": 2, "mesh_peers": 2, "publish_refused": 0, "inbound_dropped": 0},
                node["name"],
            )
        (batch,) = report["batches"]
        self.assertEqual((batch["joiners"], batch["members"]), (2, 3))
        self.assertEqual(batch["welcome_bytes"], [3 * standin.WELCOME] * 2)
        post = report["post"]
        self.assertEqual((post["poster"], post["readers"], post["unread"]), ("pax", 2, []))
        self.assertEqual((post["read"]["count"], post["lookups"]), (2, 2))

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
        self.assertEqual((report["timers"], report["max_message_kib"]), ("default", 64))

    def test_the_run_keeps_the_lines_and_the_standard_error_of_each_peer(self):
        self.run_of()

        for name in ("saro", "raya", "pax"):
            lines = self.lines(name)
            self.assertEqual((lines[0]["type"], lines[0]["name"]), ("ready", name))
            self.assertIn("result", [line["type"] for line in lines])
            self.assertTrue((self.out / f"{name}.stderr").is_file())
        self.assertIn({"type": "joined", "convo": mock.ANY, "kind": "group"}, self.lines("pax"))

    def test_seven_peers_are_a_ring_with_chords_seated_a_batch_at_a_time(self):
        report = self.run_of(peers=7, batch=3)

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
        self.assertEqual((report["post"]["poster"], report["post"]["read"]["count"]), ("peer7", 6))

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
        post = report["post"]
        read = standin.DELIVERY + standin.LOOKUP
        self.assertTrue(read <= post["read"]["median"] <= post["read"]["worst"] < read + 5, post)
        self.assertTrue(0 <= post["delivered"]["median"] - standin.DELIVERY < 5, post)
        self.assertTrue(standin.LOOKUP <= post["lookup_mean"] < standin.LOOKUP + 5, post)

    def test_a_peer_that_writes_no_ready_line_stops_the_run_at_its_start(self):
        report = self.run_of("silent=raya", budget=2.0)

        self.assertEqual(report["stopped"], "raya: no ready line within the budget")
        self.assertFalse(report["ok"])
        self.assertEqual([node["name"] for node in report["nodes"]], ["saro", "raya"])
        self.assertIn("end", report["nodes"][0])
        self.assertNotIn("ready_s", report["nodes"][1])

    def test_a_peer_that_ends_at_its_start_is_named_with_its_status(self):
        self.assertEqual(
            self.stop_of("dies-at-start=raya"),
            f"raya ended with status {standin.STATUS}: see {self.out / 'raya.stderr'}",
        )

    def test_a_line_that_is_not_json_stops_the_run_with_a_report(self):
        self.assertEqual(
            self.stop_of("stray-line=raya"), "raya: a line that is no JSON object: starting"
        )

    def test_a_registry_that_ends_at_its_start_stops_the_run_before_any_peer(self):
        registry = self.scratch / "registry.py"
        registry.write_text("import sys\nsys.exit(4)\n", encoding="utf-8")
        report = self.run_of(registry=registry)

        self.assertEqual(
            report["stopped"],
            f"the registry ended with status 4: see {self.out / 'registry.stderr'}",
        )
        self.assertEqual(report["nodes"], [])

    def test_nodes_that_dial_nobody_are_not_wired_and_are_named(self):
        self.assertEqual(
            self.stop_of("unlinked", budget=2.0), "not wired within the budget: saro, raya, pax"
        )

    def test_a_link_short_of_six_is_not_wired(self):
        self.assertEqual(
            self.stop_of("drops-a-dial=peer7", peers=7, budget=2.0),
            "not wired within the budget: saro, peer7",
        )

    def test_a_mesh_of_three_is_not_wired(self):
        self.assertEqual(
            self.stop_of("mesh-of-three", peers=7, budget=2.0),
            "not wired within the budget: saro, raya, pax, mira, kes, tomo, peer7",
        )

    def test_a_mesh_of_four_is_wired(self):
        report = self.run_of("mesh-of-four", peers=7)

        self.assertTrue(report["ok"], report["stopped"])
        for node in report["nodes"]:
            self.assertEqual(node["wired"], {"peers": 6, "mesh_peers": 4}, node["name"])

    def test_an_add_the_creator_refuses_stops_the_run(self):
        self.assertEqual(
            self.stop_of("refuses-add=saro"),
            "saro: /add was refused: The stand-in refuses to add anybody.",
        )

    def test_a_member_that_never_holds_the_roster_runs_the_batch_out_of_budget(self):
        report = self.run_of("mute=raya", budget=2.0)

        self.assertEqual(report["stopped"], "raya: no line of a new roster within the budget")
        self.assertEqual(report["batches"], [])
        self.assertIsNone(report["post"])

    def test_members_that_hold_one_roster_in_two_states_are_a_split(self):
        self.assertEqual(
            self.stop_of("split=pax"),
            "the group split: 3 of its 3 members hold epoch 1, in 2 states",
        )

    def test_a_split_is_told_also_when_another_member_is_behind(self):
        self.assertEqual(
            self.stop_of("split=pax", "mute=raya", budget=2.0),
            "the group split: 2 of its 3 members hold epoch 1, in 2 states",
        )

    def test_a_member_that_holds_the_epoch_on_a_roster_of_its_own_is_a_split(self):
        self.assertEqual(
            self.stop_of("short-roster=raya", budget=2.0),
            "the group split: 3 of its 3 members hold epoch 1, in 2 states",
        )

    def test_a_member_that_ends_while_another_is_waited_on_is_named_at_once(self):
        started = time.monotonic()
        report = self.run_of("mute=raya", "dies-at-roster=pax", budget=20.0)

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

    def test_a_post_the_poster_refuses_stops_the_run(self):
        self.assertEqual(
            self.stop_of("refuses-post=pax"),
            "pax: the post was refused: The stand-in refuses to send anything.",
        )

    def test_a_member_that_does_not_read_the_post_is_named_and_the_run_is_not_ok(self):
        report = self.run_of("deaf=raya", budget=2.0)

        self.assertFalse(report["ok"])
        self.assertEqual(report["stopped"], "the post was not read within the budget by: raya")
        self.assertEqual(report["post"]["unread"], ["raya"])
        self.assertEqual((report["post"]["readers"], report["post"]["read"]["count"]), (2, 1))

    def test_a_peer_that_does_not_end_with_its_input_is_killed(self):
        with mock.patch("sim.run.GRACE", 0.2):
            report = self.run_of("lingers=pax")
        ended_at = time.time()

        self.assertTrue(report["ok"], report["stopped"])
        # The peer writes the file when its input ends, and would then run on for `LINGER`.
        input_ended_at = (self.out / "data" / "pax.eof").stat().st_mtime
        self.assertLess(ended_at - input_ended_at, standin.LINGER)


class CommandLine(Scratch):
    """The command, run in this process."""

    def sim(self, *arguments: str, faults: Tuple[str, ...] = ()) -> Tuple[int, str]:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            chat_cli = stand_in(self.scratch, *faults)
            status = main(["--chat-cli", str(chat_cli), "--out", str(self.out), *arguments])
        return status, printed.getvalue()

    def report(self) -> dict:
        return json.loads((self.out / "run.json").read_text(encoding="utf-8"))

    def test_a_run_every_member_read_the_post_of_is_status_0(self):
        status, printed = self.sim("--peers", "3")

        self.assertEqual(status, 0)
        self.assertIn("\nevery member read the post\n", printed)
        self.assertEqual(printed, (self.out / "run.txt").read_text(encoding="utf-8"))
        self.assertTrue(self.report()["ok"])

    def test_the_timers_are_the_fast_ones_unless_another_set_is_named(self):
        self.sim("--peers", "3")

        self.assertEqual(self.report()["timers"], "fast")
        self.assertEqual(given(self.arguments("pax"), "--group-commit"), ["fast"])

    def test_a_run_that_stopped_is_status_1_and_still_writes_what_it_measured(self):
        status, printed = self.sim("--peers", "3", "--budget", "2", faults=("deaf=raya",))

        self.assertEqual(status, 1)
        self.assertIn("\nstopped: the post was not read within the budget by: raya\n", printed)
        self.assertEqual(printed, (self.out / "run.txt").read_text(encoding="utf-8"))
        report = self.report()
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["batches"]), 1)

    def test_a_command_line_it_refuses_is_status_2_and_starts_nothing(self):
        self.out.mkdir()
        for arguments in (["--peers", "1"], ["--peers", "3"], ["--peers", "3", "--batch", "0"]):
            with self.assertRaises(SystemExit) as refused:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.sim(*arguments)
            self.assertEqual(refused.exception.code, 2, arguments)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_the_signal_handlers_of_the_caller_are_put_back(self):
        before = [signal.getsignal(signum) for signum in SIGNALS]
        self.sim("--peers", "3")

        self.assertEqual([signal.getsignal(signum) for signum in SIGNALS], before)


class Signals(Scratch):
    """The command as a process of its own, sent a signal while its batch waits on a member."""

    def start(self, *faults: str, ignoring: Tuple[int, ...] = ()) -> subprocess.Popen:
        def dispositions() -> None:
            for signum in SIGNALS:
                signal.signal(signum, signal.SIG_IGN if signum in ignoring else signal.SIG_DFL)

        driver = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(TOOL),
                "--chat-cli",
                str(stand_in(self.scratch, *faults)),
                "--peers",
                "3",
                "--out",
                str(self.out),
                "--budget",
                "120",
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

    def left(self) -> List[str]:
        """The processes of the run that are still there."""
        names = []
        for kept in sorted((self.out / "data").glob("*.json")):
            with contextlib.suppress(ProcessLookupError):
                os.kill(json.loads(kept.read_text(encoding="utf-8"))["pid"], 0)
                names.append(kept.stem)
        url = (self.out / "registry.out").read_text(encoding="utf-8").strip()
        with contextlib.suppress(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", int(url.rsplit(":", 1)[1])), timeout=30).close()
            names.append("the registry")
        return names

    def interrupted(self, driver: subprocess.Popen, signum: signal.Signals) -> None:
        printed, errors = driver.communicate(timeout=60)

        self.assertEqual(driver.returncode, 128 + signum)
        self.assertEqual(errors, "")
        self.assertEqual(self.left(), [])
        report = json.loads((self.out / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(report["stopped"], f"interrupted by {signum.name}")
        self.assertFalse(report["ok"])
        for node in report["nodes"]:
            self.assertEqual(node["wired"], {"peers": 2, "mesh_peers": 2}, node["name"])
        self.assertEqual(len(report["nodes"]), 3)
        self.assertIn(f"\nstopped: interrupted by {signum.name}\n", printed)
        self.assertEqual(printed, (self.out / "run.txt").read_text(encoding="utf-8"))

    def interrupt(self, signum: signal.Signals) -> None:
        driver = self.start("mute=raya")
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
        driver = self.start("mute=raya", "lingers=pax")
        self.wait_for(self.out / "saro.log", "Publish WelcomeMessage")
        driver.send_signal(signal.SIGTERM)
        self.wait_for(self.out / "data" / "pax.eof")
        driver.send_signal(signal.SIGINT)

        self.interrupted(driver, signal.SIGTERM)

    def test_a_signal_while_the_processes_of_a_whole_run_are_ended_changes_nothing(self):
        driver = self.start("lingers=pax")
        self.wait_for(self.out / "data" / "pax.eof")
        driver.send_signal(signal.SIGTERM)
        printed, _ = driver.communicate(timeout=60)

        self.assertEqual(driver.returncode, 0)
        self.assertIn("\nevery member read the post\n", printed)
        self.assertEqual(self.left(), [])

    def test_a_signal_the_command_was_started_ignoring_stays_ignored(self):
        driver = self.start("slow", ignoring=(signal.SIGINT,))
        self.wait_for(self.out / "saro.log", "Publish WelcomeMessage")
        driver.send_signal(signal.SIGINT)
        printed, _ = driver.communicate(timeout=60)

        self.assertEqual(driver.returncode, 0)
        self.assertIn("\nevery member read the post\n", printed)
        self.assertEqual(self.left(), [])


if __name__ == "__main__":
    unittest.main()
