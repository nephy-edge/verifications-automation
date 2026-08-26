"""Multi-format ingestion and normalization (A3 step 1).

Loads the input classes named in the workflow record (A2) — loan tape, bank
statements, mobile money, ledger balances, cash map — and normalizes each row to
a single canonical schema:

    key, sheet, source_type, value_date, amount, direction,
    currency, description, account_ref, confidence

Deterministic only: no LLM. Column mapping is explicit (and overridable per
caller) so the workflow never guesses about a file's shape.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

SHEET_TAPE = "tape"
SHEET_BANK = "bank"
SHEET_MOBILE = "mobile_money"
SHEET_LEDGER = "ledger"
# A cash map (A2) is the Investment Officer's own writeup of a borrower's
# account structure, signatories, and cash-flow routing — a filled-out
# questionnaire (free text, checkboxes), never a transaction ledger. There is
# no tabular schema to detect here; it is ingested as a reference attachment
# (see app/streamlit_app.py) for a human to read, not parsed for amounts.
SHEET_CASH_MAP = "cash_map"

DIR_IN = "in"
DIR_OUT = "out"


# Default column->canonical mappings per sheet. Callers may override.
DEFAULT_SCHEMAS: dict[str, dict[str, str]] = {
    SHEET_TAPE: {"date": "value_date", "amount": "amount", "type": "description", "currency": "currency"},
    SHEET_BANK: {"date": "value_date", "amount": "amount", "narration": "description", "currency": "currency"},
    SHEET_MOBILE: {"date": "value_date", "amount": "amount", "description": "description", "currency": "currency"},
    SHEET_LEDGER: {"as_of": "value_date", "ledger_balance": "amount", "account": "description", "currency": "currency"},
}

# Columns that identify a real per-loan tape export (A2 input: "Loan tape data",
# AWS S3) as opposed to the simplified date/amount/type sample format above.
# When these are present we derive disbursement/collection events per loan
# (A5 methodology) instead of expecting a pre-flattened transaction row.
LOAN_TAPE_KEY_COLS = {
    "loan_id",
    "begin_date",
    "principal_amount",
    "total_loan_amount",
    "principal_outstanding",
}


def _parse_float(value: Any) -> float:
    """Coerce a raw cell value to float, treating None/NaN/'' as 0.0."""
    if value is None:
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f else f  # NaN != NaN


def _first_present_date(row: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v) != "nan" and str(v).strip() != "":
            return str(v)
    return ""


def normalize_loan_tape_row(row: dict[str, Any], *, account_ref: str = "") -> list[dict[str, Any]]:
    """Derive disbursement + collections events from one per-loan tape row.

    Disbursement = principal_amount, dated at begin_date (money lent out).
    Collections = total_loan_amount minus everything still outstanding
    (principal/interest/fees/penalties), dated at closure_date if the loan is
    closed else company_due_date — i.e. the amount actually paid down so far.
    """
    loan_id = str(row.get("loan_id") or "")
    out: list[dict[str, Any]] = []

    principal_amount = _parse_float(row.get("principal_amount"))
    begin_date = _first_present_date(row, "begin_date")
    if principal_amount and begin_date:
        out.append(
            {
                "key": f"tape:{account_ref}:{loan_id}:disb",
                "sheet": SHEET_TAPE,
                "source_type": SHEET_TAPE,
                "value_date": begin_date,
                "amount": abs(principal_amount),
                "direction": DIR_OUT,
                "currency": str(row.get("currency") or ""),
                "description": f"disbursement:{loan_id}",
                "account_ref": account_ref,
                "confidence": 1.0,
            }
        )

    total_loan_amount = _parse_float(row.get("total_loan_amount"))
    outstanding = (
        _parse_float(row.get("principal_outstanding"))
        + _parse_float(row.get("interest_outstanding"))
        + _parse_float(row.get("fees_outstanding"))
        + _parse_float(row.get("penalties_outstanding"))
    )
    paid_amount = max(0.0, total_loan_amount - outstanding)
    collection_date = _first_present_date(row, "closure_date", "company_due_date", "begin_date")
    if paid_amount and collection_date:
        out.append(
            {
                "key": f"tape:{account_ref}:{loan_id}:coll",
                "sheet": SHEET_TAPE,
                "source_type": SHEET_TAPE,
                "value_date": collection_date,
                "amount": paid_amount,
                "direction": DIR_IN,
                "currency": str(row.get("currency") or ""),
                "description": f"collections:{loan_id}",
                "account_ref": account_ref,
                "confidence": 1.0,
            }
        )

    return out


# Recognized column-role keywords, used both to find the real header row in
# an Excel export (which may have title/spacer rows above it) and to detect
# which column plays which role once the header is known.
_HEADER_KEYWORDS = (
    "date", "amount", "debit", "credit", "balance", "description", "narration",
    "loan_id", "principal_amount", "total_loan_amount", "as_of", "ledger_balance",
)


def _looks_like_header(values: Any) -> bool:
    text = " ".join(str(v).strip().lower() for v in values if str(v).strip().lower() not in ("nan", ""))
    return any(k in text for k in _HEADER_KEYWORDS)


def _read_excel(path: Path) -> pd.DataFrame:
    """Read an Excel file, auto-detecting the header row.

    Real exports vary in how many title/spacer rows sit above the actual
    header — a fixed offset (e.g. always row 1) is wrong for anything that
    doesn't match that specific shape. Scans the first 10 rows for one that
    contains recognizable column-role keywords and uses that as the header;
    falls back to pandas' own default if nothing matches.
    """
    raw = pd.read_excel(path, header=None)
    for i in range(min(10, len(raw))):
        if _looks_like_header(raw.iloc[i].tolist()):
            df = raw.iloc[i + 1 :].reset_index(drop=True)
            df.columns = [str(c).strip() for c in raw.iloc[i].tolist()]
            return df
    return pd.read_excel(path)


def _read_tabular(path: Path) -> pd.DataFrame:
    """Read CSV or Excel into a DataFrame."""
    if path.suffix.lower() in (".csv", ".txt"):
        return pd.read_csv(path)
    return _read_excel(path)


def _find_col(columns: Any, *needles: str) -> str | None:
    """First column whose lowercased name contains any of `needles`, in order."""
    lower_map = {str(c).strip().lower(): c for c in columns}
    for needle in needles:
        for lc, orig in lower_map.items():
            if needle in lc:
                return orig
    return None


def detect_bank_schema(columns: Any) -> dict[str, str | None]:
    """Case-insensitive column-role detection for bank/mobile statements.

    Real exports vary: a single signed `amount` column, or a split
    debit/credit pair (any naming — "Debit (-)"/"Credit (+)",
    "withdrawal"/"deposit", "dr"/"cr"). A `balance` column, when present, is
    a running balance — the reconciliation-relevant figure is the closing
    balance (see `extract_tabular_balance`), not a per-row amount.
    """
    return {
        "date": _find_col(columns, "date"),
        "description": _find_col(columns, "description", "narration", "particular", "memo", "details"),
        "amount": _find_col(columns, "amount", "value", "amt"),
        "debit": _find_col(columns, "debit", "withdrawal", " dr", "(-)"),
        "credit": _find_col(columns, "credit", "deposit", " cr", "(+)"),
        "balance": _find_col(columns, "balance"),
    }


def normalize_bank_row(
    row: dict[str, Any],
    schema: dict[str, str | None],
    *,
    account_ref: str = "",
    index: int = 0,
) -> dict[str, Any]:
    """Map one raw bank/mobile row to a canonical record via detected columns.

    Debit/credit split takes priority when both are present (more explicit
    than a single signed amount); falls back to the signed `amount` column.
    A row with no usable date or amount/debit/credit value is dropped.
    """
    date_col = schema.get("date")
    value_date = row.get(date_col) if date_col else None
    if value_date is None or str(value_date).strip() in ("", "nan"):
        return {}

    debit_col, credit_col, amount_col = schema.get("debit"), schema.get("credit"), schema.get("amount")
    amount = 0.0
    direction = ""
    if debit_col or credit_col:
        debit_val = _parse_float(row.get(debit_col)) if debit_col else 0.0
        credit_val = _parse_float(row.get(credit_col)) if credit_col else 0.0
        if credit_val:
            amount, direction = credit_val, DIR_IN
        elif debit_val:
            amount, direction = debit_val, DIR_OUT
        else:
            return {}
    elif amount_col:
        cell = row.get(amount_col)
        if cell is None or str(cell).strip().lower() in ("", "nan"):
            return {}
        raw = _parse_float(cell)
        if raw == 0.0:
            return {}  # nothing to record either way
        amount, direction = abs(raw), (DIR_IN if raw >= 0 else DIR_OUT)
    else:
        return {}

    desc_col = schema.get("description")
    description = row.get(desc_col) if desc_col else ""
    return {
        "key": f"bank:{account_ref}:{index}",
        "sheet": SHEET_BANK,
        "source_type": SHEET_BANK,
        "value_date": str(value_date),
        "amount": amount,
        "direction": direction,
        "currency": "",
        "description": str(description or ""),
        "account_ref": account_ref,
        "confidence": 1.0,
    }


def extract_tabular_balance(path: str | Path) -> float | None:
    """Closing balance from a CSV/Excel bank statement's balance column.

    Takes the last non-null value under a detected `balance` column,
    assuming chronological row order (statements are conventionally
    exported oldest-first). Returns None when there's no balance column to
    read — callers must treat that as "no balance available", not zero.
    """
    path = Path(path)
    try:
        df = _read_tabular(path)
    except Exception:
        return None
    schema = detect_bank_schema(df.columns)
    balance_col = schema.get("balance")
    if not balance_col:
        return None
    series = df[balance_col].dropna()
    if series.empty:
        return None
    try:
        return float(series.iloc[-1])
    except (TypeError, ValueError):
        return None


def normalize_row(
    row: dict[str, Any],
    *,
    sheet: str,
    schema: dict[str, str] | None = None,
    account_ref: str = "",
    index: int = 0,
) -> dict[str, Any]:
    """Map one raw row dict to a canonical record. Rows missing an amount/date are dropped."""
    schema = schema or DEFAULT_SCHEMAS.get(sheet, {})
    get_value = lambda *keys: next((row.get(k) for k in keys if row.get(k) is not None and str(row.get(k)) != "nan"), None)  # noqa: E731

    value_date = get_value(schema.get("value_date", "value_date"), "value_date", "date")
    amount_raw = get_value(schema.get("amount", "amount"), "amount")
    description = get_value(schema.get("description", "description"), "description", "narration", "type")
    currency = get_value(schema.get("currency", "currency"), "currency")

    if amount_raw is None or value_date is None:
        return {}

    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        return {}

    direction = DIR_IN if amount >= 0 else DIR_OUT
    key = f"{sheet}:{account_ref}:{index}"
    return {
        "key": key,
        "sheet": sheet,
        "source_type": sheet,
        "value_date": str(value_date),
        "amount": abs(amount),
        "direction": direction,
        "currency": currency or "",
        "description": str(description or ""),
        "account_ref": account_ref,
        "confidence": 1.0,
    }


def _load_file(path: Path, sheet: str, schema: dict[str, str] | None) -> list[dict[str, Any]]:
    if path.suffix.lower() in (".pdf",):
        # PDFs are handled by phase1 extraction; return nothing here.
        return []
    try:
        df = _read_tabular(path)
    except Exception:
        return []

    if sheet == SHEET_TAPE and schema is None and LOAN_TAPE_KEY_COLS.issubset(df.columns):
        rows: list[dict[str, Any]] = []
        for record in df.to_dict("records"):
            rows.extend(normalize_loan_tape_row(record, account_ref=path.stem))
        return rows

    if sheet in (SHEET_BANK, SHEET_MOBILE) and schema is None:
        bank_schema = detect_bank_schema(df.columns)
        has_amount_shape = bool(bank_schema["amount"] or (bank_schema["debit"] and bank_schema["credit"]))
        if bank_schema["date"] and has_amount_shape:
            rows = []
            for i, record in enumerate(df.to_dict("records")):
                n = normalize_bank_row(record, bank_schema, account_ref=path.stem, index=i)
                if n:
                    rows.append(n)
            return rows
        # Falls through to the simplified date/amount/type/currency format below.

    col_map = schema or DEFAULT_SCHEMAS.get(sheet, {})
    # Build a limited view of only the columns we might read.
    rows = []
    target_cols = set(col_map.values()) | {"value_date", "amount", "description", "currency", "date", "narration", "type"}
    for i, record in enumerate(df.to_dict("records")):
        slim = {k: v for k, v in record.items() if k in target_cols or k in col_map}
        n = normalize_row(slim, sheet=sheet, schema=schema, account_ref=path.stem, index=i)
        if n:
            rows.append(n)
    return rows


def load_and_normalize(
    files: Sequence[str | Path],
    *,
    sheet: str,
    schema: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Load one or more files for a sheet, returning normalized canonical records.

    `sheet` is one of the canonical sheets (tape/bank/mobile_money/ledger/cash_map).
    """
    out: list[dict[str, Any]] = []
    for f in files:
        p = Path(f)
        if not p.exists():
            continue
        out.extend(_load_file(p, sheet, schema))
    return out
