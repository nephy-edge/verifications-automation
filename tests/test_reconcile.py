"""Tests for Phase 2 reconcile.py — the B3 threshold rules, plain asserts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations.config import Thresholds  # noqa: E402
from phase2_verification_engine.reconcile import estimate_gateway_fee, reconcile  # noqa: E402

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


def test_estimate_gateway_fee_within_plausible_range():
    reported = {"collections": 1000.0}
    calculated = {"collections": 970.0}  # 3% gap, gateway_fee_max_pct default is 5%
    fee = estimate_gateway_fee(reported, calculated, TH)
    assert fee["estimated_gateway_fee"] == 30.0
    assert round(fee["estimated_gateway_fee_pct"], 2) == 0.03
    assert fee["within_plausible_gateway_fee_range"] is True


def test_estimate_gateway_fee_gap_too_large_not_plausible():
    reported = {"collections": 1000.0}
    calculated = {"collections": 800.0}  # 20% gap, well beyond a plausible fee
    fee = estimate_gateway_fee(reported, calculated, TH)
    assert fee["within_plausible_gateway_fee_range"] is False


def test_estimate_gateway_fee_net_above_gross_not_plausible():
    # calculated (net) exceeding reported (gross) isn't a fee pattern at all.
    reported = {"collections": 1000.0}
    calculated = {"collections": 1050.0}
    fee = estimate_gateway_fee(reported, calculated, TH)
    assert fee["estimated_gateway_fee"] == 0.0
    assert fee["within_plausible_gateway_fee_range"] is False


def test_collections_exception_suppressed_within_plausible_gateway_fee_range():
    # SOP-2: a reported-vs-net gap consistent with a plausible gateway fee
    # (small gap, net < gross) is accounted for separately, not raised as a
    # raw unexplained variance.
    calculated = {"collections": 970.0, "disbursements": 500.0, "cash_total": 1500.0}
    reported = {"collections": 1000.0, "disbursements": 500.0, "cash_total": 1500.0}
    exc = reconcile(calculated, reported, TH)
    assert not any("collections" in e.id for e in exc)


def test_collections_exception_still_raised_beyond_plausible_gateway_fee_range():
    # Same shape as the suppressed case, but the gap is too large to be a
    # plausible fee -- the ordinary collections-variance check still fires.
    calculated = {"collections": 800.0, "disbursements": 500.0, "cash_total": 1500.0}
    reported = {"collections": 1000.0, "disbursements": 500.0, "cash_total": 1500.0}
    exc = reconcile(calculated, reported, TH)
    assert any("collections" in e.id for e in exc)


if __name__ == "__main__":
    test_no_exception_when_in_tolerance()
    test_collections_variance_flag()
    test_disbursement_abs_threshold()
    test_cash_balance_escalation()
    test_estimate_gateway_fee_within_plausible_range()
    test_estimate_gateway_fee_gap_too_large_not_plausible()
    test_estimate_gateway_fee_net_above_gross_not_plausible()
    test_collections_exception_suppressed_within_plausible_gateway_fee_range()
    test_collections_exception_still_raised_beyond_plausible_gateway_fee_range()
    print("reconcile tests OK")
