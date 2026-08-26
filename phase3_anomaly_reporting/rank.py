"""Risk ranking of exceptions (A3 step 5 -> reporting).

Normalizes exception severity to 0..1 and marks items at/above the configurable
high threshold (0.75) for forensic review. Purely deterministic.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from phase0_foundations.config import Thresholds
from phase0_foundations.models import ExceptionItem


def rank_exceptions(
    exceptions: Iterable[ExceptionItem],
    thresholds: Thresholds,
) -> list[ExceptionItem]:
    items = list(exceptions)
    if not items:
        return items

    sevs = [max(0.0, min(1.0, e.severity)) for e in items]
    hi = max(sevs) if sevs else 1.0
    lo = min(sevs) if sevs else 0.0
    span = (hi - lo) or 1.0

    for e, sev in zip(items, sevs):
        e.severity = round((sev - lo) / span, 4) if span else 0.0
        if e.severity >= thresholds.anomaly_score_high:
            e.severity = min(1.0, e.severity)
        e.description += f" [severity={e.severity:.2f}]"

    items.sort(key=lambda e: e.severity, reverse=True)
    return items


def forensic_route(exceptions: Sequence[ExceptionItem], thresholds: Thresholds) -> list[ExceptionItem]:
    """Items at/above the high threshold are routed to forensic review."""
    return [e for e in exceptions if e.severity >= thresholds.anomaly_score_high]
