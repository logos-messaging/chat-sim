"""A run's report as text."""

from __future__ import annotations

from typing import List, Optional, Sequence


def render(report: dict) -> str:
    """The report as the tables a person reads."""
    post = report["post"]
    lines = [
        f"{report['peers']} peers, {report['timers']} group timers, batches of {report['batch']}, "
        f"largest message {report['max_message_kib']} KiB",
        _outcome(report),
        "",
    ]
    lines += _columns(
        ("node", "ready", "start", "dials", "wired", "mesh", "end", "mesh", "refused", "dropped"),
        [_node(node) for node in report["nodes"]],
    )
    if report["batches"]:
        lines.append("")
        lines += _columns(
            ("batch", "joiners", "members", "joined", "seated", "Welcome bytes"),
            [
                (
                    str(number),
                    str(batch["joiners"]),
                    str(batch["members"]),
                    _seconds(batch["joined_s"]),
                    _seconds(batch["seated_s"]),
                    _welcomes(batch["welcome_bytes"]),
                )
                for number, batch in enumerate(report["batches"], 1)
            ],
        )
    if post is not None:
        lines.append("")
        lines.append(
            f"post from {post['poster']}: read by {post['readers'] - len(post['unread'])} "
            f"of {post['readers']}{_spread(post['read'])}"
        )
        if post["delivered"] is None:
            lines.append(
                f"registry: {post['lookups']} lookups of the poster, not one a reader, "
                "so delivery and lookup are not told apart"
            )
        else:
            lines.append(
                f"delivered, by when the lookup reached the registry{_spread(post['delivered'])}"
            )
            lines.append(f"registry lookup: mean {_seconds(post['lookup_mean'])}")
    return "\n".join(lines) + "\n"


def _outcome(report: dict) -> str:
    if report["ok"]:
        return "every member read the post"
    return f"stopped: {report['stopped']}"


def _node(node: dict) -> Sequence[str]:
    wired = node.get("wired", {})
    end = node.get("end", {})
    return (
        node["name"],
        _seconds(node.get("ready_s")),
        _seconds(node.get("start_s")),
        str(len(node["dials"])),
        _count(wired.get("peers")),
        _count(wired.get("mesh_peers")),
        _count(end.get("peers")),
        _count(end.get("mesh_peers")),
        _count(end.get("publish_refused")),
        _count(end.get("inbound_dropped")),
    )


def _welcomes(welcome_bytes: List[int]) -> str:
    """The Welcomes of a batch: how many, and the bytes of each."""
    sizes = sorted(welcome_bytes)
    if sizes[0] == sizes[-1]:
        return f"{len(sizes)} x {sizes[0]}"
    return f"{len(sizes)} x {sizes[0]}..{sizes[-1]}"


def _spread(spread: Optional[dict]) -> str:
    if spread is None:
        return ""
    return f", median {_seconds(spread['median'])}, worst {_seconds(spread['worst'])}"


def _seconds(seconds: Optional[float]) -> str:
    return "-" if seconds is None else f"{seconds:.3f}s"


def _count(count: Optional[int]) -> str:
    return "-" if count is None else str(count)


def _columns(head: Sequence[str], rows: Sequence[Sequence[str]]) -> List[str]:
    """`rows` under `head`, the first column to the left and the others to the right."""
    widths = [max(len(row[at]) for row in [head, *rows]) for at in range(len(head))]
    return [
        "  ".join(
            cell.ljust(width) if at == 0 else cell.rjust(width)
            for at, (cell, width) in enumerate(zip(row, widths))
        ).rstrip()
        for row in [head, *rows]
    ]
