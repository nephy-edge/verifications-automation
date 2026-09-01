"""Tests for Phase 2 calculate.py — plain asserts, runnable with the venv python."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations.fx import FXConfig  # noqa: E402
from phase2_verification_engine.calculate import calculate_aggregates  # noqa: E402


def test_aggregates_positive_and_negative():
    rows = [
        {"amount": 1000.0, "direction": "in"},
        {"amount": 250.0, "direction": "in"},
        {"amount": 400.0, "direction": "out"},
        {"amount": 50.0, "direction": "out"},
    ]
    agg = calculate_aggregates(rows)
    assert agg["cash_in"] == 1250.0
    assert agg["cash_out"] == 450.0
    assert agg["cash_total"] == 1700.0
    assert agg["collections"] == 1250.0
    assert agg["disbursements"] == 450.0
    assert agg["transaction_count"] == 4


def test_aggregates_empty():
    agg = calculate_aggregates([])
    assert agg["cash_in"] == 0.0
    assert agg["cash_out"] == 0.0
    assert agg["transaction_count"] == 0


def test_aggregates_without_fx_ignores_currency_field_unchanged():
    """No `fx` passed -> byte-identical to pre-FX behavior, even if rows carry a currency."""
    rows = [
        {"amount": 1000.0, "direction": "in", "currency": "KES"},
        {"amount": 500.0, "direction": "out", "currency": "USD"},
    ]
    agg = calculate_aggregates(rows)
    assert agg["cash_in"] == 1000.0
    assert agg["cash_out"] == 500.0
    assert "unmapped_currencies" not in agg
    assert "currencies_seen" not in agg


def test_aggregates_with_fx_converts_known_currency():
    fx = FXConfig(base_currency="USD", rates={"KES": 0.0067})
    rows = [
        {"amount": 1000.0, "direction": "in", "currency": "KES"},   # -> 6.7
        {"amount": 250.0, "direction": "in", "currency": "USD"},    # -> 250.0
    ]
    agg = calculate_aggregates(rows, fx=fx)
    assert agg["cash_in"] == 256.7
    assert agg["fx_base_currency"] == "USD"
    assert agg["currencies_seen"] == ["KES", "USD"]
    assert "unmapped_currencies" not in agg


def test_aggregates_with_fx_flags_unmapped_currency_without_crashing():
    fx = FXConfig(base_currency="USD", rates={"KES": 0.0067})
    rows = [{"amount": 500.0, "direction": "in", "currency": "NGN"}]
    agg = calculate_aggregates(rows, fx=fx)
    assert agg["cash_in"] == 500.0  # left unconverted, not guessed
    assert agg["unmapped_currencies"] == ["NGN"]


def test_aggregates_with_fx_blank_currency_assumed_base():
    fx = FXConfig(base_currency="USD", rates={"KES": 0.0067})
    rows = [{"amount": 100.0, "direction": "in", "currency": ""}]
    agg = calculate_aggregates(rows, fx=fx)
    assert agg["cash_in"] == 100.0
    assert agg["currencies_seen"] == []
    assert "unmapped_currencies" not in agg


if __name__ == "__main__":
    test_aggregates_positive_and_negative()
    test_aggregates_empty()
    test_aggregates_without_fx_ignores_currency_field_unchanged()
    test_aggregates_with_fx_converts_known_currency()
    test_aggregates_with_fx_flags_unmapped_currency_without_crashing()
    test_aggregates_with_fx_blank_currency_assumed_base()
    print("calculate tests OK")
