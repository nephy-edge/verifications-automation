"""Independent aggregation (A3 step 3, workflow record B3/calculate.py).

Computes cash totals, collections, and disbursements from normalized rows —
independently of any reported figure. **100% arithmetic in code.** This is the
"LLM never does arithmetic" guarantee: these totals are computed here and passed
to other steps as immutable inputs.
"""

from __future__ import annotations

from typing import Any, Iterable

from phase1_ingestion_parsing.ingest import DIR_IN, DIR_OUT


def calculate_aggregates(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Sum inflows/outflows from canonical rows.

    - `cash_in` / `cash_out`: direction-based totals.
    - `collections` = expected borrower inflows (money in).
    - `disbursements` = money lent out (money out).
    """
    rows = list(rows)
    cash_in = sum(r.get("amount") or 0.0 for r in rows if r.get("direction") == DIR_IN)
    cash_out = sum(r.get("amount") or 0.0 for r in rows if r.get("direction") == DIR_OUT)

    return {
        "cash_total": sum(r.get("amount") or 0.0 for r in rows),
        "cash_in": cash_in,
        "cash_out": cash_out,
        "collections": cash_in,
        "disbursements": cash_out,
        "transaction_count": len(rows),
    }
