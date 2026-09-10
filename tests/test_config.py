"""Tests for phase0_foundations/config.py's LoanTapeColumnsConfig -- the
per-borrower loan-tape column mapping (config.yaml's loan_tape_columns;
see docs/loan_tape_column_survey.md for why this exists)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations.config import (  # noqa: E402
    DEFAULT_LOAN_TAPE_COLUMNS,
    LoanTapeColumnsConfig,
)


def test_default_config_resolves_to_canonical_identity_mapping():
    cfg = LoanTapeColumnsConfig()
    assert cfg.resolve("any_borrower") == DEFAULT_LOAN_TAPE_COLUMNS
    assert cfg.resolve(None) == DEFAULT_LOAN_TAPE_COLUMNS


def test_resolve_layers_borrower_override_onto_default():
    cfg = LoanTapeColumnsConfig(overrides={"mkopa": {"loan_id": "loanid"}})
    resolved = cfg.resolve("mkopa")
    assert resolved["loan_id"] == "loanid"
    # everything else still comes from the default table
    assert resolved["begin_date"] == "begin_date"


def test_resolve_ignores_overrides_for_a_different_borrower():
    cfg = LoanTapeColumnsConfig(overrides={"mkopa": {"loan_id": "loanid"}})
    resolved = cfg.resolve("leasy")
    assert resolved["loan_id"] == "loan_id"


def test_from_dict_parses_default_and_overrides():
    cfg = LoanTapeColumnsConfig.from_dict({
        "default": {"loan_id": "loan_id"},
        "overrides": {"sary": {"loan_id": "loanid", "days_past_due": "dayspastdue"}},
    })
    resolved = cfg.resolve("sary")
    assert resolved["loan_id"] == "loanid"
    assert resolved["days_past_due"] == "dayspastdue"
    # unlisted canonical fields still resolve via DEFAULT_LOAN_TAPE_COLUMNS
    assert resolved["principal_amount"] == "principal_amount"


def test_from_dict_handles_missing_sections():
    cfg = LoanTapeColumnsConfig.from_dict(None)
    assert cfg.resolve("anyone") == DEFAULT_LOAN_TAPE_COLUMNS
    assert cfg.overrides == {}


def test_default_uses_singular_fee_and_penalty_outstanding():
    """Locks in the fix: real data (e.g. leasy) uses fee_outstanding /
    penalty_outstanding, singular -- the plural form was never real."""
    assert DEFAULT_LOAN_TAPE_COLUMNS["fee_outstanding"] == "fee_outstanding"
    assert DEFAULT_LOAN_TAPE_COLUMNS["penalty_outstanding"] == "penalty_outstanding"
    assert "fees_outstanding" not in DEFAULT_LOAN_TAPE_COLUMNS
    assert "penalties_outstanding" not in DEFAULT_LOAN_TAPE_COLUMNS


def test_uses_negative_sign_true_only_for_listed_borrowers():
    """Confirmed 2026-09-09 by sampling real rows: lendmn/lendmn_revolving's
    `principal` and autocheck__ci/ug's outstanding fields are negative on
    effectively every row -- lendmn_micro is deliberately NOT listed, since
    it sampled normally signed despite being in the same borrower family."""
    cfg = LoanTapeColumnsConfig.from_dict({
        "negative_sign_borrowers": ["lendmn", "lendmn_revolving", "autocheck__ci", "autocheck__ug"],
    })
    assert cfg.uses_negative_sign("lendmn") is True
    assert cfg.uses_negative_sign("lendmn_revolving") is True
    assert cfg.uses_negative_sign("lendmn_micro") is False
    assert cfg.uses_negative_sign("leasy") is False
    assert cfg.uses_negative_sign(None) is False


def test_uses_negative_sign_defaults_to_empty():
    cfg = LoanTapeColumnsConfig.from_dict(None)
    assert cfg.negative_sign_borrowers == set()
    assert cfg.uses_negative_sign("anyone") is False


if __name__ == "__main__":
    test_default_config_resolves_to_canonical_identity_mapping()
    test_resolve_layers_borrower_override_onto_default()
    test_resolve_ignores_overrides_for_a_different_borrower()
    test_from_dict_parses_default_and_overrides()
    test_from_dict_handles_missing_sections()
    test_default_uses_singular_fee_and_penalty_outstanding()
    test_uses_negative_sign_true_only_for_listed_borrowers()
    test_uses_negative_sign_defaults_to_empty()
    print("config tests OK")
