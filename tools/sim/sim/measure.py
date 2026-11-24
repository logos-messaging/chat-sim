"""What the times of a run add up to."""

from __future__ import annotations

import statistics
from typing import Dict, Optional, Sequence


def spread(seconds: Sequence[float]) -> Optional[Dict[str, float]]:
    """The count, the median and the worst of some times, or `None` when there are none."""
    if not seconds:
        return None
    return {
        "count": len(seconds),
        "median": statistics.median(seconds),
        "worst": max(seconds),
    }
