"""Tests for phase2_verification_engine/transaction_match.py — the
matched/unmatched transaction-level comparison, adapted from
vehicle-verification's reconciliation.py. Plain asserts, same style as
test_reconcile.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase2_verification_engine.transaction_match import (  # noqa: E402
    build_match_report,
    match_transactions,
    normalize_value,
    to_exceptions,
)


def test_normalize_value_default_lowercases_and_strips():
    assert normalize_value("  Loan-123  ") == "loan-123"


def test_normalize_value_ignore_spaces_and_special_chars():
    assert normalize_value("Loan #123 (Q1)", ignore_spaces=True, ignore_special_chars=True) == "loan123q1"


def test_exact_match_pairs_identical_descriptions():
    reported = [{"key": "tape:1", "description": "collections:LN100", "amount": 500.0}]
    independent = [{"key": "bank:1", "description": "collections:LN100", "amount": 500.0}]
    result = match_transactions(reported, independent)
    assert len(result["matched"]) == 1
    assert result["matched"][0]["match_type"] == "exact"
    assert result["unmatched_reported"] == []
    assert result["unmatched_independent"] == []


def test_partial_match_when_independent_narration_contains_reported_text():
    reported = [{"key": "tape:1", "description": "collections:LN100", "amount": 500.0}]
    independent = [{"key": "bank:1", "description": "mpesa payment ref collections:LN100 confirmed", "amount": 500.0}]
    result = match_transactions(reported, independent)
    assert len(result["matched"]) == 1
    assert result["matched"][0]["match_type"] == "partial"


def test_unmatched_on_both_sides_when_nothing_overlaps():
    reported = [{"key": "tape:1", "description": "collections:LN100", "amount": 500.0}]
    independent = [{"key": "bank:1", "description": "grocery store purchase", "amount": 20.0}]
    result = match_transactions(reported, independent)
    assert result["matched"] == []
    assert len(result["unmatched_reported"]) == 1
    assert len(result["unmatched_independent"]) == 1


def test_matching_is_one_to_one_not_one_to_many():
    # Two reported records with the same description must not both claim the
    # single independent record that matches it.
    reported = [
        {"key": "tape:1", "description": "collections:LN100", "amount": 500.0},
        {"key": "tape:2", "description": "collections:LN100", "amount": 500.0},
    ]
    independent = [{"key": "bank:1", "description": "collections:LN100", "amount": 500.0}]
    result = match_transactions(reported, independent)
    assert len(result["matched"]) == 1
    assert len(result["unmatched_reported"]) == 1


def test_empty_description_counts_as_unmatched_reported():
    reported = [{"key": "tape:1", "description": "", "amount": 500.0}]
    result = match_transactions(reported, [])
    assert result["unmatched_reported"] == reported


def test_to_exceptions_covers_both_unmatched_sides():
    result = {
        "matched": [],
        "unmatched_reported": [{"key": "tape:1", "description": "collections:LN100"}],
        "unmatched_independent": [{"key": "bank:1", "description": "unrelated"}],
    }
    exceptions = to_exceptions(result, run_id="r1")
    assert len(exceptions) == 2
    assert all(e.kind == "bank_match" for e in exceptions)
    assert any(e.id == "r1:match:in_reported_only:tape:1" for e in exceptions)
    assert any(e.id == "r1:match:in_independent_only:bank:1" for e in exceptions)


def test_build_match_report_has_one_row_per_record():
    result = match_transactions(
        [{"key": "tape:1", "description": "collections:LN100", "amount": 500.0}],
        [{"key": "bank:1", "description": "unrelated", "amount": 20.0}],
    )
    df = build_match_report(result)
    assert len(df) == 2
    assert set(df["Status"]) == {"In reported only", "In independent only"}


if __name__ == "__main__":
    test_normalize_value_default_lowercases_and_strips()
    test_normalize_value_ignore_spaces_and_special_chars()
    test_exact_match_pairs_identical_descriptions()
    test_partial_match_when_independent_narration_contains_reported_text()
    test_unmatched_on_both_sides_when_nothing_overlaps()
    test_matching_is_one_to_one_not_one_to_many()
    test_empty_description_counts_as_unmatched_reported()
    test_to_exceptions_covers_both_unmatched_sides()
    test_build_match_report_has_one_row_per_record()
    print("transaction_match tests OK")
