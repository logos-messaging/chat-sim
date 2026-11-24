import shlex
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from sim.peers import Interrupted, Runner, Watch, index_of, name_of

# A runner that takes no notice of being asked to end.
DEAF = (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('deaf', flush=True)\n"
    "time.sleep(60)\n"
)


class Names(unittest.TestCase):
    def test_six_members_have_names_and_the_rest_are_numbered_from_seven(self):
        self.assertEqual(
            [name_of(index) for index in range(8)],
            ["saro", "raya", "pax", "mira", "kes", "tomo", "peer7", "peer8"],
        )

    def test_a_name_is_that_of_one_member_or_of_none(self):
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


class Ending(unittest.TestCase):
    def test_a_runner_that_does_not_end_once_asked_is_killed_after_the_grace(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        directory = Path(scratch.name)
        program = directory / "runner"
        program.write_text(f"#!/bin/sh\nexec {shlex.join([sys.executable, '-c', DEAF])}\n")
        program.chmod(0o755)
        runner = Runner(program, directory / "plan.json", directory, Watch())
        until = time.monotonic() + 60
        while "deaf" not in (directory / "runner.out").read_text(encoding="utf-8"):
            self.assertLess(time.monotonic(), until, "the runner never began")
            time.sleep(0.01)
        with mock.patch("sim.peers.GRACE", 0.2):
            runner.stop()

        self.assertEqual(runner.ended(), -signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
