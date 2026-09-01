"""Tests for phase1_ingestion_parsing/ocr.py — the OCR fallback degrades to
"no text" rather than raising when Tesseract isn't installed, exactly the
posture `extract.py` relies on. Plain asserts, same style as test_extract.py.
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase1_ingestion_parsing.ocr import (  # noqa: E402
    _fix_ocr_errors,
    _preprocess_image,
    ocr_extract_text,
    ocr_to_searchable_pdf,
    tesseract_available,
)


def test_tesseract_available_never_raises():
    assert isinstance(tesseract_available(), bool)


def test_ocr_extract_text_on_garbage_bytes_returns_empty_string():
    # Whether or not Tesseract is installed on the machine running this test,
    # bytes that aren't a real PDF must degrade to "" rather than raising.
    assert ocr_extract_text(b"not a real pdf") == ""


def test_ocr_extract_text_on_empty_bytes_returns_empty_string():
    assert ocr_extract_text(b"") == ""


def test_fix_ocr_errors_normalizes_common_misreads():
    assert _fix_ocr_errors("1@0.00") == "100.00"
    assert _fix_ocr_errors("100—50") == "100-50"


def test_preprocess_image_does_not_truncate_a_sparse_page():
    """Regression test for a real bug found 2026-09-01 (only reproducible
    against a real Tesseract binary, so it's skipped without one): the
    original preprocessing pipeline (grayscale + contrast(1.5) +
    sharpness(2.0), ported from the source this OCR support was adapted
    from) silently truncated OCR output to a single line on a page with a
    large blank region below the text -- e.g. a short statement, or a
    near-empty final page of a multi-page one -- dropping every transaction
    after the first with no error. Confirmed against the real Absa sample
    too: the sharpen step lost ~20% of recoverable text on 2 of 3 pages
    there as well, never an improvement. Grayscale + contrast alone (what
    `_preprocess_image` does now) doesn't reproduce either failure."""
    if not tesseract_available():
        return
    import pytesseract
    from PIL import Image, ImageDraw, ImageFont

    # Deliberately sparse: a little text at the top, a lot of blank canvas
    # below it -- the shape that reproduced the truncation.
    img = Image.new("RGB", (1600, 1500), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    lines = [
        "12/01/2024 SALARY PAYMENT 1,500.00",
        "15/01/2024 GROCERY STORE PURCHASE 85.50",
        "20/01/2024 ELECTRICITY BILL PAYMENT 120.00",
    ]
    y = 20
    for line in lines:
        draw.text((30, y), line, fill="black", font=font)
        y += 50

    text = pytesseract.image_to_string(_preprocess_image(img), config="--psm 3")
    for line in lines:
        assert line.split(" ", 1)[0] in text  # each transaction's date survived


def test_ocr_to_searchable_pdf_on_garbage_bytes_returns_none():
    assert ocr_to_searchable_pdf(b"not a real pdf") is None


def test_ocr_to_searchable_pdf_on_empty_bytes_returns_none():
    assert ocr_to_searchable_pdf(b"") is None


def test_ocr_to_searchable_pdf_recovers_word_positions_pdfplumber_can_read():
    """Regression test for the fix landed 2026-09-01: `_scan_numbered_table`
    needs each word's x-position to map tokens to columns, which
    `ocr_extract_text`'s flat string throws away -- measured at 0% recovery
    on a real scanned numbered-table statement. `ocr_to_searchable_pdf`
    keeps positions by re-OCRing into a PDF with an invisible text layer;
    this confirms pdfplumber can actually read words + coordinates back out
    of what Tesseract writes, not just that some bytes came back."""
    if not tesseract_available():
        return
    import pdfplumber
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1200, 300), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    draw.text((30, 30), "1 12-JAN-2024 SALARY PAYMENT 1500.00 0.00 1500.00", fill="black", font=font)

    buf = _image_to_pdf_bytes(img)
    result = ocr_to_searchable_pdf(buf)
    assert result is not None

    with pdfplumber.open(io.BytesIO(result)) as pdf:
        words = pdf.pages[0].extract_words()
    assert words, "expected pdfplumber to read a positioned text layer back out"
    assert all("x0" in w and "top" in w for w in words)
    joined = " ".join(w["text"] for w in words).upper()
    assert "SALARY" in joined


def _image_to_pdf_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PDF")
    return buf.getvalue()


if __name__ == "__main__":
    test_tesseract_available_never_raises()
    test_ocr_extract_text_on_garbage_bytes_returns_empty_string()
    test_ocr_extract_text_on_empty_bytes_returns_empty_string()
    test_fix_ocr_errors_normalizes_common_misreads()
    test_preprocess_image_does_not_truncate_a_sparse_page()
    test_ocr_to_searchable_pdf_on_garbage_bytes_returns_none()
    test_ocr_to_searchable_pdf_on_empty_bytes_returns_none()
    test_ocr_to_searchable_pdf_recovers_word_positions_pdfplumber_can_read()
    print("ocr tests OK")
