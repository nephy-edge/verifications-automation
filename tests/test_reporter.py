"""Tests for Phase 3 reporter.py's forensic-review surfacing, plain asserts."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations.config import Thresholds  # noqa: E402
from phase0_foundations.models import ExceptionItem, VerificationRun  # noqa: E402
from phase3_anomaly_reporting.reporter import build_report  # noqa: E402

TH = Thresholds()


def _run(*exceptions):
    return VerificationRun(id="t1", status="done", inputs={}, aggregates={}, exceptions=list(exceptions))


def test_statement_breakdown_rendered_in_markdown_when_present(tmp_path):
    """A watcher run carrying statement_breakdown in inputs must render the
    section in the .md (totals + by-category + by-month), and a run without it
    must render nothing (backward compatible)."""
    from phase0_foundations.models import VerificationRun
    from phase3_anomaly_reporting.reporter import _markdown

    breakdown = {
        "totals": {"bank_rows": 207, "bank_in": 8119984.02, "bank_out": 7075788.89,
                   "bank_cash_total": 15195772.91, "fx_base_currency": "USD"},
        "by_statement": {"BBVA JUN.pdf": {"rows": 32, "in": 1000.0, "out": 500.0}},
        "by_category": {"kushki (payment processor)": {"rows": 148, "in": 8105501.22, "out": 0.0}},
        "by_month": {"2026-06": {"tape_in": 1000.0, "tape_out": 200.0,
                                 "bank_in": 300.0, "bank_out": 100.0,
                                 "tape_rows": 10, "bank_rows": 32}},
    }
    run = VerificationRun(id="t1", status="done", inputs={"statement_breakdown": breakdown},
                          aggregates={"cash_total": 1.0}, exceptions=[])
    md = _markdown(run)
    assert "## Statement-side breakdown" in md
    assert "bank in 8,119,984.02" in md
    assert "kushki (payment processor)" in md
    assert "| 2026-06 |" in md

    # absent -> no section at all
    run2 = VerificationRun(id="t1", status="done", inputs={}, aggregates={}, exceptions=[])
    assert "## Statement-side breakdown" not in _markdown(run2)


def test_forensic_review_omitted_from_json_without_thresholds(tmp_path):
    run = _run(ExceptionItem(id="t1:recon:collections", kind="reconciliation", severity=0.9))
    json_path = build_report(run, tmp_path, exceptions=run.exceptions)
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert "forensic_review" not in report


def test_forensic_review_lists_high_severity_exceptions(tmp_path):
    high = ExceptionItem(id="t1:recon:cash", kind="reconciliation", severity=0.9, description="cash variance")
    low = ExceptionItem(id="t1:anom:round", kind="anomaly", severity=0.2, description="round amount")
    run = _run(high, low)
    json_path = build_report(run, tmp_path, exceptions=run.exceptions, thresholds=TH)
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["forensic_review"]["threshold"] == TH.anomaly_score_high
    assert report["forensic_review"]["count"] == 1
    assert report["forensic_review"]["exception_ids"] == ["t1:recon:cash"]

    md = json_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "Forensic review queue" in md
    assert "cash variance" in md
    assert "round amount" not in md.split("Forensic review queue")[1].split("## Human review")[0]


def test_forensic_review_reports_empty_queue_explicitly(tmp_path):
    run = _run(ExceptionItem(id="t1:anom:round", kind="anomaly", severity=0.1))
    json_path = build_report(run, tmp_path, exceptions=run.exceptions, thresholds=TH)
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["forensic_review"]["count"] == 0
    md = json_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "No exceptions at or above the forensic-review threshold." in md


def test_mandatory_fraud_referral_surfaced_regardless_of_severity(tmp_path):
    # Low raw severity on purpose: mandatory referral is id-marker-based, not
    # severity-based (see rank.mandatory_fraud_referrals).
    roundtrip = ExceptionItem(
        id="t1:anom:roundtrip:0", kind="anomaly", severity=0.1, description="round-trip finding",
    )
    run = _run(roundtrip)
    json_path = build_report(run, tmp_path, exceptions=run.exceptions)
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["mandatory_fraud_referrals"]["count"] == 1
    assert report["mandatory_fraud_referrals"]["exception_ids"] == ["t1:anom:roundtrip:0"]

    md = json_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "Mandatory fraud referrals" in md
    assert "round-trip finding" in md


def test_mandatory_fraud_referral_empty_when_no_intent_based_findings(tmp_path):
    run = _run(ExceptionItem(id="t1:recon:collections", kind="reconciliation", severity=0.9))
    json_path = build_report(run, tmp_path, exceptions=run.exceptions)
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["mandatory_fraud_referrals"]["count"] == 0
    md = json_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "None." in md.split("Mandatory fraud referrals")[1].split("## Human review")[0]
