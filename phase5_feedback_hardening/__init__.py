"""Phase 5 — Feedback & hardening loop (workflow record B5).

Captures human review signal and records rule promotions (LLM/anomaly step ->
deterministic) so the workflow becomes measurably more deterministic with each
iteration. The hardening log is the compounding asset — "a workflow at month 6
is a different, better thing than at day 1."
"""

from phase5_feedback_hardening.harden import (
    HardeningLog,
    exception_pattern,
    promote_to_rule,
    record_feedback,
)

__all__ = ["HardeningLog", "exception_pattern", "promote_to_rule", "record_feedback"]
