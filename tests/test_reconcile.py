"""Tests for Phase 2 reconcile.py — the B3 threshold rules, plain asserts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations.config import Thresholds  # noqa: E402
from phase2_verification_engine.reconcile import reconcile  # noqa: E402

TH = Thresholds()


def test_no_exception_when_in_tolerance():
    calculated = {"collections": 1000.0, "disbursements": 500.0, "cash_total": 1500.0}
    reported = {"collections": 1010.0, "disbursements": 495.0, "cash_total": 1505.0}
    exc = reconcile(calculated, reported, TH)
    assert exc == []


def test_collections_variance_flag():
    calculated = {"collections": 1000.0, "disbursements": 500.0, "cash_total": 1500.0}
    reported = {"collections": 1100.0, "disbursements": 500.0, "cash_total": 1500.0}
    exc = reconcile(calculated, reported, TH)
    kinds = [e.kind for e in exc]
    assert "reconciliation" in kinds
    assert any("collections" in e.id for e in exc)


def test_disbursement_abs_threshold():
    # Variance within 5% but over $100 absolute -> flag on abs.
    calculated = {"collections": 0.0, "disbursements": 10_000.0, "cash_total": 10_000.0}
    reported = {"collections": 0.0, "disbursements": 10_150.0, "cash_total": 10_000.0}
    exc = reconcile(calculated, reported, TH)
    assert any("disbursements" in e.id for e in exc)


def test_cash_balance_escalation():
    calculated = {"collections": 0.0, "disbursements": 0.0, "cash_total": 1000.0}
    reported = {"collections": 0.0, "disbursements": 0.0, "cash_total": 1020.0}
    exc = reconcile(calculated, reported, TH)
    assert any("cash" in e.id for e in exc)


if __name__ == "__main__":
    test_no_exception_when_in_tolerance()
    test_collections_variance_flag()
    test_disbursement_abs_threshold()
    test_cash_balance_escalation()
    print("reconcile tests OK")
