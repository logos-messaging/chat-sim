import random
import unittest
from collections import Counter
from dataclasses import replace
from typing import Dict, List, Set, Tuple
from unittest import mock

from forks.records import Record
from forks.rule import (
    LANES_IN_SIGHT,
    POSITIONS,
    SHOWN_CELLS,
    Commit,
    Conversation,
    Fates,
    Held,
    Installation,
    Kept,
    Mark,
    Message,
    Range,
    Span,
    State,
    as_json,
    report,
)

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

    def epoch(
        self, name: str, epoch: int, state: str, roster: str, convo: str = CONVO, **applied: str
    ) -> None:
        members = tuple((member, f"account-of-{member}") for member in roster.split())
        self._add(
            "epoch",
            installation=name,
            convo=convo,
            epoch=epoch,
            state=state,
            members=len(members),
            roster=members,
            **applied,
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


PAIR = "saro raya"
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

    def test_each_branch_is_a_row_under_the_one_it_leaves_and_the_later_it_leaves_the_nearer(self):
        # Pax leaves Saro's and Raya's line at T2; Mira and Kes leave it at X1, and each other
        # at L3.
        logs = started("saro", "raya", "pax", "mira", "kes")
        for name in ("saro", "raya", "pax", "mira", "kes"):
            logs[name].epoch(name, 1, "X1", ALL5)
        for name in ("saro", "raya", "pax"):
            logs[name].epoch(name, 2, "T2", ALL5)
        for name in ("saro", "raya"):
            logs[name].epoch(name, 3, "T3", ALL5)
            logs[name].epoch(name, 4, "T4", ALL5)
        logs["pax"].epoch("pax", 3, "P3", ALL5)
        for name in ("mira", "kes"):
            logs[name].epoch(name, 2, "L2", ALL5)
            logs[name].epoch(name, 3, "L3", ALL5)
        logs["mira"].epoch("mira", 4, "M4", ALL5)
        logs["kes"].epoch("kes", 4, "K4", ALL5)
        conversation = one(logs)
        self.assertEqual(
            [(b.label, b.state) for b in conversation.branches],
            [("A", "T4"), ("B", "K4"), ("C", "M4"), ("D", "P3")],
        )
        self.assertEqual(
            [(*node(state), state.lineage, state.row) for state in conversation.states],
            [
                (1, "X1", "shared", 0),
                (2, "L2", "shared", 2),
                (2, "T2", "shared", 0),
                (3, "L3", "shared", 2),
                (3, "P3", "branch", 1),
                (3, "T3", "branch", 0),
                (4, "K4", "branch", 2),
                (4, "M4", "branch", 3),
                (4, "T4", "branch", 0),
            ],
        )

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


class Commits(unittest.TestCase):
    def test_a_state_says_the_commit_its_holders_named_and_how_many_of_them_named_it(self):
        logs = started("saro", "raya", "kes")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 1, "X1", PAIR)
            logs[name].epoch(name, 2, "X2", PAIR + " kes", commit="c2", by="saro")
        # Kes joined there from a welcome, and applied no commit.
        logs["kes"].epoch("kes", 2, "X2", PAIR + " kes")
        first, second = one(logs).states
        self.assertEqual(first.commits, [])
        self.assertEqual((second.holders, second.held_by), (3, ["kes", "raya", "saro"]))
        self.assertEqual(second.commits, [Commit("c2", "saro", 2)])

    def test_one_state_under_two_hashes_names_both_the_most_named_first(self):
        logs = started("saro", "raya", "pax", "mira")
        for name in ("saro", "raya", "pax"):
            logs[name].epoch(name, 2, "X2", ALL4, commit="d2", by="saro")
        logs["mira"].epoch("mira", 2, "X2", ALL4, commit="c2", by="saro")
        (state,) = one(logs).states
        self.assertEqual(state.holders, 4)
        self.assertEqual(state.commits, [Commit("d2", "saro", 3), Commit("c2", "saro", 1)])

    def test_commits_as_many_named_go_by_their_hash_and_then_by_who_committed(self):
        logs = started("saro", "raya", "pax", "mira")
        named = {"saro": ("d2", "pax"), "raya": ("c2", "saro"), "pax": ("c2", "raya")}
        for name, (commit, by) in named.items():
            logs[name].epoch(name, 2, "X2", ALL4, commit=commit, by=by)
        logs["mira"].epoch("mira", 2, "X2", ALL4, commit="d2", by="mira")
        (state,) = one(logs).states
        self.assertEqual(
            state.commits,
            [
                Commit("c2", "raya", 1),
                Commit("c2", "saro", 1),
                Commit("d2", "mira", 1),
                Commit("d2", "pax", 1),
            ],
        )

    def test_an_installation_that_reports_a_state_again_names_its_commit_once(self):
        for again in ({}, dict(commit="c2", by="saro")):
            with self.subTest(again=again):
                logs = started("saro", "raya")
                for name in ("saro", "raya"):
                    logs[name].epoch(name, 2, "X2", PAIR, commit="c2", by="saro")
                # Saro's application starts again, and reports the state it holds.
                logs["saro"].start("saro")
                logs["saro"].epoch("saro", 2, "X2", PAIR, **again)
                (state,) = one(logs).states
                self.assertEqual(state.holders, 2)
                self.assertEqual(state.commits, [Commit("c2", "saro", 2)])

    def test_an_installation_whose_records_of_a_state_disagree_names_each_commit_once(self):
        logs = started("saro", "raya")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 2, "X2", PAIR, commit="c2", by="saro")
        logs["saro"].start("saro")
        logs["saro"].epoch("saro", 2, "X2", PAIR, commit="d2", by="raya")
        (state,) = one(logs).states
        self.assertEqual(state.holders, 2)
        self.assertEqual(state.commits, [Commit("c2", "saro", 2), Commit("d2", "raya", 1)])

    def test_who_committed_need_not_be_a_seat_of_the_conversation(self):
        logs = started("saro", "raya")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 2, "X2", PAIR, commit="c2", by="tomo")
        conversation = one(logs)
        self.assertEqual(conversation.states[0].commits, [Commit("c2", "tomo", 2)])
        self.assertEqual(sorted(positions(conversation)), ["raya", "saro"])

    def test_each_branch_of_a_split_says_the_commits_of_its_first_state(self):
        logs = started("saro", "raya", "pax", "mira")
        for name in ("saro", "raya", "pax", "mira"):
            logs[name].epoch(name, 2, "X2", ALL4, commit="c2", by="saro")
        for name in ("saro", "raya", "mira"):
            logs[name].epoch(name, 3, "A3", ALL4, commit="a3", by="saro")
        logs["pax"].epoch("pax", 3, "B3", ALL4, commit="b3", by="raya")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 4, "A4", ALL4, commit="a4", by="mira")
        conversation = one(logs)
        self.assertEqual(conversation.verdict, "split at epoch 3")
        self.assertEqual(
            [(b.label, b.since, b.state, b.first_commits) for b in conversation.branches],
            [
                ("A", 3, "A4", [Commit("a3", "saro", 3)]),
                ("B", 3, "B3", [Commit("b3", "raya", 1)]),
            ],
        )
        self.assertEqual(
            [(state.state, state.commits) for state in conversation.states],
            [
                ("X2", [Commit("c2", "saro", 4)]),
                ("A3", [Commit("a3", "saro", 3)]),
                ("B3", [Commit("b3", "raya", 1)]),
                ("A4", [Commit("a4", "mira", 2)]),
            ],
        )

    def test_a_branch_whose_first_state_nobody_named_a_commit_for_says_none(self):
        logs = started("saro", "raya", "kes")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 2, "X2", ALL3, commit="c2", by="saro")
        logs["saro"].epoch("saro", 3, "A3", ALL3, commit="a3", by="saro")
        logs["raya"].epoch("raya", 3, "B3", ALL3 + " kes", commit="b3", by="raya")
        # Kes joined a state of its own from a welcome, and went on by a commit.
        logs["kes"].epoch("kes", 3, "K3", ALL3 + " kes")
        logs["kes"].epoch("kes", 4, "K4", ALL3 + " kes", commit="k4", by="kes")
        conversation = one(logs)
        self.assertEqual(
            [(b.since, b.state, b.first_commits) for b in conversation.branches],
            [
                (3, "A3", [Commit("a3", "saro", 1)]),
                (3, "B3", [Commit("b3", "raya", 1)]),
                (3, "K4", []),
            ],
        )

    def test_the_branches_a_branch_splits_into_say_the_commits_they_began_with(self):
        logs = started("saro", "raya", "pax", "mira")
        for name in ("saro", "raya", "pax", "mira"):
            logs[name].epoch(name, 1, "X1", ALL4)
        for name in ("saro", "raya", "mira"):
            logs[name].epoch(name, 2, "A2", ALL4, commit="a2", by="saro")
        logs["pax"].epoch("pax", 2, "B2", ALL4, commit="b2", by="pax")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 3, "A3", ALL4, commit="a3", by="raya")
        logs["mira"].epoch("mira", 3, "C3", ALL4, commit="c3", by="mira")
        conversation = one(logs)
        self.assertEqual(conversation.verdict, "split at epoch 2")
        self.assertEqual(
            [(b.label, b.since, b.state, b.first_commits) for b in conversation.branches],
            [
                ("A", 3, "A3", [Commit("a3", "raya", 2)]),
                ("B", 2, "B2", [Commit("b2", "pax", 1)]),
                ("C", 3, "C3", [Commit("c3", "mira", 1)]),
            ],
        )
        # The state the two came from is on no branch, and is the first state of none.
        (shared,) = [state for state in conversation.states if state.state == "A2"]
        self.assertEqual((shared.branch, shared.commits), (None, [Commit("a2", "saro", 3)]))

    def test_a_branch_that_begins_in_two_states_says_the_commits_of_both(self):
        logs = started("saro", "raya", "pax", "mira")
        logs["saro"].epoch("saro", 3, "A3", ALL4, commit="a3", by="saro")
        for name in ("raya", "mira"):
            logs[name].epoch(name, 3, "B3", ALL4, commit="b3", by="raya")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 4, "C4", ALL4, commit="c4", by="saro")
        alone = [Commit("b3", "raya", 2), Commit("a3", "saro", 1)]
        (branch,) = one(logs).branches
        self.assertEqual((branch.since, branch.state, branch.first_commits), (3, "C4", alone))
        logs["pax"].epoch("pax", 3, "D3", ALL4, commit="d3", by="pax")
        self.assertEqual(
            [(b.label, b.since, b.state, b.first_commits) for b in one(logs).branches],
            [("A", 3, "C4", alone), ("B", 3, "D3", [Commit("d3", "pax", 1)])],
        )

    def test_an_installation_counts_once_for_a_commit_it_names_at_two_first_states(self):
        logs = started("saro", "raya")
        for state in ("A3", "B3"):
            # Saro's application starts again, and holds another state at the epoch.
            logs["saro"].start("saro")
            logs["saro"].epoch("saro", 3, state, PAIR, commit="c3", by="raya")
            logs["saro"].epoch("saro", 4, "C4", PAIR)
        logs["raya"].epoch("raya", 3, "B3", PAIR, commit="c3", by="raya")
        (branch,) = one(logs).branches
        self.assertEqual((branch.since, branch.first_commits), (3, [Commit("c3", "raya", 2)]))

    def test_the_one_branch_of_a_group_that_never_split_says_those_of_the_first_state_held(self):
        logs = started("saro", "raya")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 5, "X5", PAIR, commit="c5", by="raya")
            logs[name].epoch(name, 6, "X6", PAIR, commit="c6", by="saro")
        (branch,) = one(logs).branches
        self.assertEqual((branch.since, branch.first_commits), (5, [Commit("c5", "raya", 2)]))

    def test_a_record_that_is_read_as_a_leave_names_no_commit(self):
        logs = started("saro", "pax")
        logs["saro"].epoch("saro", 1, "X1", "saro pax")
        logs["saro"].epoch("saro", 2, "X2", "saro", commit="c2", by="saro")
        logs["pax"].epoch("pax", 1, "X1", "saro pax")
        logs["pax"].epoch("pax", 2, "X2", "saro", commit="d2", by="saro")
        (_, second) = one(logs).states
        self.assertEqual((second.held_by, second.commits), (["saro"], [Commit("c2", "saro", 1)]))

    def test_copies_of_a_run_are_one_run_whatever_commit_each_names(self):
        named = (dict(commit="c2", by="saro"), dict(commit="d2", by="raya"), {})
        pairs = [(ours, theirs) for ours in named for theirs in named if ours != theirs]
        for ours, theirs in pairs:
            with self.subTest(ours=ours, theirs=theirs):
                copies = [Log("monday"), Log("tuesday")]
                for copy, applied in zip(copies, (ours, theirs)):
                    copy.start("saro")
                    copy.epoch("saro", 2, "X2", PAIR, **applied)
                conversation = one(copies)
                self.assertEqual([run.name for run in conversation.runs], ["monday"])
                (state,) = conversation.states
                self.assertEqual(state.commits, [Commit(holders=1, **ours)] if ours else [])

    def test_runs_that_open_at_one_stamp_go_in_one_order_whatever_commit_each_names(self):
        for first, second in (("c2", "d2"), ("d2", "c2")):
            with self.subTest(first=first):
                logs = [Log("monday"), Log("tuesday")]
                for log, commit, state in zip(logs, (first, second), ("A3", "B3")):
                    log.start("saro")
                    log.epoch("saro", 2, "X2", PAIR, commit=commit, by="saro")
                    log.epoch("saro", 3, state, PAIR)
                saro = seat_of(one(logs), "saro")
                self.assertEqual((saro.runs, saro.state), (["monday", "tuesday"], "B3"))

    def test_the_commits_are_in_the_json(self):
        logs = started("saro", "raya")
        logs["saro"].epoch("saro", 2, "A2", PAIR, commit="a2", by="saro")
        logs["raya"].epoch("raya", 2, "B2", PAIR)
        told = as_json(one(logs))
        named = [{"commit": "a2", "by": "saro", "holders": 1}]
        self.assertEqual([state["commits"] for state in told["states"]], [named, []])
        self.assertEqual([branch["first_commits"] for branch in told["branches"]], [named, []])


def written(message: Message) -> Dict[str, Tuple[str, int]]:
    """The run and the line of each record a post's cells stand for."""
    return {seat: (mark.run, mark.line) for seat, mark in message.marks.items()}


def posted(seats: str, posts: int) -> Conversation:
    """A group in which Saro sent some posts and nobody wrote that one arrived."""
    logs = started(*seats.split())
    for name in seats.split():
        logs[name].epoch(name, 1, "X1", seats)
    for post in range(posts):
        logs["saro"].sent("saro", f"{post:04x}", 1)
    return one(logs)


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
            dict(saro="sent", raya="read", pax="no record", mira="no log", kes="not a member"),
        )
        self.assertEqual(
            second.cells,
            dict(saro="read", raya="sent", pax="unreadable", mira="no log", kes="no record"),
        )
        self.assertEqual((first.why, second.why), ({}, {"pax": "decrypt"}))

    def test_unreadable_frames_no_post_names_are_counted_apart(self):
        self.assertEqual(self.conversation().unmatched_unreadable, 2)

    def test_an_unreadable_frame_no_post_names_is_counted_each_time_it_is_reported(self):
        logs = started("saro", "raya", "pax")
        for name in ALL3.split():
            logs[name].epoch(name, 1, "X1", ALL3)
        logs["saro"].sent("saro", "8f21", 1)
        logs["raya"].unreadable("raya", "8f21", 1, "decrypt")
        for _ in range(2):
            logs["pax"].unreadable("pax", "77b4", 1, "decrypt")
        logs["raya"].unreadable("raya", "77b4", 1, "epoch")
        self.assertEqual(one(logs).unmatched_unreadable, 3)

    def test_a_seat_off_the_roster_is_no_member_though_it_shares_no_log(self):
        logs = started("saro", "raya")
        for name in PAIR.split():
            logs[name].epoch(name, 1, "X1", PAIR)
        logs["saro"].sent("saro", "8f21", 1)
        for name in PAIR.split():
            logs[name].epoch(name, 2, "X2", PAIR + " mira")
        logs["saro"].sent("saro", "e3b9", 2)
        before, after = one(logs).messages
        self.assertEqual(before.cells, dict(saro="sent", raya="no record", mira="not a member"))
        self.assertEqual(after.cells, dict(saro="sent", raya="no record", mira="no log"))

    def test_a_sender_that_wrote_another_record_of_its_own_post_sent_it(self):
        for kind in ("received", "unreadable"):
            with self.subTest(kind=kind):
                logs = started("saro", "raya")
                for name in PAIR.split():
                    logs[name].epoch(name, 1, "X1", PAIR)
                if kind == "received":
                    logs["saro"].received("saro", "8f21", "saro", 1)
                else:
                    logs["saro"].unreadable("saro", "8f21", 1, "decrypt")
                logs["saro"].sent("saro", "8f21", 1)
                (post,) = one(logs).messages
                self.assertEqual((post.cells["saro"], post.why), ("sent", {}))
                self.assertEqual(post.marks["saro"], post.at)
                self.assertEqual(written(post), {"saro": ("saro", 4)})

    def test_a_post_sent_at_an_epoch_its_sender_wrote_no_record_for(self):
        logs = started("saro")
        logs["saro"].epoch("saro", 1, "X1", "saro raya")
        logs["saro"].sent("saro", "8f21", 4)
        (message,) = one(logs).messages
        self.assertEqual((message.state, message.cells), (None, {"saro": "sent", "raya": "no log"}))

    def test_a_cell_that_stands_for_a_record_says_where_it_was_written(self):
        first, second = self.conversation().messages
        self.assertEqual(written(first), {"saro": ("saro", 5), "raya": ("raya", 5)})
        self.assertEqual(
            written(second), {"saro": ("saro", 4), "raya": ("raya", 4), "pax": ("pax", 4)}
        )
        self.assertEqual(first.marks["saro"], first.at)
        self.assertEqual(
            first.marks["raya"], Mark("raya", 5, "2026-10-08T12:00:05.000Z", True, at(5, hour=12))
        )

    def test_a_cell_has_a_mark_when_it_is_sent_read_or_unreadable_and_only_then(self):
        for post in self.conversation().messages:
            with self.subTest(frame=post.frame):
                of_a_record = ("sent", "read", "unreadable")
                stand = {seat for seat, cell in post.cells.items() if cell in of_a_record}
                self.assertEqual(set(post.marks), stand)

    def test_an_unreadable_frame_no_post_names_has_no_row_and_no_mark(self):
        conversation = self.conversation()
        self.assertEqual([post.frame for post in conversation.messages], ["8f21", "e3b9"])
        marked = {place for post in conversation.messages for place in written(post).values()}
        self.assertFalse(marked & {("pax", 5), ("pax", 6), ("kes", 3)})

    def test_a_frame_read_and_unreadable_on_one_installation_is_read_there(self):
        first, _ = self.conversation().messages
        self.assertEqual((first.cells["raya"], first.why), ("read", {}))
        self.assertEqual(written(first)["raya"], ("raya", 5))
        # Whichever of the two its installation wrote first.
        logs = started("saro", "raya")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 1, "X1", PAIR)
        logs["saro"].sent("saro", "2b1c", 1)
        logs["raya"].unreadable("raya", "2b1c", 1, "decrypt")
        logs["raya"].received("raya", "2b1c", "saro", 1)
        (post,) = one(logs).messages
        self.assertEqual((post.cells["raya"], post.why), ("read", {}))
        self.assertEqual(written(post)["raya"], ("raya", 4))

    def test_the_cause_and_the_mark_of_an_unreadable_cell_are_of_one_record(self):
        for why in ("epoch", "decrypt", "sender"):
            with self.subTest(why=why):
                logs = started("saro", "raya", "pax")
                for name in ALL3.split():
                    logs[name].epoch(name, 3, "X3", ALL3)
                logs["raya"].epoch("raya", 4, "X4", ALL3)
                logs["saro"].sent("saro", "5ac1", 3)
                logs["pax"].received("pax", "5ac1", "saro", 3)
                logs["raya"].unreadable("raya", "5ac1", 4, why)
                # The frame arrived again, and failed for another cause.
                logs["raya"].unreadable("raya", "5ac1", 4, "decrypt" if why == "epoch" else "epoch")
                (post,) = one(logs).messages
                self.assertEqual(post.cells, dict(saro="sent", raya="unreadable", pax="read"))
                self.assertEqual(post.why, {"raya": why})
                self.assertEqual(
                    written(post), {"saro": ("saro", 3), "raya": ("raya", 4), "pax": ("pax", 3)}
                )

    def test_of_two_records_of_one_kind_for_a_frame_the_one_written_first_counts(self):
        logs = started("saro", "raya")
        for name in PAIR.split():
            logs[name].epoch(name, 1, "X1", PAIR)
        logs["saro"].sent("saro", "5ac1", 1)
        logs["saro"].sent("saro", "9d07", 1)
        for _ in range(2):
            logs["raya"].received("raya", "5ac1", "saro", 1)
        logs["raya"].unreadable("raya", "9d07", 1, "decrypt")
        # Raya started again two hours on, in a run whose name sorts before her first.
        again = Log("a later run of raya", hour=12)
        again.start("raya")
        again.received("raya", "5ac1", "saro", 1)
        again.unreadable("raya", "9d07", 1, "epoch")
        for runs in ([*logs.values(), again], [again, *logs.values()]):
            read, unreadable = one(runs).messages
            self.assertEqual(written(read)["raya"], ("raya", 3))
            self.assertEqual(unreadable.why, {"raya": "decrypt"})
            self.assertEqual(written(unreadable)["raya"], ("raya", 5))

    def test_a_frame_two_sent_records_name_is_one_post_the_one_written_first(self):
        logs = started("saro", "raya")
        for name in PAIR.split():
            logs[name].epoch(name, 1, "X1", PAIR)
        logs["saro"].sent("saro", "8f21", 1)
        logs["saro"].epoch("saro", 2, "X2", PAIR)
        logs["saro"].sent("saro", "8f21", 2)
        again = Log("a later run of saro", hour=12)
        again.start("saro")
        again.sent("saro", "8f21", 2)
        (post,) = one([*logs.values(), again]).messages
        self.assertEqual((post.sender, post.epoch, post.state), ("saro", 1, "X1"))
        self.assertEqual(written(post), {"saro": ("saro", 3)})

    def test_a_frame_two_installations_say_they_sent_is_the_post_of_the_earlier_stamp(self):
        logs = started("saro", "raya")
        for name in PAIR.split():
            logs[name].epoch(name, 1, "X1", PAIR)
        logs["raya"].sent("raya", "8f21", 1)
        logs["saro"].hour = 9
        logs["saro"].sent("saro", "8f21", 1)
        (post,) = one(logs).messages
        self.assertEqual((post.sender, post.at.time), ("saro", "2026-10-08T09:00:03.000Z"))
        self.assertEqual(written(post), {"saro": ("saro", 3)})
        # At one stamp, the one whose id sorts first.
        logs["saro"].hour = 10
        logs["saro"].sent("saro", "e3b9", 1)
        logs["raya"].sent("raya", "e3b9", 1)
        _, post = one(logs).messages
        self.assertEqual((post.sender, written(post)), ("raya", {"raya": ("raya", 4)}))

    def test_a_seat_removed_before_a_post_is_no_member_and_has_no_mark(self):
        logs = started("saro", "raya", "kes")
        for name in ("saro", "raya", "kes"):
            logs[name].epoch(name, 1, "X1", "saro raya kes")
        for name in ("saro", "raya"):
            logs[name].epoch(name, 2, "X2", PAIR)
        logs["kes"].left("kes", 2)
        logs["saro"].sent("saro", "4e30", 2)
        logs["raya"].received("raya", "4e30", "saro", 2)
        (post,) = one(logs).messages
        self.assertEqual(post.cells, dict(saro="sent", raya="read", kes="not a member"))
        self.assertEqual(sorted(post.marks), ["raya", "saro"])

    def test_a_post_no_one_read_has_its_row_and_its_senders_mark_alone(self):
        (post,) = posted(ALL3, 1).messages
        self.assertEqual(post.cells, dict(saro="sent", raya="no record", pax="no record"))
        self.assertEqual(written(post), {"saro": ("saro", 3)})

    def test_a_mark_names_a_run_its_installation_is_read_in(self):
        def group(run: str, grown: bool) -> Log:
            log = Log(run)
            for name in ("saro", "raya"):
                log.start(name)
                log.epoch(name, 1, "X1", PAIR)
            log.sent("saro", "8f21", 1)
            log.received("raya", "8f21", "saro", 1)
            if grown:
                log.epoch("raya", 2, "X2", PAIR)
            return log

        # One log at two lengths: Raya's records are read in the longer, Saro's in the first.
        conversation = one([group("monday", grown=False), group("tuesday", grown=True)])
        (post,) = conversation.messages
        self.assertEqual(written(post), {"saro": ("monday", 5), "raya": ("tuesday", 6)})
        for seat, mark in post.marks.items():
            self.assertIn(mark.run, seat_of(conversation, seat).runs)

    def test_a_post_says_when_it_was_sent_in_utc_cut_to_its_second(self):
        stamps = [
            ("2026-10-08T10:00:02.000Z", True, "2026-10-08T10:00:02Z"),
            ("2026-10-08T15:40:02.9996+02:00", True, "2026-10-08T13:40:02Z"),
            ("2026-10-08T15:40:02.999999Z", True, "2026-10-08T15:40:02Z"),
            ("2026-10-08T00:10:59-04:30", True, "2026-10-08T04:40:59Z"),
            ("2026-10-08T10:00:02.5", False, "2026-10-08T10:00:02Z"),
            ("0001-01-02T00:00:00Z", True, "0001-01-02T00:00:00Z"),
        ]
        for time, has_offset, utc in stamps:
            with self.subTest(time=time):
                said = dict(installation="saro", convo=CONVO, frame="8f21", epoch=1)
                (conversation,) = report([Record("saro", 1, time, has_offset, "sent", **said)])
                (post,) = conversation.messages
                self.assertEqual((post.at.time, post.utc), (time, utc))

    def test_the_json_holds_a_mark_as_it_holds_where_a_post_was_sent(self):
        _, second = as_json(self.conversation())["messages"]
        self.assertEqual(
            list(second), "frame sender epoch state at utc cells why marks folds".split()
        )
        self.assertEqual(second["utc"], "2026-10-08T12:00:04Z")
        self.assertEqual(sorted(second["marks"]), ["pax", "raya", "saro"])
        self.assertEqual(second["marks"]["raya"], second["at"])
        self.assertEqual(
            second["marks"]["pax"],
            {
                "run": "pax",
                "line": 4,
                "time": "2026-10-08T10:00:04.000Z",
                "has_offset": True,
                "seconds": at(4),
            },
        )

    def test_a_page_starts_with_every_post_while_their_cells_are_few(self):
        self.assertEqual(self.conversation().shown_messages, 2)
        self.assertEqual(posted(PAIR, 0).shown_messages, 0)
        self.assertEqual(as_json(self.conversation())["shown_messages"], 2)

    def test_a_page_starts_with_the_rows_that_hold_the_most_cells_it_draws_at_first(self):
        self.assertEqual(SHOWN_CELLS, 2000)
        cases = ((PAIR, 30, 30), (ALL3, 700, 666), (ALL5, 400, 400), (ALL5, 401, 400))
        for seats, posts, shown in cases:
            with self.subTest(seats=seats, posts=posts):
                conversation = posted(seats, posts)
                self.assertEqual(len(conversation.messages), posts)
                self.assertEqual(conversation.shown_messages, shown)

    def test_a_page_starts_with_a_post_however_many_installations_there_are(self):
        with mock.patch("forks.rule.SHOWN_CELLS", 4):
            self.assertEqual(posted(ALL5, 3).shown_messages, 1)
            self.assertEqual(posted(PAIR, 3).shown_messages, 2)


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


def read_histories() -> List[Tuple[str, Conversation]]:
    """Every history of the table, as it is reported."""
    read = []
    for name, history, _, _ in HISTORIES:
        kept = [kept_line(who, *line) for who, line in KEPT.get(name, {}).items()]
        read.append((name, one(*as_logs(history), kept)))
    return read


def node(state) -> Tuple[int, str]:
    return state.epoch, state.state


def leading_to(conversation: Conversation, newest: Tuple[int, str]) -> Set[Tuple[int, str]]:
    """A state and every state the report's links lead on from to it."""
    after = {node(state): list(map(node, state.after)) for state in conversation.states}
    found, todo = {newest}, [newest]
    while todo:
        for parent in after[todo.pop()]:
            if parent not in found:
                found.add(parent)
                todo.append(parent)
    return found


def lanes(conversation: Conversation) -> List[tuple]:
    return [
        (lane.under, lane.branch, lane.positions, lane.installations)
        for lane in conversation.lanes
    ]


class ForAPage(unittest.TestCase):
    """What the report holds so that a page which draws it decides nothing."""

    split = Branches.split

    @classmethod
    def setUpClass(cls) -> None:
        cls.read = read_histories()

    def test_the_positions_are_listed_live_ones_first_each_with_its_seats_and_their_count(self):
        self.assertEqual(
            POSITIONS,
            ("tip", "behind", "lineage unknown", "ended", "removed", "never joined", "no data"),
        )
        for name, conversation in self.read:
            with self.subTest(history=name):
                found = positions(conversation)
                self.assertEqual(
                    [(at.position, at.count, at.installations) for at in conversation.positions],
                    [
                        (position, len(seats), seats)
                        for position in POSITIONS
                        for seats in [sorted(s for s, its in found.items() if its == position)]
                        if seats
                    ],
                )

    def test_the_epochs_are_those_a_state_or_a_record_stands_at(self):
        for name, conversation in self.read:
            with self.subTest(history=name):
                stood_at = {state.epoch for state in conversation.states} | {
                    step.epoch for seat in conversation.installations for step in seat.history
                }
                self.assertEqual(conversation.epochs, sorted(stood_at))

    def test_a_state_is_a_branchs_shared_one_sides_or_of_unknown_lineage(self):
        for name, conversation in self.read:
            with self.subTest(history=name):
                split = conversation.split_epoch is not None
                below = {
                    branch.label: leading_to(conversation, (branch.epoch, branch.state))
                    for branch in conversation.branches
                }
                for state in conversation.states:
                    toward = [label for label, states in below.items() if node(state) in states]
                    if split and state.branch is not None:
                        expected = ("branch", None)
                    elif split and len(toward) == 1:
                        expected = ("side", toward[0])
                    else:
                        expected = ("shared" if toward else "unknown", None)
                    self.assertEqual((state.lineage, state.toward), expected, node(state))
                    self.assertEqual(state.holders, len(state.held_by))

    def test_a_row_is_a_line_of_states_under_the_row_it_leaves(self):
        for name, conversation in self.read:
            with self.subTest(history=name):
                rows: Dict[int, List[State]] = {}
                for state in conversation.states:
                    rows.setdefault(state.row, []).append(state)
                self.assertEqual(sorted(rows), list(range(len(rows))))
                for row, line in rows.items():
                    epochs = [state.epoch for state in line]
                    self.assertEqual(len(set(epochs)), len(epochs), row)
                    for before, state in zip(line, line[1:]):
                        self.assertIn(node(before), map(node, state.after), row)
                    above = {node(state) for state in conversation.states if state.row < row}
                    self.assertTrue(
                        not line[0].after or above & set(map(node, line[0].after)), row
                    )
                newest = {(branch.epoch, branch.state) for branch in conversation.branches}
                self.assertEqual(len({node(rows[row][-1]) for row in rows} & newest), len(newest))

    def test_the_states_of_a_split_stand_as_the_links_say(self):
        logs = started("saro", "raya", "pax", "kes")
        logs["kes"].epoch("kes", 0, "X0", ALL5)
        logs["saro"].epoch("saro", 1, "X1", ALL5)
        logs["saro"].epoch("saro", 2, "X2", ALL5)
        logs["saro"].epoch("saro", 3, "A3", ALL5)
        logs["pax"].epoch("pax", 3, "A3", ALL5)
        logs["raya"].epoch("raya", 2, "X2", ALL5)
        logs["raya"].epoch("raya", 3, "B3", ALL5)
        conversation = one(logs)
        self.assertEqual(conversation.verdict, "split at epoch 3")
        self.assertEqual(
            [(*node(state), state.lineage, state.toward) for state in conversation.states],
            [
                (0, "X0", "unknown", None),
                (1, "X1", "shared", None),
                (2, "X2", "shared", None),
                (3, "A3", "branch", None),
                (3, "B3", "branch", None),
            ],
        )
        self.assertEqual(
            lanes(conversation),
            [
                ("branch", "A", ["tip"], ["pax", "saro"]),
                ("branch", "B", ["tip"], ["raya"]),
                ("not placed", None, ["lineage unknown", "no data"], ["kes", "mira"]),
            ],
        )

    def test_a_state_below_a_split_that_one_branch_alone_is_linked_to_is_that_sides(self):
        logs = started("saro", "raya", "pax")
        logs["saro"].epoch("saro", 1, "X1", ALL3)
        logs["saro"].epoch("saro", 2, "A2", ALL3)
        logs["raya"].epoch("raya", 2, "A2", ALL3)
        logs["pax"].epoch("pax", 2, "B2", ALL3)
        conversation = one(logs)
        self.assertEqual(conversation.verdict, "split seen from epoch 2")
        self.assertEqual(
            [(*node(state), state.lineage, state.toward) for state in conversation.states],
            [(1, "X1", "side", "A"), (2, "A2", "branch", None), (2, "B2", "branch", None)],
        )

    def test_a_seat_that_stopped_at_one_sides_state_is_not_placed(self):
        logs = started("saro", "raya", "pax", "mira")
        logs["saro"].epoch("saro", 1, "X1", ALL4)
        logs["saro"].epoch("saro", 2, "A2", ALL4)
        logs["raya"].epoch("raya", 2, "A2", ALL4)
        logs["pax"].epoch("pax", 2, "B2", ALL4)
        logs["mira"].epoch("mira", 1, "X1", ALL4)
        conversation = one(logs)
        self.assertEqual(
            [(*node(state), state.lineage, state.toward) for state in conversation.states],
            [(1, "X1", "side", "A"), (2, "A2", "branch", None), (2, "B2", "branch", None)],
        )
        self.assertEqual(
            lanes(conversation),
            [
                ("branch", "A", ["tip"], ["raya", "saro"]),
                ("branch", "B", ["tip"], ["pax"]),
                ("not placed", None, ["behind"], ["mira"]),
            ],
        )

    def test_without_a_split_a_state_no_link_ties_to_the_newest_is_of_unknown_lineage(self):
        logs = started("saro", "raya", "pax")
        logs["saro"].epoch("saro", 1, "X1", ALL3)
        logs["raya"].epoch("raya", 2, "X2", ALL3)
        logs["raya"].epoch("raya", 3, "X3", ALL3)
        logs["pax"].epoch("pax", 3, "X3", ALL3)
        conversation = one(logs)
        self.assertEqual(conversation.verdict, "whole as far as seen")
        self.assertEqual(
            [(*node(state), state.branch, state.lineage) for state in conversation.states],
            [(1, "X1", "A", "unknown"), (2, "X2", "A", "shared"), (3, "X3", "A", "shared")],
        )
        self.assertEqual(
            lanes(conversation),
            [
                ("one line", None, ["tip"], ["pax", "raya"]),
                ("not placed", None, ["lineage unknown"], ["saro"]),
            ],
        )

    def test_every_installation_is_under_one_heading_by_the_last_state_it_wrote(self):
        for name, conversation in self.read:
            with self.subTest(history=name):
                split = conversation.split_epoch is not None
                states = {node(state): state for state in conversation.states}
                order = [("branch", branch.label) for branch in conversation.branches if split]
                order += [("neither branch" if split else "one line", None), ("not placed", None)]
                under = {heading: [] for heading in order}
                for seat in conversation.installations:
                    wrote = [step for step in seat.history if step.kind == "epoch"]
                    last = states[wrote[-1].epoch, wrote[-1].state] if wrote else None
                    if last is not None and last.lineage == "branch":
                        under["branch", last.branch].append(seat)
                    elif last is not None and last.lineage == "shared":
                        under[order[-2]].append(seat)
                    else:
                        under[order[-1]].append(seat)
                expected = []
                for (heading, branch), seats in under.items():
                    seats.sort(key=lambda seat: (POSITIONS.index(seat.position), seat.id))
                    said = list(dict.fromkeys(seat.position for seat in seats))
                    if seats:
                        expected.append((heading, branch, said, [seat.id for seat in seats]))
                self.assertEqual(lanes(conversation), expected)
                listed = [seat for lane in conversation.lanes for seat in lane.installations]
                self.assertEqual(sorted(listed), [seat.id for seat in conversation.installations])

    def test_the_lanes_of_a_split_are_its_branches_then_neither_then_the_seats_not_placed(self):
        self.assertEqual(
            lanes(self.split()),
            [
                ("branch", "A", ["tip", "behind"], ["pax", "raya"]),
                ("branch", "B", ["tip"], ["saro"]),
                ("neither branch", None, ["behind"], ["mira"]),
                ("not placed", None, ["no data"], ["kes"]),
            ],
        )

    def test_a_seat_that_left_is_under_the_heading_of_the_last_state_it_wrote(self):
        logs = started("saro", "raya", "pax")
        for name in ALL3.split():
            logs[name].epoch(name, 1, "X1", ALL3)
        logs["saro"].epoch("saro", 2, "A2", ALL3)
        logs["raya"].epoch("raya", 2, "B2", ALL3)
        logs["pax"].epoch("pax", 2, "B2", ALL3)
        logs["pax"].left("pax", 2)
        conversation = one(logs, final={"raya"})
        self.assertEqual(
            lanes(conversation),
            [
                ("branch", "A", ["tip"], ["saro"]),
                ("branch", "B", ["ended", "removed"], ["raya", "pax"]),
            ],
        )

    def test_a_seat_behind_says_the_epochs_links_lead_on_to_and_how_far_the_last_is(self):
        for name, conversation in self.read:
            with self.subTest(history=name):
                on_from = {}
                for state in conversation.states:
                    for parent in state.after:
                        on_from.setdefault(node(parent), []).append(node(state))
                for seat in conversation.installations:
                    found, todo = set(), [(seat.epoch, seat.state)]
                    while todo:
                        for later in on_from.get(todo.pop(), ()):
                            if later not in found:
                                found.add(later)
                                todo.append(later)
                    ahead = sorted({epoch for epoch, _ in found})
                    if seat.position != "behind":
                        ahead = []
                    self.assertEqual(seat.unreached, ahead, seat.id)
                    by = ahead[-1] - seat.epoch if ahead else None
                    self.assertEqual(seat.behind_by, by, seat.id)

    def test_a_seat_two_epochs_behind_names_both(self):
        logs = started("saro", "raya", "pax")
        for name in ALL3.split():
            logs[name].epoch(name, 1, "X1", ALL3)
        for name in PAIR.split():
            logs[name].epoch(name, 2, "X2", ALL3)
            logs[name].epoch(name, 4, "X4", ALL3)
        seats = {seat.id: (seat.unreached, seat.behind_by) for seat in one(logs).installations}
        self.assertEqual(seats, {"saro": ([], None), "raya": ([], None), "pax": ([2, 4], 3)})

    def test_a_seat_that_wrote_nothing_says_the_epochs_whose_rosters_list_it(self):
        for name, conversation in self.read:
            with self.subTest(history=name):
                for seat in conversation.installations:
                    listed = sorted(
                        {state.epoch for state in conversation.states if seat.id in state.roster}
                    )
                    self.assertEqual(seat.listed, [] if seat.history else listed, seat.id)
        listed = {seat.id: seat.listed for seat in self.split().installations}
        self.assertEqual(listed, {"saro": [], "raya": [], "pax": [], "mira": [], "kes": [2, 3]})

    def test_a_lane_holds_each_record_of_its_installation_once_at_the_epoch_it_was_written_at(self):
        for name, conversation in self.read:
            with self.subTest(history=name):
                for seat in conversation.installations:
                    epochs = [cell.epoch for cell in seat.cells]
                    self.assertEqual(epochs, sorted(set(epochs)), seat.id)
                    wrote = [cell for cell in seat.cells if cell.kind == "wrote"]
                    places = [place for cell in wrote for place in cell.steps]
                    self.assertEqual(sorted(places), list(range(len(seat.history))), seat.id)
                    for cell in wrote:
                        self.assertEqual(cell.steps, sorted(cell.steps), seat.id)
                        at_it = {seat.history[place].epoch for place in cell.steps}
                        self.assertEqual(at_it, {cell.epoch}, seat.id)
                    for cell in seat.cells:
                        self.assertEqual(bool(cell.steps), cell.kind == "wrote", seat.id)

    def test_two_records_at_one_epoch_stand_in_one_cell_in_the_order_written(self):
        first, second = Log("tomo-0", hour=9), Log("tomo-1", hour=11)
        first.start("tomo")
        first.epoch("tomo", 1, "X1", "tomo kes")
        first.epoch("tomo", 2, "B2", "tomo kes")
        second.start("tomo")
        second.epoch("tomo", 2, "A2", "tomo kes")
        second.epoch("tomo", 3, "A3", "tomo kes")
        second.left("tomo", 3)
        seat = seat_of(one([second, first]), "tomo")
        self.assertEqual(
            [(cell.epoch, cell.kind, cell.steps) for cell in seat.cells],
            [(1, "wrote", [0]), (2, "wrote", [1, 2]), (3, "wrote", [3, 4])],
        )
        self.assertEqual(
            [(step.kind, step.epoch, step.state) for step in seat.history],
            [
                ("epoch", 1, "X1"),
                ("epoch", 2, "B2"),
                ("epoch", 2, "A2"),
                ("epoch", 3, "A3"),
                ("left", 3, None),
            ],
        )

    def test_an_epoch_a_seat_behind_has_not_reached_is_unreached_in_its_lane(self):
        for name, conversation in self.read:
            with self.subTest(history=name):
                for seat in conversation.installations:
                    wrote = {step.epoch for step in seat.history}
                    unreached = [cell.epoch for cell in seat.cells if cell.kind == "unreached"]
                    self.assertEqual(unreached, sorted(set(seat.unreached) - wrote), seat.id)
        logs = started("saro", "raya", "pax")
        for name in ALL3.split():
            logs[name].epoch(name, 1, "X1", ALL3)
        for name in PAIR.split():
            logs[name].epoch(name, 2, "X2", ALL3)
            logs[name].epoch(name, 4, "X4", ALL3)
        cells = [(cell.epoch, cell.kind, cell.steps) for cell in seat_of(one(logs), "pax").cells]
        self.assertEqual(cells, [(1, "wrote", [0]), (2, "unreached", []), (4, "unreached", [])])

    def test_what_a_seat_wrote_at_an_epoch_stands_there_though_it_has_not_reached_it_since(self):
        logs = started("saro", "raya")
        for name in PAIR.split():
            logs[name].epoch(name, 1, "X1", ALL3)
            logs[name].epoch(name, 2, "X2", ALL3)
            logs[name].epoch(name, 3, "X3", ALL3)
        first, second = Log("pax-0", hour=9), Log("pax-1", hour=11)
        first.start("pax")
        first.epoch("pax", 3, "Y3", ALL3)
        second.start("pax")
        second.epoch("pax", 2, "X2", ALL3)
        seat = seat_of(one([*logs.values(), first, second]), "pax")
        self.assertEqual((seat.position, seat.unreached), ("behind", [3]))
        cells = [(cell.epoch, cell.kind, cell.steps) for cell in seat.cells]
        self.assertEqual(cells, [(2, "wrote", [1]), (3, "wrote", [0])])

    def test_only_a_seat_nobody_shared_a_log_of_is_listed_in_its_lane(self):
        for name, conversation in self.read:
            with self.subTest(history=name):
                for seat in conversation.installations:
                    listed = [cell.epoch for cell in seat.cells if cell.kind == "listed"]
                    unshared = not seat.runs and not seat.kept
                    self.assertEqual(listed, seat.listed if unshared else [], seat.id)
        cells = [(cell.epoch, cell.kind, cell.steps) for cell in seat_of(self.split(), "kes").cells]
        self.assertEqual(cells, [(2, "listed", []), (3, "listed", [])])

    def test_a_seat_whose_log_holds_none_of_the_group_has_an_empty_lane(self):
        logs = started("saro", "raya", "pax")
        for name in PAIR.split():
            logs[name].epoch(name, 1, "X1", ALL3)
        seat = seat_of(one(logs), "pax")
        self.assertEqual((seat.position, seat.listed, seat.cells), ("never joined", [1], []))

    def test_a_seat_says_whether_it_goes_by_a_kept_line_and_has_an_empty_lane_when_it_does(self):
        went = set()
        for name, conversation in self.read:
            with self.subTest(history=name):
                for seat in conversation.installations:
                    by_a_line = seat.id in KEPT.get(name, {}) and not seat.history
                    self.assertEqual(seat.kept, by_a_line, seat.id)
                    if seat.kept:
                        went.add(name)
                        self.assertEqual(seat.cells, [], seat.id)
        self.assertEqual(len(went), len(KEPT) - 1)
        logs = started("raya")
        logs["raya"].epoch("raya", 1, "X1", PAIR)
        logs["raya"].epoch("raya", 2, "X2", PAIR)
        seat = seat_of(one(logs, kept=[kept_line("saro")]), "saro")
        self.assertEqual((seat.kept, seat.position, seat.epoch), (True, "ended", 2))
        self.assertEqual((seat.listed, seat.cells), ([1, 2], []))


def folds(conversation: Conversation) -> List[tuple]:
    return [
        (fold.under, fold.branch, fold.position, fold.installations)
        for fold in conversation.folds
    ]


class Folds(unittest.TestCase):
    """The lanes of one position laid over one another, for a page that draws a row for them."""

    split = Branches.split
    posts = Messages.conversation

    @classmethod
    def setUpClass(cls) -> None:
        cls.read = read_histories()

    def behind(self) -> Conversation:
        """Saro and Raya at the tip of four states, Mira and Pax behind, Kes and Tomo unshared."""
        logs = started("saro", "raya", "pax", "mira")
        logs["raya"].hour = 11
        for name, reached in (("saro", 4), ("raya", 4), ("mira", 3), ("pax", 2)):
            for epoch in range(1, reached + 1):
                logs[name].epoch(name, epoch, f"X{epoch}", ALL5 + (" tomo" if epoch > 2 else ""))
        return one(logs)

    def test_a_fold_is_the_installations_of_one_position_under_one_heading_as_they_are_read(self):
        self.assertEqual(
            folds(self.split()),
            [
                ("branch", "A", "tip", ["pax"]),
                ("branch", "A", "behind", ["raya"]),
                ("branch", "B", "tip", ["saro"]),
                ("neither branch", None, "behind", ["mira"]),
                ("not placed", None, "no data", ["kes"]),
            ],
        )
        for name, conversation in self.read:
            with self.subTest(history=name):
                where = positions(conversation)
                expected = [
                    (
                        lane.under,
                        lane.branch,
                        position,
                        [seat for seat in lane.installations if where[seat] == position],
                    )
                    for lane in conversation.lanes
                    for position in lane.positions
                ]
                self.assertEqual(folds(conversation), expected)
                # Fold after fold, the installations stand as lane after lane lists them.
                self.assertEqual(
                    [seat for fold in conversation.folds for seat in fold.installations],
                    [seat for lane in conversation.lanes for seat in lane.installations],
                )

    def test_a_fold_counts_at_each_epoch_what_its_lanes_hold_there(self):
        tip, behind, unshared = self.behind().folds
        counted = [
            [(stack.epoch, stack.wrote, stack.unreached, stack.listed) for stack in fold.cells]
            for fold in (tip, behind, unshared)
        ]
        self.assertEqual(
            counted,
            [
                [(1, 2, 0, 0), (2, 2, 0, 0), (3, 2, 0, 0), (4, 2, 0, 0)],
                [(1, 2, 0, 0), (2, 2, 0, 0), (3, 1, 1, 0), (4, 0, 2, 0)],
                [(1, 0, 0, 1), (2, 0, 0, 1), (3, 0, 0, 2), (4, 0, 0, 2)],
            ],
        )
        for name, conversation in self.read:
            for fold in conversation.folds:
                with self.subTest(history=name, fold=(fold.under, fold.branch, fold.position)):
                    seats = [seat_of(conversation, seat) for seat in fold.installations]
                    cells = [cell for seat in seats for cell in seat.cells]
                    self.assertEqual(
                        [stack.epoch for stack in fold.cells],
                        sorted({cell.epoch for cell in cells}),
                    )
                    for stack in fold.cells:
                        kinds = Counter(cell.kind for cell in cells if cell.epoch == stack.epoch)
                        self.assertEqual(
                            (stack.wrote, stack.unreached, stack.listed),
                            (kinds["wrote"], kinds["unreached"], kinds["listed"]),
                        )

    def test_a_fold_says_how_many_wrote_each_state_at_an_epoch_and_from_when_to_when(self):
        tip, behind, _ = self.behind().folds
        # Raya wrote each state an hour after Saro did.
        self.assertEqual(
            [stack.states for stack in tip.cells],
            [[Held(f"X{line - 1}", 2, at(line), at(line, hour=11))] for line in (2, 3, 4, 5)],
        )
        self.assertEqual(
            [stack.states for stack in behind.cells],
            [
                [Held("X1", 2, at(2), at(2))],
                [Held("X2", 2, at(3), at(3))],
                [Held("X3", 1, at(4), at(4))],
                [],
            ],
        )
        for name, conversation in self.read:
            for fold in conversation.folds:
                with self.subTest(history=name, fold=(fold.under, fold.branch, fold.position)):
                    seats = [seat_of(conversation, seat) for seat in fold.installations]
                    for stack in fold.cells:
                        # Each installation's records of a state at the epoch, by state.
                        wrote = [
                            (seat.id, step.state, step.at.seconds)
                            for seat in seats
                            for step in seat.history
                            if step.epoch == stack.epoch and step.kind == "epoch"
                        ]
                        states = sorted({state for _, state, _ in wrote})
                        self.assertEqual([held.state for held in stack.states], states)
                        for held in stack.states:
                            stamps = [stamp for _, state, stamp in wrote if state == held.state]
                            holders = {seat for seat, state, _ in wrote if state == held.state}
                            self.assertEqual(
                                (held.count, held.first, held.last),
                                (len(holders), min(stamps), max(stamps)),
                            )
                        left = {
                            seat.id
                            for seat in seats
                            for step in seat.history
                            if step.epoch == stack.epoch and step.kind == "left"
                        }
                        self.assertEqual(stack.left, len(left))

    def test_an_installation_counts_once_under_each_state_it_wrote_at_an_epoch_and_under_a_leave(
        self,
    ):
        # Tomo wrote B2, and in a later run A2 twice and A3 twice. Kes wrote A3 and left at its
        # epoch.
        first, second, kes = Log("tomo-0", hour=9), Log("tomo-1", hour=11), Log("kes")
        first.start("tomo")
        first.epoch("tomo", 1, "X1", "tomo kes")
        first.epoch("tomo", 2, "B2", "tomo kes")
        second.start("tomo")
        second.epoch("tomo", 2, "A2", "tomo kes")
        second.epoch("tomo", 2, "A2", "tomo kes")
        second.epoch("tomo", 3, "A3", "tomo kes")
        second.epoch("tomo", 3, "A3", "tomo kes")
        kes.start("kes")
        for epoch, state in ((1, "X1"), (2, "A2"), (3, "A3")):
            kes.epoch("kes", epoch, state, "tomo kes")
        kes.left("kes", 3)
        conversation = one([second, first, kes])
        self.assertEqual(
            folds(conversation),
            [("branch", "A", "tip", ["tomo"]), ("branch", "A", "removed", ["kes"])],
        )
        tip, removed = conversation.folds
        at_two = tip.cells[1]
        self.assertEqual(
            at_two.states,
            [
                Held("A2", 1, at(2, hour=11), at(3, hour=11)),
                Held("B2", 1, at(3, hour=9), at(3, hour=9)),
            ],
        )
        self.assertEqual((at_two.wrote, at_two.left, at_two.several), (1, 0, 1))
        # One state written twice at an epoch is one state there.
        self.assertEqual([stack.several for stack in tip.cells], [0, 1, 0])
        self.assertEqual(tip.cells[2].states, [Held("A3", 1, at(4, hour=11), at(5, hour=11))])
        at_three = removed.cells[2]
        self.assertEqual(at_three.states, [Held("A3", 1, at(4), at(4))])
        self.assertEqual((at_three.wrote, at_three.left, at_three.several), (1, 1, 1))
        self.assertEqual([(stack.left, stack.several) for stack in removed.cells[:2]], [(0, 0)] * 2)

    def test_a_fold_says_from_where_to_where_its_installations_stand(self):
        tip, behind, unshared = self.behind().folds
        self.assertEqual((tip.epoch, tip.behind_by, tip.listed_from), (Range(4, 4), None, None))
        self.assertEqual(
            (behind.epoch, behind.behind_by, behind.listed_from), (Range(2, 3), Range(1, 2), None)
        )
        # Kes is listed from the first epoch and Tomo from the third, and neither is at one.
        self.assertEqual(
            (unshared.epoch, unshared.behind_by, unshared.listed_from), (None, None, Range(1, 3))
        )
        # A group that begins at epoch 0: Raya stands at it, Mira an epoch on, and Pax, who
        # shares no log, is listed from it.
        logs = started("saro", "raya", "mira")
        for name, reached in (("saro", 2), ("raya", 0), ("mira", 1)):
            for epoch in range(reached + 1):
                logs[name].epoch(name, epoch, f"X{epoch}", ALL4)
        _, behind, unshared = one(logs).folds
        self.assertEqual(
            (behind.epoch, behind.behind_by, unshared.listed_from),
            (Range(0, 1), Range(1, 2), Range(0, 0)),
        )
        for name, conversation in self.read:
            for fold in conversation.folds:
                with self.subTest(history=name, fold=(fold.under, fold.branch, fold.position)):
                    seats = [seat_of(conversation, seat) for seat in fold.installations]
                    unplaced = [seat for seat in seats if seat.epoch is None and seat.listed]
                    for said, numbers in (
                        (fold.epoch, [seat.epoch for seat in seats]),
                        (fold.behind_by, [seat.behind_by for seat in seats]),
                        (fold.listed_from, [seat.listed[0] for seat in unplaced]),
                    ):
                        known = [number for number in numbers if number is not None]
                        self.assertEqual(said, Range(min(known), max(known)) if known else None)

    def test_a_fold_counts_the_logs_that_are_held_and_the_lines_that_are_kept(self):
        tip, behind, unshared = self.behind().folds
        self.assertEqual(
            [(fold.logs, fold.kept) for fold in (tip, behind, unshared)], [(2, 0), (2, 0), (0, 0)]
        )
        logs = started("raya")
        logs["raya"].epoch("raya", 1, "X1", PAIR)
        logs["raya"].epoch("raya", 2, "X2", PAIR)
        conversation = one(logs, kept=[kept_line("saro")])
        self.assertEqual(
            [(*fold.installations, fold.logs, fold.kept) for fold in conversation.folds],
            [("raya", 1, 0), ("saro", 0, 1)],
        )
        went = 0
        for name, conversation in self.read:
            for fold in conversation.folds:
                with self.subTest(history=name, fold=(fold.under, fold.branch, fold.position)):
                    seats = [seat_of(conversation, seat) for seat in fold.installations]
                    self.assertEqual(fold.kept, sum(seat.kept for seat in seats))
                    self.assertEqual(fold.logs, sum(bool(seat.runs) for seat in seats))
                    went += fold.kept
        self.assertEqual(went, len(KEPT) - 1)

    def test_a_page_starts_folded_with_more_installations_than_lanes_are_in_sight(self):
        self.assertEqual(LANES_IN_SIGHT, 23)
        for seats, folded in ((23, False), (24, True)):
            with self.subTest(seats=seats):
                others = (f"seat{number}" for number in range(1, seats))
                conversation = posted(" ".join(["saro", *others]), 0)
                self.assertEqual(len(conversation.installations), seats)
                self.assertEqual(conversation.folded, folded)
                self.assertIs(as_json(conversation)["folded"], folded)
                # Folded or not, the folds are there.
                everyone = sorted(positions(conversation))
                self.assertEqual(folds(conversation), [("one line", None, "tip", everyone)])
        with mock.patch("forks.rule.LANES_IN_SIGHT", 2):
            self.assertEqual((posted(PAIR, 0).folded, posted(ALL3, 0).folded), (False, True))
            # A seat nobody shared a log of has a lane as well, and counts as the others do.
            logs = started(*PAIR.split())
            for name in PAIR.split():
                logs[name].epoch(name, 1, "X1", ALL3)
            self.assertEqual((len(one(logs).installations), one(logs).folded), (3, True))

    def test_a_post_counts_under_each_fold_what_became_of_it_there(self):
        conversation = self.posts()
        self.assertEqual(
            folds(conversation),
            [
                ("branch", "A", "tip", ["kes", "raya", "saro"]),
                ("branch", "B", "tip", ["pax"]),
                ("not placed", None, "no data", ["mira"]),
            ],
        )
        first, second = conversation.messages
        self.assertEqual(
            first.folds,
            [
                Fates({"sent": 1, "read": 1, "not a member": 1}, {}),
                Fates({"no record": 1}, {}),
                Fates({"no log": 1}, {}),
            ],
        )
        self.assertEqual(
            second.folds,
            [
                Fates({"sent": 1, "read": 1, "no record": 1}, {}),
                Fates({"unreadable": 1}, {"decrypt": 1}),
                Fates({"no log": 1}, {}),
            ],
        )
        for post in conversation.messages:
            for fold, fates in zip(conversation.folds, post.folds):
                with self.subTest(frame=post.frame, fold=fold.installations):
                    became = Counter(post.cells[seat] for seat in fold.installations)
                    self.assertEqual(fates.cells, dict(became))
                    self.assertEqual(sum(fates.cells.values()), len(fold.installations))

    def test_the_counts_of_a_post_stand_in_one_order_and_its_causes_by_name(self):
        logs = started(*ALL5.split())
        for name in ALL5.split():
            logs[name].epoch(name, 1, "X1", ALL5 + " tomo")
        logs["saro"].sent("saro", "8f21", 1)
        logs["kes"].unreadable("kes", "8f21", 1, "sender")
        logs["raya"].unreadable("raya", "8f21", 1, "epoch")
        logs["pax"].unreadable("pax", "8f21", 1, "sender")
        (post,) = one(logs).messages
        counted, unshared = post.folds
        self.assertEqual(
            (list(counted.cells.items()), list(counted.why.items())),
            ([("sent", 1), ("unreadable", 3), ("no record", 1)], [("epoch", 1), ("sender", 2)]),
        )
        self.assertEqual(unshared, Fates({"no log": 1}, {}))
        logs["mira"].received("mira", "8f21", "saro", 1)
        (post,) = one(logs).messages
        self.assertEqual(list(post.folds[0].cells), ["sent", "read", "unreadable"])
        # Saro posts to Raya, Pax and Mira, and the group goes on without the two and without
        # Kes, whom it took in after the post. Pax shares a log that holds none of the group,
        # and Mira and Kes share none.
        logs = started(*ALL3.split())
        for epoch, roster in ((1, ALL4), (2, PAIR + " kes"), (3, PAIR)):
            for name in PAIR.split():
                logs[name].epoch(name, epoch, f"X{epoch}", roster)
            if epoch == 1:
                logs["saro"].sent("saro", "8f21", 1)
        conversation, removed = one(logs), ["kes", "mira", "pax"]
        self.assertEqual(folds(conversation)[1:], [("not placed", None, "removed", removed)])
        (post,) = conversation.messages
        self.assertEqual(
            list(post.folds[1].cells.items()),
            [("not a member", 1), ("no log", 1), ("no record", 1)],
        )
        self.assertEqual([post.cells[seat] for seat in removed], list(post.folds[1].cells))
        # Saro posts to Raya, Pax, Mira and Kes, takes Tomo in, and both leave. A seventh seat
        # goes on from a state no link leads to, so no log places the six, and its roster lists
        # none of them. Raya read the post and Pax could not, Mira's log holds none of the
        # group, and Kes shares none.
        logs = started("saro", "raya", "pax", "mira", "tomo", "seat7")
        logs["saro"].epoch("saro", 1, "X1", ALL5)
        logs["saro"].sent("saro", "8f21", 1)
        logs["saro"].epoch("saro", 2, "X2", "saro tomo")
        logs["raya"].received("raya", "8f21", "saro", 1)
        logs["pax"].unreadable("pax", "8f21", 1, "epoch")
        for name in ("saro", "tomo"):
            logs[name].left(name, 2)
        logs["seat7"].epoch("seat7", 3, "X3", "seat7")
        conversation, six = one(logs), sorted(ALL5.split() + ["tomo"])
        self.assertEqual(folds(conversation)[1:], [("not placed", None, "removed", six)])
        (post,) = conversation.messages
        fates = ["sent", "read", "unreadable", "not a member", "no log", "no record"]
        self.assertEqual(list(post.folds[1].cells.items()), [(fate, 1) for fate in fates])

    def test_the_first_and_the_last_stamp_of_a_state_keep_the_fraction_of_their_second(self):
        wrote = [
            stamped("saro", 1, "2026-10-08T10:00:01.201Z"),
            stamped("raya", 1, "2026-10-08T10:00:02.601Z"),
        ]
        (conversation,) = report(wrote)
        (fold,) = conversation.folds
        self.assertEqual(fold.cells[0].states, [Held("X1", 2, at(1) + 0.201, at(2) + 0.601)])

    def test_the_json_holds_a_fold_and_the_counts_of_a_post_as_the_report_does(self):
        said = as_json(self.split())
        self.assertIs(said["folded"], False)
        self.assertEqual(
            said["folds"][1],
            {
                "under": "branch",
                "branch": "A",
                "position": "behind",
                "installations": ["raya"],
                "kept": 0,
                "logs": 1,
                "epoch": {"low": 2, "high": 2},
                "behind_by": {"low": 1, "high": 1},
                "listed_from": None,
                "cells": [
                    {
                        "epoch": 1,
                        "wrote": 1,
                        "states": [{"state": "X1", "count": 1, "first": at(2), "last": at(2)}],
                        "left": 0,
                        "several": 0,
                        "unreached": 0,
                        "listed": 0,
                    },
                    {
                        "epoch": 2,
                        "wrote": 1,
                        "states": [{"state": "A2", "count": 1, "first": at(3), "last": at(3)}],
                        "left": 0,
                        "several": 0,
                        "unreached": 0,
                        "listed": 0,
                    },
                    {
                        "epoch": 3,
                        "wrote": 0,
                        "states": [],
                        "left": 0,
                        "several": 0,
                        "unreached": 1,
                        "listed": 0,
                    },
                ],
            },
        )
        self.assertEqual(
            as_json(self.posts())["messages"][1]["folds"],
            [
                {"cells": {"sent": 1, "read": 1, "no record": 1}, "why": {}},
                {"cells": {"unreadable": 1}, "why": {"decrypt": 1}},
                {"cells": {"no log": 1}, "why": {}},
            ],
        )


def at(second: int, hour: int = 10) -> float:
    """A second of that hour on 2026-10-08, as seconds since 1970 in UTC."""
    return 1791417600.0 + hour * 3600 + second


def stamped(name: str, line: int, time: str, has_offset: bool = True) -> Record:
    """An `epoch` record of the pair's first state, written at a stamp of the test's choice."""
    roster = tuple((member, f"account-of-{member}") for member in PAIR.split())
    said = dict(installation=name, convo=CONVO, epoch=1, state="X1", members=2, roster=roster)
    return Record(name, line, time, has_offset, "epoch", **said)


class Stretches(unittest.TestCase):
    def test_a_mark_holds_its_stamp_as_seconds_since_1970_in_utc(self):
        stamps = [
            ("2026-10-08T10:00:02.000Z", True, at(2)),
            ("2026-10-08T12:00:02.25+02:00", True, at(2) + 0.25),
            ("2026-10-08T05:30:02-04:30", True, at(2)),
            ("2026-10-08T10:00:02.5", False, at(2) + 0.5),
            ("1970-01-01T00:00:00Z", True, 0.0),
        ]
        for time, has_offset, seconds in stamps:
            with self.subTest(time=time):
                (conversation,) = report([stamped("saro", 1, time, has_offset)])
                (step,) = seat_of(conversation, "saro").history
                self.assertEqual((step.at.time, step.at.seconds), (time, seconds))

    def test_a_run_without_a_delivery_record_is_one_stretch_from_its_first_record_to_its_last(self):
        logs = started("saro", "raya")
        for name in PAIR.split():
            logs[name].epoch(name, 1, "X1", PAIR)
        logs["saro"].epoch("saro", 2, "X2", PAIR)
        conversation = one(logs)
        self.assertEqual(seat_of(conversation, "saro").spans, [Span(at(1), at(3), True)])
        self.assertEqual(seat_of(conversation, "raya").spans, [Span(at(1), at(2), True)])
        self.assertEqual((conversation.timeline.start, conversation.timeline.end), (at(2), at(3)))

    def test_a_run_is_cut_where_its_delivery_goes_to_or_from_online(self):
        logs = started("saro")
        log = logs["saro"]
        log.delivery("initialising")
        log.epoch("saro", 1, "X1", "saro")
        log.delivery("online")
        log.delivery("online")
        log.epoch("saro", 2, "X2", "saro")
        log.delivery("error")
        log.delivery("stopped")
        log.delivery("online")
        log.epoch("saro", 3, "X3", "saro")
        self.assertEqual(
            seat_of(one(logs), "saro").spans,
            [
                Span(at(1), at(2), True),
                Span(at(2), at(4), False),
                Span(at(4), at(7), True),
                Span(at(7), at(9), False),
                Span(at(9), at(10), True),
            ],
        )

    def test_a_run_reaches_to_the_last_of_its_delivery_records_and_begins_no_stretch_there(self):
        logs = started("saro")
        logs["saro"].epoch("saro", 1, "X1", "saro")
        logs["saro"].delivery("online")
        logs["saro"].delivery("stopped")
        conversation = one(logs)
        self.assertEqual(seat_of(conversation, "saro").spans, [Span(at(1), at(4), True)])
        self.assertEqual((conversation.timeline.start, conversation.timeline.end), (at(2), at(2)))

    def test_each_run_an_installation_wrote_in_is_a_stretch_of_its_own(self):
        first, second = Log("saro-0", hour=9), Log("saro-1", hour=11)
        for log in (first, second):
            log.start("saro")
            log.epoch("saro", 1, "X1", "saro")
        second.delivery("error")
        second.epoch("saro", 2, "X2", "saro")
        self.assertEqual(
            seat_of(one([second, first]), "saro").spans,
            [
                Span(at(1, 9), at(2, 9), True),
                Span(at(1, 11), at(3, 11), True),
                Span(at(3, 11), at(4, 11), False),
            ],
        )

    def test_a_run_of_one_stamp_is_a_stretch_and_a_clock_that_went_back_ends_none_early(self):
        back = [
            stamped("raya", 1, "2026-10-08T10:00:09.000Z"),
            stamped("raya", 2, "2026-10-08T10:00:05.000Z"),
        ]
        (conversation,) = report([stamped("saro", 1, "2026-10-08T10:00:01.000Z"), *back])
        self.assertEqual(seat_of(conversation, "saro").spans, [Span(at(1), at(1), True)])
        self.assertEqual(seat_of(conversation, "raya").spans, [Span(at(9), at(9), True)])
        self.assertEqual((conversation.timeline.start, conversation.timeline.end), (at(1), at(9)))

    def test_the_timeline_is_of_the_records_that_name_the_conversation_and_a_run_reaches_past(self):
        log = Log("saro")
        log.start("saro")
        log.epoch("saro", 7, "O7", "saro", convo="an-older-one")
        log.delivery("online")
        log.epoch("saro", 1, "X1", "saro")
        log.epoch("saro", 2, "X2", "saro")
        log.sent("saro", "f1", 2)
        log.delivery("stopped")
        (conversation, older) = report(log.records)
        self.assertEqual((conversation.id, older.id), (CONVO, "an-older-one"))
        self.assertEqual((conversation.timeline.start, conversation.timeline.end), (at(4), at(6)))
        self.assertEqual((older.timeline.start, older.timeline.end), (at(2), at(2)))
        for read in (older, conversation):
            self.assertEqual(seat_of(read, "saro").spans, [Span(at(1), at(7), True)])

    def test_a_seat_that_shares_no_log_has_no_stretch(self):
        logs = started("saro")
        logs["saro"].epoch("saro", 1, "X1", PAIR)
        self.assertEqual(seat_of(one(logs), "raya").spans, [])

    def test_one_log_of_many_installations_gives_each_its_own_stretch_and_the_runs_delivery(self):
        log = Log("everyone")
        log.start("saro")
        log.epoch("saro", 1, "X1", PAIR)
        log.start("raya")
        log.delivery("error")
        log.epoch("raya", 1, "X1", PAIR)
        log.epoch("saro", 2, "X2", PAIR)
        conversation = one([log])
        self.assertEqual(
            seat_of(conversation, "saro").spans,
            [Span(at(1), at(4), True), Span(at(4), at(6), False)],
        )
        self.assertEqual(
            seat_of(conversation, "raya").spans,
            [Span(at(3), at(4), True), Span(at(4), at(5), False)],
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
