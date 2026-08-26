"""Phase 2 — Verification engine.

Deterministic aggregation (calculate) and reconciliation (reconcile). All
arithmetic happens here in code; no LLM is involved. The LLM receives these
calculated totals only as immutable inputs.
"""

from phase2_verification_engine.calculate import calculate_aggregates
from phase2_verification_engine.reconcile import reconcile

__all__ = ["calculate_aggregates", "reconcile"]
