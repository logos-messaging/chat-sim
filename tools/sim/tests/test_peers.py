import http.client
import json
import os
import signal
import statistics
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from sim.peers import (
    Interrupted,
    OutOfBudget,
    Peer,
    Registry,
    Stopped,
    Watch,
    index_of,
    name_of,
)
from sim.run import Settings

REGISTRY = Settings.registry
NODE = "/ip4/127.0.0.1/tcp/4001/p2p/16Uiu2HAm65ah8ABpi9PiX79T6PthDwhmknXi3161ZKT1ZbeeX1Rd"
KEY = "c0ffee11" + "9" * 56
READY = json.dumps(
    {"type": "ready", "name": "raya", "address": "a1", "installation": KEY, "node_address": NODE}
)

# A peer that answers each input with a line of a roster and then with the input's result.
ANSWERING = f"""
import json, sys
print({READY!r}, flush=True)
for number, text in enumerate(sys.stdin, 1):
    print(json.dumps({{"type": "members", "convo": text.strip()}}), flush=True)
    refused = text.startswith("/add")
    result = {{"type": "result", "input": number, "ok": not refused, "error": "full"}}
    print(json.dumps(result), flush=True)
"""


def soon() -> float:
    return time.monotonic() + 30


class Scratch(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.directory = Path(scratch.name)
        self.watch = Watch()


class Names(unittest.TestCase):
    def test_six_peers_have_names_and_the_rest_are_numbered_from_seven(self):
        self.assertEqual(
            [name_of(index) for index in range(8)],
            ["saro", "raya", "pax", "mira", "kes", "tomo", "peer7", "peer8"],
        )

    def test_a_name_is_that_of_one_peer_or_of_none(self):
        self.assertEqual([index_of(name_of(index)) for index in range(12)], list(range(12)))
        for name in ("", "Saro", "saro ", "peer", "peer3", "peer6", "peer07", "peer-7", "peer7a"):
            self.assertIsNone(index_of(name), name)


class Watching(unittest.TestCase):
    def test_the_first_signal_interrupts_and_a_second_changes_nothing(self):
        watch = Watch()
        watch()
        watch.interrupt(signal.SIGTERM)
        watch.interrupt(signal.SIGINT)

        self.assertEqual(watch.signal, signal.SIGTERM)
        with self.assertRaises(Interrupted):
            watch()

    def test_a_signal_after_the_close_interrupts_nothing(self):
        watch = Watch()
        watch.close()
        watch.interrupt(signal.SIGTERM)

        self.assertIsNone(watch.signal)
        watch()

    def test_a_signal_before_the_close_stands_after_it(self):
        watch = Watch()
        watch.interrupt(signal.SIGHUP)
        watch.close()

        self.assertEqual(watch.signal, signal.SIGHUP)


class Lines(Scratch):
    """A peer that is a few lines of Python, and what the tool makes of what it writes."""

    def peer(self, program: str, name: str = "raya") -> Peer:
        command = [sys.executable, "-c", program]
        peer = Peer(name, command, self.directory, dict(os.environ), self.watch)
        self.addCleanup(peer.reap, time.monotonic() + 30)
        self.addCleanup(peer.close)
        return peer

    def stop_of(self, program: str, deadline: float = 30.0) -> Stopped:
        with self.assertRaises(Stopped) as stopped:
            self.peer(program).read_ready(time.monotonic() + deadline)
        return stopped.exception

    def test_the_ready_line_says_where_the_peer_and_its_node_are(self):
        peer = self.peer(ANSWERING)
        peer.read_ready(soon())

        self.assertEqual((peer.address, peer.node_address), ("a1", NODE))
        self.assertEqual(peer.installation, "c0ffee11")
        self.assertTrue(peer.started_at <= peer.ready_at <= time.time())

    def test_the_lines_a_peer_wrote_are_kept_beside_its_log(self):
        peer = self.peer(ANSWERING)
        peer.read_ready(soon())
        peer.input("/node", soon())
        peer.close()
        peer.reap(soon())

        kept = (self.directory / "raya.out").read_text(encoding="utf-8").splitlines()
        self.assertEqual(kept[0], READY)
        self.assertEqual(json.loads(kept[2])["input"], 1)

    def test_what_a_peer_wrote_is_read_back_once_it_ended_but_for_what_is_no_object(self):
        lines = ['{"type": "inbound_error"}', "[1]", "bye"]
        peer = self.peer(f"import sys; sys.stdin.read(); print(*{lines!r}, sep='\\n')")
        peer.close()

        self.assertFalse(peer.reap(soon()))
        self.assertEqual(peer.written(), [{"type": "inbound_error"}])

    def test_a_peer_that_does_not_end_by_the_deadline_is_killed_and_said_so(self):
        peer = self.peer("import sys, time; sys.stdin.read(); time.sleep(60)")
        peer.close()

        self.assertTrue(peer.reap(time.monotonic() + 0.2))
        self.assertEqual(peer.ended(), -signal.SIGKILL)

    def test_an_input_is_answered_by_the_result_that_bears_its_number(self):
        peer = self.peer(ANSWERING)
        peer.read_ready(soon())
        before = time.time()
        sent_at, read_at, first = peer.input("/node", soon())
        _, _, second = peer.input("/new sim", soon())

        self.assertEqual((first["input"], second["input"]), (1, 2))
        self.assertTrue(before <= sent_at <= read_at <= time.time())

    def test_a_line_no_wait_claimed_is_kept_for_the_wait_that_wants_it(self):
        peer = self.peer(ANSWERING)
        peer.read_ready(soon())
        peer.input("one", soon())
        peer.input("two", soon())

        def roster(line: dict) -> bool:
            return line["type"] == "members"

        first_at, first = peer.wait_for("roster", roster, soon())
        second_at, second = peer.wait_for("roster", roster, soon())
        self.assertEqual((first["convo"], second["convo"]), ("one", "two"))
        self.assertLessEqual(first_at, second_at)
        with self.assertRaises(OutOfBudget) as stopped:
            peer.wait_for("roster", roster, time.monotonic() + 0.3)
        self.assertEqual(str(stopped.exception), "raya: no roster within the budget")

    def test_an_input_that_was_refused_stops_the_run(self):
        peer = self.peer(ANSWERING)
        peer.read_ready(soon())

        with self.assertRaises(Stopped) as stopped:
            peer.run("/add a1", soon())
        self.assertEqual(str(stopped.exception), "raya: /add was refused: full")

    def test_a_peer_that_writes_nothing_runs_out_of_budget(self):
        stopped = self.stop_of("import sys; sys.stdin.read()", deadline=0.3)

        self.assertIsInstance(stopped, OutOfBudget)
        self.assertEqual(str(stopped), "raya: no ready line within the budget")

    def test_a_line_that_is_not_json_is_named_with_its_peer_and_its_first_80_characters(self):
        stopped = self.stop_of("import sys; print('x' * 200, flush=True); sys.stdin.read()")

        self.assertEqual(str(stopped), f"raya: a line that is no JSON object: {'x' * 80}")

    def test_a_line_of_json_that_is_no_object_is_named_as_well(self):
        stopped = self.stop_of("import sys; print('[1, 2]', flush=True); sys.stdin.read()")

        self.assertEqual(str(stopped), "raya: a line that is no JSON object: [1, 2]")

    def test_a_first_line_that_names_no_node_stops_the_run(self):
        ready = '{"type": "ready", "address": "a1"}'
        stopped = self.stop_of(f"import sys; print({ready!r}, flush=True); sys.stdin.read()")

        self.assertEqual(
            str(stopped), "raya: its first line names no node: {'type': 'ready', 'address': 'a1'}"
        )

    def test_a_peer_that_ended_is_named_with_its_status_and_its_standard_error(self):
        stopped = self.stop_of("import sys; sys.exit(3)")

        self.assertEqual(
            str(stopped), f"raya ended with status 3: see {self.directory / 'raya.stderr'}"
        )

    def test_a_peer_a_signal_ended_is_named_with_the_signal(self):
        stopped = self.stop_of("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")

        self.assertEqual(
            str(stopped), f"raya ended with signal 9: see {self.directory / 'raya.stderr'}"
        )

    def test_a_peer_that_closed_its_output_and_runs_on_is_told_from_one_that_ended(self):
        stopped = self.stop_of("import os, sys; os.close(1); sys.stdin.read()")

        self.assertEqual(str(stopped), "raya: its output ended before ready line")

    def test_a_wait_on_one_peer_ends_when_another_has_ended(self):
        pax = self.peer("import sys; sys.exit(3)", name="pax")
        pax.reap(soon())
        self.watch.processes.append(pax)
        started = time.monotonic()

        with self.assertRaises(Stopped) as stopped:
            self.peer("import sys; sys.stdin.read()").read_ready(time.monotonic() + 20)
        self.assertEqual(
            str(stopped.exception), f"pax ended with status 3: see {self.directory / 'pax.stderr'}"
        )
        self.assertLess(time.monotonic() - started, 10)

    def test_an_input_to_a_peer_that_ended_stops_the_run(self):
        peer = self.peer("pass")
        peer.reap(soon())

        with self.assertRaises(Stopped) as stopped:
            peer.input("/node", soon())
        self.assertIn("raya: its input is closed: ", str(stopped.exception))


class TheRegistry(Scratch):
    def registry(self, script: Path = REGISTRY) -> Registry:
        registry = Registry(script, self.directory, self.watch)
        self.addCleanup(registry.stop)
        return registry

    def connection(self, registry: Registry) -> http.client.HTTPConnection:
        url = urlsplit(registry.url)
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
        self.addCleanup(connection.close)
        return connection

    def test_it_serves_on_a_port_of_its_own_and_writes_down_each_request(self):
        registry = self.registry()
        registry.read_url(soon())
        connection = self.connection(registry)
        before = time.time()
        connection.request("POST", "/v1/account/a1", b'{"account": "a1"}')
        connection.getresponse().read()
        connection.request("GET", "/v1/account/a1")
        body = connection.getresponse().read()

        self.assertEqual(body, b'{"account": "a1"}')
        served = registry.served()
        self.assertEqual(
            [(request["method"], request["path"], request["status"]) for request in served],
            [("POST", "/v1/account/a1", 200), ("GET", "/v1/account/a1", 200)],
        )
        self.assertTrue(all(before <= request["at"] <= time.time() for request in served))

    def test_a_lookup_on_a_kept_connection_does_not_wait_for_an_acknowledgement(self):
        registry = self.registry()
        registry.read_url(soon())
        connection = self.connection(registry)
        connection.request("POST", "/v1/account/a1", b'{"account": "a1"}')
        connection.getresponse().read()
        took = []
        for _ in range(20):
            asked = time.monotonic()
            connection.request("GET", "/v1/account/a1")
            connection.getresponse().read()
            took.append(time.monotonic() - asked)

        # A reply whose body waits for the acknowledgement of its headers takes 40 ms.
        self.assertLess(statistics.median(took), 0.02)

    def test_stopped_it_serves_no_more(self):
        registry = self.registry()
        registry.read_url(soon())
        registry.stop()

        self.assertIsNotNone(registry.ended())
        with self.assertRaises(ConnectionRefusedError):
            self.connection(registry).request("GET", "/v1/account/a1")

    def test_a_registry_that_ended_at_its_start_is_named_with_its_status(self):
        script = self.directory / "registry.py"
        script.write_text("import sys\nsys.exit(4)\n", encoding="utf-8")

        with self.assertRaises(Stopped) as stopped:
            self.registry(script).read_url(soon())
        self.assertEqual(
            str(stopped.exception),
            f"the registry ended with status 4: see {self.directory / 'registry.stderr'}",
        )


if __name__ == "__main__":
    unittest.main()
