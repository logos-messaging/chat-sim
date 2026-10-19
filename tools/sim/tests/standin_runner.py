"""A stand-in for the runner of a plan, for the tests that run the tool in one process.

It takes the command line the tool gives the runner, reads the plan, and leaves in the directory
what the runner leaves: `group.log`, with the trace records of every member stamped by a clock
of its own, and `result.json`. It runs no group: a batch is seated `SEATED` after its add, and a
post is read as it is sent. With a `network` each member has a network model, of which it keeps
the cut and the rule that withholds a commit candidate: it loses no frame at a rate and holds
none. A roster stands for the commit candidates of its batch, which the creator made. So a
member whose rule is armed drops a candidate as the next roster arrives, and holds that roster
all the same, as a member does by the candidate of another steward.
A grow is the creator adding one member a round, and it never forks.

    standin_runner.py [--fault NAME[=MEMBER]]... PLAN.json OUT_DIR

A fault is what the runner does wrong, or what the member named does.
"""

import argparse
import json
import os
import secrets
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FAULTS = {
    "refused": "refuses its plan: it writes why, ends with status 2 and leaves nothing",
    "panics": "ends with a status of its own once its log is begun, having written PANIC",
    "killed": "is ended by a signal once its log is begun",
    "no-result": "does the plan and leaves its result empty",
    "waits": "writes its process id once its log is begun, and does nothing more for WAIT",
    "behind": "MEMBER takes no roster",
    "applied": "the group applies the candidate withheld, and its member is alone in a state",
}

# The status a runner ends with when it panics, and what it then wrote.
STATUS = 101
PANIC = (
    "thread 'main' panicked at src/runner.rs:1:1:",
    "assertion `left == right` failed",
    "  left: 1",
    " right: 2",
    "note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace",
)
REFUSAL = "timers: freeze_duration must be non-zero"
WAIT = 60

# The virtual seconds from an add to the roster of its batch.
SEATED = 0.5

# The bytes of a commit candidate and of a post.
CANDIDATE, POST = 3000, 500

# Why a member that cannot read a post rejects it.
GARBLED = "DeMlsError(Mls(ProcessMessage(ValidationError(UnableToDecrypt(AeadError)))))"


class Run:
    def __init__(self, plan: dict, out: Path, faults: list):
        named = [flag.partition("=") for flag in faults]
        self.faults = {fault for fault, _, member in named if not member}
        self.behind = {member for fault, _, member in named if fault == "behind"}
        self.plan = plan
        self.names = plan["members"]
        self.start = self.clock = float(plan["start"])
        self.began = time.monotonic()
        self.log = open(out / "group.log", "w", encoding="utf-8")
        self.installation = {name: secrets.token_hex(4) for name in self.names}
        self.account = {name: name.encode("utf-8")[:4].hex() for name in self.names}
        # What a member stamps on the frames it publishes, once it holds the conversation.
        self.app_id = dict.fromkeys(self.names)
        self.errors = {name: {} for name in self.names}
        self.dropped = {name: [] for name in self.names}
        self.network = None
        if "network" in plan:
            self.network = {name: self.model(name) for name in self.names}
        self.convo = secrets.token_hex(5)
        self.epoch = 0
        self.seated = 1
        # The member whose rule is armed, and the members a withheld candidate left alone.
        self.armed = None
        self.apart = []
        self.batches = []
        self.posts = []
        self.grown = None
        for name in self.names:
            self.record(name, "run", f"account={self.account[name]}")
        self.app_id[self.names[0]] = secrets.token_hex(10)
        self.hold_roster(self.names[0], secrets.token_hex(8), 1)

    def model(self, name: str) -> dict:
        network = self.plan["network"]
        return {
            "seed": network["seed"],
            "loss": float(network["loss"]),
            "welcome_loss": float(network["welcome_loss"]),
            "delay_ms": network["delay_ms"].get(name, 0),
            "cut": [],
            "own": [],
            "withheld": None,
            "armed": False,
            "frames": {
                "arrived": 0,
                "passed": 0,
                "held": 0,
                "dropped": {"loss": 0, "welcome_loss": 0, "cut": 0, "withheld": 0},
            },
        }

    def record(self, name: str, record: str, fields: str) -> None:
        stamp = datetime.fromtimestamp(self.clock, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        installation = self.installation[name]
        self.log.write(f"{stamp}  INFO libchat::groupv2trace: {record} installation={installation} ")
        self.log.write(f"{fields}\n")
        self.log.flush()

    def hold_roster(self, name: str, state: str, members: int) -> None:
        roster = ",".join(
            f"{self.installation[member]}:{self.account[member]}" for member in self.names[:members]
        )
        self.record(
            name,
            "epoch",
            f"convo={self.convo} epoch={self.epoch} state={state} members={members} "
            f"roster={roster}",
        )

    def arrive(self, name: str, why, kind: str, size: int, **more) -> bool:
        """Puts a frame through the network model of a member: whether the model passes it."""
        if self.network is None:
            return True
        counted = self.network[name]["frames"]
        counted["arrived"] += 1
        if why is None:
            counted["passed"] += 1
            return True
        counted["dropped"][why] += 1
        self.dropped[name].append(
            {"n": counted["arrived"], "why": why, "kind": kind, "bytes": size, **more}
        )
        return False

    def steps(self):
        """Does the steps: how many were done, and why they stopped if they did."""
        for done, step in enumerate(self.plan["steps"]):
            stopped = getattr(self, step["step"])(step)
            if stopped is not None:
                self.clock += self.plan["budget_s"]
                return done, stopped
        return len(self.plan["steps"]), None

    def seat(self, step: dict):
        while self.seated < step["to"]:
            members = min(self.seated + step["batch"], step["to"])
            stopped = self.add(members)
            if stopped is not None:
                return stopped
        return None

    def add(self, members: int):
        sent_at = self.clock
        self.clock += SEATED
        self.epoch += 1
        state = secrets.token_hex(8)
        creator = self.names[0]
        withheld, self.armed = self.armed, None
        for name in self.names[:members]:
            if name in self.behind:
                continue
            own = False
            if name == withheld:
                sender = {"app_id": self.app_id[creator], "seat": 0}
                self.arrive(name, "withheld", "candidate", CANDIDATE, **sender)
                own = "applied" in self.faults
                self.network[name].update(armed=False, withheld={**sender, "applied": own})
            elif name != creator:
                self.arrive(name, None, "candidate", CANDIDATE)
            if self.app_id[name] is None:
                self.app_id[name] = secrets.token_hex(10)
            self.hold_roster(name, secrets.token_hex(8) if own else state, members)
            if own:
                self.apart.append(name)
        waited = [name for name in self.names[:members] if name not in self.unwaited("rosters")]
        behind = [name for name in waited if name in self.behind]
        if behind:
            return (
                f"the roster of {members} members was not held within the budget by: "
                f"{', '.join(behind)}"
            )
        self.batches.append(
            {
                "joiners": members - self.seated,
                "members": members,
                "seated_s": self.clock - sent_at,
                "wall_s": 0.001,
            }
        )
        self.seated = members
        return None

    def unwaited(self, wait: str) -> list:
        return self.plan.get("unwaited", {}).get(wait, []) + self.apart

    def post(self, step: dict):
        for poster in step["from"]:
            frame = secrets.token_hex(8)
            written = f"convo={self.convo} frame={frame}"
            self.record(poster, "sent", f"{written} epoch={self.epoch}")
            read = []
            for name in self.names[: self.seated]:
                if name == poster or self.app_id[name] is None:
                    continue
                cut = self.network is not None and self.app_id[poster] in self.network[name]["cut"]
                why = "cut" if cut else None
                if not self.arrive(name, why, "group", POST, app_id=self.app_id[poster]):
                    continue
                if name in self.apart:
                    self.record(name, "unreadable", f"{written} epoch={self.epoch} why=decrypt")
                    self.errors[name][GARBLED] = self.errors[name].get(GARBLED, 0) + 1
                    continue
                sender = self.installation[poster]
                self.record(name, "received", f"{written} from={sender} epoch={self.epoch}")
                read.append(name)
            others = [name for name in self.names[: self.seated] if name != poster]
            readers = [name for name in others if name not in self.unwaited("posts")]
            unread = [name for name in readers if name not in read]
            self.posts.append(
                {
                    "poster": poster,
                    "readers": len(readers),
                    "unread": unread,
                    "read_s": [0.0 for name in readers if name in read],
                }
            )
            if unread:
                return (
                    f"the post of {poster} was not read within the budget by: {', '.join(unread)}"
                )
        return None

    def hold(self, step: dict):
        self.clock += step["seconds"]

    def cut(self, step: dict):
        one, other = step["sides"]
        for side, others in ((one, other), (other, one)):
            for name in side:
                self.network[name]["cut"] = sorted(self.app_id[member] for member in others)

    def heal(self, step: dict):
        for model in self.network.values():
            model["cut"] = []

    def withhold(self, step: dict):
        self.armed = step["from"]
        self.network[self.armed]["armed"] = True

    def grow(self, step: dict):
        rounds = []
        while self.epoch < step["epochs"] and self.seated < len(self.names):
            joiner = self.names[self.seated]
            stopped = self.add(self.seated + 1)
            if stopped is not None:
                return stopped
            # A round is no batch of the result.
            seated_s = self.batches.pop()["seated_s"]
            rounds.append({"adders": [self.names[0]], "joiners": [joiner], "refused": []})
            rounds[-1]["settled_s"] = seated_s
        members = self.names[: self.seated]
        trunk = {"kind": "trunk", "from": None, "forked_at": None, "epoch": self.epoch}
        self.grown = {
            "ended": f"the trunk reached epoch {step['epochs']}"
            if self.epoch >= step["epochs"]
            else "every member of the plan was seated",
            "trunk_epoch": self.epoch,
            "main": 1,
            "lines": [dict(trunk, members=members)],
            "fell_behind": [],
            "behind": [],
            "never_joined": [],
            "rounds": rounds,
        }
        return None

    def member(self, name: str) -> dict:
        said = {
            "name": name,
            "installation": self.installation[name],
            "account": self.account[name],
            "seated": self.names.index(name) < self.seated,
            "app_id": self.app_id[name],
            "inbound_errors": self.errors[name],
        }
        if self.network is not None:
            said.update(network=self.network[name], dropped=self.dropped[name])
        return said

    def result(self, steps_done: int, stopped) -> dict:
        grown = {} if self.grown is None else {"grow": self.grown}
        return {
            **grown,
            "steps_done": steps_done,
            "stopped": stopped,
            "conversation": self.convo,
            "members": [self.member(name) for name in self.names],
            "batches": self.batches,
            "posts": self.posts,
            "virtual_s": self.clock - self.start,
            "wall_s": time.monotonic() - self.began,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fault", action="append", default=[], metavar="NAME[=MEMBER]")
    parser.add_argument("plan", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    for flag in args.fault:
        if flag.partition("=")[0] not in FAULTS:
            parser.error(f"--fault: no such fault: {flag}")
    if "refused" in args.fault:
        print(f"harness-run: {args.plan}: {REFUSAL}", file=sys.stderr)
        sys.exit(2)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    run = Run(plan, args.out, args.fault)
    # The result is opened with the log, and written once the steps are over.
    with open(args.out / "result.json", "w", encoding="utf-8") as result:
        if "panics" in args.fault:
            print(*PANIC, sep="\n", file=sys.stderr)
            sys.exit(STATUS)
        if "killed" in args.fault:
            os.kill(os.getpid(), signal.SIGKILL)
        if "waits" in args.fault:
            print(os.getpid(), flush=True)
            time.sleep(WAIT)
        came_to = run.result(*run.steps())
        if "no-result" not in args.fault:
            result.write(json.dumps(came_to, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
