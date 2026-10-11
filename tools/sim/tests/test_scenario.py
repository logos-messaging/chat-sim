import json
import tempfile
import unittest
from pathlib import Path

from sim import scenario
from sim.scenario import ALL, LAST, Expect, Post, Refused, Scenario, Seat

TOOL = Path(__file__).resolve().parents[1]

# Ids a fork report would know five members by, in the order of the members.
IDS = ["a3f91c07", "7b1e44d9", "e09a3f52", "19f6b0e3", "b54a9d10"]


def three(**changed) -> dict:
    """A scenario of three members: the creator seats two, and the last one seated posts."""
    whole = {"members": 3, "steps": [{"step": "seat", "to": 3}, {"step": "post"}]}
    whole.update(changed)
    return whole


def steps(*steps: dict, members: int = 5) -> dict:
    return {"members": members, "steps": list(steps)}


class Reading(unittest.TestCase):
    def test_a_scenario_that_says_the_least_has_the_defaults(self):
        self.assertEqual(
            scenario.scenario(three()),
            Scenario(
                members=3,
                steps=(Seat(to=3, batch=10), Post(sender=LAST)),
                timers="fast",
                max_message_kib=512,
                budget=180.0,
                expect=Expect(verdict="whole", positions={}, posts={}),
            ),
        )

    def test_each_field_is_read_as_it_is_said(self):
        said = {
            "members": 7,
            "timers": {"consensus_timeout": 2000, "voting_delay": 0},
            "max_message_kib": 64,
            "budget": 12.5,
            "steps": [
                {"step": "seat", "to": 4, "batch": 2},
                {"step": "post", "from": "mira"},
                {"step": "seat", "to": 7},
                {"step": "post", "from": "peer7"},
                {"step": "post", "from": "all"},
                {"step": "post", "from": "last"},
            ],
            "expect": {
                "verdict": "whole, members behind",
                "positions": {"peer7": "behind"},
                "posts": {"raya": "unreadable"},
            },
        }

        self.assertEqual(
            scenario.scenario(said),
            Scenario(
                members=7,
                steps=(Seat(4, 2), Post("mira"), Seat(7, 10), Post("peer7"), Post(ALL), Post(LAST)),
                timers={"consensus_timeout": 2000, "voting_delay": 0},
                max_message_kib=64,
                budget=12.5,
                expect=Expect("whole, members behind", {"peer7": "behind"}, {"raya": "unreadable"}),
            ),
        )

    def test_the_timers_are_a_set_or_durations_by_the_names_the_library_gives_them(self):
        for timers in ("fast", "default"):
            self.assertEqual(scenario.scenario(three(timers=timers)).timers, timers)
        every = {duration: 1000 + at for at, duration in enumerate(scenario.DURATIONS)}
        self.assertEqual(scenario.scenario(three(timers=every)).timers, every)
        self.assertEqual(
            scenario.DURATIONS,
            (
                "voting_delay",
                "consensus_timeout",
                "commit_batch_window",
                "freeze_duration",
                "retry_window",
                "backup_takeover_window",
                "proposal_expiration",
                "recovery_auto_commit_delay",
            ),
        )

    def test_the_most_a_number_may_be_is_taken(self):
        most = scenario.scenario(
            {
                "members": 65534,
                "max_message_kib": 2**32 - 1,
                "budget": 1e300,
                "steps": [{"step": "seat", "to": 65534, "batch": 65534}],
            }
        )

        self.assertEqual((most.members, most.seated, most.steps[0].batch), (65534, 65534, 65534))
        self.assertEqual((most.max_message_kib, most.budget), (2**32 - 1, 1e300))
        self.assertEqual(scenario.scenario(three(budget=7)).budget, 7.0)

    def test_every_verdict_a_report_gives_can_be_expected(self):
        for verdict in (
            "whole",
            "whole, members behind",
            "whole as far as seen",
            "not enough data",
            "no live member",
            "split at epoch 3",
            "split at epoch 12, one side live",
            "split at epoch 2, no side live",
            "split seen from epoch 1",
            "split seen from epoch 2, one side live",
        ):
            self.assertEqual(
                scenario.scenario(three(expect={"verdict": verdict})).expect.verdict, verdict
            )

    def test_what_can_be_expected_is_what_the_fork_tool_tells_of_logs(self):
        histories = TOOL.parent / "forks" / "tests" / "fixtures" / "histories"
        positions = set()
        for file in sorted(histories.glob("*/expect.json")):
            expect = json.loads(file.read_text(encoding="utf-8"))
            scenario.scenario(three(expect={"verdict": expect["verdict"]}))
            positions.update(seat["position"] for seat in expect["positions"].values())

        self.assertEqual(positions, set(scenario.POSITIONS))

    def test_a_file_is_read_and_a_refusal_names_it(self):
        with tempfile.TemporaryDirectory() as scratch:
            file = Path(scratch) / "three.json"
            file.write_text(json.dumps(three()), encoding="utf-8")
            self.assertEqual(scenario.read(file), scenario.scenario(three()))

            file.write_text(json.dumps(three(peers=3)), encoding="utf-8")
            with self.assertRaises(Refused) as refused:
                scenario.read(file)
            self.assertEqual(
                str(refused.exception), f"{file}: `peers` is not a field of the scenario"
            )

            file.write_text("members: 3", encoding="utf-8")
            with self.assertRaises(Refused) as refused:
                scenario.read(file)
            self.assertEqual(str(refused.exception), f"{file} is not JSON")

            with self.assertRaises(Refused) as refused:
                scenario.read(Path(scratch) / "nowhere.json")
            self.assertIn("nowhere.json", str(refused.exception))

    def test_a_file_that_says_a_field_twice_is_refused_by_the_name_of_the_field(self):
        seated = '"members": 3, "steps": [{"step": "seat", "to": 3}]'
        twice = [
            ("members", '{%s, "members": 6}' % seated),
            ("to", '{"members": 3, "steps": [{"step": "seat", "to": 3, "to": 2}]}'),
            ("expect", '{%s, "expect": {"verdict": "split at epoch 3"}, "expect": {}}' % seated),
            ("verdict", '{%s, "expect": {"verdict": "whole", "verdict": "whole"}}' % seated),
            ("voting_delay", '{%s, "timers": {"voting_delay": 1, "voting_delay": 1}}' % seated),
        ]
        with tempfile.TemporaryDirectory() as scratch:
            file = Path(scratch) / "twice.json"
            for name, said in twice:
                with self.subTest(said=said):
                    file.write_text(said, encoding="utf-8")
                    with self.assertRaises(Refused) as refused:
                        scenario.read(file)
                    self.assertEqual(str(refused.exception), f"{file}: `{name}` is said twice")

    def test_a_file_that_is_json_to_python_alone_is_refused(self):
        seat = '{"step": "seat", "to": 3}'
        with tempfile.TemporaryDirectory() as scratch:
            file = Path(scratch) / "scenario.json"
            for said in (
                '{"members": 3, "steps": [%s], "budget": Infinity}' % seat,
                '{"members": 3, "steps": [%s], "budget": -Infinity}' % seat,
                '{"members": 3, "steps": [%s], "budget": NaN}' % seat,
                "[" * 100_000,
            ):
                with self.subTest(said=said[:60]):
                    file.write_text(said, encoding="utf-8")
                    with self.assertRaises(Refused) as refused:
                        scenario.read(file)
                    self.assertEqual(str(refused.exception), f"{file} is not JSON")

            file.write_text('{"members": 3, "steps": [%s], "budget": 1e400}' % seat, "utf-8")
            with self.assertRaises(Refused) as refused:
                scenario.read(file)
            self.assertEqual(
                str(refused.exception),
                f"{file}: `budget` is more seconds than a wait can be told to last",
            )

    def test_the_scenarios_the_repository_keeps_are_read(self):
        kept = sorted(file.name for file in (TOOL / "scenarios").glob("*.json"))
        self.assertEqual(kept, ["fifty.json", "three.json", "twenty.json"])
        for name, members in (("three.json", 3), ("twenty.json", 20), ("fifty.json", 50)):
            held = scenario.read(TOOL / "scenarios" / name)
            self.assertEqual((held.members, held.seated), (members, members), name)
            self.assertEqual(held.expect, Expect(), name)


class Refusals(unittest.TestCase):
    def refusal(self, said: object) -> str:
        with self.assertRaises(Refused) as refused:
            scenario.scenario(said)
        return str(refused.exception)

    def test_each_thing_a_scenario_cannot_say_is_refused_by_name(self):
        seat, post = {"step": "seat", "to": 3}, {"step": "post"}
        refusals = [
            ("the scenario is not an object", [three()]),
            ("the scenario lacks `members`", {"steps": [seat]}),
            ("the scenario lacks `steps`", {"members": 3}),
            ("`peers` is not a field of the scenario", three(peers=3)),
            ("`batch` is not a field of the scenario", three(batch=10)),
            ("`members` of the scenario is not a whole number", three(members="3")),
            ("`members` of the scenario is not a whole number", three(members=3.0)),
            ("`members` of the scenario is not a whole number", three(members=True)),
            ("`members` of the scenario is 1, and 2 is the least", three(members=1)),
            ("`members` of the scenario is 65535, and 65534 is the most", three(members=65535)),
            (
                f"`members` of the scenario is {10**30}, and 65534 is the most",
                three(members=10**30),
            ),
            ("`steps` is not a list of one step or more", three(steps=[])),
            ("`steps` is not a list of one step or more", three(steps=seat)),
            ("`max_message_kib` of the scenario is not a whole number", three(max_message_kib="1")),
            (
                "`max_message_kib` of the scenario is 0, and 1 is the least",
                three(max_message_kib=0),
            ),
            (
                f"`max_message_kib` of the scenario is {2**32}, and {2**32 - 1} is the most",
                three(max_message_kib=2**32),
            ),
            ("`budget` is not a number of seconds above 0", three(budget=0)),
            ("`budget` is not a number of seconds above 0", three(budget="180")),
            ("`budget` is not a number of seconds above 0", three(budget=False)),
            ("`budget` is not a number of seconds above 0", three(budget=True)),
            ("`budget` is not a number of seconds above 0", three(budget=float("nan"))),
            (
                "`budget` is more seconds than a wait can be told to last",
                three(budget=float("inf")),
            ),
            ("`budget` is more seconds than a wait can be told to last", three(budget=10**400)),
            (
                "`timers` names the set quick, and the sets are fast and default",
                three(timers="quick"),
            ),
            (
                "`timers` names the set auto, and the sets are fast and default",
                three(timers="auto"),
            ),
            ("`timers` is not the name of a set or an object of durations", three(timers=250)),
            (
                "`consensus_timeout_ms` in `timers` is not a duration of the group's settings",
                three(timers={"consensus_timeout_ms": 250}),
            ),
            (
                "`voting_delay` of `timers` is not a whole number",
                three(timers={"voting_delay": 0.05}),
            ),
            (
                "`voting_delay` of `timers` is -1, and 0 is the least",
                three(timers={"voting_delay": -1}),
            ),
            (
                "`voting_delay` of `timers` is not a whole number",
                three(timers={"voting_delay": True}),
            ),
            (
                f"`retry_window` of `timers` is {2**64}, and {2**64 - 1} is the most",
                three(timers={"retry_window": 2**64}),
            ),
            ("steps[0] is not an object whose `step` is `seat` or `post`", steps("seat")),
            ("steps[1] is not an object whose `step` is `seat` or `post`", steps(seat, {"to": 4})),
            (
                "steps[1] is not an object whose `step` is `seat` or `post`",
                steps(seat, {"step": "remove"}),
            ),
            ("steps[0] lacks `to`", steps({"step": "seat"})),
            (
                "`members` is not a field of steps[0]",
                steps({"step": "seat", "to": 3, "members": 3}),
            ),
            ("`to` of steps[0] is not a whole number", steps({"step": "seat", "to": "3"})),
            ("`to` of steps[0] is 1, and 2 is the least", steps({"step": "seat", "to": 1})),
            (
                f"`to` of steps[0] is {10**30}, and 65534 is the most",
                steps({"step": "seat", "to": 10**30}),
            ),
            (
                "`batch` of steps[0] is 0, and 1 is the least",
                steps({"step": "seat", "to": 3, "batch": 0}),
            ),
            (
                "`batch` of steps[0] is not a whole number",
                steps({"step": "seat", "to": 3, "batch": 2.0}),
            ),
            (
                f"`batch` of steps[0] is {10**30}, and 65534 is the most",
                steps({"step": "seat", "to": 3, "batch": 10**30}),
            ),
            (
                "steps[0] seats 6 members, and the scenario has 5",
                steps({"step": "seat", "to": 6}),
            ),
            ("steps[2] seats 3 members, and 3 are seated by then", steps(seat, post, seat)),
            (
                "steps[1] seats 2 members, and 3 are seated by then",
                steps(seat, {"step": "seat", "to": 2}),
            ),
            ("`to` is not a field of steps[1]", steps(seat, {"step": "post", "to": "pax"})),
            (
                "`from` of steps[1] is not a name, `last` or `all`",
                steps(seat, {"step": "post", "from": 3}),
            ),
            (
                "`from` of steps[0] names raya, who is not seated by then",
                steps({"step": "post", "from": "raya"}, seat),
            ),
            (
                "`from` of steps[1] names mira, who is not seated by then",
                steps(seat, {"step": "post", "from": "mira"}),
            ),
            (
                "`from` of steps[1] names peer3, and no member has that name",
                steps(seat, {"step": "post", "from": "peer3"}),
            ),
            (
                "`from` of steps[1] names Pax, and no member has that name",
                steps(seat, {"step": "post", "from": "Pax"}),
            ),
            (
                "`from` of steps[1] names peer6, and no member has that name",
                steps(seat, {"step": "post", "from": "peer6"}),
            ),
            (
                "`from` of steps[1] names peer07, and no member has that name",
                steps(seat, {"step": "post", "from": "peer07"}),
            ),
            (
                "`from` of steps[1] names peer-7, and no member has that name",
                steps(seat, {"step": "post", "from": "peer-7"}),
            ),
            (
                "`from` of steps[1] names peer, and no member has that name",
                steps(seat, {"step": "post", "from": "peer"}),
            ),
            ("`expect` is not an object", three(expect="whole")),
            ("`position` is not a field of `expect`", three(expect={"position": {}})),
            ("`verdict` of `expect` is not a text", three(expect={"verdict": 3})),
            *(
                (f"`verdict` of `expect` is `{verdict}`, which no fork report gives", said)
                for verdict in (
                    "",
                    "Whole",
                    "whole ",
                    "whole, a member behind",
                    "split",
                    "split at epoch",
                    "split at epoch x",
                    "split at epoch 3, both sides live",
                    "split at epoch 3\nwhole",
                )
                for said in [three(expect={"verdict": verdict})]
            ),
            ("`positions` of `expect` is not an object", three(expect={"positions": ["tip"]})),
            (
                "`positions` of `expect` names mira, who is seated by no step",
                three(members=5, expect={"positions": {"mira": "no data"}}),
            ),
            (
                "`posts` of `expect` names peer2, and no member has that name",
                three(expect={"posts": {"peer2": "read"}}),
            ),
            (
                "`positions` of `expect` gives pax `at the tip`, which is not among: tip, behind, "
                "lineage unknown, removed, never joined, no data",
                three(expect={"positions": {"pax": "at the tip"}}),
            ),
            (
                # No report read from files says a writer is gone.
                "`positions` of `expect` gives pax `ended`, which is not among: tip, behind, "
                "lineage unknown, removed, never joined, no data",
                three(expect={"positions": {"pax": "ended"}}),
            ),
            (
                "`posts` of `expect` gives raya `seen`, which is not among: read, "
                "unreadable, not a member, no log, no record",
                three(expect={"posts": {"raya": "seen"}}),
            ),
            (
                # A post is `sent` on its sender alone, and this is of the posts of the others.
                "`posts` of `expect` gives raya `sent`, which is not among: read, "
                "unreadable, not a member, no log, no record",
                three(expect={"posts": {"raya": "sent"}}),
            ),
        ]
        for why, said in refusals:
            with self.subTest(why=why, said=said):
                self.assertEqual(self.refusal(said), why)

    def test_a_name_with_a_number_no_reader_takes_is_refused(self):
        endless = "peer" + "7" * 5000

        self.assertTrue(
            self.refusal(steps({"step": "post", "from": endless})).startswith(
                "`from` of steps[0] names peer777"
            )
        )


class Expecting(unittest.TestCase):
    """What a scenario has `forks check` hold its report against."""

    def expectation(self, said: dict) -> dict:
        return scenario.scenario(said).expectation("2a30d6a2c1", IDS)

    def test_the_members_go_by_the_drivers_names_in_the_order_they_start(self):
        seated = steps({"step": "seat", "to": 4}, {"step": "seat", "to": 5}, members=5)

        self.assertEqual(
            [seat["name"] for seat in self.expectation(seated)["installations"].values()],
            ["saro", "raya", "pax", "mira", "kes"],
        )

    def test_a_scenario_that_expects_nothing_expects_a_whole_group_at_the_tip(self):
        self.assertEqual(
            self.expectation(three()),
            {
                "conversation": "2a30d6a2c1",
                "verdict": "whole",
                "installations": {
                    "a3f91c07": {"name": "saro", "position": "tip"},
                    "7b1e44d9": {"name": "raya", "position": "tip"},
                    "e09a3f52": {"name": "pax", "position": "tip"},
                },
                "posts": [
                    {
                        "sender": "e09a3f52",
                        "cells": {"a3f91c07": "read", "7b1e44d9": "read", "e09a3f52": "sent"},
                    }
                ],
            },
        )

    def test_a_scenario_of_posts_alone_expects_the_creator_and_its_posts(self):
        alone = steps({"step": "post"}, {"step": "post", "from": "all"})

        self.assertEqual(scenario.scenario(alone).seated, 1)
        self.assertEqual(
            self.expectation(alone),
            {
                "conversation": "2a30d6a2c1",
                "verdict": "whole",
                "installations": {"a3f91c07": {"name": "saro", "position": "tip"}},
                "posts": [{"sender": "a3f91c07", "cells": {"a3f91c07": "sent"}}] * 2,
            },
        )

    def test_what_is_expected_of_the_creator_is_held_like_that_of_any_member(self):
        saro, raya, pax = IDS[:3]
        expected = self.expectation(three(expect={"posts": {"saro": "unreadable"}}))

        self.assertEqual(
            expected["posts"],
            [{"sender": pax, "cells": {saro: "unreadable", raya: "read", pax: "sent"}}],
        )

    def test_a_member_no_step_seats_is_not_expected(self):
        bystanders = self.expectation(three(members=5))

        self.assertEqual(list(bystanders["installations"]), IDS[:3])
        self.assertEqual(list(bystanders["posts"][0]["cells"]), IDS[:3])

    def test_a_post_is_read_by_every_member_seated_when_it_was_sent_and_no_other(self):
        saro, raya, pax, mira, kes = IDS
        posts = self.expectation(
            steps(
                {"step": "post"},
                {"step": "seat", "to": 3},
                {"step": "post", "from": "raya"},
                {"step": "seat", "to": 5},
                {"step": "post"},
            )
        )["posts"]

        later = "not a member"
        self.assertEqual(
            posts,
            [
                {
                    "sender": saro,
                    "cells": {saro: "sent", raya: later, pax: later, mira: later, kes: later},
                },
                {
                    "sender": raya,
                    "cells": {saro: "read", raya: "sent", pax: "read", mira: later, kes: later},
                },
                {
                    "sender": kes,
                    "cells": {saro: "read", raya: "read", pax: "read", mira: "read", kes: "sent"},
                },
            ],
        )

    def test_every_member_in_turn_posts_in_the_order_of_the_members(self):
        every = three(steps=[{"step": "seat", "to": 3}, {"step": "post", "from": "all"}])
        posts = self.expectation(every)["posts"]

        self.assertEqual([post["sender"] for post in posts], IDS[:3])
        for post in posts:
            self.assertEqual(sorted(post["cells"].values()), ["read", "read", "sent"])

    def test_what_a_scenario_expects_changes_only_what_it_names(self):
        saro, raya, pax = IDS[:3]
        expected = self.expectation(
            three(
                steps=[{"step": "seat", "to": 3}, {"step": "post", "from": "all"}],
                expect={
                    "verdict": "whole, members behind",
                    "positions": {"pax": "behind"},
                    "posts": {"pax": "unreadable"},
                },
            )
        )

        self.assertEqual(expected["verdict"], "whole, members behind")
        self.assertEqual(
            [seat["position"] for seat in expected["installations"].values()],
            ["tip", "tip", "behind"],
        )
        self.assertEqual(
            [post["cells"] for post in expected["posts"]],
            [
                {saro: "sent", raya: "read", pax: "unreadable"},
                {saro: "read", raya: "sent", pax: "unreadable"},
                {saro: "read", raya: "read", pax: "sent"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
