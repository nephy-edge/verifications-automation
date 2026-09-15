"""Human-signal capture and rule hardening (B5).

- `record_feedback`: log a human review outcome for a pattern so it can be
  analysed (approved/edited/rejected together with the reason).
- `promote_to_rule`: record that a once-LLM/judgement step was promoted to a
  deterministic rule, with the effect.

Both append to the run log (jsonl) so the hardening history is auditable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from phase0_foundations.log import RunLog
from phase0_foundations.models import ExceptionItem


def exception_pattern(item: ExceptionItem) -> str:
    """Stable rule/category tag for an exception, taken from its id.

    Exception ids encode the rule that fired as ``<run_id>:<kind>:<rule>[:<n>]``
    (e.g. ``...:anom:roundtrip:3``, ``...:recon:collections``,
    ``...:asset:owner_mismatch:ABC123``, ``...:match:in_reported_only:...``),
    where the kind is the id shorthand ``recon``/``anom``/``asset``/``match``.
    Returns the segment that follows it, falling back to ``kind`` when the id
    has no such segment.
    """
    parts = (item.id or "").split(":")
    for shorthand in ("recon", "anom", "asset", "match"):
        if shorthand in parts:
            idx = parts.index(shorthand)
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return item.kind or ""


def record_feedback(log: RunLog, pattern: str, action: str, reviewer: str, effect: str = "") -> None:
    """Log a single human decision for a recurring pattern."""
    log.append(
        {
            "event": "feedback",
            "pattern": pattern,
            "action": action,      # approved | edited | rejected
            "reviewer": reviewer,
            "effect": effect,
        }
    )


def promote_to_rule(log: RunLog, pattern: str, change_made: str, effect: str) -> None:
    """Record that a pattern was promoted from LLM/judgement to a deterministic rule."""
    log.append(
        {
            "event": "hardening",
            "date": datetime.now(timezone.utc).isoformat()[:10],
            "pattern": pattern,
            "change_made": change_made,
            "effect": effect,
        }
    )


def hardening_entries(log: RunLog) -> list[dict]:
    """Read the hardening history from a run log."""
    return [e for e in log.read() if e.get("event") == "hardening"]
