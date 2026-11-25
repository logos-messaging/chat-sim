"""The runner of a run, the names of its members, and what every wait of the run looks at."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

NAMES = ("saro", "raya", "pax", "mira", "kes", "tomo")

# How long a process that was asked to end has before it is killed.
GRACE = 30.0

# How long a wait goes on before it looks again at what it watches.
WATCH = 0.2


class Stopped(Exception):
    """A step of the run did not come about: the runner could not be started, or it ended."""


class Interrupted(BaseException):
    """A signal asked the run to end."""


class Watch:
    """What every wait of a run looks at, so that it ends once it has lost its point."""

    def __init__(self) -> None:
        # The signal that interrupted the run, once one has.
        self.signal: Optional[int] = None
        self._closed = False

    def interrupt(self, signum: int) -> None:
        """Takes a signal the run was sent: the first interrupts it, and only while it is open."""
        if self.signal is None and not self._closed:
            self.signal = signum

    def close(self) -> None:
        """From here on a signal interrupts nothing: the run is ending its runner."""
        self._closed = True

    def __call__(self) -> None:
        if self.signal is not None:
            raise Interrupted()


def name_of(index: int) -> str:
    """The name of the member at `index`: six have names, the rest are numbered."""
    return NAMES[index] if index < len(NAMES) else f"peer{index + 1}"


def index_of(name: str) -> Optional[int]:
    """Where the member `name` is among the members, or `None` when no member has that name."""
    if name in NAMES:
        return NAMES.index(name)
    try:
        index = int(name[len("peer") :]) - 1
    except ValueError:
        return None
    return index if index >= len(NAMES) and name == name_of(index) else None


class Runner:
    """The runner of a plan: one process that does the plan and leaves a log and a result.

    What it writes is kept in the run's directory, as `runner.out` and `runner.stderr`.
    """

    def __init__(self, program: Path, plan: Path, directory: Path, watch: Watch):
        self.stderr = directory / "runner.stderr"
        self._watch = watch
        command = [str(program), str(plan), str(directory)]
        with open(directory / "runner.out", "wb") as out, open(self.stderr, "wb") as stderr:
            try:
                self._process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=out, stderr=stderr
                )
            except OSError as error:
                raise Stopped(f"the runner could not be started: {error}") from None

    def ended(self) -> Optional[int]:
        """The status the runner ended with, or `None` while it runs. A signal is below 0."""
        return self._process.poll()

    def gone(self) -> Stopped:
        """Why the run stops once the runner has ended."""
        status = self.ended()
        how = f"status {status}" if status >= 0 else f"signal {-status}"
        return Stopped(f"the runner ended with {how}: {self._why()}")

    def _why(self) -> str:
        """Why the runner ended: the last three lines of its standard error, or that it has none."""
        complained = self.stderr.read_text(encoding="utf-8", errors="replace").splitlines()
        said = [line for line in complained if line.strip()]
        return " / ".join(said[-3:]) if said else "it said nothing on standard error"

    def wait(self) -> None:
        """Waits until the runner has ended. Its budget is of its own clock, and bounds no wait."""
        while True:
            self._watch()
            try:
                self._process.wait(timeout=WATCH)
                return
            except subprocess.TimeoutExpired:
                pass

    def stop(self) -> None:
        """Ends the runner, and kills it when it has not ended within `GRACE`."""
        self._process.terminate()
        try:
            self._process.wait(timeout=GRACE)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
