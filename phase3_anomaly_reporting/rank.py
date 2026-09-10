"""Risk ranking of exceptions (A3 step 5 -> reporting).

Normalizes exception severity to 0..1 and marks items at/above the configurable
high threshold (0.75) for forensic review. Purely deterministic.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

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

    for e, sev in zip(items, sevs, strict=True):
        e.severity = round((sev - lo) / span, 4) if span else 0.0
        if e.severity >= thresholds.anomaly_score_high:
            e.severity = min(1.0, e.severity)
        e.description += f" [severity={e.severity:.2f}]"

    items.sort(key=lambda e: e.severity, reverse=True)
    return items


def forensic_route(exceptions: Sequence[ExceptionItem], thresholds: Thresholds) -> list[ExceptionItem]:
    """Items at/above the high threshold are routed to forensic review."""
    return [e for e in exceptions if e.severity >= thresholds.anomaly_score_high]


# Rule kinds whose id carries one of these markers signal likely intent
# (round-tripping, structuring/micro-splitting, a sudden break from an
# account's own pattern) rather than a routine threshold breach -- see
# anomaly.py. rank_exceptions() normalizes severity *relative to the other
# exceptions in the same run*, so a genuinely serious intent-based finding
# can be normalized below the forensic-review threshold purely because
# something else in that run happened to score higher, not because it's any
# less serious on its own. Mandatory referral checks the id, not the
# (possibly-normalized-down) severity, so that can't happen.
_INTENT_BASED_MARKERS = (":anom:roundtrip:", ":anom:microsplit:", ":anom:seqjump:")


def mandatory_fraud_referrals(exceptions: Sequence[ExceptionItem]) -> list[ExceptionItem]:
    """Exceptions that must always be referred for forensic review, regardless
    of where their severity lands after relative ranking. Call after
    rank_exceptions() (or independently -- this doesn't use severity)."""
    return [e for e in exceptions if any(marker in e.id for marker in _INTENT_BASED_MARKERS)]
