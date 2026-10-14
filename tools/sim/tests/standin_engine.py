"""A stand-in for docker and podman, for the tests that run the tool in containers.

A container is a process of this machine on an address of loopback: `run` starts the stand-in
for chat-cli for an image's `chat-cli`, and Python for its `python3`, and waits for it as the
engine's `run` waits for its container. A path a container is given has to be under a path it
mounts, as one under no mount is a path no container has. A network has the subnet it is
given, of loopback in the tests, and one whose subnet overlaps a subnet in use is refused as
the engine it stands in for refuses it. Every call it takes is a line of `calls.jsonl` in its
state, where it also keeps its networks and its containers.

    standin_engine.py STATE <what the tool gives the engine>

`engine.json` in STATE says which engine it stands in for, the stand-in for chat-cli it runs,
the subnets in use that are not its networks', and the faults it has.
"""

import ipaddress
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import List

FAULTS = {
    "refuses-network": "refuses to create a network",
    "refuses-tc": "has a kernel without netem, so tc refuses each qdisc",
}

# The options of `run` that take a value, and those that take none.
VALUED = {
    "--name",
    "--label",
    "--network",
    "--ip",
    "--cap-add",
    "--security-opt",
    "--user",
    "--volume",
    "--env",
    "--pull",
}
FLAGS = {"--interactive", "--rm", "--init", "--userns=keep-id"}

# The options whose value is a path, of chat-cli and of the registry.
PATHS = {"--data", "--keys", "--log-file", "--requests"}


class Engine:
    def __init__(self, state: Path):
        self.state = state
        self.said = json.loads((state / "engine.json").read_text(encoding="utf-8"))
        self.containers = state / "containers"
        self.networks = state / "networks"
        self.containers.mkdir(exist_ok=True)
        self.networks.mkdir(exist_ok=True)

    def call(self, arguments: List[str]) -> int:
        with open(self.state / "calls.jsonl", "a", encoding="utf-8") as calls:
            calls.write(json.dumps(arguments) + "\n")
        command, rest = arguments[0], arguments[1:]
        if command == "network":
            return getattr(self, f"network_{rest[0]}")(rest[1:])
        return getattr(self, command)(rest)

    def network_create(self, arguments: List[str]) -> int:
        if "refuses-network" in self.said["faults"]:
            return refuse("Error: netavark: no free subnet", 125)
        network = self.networks / arguments[-1]
        if network.exists():
            return refuse(f"Error: network name {arguments[-1]} already used", 125)
        subnet = ipaddress.ip_network(dict(zip(arguments, arguments[1:]))["--subnet"])
        used = [*self.said["taken"], *(read(other)["subnet"] for other in self.networks.iterdir())]
        if any(subnet.overlaps(ipaddress.ip_network(each)) for each in used):
            if self.said["engine"] == "podman":
                return refuse(
                    f"Error: subnet {subnet} is already used on the host or by another config", 125
                )
            return refuse(
                "Error response from daemon: Pool overlaps with other one on this address space", 1
            )
        record = {"subnet": str(subnet), "options": arguments[:-1]}
        network.write_text(json.dumps(record), encoding="utf-8")
        print(arguments[-1])
        return 0

    def network_rm(self, arguments: List[str]) -> int:
        (name,) = arguments
        (self.networks / name).unlink()
        print(name)
        return 0

    def run(self, arguments: List[str]) -> int:
        options, at = [], 0
        while arguments[at].startswith("--"):
            if arguments[at] in VALUED:
                options.append((arguments[at], arguments[at + 1]))
                at += 2
            elif arguments[at] in FLAGS:
                options.append((arguments[at], None))
                at += 1
            else:
                return refuse(f"Error: unknown flag: {arguments[at]}", 125)
        command = arguments[at + 1 :]
        given = {}
        for option, value in options:
            given.setdefault(option, []).append(value)
        mounts = [value.split(":")[1] for value in given.get("--volume", [])]
        for mount in mounts:
            if not Path(mount).is_absolute():
                return refuse(f'Error: invalid container path "{mount}", must be absolute', 125)
        paths = [word for before, word in zip(command, command[1:]) if before in PATHS]
        if command[0] == "python3":
            paths.append(command[2])
        for path in paths:
            if not any(Path(path).is_relative_to(mount) for mount in mounts):
                return refuse(f"{command[0]}: {path}: no such file or directory", 127)
        if command[0] == "chat-cli":
            program = [self.said["chat_cli"], *command[1:]]
        else:
            program = [sys.executable, *command[1:]]
        env = dict(os.environ)
        env.update(value.split("=", 1) for value in given.get("--env", []))
        (name,) = given["--name"]
        process = subprocess.Popen(program, env=env)
        record = {"pid": process.pid, "labels": given.get("--label", []), "options": options}
        (self.containers / name).write_text(json.dumps(record), encoding="utf-8")
        # As the engine proxies the signals its `run` takes to the container.
        for number in (signal.SIGTERM, signal.SIGINT):
            signal.signal(number, lambda taken, _: process.send_signal(taken))
        status = process.wait()
        (self.containers / name).unlink(missing_ok=True)
        return 128 - status if status < 0 else status

    def exec(self, arguments: List[str]) -> int:
        container = arguments[2]
        if not (self.containers / container).exists():
            return refuse(f"Error: no container with name or ID {container!r} found", 125)
        if arguments[3] == "tc" and "refuses-tc" in self.said["faults"]:
            return refuse("Error: Specified qdisc kind is unknown.", 2)
        return 0

    def ps(self, arguments: List[str]) -> int:
        label = arguments[arguments.index("--filter") + 1].removeprefix("label=")
        for container in sorted(self.containers.iterdir()):
            if label in json.loads(container.read_text(encoding="utf-8"))["labels"]:
                print(container.name)
        return 0

    def rm(self, arguments: List[str]) -> int:
        for name in arguments[1:]:
            container = self.containers / name
            pid = json.loads(container.read_text(encoding="utf-8"))["pid"]
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            container.unlink()
            print(name)
        return 0


def read(record: Path) -> dict:
    return json.loads(record.read_text(encoding="utf-8"))


def refuse(reason: str, status: int) -> int:
    print(reason, file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(Engine(Path(sys.argv[1])).call(sys.argv[2:]))
