import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from sim import scenario
from sim.peers import name_of
from sim.scenario import (
    ALL,
    LAST,
    Cut,
    Expect,
    Grow,
    Heal,
    Hold,
    Network,
    Post,
    Refused,
    Scenario,
    Seat,
    Wire,
    Withhold,
)

TOOL = Path(__file__).resolve().parents[1]

# The steps a scenario has, as a refusal names them.
STEPS = "`seat`, `post`, `hold`, `cut`, `heal`, `withhold` or `grow`"

# Ids a fork report would know five members by, in the order of the members.
IDS = ["a3f91c07", "7b1e44d9", "e09a3f52", "19f6b0e3", "b54a9d10"]


def three(**changed) -> dict:
    """A scenario of three members: the creator seats two, and the last one seated posts."""
    whole = {"members": 3, "steps": [{"step": "seat", "to": 3}, {"step": "post"}]}
    whole.update(changed)
    return whole


# A step that grows the group to the third epoch, two adders a round at most.
GROW = {"step": "grow", "epochs": 3, "adders": 2, "branches": 5}


def steps(*steps: dict, members: int = 5) -> dict:
    return {"members": members, "steps": list(steps)}


def modelled(*steps: dict, **network) -> dict:
    """A scenario of five members with a network model, which is of seed 7 and no more."""
    return {"members": 5, "network": {"seed": 7, **network}, "steps": list(steps)}


def withholding(*after: dict, **changed) -> dict:
    """A scenario that seats three, withholds a candidate from pax, seats mira, and goes on."""
    seat, withhold = {"step": "seat", "to": 3}, {"step": "withhold", "from": "pax"}
    whole = modelled(seat, withhold, {"step": "seat", "to": 4}, *after)
    whole.update(changed)
    return whole


class Reading(unittest.TestCase):
    def test_a_scenario_that_says_the_least_has_the_defaults(self):
        self.assertEqual(
            scenario.scenario(three()),
            Scenario(
                members=3,
                steps=(Seat(to=3, batch=10), Post(sender=LAST)),
                group="v2",
                timers="fast",
                max_message_kib=512,
                preset=None,
                budget=180.0,
                seed=0,
                network=None,
                expect=Expect(verdict="whole", positions={}, posts={}, applied=None),
            ),
        )

    def test_each_field_is_read_as_it_is_said(self):
        said = {
            "members": 7,
            "timers": {"consensus_timeout": 2000, "voting_delay": 0},
            "max_message_kib": 64,
            "budget": 12.5,
            "seed": 2**64 - 1,
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
                seed=2**64 - 1,
                expect=Expect("whole, members behind", {"peer7": "behind"}, {"raya": "unreadable"}),
            ),
        )

    def test_a_preset_is_read_by_its_name_whatever_the_name(self):
        for name in ("logos.test", "no preset of any library", "-x"):
            with self.subTest(name=name):
                self.assertEqual(scenario.scenario(three(preset=name)).preset, name)

    def test_a_network_model_and_its_steps_are_read_on_a_preset(self):
        held = scenario.scenario(withholding(preset="logos.test"))

        self.assertEqual((held.preset, held.network.seed), ("logos.test", 7))
        self.assertEqual(held.withheld_from, "pax")

    def test_a_hold_is_read_with_its_seconds_before_and_after_any_other_step(self):
        seat, post = {"step": "seat", "to": 3}, {"step": "post"}
        long, short = {"step": "hold", "seconds": 300}, {"step": "hold", "seconds": 0.5}
        held = scenario.scenario(steps(long, seat, short, post, long))

        self.assertEqual(held.steps, (Hold(300.0), Seat(3), Hold(0.5), Post(LAST), Hold(300.0)))
        self.assertEqual(held.seated, 3)

    def test_a_grow_step_is_read_with_its_fields_and_needs_no_network_model(self):
        held = scenario.scenario(steps(GROW))

        self.assertEqual(held.steps, (Grow(epochs=3, adders=2, branches=5),))
        self.assertTrue(held.grows)
        self.assertFalse(scenario.scenario(three()).grows)
        # How many it seats is the run's to say.
        self.assertEqual((held.seated, held.expect, held.network), (1, Expect(), None))
        by = {"step": "grow", "epochs": 3, "by": "saro", "branches": 5}
        self.assertEqual(
            scenario.scenario(steps(by)).steps, (Grow(epochs=3, adders=None, branches=5, by="saro"),)
        )
        sized = scenario.scenario(steps(dict(GROW, size=4))).steps
        self.assertEqual(sized, (Grow(epochs=3, adders=2, branches=5, size=4),))
        self.assertEqual(scenario.scenario(steps(dict(by, size=2))).steps[0].size, 2)

    def test_a_scenario_creates_a_groupv2_group_unless_it_says_v3(self):
        self.assertEqual(scenario.scenario(three()).group, "v2")
        self.assertEqual(scenario.scenario(three(group="v2")).group, "v2")
        self.assertEqual(scenario.scenario(three(group="v3")).group, "v3")
        grown = scenario.scenario(dict(steps(GROW), group="v3"))
        self.assertEqual((grown.group, grown.steps), ("v3", (Grow(epochs=3, adders=2, branches=5),)))

    def test_a_scenario_of_one_member_is_its_creator_alone(self):
        alone = scenario.scenario({"members": 1, "steps": [{"step": "hold", "seconds": 60}]})

        self.assertEqual((alone.members, alone.seated, alone.steps), (1, 1, (Hold(60.0),)))

    def test_a_network_model_that_says_its_seed_alone_has_no_loss_and_no_delay(self):
        network = scenario.scenario(three(network={"seed": 7})).network

        self.assertEqual(network, Network(seed=7, loss=0, welcome_loss=0, delay_ms=0))
        self.assertEqual([network.delay_of(name) for name in ("saro", "pax")], [0, 0])

    def test_each_field_of_a_network_model_is_read_as_it_is_said(self):
        said = {"seed": 2**64 - 1, "loss": 0.05, "welcome_loss": 1, "delay_ms": 2**64 - 1}
        network = scenario.scenario(three(network=said)).network

        self.assertEqual(network, Network(2**64 - 1, 0.05, 1, 2**64 - 1))
        self.assertEqual(network.delay_of("raya"), 2**64 - 1)
        least = scenario.scenario(three(network={"seed": 0, "loss": 0.0, "welcome_loss": 1.0}))
        self.assertEqual(least.network, Network(0, 0.0, 1.0, 0))

    def test_a_delay_by_member_holds_the_frames_of_the_members_it_names_and_of_no_other(self):
        said = {"seed": 7, "delay_ms": {"raya": 40, "pax": 0}}
        network = scenario.scenario(three(network=said)).network

        self.assertEqual(network.delay_ms, {"raya": 40, "pax": 0})
        self.assertEqual([network.delay_of(name) for name in ("saro", "raya", "pax")], [0, 40, 0])

    def test_the_wire_is_read_as_it_is_said_and_says_nothing_it_is_not_told(self):
        said = {"seed": 2**64 - 1, "delay_ms": {"raya": 40}, "jitter_ms": 10, "loss": 0.01}
        wire = scenario.scenario(three(wire=said)).wire

        self.assertEqual(wire, Wire(2**64 - 1, {"raya": 40}, 10, 0.01))
        self.assertEqual([wire.delay_of(name) for name in ("saro", "raya")], [0, 40])
        self.assertEqual(scenario.scenario(three(wire={"seed": 0})).wire, Wire(0, 0, 0, 0))
        self.assertIsNone(scenario.scenario(three()).wire)

    def test_the_steps_of_a_network_model_are_read_as_they_are_said(self):
        held = scenario.scenario(
            modelled(
                {"step": "seat", "to": 4},
                {"step": "cut", "between": [["saro", "mira"], ["raya"]]},
                {"step": "post", "from": "pax"},
                {"step": "heal"},
                {"step": "cut", "between": [["pax"], ["saro"]]},
            )
        )

        self.assertEqual(
            held.steps,
            (
                Seat(4),
                Cut((("saro", "mira"), ("raya",))),
                Post("pax"),
                Heal(),
                Cut((("pax",), ("saro",))),
            ),
        )
        self.assertIsNone(held.withheld_from)

    def test_a_candidate_is_withheld_from_the_member_a_step_names(self):
        held = scenario.scenario(withholding({"step": "post", "from": "mira"}))

        self.assertEqual(held.steps, (Seat(3), Withhold("pax"), Seat(4), Post("mira")))
        self.assertEqual(held.withheld_from, "pax")
        self.assertEqual((held.seated, held.posts()), (4, [(3, 4)]))
        self.assertIsNone(held.expect.applied)

    def test_what_is_expected_once_the_group_applied_a_withheld_candidate_is_read(self):
        applied = {
            "verdict": "split at epoch 2",
            "positions": {"mira": "behind"},
            "posts": {"pax": "unreadable"},
        }
        expect = {"verdict": "whole, members behind", "applied": applied}

        self.assertEqual(
            scenario.scenario(withholding(expect=expect)).expect,
            Expect(
                "whole, members behind",
                {},
                {},
                Expect("split at epoch 2", {"mira": "behind"}, {"pax": "unreadable"}),
            ),
        )
        self.assertEqual(
            scenario.scenario(withholding(expect={"applied": {}})).expect.applied, Expect()
        )

    def test_each_run_of_a_sweep_has_the_seeds_after_those_of_the_run_before(self):
        held = scenario.scenario(three(seed=4, network={"seed": 7, "loss": 0.5}))

        self.assertEqual(held.of_run(1), held)
        self.assertEqual([held.of_run(number).seed for number in (1, 2, 10)], [4, 5, 13])
        self.assertEqual([held.of_run(number).network.seed for number in (1, 2, 10)], [7, 8, 16])
        self.assertEqual(held.of_run(3).network, Network(9, 0.5, 0, 0))
        self.assertEqual(
            dataclasses.replace(held.of_run(3), seed=held.seed, network=held.network), held
        )
        unmodelled = scenario.scenario(three())
        self.assertEqual(unmodelled.of_run(3), dataclasses.replace(unmodelled, seed=2))

        both = scenario.scenario(three(network={"seed": 7}, wire={"seed": 70, "loss": 0.5}))
        self.assertEqual(
            (both.of_run(3).network.seed, both.of_run(3).wire), (9, Wire(72, 0, 0, 0.5))
        )
        wired = scenario.scenario(three(wire={"seed": 70}))
        self.assertEqual((wired.of_run(2).network, wired.of_run(2).wire.seed), (None, 71))

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
        self.assertEqual(
            kept,
            [
                "50-epochs-by-saro-waku-loss-0-groupv3.json",
                "50-epochs-by-saro-waku-loss-0.1-groupv3.json",
                "50-epochs-by-saro-waku-loss-0.1.json",
                "50-epochs-by-saro-waku-loss-0.json",
                "50-epochs-by-saro-waku-loss-1-groupv3.json",
                "50-epochs-by-saro-waku-loss-1.json",
                "50-epochs-by-saro-waku-loss-5-groupv3.json",
                "50-epochs-by-saro-waku-loss-5.json",
                "50-epochs-waku-loss.json",
                "50-epochs-waku.json",
                "50-epochs.json",
                "fifty-sized.json",
                "fleet-five.json",
                "grow-by-saro-waku-loss-0.01.json",
                "grow-by-saro-waku-loss-0.1.json",
                "grow-by-saro-waku-loss-0.5.json",
                "grow-by-saro-waku-loss-0.75.json",
                "grow-by-saro-waku-loss-1.json",
                "grow-by-saro-waku-loss-10.json",
                "grow-by-saro-waku-loss-5.json",
                "grow-waku-loss-0-groupv3.json",
                "grow-waku-loss-0.01.json",
                "grow-waku-loss-0.1-groupv3.json",
                "grow-waku-loss-0.1.json",
                "grow-waku-loss-0.5-groupv3.json",
                "grow-waku-loss-0.5.json",
                "grow-waku-loss-0.75.json",
                "grow-waku-loss-0.json",
                "grow-waku-loss-1-groupv3.json",
                "grow-waku-loss-1.json",
                "grow-waku-loss-10.json",
                "grow-waku-loss-5-groupv3.json",
                "grow-waku-loss-5.json",
                "grow-waku-size-50.json",
                "lossy-wire.json",
                "lossy.json",
                "lost-candidate.json",
                "three.json",
                "twenty.json",
            ],
        )
        sizes = (
            ("three.json", 3),
            ("twenty.json", 20),
            ("fifty-sized.json", 50),
            ("50-epochs.json", 51),
        )
        for name, members in sizes:
            held = scenario.read(TOOL / "scenarios" / name)
            self.assertEqual((held.members, held.seated), (members, members), name)
            self.assertEqual(held.expect, Expect(), name)
            self.assertIsNone(held.network, name)
            self.assertIsNone(held.preset, name)

    def test_fifty_epochs_are_on_the_librarys_own_timers_and_on_waku_held_and_not_lost(self):
        lossless = scenario.read(TOOL / "scenarios" / "50-epochs.json")
        waku = scenario.read(TOOL / "scenarios" / "50-epochs-waku.json")

        self.assertEqual(lossless.steps[0], Seat(to=51, batch=1))
        self.assertEqual(waku.steps, lossless.steps)
        self.assertEqual((waku.timers, lossless.timers), ("default", "default"))
        self.assertEqual((waku.network.loss, waku.network.welcome_loss), (0, 0))
        delays = [waku.network.delay_of(name_of(seat)) for seat in range(51)]
        self.assertEqual(len(set(delays)), 51)
        self.assertEqual((min(delays), max(delays), delays[0]), (284, 644, 518))
        self.assertEqual(round(sum(delays) / 51, 1), 507.6)

    def test_fifty_epochs_on_waku_with_loss_lose_a_twentieth_and_are_otherwise_those_on_waku(self):
        waku = scenario.read(TOOL / "scenarios" / "50-epochs-waku.json")
        lossy = scenario.read(TOOL / "scenarios" / "50-epochs-waku-loss.json")

        self.assertEqual((lossy.network.loss, lossy.network.welcome_loss), (0.05, 0.05))
        held = dataclasses.replace(lossy.network, loss=0, welcome_loss=0)
        self.assertEqual(dataclasses.replace(lossy, network=held), waku)

    def test_fifty_epochs_by_saro_are_those_on_waku_with_loss_grown_by_saro_at_their_rate(self):
        seated = scenario.read(TOOL / "scenarios" / "50-epochs-waku-loss.json")
        alone = Grow(epochs=50, adders=None, branches=51, by="saro")
        for percent, rate in ((0, 0), (0.1, 0.001), (1, 0.01), (5, 0.05)):
            grown = scenario.read(
                TOOL / "scenarios" / f"50-epochs-by-saro-waku-loss-{percent}.json"
            )
            rated = dataclasses.replace(seated.network, loss=rate, welcome_loss=rate)
            self.assertEqual(
                grown, dataclasses.replace(seated, network=rated, steps=(alone,)), percent
            )

    def test_a_grow_on_waku_has_two_hundred_delays_of_its_arrivals_and_differs_by_its_rate(self):
        grows = {
            rate: scenario.read(TOOL / "scenarios" / f"grow-waku-loss-{percent}.json")
            for percent, rate in (
                (0, 0),
                (0.01, 0.0001),
                (0.1, 0.001),
                (0.5, 0.005),
                (0.75, 0.0075),
                (1, 0.01),
                (5, 0.05),
                (10, 0.1),
            )
        }
        first = grows[0.01]

        self.assertEqual(first.steps, (Grow(epochs=50, adders=3, branches=5),))
        self.assertEqual((first.members, first.timers, first.network.seed), (200, "default", 1))
        delays = [first.network.delay_of(name_of(seat)) for seat in range(200)]
        self.assertEqual((min(delays), max(delays), delays[0]), (268, 659, 518))
        self.assertEqual(round(sum(delays) / 200, 1), 507.6)
        for rate, held in grows.items():
            self.assertEqual((held.network.loss, held.network.welcome_loss), (rate, rate))
            rated = dataclasses.replace(held.network, loss=0.01, welcome_loss=0.01)
            self.assertEqual(dataclasses.replace(held, network=rated), first)

    def test_a_grow_of_fifty_on_waku_goes_toward_epoch_1000_with_any_fifty_in_a_row_spread(self):
        sized = scenario.read(TOOL / "scenarios" / "grow-waku-size-50.json")

        self.assertEqual(sized.steps, (Grow(epochs=1000, adders=3, branches=2, size=50),))
        self.assertEqual((sized.members, sized.timers, sized.network.seed), (3001, "default", 1))
        self.assertEqual((sized.network.loss, sized.network.welcome_loss), (0, 0))
        delays = [sized.network.delay_of(name_of(seat)) for seat in range(3001)]
        self.assertEqual((min(delays), max(delays), delays[0]), (263, 673, 518))
        self.assertEqual(round(sum(delays) / 3001, 1), 507.6)
        spans = [
            max(delays[seat : seat + 50]) - min(delays[seat : seat + 50]) for seat in range(3001 - 49)
        ]
        self.assertEqual((min(spans), max(spans)), (318, 376))

    def test_a_grow_by_saro_on_waku_is_the_grow_on_waku_of_its_rate_with_saro_alone_adding(self):
        alone = Grow(epochs=50, adders=None, branches=5, by="saro")
        for percent in (0.01, 0.1, 0.5, 0.75, 1, 5, 10):
            drawn = scenario.read(TOOL / "scenarios" / f"grow-waku-loss-{percent}.json")
            by = scenario.read(TOOL / "scenarios" / f"grow-by-saro-waku-loss-{percent}.json")
            self.assertEqual(by, dataclasses.replace(drawn, steps=(alone,)), percent)

    def test_each_groupv3_scenario_is_its_groupv2_sibling_on_a_groupv3_group(self):
        siblings = [
            *(f"50-epochs-by-saro-waku-loss-{percent}" for percent in (0, 0.1, 1, 5)),
            *(f"grow-waku-loss-{percent}" for percent in (0, 0.1, 0.5, 1, 5)),
        ]
        kept = sorted(file.stem for file in (TOOL / "scenarios").glob("*-groupv3.json"))
        self.assertEqual(kept, sorted(f"{name}-groupv3" for name in siblings))
        for name in siblings:
            with self.subTest(name=name):
                groupv2 = scenario.read(TOOL / "scenarios" / f"{name}.json")
                groupv3 = scenario.read(TOOL / "scenarios" / f"{name}-groupv3.json")
                self.assertEqual(groupv2.group, "v2")
                self.assertEqual(groupv3, dataclasses.replace(groupv2, group="v3"))

    def test_the_scenarios_of_the_network_model_seat_five_members_one_at_a_time(self):
        one_at_a_time = Seat(to=5, batch=1)
        lossy = scenario.read(TOOL / "scenarios" / "lossy.json")
        self.assertEqual((lossy.members, lossy.steps[0], lossy.timers), (5, one_at_a_time, "fast"))
        self.assertTrue(0 < lossy.network.loss < 1)
        self.assertEqual((lossy.network.welcome_loss, lossy.network.delay_ms), (0, 0))
        self.assertEqual(lossy.expect, Expect())

        lost = scenario.read(TOOL / "scenarios" / "lost-candidate.json")
        self.assertEqual(
            lost.steps, (Seat(to=4, batch=1), Withhold("pax"), one_at_a_time, Post("kes"))
        )
        self.assertEqual((lost.members, lost.timers), (5, "fast"))
        self.assertEqual(lost.network, Network(seed=lost.network.seed))
        self.assertEqual(
            lost.expect, Expect(applied=Expect("split at epoch 4", {}, {"pax": "unreadable"}))
        )

    def test_the_scenario_of_a_lossy_wire_seats_five_two_at_a_time_and_has_each_post(self):
        wired = scenario.read(TOOL / "scenarios" / "lossy-wire.json")

        self.assertEqual((wired.members, wired.steps), (5, (Seat(to=5, batch=2), Post(ALL))))
        self.assertTrue(0 < wired.wire.loss < 1)
        self.assertTrue(wired.wire.delay_ms > 0 and wired.wire.jitter_ms > 0)
        self.assertEqual((wired.network, wired.expect), (None, Expect()))

    def test_the_scenario_of_a_fleet_seats_five_one_at_a_time_and_holds_five_minutes(self):
        fleet = scenario.read(TOOL / "scenarios" / "fleet-five.json")

        self.assertEqual((fleet.members, fleet.preset, fleet.timers), (5, "logos.test", "default"))
        self.assertEqual(fleet.steps, (Seat(to=5, batch=1), Post(LAST), Hold(300.0)))
        self.assertEqual((fleet.budget, fleet.network, fleet.expect), (400.0, None, Expect()))


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
            ("`members` of the scenario is 0, and 1 is the least", three(members=0)),
            ("`members` of the scenario is 65535, and 65534 is the most", three(members=65535)),
            (
                f"`members` of the scenario is {10**30}, and 65534 is the most",
                three(members=10**30),
            ),
            ("`group` is not `v2` or `v3`", three(group="v4")),
            ("`group` is not `v2` or `v3`", three(group="V3")),
            ("`group` is not `v2` or `v3`", three(group=3)),
            ("`group` is not `v2` or `v3`", three(group=None)),
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
            ("`seed` of the scenario is not a whole number", three(seed="1")),
            ("`seed` of the scenario is not a whole number", three(seed=True)),
            ("`seed` of the scenario is -1, and 0 is the least", three(seed=-1)),
            (f"`seed` of the scenario is {2**64}, and {2**64 - 1} is the most", three(seed=2**64)),
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
            ("`preset` is not the name of a preset", three(preset="")),
            ("`preset` is not the name of a preset", three(preset=None)),
            ("`preset` is not the name of a preset", three(preset=["logos.test"])),
            ("`preset` holds a character that cannot be printed", three(preset="logos.test\n")),
            ("`preset` holds a character that cannot be printed", three(preset="logos\0test")),
            ("`preset` holds a character that cannot be printed", three(preset="\ud800")),
            (
                "`max_message_kib` is said with `preset`, and a preset fixes the largest message",
                three(preset="logos.test", max_message_kib=512),
            ),
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
            (f"steps[0] is not an object whose `step` is {STEPS}", steps("seat")),
            (f"steps[1] is not an object whose `step` is {STEPS}", steps(seat, {"to": 4})),
            (
                f"steps[1] is not an object whose `step` is {STEPS}",
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
            ("steps[1] lacks `seconds`", steps(seat, {"step": "hold"})),
            (
                "`budget` is not a field of steps[1]",
                steps(seat, {"step": "hold", "seconds": 5, "budget": 9}),
            ),
            (
                "`seconds` of steps[1] is not a number of seconds above 0",
                steps(seat, {"step": "hold", "seconds": 0}),
            ),
            (
                "`seconds` of steps[0] is not a number of seconds above 0",
                steps({"step": "hold", "seconds": "60"}),
            ),
            (
                "`seconds` of steps[0] is not a number of seconds above 0",
                steps({"step": "hold", "seconds": True}),
            ),
            (
                "`seconds` of steps[0] is more seconds than a wait can be told to last",
                steps({"step": "hold", "seconds": 10**400}),
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
        grow = dict(GROW)
        unsaid = {name: held for name, held in grow.items() if name != "adders"}
        refusals += [
            (
                "steps[1] is a `grow` step, and a `grow` step is the only step",
                three(steps=[seat, grow]),
            ),
            (
                "steps[0] is a `grow` step, and a `grow` step is the only step",
                three(steps=[grow, post]),
            ),
            (
                "`expect` is said with a `grow` step, which expects no outcome",
                three(steps=[grow], expect={}),
            ),
            (
                "steps[0] is a `grow` step, and the scenario has 1 member",
                three(members=1, steps=[grow]),
            ),
            (
                "steps[0] lacks `branches`",
                three(steps=[{name: held for name, held in grow.items() if name != "branches"}]),
            ),
            ("`from` is not a field of steps[0]", three(steps=[dict(grow, **{"from": "saro"})])),
            ("`epochs` of steps[0] is 0, and 1 is the least", three(steps=[dict(grow, epochs=0)])),
            ("`adders` of steps[0] is 0, and 1 is the least", three(steps=[dict(grow, adders=0)])),
            (
                "steps[0] says `adders` and `by`, and a `grow` step says one or the other",
                three(steps=[dict(grow, by="saro")]),
            ),
            (
                "steps[0] lacks `adders` or `by`",
                three(steps=[unsaid]),
            ),
            (
                "`by` of steps[0] names raya, who is not seated when a `grow` step begins",
                three(steps=[dict(unsaid, by="raya")]),
            ),
            (
                "`by` of steps[0] names nobody, and no member has that name",
                three(steps=[dict(unsaid, by="nobody")]),
            ),
            ("`by` of steps[0] is not a name", three(steps=[dict(unsaid, by=0)])),
            (
                "`branches` of steps[0] is 1, and 2 is the least",
                three(steps=[dict(grow, branches=1)]),
            ),
            (
                "`epochs` of steps[0] is not a whole number",
                three(steps=[dict(grow, epochs=50.0)]),
            ),
            ("`size` of steps[0] is 1, and 2 is the least", three(steps=[dict(grow, size=1)])),
            (
                "`size` of steps[0] is below twice `adders`, and each adder of a group at its "
                "size removes a member that adds none",
                three(steps=[dict(grow, size=3)]),
            ),
            (
                "steps[0] is a `grow` step of a `size`, and a GroupV3 group removes no member",
                three(steps=[dict(grow, size=4)], group="v3"),
            ),
            (
                "steps[0] is a `grow` step of a `size`, and a GroupV3 group removes no member",
                three(steps=[dict(unsaid, by="saro", size=2)], group="v3"),
            ),
        ]
        for why, said in refusals:
            with self.subTest(why=why, said=said):
                self.assertEqual(self.refusal(said), why)

    def test_each_thing_a_network_model_cannot_be_is_refused_by_name(self):
        not_a_rate = "of `network` is not a number from 0 to 1"
        refusals = [
            ("`network` is not an object", 7),
            ("`network` is not an object", [{"seed": 7}]),
            ("`network` lacks `seed`", {}),
            ("`network` lacks `seed`", {"loss": 0.1}),
            ("`rate` is not a field of `network`", {"seed": 7, "rate": 0.1}),
            ("`seed` of `network` is not a whole number", {"seed": "7"}),
            ("`seed` of `network` is not a whole number", {"seed": 7.0}),
            ("`seed` of `network` is not a whole number", {"seed": True}),
            ("`seed` of `network` is not a whole number", {"seed": None}),
            ("`seed` of `network` is -1, and 0 is the least", {"seed": -1}),
            (
                f"`seed` of `network` is {2**64}, and {2**64 - 1} is the most",
                {"seed": 2**64},
            ),
            *(
                (f"`{rate}` {not_a_rate}", {"seed": 7, rate: said})
                for rate in ("loss", "welcome_loss")
                for said in ("0.1", True, False, None, [0.1], -0.001, 1.001, 2, float("nan"))
            ),
            ("`delay_ms` of `network` is not a whole number", {"seed": 7, "delay_ms": 0.5}),
            ("`delay_ms` of `network` is not a whole number", {"seed": 7, "delay_ms": "5"}),
            ("`delay_ms` of `network` is not a whole number", {"seed": 7, "delay_ms": True}),
            ("`delay_ms` of `network` is not a whole number", {"seed": 7, "delay_ms": [5]}),
            ("`delay_ms` of `network` is -1, and 0 is the least", {"seed": 7, "delay_ms": -1}),
            (
                f"`delay_ms` of `network` is {2**64}, and {2**64 - 1} is the most",
                {"seed": 7, "delay_ms": 2**64},
            ),
            (
                "`delay_ms` of `network` names mira, who is not among the members",
                {"seed": 7, "delay_ms": {"mira": 5}},
            ),
            (
                "`delay_ms` of `network` names peer3, and no member has that name",
                {"seed": 7, "delay_ms": {"peer3": 5}},
            ),
            (
                "`raya` of `delay_ms` of `network` is not a whole number",
                {"seed": 7, "delay_ms": {"raya": 1.5}},
            ),
            (
                "`raya` of `delay_ms` of `network` is not a whole number",
                {"seed": 7, "delay_ms": {"raya": True}},
            ),
            (
                "`raya` of `delay_ms` of `network` is -1, and 0 is the least",
                {"seed": 7, "delay_ms": {"raya": -1}},
            ),
            (
                f"`raya` of `delay_ms` of `network` is {2**64}, and {2**64 - 1} is the most",
                {"seed": 7, "delay_ms": {"raya": 2**64}},
            ),
        ]
        for why, said in refusals:
            with self.subTest(why=why, said=said):
                self.assertEqual(self.refusal(three(network=said)), why)

    def test_each_thing_the_wire_cannot_be_is_refused_by_name(self):
        refusals = [
            ("`wire` is not an object", 7),
            ("`wire` lacks `seed`", {"loss": 0.1}),
            ("`welcome_loss` is not a field of `wire`", {"seed": 7, "welcome_loss": 0.1}),
            ("`seed` of `wire` is not a whole number", {"seed": 7.0}),
            ("`seed` of `wire` is -1, and 0 is the least", {"seed": -1}),
            ("`loss` of `wire` is not a number from 0 to 1", {"seed": 7, "loss": 1.5}),
            ("`loss` of `wire` is not a number from 0 to 1", {"seed": 7, "loss": True}),
            ("`jitter_ms` of `wire` is not a whole number", {"seed": 7, "jitter_ms": 0.5}),
            ("`jitter_ms` of `wire` is -1, and 0 is the least", {"seed": 7, "jitter_ms": -1}),
            ("`delay_ms` of `wire` is not a whole number", {"seed": 7, "delay_ms": "5"}),
            (
                "`delay_ms` of `wire` names mira, who is not among the members",
                {"seed": 7, "delay_ms": {"mira": 5}},
            ),
            (
                "`raya` of `delay_ms` of `wire` is -1, and 0 is the least",
                {"seed": 7, "delay_ms": {"raya": -1}},
            ),
        ]
        for why, said in refusals:
            with self.subTest(why=why, said=said):
                self.assertEqual(self.refusal(three(wire=said)), why)

    def test_a_step_of_the_network_model_is_refused_in_a_scenario_without_one(self):
        seat = {"step": "seat", "to": 3}
        for step in (
            {"step": "cut", "between": [["saro"], ["raya"]]},
            {"step": "heal"},
            {"step": "withhold", "from": "pax"},
        ):
            with self.subTest(step=step):
                self.assertEqual(
                    self.refusal(steps(seat, step)),
                    f"steps[1] is a `{step['step']}` step, and the scenario has no `network`",
                )

    def test_each_cut_and_heal_a_run_could_not_do_is_refused_by_name(self):
        seat, heal = {"step": "seat", "to": 3}, {"step": "heal"}

        def cut(*sides: list) -> dict:
            return {"step": "cut", "between": list(sides)}

        no_sides = "`between` of steps[1] is not two lists of one name or more"
        refusals = [
            ("steps[1] lacks `between`", modelled(seat, {"step": "cut"})),
            (
                "`sides` is not a field of steps[1]",
                modelled(seat, {"step": "cut", "between": [["saro"], ["raya"]], "sides": 2}),
            ),
            (no_sides, modelled(seat, {"step": "cut", "between": "saro"})),
            (no_sides, modelled(seat, {"step": "cut", "between": {"saro": "raya"}})),
            (no_sides, modelled(seat, cut(["saro"]))),
            (no_sides, modelled(seat, cut(["saro"], ["raya"], ["pax"]))),
            (no_sides, modelled(seat, cut(["saro"], []))),
            (no_sides, modelled(seat, cut([], ["raya"]))),
            (no_sides, modelled(seat, cut(["saro"], "raya"))),
            (no_sides, modelled(seat, cut(["saro"], [3]))),
            (
                "`between` of steps[1] names mira, who is not seated by then",
                modelled(seat, cut(["saro"], ["raya", "mira"])),
            ),
            (
                "`between` of steps[1] names peer3, and no member has that name",
                modelled(seat, cut(["peer3"], ["raya"])),
            ),
            (
                "`between` of steps[1] names raya twice",
                modelled(seat, cut(["saro", "raya"], ["pax", "raya"])),
            ),
            (
                "`between` of steps[1] names pax twice",
                modelled(seat, cut(["saro"], ["pax", "pax"])),
            ),
            (
                "steps[2] cuts the group, and a cut is in force by then",
                modelled(seat, cut(["saro"], ["raya"]), cut(["saro"], ["pax"])),
            ),
            ("`between` is not a field of steps[1]", modelled(seat, dict(heal, between=[]))),
            ("steps[1] heals a cut, and no cut is in force by then", modelled(seat, heal)),
            (
                "steps[3] heals a cut, and no cut is in force by then",
                modelled(seat, cut(["saro"], ["raya"]), heal, heal),
            ),
            (
                "steps[1] is a `cut` step, and no cut stops a GroupV3 frame sent outside SDS, "
                "which names no sender",
                dict(modelled(seat, cut(["saro"], ["raya"]), heal), group="v3"),
            ),
        ]
        for why, said in refusals:
            with self.subTest(why=why, said=said):
                self.assertEqual(self.refusal(said), why)

    def test_each_withheld_candidate_a_run_could_not_tell_the_outcome_of_is_refused_by_name(self):
        seat = {"step": "seat", "to": 3}

        def withhold(member: object = "pax") -> dict:
            return {"step": "withhold", "from": member}

        def post(sender: str) -> dict:
            return {"step": "post", "from": sender}

        cut, heal = {"step": "cut", "between": [["saro"], ["raya"]]}, {"step": "heal"}
        it = "steps[1] is a `withhold` step"
        refusals = [
            ("steps[1] lacks `from`", modelled(seat, {"step": "withhold"})),
            ("`to` is not a field of steps[1]", modelled(seat, dict(withhold(), to=4))),
            ("`from` of steps[1] is not a name", modelled(seat, withhold(3))),
            (
                "`from` of steps[1] names mira, who is not seated by then",
                modelled(seat, withhold("mira"), {"step": "seat", "to": 4}),
            ),
            (
                "`from` of steps[1] names all, and no member has that name",
                modelled(seat, withhold("all"), {"step": "seat", "to": 4}),
            ),
            (
                "steps[3] is a second `withhold` step, and a run has one",
                withholding(withhold("raya"), {"step": "seat", "to": 5}),
            ),
            (f"{it}, and a GroupV3 group has no commit candidate", withholding(group="v3")),
            (f"{it}, and the step after it is not a `seat` step", modelled(seat, withhold())),
            (
                f"{it}, and the step after it is not a `seat` step",
                modelled(seat, withhold(), post("raya"), {"step": "seat", "to": 4}),
            ),
            (
                f"{it}, and steps[2] seats more than one batch",
                modelled(seat, withhold(), {"step": "seat", "to": 5, "batch": 1}),
            ),
            (
                f"{it}, and steps[4] is a second `seat` step after it",
                withholding(post("mira"), {"step": "seat", "to": 5}),
            ),
            (f"{it}, and steps[3] has pax post, whom it withholds from", withholding(post("pax"))),
            (
                f"{it}, and steps[4] has pax post, whom it withholds from",
                withholding(post("mira"), post("all")),
            ),
            (
                "steps[3] is a `withhold` step, and the scenario has a `cut` step",
                modelled(seat, cut, heal, withhold(), {"step": "seat", "to": 4}),
            ),
            (f"{it}, and the scenario has a `cut` step", withholding(cut)),
            (
                f"{it}, and `loss` of `network` is above 0",
                withholding(network={"seed": 7, "loss": 0.01}),
            ),
            (
                f"{it}, and `welcome_loss` of `network` is above 0",
                withholding(network={"seed": 7, "welcome_loss": 1}),
            ),
            (
                f"{it}, and `positions` of `expect` gives pax `behind`, whom it withholds from "
                "and the run then waits on",
                withholding(expect={"positions": {"pax": "behind"}}),
            ),
            (
                "`expect` holds `applied`, and no step is a `withhold` step",
                dict(modelled(seat), expect={"applied": {}}),
            ),
        ]
        for why, said in refusals:
            with self.subTest(why=why, said=said):
                self.assertEqual(self.refusal(said), why)

    def test_what_is_expected_of_a_candidate_applied_is_checked_as_what_is_expected(self):
        of = "of `applied` of `expect`"
        refusals = [
            ("`applied` of `expect` is not an object", "split at epoch 2"),
            (f"`applied` is not a field {of}", {"applied": {}}),
            (f"`position` is not a field {of}", {"position": {}}),
            (f"`verdict` {of} is not a text", {"verdict": 2}),
            (f"`verdict` {of} is `split`, which no fork report gives", {"verdict": "split"}),
            (f"`positions` {of} is not an object", {"positions": ["tip"]}),
            (
                f"`positions` {of} names kes, who is seated by no step",
                {"positions": {"kes": "no data"}},
            ),
            (
                f"`posts` {of} names peer2, and no member has that name",
                {"posts": {"peer2": "read"}},
            ),
            (
                f"`positions` {of} gives pax `ended`, which is not among: tip, behind, "
                "lineage unknown, removed, never joined, no data",
                {"positions": {"pax": "ended"}},
            ),
            (
                f"`posts` {of} gives pax `seen`, which is not among: read, unreadable, "
                "not a member, no log, no record",
                {"posts": {"pax": "seen"}},
            ),
        ]
        for why, said in refusals:
            with self.subTest(why=why, said=said):
                self.assertEqual(self.refusal(withholding(expect={"applied": said})), why)

    def test_the_member_a_candidate_is_withheld_from_may_be_expected_anywhere_once_applied(self):
        expect = {"positions": {"pax": "tip"}, "applied": {"positions": {"pax": "behind"}}}

        self.assertEqual(
            scenario.scenario(withholding(expect=expect)).expect.applied.positions,
            {"pax": "behind"},
        )

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

    def test_a_run_whose_group_applied_the_withheld_candidate_is_held_to_what_applied_says(self):
        saro, raya, pax, mira = IDS[:4]
        held = scenario.scenario(
            withholding(
                {"step": "post", "from": "mira"},
                expect={
                    "posts": {"raya": "no record"},
                    "applied": {
                        "verdict": "split at epoch 2",
                        "positions": {"mira": "behind"},
                        "posts": {"pax": "unreadable"},
                    },
                },
            )
        )

        not_applied = held.expectation("2a30d6a2c1", IDS)
        self.assertEqual(not_applied, held.expectation("2a30d6a2c1", IDS, applied=False))
        self.assertEqual(not_applied["verdict"], "whole")
        self.assertEqual(
            [seat["position"] for seat in not_applied["installations"].values()], ["tip"] * 4
        )
        self.assertEqual(
            not_applied["posts"],
            [
                {
                    "sender": mira,
                    "cells": {saro: "read", raya: "no record", pax: "read", mira: "sent"},
                }
            ],
        )
        self.assertEqual(
            held.expectation("2a30d6a2c1", IDS, applied=True),
            {
                "conversation": "2a30d6a2c1",
                "verdict": "split at epoch 2",
                "installations": {
                    saro: {"name": "saro", "position": "tip"},
                    raya: {"name": "raya", "position": "tip"},
                    pax: {"name": "pax", "position": "tip"},
                    mira: {"name": "mira", "position": "behind"},
                },
                "posts": [
                    {
                        "sender": mira,
                        "cells": {saro: "read", raya: "read", pax: "unreadable", mira: "sent"},
                    }
                ],
            },
        )

    def test_a_scenario_that_says_nothing_of_a_candidate_applied_expects_a_whole_group_of_it(self):
        held = scenario.scenario(withholding(expect={"verdict": "whole, members behind"}))

        self.assertEqual(
            held.expectation("2a30d6a2c1", IDS, applied=True),
            scenario.scenario(withholding()).expectation("2a30d6a2c1", IDS),
        )

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
