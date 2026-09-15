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
- volume/frequency anomaly (e.g. 10x normal, against the whole portfolio's median)
- per-account pattern break (e.g. 10x that *account's own* recent trailing
  average — catches a sudden jump the portfolio-wide check can miss)
- off-hours transactions (only when a row's date value carries a time)
- micro-splitting / structuring (many similar-size, same-account, same-day
  transactions summing to a material total)

Rule kinds intentionally left out: a mismatched-account-name check needs a
KYC/expected-recipient data source this app doesn't have (see
docs/Verifications_Checklist.md) -- not built rather than guessed.
"""

from __future__ import annotations

import statistics
from typing import Any
from collections.abc import Iterable

from phase0_foundations.config import Thresholds
from phase0_foundations.models import ExceptionItem


def _median(rows: list[dict[str, Any]]) -> float:
    amounts = [r.get("amount") or 0.0 for r in rows]
    return statistics.median(amounts) if amounts else 0.0


def _extract_hour(value_date: str) -> int | None:
    """Pull an hour-of-day (0-23) out of a date value, if it carries a time
    component at all (e.g. "2024-01-01 23:45:00" / "2024-01-01T23:45:00").
    Returns None for a bare date ("2024-01-01") -- there's no time to judge
    off-hours from, so the off-hours rule must never guess one."""
    import re

    m = re.search(r"[T ](\d{1,2}):\d{2}", str(value_date or ""))
    if not m:
        return None
    hour = int(m.group(1))
    return hour if 0 <= hour <= 23 else None


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

    # Red flag: per-account pattern break — an amount that dwarfs its own
    # account's recent trailing average, even when the portfolio-wide median
    # (the volume-anomaly rule above) isn't elevated enough to catch it. E.g.
    # an account with 1,000 / 2,000 / 4,000 then a sudden 1,000,000 flags
    # here even if other accounts in the same run are large enough that the
    # portfolio median hides it from the global check.
    by_account: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        acct = str(r.get("account_ref") or "").strip()
        if acct:
            by_account.setdefault(acct, []).append(r)
    for acct, acct_rows in by_account.items():
        ordered = sorted(acct_rows, key=lambda r: str(r.get("value_date") or ""))
        for i, r in enumerate(ordered):
            prior = ordered[:i]
            if len(prior) < thresholds.sequence_jump_min_window:
                continue
            trailing_avg = sum(p.get("amount") or 0.0 for p in prior) / len(prior)
            amt = r.get("amount") or 0.0
            if trailing_avg > 0 and amt > trailing_avg * thresholds.sequence_jump_multiple:
                items.append(
                    ExceptionItem(
                        id=f"{run_id}:anom:seqjump:{len(items)}" if run_id else f"anom:seqjump:{len(items)}",
                        kind="anomaly",
                        severity=0.75,
                        description=(
                            f"Pattern break on account '{acct}': {amt:,.2f} is "
                            f"{amt / trailing_avg:.1f}x its own trailing average "
                            f"{trailing_avg:,.2f} (last {len(prior)} txns) ('{r.get('description')}')"
                        ),
                        evidence=[r["key"]] if r.get("key") else [],
                    )
                )

    # Red flag: off-hours transaction. Only judged when the row's own date
    # value carries a time component -- a bare date never triggers this.
    for r in rows:
        hour = _extract_hour(r.get("value_date"))
        if hour is None:
            continue
        is_off_hours = hour >= thresholds.off_hours_start_hour or hour < thresholds.off_hours_end_hour
        if is_off_hours:
            items.append(
                ExceptionItem(
                    id=f"{run_id}:anom:offhours:{len(items)}" if run_id else f"anom:offhours:{len(items)}",
                    kind="anomaly",
                    severity=0.5,
                    description=(
                        f"Off-hours transaction at {hour:02d}:00 (outside "
                        f"{thresholds.off_hours_start_hour:02d}:00-{thresholds.off_hours_end_hour:02d}:00 "
                        f"business window): {r.get('amount') or 0.0:,.2f} ('{r.get('description')}')"
                    ),
                    evidence=[r["key"]] if r.get("key") else [],
                )
            )

    # Red flag: micro-splitting / structuring — several similar-size
    # transactions on the same account and day, summing to a material total.
    # A classic pattern for staying under a per-transaction reporting/review
    # threshold.
    by_account_day: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        acct = str(r.get("account_ref") or "").strip()
        date = str(r.get("value_date") or "")[:10]  # date portion only
        if acct and date:
            by_account_day.setdefault((acct, date), []).append(r)
    for (acct, date), group in by_account_day.items():
        if len(group) < thresholds.micro_split_min_count:
            continue
        amounts = [g.get("amount") or 0.0 for g in group]
        group_med = sorted(amounts)[len(amounts) // 2]
        if group_med <= 0:
            continue
        similar = all(
            abs(a - group_med) <= group_med * thresholds.micro_split_amount_tolerance_pct for a in amounts
        )
        total = sum(amounts)
        is_material = med > 0 and total > med * thresholds.micro_split_materiality_multiple
        if similar and is_material:
            items.append(
                ExceptionItem(
                    id=f"{run_id}:anom:microsplit:{len(items)}" if run_id else f"anom:microsplit:{len(items)}",
                    kind="anomaly",
                    severity=0.85,
                    description=(
                        f"Possible structuring on account '{acct}' on {date}: {len(group)} similar-size "
                        f"transactions (~{group_med:,.2f} each) totaling {total:,.2f}"
                    ),
                    evidence=[g["key"] for g in group if g.get("key")],
                )
            )

    return items
