"""Transaction-level matched/unmatched reconciliation.

Ported from the `vehicle-verification` repo's `reconciliation.py`
(`normalize_value` / `match_records` / `create_reconciliation_report`), a
capability our own `reconcile.py` doesn't have: `reconcile.py` answers "do
the totals line up" (an aggregate variance check); this answers "which
specific transaction has no counterpart on the other side" — evidence a
reviewer can point to, not just a percentage.

Adapted rather than copied verbatim: the original operated on a raw Excel
DataFrame matched against raw PDF line-records by an arbitrary column (e.g.
a transaction/cheque code) neither source here is guaranteed to carry. This
version matches on the canonical `description` field every ingested record
(loan tape, bank, mobile money) already has via `ingest.py`/`extract.py`, so
it reuses this project's validated ingestion instead of a second raw-parsing
path. It's exact-match first, falling back to substring ("partial") matching
the same way the original did — useful when, say, a bank narration echoes a
loan id from the tape's `collections:<loan_id>` description, but rarely
verbatim.

This is best-effort by nature (like `cashmap.py`'s account matching): a real
bank narration frequently shares nothing textually with a loan-tape
description, so "unmatched" here means "no shared reference text found," not
"proven absent." Kept as an opt-in, on-demand report rather than feeding
`rank_exceptions` automatically — unlike a hard registry lookup (asset
verification) or a threshold breach (reconcile.py), a low match rate here is
exactly what most real statement pairs will show and isn't itself anomalous.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from phase0_foundations.models import ExceptionItem

_SPECIAL_CHARS_RE = re.compile(r"[^\w\s-]")


def normalize_value(
    value: Any,
    *,
    case_sensitive: bool = False,
    ignore_spaces: bool = False,
    ignore_special_chars: bool = False,
) -> str:
    """Normalize a value for matching, per the caller's chosen leniency."""
    if value is None:
        return ""
    normalized = str(value).strip()
    if not case_sensitive:
        normalized = normalized.lower()
    if ignore_spaces:
        normalized = normalized.replace(" ", "")
    if ignore_special_chars:
        normalized = _SPECIAL_CHARS_RE.sub("", normalized)
    return normalized


def match_transactions(
    reported: list[dict[str, Any]],
    independent: list[dict[str, Any]],
    *,
    reported_field: str = "description",
    independent_field: str = "description",
    partial_match: bool = True,
    **normalize_opts: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Match reported-side records to independent-side records by a text field.

    Exact match on the normalized field first; when `partial_match` is set
    (default), a still-unmatched reported record can match a still-unmatched
    independent record whose normalized value contains it (or vice versa) —
    first candidate found, not a best-of-many search.
    """
    independent_norm = [
        (i, normalize_value(r.get(independent_field), **normalize_opts), r) for i, r in enumerate(independent)
    ]
    independent_by_value: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for i, val, rec in independent_norm:
        if val:
            independent_by_value.setdefault(val, []).append((i, rec))

    matched: list[dict[str, Any]] = []
    unmatched_reported: list[dict[str, Any]] = []
    matched_independent_idx: set[int] = set()

    for rec in reported:
        val = normalize_value(rec.get(reported_field), **normalize_opts)
        if not val:
            unmatched_reported.append(rec)
            continue

        candidates = [c for c in independent_by_value.get(val, []) if c[0] not in matched_independent_idx]
        if candidates:
            idx, match_rec = candidates[0]
            matched.append({"reported": rec, "independent": match_rec, "match_key": val, "match_type": "exact"})
            matched_independent_idx.add(idx)
            continue

        matched_this = False
        if partial_match:
            for j, other_val, other_rec in independent_norm:
                if j in matched_independent_idx or not other_val:
                    continue
                if val in other_val or other_val in val:
                    matched.append(
                        {
                            "reported": rec,
                            "independent": other_rec,
                            "match_key": f"{val} ~ {other_val}",
                            "match_type": "partial",
                        }
                    )
                    matched_independent_idx.add(j)
                    matched_this = True
                    break
        if not matched_this:
            unmatched_reported.append(rec)

    unmatched_independent = [rec for i, _val, rec in independent_norm if i not in matched_independent_idx]
    return {
        "matched": matched,
        "unmatched_reported": unmatched_reported,
        "unmatched_independent": unmatched_independent,
    }


def to_exceptions(match_result: dict[str, list[dict[str, Any]]], run_id: str = "") -> list[ExceptionItem]:
    """One exception per unmatched record on either side, kind="bank_match".

    Not called automatically by the main pipeline (see module docstring) —
    for a caller that explicitly wants unmatched transactions surfaced in the
    review queue rather than only the standalone match report.
    """
    tag = f"{run_id}:match" if run_id else "match"
    exceptions: list[ExceptionItem] = []
    for rec in match_result["unmatched_reported"]:
        exceptions.append(
            ExceptionItem(
                id=f"{tag}:in_reported_only:{rec.get('key') or len(exceptions)}",
                kind="bank_match",
                severity=0.4,
                description=(
                    f"Reported transaction '{rec.get('description', '')}' has no matching "
                    "independent-side record."
                ),
                evidence=[rec["key"]] if rec.get("key") else [],
            )
        )
    for rec in match_result["unmatched_independent"]:
        exceptions.append(
            ExceptionItem(
                id=f"{tag}:in_independent_only:{rec.get('key') or len(exceptions)}",
                kind="bank_match",
                severity=0.4,
                description=(
                    f"Independent-side transaction '{rec.get('description', '')}' has no matching "
                    "reported record."
                ),
                evidence=[rec["key"]] if rec.get("key") else [],
            )
        )
    return exceptions


def build_match_report(match_result: dict[str, list[dict[str, Any]]]) -> pd.DataFrame:
    """Flatten a match result into one row per record, for an Excel export."""
    rows: list[dict[str, Any]] = []
    for m in match_result["matched"]:
        rows.append(
            {
                "Status": "Matched" if m["match_type"] == "exact" else "Partial match",
                "Match key": m["match_key"],
                "Reported description": m["reported"].get("description", ""),
                "Reported amount": m["reported"].get("amount"),
                "Independent description": m["independent"].get("description", ""),
                "Independent amount": m["independent"].get("amount"),
            }
        )
    for rec in match_result["unmatched_reported"]:
        rows.append(
            {
                "Status": "In reported only",
                "Match key": "",
                "Reported description": rec.get("description", ""),
                "Reported amount": rec.get("amount"),
                "Independent description": "",
                "Independent amount": None,
            }
        )
    for rec in match_result["unmatched_independent"]:
        rows.append(
            {
                "Status": "In independent only",
                "Match key": "",
                "Reported description": "",
                "Reported amount": None,
                "Independent description": rec.get("description", ""),
                "Independent amount": rec.get("amount"),
            }
        )
    return pd.DataFrame(rows)


_AMOUNT_CLEAN_RE = re.compile(r"[^0-9.\-]")


def parse_amount(value: Any) -> float | None:
    """Best-effort parse of a raw cell value into a float.

    Tolerant of the shapes a statement's own amount column shows up in
    before any canonical normalization: currency symbols and codes
    ("$1,234.56", "KES 500"), thousands separators, and parenthesised
    negatives ("(200.00)", the common accounting convention for a debit).
    Returns None for blank/unparseable cells rather than raising, so one
    bad cell doesn't abort a whole-column total.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None  # excludes NaN
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    elif text.startswith("-"):
        negative = True
    cleaned = _AMOUNT_CLEAN_RE.sub("", text).lstrip("-")
    if not cleaned or cleaned == ".":
        return None
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return -amount if negative else amount


def sum_amount_column(records: list[dict[str, Any]], column: str) -> tuple[float, int, int]:
    """Sum a raw column across records. Returns (total, parsed_count, skipped_count) —
    the counts let a caller flag "3 of 25 rows couldn't be read as numbers"
    rather than silently folding them into the total as zero."""
    total = 0.0
    parsed = 0
    skipped = 0
    for rec in records:
        amount = parse_amount(rec.get(column))
        if amount is None:
            skipped += 1
        else:
            total += amount
            parsed += 1
    return total, parsed, skipped


def build_generic_match_report(match_result: dict[str, list[dict[str, Any]]]) -> pd.DataFrame:
    """Flatten a match result into one row per record, keeping every column
    from both sides rather than the fixed description/amount pair
    `build_match_report` assumes — for matching on raw, arbitrary columns
    (e.g. a statement's own "Transaction Code") where there's no canonical
    schema to fall back on. Reported columns are prefixed "Reported: ",
    independent "Independent: ", so same-named columns on both sides don't
    collide."""
    rows: list[dict[str, Any]] = []
    for m in match_result["matched"]:
        row: dict[str, Any] = {
            "Status": "Matched" if m["match_type"] == "exact" else "Partial match",
            "Match key": m["match_key"],
        }
        row.update({f"Reported: {k}": v for k, v in m["reported"].items()})
        row.update({f"Independent: {k}": v for k, v in m["independent"].items()})
        rows.append(row)
    for rec in match_result["unmatched_reported"]:
        row = {"Status": "In reported only", "Match key": ""}
        row.update({f"Reported: {k}": v for k, v in rec.items()})
        rows.append(row)
    for rec in match_result["unmatched_independent"]:
        row = {"Status": "In independent only", "Match key": ""}
        row.update({f"Independent: {k}": v for k, v in rec.items()})
        rows.append(row)
    return pd.DataFrame(rows)
