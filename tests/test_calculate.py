"""Tests for Phase 2 calculate.py — plain asserts, runnable with the venv python."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


if __name__ == "__main__":
    test_aggregates_positive_and_negative()
    test_aggregates_empty()
    print("calculate tests OK")
