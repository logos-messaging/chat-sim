import os
import time
import unittest
from unittest import mock

from sim import measure

from tests import FIXTURES

CONVO = "15c93801b5"
POSTER = "f9b0c875e08c9fc606a46812ed93f71fce0c950982e445754db7ab173a3ba012"


def lookup(at: float, account: str = POSTER, method: str = "GET") -> dict:
    return {"at": at, "method": method, "path": f"/v1/account/{account}", "status": 200}


class Spread(unittest.TestCase):
    def test_no_times_have_no_spread(self):
        self.assertIsNone(measure.spread([]))

    def test_an_odd_count_has_its_middle_time_as_median(self):
        self.assertEqual(
            measure.spread([0.5, 0.1, 0.3]), {"count": 3, "median": 0.3, "worst": 0.5}
        )

    def test_an_even_count_has_the_mean_of_its_middle_two(self):
        self.assertEqual(
            measure.spread([4.0, 1.0, 2.0, 3.0]), {"count": 4, "median": 2.5, "worst": 4.0}
        )


class Batches(unittest.TestCase):
    def test_the_last_batch_holds_what_is_left(self):
        self.assertEqual(measure.batches(list(range(7)), 3), [[0, 1, 2], [3, 4, 5], [6]])

    def test_a_batch_larger_than_the_joiners_is_one_batch(self):
        self.assertEqual(measure.batches(["raya", "pax"], 10), [["raya", "pax"]])

    def test_no_joiners_are_no_batches(self):
        self.assertEqual(measure.batches([], 10), [])


class PostTimes(unittest.TestCase):
    def test_read_times_are_counted_from_when_the_post_was_sent(self):
        times = measure.post_times(100.0, [100.25, 100.5, 101.0], [])

        self.assertEqual(times["read"], {"count": 3, "median": 0.5, "worst": 1.0})

    def test_a_lookup_for_each_reader_tells_delivery_from_lookup(self):
        times = measure.post_times(100.0, [100.25, 100.5, 101.0], [100.125, 100.25, 100.75])

        self.assertEqual(times["delivered"], {"count": 3, "median": 0.25, "worst": 0.75})
        # Mean read 0.5833..., mean lookup arrival 0.375.
        self.assertAlmostEqual(times["lookup_mean"], 0.5833333333 - 0.375)
        self.assertEqual(times["lookups"], 3)

    def test_the_mean_lookup_does_not_depend_on_which_request_was_whose(self):
        read = [100.25, 100.5, 101.0]
        one = measure.post_times(100.0, read, [100.125, 100.25, 100.75])
        other = measure.post_times(100.0, read, [100.75, 100.125, 100.25])

        self.assertAlmostEqual(one["lookup_mean"], other["lookup_mean"])

    def test_lookups_that_are_not_one_a_reader_tell_nothing_apart(self):
        for asked in ([], [100.125], [100.125, 100.25, 100.75, 100.875]):
            times = measure.post_times(100.0, [100.25, 100.5, 101.0], asked)

            self.assertIsNone(times["delivered"], asked)
            self.assertIsNone(times["lookup_mean"], asked)
            self.assertEqual(times["lookups"], len(asked))
            self.assertEqual(times["read"]["count"], 3)

    def test_a_post_nobody_read_has_no_times(self):
        times = measure.post_times(100.0, [], [])

        self.assertIsNone(times["read"])
        self.assertIsNone(times["lookup_mean"])


class Lookups(unittest.TestCase):
    def test_only_lookups_of_the_account_in_the_window_count(self):
        requests = [
            lookup(99.0),
            lookup(100.0),
            lookup(100.5, account="another"),
            lookup(100.5, method="POST"),
            lookup(101.0),
            lookup(101.5),
        ]

        self.assertEqual(measure.lookups(requests, POSTER, 100.0, 101.0), [100.0, 101.0])


class Logs(unittest.TestCase):
    """A creator's log as chat-cli wrote it: a group of four, then of seven, three joiners each."""

    def setUp(self):
        self.log = (FIXTURES / "creator.log").read_text(encoding="utf-8")

    def test_the_welcomes_of_a_window_are_those_published_in_it(self):
        # The first Welcome is written at 2026-10-10T05:40:17.989319Z and the last of its three
        # at 05:40:18.008206Z; the other three come from 05:40:19.134435Z.
        first, third = 1791610817.989319, 1791610818.008206

        self.assertEqual(measure.welcomes(self.log, first, third + 0.5), [9758] * 3)
        self.assertEqual(measure.welcomes(self.log, first + 0.000001, third - 0.000001), [9758])
        self.assertEqual(measure.welcomes(self.log, third + 0.5, third + 5), [13946] * 3)
        self.assertEqual(measure.welcomes(self.log, 0, first - 0.000001), [])

    def test_a_roster_is_reached_when_its_epoch_record_was_written(self):
        # 2026-10-10T05:40:19.134361Z, the record of epoch 2.
        record = measure.reached(self.log, CONVO, 7)

        self.assertAlmostEqual(record.at, 1791610819.134361, places=6)
        self.assertEqual((record.epoch, record.state, record.members), (2, "46100481aad1fe6d", 7))
        self.assertAlmostEqual(measure.reached(self.log, CONVO, 4).at, 1791610817.989239, places=6)

    def test_a_roster_held_at_two_epochs_is_reached_at_the_first(self):
        again = (
            "2026-10-10T05:40:25.500000Z  INFO libchat::groupv2trace: epoch installation=e9b55953 "
            f"convo={CONVO} epoch=3 state=0f0f0f0f0f0f0f0f members=7 roster=e9b55953:56a4e537\n"
        )
        record = measure.reached(self.log + again, CONVO, 7)

        self.assertAlmostEqual(record.at, 1791610819.134361, places=6)
        self.assertEqual((record.epoch, record.state), (2, "46100481aad1fe6d"))

    def test_a_log_is_stamped_in_utc_wherever_it_is_read(self):
        # Five hours east of UTC, as POSIX writes it.
        with mock.patch.dict(os.environ, TZ="XXX-5"):
            time.tzset()
            self.addCleanup(time.tzset)
            record = measure.reached(self.log, CONVO, 7)

        self.assertAlmostEqual(record.at, 1791610819.134361, places=6)

    def test_the_state_held_at_an_epoch_is_that_of_its_record(self):
        self.assertEqual(measure.held(self.log, CONVO, 1), "9399627bdafae3e1")
        self.assertEqual(measure.held(self.log, CONVO, 2), "46100481aad1fe6d")
        self.assertIsNone(measure.held(self.log, CONVO, 3))
        self.assertIsNone(measure.held(self.log, "another", 2))

    def test_the_frames_whose_sender_could_not_be_checked_are_counted(self):
        def unreadable(why: str) -> str:
            fields = f"installation=e9b55953 convo={CONVO} frame=d0cf2be190f059bc epoch=2 why={why}"
            # Each part in a colour of its own, as chat-cli writes a record.
            coloured = fields.replace(" ", " \x1b[3m").replace("=", "\x1b[0m\x1b[2m=\x1b[0m")
            return (
                "\x1b[2m2026-10-10T05:40:26.000000Z\x1b[0m \x1b[32m INFO\x1b[0m "
                f"\x1b[2mlibchat::groupv2trace\x1b[0m\x1b[2m:\x1b[0m unreadable \x1b[3m{coloured}\n"
            )

        log = self.log + "".join(map(unreadable, ("sender", "decrypt", "epoch", "sender")))

        self.assertEqual(measure.unchecked(log), 2)
        self.assertEqual(measure.unchecked(self.log), 0)

    def test_a_roster_the_log_does_not_hold_is_not_reached(self):
        self.assertIsNone(measure.reached(self.log, CONVO, 5))
        self.assertIsNone(measure.reached(self.log, "another", 7))
        self.assertIsNone(measure.reached("", CONVO, 7))

    def test_the_filter_lets_through_what_the_log_is_read_for(self):
        for target in ("libchat::groupv2trace=info", "libchat::inbox_v2=info"):
            self.assertIn(target, measure.LOG_FILTER.split(","))


if __name__ == "__main__":
    unittest.main()
