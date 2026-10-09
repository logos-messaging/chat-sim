"""The report as text for a terminal."""

from __future__ import annotations

import textwrap
from typing import List, Sequence, Tuple

from . import rule
from .records import Counts
from .rule import Conversation, Installation

_WIDTH = 100
_POSITIONS = (
    rule.TIP,
    rule.BEHIND,
    rule.LINEAGE_UNKNOWN,
    rule.ENDED,
    rule.REMOVED,
    rule.NEVER_JOINED,
    rule.NO_DATA,
)


def render(conversations: Sequence[Conversation], counts: Counts) -> str:
    """Every conversation's report, then what became of the lines that were read."""
    blocks = [_conversation(conversation) for conversation in conversations]
    if not blocks:
        blocks = ["no conversation in these logs"]
    if counts.malformed:
        blocks.append(_drawn_without(counts.malformed))
    blocks.append(
        f"lines: {counts.lines} read, {counts.records} records, "
        f"{counts.unstamped} without a stamp, {counts.malformed} malformed, "
        f"{counts.unknown} unknown"
    )
    return "\n\n".join(blocks)


def _drawn_without(malformed: int) -> str:
    """What a reader of the verdicts must know about the report lines that could not be read."""
    if malformed == 1:
        return "1 malformed line was skipped: every verdict above was drawn without it"
    return f"{malformed} malformed lines were skipped: every verdict above was drawn without them"


def _conversation(conversation: Conversation) -> str:
    coverage = conversation.coverage
    lines = [
        f"conversation {conversation.id}",
        f"  {conversation.verdict}",
        f"  {coverage.sharing} of {coverage.seats} seats share a log",
        "",
    ]
    for branch in conversation.branches:
        members = "1 member" if branch.members == 1 else f"{branch.members} members"
        newest = f"epoch {branch.epoch}, state {branch.state}, {members}"
        on_it = "on it: " + (", ".join(branch.installations) or "nobody")
        lines.append(f"  branch {branch.label}: {newest}")
        lines.append(
            textwrap.fill(on_it, _WIDTH, initial_indent="    ", subsequent_indent="      ")
        )
    if conversation.branches:
        lines.append("")
    rows = [["installation", "account", "name", "epoch", "position", "branch"]]
    rows += map(_row, sorted(conversation.installations, key=_reading_order))
    return "\n".join(lines + _table(rows))


def _reading_order(installation: Installation) -> Tuple:
    """Branch by branch, the installations on none last, and within each by position."""
    branch = installation.branch or ""
    return not branch, len(branch), branch, _POSITIONS.index(installation.position), installation.id


def _row(installation: Installation) -> List[str]:
    return [
        installation.id,
        installation.account or "-",
        ", ".join(installation.runs) or "-",
        "-" if installation.epoch is None else str(installation.epoch),
        installation.position,
        installation.branch or "-",
    ]


def _table(rows: List[List[str]]) -> List[str]:
    widths = [max(map(len, column)) for column in zip(*rows)]
    return [
        "  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
        for row in rows
    ]
