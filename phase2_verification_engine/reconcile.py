"""Baseline reconciliation against reported figures (A3 step 4, B3/reconcile.py).

Compares independently calculated totals to reported (loan-tape) figures and
flags exceptions when the B3 thresholds are breached. All thresholds come from
config (single source of truth). Deterministic only.
"""

from __future__ import annotations

from typing import Any

from phase0_foundations.config import Thresholds
from phase0_foundations.models import ExceptionItem


def _pct(reported: float, calculated: float) -> float:
    """Relative variance, guarding against divide-by-zero."""
    if calculated == 0:
        return 0.0 if reported == 0 else float("inf")
    return abs(reported - calculated) / abs(calculated)


def reconcile(
    calculated: dict[str, Any],
    reported: dict[str, Any],
    thresholds: Thresholds,
    run_id: str = "",
) -> list[ExceptionItem]:
    """Return exception items for any threshold breach.

    Implements the B3 formulas verbatim:
      collections_variance  = abs(reported - calculated)/calculated        > 2%  -> flag
      disbursement_variance = abs(reported - calculated)                   >$100 or >5% -> investigate
      cash_balance_variance = abs(ledger - statement)/statement            > 1% -> escalate
    """
    exceptions: list[ExceptionItem] = []

    calc_col = calculated.get("collections", 0.0)
    rep_col = reported.get("collections", 0.0)
    col_var = _pct(rep_col, calc_col)
    if col_var > thresholds.collections_variance:
        exceptions.append(
            ExceptionItem(
                id=f"{run_id}:recon:collections" if run_id else "recon:collections",
                kind="reconciliation",
                severity=min(1.0, col_var),
                description=(
                    f"Collections variance {col_var:.1%} exceeds {thresholds.collections_variance:.0%} "
                    f"(reported={rep_col:,.2f}, calculated={calc_col:,.2f})"
                ),
            )
        )

    calc_disb = calculated.get("disbursements", 0.0)
    rep_disb = reported.get("disbursements", 0.0)
    disb_var = abs(rep_disb - calc_disb)
    disb_pct = _pct(rep_disb, calc_disb)
    if disb_var > thresholds.disbursement_abs or disb_pct > thresholds.disbursement_pct:
        exceptions.append(
            ExceptionItem(
                id=f"{run_id}:recon:disbursements" if run_id else "recon:disbursements",
                kind="reconciliation",
                severity=min(1.0, disb_pct),
                description=(
                    f"Disbursement variance {disb_var:,.2f} (${thresholds.disbursement_abs:,.0f} abs)"
                    f" or {disb_pct:.1%} (> {thresholds.disbursement_pct:.0%}) "
                    f"(reported={rep_disb:,.2f}, calculated={calc_disb:,.2f})"
                ),
            )
        )

    calc_cash = calculated.get("cash_total", 0.0)
    rep_cash = reported.get("cash_total", 0.0)
    cash_var = _pct(rep_cash, calc_cash)
    if cash_var > thresholds.cash_balance_variance:
        exceptions.append(
            ExceptionItem(
                id=f"{run_id}:recon:cash" if run_id else "recon:cash",
                kind="reconciliation",
                severity=min(1.0, cash_var),
                description=(
                    f"Cash balance variance {cash_var:.1%} exceeds {thresholds.cash_balance_variance:.0%} "
                    f"(ledger={rep_cash:,.2f}, statement={calc_cash:,.2f})"
                ),
            )
        )

    return exceptions
