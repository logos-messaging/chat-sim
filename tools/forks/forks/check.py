"""Holds one conversation's report against an expectation: what `forks check` does.

An expectation is a file that names a conversation and says what its report must hold: the
verdict, the position of each installation, and what became of each post on each installation.
A post is known by its sender and its place among that sender's posts, since a log holds
nothing of what a post said.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .rule import POSITIONS, Conversation, Message

# What a report says became of a post on an installation.
CELLS = ("sent", "read", "unreadable", "not a member", "no log", "no record")

_INSTALLATION = re.compile(r"[0-9a-f]{8}")


class NoExpectation(Exception):
    """Why a file is no expectation."""


@dataclass(frozen=True)
class Post:
    """A post: who sent it, and what became of it on each installation the file holds it for."""

    sender: str
    cells: Dict[str, str]


@dataclass(frozen=True)
class Expectation:
    """What the report of a conversation must hold.

    `names` gives a name for the installations the file gives one for. `posts` is `None` when
    the file holds nothing of posts, and they are then not looked at.
    """

    conversation: str
    verdict: str
    positions: Dict[str, str]
    names: Dict[str, str]
    posts: Optional[List[Post]]


def read(path: Path) -> Expectation:
    """The expectation a file holds."""
    try:
        said = json.loads(path.read_bytes(), object_pairs_hook=_said_once)
    except (ValueError, RecursionError):
        raise NoExpectation(f"{path} is not JSON, or says a field twice") from None
    try:
        return _expectation(said)
    except NoExpectation as why:
        raise NoExpectation(f"{path}: {why}") from None


def _said_once(pairs: List[Tuple[str, object]]) -> Dict[str, object]:
    """An object of JSON none of whose keys is said twice: a reader keeps the last and no other."""
    said = dict(pairs)
    if len(said) != len(pairs):
        raise ValueError("a key is said twice")
    return said


def _fields(said: object, what: str, needed: Tuple[str, ...], known: Tuple[str, ...]) -> dict:
    """`said` as the object `what` is: every field it needs, and no field that is not known."""
    if not isinstance(said, dict):
        raise NoExpectation(f"{what} is not an object")
    for field in needed:
        if field not in said:
            raise NoExpectation(f"{what} lacks `{field}`")
    for field in said:
        if field not in known:
            raise NoExpectation(f"{what} holds an unknown field, `{_quoted(field)}`")
    return said


def _quoted(said: object) -> str:
    """What a file said, as a refusal quotes it: a character that is not printed is escaped."""
    return "".join(
        character if character.isprintable() else character.encode("unicode_escape").decode()
        for character in str(said)
    )


def _text(said: dict, field: str, what: str) -> str:
    """A field that is a text a line can be printed with: no line break, no control character."""
    if not isinstance(said[field], str) or not said[field]:
        raise NoExpectation(f"`{field}` of {what} is not a text")
    if not said[field].isprintable():
        raise NoExpectation(f"`{field}` of {what} holds a character that is not printed")
    return said[field]


def _expectation(said: object) -> Expectation:
    whole = _fields(
        said,
        "the expectation",
        ("conversation", "verdict", "installations"),
        ("conversation", "verdict", "installations", "posts"),
    )
    installations = whole["installations"]
    if not isinstance(installations, dict):
        raise NoExpectation("`installations` is not an object")
    positions, names = {}, {}
    for installation, held in installations.items():
        if not _INSTALLATION.fullmatch(installation):
            raise NoExpectation(
                f"`{_quoted(installation)}` is no installation: 8 hex characters name one"
            )
        seat = f"the installation {installation}"
        held = _fields(held, seat, ("position",), ("name", "position"))
        positions[installation] = _text(held, "position", seat)
        if positions[installation] not in POSITIONS:
            raise NoExpectation(f"`{positions[installation]}` of {seat} is no position")
        if "name" in held:
            names[installation] = _text(held, "name", seat)
    posts = None
    if "posts" in whole:
        if not isinstance(whole["posts"], list):
            raise NoExpectation("`posts` is not a list")
        posts = [
            _post(post, f"post {number}", positions)
            for number, post in enumerate(whole["posts"], 1)
        ]
    return Expectation(
        conversation=_text(whole, "conversation", "the expectation"),
        verdict=_text(whole, "verdict", "the expectation"),
        positions=positions,
        names=names,
        posts=posts,
    )


def _post(said: object, what: str, installations: Dict[str, str]) -> Post:
    post = _fields(said, what, ("sender", "cells"), ("sender", "cells"))
    sender, cells = post["sender"], post["cells"]
    if not isinstance(sender, str) or sender not in installations:
        raise NoExpectation(f"the sender of {what} is not among `installations`")
    if not isinstance(cells, dict):
        raise NoExpectation(f"`cells` of {what} is not an object")
    for installation, cell in cells.items():
        if installation not in installations:
            raise NoExpectation(
                f"`{_quoted(installation)}` in {what} is not among `installations`"
            )
        if cell not in CELLS:
            raise NoExpectation(f"`{_quoted(cell)}` in {what} is not what a report says of a post")
    return Post(sender, dict(cells))


def _as_sent(posts: List[Message]) -> List[Message]:
    """One sender's posts in the order it wrote them: run after run, and by line within a run.

    `posts` are in the report's order, which is that of their stamps, and a sender may write two
    at one stamp.
    """
    runs: Dict[str, List[Message]] = {}
    for post in posts:
        runs.setdefault(post.at.run, []).append(post)
    return [post for run in runs.values() for post in sorted(run, key=lambda post: post.at.line)]


def differences(expectation: Expectation, conversation: Optional[Conversation]) -> List[str]:
    """Each way a conversation's report is not what was expected, a line each.

    `conversation` is `None` when the logs hold no record of the one the expectation names.
    """
    if conversation is None:
        return [f"conversation {expectation.conversation}: not in the logs"]

    def called(installation: str) -> str:
        name = expectation.names.get(installation)
        return f"{name} ({installation})" if name else installation

    found = []
    if conversation.verdict != expectation.verdict:
        found.append(f"verdict: {conversation.verdict}, expected {expectation.verdict}")
    reported = {seat.id: seat.position for seat in conversation.installations}
    for installation, position in expectation.positions.items():
        stands = reported.get(installation, "not in the report")
        if stands != position:
            found.append(f"{called(installation)}: {stands}, expected {position}")
    found += [
        f"{installation}: {position}, not expected"
        for installation, position in reported.items()
        if installation not in expectation.positions
    ]
    if expectation.posts is None:
        return found

    sent: Dict[str, List[Message]] = {}
    for message in conversation.messages:
        sent.setdefault(message.sender, []).append(message)
    sent = {sender: _as_sent(posts) for sender, posts in sent.items()}
    expected: Dict[str, int] = {}
    for post in expectation.posts:
        expected[post.sender] = number = expected.get(post.sender, 0) + 1
        of = f"post {number} of {called(post.sender)}"
        if number > len(sent.get(post.sender, ())):
            found.append(f"{of}: not in the report")
            continue
        message = sent[post.sender][number - 1]
        for installation, cell in post.cells.items():
            became = message.cells.get(installation, "not in the report")
            if became != cell:
                if message.why.get(installation) == "sender":
                    # The post decrypted, so the group's state is not what kept it from being read.
                    became += " since the sender's account could not be checked"
                found.append(f"{of} on {called(installation)}: {became}, expected {cell}")
    found += [
        f"post {number} of {called(sender)}: in the report, not expected"
        for sender, posts in sent.items()
        for number in range(expected.get(sender, 0) + 1, len(posts) + 1)
    ]
    return found
