"""Phase 1 — Ingestion & PDF parsing.

Multi-format ingestion and normalization plus unstructured PDF extraction.
All ingestion is deterministic; the LLM prompt is reserved for layouts that
cannot be parsed with rules (D2 band of the determinism spectrum).
"""

from phase1_ingestion_parsing.extract import extract_pdf, extract_pdf_text
from phase1_ingestion_parsing.ingest import load_and_normalize, normalize_row

__all__ = [
    "extract_pdf",
    "extract_pdf_text",
    "load_and_normalize",
    "normalize_row",
]
