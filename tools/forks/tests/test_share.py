import contextlib
import fcntl
import http.client
import io
import itertools
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Iterator, List, NamedTuple, Optional, Tuple
from unittest import mock

from forks import share
from forks.cli import main
from forks.share import address_of

from . import FIXTURES, run_report
from .test_serve import TOKEN, Collecting, Served, logged, member, positions

TOOL = Path(__file__).resolve().parents[1]
HISTORIES = FIXTURES / "histories"
MIB = 1024 * 1024
KEPT = ".forks-share.json"
USAGE = "usage: forks share [-h] --to ADDRESS PATH [PATH ...]\n"
NOT_A_TOKEN = "a token that is not 16 to 128 characters of A-Z, a-z, 0-9, _ and -"


def setUpModule() -> None:
    # argparse breaks its usage line at the terminal's width.
    width = mock.patch.dict(os.environ, {"COLUMNS": "80"})
    width.start()
    unittest.addModuleCleanup(width.stop)


class Shared(NamedTuple):
    """What a go of `forks share` did: its exit status, what it printed, what it complained of."""

    status: int
    out: str
    err: str


def go(address: str, *paths: Path) -> Shared:
    """Runs `forks share --to ADDRESS PATH...` the way the command line does."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = main(["share", "--to", address, *map(str, paths)])
        except SystemExit as stopped:
            status = stopped.code
    return Shared(status, out.getvalue(), err.getvalue())


def as_reported(report: dict) -> dict:
    """A collector's report as `forks report` writes it: each run under the name it was sent by."""
    said = json.dumps(report)
    for run in report["runs"]:
        said = said.replace(json.dumps(run["id"]), json.dumps(run["name"]))
    renamed = json.loads(said)
    del renamed["lines"]
    renamed["runs"] = [
        {"name": run["name"], "final": run["final"], "delivery": run["delivery"]}
        for run in renamed["runs"]
    ]
    return renamed


def took(served: Served) -> List[int]:
    """The bytes a collector appended at each request it accepted, in the order they came."""
    found = (re.search(r" took (\d+) bytes", line) for line in served.written)
    return [int(line[1]) for line in found if line]


def patterned(size: int, seed: int = 0) -> bytes:
    """Bytes no two stretches of which are alike, so that one out of its place is seen."""
    return b"".join(b"%07d\n" % number for number in range(seed, seed + size // 8 + 1))[:size]


class Killed(BaseException):
    """The sender stopped where it stood, as a process that is killed does."""


def kill() -> None:
    raise Killed


def at_request(number: int, happen: Callable[[], None], answered: bool = False):
    """Has something happen at one request of a go: before it is sent, or once it is answered.

    `seen` of what it yields lists every request the go made, as its method and its body.
    """
    call = share._Calls._call
    seen: List[Tuple[str, bytes]] = []

    def watched(calls, method: str, path: str, body: bytes, headers: dict):
        seen.append((method, body))
        mine = len(seen) == number
        if mine and not answered:
            happen()
        answer = call(calls, method, path, body, headers)
        if mine and answered:
            happen()
        return answer

    watched.seen = seen
    return mock.patch.object(share._Calls, "_call", watched)


SILENT = "never answered"


class Asked(NamedTuple):
    """A request a stand-in was sent."""

    method: str
    path: str
    headers: Dict[str, str]
    body: bytes


class StandIn:
    """Answers in a collector's place as a script says, and notes each request it was sent.

    The script answers a request with a status and a body, an object for JSON, and headers if
    it likes; with bytes, which are sent as they are; with a function, which is given what to
    write to and what is set when the test is over; or with `SILENT`, and the request is then
    never answered.
    """

    def __init__(self, script: Callable[[Asked], object]) -> None:
        self.asked: List[Asked] = []
        self._over = threading.Event()
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def __getattr__(self, name: str):
                if name.startswith("do_"):
                    return self.answer
                raise AttributeError(name)

            def answer(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                asked = Asked(self.command, self.path, dict(self.headers.items()), body)
                stand_in.asked.append(asked)
                said = script(asked)
                if said is SILENT:
                    stand_in._over.wait()
                elif isinstance(said, bytes):
                    self.wfile.write(said)
                elif callable(said):
                    said(self.wfile, stand_in._over)
                else:
                    status, body, *headers = said
                    sent = body if isinstance(body, bytes) else json.dumps(body).encode()
                    self.send_response_only(status)
                    for header in headers:
                        for key, value in header.items():
                            self.send_header(key, value)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(sent)))
                    self.end_headers()
                    self.wfile.write(sent)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A sender that has given a request up fails no test by going.
        self.server.handle_error = lambda request, client_address: None
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, args=(0.01,))
        self._thread.start()

    def stop(self) -> None:
        self._over.set()
        self.server.shutdown()
        self.server.server_close()
        self._thread.join()


class Plays:
    """A script that plays a collector: runs are `s1`, `s2` and on, bytes go at the size held.

    Where `instead` answers a request, that is the answer.
    """

    def __init__(self, instead: Callable[[Asked], object] = lambda asked: None) -> None:
        self.instead = instead
        self.held: Dict[str, bytes] = {}

    def __call__(self, asked: Asked) -> object:
        said = self.instead(asked)
        if said is not None:
            return said
        if asked.method == "POST":
            run = f"s{len(self.held) + 1}"
            self.held[run] = b""
            return 201, {"id": run, "receipt": f"receipt-of-{run}"}
        run, offset = re.fullmatch(r".*/runs/([\w-]+)/log\?offset=(\d+)", asked.path).groups()
        if run not in self.held:
            return 404, {"error": "the collector has no log of this run"}
        if int(offset) == len(self.held[run]):
            self.held[run] += asked.body
            return 200, {"size": len(self.held[run]), "final": False}
        return 409, {"size": len(self.held[run]), "final": False}


def to_puts(answer: object) -> Callable[[Asked], object]:
    """A script's answer to every `PUT`, and to nothing else."""
    return lambda asked: answer if asked.method == "PUT" else None


def to_posts(answer: object) -> Callable[[Asked], object]:
    return lambda asked: answer if asked.method == "POST" else None


def in_turn(*answers: object) -> Callable[[Asked], object]:
    """A script's answers to the requests as they come, one each; nothing stands for its own."""
    left = iter(answers)
    return lambda asked: next(left, None)


def watching():
    """Notes every request of a go, and changes none."""
    return at_request(0, kill)


def on_replace(instead: Callable[[Path, Path], None]):
    """Has a remembered file replaced another way; any other file is replaced as it ever is."""
    replace = os.replace

    def replaced(scratch, path) -> None:
        if Path(path).name == KEPT:
            instead(Path(scratch), Path(path))
        else:
            replace(scratch, path)

    return mock.patch("os.replace", replaced)


class Sharing(Collecting):
    """A test that sends the logs of a directory of its own to a collector."""

    def setUp(self) -> None:
        self.collectors: List[Served] = []
        super().setUp()
        self.logs = self.scratch.resolve() / "logs"
        self.logs.mkdir()
        self.host = f"127.0.0.1:{self.served.port}"
        # Whatever a test had fail, the token is in nothing that was written.
        self.addCleanup(self.nothing_holds_the_token)

    def numbers(self) -> Iterator[int]:
        # Three digits each, so that the ids come in the order the runs were opened in.
        return itertools.count(101)

    def serve(self, data: Optional[Path] = None) -> Served:
        served = super().serve(data)
        self.collectors.append(served)
        return served

    def stand_in(self, script: Callable[[Asked], object]) -> StandIn:
        stand_in = StandIn(script)
        self.addCleanup(stand_in.stop)
        return stand_in

    def nothing_holds_the_token(self) -> None:
        for path in self.scratch.rglob("*"):
            if path.is_file():
                self.assertNotIn(TOKEN.encode(), path.read_bytes(), path)
        for served in self.collectors:
            self.assertNotIn(TOKEN, "\n".join(served.written))

    def address(self, collector=None, token: str = TOKEN, under: str = "/") -> str:
        port = (collector or self.served).port
        return f"http://127.0.0.1:{port}{under}?token={token}"

    def write(self, name: str, log: bytes, directory: Optional[Path] = None) -> Path:
        path = (directory or self.logs) / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(log)
        return path

    def share(self, *paths: Path, to: Optional[str] = None) -> Shared:
        """Sends the paths, or the directory of logs. A go never prints the token."""
        shared = go(to or self.address(), *(paths or (self.logs,)))
        self.assertNotIn(TOKEN, shared.out + shared.err)
        return shared

    def line(self, name: str, sent: int, held: int, size: Optional[int] = None, at=None) -> str:
        """The line a go prints of a run, which is whole on the collector unless a size is given."""
        host = f"127.0.0.1:{at.port}" if at else self.host
        size = held if size is None else size
        return f"{name}: sent {sent} bytes, {host} holds {held} of {size}\n"

    def kept(self, directory: Optional[Path] = None) -> dict:
        return json.loads(((directory or self.logs) / KEPT).read_text())

    def entry(self, name: str) -> Tuple[str, str]:
        """The id and the receipt of the run a log was sent as."""
        (runs,) = self.kept().values()
        return runs[name]["id"], runs[name]["receipt"]

    def held(self, served: Optional[Served] = None) -> List[Tuple[str, bytes]]:
        """Every run a collector holds: the name it goes by and its log."""
        served = served or self.served
        runs = [path.name for path in served.data.iterdir()]
        return sorted((served.run_json(run)["name"], served.held(run)) for run in runs)


class Directory(Sharing):
    def test_a_directory_of_two_members_logs_is_sent_and_both_are_live(self):
        saro, raya = logged(member("saro")), logged(member("raya"))
        self.write("saro.log", saro)
        self.write("raya.log", raya)
        shared = self.share()
        out = self.line("raya", len(raya), len(raya)) + self.line("saro", len(saro), len(saro))
        self.assertEqual(shared, Shared(0, out, ""))
        self.assertEqual(self.held(), [("raya", raya), ("saro", saro)])
        report = self.served.report()
        self.assertEqual(report["verdict"], "whole")
        self.assertEqual(positions(report, ["saro", "raya"]), dict(saro="tip", raya="tip"))
        self.assertEqual(
            [(run["name"], run["app"], run["final"], run["size"]) for run in report["runs"]],
            [("raya", "forks share", False, len(raya)), ("saro", "forks share", False, len(saro))],
        )
        written = run_report(self.logs)
        self.assertEqual((written.status, written.conversation["verdict"]), (0, "whole"))
        self.assertEqual(as_reported(report), written.conversation)

    def test_every_directory_of_logs_reads_on_the_collector_as_report_reads_it(self):
        for fixture in [*sorted(HISTORIES.iterdir()), FIXTURES / "real"]:
            with self.subTest(logs=fixture.name):
                served = self.elsewhere()
                logs = self.scratch / fixture.name
                shutil.copytree(fixture, logs)
                shared = self.share(logs, to=self.address(served))
                self.assertEqual((shared.status, shared.err), (0, ""))
                written = run_report(logs)
                listed = served.listed()
                self.assertEqual(listed["lines"], written.lines)
                self.assertEqual(len(listed["conversations"]), len(written.conversations))
                for conversation in written.conversations:
                    self.assertEqual(as_reported(served.report(conversation["id"])), conversation)

    def test_no_run_is_marked_final_however_often_it_is_sent(self):
        self.write("saro.log", logged(member("saro")))
        self.write("raya.log", logged(member("raya")))
        with watching() as watched:
            for _ in range(3):
                self.assertEqual(self.share().status, 0)
        self.assertEqual(len(watched.seen), 2 + 2 + 2 * 2)
        report = self.served.report()
        self.assertEqual([run["final"] for run in report["runs"]], [False, False])
        self.assertEqual(positions(report, ["saro", "raya"]), dict(saro="tip", raya="tip"))
        self.assertFalse(any("final" in line for line in self.served.written))

    def test_a_member_that_stopped_stays_live_and_is_ended_only_by_a_mark_share_never_sends(self):
        self.write("saro.log", logged(member("saro", epochs=4)))
        self.write("raya.log", logged(member("raya", epochs=2)))
        self.assertEqual(self.share().status, 0)
        report = self.served.report()
        self.assertEqual(report["verdict"], "whole, members behind")
        self.assertEqual(positions(report, ["saro", "raya"]), dict(saro="tip", raya="behind"))
        self.assertEqual(self.served.put(*self.entry("raya"), final=True).status, 200)
        report = self.served.report()
        self.assertEqual(report["verdict"], "whole")
        self.assertEqual(positions(report, ["saro", "raya"]), dict(saro="tip", raya="ended"))

    def test_a_second_go_sends_no_byte_and_the_collector_hears_from_every_run(self):
        logs = {"saro": logged(member("saro")), "raya": logged(member("raya"))}
        for name, log in logs.items():
            self.write(f"{name}.log", log)
        self.share()
        kept, written = self.kept(), len(self.served.written)
        self.clock.pass_days(29)
        shared = self.share()
        out = "".join(self.line(name, 0, len(logs[name])) for name in sorted(logs))
        self.assertEqual(shared, Shared(0, out, ""))
        self.assertEqual(took(self.served)[-2:], [0, 0])
        self.assertEqual(len(self.served.written), written + 2)
        self.assertEqual(self.kept(), kept)
        # Heard from on day 29, the runs are there on day 31.
        self.clock.pass_days(2)
        self.assertEqual(self.held(), sorted(logs.items()))

    def test_of_a_log_that_grew_only_the_new_bytes_are_sent(self):
        log = logged(member("saro", epochs=4))
        cut = len(logged(member("saro", epochs=2)))
        path = self.write("saro.log", log[:cut])
        self.assertEqual(self.share().out, self.line("saro", cut, cut))
        path.write_bytes(log)
        with watching() as watched:
            shared = self.share()
        self.assertEqual(shared, Shared(0, self.line("saro", len(log) - cut, len(log)), ""))
        self.assertEqual(took(self.served), [cut, len(log) - cut])
        self.assertEqual(watched.seen, [("PUT", b""), ("PUT", log[cut:])])
        self.assertEqual(self.held(), [("saro", log)])

    def test_a_log_over_1_mib_goes_in_several_requests_and_none_holds_more(self):
        self.assertEqual(share.PIECE, MIB)
        log = patterned(2 * MIB + 5)
        self.write("saro.log", log)
        with watching() as watched:
            shared = self.share()
        self.assertEqual(shared, Shared(0, self.line("saro", len(log), len(log)), ""))
        self.assertEqual(took(self.served), [MIB, MIB, 5])
        sent = [len(body) for method, body in watched.seen if method == "PUT"]
        self.assertEqual(sent, [MIB, MIB, 5])
        self.assertEqual(self.held(), [("saro", log)])

    def test_a_log_of_just_1_mib_and_one_of_no_byte_take_a_request_each(self):
        self.write("saro.log", patterned(MIB))
        self.write("raya.log", b"")
        out = self.line("raya", 0, 0) + self.line("saro", MIB, MIB)
        self.assertEqual(self.share(), Shared(0, out, ""))
        self.assertEqual(took(self.served), [0, MIB])

    def test_rotated_parts_arrive_as_one_stream_and_a_part_added_later_goes_on_the_next_go(self):
        parts = [patterned(300, 100), patterned(450, 200), patterned(120, 300)]
        self.write("chat_module_20261008_133105.001.log", parts[0])
        self.write("chat_module_20261008_133105.002.log", parts[1])
        written = self.write("chat_module_20261008_133105.log", parts[2])
        name, size = "chat_module_20261008_133105", 870
        self.assertEqual(self.share(), Shared(0, self.line(name, size, size), ""))
        self.assertEqual(self.held(), [(name, b"".join(parts))])
        self.assertEqual(took(self.served), [size])
        # Its writer fills the file, moves it aside as the next part and starts the name anew.
        grown, new = parts[2] + patterned(80, 400), patterned(200, 500)
        written.write_bytes(grown)
        written.rename(self.logs / "chat_module_20261008_133105.003.log")
        written.write_bytes(new)
        self.assertEqual(self.share(), Shared(0, self.line(name, 280, size + 280), ""))
        self.assertEqual(self.held(), [(name, b"".join(parts[:2]) + grown + new)])
        self.assertEqual(took(self.served), [size, 280])
        self.assertEqual(list(self.kept()[f"http://{self.host}/"]), [name])

    def test_a_request_is_cut_across_the_parts_wherever_it_ends(self):
        parts = [patterned(7, 10), b"", patterned(1, 20), patterned(12, 30), b"", patterned(5, 40)]
        for number, part in enumerate(parts[:-1], 1):
            self.write(f"saro.{number:03d}.log", part)
        self.write("saro.log", parts[-1])
        whole = b"".join(parts)
        for piece in range(1, len(whole) + 2):
            with self.subTest(piece=piece):
                served = self.elsewhere()
                with mock.patch.object(share, "PIECE", piece):
                    shared = self.share(to=self.address(served))
                self.assertEqual(shared.status, 0)
                self.assertEqual(self.held(served), [("saro", whole)])
                pieces, rest = divmod(len(whole), piece)
                self.assertEqual(took(served), [piece] * pieces + [rest] * bool(rest))

    def test_a_run_goes_by_the_name_of_its_log_cut_to_64_characters(self):
        long = "a-log-" + "x" * 58 + "-and-more"
        self.write(f"{long}.log", b"one\n")
        self.write("  kes of the west .log", b"two\n")
        shared = self.share()
        out = self.line("  kes of the west ", 4, 4) + self.line(long, 4, 4)
        self.assertEqual(shared, Shared(0, out, ""))
        self.assertEqual(self.held(), [(long[:64], b"one\n"), ("kes of the west", b"two\n")])
        self.assertEqual(sorted(self.kept()[f"http://{self.host}/"]), ["  kes of the west ", long])
        # The run is remembered by the whole name, and found by it again.
        self.assertEqual(self.share().status, 0)
        self.assertEqual(len(self.held()), 2)

    def test_a_name_is_trimmed_and_then_cut_by_its_sender(self):
        self.write("  kes of the west .log", b"one\n")
        self.write(f" {'x' * 70}.log", b"two\n")
        stand_in = self.stand_in(Plays())
        self.assertEqual(self.share(to=self.address(stand_in)).status, 0)
        opened = [json.loads(asked.body) for asked in stand_in.asked if asked.method == "POST"]
        self.assertEqual([said["name"] for said in opened], ["kes of the west", "x" * 64])

    def test_logs_given_one_by_one_are_sent_like_their_directory(self):
        saro = self.write("saro.log", b"one\n")
        part = self.write("saro.001.log", b"zero\n")
        other = self.write("raya.log", b"two\n", self.scratch / "elsewhere")
        self.write("pax.log", b"three\n")
        shared = self.share(saro, part, other)
        out = self.line("raya", 4, 4) + self.line("saro", 9, 9)
        self.assertEqual(shared, Shared(0, out, ""))
        self.assertEqual(self.held(), [("raya", b"two\n"), ("saro", b"zero\none\n")])
        base = f"http://{self.host}/"
        self.assertEqual(list(self.kept()[base]), ["saro"])
        self.assertEqual(list(self.kept(self.scratch / "elsewhere")[base]), ["raya"])

    def test_a_log_named_alone_is_refused_once_its_writer_has_moved_a_part_aside(self):
        written = self.write("chat.log", b"aaaa\n")
        self.assertEqual(self.share(written), Shared(0, self.line("chat", 5, 5), ""))
        part = self.logs / "chat.001.log"
        written.rename(part)
        written.write_bytes(b"bbbb\n")
        kept = self.kept()
        err = (
            f"forks: {part} is a part of the run chat and not among the paths: "
            "a run is sent whole\n"
        )
        self.assertEqual(self.share(written), Shared(2, "", err))
        self.assertEqual((self.held(), self.kept()), ([("chat", b"aaaa\n")], kept))
        # Named whole, the run goes on from what the collector holds of it.
        self.assertEqual(self.share(written, part), Shared(0, self.line("chat", 5, 10), ""))
        self.assertEqual(self.share(), Shared(0, self.line("chat", 0, 10), ""))
        self.assertEqual(self.held(), [("chat", b"aaaa\nbbbb\n")])


class Killing(Sharing):
    """A sender that stops between two requests, or before it hears the answer to one."""

    def setUp(self) -> None:
        super().setUp()
        self.enter(mock.patch.object(share, "PIECE", 1000))
        self.whole = {
            "pax": b"",
            "raya": patterned(1200, 100) + patterned(900, 300),
            "saro": patterned(2500, 500),
        }
        self.write("pax.log", b"")
        self.write("raya.001.log", self.whole["raya"][:1200])
        self.write("raya.log", self.whole["raya"][1200:])
        self.write("saro.log", self.whole["saro"])

    def stopped_and_run_again(self, number: int, answered: bool) -> Tuple[Served, List[tuple]]:
        """Stops a go at one of its requests and runs another: the collector, and its orphans."""
        served = self.elsewhere()
        logs = self.scratch / f"logs-{number}-{answered}"
        shutil.copytree(self.logs, logs)
        with at_request(number, kill, answered) as watched, self.assertRaises(Killed):
            self.share(logs, to=self.address(served))
        method, body = watched.seen[-1]
        again = self.share(logs, to=self.address(served))
        self.assertEqual((again.status, again.err), (0, ""))
        for name, run in self.kept(logs)[f"http://127.0.0.1:{served.port}/"].items():
            self.assertEqual(served.held(run["id"]), self.whole[name])
        # A run whose opening was answered and not heard is on the collector, and stays empty.
        unheard = answered and method == "POST"
        return served, [(json.loads(body)["name"], b"")] if unheard else []

    def test_a_go_makes_a_request_to_open_each_run_and_one_for_each_piece(self):
        with watching() as watched:
            self.assertEqual(self.share().status, 0)
        self.assertEqual(
            [(method, len(body)) for method, body in watched.seen if method == "PUT"],
            [("PUT", 0), ("PUT", 1000), ("PUT", 1000), ("PUT", 100)]
            + [("PUT", 1000), ("PUT", 1000), ("PUT", 500)],
        )
        self.assertEqual([method for method, _ in watched.seen].count("POST"), 3)
        self.assertEqual(len(watched.seen), 10)

    def test_stopped_before_any_request_and_run_again_each_log_is_held_once_and_whole(self):
        for number in range(1, 11):
            with self.subTest(request=number):
                served, orphans = self.stopped_and_run_again(number, answered=False)
                self.assertEqual(orphans, [])
                self.assertEqual(self.held(served), sorted(self.whole.items()))

    def test_stopped_before_an_answer_was_heard_and_run_again_each_log_is_held_once_and_whole(self):
        opened = 0
        for number in range(1, 11):
            with self.subTest(request=number):
                served, orphans = self.stopped_and_run_again(number, answered=True)
                opened += len(orphans)
                self.assertEqual(self.held(served), sorted([*self.whole.items(), *orphans]))
        self.assertEqual(opened, 3)

    def test_stopped_at_every_request_in_turn_the_goes_end_with_each_log_held_whole(self):
        # Each go is stopped one request later than the one before, until one is not stopped.
        done = None
        for number in range(1, 11):
            with at_request(number, kill), contextlib.suppress(Killed):
                done = self.share().status
        self.assertEqual(done, 0)
        self.assertEqual(self.held(), sorted(self.whole.items()))
        with watching() as watched:
            self.assertEqual(self.share().status, 0)
        self.assertEqual(watched.seen, [("PUT", b"")] * 3)


class Answers(Sharing):
    """What a collector answers, and what the sender then does and says."""

    def setUp(self) -> None:
        super().setUp()
        self.raya, self.saro = patterned(700, 100), patterned(900, 200)
        self.write("raya.log", self.raya)
        self.write("saro.log", self.saro)

    def test_a_409_goes_on_from_the_size_held(self):
        self.enter(mock.patch.object(share, "PIECE", 400))
        # Stopped after the first piece of the first log was sent.
        with at_request(3, kill), self.assertRaises(Killed):
            self.share()
        self.assertEqual(self.held(), [("raya", self.raya[:400])])
        with watching() as watched:
            shared = self.share()
        out = self.line("raya", 300, 700) + self.line("saro", 900, 900)
        self.assertEqual(shared, Shared(0, out, ""))
        self.assertEqual(watched.seen[:2], [("PUT", b""), ("PUT", self.raya[400:])])
        self.assertEqual(self.held(), [("raya", self.raya), ("saro", self.saro)])

    def test_a_run_remembered_and_empty_on_the_collector_is_sent_from_its_start(self):
        with at_request(2, kill), self.assertRaises(Killed):
            self.share()
        self.assertEqual(self.held(), [("raya", b"")])
        out = self.line("raya", 700, 700) + self.line("saro", 900, 900)
        self.assertEqual(self.share(), Shared(0, out, ""))
        self.assertEqual(self.held(), [("raya", self.raya), ("saro", self.saro)])

    def test_a_collector_that_holds_more_than_the_disk_stops_that_run_and_exits_1(self):
        self.share()
        kept = self.kept()
        (self.logs / "raya.log").write_bytes(self.raya[:300])
        written = list(self.served.written)
        shared = self.share()
        out = self.line("raya", 0, 700, 300) + self.line("saro", 0, 900)
        err = f"forks: raya: {self.host} holds 700 bytes and the disk has 300\n"
        self.assertEqual(shared, Shared(1, out, err))
        self.assertEqual(self.held(), [("raya", self.raya), ("saro", self.saro)])
        # The collector was asked, and sent nothing: no write of raya's, and one of saro's.
        self.assertEqual(len(self.served.written), len(written) + 1)
        # The run stays remembered: the next go asks of the same one, and opens no other.
        self.assertEqual(self.kept(), kept)
        self.assertEqual(self.share(), Shared(1, out, err))
        self.assertEqual(self.held(), [("raya", self.raya), ("saro", self.saro)])

    def test_a_run_that_takes_no_bytes_at_the_size_it_says_it_holds_is_stopped(self):
        self.share()
        run, receipt = self.entry("raya")
        self.assertEqual(self.served.put(run, receipt, final=True).status, 200)
        with open(self.logs / "raya.log", "ab") as file:
            file.write(b"more\n")
        shared = self.share()
        out = self.line("raya", 0, 700, 705) + self.line("saro", 0, 900)
        err = f"forks: raya: {self.host} took no bytes at the size it said it holds\n"
        self.assertEqual(shared, Shared(1, out, err))
        self.assertEqual(self.held(), [("raya", self.raya), ("saro", self.saro)])

    def test_a_run_that_grows_by_another_hand_is_gone_on_with_once(self):
        self.enter(mock.patch.object(share, "PIECE", 300))
        added = []

        def another_hand() -> None:
            run, receipt = self.entry("raya")
            held, disk = len(self.served.held(run)), (self.logs / "raya.log").read_bytes()
            added.append(self.served.put(run, receipt, disk[held : held + 50]).status)

        with at_request(3, another_hand):
            shared = self.share(self.logs / "raya.log")
        self.assertEqual(shared, Shared(0, self.line("raya", 650, 700), ""))
        self.assertEqual((added, self.held()), ([200], [("raya", self.raya)]))
        added.clear()
        with open(self.logs / "raya.log", "ab") as file:
            file.write(patterned(700, 300))
        # Asked how much it holds, and then refused once more: the run moves by another hand.
        with at_request(2, another_hand):
            shared = self.share(self.logs / "raya.log")
        err = f"forks: raya: {self.host} took no bytes at the size it said it holds\n"
        self.assertEqual(shared, Shared(1, self.line("raya", 0, 750, 1400), err))
        self.assertEqual(added, [200])

    def test_a_second_409_stops_the_run_though_bytes_were_taken_since_the_first(self):
        self.enter(mock.patch.object(share, "PIECE", 300))
        raya = self.logs / "raya.log"
        with at_request(3, kill), self.assertRaises(Killed):
            self.share(raya)
        self.assertEqual(self.held(), [("raya", self.raya[:300])])

        def another_hand() -> None:
            run, receipt = self.entry("raya")
            self.served.put(run, receipt, self.raya[600:650])

        # Asked and told 300 by a 409, a piece taken, and then refused once more.
        with at_request(3, another_hand) as watched:
            shared = self.share(raya)
        err = f"forks: raya: {self.host} took no bytes at the size it said it holds\n"
        self.assertEqual(shared, Shared(1, self.line("raya", 300, 650, 700), err))
        self.assertEqual([len(body) for _, body in watched.seen], [0, 300, 100])

    def test_a_run_another_hand_made_whole_is_whole(self):
        self.enter(mock.patch.object(share, "PIECE", 300))

        def another_hand() -> None:
            run, receipt = self.entry("raya")
            self.served.put(run, receipt, self.raya[300:])

        with at_request(3, another_hand):
            shared = self.share(self.logs / "raya.log")
        self.assertEqual(shared, Shared(0, self.line("raya", 300, 700), ""))
        self.assertEqual(self.held(), [("raya", self.raya)])

    def test_a_404_opens_the_run_again_and_sends_it_whole_in_the_same_go(self):
        self.share()
        gone = self.entry("raya")
        self.assertEqual(self.served.delete(*gone).status, 204)
        kept = self.kept()
        shared = self.share()
        out = self.line("raya", 700, 700) + self.line("saro", 0, 900)
        self.assertEqual(shared, Shared(0, out, ""))
        self.assertEqual(self.held(), [("raya", self.raya), ("saro", self.saro)])
        again = self.entry("raya")
        self.assertNotEqual(again[0], gone[0])
        self.assertEqual(self.served.held(again[0]), self.raya)
        self.assertEqual(self.entry("saro"), tuple(kept[f"http://{self.host}/"]["saro"].values()))

    def test_a_run_that_goes_while_it_is_sent_is_opened_again_and_sent_whole(self):
        self.enter(mock.patch.object(share, "PIECE", 200))
        raya = self.logs / "raya.log"
        with at_request(3, kill), self.assertRaises(Killed):
            self.share(raya)
        gone = self.entry("raya")
        self.assertEqual(self.held(), [("raya", self.raya[:200])])
        # Asked, and a piece sent: the run goes before the next.
        with at_request(3, lambda: self.served.delete(*gone)) as watched:
            shared = self.share(raya)
        self.assertEqual(shared, Shared(0, self.line("raya", 200 + 700, 700), ""))
        self.assertEqual(
            [(method, len(body)) for method, body in watched.seen if method == "PUT"],
            [("PUT", 0), ("PUT", 200), ("PUT", 200)] + [("PUT", 200)] * 3 + [("PUT", 100)],
        )
        self.assertEqual([method for method, _ in watched.seen].index("POST"), 3)
        self.assertEqual(self.held(), [("raya", self.raya)])
        self.assertNotEqual(self.entry("raya")[0], gone[0])

    def test_a_run_that_goes_while_it_is_sent_and_cannot_be_opened_again_has_no_line(self):
        self.enter(mock.patch.object(share, "PIECE", 200))
        plays = Plays()
        stand_in = self.stand_in(plays)
        raya = self.logs / "raya.log"
        with at_request(3, kill), self.assertRaises(Killed):
            self.share(raya, to=self.address(stand_in))
        gone = 404, {"error": "the collector has no log of this run"}
        plays.instead = in_turn(None, None, gone, (503, {"error": "not now"}))
        shared = self.share(to=self.address(stand_in))
        err = f"forks: raya: 127.0.0.1:{stand_in.port} answered 503, runs not tried: 1\n"
        self.assertEqual(shared, Shared(1, "", err))
        self.assertEqual(self.kept(), {})

    def test_a_run_opened_again_is_gone_on_with_once_like_any_other(self):
        plays = Plays()
        stand_in = self.stand_in(plays)
        gone = {"raya": {"id": "old-raya", "receipt": "a-receipt"}}
        (self.logs / KEPT).write_text(json.dumps({f"http://127.0.0.1:{stand_in.port}/": gone}))

        def another_hand(asked: Asked) -> None:
            # The run is asked for and gone, opened again, and holds bytes when the first come.
            if len(stand_in.asked) == 3:
                plays.held["s1"] = self.raya[:100]

        plays.instead = another_hand
        shared = self.share(self.logs / "raya.log", to=self.address(stand_in))
        self.assertEqual(shared, Shared(0, self.line("raya", 600, 700, at=stand_in), ""))
        self.assertEqual(plays.held, {"s1": self.raya})

    def test_a_run_opened_again_is_gone_on_with_once_though_the_one_gone_answered_a_409(self):
        plays = Plays()
        stand_in = self.stand_in(plays)
        plays.held["s1"] = self.raya[:100]
        kept = {"raya": {"id": "s1", "receipt": "receipt-of-s1"}}
        (self.logs / KEPT).write_text(json.dumps({f"http://127.0.0.1:{stand_in.port}/": kept}))

        def another_hand(asked: Asked) -> None:
            # Asked, the run says by a 409 that it holds 100, and is gone when they are sent
            # on. Opened again, it holds bytes when the first come.
            if len(stand_in.asked) == 2:
                del plays.held["s1"]
            if len(stand_in.asked) == 4:
                plays.held["s1"] = self.raya[:200]

        plays.instead = another_hand
        shared = self.share(self.logs / "raya.log", to=self.address(stand_in))
        self.assertEqual(shared, Shared(0, self.line("raya", 500, 700, at=stand_in), ""))
        self.assertEqual(plays.held, {"s1": self.raya})
        self.assertEqual(
            [asked.method for asked in stand_in.asked], ["PUT", "PUT", "POST", "PUT", "PUT"]
        )

    def test_runs_the_collector_let_go_after_thirty_days_are_opened_again(self):
        self.share()
        self.clock.pass_days(31)
        self.assertEqual((self.served.listed()["conversations"], self.held()), ([], []))
        out = self.line("raya", 700, 700) + self.line("saro", 900, 900)
        self.assertEqual(self.share(), Shared(0, out, ""))
        self.assertEqual(self.held(), [("raya", self.raya), ("saro", self.saro)])

    def test_a_404_drops_the_entry_though_the_run_cannot_be_opened_again(self):
        stand_in = self.stand_in(Plays(to_posts((503, {"error": "not now"}))))
        base = f"http://127.0.0.1:{stand_in.port}/"
        kept = {name: {"id": f"old-{name}", "receipt": "a-receipt"} for name in ("raya", "saro")}
        (self.logs / KEPT).write_text(json.dumps({base: kept}))
        shared = self.share(to=self.address(stand_in))
        err = f"forks: raya: 127.0.0.1:{stand_in.port} answered 503, runs not tried: 1\n"
        self.assertEqual(shared, Shared(1, "", err))
        self.assertEqual([asked.method for asked in stand_in.asked], ["PUT", "POST"])
        self.assertEqual(self.kept(), {base: {"saro": kept["saro"]}})

    def test_a_wrong_receipt_is_said_and_the_next_run_goes_on(self):
        self.share()
        kept = self.kept()
        kept[f"http://{self.host}/"]["raya"]["receipt"] = "a-receipt-of-another-run"
        (self.logs / KEPT).write_text(json.dumps(kept))
        with open(self.logs / "saro.log", "ab") as file:
            file.write(b"more\n")
        shared = self.share()
        err = f"forks: raya: {self.host} refused the run's receipt\n"
        self.assertEqual(shared, Shared(1, self.line("saro", 5, 905), err))
        self.assertEqual(self.held(), [("raya", self.raya), ("saro", self.saro + b"more\n")])
        self.assertEqual(self.kept(), kept)

    def test_a_wrong_token_is_said_and_no_other_run_is_tried(self):
        wrong = "another-token_for-the-tests"
        shared = self.share(to=self.address(token=wrong))
        err = f"forks: raya: {self.host} refused the token, runs not tried: 1\n"
        self.assertEqual(shared, Shared(1, "", err))
        self.assertNotIn(wrong, (self.logs / KEPT).read_text())
        self.assertEqual(self.held(), [])

    def test_a_token_that_is_refused_later_stops_the_go_there(self):
        refused = 401, {"error": "this call needs the collector's token"}
        stand_in = self.stand_in(Plays(in_turn(None, None, refused)))
        shared = self.share(to=self.address(stand_in))
        host = f"127.0.0.1:{stand_in.port}"
        err = f"forks: saro: {host} refused the token\n"
        self.assertEqual(shared, Shared(1, self.line("raya", 700, 700, at=stand_in), err))
        self.assertEqual(len(stand_in.asked), 3)

    def test_a_request_longer_than_something_takes_is_said_and_the_next_run_goes_on(self):
        long = lambda asked: (413, {"error": "too long"}) if len(asked.body) > 800 else None
        stand_in = self.stand_in(Plays(long))
        self.write("pax.log", patterned(1000, 300))
        shared = self.share(to=self.address(stand_in))
        host = f"127.0.0.1:{stand_in.port}"
        out = self.line("raya", 700, 700, at=stand_in)
        err = "".join(
            f"forks: {name}: {host} takes no request of {size} bytes\n"
            for name, size in (("pax", 1000), ("saro", 900))
        )
        self.assertEqual(shared, Shared(1, out, err))
        self.assertEqual(len(stand_in.asked), 6)
        # Each run stays remembered: the next go asks of the same three, and opens none.
        kept = self.kept()
        self.assertEqual(sorted(kept[f"http://{host}/"]), ["pax", "raya", "saro"])
        shared = self.share(to=self.address(stand_in))
        out = (
            self.line("pax", 0, 0, 1000, at=stand_in)
            + self.line("raya", 0, 700, at=stand_in)
            + self.line("saro", 0, 0, 900, at=stand_in)
        )
        self.assertEqual(shared, Shared(1, out, err))
        self.assertEqual(self.kept(), kept)
        self.assertEqual([asked.method for asked in stand_in.asked].count("POST"), 3)


class Full(Sharing):
    max_bytes = 1500

    def test_a_collector_at_its_cap_is_said_and_no_other_run_is_tried(self):
        self.enter(mock.patch.object(share, "PIECE", 1000))
        for name in ("pax", "raya", "saro"):
            self.write(f"{name}.log", patterned(2000))
        shared = self.share()
        out = self.line("pax", 1000, 1000, 2000)
        err = f"forks: pax: {self.host} holds as many bytes as it may, runs not tried: 2\n"
        self.assertEqual(shared, Shared(1, out, err))
        self.assertEqual(self.held(), [("pax", patterned(1000))])
        self.assertEqual(list(self.kept()[f"http://{self.host}/"]), ["pax"])


class Unpromised(Sharing):
    """Answers a collector never gives, which a stand-in gives in its place."""

    def setUp(self) -> None:
        super().setUp()
        self.write("raya.log", patterned(700, 100))
        self.write("saro.log", patterned(900, 200))

    def stopped(self, script: Callable[[Asked], object], said: str, out: str = "") -> StandIn:
        """Shares with a stand-in whose answer stops the go at its first run, and says so."""
        stand_in = self.stand_in(Plays(script))
        shared = self.share(to=self.address(stand_in))
        err = f"forks: raya: 127.0.0.1:{stand_in.port} {said}, runs not tried: 1\n"
        self.assertEqual(shared, Shared(1, out.replace("HOST", f"127.0.0.1:{stand_in.port}"), err))
        return stand_in

    def test_an_opening_answered_with_another_status_stops_the_go(self):
        for status in (200, 204, 301, 400, 403, 404, 409, 413, 500, 502, 503, 507):
            with self.subTest(status=status):
                answer = status, {"id": "s1", "receipt": "receipt-of-s1"}
                stand_in = self.stopped(to_posts(answer), f"answered {status}")
                self.assertEqual(len(stand_in.asked), 1)
                self.assertFalse((self.logs / KEPT).read_text().strip("{}\n"))

    def test_an_opening_answered_without_an_id_and_a_receipt_stops_the_go(self):
        bodies = [
            {},
            [],
            "s1",
            {"id": "s1"},
            {"receipt": "receipt-of-s1"},
            {"id": "", "receipt": "receipt-of-s1"},
            {"id": "s1", "receipt": ""},
            {"id": 1, "receipt": "receipt-of-s1"},
            {"id": "s1", "receipt": ["receipt-of-s1"]},
            {"id": "s/1", "receipt": "receipt-of-s1"},
            {"id": "../s1", "receipt": "receipt-of-s1"},
            {"id": "s" * 65, "receipt": "receipt-of-s1"},
            {"id": "s1", "receipt": "r" * 65},
            {"id": "s1", "receipt": "a receipt"},
            {"id": "s1", "receipt": "receipt\r\nForks-More: 1"},
            {"id": "s1", "receipt": "receipt-of-s1", "pad": "x" * 4096},
            {"id": "s1", "receipt": TOKEN},
            {"id": TOKEN, "receipt": "receipt-of-s1"},
            {"id": "s1", "receipt": f"receipt-{TOKEN}-of-s1"},
            b"<html>a page</html>",
            b"",
            b'{"id": "s1", "receipt": "receipt-of-',
        ]
        for body in bodies:
            with self.subTest(body=str(body)[:40]):
                stand_in = self.stopped(
                    to_posts((201, body)), "answered 201 and not what the call promises"
                )
                self.assertEqual(len(stand_in.asked), 1)
        self.assertFalse((self.logs / KEPT).read_text().strip("{}\n"))

    def test_an_id_and_a_receipt_of_64_characters_are_taken(self):
        answer = 201, {"id": "s" * 64, "receipt": "r" * 64, "more": "of something"}
        script = lambda asked: (200, {"size": 700}) if asked.method == "PUT" else answer
        stand_in = self.stand_in(script)
        shared = self.share(self.logs / "raya.log", to=self.address(stand_in))
        self.assertEqual(shared, Shared(0, self.line("raya", 700, 700, at=stand_in), ""))
        put = stand_in.asked[1]
        self.assertEqual(put.path, f"/runs/{'s' * 64}/log?offset=0")
        self.assertEqual(put.headers["Forks-Receipt"], "r" * 64)

    def test_bytes_answered_with_another_status_stop_the_go(self):
        for status in (201, 204, 302, 400, 411, 500, 502, 503):
            with self.subTest(status=status):
                stand_in = self.stopped(to_puts((status, {"size": 700})), f"answered {status}")
                self.assertEqual(len(stand_in.asked), 2)

    def test_bytes_answered_without_the_size_then_held_stop_the_go(self):
        bodies = [
            {},
            [],
            700,
            {"size": "700"},
            {"size": 700.0},
            {"size": True},
            {"size": None},
            {"size": -1},
            {"size": 699},
            {"size": 701},
            {"size": 0},
            {"final": False},
            {"size": 700, "pad": "x" * 4096},
            b" " * 4096 + b'{"size": 700}',
            b"<html>a page</html>",
            b"",
        ]
        for body in bodies:
            with self.subTest(body=str(body)[:40]):
                stand_in = self.stopped(
                    to_puts((200, body)), "answered 200 and not what the call promises"
                )
                self.assertEqual(len(stand_in.asked), 2)

    def test_a_409_without_the_size_held_stops_the_go(self):
        for body in ({}, {"size": "0"}, {"size": -1}, {"size": False}, b"<html>a page</html>"):
            with self.subTest(body=str(body)):
                self.stopped(to_puts((409, body)), "answered 409 and not what the call promises")

    def test_a_409_says_the_size_whatever_else_it_says(self):
        stand_in = self.stand_in(Plays(in_turn(None, (409, {"size": 200, "more": 1}))))
        self.share(self.logs / "raya.log", to=self.address(stand_in))
        sent, rest = stand_in.asked[2], patterned(700, 100)[200:]
        self.assertEqual((sent.path, sent.body), ("/runs/s1/log?offset=200", rest))

    def test_a_run_just_opened_that_the_collector_does_not_have_stops_the_go(self):
        stand_in = self.stopped(to_puts((404, {"error": "no such run"})), "answered 404")
        self.assertEqual([asked.method for asked in stand_in.asked], ["POST", "PUT"])

    def test_a_run_that_is_gone_again_as_soon_as_it_is_opened_stops_the_go(self):
        stand_in = self.stand_in(Plays(to_puts((404, {"error": "no such run"}))))
        kept = {name: {"id": f"old-{name}", "receipt": "a-receipt"} for name in ("raya", "saro")}
        (self.logs / KEPT).write_text(json.dumps({f"http://127.0.0.1:{stand_in.port}/": kept}))
        shared = self.share(to=self.address(stand_in))
        err = f"forks: raya: 127.0.0.1:{stand_in.port} answered 404, runs not tried: 1\n"
        self.assertEqual(shared, Shared(1, "", err))
        self.assertEqual([asked.method for asked in stand_in.asked], ["PUT", "POST", "PUT"])

    def test_a_server_error_stops_the_go_at_the_run_it_came_for(self):
        stand_in = self.stand_in(Plays(in_turn(None, None, None, (503, {"error": "not now"}))))
        self.write("pax.log", patterned(100))
        shared = self.share(to=self.address(stand_in))
        host = f"127.0.0.1:{stand_in.port}"
        err = f"forks: raya: {host} answered 503, runs not tried: 1\n"
        self.assertEqual(shared, Shared(1, self.line("pax", 100, 100, at=stand_in), err))
        self.assertEqual(len(stand_in.asked), 4)

    def test_an_answer_that_is_not_http_or_is_cut_short_stops_the_go(self):
        opened, unread = b"HTTP/1.0 201 Created\r\n", "gave an answer that cannot be read"
        answers = {
            b"what is this\r\n\r\n": unread,
            opened + b"x" * 70000: unread,
            opened + b"X-More: 1\r\n" * 200: unread,
            opened + b'Content-Length: 50\r\n\r\n{"id": "s1", "rec': (
                "answered 201 and not what the call promises"
            ),
            b"": "could not be called: Remote end closed connection without response",
        }
        for answer, said in answers.items():
            with self.subTest(answer=answer[:40]):
                stand_in = self.stopped(to_posts(answer), said)
                self.assertEqual(len(stand_in.asked), 1)

    def test_what_a_server_says_is_in_no_line(self):
        said = f"a page that quotes {TOKEN}"
        script = to_posts((503, {"error": said}, {"X-Said": said}))
        self.stopped(script, "answered 503")
        unread = "gave an answer that cannot be read"
        self.stopped(to_posts(f"HTTP/1.0 {said}\r\n\r\n".encode()), unread)

    def test_a_redirect_is_not_followed(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                target = self.stand_in(Plays())
                there = {"Location": f"http://127.0.0.1:{target.port}/runs"}
                front = self.stopped(lambda asked: (status, {}, there), f"answered {status}")
                self.assertEqual((len(front.asked), target.asked), (1, []))

    def test_a_collector_that_never_answers_is_given_up(self):
        self.assertEqual(share.REQUEST, 30)
        self.enter(mock.patch.object(share, "REQUEST", 0.5))
        started = time.monotonic()
        stand_in = self.stopped(lambda asked: SILENT, "gave no answer in 0.5 seconds")
        waited = time.monotonic() - started
        self.assertEqual(len(stand_in.asked), 1)
        # For the time it states, and not for much longer.
        self.assertTrue(0.5 <= waited < 1.25, waited)

    def test_a_collector_that_answers_a_byte_now_and_then_is_given_up_all_the_same(self):
        def slowly(wfile, over: threading.Event) -> None:
            wfile.write(b"HTTP/1.0 200 OK\r\nX-Slow: ")
            while not over.wait(0.02):
                wfile.write(b"a")

        self.enter(mock.patch.object(share, "REQUEST", 0.3))
        stand_in = self.stopped(in_turn(None, slowly), "gave no answer in 0.3 seconds")
        self.assertEqual(len(stand_in.asked), 2)

    def test_a_collector_that_cannot_be_reached_is_said_in_a_line(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            port = taken.getsockname()[1]
        shared = self.share(to=f"http://127.0.0.1:{port}/?token={TOKEN}")
        self.assertEqual((shared.status, shared.out), (1, ""))
        self.assertRegex(
            shared.err,
            rf"^forks: raya: 127\.0\.0\.1:{port} could not be called: \[Errno \d+\] [^\n]+"
            r", runs not tried: 1\n$",
        )
        self.assertEqual(self.held(), [])

    def test_a_call_that_fails_in_a_way_nobody_foresaw_is_said_at_once_and_not_quoted(self):
        self.enter(mock.patch.object(share, "REQUEST", 5))
        said = f"a request that quotes {TOKEN}"
        failing = {
            "ValueError": mock.patch.object(
                http.client.HTTPConnection, "request", side_effect=ValueError(said)
            ),
            "RuntimeError": mock.patch.object(
                http.client, "HTTPConnection", side_effect=RuntimeError(said)
            ),
        }
        for name, failure in failing.items():
            with self.subTest(error=name):
                started = time.monotonic()
                with failure:
                    shared = self.share()
                err = f"forks: raya: {self.host} could not be called: {name}, runs not tried: 1\n"
                self.assertEqual(shared, Shared(1, "", err))
                self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(self.held(), [])

    def test_every_call_is_made_under_the_path_of_the_address(self):
        for under in ("/shared/forks", "/shared/forks/", "/a%20b/~saro/"):
            with self.subTest(under=under):
                stand_in = self.stand_in(Plays())
                shared = self.share(to=self.address(stand_in, under=under))
                self.assertEqual(shared.status, 0)
                base = under.rstrip("/")
                self.assertEqual(
                    [(asked.method, asked.path) for asked in stand_in.asked],
                    [
                        ("POST", f"{base}/runs"),
                        ("PUT", f"{base}/runs/s1/log?offset=0"),
                        ("POST", f"{base}/runs"),
                        ("PUT", f"{base}/runs/s2/log?offset=0"),
                    ],
                )
                kept = self.kept()[f"http://127.0.0.1:{stand_in.port}{base}/"]
                self.assertEqual(sorted(kept), ["raya", "saro"])

    def test_the_token_goes_in_one_header_and_the_receipt_in_another(self):
        stand_in = self.stand_in(Plays())
        self.share(to=self.address(stand_in))
        self.share(to=self.address(stand_in))
        self.assertEqual(len(stand_in.asked), 6)
        for asked in stand_in.asked:
            with self.subTest(asked=asked.path):
                headers = dict(asked.headers)
                self.assertEqual(headers.pop("Authorization"), f"Bearer {TOKEN}")
                self.assertEqual(headers.pop("Content-Length"), str(len(asked.body)))
                self.assertEqual(headers.pop("Connection"), "close")
                self.assertEqual(headers.pop("Host"), f"127.0.0.1:{stand_in.port}")
                self.assertEqual(headers.pop("Accept-Encoding"), "identity")
                if asked.method == "POST":
                    self.assertEqual(headers, {"Content-Type": "application/json"})
                    said = json.loads(asked.body)
                    self.assertEqual(said, {"name": said["name"], "app": "forks share"})
                else:
                    run = asked.path.split("/")[2]
                    self.assertEqual(headers.pop("Forks-Receipt"), f"receipt-of-{run}")
                    self.assertEqual(headers, {"Content-Type": "application/octet-stream"})
                self.assertNotIn(TOKEN, asked.path)
                self.assertNotIn(TOKEN.encode(), asked.body)


class Remembering(Sharing):
    """The file a directory's runs are remembered in."""

    def setUp(self) -> None:
        super().setUp()
        self.file = self.logs / KEPT
        self.base = f"http://{self.host}/"
        self.write("saro.log", b"one\n")

    def test_it_holds_the_id_and_the_receipt_of_each_run_by_collector_and_by_name(self):
        self.write("raya.log", b"two\n")
        self.share()
        self.assertEqual(
            self.file.read_text(),
            json.dumps(
                {
                    self.base: {
                        "raya": {"id": "r101", "receipt": "receipt-of-r101"},
                        "saro": {"id": "r102", "receipt": "receipt-of-r102"},
                    }
                },
                indent=2,
            )
            + "\n",
        )
        there = sorted(path.name for path in self.logs.iterdir())
        self.assertEqual(there, [KEPT, "raya.log", "saro.log"])

    def test_it_is_its_owners_alone_whatever_was_there_before(self):
        self.addCleanup(os.umask, os.umask(0))
        self.share()
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.file.chmod(0o666)
        left = self.logs / ".forks-share.new"
        left.write_text("what a stop left behind")
        left.chmod(0o666)
        self.write("raya.log", b"two\n")
        modes, replace = [], os.replace

        def watched(scratch: Path, path: Path) -> None:
            modes.append(stat.S_IMODE(scratch.stat().st_mode))
            replace(scratch, path)

        with on_replace(watched):
            self.assertEqual(self.share().status, 0)
        self.assertEqual(modes, [0o600, 0o600])
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertEqual(sorted(self.kept()[self.base]), ["raya", "saro"])
        self.assertFalse(left.exists())

    def test_it_is_replaced_in_one_step_by_a_file_that_is_whole(self):
        self.share()
        before = self.file.read_bytes()
        self.write("raya.log", b"two\n")
        replaced, replace = [], os.replace

        def watched(scratch: Path, path: Path) -> None:
            # What is there until the step, and what is there from it on.
            replaced.append((path.read_bytes(), json.loads(scratch.read_bytes())))
            replace(scratch, path)

        with on_replace(watched):
            self.assertEqual(self.share().status, 0)
        self.assertEqual([old for old, _ in replaced][0], before)
        names = [sorted(new[self.base]) for _, new in replaced]
        self.assertEqual(names, [["saro"], ["raya", "saro"]])
        self.assertEqual(replaced[-1][1], self.kept())

    def test_a_stop_inside_the_replacing_leaves_the_one_before(self):
        self.share()
        before = self.file.read_bytes()
        self.write("raya.log", b"two\n")
        with on_replace(lambda scratch, path: kill()), self.assertRaises(Killed):
            self.share()
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.share().status, 0)
        self.assertEqual(self.held(), [("raya", b"two\n"), ("saro", b"one\n")])

    def test_a_directory_that_cannot_keep_the_file_has_no_run_opened(self):
        def refused(scratch: Path, path: Path) -> None:
            raise PermissionError(13, "Permission denied")

        with on_replace(refused):
            shared = self.share()
        err = f"forks: [Errno 13] Permission denied: no more is sent from {self.logs}\n"
        self.assertEqual(shared, Shared(2, "", err))
        self.assertEqual((self.held(), self.served.written), ([], []))
        self.assertFalse(self.file.exists())

    def test_a_directory_that_takes_no_file_is_said_and_the_others_are_sent(self):
        if os.geteuid() == 0:
            self.skipTest("nothing is closed to the user these tests run as")
        other = self.scratch / "elsewhere"
        self.write("raya.log", b"two\n", other)
        self.logs.chmod(0o500)
        self.addCleanup(self.logs.chmod, 0o700)
        shared = self.share(self.logs, other)
        self.assertEqual((shared.status, shared.out), (2, self.line("raya", 4, 4)))
        self.assertRegex(
            shared.err,
            rf"^forks: \[Errno 13\] [^\n]+\.forks-share\.new': no more is sent from {self.logs}\n$",
        )
        self.assertEqual(self.held(), [("raya", b"two\n")])

    def test_a_write_that_fails_later_sends_no_more_from_its_directory(self):
        self.write("raya.log", b"two\n")
        self.write("tomo.log", b"four\n")
        other = self.scratch / "elsewhere"
        self.write("pax.log", b"three\n", other)
        replace, calls = os.replace, itertools.count(1)

        def full(scratch: Path, path: Path) -> None:
            if path.parent == self.logs and next(calls) > 2:
                raise OSError(28, "No space left on device")
            replace(scratch, path)

        with on_replace(full):
            shared = self.share(self.logs, other)
        err = f"forks: [Errno 28] No space left on device: no more is sent from {self.logs}\n"
        self.assertEqual(shared, Shared(2, self.line("pax", 6, 6) + self.line("raya", 4, 4), err))
        self.assertEqual(self.held(), [("pax", b"three\n"), ("raya", b"two\n")])
        self.assertEqual(list(self.kept()[self.base]), ["raya"])

    def test_the_entries_of_a_second_collector_stay_beside_those_of_the_first(self):
        other = self.elsewhere()
        there = f"http://127.0.0.1:{other.port}/"
        self.assertEqual(self.share().status, 0)
        first = self.kept()
        self.assertEqual(self.share(to=self.address(other)).status, 0)
        both = self.kept()
        self.assertEqual((list(both), both[self.base]), ([self.base, there], first[self.base]))
        self.write("raya.log", b"two\n")
        self.assertEqual(self.share().status, 0)
        self.assertEqual(self.kept()[there], both[there])
        self.assertEqual(sorted(self.kept()[self.base]), ["raya", "saro"])
        self.assertEqual(self.share(to=self.address(other)).status, 0)
        for served in (self.served, other):
            self.assertEqual(self.held(served), [("raya", b"two\n"), ("saro", b"one\n")])

    def test_a_collector_under_another_spelling_of_its_address_is_another(self):
        self.share()
        spelled = self.base.replace("http://", "HTTP://")
        shared = self.share(to=self.address().replace("http://", "HTTP://"))
        self.assertEqual(shared, Shared(0, self.line("saro", 4, 4), ""))
        self.assertEqual(self.held(), [("saro", b"one\n"), ("saro", b"one\n")])
        self.assertEqual(list(self.kept()), [self.base, spelled])

    def test_a_log_behind_a_link_is_remembered_where_the_link_leads(self):
        log = self.write("raya.log", b"two\n", self.logs.parent / "elsewhere")
        links = self.logs.parent / "links"
        links.mkdir()
        (links / "pax.log").symlink_to(log)
        shared = self.share(links)
        self.assertEqual(shared, Shared(0, self.line("raya", 4, 4), ""))
        self.assertEqual(list(self.kept(log.parent)[self.base]), ["raya"])
        self.assertEqual(list(links.iterdir()), [links / "pax.log"])

    def test_an_entry_stays_while_its_log_is_not_among_the_paths(self):
        raya = self.write("raya.log", b"two\n")
        self.share()
        kept = self.kept()
        (self.logs / "saro.log").unlink()
        self.assertEqual(self.share(raya).status, 0)
        self.assertEqual(self.kept(), kept)

    def test_one_it_did_not_write_is_refused_and_left_as_it_is(self):
        entry = {"id": "r1", "receipt": "receipt-of-r1"}
        base, said = self.base.encode(), json.dumps(entry).encode()
        other = json.dumps({"id": "r2", "receipt": "receipt-of-r2"}).encode()
        foreign = [
            b"",
            b"notes of my own\n",
            b"\xff\xfe\x00",
            b"null",
            b"[]",
            b'"saro"',
            b"{" * 100000,
            json.dumps({self.base: {"saro": entry}})[:-9].encode(),
            json.dumps({self.base: [entry]}).encode(),
            json.dumps({self.base: {"saro": "r1"}}).encode(),
            json.dumps({self.base: {"saro": {"id": "r1"}}}).encode(),
            json.dumps({self.base: {"saro": {"receipt": "receipt-of-r1"}}}).encode(),
            json.dumps({self.base: {"saro": dict(entry, token="t" * 16)}}).encode(),
            json.dumps({self.base: {"saro": dict(entry, id=1)}}).encode(),
            json.dumps({self.base: {"saro": dict(entry, id="r/1")}}).encode(),
            json.dumps({self.base: {"saro": dict(entry, receipt="a receipt")}}).encode(),
            json.dumps({self.base: {"saro": dict(entry, receipt="r" * 65)}}).encode(),
            json.dumps({"http://elsewhere.example/": {"raya": {}}, self.base: {}}).encode(),
            # A key said twice: a reader that kept the last would lose the run of the first.
            b'{"%s": {"saro": %s, "saro": %s}}' % (base, said, other),
            b'{"%s": {"saro": %s}, "%s": {}}' % (base, said, base),
            b'{"%s": {"saro": {"id": "r1", "id": "r2", "receipt": "receipt-of-r1"}}}' % base,
            json.dumps({"address": self.address(), "keep": True, "runs": []}).replace(
                TOKEN, "a-token-of-another-box"
            ).encode(),
        ]
        other = self.scratch / "elsewhere"
        self.write("raya.log", b"two\n", other)
        self.write("tomo.log", b"four\n")
        for written in foreign:
            with self.subTest(written=written[:60]):
                served = self.elsewhere()
                self.file.write_bytes(written)
                self.file.chmod(0o644)
                shared = self.share(self.logs, other, to=self.address(served))
                err = (
                    f"forks: {self.file} is not a file `forks share` wrote: "
                    f"no more is sent from {self.logs}\n"
                )
                self.assertEqual(shared, Shared(2, self.line("raya", 4, 4, at=served), err))
                self.assertEqual(self.file.read_bytes(), written)
                self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o644)
                self.assertEqual(self.held(served), [("raya", b"two\n")])
                there = sorted(path.name for path in self.logs.iterdir())
                self.assertEqual(there, [KEPT, "saro.log", "tomo.log"])

    def test_one_that_cannot_be_read_is_refused_and_left_as_it_is(self):
        self.file.mkdir()
        shared = self.share()
        self.assertEqual((shared.status, shared.out), (2, ""))
        self.assertRegex(
            shared.err, rf"^forks: \[Errno \d+\] [^\n]+: no more is sent from {self.logs}\n$"
        )
        self.assertTrue(self.file.is_dir())
        self.assertEqual(self.held(), [])

    def test_one_closed_to_its_reader_is_refused_and_left_as_it_is(self):
        if os.geteuid() == 0:
            self.skipTest("nothing is closed to the user these tests run as")
        self.share()
        kept = self.file.read_bytes()
        self.write("raya.log", b"two\n")
        self.file.chmod(0)
        self.addCleanup(self.file.chmod, 0o600)
        shared = self.share()
        self.assertEqual((shared.status, shared.out), (2, ""))
        self.assertRegex(
            shared.err, rf"^forks: \[Errno 13\] [^\n]+: no more is sent from {self.logs}\n$"
        )
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0)
        self.file.chmod(0o600)
        self.assertEqual(self.file.read_bytes(), kept)
        self.assertEqual(self.held(), [("saro", b"one\n")])

    def test_a_run_that_is_not_whole_leaves_the_go_at_the_2_of_a_directory_it_could_not_use(self):
        other = self.scratch / "elsewhere"
        tomo = self.write("tomo.log", b"four\n", other)
        self.assertEqual(self.share(tomo).status, 0)
        tomo.write_bytes(b"")
        self.file.write_bytes(b"notes of my own\n")
        shared = self.share(self.logs, other)
        err = (
            f"forks: {self.file} is not a file `forks share` wrote: "
            f"no more is sent from {self.logs}\n"
            f"forks: tomo: {self.host} holds 5 bytes and the disk has 0\n"
        )
        self.assertEqual(shared, Shared(2, self.line("tomo", 0, 5, 0), err))

    def test_what_took_the_name_the_file_is_first_written_under_is_not_written_through(self):
        other = self.scratch / "a-file-of-another"
        other.write_text("as it was\n")
        unlink = Path.unlink

        def taken(path: Path, missing_ok: bool = False) -> None:
            unlink(path, missing_ok=missing_ok)
            if path.name == ".forks-share.new":
                path.symlink_to(other)

        with mock.patch.object(Path, "unlink", taken):
            shared = self.share()
        self.assertEqual((shared.status, shared.out), (2, ""))
        self.assertRegex(
            shared.err, rf"^forks: \[Errno 17\] [^\n]+: no more is sent from {self.logs}\n$"
        )
        self.assertEqual(other.read_text(), "as it was\n")
        self.assertEqual(self.held(), [])
        self.assertFalse(self.file.exists())

    def test_one_that_holds_no_run_is_its_own(self):
        for written in (b"{}", b"{}\n", json.dumps({"http://elsewhere.example/": {}}).encode()):
            with self.subTest(written=written):
                served = self.elsewhere()
                self.file.write_bytes(written)
                shared = self.share(to=self.address(served))
                self.assertEqual(shared, Shared(0, self.line("saro", 4, 4, at=served), ""))
                self.assertEqual(list(self.kept()), [f"http://127.0.0.1:{served.port}/"])

    def test_it_never_holds_the_token(self):
        self.share()
        self.share(to=self.address(under="/elsewhere/"))
        self.assertNotIn(TOKEN, self.file.read_text())
        self.assertEqual(list(self.kept()), [self.base])


class Alone(Sharing):
    """Two goes at a time on one directory."""

    def test_of_two_goes_at_a_time_one_sends_from_a_directory_and_the_other_says_so(self):
        self.write("raya.log", b"one\n")
        self.write("saro.log", b"two\n")
        other = self.scratch / "elsewhere"
        self.write("tomo.log", b"four\n", other)
        reached, done = threading.Event(), threading.Event()

        def first_held(asked: Asked) -> None:
            # The first request of the first go waits until the second go is over.
            if not reached.is_set():
                reached.set()
                done.wait(20)

        plays = Plays(first_held)
        stand_in = self.stand_in(plays)
        command = [sys.executable, str(TOOL), "share", "--to", self.address(stand_in)]
        with subprocess.Popen(
            [*command, str(self.logs)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ) as first:
            self.assertTrue(reached.wait(20))
            shared = self.share(self.logs, other, to=self.address(stand_in))
            made = [(asked.method, asked.body) for asked in stand_in.asked]
            done.set()
            out, err = first.communicate(timeout=20)
        said = (
            "forks: another `forks share` is sending from the directory: "
            f"no more is sent from {self.logs}\n"
        )
        self.assertEqual(shared, Shared(2, self.line("tomo", 5, 5, at=stand_in), said))
        # The second go made no request for a run of the directory the first had.
        opened = [
            ("POST", json.dumps({"name": name, "app": "forks share"}).encode())
            for name in ("raya", "tomo")
        ]
        self.assertEqual(made, [*opened, ("PUT", b"four\n")])
        lines = self.line("raya", 4, 4, at=stand_in) + self.line("saro", 4, 4, at=stand_in)
        self.assertEqual((first.returncode, out, err), (0, lines.encode(), b""))
        self.assertEqual(sorted(plays.held.values()), [b"four\n", b"one\n", b"two\n"])
        base = f"http://127.0.0.1:{stand_in.port}/"
        self.assertEqual(sorted(self.kept()[base]), ["raya", "saro"])
        # The directory is the next go's again, and that of a go that was stopped.
        with at_request(1, kill), self.assertRaises(Killed):
            self.share(to=self.address(stand_in))
        shared = self.share(self.logs, other, to=self.address(stand_in))
        self.assertEqual((shared.status, shared.err), (0, ""))
        self.assertEqual(len(plays.held), 3)

    def test_a_directory_the_system_gives_no_lock_on_is_said_and_the_others_are_sent(self):
        self.write("saro.log", b"one\n")
        other = self.scratch / "elsewhere"
        self.write("raya.log", b"two\n", other)
        flock = fcntl.flock

        def refused(held: int, how: int) -> None:
            if os.path.samestat(os.fstat(held), os.stat(self.logs)):
                raise OSError(37, "No locks available")
            flock(held, how)

        with mock.patch.object(fcntl, "flock", refused):
            shared = self.share(self.logs, other)
        err = f"forks: [Errno 37] No locks available: no more is sent from {self.logs}\n"
        self.assertEqual(shared, Shared(2, self.line("raya", 4, 4), err))
        self.assertEqual(self.held(), [("raya", b"two\n")])
        self.assertFalse((self.logs / KEPT).exists())

    def test_a_python_without_flock_sends_nothing_and_says_so(self):
        self.write("saro.log", b"one\n")
        with mock.patch.dict(sys.modules, {"fcntl": None}):
            shared = self.share()
        err = (
            "forks: this Python has no `flock` to hold a directory by: "
            f"no more is sent from {self.logs}\n"
        )
        self.assertEqual(shared, Shared(2, "", err))
        self.assertEqual(self.held(), [])
        self.assertFalse((self.logs / KEPT).exists())


class Moving(Sharing):
    """A writer that moves its log aside, or cuts it, while a go is under way."""

    def setUp(self) -> None:
        super().setUp()
        self.enter(mock.patch.object(share, "PIECE", 400))
        self.first, self.second = patterned(1000, 100), patterned(1100, 500)
        self.log = self.write("saro.log", self.first)

    def rotate(self) -> None:
        self.log.rename(self.logs / "saro.001.log")
        self.log.write_bytes(self.second)

    def test_a_log_moved_aside_during_a_go_is_not_sent_as_the_file_that_took_its_name(self):
        # The second piece is read by then, and the third is not.
        with at_request(3, self.rotate):
            shared = self.share()
        err = "forks: saro: saro.log was moved or cut while it was sent\n"
        self.assertEqual(shared, Shared(1, self.line("saro", 800, 800, 1000), err))
        self.assertEqual(self.held(), [("saro", self.first[:800])])
        shared = self.share()
        self.assertEqual(shared, Shared(0, self.line("saro", 1300, 2100), ""))
        self.assertEqual(self.held(), [("saro", self.first + self.second)])

    def test_a_log_moved_aside_while_the_logs_are_listed_is_listed_again(self):
        find, listed = share.find_runs, []

        def moving(paths):
            runs = find(paths)
            if not listed:
                self.rotate()
            listed.append(runs)
            return runs

        with mock.patch.object(share, "find_runs", moving):
            shared = self.share()
        self.assertEqual(shared, Shared(0, self.line("saro", 2100, 2100), ""))
        self.assertEqual(self.held(), [("saro", self.first + self.second)])
        self.assertEqual([len(runs[0].parts) for runs in listed], [1, 2, 2, 2])

    def test_a_log_cut_during_a_go_is_said_and_sent_no_further(self):
        with at_request(3, lambda: self.log.write_bytes(self.first[:900])):
            shared = self.share()
        err = "forks: saro: saro.log was moved or cut while it was sent\n"
        self.assertEqual(shared, Shared(1, self.line("saro", 800, 800, 1000), err))
        self.assertEqual(self.held(), [("saro", self.first[:800])])

    def test_a_log_that_is_gone_during_a_go_is_said_and_the_next_run_goes_on(self):
        self.write("tomo.log", b"one\n")
        with at_request(3, self.log.unlink):
            shared = self.share()
        out = self.line("saro", 800, 800, 1000) + self.line("tomo", 4, 4)
        self.assertEqual((shared.status, shared.out), (1, out))
        self.assertRegex(
            shared.err, r"^forks: saro: its log cannot be read: \[Errno 2\] [^\n]+saro\.log'\n$"
        )

    def test_what_a_log_gains_during_a_go_is_sent_by_the_next(self):
        def gain() -> None:
            with open(self.log, "ab") as file:
                file.write(self.second)

        with at_request(2, gain):
            shared = self.share()
        self.assertEqual(shared, Shared(0, self.line("saro", 1000, 1000), ""))
        self.assertEqual(self.share(), Shared(0, self.line("saro", 1100, 2100), ""))
        self.assertEqual(self.held(), [("saro", self.first + self.second)])


class Paths(Sharing):
    """Paths that cannot be used: the go ends with 2, and nothing was sent."""

    def refused(self, *paths: Path) -> str:
        shared = self.share(*paths)
        self.assertEqual((shared.status, shared.out), (2, ""))
        self.assertEqual((self.held(), self.served.written), ([], []))
        self.assertEqual(list(self.scratch.rglob(KEPT)), [])
        return shared.err

    def test_paths_that_make_no_set_of_runs_are_refused_as_report_refuses_them(self):
        self.assertEqual(self.refused(), f"forks: {self.logs} holds no .log file\n")
        saro = self.write("saro.log", b"one\n")
        other = self.write("saro.log", b"two\n", self.scratch / "elsewhere")
        self.assertIn("more than one run is named saro", self.refused(saro, other))
        twice = self.refused(saro, self.write("saro.txt", b""))
        self.assertIn("the same part of the run saro", twice)

    def test_a_log_that_is_not_there_is_refused(self):
        self.write("saro.log", b"one\n")
        said = self.refused(self.logs, self.scratch / "nowhere.log")
        self.assertRegex(said, r"^forks: \[Errno 2\] [^\n]+nowhere\.log'\n$")

    def test_a_pipe_is_refused(self):
        self.write("saro.log", b"one\n")
        pipe = self.logs / "raya.log"
        try:
            os.mkfifo(pipe)
        except (AttributeError, OSError):
            self.skipTest("this system makes no pipe with a name")
        self.assertEqual(
            self.refused(self.logs / "saro.log", pipe),
            f"forks: {pipe} is not a file, and a log is sent from one\n",
        )

    def test_a_log_that_cannot_be_read_is_found_before_anything_is_sent(self):
        if os.geteuid() == 0:
            self.skipTest("nothing is closed to the user these tests run as")
        self.write("raya.log", b"one\n")
        closed = self.write("saro.log", b"two\n")
        closed.chmod(0)
        self.addCleanup(closed.chmod, 0o600)
        self.assertRegex(self.refused(), r"^forks: \[Errno 13\] [^\n]+saro\.log'\n$")

    def test_a_log_whose_name_is_white_space_is_refused(self):
        self.write("saro.log", b"one\n")
        self.write("  .001.log", b"one\n")
        blank = self.write("  .log", b"two\n")
        self.assertEqual(self.refused(), f"forks: {blank} has no name a run could carry\n")

    def test_a_run_of_which_only_some_files_are_named_is_refused(self):
        first = self.write("chat.001.log", b"aaaa\n")
        second = self.write("chat.002.log", b"bbbb\n")
        written = self.write("chat.log", b"cccc\n")
        raya = self.write("raya.log", b"two\n")
        # Of the files left out, the first of the run is the one named.
        refused = (((written, second), first), ((first, second), written), ((written,), first))
        for named, left in refused:
            with self.subTest(named=[file.name for file in named]):
                self.assertEqual(
                    self.refused(raya, *named),
                    f"forks: {left} is a part of the run chat and not among the paths: "
                    "a run is sent whole\n",
                )

    def test_a_usage_error_exits_2(self):
        self.write("saro.log", b"one\n")
        for arguments in (["share"], ["share", str(self.logs)], ["share", "--to", self.address()]):
            with self.subTest(arguments=arguments[1:2]):
                said = io.StringIO()
                with contextlib.redirect_stderr(said), self.assertRaises(SystemExit) as stopped:
                    main(arguments)
                self.assertEqual(stopped.exception.code, 2)
                self.assertNotIn(TOKEN, said.getvalue())
        self.assertEqual(self.held(), [])

    def test_an_address_out_of_its_place_is_never_quoted(self):
        self.write("saro.log", b"one\n")
        address, logs = self.address(), str(self.logs)
        escaped = address.replace("token=", "%74oken=")
        # The word is left out and nothing else: what was refused is still said.
        usage = r"usage: forks[^\n]*\n(?: [^\n]+\n)*"
        no_command = (
            rf"{usage}forks: error: argument COMMAND: "
            r"invalid choice: ADDRESS \(choose from [^\n]+\)\n"
        )
        no_path = r"forks: \[Errno 2\] No such file or directory: ADDRESS\n"
        refused = {
            ("--to", address, "share", logs): no_command,
            (address,): no_command,
            (escaped, "share", logs): no_command,
            ("share", "--to", address, logs, "--more", address): (
                rf"{usage}forks: error: unrecognized arguments: --more ADDRESS\n"
            ),
            ("share", "--to", address, address): no_path,
            ("share", "--to", address, logs, escaped): no_path,
            ("report", address): no_path,
            # Refused by the parser of a command, and not by that of `forks`.
            ("share", "--to", address, "--=" + address, logs): (
                rf"{usage}forks share: error: ambiguous option: ADDRESS could match [^\n]+\n"
            ),
            ("serve", "--port", address): (
                rf"{usage}forks serve: error: argument --port: ADDRESS is not a port\n"
            ),
        }
        for arguments, said in refused.items():
            with self.subTest(arguments=[word.split("?")[0] for word in arguments]):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        status = main(list(arguments))
                    except SystemExit as stopped:
                        status = stopped.code
                self.assertEqual((status, out.getvalue()), (2, ""))
                self.assertNotIn(TOKEN, err.getvalue())
                self.assertRegex(err.getvalue(), f"^{said}$")
        self.assertEqual(self.held(), [])
        self.assertEqual(list(self.scratch.rglob(KEPT)), [])


class Addresses(unittest.TestCase):
    """What is taken for a share address, and what is refused."""

    def test_the_base_is_all_before_the_query_with_a_slash_at_its_end(self):
        taken = {
            f"http://127.0.0.1:8750/?token={TOKEN}": ("http://127.0.0.1:8750/", "127.0.0.1:8750"),
            f"http://127.0.0.1:8750?token={TOKEN}": ("http://127.0.0.1:8750/", "127.0.0.1:8750"),
            f"https://forks.example/shared/logs?token={TOKEN}": (
                "https://forks.example/shared/logs/",
                "forks.example",
            ),
            f"https://forks.example/shared/logs/?token={TOKEN}": (
                "https://forks.example/shared/logs/",
                "forks.example",
            ),
            f"HTTP://Forks.Example:80/?token={TOKEN}": (
                "HTTP://Forks.Example:80/",
                "Forks.Example:80",
            ),
            f"http://[::1]:8750/?token={TOKEN}": ("http://[::1]:8750/", "[::1]:8750"),
            f"http://127.0.0.1:8750/?name=saro&token={TOKEN}&more": (
                "http://127.0.0.1:8750/",
                "127.0.0.1:8750",
            ),
            f"http://{'x' * 63}.forks.example./?token={TOKEN}": (
                f"http://{'x' * 63}.forks.example./",
                f"{'x' * 63}.forks.example.",
            ),
        }
        for address, (base, host) in taken.items():
            with self.subTest(address=address):
                collector = address_of(address)
                self.assertEqual((collector.base, collector.host), (base, host))
                self.assertEqual(collector.token, TOKEN)

    def test_a_token_is_16_to_128_characters(self):
        for token in ("t" * 16, "T0_-" * 32):
            self.assertEqual(address_of(f"http://127.0.0.1:8750/?token={token}").token, token)

    def test_what_is_no_share_address_is_refused_and_never_quoted(self):
        at = "http://127.0.0.1:8750/"
        refused = {
            "the address is not http or https": [
                f"ftp://127.0.0.1:8750/?token={TOKEN}",
                f"ws://127.0.0.1:8750/?token={TOKEN}",
                f"file:///forks-data/?token={TOKEN}",
                f"127.0.0.1:8750/?token={TOKEN}",
                f"//127.0.0.1:8750/?token={TOKEN}",
                f"token={TOKEN}",
                "",
            ],
            "the address holds a fragment": [
                f"{at}?token={TOKEN}#lanes",
                f"{at}?token={TOKEN}#",
                f"{at}#lanes?token={TOKEN}",
            ],
            "the address holds a user or a password": [
                f"http://saro@127.0.0.1:8750/?token={TOKEN}",
                f"http://saro:word@127.0.0.1:8750/?token={TOKEN}",
                f"http://:word@127.0.0.1:8750/?token={TOKEN}",
                f"http://@127.0.0.1:8750/?token={TOKEN}",
            ],
            "the address holds no token": [
                at,
                f"{at}?",
                f"{at}?tokens={TOKEN}",
                f"{at}?TOKEN={TOKEN}",
                f"{at}?name=token&key={TOKEN}",
                f"{at}{TOKEN}",
            ],
            "the address holds more than one token": [
                f"{at}?token={TOKEN}&token={TOKEN}",
                f"{at}?token={TOKEN}&token=",
                f"{at}?token={TOKEN}&%74oken={TOKEN}s",
            ],
            "the address spells its token with an escape": [
                f"{at}?%74oken={TOKEN}",
                f"{at}?tok%65n={TOKEN}",
                f"{at}?token={TOKEN.replace('-', '%2D')}",
                f"{at}?token=%61{TOKEN[1:]}",
            ],
            f"the address holds {NOT_A_TOKEN}": [
                f"{at}?token={'t' * 15}",
                f"{at}?token={'t' * 129}",
                f"{at}?token=",
                f"{at}?token",
                f"{at}?token=a-token/of+other=signs",
                f"{at}?token={TOKEN}?",
                f"{at}?token=={TOKEN}",
            ],
            "the address names no host": [
                f"http://:8750/?token={TOKEN}",
                f"http:///?token={TOKEN}",
                f"http:/127.0.0.1:8750/?token={TOKEN}",
                f"http:?token={TOKEN}",
            ],
            "the address holds no host and port that can be read": [
                f"http://127.0.0.1:port/?token={TOKEN}",
                f"http://127.0.0.1:99999/?token={TOKEN}",
                f"http://[::1/?token={TOKEN}",
                f"http://forks..example:8750/?token={TOKEN}",
                f"http://.forks.example/?token={TOKEN}",
                f"http://forks.example..:8750/?token={TOKEN}",
                f"http://{'x' * 64}.forks.example/?token={TOKEN}",
            ],
            "the address holds a character no address is written in": [
                f"{at}a log/?token={TOKEN}",
                f"{at}?token={TOKEN}\n",
                f" {at}?token={TOKEN}",
                f"{at}\t?token={TOKEN}",
                f"http://forks.ex\u00e4mple/?token={TOKEN}",
                f"{at}\x7f?token={TOKEN}",
            ],
        }
        self.assertEqual(sum(map(len, refused.values())), 51)
        for why, addresses in refused.items():
            for address in addresses:
                with self.subTest(address=address):
                    with self.assertRaises(share.NoAddress) as stopped:
                        address_of(address)
                    self.assertEqual(str(stopped.exception), why)
                    shared = go(address, Path("a-directory-there-is-not"))
                    err = f"{USAGE}forks share: error: argument --to: {why}\n"
                    self.assertEqual(shared, Shared(2, "", err))

    def test_an_address_is_called_at_its_host_and_port_or_at_the_port_of_its_scheme(self):
        called = {
            f"http://forks.example/?token={TOKEN}": ("HTTPConnection", "forks.example", 80),
            f"https://forks.example/?token={TOKEN}": ("HTTPSConnection", "forks.example", 443),
            f"https://forks.example:8443/?token={TOKEN}": (
                "HTTPSConnection",
                "forks.example",
                8443,
            ),
            f"HTTPS://Forks.Example/?token={TOKEN}": ("HTTPSConnection", "forks.example", 443),
            f"http://[::1]/?token={TOKEN}": ("HTTPConnection", "::1", 80),
            f"http://[::1]:8750/?token={TOKEN}": ("HTTPConnection", "::1", 8750),
            f"http://127.0.0.1:0/?token={TOKEN}": ("HTTPConnection", "127.0.0.1", 0),
        }
        ports = {"HTTPConnection": 80, "HTTPSConnection": 443}
        with tempfile.TemporaryDirectory() as scratch:
            log = Path(scratch) / "saro.log"
            log.write_bytes(b"one\n")
            for address, (kind, host, port) in called.items():
                with self.subTest(address=address):
                    (other,) = set(ports) - {kind}
                    with mock.patch.object(http.client, kind) as connection:
                        with mock.patch.object(http.client, other, side_effect=AssertionError):
                            connection.default_port = ports[kind]
                            connection.return_value.request.side_effect = OSError("no network")
                            shared = go(address, log)
                    connection.assert_called_once_with(host, port)
                    self.assertEqual((shared.status, shared.out), (1, ""))
                    self.assertTrue(shared.err.endswith(" could not be called: no network\n"))


class Processes(Sharing):
    """`forks share` as a process of its own."""

    def run_it(self, *arguments: str, encoding: str = "utf-8") -> "subprocess.CompletedProcess":
        done = subprocess.run(
            [sys.executable, str(TOOL), "share", *arguments],
            capture_output=True,
            env={**os.environ, "PYTHONIOENCODING": encoding},
        )
        self.assertNotIn(TOKEN.encode(), done.stdout + done.stderr)
        return done

    def test_the_command_exits_with_its_status_and_says_no_more_than_its_lines(self):
        self.write("saro.log", b"one\n")
        done = self.run_it("--to", self.address(), str(self.logs))
        out = self.line("saro", 4, 4).encode()
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, out, b""))
        (self.logs / "saro.log").write_bytes(b"")
        done = self.run_it("--to", self.address(), str(self.logs))
        err = f"forks: saro: {self.host} holds 4 bytes and the disk has 0\n".encode()
        out = self.line("saro", 0, 4, 0).encode()
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, out, err))
        done = self.run_it("--to", self.address(), str(self.scratch / "nowhere"))
        self.assertEqual((done.returncode, done.stdout), (2, b""))
        self.assertRegex(done.stderr, rb"^forks: \[Errno 2\] [^\n]+nowhere'\n$")
        done = self.run_it("--to", self.address().replace("http", "gopher"), str(self.logs))
        err = f"{USAGE}forks share: error: argument --to: the address is not http or https\n"
        self.assertEqual((done.returncode, done.stdout, done.stderr), (2, b"", err.encode()))

    def test_the_command_ends_once_a_request_is_given_up(self):
        self.write("saro.log", b"one\n")
        stand_in = self.stand_in(lambda asked: SILENT)
        # The command as it is run, but for the time it gives a request.
        quick = (
            "import sys; sys.path.insert(0, sys.argv.pop(1)); from forks import cli, share; "
            "share.REQUEST = 0.3; sys.exit(cli.main())"
        )
        command = [sys.executable, "-c", quick, str(TOOL), "share", "--to"]
        done = subprocess.run(
            [*command, self.address(stand_in), str(self.logs)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        err = f"forks: saro: 127.0.0.1:{stand_in.port} gave no answer in 0.3 seconds\n"
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, "", err))

    def test_an_output_that_is_gone_ends_the_go_and_is_not_laid_to_the_directory(self):
        self.write("raya.log", b"one\n")
        self.write("saro.log", b"two\n")

        class Gone(io.StringIO):
            def write(self, text: str) -> int:
                raise BrokenPipeError(32, "Broken pipe")

        said = io.StringIO()
        with contextlib.redirect_stdout(Gone()), contextlib.redirect_stderr(said):
            status = main(["share", "--to", self.address(), str(self.logs)])
        self.assertEqual((status, said.getvalue()), (2, "forks: [Errno 32] Broken pipe\n"))
        self.assertEqual(self.held(), [("raya", b"one\n")])

    def test_a_pipe_nobody_reads_ends_the_go_with_2_and_no_word_of_the_interpreters(self):
        self.write("raya.log", b"one\n")
        self.write("saro.log", b"two\n")
        reached, unread = threading.Event(), threading.Event()

        def first_held(asked: Asked) -> None:
            # The first bytes wait until nobody reads what the go prints.
            if asked.method == "PUT":
                reached.set()
                unread.wait(20)

        plays = Plays(first_held)
        stand_in = self.stand_in(plays)
        command = [sys.executable, str(TOOL), "share", "--to", self.address(stand_in)]
        with subprocess.Popen(
            [*command, str(self.logs)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ) as going:
            self.assertTrue(reached.wait(20))
            going.stdout.close()
            unread.set()
            err = going.stderr.read()
        self.assertEqual((going.returncode, err), (2, b"forks: [Errno 32] Broken pipe\n"))
        self.assertEqual(plays.held, {"s1": b"one\n"})

    def test_a_name_the_output_encoding_cannot_write_is_escaped(self):
        name = "jos\u00e9_\u65e5"
        self.write(f"{name}.log", b"one\n")
        written = {"utf-8": name.encode(), "ascii": b"jos\\xe9_\\u65e5"}
        for encoding, printed in written.items():
            with self.subTest(encoding=encoding):
                done = self.run_it("--to", self.address(), str(self.logs), encoding=encoding)
                self.assertEqual((done.returncode, done.stderr), (0, b""))
                self.assertTrue(done.stdout.startswith(printed + b": sent "), done.stdout)
        self.assertEqual(self.held(), [(name, b"one\n")])


class Readme(Sharing):
    def setUp(self) -> None:
        super().setUp()
        readme = (TOOL / "README.md").read_text()
        self.section = readme.split("\n## Sharing logs\n")[1].split("\n## ")[0]

    def test_the_examples_the_readme_gives_of_sharing_run(self):
        (command,) = map(shlex.split, re.findall(r"```sh\n(.*?)```", self.section, re.S))
        console = re.search(r"```console\n(.*?)```", self.section, re.S)[1]
        printed = console.splitlines(keepends=True)
        remembered = json.loads(re.search(r"```json\n(.*?)```", self.section, re.S)[1])
        self.assertEqual(command[:4], ["python3", "tools/forks", "share", "--to"])
        self.assertEqual(shlex.split(printed[0]), ["$", *command])
        there = address_of(command[4])
        self.assertEqual((there.base, there.host), ("http://127.0.0.1:8750/", "127.0.0.1:8750"))
        self.assertEqual(list(remembered), [there.base])

        # The directory of the example of `report`, sent to a collector on a port of its own.
        self.assertEqual(command[5:], [f"{self.logs.name}/"])
        shutil.copytree(HISTORIES / "split-at-an-epoch", self.logs, dirs_exist_ok=True)
        done = subprocess.run(
            [sys.executable, str(TOOL), "share", "--to", self.address(), *command[5:]],
            cwd=self.scratch,
            capture_output=True,
            text=True,
        )
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(done.stdout, "".join(printed[1:]).replace(there.host, self.host))
        self.assertEqual(self.served.report("b05d4417e9")["verdict"], "split at epoch 3")

        (name, entry), = remembered[there.base].items()
        kept = self.kept()[f"http://{self.host}/"]
        self.assertEqual(sorted(kept), ["mira", "pax", "raya", "saro"])
        self.assertEqual(list(kept[name]), list(entry))
        # The example is a file `share` takes for its own.
        (self.logs / KEPT).write_text(json.dumps({f"http://{self.host}/": {name: entry}}))
        done = subprocess.run(
            [sys.executable, str(TOOL), "share", "--to", self.address(), *command[5:]],
            cwd=self.scratch,
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_readme_says_the_name_the_limits_and_the_time_the_code_has(self):
        self.assertIn(f"`{share.REMEMBERED}`", self.section)
        self.assertIn(f"`{share.APP}`", self.section)
        self.assertIn(f"{share.REQUEST} seconds", self.section)
        self.assertEqual(share.PIECE, MIB)
        self.assertIn("1 MiB", self.section)


if __name__ == "__main__":
    unittest.main()
