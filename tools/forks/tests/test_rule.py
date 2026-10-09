import random
import unittest
from dataclasses import replace
from typing import Dict, List, Set, Tuple

from forks.records import Record
from forks.rule import Conversation, Installation, Kept, report

CONVO = "2a30d6a2c1"


class Log:
    """A run's records as they are appended; each line is a second after the one before."""

    def __init__(self, run: str, hour: int = 10):
        self.run = run
        self.hour = hour
        self.records: List[Record] = []

    def _add(self, kind: str, **fields) -> None:
        line = len(self.records) + 1
        time = f"2026-10-08T{self.hour:02d}:{line // 60:02d}:{line % 60:02d}.000Z"
        self.records.append(Record(self.run, line, time, True, kind, **fields))

    def start(self, name: str) -> None:
        self._add("run", installation=name, account=f"account-of-{name}")

    def epoch(self, name: str, epoch: int, state: str, roster: str, convo: str = CONVO) -> None:
        members = tuple((member, f"account-of-{member}") for member in roster.split())
        self._add(
            "epoch",
            installation=name,
            convo=convo,
            epoch=epoch,
            state=state,
            members=len(members),
            roster=members,
        )

    def left(self, name: str, epoch: int) -> None:
        self._add("left", installation=name, convo=CONVO, epoch=epoch)

    def sent(self, name: str, frame: str, epoch: int) -> None:
        self._add("sent", installation=name, convo=CONVO, frame=frame, epoch=epoch)

    def received(self, name: str, frame: str, sender: str, epoch: int) -> None:
        self._add(
            "received", installation=name, convo=CONVO, frame=frame, sender=sender, epoch=epoch
        )

    def unreadable(self, name: str, frame: str, epoch: int, why: str) -> None:
        self._add("unreadable", installation=name, convo=CONVO, frame=frame, epoch=epoch, why=why)

    def delivery(self, state: str) -> None:
        self._add("delivery", state=state)


def started(*names: str) -> Dict[str, Log]:
    """A run for each name, its `run` record written."""
    logs = {name: Log(name) for name in names}
    for name, log in logs.items():
        log.start(name)
    return logs


def one(logs, final=(), kept=()) -> Conversation:
    logs = logs.values() if isinstance(logs, dict) else logs
    (conversation,) = report([record for log in logs for record in log.records], final, kept)
    return conversation


def positions(conversation: Conversation) -> Dict[str, str]:
    return {seat.id: seat.position for seat in conversation.installations}


def seat_of(conversation: Conversation, name: str) -> Installation:
    (seat,) = [seat for seat in conversation.installations if seat.id == name]
    return seat


# The histories the rule must read: an installation is {name: [run, ...]}, a run is
# (final, [record, ...]), a record is ("epoch", n, state, roster) or ("left", n). KEPT holds, for
# a history, the kept lines: for an installation, the epoch of its last record and whether that
# record was a leave.


def ep(n, state, roster):
    return ("epoch", n, state, set(roster.split()))


def run(*records, final=False):
    return (final, list(records))


ALL3 = "saro raya pax"
ALL4 = ALL3 + " mira"
ALL5 = ALL4 + " kes"
FOUR = "saro raya tomo pax"
FIVE = "saro pax tomo kes mira"
SEVEN = "a b c d e f g".split()
EIGHT = "a b c d e f g pax"
NINE = EIGHT + " mira"
MAJORITY = {m: [run(ep(6, "X6", EIGHT), ep(7, "A7", EIGHT), ep(8, "A8", EIGHT))] for m in SEVEN}

HISTORIES = [
    ("healthy group",
     {m: [run(ep(1, "X1", ALL3), ep(2, "X2", ALL3))] for m in ALL3.split()},
     "whole", dict(saro="tip", raya="tip", pax="tip")),
    ("one member behind, witnessed",
     {"saro": [run(ep(1, "X1", ALL3), ep(2, "X2", ALL3))],
      "raya": [run(ep(1, "X1", ALL3), ep(2, "X2", ALL3))],
      "pax": [run(ep(1, "X1", ALL3))]},
     "whole, members behind", dict(pax="behind")),
    ("split seven to one",
     {**MAJORITY, "pax": [run(ep(6, "X6", EIGHT), ep(7, "B7", EIGHT))]},
     "split at epoch 7", dict(pax="tip")),
    ("the forked member restarts the app",
     {**MAJORITY, "pax": [run(ep(6, "X6", EIGHT), ep(7, "B7", EIGHT), final=True)]},
     "split at epoch 7, one side live", dict(pax="ended")),
    ("split, then every run ended",
     {**{m: [(True, r[0][1])] for m, r in MAJORITY.items()},
      "pax": [run(ep(6, "X6", EIGHT), ep(7, "B7", EIGHT), final=True)]},
     "split at epoch 7, no side live", {}),
    ("split, both sides ended, one live member still on the trunk",
     {**{m: [run(ep(6, "X6", NINE), ep(7, "A7", NINE), final=True)] for m in SEVEN},
      "pax": [run(ep(6, "X6", NINE), ep(7, "B7", NINE), final=True)],
      "mira": [run(ep(6, "X6", NINE))]},
     "split at epoch 7, no side live", dict(mira="behind")),
    ("on the trunk, removed by one branch and kept by the other",
     {"saro": [run(ep(6, "X6", ALL3 + " mira"), ep(7, "A7", ALL3))],
      "raya": [run(ep(6, "X6", ALL3 + " mira"), ep(7, "A7", ALL3))],
      "pax": [run(ep(6, "X6", ALL3 + " mira"), ep(7, "B7", ALL3 + " mira"))],
      "mira": [run(ep(6, "X6", ALL3 + " mira"))]},
     "split at epoch 7", dict(mira="behind")),
    ("removed and added again by commits it never received",
     {"saro": [run(ep(5, "X5", ALL3), ep(6, "X6", "saro raya"), ep(7, "X7", ALL3))],
      "raya": [run(ep(5, "X5", ALL3), ep(6, "X6", "saro raya"), ep(7, "X7", ALL3))],
      "pax": [run(ep(5, "X5", ALL3))]},
     "whole, members behind", dict(pax="behind")),
    ("a stuck pair under a joiner nobody links to",
     {"raya": [run(ep(5, "X5", ALL3))], "pax": [run(ep(5, "X5", ALL3))],
      "kes": [run(ep(6, "Y6", ALL3 + " kes"))]},
     "whole as far as seen", dict(raya="lineage unknown", pax="lineage unknown", kes="tip")),
    ("one stuck member under a joiner nobody links to",
     {"raya": [run(ep(5, "X5", ALL3))], "kes": [run(ep(6, "Y6", ALL3 + " kes"))]},
     "not enough data", dict(raya="lineage unknown")),
    ("an ended installation is the witness",
     {"saro": [run(ep(1, "X1", "saro raya"), ep(2, "X2", ALL3), final=True)],
      "raya": [run(ep(1, "X1", "saro raya"))],
      "pax": [run(ep(2, "X2", ALL3))]},
     "whole, members behind", dict(saro="ended", raya="behind", pax="tip")),
    ("one sharing member alone",
     {"saro": [run(ep(1, "X1", ALL3), ep(2, "X2", ALL3))]},
     "not enough data", dict(saro="tip", raya="no data")),
    ("a removed member writes left",
     {"saro": [run(ep(5, "X5", ALL3), ep(6, "X6", "saro raya"))],
      "raya": [run(ep(5, "X5", ALL3), ep(6, "X6", "saro raya"))],
      "pax": [run(ep(5, "X5", ALL3), ("left", 6))]},
     "whole", dict(pax="removed")),
    ("a removed member writes epoch with a state of its own",
     {"saro": [run(ep(5, "X5", ALL3), ep(6, "X6", "saro raya"))],
      "raya": [run(ep(5, "X5", ALL3), ep(6, "X6", "saro raya"))],
      "pax": [run(ep(5, "X5", ALL3), ep(6, "Z6", "saro raya"))]},
     "whole", dict(pax="removed")),
    ("removed by a commit it never received",
     {"saro": [run(ep(5, "X5", ALL3), ep(6, "X6", "saro raya"))],
      "raya": [run(ep(5, "X5", ALL3), ep(6, "X6", "saro raya"))],
      "pax": [run(ep(5, "X5", ALL3))]},
     "whole", dict(pax="removed")),
    ("removed, then added again",
     {"saro": [run(ep(5, "X5", ALL3), ep(6, "X6", "saro raya"), ep(7, "X7", ALL3))],
      "raya": [run(ep(5, "X5", ALL3), ep(6, "X6", "saro raya"), ep(7, "X7", ALL3))],
      "pax": [run(ep(5, "X5", ALL3), ("left", 6), ep(7, "X7", ALL3))]},
     "whole", dict(pax="tip")),
    ("everyone relaunched",
     {m: [run(ep(1, "X1", ALL3), final=True)] for m in ALL3.split()},
     "no live member", {}),
    ("one installation, a final run and a live one",
     {"saro": [run(ep(1, "X1", ALL3), final=True), run(ep(2, "X2", ALL3))],
      "raya": [run(ep(1, "X1", ALL3), ep(2, "X2", ALL3))]},
     "whole", dict(saro="tip")),
    ("a split whose second branch starts with a joiner",
     {"saro": [run(ep(6, "X6", ALL3), ep(7, "A7", ALL3))],
      "raya": [run(ep(6, "X6", ALL3), ep(7, "A7", ALL3))],
      "kes": [run(ep(7, "B7", "pax kes"))]},
     "split seen from epoch 7", {}),
    ("behind a split that the other branch removed it from",
     {"saro": [run(ep(6, "X6", ALL3), ep(7, "A7", ALL3), ep(8, "A8", "saro raya"))],
      "raya": [run(ep(6, "X6", ALL3), ep(7, "A7", ALL3), ep(8, "A8", "saro raya"))],
      "pax": [run(ep(6, "X6", ALL3), ep(7, "B7", ALL3))]},
     "split at epoch 7", dict(pax="tip")),
    ("a welcome that never arrived",
     {"saro": [run(ep(1, "X1", ALL3))], "raya": [run(ep(1, "X1", ALL3))], "pax": [run()]},
     "whole", dict(pax="never joined")),
    ("a branch splits again, both of its sides live",
     {"saro": [run(ep(1, "X1", ALL4), ep(2, "A2", ALL4), ep(3, "A3", ALL4))],
      "raya": [run(ep(1, "X1", ALL4), ep(2, "A2", ALL4), ep(3, "A3", ALL4))],
      "mira": [run(ep(1, "X1", ALL4), ep(2, "A2", ALL4), ep(3, "C3", ALL4))],
      "pax": [run(ep(1, "X1", ALL4), ep(2, "B2", ALL4), ("left", 3))]},
     "split at epoch 2", dict(saro="tip", raya="tip", mira="tip", pax="removed")),
    ("one linked line above a split nobody live is on",
     {"saro": [run(ep(2, "X2", FIVE), ep(3, "A3", FIVE), ("left", 4))],
      "pax": [run(ep(2, "X2", FIVE), ep(3, "B3", FIVE), ("left", 4))],
      "tomo": [run(ep(5, "K5", FIVE), ep(6, "K6", FIVE), ("left", 7))],
      "kes": [run(ep(5, "K5", FIVE))],
      "mira": [run(ep(6, "K6", FIVE))]},
     "split at epoch 3, one side live", dict(kes="behind", mira="tip", tomo="removed")),
    ("two joiners above a split, nothing ties them to one line",
     {"saro": [run(ep(2, "X2", FIVE), ep(3, "A3", FIVE), ("left", 4))],
      "pax": [run(ep(2, "X2", FIVE), ep(3, "B3", FIVE), ("left", 4))],
      "kes": [run(ep(5, "K5", FIVE))],
      "mira": [run(ep(6, "M6", FIVE))]},
     "split at epoch 3", dict(kes="lineage unknown", mira="tip")),
    ("behind, and under a later state nothing links to",
     {"tomo": [run(ep(1, "X1", "tomo pax kes"), ep(2, "X2", "tomo pax kes"), ("left", 3))],
      "pax": [run(ep(1, "X1", "tomo pax kes"))],
      "kes": [run(ep(5, "Y5", "pax kes"))]},
     "whole as far as seen", dict(pax="behind", kes="tip", tomo="removed")),
    ("two states at an epoch, linked from one state two epochs below",
     {"saro": [run(ep(1, "X1", ALL3), ep(3, "A3", ALL3))],
      "raya": [run(ep(1, "X1", ALL3), ep(3, "A3", ALL3))],
      "pax": [run(ep(1, "X1", ALL3), ep(3, "B3", ALL3))]},
     "split seen from epoch 3", {}),
    ("a seat with no record, off the newest roster",
     {"saro": [run(ep(1, "X1", ALL3), ep(2, "X2", "saro raya"))],
      "raya": [run(ep(1, "X1", ALL3), ep(2, "X2", "saro raya"))]},
     "whole", dict(pax="removed")),
    ("a welcome that never arrived, then off the newest roster",
     {"saro": [run(ep(1, "X1", ALL3), ep(2, "X2", "saro raya"))],
      "raya": [run(ep(1, "X1", ALL3), ep(2, "X2", "saro raya"))],
      "pax": [run()]},
     "whole", dict(pax="removed")),
    ("a seat with no record, removed under a chain a restart broke",
     {"saro": [run(ep(1, "X1", "saro pax")), run(ep(2, "X2", "saro raya"))],
      "raya": [run(ep(2, "X2", "saro raya"))]},
     "whole", dict(pax="removed", saro="tip")),
    ("a seat with no record that one stuck member's roster still lists",
     {"raya": [run(ep(5, "X5", "raya mira pax"))], "mira": [run(ep(5, "X5", "raya mira pax"))],
      "kes": [run(ep(6, "Y6", "raya mira kes"))]},
     "whole as far as seen", dict(pax="no data")),
    ("a seat with no record that one side of a split lists, after that side's one holder left",
     {"saro": [run(ep(1, "X1", FOUR), ep(2, "A2", "saro raya"))],
      "raya": [run(ep(1, "X1", FOUR), ep(2, "A2", "saro raya"))],
      "tomo": [run(ep(1, "X1", FOUR), ep(2, "B2", FOUR), ("left", 3))]},
     "split at epoch 2, one side live", dict(pax="no data", tomo="removed")),
    ("seats only one side of a split lists, after its one holder restarted onto the other side",
     {"saro": [run(ep(2, "A2", "saro raya"), ep(3, "A3", "saro raya tomo"))],
      "raya": [run(ep(2, "A2", "saro raya"), ep(3, "A3", "saro raya tomo"))],
      "tomo": [run(ep(1, "X1", "tomo kes"), ep(2, "B2", "tomo kes pax")),
               run(ep(3, "A3", "saro raya tomo"))]},
     "split seen from epoch 2, one side live", dict(kes="no data", pax="no data", tomo="tip")),
    ("a seat an ended installation's last state lists, under a later state nothing links to",
     {"saro": [run(ep(1, "X1", "saro pax"), final=True)],
      "raya": [run(ep(2, "X2", "raya kes"))], "kes": [run(ep(2, "X2", "raya kes"))]},
     "whole", dict(pax="no data", saro="ended")),
    ("the member that removed a seat left afterwards",
     {"saro": [run(ep(1, "X1", ALL3), ep(2, "X2", "saro raya"), ("left", 3))],
      "raya": [run(ep(2, "X2", "saro raya"), ep(3, "X3", "raya"))]},
     "whole", dict(pax="removed", saro="removed", raya="tip")),
    ("seats one side of a split lists, after its one holder went to the other side and on",
     {"saro": [run(ep(2, "A2", "saro raya"), ep(3, "A3", "saro raya tomo"))],
      "tomo": [run(ep(1, "X1", "tomo kes"), ep(2, "B2", "tomo kes pax")),
               run(ep(3, "A3", "saro raya tomo")),
               run(ep(5, "A5", "saro raya tomo"))]},
     "split seen from epoch 2", dict(kes="no data", pax="no data", raya="no data")),
    ("a seat one side of a split lists, after its one holder joined a later state nothing ties "
     "to a side",
     {"mira": [run(ep(1, "X1", "mira tomo kes"), ep(2, "A2", "mira tomo kes saro"))],
      "tomo": [run(ep(1, "X1", "mira tomo kes"), ep(2, "B2", "mira tomo kes pax")),
               run(ep(4, "A4", "saro raya tomo"))],
      "raya": [run(ep(4, "A4", "saro raya tomo"))]},
     "split at epoch 2", dict(pax="no data")),
    ("two live states behind one later state nobody live stands at",
     {"saro": [run(ep(2, "X2", ALL5), ep(3, "A3", ALL5), ep(5, "A5", ALL5), ("left", 6))],
      "raya": [run(ep(4, "A4", ALL5), ep(5, "A5", ALL5), ("left", 6))],
      "mira": [run(ep(2, "X2", ALL5), ep(3, "A3", ALL5))],
      "kes": [run(ep(4, "A4", ALL5))],
      "pax": [run(ep(2, "X2", ALL5), ep(3, "B3", ALL5), ("left", 4))]},
     "split at epoch 3, one side live", dict(mira="behind", kes="behind")),
    ("a final run's log is gone and its line is kept: the seat stays ended",
     {"raya": [run(ep(3, "X3", ALL3))], "pax": [run(ep(3, "X3", ALL3))]},
     "whole", dict(saro="ended", raya="tip", pax="tip")),
    ("a kept line of a seat the newest roster has dropped since",
     {"raya": [run(ep(3, "X3", ALL3), ep(4, "X4", "raya pax"))],
      "pax": [run(ep(4, "X4", "raya pax"))]},
     "whole", dict(saro="removed", raya="tip", pax="tip")),
    ("a kept line of a run that ended in a leave, where the one log left never saw the removal",
     {"raya": [run(ep(1, "X1", "saro raya"))]},
     "not enough data", dict(saro="removed", raya="tip")),
    ("a kept line above the one roster left, which is older and lacks the seat",
     {"raya": [run(ep(1, "X1", "raya pax"))]},
     "not enough data", dict(saro="ended", raya="tip", pax="no data")),
    ("a kept line of an installation whose later run holds no record of the group",
     {"saro": [run()], "raya": [run(ep(2, "X2", ALL3))], "pax": [run(ep(2, "X2", ALL3))]},
     "whole", dict(saro="ended", raya="tip", pax="tip")),
    ("a kept line of an installation that holds the group again in a held log",
     {"saro": [run(ep(4, "X4", ALL3))], "raya": [run(ep(4, "X4", ALL3))]},
     "whole", dict(saro="tip", raya="tip", pax="no data")),
]
KEPT = {
    "a final run's log is gone and its line is kept: the seat stays ended": {"saro": (3, False)},
    "a kept line of a seat the newest roster has dropped since": {"saro": (3, False)},
    "a kept line of a run that ended in a leave, where the one log left never saw the removal":
        {"saro": (2, True)},
    "a kept line above the one roster left, which is older and lacks the seat":
        {"saro": (3, False)},
    "a kept line of an installation whose later run holds no record of the group":
        {"saro": (2, False)},
    "a kept line of an installation that holds the group again in a held log":
        {"saro": (2, False)},
}


def as_logs(history) -> Tuple[List[Log], Set[str]]:
    """A history as runs of records: every run starts with its installation's `run` record."""
    logs, final = [], set()
    for name, runs in history.items():
        for index, (is_final, written) in enumerate(runs):
            log = Log(f"{name}-{index}", hour=index)
            log.start(name)
            for record in written:
                if record[0] == "epoch":
                    log.epoch(name, record[1], record[2], " ".join(sorted(record[3])))
                else:
                    log.left(name, record[1])
            logs.append(log)
            if is_final:
                final.add(log.run)
    return logs, final


def kept_line(name: str, epoch: int = 2, left: bool = False) -> Kept:
    return Kept(CONVO, name, f"account-of-{name}", epoch, left)


class Histories(unittest.TestCase):
    def test_all_of_them_are_here(self):
        self.assertEqual(len(HISTORIES), 43)
        self.assertLessEqual(set(KEPT), {name for name, *_ in HISTORIES})

    def test_each_reads_as_the_table_says(self):
        for name, history, verdict, expected in HISTORIES:
            with self.subTest(history=name):
                kept = [kept_line(who, *line) for who, line in KEPT.get(name, {}).items()]
                conversation = one(*as_logs(history), kept)
                self.assertEqual(conversation.verdict, verdict)
                found = positions(conversation)
                self.assertEqual({who: found[who] for who in expected}, expected)


class Seats(unittest.TestCase):
    def test_a_seat_with_no_log_is_removed_when_every_newest_roster_lacks_it(self):
        logs = started("saro", "raya")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 1, "X1", ALL3)
            logs[name].epoch(name, 2, "X2", "saro raya")
        conversation = one(logs)
        self.assertEqual(positions(conversation), {"saro": "tip", "raya": "tip", "pax": "removed"})
        self.assertEqual((conversation.coverage.sharing, conversation.coverage.seats), (2, 2))

    def test_a_seat_one_branch_still_lists_is_not_removed(self):
        logs = started("saro", "raya")
        logs["saro"].epoch("saro", 1, "X1", ALL3)
        logs["saro"].epoch("saro", 2, "A2", "saro raya")
        logs["raya"].epoch("raya", 1, "X1", ALL3)
        logs["raya"].epoch("raya", 2, "B2", ALL3)
        self.assertEqual(positions(one(logs))["pax"], "no data")

    def test_a_seat_neither_branch_lists_is_removed_though_the_state_they_left_lists_it(self):
        logs = started("saro", "raya")
        logs["saro"].epoch("saro", 1, "X1", ALL3)
        logs["saro"].epoch("saro", 2, "A2", "saro raya")
        logs["raya"].epoch("raya", 1, "X1", ALL3)
        logs["raya"].epoch("raya", 2, "B2", "saro raya")
        self.assertEqual(positions(one(logs))["pax"], "removed")

    def test_a_seat_that_never_joined_and_was_removed_since_is_removed(self):
        logs = started("saro", "raya", "pax")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 1, "X1", ALL3)
            logs[name].epoch(name, 2, "X2", "saro raya")
        self.assertEqual(positions(one(logs))["pax"], "removed")

    def test_a_state_its_reporter_passed_in_a_later_run_keeps_no_seat(self):
        before, after = Log("saro-monday", hour=8), Log("saro-tuesday", hour=9)
        before.start("saro")
        before.epoch("saro", 1, "X1", "saro pax")
        after.start("saro")
        after.epoch("saro", 2, "X2", "saro raya")
        raya = Log("raya")
        raya.start("raya")
        raya.epoch("raya", 2, "X2", "saro raya")
        conversation = one([before, after, raya])
        self.assertEqual(positions(conversation), {"saro": "tip", "raya": "tip", "pax": "removed"})
        self.assertEqual((conversation.coverage.sharing, conversation.coverage.seats), (2, 2))

    def test_a_state_that_another_was_seen_to_follow_keeps_no_seat(self):
        logs = started("saro", "raya")
        logs["saro"].epoch("saro", 1, "X1", ALL3)
        logs["saro"].epoch("saro", 2, "X2", "saro raya")
        logs["raya"].epoch("raya", 1, "X1", ALL3)
        self.assertEqual(positions(one(logs)), {"saro": "tip", "raya": "behind", "pax": "removed"})

    def test_a_state_is_passed_only_by_a_later_epoch_its_reporter_wrote_afterwards(self):
        before, after = Log("saro-monday", hour=8), Log("saro-tuesday", hour=9)
        before.start("saro")
        before.epoch("saro", 2, "X2", "saro pax")
        after.start("saro")
        after.epoch("saro", 1, "X1", "saro raya")
        self.assertEqual(
            positions(one([before, after])),
            {"saro": "lineage unknown", "raya": "no data", "pax": "no data"},
        )

    def test_a_state_links_tie_to_a_side_of_a_split_is_never_passed(self):
        before, after = Log("tomo-monday", hour=8), Log("tomo-tuesday", hour=9)
        before.start("tomo")
        before.epoch("tomo", 1, "X1", "saro tomo")
        before.epoch("tomo", 2, "B2", "saro tomo")
        before.epoch("tomo", 3, "B3", "saro tomo pax")
        after.start("tomo")
        after.epoch("tomo", 5, "K5", "tomo kes")
        logs = started("saro", "kes")
        logs["saro"].epoch("saro", 1, "X1", "saro tomo")
        logs["saro"].epoch("saro", 2, "A2", "saro raya")
        logs["kes"].epoch("kes", 5, "K5", "tomo kes")
        conversation = one([before, after, *logs.values()])
        self.assertEqual(
            positions(conversation),
            dict(saro="lineage unknown", tomo="tip", kes="tip", raya="no data", pax="no data"),
        )
        self.assertEqual((conversation.coverage.sharing, conversation.coverage.seats), (3, 5))

    def test_a_reporter_back_on_its_side_of_a_split_does_not_pass_its_earlier_state_there(self):
        logs = started("saro", "tomo")
        for name in ("saro", "tomo"):
            logs[name].epoch(name, 1, "X1", "saro tomo pax")
        logs["saro"].epoch("saro", 2, "A2", "saro")
        logs["tomo"].epoch("tomo", 2, "B2", "saro tomo pax")
        logs["tomo"].left("tomo", 3)
        logs["tomo"].epoch("tomo", 4, "B4", "tomo kes")
        conversation = one(logs)
        self.assertEqual(conversation.verdict, "split at epoch 2")
        self.assertEqual(
            positions(conversation),
            dict(saro="lineage unknown", tomo="tip", kes="no data", pax="no data"),
        )
        self.assertEqual((conversation.coverage.sharing, conversation.coverage.seats), (2, 4))

    def test_a_state_on_a_side_of_a_later_split_is_never_passed(self):
        before, after = Log("tomo-monday", hour=8), Log("tomo-tuesday", hour=9)
        before.start("tomo")
        before.epoch("tomo", 3, "C3", "saro tomo pax")
        after.start("tomo")
        after.epoch("tomo", 5, "K5", "tomo kes")
        logs = started("saro", "raya", "kes")
        logs["saro"].epoch("saro", 2, "A2", "saro raya")
        logs["saro"].epoch("saro", 3, "A3", "saro raya")
        logs["raya"].epoch("raya", 2, "B2", "saro raya")
        logs["kes"].epoch("kes", 5, "K5", "tomo kes")
        conversation = one([before, after, *logs.values()])
        self.assertEqual(conversation.split_epoch, 2)
        self.assertEqual(positions(conversation)["pax"], "no data")

    def test_a_state_on_no_side_is_passed_though_the_logs_show_a_split(self):
        before, after = Log("saro-monday", hour=8), Log("saro-tuesday", hour=9)
        before.start("saro")
        before.epoch("saro", 1, "X1", "saro pax")
        after.start("saro")
        after.epoch("saro", 2, "A2", "saro raya")
        raya = Log("raya")
        raya.start("raya")
        raya.epoch("raya", 2, "B2", "saro raya")
        self.assertEqual(
            positions(one([before, after, raya])), {"saro": "tip", "raya": "tip", "pax": "removed"}
        )

    def test_a_leave_does_not_hide_the_roster_of_the_state_before_it(self):
        logs = started("saro", "raya")
        logs["raya"].epoch("raya", 1, "X1", "saro raya")
        logs["saro"].epoch("saro", 2, "X2", ALL3)
        logs["saro"].left("saro", 3)
        conversation = one(logs)
        self.assertEqual(
            positions(conversation),
            {"saro": "removed", "raya": "lineage unknown", "pax": "no data"},
        )
        self.assertEqual((conversation.coverage.sharing, conversation.coverage.seats), (1, 2))

    def test_a_seat_is_not_removed_when_no_state_was_reported(self):
        logs = started("saro", "pax")
        logs["saro"].left("saro", 2)
        logs["pax"].unreadable("pax", "77b4", 2, "epoch")
        self.assertEqual(positions(one(logs)), {"saro": "removed", "pax": "never joined"})

    def test_a_run_that_ended_below_the_commit_that_removed_it_reads_removed(self):
        logs = started("saro", "raya", "pax")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 5, "X5", ALL3)
            logs[name].epoch(name, 6, "X6", "saro raya")
        logs["pax"].epoch("pax", 5, "X5", ALL3)
        self.assertEqual(positions(one(logs, final={"pax"}))["pax"], "removed")

    def test_an_installation_no_roster_lists_is_not_in_the_report(self):
        logs = started("saro", "raya", "kes")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 1, "X1", "saro raya")
        self.assertEqual(sorted(positions(one(logs))), ["raya", "saro"])

    def test_a_seat_takes_its_account_from_a_roster_when_it_shares_no_log(self):
        logs = started("saro")
        logs["saro"].epoch("saro", 1, "X1", "saro raya")
        accounts = {seat.id: seat.account for seat in one(logs).installations}
        self.assertEqual(accounts, {"saro": "account-of-saro", "raya": "account-of-raya"})

    def test_coverage_counts_the_seats_that_are_neither_ended_nor_removed(self):
        five = "saro raya pax mira kes"
        logs = started("saro", "raya", "pax", "kes")
        logs["saro"].epoch("saro", 1, "X1", five)
        logs["raya"].epoch("raya", 1, "X1", five)
        logs["pax"].epoch("pax", 1, "X1", five)
        logs["pax"].left("pax", 2)
        conversation = one(logs, final={"raya"})
        self.assertEqual(
            positions(conversation),
            dict(saro="tip", raya="ended", pax="removed", mira="no data", kes="never joined"),
        )
        self.assertEqual((conversation.coverage.sharing, conversation.coverage.seats), (2, 3))


class KeptLines(unittest.TestCase):
    def group(self) -> Dict[str, Log]:
        """Raya and Pax at one state, whose roster lists Saro and Mira as well."""
        logs = started("raya", "pax")
        for name in ("raya", "pax"):
            logs[name].epoch(name, 3, "X3", ALL4)
        return logs

    def test_an_installation_known_only_by_its_line_is_ended_at_its_epoch_in_no_state(self):
        conversation = one(self.group(), kept=[kept_line("saro", epoch=2)])
        self.assertEqual(
            positions(conversation), dict(saro="ended", raya="tip", pax="tip", mira="no data")
        )
        saro = seat_of(conversation, "saro")
        self.assertEqual((saro.epoch, saro.state, saro.branch), (2, None, None))
        self.assertEqual((saro.runs, saro.history), ([], []))
        self.assertEqual((conversation.coverage.sharing, conversation.coverage.seats), (2, 3))

    def test_a_line_brings_no_state_no_roster_and_no_link(self):
        logs = self.group()
        without, kept = one(logs), one(logs, kept=[kept_line("kes", epoch=2)])
        self.assertEqual(positions(kept), dict(positions(without), kes="removed"))
        self.assertEqual(
            (kept.verdict, kept.states, kept.branches, kept.runs),
            (without.verdict, without.states, without.branches, without.runs),
        )

    def test_it_is_ended_when_no_roster_is_the_newest(self):
        logs = started("raya")
        logs["raya"].left("raya", 4)
        conversation = one(logs, kept=[kept_line("saro")])
        self.assertEqual(positions(conversation), dict(saro="ended", raya="removed"))

    def test_a_line_whose_last_record_was_a_leave_is_removed_whatever_a_roster_lists(self):
        conversation = one(self.group(), kept=[kept_line("saro", epoch=2, left=True)])
        saro = seat_of(conversation, "saro")
        self.assertEqual((saro.position, saro.epoch, saro.state), ("removed", 2, None))
        self.assertEqual((conversation.coverage.sharing, conversation.coverage.seats), (2, 3))
        logs = started("raya")
        logs["raya"].left("raya", 4)
        conversation = one(logs, kept=[kept_line("saro", left=True)])
        self.assertEqual(positions(conversation), dict(saro="removed", raya="removed"))

    def test_only_a_newest_roster_at_or_above_the_line_can_show_its_seat_removed(self):
        logs = started("raya", "pax")
        logs["raya"].epoch("raya", 1, "X1", "saro raya")
        logs["pax"].epoch("pax", 3, "Y3", "pax")
        read = {
            epoch: positions(one(logs, kept=[kept_line("saro", epoch)]))["saro"]
            for epoch in (1, 2, 3, 4)
        }
        # At 1 a roster that lists it is among them, and at 4 there is none.
        self.assertEqual(read, {1: "ended", 2: "removed", 3: "removed", 4: "ended"})

    def test_an_installation_with_an_epoch_or_a_left_record_there_goes_by_its_records(self):
        logs = self.group()
        logs["pax"].left("pax", 4)
        lines = [Kept(CONVO, "raya", "another", 9, True), Kept(CONVO, "pax", "another", 9, False)]
        conversation = one(logs, kept=lines)
        raya, pax = seat_of(conversation, "raya"), seat_of(conversation, "pax")
        self.assertEqual((raya.position, raya.epoch, raya.state), ("tip", 3, "X3"))
        self.assertEqual((pax.position, pax.epoch, len(pax.history)), ("removed", 4, 2))
        self.assertEqual((raya.account, pax.account), ("account-of-raya", "account-of-pax"))

    def test_an_installation_with_only_other_records_goes_by_its_line(self):
        again = Log("saro")
        again.start("saro")
        again.epoch("saro", 1, "Y1", "saro", convo="77c1e09b3d")
        again.sent("saro", "77b4", 2)
        records = [*one_log(self.group()), *again.records]
        here, there = report(records, kept=[kept_line("saro", epoch=2)])
        saro = seat_of(here, "saro")
        self.assertEqual((saro.position, saro.epoch, saro.state), ("ended", 2, None))
        self.assertEqual((saro.runs, saro.history), (["saro"], []))
        self.assertEqual(positions(here), dict(saro="ended", raya="tip", pax="tip", mira="no data"))
        self.assertEqual((here.coverage.sharing, here.coverage.seats), (2, 3))
        saro = seat_of(there, "saro")
        self.assertEqual((saro.position, saro.epoch, saro.state), ("tip", 1, "Y1"))

    def test_an_installation_no_roster_lists_is_a_seat_by_its_line(self):
        again = Log("kes")
        again.start("kes")
        for epoch, position in ((3, "removed"), (4, "ended")):
            conversation = one([*self.group().values(), again], kept=[kept_line("kes", epoch)])
            self.assertEqual(positions(conversation)["kes"], position)

    def test_the_account_is_the_lines_own_and_a_rosters_when_the_line_has_none(self):
        logs = self.group()
        own = Kept(CONVO, "saro", "an-account-of-its-own", 2, False)
        self.assertEqual(seat_of(one(logs, kept=[own]), "saro").account, "an-account-of-its-own")
        none = Kept(CONVO, "saro", None, 2, False)
        self.assertEqual(seat_of(one(logs, kept=[none]), "saro").account, "account-of-saro")

    def test_of_two_lines_for_one_installation_the_one_that_ended_later_is_read(self):
        later, earlier = kept_line("saro", 5), kept_line("saro", 2)
        for lines in ([later, earlier], [earlier, later]):
            self.assertEqual(seat_of(one(self.group(), kept=lines), "saro").epoch, 5)

    def test_a_line_of_another_conversation_is_not_read(self):
        other = Kept("77c1e09b3d", "kes", "account-of-kes", 2, False)
        self.assertNotIn("kes", positions(one(self.group(), kept=[other])))

    def test_a_conversation_no_record_names_is_not_reported(self):
        self.assertEqual(report([], kept=[kept_line("saro")]), [])


def one_log(logs: Dict[str, Log]) -> List[Record]:
    return [record for log in logs.values() for record in log.records]


class Chains(unittest.TestCase):
    def behind_or_not(self, restart: bool) -> Conversation:
        logs = started("saro", "raya", "pax")
        logs["saro"].epoch("saro", 1, "X1", ALL3)
        if restart:
            logs["saro"].start("saro")
        logs["saro"].epoch("saro", 2, "X2", ALL3)
        logs["raya"].epoch("raya", 2, "X2", ALL3)
        logs["pax"].epoch("pax", 1, "X1", ALL3)
        return one(logs)

    def test_two_records_in_a_row_link_their_states(self):
        conversation = self.behind_or_not(restart=False)
        self.assertEqual(conversation.verdict, "whole, members behind")
        self.assertEqual(positions(conversation)["pax"], "behind")

    def test_a_run_record_of_the_installation_restarts_its_chain(self):
        conversation = self.behind_or_not(restart=True)
        self.assertEqual(conversation.verdict, "whole as far as seen")
        self.assertEqual(positions(conversation)["pax"], "lineage unknown")

    def test_one_log_of_many_installations_links_each_ones_own_records(self):
        log = Log("everyone")
        for name in ALL3.split():
            log.start(name)
        log.epoch("saro", 1, "X1", ALL3)
        log.epoch("pax", 1, "X1", ALL3)
        log.epoch("raya", 2, "X2", ALL3)
        log.epoch("saro", 2, "X2", ALL3)
        conversation = one([log])
        self.assertEqual(positions(conversation), {"saro": "tip", "raya": "tip", "pax": "behind"})
        (first, second) = conversation.states
        self.assertEqual(first.after, [])
        self.assertEqual([(parent.epoch, parent.state) for parent in second.after], [(1, "X1")])

    def test_another_installations_run_record_restarts_nothing(self):
        log = Log("everyone")
        log.start("saro")
        log.epoch("saro", 1, "X1", "saro raya")
        log.start("raya")
        log.epoch("saro", 2, "X2", "saro raya")
        (_, second) = one([log]).states
        self.assertEqual(len(second.after), 1)

    def test_a_repeated_record_does_not_put_a_state_behind_itself(self):
        logs = started("saro", "raya")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 1, "X1", "saro raya")
            logs[name].epoch(name, 1, "X1", "saro raya")
        conversation = one(logs)
        self.assertEqual(conversation.verdict, "whole")
        self.assertEqual(positions(conversation), {"saro": "tip", "raya": "tip"})

    def test_an_installations_runs_are_read_in_the_order_of_their_stamps(self):
        logs = started("saro", "raya")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 1, "X1", ALL3)
            logs[name].epoch(name, 2, "X2", "saro raya")
            logs[name].epoch(name, 3, "X3", ALL3)
        earlier, later = Log("pax-b", hour=8), Log("pax-a", hour=9)
        earlier.start("pax")
        earlier.epoch("pax", 1, "X1", ALL3)
        earlier.left("pax", 2)
        later.start("pax")
        later.epoch("pax", 3, "X3", ALL3)
        conversation = one([*logs.values(), earlier, later])
        self.assertEqual(positions(conversation)["pax"], "tip")
        pax = seat_of(conversation, "pax")
        self.assertEqual(pax.runs, ["pax-b", "pax-a"])
        self.assertEqual(
            [(step.kind, step.epoch, step.state) for step in pax.history],
            [("epoch", 1, "X1"), ("left", 2, None), ("epoch", 3, "X3")],
        )

    def test_the_guard_reads_a_roster_without_its_writer_as_a_leave(self):
        logs = started("saro", "pax")
        logs["saro"].epoch("saro", 1, "X1", "saro pax")
        logs["saro"].epoch("saro", 2, "X2", "saro")
        logs["pax"].epoch("pax", 1, "X1", "saro pax")
        logs["pax"].epoch("pax", 2, "Z2", "saro")
        conversation = one(logs)
        self.assertEqual(
            [(state.epoch, state.state) for state in conversation.states], [(1, "X1"), (2, "X2")]
        )
        pax = seat_of(conversation, "pax")
        self.assertEqual((pax.position, pax.epoch, pax.state), ("removed", 2, None))
        self.assertEqual([step.kind for step in pax.history], ["epoch", "left"])


class RunsSeenTwice(unittest.TestCase):
    def group(self, run: str, epochs: int, hour: int = 10) -> Log:
        """One log of Saro and Raya, as long as it was when they had reached an epoch."""
        log = Log(run, hour)
        for name in ("saro", "raya"):
            log.start(name)
        for epoch in range(1, epochs + 1):
            for name in ("saro", "raya"):
                log.epoch(name, epoch, f"X{epoch}", "saro raya")
        return log

    def test_only_the_longer_length_of_a_run_is_read_whatever_the_names(self):
        for earlier, whole in (("group.snapshot", "group"), ("earlier", "group")):
            with self.subTest(earlier=earlier):
                conversation = one([self.group(earlier, 1), self.group(whole, 2)])
                self.assertEqual(conversation.verdict, "whole")
                self.assertEqual(positions(conversation), {"saro": "tip", "raya": "tip"})
                self.assertEqual(seat_of(conversation, "saro").runs, [whole])
                self.assertEqual([run.name for run in conversation.runs], [whole])

    def test_two_copies_of_a_run_are_read_once(self):
        copies = [self.group("monday", 2), self.group("tuesday", 2)]
        for copy in copies:
            copy.unreadable("saro", "77b4", 2, "epoch")
        conversation = one(copies)
        self.assertEqual(conversation.verdict, "whole")
        self.assertEqual(seat_of(conversation, "saro").runs, ["monday"])
        self.assertEqual(conversation.unmatched_unreadable, 1)

    def test_a_mark_on_either_copy_of_a_run_says_its_writer_is_gone(self):
        for marked in ("monday", "tuesday"):
            with self.subTest(marked=marked):
                copies = [self.group("monday", 2), self.group("tuesday", 2)]
                conversation = one(copies, final=[marked])
                self.assertEqual(positions(conversation), {"saro": "ended", "raya": "ended"})
                self.assertEqual(
                    [(run.name, run.final) for run in conversation.runs], [("monday", True)]
                )

    def test_a_mark_on_a_run_at_an_earlier_length_counts_for_the_longer(self):
        for earlier, whole in (("group.snapshot", "group"), ("earlier", "group")):
            with self.subTest(earlier=earlier):
                logs = [self.group(earlier, 1), self.group(whole, 2)]
                conversation = one(logs, final=[earlier])
                self.assertEqual(positions(conversation), {"saro": "ended", "raya": "ended"})
                self.assertEqual(
                    [(run.name, run.final) for run in conversation.runs], [(whole, True)]
                )
                self.assertEqual(positions(one(logs)), {"saro": "tip", "raya": "tip"})

    def test_a_mark_on_a_run_counts_for_no_run_that_only_starts_as_it_does(self):
        first, second = self.group("monday", 1), self.group("tuesday", 0)
        second.epoch("saro", 2, "X2", "saro raya")
        second.epoch("raya", 2, "X2", "saro raya")
        conversation = one([first, second], final=["monday"])
        self.assertEqual(positions(conversation), {"saro": "tip", "raya": "tip"})
        self.assertEqual(
            [(run.name, run.final) for run in conversation.runs],
            [("monday", True), ("tuesday", False)],
        )

    def test_a_run_that_only_starts_at_the_same_time_as_another_is_read(self):
        first, second = self.group("monday", 1), self.group("tuesday", 0)
        second.epoch("saro", 2, "X2", "saro raya")
        second.epoch("raya", 2, "X2", "saro raya")
        conversation = one([first, second])
        self.assertEqual(seat_of(conversation, "saro").runs, ["monday", "tuesday"])
        self.assertEqual([state.state for state in conversation.states], ["X1", "X2"])

    def test_one_installations_records_alone_in_a_shorter_run_are_read_once(self):
        whole = self.group("group", 2)
        own = [record for record in whole.records if record.installation == "saro"]
        for name in ("alone", "saro"):
            with self.subTest(name=name):
                alone = Log(name)
                alone.records = [
                    replace(record, run=name, line=line) for line, record in enumerate(own[:2], 1)
                ]
                conversation = one([whole, alone])
                self.assertEqual(conversation.verdict, "whole")
                self.assertEqual(positions(conversation), {"saro": "tip", "raya": "tip"})
                self.assertEqual(seat_of(conversation, "saro").runs, ["group"])
                self.assertEqual([run.name for run in conversation.runs], ["group"])

    def test_a_run_that_says_what_another_began_with_at_other_stamps_is_read(self):
        conversation = one([self.group("monday", 1, hour=8), self.group("tuesday", 2, hour=9)])
        saro = seat_of(conversation, "saro")
        self.assertEqual(saro.runs, ["monday", "tuesday"])
        self.assertEqual([step.epoch for step in saro.history], [1, 1, 2])
        self.assertEqual([run.name for run in conversation.runs], ["monday", "tuesday"])

    def test_runs_that_open_at_one_stamp_go_by_their_records_whatever_their_names(self):
        for lower, higher in (("monday", "tuesday"), ("tuesday", "monday")):
            with self.subTest(lower=lower):
                first, second = self.group(lower, 1), self.group(higher, 0)
                second.epoch("saro", 2, "X2", "saro raya")
                second.epoch("raya", 2, "X2", "saro raya")
                conversation = one([first, second])
                self.assertEqual(conversation.verdict, "whole")
                self.assertEqual(positions(conversation), {"saro": "tip", "raya": "tip"})
                self.assertEqual(seat_of(conversation, "saro").runs, [lower, higher])


class Branches(unittest.TestCase):
    def split(self) -> Conversation:
        logs = started("saro", "raya", "pax", "mira")
        for name in ("saro", "raya", "pax"):
            logs[name].epoch(name, 1, "X1", "saro raya pax mira")
        logs["mira"].epoch("mira", 1, "X1", "saro raya pax mira")
        logs["saro"].epoch("saro", 2, "B2", "saro raya pax mira")
        logs["raya"].epoch("raya", 2, "A2", "saro raya pax mira kes")
        logs["pax"].epoch("pax", 2, "A2", "saro raya pax mira kes")
        logs["pax"].epoch("pax", 3, "A3", "saro raya pax mira kes")
        return one(logs)

    def test_the_branch_more_installations_are_on_comes_first(self):
        conversation = self.split()
        self.assertEqual(conversation.split_epoch, 2)
        self.assertEqual(
            [
                (b.label, b.since, b.epoch, b.state, b.members, b.installations)
                for b in conversation.branches
            ],
            [("A", 2, 3, "A3", 5, ["pax", "raya"]), ("B", 2, 2, "B2", 4, ["saro"])],
        )

    def test_the_states_below_the_split_are_on_no_branch(self):
        conversation = self.split()
        self.assertEqual(
            [(state.epoch, state.state, state.branch) for state in conversation.states],
            [(1, "X1", None), (2, "A2", "A"), (2, "B2", "B"), (3, "A3", "A")],
        )
        branch = {seat.id: seat.branch for seat in conversation.installations}
        self.assertEqual(branch, {"saro": "B", "raya": "A", "pax": "A", "mira": None, "kes": None})

    def test_a_state_says_who_held_it_and_what_it_holds(self):
        (_, second, _, _) = self.split().states
        self.assertEqual((second.members, second.held_by), (5, ["pax", "raya"]))
        self.assertEqual(second.roster, ["kes", "mira", "pax", "raya", "saro"])

    def test_branches_of_one_size_go_by_the_fingerprint_of_their_first_state(self):
        logs = started("saro", "raya")
        logs["saro"].epoch("saro", 1, "X1", "saro raya")
        logs["saro"].epoch("saro", 2, "N2", "saro raya")
        logs["saro"].epoch("saro", 3, "A3", "saro raya")
        logs["raya"].epoch("raya", 1, "X1", "saro raya")
        logs["raya"].epoch("raya", 2, "M2", "saro raya")
        conversation = one(logs)
        self.assertEqual(
            [(b.label, b.state) for b in conversation.branches], [("A", "M2"), ("B", "A3")]
        )

    def test_a_group_that_never_split_is_one_branch(self):
        logs = started("raya", "kes")
        logs["raya"].epoch("raya", 5, "X5", ALL3)
        logs["kes"].epoch("kes", 6, "Y6", ALL3 + " kes")
        conversation = one(logs)
        self.assertEqual(
            [(b.label, b.since, b.epoch, b.installations) for b in conversation.branches],
            [("A", 5, 6, ["kes", "raya"])],
        )
        self.assertIsNone(conversation.split_epoch)

    def test_a_branch_that_splits_again_is_shared_by_the_two_it_becomes(self):
        logs = started("saro", "raya", "pax", "mira")
        for name in ("saro", "raya", "pax", "mira"):
            logs[name].epoch(name, 1, "X1", "saro raya pax mira")
        for name in ("saro", "raya", "mira"):
            logs[name].epoch(name, 2, "A2", "saro raya pax mira")
        logs["pax"].epoch("pax", 2, "B2", "saro raya pax mira")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 3, "A3", "saro raya pax mira")
        logs["mira"].epoch("mira", 3, "C3", "saro raya pax mira")
        conversation = one(logs)
        self.assertEqual(conversation.verdict, "split at epoch 2")
        self.assertEqual(
            [(b.label, b.state, b.installations) for b in conversation.branches],
            [("A", "A3", ["raya", "saro"]), ("B", "B2", ["pax"]), ("C", "C3", ["mira"])],
        )
        shared = [state.state for state in conversation.states if state.branch is None]
        self.assertEqual(shared, ["X1", "A2"])

    def test_two_states_linked_from_one_neither_following_the_other_are_two_branches(self):
        logs = started("saro", "raya", "pax")
        for name in ("saro", "raya", "pax"):
            logs[name].epoch(name, 2, "X2", ALL3)
        for name in ("saro", "raya"):
            logs[name].epoch(name, 3, "A3", ALL3)
        logs["pax"].epoch("pax", 3, "B3", ALL3)
        logs["saro"].epoch("saro", 4, "A4", ALL3)
        logs["raya"].epoch("raya", 5, "A5", ALL3)
        conversation = one(logs)
        self.assertEqual(conversation.verdict, "split at epoch 3")
        self.assertEqual(
            [(b.label, b.since, b.state, b.installations) for b in conversation.branches],
            [("A", 4, "A4", ["saro"]), ("B", 5, "A5", ["raya"]), ("C", 3, "B3", ["pax"])],
        )
        on_none = [state.state for state in conversation.states if state.branch is None]
        self.assertEqual(on_none, ["X2", "A3"])

    def two_behind(self, saro: List[int], raya: List[int]) -> Conversation:
        """A split at epoch 3 where Mira stays at A3 and Kes at A4.

        Saro and Raya write the states of side A at these epochs and leave; Pax left side B.
        """
        logs = started("saro", "raya", "mira", "kes", "pax")
        for name in ("saro", "mira", "pax"):
            logs[name].epoch(name, 2, "X2", ALL5)
        logs["mira"].epoch("mira", 3, "A3", ALL5)
        logs["kes"].epoch("kes", 4, "A4", ALL5)
        logs["pax"].epoch("pax", 3, "B3", ALL5)
        logs["pax"].left("pax", 4)
        for name, epochs in (("saro", saro), ("raya", raya)):
            for epoch in epochs:
                logs[name].epoch(name, epoch, f"A{epoch}", ALL5)
            logs[name].left(name, epochs[-1] + 1)
        return one(logs)

    def test_two_live_states_links_lead_on_from_to_one_state_are_one_branch_and_one_side(self):
        for saro, raya in (([3, 5], [4, 5]), ([3, 6], [4, 5, 6])):
            with self.subTest(saro=saro, raya=raya):
                conversation = self.two_behind(saro, raya)
                self.assertEqual(conversation.verdict, "split at epoch 3, one side live")
                self.assertEqual(
                    [(b.label, b.since, b.installations) for b in conversation.branches],
                    [("A", 3, ["kes", "mira"]), ("B", 3, [])],
                )

    def test_two_live_states_links_lead_on_from_to_two_states_are_two_branches_and_two_sides(self):
        conversation = self.two_behind(saro=[3, 5], raya=[4, 6])
        self.assertEqual(conversation.verdict, "split at epoch 3")
        self.assertEqual(
            [(b.label, b.since, b.state, b.installations) for b in conversation.branches],
            [("A", 3, "A5", ["mira"]), ("B", 4, "A6", ["kes"]), ("C", 3, "B3", [])],
        )

    def test_a_line_of_states_above_a_split_is_one_branch(self):
        logs = started("saro", "raya", "pax")
        for name in ("saro", "raya", "pax"):
            logs[name].epoch(name, 2, "X2", ALL3)
        logs["pax"].epoch("pax", 3, "B3", ALL3)
        logs["saro"].epoch("saro", 3, "A3", ALL3)
        logs["saro"].epoch("saro", 5, "A5", ALL3)
        logs["raya"].epoch("raya", 3, "A3", ALL3)
        logs["raya"].epoch("raya", 4, "A4", ALL3)
        logs["raya"].epoch("raya", 5, "A5", ALL3)
        conversation = one(logs)
        self.assertEqual(
            [(b.label, b.since, b.state, b.installations) for b in conversation.branches],
            [("A", 3, "A5", ["raya", "saro"]), ("B", 3, "B3", ["pax"])],
        )

    def test_the_twenty_seventh_branch_is_aa(self):
        names = [f"member{index:02d}" for index in range(28)]
        logs = started(*names)
        for index, name in enumerate(names):
            logs[name].epoch(name, 1, f"S{index:02d}", " ".join(names))
        conversation = one(logs)
        self.assertEqual(conversation.verdict, "split seen from epoch 1")
        self.assertEqual([branch.label for branch in conversation.branches][-3:], ["Z", "AA", "AB"])


class Messages(unittest.TestCase):
    def conversation(self) -> Conversation:
        four = "saro raya pax mira"
        logs = started("saro", "raya", "pax", "kes")
        for name in ("saro", "raya", "pax"):
            logs[name].epoch(name, 1, "X1", four)
        logs["saro"].epoch("saro", 2, "A2", four + " kes")
        logs["raya"].epoch("raya", 2, "A2", four + " kes")
        logs["kes"].epoch("kes", 2, "A2", four + " kes")
        logs["pax"].epoch("pax", 2, "B2", four)
        # Raya's post, written later than Saro's by the clock, on a branch Pax is not on.
        logs["raya"].hour = 12
        logs["raya"].sent("raya", "e3b9", 2)
        logs["saro"].received("saro", "e3b9", "raya", 2)
        logs["pax"].unreadable("pax", "e3b9", 2, "decrypt")
        logs["saro"].hour = 11
        logs["saro"].sent("saro", "8f21", 1)
        logs["raya"].received("raya", "8f21", "saro", 1)
        logs["raya"].unreadable("raya", "8f21", 1, "decrypt")
        logs["pax"].unreadable("pax", "77b4", 2, "epoch")
        logs["pax"].unreadable("pax", "0be7", 2, "epoch")
        logs["kes"].received("kes", "d2c5", "mira", 2)
        return one(logs)

    def test_a_row_for_each_post_in_the_order_sent(self):
        first, second = self.conversation().messages
        self.assertEqual(
            [(post.frame, post.sender, post.epoch, post.state) for post in (first, second)],
            [("8f21", "saro", 1, "X1"), ("e3b9", "raya", 2, "A2")],
        )
        self.assertEqual((second.at.run, second.at.line), ("raya", 4))

    def test_every_installation_has_a_cell(self):
        first, second = self.conversation().messages
        self.assertEqual(
            first.cells,
            dict(saro="sent", raya="read", pax="never got", mira="no log", kes="not a member"),
        )
        self.assertEqual(
            second.cells,
            dict(saro="read", raya="sent", pax="unreadable", mira="no log", kes="never got"),
        )
        self.assertEqual((first.why, second.why), ({}, {"pax": "decrypt"}))

    def test_unreadable_frames_no_post_names_are_counted_apart(self):
        self.assertEqual(self.conversation().unmatched_unreadable, 2)

    def test_a_post_sent_at_an_epoch_its_sender_wrote_no_record_for(self):
        logs = started("saro")
        logs["saro"].epoch("saro", 1, "X1", "saro raya")
        logs["saro"].sent("saro", "8f21", 4)
        (message,) = one(logs).messages
        self.assertEqual((message.state, message.cells), (None, {"saro": "sent", "raya": "no log"}))


class Runs(unittest.TestCase):
    def test_a_runs_delivery_changes_are_listed_with_their_times(self):
        logs = started("saro", "raya")
        logs["saro"].delivery("initialising")
        logs["saro"].epoch("saro", 1, "X1", "saro raya")
        logs["saro"].delivery("online")
        logs["raya"].epoch("raya", 1, "X1", "saro raya")
        stranger = Log("kes")
        stranger.start("kes")
        stranger.delivery("online")
        raya, saro = one([*logs.values(), stranger], final={"raya"}).runs
        self.assertEqual((raya.name, raya.final, raya.delivery), ("raya", True, []))
        self.assertEqual((saro.name, saro.final), ("saro", False))
        self.assertEqual(
            [(change.state, change.at.line, change.at.time) for change in saro.delivery],
            [
                ("initialising", 2, "2026-10-08T10:00:02.000Z"),
                ("online", 4, "2026-10-08T10:00:04.000Z"),
            ],
        )


class Reports(unittest.TestCase):
    def records(self) -> List[Record]:
        logs = started("saro", "raya", "pax")
        for name in ("saro", "raya", "pax"):
            logs[name].epoch(name, 1, "X1", ALL3)
            logs[name].epoch(name, 1, "Y1", ALL3, convo="77c1e09b3d")
        logs["saro"].epoch("saro", 2, "X2", ALL3)
        logs["raya"].epoch("raya", 2, "W2", ALL3)
        logs["saro"].sent("saro", "8f21", 2)
        return [record for log in logs.values() for record in log.records]

    def test_conversations_are_reported_apart_and_by_id(self):
        first, second = report(self.records())
        self.assertEqual((first.id, first.verdict), ("2a30d6a2c1", "split at epoch 2"))
        self.assertEqual((second.id, second.verdict), ("77c1e09b3d", "whole"))

    def test_the_order_of_the_records_does_not_matter(self):
        records = self.records()
        expected = report(records)
        shuffler = random.Random(223)
        for _ in range(20):
            shuffler.shuffle(records)
            self.assertEqual(report(records), expected)

    def test_no_records_no_conversations(self):
        self.assertEqual(report([]), [])

    def test_only_the_conversations_asked_for_are_reported(self):
        records = self.records()
        first, second = report(records)
        self.assertEqual(report(records, only=["77c1e09b3d"]), [second])
        self.assertEqual(report(records, only=["2a30d6a2c1", "77c1e09b3d"]), [first, second])
        self.assertEqual(report(records, only=[]), [])


if __name__ == "__main__":
    unittest.main()
