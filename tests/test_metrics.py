"""Tests for phase0_foundations/metrics.py + harden.exception_pattern.

Covers coverage_pct, run->sign-off lead times, and the per-borrower recency
view from the run log/persisted reports. Plain asserts, same style as
test_reconcile.py.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations.log import RunLog  # noqa: E402
from phase0_foundations.metrics import (  # noqa: E402
    coverage,
    last_verification_per_borrower,
    lead_times,
)
from phase0_foundations.models import ExceptionItem  # noqa: E402
from phase5_feedback_hardening.harden import exception_pattern  # noqa: E402


def test_coverage_full_population():
    assert coverage(5, 5) == {"rows_ingested": 5, "population_size": 5, "coverage_pct": 100.0}


def test_coverage_partial_population():
    stats = coverage(2, 10)
    assert stats["rows_ingested"] == 2
    assert stats["population_size"] == 10
    assert stats["coverage_pct"] == 20.0


def test_coverage_empty_population_is_vacuously_complete():
    assert coverage(0, 0)["coverage_pct"] == 100.0


def test_exception_pattern_recon():
    item = ExceptionItem(id="run123:recon:collections", kind="reconciliation")
    assert exception_pattern(item) == "collections"


def test_exception_pattern_anomaly():
    item = ExceptionItem(id="run123:anom:roundtrip:3", kind="anomaly")
    assert exception_pattern(item) == "roundtrip"


def test_exception_pattern_falls_back_to_kind():
    item = ExceptionItem(id="no_segments", kind="anomaly")
    assert exception_pattern(item) == "anomaly"


def test_lead_times_matches_run_started_to_last_sign_off():
    with tempfile.TemporaryDirectory() as tmp:
        log = RunLog(Path(tmp) / "log.jsonl")
        log.append({"event": "run_started", "run_id": "aaa", "ts": "2026-08-25T09:00:00+00:00"})
        log.append({"event": "sign_off", "exception_id": "aaa:anom:dup:0", "ts": "2026-08-25T09:05:30+00:00"})
        rows = lead_times(log)
        assert len(rows) == 1
        row = rows[0]
        assert row["run_id"] == "aaa"
        assert row["signed_off"] == "2026-08-25T09:05:30+00:00"
        assert row["elapsed_seconds"] == 330.0


def test_lead_times_unfinished_run_has_no_elapsed():
    with tempfile.TemporaryDirectory() as tmp:
        log = RunLog(Path(tmp) / "log.jsonl")
        log.append({"event": "run_started", "run_id": "bbb", "ts": "2026-08-25T09:00:00+00:00"})
        rows = lead_times(log)
        assert rows[0]["run_id"] == "bbb"
        assert rows[0]["signed_off"] is None
        assert rows[0]["elapsed_seconds"] is None


def test_last_verification_keeps_latest_per_borrower_oldest_first():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        log = RunLog(Path(tmp) / "log.jsonl")
        for run_id, borrower, ts in [
            ("r1", "leasy", "2026-08-20T09:00:00+00:00"),
            ("r2", "first_circle", "2026-08-21T09:00:00+00:00"),
            ("r3", "leasy", "2026-08-22T09:00:00+00:00"),
        ]:
            report = out / run_id / f"run_{run_id}.json"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({"inputs": {"borrower": borrower}}), encoding="utf-8")
            log.append({"event": "run_completed", "run_id": run_id, "ts": ts})
        rows = last_verification_per_borrower(log, out)
        assert [(r["borrower"], r["run_id"]) for r in rows] == [
            ("first_circle", "r2"),
            ("leasy", "r3"),
        ]


def test_last_verification_defaults_borrower_to_uploaded():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        log = RunLog(Path(tmp) / "log.jsonl")
        log.append({"event": "run_completed", "run_id": "r9", "ts": "2026-08-20T09:00:00+00:00"})
        rows = last_verification_per_borrower(log, out)
        assert rows[0]["borrower"] == "uploaded"


if __name__ == "__main__":
    test_coverage_full_population()
    test_coverage_partial_population()
    test_coverage_empty_population_is_vacuously_complete()
    test_exception_pattern_recon()
    test_exception_pattern_anomaly()
    test_exception_pattern_falls_back_to_kind()
    test_lead_times_matches_run_started_to_last_sign_off()
    test_lead_times_unfinished_run_has_no_elapsed()
    test_last_verification_keeps_latest_per_borrower_oldest_first()
    test_last_verification_defaults_borrower_to_uploaded()
    print("metrics tests OK")
