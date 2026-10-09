import unittest

from forks.records import Counts, instant, read_lines

ROSTER = "a3f91c07:5be2c0d1,7b1e44d9:91c0e3a7"
PLACED = "installation=a3f91c07 convo=2a30d6a2c1 epoch=7"
EPOCH = f"epoch {PLACED} state=b77d02c4a91e03d5 members=2 roster={ROSTER}"
LEFT = "left installation=e09a3f52 convo=2a30d6a2c1 epoch=8"


def tracing(message: str, target: str = "libchat::groupv2trace", spans: str = "") -> str:
    """A line as tracing's formatter writes it."""
    return f"2026-10-09T06:54:50.313738Z  INFO {spans}{target}: {message}"


def module(message: str, target: str = "libchat::groupv2trace", offset: str = "+02:00") -> str:
    """A line as chat-module writes it."""
    return f"2026-10-08T16:02:11.004{offset} INFO: {target}: {message}"


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
            f"{bold}}}{off}{dim}:{off} {dim}libchat::groupv2trace{off}{dim}:{off} left "
            f"{italic}installation{off}{dim}={off}7b1e44d9 "
            f"{italic}convo{off}{dim}={off}2a30d6a2c1 {italic}epoch{off}{dim}={off}8"
        )
        record = only(coloured)
        self.assertEqual((record.kind, record.installation, record.epoch), ("left", "7b1e44d9", 8))
        self.assertEqual(record.time, "2026-10-09T12:17:37.592411Z")

    def test_span_prefixes_come_before_the_target(self):
        prefixes = [
            "core.handle_frame{user_id=raya}: ",
            "core.handle_frame{user_id=raya}:groupv2.handle_frame{user_id=raya}: ",
            "groupv2.wakeup{user_id=raya with a space}: ",
            "main_loop: ",
            "main_loop:core.handle_frame{user_id=raya}: ",
        ]
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.assertEqual(only(tracing(EPOCH, spans=prefix)).kind, "epoch")

    def test_every_level_is_read(self):
        for level in ("TRACE", "DEBUG", " WARN", "ERROR"):
            line = f"2026-10-09T06:54:50.327025Z {level} libchat::groupv2trace: {EPOCH}"
            self.assertEqual(only(line).kind, "epoch")
        line = f"2026-10-08T16:02:11.004 WARNING: libchat::groupv2trace: {EPOCH}"
        self.assertEqual(only(line).kind, "epoch")


class TheSevenRecords(unittest.TestCase):
    def test_run(self):
        record = only(tracing("run installation=810d744e account=7361726f"))
        self.assertEqual(
            (record.kind, record.installation, record.account), ("run", "810d744e", "7361726f")
        )

    def test_an_account_shorter_than_four_bytes(self):
        record = only(tracing("run installation=4caf5f49 account=706178"))
        self.assertEqual(record.account, "706178")

    def test_an_account_of_no_bytes(self):
        record = only(tracing("run installation=4caf5f49 account="))
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

    def test_left(self):
        record = only(module(LEFT))
        self.assertEqual((record.kind, record.convo, record.epoch), ("left", "2a30d6a2c1", 8))

    def test_sent(self):
        record = only(module(f"sent {PLACED} frame=41d6"))
        self.assertEqual((record.kind, record.frame, record.epoch), ("sent", "41d6", 7))

    def test_received(self):
        record = only(module(f"received {PLACED} frame=41d6 from=e09a3f52"))
        self.assertEqual(
            (record.kind, record.frame, record.sender), ("received", "41d6", "e09a3f52")
        )

    def test_unreadable(self):
        for why in ("epoch", "decrypt", "sender"):
            record = only(module(f"unreadable {PLACED} frame=e3b9 why={why}"))
            self.assertEqual((record.kind, record.frame, record.why), ("unreadable", "e3b9", why))

    def test_a_conversation_id_stays_as_written(self):
        record = only(tracing("left installation=e09a3f52 convo=a%20b%3dc%0ad%25 epoch=8"))
        self.assertEqual(record.convo, "a%20b%3dc%0ad%25")

    def test_a_roster_of_a_hundred(self):
        members = [(f"{index:08x}", f"{index + 1000:08x}") for index in range(100)]
        roster = ",".join(f"{installation}:{account}" for installation, account in members)
        record = only(tracing(f"epoch {PLACED} state=b77d02c4a91e03d5 members=100 roster={roster}"))
        self.assertEqual(record.roster, tuple(members))

    def test_a_field_the_tool_does_not_know_is_ignored(self):
        record = only(tracing(f"{EPOCH} commit=c41f by=a3f91c07"))
        self.assertEqual((record.kind, record.epoch), ("epoch", 7))


class NotARecord(unittest.TestCase):
    def assertNoRecord(self, line: str, **counted: int):
        reading = read(line)
        self.assertEqual(reading.records, [])
        self.assertEqual(reading.counts, Counts(lines=1, **counted))

    def test_the_targets_text_inside_another_line(self):
        forged = f"libchat::groupv2trace: {EPOCH}"
        wakeup = "groupv2.wakeup{user_id=saro}: "
        lines = [
            tracing(f"Wakeup convo=x {forged}", "libchat::conversation::group_v2", wakeup),
            tracing(f"Wakeup convo=x {forged}", "libchat::conversation::group_v2"),
            tracing(f"Wakeup convo=x}}: {forged}", "libchat::conversation::group_v2"),
            tracing(f"Wakeup convo=x}}: {forged}", "libchat::core", wakeup),
            tracing(f"closed conn=x: {forged}", "hyper"),
            module(f"sent 48 bytes to x {forged}", "chat_module::actions"),
            module(f"sent 48 bytes to x INFO: {forged}", "chat_module::actions"),
            module(EPOCH, "main_loop: libchat::groupv2trace"),
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertNoRecord(line)

    def test_a_span_field_that_holds_a_records_text_forges_nothing(self):
        name = f"x}}: libchat::groupv2trace: {LEFT} z="
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
            f"2026-13-45T06:54:50.313738Z  INFO libchat::groupv2trace: {EPOCH}",
            f"2026-10-09T06:54:50.313738Zx INFO libchat::groupv2trace: {EPOCH}",
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
        malformed = {
            "a missing field": "left installation=e09a3f52 convo=2a30d6a2c1",
            "a repeated key": f"{LEFT} epoch=9",
            "a repeated key the tool does not know": f"{EPOCH} commit=c41f commit=9be2",
            "a word that is no field": f"{LEFT} late",
            "two spaces": LEFT.replace(" convo", "  convo"),
            "an installation of the wrong length": EPOCH.replace("a3f91c07 ", "a3f91c "),
            "an installation in upper case": EPOCH.replace("a3f91c07 ", "A3F91C07 "),
            "an account of three characters": "run installation=a3f91c07 account=5be",
            "an account of nine characters": "run installation=a3f91c07 account=5be2c0d1a",
            "an account of five bytes": "run installation=a3f91c07 account=5be2c0d1a3",
            "an epoch that is no number": LEFT.replace("epoch=8", "epoch=eight"),
            "a negative epoch": LEFT.replace("epoch=8", "epoch=-8"),
            "a state of the wrong length": EPOCH.replace("b77d02c4a91e03d5", "b77d02c4"),
            "a roster entry without an account": EPOCH.replace(ROSTER, "a3f91c07,7b1e44d9"),
            "an empty roster": EPOCH.replace(ROSTER, ""),
            "a roster shorter than its members": EPOCH.replace(",7b1e44d9:91c0e3a7", ""),
            "a roster longer than its members": EPOCH.replace("members=2", "members=1"),
            "a conversation id with a raw byte": LEFT.replace("2a30d6a2c1", "a/b"),
            "an empty conversation id": LEFT.replace("2a30d6a2c1", ""),
            "a cause the tool does not know": f"unreadable {PLACED} frame=e3b9 why=late",
            "a frame that is no hash": f"sent {PLACED} frame=xyz",
        }
        for what, message in malformed.items():
            with self.subTest(what=what):
                self.assertNoRecord(tracing(message), malformed=1)
        asleep = module("delivery state=asleep", target="chat_module::report")
        self.assertNoRecord(asleep, malformed=1)

    def test_invalid_utf8_is_counted_and_skipped(self):
        stamp = b"2026-10-09T06:54:50.313738Z  INFO "
        record = stamp + b"libchat::groupv2trace: left installation=e09a3f52 convo=\xff\xfe epoch=8\n"
        self.assertEqual(read_lines(record, "saro").counts, Counts(lines=1, malformed=1))
        other = stamp + b"libchat::core: Wakeup convo=\xff\xfe\n"
        self.assertEqual(read_lines(other, "saro").counts, Counts(lines=1))

    def test_an_unknown_record_is_counted(self):
        unknown = [
            tracing("commit installation=e09a3f52 hash=c41f"),
            tracing("Epoch installation=e09a3f52"),
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
            tracing("run installation=810d744e account=7361726f"),
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
        self.assertTrue(cut.endswith(b"members=2 roster=a3f91c07:5be2c0d1"))
        self.assertEqual(read_lines(cut, "saro").counts, Counts(lines=2, records=1, unstamped=1))

    def test_bytes_without_a_line_break_hold_no_line(self):
        for data in (b"", tracing(EPOCH).encode()):
            reading = read_lines(data, "saro")
            self.assertEqual((reading.records, reading.counts), ([], Counts()))

    def test_a_windows_line_ending_is_no_part_of_a_value(self):
        reading = read_lines(self.LOG.replace(b"\n", b"\r\n"), "saro")
        self.assertEqual(reading.counts, self.COUNTS)


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
