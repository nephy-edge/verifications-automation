"""Tests for Phase 1 ingest.py — real per-loan tape parsing, plain asserts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from phase1_ingestion_parsing.ingest import DIR_IN, DIR_OUT, normalize_loan_tape_row, read_raw_table  # noqa: E402


def test_read_raw_table_keeps_the_files_own_column_names(tmp_path):
    """The Transaction Matching tab's column picker needs a file's *real*
    columns (e.g. "Transaction Code"), not the canonical schema
    `load_and_normalize` maps everything into — that schema doesn't have a
    slot for a transaction-code column at all, it would just be dropped."""
    path = tmp_path / "ledger.xlsx"
    # A title row above the real header, same shape as a real bank export
    # that broke the Transaction Matching tab before the column-picker fix.
    pd.DataFrame(
        [
            ["Some Bank - Customer Ledger", None, None],
            ["Date", "Transaction Code", "Amount"],
            ["2026-06-02", "TXN-001", 100.0],
        ]
    ).to_excel(path, index=False, header=False)

    df = read_raw_table(path)
    assert list(df.columns) == ["Date", "Transaction Code", "Amount"]
    assert df.iloc[0]["Transaction Code"] == "TXN-001"


def test_paid_loan_emits_disbursement_and_collections():
    row = {
        "loan_id": "L1",
        "begin_date": "2024-01-01",
        "closure_date": "2024-02-01",
        "company_due_date": "2024-02-05",
        "principal_amount": 1000.0,
        "total_loan_amount": 1090.0,
        "principal_outstanding": 0.0,
        "interest_outstanding": 0.0,
        "fees_outstanding": 0.0,
        "penalties_outstanding": 0.0,
    }
    events = normalize_loan_tape_row(row, account_ref="tape")
    kinds = {e["description"]: e for e in events}
    assert "disbursement:L1" in kinds and kinds["disbursement:L1"]["amount"] == 1000.0
    assert kinds["disbursement:L1"]["direction"] == DIR_OUT
    assert kinds["disbursement:L1"]["value_date"] == "2024-01-01"
    assert "collections:L1" in kinds and kinds["collections:L1"]["amount"] == 1090.0
    assert kinds["collections:L1"]["direction"] == DIR_IN
    assert kinds["collections:L1"]["value_date"] == "2024-02-01"  # closure_date preferred


def test_defaulted_loan_with_nothing_collected_emits_no_collections_row():
    row = {
        "loan_id": "L2",
        "begin_date": "2021-02-24",
        "closure_date": "",
        "company_due_date": "2022-02-10",
        "principal_amount": 1000.0,
        "total_loan_amount": 1090.0,
        "principal_outstanding": 1000.0,
        "interest_outstanding": 0.0,
        "fees_outstanding": 90.0,
        "penalties_outstanding": 1090.0,
    }
    events = normalize_loan_tape_row(row, account_ref="tape")
    descriptions = [e["description"] for e in events]
    assert "disbursement:L2" in descriptions
    assert "collections:L2" not in descriptions


def test_active_loan_emits_partial_collections_dated_at_due_date():
    row = {
        "loan_id": "L3",
        "begin_date": "2026-06-20",
        "closure_date": "",
        "company_due_date": "2026-08-27",
        "principal_amount": 4206.9,
        "total_loan_amount": 4632.76,
        "principal_outstanding": 1051.71,
        "interest_outstanding": 0.0,
        "fees_outstanding": 0.0,
        "penalties_outstanding": 0.0,
    }
    events = normalize_loan_tape_row(row, account_ref="tape")
    coll = next(e for e in events if e["description"] == "collections:L3")
    assert round(coll["amount"], 2) == 3581.05
    assert coll["value_date"] == "2026-08-27"  # falls back to company_due_date


if __name__ == "__main__":
    test_paid_loan_emits_disbursement_and_collections()
    test_defaulted_loan_with_nothing_collected_emits_no_collections_row()
    test_active_loan_emits_partial_collections_dated_at_due_date()
    print("ingest tests OK")
