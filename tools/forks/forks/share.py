"""Sends the runs in some paths to a collector, once: what `forks share` does.

A run goes as one stream, its parts in order under one offset, into a run the collector opened
for it. The id the collector gave that run and the receipt its writes must carry are remembered
in a file beside the logs, so that the next go adds to the same run. No offset is kept: a request
is sent, and the collector's answer says how much of the run it holds.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import queue
import re
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import unquote, urlsplit

from .runs import InputError, find_runs, left_out
from .serve import TOKEN

# What a run sent from a terminal gives as its application.
APP = "forks share"
# The file that remembers the runs of the directory it is in.
REMEMBERED = ".forks-share.json"
# The most bytes of a run that go in one request.
PIECE = 1024 * 1024
# A request is given up after this many seconds, the time a collector gives a connection that
# has fallen silent.
REQUEST = 30
# The longest name a run may carry.
_NAME = 64
# An id or a receipt, which a client takes as the collector made it.
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# What an address is written in: nothing a request line cannot carry.
_WRITTEN = re.compile(r"[!-~]*")
# As much of an answer is read. One a call promises is under 200 bytes, an id and a receipt of
# 64 characters each.
_ANSWER = 4096


class NoAddress(Exception):
    """Why a share address is refused. It never quotes the address, which holds the token."""


class _Fault(Exception):
    """Why a run is not whole on the collector, in a line; `stop` when no run would fare better."""

    def __init__(self, said: str, stop: bool = False) -> None:
        super().__init__(said)
        self.stop = stop


class _Gone(Exception):
    """The collector no longer has a run."""


class _Unkept(Exception):
    """Why nothing is sent from a directory: its runs cannot be remembered, or another go has it."""


@dataclass(frozen=True)
class Address:
    """A collector as its share address names it."""

    # Everything before the query, a slash at its end: each call is made under it.
    base: str
    token: str

    @property
    def host(self) -> str:
        """What the collector is called where it is printed: its host, and its port if given."""
        return urlsplit(self.base).netloc


def address_of(text: str) -> Address:
    """The collector a share address names."""
    if not _WRITTEN.fullmatch(text):
        raise NoAddress("the address holds a character no address is written in")
    if "#" in text:
        raise NoAddress("the address holds a fragment")
    base, _, query = text.partition("?")
    try:
        at = urlsplit(base)
        host, _ = at.hostname, at.port
        # As a socket reads a host it is to look up: no label is empty or longer than 63.
        (host or "").encode("idna")
    except ValueError:
        raise NoAddress("the address holds no host and port that can be read") from None
    if at.scheme not in ("http", "https"):
        raise NoAddress("the address is not http or https")
    if "@" in at.netloc:
        raise NoAddress("the address holds a user or a password")
    if not host:
        raise NoAddress("the address names no host")
    asked = [pair.partition("=") for pair in query.split("&")]
    tokens = [(key, token) for key, _, token in asked if unquote(key) == "token"]
    if not tokens:
        raise NoAddress("the address holds no token")
    if len(tokens) > 1:
        raise NoAddress("the address holds more than one token")
    ((key, token),) = tokens
    if key != "token" or "%" in token:
        raise NoAddress("the address spells its token with an escape")
    if not TOKEN.fullmatch(token):
        raise NoAddress(
            "the address holds a token that is not 16 to 128 characters of A-Z, a-z, 0-9, _ and -"
        )
    return Address(base if base.endswith("/") else base + "/", token)


@dataclass(frozen=True)
class _Part:
    """A file of a run's log as it was when the go began: which file its path named, how long."""

    path: Path
    file: Tuple[int, int]
    size: int

    def read(self, start: int, count: int) -> bytes:
        try:
            with open(self.path, "rb") as file:
                held = os.fstat(file.fileno())
                file.seek(start)
                data = file.read(count)
        except OSError as error:
            raise _Fault(f"its log cannot be read: {error}") from None
        # A writer that moved the log aside left the path to another file. Bytes of that one
        # would go to the collector as bytes of this.
        if (held.st_dev, held.st_ino) != self.file or len(data) != count:
            raise _Fault(f"{self.path.name} was moved or cut while it was sent")
        return data


@dataclass(frozen=True)
class _Log:
    """A run's log as the disk held it when the go began."""

    name: str
    parts: Tuple[_Part, ...]

    @property
    def directory(self) -> Path:
        return self.parts[-1].path.parent

    @property
    def size(self) -> int:
        return sum(part.size for part in self.parts)

    def read(self, offset: int) -> bytes:
        """The bytes of the stream from an offset on, as many as go in one request."""
        pieces: List[bytes] = []
        room = PIECE
        for part in self.parts:
            if offset >= part.size:
                offset -= part.size
            elif room:
                pieces.append(part.read(offset, min(room, part.size - offset)))
                room -= len(pieces[-1])
                offset = 0
        return b"".join(pieces)


class _Alone(contextlib.ExitStack):
    """The directories a go sends from, each held against any other go until this one is over.

    Two goes at a time would each open a run for a log, and each replace the other's file.
    """

    def __init__(self) -> None:
        super().__init__()
        self._held: Set[Path] = set()

    def hold(self, directory: Path) -> None:
        if directory in self._held:
            return
        try:
            # Imported here and not at the top, so that only this command needs a Python that
            # has it.
            import fcntl

            lock = os.open(directory, os.O_RDONLY)
            self.callback(os.close, lock)
            # On the directory itself, which leaves no file behind and is let go of by the
            # system when the go ends, however it ends.
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            raise _Unkept("this Python has no `flock` to hold a directory by") from None
        except BlockingIOError:
            raise _Unkept("another `forks share` is sending from the directory") from None
        except OSError as error:
            raise _Unkept(str(error)) from None
        self._held.add(directory)


class _Remembered:
    """The file that remembers a directory's runs.

    For each collector, by its base, and each run, by its name: the run's id and its receipt. A
    token is never in it.
    """

    def __init__(self, path: Path, base: str) -> None:
        self._path = path
        try:
            self._kept = _kept_in(path)
        except OSError as error:
            raise _Unkept(str(error)) from None
        # The runs on the collector this go sends to. Those on any other stay as they are.
        self._runs = self._kept.setdefault(base, {})

    def of(self, name: str) -> Optional[Tuple[str, str]]:
        """The id and the receipt of the run a log was sent as."""
        entry = self._runs.get(name)
        return entry and (entry["id"], entry["receipt"])

    def keep(self, name: str, entry: Tuple[str, str]) -> None:
        self._runs[name] = {"id": entry[0], "receipt": entry[1]}

    def drop(self, name: str) -> None:
        del self._runs[name]

    def save(self) -> None:
        """Replaces the file in one step: a stop leaves the old one or the new."""
        kept = {base: runs for base, runs in self._kept.items() if runs}
        scratch = self._path.with_suffix(".new")
        # Made anew, for its owner alone: a receipt is the right to add to a run and to remove it.
        made = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            scratch.unlink(missing_ok=True)
            with open(os.open(scratch, made, 0o600), "w") as file:
                file.write(json.dumps(kept, indent=2) + "\n")
            os.replace(scratch, self._path)
        except OSError as error:
            raise _Unkept(str(error)) from None


class _Calls:
    """The calls an uploader makes on one collector, which a fault names by its host."""

    def __init__(self, address: Address) -> None:
        at = urlsplit(address.base)
        secure = at.scheme == "https"
        self._connection = http.client.HTTPSConnection if secure else http.client.HTTPConnection
        # The port is always said: a host that is an IPv6 address would be cut at its last colon.
        self._at = at.hostname, self._connection.default_port if at.port is None else at.port
        self._under = at.path
        # Sent in a header and never in the path, which is the part of a request that gets quoted.
        self._token = address.token
        self.host = address.host

    def open(self, name: str) -> Tuple[str, str]:
        """Opens a run under a name: its id, and the receipt its writes must carry."""
        body = json.dumps({"name": name, "app": APP}).encode()
        status, said = self._call("POST", "runs", body, {"Content-Type": "application/json"})
        if status != 201:
            raise _Fault(f"{self.host} answered {status}", stop=True)
        if not _is_entry(said):
            raise self._unpromised(status)
        run, receipt = said["id"], said["receipt"]
        # They are remembered beside the logs, where the token is never to be.
        if self._token in run or self._token in receipt:
            raise self._unpromised(status)
        return run, receipt

    def put(self, entry: Tuple[str, str], offset: int, data: bytes) -> Tuple[int, int]:
        """Sends bytes of a run at an offset: the status answered, 200 or 409, and the size held."""
        run, receipt = entry
        headers = {"Forks-Receipt": receipt, "Content-Type": "application/octet-stream"}
        status, said = self._call("PUT", f"runs/{run}/log?offset={offset}", data, headers)
        if status == 404:
            raise _Gone
        if status == 403:
            raise _Fault(f"{self.host} refused the run's receipt")
        if status == 413:
            raise _Fault(f"{self.host} takes no request of {len(data)} bytes")
        if status == 507:
            raise _Fault(f"{self.host} holds as many bytes as it may", stop=True)
        if status not in (200, 409):
            raise _Fault(f"{self.host} answered {status}", stop=True)
        size = said.get("size") if isinstance(said, dict) else None
        if type(size) is not int or size < 0 or (status == 200 and size != offset + len(data)):
            raise self._unpromised(status)
        return status, size

    def _unpromised(self, status: int) -> _Fault:
        return _Fault(f"{self.host} answered {status} and not what the call promises", stop=True)

    def _call(
        self, method: str, call: str, body: bytes, headers: Dict[str, str]
    ) -> Tuple[int, object]:
        """Makes a call under the base: the status answered, and what the body says as JSON."""
        sent = dict(headers, Authorization=f"Bearer {self._token}", Connection="close")
        answered: "queue.Queue[object]" = queue.Queue()
        # On a thread of its own, left behind when the time is up. A socket's timeout is for each
        # wait, and a collector that sent a byte now and then would never run into it.
        threading.Thread(
            target=self._answer,
            args=(answered, method, self._under + call, body, sent),
            daemon=True,
        ).start()
        try:
            answer = answered.get(timeout=REQUEST)
        except queue.Empty:
            raise _Fault(f"{self.host} gave no answer in {REQUEST} seconds", stop=True) from None
        if isinstance(answer, OSError):
            raise _Fault(f"{self.host} could not be called: {answer}", stop=True)
        if isinstance(answer, http.client.HTTPException):
            # What it sent stays out of the line: nothing says what a server puts in an answer.
            raise _Fault(f"{self.host} gave an answer that cannot be read", stop=True)
        if isinstance(answer, Exception):
            # Named and not quoted: only an error of the system's is known to quote no request.
            raise _Fault(f"{self.host} could not be called: {type(answer).__name__}", stop=True)
        status, said = answer
        if status == 401:
            raise _Fault(f"{self.host} refused the token", stop=True)
        try:
            return status, json.loads(said)
        except (ValueError, RecursionError):
            return status, None

    def _answer(
        self, answered: "queue.Queue[object]", method: str, target: str, body: bytes, headers: dict
    ) -> None:
        """Makes a request, and puts what it was answered, or what kept it from one, in a queue."""
        try:
            connection = self._connection(*self._at)
            try:
                connection.request(method, target, body, headers)
                answer = connection.getresponse()
                answered.put((answer.status, answer.read(_ANSWER)))
            finally:
                connection.close()
        except Exception as error:
            # Whatever it is: a thread that ended without a word would be waited for in vain.
            answered.put(error)


def send(
    address: Address,
    paths: Sequence[Path],
    say: Callable[[str], None],
    complain: Callable[[str], None],
) -> int:
    """Sends every run in the paths to a collector, once, and answers the exit status.

    0 when the collector then holds every run whole, 1 when it does not, 2 when nothing could be
    sent from a directory. A line is said of each run the collector answered for, and a complaint
    is made of each that is not whole there.
    """
    logs = _logs(paths)
    calls = _Calls(address)
    status = 0
    # The directories that another go has or whose runs cannot be remembered: no more is sent
    # from them.
    unkept: Set[Path] = set()
    with _Alone() as alone:
        for at, log in enumerate(logs):
            if log.directory in unkept:
                continue
            try:
                alone.hold(log.directory)
                _send(calls, _Remembered(log.directory / REMEMBERED, address.base), log, say)
            except _Fault as fault:
                status = max(status, 1)
                if fault.stop:
                    left = len(logs) - at - 1
                    complain(f"{log.name}: {fault}" + (f", runs not tried: {left}" if left else ""))
                    return status
                complain(f"{log.name}: {fault}")
            except _Unkept as why:
                unkept.add(log.directory)
                complain(f"{why}: no more is sent from {log.directory}")
                status = 2
    return status


def _send(calls: _Calls, kept: _Remembered, log: _Log, say: Callable[[str], None]) -> None:
    """Sends a run until the collector holds what the disk held of it, and says how much that is."""
    entry = kept.of(log.name)
    opened = entry is None
    if entry is None:
        entry = _open(calls, kept, log)
    # A run opened just now is empty. Of a remembered one the collector is asked, by a request
    # without bytes at the size the disk has: 200 says it holds the run whole, 409 how much.
    offset = 0 if opened else log.size
    sent, held, refused = 0, None, False
    try:
        while held != log.size:
            data = log.read(offset)
            try:
                status, held = calls.put(entry, offset, data)
            except _Gone:
                if opened:
                    raise _Fault(f"{calls.host} answered 404", stop=True) from None
                kept.drop(log.name)
                # Before the run is opened again: if it is not, no size is said of the one gone.
                offset, held, refused = 0, None, False
                entry, opened = _open(calls, kept, log), True
                continue
            if status == 200:
                sent += len(data)
            elif held > log.size:
                raise _Fault(f"{calls.host} holds {held} bytes and the disk has {log.size}")
            elif refused:
                # One refusal is how the size is learned. A second means the run grows on the
                # collector by another hand, or takes no more.
                raise _Fault(f"{calls.host} took no bytes at the size it said it holds")
            else:
                refused = True
            offset = held
    finally:
        if held is not None:
            say(f"{log.name}: sent {sent} bytes, {calls.host} holds {held} of {log.size}")


def _open(calls: _Calls, kept: _Remembered, log: _Log) -> Tuple[str, str]:
    """Opens a run for a log, and remembers it."""
    # The file is written first: a directory that cannot keep it has no run opened in its name,
    # which the next go would open once more.
    kept.save()
    entry = calls.open(log.name.strip()[:_NAME])
    kept.keep(log.name, entry)
    kept.save()
    return entry


def _logs(paths: Sequence[Path]) -> List[_Log]:
    """Every run in the paths as the disk holds it now, in the order `report` reads them."""
    while True:
        runs = find_runs(paths)
        logs = [_Log(run.name, tuple(map(_part, run.parts))) for run in runs]
        unnamed = [(file, run.name) for run in runs for file in left_out(run)]
        # Listed again: a writer that moved a log aside in between left the first listing with
        # a name for the wrong file.
        if find_runs(paths) == runs:
            break
    for log in logs:
        if not log.name.strip():
            raise InputError(f"{log.parts[-1].path} has no name a run could carry")
    if unnamed:
        # A run is remembered by its name, which is to stand for one stream whatever the paths.
        file, name = unnamed[0]
        raise InputError(
            f"{file} is a part of the run {name} and not among the paths: a run is sent whole"
        )
    return logs


def _part(path: Path) -> _Part:
    """A file of a log as it is now."""
    held = path.stat()
    if not stat.S_ISREG(held.st_mode):
        raise InputError(f"{path} is not a file, and a log is sent from one")
    # Opened, so that a log that cannot be read is found before anything is sent.
    open(path, "rb").close()
    return _Part(path, (held.st_dev, held.st_ino), held.st_size)


def _kept_in(path: Path) -> Dict[str, Dict[str, Dict[str, str]]]:
    """What a remembered file holds. One that is not as `share` writes it is refused."""
    try:
        written = path.read_bytes()
    except FileNotFoundError:
        return {}
    try:
        kept = json.loads(written, object_pairs_hook=_said_once)
    except (ValueError, RecursionError):
        kept = None
    if not _is_kept(kept):
        raise _Unkept(f"{path} is not a file `forks share` wrote")
    return kept


def _said_once(pairs: List[Tuple[str, object]]) -> Dict[str, object]:
    """An object of JSON none of whose keys is said twice: a reader keeps the last and no other."""
    said = dict(pairs)
    if len(said) != len(pairs):
        raise ValueError("a key is said twice")
    return said


def _is_kept(kept: object) -> bool:
    """Whether a file says what `share` remembers: an id and a receipt, by base and by name."""
    if not isinstance(kept, dict) or not all(isinstance(runs, dict) for runs in kept.values()):
        return False
    return all(
        _is_entry(entry) and len(entry) == 2 for runs in kept.values() for entry in runs.values()
    )


def _is_entry(said: object) -> bool:
    """Whether an object gives a run's id and its receipt, each as a collector makes them."""
    if not isinstance(said, dict):
        return False
    words = said.get("id"), said.get("receipt")
    return all(isinstance(word, str) and _OPAQUE.fullmatch(word) for word in words)
