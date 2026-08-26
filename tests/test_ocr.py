"""Tests for phase1_ingestion_parsing/ocr.py — the OCR fallback degrades to
"no text" rather than raising when Tesseract isn't installed, exactly the
posture `extract.py` relies on. Plain asserts, same style as test_extract.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase1_ingestion_parsing.ocr import (  # noqa: E402
    _fix_ocr_errors,
    ocr_extract_text,
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


if __name__ == "__main__":
    test_tesseract_available_never_raises()
    test_ocr_extract_text_on_garbage_bytes_returns_empty_string()
    test_ocr_extract_text_on_empty_bytes_returns_empty_string()
    test_fix_ocr_errors_normalizes_common_misreads()
    print("ocr tests OK")
