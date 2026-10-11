"""A run's report as text, and a sweep's row for a run."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Union

# A sweep's row up to what its check found, which is as long as it is.
_ROW = "{:>3}  {:>7}  {:>10}  {:<22}  "


def render(report: dict) -> str:
    """The report as the tables a person reads."""
    lines = [
        f"{report['members']} members, {_timers(report['timers'])}, "
        f"largest message {report['max_message_kib']} KiB",
        *_outcome(report),
        _load(report["load"]),
        "",
    ]
    lines += _columns(
        (
            "node",
            "ready",
            "start",
            "dials",
            "wired",
            "mesh",
            "end",
            "mesh",
            "refused",
            "dropped",
            "errors",
            "exit",
        ),
        [_node(node) for node in report["nodes"]],
    )
    errors = _errors(report["nodes"])
    if errors:
        lines.append("")
        lines += _columns(("inbound_error", "lines", "peers"), errors)
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
    if report["posts"]:
        lines.append("")
        lines += _columns(
            ("post", "from", "read by", "median", "worst", "delivered", "worst", "lookup"),
            [_post(number, post) for number, post in enumerate(report["posts"], 1)],
        )
    return "\n".join(lines) + "\n"


def row(number: int, seconds: float, report: dict) -> str:
    """A sweep's row for a run: how its check went, and what stopped its steps if anything did."""
    said = [_check(report)]
    if report["stopped"] is not None:
        said.append(f"stopped: {report['stopped']}")
    if report["page"] is not None:
        said.append(report["page"])
    elif report["unshared"]:
        said.append(f"not shared: {report['unshared'][0]}")
    at_tip = "-" if report["at_tip"] is None else f"{report['at_tip']} of {report['seated']}"
    return _ROW.format(number, f"{seconds:.1f}", at_tip, report["verdict"] or "-") + "; ".join(said)


def head() -> str:
    """The line above a sweep's rows."""
    return _ROW.format("run", "seconds", "at the tip", "verdict") + "check"


def _check(report: dict) -> str:
    """How a run's check went, in a few words: the first difference, and how many more."""
    check = report["check"]
    if check is None:
        unchecked = report["unchecked"]
        return "not checked" if unchecked is None else f"not checked: {unchecked}"
    if check["as_expected"]:
        return "as expected"
    first, more = check["differences"][0], len(check["differences"]) - 1
    return f"{first}, and {more} more" if more else first


def _timers(timers: Union[str, Dict[str, int]]) -> str:
    if isinstance(timers, str):
        return f"{timers} group timers"
    given = ", ".join(f"{name} {milliseconds} ms" for name, milliseconds in timers.items())
    return f"group timers {given}" if given else "default group timers"


def _outcome(report: dict) -> List[str]:
    lines = []
    if report["stopped"] is not None:
        lines.append(
            f"stopped after {report['steps_done']} of {report['steps']} steps: "
            f"{report['stopped']}"
        )
    check = report["check"]
    if check is None and report["unchecked"] is None:
        lines.append("not checked: " + _unchecked(report))
        return lines
    lines.append(
        f"{report['verdict'] or 'no report'}: "
        f"{report['at_tip'] or 0} of {report['seated']} members at the tip"
    )
    if check is None:
        lines.append(f"not checked: {report['unchecked']}")
    elif check["as_expected"]:
        lines.append("the report is as expected")
    else:
        lines += [f"not as expected: {difference}" for difference in check["differences"]]
    if report["page"] is not None:
        lines.append(f"shared: {report['page']}")
    lines += [f"not shared: {complaint}" for complaint in report["unshared"]]
    return lines


def _unchecked(report: dict) -> str:
    if report["conversation"] is None:
        return "the run made no conversation"
    return "the run was interrupted"


def _load(load: dict) -> str:
    start, end = load["start"][0], (load["end"] or [None])[0]
    at_the_end = "" if end is None else f", {end:.2f} at the end"
    return f"load average {start:.2f} at the start{at_the_end}"


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
        _count(sum(node["inbound_errors"].values()) if "inbound_errors" in node else None),
        _exit(node),
    )


def _exit(node: dict) -> str:
    """How a peer ended, where that was not with status 0 once it was asked to."""
    if "ended" not in node:
        return "-"
    if node["exit_signal"] is not None:
        status = f"signal {node['exit_signal']}"
    else:
        status = str(node["exit_status"])
    return status if node["ended"] == "asked" else f"{status} {node['ended']}"


def _errors(nodes: Sequence[dict]) -> List[Sequence[str]]:
    """Each message of an `inbound_error` line: how many lines held it, and of how many peers."""
    lines: Dict[str, int] = {}
    peers: Dict[str, int] = {}
    for node in nodes:
        for message, count in node.get("inbound_errors", {}).items():
            lines[message] = lines.get(message, 0) + count
            peers[message] = peers.get(message, 0) + 1
    return [
        (message, str(count), str(peers[message]))
        for message, count in sorted(lines.items(), key=lambda held: (-held[1], held[0]))
    ]


def _post(number: int, post: dict) -> Sequence[str]:
    read = post["read"] or {}
    delivered = post["delivered"] or {}
    return (
        str(number),
        post["poster"],
        f"{post['readers'] - len(post['unread'])} of {post['readers']}",
        _seconds(read.get("median")),
        _seconds(read.get("worst")),
        _seconds(delivered.get("median")),
        _seconds(delivered.get("worst")),
        _seconds(post["lookup_mean"]),
    )


def _welcomes(welcome_bytes: List[int]) -> str:
    """The Welcomes of a batch: how many, and the bytes of each."""
    sizes = sorted(welcome_bytes)
    if sizes[0] == sizes[-1]:
        return f"{len(sizes)} x {sizes[0]}"
    return f"{len(sizes)} x {sizes[0]}..{sizes[-1]}"


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
