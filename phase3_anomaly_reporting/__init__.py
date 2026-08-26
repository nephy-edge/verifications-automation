"""Phase 3 — Anomaly detection & reporting.

Rules-based anomaly detection (D1) plus the LLM narrative step (D3) and
template-driven reporters. Deterministic rules run first; the LLM only explains
and cross-synthesizes, never computes.
"""

from phase3_anomaly_reporting.anomaly import detect_anomalies
from phase3_anomaly_reporting.rank import rank_exceptions
from phase3_anomaly_reporting.reporter import build_report

__all__ = ["build_report", "detect_anomalies", "rank_exceptions"]
