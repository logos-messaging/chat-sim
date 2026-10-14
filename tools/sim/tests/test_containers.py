import contextlib
import io
import ipaddress
import json
import os
import signal
import sys
import time
import unittest
from pathlib import Path
from typing import List
from unittest import mock

from sim import containers, scenario
from sim.cli import main
from sim.containers import Rig
from sim.peers import Watch
from sim.run import Settings, run
from sim.sweep import Interrupts, sweep

from tests import stand_in, stand_in_engine, standin
from tests.test_run import Scratch, given, said

IMAGE = "logos-chat-sim:test"

# The wire of the tests: raya's packets are held longer than any other member's.
WIRE = {"seed": 5, "delay_ms": {"raya": 40}, "jitter_ms": 3, "loss": 0.015}

# The range the tests' networks take a subnet of: loopback, where a stand-in listens.
LOOPBACK = "127.77.0.0/16"


@unittest.skipUnless(sys.platform == "linux", "a container is on an address of loopback")
class Containers(Scratch):
    """`run` in containers of a stand-in engine, whose containers are stand-ins on loopback."""

    def setUp(self):
        super().setUp()
        self.bin = self.scratch / "bin"
        self.bin.mkdir()
        path = mock.patch.dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        path.start()
        self.addCleanup(path.stop)
        subnets = mock.patch.object(containers, "SUBNETS", ipaddress.ip_network(LOOPBACK))
        subnets.start()
        self.addCleanup(subnets.stop)

    def settings(
        self,
        *faults: str,
        engine: str = "podman",
        refuses: tuple = (),
        taken: tuple = (),
        **changed,
    ) -> Settings:
        """A run whose chat-cli has `faults`, and whose engine `refuses` and holds `taken`."""
        chat_cli = stand_in(self.bin, *faults)
        self.state = stand_in_engine(self.bin, engine, chat_cli, *refuses, taken=taken)
        return Settings(
            chat_cli=None,
            scenario=scenario.scenario(said(**changed)),
            out=self.out,
            image=IMAGE,
            engine=engine,
        )

    def run_of(self, *faults: str, **changed) -> dict:
        report = run(self.settings(*faults, **changed), Watch())
        self.assertLeftNothing()
        return report

    def assertLeftNothing(self):
        with self.assertRaises(ChildProcessError, msg="the run left a process"):
            os.waitpid(-1, os.WNOHANG)
        self.assertEqual(self.left(), ([], []), "the run left a container or its network")

    def calls(self, command: str) -> List[List[str]]:
        """Each call of the engine that was `command`, in order: of a `run`, its options."""
        kept = (self.state / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        called = [call for call in map(json.loads, kept) if call[0] == command]
        return [call[: call.index(IMAGE)] if command == "run" else call for call in called]

    def left(self):
        """The containers and the networks that are left."""
        return tuple(
            sorted(entry.name for entry in (self.state / kind).iterdir())
            for kind in ("containers", "networks")
        )

    def test_each_member_runs_in_a_container_of_its_own_at_an_address_of_its_own(self):
        report = self.run_of()

        self.assertTrue(report["ok"], report["stopped"])
        self.assertEqual(
            (report["rig"], report["engine"], report["image"]), ("containers", "podman", IMAGE)
        )
        ((*created, label, network),) = self.calls("network")[:1]
        subnet = ipaddress.ip_network(given(created, "--subnet")[0])
        gateway, registry, *addresses = (str(host) for host in subnet.hosts())
        addresses = addresses[:3]
        self.assertEqual([node["ip"] for node in report["nodes"]], addresses)
        for name, address in zip(("saro", "raya", "pax"), addresses):
            self.assertEqual(given(self.arguments(name), "--node-ip"), [address])
        self.assertTrue(
            given(self.arguments("pax"), "--registry-url")[0].startswith(f"http://{registry}:")
        )
        self.assertEqual(
            [node.split("/")[2] for node in given(self.arguments("pax"), "--static-node")],
            addresses[:2],
        )
        self.assertEqual(created[:3], ["network", "create", "--internal"])
        self.assertEqual(given(created, "--gateway"), [gateway])
        self.assertEqual(label, f"logos-chat-sim={network.removeprefix('sim-')}")
        self.assertEqual(self.calls("network")[-1], ["network", "rm", network])
        runs = self.calls("run")
        self.assertEqual(
            [given(call, "--name") for call in runs],
            [[f"{network}-{name}"] for name in ("registry", "saro", "raya", "pax")],
        )
        for call in runs:
            self.assertIn("--userns=keep-id", call)
            self.assertEqual(given(call, "--network"), [network])
            self.assertEqual(given(call, "--label"), [label])
        self.assertIsNone(report["unremoved"])

    def test_a_container_of_docker_runs_as_the_user_of_this_machine(self):
        report = self.run_of(engine="docker")

        self.assertTrue(report["ok"], report["stopped"])
        for call in self.calls("run"):
            self.assertEqual(given(call, "--user"), [f"{os.getuid()}:{os.getgid()}"])
            self.assertNotIn("--userns=keep-id", call)

    def test_the_wire_shapes_what_each_member_sends_once_it_is_ready(self):
        report = self.run_of(wire=WIRE)

        self.assertTrue(report["ok"], report["stopped"])
        self.assertEqual(report["wire"], WIRE)
        kept = (self.state / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        order = [
            (call[0], (given(call, "--name") or call[3:4])[0].rpartition("-")[2])
            for call in map(json.loads, kept)
            if call[0] in ("run", "exec")
        ]
        self.assertEqual(
            order,
            [("run", "registry")]
            + [(command, name) for name in ("saro", "raya", "pax") for command in ("run", "exec")],
        )
        shaped = [call[4:] for call in self.calls("exec")]
        for qdisc, delay in zip(shaped, ("0ms", "40ms", "0ms")):
            self.assertEqual(
                qdisc[:-1],
                ["tc", "qdisc", "add", "dev", "eth0", "root", "netem"]
                + ["delay", delay, "3ms", "loss", "1.5%", "seed"],
            )
        self.assertEqual(len({qdisc[-1] for qdisc in shaped}), 3)

    def test_each_run_of_a_sweep_has_the_wire_seed_after_that_of_the_run_before(self):
        report = sweep(self.settings(wire=WIRE), 2, Interrupts(), lambda _: None)
        self.assertLeftNothing()

        self.assertEqual(report["as_expected"], 2)
        self.assertEqual([row["wire_seed"] for row in report["rows"]], [5, 6])
        seeds = [call[-1] for call in self.calls("exec")]
        self.assertEqual(len(seeds), 6)
        self.assertEqual(len(set(seeds)), 6)

    def test_a_qdisc_tc_refuses_stops_the_run_and_its_containers_are_removed(self):
        report = self.run_of(refuses=("refuses-tc",), wire=WIRE)

        self.assertEqual(
            report["stopped"],
            "tc on the interface of saro ended with status 2: "
            "Error: Specified qdisc kind is unknown.",
        )
        self.assertEqual([node["name"] for node in report["nodes"]], ["saro"])

    def test_a_network_the_engine_refuses_stops_the_run_before_any_container(self):
        report = self.run_of(refuses=("refuses-network",))

        self.assertEqual(
            report["stopped"],
            "podman network create ended with status 125: Error: netavark: no free subnet",
        )
        self.assertEqual(len(self.calls("network")), 1)
        self.assertEqual((self.calls("run"), report["unremoved"]), ([], None))

    def test_a_subnet_docker_says_is_taken_is_passed_over_for_the_next(self):
        self.assertPassesOverTaken("docker")

    def test_a_subnet_podman_says_is_taken_is_passed_over_for_the_next(self):
        self.assertPassesOverTaken("podman")

    def assertPassesOverTaken(self, engine: str):
        """Three of four subnets are taken: the run tries them from the last, which its id picks."""
        four = ipaddress.ip_network("127.77.0.0/22")
        first, second, third, last = four.subnets(new_prefix=24)
        taken = (str(first), str(second), str(last))
        ids = mock.Mock(**{"token_hex.return_value": "00000003"})
        with mock.patch.object(containers, "SUBNETS", four), mock.patch.object(
            containers, "secrets", ids
        ):
            report = self.run_of(engine=engine, taken=taken)

        self.assertTrue(report["ok"], report["stopped"])
        created = [call for call in self.calls("network") if call[1] == "create"]
        tried = [ipaddress.ip_network(given(call, "--subnet")[0]) for call in created]
        self.assertEqual(tried, [last, first, second, third])
        hosts = [str(host) for host in third.hosts()]
        self.assertEqual([node["ip"] for node in report["nodes"]], hosts[2:5])

    def test_a_run_whose_engine_says_each_subnet_is_taken_stops_before_any_container(self):
        with mock.patch.object(containers, "SUBNETS", ipaddress.ip_network("127.77.0.0/23")):
            report = self.run_of(taken=("127.77.0.0/23",))

        self.assertEqual(report["stopped"], "the engine says each /24 of 127.77.0.0/23 is taken")
        self.assertEqual([call[1] for call in self.calls("network")], ["create", "create"])
        self.assertEqual((self.calls("run"), report["unremoved"]), ([], None))

    def test_a_run_of_more_members_than_a_network_holds_stops_before_the_network(self):
        report = self.run_of(members=253)

        self.assertEqual(report["stopped"], "the run needs 254 addresses, and its network has 253")
        self.assertEqual((self.calls("network"), self.calls("run")), ([], []))

    def test_a_peer_a_signal_ends_in_its_container_is_said_to_have_ended_by_that_signal(self):
        report = self.run_of("signalled-at-the-end=pax")

        self.assertTrue(report["ok"], report["stopped"])
        self.assertEqual(
            [(node["exit_status"], node["exit_signal"], node["ended"]) for node in report["nodes"]],
            [(0, None, "asked"), (0, None, "asked"), (None, standin.SIGNAL, "in its stop")],
        )

    def test_the_container_of_a_peer_that_was_killed_is_removed_with_the_run(self):
        with mock.patch("sim.run.GRACE", 0.2):
            report = self.run_of("lingers=pax")

        self.assertTrue(report["ok"], report["stopped"])
        (removed,) = self.calls("rm")
        self.assertEqual(removed[:2], ["rm", "--force"])
        self.assertTrue(removed[2].endswith("-pax"), removed)
        pid = json.loads((self.out / "data" / "pax.json").read_text(encoding="utf-8"))["pid"]
        self.assertTrue(gone(pid), "the peer of the container removed still runs")


def gone(pid: int) -> bool:
    """Whether the process `pid` is gone within a second, as its parent reaps it."""
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


class Seeds(unittest.TestCase):
    """The seed each member's interface draws its loss and its jitter from."""

    def seeds(self, wire: dict, names=("saro", "raya", "pax")) -> List[str]:
        rig = Rig("podman", IMAGE, Path("run"))
        shaped = []
        with mock.patch.object(Rig, "_engine", lambda _, *call, what: shaped.append(call[-1])):
            for name in names:
                rig.shape(name, 0, scenario.Wire(**wire))
        return shaped

    def test_each_member_has_a_seed_of_its_own_and_the_same_one_in_each_run(self):
        first = self.seeds({"seed": 5})

        self.assertEqual(len(set(first)), 3)
        self.assertEqual(self.seeds({"seed": 5}), first)
        self.assertNotEqual(self.seeds({"seed": 6}), first)
        self.assertEqual(self.seeds({"seed": 5}, ("pax",)), first[2:])


@unittest.skipUnless(sys.platform == "linux", "a container is on an address of loopback")
class CommandLine(Scratch):
    """The command with --image, run in this process."""

    def setUp(self):
        super().setUp()
        self.bin = self.scratch / "bin"
        self.bin.mkdir()
        path = mock.patch.dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        path.start()
        self.addCleanup(path.stop)
        subnets = mock.patch.object(containers, "SUBNETS", ipaddress.ip_network(LOOPBACK))
        subnets.start()
        self.addCleanup(subnets.stop)
        stand_in_engine(self.bin, "podman", stand_in(self.bin))

    def sim(self, *arguments: str, **changed) -> str:
        file = self.scratch / "scenario.json"
        file.write_text(json.dumps(said(**changed)), encoding="utf-8")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            status = main(["run", str(file), *arguments, "--out", str(self.out)])
        self.assertEqual(status, 0, printed.getvalue())
        return printed.getvalue()

    def refused(self, *arguments: str, **changed) -> str:
        """What the command says of a command line it refuses."""
        complained = io.StringIO()
        with self.assertRaises(SystemExit) as refused:
            with contextlib.redirect_stderr(complained):
                self.sim(*arguments, **changed)
        self.assertEqual(refused.exception.code, 2, arguments)
        return complained.getvalue().splitlines()[-1]

    def test_what_a_run_in_containers_prints_names_the_image_the_engine_and_the_wire(self):
        printed = self.sim("--image", IMAGE, "--engine", "podman", wire=WIRE)

        self.assertEqual(
            printed.splitlines()[0],
            "3 members, fast group timers, largest message 512 KiB, "
            f"containers of {IMAGE} on podman, wire seed 5, delay by member, jitter 3 ms, "
            "loss 0.015",
        )

    def test_each_thing_a_run_in_containers_cannot_be_given_is_refused(self):
        chat_cli = str(stand_in(self.bin))
        refusals = [
            (
                "sim run: error: argument --image: not allowed with argument --chat-cli",
                ("--chat-cli", chat_cli, "--image", IMAGE),
                {},
            ),
            (
                "sim: error: --engine: it runs --image, and none is given",
                ("--chat-cli", chat_cli, "--engine", "podman"),
                {},
            ),
            (
                "sim: error: --image: the scenario has `preset`, "
                "and a network of containers reaches no fleet",
                ("--image", IMAGE, "--engine", "podman"),
                {"preset": "logos.test"},
            ),
            (
                "sim: error: the scenario has `wire`, and only --image puts conditions "
                "on the wire",
                ("--chat-cli", chat_cli),
                {"wire": WIRE},
            ),
        ]
        for why, arguments, changed in refusals:
            with self.subTest(why=why):
                self.assertEqual(self.refused(*arguments, **changed), why)

    def test_an_engine_that_is_not_on_path_is_refused(self):
        with mock.patch.dict(os.environ, PATH=str(self.bin)):
            refused = self.refused("--image", IMAGE, "--engine", "docker")

        self.assertEqual(refused, "sim: error: --engine: docker is not on PATH")


if __name__ == "__main__":
    unittest.main()
