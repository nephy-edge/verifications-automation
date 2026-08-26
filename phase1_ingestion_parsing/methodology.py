"""Encoded verification methodology (workflow record A5).

The judgement of the business owners, written down as data so it can be applied
deterministically and remain auditable. A competitor can buy the same model;
they cannot replicate this methodology.
"""

from __future__ import annotations


class Methodology:
    """Definitions and tolerances used throughout the verification phases."""

    # Collections Verification: reported loan-tape repayments are reconciled
    # against payment inflows (mobile money wallets, commercial banks, virtual
    # banks). "Collections" = expected inflows from borrowers.
    collections_source = "payment inflows (mobile money, commercial banks, virtual banks)"
    collections_from_tape_field = "collections"

    # Disbursements Verification: reported loan-tape disbursements are verified
    # against outbound cash flows. "Disbursements" = money lent out.
    disbursements_source = "outbound cash flows"
    disbursements_from_tape_field = "disbursements"

    # Cash balance verification: ledger balances are substantiated against bank
    # statements as of the cut-off date.
    cash_balance_source = "bank statements as of cut-off date"
    cash_balance_from_ledger_field = "ledger_balance"

    # Sample vs population: full-population verification is preferred over
    # sampling for audit independence.
    population_policy = "full-population verification preferred over sampling"
