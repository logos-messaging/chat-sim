"""Turns files and directories into runs.

A run is one stream of log bytes: one file, or the parts a writer moved aside as `NAME.001.log`,
`NAME.002.log` and so on, in that order, and then `NAME.log`, the file it is still writing.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .records import Reading, read_lines

# Three digits, as logos-chat-module reads the names of its own parts.
_PART = re.compile(r"(?P<run>.+)\.(?P<number>[0-9]{3})\.log")
# A file's place among its run's files: the parts by number, then the file still being written.
_Place = Tuple[bool, int]
_STILL_WRITTEN: _Place = (True, 0)


class InputError(Exception):
    """The paths given do not make a set of runs."""


@dataclass(frozen=True)
class RunLog:
    """A run's name and the files that hold its log, in the order they were written."""

    name: str
    parts: Tuple[Path, ...]


def find_runs(paths: Iterable[Path]) -> List[RunLog]:
    """The runs in `paths`, by name: a file is a log, a directory holds one in each `.log` file."""
    # Two spellings of one file are one log. What is not a file, a pipe for one, is read as given:
    # its link resolves to a name nothing can open.
    files = {file.resolve() if file.is_file() else file for path in paths for file in _logs(path)}
    parts: Dict[Tuple[Path, str], Dict[_Place, Path]] = defaultdict(dict)
    for file in sorted(files):
        name, place = _place(file.name)
        other = parts[file.parent, name].setdefault(place, file)
        if other != file:
            raise InputError(f"two files are the same part of the run {name}: {other}, {file}")
    runs = sorted(
        (RunLog(name, _in_order(found)) for (_, name), found in parts.items()),
        key=lambda run: (run.name, run.parts),
    )
    shared = [name for name, count in Counter(run.name for run in runs).items() if count > 1]
    if shared:
        logs = ", ".join(str(run.parts[-1]) for run in runs if run.name == shared[0])
        raise InputError(f"more than one run is named {shared[0]}: {logs}")
    return runs


def left_out(run: RunLog) -> List[Path]:
    """The `.log` files of a run's directory that are parts of the run and not among its own.

    A run found by the files that were named may lack them; found by its directory, it has them.
    """
    directory = run.parts[-1].parent
    beside = {file.resolve() for file in _held(directory)} - set(run.parts)
    return sorted(
        file for file in beside if file.parent == directory and _place(file.name)[0] == run.name
    )


def _in_order(found: Dict[_Place, Path]) -> Tuple[Path, ...]:
    return tuple(found[place] for place in sorted(found))


def read_run(run: RunLog) -> Reading:
    """Reads a run's parts as the one stream they were written as."""
    return read_lines(b"".join(part.read_bytes() for part in run.parts), run.name)


def _logs(path: Path) -> List[Path]:
    if not path.is_dir():
        return [path]
    logs = _held(path)
    if not logs:
        raise InputError(f"{path} holds no .log file")
    return logs


def _held(directory: Path) -> List[Path]:
    """The logs a directory holds: the `.log` files directly in it."""
    return [file for file in directory.iterdir() if file.suffix == ".log" and file.is_file()]


def _place(file_name: str) -> Tuple[str, _Place]:
    """The run a file belongs to, and its place among that run's files."""
    part = _PART.fullmatch(file_name)
    if part:
        return _printable(part["run"]), (False, int(part["number"]))
    return _printable(Path(file_name).stem), _STILL_WRITTEN


def _printable(name: str) -> str:
    """A file's name as text every stream can write: a byte that is not UTF-8 becomes `\\xNN`."""
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
