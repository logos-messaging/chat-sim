"""Serves a collector over HTTP: the calls an uploader and the report page make.

Every call carries the collector's one token, and a write to a run carries that run's receipt.
Nothing is answered before the token is checked but a request that is not HTTP.
"""

from __future__ import annotations

import hmac
import json
import re
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote

from .collector import Collector, Refused

# What a share address can carry as its token.
TOKEN = re.compile(r"[A-Za-z0-9_-]{16,128}")
# An uploader sends at most a quarter of this a request.
MAX_BODY = 4 * 1024 * 1024
# As much of a body that is not kept is in memory at a time.
_PIECE = 64 * 1024
_NUMBER = re.compile(r"[0-9]{1,20}")
_Answer = Tuple[int, object]


class Server(ThreadingHTTPServer):
    """A collector on a socket, a thread for each connection."""

    def __init__(self, address: Tuple[str, int], collector: Collector, token: str) -> None:
        self.collector, self.token = collector, token.encode()
        # What the letting go between requests last failed of, said once while it lasts.
        self._failed: Optional[str] = None
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, Handler)

    def service_actions(self) -> None:
        # Between requests too: a collector nobody calls still lets go on time. What fails
        # here is no call's failure, and ends the serving of none.
        try:
            self.collector.expire()
            failed = None
        except Exception as error:
            failed = _failure(error)
            if failed != self._failed:
                self.collector.say(f"letting go failed: {failed}")
        self._failed = failed

    def handle_error(self, request: object, client_address: object) -> None:
        self.collector.say(f"a call failed: {_failure(sys.exc_info()[1])}")


class Handler(BaseHTTPRequestHandler):
    """Answers one request, and closes the connection."""

    server: Server
    # A request is over within an uploader's tick of 30 seconds: a connection silent for as long
    # is closed.
    timeout = 30
    # Whether the request showed the token.
    known = False

    def __getattr__(self, name: str) -> Callable[[], None]:
        # Every method comes here, so that none is answered before the token is checked.
        if name.startswith("do_"):
            return self._answer
        raise AttributeError(name)

    def handle(self) -> None:
        try:
            super().handle()
        except ConnectionError:
            # A peer that went has failed no call.
            pass
        except Exception:
            # Nor is the log a stranger's to write in: only a call with the token fails in it.
            if self.known:
                raise

    def _answer(self) -> None:
        self.known = self._has_token()
        try:
            if not self.known:
                self._drop_body()
                raise Refused(401, error="this call needs the collector's token")
            status, said = self._call(self._body())
        except Refused as refused:
            status, said = refused.status, refused.said
        self._send(status, said)

    def _length(self) -> int:
        """The length the request gives its body: one, in digits, within the most a body may be."""
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            if self.command in ("POST", "PUT"):
                raise Refused(411, error="a body must say its length")
            return 0
        # A transfer coding beside a length would have the body end somewhere else.
        coded = "Transfer-Encoding" in self.headers
        if coded or len(lengths) > 1 or not _NUMBER.fullmatch(lengths[0]):
            raise Refused(400, error="the length of the body cannot be read")
        if int(lengths[0]) > MAX_BODY:
            raise Refused(413, error="a body holds at most 4 MiB")
        return int(lengths[0])

    def _body(self) -> bytes:
        """The request's body, whole."""
        length = self._length()
        body = self.rfile.read(length)
        if len(body) < length:
            raise Refused(400, error="the body is shorter than its length")
        return body

    def _drop_body(self) -> None:
        """Reads the body of a call without the token, and keeps none of it.

        Its sender could not read the answer while sending. A body that would be refused for its
        length is left unread, and so is what has not come when a request is to be over.
        """
        try:
            left = self._length()
        except Refused:
            return
        over = time.monotonic() + self.timeout
        try:
            while left:
                wait = over - time.monotonic()
                if wait <= 0:
                    break
                self.connection.settimeout(wait)
                # One read of the socket a time, so that the wait is told before each.
                piece = self.rfile.read1(min(left, _PIECE))
                if not piece:
                    break
                left -= len(piece)
        except socket.timeout:
            pass
        finally:
            self.connection.settimeout(self.timeout)

    def _has_token(self) -> bool:
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(token.encode(), self.server.token)

    def _call(self, body: bytes) -> _Answer:
        path, _, query = self.path.partition("?")
        # A part is decoded after the path is cut, so that an encoded slash stays in its part.
        parts = tuple(unquote(part) for part in path.split("/")[1:])
        asked = parse_qs(query, keep_blank_values=True)
        # The second part of a path is an id: a call is its method and the parts around that.
        where = parts[:1] + parts[2:]
        call = self.command, len(parts), where
        if call == ("POST", 1, ("runs",)):
            return self._open(body)
        if call == ("PUT", 3, ("runs", "log")):
            return self._append(parts[1], asked, body)
        if call == ("GET", 3, ("runs", "log")):
            return self._lines(parts[1], asked)
        if call == ("DELETE", 2, ("runs",)):
            self.server.collector.delete(parts[1], self.headers.get("Forks-Receipt"))
            return 204, None
        if call == ("GET", 1, ("conversations",)):
            return 200, self.server.collector.conversations()
        if call == ("GET", 2, ("conversations",)):
            return 200, self.server.collector.conversation(parts[1])
        raise Refused(404, error="the collector has no such call")

    def _open(self, body: bytes) -> _Answer:
        try:
            said = json.loads(body)
        except (ValueError, RecursionError):
            said = None
        name, app = (_label(said, key) for key in ("name", "app"))
        if name is None or app is None:
            raise Refused(400, error="a run needs a name and an app, each 1 to 64 characters")
        run, receipt = self.server.collector.open_run(name, app)
        return 201, {"id": run, "receipt": receipt}

    def _append(self, run: str, asked: Dict[str, List[str]], body: bytes) -> _Answer:
        offset, final = _number(asked, "offset"), asked.get("final")
        if offset is None or final not in (None, ["1"]):
            raise Refused(400, error="an append needs offset=N, and final=1 or no final")
        receipt = self.headers.get("Forks-Receipt")
        return 200, self.server.collector.append(run, receipt, offset, body, final is not None)

    def _lines(self, run: str, asked: Dict[str, List[str]]) -> _Answer:
        line = _number(asked, "line")
        if not line:
            raise Refused(400, error="the lines around a line need line=N, from 1")
        return 200, {"lines": self.server.collector.lines_around(run, line)}

    def _send(self, status: int, said: object) -> None:
        """Answers a status and, unless there is nothing to say, a body of JSON."""
        self.send_response_only(status)
        if status == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        if said is None:
            self.end_headers()
            return
        body = said if isinstance(said, bytes) else json.dumps(said).encode()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            # In as many sends as its reader takes: the timeout is on each, so on the reader's
            # silence. One send of the whole would end under it however steadily it was read.
            unsent = memoryview(body)
            while unsent:
                unsent = unsent[self.connection.send(unsent) :]

    def send_error(self, code: int, message: Optional[str] = None, explain: object = None) -> None:
        # What the standard library refuses before a call is read is answered like the rest,
        # and without its message, which quotes the request.
        self._send(code, {"error": "the request cannot be read"})

    def log_message(self, format: str, *args: object) -> None:
        # The collector's log holds its own lines, and none of the standard library's.
        pass


def _failure(error: Optional[BaseException]) -> str:
    """What failed, in a line: a traceback could quote what a call carried."""
    return str(error) if isinstance(error, OSError) else type(error).__name__


def _label(said: object, key: str) -> Optional[str]:
    """A name as a run may carry it: 1 to 64 characters once trimmed."""
    value = said.get(key) if isinstance(said, dict) else None
    if isinstance(value, str) and 1 <= len(value.strip()) <= 64:
        return value.strip()
    return None


def _number(asked: Dict[str, List[str]], key: str) -> Optional[int]:
    values = asked.get(key, [])
    if len(values) == 1 and _NUMBER.fullmatch(values[0]):
        return int(values[0])
    return None
