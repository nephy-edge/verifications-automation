"""Tests for the run-level sign-off trail (phase0_foundations/signoff.py).

SOP 1's "Sign-Off & Documentation" step needs a durable record of who
approved a run's working paper and when. This is deliberately a *run-level*
approval (the whole working paper), distinct from the existing per-exception
`phase4_human_review.approval.sign_off` (Head-of-Risk gate on one flagged
exception) -- different event name (`run_signed_off` vs `sign_off`) so the
two don't collide in the shared audit log or in metrics.lead_times.
"""

from __future__ import annotations

import json

import pytest

from phase0_foundations.log import RunLog
from phase0_foundations.signoff import (
    AlreadySignedOffError,
    ReportNotFoundError,
    get_signoff,
    sign_off_run,
    verify_signoff,
)
from phase3_anomaly_reporting.reporter import build_report
from phase0_foundations.models import VerificationRun


def _write_report(out_dir, borrower="leasy", run_id="abc123"):
    run = VerificationRun(id=run_id, status="done", started_at="2026-09-11T00:00:00+00:00")
    run.aggregates = {"collections": 100.0}
    run.inputs = {"borrower": borrower}
    build_report(run, out_dir / borrower)
    return run_id


def test_sign_off_run_writes_record_and_logs_event(tmp_path):
    out_dir = tmp_path / "out"
    log_path = tmp_path / "verifications.log.jsonl"
    run_id = _write_report(out_dir)

    record = sign_off_run(out_dir, log_path, "leasy", run_id, "nephy@lendable.io", note="ok")

    assert record.signed_by == "nephy@lendable.io"
    assert record.note == "ok"
    assert record.report_sha256

    signoff_file = out_dir / "leasy" / f"run_{run_id}.signoff.json"
    assert signoff_file.exists()
    on_disk = json.loads(signoff_file.read_text(encoding="utf-8"))
    assert on_disk["signed_by"] == "nephy@lendable.io"

    events = RunLog(log_path).read()
    matching = [e for e in events if e.get("event") == "run_signed_off"]
    assert len(matching) == 1
    assert matching[0]["run_id"] == run_id
    assert matching[0]["signed_by"] == "nephy@lendable.io"


def test_get_signoff_returns_none_when_absent(tmp_path):
    out_dir = tmp_path / "out"
    run_id = _write_report(out_dir)
    assert get_signoff(out_dir, "leasy", run_id) is None


def test_sign_off_missing_report_raises(tmp_path):
    out_dir = tmp_path / "out"
    log_path = tmp_path / "verifications.log.jsonl"
    with pytest.raises(ReportNotFoundError):
        sign_off_run(out_dir, log_path, "leasy", "does-not-exist", "nephy@lendable.io")


def test_sign_off_twice_refuses_to_overwrite(tmp_path):
    out_dir = tmp_path / "out"
    log_path = tmp_path / "verifications.log.jsonl"
    run_id = _write_report(out_dir)

    sign_off_run(out_dir, log_path, "leasy", run_id, "nephy@lendable.io")
    with pytest.raises(AlreadySignedOffError):
        sign_off_run(out_dir, log_path, "leasy", run_id, "someone_else@lendable.io")

    # the original signer of record is untouched
    record = get_signoff(out_dir, "leasy", run_id)
    assert record["signed_by"] == "nephy@lendable.io"


def test_sign_off_requires_signed_by(tmp_path):
    out_dir = tmp_path / "out"
    log_path = tmp_path / "verifications.log.jsonl"
    run_id = _write_report(out_dir)
    with pytest.raises(ValueError):
        sign_off_run(out_dir, log_path, "leasy", run_id, "   ")


def test_verify_signoff_detects_report_changed_after_signing(tmp_path):
    out_dir = tmp_path / "out"
    log_path = tmp_path / "verifications.log.jsonl"
    run_id = _write_report(out_dir)

    sign_off_run(out_dir, log_path, "leasy", run_id, "nephy@lendable.io")
    assert verify_signoff(out_dir, "leasy", run_id) is True

    # simulate the report being regenerated/edited after sign-off
    report_path = out_dir / "leasy" / f"run_{run_id}.json"
    data = json.loads(report_path.read_text(encoding="utf-8"))
    data["aggregates"]["collections"] = 999.0
    report_path.write_text(json.dumps(data), encoding="utf-8")

    assert verify_signoff(out_dir, "leasy", run_id) is False


def test_verify_signoff_false_when_never_signed(tmp_path):
    out_dir = tmp_path / "out"
    run_id = _write_report(out_dir)
    assert verify_signoff(out_dir, "leasy", run_id) is False
