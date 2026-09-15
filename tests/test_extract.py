"""Tests for Phase 1 extract.py — statement text parsing, plain asserts.

Uses synthetic text (not a real PDF) so these run without pdfplumber/a file
fixture; `extract_pdf()` itself just wraps `extract_pdf_text` + this.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase1_ingestion_parsing.extract import (  # noqa: E402
    _stamp_currency,
    assess_confidence,
    detect_shape,
    extract_pdf,
    extract_pdf_balance,
    extract_pdf_table_rows,
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


def test_numbered_table_layout_tried_via_searchable_pdf_before_flat_text_ocr(tmp_path):
    """`extract_pdf` should recover the numbered-table layout from a scan by
    re-OCRing into a searchable PDF first — the flat-text OCR path
    (`ocr_extract_text`) can never resolve this layout (it needs word
    x-positions), so it must not even be reached when the searchable-PDF
    path already found real rows."""
    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")
    numbered_rows = [{"description": "collections:L1", "amount": 500.0, "direction": "in", "value_date": "2024-01-01"}]
    with patch("phase1_ingestion_parsing.extract.extract_pdf_text", return_value=""), patch(
        "phase1_ingestion_parsing.extract.ocr_to_searchable_pdf", return_value=b"%PDF-searchable"
    ), patch("phase1_ingestion_parsing.extract._scan_numbered_table", return_value=numbered_rows), patch(
        "phase1_ingestion_parsing.extract.detect_shape", return_value={}
    ), patch("phase1_ingestion_parsing.extract.ocr_extract_text") as mock_flat_ocr:
        rows = extract_pdf(fake_pdf, account_ref="acct")
    mock_flat_ocr.assert_not_called()
    assert len(rows) == 1
    assert rows[0]["ocr"] is True
    assert rows[0]["confidence"] <= 0.6
    assert rows[0]["amount"] == 500.0


def test_numbered_table_layout_not_found_falls_back_to_flat_text_ocr(tmp_path):
    """When the searchable-PDF path can't identify the numbered-table header
    (a different, flat-text layout), `extract_pdf` must still fall back to
    the existing flat-text OCR path rather than giving up."""
    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")
    with patch("phase1_ingestion_parsing.extract.extract_pdf_text", return_value=""), patch(
        "phase1_ingestion_parsing.extract.ocr_to_searchable_pdf", return_value=b"%PDF-searchable"
    ), patch("phase1_ingestion_parsing.extract._scan_numbered_table", return_value=None), patch(
        "phase1_ingestion_parsing.extract.ocr_extract_text",
        return_value="12/03/2024 SALARY PAYMENT 1,500.00",
    ):
        rows = extract_pdf(fake_pdf, account_ref="acct")
    assert len(rows) == 1
    assert rows[0]["amount"] == 1500.00


def test_numbered_table_layout_via_ocr_on_a_real_synthetic_scan():
    """End-to-end regression test for the fix landed 2026-09-01: OCR-ing the
    repo's real Absa statement directly into flat text recovers 0 of its 15
    numbered-table rows (proven earlier the same day), because that layout
    needs each word's x-position to map tokens to columns and a flat string
    doesn't carry it. Rendering the real PDF's pages to images (stripping
    the text layer, to genuinely simulate a scan) and re-OCRing into a
    *searchable* PDF instead should recover at least one real, correctly
    parsed row — proof the coordinate mapper runs successfully against OCR
    output, not just against a native text layer."""
    from phase1_ingestion_parsing.ocr import tesseract_available

    if not tesseract_available():
        return
    import pypdfium2 as pdfium

    src = Path(__file__).resolve().parent.parent / "samples" / "statement_absa.pdf"
    if not src.exists():
        return

    pdf = pdfium.PdfDocument(str(src))
    try:
        images = [pdf[i].render(scale=2).to_pil().convert("RGB") for i in range(len(pdf))]
    finally:
        pdf.close()

    with tempfile.TemporaryDirectory() as d:
        scan_path = Path(d) / "statement_absa_scan.pdf"
        images[0].save(scan_path, save_all=True, append_images=images[1:])

        from phase1_ingestion_parsing.extract import extract_pdf_text

        assert extract_pdf_text(scan_path).strip() == ""  # confirms this is a genuine scan, no text layer

        rows = extract_pdf(scan_path, account_ref="absa_scan")

    assert rows, "expected the searchable-PDF path to recover at least one real transaction"
    assert all(r.get("ocr") is True for r in rows)
    # A specific known-correct row from the real statement (verified against
    # the native-text-layer parse of the same file): date, amount, direction,
    # and description all match — the OCR path recovers real data, not noise.
    match = next((r for r in rows if r.get("value_date") == "2022-03-21" and r.get("amount") == 3.75), None)
    assert match is not None
    assert match["direction"] == "out"
    assert "GTWORLD" in match["description"]


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


def test_detect_shape_maps_spanish_currency_labels():
    """Peruvian/Mexican statements label the currency in Spanish ("MONEDA:
    SOLES" = PEN, "DOLARES" = USD); detect_shape must map these to the ISO
    codes so FX normalization converts the amounts rather than summing them as
    if already in the base currency (real BBVA Peru statements use SOLES)."""
    peru = detect_shape("CUENTA CORRIENTE\nMONEDA: SOLES\n01-06 01-06 FOO 1.00 2.00")
    assert peru["layout"] == "two_date"
    assert peru["currency"] == "PEN"
    usd = detect_shape("MONEDA: DOLARES\n01-06 01-06 FOO 1.00 2.00")
    assert usd["currency"] == "USD"
    # ISO codes still pass through unchanged.
    assert detect_shape("SAVINGS / KES Aug 2025")["currency"] == "KES"


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


def test_stamp_currency_fills_blank_row_currency_from_detected_shape():
    """The per-line parsers never populate a row's own `currency` — only the
    statement-level regex (`detect_shape`) does, from header/metadata text
    that appears once per file, not per transaction line. FX normalization
    (phase2/calculate.py) reads the row's `currency`, so it must be
    propagated down or every PDF-sourced row would silently look base-
    currency regardless of what the statement actually says."""
    rows = [{"amount": 100.0, "currency": ""}, {"amount": 50.0, "currency": ""}]
    _stamp_currency(rows, {"currency": "KES"})
    assert all(r["currency"] == "KES" for r in rows)


def test_stamp_currency_does_not_overwrite_an_already_set_row_currency():
    rows = [{"amount": 100.0, "currency": "USD"}]
    _stamp_currency(rows, {"currency": "KES"})
    assert rows[0]["currency"] == "USD"


def test_stamp_currency_no_op_when_shape_has_no_currency():
    rows = [{"amount": 100.0, "currency": ""}]
    _stamp_currency(rows, {})
    assert rows[0]["currency"] == ""


def test_numbered_table_full_parse_of_real_absa_sample_stamps_kes_currency():
    """Same real-sample guard as the tie-out test above: the Absa statement's
    own header text names KES, and every extracted row should carry it now
    that `extract_pdf` stamps the statement-level currency onto each row."""
    import pdfplumber

    path = Path(__file__).resolve().parent.parent / "samples" / "statement_absa.pdf"
    if pdfplumber is None or not path.exists():
        return
    rows = extract_pdf(path, account_ref="absa")
    assert rows
    assert all(r.get("currency") == "KES" for r in rows)


def test_extract_pdf_table_rows_skips_a_summary_table_with_no_data_rows(tmp_path):
    """Regression test for a real bug found 2026-09-01: a one-row account-
    summary table (e.g. "Starting Balance $100 | Ending Balance $200") can
    loosely satisfy the header keyword check too (it has "balance" in it),
    and if picked first, every real transaction row that follows gets
    force-mapped onto its wrong, unrelated columns. A candidate header must
    have at least one data row beneath it before it's accepted."""
    fake_pdf = tmp_path / "statement.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")

    summary_table = [["Starting Balance $100.00", "Ending Balance $200.00"]]  # 1 row, no data -> must be skipped
    real_table = [
        ["DATE", "TRANSACTION CODE", "DESCRIPTION", "AMOUNT"],
        ["2026-06-02", "TXN-001", "Coffee", "$5.00"],
        ["2026-06-03", "TXN-002", "Groceries", "$40.00"],
    ]

    fake_page = MagicMock()
    fake_page.extract_tables.return_value = [summary_table, real_table]
    fake_pdf_obj = MagicMock()
    fake_pdf_obj.pages = [fake_page]
    fake_pdf_obj.__enter__.return_value = fake_pdf_obj
    fake_pdf_obj.__exit__.return_value = False

    with patch("phase1_ingestion_parsing.extract.pdfplumber.open", return_value=fake_pdf_obj):
        rows, columns = extract_pdf_table_rows(fake_pdf, account_ref="acct")

    assert columns == ["DATE", "TRANSACTION CODE", "DESCRIPTION", "AMOUNT"]
    assert len(rows) == 2
    assert rows[0]["TRANSACTION CODE"] == "TXN-001"
    assert rows[0]["DATE"] == "2026-06-02"


def test_extract_pdf_table_rows_drops_a_repeated_header_on_a_later_page(tmp_path):
    fake_pdf = tmp_path / "statement.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")

    page1_table = [
        ["DATE", "DESCRIPTION", "AMOUNT"],
        ["2026-06-02", "Coffee", "$5.00"],
    ]
    page2_table = [
        ["DATE", "DESCRIPTION", "AMOUNT"],  # repeated header, not data
        ["2026-06-03", "Groceries", "$40.00"],
    ]
    page1 = MagicMock()
    page1.extract_tables.return_value = [page1_table]
    page2 = MagicMock()
    page2.extract_tables.return_value = [page2_table]
    fake_pdf_obj = MagicMock()
    fake_pdf_obj.pages = [page1, page2]
    fake_pdf_obj.__enter__.return_value = fake_pdf_obj
    fake_pdf_obj.__exit__.return_value = False

    with patch("phase1_ingestion_parsing.extract.pdfplumber.open", return_value=fake_pdf_obj):
        rows, columns = extract_pdf_table_rows(fake_pdf, account_ref="acct")

    assert len(rows) == 2
    assert [r["DESCRIPTION"] for r in rows] == ["Coffee", "Groceries"]


def test_extract_pdf_table_rows_falls_back_to_canonical_when_no_real_table_found():
    canonical_rows = [
        {"value_date": "2026-06-02", "description": "Coffee", "amount": 5.0, "direction": "out", "currency": "USD"}
    ]
    with patch("phase1_ingestion_parsing.extract.extract_pdf", return_value=canonical_rows):
        rows, columns = extract_pdf_table_rows("does-not-exist.pdf", account_ref="acct")

    assert columns == ["Date", "Description", "Amount", "Direction", "Currency"]
    assert rows == [{"Date": "2026-06-02", "Description": "Coffee", "Amount": 5.0, "Direction": "out", "Currency": "USD"}]


def test_extract_pdf_table_rows_recovers_real_columns_from_a_scan_via_ocr(tmp_path):
    """A scanned numbered-table statement (blank text layer, no ruling-line
    table) must recover the file's REAL column names — `DATE/NARRATION/DEBIT/
    CREDIT/BALANCE` — via the OCR searchable-PDF + coordinate path, instead of
    collapsing to the 5-generic-column canonical fallback. This is the gap the
    2026-09-01 PROGRESS entry flagged: a scan reached the Transaction Matching
    tab's column picker with only generic columns, so you couldn't pick e.g.
    a real transaction-code column to match on."""
    import phase1_ingestion_parsing.extract as ext
    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")

    # Simulate the raw word-lines the coordinate mapper would see: a header
    # line and one transaction line, with tokens already assigned to columns.
    header_cols = [("#", 0.0, 10.0), ("DATE", 10.0, 40.0), ("NARRATION", 40.0, 90.0),
                   ("DEBIT", 90.0, 130.0), ("CREDIT", 130.0, 170.0), ("BALANCE", 170.0, float("inf"))]
    header_line = {"cols": header_cols, "is_header": True}
    data_line = {"cols": header_cols,
                 "tokens": {"#": ["1"], "DATE": ["Mar", "4,", "2026"], "NARRATION": ["Coffee"],
                            "DEBIT": [], "CREDIT": ["5.00"], "BALANCE": ["105.00"]}}
    pending_line = {"cols": header_cols,
                    "tokens": {"#": ["2"], "DATE": ["Mar", "5,", "2026"], "NARRATION": ["Transfer"],
                               "DEBIT": ["1.00"], "CREDIT": [], "BALANCE": ["104.00"]}}

    def fake_header_columns(line):
        # Mirrors the real `_table_header_columns`: only the header line returns
        # column ranges; data/continuation lines return None.
        return line["cols"] if line.get("is_header") else None

    def fake_map_line_to_columns(line, columns):
        return line["tokens"]

    fake_pdf_obj = MagicMock()
    fake_pdf_obj.pages = [MagicMock()]  # `_page_lines` is mocked, so pages just needs to iterate once
    fake_pdf_obj.__enter__.return_value = fake_pdf_obj
    fake_pdf_obj.__exit__.return_value = False

    with patch.object(ext, "extract_pdf_text", return_value=""), patch.object(
        ext, "ocr_to_searchable_pdf", return_value=b"%PDF-searchable"
    ), patch("phase1_ingestion_parsing.extract.pdfplumber.open", return_value=fake_pdf_obj), patch.object(
        ext, "_page_lines", return_value=[header_line, data_line, pending_line]
    ), patch.object(ext, "_is_noise_line", return_value=False), patch.object(
        ext, "_table_header_columns", side_effect=fake_header_columns), patch.object(
        ext, "_map_line_to_columns", side_effect=fake_map_line_to_columns
    ):
        rows, columns = extract_pdf_table_rows(fake_pdf, account_ref="acct")

    assert columns == ["#", "DATE", "NARRATION", "DEBIT", "CREDIT", "BALANCE"]
    # Two real transaction rows, each keyed by the file's own column names.
    assert rows[0]["DATE"] == "Mar 4, 2026"
    assert rows[0]["NARRATION"] == "Coffee"
    assert rows[0]["CREDIT"] == "5.00"
    assert rows[0]["DEBIT"] == ""
    assert rows[1]["DATE"] == "Mar 5, 2026"
    assert rows[1]["NARRATION"] == "Transfer"
    assert rows[1]["DEBIT"] == "1.00"


def test_extract_pdf_table_rows_uses_native_coordinates_not_ocr_when_text_layer_exists(tmp_path):
    """A numbered-table PDF that ALREADY has a text layer (but no ruling-line
    table — so `extract_tables()` finds nothing) must recover real columns from
    the native coordinates WITHOUT paying the OCR cost."""
    import phase1_ingestion_parsing.extract as ext
    fake_pdf = tmp_path / "native.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")

    header_cols = [("#", 0.0, 10.0), ("DATE", 10.0, 40.0), ("NARRATION", 40.0, 90.0),
                   ("DEBIT", 90.0, 130.0), ("CREDIT", 130.0, 170.0), ("BALANCE", 170.0, float("inf"))]
    header_line = {"cols": header_cols, "is_header": True}
    data_line = {"cols": header_cols,
                 "tokens": {"#": ["7"], "DATE": ["Jun", "2"], "NARRATION": ["Salary"],
                            "DEBIT": [], "CREDIT": ["1,500.00"], "BALANCE": ["2,500.00"]}}

    fake_pdf_obj = MagicMock()
    fake_pdf_obj.pages = [MagicMock()]  # `_page_lines` is mocked, so pages just needs to iterate once
    fake_pdf_obj.__enter__.return_value = fake_pdf_obj
    fake_pdf_obj.__exit__.return_value = False

    with patch.object(ext, "extract_pdf_text", return_value="SOME NATIVE TEXT LAYER\n# DATE NARRATION..."), patch.object(
        ext, "ocr_to_searchable_pdf"
    ) as mock_ocr, patch("phase1_ingestion_parsing.extract.pdfplumber.open", return_value=fake_pdf_obj), patch.object(
        ext, "_page_lines", return_value=[header_line, data_line]
    ), patch.object(ext, "_is_noise_line", return_value=False), patch.object(
        ext, "_table_header_columns",
        side_effect=lambda line: line["cols"] if line.get("is_header") else None
    ), patch.object(
        ext, "_map_line_to_columns", side_effect=lambda line, c: line["tokens"]
    ):
        rows, columns = extract_pdf_table_rows(fake_pdf, account_ref="acct")

    mock_ocr.assert_not_called()  # native text layer -> OCR must NOT run
    assert columns == ["#", "DATE", "NARRATION", "DEBIT", "CREDIT", "BALANCE"]
    assert rows[0]["CREDIT"] == "1,500.00"


def test_extract_positioned_table_rows_returns_none_when_scan_ocr_unavailable(tmp_path):
    """A scan with no usable OCR (e.g. tesseract missing) must fall through to
    the canonical fallback, not crash or return empty-with-header."""
    import phase1_ingestion_parsing.extract as ext
    fake_pdf = tmp_path / "scan_no_ocr.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")

    fake_pdf_obj = MagicMock()
    fake_pdf_obj.pages = [MagicMock()]
    fake_pdf_obj.__enter__.return_value = fake_pdf_obj
    fake_pdf_obj.__exit__.return_value = False

    with patch.object(ext, "extract_pdf_text", return_value=""), patch.object(
        ext, "ocr_to_searchable_pdf", return_value=None
    ), patch("phase1_ingestion_parsing.extract.pdfplumber.open", return_value=fake_pdf_obj):
        rows, columns = extract_pdf_table_rows(fake_pdf, account_ref="acct")

    # Falls all the way to canonical (which itself degrades to placeholder
    # rows via extract_pdf's own no-OCR path).
    assert columns == ["Date", "Description", "Amount", "Direction", "Currency"]


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
    with tempfile.TemporaryDirectory() as d:
        test_numbered_table_layout_tried_via_searchable_pdf_before_flat_text_ocr(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_numbered_table_layout_not_found_falls_back_to_flat_text_ocr(Path(d))
    test_numbered_table_layout_via_ocr_on_a_real_synthetic_scan()
    test_unmapped_text_layout_produces_a_flaggable_placeholder_row()
    test_recognized_layout_rows_do_not_need_spot_check()
    test_month_name_date_parsed_to_iso()
    test_detect_shape_on_mono_absa_text()
    test_detect_shape_layouts()
    test_tie_out_pass_and_fail()
    test_failed_tie_out_drops_confidence_below_floor()
    test_numbered_table_full_parse_of_real_absa_sample()
    test_stamp_currency_fills_blank_row_currency_from_detected_shape()
    test_stamp_currency_does_not_overwrite_an_already_set_row_currency()
    test_stamp_currency_no_op_when_shape_has_no_currency()
    test_numbered_table_full_parse_of_real_absa_sample_stamps_kes_currency()
    with tempfile.TemporaryDirectory() as d:
        test_extract_pdf_table_rows_skips_a_summary_table_with_no_data_rows(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_extract_pdf_table_rows_drops_a_repeated_header_on_a_later_page(Path(d))
    test_extract_pdf_table_rows_falls_back_to_canonical_when_no_real_table_found()
    test_numbered_table_does_not_parse_monthly_summary_or_totals_lines()
    print("extract tests OK")
