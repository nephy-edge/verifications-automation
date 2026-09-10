"""Tests for Phase 1 ingest.py — real per-loan tape parsing, plain asserts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from phase1_ingestion_parsing.ingest import DIR_IN, DIR_OUT, normalize_loan_tape_row, read_raw_table  # noqa: E402


def test_read_raw_table_keeps_the_files_own_column_names(tmp_path):
    """The Transaction Matching tab's column picker needs a file's *real*
    columns (e.g. "Transaction Code"), not the canonical schema
    `load_and_normalize` maps everything into — that schema doesn't have a
    slot for a transaction-code column at all, it would just be dropped."""
    path = tmp_path / "ledger.xlsx"
    # A title row above the real header, same shape as a real bank export
    # that broke the Transaction Matching tab before the column-picker fix.
    pd.DataFrame(
        [
            ["Some Bank - Customer Ledger", None, None],
            ["Date", "Transaction Code", "Amount"],
            ["2026-06-02", "TXN-001", 100.0],
        ]
    ).to_excel(path, index=False, header=False)

    df = read_raw_table(path)
    assert list(df.columns) == ["Date", "Transaction Code", "Amount"]
    assert df.iloc[0]["Transaction Code"] == "TXN-001"


def test_paid_loan_emits_disbursement_and_collections():
    row = {
        "loan_id": "L1",
        "begin_date": "2024-01-01",
        "closure_date": "2024-02-01",
        "company_due_date": "2024-02-05",
        "principal_amount": 1000.0,
        "total_loan_amount": 1090.0,
        "principal_outstanding": 0.0,
        "interest_outstanding": 0.0,
        "fee_outstanding": 0.0,
        "penalty_outstanding": 0.0,
    }
    events = normalize_loan_tape_row(row, account_ref="tape")
    kinds = {e["description"]: e for e in events}
    assert "disbursement:L1" in kinds and kinds["disbursement:L1"]["amount"] == 1000.0
    assert kinds["disbursement:L1"]["direction"] == DIR_OUT
    assert kinds["disbursement:L1"]["value_date"] == "2024-01-01"
    assert "collections:L1" in kinds and kinds["collections:L1"]["amount"] == 1090.0
    assert kinds["collections:L1"]["direction"] == DIR_IN
    assert kinds["collections:L1"]["value_date"] == "2024-02-01"  # closure_date preferred


def test_defaulted_loan_with_nothing_collected_emits_no_collections_row():
    row = {
        "loan_id": "L2",
        "begin_date": "2021-02-24",
        "closure_date": "",
        "company_due_date": "2022-02-10",
        "principal_amount": 1000.0,
        "total_loan_amount": 1090.0,
        "principal_outstanding": 1000.0,
        "interest_outstanding": 0.0,
        "fee_outstanding": 90.0,
        "penalty_outstanding": 1090.0,
    }
    events = normalize_loan_tape_row(row, account_ref="tape")
    descriptions = [e["description"] for e in events]
    assert "disbursement:L2" in descriptions
    assert "collections:L2" not in descriptions


def test_active_loan_emits_partial_collections_dated_at_due_date():
    row = {
        "loan_id": "L3",
        "begin_date": "2026-06-20",
        "closure_date": "",
        "company_due_date": "2026-08-27",
        "principal_amount": 4206.9,
        "total_loan_amount": 4632.76,
        "principal_outstanding": 1051.71,
        "interest_outstanding": 0.0,
        "fee_outstanding": 0.0,
        "penalty_outstanding": 0.0,
    }
    events = normalize_loan_tape_row(row, account_ref="tape")
    coll = next(e for e in events if e["description"] == "collections:L3")
    assert round(coll["amount"], 2) == 3581.05
    assert coll["value_date"] == "2026-08-27"  # falls back to company_due_date


def test_fee_and_penalty_outstanding_use_singular_column_names_by_default():
    """Regression test: normalize_loan_tape_row() used to look for
    fees_outstanding/penalties_outstanding (plural), which don't exist in
    real data (e.g. leasy uses fee_outstanding/penalty_outstanding,
    singular) -- so those terms were always silently 0, overstating
    collected-to-date for every borrower. A row that only has the singular
    columns should now be counted correctly."""
    row = {
        "loan_id": "L4",
        "begin_date": "2024-01-01",
        "closure_date": "2024-02-01",
        "principal_amount": 1000.0,
        "total_loan_amount": 1000.0,
        "principal_outstanding": 0.0,
        "interest_outstanding": 0.0,
        "fee_outstanding": 100.0,
        "penalty_outstanding": 50.0,
    }
    events = normalize_loan_tape_row(row, account_ref="tape")
    coll = next(e for e in events if e["description"] == "collections:L4")
    # outstanding = 0 + 0 + 100 + 50 = 150 -> paid = 1000 - 150 = 850, not 1000
    assert coll["amount"] == 850.0


def test_comma_formatted_money_strings_parse_correctly():
    """Some Redshift sources store money columns as thousands-separated text
    (confirmed live on exitus.principal, e.g. "6,959,813.28"). Before this fix
    float() rejected the embedded comma and silently zeroed the value -- same
    bug class as the fee/penalty singular-vs-plural mismatch."""
    row = {
        "loan_id": "L7",
        "begin_date": "2024-05-01",
        "closure_date": "2024-06-01",
        "principal_amount": "6,959,813.28",
        "total_loan_amount": "6,959,813.28",
        "principal_outstanding": "1,000,000.00",
        "interest_outstanding": "0",
        "fee_outstanding": "0",
        "penalty_outstanding": "0",
    }
    events = normalize_loan_tape_row(row, account_ref="tape")
    disb = next(e for e in events if e["description"] == "disbursement:L7")
    coll = next(e for e in events if e["description"] == "collections:L7")
    assert disb["amount"] == 6_959_813.28
    assert coll["amount"] == 6_959_813.28 - 1_000_000.00


def test_negative_outstanding_component_is_floored_not_summed_raw():
    """An outstanding-balance column that is a live negative in the source
    (confirmed on autocheck__ci/ug's principal_out et al., e.g. -968,118.0)
    must not make total outstanding negative or inflate the derived paid-down
    amount -- floor each component at 0 instead of summing the raw sign."""
    row = {
        "loan_id": "L8",
        "begin_date": "2024-07-01",
        "closure_date": "2024-08-01",
        "principal_amount": 1000.0,
        "total_loan_amount": 1000.0,
        "principal_outstanding": "-500.0",  # negative sign convention in source
        "interest_outstanding": 0.0,
        "fee_outstanding": 0.0,
        "penalty_outstanding": 0.0,
    }
    events = normalize_loan_tape_row(row, account_ref="tape")
    coll = next(e for e in events if e["description"] == "collections:L8")
    # outstanding floored to 0 (not -500), so paid = 1000 - 0 = 1000, not 1500
    assert coll["amount"] == 1000.0


def test_negative_sign_borrower_uses_abs_not_floor():
    """Some borrowers' sources use a live negative-sign accounting convention
    on effectively every row (confirmed 2026-09-09: lendmn/lendmn_revolving's
    `principal`, autocheck__ci/ug's `principal_out` et al.) rather than an
    occasional anomaly. Flooring these at 0 would silently zero out real
    debt; negative_sign=True must recover the real magnitude via abs()
    instead."""
    row = {
        "loan_id": "L9",
        "begin_date": "2024-09-01",
        "closure_date": "",
        "company_due_date": "2024-10-01",
        "principal_amount": -1000.0,
        "total_loan_amount": -1000.0,
        "principal_outstanding": "-600.0",
        "interest_outstanding": 0.0,
        "fee_outstanding": 0.0,
        "penalty_outstanding": 0.0,
    }
    events = normalize_loan_tape_row(row, account_ref="tape", negative_sign=True)
    disb = next(e for e in events if e["description"] == "disbursement:L9")
    coll = next(e for e in events if e["description"] == "collections:L9")
    assert disb["amount"] == 1000.0
    # outstanding = abs(-600) = 600 -> paid = 1000 - 600 = 400, not 1000 (floor-at-0 case)
    assert coll["amount"] == 400.0


def test_default_borrower_still_floors_negative_outstanding_at_zero():
    """Without negative_sign=True (the default), a negative outstanding
    component is still treated as "nothing owed on this component" -- the
    conservative reading for a borrower with no confirmed sign convention."""
    row = {
        "loan_id": "L10",
        "begin_date": "2024-09-01",
        "closure_date": "",
        "company_due_date": "2024-10-01",
        "principal_amount": 1000.0,
        "total_loan_amount": 1000.0,
        "principal_outstanding": "-600.0",
        "interest_outstanding": 0.0,
        "fee_outstanding": 0.0,
        "penalty_outstanding": 0.0,
    }
    events = normalize_loan_tape_row(row, account_ref="tape")
    coll = next(e for e in events if e["description"] == "collections:L10")
    assert coll["amount"] == 1000.0  # outstanding floored to 0, paid = full total


def test_normalize_loan_tape_row_resolves_a_borrower_specific_column_map():
    """A borrower whose real columns don't match the canonical names at all
    (see docs/loan_tape_column_survey.md) should still normalize correctly
    once given its column map -- this is the config.yaml
    loan_tape_columns.overrides mechanism, exercised directly here without
    going through Config/YAML."""
    row = {
        "loanid": "L5",  # mkopa/sary-style: no underscore
        "begin_date": "2024-03-01",
        "closure_date": "2024-04-01",
        "principal_amount": 500.0,
        "total_loan_amount": 500.0,
        "principal_outstanding": 0.0,
        "interest_outstanding": 0.0,
        "fee_outstanding": 0.0,
        "penalty_outstanding": 0.0,
    }
    column_map = {"loan_id": "loanid"}  # everything else defaults to identity
    events = normalize_loan_tape_row(row, account_ref="tape", columns=column_map)
    keys = {e["key"] for e in events}
    assert "tape:tape:L5:disb" in keys
    assert "tape:tape:L5:coll" in keys


def test_normalize_loan_tape_row_without_mapped_column_treats_field_as_absent():
    """If a borrower's column map doesn't cover a field the row also lacks
    under its canonical name, that figure is silently 0/absent -- same
    documented behavior as an unmapped field in config.yaml, not a crash."""
    row = {"loan_id": "L6", "principal_amount": 1000.0}  # no begin_date at all
    events = normalize_loan_tape_row(row, account_ref="tape", columns={})
    assert events == []  # no begin_date -> no disbursement event emitted


if __name__ == "__main__":
    test_paid_loan_emits_disbursement_and_collections()
    test_defaulted_loan_with_nothing_collected_emits_no_collections_row()
    test_active_loan_emits_partial_collections_dated_at_due_date()
    test_fee_and_penalty_outstanding_use_singular_column_names_by_default()
    test_comma_formatted_money_strings_parse_correctly()
    test_negative_outstanding_component_is_floored_not_summed_raw()
    test_negative_sign_borrower_uses_abs_not_floor()
    test_default_borrower_still_floors_negative_outstanding_at_zero()
    test_normalize_loan_tape_row_resolves_a_borrower_specific_column_map()
    test_normalize_loan_tape_row_without_mapped_column_treats_field_as_absent()
    print("ingest tests OK")
