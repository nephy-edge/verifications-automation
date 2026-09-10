"""Independent aggregation (A3 step 3, workflow record B3/calculate.py).

Computes cash totals, collections, and disbursements from normalized rows —
independently of any reported figure. **100% arithmetic in code.** This is the
"LLM never does arithmetic" guarantee: these totals are computed here and passed
to other steps as immutable inputs.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Iterable

from phase0_foundations.fx import FXConfig, convert_to_base
from phase1_ingestion_parsing.ingest import DIR_IN, DIR_OUT


def calculate_aggregates(rows: Iterable[dict[str, Any]], fx: FXConfig | None = None) -> dict[str, Any]:
    """Sum inflows/outflows from canonical rows.

    - `cash_in` / `cash_out`: direction-based totals.
    - `collections` = expected borrower inflows (money in).
    - `disbursements` = money lent out (money out).

    ``fx`` is optional (SOP 1: bi-weekly cash tracking's FX-normalization
    gap). When omitted, behavior is identical to before FX support existed —
    a raw sum regardless of currency. When passed, each row's `amount` is
    converted to `fx.base_currency` first via its `currency` field; a
    currency present but not in `fx.rates` is left unconverted and its code
    surfaced in the returned `unmapped_currencies` list rather than silently
    mis-summed.
    """
    rows = list(rows)
    currencies_seen: set[str] = set()
    unmapped_currencies: set[str] = set()

    def _amt(r: dict[str, Any]) -> float:
        amount = r.get("amount") or 0.0
        if fx is None:
            return amount
        currency = r.get("currency") or ""
        if currency:
            currencies_seen.add(currency.strip().upper())
        converted, unmapped = convert_to_base(amount, currency, fx)
        if unmapped:
            unmapped_currencies.add(unmapped)
        return converted

    cash_in = sum(_amt(r) for r in rows if r.get("direction") == DIR_IN)
    cash_out = sum(_amt(r) for r in rows if r.get("direction") == DIR_OUT)

    result = {
        "cash_total": sum(_amt(r) for r in rows),
        "cash_in": cash_in,
        "cash_out": cash_out,
        "collections": cash_in,
        "disbursements": cash_out,
        "transaction_count": len(rows),
    }
    if fx is not None:
        result["fx_base_currency"] = fx.base_currency
        result["currencies_seen"] = sorted(currencies_seen)
        if unmapped_currencies:
            result["unmapped_currencies"] = sorted(unmapped_currencies)
    return result
