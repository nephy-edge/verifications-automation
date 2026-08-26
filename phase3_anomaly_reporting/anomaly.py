"""Rules-based anomaly & fraud detection (A3 step 5, workflow record A5 red flags).

Deterministic rules only (D1 band). Each rule contributes a severity in 0..1;
the LLM never computes these — it only explains (see anomaly_prompt.txt). Each
exception carries `evidence`: the canonical record `key`(s) that produced it,
so a reviewer can trace the finding back to its source row(s) (B2: "digital
working papers with attached supporting evidence").

Red flags encoded from A5:
- true duplicate transactions (same description, amount, and date)
- round-dollar amounts that are also material for this portfolio
- same-day disbursement + collection on the same loan (rapid round-tripping)
- volume/frequency anomaly (e.g. 10x normal)
"""

from __future__ import annotations

from typing import Any, Iterable

from phase0_foundations.config import Thresholds
from phase0_foundations.models import ExceptionItem


def _median(rows: list[dict[str, Any]]) -> float:
    amounts = sorted(r.get("amount") or 0.0 for r in rows)
    return amounts[len(amounts) // 2] if amounts else 0.0


def _roundness(amount: float) -> float:
    """0 (not a round figure) .. 1 (very round). Round to 1000 outranks round
    to 100; a whole-dollar amount with no other structure isn't round at all
    — virtually every financial-system amount has no cents, so "no cents"
    alone is not a meaningful signal."""
    if amount <= 0:
        return 0.0
    if amount % 1000 == 0:
        return 1.0
    if amount % 100 == 0:
        return 0.5
    return 0.0


def detect_anomalies(
    rows: Iterable[dict[str, Any]],
    thresholds: Thresholds,
    run_id: str = "",
) -> list[ExceptionItem]:
    rows = list(rows)
    items: list[ExceptionItem] = []
    med = _median(rows)

    # Red flag: true duplicate transactions — same description, amount, and
    # date. Keyed narrowly on purpose: a bank/mobile feed's narration
    # category (e.g. "AIRTIME PURCHASE") recurring on its own is normal;
    # the same transaction (same text, same amount, same date) appearing
    # more than once is the actual red flag.
    seen: dict[tuple[str, float, str], list[dict[str, Any]]] = {}
    for r in rows:
        key = (str(r.get("description") or ""), round(r.get("amount") or 0.0, 2), str(r.get("value_date") or ""))
        seen.setdefault(key, []).append(r)
    for (desc, amt, date), group in seen.items():
        if len(group) > 1:
            items.append(
                ExceptionItem(
                    id=f"{run_id}:anom:dup:{len(items)}" if run_id else f"anom:dup:{len(items)}",
                    kind="anomaly",
                    severity=0.5,
                    description=f"Duplicate transaction: '{desc}' {amt:,.2f} on {date} appears {len(group)}x",
                    evidence=[r["key"] for r in group if r.get("key")],
                )
            )

    # Red flag: round-dollar amounts, gated on materiality. A routine round
    # product amount (a 2,000 salary advance) isn't a falsification signal;
    # a round amount that's also several times the typical transaction size
    # for this portfolio is worth a second look.
    for r in rows:
        amt = r.get("amount") or 0.0
        roundness = _roundness(amt)
        is_material = med > 0 and amt > med * thresholds.round_dollar_materiality_multiple
        if roundness and is_material:
            severity = min(1.0, 0.2 + 0.4 * roundness + 0.2 * min(1.0, amt / (med * thresholds.transaction_volume_multiple)))
            items.append(
                ExceptionItem(
                    id=f"{run_id}:anom:round:{len(items)}" if run_id else f"anom:round:{len(items)}",
                    kind="anomaly",
                    severity=round(severity, 4),
                    description=(
                        f"Round-dollar amount {amt:,.2f} is {amt / med:.1f}x the median {med:,.2f} "
                        f"('{r.get('description')}')"
                    ),
                    evidence=[r["key"]] if r.get("key") else [],
                )
            )

    # Red flag: rapid round-tripping — a disbursement and a collection on the
    # same loan landing on the same date. Only meaningful for tape-derived
    # rows, which carry the loan id in their description
    # ("disbursement:<loan_id>" / "collections:<loan_id>").
    disb_rows: dict[str, dict[str, Any]] = {}
    coll_rows: dict[str, dict[str, Any]] = {}
    for r in rows:
        desc = str(r.get("description") or "")
        if desc.startswith("disbursement:"):
            disb_rows[desc.split(":", 1)[1]] = r
        elif desc.startswith("collections:"):
            coll_rows.setdefault(desc.split(":", 1)[1], r)
    for loan_id, disb_row in disb_rows.items():
        coll_row = coll_rows.get(loan_id)
        d_date = str(disb_row.get("value_date") or "")
        c_date = str(coll_row.get("value_date") or "") if coll_row else ""
        if d_date and c_date and d_date == c_date:
            items.append(
                ExceptionItem(
                    id=f"{run_id}:anom:roundtrip:{len(items)}" if run_id else f"anom:roundtrip:{len(items)}",
                    kind="anomaly",
                    severity=0.8,
                    description=f"Same-day disbursement and collection for loan {loan_id} on {d_date} (round-tripping)",
                    evidence=[k for k in (disb_row.get("key"), coll_row.get("key")) if k],
                )
            )

    # Red flag: frequency / volume anomaly (10x median transaction proxy).
    for r in rows:
        amt = r.get("amount") or 0.0
        if med > 0 and amt > med * thresholds.transaction_volume_multiple:
            items.append(
                ExceptionItem(
                    id=f"{run_id}:anom:vol:{len(items)}" if run_id else f"anom:vol:{len(items)}",
                    kind="anomaly",
                    severity=0.6,
                    description=(
                        f"Volume anomaly: {amt:,.2f} > {thresholds.transaction_volume_multiple:,.0f}x "
                        f"median {med:,.2f} ('{r.get('description')}')"
                    ),
                    evidence=[r["key"]] if r.get("key") else [],
                )
            )

    return items
