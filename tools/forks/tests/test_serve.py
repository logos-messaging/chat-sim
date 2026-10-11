import contextlib
import hashlib
import http.client
import io
import itertools
import json
import os
import queue
import re
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, NamedTuple, Optional, Tuple
from unittest import mock
from urllib.parse import quote

from forks import rule
from forks.cli import main
from forks.collector import Collector, Refused, mint, to_stderr
from forks.records import Record
from forks.runs import find_runs
from forks.serve import Handler, Server

from . import FIXTURES, run_report
from .test_rule import ALL3, CONVO, HISTORIES, KEPT, Log, as_logs

TOOL = Path(__file__).resolve().parents[1]
REAL = FIXTURES / "real"
TOKEN = "a-token_for-the-tests"
HOUR = 60 * 60
DAY = 24 * HOUR
MAX_BODY = 4 * 1024 * 1024
# Noon on 2026-10-09, UTC.
START = 1791547200
OTHER = "77c1e09b3d"
PAIR = "saro raya"
# The first line `forks serve` logs.
SERVING = re.compile(
    r"[0-9-]{10}T[0-9:]{8}Z serving (?P<data>.+) on http://(?P<host>\S+):(?P<port>[0-9]+)/\n"
)
NO_TOKEN = b'{"error": "this call needs the collector\'s token"}'
POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
# What every answer carries, whatever it answers.
ON_EVERY_ANSWER = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Content-Security-Policy", POLICY),
)
KEYS = [
    "id",
    "verdict",
    "split_epoch",
    "coverage",
    "positions",
    "epochs",
    "branches",
    "states",
    "lanes",
    "folds",
    "folded",
    "installations",
    "timeline",
    "messages",
    "shown_messages",
    "unmatched_unreadable",
    "runs",
    "heard",
    "lines",
]


def stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hexed(name: str, digits: int = 8) -> str:
    """The hex a test's name for an installation or a state is logged as."""
    return hashlib.sha256(name.encode()).hexdigest()[:digits]


def account(name: str) -> str:
    return name.encode().hex()[:8]


def line_of(record: Record, group: str = "") -> str:
    """A record of the rule's tests as its writer logs it; a group has installations of its own."""
    if record.kind == "delivery":
        return f"{record.time}  INFO chat_module::report: delivery state={record.state}\n"
    said: Dict[str, object] = {"installation": hexed(group + record.installation)}
    if record.kind == "run":
        said["account"] = account(record.installation)
    else:
        said.update(convo=record.convo + group, epoch=record.epoch)
    if record.kind == "epoch":
        roster = ",".join(f"{hexed(group + name)}:{account(name)}" for name, _ in record.roster)
        said.update(state=hexed(record.state, 16), members=len(record.roster), roster=roster)
    if record.commit is not None:
        said.update(commit=hexed(record.commit, 16), by=hexed(group + record.by))
    if record.frame is not None:
        said["frame"] = record.frame
    if record.sender is not None:
        said["from"] = hexed(group + record.sender)
    if record.why is not None:
        said["why"] = record.why
    words = " ".join(f"{key}={value}" for key, value in said.items())
    return f"{record.time}  INFO libchat::groupv2trace: {record.kind} {words}\n"


def logged(log: Log, group: str = "") -> bytes:
    """A run of the rule's tests as the bytes its writer logs."""
    return "".join(line_of(record, group) for record in log.records).encode()


def member(name: str, epochs: int = 2, roster: str = PAIR, convo: str = CONVO, hour=10) -> Log:
    """A member's run: it starts, and holds the group at every epoch up to one."""
    log = Log(name, hour)
    log.start(name)
    for epoch in range(1, epochs + 1):
        log.epoch(name, epoch, f"X{epoch}", roster, convo)
    return log


def positions(report: dict, names: Iterable[str]) -> Dict[str, Optional[str]]:
    found = {seat["id"]: seat["position"] for seat in report["installations"]}
    return {name: found.get(hexed(name)) for name in names}


def seat_of(report: dict, name: str) -> dict:
    (seat,) = [seat for seat in report["installations"] if seat["id"] == hexed(name)]
    return seat


def history(name: str) -> dict:
    """A history of the rule's table."""
    (found,) = [history for other, history, _, _ in HISTORIES if other == name]
    return found


def share_history(served: "Served", told: dict, group: str = "") -> None:
    """Shares a history of the rule's table as its members would, the final runs marked."""
    logs, final = as_logs(told)
    for log in logs:
        served.share(logged(log, group), log.run, log.run in final)


def shape(line: str) -> str:
    """A line of the collector's log without its stamp, its port and the id of its run."""
    line = re.sub(r"^[0-9-]{10}T[0-9:]{8}Z ", "", line)
    line = re.sub(r"\b[0-9a-f]{32}\b", "ID", line)
    return re.sub(r":[0-9]+/$", ":PORT/", line)


def tree(directory: Path) -> Dict[str, Optional[bytes]]:
    """Every file under a directory with its bytes, and every directory under it."""
    return {
        str(path.relative_to(directory)): path.read_bytes() if path.is_file() else None
        for path in sorted(directory.rglob("*"))
    }


def failing(name: str, error: OSError):
    """Has the disk refuse to remove every file of one name."""
    unlink = Path.unlink

    def refused(path: Path, missing_ok: bool = False) -> None:
        if path.name == name:
            raise error
        unlink(path, missing_ok=missing_ok)

    return mock.patch.object(Path, "unlink", refused)


def staying(name: str, error: OSError):
    """Has the disk refuse to remove every directory of one name."""
    rmtree = shutil.rmtree

    def refused(path: Path) -> None:
        if path.name == name:
            raise error
        rmtree(path)

    return mock.patch("shutil.rmtree", refused)


def numbered(numbers: Iterator[int]) -> Callable[[], Tuple[str, str]]:
    """Ids and receipts a test can tell apart: `r1` and `receipt-of-r1`, and so on."""

    def mint() -> Tuple[str, str]:
        run = f"r{next(numbers)}"
        return run, f"receipt-of-{run}"

    return mint


class Clock:
    """The time a collector is told it is."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now

    def pass_days(self, days: int) -> None:
        self.now += days * DAY


class Answer(NamedTuple):
    """What a call was answered."""

    status: int
    headers: Tuple[Tuple[str, str], ...]
    body: bytes

    @property
    def said(self):
        return json.loads(self.body)


class Served:
    """A collector served on a loopback port the system picks, and the calls a test makes on it."""

    def __init__(self, data: Path, clock, mint, max_bytes: Optional[int] = None) -> None:
        self.data = data
        self.written: List[str] = []
        # Whether nobody reads the collector's own log any more, as when its pipe is closed.
        self.unread = False
        self._wrote = threading.Condition()
        self.collector = Collector(data, max_bytes, clock, mint, self._write)
        self.server = Server(("127.0.0.1", 0), self.collector, TOKEN)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, args=(0.01,))
        self._thread.start()

    def _write(self, line: str) -> None:
        if self.unread:
            raise BrokenPipeError(32, "Broken pipe")
        with self._wrote:
            self.written.append(line)
            self._wrote.notify_all()

    def wrote(self, text: str) -> bool:
        """Waits until the collector's own log holds a line with this text in it."""
        with self._wrote:
            return self._wrote.wait_for(lambda: any(text in line for line in self.written), 10)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join()

    def closing(self) -> threading.Semaphore:
        """Counts the connections the server is done with, each once what failed in it was said."""
        done = threading.Semaphore(0)
        shut = self.server.shutdown_request

        def counted(request) -> None:
            shut(request)
            done.release()

        self.server.shutdown_request = counted
        return done

    def call(self, method, path, body=None, token=TOKEN, receipt=None, headers=()) -> Answer:
        sent = dict(headers)
        if token is not None:
            sent["Authorization"] = f"Bearer {token}"
        if receipt is not None:
            sent["Forks-Receipt"] = receipt
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body, sent)
            answer = connection.getresponse()
            return Answer(answer.status, tuple(answer.getheaders()), answer.read())
        finally:
            connection.close()

    def raw(self, request: bytes) -> bytes:
        """What these bytes are answered, sent as they are and followed by nothing."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as connection:
            connection.sendall(request)
            connection.shutdown(socket.SHUT_WR)
            return b"".join(iter(lambda: connection.recv(65536), b""))

    def open(self, name: str = "saro", app: str = "tests") -> Tuple[str, str]:
        answer = self.call("POST", "/runs", json.dumps({"name": name, "app": app}))
        assert answer.status == 201, answer
        return answer.said["id"], answer.said["receipt"]

    def put(self, run, receipt, data=b"", offset=None, final=False) -> Answer:
        """Appends to a run, at the size the collector holds unless another offset is given."""
        if offset is None:
            offset = len(self.held(run))
        query = f"offset={offset}" + ("&final=1" if final else "")
        return self.call("PUT", f"/runs/{run}/log?{query}", data, receipt=receipt)

    def share(self, log: bytes, name: str = "saro", final: bool = False) -> Tuple[str, str]:
        """Opens a run and sends its whole log."""
        run, receipt = self.open(name)
        answer = self.put(run, receipt, log, 0, final)
        assert (answer.status, answer.said) == (200, {"size": len(log), "final": final}), answer
        return run, receipt

    def delete(self, run: str, receipt: Optional[str]) -> Answer:
        return self.call("DELETE", f"/runs/{run}", receipt=receipt)

    def get(self, path: str) -> Answer:
        return self.call("GET", path)

    def report(self, convo: str = CONVO) -> dict:
        answer = self.get(f"/conversations/{quote(convo, safe='')}")
        assert answer.status == 200, answer
        return answer.said

    def listed(self) -> dict:
        answer = self.get("/conversations")
        assert answer.status == 200, answer
        return answer.said

    def held(self, run: str) -> bytes:
        return (self.data / run / f"{run}.log").read_bytes()

    def run_json(self, run: str) -> dict:
        return json.loads((self.data / run / "run.json").read_text())


class Collecting(unittest.TestCase):
    """A test of a collector that keeps its runs in a directory of the test's own."""

    max_bytes: Optional[int] = None

    def setUp(self) -> None:
        self.scratch = Path(self.enter(tempfile.TemporaryDirectory()))
        self.data = self.scratch / "data"
        self.data.mkdir()
        self.clock = Clock()
        self.mint = numbered(self.numbers())
        self.served = self.serve()

    def enter(self, context):
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    def numbers(self) -> Iterator[int]:
        return itertools.count(1)

    def serve(self, data: Optional[Path] = None) -> Served:
        served = Served(data or self.data, self.clock, self.mint, self.max_bytes)
        self.addCleanup(served.stop)
        return served

    def elsewhere(self) -> Served:
        """Another collector, on a directory of its own."""
        return self.serve(Path(self.enter(tempfile.TemporaryDirectory())))

    def share_group(self, *names: str, epochs: int = 2, roster: str = PAIR) -> Dict[str, tuple]:
        """Shares a run for each member of a group that is whole, and answers each one's keys."""
        return {
            name: self.served.share(logged(member(name, epochs, roster)), name) for name in names
        }


class Tokens(Collecting):
    def setUp(self) -> None:
        super().setUp()
        self.run, self.receipt = self.served.share(logged(member("saro")))
        self.served.share(logged(member("raya")), "raya")

    def calls(self) -> List[Tuple[str, str, Optional[bytes]]]:
        """Every call, on what the collector has and on what it has not, and calls there are not."""
        size = len(self.served.held(self.run))
        return [
            ("POST", "/runs", b'{"name": "pax", "app": "tests"}'),
            ("POST", "/runs", b"no name and no app"),
            ("PUT", f"/runs/{self.run}/log?offset={size}", b"more\n"),
            ("PUT", f"/runs/{self.run}/log?offset={size}&final=1", b""),
            ("PUT", f"/runs/{self.run}/log?offset=0", b"more\n"),
            ("PUT", f"/runs/{self.run}/log", b"more\n"),
            ("PUT", "/runs/r9/log?offset=0", b""),
            ("DELETE", f"/runs/{self.run}", None),
            ("DELETE", "/runs/r9", None),
            ("GET", "/conversations", None),
            ("GET", f"/conversations/{CONVO}", None),
            ("GET", f"/conversations/{OTHER}", None),
            ("GET", f"/runs/{self.run}/log?line=1", None),
            ("GET", "/runs/r9/log?line=1", None),
            ("GET", "/", None),
            ("GET", "/page/report.js", None),
            ("GET", "/nothing", None),
            ("PATCH", "/runs", b"{}"),
            ("OPTIONS", "/conversations", None),
        ]

    def test_without_the_token_every_call_is_answered_the_same_401_and_does_nothing(self):
        strangers = [
            {},
            {"Authorization": f"Bearer {TOKEN[:-1]}"},
            {"Authorization": f"Bearer {TOKEN}s"},
            {"Authorization": f"Bearer {TOKEN[:-1]}S"},
            {"Authorization": "Bearer"},
            {"Authorization": TOKEN},
            {"Authorization": f"Basic {TOKEN}"},
            {"Authorization": f"Bearer {TOKEN} {TOKEN}"},
            {"Cookie": f"forks_token={TOKEN[:-1]}"},
            {"Cookie": f"token={TOKEN}"},
            {"Forks-Receipt": self.receipt},
        ]
        held, written = tree(self.data), list(self.served.written)
        answers = set()
        for (method, path, body), headers in itertools.product(self.calls(), strangers):
            answers.add(self.served.call(method, path, body, None, headers=headers.items()))
        for method, path, body in self.calls():
            # The base alone takes the token in its address: it is a browser's way in.
            if (method, path) != ("GET", "/"):
                in_the_address = path + ("&" if "?" in path else "?") + f"token={TOKEN}"
                answers.add(self.served.call(method, in_the_address, body, None))
        (answer,) = answers
        self.assertEqual((answer.status, answer.body), (401, NO_TOKEN))
        self.assertEqual(dict(answer.headers)["WWW-Authenticate"], "Bearer")
        self.assertEqual((tree(self.data), self.served.written), (held, written))

    def test_without_the_token_a_body_the_collector_would_refuse_is_answered_the_same(self):
        refused = self.served.raw(b"GET /conversations HTTP/1.1\r\n\r\n")
        self.assertTrue(refused.startswith(b"HTTP/1.0 401 "))
        self.assertTrue(refused.endswith(b"\r\n\r\n" + NO_TOKEN))
        for length in ("", "Content-Length: many\r\n", f"Content-Length: {MAX_BODY + 1}\r\n"):
            with self.subTest(length=length):
                request = f"PUT /runs/{self.run}/log?offset=0 HTTP/1.1\r\n{length}\r\n"
                self.assertEqual(self.served.raw(request.encode()), refused)
        headers_alone = self.served.raw(b"HEAD /conversations HTTP/1.1\r\n\r\n")
        self.assertEqual(headers_alone + NO_TOKEN, refused)

    def test_with_the_token_a_call_is_answered(self):
        self.assertEqual(self.served.get("/conversations").status, 200)
        for scheme in ("bearer", "BEARER"):
            headers = {"Authorization": f"{scheme} {TOKEN}"}
            answer = self.served.call("GET", "/conversations", token=None, headers=headers.items())
            self.assertEqual(answer.status, 200)

    def test_a_call_the_collector_does_not_have_answers_404(self):
        calls = [
            ("GET", "/nothing"),
            ("GET", "/page"),
            ("POST", "/"),
            ("PUT", "/page/report.js"),
            ("GET", "/runs"),
            ("GET", f"/runs/{self.run}"),
            ("GET", f"/runs/{self.run}/log/more?line=1"),
            ("GET", f"/conversations/{CONVO}/more"),
            ("GET", "/conversations/"),
            ("POST", "/runs/"),
            ("POST", "/conversations"),
            ("PUT", f"/runs/{self.run}"),
            ("DELETE", f"/runs/{self.run}/log"),
            ("DELETE", "/runs"),
            ("PATCH", "/runs"),
            ("OPTIONS", "/conversations"),
        ]
        held = tree(self.data)
        for method, path in calls:
            with self.subTest(method=method, path=path):
                answer = self.served.call(method, path, b"" if method in ("POST", "PUT") else None)
                self.assertEqual((answer.status, list(answer.said)), (404, ["error"]))
        for path in ("/conversations", "/"):
            answer = self.served.call("HEAD", path)
            self.assertEqual((answer.status, answer.body), (404, b""))
        self.assertEqual(tree(self.data), held)

    def test_an_answer_with_a_body_is_json_and_an_error_says_one_thing(self):
        size = len(self.served.held(self.run))
        answers = {
            200: self.served.get("/conversations"),
            201: self.served.call("POST", "/runs", b'{"name": "pax", "app": "tests"}'),
            400: self.served.call("POST", "/runs", b"no name and no app"),
            401: self.served.call("GET", "/conversations", token=None),
            403: self.served.put(self.run, None),
            404: self.served.get("/nothing"),
            409: self.served.put(self.run, self.receipt, b"more\n", 0),
        }
        for status, answer in answers.items():
            with self.subTest(status=status):
                self.assertEqual(answer.status, status)
                kind = dict(answer.headers)["Content-Type"]
                self.assertEqual(kind, "application/json; charset=utf-8")
                self.assertEqual(dict(answer.headers)["Content-Length"], str(len(answer.body)))
                if status in (400, 401, 403, 404):
                    (error,) = answer.said.items()
                    self.assertEqual(error[0], "error")
                    self.assertRegex(error[1], r"^[a-z][^.\n]+$")
        self.assertEqual(answers[409].said, {"size": size, "final": False})
        deleted = self.served.delete(self.run, self.receipt)
        self.assertEqual(
            (deleted.status, deleted.headers, deleted.body), (204, ON_EVERY_ANSWER, b"")
        )

    def test_a_request_that_is_not_http_is_answered_without_its_own_words(self):
        answer = self.served.raw(b"what a log says\r\n\r\n")
        self.assertTrue(answer.endswith(b'{"error": "the request cannot be read"}'))
        self.assertNotIn(b"what a log says", answer)

    def test_a_connection_that_falls_silent_is_closed_unanswered(self):
        cut_short = (
            f"PUT /runs/{self.run}/log?offset=0 HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\n"
            f"Forks-Receipt: {self.receipt}\r\nContent-Length: 10\r\n\r\nmore\n"
        )
        held, written = tree(self.data), list(self.served.written)
        self.assertEqual(Handler.timeout, 30)
        said = io.StringIO()
        with mock.patch.object(Handler, "timeout", 0.05), contextlib.redirect_stderr(said):
            for sent in (b"", b"GET /conversations HTTP/1.1\r\n", cut_short.encode()):
                with self.subTest(sent=sent):
                    address = ("127.0.0.1", self.served.port)
                    with socket.create_connection(address, timeout=10) as connection:
                        connection.sendall(sent)
                        self.assertEqual(connection.recv(65536), b"")
        self.assertEqual((tree(self.data), self.served.written), (held, written))
        self.assertEqual(said.getvalue(), "")


class Reads:
    """What a handler reads a request from, noting each read of a body.

    A read is noted as the bytes it took and the seconds the connection was given to wait for
    them. After each the handler is held up for `pause` seconds, as a box with much to do is.
    """

    def __init__(self, handler: Handler, took: List[Tuple[int, float]], pause: float) -> None:
        self._rfile, self._connection = handler.rfile, handler.connection
        self._took, self._pause = took, pause

    def __getattr__(self, name: str):
        return getattr(self._rfile, name)

    def read(self, size: int) -> bytes:
        return self._noted(self._rfile.read, size)

    def read1(self, size: int) -> bytes:
        return self._noted(self._rfile.read1, size)

    def _noted(self, read: Callable[[int], bytes], size: int) -> bytes:
        wait = self._connection.gettimeout()
        piece = read(size)
        self._took.append((len(piece), wait))
        time.sleep(self._pause)
        return piece


class Peers(Collecting):
    """Peers that send slowly, read slowly, or go."""

    def setUp(self) -> None:
        super().setUp()
        self.run, self.receipt = self.served.share(logged(member("saro")))
        self.put = f"PUT /runs/{self.run}/log?offset=0 HTTP/1.1\r\n"
        self.keys = f"Authorization: Bearer {TOKEN}\r\nForks-Receipt: {self.receipt}\r\n"

    def connect(self) -> socket.socket:
        connection = socket.create_connection(("127.0.0.1", self.served.port), timeout=10)
        self.addCleanup(connection.close)
        return connection

    def reads(self, pause: float = 0) -> List[Tuple[int, float]]:
        """Has every handler from here on note the reads of its request's body."""
        took: List[Tuple[int, float]] = []
        setup = Handler.setup

        def watched(handler: Handler) -> None:
            setup(handler)
            handler.rfile = Reads(handler, took, pause)

        self.enter(mock.patch.object(Handler, "setup", watched))
        return took

    def test_without_the_token_a_body_is_read_in_pieces_and_none_of_it_is_kept(self):
        took = self.reads()
        answered_under: List[float] = []
        send = Handler._send

        def sent(handler: Handler, status: int, said: object) -> None:
            answered_under.append(handler.connection.gettimeout())
            send(handler, status, said)

        held, written = tree(self.data), list(self.served.written)
        path = f"/runs/{self.run}/log?offset=0"
        with mock.patch.object(Handler, "_send", sent):
            answer = self.served.call("PUT", path, bytes(MAX_BODY), token=None)
        self.assertEqual((answer.status, answer.body), (401, NO_TOKEN))
        # All of it was read, so that its sender came to read the answer, and no more than
        # 64 KiB of it at a time.
        self.assertEqual(sum(size for size, _ in took), MAX_BODY)
        self.assertLessEqual(max(size for size, _ in took), 64 * 1024)
        # Each read was given what was left of the 30 seconds a request has in all, and the
        # answer the 30 seconds of any.
        waits = [wait for _, wait in took]
        self.assertEqual(waits, sorted(waits, reverse=True))
        self.assertTrue(30 >= waits[0] > waits[-1] > 20, waits)
        self.assertEqual(answered_under, [30])
        self.assertEqual((tree(self.data), self.served.written), (held, written))

    def test_without_the_token_a_read_that_ends_when_a_request_is_over_is_the_last(self):
        took = self.reads(pause=0.3)
        with mock.patch.object(Handler, "timeout", 0.2):
            answer, _ = self.answered(self.put + "Content-Length: 1000\r\n\r\none", None)
        self.assertTrue(answer.startswith(b"HTTP/1.0 401 "), answer)
        self.assertEqual([size for size, _ in took], [3])

    def test_without_the_token_a_body_of_a_length_that_is_refused_is_left_unread(self):
        took = self.reads()
        for length in (MAX_BODY + 1, "many", "3\r\nContent-Length: 3"):
            with self.subTest(length=length):
                request = f"{self.put}Content-Length: {length}\r\n\r\none"
                answer = self.served.raw(request.encode())
                self.assertTrue(answer.startswith(b"HTTP/1.0 401 "), answer)
                self.assertTrue(answer.endswith(b"\r\n\r\n" + NO_TOKEN), answer)
        self.assertEqual(took, [])

    def test_without_the_token_a_body_cut_short_is_answered_at_once(self):
        started = time.monotonic()
        answer = self.served.raw(f"{self.put}Content-Length: 1000\r\n\r\none".encode())
        self.assertTrue(answer.startswith(b"HTTP/1.0 401 "), answer)
        self.assertLess(time.monotonic() - started, 5)

    def answered(self, request: str, pause: Optional[float]) -> Tuple[bytes, float]:
        """What a request is answered, and when, whose sender goes on with a byte at a time.

        With no pause the sender says nothing more.
        """
        connection = self.connect()
        connection.settimeout(pause or 10)
        answer, started = b"", time.monotonic()
        connection.sendall(request.encode())
        while time.monotonic() - started < 10:
            try:
                piece = connection.recv(65536)
            except socket.timeout:
                connection.sendall(b".")
                continue
            except ConnectionError:
                # The answer came, and then a byte more than the collector waited for.
                break
            if not piece:
                break
            answer += piece
        return answer, time.monotonic() - started

    def test_without_the_token_a_body_that_does_not_end_is_answered_when_a_request_is_over(self):
        request = self.put + "Content-Length: 1000\r\n\r\none"
        held, written = tree(self.data), list(self.served.written)
        with mock.patch.object(Handler, "timeout", 0.3):
            for pause in (0.02, None):
                with self.subTest(pause=pause):
                    answer, after = self.answered(request, pause)
                    self.assertTrue(answer.startswith(b"HTTP/1.0 401 "), answer)
                    self.assertTrue(answer.endswith(b"\r\n\r\n" + NO_TOKEN), answer)
                    self.assertTrue(0.3 <= after < 5, after)
        self.assertEqual((tree(self.data), self.served.written), (held, written))

    def test_with_the_token_a_body_that_comes_slowly_is_waited_for(self):
        request = self.put.replace("offset=0", f"offset={len(self.served.held(self.run))}")
        request += self.keys + "Content-Length: 28\r\n\r\none"
        with mock.patch.object(Handler, "timeout", 0.3):
            # A byte every 20 ms: longer in all than a silence may last, and never silent.
            answer, after = self.answered(request, 0.02)
        self.assertTrue(answer.startswith(b"HTTP/1.0 200 "), answer)
        self.assertGreater(after, 0.3)
        self.assertTrue(self.served.held(self.run).endswith(b"one" + b"." * 25))

    def test_an_answer_is_sent_for_as_long_as_its_reader_goes_on_reading(self):
        line = self.served.held(self.run).count(b"\n") + 1
        self.served.put(self.run, self.receipt, b"y" * (2 * 1024 * 1024) + b"\n")
        setup = Handler.setup

        def on_a_slow_link(handler: Handler) -> None:
            setup(handler)
            handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)

        slow = mock.patch.object(Handler, "setup", on_a_slow_link)
        reader = socket.socket()
        self.addCleanup(reader.close)
        reader.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        reader.settimeout(10)
        with slow, mock.patch.object(Handler, "timeout", 0.5):
            reader.connect(("127.0.0.1", self.served.port))
            asked = f"GET /runs/{self.run}/log?line={line} HTTP/1.1\r\n"
            reader.sendall(f"{asked}Authorization: Bearer {TOKEN}\r\n\r\n".encode())
            answer, started = bytearray(), time.monotonic()
            # 16 KiB every 8 ms: a second for the whole, and no silence near half of one.
            for piece in iter(lambda: reader.recv(16384), b""):
                answer += piece
                time.sleep(0.008)
            after = time.monotonic() - started
        head, _, body = bytes(answer).partition(b"\r\n\r\n")
        self.assertGreater(after, 0.5)
        self.assertEqual(len(body), int(re.search(rb"Content-Length: (\d+)", head)[1]))
        self.assertEqual(len(json.loads(body)["lines"][-1]["text"]), 2 * 1024 * 1024)

    def test_a_peer_that_goes_is_no_line_of_the_log(self):
        closed = self.served.closing()
        held, written = tree(self.data), list(self.served.written)
        for keys in ("", self.keys):
            with self.subTest(keys=bool(keys)):
                connection = self.connect()
                connection.sendall(f"{self.put}{keys}Content-Length: 100\r\n\r\none".encode())
                # Closed with a reset, as a peer that is killed or cut off goes.
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                connection.close()
                self.assertTrue(closed.acquire(timeout=10))
        self.assertEqual((tree(self.data), self.served.written), (held, written))

    def test_only_a_call_with_the_token_fails_in_the_log(self):
        closed = self.served.closing()
        written = list(self.served.written)
        fails = mock.patch.object(Handler, "_send", side_effect=ValueError("what a log says"))
        with fails:
            for token, lines in ((None, 0), (TOKEN, 1)):
                with self.assertRaises(http.client.HTTPException):
                    self.served.call("GET", "/conversations", token=token)
                self.assertTrue(closed.acquire(timeout=10))
                self.assertEqual(len(self.served.written), len(written) + lines)
        self.assertEqual(self.served.written[-1], f"{stamp(START)} a call failed: ValueError")
        # What fails before the token is read is nobody's call yet.
        unread = mock.patch.object(Handler, "parse_request", side_effect=ValueError("early"))
        with unread, self.assertRaises(http.client.HTTPException):
            self.served.listed()
        self.assertTrue(closed.acquire(timeout=10))
        self.assertEqual(len(self.served.written), len(written) + 1)


class Opening(Collecting):
    def test_a_run_is_opened_empty_and_not_final(self):
        answer = self.served.call("POST", "/runs", b'{"name": "saro", "app": "tests"}')
        self.assertEqual(answer.status, 201)
        self.assertEqual(answer.said, {"id": "r1", "receipt": "receipt-of-r1"})
        self.assertEqual(sorted(tree(self.data)), ["r1", "r1/r1.log", "r1/run.json"])
        self.assertEqual(self.served.held("r1"), b"")
        self.assertEqual(
            self.served.run_json("r1"),
            {
                "name": "saro",
                "app": "tests",
                "receipt": hashlib.sha256(b"receipt-of-r1").hexdigest(),
                "final": False,
                "heard": "2026-10-09T12:00:00Z",
            },
        )
        answer = self.served.put("r1", "receipt-of-r1")
        self.assertEqual((answer.status, answer.said), (200, {"size": 0, "final": False}))

    def test_the_receipt_is_answered_once_and_not_kept(self):
        _, receipt = self.served.open()
        for held in tree(self.data).values():
            self.assertNotIn(receipt.encode(), held or b"")

    def test_a_body_that_is_not_a_name_and_an_app_answers_400_and_opens_nothing(self):
        bodies = [
            b"",
            b"saro",
            b"null",
            b'"saro"',
            b'["saro", "tests"]',
            b'{"name": "saro"}',
            b'{"app": "tests"}',
            b'{"name": "", "app": "tests"}',
            b'{"name": "saro", "app": "  \\n "}',
            b'{"name": 7, "app": "tests"}',
            b'{"name": "saro", "app": ["tests"]}',
            b'{"name": "saro", "app": null}',
            json.dumps({"name": "s" * 65, "app": "tests"}).encode(),
            json.dumps({"name": "saro", "app": "t" * 65}).encode(),
            b'{"name": "saro", "app": "tests"',
            b"\xff\xfe\xfd",
            b"[" * 100_000,
        ]
        for body in bodies:
            with self.subTest(body=body[:40]):
                answer = self.served.call("POST", "/runs", body)
                self.assertEqual((answer.status, list(answer.said)), (400, ["error"]))
        self.assertEqual((tree(self.data), self.served.written), ({}, []))

    def test_a_name_and_an_app_are_trimmed_and_may_hold_1_to_64_characters(self):
        said = {"name": "  " + "s" * 64 + "\n", "app": "\tt ", "more": [1]}
        answer = self.served.call("POST", "/runs", json.dumps(said))
        self.assertEqual(answer.status, 201)
        held = self.served.run_json("r1")
        self.assertEqual((held["name"], held["app"]), ("s" * 64, "t"))

    def test_an_id_and_a_receipt_of_the_collectors_own_making_are_opaque_words(self):
        self.mint = mint
        served = self.elsewhere()
        first, second = served.open(), served.open("raya")
        for word in (*first, *second):
            self.assertRegex(word, r"^[A-Za-z0-9_-]{32,64}$")
        self.assertEqual(len({*first, *second}), 4)
        kept = sorted(path.name for path in served.data.iterdir())
        self.assertEqual(kept, sorted([first[0], second[0]]))


class Appending(Collecting):
    def setUp(self) -> None:
        super().setUp()
        self.run, self.receipt = self.served.open()

    def put(self, data: bytes = b"", offset: Optional[int] = None, final: bool = False) -> Answer:
        return self.served.put(self.run, self.receipt, data, offset, final)

    def test_bytes_are_appended_at_the_size_held(self):
        answer = self.put(b"one\ntw", 0)
        self.assertEqual((answer.status, answer.said), (200, {"size": 6, "final": False}))
        answer = self.put(b"o\n", 6)
        self.assertEqual((answer.status, answer.said), (200, {"size": 8, "final": False}))
        answer = self.put(b"", 8)
        self.assertEqual((answer.status, answer.said), (200, {"size": 8, "final": False}))
        self.assertEqual(self.served.held(self.run), b"one\ntwo\n")

    def test_a_log_is_held_byte_for_byte(self):
        log = bytes(range(256)) * 3 + b"\x1b[32mgreen\x1b[0m\r\n\xff\xfe and no line break"
        self.put(log[:300], 0)
        self.put(log[300:], 300)
        self.assertEqual(self.served.held(self.run), log)

    def test_a_wrong_offset_answers_409_with_the_size_held_and_appends_nothing(self):
        self.put(b"one\n", 0)
        for offset, final in itertools.product((0, 3, 5, 10**19), (False, True)):
            for data in (b"two\n", b""):
                with self.subTest(offset=offset, final=final, data=data):
                    answer = self.put(data, offset, final)
                    self.assertEqual(answer.status, 409)
                    self.assertEqual(answer.said, {"size": 4, "final": False})
        self.assertEqual(self.served.held(self.run), b"one\n")
        self.assertIs(self.served.run_json(self.run)["final"], False)
        self.assertEqual(self.put(b"", 4).said, {"size": 4, "final": False})

    def test_an_offset_that_is_not_one_number_answers_400(self):
        queries = [
            "",
            "?final=1",
            "?offset=",
            "?offset=-1",
            "?offset=+0",
            "?offset=0.0",
            "?offset=0x0",
            "?offset=%D9%A0",
            "?offset=" + "0" * 21,
            "?offset=0&offset=0",
            "?offset=0&final=0",
            "?offset=0&final=",
            "?offset=0&final=true",
            "?offset=0&final=1&final=1",
        ]
        for query in queries:
            with self.subTest(query=query):
                path = f"/runs/{self.run}/log{query}"
                answer = self.served.call("PUT", path, b"one\n", receipt=self.receipt)
                self.assertEqual((answer.status, list(answer.said)), (400, ["error"]))
        self.assertEqual(self.served.held(self.run), b"")
        self.assertIs(self.served.run_json(self.run)["final"], False)

    def test_a_write_without_the_runs_receipt_is_refused(self):
        other, other_receipt = self.served.open("raya")
        self.put(b"one\n", 0)
        held = tree(self.data)
        for receipt in (None, "", other_receipt, self.receipt + "s", self.receipt[:-1]):
            with self.subTest(receipt=receipt):
                answers = [
                    self.served.put(self.run, receipt, b"two\n", 4),
                    self.served.put(self.run, receipt, b"", 4, final=True),
                    self.served.put(self.run, receipt, b"two\n", 0),
                    self.served.delete(self.run, receipt),
                ]
                for answer in answers:
                    self.assertEqual((answer.status, list(answer.said)), (403, ["error"]))
        self.assertEqual(tree(self.data), held)
        self.assertEqual(self.put(b"", 4).said, {"size": 4, "final": False})
        self.assertEqual(self.served.delete(other, other_receipt).status, 204)

    def test_a_final_run_takes_no_more_bytes_and_its_mark_may_be_repeated(self):
        answer = self.put(b"one\n", 0, final=True)
        self.assertEqual((answer.status, answer.said), (200, {"size": 4, "final": True}))
        self.assertIs(self.served.run_json(self.run)["final"], True)
        for final in (False, True):
            answer = self.put(b"two\n", 4, final)
            self.assertEqual((answer.status, answer.said), (409, {"size": 4, "final": True}))
            answer = self.put(b"", 4, final)
            self.assertEqual((answer.status, answer.said), (200, {"size": 4, "final": True}))
        self.assertEqual(self.served.held(self.run), b"one\n")

    def test_a_mark_may_come_with_the_last_bytes_or_after_them(self):
        self.put(b"one\n", 0)
        answer = self.put(b"", 4, final=True)
        self.assertEqual((answer.status, answer.said), (200, {"size": 4, "final": True}))
        self.assertEqual(self.served.held(self.run), b"one\n")

    def test_a_run_the_collector_does_not_have_answers_404(self):
        answers = [
            self.served.put("r9", self.receipt, b"one\n", 0),
            self.served.get("/runs/r9/log?line=1"),
            self.served.delete("r9", self.receipt),
        ]
        for answer in answers:
            self.assertEqual((answer.status, list(answer.said)), (404, ["error"]))

    def test_an_id_the_collector_did_not_make_answers_404_and_touches_nothing(self):
        outside = self.scratch / "outside"
        outside.mkdir()
        (outside / "outside.log").write_bytes(b"what a log says\n")
        (outside / "run.json").write_bytes((self.data / self.run / "run.json").read_bytes())
        self.put(b"one\n", 0)
        ids = [
            "",
            ".",
            "..",
            "../outside",
            "..%2Foutside",
            "%2E%2E%2Foutside",
            "..%5Coutside",
            f"../data/{self.run}",
            f"{self.run}/../{self.run}",
            f"{self.run}%2F..%2F{self.run}",
            f"{self.run}%2F",
            f"{self.run}%00",
            f"{self.run}.log",
            f"%20{self.run}",
            quote(str(outside), safe=""),
            "r" * 5000,
        ]
        held = tree(self.scratch)
        for run in ids:
            with self.subTest(run=run[:40]):
                answers = [
                    self.served.put(run, self.receipt, b"two\n", 0),
                    self.served.put(run, self.receipt, b"", 4, final=True),
                    self.served.get(f"/runs/{run}/log?line=1"),
                    self.served.delete(run, self.receipt),
                ]
                for answer in answers:
                    self.assertEqual((answer.status, list(answer.said)), (404, ["error"]))
        self.assertEqual(tree(self.scratch), held)

    def test_a_body_of_4_mib_is_appended_and_a_longer_one_answers_413(self):
        answer = self.put(bytes(MAX_BODY), 0)
        self.assertEqual((answer.status, answer.said), (200, {"size": MAX_BODY, "final": False}))
        for length in (MAX_BODY + 1, 10**19):
            request = (
                f"PUT /runs/{self.run}/log?offset={MAX_BODY} HTTP/1.1\r\n"
                f"Authorization: Bearer {TOKEN}\r\nForks-Receipt: {self.receipt}\r\n"
                f"Content-Length: {length}\r\n\r\n"
            )
            answer = self.served.raw(request.encode())
            self.assertTrue(answer.startswith(b"HTTP/1.0 413 "), answer)
        self.assertEqual(len(self.served.held(self.run)), MAX_BODY)

    def raw_put(self, length: str, body: bytes = b"") -> bytes:
        request = (
            f"PUT /runs/{self.run}/log?offset=0 HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\n"
            f"Forks-Receipt: {self.receipt}\r\n{length}\r\n"
        )
        return self.served.raw(request.encode() + body)

    def test_a_body_that_does_not_say_its_length_is_refused(self):
        for length in ("", "Transfer-Encoding: chunked\r\n"):
            with self.subTest(length=length):
                self.assertTrue(self.raw_put(length).startswith(b"HTTP/1.0 411 "))
                request = f"POST /runs HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\n{length}\r\n"
                self.assertTrue(self.served.raw(request.encode()).startswith(b"HTTP/1.0 411 "))
        for length in ("many", "-4", "+4", "4.0", "4, 4", "0x4", "4" * 21):
            with self.subTest(length=length):
                answer = self.raw_put(f"Content-Length: {length}\r\n")
                self.assertTrue(answer.startswith(b"HTTP/1.0 400 "), answer)
        self.assertEqual(sorted(tree(self.data)), ["r1", "r1/r1.log", "r1/run.json"])
        self.assertEqual(self.served.held(self.run), b"")

    def test_a_body_that_says_its_length_twice_or_under_a_transfer_coding_is_refused(self):
        coded, length = "Transfer-Encoding: chunked\r\n", "Content-Length: 14\r\n"
        said = [(coded + length, b"4\r\none\n\r\n0\r\n\r\n"), (length + coded, b"one\n" * 4)]
        for first, second in ((4, 0), (0, 4), (4, 4)):
            said.append((f"Content-Length: {first}\r\nContent-Length: {second}\r\n", b"one\n"))
        for lengths, body in said:
            with self.subTest(lengths=lengths):
                answer = self.raw_put(lengths, body)
                self.assertTrue(answer.startswith(b"HTTP/1.0 400 "), answer)
        self.assertEqual(self.served.held(self.run), b"")

    def test_a_body_cut_short_of_its_length_appends_nothing(self):
        answer = self.raw_put("Content-Length: 10\r\n", b"one\n")
        self.assertTrue(answer.startswith(b"HTTP/1.0 400 "))
        self.assertEqual(self.served.held(self.run), b"")
        answer = self.raw_put("Content-Length: 4\r\n", b"one\n")
        self.assertTrue(answer.startswith(b"HTTP/1.0 200 "))
        self.assertEqual(self.served.held(self.run), b"one\n")

    def test_bytes_that_could_not_all_be_written_are_not_held(self):
        try:
            import resource
        except ImportError:
            self.skipTest("this system has no limit on the size of a file to set")
        self.put(b"one\n", 0)
        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        # The system then writes the first two of the four bytes, and fails the rest.
        resource.setrlimit(resource.RLIMIT_FSIZE, (6, hard))
        try:
            with self.assertRaises((http.client.HTTPException, OSError)):
                self.put(b"two\n", 4)
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
        self.assertEqual(self.served.held(self.run), b"one\n")
        self.assertRegex(self.served.written[-1], r"Z a call failed: \[Errno \d+\] ")
        answer = self.put(b"two\n", 4)
        self.assertEqual((answer.status, answer.said), (200, {"size": 8, "final": False}))
        self.assertEqual(self.served.held(self.run), b"one\ntwo\n")


class Capped(Collecting):
    max_bytes = 10

    def test_an_append_past_the_most_bytes_to_hold_answers_507_and_appends_nothing(self):
        saro, raya = self.served.open(), self.served.open("raya")
        self.assertEqual(self.served.put(*saro, b"1234567\n").status, 200)
        answer = self.served.put(*raya, b"123")
        self.assertEqual((answer.status, list(answer.said)), (507, ["error"]))
        self.assertEqual(self.served.held(raya[0]), b"")
        self.assertEqual(self.served.put(*raya, b"12").status, 200)
        self.assertEqual(self.served.put(*saro, b"8").status, 507)
        self.assertEqual(self.served.put(*saro, b"8", final=True).status, 507)
        self.assertIs(self.served.run_json(saro[0])["final"], False)
        self.assertEqual(self.served.held(saro[0]) + self.served.held(raya[0]), b"1234567\n12")

    def test_at_the_most_bytes_a_run_is_still_heard_from_marked_and_deleted(self):
        saro, raya = self.served.open(), self.served.open("raya")
        self.served.put(*saro, b"123456789\n")
        self.assertEqual(self.served.put(*saro).said, {"size": 10, "final": False})
        self.assertEqual(self.served.put(*saro, final=True).said, {"size": 10, "final": True})
        self.assertEqual(self.served.put(*raya, b"1").status, 507)
        self.assertEqual(self.served.delete(*saro).status, 204)
        self.assertEqual(self.served.put(*raya, b"1234567890").status, 200)

    def test_a_run_whose_bytes_the_collector_cannot_take_is_heard_from_all_the_same(self):
        saro, raya = self.served.open(), self.served.open("raya")
        self.served.put(*saro, b"12345678\n")
        self.served.put(*raya, b"\n")
        for _ in range(3):
            self.clock.pass_days(29)
            self.assertEqual(self.served.put(*saro).status, 200)
            answer = self.served.put(*raya, b"more\n", final=True)
            self.assertEqual((answer.status, list(answer.said)), (507, ["error"]))
        held = self.served.run_json(raya[0])
        self.assertEqual((held["heard"], held["final"]), (stamp(self.clock.now), False))
        self.assertEqual(self.served.held(raya[0]), b"\n")
        said = f"{stamp(self.clock.now)} run {raya[0]} could not take 5 bytes, holds 1"
        self.assertEqual(self.served.written[-1], said)
        self.clock.pass_days(29)
        self.assertEqual(self.served.put(*raya, b"more\n").status, 507)
        self.clock.pass_days(30)
        self.assertEqual(self.served.put(*raya, b"more\n").status, 404)

    def test_a_collector_that_holds_more_than_it_may_takes_a_mark_and_no_byte(self):
        saro = self.served.open()
        self.served.put(*saro, b"123456789\n")
        self.max_bytes = 5
        smaller = self.serve()
        self.assertEqual(smaller.put(*saro, b"1").status, 507)
        self.assertEqual(smaller.put(*saro).said, {"size": 10, "final": False})
        self.assertEqual(smaller.put(*saro, final=True).said, {"size": 10, "final": True})


class Deleting(Collecting):
    def test_a_delete_removes_the_log_and_its_records(self):
        runs = self.share_group("saro", "raya")
        self.assertEqual(positions(self.served.report(), runs), dict(saro="tip", raya="tip"))
        answer = self.served.delete(*runs["saro"])
        self.assertEqual((answer.status, answer.body), (204, b""))
        self.assertEqual(sorted(path.name for path in self.data.iterdir()), [runs["raya"][0]])
        report = self.served.report()
        self.assertEqual(positions(report, runs), dict(saro="no data", raya="tip"))
        self.assertEqual([run["id"] for run in report["runs"]], [runs["raya"][0]])
        self.assertEqual(self.served.listed()["lines"]["records"], 3)
        run, receipt = runs["saro"]
        answers = [
            self.served.put(run, receipt, b"", 0),
            self.served.get(f"/runs/{run}/log?line=1"),
            self.served.delete(run, receipt),
        ]
        for answer in answers:
            self.assertEqual((answer.status, list(answer.said)), (404, ["error"]))

    def test_a_conversation_goes_with_its_last_log(self):
        runs = self.share_group("saro", "raya")
        other = self.served.share(logged(member("pax", roster="pax", convo=OTHER)), "pax")
        self.served.delete(*runs["saro"])
        self.assertEqual(self.served.get(f"/conversations/{CONVO}").status, 200)
        self.served.delete(*runs["raya"])
        answer = self.served.get(f"/conversations/{CONVO}")
        self.assertEqual((answer.status, list(answer.said)), (404, ["error"]))
        self.assertEqual([entry["id"] for entry in self.served.listed()["conversations"]], [OTHER])
        self.served.delete(*other)
        self.assertEqual(
            self.served.listed(),
            {
                "lines": {"lines": 0, "records": 0, "unstamped": 0, "malformed": 0, "unknown": 0},
                "conversations": [],
            },
        )
        self.assertEqual(tree(self.data), {})


class Lines(Collecting):
    def test_a_chunk_that_ends_inside_a_line_yields_no_record_until_the_rest_arrives(self):
        self.served.share(logged(member("raya", epochs=1)), "raya")
        log = logged(member("saro", epochs=1))
        cut = log.index(b"\n") + 40
        run, receipt = self.served.open()

        def read() -> Tuple[Optional[str], Optional[int], int]:
            saro = seat_of(self.served.report(), "saro")
            return saro["position"], saro["epoch"], self.served.listed()["lines"]["lines"]

        self.served.put(run, receipt, log[:cut])
        self.assertEqual(read(), ("never joined", None, 3))
        self.served.put(run, receipt, log[cut:-1])
        self.assertEqual(read(), ("never joined", None, 3))
        self.served.put(run, receipt, b"\n")
        self.assertEqual(read(), ("tip", 1, 4))

    def test_a_log_reads_the_same_sent_whole_and_in_pieces_of_every_size(self):
        self.served.share(logged(member("raya", epochs=1)), "raya")
        start, epoch = logged(member("saro", epochs=1, roster="saro")).splitlines(keepends=True)
        log = start + b"  at a line without a stamp\r\n" + epoch + b"a line still being wr"

        def read(run: str) -> Tuple[dict, dict]:
            """The report and the count of lines, without the id of the run that was sent."""
            report = self.served.get(f"/conversations/{CONVO}").body
            named = report.replace(f'"{run}"'.encode(), b'"the run"')
            return json.loads(named), self.served.listed()["lines"]

        run, receipt = self.served.share(log)
        whole = read(run)
        self.assertEqual(seat_of(whole[0], "saro")["history"][0]["at"]["line"], 3)
        self.assertEqual(whole[1], {**whole[1], "lines": 5, "records": 4, "unstamped": 1})
        self.served.delete(run, receipt)
        for size in range(1, len(log) + 1):
            run, receipt = self.served.open()
            for offset in range(0, len(log), size):
                self.served.put(run, receipt, log[offset : offset + size], offset)
            self.assertEqual(self.served.held(run), log)
            self.assertEqual(read(run), whole, size)
            self.served.delete(run, receipt)


class Excerpts(Collecting):
    def setUp(self) -> None:
        super().setUp()
        log = b"".join(b"line %d\n" % number for number in range(1, 11)) + b"line 11, unfinished"
        self.run, _ = self.served.share(log)

    def around(self, line: int) -> List[Tuple[int, str]]:
        answer = self.served.get(f"/runs/{self.run}/log?line={line}")
        self.assertEqual((answer.status, list(answer.said)), (200, ["lines"]))
        for entry in answer.said["lines"]:
            self.assertEqual(list(entry), ["line", "text"])
        return [(entry["line"], entry["text"]) for entry in answer.said["lines"]]

    def test_the_lines_around_a_line_are_the_two_before_it_and_the_three_after(self):
        self.assertEqual(self.around(5), [(number, f"line {number}") for number in range(3, 9)])

    def test_there_are_fewer_at_either_end_and_an_unfinished_line_is_none(self):
        self.assertEqual(self.around(1), [(number, f"line {number}") for number in range(1, 5)])
        self.assertEqual(self.around(2), [(number, f"line {number}") for number in range(1, 6)])
        self.assertEqual(self.around(9), [(number, f"line {number}") for number in range(7, 11)])
        self.assertEqual(self.around(12), [(10, "line 10")])
        self.assertEqual(self.around(13), [])
        self.assertEqual(self.around(10**19), [])
        one_line, _ = self.served.share(b"\n", "raya")
        answer = self.served.get(f"/runs/{one_line}/log?line=1")
        self.assertEqual(answer.said, {"lines": [{"line": 1, "text": ""}]})

    def test_a_log_that_went_while_its_lines_were_asked_for_answers_404(self):
        (self.data / self.run / f"{self.run}.log").unlink()
        answer = self.served.get(f"/runs/{self.run}/log?line=1")
        self.assertEqual((answer.status, list(answer.said)), (404, ["error"]))

    def test_a_line_is_the_text_a_record_is_read_from(self):
        log = b"\x1b[2m2026\x1b[0m \x1b[32m INFO\x1b[0m coloured\r\nnot \xff UTF-8\n\n"
        run, _ = self.served.share(log, "raya")
        answer = self.served.get(f"/runs/{run}/log?line=1")
        self.assertEqual(
            answer.said["lines"],
            [
                {"line": 1, "text": "2026  INFO coloured"},
                {"line": 2, "text": "not \ufffd UTF-8"},
                {"line": 3, "text": ""},
            ],
        )

    def test_the_number_of_a_records_line_finds_that_line(self):
        log = b"a line that is no record\n\n" + logged(member("pax", roster="pax", convo=OTHER))
        run, receipt = self.served.open("pax")
        self.served.put(run, receipt, log[:70])
        self.served.put(run, receipt, log[70:])
        for step in seat_of(self.served.report(OTHER), "pax")["history"]:
            at = step["at"]
            answer = self.served.get(f"/runs/{at['run']}/log?line={at['line']}")
            (text,) = [e["text"] for e in answer.said["lines"] if e["line"] == at["line"]]
            self.assertTrue(text.startswith(at["time"]))
            self.assertIn(f" epoch={step['epoch']} ", text)

    def test_a_line_that_is_not_one_number_from_1_answers_400(self):
        for query in ("", "?line=", "?line=0", "?line=-1", "?line=one", "?line=1&line=2"):
            with self.subTest(query=query):
                answer = self.served.get(f"/runs/{self.run}/log{query}")
                self.assertEqual((answer.status, list(answer.said)), (400, ["error"]))


class Reports(Collecting):
    def read(self, name: str) -> dict:
        share_history(self.served, history(name))
        return self.served.report()

    def test_every_history_of_the_rules_table_reads_the_same_through_the_calls(self):
        for name, told, verdict, expected in HISTORIES:
            if name in KEPT:
                # Read under ThirtyDays, where a log goes and a line is kept.
                continue
            with self.subTest(history=name):
                served = self.elsewhere()
                share_history(served, told)
                report = served.report()
                self.assertEqual(report["verdict"], verdict)
                self.assertEqual(positions(report, expected), expected)

    def test_two_logs_sent_unmarked_are_two_live_members(self):
        runs = self.share_group("saro", "raya")
        report = self.served.report()
        self.assertEqual(report["verdict"], "whole")
        self.assertEqual(positions(report, runs), dict(saro="tip", raya="tip"))
        self.assertEqual([run["final"] for run in report["runs"]], [False, False])

    def test_a_forked_members_final_run_leaves_one_side_live(self):
        report = self.read("the forked member restarts the app")
        self.assertEqual(report["verdict"], "split at epoch 7, one side live")
        self.assertEqual(positions(report, ["a", "g", "pax"]), dict(a="tip", g="tip", pax="ended"))

    def test_every_run_final_on_a_split_leaves_no_side_live(self):
        report = self.read("split, then every run ended")
        self.assertEqual(report["verdict"], "split at epoch 7, no side live")
        self.assertEqual({seat["position"] for seat in report["installations"]}, {"ended"})

    def test_every_run_final_on_a_whole_group_leaves_no_live_member(self):
        report = self.read("everyone relaunched")
        self.assertEqual(report["verdict"], "no live member")
        self.assertEqual({seat["position"] for seat in report["installations"]}, {"ended"})

    def test_both_sides_final_leave_no_side_live_whoever_is_live_below_the_split(self):
        report = self.read("split, both sides ended, one live member still on the trunk")
        self.assertEqual(report["verdict"], "split at epoch 7, no side live")
        self.assertEqual(
            positions(report, ["a", "pax", "mira"]), dict(a="ended", pax="ended", mira="behind")
        )

    def test_an_ended_installation_is_the_one_witness_of_a_member_behind(self):
        report = self.read("an ended installation is the witness")
        self.assertEqual(report["verdict"], "whole, members behind")
        self.assertEqual(
            positions(report, ALL3.split()), dict(saro="ended", raya="behind", pax="tip")
        )

    def test_an_installation_with_a_final_run_and_an_unmarked_one_is_live(self):
        report = self.read("one installation, a final run and a live one")
        self.assertEqual(report["verdict"], "whole")
        self.assertEqual(positions(report, PAIR.split()), dict(saro="tip", raya="tip"))
        self.assertEqual([run["final"] for run in report["runs"]], [True, False, False])

    def test_a_run_is_its_id_and_its_name_is_a_label(self):
        saro = member("saro")
        saro.delivery("online")
        first, _ = self.served.share(logged(saro), "the name of both", final=True)
        self.clock.pass_days(1)
        second, _ = self.served.share(logged(member("raya")), "the name of both")
        report = self.served.report()
        self.assertEqual(list(report), KEYS)
        at = {
            "run": first,
            "line": 4,
            "time": "2026-10-08T10:00:04.000Z",
            "has_offset": True,
            "seconds": START - DAY - 2 * HOUR + 4,
        }
        self.assertEqual(
            report["runs"],
            [
                {
                    "id": first,
                    "name": "the name of both",
                    "app": "tests",
                    "final": True,
                    "size": len(logged(saro)),
                    "heard": "2026-10-09T12:00:00Z",
                    "delivery": [{"state": "online", "at": at}],
                },
                {
                    "id": second,
                    "name": "the name of both",
                    "app": "tests",
                    "final": False,
                    "size": len(logged(member("raya"))),
                    "heard": "2026-10-10T12:00:00Z",
                    "delivery": [],
                },
            ],
        )
        seats = {name: seat_of(report, name) for name in PAIR.split()}
        self.assertEqual((seats["saro"]["runs"], seats["raya"]["runs"]), ([first], [second]))
        self.assertEqual(seats["saro"]["history"][0]["at"]["run"], first)
        self.assertEqual(positions(report, seats), dict(saro="ended", raya="tip"))

    def test_a_fold_says_how_many_runs_its_installations_wrote_in_and_when_they_were_heard(self):
        # Saro shares a run Raya wrote in too, and Raya a run that is final and a day later one
        # that is not.
        trio = "saro raya kes"
        both = member("saro", roster=trio)
        both.epoch("raya", 1, "X1", trio)
        self.served.share(logged(both), "saro")
        self.clock.pass_days(1)
        self.served.share(logged(member("raya", roster=trio)), "raya", final=True)
        self.clock.pass_days(1)
        last, receipt = self.served.share(logged(member("raya", roster=trio, hour=11)), "raya")
        report = self.served.report()
        self.assertEqual(
            [(fold["position"], len(fold["installations"])) for fold in report["folds"]],
            [("tip", 2), ("no data", 1)],
        )
        days = [f"2026-10-{day:02}T12:00:00Z" for day in (9, 10, 11, 12)]
        nothing = {"runs": 0, "final": 0, "first": None, "last": None}
        self.assertEqual(
            report["heard"],
            [{"runs": 3, "final": 1, "first": days[0], "last": days[2]}, nothing],
        )
        # A run that is heard from again, with no byte more, is the last heard.
        self.clock.pass_days(1)
        self.served.put(last, receipt)
        self.assertEqual(
            self.served.report()["heard"],
            [{"runs": 3, "final": 1, "first": days[0], "last": days[3]}, nothing],
        )

    def test_a_log_shared_twice_and_marked_once_ends_its_installation_whichever_is_marked(self):
        for marked in (0, 1):
            with self.subTest(marked=marked):
                served = self.elsewhere()
                served.share(logged(member("raya")), "raya")
                for copy in (0, 1):
                    served.share(logged(member("saro")), f"copy {copy}", final=copy == marked)
                report = served.report()
                self.assertEqual(positions(report, PAIR.split()), dict(saro="ended", raya="tip"))
                self.assertEqual(report["coverage"], {"sharing": 1, "seats": 1})
                (read,) = seat_of(report, "saro")["runs"]
                self.assertEqual(
                    [run["final"] for run in report["runs"] if run["id"] == read], [True]
                )

    def test_the_report_is_the_one_the_command_writes_of_the_logs_held(self):
        shared = [*sorted((FIXTURES / "histories").iterdir()), REAL]
        for directory in shared:
            with self.subTest(logs=directory.name):
                served = self.elsewhere()
                for run in find_runs([directory]):
                    served.share(b"".join(part.read_bytes() for part in run.parts), run.name)
                written = run_report(*served.data.glob("*/*.log"))
                listed = served.listed()
                self.assertEqual(listed["lines"], written.lines)
                self.assertEqual(
                    sorted(entry["id"] for entry in listed["conversations"]),
                    [conversation["id"] for conversation in written.conversations],
                )
                for conversation in written.conversations:
                    report = served.report(conversation["id"])
                    for run in report["runs"]:
                        self.assertEqual(run["size"], len(served.held(run["id"])))
                    report["runs"] = [
                        {"name": run["id"], "final": run["final"], "delivery": run["delivery"]}
                        for run in report["runs"]
                    ]
                    if len(written.conversations) == 1:
                        self.assertEqual(report["lines"], written.lines)
                    del report["lines"]
                    del report["heard"]
                    self.assertEqual(json.dumps(report), json.dumps(conversation))

    def test_a_report_is_drawn_from_every_run_a_seat_wrote_in(self):
        first, receipt = self.served.share(logged(member("saro")), final=True)
        self.served.share(logged(member("raya")), "raya")
        self.served.share(logged(member("mira", roster="mira", convo="0ddba11")), "mira")
        self.assertEqual(seat_of(self.served.report(), "saro")["position"], "ended")
        again = member("saro", roster="saro pax", convo=OTHER, hour=11)
        second, _ = self.served.share(logged(again))
        report = self.served.report()
        self.assertEqual(seat_of(report, "saro")["position"], "tip")
        self.assertEqual(seat_of(report, "saro")["runs"], [first, second])
        self.assertEqual(len(report["runs"]), 3)
        written = run_report(*self.data.glob("*/*.log"))
        mine = [self.served.report(conversation["id"]) for conversation in written.conversations]
        self.assertEqual(
            [[seat["runs"] for seat in report["installations"]] for report in mine],
            [[seat["runs"] for seat in c["installations"]] for c in written.conversations],
        )

    def test_an_installation_is_a_seat_of_every_conversation_a_roster_lists_it_in(self):
        saro = Log("saro")
        saro.start("saro")
        # A roster that follows another in its conversation, then the same one in another.
        saro.epoch("saro", 1, "X1", PAIR)
        saro.epoch("saro", 2, "X2", ALL3)
        saro.epoch("saro", 1, "Y1", ALL3, OTHER)
        pax = Log("pax")
        pax.start("pax")
        self.served.share(logged(saro))
        self.served.share(logged(member("raya", 1)), "raya")
        self.served.share(logged(pax), "pax")
        mine = {convo: self.served.report(convo) for convo in (CONVO, OTHER)}
        self.assertEqual(
            positions(mine[CONVO], ALL3.split()),
            dict(saro="tip", raya="behind", pax="never joined"),
        )
        self.assertEqual(
            positions(mine[OTHER], ALL3.split()),
            dict(saro="tip", raya="never joined", pax="never joined"),
        )
        for conversation in run_report(*self.data.glob("*/*.log")).conversations:
            self.assertEqual(
                mine[conversation["id"]]["installations"], conversation["installations"]
            )

    def test_a_log_that_only_leaves_a_conversation_is_reported(self):
        saro = Log("saro")
        saro.start("saro")
        saro.left("saro", 3)
        self.served.share(logged(saro))
        self.assertEqual(seat_of(self.served.report(), "saro")["position"], "removed")

    def test_a_conversation_is_asked_for_by_its_id_as_written(self):
        convo = "a%2fgroup_of-2"
        self.served.share(logged(member("saro", convo=convo)))
        self.assertEqual(self.served.report(convo)["id"], convo)
        self.assertEqual(self.served.get(f"/conversations/{convo}").status, 404)


class Watched:
    """The lock a drawing is made under, and a count of who came for it."""

    def __init__(self, lock) -> None:
        self.lock, self.came = lock, threading.Semaphore(0)

    def __enter__(self) -> None:
        self.came.release()
        self.lock.acquire()

    def __exit__(self, *raised) -> None:
        self.lock.release()


class Drawing(Collecting):
    """A report is drawn when it is asked for, once for each change to its records."""

    def setUp(self) -> None:
        super().setUp()
        self.drawn: List[str] = []
        # The runs each drawing was given the records of.
        self.read: List[List[str]] = []
        self.started, self.go = threading.Event(), threading.Event()
        self.go.set()
        draw = rule.report

        def watched(records, final=(), kept=(), only=None):
            if only is not None:
                self.drawn += only
                self.read.append(sorted({record.run for record in records}))
                self.started.set()
                assert self.go.wait(10)
            return draw(records, final, kept, only)

        self.enter(mock.patch.object(rule, "report", watched))
        self.runs = self.share_group("saro", "raya", epochs=1)

    def asked(self, convo: str = CONVO) -> bytes:
        answer = self.served.get(f"/conversations/{convo}")
        self.assertEqual(answer.status, 200)
        return answer.body

    def epoch(self, name: str, epoch: int, roster: str = PAIR, convo: str = CONVO) -> bytes:
        log = Log(name, hour=11)
        log.epoch(name, epoch, f"X{epoch}", roster, convo)
        return logged(log)

    def test_nothing_is_drawn_until_a_report_is_asked_for_and_then_once(self):
        self.assertEqual(self.drawn, [])
        first = self.asked()
        self.assertEqual((self.asked(), self.asked()), (first, first))
        self.served.listed()
        self.assertEqual(self.drawn, [CONVO])

    def test_a_collector_started_on_the_directory_draws_a_report_once_too(self):
        again = self.serve()
        first = again.get(f"/conversations/{CONVO}")
        self.assertEqual(again.get(f"/conversations/{CONVO}").body, first.body)
        self.assertEqual(self.drawn, [CONVO])

    def test_a_report_is_drawn_from_the_runs_its_seats_wrote_in_and_from_no_other(self):
        pax, _ = self.served.share(logged(member("pax", roster="pax", convo=OTHER)), "pax")
        # Mira's run names the pair's conversation and one of her own.
        mira = member("mira", roster=f"{PAIR} mira")
        mira.epoch("mira", 1, "Y1", "mira", "0ddba11")
        both, _ = self.served.share(logged(mira), "mira")
        for convo in (OTHER, CONVO, "0ddba11"):
            self.asked(convo)
        pair = [run for run, _ in self.runs.values()]
        self.assertEqual(self.drawn, [OTHER, CONVO, "0ddba11"])
        self.assertEqual(self.read, [[pax], sorted([*pair, both]), [both]])

    def test_the_list_draws_each_conversation_once(self):
        self.served.share(logged(member("pax", roster="pax", convo=OTHER)), "pax")
        self.served.listed()
        self.served.listed()
        self.asked()
        self.asked(OTHER)
        self.assertEqual(sorted(self.drawn), sorted([CONVO, OTHER]))

    def test_a_write_that_brings_no_record_draws_nothing(self):
        before = json.loads(self.asked())
        noise, line = b"a line that is no record\n\n  at a backtrace\n", self.epoch("saro", 2)
        self.served.put(*self.runs["saro"])
        self.served.put(*self.runs["saro"], noise)
        self.served.put(*self.runs["saro"], line[:-1])
        after = json.loads(self.asked())
        self.assertEqual(self.drawn, [CONVO])
        sent = len(noise) + len(line) - 1
        self.assertEqual(after["runs"][0]["size"], before["runs"][0]["size"] + sent)
        read = {count: before["lines"][count] + 3 for count in ("lines", "unstamped")}
        self.assertEqual(after["lines"], dict(before["lines"], **read))
        self.assertEqual(dict(after, runs=None, lines=None), dict(before, runs=None, lines=None))
        self.served.put(*self.runs["saro"], b"\n")
        self.assertEqual(seat_of(json.loads(self.asked()), "saro")["epoch"], 2)
        self.assertEqual(self.drawn, [CONVO, CONVO])

    def test_each_change_to_its_records_draws_it_once_more(self):
        self.asked()
        online = Log("saro", hour=12)
        online.delivery("online")
        changes = [
            lambda: self.served.put(*self.runs["saro"], self.epoch("saro", 2)),
            lambda: self.served.put(*self.runs["saro"], logged(online)),
            lambda: self.served.put(*self.runs["raya"], final=True),
            lambda: self.served.share(logged(member("pax", roster=ALL3)), "pax"),
            lambda: self.served.delete(*self.runs["raya"]),
        ]
        for count, change in enumerate(changes, 2):
            change()
            first = self.asked()
            self.assertEqual(self.asked(), first)
            self.assertEqual(self.drawn, [CONVO] * count)
        report = json.loads(first)
        self.assertEqual(
            positions(report, ALL3.split()), dict(saro="tip", raya="no data", pax="tip")
        )
        self.assertEqual([len(run["delivery"]) for run in report["runs"]], [1, 0])

    def test_a_mark_repeated_and_a_run_only_heard_from_draw_nothing(self):
        self.served.put(*self.runs["raya"], final=True)
        self.asked()
        self.clock.pass_days(1)
        self.served.put(*self.runs["raya"], final=True)
        self.served.put(*self.runs["saro"])
        heard = [run["heard"] for run in json.loads(self.asked())["runs"]]
        self.assertEqual(heard, ["2026-10-10T12:00:00Z"] * 2)
        self.assertEqual(self.drawn, [CONVO])

    def test_what_names_no_seat_of_a_conversation_draws_it_no_more(self):
        self.asked()
        pax = self.served.share(logged(member("pax", roster="pax", convo=OTHER)), "pax")
        self.served.put(*pax, self.epoch("pax", 3, "pax", OTHER))
        self.served.put(*pax, final=True)
        self.asked()
        self.asked(OTHER)
        self.served.delete(*pax)
        self.asked()
        self.assertEqual(self.drawn, [CONVO, OTHER])

    def test_a_seats_record_in_another_conversation_draws_both(self):
        self.asked()
        self.served.put(*self.runs["saro"], self.epoch("saro", 1, "saro", OTHER))
        self.asked()
        self.asked(OTHER)
        self.assertEqual(self.drawn, [CONVO, CONVO, OTHER])

    def elsewhere_again(self) -> Tuple[str, str]:
        """Saro's run is marked, and Saro runs again in another conversation alone."""
        self.served.put(*self.runs["saro"], final=True)
        second = self.served.share(logged(member("saro", roster="saro", convo=OTHER, hour=11)))
        self.assertEqual(seat_of(json.loads(self.asked()), "saro")["position"], "tip")
        return second

    def test_a_seats_run_in_another_conversation_that_is_deleted_draws_it_once_more(self):
        second = self.elsewhere_again()
        drawn = len(self.drawn)
        self.assertEqual(self.served.delete(*second).status, 204)
        report = json.loads(self.asked())
        self.assertEqual(seat_of(report, "saro")["position"], "ended")
        self.assertEqual(
            [run["id"] for run in report["runs"]], [run for run, _ in self.runs.values()]
        )
        self.asked()
        self.assertEqual(self.drawn[drawn:], [CONVO])

    def test_a_seats_run_in_another_conversation_that_expires_draws_it_once_more(self):
        self.elsewhere_again()
        self.clock.pass_days(29)
        for run in self.runs.values():
            self.assertEqual(self.served.put(*run).status, 200)
        self.asked()
        drawn = len(self.drawn)
        self.clock.pass_days(2)
        report = json.loads(self.asked())
        self.assertEqual(seat_of(report, "saro")["position"], "ended")
        self.assertEqual(
            [run["id"] for run in report["runs"]], [run for run, _ in self.runs.values()]
        )
        self.asked()
        self.assertEqual(self.drawn[drawn:], [CONVO])

    def test_a_report_being_drawn_holds_up_nothing_but_another_drawing(self):
        self.served.share(logged(member("pax", roster="pax", convo=OTHER)), "pax")
        drawn_before = self.asked(OTHER)
        answers: List[dict] = []
        asking = threading.Thread(target=lambda: answers.append(self.served.report()))
        self.started.clear()
        self.go.clear()
        asking.start()
        try:
            self.assertTrue(self.started.wait(10))
            answer = self.served.put(*self.runs["saro"], self.epoch("saro", 2))
            self.assertEqual(answer.status, 200)
            run, _ = self.runs["saro"]
            self.assertEqual(self.served.get(f"/runs/{run}/log?line=1").status, 200)
            self.assertEqual(self.served.open("mira")[0], "r4")
            self.assertEqual(self.asked(OTHER), drawn_before)
        finally:
            self.go.set()
            asking.join()
        self.assertEqual(seat_of(answers[0], "saro")["epoch"], 1)
        self.assertEqual(seat_of(self.served.report(), "saro")["epoch"], 2)
        self.assertEqual(self.drawn, [OTHER, CONVO, CONVO])

    def test_a_conversation_that_goes_while_its_report_is_waited_for_answers_404(self):
        lock = self.served.collector._drawing = Watched(self.served.collector._drawing)
        answered: List[int] = []
        asking = [
            threading.Thread(
                target=lambda: answered.append(self.served.get(f"/conversations/{CONVO}").status)
            )
            for _ in "12"
        ]
        self.go.clear()
        for thread in asking:
            thread.start()
        try:
            self.assertTrue(lock.came.acquire(timeout=10) and lock.came.acquire(timeout=10))
            for run in self.runs.values():
                self.assertEqual(self.served.delete(*run).status, 204)
        finally:
            self.go.set()
            for thread in asking:
                thread.join()
        self.assertEqual(sorted(answered), [200, 404])
        self.assertEqual(self.drawn, [CONVO])

    def test_two_who_ask_at_once_are_answered_from_one_drawing(self):
        lock = self.served.collector._drawing = Watched(self.served.collector._drawing)
        answers: List[bytes] = []
        asking = [threading.Thread(target=lambda: answers.append(self.asked())) for _ in "12"]
        self.go.clear()
        for thread in asking:
            thread.start()
        try:
            self.assertTrue(lock.came.acquire(timeout=10) and lock.came.acquire(timeout=10))
        finally:
            self.go.set()
            for thread in asking:
                thread.join()
        self.assertEqual((len(answers), len(set(answers))), (2, 1))
        self.assertEqual(self.drawn, [CONVO])


class FrontPage(Collecting):
    def test_conversations_are_listed_the_worst_first(self):
        told = [
            ("healthy group", "whole"),
            ("one sharing member alone", "not enough data"),
            ("split, then every run ended", "split at epoch 7, no side live"),
            ("a stuck pair under a joiner nobody links to", "whole as far as seen"),
            ("split seven to one", "split at epoch 7"),
            ("everyone relaunched", "no live member"),
            ("the forked member restarts the app", "split at epoch 7, one side live"),
            ("one member behind, witnessed", "whole, members behind"),
            ("a split whose second branch starts with a joiner", "split seen from epoch 7"),
        ]
        for group, (name, _) in enumerate(told):
            share_history(self.served, history(name), f"-{group}")
            self.clock.pass_days(1)
        listed = self.served.listed()["conversations"]
        self.assertEqual(
            [(entry["id"], entry["verdict"]) for entry in listed],
            [(f"{CONVO}-{group}", told[group][1]) for group in (8, 4, 6, 2, 3, 7, 1, 5, 0)],
        )

    def test_of_two_that_read_alike_the_one_heard_from_last_is_first(self):
        runs = {}
        for convo, name in (("c3", "saro"), ("c1", "raya"), ("c2", "pax")):
            runs[convo] = self.served.share(logged(member(name, roster=name, convo=convo)), name)
        listed = self.served.listed()["conversations"]
        self.assertEqual([entry["id"] for entry in listed], ["c1", "c2", "c3"])
        self.clock.pass_days(1)
        self.served.put(*runs["c2"])
        self.clock.pass_days(1)
        self.served.put(*runs["c3"])
        listed = self.served.listed()["conversations"]
        self.assertEqual(
            [(entry["id"], entry["heard"]) for entry in listed],
            [
                ("c3", "2026-10-11T12:00:00Z"),
                ("c2", "2026-10-10T12:00:00Z"),
                ("c1", "2026-10-09T12:00:00Z"),
            ],
        )

    def test_a_conversation_was_heard_from_when_the_last_of_its_runs_was(self):
        runs = self.share_group("saro", "raya")
        self.clock.pass_days(1)
        self.served.put(*runs["saro"])
        (entry,) = self.served.listed()["conversations"]
        self.assertEqual(entry["heard"], "2026-10-10T12:00:00Z")

    def test_a_conversation_was_heard_from_when_a_run_its_report_lists_was(self):
        saro = member("saro", epochs=1)
        # A roster without its writer is a leave: Pax, whom it lists, is no seat for it.
        saro.epoch("saro", 2, "X2", "raya pax")
        run, _ = self.served.share(logged(saro))
        self.clock.pass_days(1)
        pax = Log("pax", hour=11)
        pax.start("pax")
        self.served.share(logged(pax), "pax")
        asked = [self.served.listed()["conversations"] for _ in "12"]
        again = self.serve()
        asked += [again.listed()["conversations"] for _ in "12"]
        heard = [[entry["heard"] for entry in listed] for listed in asked]
        self.assertEqual(heard, [[stamp(START)]] * 4)
        self.assertEqual([entry["id"] for entry in self.served.report()["runs"]], [run])

    def test_two_that_read_alike_stay_in_their_order_however_often_they_are_asked_for(self):
        self.served.share(logged(member("saro", roster="saro", convo="c1")))
        mira = Log("mira")
        mira.start("mira")
        mira.epoch("mira", 1, "Y1", "pax", "c1")
        self.served.share(logged(mira), "mira")
        self.clock.pass_days(1)
        self.served.share(logged(member("raya", roster="raya", convo="c2")), "raya")
        self.clock.pass_days(1)
        pax = Log("pax", hour=11)
        pax.start("pax")
        self.served.share(logged(pax), "pax")
        for served in (self.served, self.serve()):
            for _ in "12":
                listed = served.listed()["conversations"]
                self.assertEqual(
                    [(entry["id"], entry["verdict"]) for entry in listed],
                    [("c2", "not enough data"), ("c1", "not enough data")],
                )

    def test_an_entry_says_what_the_front_page_draws(self):
        share_history(self.served, history("split seven to one"))
        alone = logged(member("mira", roster="mira", convo=OTHER))
        self.served.share(b"  at a backtrace\n" + alone, "mira")
        listed = self.served.listed()
        self.assertEqual(
            listed["lines"],
            {"lines": 35, "records": 34, "unstamped": 1, "malformed": 0, "unknown": 0},
        )
        self.assertEqual(
            listed["conversations"],
            [
                {
                    "id": CONVO,
                    "verdict": "split at epoch 7",
                    "split_epoch": 7,
                    "coverage": {"sharing": 8, "seats": 8},
                    "branches": [{"label": "A", "epoch": 8}, {"label": "B", "epoch": 7}],
                    "heard": "2026-10-09T12:00:00Z",
                    "unheard": 0,
                },
                {
                    "id": OTHER,
                    "verdict": "not enough data",
                    "split_epoch": None,
                    "coverage": {"sharing": 1, "seats": 1},
                    "branches": [{"label": "A", "epoch": 2}],
                    "heard": "2026-10-09T12:00:00Z",
                    "unheard": 0,
                },
            ],
        )


class ThirtyDays(Collecting):
    def gone(self, run: str, receipt: str) -> None:
        answers = [
            self.served.put(run, receipt, b"", 0),
            self.served.get(f"/runs/{run}/log?line=1"),
            self.served.delete(run, receipt),
        ]
        for answer in answers:
            self.assertEqual((answer.status, list(answer.said)), (404, ["error"]))

    def test_a_run_unheard_for_31_days_is_gone(self):
        run, receipt = self.served.share(logged(member("saro")))
        self.clock.pass_days(31)
        self.gone(run, receipt)
        self.assertEqual(self.served.get(f"/conversations/{CONVO}").status, 404)
        self.assertEqual(tree(self.data), {})

    def test_a_run_unheard_for_29_days_stays_and_reading_it_is_not_hearing_from_it(self):
        run, receipt = self.served.share(logged(member("saro")))
        held = tree(self.data)
        self.clock.pass_days(29)
        self.assertEqual(self.served.get(f"/runs/{run}/log?line=1").status, 200)
        self.assertEqual(self.served.report()["runs"][0]["heard"], "2026-10-09T12:00:00Z")
        self.assertEqual(len(self.served.listed()["conversations"]), 1)
        self.assertEqual(tree(self.data), held)
        self.clock.pass_days(2)
        self.gone(run, receipt)

    def test_an_empty_put_on_day_29_keeps_a_run_another_30(self):
        run, receipt = self.served.share(logged(member("saro")))
        self.clock.pass_days(29)
        self.assertEqual(self.served.put(run, receipt).status, 200)
        self.assertEqual(self.served.run_json(run)["heard"], "2026-11-07T12:00:00Z")
        self.clock.pass_days(29)
        self.assertEqual(self.served.report()["runs"][0]["heard"], "2026-11-07T12:00:00Z")
        self.clock.pass_days(2)
        self.gone(run, receipt)

    def test_a_write_that_is_refused_keeps_no_run(self):
        run, receipt = self.served.share(logged(member("saro")))
        self.clock.pass_days(29)
        self.assertEqual(self.served.put(run, None).status, 403)
        self.assertEqual(self.served.put(run, receipt, b"", 0).status, 409)
        self.assertEqual(self.served.put(run, receipt, b"more\n", final=True).status, 200)
        self.clock.pass_days(29)
        self.assertEqual(self.served.put(run, receipt, b"more\n").status, 409)
        self.clock.pass_days(2)
        self.assertEqual(self.served.put(run, receipt, b"", 0).status, 404)

    def test_a_run_is_gone_at_the_call_made_30_days_after_it_was_heard_from(self):
        log = logged(member("saro"))

        def opened() -> Tuple[Collector, Path, str, str]:
            # No server: nothing lets go of a run but the call itself.
            data = Path(self.enter(tempfile.TemporaryDirectory()))
            collector = Collector(data, None, self.clock, self.mint, [].append)
            run, receipt = collector.open_run("saro", "tests")
            collector.append(run, receipt, 0, log, False)
            return collector, data, run, receipt

        calls = {
            "an append": lambda held, run, receipt: held.append(run, receipt, 0, b"", False),
            "a delete": lambda held, run, receipt: held.delete(run, receipt),
            "its lines": lambda held, run, receipt: held.lines_around(run, 1),
            "its report": lambda held, run, receipt: held.conversation(CONVO),
        }
        for name, call in calls.items():
            with self.subTest(call=name):
                collector, data, run, receipt = opened()
                self.clock.now += 30 * DAY - 1
                collector.lines_around(run, 1)
                collector.conversation(CONVO)
                self.clock.now += 1
                with self.assertRaises(Refused) as refused:
                    call(collector, run, receipt)
                self.assertEqual(refused.exception.status, 404)
                self.assertEqual(tree(data), {})
        collector, data, _, _ = opened()
        self.clock.now += 30 * DAY
        self.assertEqual(collector.conversations()["lines"]["lines"], 0)
        self.assertEqual(tree(data), {})

    def test_a_collector_nobody_calls_lets_go_on_time(self):
        run, _ = self.served.share(logged(member("saro")))
        self.clock.pass_days(31)
        self.assertTrue(self.served.wrote(f"run {run} unheard for 30 days, gone"))
        self.assertEqual(tree(self.data), {})

    def keep(self, final: bool = True) -> Tuple[Tuple[str, str], Dict[str, Tuple[str, str]]]:
        """Saro's run, in two conversations, goes unheard while Raya's two are heard from."""
        saro = member("saro")
        saro.epoch("saro", 5, "Y5", PAIR, OTHER)
        gone = self.served.share(logged(saro), final=final)
        stay = {
            convo: self.served.share(logged(member("raya", epochs, convo=convo)), "raya")
            for convo, epochs in ((CONVO, 2), (OTHER, 5))
        }
        self.clock.pass_days(29)
        for run in stay.values():
            self.served.put(*run)
        self.clock.pass_days(2)
        return gone, stay

    def lines(self, *convos: str) -> List[dict]:
        epochs = {CONVO: 2, OTHER: 5}
        saro = {"installation": hexed("saro"), "account": account("saro"), "left": False}
        return [dict(saro, convo=convo, epoch=epochs[convo]) for convo in convos]

    def test_a_final_run_leaves_a_line_for_each_installation_and_conversation(self):
        (run, receipt), _ = self.keep()
        self.assertEqual(self.served.put(run, receipt, b"", 0).status, 404)
        self.assertEqual(self.served.get(f"/runs/{run}/log?line=1").status, 404)
        self.assertEqual([path.name for path in (self.data / run).iterdir()], ["run.json"])
        self.assertEqual(
            self.served.run_json(run),
            {
                "name": "saro",
                "app": "tests",
                "receipt": hashlib.sha256(receipt.encode()).hexdigest(),
                "final": True,
                "heard": "2026-10-09T12:00:00Z",
                "kept": self.lines(CONVO, OTHER),
            },
        )

    def test_a_line_holds_the_epoch_of_its_installations_last_record_and_whether_it_left(self):
        four = f"{ALL3} mira"
        saro = member("saro", epochs=1, roster=four)
        saro.left("saro", 2)
        pax = member("pax", epochs=1, roster=four)
        # A roster without its writer is a leave as well.
        pax.epoch("pax", 3, "X3", "saro raya mira")
        # A leave that another record follows is not the last.
        mira = member("mira", epochs=1, roster=four)
        mira.left("mira", 2)
        mira.epoch("mira", 3, "Z3", four)
        gone = [self.served.share(logged(log), log.run, final=True) for log in (saro, pax, mira)]
        self.clock.pass_days(29)
        self.served.share(logged(member("raya", roster=four)), "raya")
        self.clock.pass_days(2)
        report = self.served.report()
        ended = (("saro", 2, True), ("pax", 3, True), ("mira", 3, False))
        self.assertEqual(
            [self.served.run_json(run)["kept"] for run, _ in gone],
            [
                [
                    dict(
                        convo=CONVO,
                        installation=hexed(name),
                        account=account(name),
                        epoch=epoch,
                        left=left,
                    )
                ]
                for name, epoch, left in ended
            ],
        )
        for name, epoch, left in ended:
            seat = seat_of(report, name)
            position = "removed" if left else "ended"
            self.assertEqual((seat["position"], seat["epoch"]), (position, epoch))

    def unheard(self, saro: Log, raya: Log) -> Tuple[dict, dict]:
        """Saro's seat two days before its final run goes unheard for thirty, and a day after."""
        self.served.share(logged(saro), final=True)
        stays = self.served.share(logged(raya), "raya")
        seats = []
        for days in (28, 3):
            self.clock.pass_days(days)
            self.served.put(*stays)
            seats.append(seat_of(self.served.report(), "saro"))
        self.assertEqual((len(seats[0]["history"]), seats[1]["history"]), (2, []))
        return seats[0], seats[1]

    def test_a_seat_whose_final_run_ended_in_a_leave_stays_removed_when_its_log_goes(self):
        saro = member("saro", epochs=1, roster=ALL3)
        saro.left("saro", 2)
        # The one log left never saw the removal: its roster lists Saro.
        for seat in self.unheard(saro, member("raya", epochs=1, roster=ALL3)):
            self.assertEqual((seat["position"], seat["epoch"]), ("removed", 2))

    def test_a_roster_older_than_a_kept_line_does_not_show_its_seat_removed(self):
        saro = Log("saro")
        saro.start("saro")
        for epoch in (2, 3):
            saro.epoch("saro", epoch, f"X{epoch}", ALL3)
        for seat in self.unheard(saro, member("raya", epochs=1, roster="raya pax")):
            self.assertEqual((seat["position"], seat["epoch"]), ("ended", 3))

    def test_the_report_shows_an_installation_of_a_kept_line_ended(self):
        (run, _), stay = self.keep()
        for convo, epoch in ((CONVO, 2), (OTHER, 5)):
            report = self.served.report(convo)
            self.assertEqual(report["verdict"], "not enough data")
            self.assertEqual(report["coverage"], {"sharing": 1, "seats": 1})
            self.assertEqual(
                seat_of(report, "saro"),
                {
                    "id": hexed("saro"),
                    "account": account("saro"),
                    "runs": [],
                    "kept": True,
                    "position": "ended",
                    "branch": None,
                    "epoch": epoch,
                    "state": None,
                    "listed": list(range(1, epoch + 1)),
                    "unreached": [],
                    "behind_by": None,
                    "history": [],
                    "cells": [],
                    "spans": [],
                },
            )
            self.assertEqual(
                [entry["id"] for entry in report["runs"]], sorted(run for run, _ in stay.values())
            )
        for _ in range(3):
            self.clock.pass_days(20)
            for heard in stay.values():
                self.served.put(*heard)
        self.assertEqual(seat_of(self.served.report(), "saro")["position"], "ended")
        self.assertEqual(self.served.run_json(run)["kept"], self.lines(CONVO, OTHER))

    def test_a_delete_removes_the_kept_lines(self):
        (run, receipt), _ = self.keep()
        self.assertEqual(self.served.delete(run, "receipt-of-another").status, 403)
        self.assertEqual(seat_of(self.served.report(), "saro")["position"], "ended")
        answer = self.served.delete(run, receipt)
        self.assertEqual((answer.status, answer.body), (204, b""))
        self.assertFalse((self.data / run).exists())
        for convo in (CONVO, OTHER):
            self.assertEqual(seat_of(self.served.report(convo), "saro")["position"], "no data")
        self.assertEqual(self.served.delete(run, receipt).status, 404)

    def test_the_kept_lines_go_with_their_conversations_last_log(self):
        (run, receipt), stay = self.keep()
        self.served.delete(*stay[CONVO])
        self.assertEqual(self.served.get(f"/conversations/{CONVO}").status, 404)
        self.assertEqual(self.served.run_json(run)["kept"], self.lines(OTHER))
        self.served.share(logged(member("raya")), "raya")
        self.assertEqual(seat_of(self.served.report(), "saro")["position"], "no data")
        self.assertEqual(seat_of(self.served.report(OTHER), "saro")["position"], "ended")
        self.served.delete(*stay[OTHER])
        self.assertFalse((self.data / run).exists())
        self.assertEqual(self.served.delete(run, receipt).status, 404)

    def test_a_run_that_was_not_final_leaves_nothing(self):
        (run, receipt), _ = self.keep(final=False)
        self.gone(run, receipt)
        self.assertFalse((self.data / run).exists())
        self.assertEqual(seat_of(self.served.report(), "saro")["position"], "no data")

    def test_an_installation_whose_new_run_holds_its_run_record_alone_goes_by_its_line(self):
        _, stay = self.keep()
        again = Log("saro", hour=12)
        again.start("saro")
        run, _ = self.served.share(logged(again))
        saro = seat_of(self.served.report(), "saro")
        self.assertEqual((saro["position"], saro["epoch"], saro["runs"]), ("ended", 2, [run]))
        self.assertEqual(self.served.report()["coverage"], {"sharing": 1, "seats": 1})
        # The new run, never marked, goes unheard for thirty days in its turn.
        for _ in range(2):
            self.clock.pass_days(20)
            for heard in stay.values():
                self.served.put(*heard)
        self.assertFalse((self.data / run).exists())
        saro = seat_of(self.served.report(), "saro")
        self.assertEqual((saro["position"], saro["epoch"], saro["runs"]), ("ended", 2, []))

    def test_an_installation_that_holds_the_group_again_goes_by_its_records(self):
        self.keep()
        again = Log("saro", hour=12)
        again.start("saro")
        run, receipt = self.served.share(logged(again))
        self.assertEqual(seat_of(self.served.report(), "saro")["position"], "ended")
        again.epoch("saro", 2, "X2", PAIR)
        self.served.put(run, receipt, logged(again)[len(self.served.held(run)) :])
        report = self.served.report()
        self.assertEqual(report["verdict"], "whole")
        self.assertEqual(positions(report, PAIR.split()), dict(saro="tip", raya="tip"))
        self.assertEqual(seat_of(report, "saro")["state"], hexed("X2", 16))
        # Its records of one conversation are none of the other, where its line still speaks.
        other = seat_of(self.served.report(OTHER), "saro")
        self.assertEqual((other["position"], other["epoch"], other["runs"]), ("ended", 5, [run]))

    def test_an_installation_no_roster_lists_is_a_seat_by_its_line_and_its_new_run_is_listed(self):
        self.served.share(logged(member("saro")), final=True)
        self.clock.pass_days(29)
        self.served.share(logged(member("raya", roster="raya")), "raya")
        self.clock.pass_days(2)
        saro = seat_of(self.served.report(), "saro")
        self.assertEqual((saro["position"], saro["epoch"], saro["runs"]), ("removed", 2, []))
        again = Log("saro", hour=12)
        again.start("saro")
        new = self.served.share(logged(again))
        saro = seat_of(self.served.report(), "saro")
        self.assertEqual((saro["position"], saro["epoch"], saro["runs"]), ("removed", 2, [new[0]]))
        self.clock.pass_days(5)
        self.served.put(*new)
        for served in (self.served, self.serve()):
            for _ in "12":
                heard = served.listed()["conversations"][0]["heard"]
                self.assertEqual(heard, stamp(self.clock.now))

    def test_the_histories_of_the_rules_table_that_a_kept_line_decides(self):
        for name, told, verdict, expected in HISTORIES:
            if name not in KEPT:
                continue
            with self.subTest(history=name):
                served = self.elsewhere()
                gone = {}
                for who, (epoch, left) in KEPT[name].items():
                    # A final run that ends where the line says: holding the group, or leaving it.
                    log = member(who, epoch - 1 if left else epoch, ALL3)
                    if left:
                        log.left(who, epoch)
                    gone[who], _ = served.share(logged(log), who, final=True)
                self.clock.pass_days(29)
                share_history(served, told)
                self.clock.pass_days(2)
                report = served.report()
                self.assertEqual(report["verdict"], verdict)
                self.assertEqual(positions(report, expected), expected)
                for who, (epoch, left) in KEPT[name].items():
                    (line,) = served.run_json(gone[who])["kept"]
                    self.assertEqual((line["epoch"], line["left"]), (epoch, left))


class Stuck(Collecting):
    """A run the disk does not let go of."""

    def test_it_stays_as_it_is_the_others_go_and_it_is_tried_again_an_hour_later(self):
        stuck, _ = self.served.share(logged(member("saro")))
        goes, _ = self.served.share(logged(member("raya")), "raya")
        stays = self.served.share(logged(member("pax", roster="pax", convo=OTHER)), "pax")
        self.clock.pass_days(29)
        self.served.put(*stays)
        held, written = tree(self.data / stuck), len(self.served.written)
        left = (
            f" run {stuck} unheard for 30 days, left as it is: "
            "[Errno 13] Permission denied: 'the log'"
        )
        refused = failing(f"{stuck}.log", PermissionError(13, "Permission denied", "the log"))
        # Nothing is let go between the calls, so each call here is one letting go.
        with refused, mock.patch.object(self.served.collector, "expire"):
            self.clock.pass_days(2)
            report = self.served.report()
            self.assertEqual(positions(report, PAIR.split()), dict(saro="tip", raya="no data"))
            self.assertEqual(self.served.get(f"/runs/{stuck}/log?line=1").status, 200)
            self.assertEqual(len(self.served.listed()["conversations"]), 2)
            now = stamp(self.clock.now)
            self.assertEqual(
                self.served.written[written:],
                [now + left, f"{now} run {goes} unheard for 30 days, gone"],
            )
            self.assertEqual(tree(self.data / stuck), held)
            self.assertFalse((self.data / goes).exists())
            self.clock.now += HOUR - 1
            self.served.listed()
            self.assertEqual(len(self.served.written), written + 2)
            self.clock.now += 1
            self.served.listed()
            self.assertEqual(self.served.written[written + 2 :], [stamp(self.clock.now) + left])
        self.clock.now += HOUR
        self.assertEqual(self.served.get(f"/conversations/{CONVO}").status, 404)
        self.assertEqual(
            self.served.written[written + 3 :],
            [f"{stamp(self.clock.now)} run {stuck} unheard for 30 days, gone"],
        )
        self.assertFalse((self.data / stuck).exists())

    def test_a_final_run_whose_lines_cannot_be_kept_stays_whole(self):
        stuck, _ = self.served.share(logged(member("saro")), final=True)
        self.clock.pass_days(29)
        self.served.share(logged(member("raya")), "raya")
        held = tree(self.data / stuck)
        with mock.patch("os.replace", side_effect=OSError(28, "No space left on device")):
            self.clock.pass_days(2)
            saro = seat_of(self.served.report(), "saro")
            self.assertEqual((saro["position"], saro["state"]), ("ended", hexed("X2", 16)))
            said = f" run {stuck} unheard for 30 days, left as it is: [Errno 28] No space left"
            self.assertIn(said, self.served.written[-1])
            # The log and the `run.json` of before; the one that did not replace it lies beside.
            now = tree(self.data / stuck)
            self.assertEqual(sorted(now), sorted([*held, "run.new"]))
            self.assertEqual({name: now[name] for name in held}, held)
        self.clock.now += HOUR
        saro = seat_of(self.served.report(), "saro")
        self.assertEqual((saro["position"], saro["state"], saro["epoch"]), ("ended", None, 2))
        self.assertEqual(sorted(tree(self.data / stuck)), ["run.json"])
        self.assertEqual(len(self.served.run_json(stuck)["kept"]), 1)

    def test_a_final_run_whose_log_cannot_be_removed_keeps_its_lines_beside_it(self):
        stuck, _ = self.served.share(logged(member("saro")), final=True)
        self.clock.pass_days(29)
        self.served.share(logged(member("raya")), "raya")
        log = self.served.held(stuck)
        left = f" run {stuck} unheard for 30 days, left as it is: [Errno 13] Permission denied"
        with failing(f"{stuck}.log", PermissionError(13, "Permission denied")):
            self.clock.pass_days(2)
            # The collector the disk refused, and then one started on what it left.
            for start in (lambda: self.served, self.serve):
                served = start()
                saro = seat_of(served.report(), "saro")
                self.assertEqual((saro["position"], saro["state"]), ("ended", hexed("X2", 16)))
                self.assertEqual(served.written[-1], stamp(self.clock.now) + left)
                self.assertEqual(len(served.run_json(stuck)["kept"]), 1)
                self.assertEqual(served.held(stuck), log)
                self.assertEqual(served.get(f"/runs/{stuck}/log?line=1").status, 200)
                served.stop()
        again = self.serve()
        saro = seat_of(again.report(), "saro")
        self.assertEqual((saro["position"], saro["state"], saro["epoch"]), ("ended", None, 2))
        self.assertEqual(sorted(tree(self.data / stuck)), ["run.json"])

    def test_lines_the_disk_does_not_let_go_of_stay_until_the_next_run_goes(self):
        gone, _ = self.served.share(logged(member("saro")), final=True)
        self.clock.pass_days(29)
        last = self.served.share(logged(member("raya")), "raya")
        self.clock.pass_days(2)
        self.served.report()
        held = tree(self.data / gone)
        now = stamp(self.clock.now)
        left = (
            f"{now} run {gone}: a conversation's last log is gone, left as it is: "
            "[Errno 13] Permission denied"
        )
        with staying(gone, PermissionError(13, "Permission denied")):
            self.assertEqual(self.served.delete(*last).status, 204)
            self.assertEqual(self.served.written[-2:], [f"{now} run {last[0]} deleted", left])
            again = self.serve()
            for served in (self.served, again):
                self.assertEqual(served.listed()["conversations"], [])
            self.assertEqual(again.written, [left])
            self.assertEqual(tree(self.data), {gone: None, f"{gone}/run.json": held["run.json"]})
        # The next run that goes has the lines tried again.
        self.served.delete(*self.served.share(logged(member("pax", roster="pax", convo=OTHER))))
        said = f"{now} run {gone}: a conversation's last log is gone, lines kept: 0"
        self.assertEqual(self.served.written[-1], said)
        self.assertEqual(tree(self.data), {})

    def test_a_letting_go_that_fails_between_requests_ends_no_serving(self):
        run, _ = self.served.share(logged(member("saro")))
        collector, expire = self.served.collector, self.served.collector.expire

        def between_requests(fails: bool) -> None:
            """Has the collector let go three times between requests, and then answer one."""
            tried = threading.Semaphore(0)

            def let_go() -> None:
                tried.release()
                if fails:
                    raise ValueError(logged(member("saro")))
                expire()

            with mock.patch.object(collector, "expire", let_go):
                self.assertTrue(all(tried.acquire(timeout=10) for _ in "123"))
                self.assertEqual(self.served.get(f"/runs/{run}/log?line=1").status, 200)

        # A failure is said when it starts, and again when it starts again.
        said = f"{stamp(START)} letting go failed: ValueError"
        for count in (1, 2):
            between_requests(fails=True)
            self.assertEqual(self.served.written[2:], [said] * count)
            between_requests(fails=False)


class Restarting(Collecting):
    def numbers(self) -> Iterator[int]:
        # Counted down, so that a run opened later comes earlier in the order of the ids.
        return itertools.count(9, -1)

    def test_a_collector_started_on_the_directory_holds_the_index_the_first_one_held(self):
        saro, raya = member("saro"), member("raya")
        raya.left("raya", 3)
        gone = [self.served.share(logged(log), log.run, final=True) for log in (saro, raya)]
        self.clock.pass_days(20)
        pax = member("pax", roster=ALL3)
        pax.delivery("online")
        pax.epoch("pax", 1, "Y1", "pax", OTHER)
        stay = self.served.share(logged(pax)[:-30], "pax")
        left = self.served.share(logged(member("mira", roster="mira", convo="0ddba11")), "mira")
        self.served.share(b"  at a backtrace\n2026-10-09T10:00:00Z  INFO libchat::groupv2trace: run\n")
        self.served.open("kes")
        self.clock.pass_days(11)
        self.served.put(*stay, logged(pax)[-30:], final=True)
        self.served.delete(*left)
        kept = [self.served.run_json(run)["kept"] for run, _ in gone]
        self.assertEqual([[line["left"] for line in lines] for lines in kept], [[False], [True]])
        asked = ["/conversations", f"/conversations/{CONVO}", f"/conversations/{OTHER}"]
        first = [self.served.get(path) for path in asked]
        again = self.serve()
        self.assertEqual(again.collector.index(), self.served.collector.index())
        self.assertEqual([again.get(path) for path in asked], first)
        self.assertEqual(
            positions(first[1].said, PAIR.split()), dict(saro="ended", raya="removed")
        )
        self.assertEqual(first[0].said["lines"]["malformed"], 1)

    def test_the_index_holds_one_copy_of_a_roster_however_many_records_list_it(self):
        def rosters(served: Served) -> List[tuple]:
            runs = served.collector.index()[0].values()
            return [record.roster for run in runs for record in run.records if record.roster]

        runs = self.share_group("saro", "raya", epochs=3)
        again = self.serve()
        for served in (self.served, again):
            held = rosters(served)
            self.assertEqual((len(held), len(set(map(id, held)))), (6, 1))
        self.served.delete(*runs["saro"])
        late = Log("raya", hour=11)
        late.epoch("raya", 4, "X4", PAIR)
        self.served.put(*runs["raya"], logged(late))
        held = rosters(self.served)
        self.assertEqual((len(held), len(set(map(id, held)))), (4, 1))

    def test_a_stop_between_an_append_and_its_run_json_loses_no_byte(self):
        log = logged(member("saro"))
        cut = log.index(b"\n") + 1
        run, receipt = self.served.share(log[:cut])
        self.served.stop()
        with open(self.data / run / f"{run}.log", "ab") as file:
            file.write(log[cut:])
        again = self.serve()
        answer = again.put(run, receipt, log[cut:], cut)
        self.assertEqual((answer.status, answer.said), (409, {"size": len(log), "final": False}))
        self.assertEqual(seat_of(again.report(), "saro")["epoch"], 2)
        self.assertEqual(again.put(run, receipt, b"", len(log)).status, 200)
        self.assertEqual(again.held(run), log)

    def test_a_stop_inside_the_replacing_of_run_json_leaves_the_one_before(self):
        run, receipt = self.served.share(logged(member("saro")))
        self.served.stop()
        (self.data / run / "run.new").write_text('{"name": "sar')
        again = self.serve()
        self.assertEqual(again.report()["runs"][0]["name"], "saro")
        self.assertEqual(again.put(run, receipt, final=True).status, 200)
        self.assertIs(again.run_json(run)["final"], True)

    def test_a_run_json_that_could_not_all_be_written_leaves_the_one_before(self):
        run, receipt = self.served.share(logged(member("saro")))
        before = (self.data / run / "run.json").read_bytes()
        write_text = Path.write_text

        def cut_short(path: Path, text: str) -> None:
            write_text(path, text[: len(text) // 2])
            raise OSError(28, "No space left on device")

        with mock.patch.object(Path, "write_text", cut_short):
            with self.assertRaises(http.client.HTTPException):
                self.served.put(run, receipt, final=True)
        self.assertEqual((self.data / run / "run.json").read_bytes(), before)
        self.assertIs(self.serve().report()["runs"][0]["final"], False)

    def test_a_stop_between_keeping_the_lines_and_removing_the_log_is_made_good(self):
        run, receipt = self.served.share(logged(member("saro")), final=True)
        self.served.share(logged(member("raya")), "raya")
        self.served.stop()
        said = self.served.run_json(run)
        line = dict(convo=CONVO, installation=hexed("saro"), account="", epoch=1, left=True)
        (self.data / run / "run.json").write_text(json.dumps(dict(said, kept=[line])))
        again = self.serve()
        self.assertEqual(seat_of(again.report(), "saro")["position"], "ended")
        self.assertEqual(seat_of(again.report(), "saro")["state"], hexed("X2", 16))
        self.assertEqual(again.put(run, receipt).status, 200)
        self.assertNotIn("kept", again.run_json(run))

    def test_a_stop_between_removing_a_log_and_its_directory_is_made_good(self):
        run, receipt = self.served.share(logged(member("saro")))
        other, _ = self.served.share(logged(member("raya")), "raya")
        self.served.stop()
        for cut in (run, other):
            (self.data / cut / f"{cut}.log").unlink()
        (self.data / other / "run.new").write_text('{"name": "ray')
        again = self.serve()
        self.assertEqual(tree(self.data), {})
        self.assertEqual(
            again.written,
            [f"{stamp(START)} run {cut}: a removal was cut short, gone" for cut in (other, run)],
        )
        self.assertEqual(again.put(run, receipt, b"", 0).status, 404)
        self.assertEqual(again.listed()["conversations"], [])

    def test_a_removal_cut_short_that_the_disk_does_not_finish_stops_no_start(self):
        run, receipt = self.served.share(logged(member("saro")))
        self.served.stop()
        (self.data / run / f"{run}.log").unlink()
        held = tree(self.data)
        with staying(run, PermissionError(13, "Permission denied")):
            again = self.serve()
        self.assertEqual(again.written, [f"{stamp(START)} {run} is not a run, left as it is"])
        self.assertEqual(again.put(run, receipt, b"", 0).status, 404)
        self.assertEqual(tree(self.data), held)

    def test_a_directory_that_is_not_a_run_is_left_as_it_is(self):
        run, receipt = self.served.share(logged(member("saro")))
        self.served.stop()
        said = self.served.run_json(run)
        line = dict(convo=CONVO, installation=hexed("raya"), account=None, epoch=2, left=False)
        unsaid = {key: value for key, value in line.items() if key != "left"}

        def run_json(**other: object) -> bytes:
            return json.dumps(dict(said, **other)).encode()

        strays = {
            "notes": {"notes.log": logged(member("raya"))},
            "half": {"half.log": logged(member("raya")), "run.json": b'{"name": "ray'},
            "a.b": {"a.b.log": logged(member("raya")), "run.json": run_json()},
            "old": {"old.log": b"", "run.json": run_json(heard="long ago")},
            "odd": {"odd.log": b"", "run.json": run_json(final="no")},
            "list": {"list.log": b"", "run.json": b"[]"},
            # A run's directory under another name, and a `run.json` beside what is no log.
            "moved": {f"{run}.log": logged(member("raya")), "run.json": run_json()},
            "another": {"data.db": b"rows", "run.json": run_json()},
            # Each with the fields of a `run.json`, and none as the collector writes them.
            "deep": {"deep.log": b"", "run.json": b"[" * 100_000},
            "key": {"key.log": b"", "run.json": run_json(receipt="\u00e9" * 64)},
            "short": {"short.log": b"", "run.json": run_json(receipt=said["receipt"][:-1])},
            "name": {"name.log": b"", "run.json": run_json(name=7)},
            "lines": {"run.json": run_json(kept=[dict(line, convo=[CONVO])])},
            "seat": {"run.json": run_json(kept=[dict(line, installation=None)])},
            "whose": {"run.json": run_json(kept=[dict(line, account=7)])},
            "when": {"run.json": run_json(kept=[dict(line, epoch="2")])},
            "whether": {"run.json": run_json(kept=[dict(line, epoch=True)])},
            "unsaid": {"run.json": run_json(kept=[unsaid])},
            "maybe": {"run.json": run_json(kept=[dict(line, left=None)])},
            "counted": {"run.json": run_json(kept=[dict(line, left=0)])},
            "worded": {"run.json": run_json(kept=[dict(line, left="false")])},
            "more": {"run.json": run_json(kept=[dict(line, more=1)])},
            "none": {"run.json": run_json(kept=None)},
        }
        for name, files in strays.items():
            (self.data / name).mkdir()
            for file, held in files.items():
                (self.data / name / file).write_bytes(held)
        (self.data / "a file.log").write_bytes(logged(member("raya")))
        held = tree(self.data)
        again = self.serve()
        self.assertEqual(
            again.written,
            [f"{stamp(START)} {name} is not a run, left as it is" for name in sorted(strays)],
        )
        self.assertEqual(positions(again.report(), PAIR.split()), dict(saro="tip", raya="no data"))
        for name in strays:
            self.assertEqual(again.put(name, receipt, b"", 0).status, 404)
            self.assertEqual(again.delete(name, receipt).status, 404)
        self.assertEqual(len(again.listed()["conversations"]), 1)
        self.assertEqual(tree(self.data), held)

    def expired(self) -> Tuple[str, Tuple[str, str]]:
        """Saro's run has expired and left a line, and Raya's is the one log of the group."""
        gone, _ = self.served.share(logged(member("saro")), final=True)
        self.clock.pass_days(29)
        stays = self.served.share(logged(member("raya")), "raya")
        self.clock.pass_days(2)
        report = self.served.report()
        self.assertEqual(positions(report, PAIR.split()), dict(saro="ended", raya="tip"))
        self.served.stop()
        return gone, stays

    def test_a_run_json_whose_kept_line_does_not_say_whether_it_left_is_not_a_run(self):
        gone, _ = self.expired()
        said = self.served.run_json(gone)
        (line,) = said["kept"]
        self.assertIs(line.pop("left"), False)
        (self.data / gone / "run.json").write_text(json.dumps(said))
        held = tree(self.data)
        again = self.serve()
        left = f"{stamp(self.clock.now)} {gone} is not a run, left as it is"
        self.assertEqual(again.written, [left])
        self.assertEqual(positions(again.report(), PAIR.split()), dict(saro="no data", raya="tip"))
        self.assertEqual(tree(self.data), held)

    def test_lines_stay_while_a_log_that_may_name_their_conversation_is_not_read(self):
        gone, (stays, _) = self.expired()
        kept = (self.data / gone / "run.json").read_bytes()
        whole = (self.data / stays / "run.json").read_bytes()
        (self.data / stays / "run.json").write_bytes(whole[: len(whole) // 2])
        # Read after it, and holding no log.
        (self.data / "s").mkdir()
        again = self.serve()
        now = stamp(self.clock.now)
        left = [f"{now} {name} is not a run, left as it is" for name in (stays, "s")]
        self.assertEqual(again.written, left)
        self.assertEqual(again.listed()["conversations"], [])
        # What changes the index files the lines again, and lets none of them go.
        again.delete(*again.share(logged(member("pax", roster="pax", convo=OTHER)), "pax"))
        self.assertEqual((self.data / gone / "run.json").read_bytes(), kept)
        again.stop()
        (self.data / stays / "run.json").write_bytes(whole)
        mended = self.serve()
        self.assertEqual(positions(mended.report(), PAIR.split()), dict(saro="ended", raya="tip"))

    def test_lines_stay_while_a_directory_that_may_hold_a_log_cannot_be_listed(self):
        gone, (stays, _) = self.expired()
        kept = (self.data / gone / "run.json").read_bytes()
        shutil.rmtree(self.data / stays)
        (self.data / "closed").mkdir()
        iterdir = Path.iterdir

        def closed(path: Path) -> Iterator[Path]:
            if path.name == "closed":
                raise PermissionError(13, "Permission denied")
            return iterdir(path)

        with mock.patch.object(Path, "iterdir", closed):
            again = self.serve()
        left = f"{stamp(self.clock.now)} closed is not a run, left as it is"
        self.assertEqual(again.written, [left])
        self.assertEqual(again.listed()["conversations"], [])
        self.assertEqual((self.data / gone / "run.json").read_bytes(), kept)

    def test_lines_go_with_their_conversations_last_log_whatever_else_is_not_a_run(self):
        gone, (stays, _) = self.expired()
        shutil.rmtree(self.data / stays)
        (self.data / "notes").mkdir()
        (self.data / "notes" / "run.json").write_text('{"name": "ray')
        again = self.serve()
        now = stamp(self.clock.now)
        self.assertEqual(
            again.written,
            [
                f"{now} notes is not a run, left as it is",
                f"{now} run {gone}: a conversation's last log is gone, lines kept: 0",
            ],
        )
        self.assertEqual(sorted(tree(self.data)), ["notes", "notes/run.json"])


class AtOnce(Collecting):
    def setUp(self) -> None:
        super().setUp()
        # Threads take turns as often as they can, so that what two calls share shows.
        self.addCleanup(sys.setswitchinterval, sys.getswitchinterval())
        sys.setswitchinterval(1e-6)

    def stream(self, name: str) -> bytes:
        return logged(member(name, epochs=12, roster=name, convo=f"group-of-{name}"))

    def test_two_runs_sent_at_once_each_hold_what_was_sent(self):
        streams = {name: self.stream(name) for name in "saro raya pax mira".split()}

        def send(name: str, size: int) -> str:
            run, receipt = self.served.open(name)
            stream = streams[name]
            for offset in range(0, len(stream), size):
                answer = self.served.put(run, receipt, stream[offset : offset + size], offset)
                assert answer.status == 200, answer
                assert stream.startswith(self.served.held(run))
            return run

        with ThreadPoolExecutor(len(streams)) as senders:
            runs = dict(zip(streams, senders.map(send, streams, (7, 11, 13, 17))))
        for name, run in runs.items():
            self.assertEqual(self.served.held(run), streams[name])
            self.assertEqual(self.served.report(f"group-of-{name}")["branches"][0]["epoch"], 12)
        self.assertEqual(self.served.listed()["lines"]["records"], 4 * 13)

    def test_one_run_sent_by_several_at_once_holds_a_prefix_of_what_was_sent(self):
        stream = self.stream("saro")
        run, receipt = self.served.open()

        def send(size: int) -> int:
            offset = sent = 0
            while offset < len(stream):
                answer = self.served.put(run, receipt, stream[offset : offset + size], offset)
                assert answer.status in (200, 409), answer
                assert stream.startswith(self.served.held(run))
                # Either answer names a size past the offset: the bytes sent, or another's.
                assert answer.said["size"] > offset, answer
                offset = answer.said["size"]
                sent += answer.status == 200
            return sent

        with ThreadPoolExecutor(4) as senders:
            sent = list(senders.map(send, (5, 16, 33, 100)))
        self.assertEqual(self.served.held(run), stream)
        self.assertEqual(self.served.listed()["lines"]["records"], 13)
        self.assertEqual(len(self.served.written), 1 + sum(sent))


class OwnLog(Collecting):
    def test_every_write_is_a_line_and_a_call_that_writes_nothing_is_none(self):
        log = logged(member("saro"))
        saro = self.served.share(log[:9])
        self.served.put(*saro, log[9:], final=True)
        raya = self.served.share(logged(member("raya")), "raya")
        pax = self.served.open("pax")
        self.served.delete(*pax)
        self.served.report()
        self.served.listed()
        self.served.get(f"/runs/{saro[0]}/log?line=1")
        self.served.put(*raya, b"more\n", 0)
        self.served.put(raya[0], None)
        self.served.get("/nothing")
        self.served.call("GET", f"/conversations?token={TOKEN}", token=None)
        self.clock.pass_days(29)
        self.served.put(*raya)
        self.clock.pass_days(2)
        self.served.delete(*raya)
        size = len(logged(member("raya")))
        self.assertEqual(
            self.served.written,
            [
                "2026-10-09T12:00:00Z run r1 opened",
                "2026-10-09T12:00:00Z run r1 took 9 bytes, holds 9",
                f"2026-10-09T12:00:00Z run r1 took {len(log) - 9} bytes, holds {len(log)}, final",
                "2026-10-09T12:00:00Z run r2 opened",
                f"2026-10-09T12:00:00Z run r2 took {size} bytes, holds {size}",
                "2026-10-09T12:00:00Z run r3 opened",
                "2026-10-09T12:00:00Z run r3 deleted",
                f"2026-11-07T12:00:00Z run r2 took 0 bytes, holds {size}",
                "2026-11-09T12:00:00Z run r1 unheard for 30 days, its log is gone, lines kept: 1",
                "2026-11-09T12:00:00Z run r2 deleted",
                "2026-11-09T12:00:00Z run r1: a conversation's last log is gone, lines kept: 0",
            ],
        )

    def test_a_log_nobody_reads_any_more_stops_no_call(self):
        saro = self.served.share(logged(member("saro")))
        self.served.unread = True
        raya = self.served.share(logged(member("raya")), "raya")
        self.assertEqual(self.served.put(*raya, final=True).status, 200)
        self.assertEqual(self.served.delete(*saro).status, 204)
        report = self.served.report()
        self.assertEqual(positions(report, PAIR.split()), dict(saro="no data", raya="ended"))
        self.assertEqual(len(self.served.listed()["conversations"]), 1)
        self.clock.pass_days(31)
        self.assertEqual(self.served.listed()["conversations"], [])
        self.assertEqual(tree(self.data), {})
        self.assertEqual(len(self.served.written), 2)

    def test_the_log_never_holds_the_token_a_receipt_or_what_a_log_says(self):
        self.mint = mint
        served = self.elsewhere()
        log = logged(member("saro"))
        run, receipt = served.share(log, "a name", final=True)
        served.put(run, f"{receipt}s")
        served.put(run, receipt, log, 0)
        served.call("PUT", f"/runs/{run}/log?offset=0&token={TOKEN}", log, None)
        served.call("GET", f"/{TOKEN}/{receipt}")
        served.get(f"/conversations/{hexed('saro')}")
        served.call("POST", "/runs", log)
        served.raw(log)
        with mock.patch.object(served.collector, "conversations", side_effect=ValueError(log)):
            with self.assertRaises(http.client.HTTPException):
                served.listed()
        self.clock.pass_days(31)
        served.delete(run, receipt)
        said = "\n".join(served.written)
        self.assertEqual(len(served.written), 5)
        self.assertEqual(served.written[2], f"{stamp(START)} a call failed: ValueError")
        words = re.findall(rb"[0-9A-Za-z_-]{6,}", log) + [b"a name", b"tests"]
        for secret in (TOKEN, receipt, *(word.decode() for word in words)):
            # An id is hex, as much in a log is, and an id is no secret.
            self.assertNotIn(secret, said.replace(run, ""))

    def test_a_line_goes_to_standard_error_in_one_write(self):
        class Writes(list):
            write = list.append

        with mock.patch.object(sys, "stderr", Writes()) as writes:
            to_stderr("a line of the log")
            Collector(self.elsewhere().data, clock=self.clock).say("another")
        self.assertEqual(writes, ["a line of the log\n", "2026-10-09T12:00:00Z another\n"])


class Command:
    """`forks serve` as a process of its own, and the lines it logs."""

    def __init__(self, arguments: Iterable[str], directory: Path) -> None:
        # A shell that runs the tests in the background has them ignore an interrupt, and what
        # they start would ignore it too. A handler here is the default there.
        ignored = signal.getsignal(signal.SIGINT) == signal.SIG_IGN
        if ignored:
            signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(TOOL), "serve", *arguments],
                cwd=directory,
                stderr=subprocess.PIPE,
                text=True,
            )
        finally:
            if ignored:
                signal.signal(signal.SIGINT, signal.SIG_IGN)
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        for line in self.process.stderr:
            self._lines.put(line)
        self._lines.put("")

    def line(self) -> str:
        """The next line it logs, or nothing once it has stopped. Ten seconds of neither fail."""
        return self._lines.get(timeout=10)

    def interrupt(self) -> Tuple[int, str]:
        """Interrupts it: the status it ends with, and what it logged that was not read."""
        self.process.send_signal(signal.SIGINT)
        return self.process.wait(10), "".join(iter(self.line, ""))

    def close(self) -> None:
        self.process.kill()
        self.process.wait()
        self._reader.join()
        self.process.stderr.close()


class CommandLine(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)

    def token_in(self, token: str) -> str:
        (self.scratch / "token").write_text(token)
        return str(self.scratch / "token")

    def start(self, *arguments: str) -> Command:
        """Starts `forks serve` in the scratch directory."""
        command = Command(arguments, self.scratch)
        self.addCleanup(command.close)
        return command

    def call(self, at: Tuple[str, int], method: str, path: str, body=None, **headers) -> Answer:
        connection = http.client.HTTPConnection(*at, timeout=10)
        try:
            connection.request(method, path, body, headers)
            answer = connection.getresponse()
            return Answer(answer.status, tuple(answer.getheaders()), answer.read())
        finally:
            connection.close()

    def test_the_command_serves_a_directory_under_the_token_a_file_holds(self):
        for token in ("t" * 16, "T0_-" * 32):
            with self.subTest(token=token):
                data = self.scratch / f"data-{len(token)}" / "runs"
                token_file = self.token_in(f"\n {token}\n")
                options = ["--port", "0", "--max-bytes", "5"]
                command = self.start("--data", str(data), "--token-file", token_file, *options)
                line = command.line()
                serving = SERVING.fullmatch(line)
                self.assertIsNotNone(serving, line)
                self.assertEqual((serving["data"], serving["host"]), (str(data), "127.0.0.1"))
                at = ("127.0.0.1", int(serving["port"]))
                key = {"Authorization": f"Bearer {token}"}
                self.assertEqual(self.call(at, "GET", "/conversations").status, 401)
                self.assertEqual(self.call(at, "GET", "/conversations", **key).status, 200)
                opened = self.call(at, "POST", "/runs", b'{"name": "saro", "app": "tests"}', **key)
                run, key["Forks-Receipt"] = opened.said["id"], opened.said["receipt"]
                path = f"/runs/{run}/log?offset=0"
                self.assertEqual(self.call(at, "PUT", path, b"123456", **key).status, 507)
                self.assertEqual(self.call(at, "PUT", path, b"1234\n", **key).status, 200)
                self.assertEqual((data / run / f"{run}.log").read_bytes(), b"1234\n")
                self.assertTrue(command.line().endswith(f"Z run {run} opened\n"))
                full = command.line()
                said = f"Z run {run} could not take 6 bytes, holds 0\n"
                self.assertTrue(full.endswith(said), full)
                took = command.line()
                self.assertTrue(took.endswith(f"Z run {run} took 5 bytes, holds 5\n"), took)
                self.assertEqual(command.interrupt(), (0, ""))
                again = self.start("--data", str(data), "--token-file", token_file, *options)
                at = ("127.0.0.1", int(SERVING.fullmatch(again.line())["port"]))
                held = self.call(at, "GET", f"/runs/{run}/log?line=1", **key)
                self.assertEqual(held.said, {"lines": [{"line": 1, "text": "1234"}]})

    def test_an_address_of_the_other_family_is_served_and_logged_as_one_to_call(self):
        try:
            with socket.socket(socket.AF_INET6) as loopback:
                loopback.bind(("::1", 0))
        except OSError:
            self.skipTest("this system has no IPv6 loopback")
        command = self.start(
            "--data", "data", "--token-file", self.token_in(TOKEN), "--bind", "::1", "--port", "0"
        )
        line = command.line()
        serving = SERVING.fullmatch(line)
        self.assertIsNotNone(serving, line)
        self.assertEqual((serving["data"], serving["host"]), ("data", "[::1]"))
        key = {"Authorization": f"Bearer {TOKEN}"}
        answer = self.call(("::1", int(serving["port"])), "GET", "/conversations", **key)
        self.assertEqual(answer.status, 200)

    def test_the_examples_the_readme_gives_of_the_collector_run(self):
        readme = (TOOL / "README.md").read_text()
        section = readme.split("\n## Collecting logs\n")[1].split("\n## ")[0]
        serve, report = map(shlex.split, re.findall(r"```sh\n(.*?)```", section, re.S))
        logged_as = re.search(r"```text\n(.*?)```", section, re.S)[1].splitlines(keepends=True)
        entry = json.loads(re.search(r"```json\n(.*?)```", section, re.S)[1])
        self.assertEqual(serve[:3], ["python3", "tools/forks", "serve"])
        self.assertEqual(report[:3], ["python3", "tools/forks", "report"])

        (self.scratch / "forks-token").write_text(f"{TOKEN}\n")
        command = self.start(*serve[3:], "--port", "0")
        line = command.line()
        serving = SERVING.fullmatch(line)
        self.assertIsNotNone(serving, line)
        at = ("127.0.0.1", int(serving["port"]))
        key = {"Authorization": f"Bearer {TOKEN}"}
        said = json.dumps({"name": entry["name"], "app": entry["app"]})
        opened = self.call(at, "POST", "/runs", said, **key).said
        log = (REAL / "run-split.log").read_bytes()
        path = f"/runs/{opened['id']}/log?offset=0"
        took = self.call(at, "PUT", path, log, **{**key, "Forks-Receipt": opened["receipt"]})
        self.assertEqual(took.said, {"size": len(log), "final": False})
        written = [line, command.line(), command.line()]
        self.assertEqual([shape(line) for line in written], [shape(line) for line in logged_as])

        (held,) = self.call(at, "GET", "/conversations/6e05e5b37e", **key).said["runs"]
        self.assertEqual(list(held), list(entry))
        self.assertEqual(dict(held, id=entry["id"], heard=entry["heard"]), entry)

        logs = [str(log) for word in report[3:] for log in sorted(self.scratch.glob(word))]
        run = held["id"]
        self.assertEqual(logs, [str(self.scratch / "forks-data" / run / f"{run}.log")])
        done = subprocess.run(
            [sys.executable, str(TOOL), "report", *logs], capture_output=True, text=True
        )
        self.assertEqual((done.returncode, done.stderr), (1, ""))
        self.assertTrue(done.stdout.startswith("conversation 6e05e5b37e\n  split at epoch 3\n"))
        self.assertIn(f"  7361726f  {run}  4 ", done.stdout)

    def ended(self, *arguments: str) -> Tuple[int, str]:
        """The status `forks serve` ends with and what it says, were it to serve nobody."""
        said = io.StringIO()
        with contextlib.redirect_stderr(said), mock.patch.object(Server, "serve_forever"):
            try:
                status = main(["serve", "--port", "0", *arguments])
            except SystemExit as stopped:
                status = stopped.code
        return status, said.getvalue()

    def refused(self, *arguments: str) -> str:
        status, said = self.ended(*arguments)
        self.assertEqual(status, 2, said)
        return said

    def test_a_file_that_does_not_hold_a_token_is_refused_and_not_quoted(self):
        data = str(self.scratch / "data")
        tokens = ["", "t" * 15, "t" * 129, "a token of words!", "a-token/of+other=signs"]
        for token in [*tokens, "two-tokens-in-a-file\none-to-a-line"]:
            with self.subTest(token=token):
                said = self.refused("--data", data, "--token-file", self.token_in(token))
                self.assertIn("does not hold a token", said)
                self.assertNotIn(token or "no token", said)
        said = self.refused("--data", data, "--token-file", str(self.scratch / "no such file"))
        self.assertIn("No such file", said)
        self.assertFalse(Path(data).exists())

    def test_the_token_comes_from_no_argument_and_no_variable(self):
        data = str(self.scratch / "data")
        with mock.patch.dict(os.environ, {"FORKS_TOKEN": TOKEN, "TOKEN": TOKEN}):
            self.assertIn("--token-file", self.refused("--data", data))
            self.refused("--data", data, "--token", TOKEN)
        self.assertIn("--data", self.refused("--token-file", self.token_in(TOKEN)))

    def test_told_no_address_the_command_takes_port_8750_of_the_loopback(self):
        served = ["--data", str(self.scratch / "data"), "--token-file", self.token_in(TOKEN)]
        said = io.StringIO()
        # The socket is left as it is made: the port may be taken on this system.
        unbound = mock.patch.multiple(
            Server,
            server_bind=mock.DEFAULT,
            server_activate=mock.DEFAULT,
            serve_forever=mock.DEFAULT,
        )
        with unbound, contextlib.redirect_stderr(said):
            self.assertEqual(main(["serve", *served]), 0)
        self.assertTrue(said.getvalue().endswith(" on http://127.0.0.1:8750/\n"), said.getvalue())

    def test_an_address_that_is_taken_and_a_port_there_is_not_exit_2(self):
        served = ["--data", str(self.scratch / "data"), "--token-file", self.token_in(TOKEN)]
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            said = self.refused(*served, "--port", str(taken.getsockname()[1]))
        self.assertTrue(said.startswith("forks: "), said)
        for port in ("65536", "-1", "many", ""):
            with self.subTest(port=port):
                self.assertIn(f"{port} is not a port", self.refused(*served, "--port", port))
        # The last port there is may be taken on this system, and is a port all the same.
        self.assertNotIn("is not a port", self.ended(*served, "--port", "65535")[1])


if __name__ == "__main__":
    unittest.main()
