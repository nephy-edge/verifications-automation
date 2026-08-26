"""OCR fallback for scanned/image-only bank statements (A3 step 2 extension).

Ported from the `vehicle-verification` repo's `reconciliation.py` (the render
+ preprocess + Tesseract steps), adapted to feed our *existing* rules-based
layout parsers rather than a separate narrow format: `extract.py`'s
`extract_pdf()` calls `ocr_extract_text()` only when the PDF has no text
layer at all (a real scan), then re-runs the same `detect_shape()` /
`parse_statement_text()` / tie-out pipeline used for every other PDF against
the OCR'd text. That way a scanned copy of a known statement layout still
gets parsed by validated logic instead of a second, narrower parser — OCR
only replaces *how the text is obtained*, never how it's interpreted.

Both `pytesseract` and `pypdfium2` are optional dependencies (like
`pdfplumber` in extract.py): if the packages aren't installed, or the
`tesseract` binary itself isn't on the machine, every function here degrades
to "no text extracted" rather than raising, so the caller falls back to the
existing confidence:0.0 placeholder exactly as it did before OCR existed.
"""

from __future__ import annotations

import os
import shutil

try:
    import pytesseract
except Exception:  # pragma: no cover - optional dep
    pytesseract = None

try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover - optional dep
    pdfium = None

try:
    from PIL import Image, ImageEnhance
except Exception:  # pragma: no cover - optional dep
    Image = None
    ImageEnhance = None

# Common Windows install locations, checked when TESSERACT_CMD isn't set and
# the binary isn't already on PATH (mirrors the ported tool's own fallback).
_WINDOWS_TESSERACT_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _configure_tesseract_cmd() -> None:
    if pytesseract is None:
        return
    configured = os.environ.get("TESSERACT_CMD")
    if configured:
        pytesseract.pytesseract.tesseract_cmd = configured
        return
    if shutil.which("tesseract"):
        return  # already resolvable on PATH
    for candidate in _WINDOWS_TESSERACT_PATHS:
        if os.path.exists(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            return


def tesseract_available() -> bool:
    """Whether OCR can actually run here — deps installed and a binary found."""
    if pytesseract is None or pdfium is None or Image is None:
        return False
    _configure_tesseract_cmd()
    cmd = pytesseract.pytesseract.tesseract_cmd or "tesseract"
    return os.path.exists(cmd) or bool(shutil.which(cmd))


def _preprocess_image(image: "Image.Image") -> "Image.Image":
    """Grayscale + contrast/sharpness boost — measurably improves Tesseract
    accuracy on the low-contrast, small-font scans bank statements tend to be."""
    image = image.convert("L")
    image = ImageEnhance.Contrast(image).enhance(1.5)
    image = ImageEnhance.Sharpness(image).enhance(2.0)
    return image


def _fix_ocr_errors(text: str) -> str:
    """Fix Tesseract misreads common enough to be worth a blind substitution."""
    text = text.replace("@", "0")
    text = text.replace("\u2014", "-")  # em dash
    text = text.replace("\u2013", "-")  # en dash
    text = text.replace("€", "e")
    return text


def ocr_extract_text(pdf_bytes: bytes) -> str:
    """OCR every page of a PDF into text, or "" if OCR isn't usable/found nothing.

    Never raises: a missing `pytesseract`/`pypdfium2` install, a missing
    `tesseract` binary, or a corrupt/unreadable PDF are all just "no text",
    the same outcome as a genuinely blank scan — the caller already has a
    placeholder/spot-check path for that.
    """
    if not tesseract_available():
        return ""
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception:
        return ""
    try:
        parts: list[str] = []
        for page_idx in range(len(pdf)):
            bitmap = pdf[page_idx].render(scale=3)
            image = _preprocess_image(bitmap.to_pil())
            try:
                parts.append(pytesseract.image_to_string(image, config="--psm 3"))
            except Exception:
                return ""  # tesseract present but failed to run (e.g. binary broken)
        return _fix_ocr_errors("\n".join(parts))
    finally:
        pdf.close()
