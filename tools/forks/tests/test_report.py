import contextlib
import io
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List, Tuple

from forks.cli import main

from . import FIXTURES, Reported, run_report

HISTORIES = FIXTURES / "histories"
REAL = FIXTURES / "real"
TOOL = Path(__file__).resolve().parents[1]

SARO, RAYA, PAX, MIRA, KES = "a3f91c07", "7b1e44d9", "e09a3f52", "19f6b0e3", "b54a9d10"


def positions(conversation: dict) -> dict:
    return {seat["id"]: seat["position"] for seat in conversation["installations"]}


def seat_of(conversation: dict, id: str) -> dict:
    (seat,) = [seat for seat in conversation["installations"] if seat["id"] == id]
    return seat


def steps(seat: dict) -> List[Tuple[str, int]]:
    return [(step["kind"], step["epoch"]) for step in seat["history"]]


def report_of(*lines: str) -> Reported:
    """The report of one log, `group.log`, that holds these lines."""
    with tempfile.TemporaryDirectory() as scratch:
        log = Path(scratch) / "group.log"
        log.write_text("".join(line + "\n" for line in lines))
        return run_report(log)


def epoch_line(installation: str) -> str:
    """Saro's or Raya's record of the one state of a group of the two."""
    return (
        "2026-10-09T06:54:50.313738Z  INFO libchat::groupv2trace: epoch "
        f"installation={installation} convo=2a30d6a2c1 epoch=1 state=b77d02c4a91e03d5 "
        f"members=2 roster={SARO}:5be2c0d1,{RAYA}:91c0e3a7"
    )


def counted(lines: int, records: int, unstamped: int = 0) -> dict:
    return {
        "lines": lines,
        "records": records,
        "unstamped": unstamped,
        "malformed": 0,
        "unknown": 0,
    }


class Histories(unittest.TestCase):
    """The histories a directory of logs can show, each read the way the command line reads it."""

    def test_all_thirty_are_here(self):
        self.assertEqual(len(list(HISTORIES.iterdir())), 30)

    def test_each_reads_as_its_expectation_says(self):
        for directory in sorted(HISTORIES.iterdir()):
            with self.subTest(history=directory.name):
                expect = json.loads((directory / "expect.json").read_text())
                expected = {
                    person["installation"]: person["position"]
                    for person in expect["positions"].values()
                }
                reported = run_report(directory)
                self.assertEqual(reported.conversation["verdict"], expect["verdict"])
                self.assertEqual(positions(reported.conversation), expected)
                self.assertEqual(reported.status, 0 if expect["verdict"] == "whole" else 1)
                self.assertEqual((reported.lines["malformed"], reported.lines["unknown"]), (0, 0))

    def test_one_log_of_many_members(self):
        reported = run_report(HISTORIES / "whole")
        seats = reported.conversation["installations"]
        self.assertEqual([seat["runs"] for seat in seats], [["group"]] * 4)
        # Five installations started and four joined; one line prints a record's text and is none.
        self.assertEqual(reported.lines, counted(lines=32, records=12, unstamped=4))

    def test_a_rotated_run_is_read_as_one_stream(self):
        saro = seat_of(run_report(HISTORIES / "behind-with-a-witness").conversation, SARO)
        self.assertEqual(saro["runs"], ["chat_module_20261008_133105"])
        self.assertEqual(
            [(step["epoch"], step["at"]["line"]) for step in saro["history"]], [(1, 7), (2, 13)]
        )

    def test_a_stamp_is_kept_as_written(self):
        stamps = {
            ("split-at-an-epoch", PAX): ("2026-10-08T15:33:12.080+02:00", True),
            ("stuck-pair-under-a-joiner", RAYA): ("2026-08-21T21:12:05.021", False),
            ("removed-by-left", SARO): ("2026-10-09T06:54:48.909027Z", True),
            ("split-seen-from-an-epoch", KES): ("2026-10-09T12:20:54.143575Z", True),
        }
        for (history, id), stamp in stamps.items():
            with self.subTest(history=history):
                seat = seat_of(run_report(HISTORIES / history).conversation, id)
                first = seat["history"][0]["at"]
                self.assertEqual((first["time"], first["has_offset"]), stamp)

    def test_a_conversation_id_is_kept_as_written(self):
        reported = run_report(HISTORIES / "one-sharer-alone")
        self.assertEqual(reported.conversation["id"], "team%20chat")

    def test_a_seat_without_a_log_has_an_account_and_no_run(self):
        pax = seat_of(run_report(HISTORIES / "split-seen-from-an-epoch").conversation, PAX)
        self.assertEqual(pax["account"], "0d7a66f3")
        self.assertEqual((pax["runs"], pax["epoch"], pax["history"]), ([], None, []))

    def test_posts_on_both_sides_of_a_split(self):
        conversation = run_report(HISTORIES / "split-at-an-epoch").conversation
        posts = conversation["messages"]
        self.assertEqual(
            [(post["frame"], post["sender"], post["epoch"]) for post in posts],
            [("8f21c07a", SARO, 2), ("e3b94d10", RAYA, 3), ("41d6a9e0", PAX, 3)],
        )
        before, on_a, on_b = [post["cells"] for post in posts]
        self.assertEqual(before, {SARO: "sent", RAYA: "read", PAX: "read", MIRA: "read"})
        self.assertEqual(on_a, {SARO: "read", RAYA: "sent", PAX: "unreadable", MIRA: "read"})
        self.assertEqual(
            on_b, {SARO: "unreadable", RAYA: "unreadable", PAX: "sent", MIRA: "unreadable"}
        )
        self.assertEqual(posts[1]["why"], {PAX: "decrypt"})
        self.assertEqual(conversation["unmatched_unreadable"], 1)

    def test_a_cell_of_a_post_says_where_its_record_stands_in_the_logs(self):
        conversation = run_report(HISTORIES / "split-at-an-epoch").conversation
        before, on_a, on_b = [
            {seat: (mark["run"], mark["line"]) for seat, mark in post["marks"].items()}
            for post in conversation["messages"]
        ]
        self.assertEqual(
            before, {SARO: ("saro", 9), RAYA: ("raya", 9), PAX: ("pax", 9), MIRA: ("mira", 9)}
        )
        self.assertEqual(
            on_a, {SARO: ("saro", 12), RAYA: ("raya", 12), PAX: ("pax", 16), MIRA: ("mira", 12)}
        )
        self.assertEqual(
            on_b, {SARO: ("saro", 14), RAYA: ("raya", 14), PAX: ("pax", 18), MIRA: ("mira", 14)}
        )
        unreadable = conversation["messages"][1]["marks"][PAX]
        self.assertEqual(
            (unreadable["time"], unreadable["has_offset"], unreadable["seconds"]),
            ("2026-10-08T15:37:01.201+02:00", True, 1791466621.201),
        )
        self.assertEqual(conversation["shown_messages"], 3)

    def test_a_runs_delivery_changes(self):
        conversation = run_report(HISTORIES / "split-at-an-epoch").conversation
        runs = {run["name"]: run for run in conversation["runs"]}
        self.assertEqual(sorted(runs), ["mira", "pax", "raya", "saro"])
        self.assertFalse(any(run["final"] for run in runs.values()))
        self.assertEqual(
            [(change["state"], change["at"]["time"]) for change in runs["pax"]["delivery"]],
            [
                ("initialising", "2026-10-08T15:32:19.017+02:00"),
                ("online", "2026-10-08T15:32:20.077+02:00"),
                ("error", "2026-10-08T15:35:25.491+02:00"),
                ("online", "2026-10-08T15:35:26.595+02:00"),
            ],
        )


class RealLogs(unittest.TestCase):
    """Two runs of a group's members in one process, cut to their records and the lines around."""

    def test_a_run_that_forked(self):
        reported = run_report(REAL / "run-split.log")
        conversation = reported.conversation
        self.assertEqual(conversation["id"], "6e05e5b37e")
        self.assertEqual(conversation["verdict"], "split at epoch 3")
        self.assertEqual(conversation["split_epoch"], 3)
        # A fifth installation, 6d0eb502, wrote a run record and is on no roster.
        self.assertEqual(
            positions(conversation),
            {"810d744e": "tip", "4e826d53": "tip", "602b850d": "tip", "4caf5f49": "removed"},
        )
        self.assertEqual(conversation["coverage"], {"sharing": 3, "seats": 3})
        self.assertEqual(
            conversation["branches"],
            [
                {
                    "label": "A",
                    "since": 3,
                    "epoch": 4,
                    "state": "a4a74167e563f44b",
                    "members": 2,
                    "installations": ["4e826d53", "810d744e"],
                },
                {
                    "label": "B",
                    "since": 3,
                    "epoch": 3,
                    "state": "9c8d750bcf2c225f",
                    "members": 3,
                    "installations": ["602b850d"],
                },
            ],
        )
        states = conversation["states"]
        self.assertEqual(
            [(state["epoch"], state["state"], state["branch"]) for state in states],
            [
                (1, "bf44f7091b545cb5", None),
                (2, "0e8ea0c2a11e1bd9", None),
                (3, "9c8d750bcf2c225f", "B"),
                (3, "a96639b2360375bf", "A"),
                (4, "a4a74167e563f44b", "A"),
            ],
        )
        self.assertEqual(
            [state["held_by"] for state in states],
            [
                ["4caf5f49", "4e826d53", "810d744e"],
                ["4caf5f49", "4e826d53", "602b850d", "810d744e"],
                ["602b850d"],
                ["4e826d53", "810d744e"],
                ["4e826d53", "810d744e"],
            ],
        )
        both_came_from = [{"epoch": 2, "state": "0e8ea0c2a11e1bd9"}]
        self.assertEqual([state["after"] for state in states[2:4]], [both_came_from] * 2)
        pax = seat_of(conversation, "4caf5f49")
        self.assertEqual((pax["account"], pax["epoch"], pax["state"]), ("706178", 3, None))
        self.assertIsNone(pax["branch"])
        self.assertEqual(steps(pax), [("epoch", 1), ("epoch", 2), ("left", 3)])
        self.assertEqual(reported.lines, counted(lines=50, records=18))
        self.assertEqual(reported.status, 1)

    def test_a_run_that_stayed_whole(self):
        reported = run_report(REAL / "run-whole.log")
        conversation = reported.conversation
        self.assertEqual((conversation["id"], conversation["verdict"]), ("a5bb59cd29", "whole"))
        self.assertIsNone(conversation["split_epoch"])
        # A fifth installation, 1afdfd4e, wrote a run record and is on no roster.
        self.assertEqual(
            positions(conversation),
            {"57d94add": "tip", "a36648f1": "tip", "58078287": "removed", "f219e159": "removed"},
        )
        self.assertEqual(conversation["coverage"], {"sharing": 2, "seats": 2})
        self.assertEqual(
            conversation["branches"],
            [
                {
                    "label": "A",
                    "since": 1,
                    "epoch": 4,
                    "state": "4410ce160df420ac",
                    "members": 2,
                    "installations": ["57d94add", "a36648f1"],
                }
            ],
        )
        states = conversation["states"]
        self.assertEqual(
            [(state["epoch"], state["state"], state["members"]) for state in states],
            [
                (1, "2addb4221853892f", 3),
                (2, "166dfc142b758ea7", 4),
                (3, "19d028ec02a67735", 3),
                (4, "4410ce160df420ac", 2),
            ],
        )
        mira = seat_of(conversation, "f219e159")
        self.assertEqual(steps(mira), [("epoch", 2), ("epoch", 3), ("left", 4)])
        self.assertEqual(reported.lines, counted(lines=53, records=19))
        self.assertEqual(reported.status, 0)

    def test_the_text_report(self):
        self.assertEqual(
            run_report(REAL / "run-split.log").text,
            "conversation 6e05e5b37e\n"
            "  split at epoch 3\n"
            "  3 of 3 seats share a log\n"
            "\n"
            "  branch A: epoch 4, state a4a74167e563f44b, 2 members\n"
            "    on it: 4e826d53, 810d744e\n"
            "  branch B: epoch 3, state 9c8d750bcf2c225f, 3 members\n"
            "    on it: 602b850d\n"
            "\n"
            "  installation  account   name       epoch  position  branch\n"
            "  4e826d53      72617961  run-split  4      tip       A\n"
            "  810d744e      7361726f  run-split  4      tip       A\n"
            "  602b850d      6d697261  run-split  3      tip       B\n"
            "  4caf5f49      706178    run-split  3      removed   -\n"
            "\n"
            "lines: 50 read, 18 records, 0 without a stamp, 0 malformed, 0 unknown\n",
        )

    def test_a_member_with_an_empty_account_hides_no_record(self):
        joined = re.sub(
            r"members=([0-9]+) roster=([0-9a-f:,]+)",
            lambda found: f"members={int(found[1]) + 1} roster={found[2]},{KES}:",
            (REAL / "run-split.log").read_text(),
        )
        with tempfile.TemporaryDirectory() as scratch:
            log = Path(scratch) / "run-split.log"
            log.write_text(joined)
            reported = run_report(log)
        self.assertEqual(reported.conversation["verdict"], "split at epoch 3")
        self.assertEqual(reported.lines, counted(lines=50, records=18))
        kes = seat_of(reported.conversation, KES)
        self.assertEqual((kes["account"], kes["position"]), ("", "no data"))
        self.assertIn("  b54a9d10      -         -          -      no data   -\n", reported.text)

    def test_a_copy_cut_inside_its_last_record_removes_nobody(self):
        lines = (REAL / "run-whole.log").read_bytes().split(b"\n")
        whole, last = lines[:48], lines[48]
        cut = last[: last.index(b",a36648f1:72617961")]
        self.assertIn(b"epoch installation=a36648f1 ", cut)
        self.assertTrue(cut.endswith(b"members=2 roster=57d94add:7361726f"))
        with tempfile.TemporaryDirectory() as scratch:
            log = Path(scratch) / "run-whole.log"
            log.write_bytes(b"".join(line + b"\n" for line in whole) + cut)
            reported = run_report(log)
        self.assertEqual(positions(reported.conversation)["a36648f1"], "behind")
        self.assertEqual(reported.lines["lines"], 48)
        self.assertEqual(reported.lines["malformed"], 0)

    def test_two_logs_are_two_conversations(self):
        reported = run_report(REAL)
        ids = [conversation["id"] for conversation in reported.conversations]
        self.assertEqual(ids, ["6e05e5b37e", "a5bb59cd29"])
        self.assertEqual(reported.lines, counted(lines=103, records=37))
        self.assertEqual(reported.status, 1)


class RunsSeenTwice(unittest.TestCase):
    def test_a_snapshot_beside_the_whole_log_changes_no_verdict_whatever_its_name(self):
        whole = (HISTORIES / "whole" / "group.log").read_bytes()
        earlier = b"".join(line + b"\n" for line in whole.split(b"\n")[:22])
        expected = run_report(HISTORIES / "whole")
        for name in ("group.snapshot.log", "earlier.log"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as scratch:
                (Path(scratch) / "group.log").write_bytes(whole)
                (Path(scratch) / name).write_bytes(earlier)
                reported = run_report(Path(scratch))
                self.assertEqual(reported.conversations, expected.conversations)
                self.assertEqual(reported.status, 0)
                self.assertEqual(reported.lines, counted(lines=54, records=20, unstamped=7))


class CommandLine(unittest.TestCase):
    def test_the_order_of_the_files_does_not_change_a_byte(self):
        files = sorted((HISTORIES / "split-at-an-epoch").glob("*.log"))
        expected = run_report(HISTORIES / "split-at-an-epoch")
        for order in itertools.permutations(files):
            self.assertEqual(run_report(*order), expected)

    def test_the_parts_of_a_run_may_be_given_in_any_order(self):
        history = HISTORIES / "behind-with-a-witness"
        files = sorted(history.glob("*.log"))
        self.assertEqual(run_report(*reversed(files)), run_report(history))

    def test_a_branch_of_many_is_wrapped(self):
        members = [(f"{index:08x}", f"{index + 256:08x}") for index in range(1, 13)]
        roster = ",".join(f"{installation}:{account}" for installation, account in members)
        text = report_of(
            *(
                "2026-10-09T06:54:50.313738Z  INFO libchat::groupv2trace: epoch "
                f"installation={installation} convo=2a30d6a2c1 epoch=1 state=b77d02c4a91e03d5 "
                f"members=12 roster={roster}"
                for installation, _ in members
            )
        ).text
        self.assertIn(
            "  branch A: epoch 1, state b77d02c4a91e03d5, 12 members\n"
            "    on it: 00000001, 00000002, 00000003, 00000004, 00000005, 00000006, 00000007, "
            "00000008, 00000009,\n"
            "      0000000a, 0000000b, 0000000c\n",
            text,
        )

    def test_a_branch_everyone_left(self):
        placed = "installation=a3f91c07 convo=2a30d6a2c1"
        reported = report_of(
            "2026-10-09T06:54:50.313738Z  INFO libchat::groupv2trace: "
            f"epoch {placed} epoch=1 state=b77d02c4a91e03d5 members=1 roster=a3f91c07:5be2c0d1",
            f"2026-10-09T06:54:51.207311Z  INFO libchat::groupv2trace: left {placed} epoch=2",
        )
        self.assertEqual(
            reported.text,
            "conversation 2a30d6a2c1\n"
            "  no live member\n"
            "  0 of 0 seats share a log\n"
            "\n"
            "  branch A: epoch 1, state b77d02c4a91e03d5, 1 member\n"
            "    on it: nobody\n"
            "\n"
            "  installation  account   name   epoch  position  branch\n"
            "  a3f91c07      5be2c0d1  group  2      removed   -\n"
            "\n"
            "lines: 2 read, 2 records, 0 without a stamp, 0 malformed, 0 unknown\n",
        )
        self.assertEqual(reported.status, 1)

    def test_a_malformed_line_is_said_after_the_conversations_and_exits_1(self):
        whole = [epoch_line(SARO), epoch_line(RAYA)]
        self.assertEqual(report_of(*whole).status, 0)
        cut = epoch_line(RAYA).replace("epoch=1", "epoch=2").replace(f",{RAYA}:91c0e3a7", "")
        reported = report_of(*whole, cut)
        self.assertEqual(reported.conversation["verdict"], "whole")
        self.assertEqual(reported.status, 1)
        self.assertTrue(
            reported.text.endswith(
                "  a3f91c07      5be2c0d1  group  1      tip       A\n"
                "\n"
                "1 malformed line was skipped: every verdict above was drawn without it\n"
                "\n"
                "lines: 3 read, 2 records, 0 without a stamp, 1 malformed, 0 unknown\n"
            )
        )
        self.assertIn(
            "\n2 malformed lines were skipped: every verdict above was drawn without them\n",
            report_of(*whole, cut, cut).text,
        )

    def test_an_unknown_record_is_a_count_and_changes_no_status(self):
        stamp = "2026-10-09T06:54:50.313738Z  INFO libchat::groupv2trace: "
        reported = report_of(epoch_line(SARO), epoch_line(RAYA), f"{stamp}commit hash=c41f")
        self.assertEqual(reported.status, 0)
        self.assertNotIn("skipped", reported.text)
        self.assertTrue(reported.text.endswith("0 malformed, 1 unknown\n"))

    def test_logs_without_a_conversation_do_not_read_whole(self):
        reported = report_of(
            "2026-10-08T16:02:11.004+02:00 INFO: chat_module::actions: delivery is online"
        )
        self.assertEqual(reported.status, 1)
        self.assertEqual(
            reported.text,
            "no conversation in these logs\n"
            "\n"
            "lines: 1 read, 0 records, 0 without a stamp, 0 malformed, 0 unknown\n",
        )
        self.assertEqual(reported.conversations, [])

    def failing(self, *arguments: str) -> str:
        complaint = io.StringIO()
        with contextlib.redirect_stderr(complaint), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(list(arguments)), 2)
        return complaint.getvalue()

    def test_a_log_that_cannot_be_read_exits_2(self):
        self.assertIn("nowhere.log", self.failing("report", str(FIXTURES / "nowhere.log")))

    def test_two_runs_under_one_name_exit_2(self):
        removed = [HISTORIES / "removed-by-left", HISTORIES / "removed-by-the-guard"]
        complaint = self.failing("report", *map(str, removed))
        self.assertIn("more than one run is named", complaint)

    def test_two_files_at_one_place_of_a_run_exit_2(self):
        with tempfile.TemporaryDirectory() as scratch:
            paths = [Path(scratch) / "saro.log", Path(scratch) / "saro.txt"]
            for path in paths:
                path.write_text(epoch_line(SARO) + "\n")
            complaint = self.failing("report", *map(str, paths))
        self.assertIn("two files are the same part of the run saro", complaint)

    def test_a_directory_without_logs_exits_2(self):
        with tempfile.TemporaryDirectory() as scratch:
            self.assertIn("holds no .log file", self.failing("report", scratch))

    def test_a_usage_error_exits_2(self):
        for arguments in ([], ["report"], ["mend", "saro.log"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    main(arguments)
                self.assertEqual(stopped.exception.code, 2)

    def test_the_tool_runs_from_its_directory(self):
        done = subprocess.run(
            [sys.executable, str(TOOL), "report", str(REAL / "run-whole.log")],
            capture_output=True,
            text=True,
        )
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertTrue(done.stdout.startswith("conversation a5bb59cd29\n  whole\n"))

    def test_the_tool_exits_with_the_status_of_its_command(self):
        split = [sys.executable, str(TOOL), "report", str(REAL / "run-split.log")]
        self.assertEqual(subprocess.run(split, capture_output=True).returncode, 1)

    def test_a_log_is_read_from_a_pipe(self):
        done = subprocess.run(
            [sys.executable, str(TOOL), "report", "/dev/stdin"],
            input=(REAL / "run-whole.log").read_bytes(),
            capture_output=True,
        )
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        self.assertTrue(done.stdout.startswith(b"conversation a5bb59cd29\n  whole\n"))
        self.assertIn(b"\n  57d94add      7361726f  stdin  4      tip       A\n", done.stdout)

    def test_a_run_named_after_a_file_whose_name_is_not_utf8_is_printed(self):
        with tempfile.TemporaryDirectory() as scratch:
            log = os.path.join(os.fsencode(scratch), b"kes\xff.log")
            try:
                with open(log, "w") as file:
                    file.write(epoch_line(SARO) + "\n" + epoch_line(RAYA) + "\n")
            except OSError:
                self.skipTest("this file system takes no name that is not UTF-8")
            done = subprocess.run(
                [sys.executable, str(TOOL), "report", scratch],
                capture_output=True,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        self.assertIn(b"\n  a3f91c07      5be2c0d1  kes\\xff  1      tip       A\n", done.stdout)

    def test_a_name_the_output_encoding_cannot_write_is_escaped(self):
        written = {
            "utf-8": "jos\u00e9_\u65e5".encode(),
            "latin-1": b"jos\xe9_\\u65e5",
            "ascii": b"jos\\xe9_\\u65e5",
        }
        with tempfile.TemporaryDirectory() as scratch:
            log = Path(scratch) / "jos\u00e9_\u65e5.log"
            log.write_text(epoch_line(SARO) + "\n" + epoch_line(RAYA) + "\n")
            for encoding, name in written.items():
                with self.subTest(encoding=encoding):
                    done = subprocess.run(
                        [sys.executable, str(TOOL), "report", scratch],
                        capture_output=True,
                        env={**os.environ, "PYTHONIOENCODING": encoding},
                    )
                    self.assertEqual((done.returncode, done.stderr), (0, b""))
                    self.assertIn(
                        b"\n  a3f91c07      5be2c0d1  " + name + b"  1      tip       A\n",
                        done.stdout,
                    )


if __name__ == "__main__":
    unittest.main()
