"""What the lines, the logs and the registry's requests of a run add up to."""

from __future__ import annotations

import re
import statistics
from datetime import datetime, timezone
from typing import Dict, Iterator, List, NamedTuple, Optional, Sequence

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_EPOCH = re.compile(
    r"^(\S+) .*libchat::groupv2trace: epoch .*\bconvo=(\S+) epoch=(\d+) state=(\S+) members=(\d+)"
)
_WELCOME = re.compile(r"^(\S+) .*libchat::inbox_v2: Publish WelcomeMessage bytes=(\d+)")
_UNCHECKED = re.compile(r" libchat::groupv2trace: unreadable .*\bwhy=sender\b")

# What a log has to let through for `welcomes` to find anything in it.
LOG_FILTER = "warn,libchat::groupv2trace=info,libchat::inbox_v2=info"


def spread(seconds: Sequence[float]) -> Optional[Dict[str, float]]:
    """The count, the median and the worst of some times, or `None` when there are none."""
    if not seconds:
        return None
    return {
        "count": len(seconds),
        "median": statistics.median(seconds),
        "worst": max(seconds),
    }


def batches(joiners: Sequence, size: int) -> List[list]:
    """`joiners` in the order given, cut into batches of `size`; the last holds what is left."""
    return [list(joiners[at : at + size]) for at in range(0, len(joiners), size)]


def _written(stamp: str) -> float:
    """When a log line was written, in seconds since the epoch."""
    written = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ")
    return written.replace(tzinfo=timezone.utc).timestamp()


class Epoch(NamedTuple):
    """An epoch record: when it was written, and what its installation then held."""

    at: float
    epoch: int
    state: str
    members: int


def _epochs(log: str, convo: str) -> Iterator[Epoch]:
    """The epoch records a log holds of `convo`, in the order they were written."""
    for line in _ANSI.sub("", log).splitlines():
        record = _EPOCH.search(line)
        if record and record.group(2) == convo:
            written, _, epoch, state, members = record.groups()
            yield Epoch(_written(written), int(epoch), state, int(members))


def reached(log: str, convo: str, members: int) -> Optional[Epoch]:
    """The record by which a log's installation first held `convo` with `members` members.

    `None` when the log holds no such record.
    """
    return next((record for record in _epochs(log, convo) if record.members == members), None)


def held(log: str, convo: str, epoch: int) -> Optional[str]:
    """The state a log's installation holds `convo` in at `epoch`, or `None` when it holds none."""
    return next((record.state for record in _epochs(log, convo) if record.epoch == epoch), None)


def welcomes(log: str, since: float, until: float) -> List[int]:
    """The bytes of each Welcome a log says its installation published from `since` to `until`."""
    found = (_WELCOME.search(line) for line in _ANSI.sub("", log).splitlines())
    return [
        int(welcome.group(2))
        for welcome in found
        if welcome and since <= _written(welcome.group(1)) <= until
    ]


def unchecked(log: str) -> int:
    """How many frames a log says decrypted with no answer for their sender's account.

    Such a record tells of the registry and not of the group: the lookup of the sender failed.
    """
    return sum(bool(_UNCHECKED.search(line)) for line in _ANSI.sub("", log).splitlines())


def lookups(requests: Sequence[dict], account: str, since: float, until: float) -> List[float]:
    """When each lookup of `account` reached the registry, from `since` to `until`."""
    path = f"/v1/account/{account}"
    return [
        request["at"]
        for request in requests
        if request["method"] == "GET"
        and request["path"] == path
        and since <= request["at"] <= until
    ]


def post_times(sent_at: float, read_at: Sequence[float], asked_at: Sequence[float]) -> dict:
    """How long a post took to be read, and how much of that was the registry.

    A member that gets a post asks the registry for its sender's account before it shows the
    post, and the registry stamps the request as it arrives. So up to that stamp the time is
    delivery, and after it the lookup. A request does not say whose it is: when each reader made
    one, the mean lookup is the mean read time less the mean time a request arrived, and when
    they did not the two cannot be told apart.
    """
    read = [at - sent_at for at in read_at]
    asked = [at - sent_at for at in asked_at]
    apart = bool(read) and len(read) == len(asked)
    return {
        "read": spread(read),
        "delivered": spread(asked) if apart else None,
        "lookup_mean": statistics.fmean(read) - statistics.fmean(asked) if apart else None,
        "lookups": len(asked),
    }
