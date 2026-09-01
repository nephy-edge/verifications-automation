"""Tests for phase2_verification_engine/transaction_match.py — the
matched/unmatched transaction-level comparison, adapted from
vehicle-verification's reconciliation.py. Plain asserts, same style as
test_reconcile.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase2_verification_engine.transaction_match import (  # noqa: E402
    build_generic_match_report,
    build_match_report,
    match_transactions,
    normalize_value,
    parse_amount,
    sum_amount_column,
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


def test_match_transactions_matches_on_an_arbitrary_chosen_field():
    # The Transaction Matching tab's column picker: match on a statement's
    # own reference code rather than the fixed "description" field.
    reported = [{"Transaction Code": "TXN-001", "Amount": 100.0}]
    independent = [{"TRANSACTION CODE": "TXN-001", "Amount": 100.0}]
    result = match_transactions(
        reported, independent, reported_field="Transaction Code", independent_field="TRANSACTION CODE"
    )
    assert len(result["matched"]) == 1
    assert result["matched"][0]["match_type"] == "exact"


def test_build_generic_match_report_prefixes_columns_by_side():
    result = match_transactions(
        [{"Transaction Code": "TXN-001", "Amount": 100.0}],
        [{"TRANSACTION CODE": "TXN-001", "Amount": 100.0}],
        reported_field="Transaction Code",
        independent_field="TRANSACTION CODE",
    )
    df = build_generic_match_report(result)
    assert len(df) == 1
    assert df.iloc[0]["Status"] == "Matched"
    assert df.iloc[0]["Reported: Transaction Code"] == "TXN-001"
    assert df.iloc[0]["Independent: TRANSACTION CODE"] == "TXN-001"


def test_build_generic_match_report_keeps_every_column_not_just_description_and_amount():
    result = {
        "matched": [],
        "unmatched_reported": [{"Transaction Code": "TXN-002", "Category": "Groceries"}],
        "unmatched_independent": [],
    }
    df = build_generic_match_report(result)
    assert list(df.columns) == ["Status", "Match key", "Reported: Transaction Code", "Reported: Category"]


def test_parse_amount_handles_currency_symbols_and_commas():
    assert parse_amount("$1,234.56") == 1234.56


def test_parse_amount_treats_parentheses_as_negative():
    assert parse_amount("(200.00)") == -200.00


def test_parse_amount_passes_through_native_numbers():
    assert parse_amount(500) == 500.0
    assert parse_amount(500.5) == 500.5


def test_parse_amount_returns_none_for_blank_or_unparseable():
    assert parse_amount("") is None
    assert parse_amount(None) is None
    assert parse_amount("N/A") is None


def test_parse_amount_excludes_nan():
    assert parse_amount(float("nan")) is None


def test_sum_amount_column_totals_and_counts_skips():
    records = [{"Amount": "$100.00"}, {"Amount": "N/A"}, {"Amount": "$50.00"}]
    total, parsed, skipped = sum_amount_column(records, "Amount")
    assert total == 150.00
    assert parsed == 2
    assert skipped == 1


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
    test_match_transactions_matches_on_an_arbitrary_chosen_field()
    test_build_generic_match_report_prefixes_columns_by_side()
    test_build_generic_match_report_keeps_every_column_not_just_description_and_amount()
    print("transaction_match tests OK")
