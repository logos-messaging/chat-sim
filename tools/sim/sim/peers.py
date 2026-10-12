"""The processes of a run: the mock registry and headless chat-cli peers, read a line at a time."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, IO, List, Optional, Sequence, Tuple

NAMES = ("saro", "raya", "pax", "mira", "kes", "tomo")

# How long a process that was asked to end has before it is killed.
GRACE = 30.0

# How long a wait goes on before it looks again at what it watches.
WATCH = 0.2

# A fork report names an installation by the first four bytes of its key, in hex.
INSTALLATION = 8


class Stopped(Exception):
    """A step of the run did not come about: a wait ran out of budget, or a process ended."""


class OutOfBudget(Stopped):
    """What a wait was for did not come within its budget."""


class Interrupted(BaseException):
    """A signal asked the run to end."""


class Watch:
    """What every wait of a run looks at, so that it ends once it has lost its point."""

    def __init__(self) -> None:
        # The signal that interrupted the run, once one has.
        self.signal: Optional[int] = None
        # The processes whose end stops the run.
        self.processes: List[_Process] = []
        self._closed = False

    def interrupt(self, signum: int) -> None:
        """Takes a signal the run was sent: the first interrupts it, and only while it is open."""
        if self.signal is None and not self._closed:
            self.signal = signum

    def close(self) -> None:
        """From here on a signal interrupts nothing: the run is ending its processes."""
        self._closed = True

    def __call__(self) -> None:
        if self.signal is not None:
            raise Interrupted()
        for process in self.processes:
            if process.ended() is not None:
                raise process.gone()


def name_of(index: int) -> str:
    """The name of the peer started at `index`: six have names, the rest are numbered."""
    return NAMES[index] if index < len(NAMES) else f"peer{index + 1}"


def index_of(name: str) -> Optional[int]:
    """Where the peer `name` is started among the peers, or `None` when no peer has that name."""
    if name in NAMES:
        return NAMES.index(name)
    try:
        index = int(name[len("peer") :]) - 1
    except ValueError:
        return None
    return index if index >= len(NAMES) and name == name_of(index) else None


class _Lines:
    """The lines of a pipe, each stamped with the time it was read and copied to a file."""

    def __init__(self, source: IO[bytes], copy: Path):
        self._queue: "queue.Queue[Optional[Tuple[float, str]]]" = queue.Queue()
        self._thread = threading.Thread(target=self._read, args=(source, copy), daemon=True)
        self._thread.start()

    def _read(self, source: IO[bytes], copy: Path) -> None:
        with source, open(copy, "wb") as kept:
            for raw in source:
                at = time.time()
                kept.write(raw)
                kept.flush()
                self._queue.put((at, raw.decode("utf-8", "replace").rstrip("\n")))
        self._queue.put(None)

    def join(self) -> None:
        """Waits until every line the process wrote before it ended is in the copy."""
        self._thread.join(GRACE)

    def next(self, deadline: float, watch: Watch) -> Optional[Tuple[float, str]]:
        """The next line and when it was read, or `None` at the end of the pipe.

        `watch` is looked at before the wait and every `WATCH` seconds of it.
        """
        while True:
            watch()
            left = deadline - time.monotonic()
            try:
                return self._queue.get(timeout=max(min(left, WATCH), 0))
            except queue.Empty:
                if left <= WATCH:
                    raise OutOfBudget("no line within the budget") from None


def _end(process: subprocess.Popen, deadline: float) -> bool:
    """Waits for `process` to end by `deadline`, and kills it when it has not: `True` then."""
    try:
        process.wait(timeout=max(deadline - time.monotonic(), 0))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return True
    return False


class _Process:
    """A process of the run: the lines it writes, copied to `<stem>.out`, and its standard error."""

    def __init__(
        self,
        name: str,
        stem: str,
        command: Sequence[str],
        directory: Path,
        watch: Watch,
        stdin: int,
        env: Optional[dict] = None,
    ):
        self.name = name
        self.stderr = directory / f"{stem}.stderr"
        self.out = directory / f"{stem}.out"
        self._watch = watch
        with open(self.stderr, "wb") as stderr:
            try:
                self._process = subprocess.Popen(
                    command, stdin=stdin, stdout=subprocess.PIPE, stderr=stderr, env=env
                )
            except OSError as error:
                raise Stopped(f"{name} could not be started: {error}") from None
        self._lines = _Lines(self._process.stdout, self.out)

    def ended(self) -> Optional[int]:
        """The status the process ended with, or `None` while it runs."""
        return self._process.poll()

    def gone(self) -> Stopped:
        """Why the run stops once the process has ended."""
        status = self._process.returncode
        how = f"status {status}" if status >= 0 else f"signal {-status}"
        return Stopped(f"{self.name} ended with {how}: see {self.stderr}")

    def _line(self, what: str, deadline: float) -> Tuple[float, str]:
        """The next line and when it was read. `what` names the line in the reason there is none."""
        try:
            line = self._lines.next(deadline, self._watch)
        except OutOfBudget:
            raise OutOfBudget(f"{self.name}: no {what} within the budget") from None
        if line is None:
            # A process closes its output by ending.
            try:
                self._process.wait(timeout=WATCH)
            except subprocess.TimeoutExpired:
                raise Stopped(f"{self.name}: its output ended before {what}") from None
            raise self.gone()
        return line


class Registry(_Process):
    """The mock registry on a port the system picked, writing each request it serves to a file."""

    def __init__(self, script: Path, directory: Path, watch: Watch):
        self.requests = directory / "registry.requests"
        self.url = ""
        super().__init__(
            "the registry",
            "registry",
            [sys.executable, "-B", str(script), "--port", "0", "--requests", str(self.requests)],
            directory,
            watch,
            subprocess.DEVNULL,
        )

    def read_url(self, deadline: float) -> None:
        """Reads the first line, which is the address the registry serves on."""
        _, self.url = self._line("address", deadline)

    def served(self) -> List[dict]:
        """Every request served so far, in the order they arrived."""
        with open(self.requests, encoding="utf-8") as lines:
            return [json.loads(line) for line in lines if line.strip()]

    def stop(self) -> None:
        self._process.terminate()
        _end(self._process, time.monotonic() + GRACE)


class Peer(_Process):
    """A headless chat-cli: its inputs, the lines it has written, and its log."""

    def __init__(self, name: str, command: Sequence[str], directory: Path, env: dict, watch: Watch):
        self.log = directory / f"{name}.log"
        self.started_at = time.time()
        command = [*command, "--log-file", str(self.log)]
        super().__init__(name, name, command, directory, watch, subprocess.PIPE, env)
        self._unclaimed: List[Tuple[float, dict]] = []
        self._inputs = 0
        self.address = ""
        self.installation = ""
        self.node_address = ""
        self.ready_at = 0.0
        # What it stamps on the frames it publishes to the group, once a line of its told it.
        self.app_id: Optional[str] = None

    def _next(self, what: str, deadline: float) -> Tuple[float, dict]:
        read_at, text = self._line(what, deadline)
        try:
            line = json.loads(text)
        except ValueError:
            line = None
        if not isinstance(line, dict):
            raise Stopped(f"{self.name}: a line that is no JSON object: {text[:80]}")
        # The result of `/new` tells it to the creator, and its `joined` line to a joiner.
        if line.get("type") in ("result", "joined") and "app_id" in line:
            self.app_id = line["app_id"]
        return read_at, line

    def wait_for(
        self, what: str, wanted: Callable[[dict], bool], deadline: float
    ) -> Tuple[float, dict]:
        """The first line `wanted` accepts that no wait has claimed, and when it was read."""
        for at, (read_at, line) in enumerate(self._unclaimed):
            if wanted(line):
                del self._unclaimed[at]
                return read_at, line
        while True:
            read_at, line = self._next(what, deadline)
            if wanted(line):
                return read_at, line
            self._unclaimed.append((read_at, line))

    def read_ready(self, deadline: float) -> None:
        """Reads the first line, which says the peer is ready and where its node is dialed."""
        self.ready_at, ready = self._next("ready line", deadline)
        if ready.get("type") != "ready" or "node_address" not in ready:
            raise Stopped(f"{self.name}: its first line names no node: {ready}")
        self.address = ready["address"]
        self.installation = ready["installation"][:INSTALLATION]
        self.node_address = ready["node_address"]

    def read_app_id(self, deadline: float) -> str:
        """Reads on until a line has told the peer's app id, and returns it."""
        while self.app_id is None:
            self._unclaimed.append(self._next("app id", deadline))
        return self.app_id

    def input(self, text: str, deadline: float) -> Tuple[float, float, dict]:
        """Writes one input. Returns when it was written, when its result was read, the result."""
        self._watch()
        self._inputs += 1
        number = self._inputs
        sent_at = time.time()
        try:
            self._process.stdin.write(text.encode("utf-8") + b"\n")
            self._process.stdin.flush()
        except OSError as error:
            raise Stopped(f"{self.name}: its input is closed: {error}") from None
        read_at, result = self.wait_for(
            f"result of {text.split(' ', 1)[0]}",
            lambda line: line.get("type") == "result" and line.get("input") == number,
            deadline,
        )
        return sent_at, read_at, result

    def run(self, text: str, deadline: float) -> dict:
        """An input that has to work: returns its result."""
        _, _, result = self.input(text, deadline)
        if result.get("ok") is not True:
            command = text.split(" ", 1)[0]
            raise Stopped(f"{self.name}: {command} was refused: {result.get('error')}")
        return result

    def node(self, deadline: float) -> dict:
        """What the peer's delivery node says of itself."""
        return self.run("/node", deadline)["node"]

    def close(self) -> None:
        """Ends the peer's input, which is how it is asked to end."""
        try:
            self._process.stdin.close()
        except OSError:
            pass

    def reap(self, deadline: float) -> bool:
        """Waits for the peer to end, killing it at `deadline`: `True` when it was killed."""
        return _end(self._process, deadline)

    def written(self) -> List[dict]:
        """Every line the peer wrote, once it has ended. One that is no JSON object is left out."""
        self._lines.join()
        lines = []
        for text in self.out.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                line = json.loads(text)
            except ValueError:
                continue
            if isinstance(line, dict):
                lines.append(line)
        return lines
