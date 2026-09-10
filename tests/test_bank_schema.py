"""Tests for Phase 1 ingest.py — CSV/Excel bank/mobile statement parsing.

Covers case-insensitive column detection, debit/credit-split and
single-signed-amount shapes, balance extraction, and the Excel auto-header
detection (title/spacer rows above the real header).
"""

import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase1_ingestion_parsing.ingest import (  # noqa: E402
    DIR_IN,
    DIR_OUT,
    detect_bank_schema,
    extract_tabular_balance,
    load_and_normalize,
    normalize_bank_row,
)


def test_detect_schema_case_insensitive_debit_credit():
    cols = ["Date", "Transaction Code", "Description", "Category", "Debit (-)", "Credit (+)", "Running Balance"]
    schema = detect_bank_schema(cols)
    assert schema["date"] == "Date"
    assert schema["debit"] == "Debit (-)"
    assert schema["credit"] == "Credit (+)"
    assert schema["balance"] == "Running Balance"
    assert schema["amount"] is None  # no single amount column in this shape


def test_normalize_bank_row_prefers_credit_when_present():
    cols = ["Date", "Debit (-)", "Credit (+)"]
    schema = detect_bank_schema(cols)
    row = {"Date": "2026-06-02", "Debit (-)": None, "Credit (+)": 3019.09}
    n = normalize_bank_row(row, schema, account_ref="acct", index=0)
    assert n["amount"] == 3019.09
    assert n["direction"] == DIR_IN


def test_normalize_bank_row_debit_side():
    cols = ["Date", "Debit (-)", "Credit (+)"]
    schema = detect_bank_schema(cols)
    row = {"Date": "2026-06-03", "Debit (-)": 21.05, "Credit (+)": None}
    n = normalize_bank_row(row, schema, account_ref="acct", index=1)
    assert n["amount"] == 21.05
    assert n["direction"] == DIR_OUT


def test_normalize_bank_row_zero_both_sides_dropped():
    cols = ["Date", "Debit (-)", "Credit (+)"]
    schema = detect_bank_schema(cols)
    row = {"Date": "2026-06-03", "Debit (-)": 0, "Credit (+)": 0}
    assert normalize_bank_row(row, schema, account_ref="acct", index=0) == {}


def test_normalize_bank_row_single_signed_amount_column():
    cols = ["date", "amount", "narration"]
    schema = detect_bank_schema(cols)
    row = {"date": "2024-03-12", "amount": -55.0, "narration": "AIRTIME"}
    n = normalize_bank_row(row, schema, account_ref="acct", index=0)
    assert n["amount"] == 55.0
    assert n["direction"] == DIR_OUT


def test_missing_date_row_dropped():
    cols = ["Date", "Debit (-)", "Credit (+)"]
    schema = detect_bank_schema(cols)
    row = {"Date": None, "Debit (-)": 10, "Credit (+)": None}
    assert normalize_bank_row(row, schema, account_ref="acct", index=0) == {}


def _write_xlsx_with_title_rows(path: Path, header: list[str], data_rows: list[list]) -> None:
    """Build a small .xlsx with two blank/title rows above the real header,
    matching the shape real exports often have (bank name/title, spacer)."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Some Bank - Statement"])
    ws.append([])
    ws.append(header)
    for row in data_rows:
        ws.append(row)
    wb.save(path)


def test_excel_auto_header_and_balance_extraction_with_title_rows():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "statement.xlsx"
        _write_xlsx_with_title_rows(
            path,
            header=["Date", "Description", "Debit (-)", "Credit (+)", "Running Balance"],
            data_rows=[
                ["2026-01-01", "Deposit", None, 100.0, 1100.0],
                ["2026-01-02", "Withdrawal", 40.0, None, 1060.0],
            ],
        )
        rows = load_and_normalize([path], sheet="bank")
        assert len(rows) == 2
        assert rows[0]["amount"] == 100.0 and rows[0]["direction"] == DIR_IN
        assert rows[1]["amount"] == 40.0 and rows[1]["direction"] == DIR_OUT

        balance = extract_tabular_balance(path)
        assert balance == 1060.0


if __name__ == "__main__":
    test_detect_schema_case_insensitive_debit_credit()
    test_normalize_bank_row_prefers_credit_when_present()
    test_normalize_bank_row_debit_side()
    test_normalize_bank_row_zero_both_sides_dropped()
    test_normalize_bank_row_single_signed_amount_column()
    test_missing_date_row_dropped()
    test_excel_auto_header_and_balance_extraction_with_title_rows()
    print("bank schema tests OK")
