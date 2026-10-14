"""A run in containers: a network of the run's own, and the registry and each member in a
container on it, each at an address of its own, with the wire's conditions on what it sends.

The engine is docker or podman, driven by its command line. A container is started with
`run -i`, so the run holds its input and output as it holds those of a process. The run's
directory and the registry's script are mounted at the paths they have on this machine, so a
path on a command line is the same in a container and out of it.
"""

from __future__ import annotations

import hashlib
import ipaddress
import itertools
import os
import secrets
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

from .peers import Stopped
from .scenario import Wire

ENGINES = ("docker", "podman")

# What every container and the network of a run are labelled with, by the run's id.
LABEL = "logos-chat-sim"

# The interface a container has on the run's network.
INTERFACE = "eth0"

# How long the engine has to do what a run asks of it, but for running a container.
ANSWER = 60.0

# A run's network is a /24 of these: after its gateway, room for the registry and 252 members,
# against the 200 of the largest scenario.
SUBNETS = ipaddress.ip_network("10.213.0.0/16")
PREFIX = 24

# What docker and podman say when the subnet a network is given overlaps one in use.
TAKEN = (
    "Pool overlaps with other one on this address space",
    "is already used on the host or by another config",
)


class Rig:
    """The engine's command lines for one run, and the network its containers share."""

    def __init__(self, engine: str, image: str, out: Path):
        self.engine = engine
        self.image = image
        # Mounted where it is, so it is a path of this machine and of every container.
        self.out = out.resolve()
        self.id = secrets.token_hex(4)
        self.network = f"sim-{self.id}"
        self._created = False
        self._addresses: List[str] = []

    def create(self, members: int) -> None:
        """Makes the run's network, and takes an address on it for the registry and each member.

        The network reaches nothing outside it. Its subnet is the one of `SUBNETS` the run's id
        picks, or the next the engine does not say is taken: docker takes a container's address
        only on a network it was given the subnet of.
        """
        subnets = list(SUBNETS.subnets(new_prefix=PREFIX))
        # Less the network's own address, its gateway's and its broadcast address.
        room = subnets[0].num_addresses - 3
        if members + 1 > room:
            raise Stopped(f"the run needs {members + 1} addresses, and its network has {room}")
        label = f"{LABEL}={self.id}"
        start = int(self.id, 16) % len(subnets)
        for subnet in subnets[start:] + subnets[:start]:
            gateway, *hosts = (str(host) for host in subnet.hosts())
            try:
                self._engine(
                    "network",
                    "create",
                    "--internal",
                    "--subnet",
                    str(subnet),
                    "--gateway",
                    gateway,
                    "--label",
                    label,
                    self.network,
                )
            except Stopped as refused:
                if any(taken in str(refused) for taken in TAKEN):
                    continue
                raise
            self._created = True
            self._addresses = hosts[: members + 1]
            return
        raise Stopped(f"the engine says each /{PREFIX} of {SUBNETS} is taken")

    def address(self, index: int) -> str:
        """The address of the member started at `index`."""
        return self._addresses[index + 1]

    def registry(self, script: Path, arguments: Sequence[str]) -> List[str]:
        """The command line that runs the mock registry in a container, serving on its address."""
        address = self._addresses[0]
        command = ["python3", "-B", str(script), *arguments, "--bind", address]
        return self._run("registry", address, command, {}, [script.resolve()])

    def member(self, name: str, index: int, arguments: Sequence[str], env: dict) -> List[str]:
        """The command line that runs chat-cli with `arguments` in the member's container."""
        address = self.address(index)
        command = ["chat-cli", *arguments, "--node-ip", address]
        return self._run(name, address, command, env, [])

    def shape(self, name: str, delay_ms: int, wire: Wire) -> None:
        """Puts the wire's conditions on what the member `name` sends from here on."""
        seed = int.from_bytes(
            hashlib.blake2b(f"{wire.seed} {name}".encode(), digest_size=8).digest(), "big"
        )
        netem = [
            "delay",
            f"{delay_ms}ms",
            f"{wire.jitter_ms}ms",
            "loss",
            f"{100 * wire.loss:.10g}%",
            "seed",
            str(seed),
        ]
        qdisc = ["tc", "qdisc", "add", "dev", INTERFACE, "root", "netem", *netem]
        what = f"tc on the interface of {name}"
        self._engine("exec", "--user", "0", self._container(name), *qdisc, what=what)

    def remove(self) -> Optional[str]:
        """Removes every container of the run and its network. Returns what the engine refused."""
        try:
            label = f"label={LABEL}={self.id}"
            left = self._engine("ps", "--all", "--quiet", "--filter", label).split()
            if left:
                self._engine("rm", "--force", *left)
            if self._created:
                self._engine("network", "rm", self.network)
        except Stopped as refused:
            return str(refused)
        return None

    def _container(self, name: str) -> str:
        return f"sim-{self.id}-{name}"

    def _run(
        self, name: str, address: str, command: Sequence[str], env: dict, mounts: Sequence[Path]
    ) -> List[str]:
        line = [
            self.engine,
            "run",
            "--interactive",
            "--rm",
            # chat-cli and the registry are the first process of their container, which no
            # signal ends by its default action.
            "--init",
            "--pull",
            "never",
            "--name",
            self._container(name),
            "--label",
            f"{LABEL}={self.id}",
            "--network",
            self.network,
            "--ip",
            address,
            "--cap-add",
            "NET_ADMIN",
            # A container labelled apart reads no file of this machine where SELinux enforces.
            "--security-opt",
            "label=disable",
            *self._user(),
            "--volume",
            f"{self.out}:{self.out}",
        ]
        for mount in mounts:
            line += ["--volume", f"{mount}:{mount}:ro"]
        for key, value in env.items():
            line += ["--env", f"{key}={value}"]
        return [*line, self.image, *command]

    def _user(self) -> List[str]:
        """Who a container runs as: the user of this machine, so that what it writes is theirs."""
        if self.engine == "podman":
            return ["--userns=keep-id"]
        return ["--user", f"{os.getuid()}:{os.getgid()}"]

    def _engine(self, *arguments: str, what: Optional[str] = None) -> str:
        """What the engine writes for a command that has to work, which `what` names."""
        if what is None:
            words = itertools.takewhile(lambda word: not word.startswith("-"), arguments)
            what = " ".join([self.engine, *itertools.islice(words, 2)])
        try:
            done = subprocess.run(
                [self.engine, *arguments], capture_output=True, text=True, timeout=ANSWER
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Stopped(f"{what}: {error}") from None
        if done.returncode != 0:
            said = [line for line in done.stderr.splitlines() if line.strip()]
            raise Stopped(
                f"{what} ended with status {done.returncode}: "
                f"{said[-1] if said else 'it said nothing on standard error'}"
            )
        return done.stdout

