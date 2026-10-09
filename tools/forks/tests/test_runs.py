import os
import tempfile
import unittest
from pathlib import Path

from forks.records import Counts
from forks.runs import InputError, find_runs, read_run


def run_line(installation: str) -> str:
    stamp = "2026-10-08T16:02:11.004+02:00"
    return f"{stamp} INFO: libchat::groupv2trace: run installation={installation} account=5be2c0d1\n"


class Runs(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.directory = Path(scratch.name)

    def write(self, name: str, text: str = "") -> Path:
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_a_directory_holds_a_run_in_every_log(self):
        self.write("saro.log")
        self.write("raya.log")
        self.write("expect.json")
        self.write("deeper/pax.log")
        self.assertEqual([run.name for run in find_runs([self.directory])], ["raya", "saro"])

    def test_a_file_is_a_run_whatever_its_name(self):
        capture = self.write("capture.txt", run_line("a3f91c07"))
        (run,) = find_runs([capture])
        self.assertEqual(run.name, "capture")
        self.assertEqual(read_run(run).counts, Counts(lines=1, records=1))

    def test_rotated_parts_are_one_run_in_the_order_written(self):
        self.write("chat_module_20261008_133105.log", run_line("00000003"))
        self.write("chat_module_20261008_133105.002.log", run_line("00000002") + "a backtrace\n")
        self.write("chat_module_20261008_133105.001.log", run_line("00000001"))
        self.write("chat_module_20261008_140000.log", run_line("00000004"))
        first, second = find_runs([self.directory])
        self.assertEqual(
            (first.name, second.name),
            ("chat_module_20261008_133105", "chat_module_20261008_140000"),
        )
        self.assertEqual(
            [part.name for part in first.parts],
            [
                "chat_module_20261008_133105.001.log",
                "chat_module_20261008_133105.002.log",
                "chat_module_20261008_133105.log",
            ],
        )
        reading = read_run(first)
        self.assertEqual(
            [(record.installation, record.line, record.run) for record in reading.records],
            [
                ("00000001", 1, "chat_module_20261008_133105"),
                ("00000002", 2, "chat_module_20261008_133105"),
                ("00000003", 4, "chat_module_20261008_133105"),
            ],
        )
        self.assertEqual(reading.counts, Counts(lines=4, records=3, unstamped=1))

    def test_more_than_three_digits_are_no_part_number(self):
        for name in ("saro.log", "saro.999.log", "saro.1000.log", "saro.20261009.log"):
            self.write(name)
        self.assertEqual(
            [(run.name, [part.name for part in run.parts]) for run in find_runs([self.directory])],
            [
                ("saro", ["saro.999.log", "saro.log"]),
                ("saro.1000", ["saro.1000.log"]),
                ("saro.20261009", ["saro.20261009.log"]),
            ],
        )

    def test_two_digits_are_no_part_number(self):
        self.write("saro.01.log")
        self.write("saro.log")
        self.assertEqual([run.name for run in find_runs([self.directory])], ["saro", "saro.01"])

    def test_parts_without_the_file_still_written_are_a_run(self):
        self.write("saro.001.log")
        self.write("saro.002.log")
        (run,) = find_runs([self.directory])
        self.assertEqual((run.name, len(run.parts)), ("saro", 2))

    def test_a_log_given_twice_is_read_once(self):
        saro = self.write("saro.log")
        self.assertEqual(len(find_runs([saro, self.directory, saro])), 1)

    def test_files_given_one_by_one_are_grouped_like_a_directory(self):
        paths = [self.write("saro.log"), self.write("saro.001.log"), self.write("raya.log")]
        runs = find_runs(paths)
        self.assertEqual([(run.name, len(run.parts)) for run in runs], [("raya", 1), ("saro", 2)])

    def test_two_runs_under_one_name_are_refused(self):
        paths = [self.write("monday/saro.log"), self.write("tuesday/saro.log")]
        with self.assertRaisesRegex(InputError, "more than one run is named saro"):
            find_runs(paths)

    def test_two_files_at_one_place_of_a_run_are_refused(self):
        paths = [self.write("saro.log"), self.write("saro.txt")]
        with self.assertRaisesRegex(InputError, "the same part of the run saro"):
            find_runs(paths)

    def test_a_run_named_after_a_file_whose_name_is_not_utf8_can_be_printed(self):
        try:
            paths = [self.write(os.fsdecode(name)) for name in (b"kes\xff.log", b"kes\xff.001.log")]
        except OSError:
            self.skipTest("this file system takes no name that is not UTF-8")
        (run,) = find_runs(paths)
        self.assertEqual((run.name, len(run.parts)), ("kes\\xff", 2))
        self.assertEqual(read_run(run).records, [])

    def test_a_directory_without_a_log_is_refused(self):
        self.write("notes.txt")
        with self.assertRaisesRegex(InputError, "holds no .log file"):
            find_runs([self.directory])

    def test_runs_come_by_name(self):
        names = [f"member{index:02d}" for index in range(12)]
        for name in reversed(names):
            self.write(f"{name}.log")
        self.assertEqual([run.name for run in find_runs([self.directory])], names)

    def test_the_order_of_the_paths_does_not_matter(self):
        paths = [self.write(name) for name in ("saro.log", "raya.log", "pax.001.log", "pax.log")]
        self.assertEqual(find_runs(paths), find_runs(reversed(paths)))


if __name__ == "__main__":
    unittest.main()
