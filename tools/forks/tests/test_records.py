import unittest
from typing import Optional

from forks.records import MAX_CONVO, MAX_NUMBER, Counts, instant, read_complete_lines, read_lines

TARGET = "libchat::mls_trace"
ROSTER = "a3f91c07:5be2c0d1,7b1e44d9:91c0e3a7"
# The op that writes a record names its signer and its conversation, and the epoch it began in.
OP = "groupv2.handle_frame{user_id=saro signer=a3f91c07 convo=2a30d6a2c1 epoch=6}: "
EPOCH = f"epoch epoch=7 state=b77d02c4a91e03d5 members=2 signers={ROSTER}"
LEFT = "left epoch=8"
RUN = "run signer=810d744e participant=7361726f"
APPLIED = "commit=c0c1c2c3c4c5c6c7 by=7b1e44d9"


def tracing(message: str, target: str = TARGET, spans: Optional[str] = None) -> str:
    """A line as tracing's formatter writes it; a record sits in its op's span unless told not."""
    return f"2026-10-09T06:54:50.313738Z  INFO {_spans(spans, target)}{target}: {message}"


def module(
    message: str, target: str = TARGET, offset: str = "+02:00", spans: Optional[str] = None
) -> str:
    """A line as chat-module writes it, with the span prefixes tracing's formatter writes."""
    return f"2026-10-08T16:02:11.004{offset} INFO: {_spans(spans, target)}{target}: {message}"


def _spans(spans: Optional[str], target: str) -> str:
    if spans is not None:
        return spans
    return OP if target == TARGET else ""


def read(*lines: str):
    return read_lines("".join(line + "\n" for line in lines).encode(), "saro")


def only(line: str):
    reading = read(line)
    assert reading.counts == Counts(lines=1, records=1), reading.counts
    return reading.records[0]


class LineShapes(unittest.TestCase):
    def test_tracing_stamps_utc(self):
        record = only(tracing(EPOCH))
        self.assertEqual((record.time, record.has_offset), ("2026-10-09T06:54:50.313738Z", True))
        self.assertEqual((record.run, record.line, record.kind), ("saro", 1, "epoch"))

    def test_chat_module_stamps_local_time_without_an_offset(self):
        record = only(module(EPOCH, offset=""))
        self.assertEqual((record.time, record.has_offset), ("2026-10-08T16:02:11.004", False))

    def test_chat_module_stamps_local_time_with_an_offset(self):
        record = only(module(EPOCH))
        self.assertEqual(
            (record.time, record.has_offset), ("2026-10-08T16:02:11.004+02:00", True)
        )

    def test_every_shape_gives_the_same_fields(self):
        # The op began at epoch 6; the record is of the epoch it reached.
        for line in (tracing(EPOCH), module(EPOCH, offset=""), module(EPOCH)):
            with self.subTest(line=line):
                record = only(line)
                self.assertEqual(record.installation, "a3f91c07")
                self.assertEqual(record.convo, "2a30d6a2c1")
                self.assertEqual(
                    (record.epoch, record.state, record.members), (7, "b77d02c4a91e03d5", 2)
                )
                self.assertEqual(
                    record.roster, (("a3f91c07", "5be2c0d1"), ("7b1e44d9", "91c0e3a7"))
                )

    def test_colour_codes_are_stripped(self):
        dim, bold, italic, green, off = "\x1b[2m", "\x1b[1m", "\x1b[3m", "\x1b[32m", "\x1b[0m"
        coloured = (
            f"{dim}2026-10-09T12:17:37.592411Z{off} {green} INFO{off} "
            f"{bold}core.handle_frame{off}{bold}{{{off}{italic}user_id{off}{dim}={off}raya"
            f"{bold}}}{off}{dim}:{off}{bold}groupv2.handle_frame{off}{bold}{{{off}"
            f"{italic}signer{off}{dim}={off}7b1e44d9 {italic}convo{off}{dim}={off}2a30d6a2c1"
            f"{bold}}}{off}{dim}:{off} {dim}libchat::mls_trace{off}{dim}:{off} left "
            f"{italic}epoch{off}{dim}={off}8"
        )
        record = only(coloured)
        self.assertEqual((record.kind, record.installation, record.epoch), ("left", "7b1e44d9", 8))
        self.assertEqual(record.time, "2026-10-09T12:17:37.592411Z")

    def test_span_prefixes_come_before_the_target(self):
        prefixes = [
            OP,
            f"core.handle_frame{{user_id=raya}}:{OP}",
            "groupv2.wakeup{user_id=raya with a space signer=a3f91c07 convo=2a30d6a2c1}: ",
            f"main_loop:{OP}",
            f"main_loop:core.handle_frame{{user_id=raya}}:{OP}",
        ]
        for prefix in prefixes:
            for line in (tracing(EPOCH, spans=prefix), module(EPOCH, spans=prefix)):
                with self.subTest(line=line):
                    self.assertEqual(only(line).kind, "epoch")

    def test_the_signer_and_the_conversation_are_the_innermost_spans_that_name_them(self):
        welcome = (
            "core.handle_frame{user_id=raya}:inboxV2.handle_frame{user_id=raya}:"
            "groupv2.new_from_welcome{user_id=raya signer=7b1e44d9 convo=2a30d6a2c1}: "
        )
        outer = "a{signer=a3f91c07 convo=9e04b7c61d}:b{user_id=raya}:"
        expected = {
            welcome: ("7b1e44d9", "2a30d6a2c1"),
            f"{outer}c{{signer=7b1e44d9}}: ": ("7b1e44d9", "9e04b7c61d"),
            f"{outer}c{{convo=2a30d6a2c1}}: ": ("a3f91c07", "2a30d6a2c1"),
        }
        for spans, placed in expected.items():
            with self.subTest(spans=spans):
                record = only(tracing(LEFT, spans=spans))
                self.assertEqual((record.installation, record.convo), placed)

    def test_every_level_is_read(self):
        for level in ("TRACE", "DEBUG", " WARN", "ERROR"):
            line = f"2026-10-09T06:54:50.327025Z {level} {OP}libchat::mls_trace: {EPOCH}"
            self.assertEqual(only(line).kind, "epoch")
        line = f"2026-10-08T16:02:11.004 WARNING: {OP}libchat::mls_trace: {EPOCH}"
        self.assertEqual(only(line).kind, "epoch")


class TheSevenRecords(unittest.TestCase):
    def test_run(self):
        record = only(tracing(RUN, spans=""))
        self.assertEqual(
            (record.kind, record.installation, record.account), ("run", "810d744e", "7361726f")
        )

    def test_an_account_shorter_than_four_bytes(self):
        record = only(tracing("run signer=4caf5f49 participant=706178", spans=""))
        self.assertEqual(record.account, "706178")

    def test_an_account_of_no_bytes(self):
        record = only(tracing("run signer=4caf5f49 participant=", spans=""))
        self.assertEqual((record.installation, record.account), ("4caf5f49", ""))
        listed = only(tracing(EPOCH.replace("91c0e3a7", "")))
        self.assertEqual(listed.roster, (("a3f91c07", "5be2c0d1"), ("7b1e44d9", "")))

    def test_delivery(self):
        for state in ("initialising", "online", "error", "stopped"):
            record = only(module(f"delivery state={state}", target="chat_module::report"))
            self.assertEqual(
                (record.kind, record.state, record.installation), ("delivery", state, None)
            )

    def test_epoch(self):
        record = only(module(EPOCH))
        self.assertEqual((record.kind, record.epoch, record.members), ("epoch", 7, 2))
        self.assertEqual(record.state, "b77d02c4a91e03d5")

    def test_an_epoch_names_the_commit_it_was_reached_by_and_who_committed_it(self):
        for line in (tracing(f"{EPOCH} {APPLIED}"), module(f"{EPOCH} {APPLIED}")):
            with self.subTest(line=line):
                record = only(line)
                self.assertEqual((record.commit, record.by), ("c0c1c2c3c4c5c6c7", "7b1e44d9"))
                self.assertEqual((record.kind, record.epoch, record.members), ("epoch", 7, 2))

    def test_an_epoch_no_commit_was_applied_to_reach_names_none(self):
        record = only(tracing(EPOCH))
        self.assertEqual((record.commit, record.by), (None, None))

    def test_left(self):
        record = only(module(LEFT))
        self.assertEqual((record.kind, record.convo, record.epoch), ("left", "2a30d6a2c1", 8))

    def test_sent(self):
        record = only(module("sent epoch=7 frame=41d6"))
        self.assertEqual((record.kind, record.frame, record.epoch), ("sent", "41d6", 7))

    def test_received(self):
        record = only(module("received epoch=7 frame=41d6 from=e09a3f52"))
        self.assertEqual(
            (record.kind, record.frame, record.sender), ("received", "41d6", "e09a3f52")
        )

    def test_unreadable(self):
        for why in ("epoch", "decrypt", "sender"):
            record = only(module(f"unreadable epoch=7 frame=e3b9 why={why}"))
            self.assertEqual((record.kind, record.frame, record.why), ("unreadable", "e3b9", why))

    def test_a_conversation_id_stays_as_written(self):
        record = only(tracing(LEFT, spans=OP.replace("2a30d6a2c1", "a%20b%3dc%0ad%25")))
        self.assertEqual(record.convo, "a%20b%3dc%0ad%25")

    def test_a_roster_of_a_hundred(self):
        members = [(f"{index:08x}", f"{index + 1000:08x}") for index in range(100)]
        roster = ",".join(f"{installation}:{account}" for installation, account in members)
        record = only(tracing(f"epoch epoch=7 state=b77d02c4a91e03d5 members=100 signers={roster}"))
        self.assertEqual(record.roster, tuple(members))

    def test_a_field_the_tool_does_not_know_is_ignored(self):
        record = only(tracing(f"{EPOCH} tree=c41f signed=a3f91c07"))
        self.assertEqual((record.kind, record.epoch), ("epoch", 7))

    def test_a_commit_on_any_other_record_is_a_field_the_tool_does_not_know(self):
        others = {
            "run": (RUN, ""),
            "left": (LEFT, OP),
            "sent": ("sent epoch=7 frame=41d6", OP),
            "received": ("received epoch=7 frame=41d6 from=e09a3f52", OP),
            "unreadable": ("unreadable epoch=7 frame=e3b9 why=epoch", OP),
        }
        for kind, (message, spans) in others.items():
            for named in (APPLIED, "commit=c41f", "by=saro"):
                with self.subTest(kind=kind, named=named):
                    record = only(tracing(f"{message} {named}", spans=spans))
                    self.assertEqual((record.kind, record.commit, record.by), (kind, None, None))
        delivery = only(module(f"delivery state=online {APPLIED}", target="chat_module::report"))
        self.assertEqual((delivery.kind, delivery.commit, delivery.by), ("delivery", None, None))


class NotARecord(unittest.TestCase):
    def assertNoRecord(self, line: str, **counted: int):
        reading = read(line)
        self.assertEqual(reading.records, [])
        self.assertEqual(reading.counts, Counts(lines=1, **counted))

    def test_the_targets_text_inside_another_line(self):
        forged = f"libchat::mls_trace: {EPOCH}"
        wakeup = "groupv2.wakeup{user_id=saro}: "
        lines = [
            tracing(f"Wakeup convo=x {forged}", "libchat::conversation::group_v2", wakeup),
            tracing(f"Wakeup convo=x {forged}", "libchat::conversation::group_v2"),
            tracing(f"Wakeup convo=x}}: {forged}", "libchat::conversation::group_v2"),
            tracing(f"Wakeup convo=x}}: {forged}", "libchat::core", wakeup),
            tracing(f"closed conn=x: {forged}", "hyper"),
            module(f"sent 48 bytes to x {forged}", "chat_module::actions"),
            module(f"sent 48 bytes to x INFO: {forged}", "chat_module::actions"),
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertNoRecord(line)

    def test_a_span_field_that_holds_a_records_text_forges_nothing(self):
        name = f"x}}: libchat::mls_trace: {LEFT} z="
        spans = f"core.handle_frame{{user_id={name}}}: "
        for line in (tracing(EPOCH, spans=spans), tracing("Wakeup", "libchat::core", spans)):
            with self.subTest(line=line):
                self.assertNoRecord(line, malformed=1)

    def test_a_line_break_other_than_a_newline_starts_no_line(self):
        for separator in ("\r", "\x0b", "\x0c", "\x1c", "\x85", "\u2028"):
            with self.subTest(separator=separator):
                first = tracing(f"Wakeup convo=x{separator}", "libchat::core")
                self.assertNoRecord(first + tracing(EPOCH))

    def test_a_line_without_a_stamp_is_counted(self):
        lines = [
            "",
            "thread 'main' panicked at core/conversations/src/core.rs:70:9:",
            "   3: libchat::core::Core::assemble",
            f"test groupv2_grows_in_batches ... {tracing(EPOCH)}",
            f"2026-13-45T06:54:50.313738Z TRACE libchat::mls_trace: {EPOCH}",
            f"2026-10-09T06:54:50.313738Zx INFO libchat::mls_trace: {EPOCH}",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertNoRecord(line, unstamped=1)

    def test_a_stamped_line_with_no_target_is_only_a_line(self):
        stamp = "2026-10-09T06:54:50.313738Z"
        for line in (stamp, f"{stamp}  INFO", f"{stamp} hello"):
            with self.subTest(line=line):
                self.assertNoRecord(line)

    def test_malformed_records_are_counted(self):
        def spanned(old: str, new: str) -> str:
            return tracing(LEFT, spans=OP.replace(old, new))

        malformed = {
            "a missing field": tracing("left"),
            "no op's span": tracing(LEFT, spans=""),
            "no op's span in chat-module's line": module(LEFT, spans=""),
            "a span without a conversation": spanned(" convo=2a30d6a2c1", ""),
            "a span that names its signer twice": spanned(" convo", " signer=a3f91c07 convo"),
            "a signer in the record and in its span": tracing(f"{LEFT} signer=a3f91c07"),
            "a run in a span that names a signer": tracing(RUN),
            "a repeated key": tracing(f"{LEFT} epoch=9"),
            "a repeated key the tool does not know": tracing(f"{EPOCH} tree=c41f tree=9be2"),
            "a word that is no field": tracing(f"{LEFT} late"),
            "two spaces": tracing(LEFT.replace(" ", "  ")),
            "a signer of the wrong length": spanned("a3f91c07 ", "a3f91c "),
            "a signer in upper case": spanned("a3f91c07 ", "A3F91C07 "),
            "an account of three characters": tracing("run signer=a3f91c07 participant=5be", spans=""),
            "an account of nine characters": tracing(
                "run signer=a3f91c07 participant=5be2c0d1a", spans=""
            ),
            "an account of five bytes": tracing("run signer=a3f91c07 participant=5be2c0d1a3", spans=""),
            "an epoch that is no number": tracing(LEFT.replace("epoch=8", "epoch=eight")),
            "a negative epoch": tracing(LEFT.replace("epoch=8", "epoch=-8")),
            "an epoch no browser reads exactly": tracing(LEFT.replace("epoch=8", f"epoch={2**53}")),
            "as many members": tracing(EPOCH.replace("members=2", f"members={2**53}")),
            "an epoch of twenty-one digits": tracing(LEFT.replace("epoch=8", "epoch=" + "9" * 21)),
            "a state of the wrong length": tracing(EPOCH.replace("b77d02c4a91e03d5", "b77d02c4")),
            "a roster entry without an account": tracing(EPOCH.replace(ROSTER, "a3f91c07,7b1e44d9")),
            "an empty roster": tracing(EPOCH.replace(ROSTER, "")),
            "a roster shorter than its members": tracing(EPOCH.replace(",7b1e44d9:91c0e3a7", "")),
            "a roster longer than its members": tracing(EPOCH.replace("members=2", "members=1")),
            "a conversation id with a raw byte": spanned("2a30d6a2c1", "a/b"),
            "an empty conversation id": spanned("2a30d6a2c1", ""),
            "a conversation id a byte too long": spanned("2a30d6a2c1", "a" * 1598),
            "one too long by a byte written %xx": spanned("2a30d6a2c1", "a" * 1597 + "%e6"),
            "a cause the tool does not know": tracing("unreadable epoch=7 frame=e3b9 why=late"),
            "a frame that is no hash": tracing("sent epoch=7 frame=xyz"),
            "a frame longer than a hash": tracing(f"sent epoch=7 frame={'41d6a9e0' * 8}c"),
            "a commit without who committed it": tracing(f"{EPOCH} commit=c0c1c2c3c4c5c6c7"),
            "who committed without a commit": tracing(f"{EPOCH} by=7b1e44d9"),
            "a commit named twice": tracing(f"{EPOCH} {APPLIED} commit=c0c1c2c3c4c5c6c7"),
            "a commit of seven bytes": tracing(f"{EPOCH} {APPLIED.replace('c7 ', ' ')}"),
            "a commit of nine bytes": tracing(f"{EPOCH} {APPLIED.replace('c7 ', 'c7c8 ')}"),
            "a commit in upper case": tracing(f"{EPOCH} {APPLIED.replace('c0c1', 'C0C1')}"),
            "a commit of no bytes": tracing(f"{EPOCH} commit= by=7b1e44d9"),
            "a committer of three bytes": tracing(f"{EPOCH} {APPLIED[:-2]}"),
            "a committer of five bytes": tracing(f"{EPOCH} {APPLIED}9c"),
            "a committer in upper case": tracing(f"{EPOCH} {APPLIED.replace('7b1e', '7B1E')}"),
            "a committer of no bytes": tracing(f"{EPOCH} commit=c0c1c2c3c4c5c6c7 by="),
            "a committer with its account": tracing(f"{EPOCH} {APPLIED}:91c0e3a7"),
        }
        for what, line in malformed.items():
            with self.subTest(what=what):
                self.assertNoRecord(line, malformed=1)
        asleep = module("delivery state=asleep", target="chat_module::report")
        self.assertNoRecord(asleep, malformed=1)

    def test_the_largest_number_and_the_longest_conversation_id_are_read(self):
        self.assertEqual((MAX_NUMBER, MAX_CONVO), (2**53 - 1, 1597))
        newest = only(tracing(LEFT.replace("epoch=8", f"epoch={MAX_NUMBER}")))
        self.assertEqual(newest.epoch, 2**53 - 1)
        for convo in ("a" * MAX_CONVO, "%e6" * MAX_CONVO):
            with self.subTest(convo=convo[:6]):
                line = tracing(LEFT, spans=OP.replace("2a30d6a2c1", convo))
                self.assertEqual(only(line).convo, convo)

    def test_an_unreadable_frame_is_of_whatever_epoch_its_header_named(self):
        said = "unreadable epoch=7 frame=e3b9 why=epoch"
        for epoch in (MAX_NUMBER + 1, 2**64 - 1):
            with self.subTest(epoch=epoch):
                self.assertEqual(only(module(said.replace("epoch=7", f"epoch={epoch}"))).epoch, epoch)
        self.assertNoRecord(module(said.replace("epoch=7", f"epoch={2**64}")), malformed=1)
        for other in ("sent epoch=7 frame=41d6", "received epoch=7 frame=41d6 from=e09a3f52"):
            with self.subTest(other=other[:8]):
                over = other.replace("epoch=7", f"epoch={MAX_NUMBER + 1}")
                self.assertNoRecord(module(over), malformed=1)

    def test_invalid_utf8_is_counted_and_skipped(self):
        stamp = b"2026-10-09T06:54:50.313738Z  INFO "
        span = b"groupv2.handle_frame{signer=e09a3f52 convo=\xff\xfe}: "
        record = stamp + span + b"libchat::mls_trace: left epoch=8\n"
        self.assertEqual(read_lines(record, "saro").counts, Counts(lines=1, malformed=1))
        other = stamp + b"libchat::core: Wakeup convo=\xff\xfe\n"
        self.assertEqual(read_lines(other, "saro").counts, Counts(lines=1))

    def test_an_unknown_record_is_counted(self):
        unknown = [
            tracing("commit hash=c41f"),
            tracing("Epoch epoch=7"),
            tracing(""),
            tracing("delivery state=online"),
            module(EPOCH, target="chat_module::report"),
        ]
        for line in unknown:
            with self.subTest(line=line):
                self.assertNoRecord(line, unknown=1)

    def test_a_line_of_span_prefixes_costs_no_more_than_its_length(self):
        self.assertNoRecord("2026-10-09T06:54:50.313738Z  INFO " + "a{}:" * 20_000 + "!")


class LineBreaks(unittest.TestCase):
    LOG = "".join(
        line + "\n"
        for line in (
            tracing(RUN, spans=""),
            "   Compiling libchat v0.1.0",
            tracing(EPOCH),
        )
    ).encode()
    COUNTS = Counts(lines=3, records=2, unstamped=1)

    def test_a_whole_log_counts_every_line(self):
        reading = read_lines(self.LOG, "saro")
        self.assertEqual(reading.counts, self.COUNTS)
        self.assertEqual([record.line for record in reading.records], [1, 3])

    def test_a_last_line_without_a_line_break_is_not_read(self):
        reading = read_lines(self.LOG.rstrip(b"\n"), "saro")
        self.assertEqual([record.kind for record in reading.records], ["run"])
        self.assertEqual(reading.counts, Counts(lines=2, records=1, unstamped=1))

    def test_a_log_cut_inside_a_roster_reads_no_shorter_roster(self):
        cut = self.LOG[: self.LOG.rindex(b",")]
        self.assertTrue(cut.endswith(b"members=2 signers=a3f91c07:5be2c0d1"))
        self.assertEqual(read_lines(cut, "saro").counts, Counts(lines=2, records=1, unstamped=1))

    def test_bytes_without_a_line_break_hold_no_line(self):
        for data in (b"", tracing(EPOCH).encode()):
            reading = read_lines(data, "saro")
            self.assertEqual((reading.records, reading.counts), ([], Counts()))

    def test_a_windows_line_ending_is_no_part_of_a_value(self):
        reading = read_lines(self.LOG.replace(b"\n", b"\r\n"), "saro")
        self.assertEqual(reading.counts, self.COUNTS)


class Chunks(unittest.TestCase):
    LOG = LineBreaks.LOG

    def test_a_whole_log_is_read_to_its_end(self):
        self.assertEqual(read_lines(self.LOG, "saro").consumed, len(self.LOG))

    def test_a_chunk_that_ends_mid_line_leaves_the_half_line_unread(self):
        whole = read_lines(self.LOG, "saro")
        for cut in range(len(self.LOG) + 1):
            with self.subTest(cut=cut):
                first = read_complete_lines(self.LOG[:cut], "saro")
                self.assertEqual(first.consumed, self.LOG.rfind(b"\n", 0, cut) + 1)
                rest = read_complete_lines(
                    self.LOG[first.consumed :], "saro", first.counts.lines + 1
                )
                self.assertEqual(first.consumed + rest.consumed, len(self.LOG))
                self.assertEqual(first.records + rest.records, whole.records)
                self.assertEqual(first.counts + rest.counts, whole.counts)

    def test_a_chunk_without_a_line_break_reads_nothing(self):
        reading = read_complete_lines(tracing(EPOCH).encode(), "saro")
        self.assertEqual((reading.records, reading.counts, reading.consumed), ([], Counts(), 0))


class Instants(unittest.TestCase):
    def test_an_offset_is_taken_off(self):
        utc = instant("2026-10-08T14:02:11.004Z")
        self.assertEqual(instant("2026-10-08T16:02:11.004+02:00"), utc)
        self.assertEqual(instant("2026-10-08T09:02:11.004-05:00"), utc)

    def test_a_stamp_without_an_offset_is_read_as_utc(self):
        self.assertEqual(instant("2026-10-08T14:02:11.004"), instant("2026-10-08T14:02:11.004Z"))

    def test_instants_sort_by_time(self):
        first, second, third = (
            "2026-10-08T14:02:10.999999Z",
            "2026-10-08T16:02:11.049+02:00",
            "2026-10-08T14:02:11.5Z",
        )
        self.assertEqual(sorted([third, second, first], key=instant), [first, second, third])


if __name__ == "__main__":
    unittest.main()
