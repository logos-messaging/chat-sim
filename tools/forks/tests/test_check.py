import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List, Tuple

from forks.cli import main
from forks.rule import POSITIONS

from . import FIXTURES, run_report

HISTORIES = FIXTURES / "histories"
TOOL = Path(__file__).resolve().parents[1]

SARO, RAYA, PAX, MIRA = "a3f91c07", "7b1e44d9", "e09a3f52", "19f6b0e3"
KES, TOMO = "b54a9d10", "4d7c21be"
CONVO = "2a30d6a2c1"
ROSTER = f"{SARO}:5be2c0d1,{RAYA}:91c0e3a7,{PAX}:0d7a66f3"


def of_history(directory: Path) -> dict:
    """The expectation a history's `expect.json` comes to, for the conversation its logs hold."""
    expect = json.loads((directory / "expect.json").read_text())
    return {
        "conversation": run_report(directory).conversation["id"],
        "verdict": expect["verdict"],
        "installations": {
            person["installation"]: {"name": name, "position": person["position"]}
            for name, person in expect["positions"].items()
        },
    }


def record(second: int, kind: str, installation: str, rest: str) -> str:
    return (
        f"2026-10-09T06:54:{second:02d}.000000Z  INFO libchat::groupv2trace: {kind} "
        f"installation={installation} convo={CONVO} {rest}"
    )


def held(second: int, installation: str) -> str:
    """An installation's record of the one state of a group of Saro, Raya and Pax."""
    return record(
        second, "epoch", installation, f"epoch=1 state=b77d02c4a91e03d5 members=3 roster={ROSTER}"
    )


# A group of three: Pax posts twice and Saro once. Raya reads Pax's first post and Saro's, and
# cannot read Pax's second.
POSTS = [
    held(1, SARO),
    held(2, RAYA),
    held(3, PAX),
    record(10, "sent", PAX, "frame=aa01 epoch=1"),
    record(11, "received", SARO, f"frame=aa01 from={PAX} epoch=1"),
    record(12, "received", RAYA, f"frame=aa01 from={PAX} epoch=1"),
    record(20, "sent", SARO, "frame=bb01 epoch=1"),
    record(21, "received", RAYA, f"frame=bb01 from={SARO} epoch=1"),
    record(22, "received", PAX, f"frame=bb01 from={SARO} epoch=1"),
    record(30, "sent", PAX, "frame=aa02 epoch=1"),
    record(31, "received", SARO, f"frame=aa02 from={PAX} epoch=1"),
    record(32, "unreadable", RAYA, "frame=aa02 epoch=1 why=decrypt"),
]


def three(**changed) -> dict:
    """What the report of `POSTS` holds, as an expectation."""
    whole = {
        "conversation": CONVO,
        "verdict": "whole",
        "installations": {
            SARO: {"name": "saro", "position": "tip"},
            RAYA: {"name": "raya", "position": "tip"},
            PAX: {"name": "pax", "position": "tip"},
        },
        "posts": [
            {"sender": PAX, "cells": {SARO: "read", RAYA: "read", PAX: "sent"}},
            {"sender": SARO, "cells": {SARO: "sent", RAYA: "read", PAX: "read"}},
            {"sender": PAX, "cells": {SARO: "read", RAYA: "unreadable", PAX: "sent"}},
        ],
    }
    whole.update(changed)
    return whole


class Checking(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)

    def expecting(self, expectation: object) -> Path:
        file = self.scratch / "expect.json"
        file.write_text(expectation if isinstance(expectation, str) else json.dumps(expectation))
        return file

    def check(self, expectation: object, *paths: Path) -> Tuple[int, List[str], str]:
        """Runs `forks check` the way the command line does: its status, its lines, its errors."""
        printed, complained = io.StringIO(), io.StringIO()
        arguments = ["check", "--expect", str(self.expecting(expectation)), *map(str, paths)]
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(complained):
            status = main(arguments)
        return status, printed.getvalue().splitlines(), complained.getvalue()

    def logs(self, *lines: str, name: str = "group") -> Path:
        log = self.scratch / f"{name}.log"
        log.write_text("".join(line + "\n" for line in lines))
        return log

    def differing(self, expectation: dict, *paths: Path) -> List[str]:
        status, lines, complained = self.check(expectation, *paths)
        self.assertEqual((status, complained), (1, ""), lines)
        return lines

    def refused(self, expectation: object, *paths: Path) -> str:
        status, lines, complained = self.check(expectation, *(paths or [HISTORIES / "whole"]))
        self.assertEqual((status, lines), (2, []), complained)
        return complained


class Histories(Checking):
    """Each history of the fixtures, held against what its `expect.json` says and against less."""

    def histories(self) -> List[Path]:
        return sorted(HISTORIES.iterdir())

    def test_each_reads_as_expected_by_its_verdict_and_its_positions(self):
        for directory in self.histories():
            with self.subTest(history=directory.name):
                expectation = of_history(directory)
                self.assertEqual(
                    self.check(expectation, directory),
                    (0, [f"conversation {expectation['conversation']}: as expected"], ""),
                )

    def test_the_histories_hold_every_verdict_and_position_a_file_of_logs_can_show(self):
        verdicts, positions = set(), set()
        for directory in self.histories():
            expectation = of_history(directory)
            verdicts.add(expectation["verdict"].rstrip("0123456789"))
            positions.update(seat["position"] for seat in expectation["installations"].values())
        self.assertEqual(
            verdicts,
            {
                "whole",
                "whole, members behind",
                "whole as far as seen",
                "not enough data",
                "split at epoch ",
                "split at epoch 2, one side live",
                "split at epoch 3, one side live",
                "split seen from epoch ",
                "split seen from epoch 2, one side live",
            },
        )
        # No file says its writer is gone, so no log read from a file shows `ended`.
        self.assertEqual(positions, set(POSITIONS) - {"ended"})

    def test_another_verdict_is_one_difference(self):
        for directory in self.histories():
            with self.subTest(history=directory.name):
                expectation = of_history(directory)
                verdict = expectation["verdict"]
                expectation["verdict"] = "no live member"
                self.assertEqual(
                    self.differing(expectation, directory),
                    [f"verdict: {verdict}, expected no live member"],
                )

    def test_another_position_is_a_difference_for_its_installation_alone(self):
        for directory in self.histories():
            expectation = of_history(directory)
            for installation, seat in expectation["installations"].items():
                with self.subTest(history=directory.name, installation=installation):
                    position = seat["position"]
                    other = "ended" if position != "ended" else "tip"
                    changed = dict(expectation["installations"], **{installation: dict(seat)})
                    changed[installation]["position"] = other
                    self.assertEqual(
                        self.differing(dict(expectation, installations=changed), directory),
                        [f"{seat['name']} ({installation}): {position}, expected {other}"],
                    )

    def test_an_installation_the_file_leaves_out_is_a_difference(self):
        for directory in self.histories():
            with self.subTest(history=directory.name):
                expectation = of_history(directory)
                installation, seat = sorted(expectation["installations"].items())[0]
                del expectation["installations"][installation]
                self.assertEqual(
                    self.differing(expectation, directory),
                    [f"{installation}: {seat['position']}, not expected"],
                )

    def test_an_installation_the_report_does_not_list_is_a_difference(self):
        for directory in self.histories():
            with self.subTest(history=directory.name):
                expectation = of_history(directory)
                expectation["installations"]["0badc0de"] = {"name": "tomo", "position": "tip"}
                self.assertEqual(
                    self.differing(expectation, directory),
                    ["tomo (0badc0de): not in the report, expected tip"],
                )


class Differences(Checking):
    def test_a_group_with_a_member_behind_held_against_a_whole_one_is_a_line_a_difference(self):
        history = HISTORIES / "behind-with-a-witness"
        expectation = of_history(history)
        expectation["verdict"] = "whole"
        for seat in expectation["installations"].values():
            seat["position"] = "tip"

        self.assertEqual(
            self.differing(expectation, history),
            [
                "verdict: whole, members behind, expected whole",
                f"pax ({PAX}): behind, expected tip",
            ],
        )

    def test_a_verdict_in_another_case_is_a_difference(self):
        self.assertEqual(
            self.differing(three(verdict="Whole"), self.logs(*POSTS)),
            ["verdict: whole, expected Whole"],
        )

    def test_what_the_file_expects_is_said_before_what_it_does_not(self):
        expectation = of_history(HISTORIES / "whole")
        del expectation["installations"][SARO]
        expectation["installations"]["0badc0de"] = {"name": "tomo", "position": "tip"}

        self.assertEqual(
            self.differing(expectation, HISTORIES / "whole"),
            ["tomo (0badc0de): not in the report, expected tip", f"{SARO}: tip, not expected"],
        )

    def test_an_installation_without_a_name_goes_by_its_id(self):
        expectation = of_history(HISTORIES / "whole")
        expectation["installations"][PAX] = {"position": "behind"}

        self.assertEqual(
            self.differing(expectation, HISTORIES / "whole"), [f"{PAX}: tip, expected behind"]
        )

    def test_the_posts_of_the_fixtures_read_as_the_report_draws_them(self):
        history = HISTORIES / "split-at-an-epoch"
        expectation = of_history(history)
        everyone = {SARO: "read", RAYA: "read", PAX: "read", MIRA: "read"}
        expectation["posts"] = [
            {"sender": SARO, "cells": dict(everyone, **{SARO: "sent"})},
            {"sender": RAYA, "cells": dict(everyone, **{RAYA: "sent", PAX: "unreadable"})},
            {"sender": PAX, "cells": dict(everyone, **{PAX: "sent"})},
        ]

        self.assertEqual(
            self.differing(expectation, history),
            [
                f"post 1 of pax ({PAX}) on saro ({SARO}): unreadable, expected read",
                f"post 1 of pax ({PAX}) on raya ({RAYA}): unreadable, expected read",
                f"post 1 of pax ({PAX}) on mira ({MIRA}): unreadable, expected read",
            ],
        )

    def test_posts_as_expected_are_no_difference(self):
        self.assertEqual(
            self.check(three(), self.logs(*POSTS)),
            (0, [f"conversation {CONVO}: as expected"], ""),
        )

    def test_a_post_is_matched_by_its_sender_and_its_order_among_that_senders_posts(self):
        expectation = three()
        # Pax's two posts the other way round: the one Raya cannot read is expected first.
        expectation["posts"].reverse()

        self.assertEqual(
            self.differing(expectation, self.logs(*POSTS)),
            [
                f"post 1 of pax ({PAX}) on raya ({RAYA}): read, expected unreadable",
                f"post 2 of pax ({PAX}) on raya ({RAYA}): unreadable, expected read",
            ],
        )

    def test_two_posts_a_sender_wrote_at_one_stamp_are_in_the_order_it_wrote_them(self):
        # Pax writes two posts within one stamp, the first under the greater frame id. Raya
        # reads the first and not the second.
        lines = [
            *POSTS[:3],
            record(10, "sent", PAX, "frame=ff01 epoch=1"),
            record(10, "sent", PAX, "frame=aa02 epoch=1"),
            record(11, "received", RAYA, f"frame=ff01 from={PAX} epoch=1"),
            record(12, "unreadable", RAYA, "frame=aa02 epoch=1 why=decrypt"),
        ]
        posts = [
            {"sender": PAX, "cells": {RAYA: "read"}},
            {"sender": PAX, "cells": {RAYA: "unreadable"}},
        ]

        self.assertEqual(
            self.check(three(posts=posts), self.logs(*lines)),
            (0, [f"conversation {CONVO}: as expected"], ""),
        )
        self.assertEqual(
            self.differing(three(posts=posts[::-1]), self.logs(*lines)),
            [
                f"post 1 of pax ({PAX}) on raya ({RAYA}): read, expected unreadable",
                f"post 2 of pax ({PAX}) on raya ({RAYA}): unreadable, expected read",
            ],
        )

    def test_the_posts_of_a_sender_are_counted_through_every_log_given(self):
        # Pax's second post and what became of it are in a log of their own.
        first, second = self.logs(*POSTS[:9], name="first"), self.logs(*POSTS[9:], name="second")

        self.assertEqual(self.check(three(), first, second)[0], 0)
        self.assertEqual(self.check(three(), second, first)[0], 0)
        self.assertEqual(
            self.differing(three(), first), [f"post 2 of pax ({PAX}): not in the report"]
        )

    def test_a_cell_of_an_installation_the_report_lacks_is_a_difference(self):
        expectation = three()
        expectation["installations"][TOMO] = {"name": "tomo", "position": "no data"}
        expectation["posts"][0]["cells"][TOMO] = "no log"

        self.assertEqual(
            self.differing(expectation, self.logs(*POSTS)),
            [
                f"tomo ({TOMO}): not in the report, expected no data",
                f"post 1 of pax ({PAX}) on tomo ({TOMO}): not in the report, expected no log",
            ],
        )

    def test_a_post_unreadable_for_its_senders_account_says_so(self):
        unchecked = self.logs(*(line.replace("why=decrypt", "why=sender") for line in POSTS))
        expectation = three()
        expectation["posts"][2]["cells"][RAYA] = "read"

        self.assertEqual(
            self.differing(expectation, unchecked),
            [
                f"post 2 of pax ({PAX}) on raya ({RAYA}): unreadable since the sender's account "
                "could not be checked, expected read"
            ],
        )
        self.assertEqual(self.check(three(), unchecked)[0], 0)

    def test_a_frame_under_another_id_is_the_same_post(self):
        renamed = [line.replace("frame=aa0", "frame=cc0") for line in POSTS]

        self.assertEqual(self.check(three(), self.logs(*renamed))[0], 0)

    def test_what_became_of_a_post_is_held_in_each_of_the_reports_words(self):
        # Pax posts to a group of five. Saro reads the post and Raya cannot. Tomo shares a log
        # that holds nothing of it, and Mira shares none. Kes joins after it was sent.
        five = f"{ROSTER},{MIRA}:d2184c8e,{TOMO}:6f0e1d2c"
        sharing = (SARO, RAYA, PAX, TOMO)
        lines = [
            record(at, "epoch", seat, f"epoch=1 state=b77d02c4a91e03d5 members=5 roster={five}")
            for at, seat in enumerate(sharing, 1)
        ]
        lines += [
            record(10, "sent", PAX, "frame=aa01 epoch=1"),
            record(11, "received", SARO, f"frame=aa01 from={PAX} epoch=1"),
            record(12, "unreadable", RAYA, "frame=aa01 epoch=1 why=decrypt"),
        ]
        lines += [
            record(
                at,
                "epoch",
                seat,
                f"epoch=2 state=0c8e3f5a7b9d1e24 members=6 roster={five},{KES}:aa0b1c2d",
            )
            for at, seat in enumerate(sharing, 20)
        ]
        became = {
            PAX: "sent",
            SARO: "read",
            RAYA: "unreadable",
            TOMO: "no record",
            MIRA: "no log",
            KES: "not a member",
        }
        expectation = {
            "conversation": CONVO,
            "verdict": "whole",
            "installations": {
                seat: {"position": "tip" if seat in sharing else "no data"} for seat in became
            },
            "posts": [{"sender": PAX, "cells": became}],
        }
        self.assertEqual(self.check(expectation, self.logs(*lines))[0], 0)

        # Each installation is now expected to hold what the next one does.
        seats = list(became)
        expectation["posts"][0]["cells"] = {
            seat: became[other] for seat, other in zip(seats, seats[1:] + seats[:1])
        }
        self.assertEqual(
            self.differing(expectation, self.logs(*lines)),
            [
                f"post 1 of {PAX} on {PAX}: sent, expected read",
                f"post 1 of {PAX} on {SARO}: read, expected unreadable",
                f"post 1 of {PAX} on {RAYA}: unreadable, expected no record",
                f"post 1 of {PAX} on {TOMO}: no record, expected no log",
                f"post 1 of {PAX} on {MIRA}: no log, expected not a member",
                f"post 1 of {PAX} on {KES}: not a member, expected sent",
            ],
        )

    def test_an_installation_a_post_leaves_out_is_not_held_for_it(self):
        expectation = three()
        for post in expectation["posts"]:
            del post["cells"][RAYA]

        self.assertEqual(self.check(expectation, self.logs(*POSTS))[0], 0)

    def test_a_post_the_report_does_not_hold_and_one_nobody_expected(self):
        expectation = three()
        expectation["posts"].append({"sender": RAYA, "cells": {SARO: "read"}})
        del expectation["posts"][2]

        self.assertEqual(
            self.differing(expectation, self.logs(*POSTS)),
            [
                f"post 1 of raya ({RAYA}): not in the report",
                f"post 2 of pax ({PAX}): in the report, not expected",
            ],
        )

    def test_a_file_without_posts_holds_nothing_of_them_and_one_with_none_expects_none(self):
        without_posts = {field: held for field, held in three().items() if field != "posts"}
        self.assertEqual(self.check(without_posts, self.logs(*POSTS))[0], 0)
        self.assertEqual(
            self.differing(three(posts=[]), self.logs(*POSTS)),
            [
                f"post 1 of pax ({PAX}): in the report, not expected",
                f"post 2 of pax ({PAX}): in the report, not expected",
                f"post 1 of saro ({SARO}): in the report, not expected",
            ],
        )

    def test_a_conversation_the_logs_do_not_hold_is_a_difference(self):
        self.assertEqual(
            self.differing(three(conversation="0f0f0f0f0f"), self.logs(*POSTS)),
            ["conversation 0f0f0f0f0f: not in the logs"],
        )

    def test_another_conversation_in_the_logs_is_not_held_against_the_file(self):
        other = [line.replace(CONVO, "0f0f0f0f0f") for line in POSTS[:2]]

        self.assertEqual(self.check(three(), self.logs(*POSTS, *other))[0], 0)

    def test_a_malformed_record_is_a_difference_of_a_report_as_expected(self):
        cut = held(50, RAYA).replace("epoch=1", "epoch=2").replace(f",{PAX}:0d7a66f3", "")

        self.assertEqual(
            self.differing(three(), self.logs(*POSTS, cut)),
            ["1 malformed line was skipped: the report was drawn without it"],
        )
        self.assertEqual(
            self.differing(three(verdict="whole, members behind"), self.logs(*POSTS, cut, cut)),
            [
                "verdict: whole, expected whole, members behind",
                "2 malformed lines were skipped: the report was drawn without them",
            ],
        )

    def test_a_malformed_record_of_any_log_given_is_a_difference(self):
        cut = held(50, RAYA).replace("epoch=1", "epoch=2").replace(f",{PAX}:0d7a66f3", "")
        whole, other = self.logs(*POSTS), self.logs(cut, name="other")

        for paths in ((whole, other), (other, whole)):
            self.assertEqual(
                self.differing(three(), *paths),
                ["1 malformed line was skipped: the report was drawn without it"],
            )


class Refusals(Checking):
    """A file that is no expectation, and logs that cannot be read: status 2 and one line."""

    def test_a_file_that_is_no_expectation_is_refused_by_what_is_wrong_with_it(self):
        def without(field: str) -> dict:
            return {key: value for key, value in three().items() if key != field}

        def seat(**held) -> dict:
            return three(installations={SARO: held})

        def post(**held) -> dict:
            return three(posts=[held])

        not_json = "is not JSON, or says a field twice"
        refusals: List[Tuple[str, object]] = [
            (not_json, "{"),
            (not_json, '{"verdict": "whole", "verdict": "whole"}'),
            (not_json, "[" * 100_000),
            ("the expectation is not an object", [three()]),
            ("the expectation lacks `conversation`", without("conversation")),
            ("the expectation lacks `verdict`", without("verdict")),
            ("the expectation lacks `installations`", without("installations")),
            ("the expectation holds an unknown field, `positions`", three(positions={})),
            (
                "the expectation holds an unknown field, `\\x1b[31mred\\x00\\x07`",
                three(**{"\x1b[31mred\x00\x07": 1}),
            ),
            ("`conversation` of the expectation is not a text", three(conversation=7)),
            (
                "`conversation` of the expectation holds a character that is not printed",
                three(conversation="\x1b[2J"),
            ),
            ("`verdict` of the expectation is not a text", three(verdict="")),
            (
                "`verdict` of the expectation holds a character that is not printed",
                three(verdict="whole\r"),
            ),
            ("`installations` is not an object", three(installations=[SARO])),
            (
                "`saro` is no installation: 8 hex characters name one",
                three(installations={"saro": {"position": "tip"}}),
            ),
            (
                f"`{SARO}0` is no installation: 8 hex characters name one",
                three(installations={SARO + "0": {"position": "tip"}}),
            ),
            (
                f"`{SARO.upper()}` is no installation: 8 hex characters name one",
                three(installations={SARO.upper(): {"position": "tip"}}),
            ),
            (
                "`\\x1b[2J\\u202e` is no installation: 8 hex characters name one",
                three(installations={"\x1b[2J\u202e": {"position": "tip"}}),
            ),
            (f"the installation {SARO} is not an object", three(installations={SARO: "tip"})),
            (f"the installation {SARO} lacks `position`", seat(name="saro")),
            (
                f"the installation {SARO} holds an unknown field, `epoch`",
                seat(position="tip", epoch=3),
            ),
            (f"`position` of the installation {SARO} is not a text", seat(position=1)),
            (
                f"`at the tip` of the installation {SARO} is no position",
                seat(position="at the tip"),
            ),
            (f"`Tip` of the installation {SARO} is no position", seat(position="Tip")),
            (f"` tip` of the installation {SARO} is no position", seat(position=" tip")),
            (
                f"`position` of the installation {SARO} holds a character that is not printed",
                seat(position="tip\x07"),
            ),
            (
                f"`name` of the installation {SARO} is not a text",
                seat(position="tip", name=["saro"]),
            ),
            (
                f"`name` of the installation {SARO} holds a character that is not printed",
                seat(position="tip", name=f"pax\nconversation {CONVO}: as expected"),
            ),
            (
                f"`name` of the installation {SARO} holds a character that is not printed",
                seat(position="tip", name="\u202e\u2066pax"),
            ),
            ("`posts` is not a list", three(posts={})),
            ("post 1 is not an object", three(posts=[SARO])),
            ("post 1 lacks `sender`", post(cells={})),
            ("post 1 lacks `cells`", post(sender=SARO)),
            ("post 1 holds an unknown field, `text`", post(sender=SARO, cells={}, text="hello")),
            ("the sender of post 1 is not among `installations`", post(sender=MIRA, cells={})),
            ("the sender of post 1 is not among `installations`", post(sender=[SARO], cells={})),
            ("the sender of post 1 is not among `installations`", post(sender={SARO: 1}, cells={})),
            ("`cells` of post 1 is not an object", post(sender=SARO, cells=["read"])),
            (
                f"`{MIRA}` in post 1 is not among `installations`",
                post(sender=SARO, cells={MIRA: "read"}),
            ),
            (
                f"`\\u202e{MIRA}` in post 1 is not among `installations`",
                post(sender=SARO, cells={"\u202e" + MIRA: "read"}),
            ),
            (
                "`seen` in post 1 is not what a report says of a post",
                post(sender=SARO, cells={RAYA: "seen"}),
            ),
            (
                "`read\\n\\x1b[0m` in post 1 is not what a report says of a post",
                post(sender=SARO, cells={RAYA: "read\n\x1b[0m"}),
            ),
        ]
        file = self.scratch / "expect.json"
        for why, expectation in refusals:
            with self.subTest(why=why, expectation=expectation):
                separator = " " if why == not_json else ": "
                self.assertEqual(self.refused(expectation), f"forks: {file}{separator}{why}\n")

    def test_a_file_that_is_not_there_is_refused(self):
        printed, complained = io.StringIO(), io.StringIO()
        nowhere = self.scratch / "nowhere.json"
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(complained):
            status = main(["check", "--expect", str(nowhere), str(HISTORIES / "whole")])

        self.assertEqual((status, printed.getvalue()), (2, ""))
        self.assertIn(str(nowhere), complained.getvalue())

    def test_logs_that_cannot_be_read_are_refused(self):
        self.assertIn("nowhere.log", self.refused(three(), self.scratch / "nowhere.log"))
        self.assertIn("holds no .log file", self.refused(three(), self.scratch))

    def test_a_command_line_without_an_expectation_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            main(["check", str(HISTORIES / "whole")])
        self.assertEqual(stopped.exception.code, 2)

    def test_the_tool_exits_with_the_status_of_the_check(self):
        whole = of_history(HISTORIES / "whole")
        for expectation, status in ((whole, 0), (dict(whole, verdict="not enough data"), 1)):
            done = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "check",
                    "--expect",
                    str(self.expecting(expectation)),
                    str(HISTORIES / "whole"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual((done.returncode, done.stderr), (status, ""), done.stdout)


if __name__ == "__main__":
    unittest.main()
