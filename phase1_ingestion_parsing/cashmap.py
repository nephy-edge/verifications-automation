"""Cash-map parsing and account matching (A2 cash map -> per-account context).

A cash map (see ingest.py's SHEET_CASH_MAP note) is the Investment Officer's
own writeup of a borrower's account structure — free text (.txt/.html/.pdf),
a Word doc (.docx), or a structured schema (.json) — never a transaction
ledger. This module reads just enough of it to answer one question per
uploaded bank/mobile statement: "which named account is this, and what is it
for?"

Matching is necessarily best-effort: most real cash maps carry no account
number, only a bank name and/or a role description (see the two real
samples this was built against — one names banks inline, one doesn't at
all). When a bank name is available and appears in the statement's own text
or filename, that narrows the candidates; otherwise every account in the
cash map is returned as an equally-likely candidate for a human to confirm.
This module never asserts a match on its own.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

import xml.etree.ElementTree as ET

from phase1_ingestion_parsing.extract import extract_pdf_text

_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _read_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            xml_bytes = z.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, FileNotFoundError, OSError):
        return ""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    paragraphs = []
    for p in root.iter(f"{_DOCX_NS}p"):
        text = "".join(node.text or "" for node in p.iter(f"{_DOCX_NS}t"))
        paragraphs.append(text)
    return "\n".join(paragraphs)


def _strip_html(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<[^>]+>", "\n", html)
    return html


def _read_cash_map_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(path)
    if suffix == ".docx":
        return _read_docx_text(path)
    raw = path.read_text(encoding="utf-8", errors="ignore")
    return _strip_html(raw) if suffix == ".html" else raw


def _parse_structured_json(data: dict[str, Any]) -> dict[str, Any] | None:
    """The richer schema (schema_version 2.0+): a real `accounts` array with
    bank_name/currency/purpose per account. Returns None if `data` doesn't
    look like this schema, so the caller can fall back to free-text parsing.
    """
    accounts = data.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        return None
    parsed: list[dict[str, Any]] = []
    for a in accounts:
        if not isinstance(a, dict):
            continue
        purpose = a.get("purpose") or []
        if isinstance(purpose, dict):
            purpose = purpose.get("value") or []
        if not isinstance(purpose, list):
            purpose = [str(purpose)]
        label = str(a.get("label") or a.get("id") or "")
        description = label + (f" — {', '.join(str(p) for p in purpose)}" if purpose else "")
        parsed.append(
            {
                "id": str(a.get("id") or label),
                "label": label,
                "bank_name": str(a.get("bank_name") or ""),
                "currency": str(a.get("currency") or ""),
                "purpose": [str(p) for p in purpose],
                "description": description,
            }
        )
    borrower_name = str((data.get("narrative") or {}).get("borrower_name") or "")
    return {"borrower_name": borrower_name, "accounts": parsed, "source": "structured_json"}


# The free-text questionnaire's account-listing question. Answers vary in
# shape: some list one "Account N: ..." per line, some run them together in
# one paragraph separated by periods — this matches either, stopping each
# entry at the next "Account N:" token or the end of the block.
_ACCOUNT_SECTION_RE = re.compile(
    r"describe its purpose,?\s*bank.*?\n(?P<body>.*?)(?=\n\s*4\.3|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_ACCOUNT_ENTRY_RE = re.compile(r"Account\s+(\d+)\s*:\s*(.+?)(?=Account\s+\d+\s*:|\Z)", re.DOTALL)
_BORROWER_NAME_RE = re.compile(r"Borrower name:\s*(.+)")

# A light heuristic for a bank name embedded in an account's free-text
# description, e.g. "...disbursement account at BDO Philippines." — only
# used when the cash map names one; most don't, and that's left blank
# rather than guessed.
_BANK_NAME_HINT_RE = re.compile(r"(?:\bat\b|\bthrough\b|\bwith\b|\bvia\b)\s+([A-Z][A-Za-z.&' ]{2,40}?)(?=[.,\n]|$)")


def _parse_free_text(text: str) -> dict[str, Any]:
    accounts: list[dict[str, Any]] = []
    section = _ACCOUNT_SECTION_RE.search(text)
    body = section.group("body") if section else text
    # Bracketed tags (e.g. "[Account 1: Purpose - Loan disbursements]",
    # "[e]") are the questionnaire template's own field labels, echoed
    # inline ahead of the answer - not content, and "Account N:" inside one
    # would otherwise be mistaken for a real account entry.
    body = re.sub(r"\[[^\]]*\]", "", body)

    for match in _ACCOUNT_ENTRY_RE.finditer(body):
        number, description = match.group(1), " ".join(match.group(2).split())
        bank_hint = _BANK_NAME_HINT_RE.search(description)
        accounts.append(
            {
                "id": f"account_{number}",
                "label": f"Account {number}",
                "bank_name": bank_hint.group(1).strip() if bank_hint else "",
                "currency": "",
                "purpose": [description],
                "description": description,
            }
        )

    # Accounts mentioned outside the numbered list (e.g. "an additional
    # escrow account controlled by Actinver") shouldn't be silently dropped -
    # unless a numbered entry's non-greedy match already swallowed the same
    # sentence (the two run together with no separator in the source text).
    already_captured = " ".join(a["description"] for a in accounts)
    for line in body.splitlines():
        stripped = " ".join(line.split())
        if not stripped or _ACCOUNT_ENTRY_RE.match(stripped) or stripped in already_captured:
            continue
        if "escrow" in stripped.lower():
            bank_hint = _BANK_NAME_HINT_RE.search(stripped)
            accounts.append(
                {
                    "id": f"account_extra_{len(accounts) + 1}",
                    "label": "Additional account (mentioned outside the numbered list)",
                    "bank_name": bank_hint.group(1).strip() if bank_hint else "",
                    "currency": "",
                    "purpose": [stripped],
                    "description": stripped,
                }
            )

    borrower_match = _BORROWER_NAME_RE.search(text)
    borrower_name = borrower_match.group(1).strip() if borrower_match else ""
    return {"borrower_name": borrower_name, "accounts": accounts, "source": "free_text"}


def parse_cash_map(path: str | Path) -> dict[str, Any]:
    """Parse one cash-map file into `{borrower_name, accounts, source}`.

    `accounts` entries always carry id/label/bank_name/currency/purpose/
    description, regardless of source format, so callers never need to
    branch on `source`.
    """
    path = Path(path)
    text = _read_cash_map_text(path)
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            structured = _parse_structured_json(data)
            if structured is not None:
                return structured
    return _parse_free_text(text)


def match_accounts(
    statement_text: str,
    statement_filename: str,
    accounts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Candidate cash-map accounts for one bank/mobile statement.

    Narrows to accounts whose declared bank name appears in the statement's
    own text or filename. If no account carries a bank name, or none match,
    every account is returned — ambiguous by nature of the source data, so
    a human picks rather than the tool guessing.
    """
    haystack = f"{statement_text}\n{statement_filename}".lower()
    named_matches = [a for a in accounts if a.get("bank_name") and a["bank_name"].lower() in haystack]
    return named_matches if named_matches else accounts
