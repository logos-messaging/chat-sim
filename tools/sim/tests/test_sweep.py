import contextlib
import dataclasses
import io
import json
import re
import shlex
import signal
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from unittest import mock

from sim import scenario
from sim.cli import main
from sim.peers import Watch
from sim.run import Settings
from sim.sweep import Interrupts, sweep

from tests import STANDIN, forks_whose
from tests.test_run import APPLIED, Driven, Scratch, given, said, withholding
from tests.test_table import report as whole_run

ROW = re.compile(r" *(\d+) +(\d+\.\d) +(\S+ of \S+|-) +(.*?)  +(\S.*)")


def stand_ins(directory: Path, faults: Dict[int, Sequence[str]]) -> Path:
    """Writes a stand-in for chat-cli that has the faults given for a run in that run alone."""
    program = directory / "chat-cli"
    standin = [sys.executable, "-B", str(STANDIN)]
    lines = ["#!/bin/sh", 'case "$*" in']
    for number, named in faults.items():
        flags = [word for fault in named for word in ("--fault", fault)]
        lines.append(f'*"/{number}/data"*) exec {shlex.join(standin + flags)} "$@" ;;')
    lines += ["esac", f'exec {shlex.join(standin)} "$@"']
    program.write_text("\n".join(lines) + "\n", encoding="utf-8")
    program.chmod(0o755)
    return program


def cells(row: str) -> Tuple[str, ...]:
    """A row but for its seconds: the run, the members at the tip, the verdict and the check."""
    number, _, at_tip, verdict, check = ROW.fullmatch(row).groups()
    return number, at_tip, verdict, check


class Sweeping(Scratch):
    """`sweep` against stand-ins for chat-cli."""

    def sweep_of(
        self,
        runs: int,
        faults: Dict[int, Sequence[str]],
        forks: Optional[Path] = None,
        **changed,
    ) -> dict:
        self.said: List[str] = []
        settings = Settings(
            chat_cli=stand_ins(self.scratch, faults),
            scenario=scenario.scenario(said(**changed)),
            out=self.out,
        )
        if forks is not None:
            settings = dataclasses.replace(settings, forks=forks)
        return sweep(settings, runs, Interrupts(), self.said.append)

    def test_each_run_has_a_directory_and_a_row_and_the_last_line_counts_them(self):
        report = self.sweep_of(3, {2: ["deaf=raya"]}, budget=2)

        unread = "the post was not read within the budget by: raya"
        self.assertEqual(self.said[0], "run  seconds  at the tip  verdict                 check")
        self.assertEqual(
            [cells(row)[:3] for row in self.said[1:4]],
            [("1", "3 of 3", "whole"), ("2", "3 of 3", "whole"), ("3", "3 of 3", "whole")],
        )
        self.assertEqual(cells(self.said[1])[3], "as expected")
        self.assertRegex(
            cells(self.said[2])[3],
            r"^post 1 of pax \([0-9a-f]{8}\) on raya \([0-9a-f]{8}\): no record, expected read; "
            f"stopped: {unread}$",
        )
        self.assertEqual(self.said[4:], ["2 of 3 runs went as expected"])

        self.assertEqual(
            (report["runs"], report["done"], report["as_expected"], report["interrupted"]),
            (3, 3, 2, None),
        )
        self.assertEqual([row["ok"] for row in report["rows"]], [True, False, True])
        self.assertEqual([row["directory"] for row in report["rows"]], ["1", "2", "3"])
        first, second, _ = report["rows"]
        self.assertEqual(
            {name: held for name, held in first.items() if name != "seconds"},
            {
                "run": 1,
                "directory": "1",
                "ok": True,
                "differences": [],
                "unchecked": None,
                "stopped": None,
                "verdict": "whole",
                "at_tip": 3,
                "seated": 3,
                "seed": None,
                "network_seed": None,
                "wire_seed": None,
                "withheld": None,
            },
        )
        self.assertEqual((second["stopped"], len(second["differences"])), (unread, 1))
        self.assertGreater(second["seconds"], 2)
        for row in report["rows"]:
            kept = json.loads((self.out / row["directory"] / "run.json").read_text("utf-8"))
            self.assertEqual(kept["ok"], row["ok"])

    def test_the_rows_are_written_as_they_were_said_and_as_json(self):
        with mock.patch("os.getloadavg", return_value=(1.5, 1.25, 1.0)):
            report = self.sweep_of(2, {})

        self.assertEqual(report["load"], {"start": [1.5, 1.25, 1.0], "end": [1.5, 1.25, 1.0]})
        self.assertEqual(json.loads((self.out / "sweep.json").read_text("utf-8")), report)
        self.assertEqual((self.out / "sweep.txt").read_text("utf-8").splitlines(), self.said)
        self.assertEqual(self.said[-1], "2 of 2 runs went as expected")

    def test_a_run_whose_check_was_not_made_has_a_row_that_says_so_and_the_sweep_goes_on(self):
        broke = forks_whose("check", "raise RuntimeError('the check broke')", self.scratch)
        report = self.sweep_of(2, {}, forks=broke)

        why = "forks check ended with status 1: RuntimeError: the check broke"
        self.assertEqual(
            [cells(row) for row in self.said[1:3]],
            [(number, "3 of 3", "whole", f"not checked: {why}") for number in ("1", "2")],
        )
        self.assertEqual(self.said[3:], ["0 of 2 runs went as expected"])
        self.assertEqual(
            [(row["ok"], row["differences"], row["unchecked"]) for row in report["rows"]],
            [(False, None, why)] * 2,
        )
        self.assertEqual(json.loads((self.out / "sweep.json").read_text("utf-8")), report)

    def test_the_rows_said_are_written_before_the_next_run_is_made(self):
        whole = whole_run()
        with mock.patch("sim.sweep.run", side_effect=[whole, RuntimeError("the run broke")]):
            with self.assertRaises(RuntimeError):
                self.sweep_of(3, {})

        kept = json.loads((self.out / "sweep.json").read_text("utf-8"))
        self.assertEqual((kept["runs"], kept["done"], kept["as_expected"]), (3, 1, 1))
        self.assertEqual([row["run"] for row in kept["rows"]], [1])
        self.assertEqual(len(self.said), 2)
        self.assertEqual((self.out / "sweep.txt").read_text("utf-8").splitlines(), self.said)

    def test_the_directories_of_ten_runs_sort_in_the_order_of_the_runs(self):
        whole = whole_run()
        with mock.patch("sim.sweep.run", return_value=whole) as run:
            report = self.sweep_of(10, {})

        directories = [call.args[0].out for call in run.call_args_list]
        self.assertEqual(directories, [self.out / f"{number:02d}" for number in range(1, 11)])
        self.assertEqual(report["rows"][9]["directory"], "10")
        self.assertEqual(self.said[-1], "10 of 10 runs went as expected")

    def test_a_sweep_from_a_run_does_the_runs_from_it_with_the_seeds_each_has_from_run_1(self):
        whole = whole_run()
        settings = Settings(Path(), scenario.scenario(said(seed=4, network={"seed": 7})), self.out)
        lines: List[str] = []
        with mock.patch("sim.sweep.run", return_value=whole) as run:
            report = sweep(settings, 3, Interrupts(), lines.append, 9)

        given = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            [one.out for one in given], [self.out / name for name in ("09", "10", "11")]
        )
        self.assertEqual(
            [one.scenario for one in given], [settings.scenario.of_run(k) for k in (9, 10, 11)]
        )
        self.assertEqual([one.scenario.network.seed for one in given], [15, 16, 17])
        self.assertEqual((report["from"], report["runs"], report["done"]), (9, 3, 3))
        self.assertEqual(
            [(row["run"], row["directory"]) for row in report["rows"]],
            [(9, "09"), (10, "10"), (11, "11")],
        )
        self.assertEqual(lines[-1], "3 of 3 runs went as expected, runs 9 to 11")
        self.assertEqual(json.loads((self.out / "sweep.json").read_text("utf-8")), report)
        self.assertEqual((self.out / "sweep.txt").read_text("utf-8").splitlines(), lines)

    def test_a_sweep_of_one_run_from_a_run_names_it_and_one_from_run_1_names_none(self):
        whole = whole_run()
        settings = Settings(Path(), scenario.scenario(said()), self.out)
        for first, counted in (
            (2, "1 of 1 runs went as expected, run 2"),
            (1, "1 of 1 runs went as expected"),
        ):
            with self.subTest(first=first):
                lines: List[str] = []
                out = self.scratch / str(first)
                with mock.patch("sim.sweep.run", return_value=whole) as run:
                    report = sweep(
                        dataclasses.replace(settings, out=out), 1, Interrupts(), lines.append, first
                    )
                self.assertEqual(run.call_args.args[0].out, out / str(first))
                self.assertEqual((report["from"], lines[-1]), (first, counted))

    def test_each_run_has_the_seed_after_that_of_the_run_before_and_its_row_names_it(self):
        report = self.sweep_of(3, {}, network={"seed": 7})

        for number, seed in ((1, 7), (2, 8), (3, 9)):
            run = self.out / str(number)
            started = json.loads((run / "data" / "pax.json").read_text("utf-8"))["arguments"]
            self.assertEqual(given(started, "--network-seed"), [str(seed)])
            kept = json.loads((run / "run.json").read_text("utf-8"))
            self.assertEqual(kept["network"]["seed"], seed)
        self.assertEqual([row["network_seed"] for row in report["rows"]], [7, 8, 9])
        self.assertEqual([row["seed"] for row in report["rows"]], [None] * 3)
        self.assertEqual([row["withheld"] for row in report["rows"]], [None] * 3)
        self.assertEqual(
            [cells(row)[3] for row in self.said[1:4]],
            [f"as expected; network seed {seed}" for seed in (7, 8, 9)],
        )

    def test_a_scenario_on_a_preset_is_swept_as_any_other(self):
        report = self.sweep_of(2, {}, preset="logos.test")

        self.assertEqual((report["done"], report["as_expected"]), (2, 2))
        for number in (1, 2):
            run = self.out / str(number)
            started = json.loads((run / "data" / "pax.json").read_text("utf-8"))["arguments"]
            self.assertIn("--preset=logos.test", started)
            kept = json.loads((run / "run.json").read_text("utf-8"))
            self.assertEqual((kept["preset"], kept["cluster_id"]), ("logos.test", None))

    def test_the_row_of_a_run_that_withheld_a_candidate_says_whose_and_whether_applied(self):
        applied = ["lone=pax", "says-applied=pax"]
        report = self.sweep_of(2, {2: applied}, **withholding(expect={"applied": APPLIED}))

        told = "withheld from pax: the candidate of saro"
        split = "split at epoch 2"
        self.assertEqual(
            [cells(row) for row in self.said[1:3]],
            [
                ("1", "4 of 4", "whole", f"as expected; network seed 7; {told}, not applied"),
                ("2", "4 of 4", split, f"as expected; network seed 8; {told}, applied"),
            ],
        )
        self.assertEqual(self.said[3:], ["2 of 2 runs went as expected"])
        for row, applied in zip(report["rows"], (False, True)):
            kept = json.loads((self.out / row["directory"] / "run.json").read_text("utf-8"))
            self.assertEqual(row["withheld"], kept["withheld"])
            self.assertEqual(
                (row["withheld"]["from"], row["withheld"]["minter"], row["withheld"]["applied"]),
                ("pax", "saro", applied),
            )

    def test_a_signal_once_a_run_is_past_interrupting_keeps_its_row_and_starts_no_other(self):
        whole = whole_run()
        interrupts = Interrupts()

        def run(settings: Settings, watch: Watch) -> dict:
            if settings.out.name == "2":
                watch.close()
                interrupts.take(signal.SIGTERM)
            return whole

        settings = Settings(Path(), scenario.scenario(said()), self.out)
        lines: List[str] = []
        with mock.patch("sim.sweep.run", side_effect=run) as ran:
            report = sweep(settings, 3, interrupts, lines.append)

        self.assertEqual(ran.call_count, 2)
        self.assertEqual((report["done"], report["interrupted"]), (2, "SIGTERM"))
        self.assertEqual(len(report["rows"]), 2)
        self.assertEqual(
            lines[-1], "2 of 2 runs went as expected: interrupted by SIGTERM after 2 of 3"
        )


    def test_a_signal_taken_as_the_next_run_is_made_ready_starts_no_run_or_interrupts_it(self):
        whole = whole_run()

        class Late(Interrupts):
            """Interrupts that take a signal just before the watch of the second run is made."""

            def watch(self) -> Watch:
                if ran.call_count == 1:
                    self.take(signal.SIGTERM)
                return super().watch()

        def run(settings: Settings, watch: Watch) -> dict:
            if watch.signal is None:
                return whole
            # A run whose watch holds a signal is interrupted at its first wait.
            return dict(whole, ok=False, stopped="interrupted by SIGTERM", check=None)

        settings = Settings(Path(), scenario.scenario(said()), self.out)
        lines: List[str] = []
        with mock.patch("sim.sweep.run", side_effect=run) as ran:
            report = sweep(settings, 3, Late(), lines.append)

        self.assertEqual((report["done"], report["interrupted"]), (1, "SIGTERM"))
        self.assertEqual([row["run"] for row in report["rows"]], [1])
        self.assertEqual(
            lines[-1], "1 of 1 runs went as expected: interrupted by SIGTERM after 1 of 3"
        )

    def test_the_first_of_two_signals_is_the_one_a_sweep_ends_by(self):
        interrupts = Interrupts()

        def run(settings: Settings, watch: Watch) -> dict:
            interrupts.take(signal.SIGTERM)
            interrupts.take(signal.SIGINT)
            return dict(whole_run(), ok=False, stopped="interrupted by SIGTERM", check=None)

        settings = Settings(Path(), scenario.scenario(said()), self.out)
        lines: List[str] = []
        with mock.patch("sim.sweep.run", side_effect=run):
            report = sweep(settings, 3, interrupts, lines.append)

        self.assertEqual((report["done"], report["interrupted"]), (0, "SIGTERM"))
        self.assertEqual(
            lines[-1], "0 of 0 runs went as expected: interrupted by SIGTERM after 0 of 3"
        )

    def test_a_sweep_from_a_run_that_a_signal_interrupts_names_the_runs_asked_for(self):
        interrupts = Interrupts()

        def run(settings: Settings, watch: Watch) -> dict:
            if settings.out.name == "12":
                watch.close()
                interrupts.take(signal.SIGTERM)
            return whole_run()

        settings = Settings(Path(), scenario.scenario(said()), self.out)
        lines: List[str] = []
        with mock.patch("sim.sweep.run", side_effect=run):
            report = sweep(settings, 3, interrupts, lines.append, 11)

        self.assertEqual([row["run"] for row in report["rows"]], [11, 12])
        self.assertEqual(
            lines[-1],
            "2 of 2 runs went as expected, runs 11 to 13: interrupted by SIGTERM after 2 of 3",
        )


class CommandLine(Scratch):
    def sim(self, *arguments: str, faults: Dict[int, Sequence[str]]) -> Tuple[int, str]:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            chat_cli = stand_ins(self.scratch, faults)
            given = ["--chat-cli", str(chat_cli), "--out", str(self.out)]
            status = main(["sweep", *arguments, *given])
        return status, printed.getvalue()

    def test_a_sweep_every_run_of_which_went_as_expected_is_status_0(self):
        status, printed = self.sim(str(self.scenario()), "--runs", "2", faults={})

        self.assertEqual(status, 0)
        self.assertEqual(printed, (self.out / "sweep.txt").read_text("utf-8"))
        self.assertTrue(printed.endswith("\n2 of 2 runs went as expected\n"))

    def test_a_sweep_from_a_run_does_that_run_with_its_seeds(self):
        seeded = str(self.scenario(network={"seed": 7}))
        status, printed = self.sim(seeded, "--runs", "1", "--from", "3", faults={})

        self.assertEqual(status, 0)
        self.assertTrue(printed.endswith("\n1 of 1 runs went as expected, run 3\n"))
        kept = json.loads((self.out / "3" / "run.json").read_text("utf-8"))
        self.assertEqual(kept["network"]["seed"], 9)

    def test_a_sweep_from_run_0_or_whose_last_run_would_pass_the_most_seed_is_refused(self):
        for arguments, said in (
            (
                (str(self.scenario()), "--runs", "1", "--from", "0"),
                "--from: a sweep's first run is run 1",
            ),
            (
                (str(self.scenario(seed=2**64 - 3)), "--runs", "2", "--from", "3"),
                f"--runs: run 4 would have the seed {2**64}, and {2**64 - 1} is the most",
            ),
        ):
            with self.subTest(said=said):
                complained = io.StringIO()
                with self.assertRaises(SystemExit) as refused:
                    with contextlib.redirect_stderr(complained):
                        self.sim(*arguments, faults={})
                self.assertEqual(refused.exception.code, 2)
                self.assertEqual(complained.getvalue().splitlines()[-1], f"sim: error: {said}")
                self.assertFalse(self.out.exists())

    def test_a_sweep_one_run_of_which_did_not_is_status_1(self):
        faults = {2: ["deaf=raya"]}
        status, printed = self.sim(str(self.scenario(budget=2)), "--runs", "2", faults=faults)

        self.assertEqual(status, 1)
        self.assertTrue(printed.endswith("\n1 of 2 runs went as expected\n"))


class Signals(Driven):
    """A sweep sent a signal while a batch of its second run waits on a member."""

    def test_an_interrupt_stops_the_run_in_hand_and_keeps_the_rows_of_the_runs_before(self):
        driver = self.start(
            "sweep",
            str(self.scenario(budget=120)),
            "--runs",
            "3",
            chat_cli=stand_ins(self.scratch, {2: ["mute=raya"]}),
        )
        self.wait_for(self.out / "2" / "saro.log", "Publish WelcomeMessage")
        driver.send_signal(signal.SIGINT)
        printed, errors = driver.communicate(timeout=60)

        self.assertEqual(driver.returncode, 128 + signal.SIGINT)
        self.assertEqual(errors, "")
        head, row, counted = printed.splitlines()
        self.assertEqual(cells(row), ("1", "3 of 3", "whole", "as expected"))
        self.assertEqual(
            counted, "1 of 1 runs went as expected: interrupted by SIGINT after 1 of 3"
        )
        report = json.loads((self.out / "sweep.json").read_text("utf-8"))
        self.assertEqual(
            (report["runs"], report["done"], report["as_expected"], report["interrupted"]),
            (3, 1, 1, "SIGINT"),
        )
        self.assertEqual([row["run"] for row in report["rows"]], [1])
        self.assertEqual(printed, (self.out / "sweep.txt").read_text("utf-8"))
        # The run in hand ended its processes and wrote what it had, and no run followed it.
        self.assertEqual(self.left(self.out / "2"), [])
        in_hand = json.loads((self.out / "2" / "run.json").read_text("utf-8"))
        self.assertEqual(in_hand["stopped"], "interrupted by SIGINT")
        self.assertFalse((self.out / "3").exists())
