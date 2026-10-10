"""Which nodes each node dials, so that the network ends as a ring with chords."""

from __future__ import annotations

from typing import List, Tuple

# The mesh degree gossipsub aims for: the links a node is given.
LINKS = 6

# The mesh size gossipsub grows back from: a node with fewer grafts more.
MESH_LOW = 4


def strides(n: int) -> Tuple[int, int, int]:
    """The three steps round a ring of `n` nodes, 7 or more, that each node is linked along.

    They are 1, `a` and `a * a` for the smallest `a` whose cube reaches `n`, so a walk of a few
    links of each step reaches any node. On a small ring the longer two are pulled in until all
    three differ and none meets itself coming the other way round.
    """
    a = 2
    while a**3 < n:
        a += 1
    longest = min(a * a, (n - 1) // 2)
    return (1, min(a, longest - 1), longest)


def dials(n: int) -> List[List[int]]:
    """For each of `n` nodes, the nodes it dials at its start, each of them started before it.

    Fewer than seven nodes all link to each other. From seven up each node has `LINKS` links: one
    in each direction along each stride. Whichever end of a link starts later dials it.
    """
    if n <= LINKS:
        return [list(range(node)) for node in range(n)]
    dialing = []
    for node in range(n):
        earlier = set()
        for stride in strides(n):
            for other in (node - stride, node + stride - n):
                if 0 <= other < node:
                    earlier.add(other)
        dialing.append(sorted(earlier))
    return dialing


def links(dialing: List[List[int]]) -> List[int]:
    """How many links each node ends with: those it dials and those that dial it."""
    count = [len(dialed) for dialed in dialing]
    for dialed in dialing:
        for other in dialed:
            count[other] += 1
    return count
