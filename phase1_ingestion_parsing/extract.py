"""PDF parsing and unstructured extraction (A3 step 2).

Rules-first: extract the text layer with pdfplumber and parse transaction lines
with deterministic patterns. Every produced row carries a `confidence` 0..1.
Rows below the configurable floor (0.85) are flagged for a manual spot-check.

Four layouts are handled:
  1. `date description amount` (single date column).
  2. `date_op date_val description ... amount [tax] balance` (two date
     columns, trailing amount/tax/balance — the common bank-statement-table
     layout, e.g. BBVA Peru). The balance always trails; when a tax/fee
     column is also present it sits between the amount and the balance.
  3. `YYYY-MM-DD txn-code description [category] amount balance` (a single
     ISO date, a reference-code token, then a running balance but no
     debit/credit sign — e.g. a generic US-style statement). Direction isn't
     printed anywhere on the line, so it's inferred from the balance delta
     against the previous row (seeded from a declared "STARTING BALANCE ...
     ENDING BALANCE" summary block when the statement has one; otherwise a
     description-keyword guess for the very first row only).
  4. `# DATE NARRATION DEBIT CREDIT BALANCE` (a numbered-transaction table
     with month-name dates, multi-line narrations, and unsigned amounts whose
     direction lives in *which column* — e.g. Mono/GTBank-style statements
     such as `samples/statement_absa.pdf`). The flattened text layer can't
     resolve this layout because columns are lost, so it is parsed from the
     word coordinates instead: the header line is detected, per-column x-ranges
     are derived from the header words, and every data-line token is mapped
     back to its column. `detect_shape()` reads the statement's own header
     metadata (bank, account, currency, period, declared totals/balances) and
     the parsed rows are validated against those declared balances — a failed
     tie-out drops each row's confidence so B3 flags it for manual review.

Layouts 2 and 3 both carry a running balance, so `extract_statement_balance` /
`extract_pdf_balance` can recover the statement's *closing* balance (the
figure the A5/B3 cash-balance check actually needs — a point-in-time
balance, not a sum of transaction amounts).

For a genuinely scanned/image-only PDF (no text layer at all), `ocr.py`
provides a Tesseract-based fallback. Two OCR modes, tried in order:
  1. `_extract_numbered_table_from_ocr` re-OCRs into a *searchable* PDF (a
     positioned, invisible text layer) and runs layout 4's coordinate-based
     column mapper against it — the one layout a flat OCR string can never
     recover, since it needs each word's x-position on the page.
  2. `_extract_from_ocr` OCRs into flat text and feeds it through the same
     flat-text layouts 1-3 above, rather than a separate parser — OCR only
     changes how the text is obtained.
OCR rows are capped below the B3 confidence floor regardless of tie-out, and
degrade to the existing placeholder when the `tesseract` binary isn't
installed or neither mode finds anything.

`extract_prompt.txt` defines a separate LLM extraction contract (D2 -> rules
over time) for cases neither rules nor OCR resolve; it is not yet wired to any
LLM client. The code here never trusts the LLM's numbers for arithmetic:
parsed amounts are parsed again into floats here.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from phase1_ingestion_parsing.ingest import DIR_IN, DIR_OUT, _looks_like_header
from phase1_ingestion_parsing.ocr import ocr_extract_text, ocr_to_searchable_pdf

try:
    import pdfplumber
except Exception:  # pragma: no cover - optional dep
    pdfplumber = None

# Layout 1: a single date, a description, and a trailing amount.
_TXN_RE = re.compile(
    r"(?P<date>\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<amt>[-+]?[0-9][0-9,]*\.\d{2})"
)

# Layout 2: two leading date columns (FECHA OPER / FECHA VALOR), often without
# a year since the statement period supplies it.
_TWO_DATE_RE = re.compile(r"^(?P<d1>\d{1,2}[-/.]\d{1,2}(?:[-/.]\d{2,4})?)\s+\d{1,2}[-/.]\d{1,2}(?:[-/.]\d{2,4})?\s+(?P<rest>.+)$")

# A decimal money token, guarded so it can't match a fragment of a dotted
# date embedded in narration text (e.g. "03.06.26" must never look like the
# number "03.06").
_NUM_RE = re.compile(r"(?<![\d.])[-+]?[0-9][0-9,]*\.\d{2}(?![\d.])")

# Any full DD-MM-YYYY (or DD/MM/YYYY) date in the document, used to recover
# the year for two-date-column rows that only carry day/month.
_YEAR_RE = re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/](\d{4})\b")

# Layout 3: a single ISO date, followed eventually by a trailing amount and
# running balance. No debit/credit sign survives the text extraction, so
# direction has to be inferred elsewhere (see `_running_balance_lines`).
_ISO_DATE_LINE_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<rest>.+)$")

# A leading all-caps/digits/hyphen reference token (e.g. "TXN-20260602-9E96E5")
# that some statements print between the date and the description.
_CODE_TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{4,}\s+")

# A declared opening/closing balance summary block, e.g.
# "STARTING BALANCE TOTAL DEPOSITS (+) TOTAL WITHDRAWALS (-) ENDING BALANCE"
# followed by a line of four dollar figures. Anchors layout-3 direction
# inference when the statement provides it.
_BALANCE_SUMMARY_HEADER_RE = re.compile(r"STARTING BALANCE.*ENDING BALANCE", re.IGNORECASE)

# Last-resort direction guess for a layout-3 statement's first row when no
# declared starting balance is available to anchor the running-balance diff.
_DEPOSIT_KEYWORDS = ("deposit", "salary", "payroll", "credit", "transfer from", "refund", "interest")

# Layout 4 (positional table parsing).
_MONTH_SHORT = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# A month-name date without separators: "June 23rd 2022", "Mar 4, 2026".
_MONTH_NAME_DATE_RE = re.compile(
    r"\b(?P<mon>[A-Za-z]{3,9})\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?[\s,]+(?P<y>\d{4})\b"
)

# Distinctive header of the registered positional table layout. A statement is
# routed to the table scanner only when a line carries all of these exact words,
# in order — so the flat-text layouts (and the page-1 "DATE TOTAL DEBITS ..."
# summary) can never match it by accident.
_NUMBERED_TABLE_HEADERS = ["#", "DATE", "NARRATION", "DEBIT", "CREDIT", "BALANCE"]

_ROW_NUM_RE = re.compile(r"^\d+$")

# Generator/footer lines that are neither a header nor a data row.
_NOISE_RE = (
    re.compile(r"Generated on", re.IGNORECASE),
    re.compile(r"api\.withmono\.com", re.IGNORECASE),
    re.compile(r"All \d+ transactions", re.IGNORECASE),
    re.compile(r"^\d+/\d+$"),
    re.compile(r"^Page\s+\d+\s+of\s+\d+", re.IGNORECASE),
)


def extract_pdf_text(path: str | Path) -> str:
    """Return the text layer of a PDF, or '' if unavailable."""
    if pdfplumber is None:
        return ""
    text_parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def _infer_year(text: str) -> str:
    m = _YEAR_RE.search(text)
    return m.group(1) if m else ""


def _parse_two_date_line(line: str, year: str) -> dict[str, Any] | None:
    """Parse a `date date description... amount [tax] balance` line.

    The trailing numbers are, in order: amount, an optional tax/fee, and
    always the running balance last. Two numbers -> amount, balance. Three
    or more -> amount is the first; everything after it up to the balance is
    ignored (tax columns). The balance is kept (under "balance") so callers
    can recover the statement's closing balance — it is not itself a row.
    """
    m = _TWO_DATE_RE.match(line.strip())
    if not m:
        return None
    rest = m.group("rest")
    nums = list(_NUM_RE.finditer(rest))
    if len(nums) < 2:
        return None  # can't tell amount apart from the running balance

    amt_match = nums[0]
    amount = float(amt_match.group().replace(",", ""))
    desc = rest[: amt_match.start()].strip()
    balance = float(nums[-1].group().replace(",", ""))

    date_raw = m.group("d1")
    separators = sum(date_raw.count(c) for c in "-/.")
    date_str = f"{date_raw}-{year}" if separators == 1 and year else date_raw

    return {
        "value_date": date_str,
        "amount": abs(amount),
        "direction": DIR_IN if amount >= 0 else DIR_OUT,
        "description": desc,
        "balance": balance,
    }


def _parse_single_date_line(line: str) -> dict[str, Any] | None:
    m = _TXN_RE.search(line)
    if not m:
        return None
    amount = float(m.group("amt").replace(",", ""))
    return {
        "value_date": m.group("date"),
        "amount": abs(amount),
        "direction": DIR_IN if amount >= 0 else DIR_OUT,
        "description": m.group("desc").strip(),
    }


def _parse_running_balance_line(line: str) -> dict[str, Any] | None:
    """Parse one `YYYY-MM-DD [code] description... amount balance` line.

    Returns amount/balance/description only — direction is resolved across
    the whole statement by `_running_balance_lines`, not line-by-line.
    """
    m = _ISO_DATE_LINE_RE.match(line.strip())
    if not m:
        return None
    rest = m.group("rest")
    nums = list(_NUM_RE.finditer(rest))
    if len(nums) < 2:
        return None  # can't tell amount apart from the running balance
    amount = float(nums[0].group().replace(",", ""))
    balance = float(nums[-1].group().replace(",", ""))
    desc = rest[: nums[0].start()].strip()
    desc = _CODE_TOKEN_RE.sub("", desc, count=1)
    desc = re.sub(r"[\s$]+$", "", desc).strip()
    return {"value_date": m.group("date"), "amount": abs(amount), "balance": balance, "description": desc}


def _find_declared_starting_balance(text: str) -> float | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _BALANCE_SUMMARY_HEADER_RE.search(line):
            for candidate in lines[i + 1 : i + 3]:
                nums = _NUM_RE.findall(candidate)
                if nums:
                    return float(nums[0].replace(",", ""))
    return None


def _guess_direction_from_description(description: str) -> str:
    low = description.lower()
    return DIR_IN if any(k in low for k in _DEPOSIT_KEYWORDS) else DIR_OUT


def _running_balance_lines(text: str) -> list[dict[str, Any]]:
    """Parse every layout-3 line, resolving direction from the balance delta
    against the running balance (seeded from a declared starting-balance
    summary block when present, else guessed from the first row's wording).
    """
    prev_balance = _find_declared_starting_balance(text)
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        parsed = _parse_running_balance_line(line)
        if parsed is None:
            continue
        amount, balance = parsed["amount"], parsed["balance"]
        if prev_balance is None:
            direction = _guess_direction_from_description(parsed["description"])
        elif round(prev_balance + amount, 2) == round(balance, 2):
            direction = DIR_IN
        elif round(prev_balance - amount, 2) == round(balance, 2):
            direction = DIR_OUT
        else:
            direction = DIR_IN if balance > prev_balance else DIR_OUT
        prev_balance = balance
        rows.append({**parsed, "direction": direction})
    return rows


def parse_statement_text(text: str, account_ref: str = "") -> list[dict[str, Any]]:
    """Parse an already-extracted text layer into canonical bank records."""
    year = _infer_year(text)
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines()):
        parsed = _parse_two_date_line(line, year) or _parse_single_date_line(line)
        if parsed is None:
            continue
        rows.append(
            {
                "key": f"bank:{account_ref}:{i}",
                "sheet": "bank",
                "source_type": "bank",
                "value_date": parsed["value_date"],
                "amount": parsed["amount"],
                "direction": parsed["direction"],
                "currency": "",
                "description": parsed["description"],
                "account_ref": account_ref,
                "confidence": 1.0,
            }
        )
    if rows:
        return rows
    # Neither layout 1 nor 2 matched anything: try layout 3 (ISO date +
    # running balance, no explicit sign) before giving up on the text.
    return [
        {
            "key": f"bank:{account_ref}:{i}",
            "sheet": "bank",
            "source_type": "bank",
            "value_date": r["value_date"],
            "amount": r["amount"],
            "direction": r["direction"],
            "currency": "",
            "description": r["description"],
            "account_ref": account_ref,
            "confidence": 1.0,
        }
        for i, r in enumerate(_running_balance_lines(text))
    ]


def extract_statement_balance(text: str) -> float | None:
    """Return the statement's closing balance (B3/A5: "as of cut-off date").

    This is the running balance after the *last* parsed transaction line —
    only available for layouts 2 and 3, which carry a trailing balance
    column. Single-date layouts (and CSV/Excel bank exports) have no balance
    column to recover, so this returns None for those; callers must treat
    that as "no balance available", not as zero.
    """
    year = _infer_year(text)
    balance: float | None = None
    for line in text.splitlines():
        parsed = _parse_two_date_line(line, year)
        if parsed is not None:
            balance = parsed["balance"]
    if balance is not None:
        return balance
    running_rows = _running_balance_lines(text)
    return running_rows[-1]["balance"] if running_rows else None


def extract_pdf_balance(path: str | Path) -> float | None:
    """`extract_statement_balance` for a PDF file directly.

    Falls back to the positional numbered-table parser's last-row balance when
    the text layer doesn't carry a recognizable running-balance layout (e.g. a
    Mono/GTBank statement whose balance column only survives in the word
    coordinates, not in the flattened text).
    """
    text = extract_pdf_text(path)
    if not text.strip():
        return None
    balance = extract_statement_balance(text)
    if balance is not None:
        return balance
    if pdfplumber is None or not Path(path).exists():
        return None
    rows = _scan_numbered_table(path, account_ref="")
    return rows[-1].get("balance") if rows and rows[-1].get("balance") is not None else None


def _unmapped_layout_placeholder(account_ref: str, reason: str) -> list[dict[str, Any]]:
    """A single confidence:0.0 row standing in for "nothing was recognized",
    so `assess_confidence` (B3: <85% -> manual spot-check) has something to
    flag instead of the file silently contributing zero rows with no signal.
    """
    return [
        {
            "key": f"bank:{account_ref}:unmapped",
            "sheet": "bank",
            "source_type": "bank",
            "value_date": "",
            "amount": 0.0,
            "direction": "",
            "currency": "",
            "description": reason,
            "account_ref": account_ref,
            "confidence": 0.0,
        }
    ]


def _parse_month_name_date(text: str) -> str:
    """Parse "June 23rd 2022" (or "Mar 4, 2026") into ISO YYYY-MM-DD, or ''."""
    m = _MONTH_NAME_DATE_RE.search(text)
    if not m:
        return ""
    num = _MONTH_SHORT.get(m.group("mon").lower()[:3])
    if not num:
        return ""
    return f"{m.group('y')}-{num:02d}-{int(m.group('d')):02d}"


def _page_lines(page) -> list[list[dict[str, Any]]]:
    """Group a page's extracted words into visual lines, sorted left-to-right.

    Words on the same baseline (within a tolerance derived from the median
    word height) form one line. Each word keeps its `x0`/`x1`/`top` so column
    membership can be recovered later — information `page.extract_text()`
    throws away when it flattens the page into a string.
    """
    words = page.extract_words() or []
    if not words:
        return []
    words.sort(key=lambda w: (w["top"], w["x0"]))
    heights = sorted(w["bottom"] - w["top"] for w in words)
    median_h = heights[len(heights) // 2] or 7.0
    tol = max(2.0, median_h * 0.5)
    lines: list[list[dict[str, Any]]] = []
    for w in words:
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])
    return lines


def _table_header_columns(line: list[dict[str, Any]]) -> list[tuple[str, float, float]] | None:
    """If `line` is a `# DATE NARRATION DEBIT CREDIT BALANCE` header, return
    (column_name, x_low, x_high) ranges derived from the header word positions.

    The boundary between two columns is the midpoint of the gap between the
    two header words, so a data token (e.g. a right-aligned amount) is mapped
    to the column whose x-range contains its x0.
    """
    up = [w["text"].upper() for w in line]
    idx: list[int] = []
    pos = 0
    for kw in _NUMBERED_TABLE_HEADERS:
        match = None
        for i in range(pos, len(up)):
            if up[i] == kw:
                match = i
                break
        if match is None:
            return None
        idx.append(match)
        pos = match + 1
    columns: list[tuple[str, float, float]] = []
    for k, i in enumerate(idx):
        prev_x1 = line[idx[k - 1]]["x1"] if k > 0 else None
        next_x0 = line[idx[k + 1]]["x0"] if k < len(idx) - 1 else None
        lo = (prev_x1 + line[i]["x0"]) / 2 if prev_x1 is not None else float("-inf")
        hi = (line[i]["x1"] + next_x0) / 2 if next_x0 is not None else float("inf")
        columns.append((_NUMBERED_TABLE_HEADERS[k], lo, hi))
    return columns


def _map_line_to_columns(line: list[dict[str, Any]], columns) -> dict[str, list[str]]:
    """Assign each word on a data line to the column whose x-range holds it."""
    out: dict[str, list[str]] = {name: [] for name, _, _ in columns}
    for w in line:
        for name, lo, hi in columns:
            if lo <= w["x0"] < hi:
                out[name].append(w["text"])
                break
    return out


def _first_number(tokens: list[str]) -> float | None:
    """First decimal money token in a column, or None."""
    for t in tokens:
        m = _NUM_RE.search(t)
        if m:
            return float(m.group().replace(",", ""))
    return None


def _is_noise_line(line: list[dict[str, Any]]) -> bool:
    """True for generator/footer lines that are neither header nor data."""
    text = " ".join(w["text"] for w in line).strip()
    return any(r.search(text) for r in _NOISE_RE)


def _scan_numbered_table(path: str | Path, account_ref: str = "") -> list[dict[str, Any]] | None:
    """Parse a Mono/GTBank numbered-narration statement from its word layout.

    Returns canonical rows, or None when the statement doesn't present the
    registered `# DATE NARRATION DEBIT CREDIT BALANCE` table header (so the
    caller can fall through to the flat-text layouts). A `balance` field is
    kept next to each row (the statement's own running balance column) — it is
    evidence, not a canonical amount.
    """
    try:
        with pdfplumber.open(str(path)) as pdf:
            pages_lines = [_page_lines(p) for p in pdf.pages]
    except Exception:  # pragma: no cover - unreadable PDF falls through to caller
        return None

    rows: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    prefix: list[str] = []
    header_columns: list[tuple[str, float, float]] | None = None
    row_no = 0

    def commit() -> None:
        nonlocal pending
        if pending is not None:
            rows.append(pending)
            pending = None

    for page_lines in pages_lines:
        for line in page_lines:
            if _is_noise_line(line):
                commit()
                prefix = []
                continue
            cols = _table_header_columns(line)
            if cols is not None:
                header_columns = cols
                commit()
                prefix = []
                continue
            if header_columns is None:
                continue  # page 1 header blocks / monthly summaries: not this table

            tokens = _map_line_to_columns(line, header_columns)
            hash_tok = "".join(tokens["#"])
            if _ROW_NUM_RE.match(hash_tok) and "".join(tokens["DATE"]):
                # A numbered transaction row.
                commit()
                debit = _first_number(tokens["DEBIT"])
                credit = _first_number(tokens["CREDIT"])
                balance = _first_number(tokens["BALANCE"])
                if credit is not None:
                    amount, direction = credit, DIR_IN
                elif debit is not None:
                    amount, direction = debit, DIR_OUT
                else:
                    continue  # numbered line with no money: not a transaction
                description = " ".join(prefix + tokens["NARRATION"]).strip()
                prefix = []
                row_no += 1
                pending = {
                    "key": f"bank:{account_ref}:{row_no}",
                    "sheet": "bank",
                    "source_type": "bank",
                    "value_date": _parse_month_name_date(" ".join(tokens["DATE"])),
                    "amount": abs(amount),
                    "direction": direction,
                    "currency": "",
                    "description": description,
                    "account_ref": account_ref,
                    "confidence": 1.0,
                    "balance": balance,
                }
            else:
                # A narration continuation — prefix before the first numbered
                # row, suffix after any row in flight. Multi-line narrations.
                narration = " ".join(tokens["NARRATION"]).strip()
                if narration:
                    if pending is not None:
                        pending["description"] = (
                            pending["description"] + " " + narration
                            if pending["description"]
                            else narration
                        )
                    else:
                        prefix.append(narration)
    commit()
    return rows if rows else None


# ------------------------------------------------------ statement metadata (-_-)
_BANK_NAMES = [
    "absa", "sterling", "gtbank", "gt bank", "access bank", "equity", "kcb",
    "cooperative bank", "stanbic", "standard chartered", "bbva", "interbank",
    "bancolombia", "baz", "ncba", "mono", "withmono", "safaricom", "mtn",
]
_CURRENCY_RE = re.compile(
    r"\b(KES|NGN|USD|PEN|EUR|GBP|UGX|TZS|RWF|ZMW|MXN|COP|BRL|GHS|XOF|ZAR|SOLES|DOLARES)\b",
    re.IGNORECASE,
)
# Spanish bank-statement currency labels map onto the ISO codes above — e.g.
# Peruvian "MONEDA: SOLES" means PEN, "DOLARES" means USD. Without this, a
# real Peru statement (BBVA "SOLES") would carry a blank currency and its
# amounts would be summed as if already in the base currency.
_CURRENCY_ALIASES = {"soles": "PEN", "dolares": "USD"}
# `account no.` and the number often sit on different lines ("Account no. Bank"
# then "0131883461 Absa Bank"), so the gap is any run of non-digits.
_ACCOUNT_NO_RE = re.compile(r"account(?: no\.?| number)?[^\d]{0,24}(\d{6,14})", re.IGNORECASE)
# Same-line forms only: the number follows its label on the same line, so the
# label gap must not cross a newline (a line-broken "Total debits Total
# credits\nKES ..." should never match a stray number on the next line).
_TOTAL_DEBITS_RE = re.compile(r"total\s+debits?[^\d\n]{0,15}([\d,]+\.\d{2})", re.IGNORECASE)
_TOTAL_CREDITS_RE = re.compile(r"total\s+credits?[^\d\n]{0,15}([\d,]+\.\d{2})", re.IGNORECASE)
_TOTAL_WITHDRAWALS_RE = re.compile(r"total\s+withdrawals?\(?-?\)?[^\d\n]{0,15}([\d,]+\.\d{2})", re.IGNORECASE)
_TOTAL_DEPOSITS_RE = re.compile(r"total\s+deposits?\(?\+?\)?[^\d\n]{0,15}([\d,]+\.\d{2})", re.IGNORECASE)
_AVAILABLE_BALANCE_RE = re.compile(r"available\s+balance[^\d\n]{0,15}([\d,]+\.\d{2})", re.IGNORECASE)
_OPENING_BALANCE_RE = re.compile(
    r"(?:saldo\s+anterior|starting\s+balance|opening\s+balance)[^\d\n]{0,15}([\d,]+\.\d{2})",
    re.IGNORECASE,
)
# The Mono/GTBank "Available balance / Total debits / Total credits" header with
# all three figures on the following line, in label order.
_TOTALS_BLOCK_RE = re.compile(
    r"available\s+balance\s+total\s+debits?\s+total\s+credits?\s*\n"
    r"[^\n]*?(?P<av>[\d,]+\.\d{2})[^\n]*?(?P<db>[\d,]+\.\d{2})[^\n]*?(?P<cr>[\d,]+\.\d{2})",
    re.IGNORECASE,
)
_PERIOD_RE = re.compile(
    r"(?:statement\s+period|statement\s+date)?[:\s]*(?P<s>[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"
    r"\s*(?:to|[-–—])\s*(?P<e>[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)


def detect_shape(text: str) -> dict[str, Any]:
    """Read the statement's own header metadata from its text layer.

    Bank, account number, currency, statement period, and any declared totals
    /opening/closing balances. Never used for arithmetic: it only feeds layout
    selection and the balance tie-out confidence check below.
    """
    shape: dict[str, Any] = {
        "layout": "", "bank": "", "account_no": "", "currency": "",
        "period_start": "", "period_end": "",
        "available_balance": None, "total_debits": None, "total_credits": None,
        "opening_balance": None, "closing_balance": None,
    }

    lines = text.splitlines()
    if all(kw in text for kw in _NUMBERED_TABLE_HEADERS):
        shape["layout"] = "numbered_table"
    elif _BALANCE_SUMMARY_HEADER_RE.search(text) and any(_ISO_DATE_LINE_RE.match(ln) for ln in lines):
        shape["layout"] = "iso_running"
    elif any(_TWO_DATE_RE.match(ln) for ln in lines):
        shape["layout"] = "two_date"
    elif _TXN_RE.search(text):
        shape["layout"] = "single_date"

    lower = text.lower()
    for bank in _BANK_NAMES:
        if bank in lower:
            shape["bank"] = bank
            break
    cur = _CURRENCY_RE.search(text)
    if cur:
        shape["currency"] = _CURRENCY_ALIASES.get(cur.group(1).lower(), cur.group(1).upper())
    acct = _ACCOUNT_NO_RE.search(text)
    if acct:
        shape["account_no"] = acct.group(1)
    period = _PERIOD_RE.search(text)
    if period:
        shape["period_start"] = period.group("s")
        shape["period_end"] = period.group("e")

    def _money(compiled) -> float | None:
        m = compiled.search(text)
        return float(m.group(1).replace(",", "")) if m else None

    # Mono/GTBank statements break "Available balance / Total debits / Total
    # credits" across two lines; the block form associates each figure with its
    # label by order. Everything else keeps the figure on its label's line.
    block = _TOTALS_BLOCK_RE.search(text)
    if block:
        shape["available_balance"] = float(block.group("av").replace(",", ""))
        shape["total_debits"] = float(block.group("db").replace(",", ""))
        shape["total_credits"] = float(block.group("cr").replace(",", ""))
    else:
        tdb = _money(_TOTAL_DEBITS_RE)
        if tdb is None:
            tdb = _money(_TOTAL_WITHDRAWALS_RE)
        tcr = _money(_TOTAL_CREDITS_RE)
        if tcr is None:
            tcr = _money(_TOTAL_DEPOSITS_RE)
        shape["total_debits"] = tdb
        shape["total_credits"] = tcr
        shape["available_balance"] = _money(_AVAILABLE_BALANCE_RE)
    shape["opening_balance"] = _money(_OPENING_BALANCE_RE)
    shape["closing_balance"] = _find_declared_closing_balance(text)
    return shape


def _find_declared_closing_balance(text: str) -> float | None:
    """Closing balance from a layout-3 "STARTING ... ENDING" summary block."""
    for i, line in enumerate(text.splitlines()):
        if _BALANCE_SUMMARY_HEADER_RE.search(line):
            for candidate in text.splitlines()[i + 1 : i + 3]:
                nums = _NUM_RE.findall(candidate)
                if len(nums) >= 4:
                    return float(nums[3].replace(",", ""))
    return None


def _tie_out(rows: list[dict[str, Any]], shape: dict[str, Any]) -> bool | None:
    """Reconcile parsed rows against the statement's own declared figures.

    Case A (numbered-table/Mono statements): sum of parsed debits and credits
    vs. the statement's declared "Total debits"/"Total credits". Case B
    (layout-3, ISO + running balance): opening + net == closing. Returns True
    (matches), False (mismatch -> rows need a manual spot-check), or None when
    the statement declares nothing to verify against.
    """
    if not rows:
        return None
    s_debits = sum(r["amount"] for r in rows if r["direction"] == DIR_OUT)
    s_credits = sum(r["amount"] for r in rows if r["direction"] == DIR_IN)

    def close(a: float, b: float) -> bool:
        return abs(a - b) <= max(0.01, abs(b) * 0.005)

    if shape.get("total_debits") is not None or shape.get("total_credits") is not None:
        ok = True
        if shape.get("total_debits") is not None:
            ok = ok and close(s_debits, shape["total_debits"])
        if shape.get("total_credits") is not None:
            ok = ok and close(s_credits, shape["total_credits"])
        return bool(ok)
    if shape.get("opening_balance") is not None and shape.get("closing_balance") is not None:
        net = s_credits - s_debits
        return bool(close(shape["opening_balance"] + net, shape["closing_balance"]))
    return None


def _stamp_currency(rows: list[dict[str, Any]], shape: dict[str, Any]) -> list[dict[str, Any]]:
    """Fill each row's blank `currency` from the statement-level, regex-detected
    one (`shape["currency"]`, from `detect_shape`) so FX normalization
    (phase2/calculate.py) has something to key off. The per-line parsers never
    populate a row's own `currency` — the statement declares it once, in its
    header/metadata text, not on every transaction line."""
    currency = shape.get("currency") or ""
    if currency:
        for r in rows:
            if not r.get("currency"):
                r["currency"] = currency
    return rows


def _apply_layout_confidence(rows: list[dict[str, Any]], text: str, shape: dict[str, Any]) -> None:
    """Adjust confidence from the balance tie-out, where the layout supports it.

    The two-date (BBVA-style) layout carries its own tax/fee column between the
    amount and the balance, so opening + net never equals closing there — the
    tie-out is only applied to layouts where the statement's own declared
    figures should hold exactly (numbered-table totals, ISO running balance).
    """
    if shape.get("layout") == "numbered_table":
        tie = _tie_out(rows, shape)
    elif shape.get("layout") == "iso_running":
        opening = _find_declared_starting_balance(text)
        closing = _find_declared_closing_balance(text)
        shape["opening_balance"] = opening
        shape["closing_balance"] = closing
        tie = _tie_out(rows, shape)
    else:
        tie = None
    if tie is None:
        return
    verdict = "ok" if tie else "failed"
    for r in rows:
        r["confidence"] = 1.0 if tie else 0.5
        r["tie_out"] = verdict


# ------------------------------------------------------------- public entry -_-
def _extract_from_ocr(path: str | Path, account_ref: str) -> list[dict[str, Any]] | None:
    """OCR a scanned/image-only PDF and run the result through the same
    flat-text layouts used for a real text layer. Returns None (never []) when
    OCR isn't usable or found nothing, so the caller can fall back to the
    placeholder with an accurate reason either way.
    """
    try:
        pdf_bytes = Path(path).read_bytes()
    except OSError:
        return None
    ocr_text = ocr_extract_text(pdf_bytes)
    if not ocr_text.strip():
        return None
    ocr_rows = parse_statement_text(ocr_text, account_ref=account_ref)
    if not ocr_rows:
        return None
    ocr_shape = detect_shape(ocr_text)
    _apply_layout_confidence(ocr_rows, ocr_text, ocr_shape)
    _stamp_currency(ocr_rows, ocr_shape)
    # OCR introduces its own error class beyond layout tie-out (misread
    # digits, dropped lines) — never let it read as fully trustworthy even
    # when the tie-out happens to pass.
    for r in ocr_rows:
        r["confidence"] = min(r.get("confidence", 1.0), 0.6)
        r["ocr"] = True
    return ocr_rows


def _extract_numbered_table_from_ocr(path: str | Path, account_ref: str) -> list[dict[str, Any]] | None:
    """OCR a scanned PDF into a searchable PDF and try the coordinate-based
    numbered-table layout against it — the one layout `_extract_from_ocr`'s
    flat text can never recover, since `_scan_numbered_table` maps tokens to
    columns by each word's x-position, information a flat string doesn't
    carry. Returns None (not just []) whenever this path isn't usable or the
    statement isn't this layout, so the caller falls through to the existing
    flat-text OCR fallback exactly as before — this only adds a path, it
    doesn't replace one.

    Runs its own OCR pass independent of `_extract_from_ocr`'s (rather than
    sharing one) to keep the well-tested flat-text path untouched; the extra
    Tesseract pass only happens for a genuine scan, not on every PDF.
    """
    if pdfplumber is None:
        return None
    try:
        pdf_bytes = Path(path).read_bytes()
    except OSError:
        return None
    searchable_pdf_bytes = ocr_to_searchable_pdf(pdf_bytes)
    if searchable_pdf_bytes is None:
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(searchable_pdf_bytes)
        tmp.close()
        table_rows = _scan_numbered_table(tmp.name, account_ref)
        if not table_rows:
            return None
        text = extract_pdf_text(tmp.name)
        shape = detect_shape(text)
        _apply_layout_confidence(table_rows, text, shape)
        _stamp_currency(table_rows, shape)
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    for r in table_rows:
        r["confidence"] = min(r.get("confidence", 1.0), 0.6)
        r["ocr"] = True
    return table_rows


def extract_pdf(path: str | Path, account_ref: str = "") -> list[dict[str, Any]]:
    """Parse a PDF statement into canonical records with confidence scores.

    Deterministic row parser. Order of preference: layout 4 (the positional
    numbered-narration table, when the statement presents its header), then the
    flat-text layouts 1-3. Rows are validated against the statement's own
    declared balances; a failed tie-out drops confidence so B3 flags a spot-check.
    If the page yields text but no layout matches, a confidence:0.0 placeholder
    row stands in so `assess_confidence` still flags it for manual review.

    A blank text layer (a real scan) tries OCR (`ocr.py`) before giving up —
    OCR only replaces how the text is obtained, not how it's parsed, so a
    scanned copy of a known layout still goes through the same validated
    rules. OCR rows are capped at confidence 0.6 (always below the 0.85 B3
    floor) regardless of tie-out, since OCR carries its own error class.
    """
    text = extract_pdf_text(path)
    if not text.strip():
        numbered_table_rows = _extract_numbered_table_from_ocr(path, account_ref)
        if numbered_table_rows is not None:
            return numbered_table_rows
        ocr_rows = _extract_from_ocr(path, account_ref)
        if ocr_rows is not None:
            return ocr_rows
        return _unmapped_layout_placeholder(
            account_ref,
            "scanned/image-only PDF - OCR unavailable or found no known transaction layout - manual transcription required",
        )
    shape = detect_shape(text)

    if pdfplumber is not None and Path(path).exists():
        table_rows = _scan_numbered_table(path, account_ref)
        if table_rows:
            _apply_layout_confidence(table_rows, text, shape)
            return _stamp_currency(table_rows, shape)

    rows = parse_statement_text(text, account_ref=account_ref)
    if rows:
        _apply_layout_confidence(rows, text, shape)
        return _stamp_currency(rows, shape)
    # Text layer exists, but no line matched a known layout: an unmapped
    # format, not an empty statement. Flag it rather than silently returning
    # nothing.
    return _unmapped_layout_placeholder(
        account_ref, "PDF has a text layer but no line matched a known statement layout - manual spot-check required"
    )


def _canonical_as_table_rows(path: str | Path, account_ref: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Fallback for `extract_pdf_table_rows` when no real embedded table is
    found: reuse the validated canonical parser rather than writing a second
    raw one, relabeled to generic display columns."""
    canonical = extract_pdf(path, account_ref=account_ref)
    columns = ["Date", "Description", "Amount", "Direction", "Currency"]
    rows = [
        {
            "Date": r.get("value_date", ""),
            "Description": r.get("description", ""),
            "Amount": r.get("amount", ""),
            "Direction": r.get("direction", ""),
            "Currency": r.get("currency", ""),
        }
        for r in canonical
    ]
    return rows, columns


def extract_pdf_table_rows(path: str | Path, account_ref: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """Raw, real-column-name extraction for the Transaction Matching tab's
    column picker — deliberately *not* the canonical schema `extract_pdf()`
    produces. Matching on a statement's own reference key (e.g. "Transaction
    Code") needs that exact column, which the canonical parser discards in
    favor of a fixed `description`/`amount`/`direction` shape.

    Tries pdfplumber's own table detection first (real ruling-line tables;
    the header row is identified with the same keyword heuristic
    `ingest.py` uses for an Excel export's title-row offset), concatenating
    tables across pages that share that header. Falls back to `extract_pdf`'s
    validated canonical rows — relabeled to generic display columns — when no
    real table is found (a flat-text layout, or OCR'd text), reusing proven
    parsing rather than a second raw one for that case.
    """
    if pdfplumber is None or not Path(path).exists():
        return _canonical_as_table_rows(path, account_ref)

    header: list[str] | None = None
    rows: list[dict[str, Any]] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                if not table or not table[0]:
                    continue
                first_row = table[0]
                if header is None:
                    # A summary/metadata table (account totals, address block)
                    # can loosely satisfy the keyword check too (e.g. a
                    # "Starting Balance" cell matching on "balance") but never
                    # has data rows beneath its own header-like line — require
                    # at least one to rule those out before locking this in
                    # as *the* transaction table.
                    if not _looks_like_header(first_row) or len(table) < 2:
                        continue
                    header = [str(c or "").strip() or f"Column {i + 1}" for i, c in enumerate(first_row)]
                    body = table[1:]
                else:
                    # A repeated header on a later page (common on multi-page
                    # statements) is dropped; anything else is data.
                    body = table[1:] if _looks_like_header(first_row) else table
                for raw_row in body:
                    if not raw_row or not any(raw_row):
                        continue
                    row: dict[str, Any] = {}
                    for i, value in enumerate(raw_row):
                        col = header[i] if i < len(header) else f"Column {i + 1}"
                        row[col] = value.strip() if isinstance(value, str) else value
                    rows.append(row)

    if header and rows:
        return rows, header
    return _canonical_as_table_rows(path, account_ref)


def assess_confidence(rows: list[dict[str, Any]], floor: float) -> list[dict[str, Any]]:
    """Tag rows whose confidence is below the floor for manual spot-check."""
    for r in rows:
        r["needs_spot_check"] = r.get("confidence", 1.0) < floor
    return rows
