"""A run's report as text, and a sweep's row for a run."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Union

# What `rig` of a report says of a run whose members were clients in one process.
ONE_PROCESS = "one process"

# A sweep's row up to what its check found, which is as long as it is.
_ROW = "{:>3}  {:>7}  {:>10}  {:<22}  "


def render(report: dict) -> str:
    """The report as the tables a person reads: every duration under its first line is virtual."""
    modelled = report["network"] is not None
    lines = [
        f"{_members(report['members'])}, {_group(report)}, "
        f"{_clock(report)}{_network(report['network'])}",
        *_outcome(report),
        _load(report["load"]),
        "",
    ]
    lines += _columns(
        (
            "member",
            "installation",
            *(("arrived", "passed", "held", "lost") if modelled else ()),
            "errors",
        ),
        [_member(node, modelled) for node in report["nodes"]],
    )
    errors = _errors(report["nodes"])
    if errors:
        lines.append("")
        lines += _columns(("inbound error", "frames", "members"), errors)
    if report["batches"]:
        lines.append("")
        lines += _columns(
            ("batch", "joiners", "members", "seated"),
            [
                (
                    str(number),
                    str(batch["joiners"]),
                    str(batch["members"]),
                    _seconds(batch["seated_s"]),
                )
                for number, batch in enumerate(report["batches"], 1)
            ],
        )
    if report["posts"]:
        lines.append("")
        lines += _columns(
            ("post", "from", "read by", "median", "worst"),
            [_read(number, post) for number, post in enumerate(report["posts"], 1)],
        )
    if report["holds"]:
        lines.append("")
        lines += [_hold(number, hold) for number, hold in enumerate(report["holds"], 1)]
    if report["grow"] is not None:
        lines += ["", *_grow(report["grow"])]
    return "\n".join(lines) + "\n"


def row(number: int, report: dict) -> str:
    """A sweep's row for a run: how its check went, and what stopped its steps if anything did.

    Then the run's seed, that of its network model and what was withheld, for a run that has
    them. Its seconds are those of its virtual clock. A run of a `grow` step says how it ended
    in place of its check.
    """
    said = [came_to(report)]
    if report["stopped"] is not None:
        said.append(f"stopped: {report['stopped']}")
    said.append(f"seed {report['seed']}")
    if report["network"] is not None:
        said.append(f"network seed {report['network']['seed']}")
    if report["withheld"] is not None:
        said.append(_withheld(report["withheld"]))
    at_tip = "-" if report["at_tip"] is None else f"{report['at_tip']} of {report['seated']}"
    seconds = report["virtual_s"]
    took = "-" if seconds is None else f"{seconds:.1f}"
    return _ROW.format(number, took, at_tip, report["verdict"] or "-") + "; ".join(said)


def came_to(report: dict) -> str:
    """How a run came out, in a few words: its check, or how its `grow` step ended."""
    return _check(report) if report["grow"] is None else _grew(report["grow"])


def head() -> str:
    """The line above a sweep's rows, which says that its runs are in one process."""
    columns = _ROW.format("run", "seconds", "at the tip", "verdict") + "check"
    return f"{columns} (one process: seconds of its virtual clock)"


def _grew(grow: dict) -> str:
    """How a `grow` step ended, in a few words: its lines, and the members not on one."""
    subs = sum(line["kind"] == "sub" for line in grow["lines"])
    fell = len({held["name"] for held in grow["fell_behind"]})
    said = (
        f"{grow['ended']}: trunk at epoch {grow['trunk_epoch']}, {grow['main']} main, {subs} sub, "
        f"{fell} fell behind, {len(grow['behind'])} behind at the end, "
        f"{len(grow['never_joined'])} never joined"
    )
    if grow["removed"]:
        said += f", {len(grow['removed'])} removed"
    return said


def _grow(grow: dict) -> List[str]:
    """What a `grow` step came to: each line, who fell behind, and its rounds."""
    rounds = grow["rounds"]
    lines = [
        _grew(grow),
        f"{len(rounds)} rounds, {sum(len(held['joiners']) for held in rounds)} joiners, "
        f"{sum(len(held['refused']) for held in rounds)} of the adders refused a whole round, "
        f"the longest {_seconds(max((held['settled_s'] for held in rounds), default=None))}",
        "",
    ]
    lines += _columns(
        ("line", "kind", "from", "forked at", "epoch", "members"),
        [
            (
                str(number),
                line["kind"],
                _count(line["from"]),
                _count(line["forked_at"]),
                str(line["epoch"]),
                str(len(line["members"])),
            )
            for number, line in enumerate(grow["lines"])
        ],
    )
    fell = [f"{held['name']} at epoch {held['epoch']}" for held in grow["fell_behind"]]
    for said, names in (
        ("fell behind", fell),
        ("behind at the end", grow["behind"]),
        ("never joined", grow["never_joined"]),
    ):
        if names:
            lines.append(f"{said}: {', '.join(names)}")
    return lines


def _clock(report: dict) -> str:
    """What the first line says of the one process: how far its clock went, in how long."""
    said = "one process on a virtual clock"
    if report["wall_s"] is not None:
        said = (
            f"one process, {_seconds(report['virtual_s'])} on a virtual clock "
            f"in {_seconds(report['wall_s'])} of wall time"
        )
    said += f", seed {report['seed']}"
    return said


def _member(node: dict, modelled: bool) -> Sequence[str]:
    """A member of a run in one process, with what became of the frames its model was handed."""
    frames: List[Optional[int]] = []
    if modelled:
        counted = node["network"]["frames"]
        frames = [counted["arrived"], counted["passed"], counted["held"], _lost(node)]
    return (
        node["name"],
        node["installation"],
        *map(_count, frames),
        _count(sum(node["inbound_errors"].values())),
    )


def _read(number: int, post: dict) -> Sequence[str]:
    """A post: who read it of those waited on, and when."""
    read = post["read"] or {}
    return (
        str(number),
        post["poster"],
        f"{post['readers'] - len(post['unread'])} of {post['readers']}",
        _seconds(read.get("median")),
        _seconds(read.get("worst")),
    )


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


def _members(members: int) -> str:
    return "1 member" if members == 1 else f"{members} members"


def _group(report: dict) -> str:
    """The group of a run: GroupV3, or a GroupV2 group by its timers."""
    return "GroupV3" if report["group"] == "v3" else _timers(report["timers"])


def _timers(timers: Union[str, Dict[str, int]]) -> str:
    if isinstance(timers, str):
        return f"{timers} group timers"
    given = ", ".join(f"{name} {milliseconds} ms" for name, milliseconds in timers.items())
    return f"group timers {given}" if given else "default group timers"


def _network(network: Optional[dict]) -> str:
    """The seed and the rates of a run's network model, as the head line ends with them."""
    if network is None:
        return ""
    return (
        f", network seed {network['seed']}, loss {network['loss']}, "
        f"welcome loss {network['welcome_loss']}"
    )


def _withheld(withheld: dict) -> str:
    """Whose candidate was withheld from whom, and whether the group applied it."""
    minter = withheld["minter"] or "no member of the run"
    applied = "applied" if withheld["applied"] else "not applied"
    return f"withheld from {withheld['from']}: the candidate of {minter}, {applied}"


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
    if report["withheld"] is not None:
        lines.append(_withheld(report["withheld"]))
    if check is None:
        lines.append(f"not checked: {report['unchecked']}")
    elif check["as_expected"]:
        lines.append("the report is as expected")
    else:
        lines += [f"not as expected: {difference}" for difference in check["differences"]]
    return lines


def _unchecked(report: dict) -> str:
    if report["conversation"] is None:
        return "no result of the runner was read"
    return "the run was interrupted"


def _load(load: dict) -> str:
    start, end = load["start"][0], (load["end"] or [None])[0]
    at_the_end = "" if end is None else f", {end:.2f} at the end"
    return f"load average {start:.2f} at the start{at_the_end}"


def _lost(node: dict) -> int:
    """How many frames a member's network model dropped."""
    return sum(node["network_dropped"].values())


def _errors(nodes: Sequence[dict]) -> List[Sequence[str]]:
    """Each reason the members rejected frames for: how many frames, and of how many members."""
    frames: Dict[str, int] = {}
    members: Dict[str, int] = {}
    for node in nodes:
        for reason, count in node["inbound_errors"].items():
            frames[reason] = frames.get(reason, 0) + count
            members[reason] = members.get(reason, 0) + 1
    return [
        (reason, str(count), str(members[reason]))
        for reason, count in sorted(frames.items(), key=lambda held: (-held[1], held[0]))
    ]


def _hold(number: int, hold: dict) -> str:
    """The line of a hold: its seconds."""
    return f"hold {number}: {_seconds(hold['seconds'])}"


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
