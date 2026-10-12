"""One run in one process: a runner is told a plan, and what it leaves is the run."""

from __future__ import annotations

import collections
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from . import measure, table
from .peers import Interrupted, Runner, Stopped, Watch, name_of
from .scenario import Cut, Heal, Hold, Post, Scenario, Seat, senders

if TYPE_CHECKING:
    from .run import Settings

# What a run reads of a runner's result: the types a value may be of, the fields an object has
# to hold, or in a list what each of its items is.
_NONE = type(None)
_TEXT, _WHOLE, _NUMBER = (str,), (int,), (int, float)
_RESULT = {
    "stopped": (str, _NONE),
    "steps_done": _WHOLE,
    "conversation": _TEXT,
    "virtual_s": _NUMBER,
    "wall_s": _NUMBER,
    "members": [
        {"name": _TEXT, "installation": _TEXT, "app_id": (str, _NONE), "inbound_errors": {}}
    ],
    "batches": [{"joiners": _WHOLE, "members": _WHOLE, "seated_s": _NUMBER}],
    "posts": [{"poster": _TEXT, "readers": _WHOLE, "unread": [_TEXT], "read_s": [_NUMBER]}],
}
# What it reads of a member besides, where the plan has a network.
_MODELLED = {
    "network": {
        "withheld": (dict, _NONE),
        "frames": {"arrived": _WHOLE, "passed": _WHOLE, "held": _WHOLE},
    },
    "dropped": [{"why": _TEXT}],
}
_WITHHELD = {"app_id": (str, _NONE), "seat": (int, _NONE), "applied": (bool,)}


def plan(scenario: Scenario, start: int) -> dict:
    """What the runner is told: the scenario's steps one for one, with every name resolved.

    `start` is the second its clock starts at. As in a run of processes, a roster is waited on
    by the creator and by each other member the scenario expects at the tip, and a post by the
    members it expects to read it.
    """
    names = [name_of(index) for index in range(scenario.members)]
    told: dict = {
        "members": names,
        "timers": scenario.timers,
        "budget_s": scenario.budget,
        "start": start,
    }
    network = scenario.network
    if network is not None:
        told["network"] = {
            "seed": network.seed,
            "loss": network.loss,
            "welcome_loss": network.welcome_loss,
            "delay_ms": {name: network.delay_of(name) for name in names},
        }
    expect = scenario.expect
    told["unwaited"] = {
        "rosters": [name for name in names[1:] if expect.positions.get(name, "tip") != "tip"],
        "posts": [name for name in names if expect.posts.get(name, "read") != "read"],
    }
    steps: List[dict] = []
    seated = 1
    for step in scenario.steps:
        if isinstance(step, Seat):
            steps.append({"step": "seat", "to": step.to, "batch": step.batch})
            seated = step.to
        elif isinstance(step, Post):
            posters = [name_of(sender) for sender in senders(step, seated)]
            steps.append({"step": "post", "from": posters})
        elif isinstance(step, Hold):
            steps.append({"step": "hold", "seconds": step.seconds})
        elif isinstance(step, Cut):
            steps.append({"step": "cut", "sides": [list(side) for side in step.sides]})
        elif isinstance(step, Heal):
            steps.append({"step": "heal"})
        else:
            steps.append({"step": "withhold", "from": step.member})
    told["steps"] = steps
    return told


def run(settings: Settings, report: dict, watch: Watch) -> None:
    """Has the runner do a scenario's steps, and says in `report` what it left.

    The run's directory gains `plan.json`, what the runner was told, and the runner's
    `group.log` and `result.json`. A signal `watch` takes while the runner runs ends it.
    """
    out = settings.out
    report.update(rig=table.ONE_PROCESS, cluster_id=None, virtual_s=None, wall_s=None)
    told = plan(settings.scenario, int(time.time()))
    (out / "plan.json").write_text(json.dumps(told, indent=2) + "\n", encoding="utf-8")
    runner: Optional[Runner] = None
    try:
        runner = Runner(settings.one_process, out / "plan.json", out, watch)
        runner.wait()
    except Stopped as stopped:
        report["stopped"] = str(stopped)
    except Interrupted:
        pass
    finally:
        watch.close()
        if runner is not None:
            runner.stop()
    if runner is None:
        return
    if runner.ended() != 0:
        report["stopped"] = str(runner.gone())
        return
    result = _result(out / "result.json")
    if result is None:
        report["stopped"] = "the runner ended with status 0 and left no result"
        return
    amiss = _amiss(result, told)
    if amiss is not None:
        report["stopped"] = f"the runner left a result that cannot be read: {amiss}"
        return
    report["stopped"] = result["stopped"]
    report["steps_done"] = result["steps_done"]
    report["conversation"] = result["conversation"]
    report["virtual_s"], report["wall_s"] = result["virtual_s"], result["wall_s"]
    report["nodes"] = [_node(member) for member in result["members"]]
    report["batches"] = result["batches"]
    report["posts"] = [_post(post) for post in result["posts"]]
    done = settings.scenario.steps[: result["steps_done"]]
    report["holds"] = [{"seconds": step.seconds} for step in done if isinstance(step, Hold)]


def _result(file: Path) -> Optional[dict]:
    """What the runner's result holds, or `None`: a runner that was ended leaves it empty."""
    try:
        result = json.loads(file.read_bytes())
    except (OSError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def _amiss(result: dict, told: dict) -> Optional[str]:
    """What of a runner's result a run cannot read, by its name, or `None` when it reads it all.

    `told` is the plan the result is of, whose members it holds in the plan's order.
    """
    amiss = _unlike(result, _RESULT, "")
    if amiss is not None:
        return amiss
    if [member["name"] for member in result["members"]] != told["members"]:
        return "members are not those of the plan, in its order"
    modelled = "network" in told
    for at, member in enumerate(result["members"]):
        # A member rejects frames for whatever reasons, and each has a count.
        shape = {"inbound_errors": {reason: _WHOLE for reason in member["inbound_errors"]}}
        amiss = _unlike(member, dict(shape, **_MODELLED) if modelled else shape, f"members[{at}]")
        if amiss is None and modelled and member["network"]["withheld"] is not None:
            withheld = member["network"]["withheld"]
            amiss = _unlike(withheld, _WITHHELD, f"members[{at}].network.withheld")
        if amiss is not None:
            return amiss
    return None


def _unlike(held: object, shape: object, path: str) -> Optional[str]:
    """What keeps `held`, which `path` names, from being of `shape`: the first, or `None`."""
    if isinstance(shape, tuple):
        return None if type(held) in shape else f"{path} is not what a result holds"
    if isinstance(shape, list):
        if not isinstance(held, list):
            return f"{path} is not a list"
        for at, item in enumerate(held):
            amiss = _unlike(item, shape[0], f"{path}[{at}]")
            if amiss is not None:
                return amiss
        return None
    if not isinstance(held, dict):
        return f"{path} is not an object"
    for name, inner in shape.items():
        named = f"{path}.{name}" if path else name
        if name not in held:
            return f"{named} is missing"
        amiss = _unlike(held[name], inner, named)
        if amiss is not None:
            return amiss
    return None


def _node(member: dict) -> dict:
    """A member of the runner's result as a run tells of a node: its drops counted by `why`."""
    node = {name: held for name, held in member.items() if name != "dropped"}
    if "dropped" in member:
        node["network_dropped"] = _network_dropped(member["dropped"])
    return node


def _post(post: dict) -> dict:
    """A post of the runner's result as a run tells of a post, less what a registry adds."""
    return {
        "poster": post["poster"],
        "readers": post["readers"],
        "unread": post["unread"],
        "read": measure.spread(post["read_s"]),
    }


def _network_dropped(dropped: List[dict]) -> Dict[str, int]:
    """How many frames a member's network model dropped, by their `why`."""
    counted = collections.Counter(str(drop["why"]) for drop in dropped)
    return dict(counted.most_common())
