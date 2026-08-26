"""Tests for Phase 1 extract.py — statement text parsing, plain asserts.

Uses synthetic text (not a real PDF) so these run without pdfplumber/a file
fixture; `extract_pdf()` itself just wraps `extract_pdf_text` + this.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase1_ingestion_parsing.extract import (  # noqa: E402
    assess_confidence,
    extract_pdf,
    extract_pdf_balance,
    extract_statement_balance,
    parse_statement_text,
)
from phase1_ingestion_parsing.ingest import DIR_IN, DIR_OUT  # noqa: E402


def test_two_date_column_layout_with_tax_and_balance():
    # date_op date_val description... amount tax balance (BBVA Peru style).
    text = "\n".join(
        [
            "FECHA FECHA SALDO",
            "SALDO ANTERIOR 5,292,750.25",
            "01-06 01-06 TIN002F000000000000 KUSHKI PERU SRL 1177 183,883.73 9.15 5,476,624.83",
            "30-06 30-06 COMISION DE MANTENIMIENTO 1236 -40.00 4,610,508.77",
            "OFICINA FECHA CODIGO DE CUENTA HOJA",
            "OF.CAMINO REAL 30-06-2026 ENTIDAD OFICINA CUENTA D.C. 6",
        ]
    )
    rows = parse_statement_text(text, account_ref="bbva")
    assert len(rows) == 2  # the balance-only and footer lines must not be parsed as rows
    first, second = rows
    assert first["value_date"] == "01-06-2026"  # year recovered from the footer
    assert first["amount"] == 183883.73
    assert first["direction"] == DIR_IN
    assert "KUSHKI PERU SRL" in first["description"]
    assert second["amount"] == 40.00
    assert second["direction"] == DIR_OUT


def test_embedded_dotted_date_in_narration_is_not_mistaken_for_an_amount():
    # "03.06.26" inside the narration must not be parsed as the number 03.06.
    text = "03-06 03-06 OP FX PF LEASY II 03.06.26 BCA. INTERNET BIE 1181 -5,430,000.00 271.50 252,794.86"
    rows = parse_statement_text(text, account_ref="bbva")
    assert len(rows) == 1
    assert rows[0]["amount"] == 5430000.00
    assert rows[0]["direction"] == DIR_OUT
    assert "03.06.26" in rows[0]["description"]  # stays in the description, not parsed as a number


def test_single_date_column_layout_still_works():
    text = "12/03/2024 SALARY PAYMENT 1,500.00"
    rows = parse_statement_text(text, account_ref="legacy")
    assert len(rows) == 1
    assert rows[0]["value_date"] == "12/03/2024"
    assert rows[0]["amount"] == 1500.00
    assert rows[0]["direction"] == DIR_IN


def test_line_with_only_a_balance_is_not_parsed_as_a_transaction():
    text = "01-06 01-06 SALDO ANTERIOR 5,292,750.25"
    rows = parse_statement_text(text, account_ref="bbva")
    assert rows == []


def test_closing_balance_is_the_balance_after_the_last_transaction():
    text = "\n".join(
        [
            "01-06 01-06 TIN002F000000000000 KUSHKI PERU SRL 1177 183,883.73 9.15 5,476,624.83",
            "30-06 30-06 COMISION DE MANTENIMIENTO 1236 -40.00 4,610,508.77",
        ]
    )
    assert extract_statement_balance(text) == 4610508.77


def test_closing_balance_is_none_for_a_layout_without_a_balance_column():
    text = "12/03/2024 SALARY PAYMENT 1,500.00"
    assert extract_statement_balance(text) is None


def test_iso_date_running_balance_layout_infers_direction_from_starting_balance():
    # No debit/credit sign on the line itself - direction must come from the
    # balance delta, anchored by the declared STARTING BALANCE summary.
    text = "\n".join(
        [
            "STARTING BALANCE TOTAL DEPOSITS (+) TOTAL WITHDRAWALS (-) ENDING BALANCE",
            "$5,432.10 $12,466.98 $889.93 $17,009.15",
            "2026-06-02 TXN-20260602-9E96E5 Direct Deposit / Payroll Salary $3,019.09 $8,451.19",
            "2026-06-03 TXN-20260603-A3D0A6 Shell Fuel Transportation $21.05 $8,430.14",
        ]
    )
    rows = parse_statement_text(text, account_ref="apex")
    assert len(rows) == 2
    assert rows[0]["value_date"] == "2026-06-02"
    assert rows[0]["amount"] == 3019.09
    assert rows[0]["direction"] == DIR_IN
    assert "TXN-" not in rows[0]["description"]
    assert not rows[0]["description"].endswith("$")
    assert rows[1]["amount"] == 21.05
    assert rows[1]["direction"] == DIR_OUT
    assert extract_statement_balance(text) == 8430.14


def test_iso_date_running_balance_layout_without_summary_guesses_first_row_direction():
    text = "2026-06-02 TXN-1 Direct Deposit Payroll $3,019.09 $8,451.19"
    rows = parse_statement_text(text, account_ref="apex")
    assert len(rows) == 1
    assert rows[0]["direction"] == DIR_IN  # "Deposit"/"Payroll" keyword fallback


def test_scanned_pdf_produces_a_flaggable_placeholder_row():
    with patch("phase1_ingestion_parsing.extract.extract_pdf_text", return_value=""):
        rows = extract_pdf("ignored.pdf", account_ref="acct")
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.0
    assess_confidence(rows, floor=0.85)
    assert rows[0]["needs_spot_check"] is True


def test_scanned_pdf_with_ocr_available_parses_via_existing_layouts(tmp_path):
    # OCR only changes how the text is obtained — a scanned copy of a known
    # layout should still go through the same rules-based parser, not a
    # separate one.
    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")
    with patch("phase1_ingestion_parsing.extract.extract_pdf_text", return_value=""), patch(
        "phase1_ingestion_parsing.extract.ocr_extract_text",
        return_value="12/03/2024 SALARY PAYMENT 1,500.00",
    ):
        rows = extract_pdf(fake_pdf, account_ref="acct")
    assert len(rows) == 1
    assert rows[0]["ocr"] is True
    assert rows[0]["amount"] == 1500.00
    # OCR carries its own error class beyond layout tie-out — never fully
    # trustworthy, always below the B3 confidence floor.
    assert rows[0]["confidence"] <= 0.6


def test_ocr_failure_falls_back_to_placeholder_with_updated_reason(tmp_path):
    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")
    with patch("phase1_ingestion_parsing.extract.extract_pdf_text", return_value=""), patch(
        "phase1_ingestion_parsing.extract.ocr_extract_text", return_value=""
    ):
        rows = extract_pdf(fake_pdf, account_ref="acct")
    assert rows[0]["confidence"] == 0.0
    assert "OCR" in rows[0]["description"]


def test_unmapped_text_layout_produces_a_flaggable_placeholder_row():
    # Real text, but nothing in it matches either known statement layout.
    unmapped_text = "Some Other Bank\nStatement for account 12345\nNo recognizable rows here."
    with patch("phase1_ingestion_parsing.extract.extract_pdf_text", return_value=unmapped_text):
        rows = extract_pdf("ignored.pdf", account_ref="acct")
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.0
    assert "spot-check" in rows[0]["description"]


def test_recognized_layout_rows_do_not_need_spot_check():
    with patch(
        "phase1_ingestion_parsing.extract.extract_pdf_text",
        return_value="12/03/2024 SALARY PAYMENT 1,500.00",
    ):
        rows = extract_pdf("ignored.pdf", account_ref="acct")
    assess_confidence(rows, floor=0.85)
    assert rows[0]["needs_spot_check"] is False


# ---------------------------------------------------------------- layout 4 (positional table)

def test_month_name_date_parsed_to_iso():
    from phase1_ingestion_parsing.extract import _parse_month_name_date

    assert _parse_month_name_date("June 23rd 2022") == "2022-06-23"
    assert _parse_month_name_date("Mar 4, 2026") == "2026-03-04"
    assert _parse_month_name_date("no date here") == ""


def test_detect_shape_on_mono_absa_text():
    from phase1_ingestion_parsing.extract import detect_shape

    text = "\n".join(
        [
            "Contact Account no. Bank",
            "0131883461 Absa Bank",
            "SAVINGS_ACCOUNT / KES Aug 12, 2025 to Aug 12, 2026",
            "Available balance Total debits Total credits",
            "KES 100,000.00 KES 4,015.00 KES 4,672,790.16",
            "# DATE NARRATION DEBIT CREDIT BALANCE",
        ]
    )
    shape = detect_shape(text)
    assert shape["layout"] == "numbered_table"
    assert shape["bank"] == "absa"
    assert shape["account_no"] == "0131883461"
    assert shape["currency"] == "KES"
    assert shape["total_debits"] == 4015.00
    assert shape["total_credits"] == 4672790.16
    assert shape["available_balance"] == 100000.00


def test_detect_shape_layouts():
    from phase1_ingestion_parsing.extract import detect_shape

    assert detect_shape("SALDO ANTERIOR \n01-06 01-06 FOO 1.00 2.00")["layout"] == "two_date"
    assert detect_shape("12/03/2024 SALARY PAYMENT 1,500.00")["layout"] == "single_date"
    iso = "\n".join(
        [
            "STARTING BALANCE TOTAL DEPOSITS (+) TOTAL WITHDRAWALS (-) ENDING BALANCE",
            "$5,432.10 $12,466.98 $889.93 $17,009.15",
            "2026-06-02 TXN-1 Description $3,019.09 $8,451.19",
        ]
    )
    assert detect_shape(iso)["layout"] == "iso_running"


def test_tie_out_pass_and_fail():
    from phase1_ingestion_parsing.extract import _tie_out

    rows = [
        {"amount": 100.0, "direction": DIR_IN},
        {"amount": 20.0, "direction": DIR_OUT},
    ]
    assert _tie_out(rows, {"total_debits": 20.0, "total_credits": 100.0}) is True
    assert _tie_out(rows, {"total_debits": 999.0, "total_credits": 100.0}) is False
    assert (
        _tie_out(
            rows,
            {
                "total_debits": None,
                "total_credits": None,
                "opening_balance": 50.0,
                "closing_balance": 130.0,
            },
        )
        is True
    )
    assert _tie_out([], {"total_debits": None, "total_credits": None}) is None


def test_failed_tie_out_drops_confidence_below_floor():
    from phase1_ingestion_parsing.extract import _apply_layout_confidence

    rows = [
        {"amount": 100.0, "direction": DIR_IN, "description": "x", "key": "k1"},
        {"amount": 20.0, "direction": DIR_OUT, "description": "y", "key": "k2"},
    ]
    shape = {"layout": "numbered_table", "total_debits": 999.0, "total_credits": 100.0}
    _apply_layout_confidence(rows, "# DATE NARRATION DEBIT CREDIT BALANCE", shape)
    assert all(r["confidence"] == 0.5 for r in rows)
    assess_confidence(rows, floor=0.85)
    assert all(r["needs_spot_check"] for r in rows)


def test_numbered_table_full_parse_of_real_absa_sample():
    """End-to-end on the repo's real Mono/GTBank-style Absa statement.

    The flattened text layer can't resolve this layout (month-name dates,
    multi-line narrations, unsigned amounts); the positional word-coordinate
    parser must recover all 15 transactions with the right direction, tie the
    parsed debits/credits to the statement's own declared totals, and produce
    a closing balance for the cash check.
    """
    import pdfplumber

    path = Path(__file__).resolve().parent.parent / "samples" / "statement_absa.pdf"
    if pdfplumber is None or not path.exists():
        return
    rows = extract_pdf(path, account_ref="absa")
    assert len(rows) == 15
    assert all(r["direction"] in (DIR_IN, DIR_OUT) for r in rows)
    assert all(r.get("tie_out") == "ok" for r in rows)
    assert all(r["confidence"] == 1.0 for r in rows)
    assert rows[0]["value_date"] == "2022-06-23"
    ins = sum(r["amount"] for r in rows if r["direction"] == DIR_IN)
    outs = sum(r["amount"] for r in rows if r["direction"] == DIR_OUT)
    assert round(ins, 2) == 4672790.16  # matches declared "Total credits"
    assert round(outs, 2) == 4015.00  # matches declared "Total debits"
    assert extract_pdf_balance(path) == 2003.94


def test_numbered_table_does_not_parse_monthly_summary_or_totals_lines():
    """The page-1 'DATE TOTAL DEBITS TOTAL CREDITS BALANCE' monthly summary and
    the 'Available balance ... Total credits' totals block must not be mistaken
    for transactions — there is no row number in the # column for those."""
    import pdfplumber

    path = Path(__file__).resolve().parent.parent / "samples" / "statement_absa.pdf"
    if pdfplumber is None or not path.exists():
        return
    rows = extract_pdf(path, account_ref="absa")
    assert not any("TOTAL DEBITS" in r["description"] for r in rows)


if __name__ == "__main__":
    test_two_date_column_layout_with_tax_and_balance()
    test_embedded_dotted_date_in_narration_is_not_mistaken_for_an_amount()
    test_single_date_column_layout_still_works()
    test_line_with_only_a_balance_is_not_parsed_as_a_transaction()
    test_closing_balance_is_the_balance_after_the_last_transaction()
    test_closing_balance_is_none_for_a_layout_without_a_balance_column()
    test_iso_date_running_balance_layout_infers_direction_from_starting_balance()
    test_iso_date_running_balance_layout_without_summary_guesses_first_row_direction()
    test_scanned_pdf_produces_a_flaggable_placeholder_row()
    with tempfile.TemporaryDirectory() as d:
        test_scanned_pdf_with_ocr_available_parses_via_existing_layouts(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_ocr_failure_falls_back_to_placeholder_with_updated_reason(Path(d))
    test_unmapped_text_layout_produces_a_flaggable_placeholder_row()
    test_recognized_layout_rows_do_not_need_spot_check()
    test_month_name_date_parsed_to_iso()
    test_detect_shape_on_mono_absa_text()
    test_detect_shape_layouts()
    test_tie_out_pass_and_fail()
    test_failed_tie_out_drops_confidence_below_floor()
    test_numbered_table_full_parse_of_real_absa_sample()
    test_numbered_table_does_not_parse_monthly_summary_or_totals_lines()
    print("extract tests OK")
