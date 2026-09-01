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
