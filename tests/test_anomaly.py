"""Tests for Phase 3 anomaly.py — the A5 red flags, plain asserts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations.config import Thresholds  # noqa: E402
from phase3_anomaly_reporting.anomaly import detect_anomalies  # noqa: E402

TH = Thresholds()


def _rows(*rows):
    return [dict(r) for r in rows]


def test_routine_round_amounts_are_not_flagged():
    # A portfolio of ordinary, similarly-sized round loans should not trip
    # the round-dollar rule just because none of them have cents.
    rows = _rows(
        {"description": "disbursement:L1", "amount": 2000.0, "value_date": "2024-01-01"},
        {"description": "disbursement:L2", "amount": 1000.0, "value_date": "2024-01-02"},
        {"description": "disbursement:L3", "amount": 2500.0, "value_date": "2024-01-03"},
        {"description": "disbursement:L4", "amount": 1800.0, "value_date": "2024-01-04"},
    )
    items = detect_anomalies(rows, TH)
    assert not any("Round-dollar" in i.description for i in items)


def test_large_round_amount_is_flagged():
    rows = _rows(
        {"description": "disbursement:L1", "amount": 2000.0, "value_date": "2024-01-01"},
        {"description": "disbursement:L2", "amount": 2000.0, "value_date": "2024-01-02"},
        {"description": "disbursement:L3", "amount": 2000.0, "value_date": "2024-01-03"},
        {"description": "disbursement:L4", "amount": 40000.0, "value_date": "2024-01-04", "key": "tape:x:L4:disb"},
    )
    items = detect_anomalies(rows, TH)
    hit = next(i for i in items if "Round-dollar" in i.description and "L4" in i.description)
    assert hit.evidence == ["tape:x:L4:disb"]


def test_non_round_amount_never_flagged_even_if_large():
    rows = _rows(
        {"description": "disbursement:L1", "amount": 2000.0, "value_date": "2024-01-01"},
        {"description": "disbursement:L2", "amount": 2000.0, "value_date": "2024-01-02"},
        {"description": "disbursement:L3", "amount": 2000.0, "value_date": "2024-01-03"},
        {"description": "disbursement:L4", "amount": 40123.47, "value_date": "2024-01-04"},
    )
    items = detect_anomalies(rows, TH)
    assert not any("Round-dollar" in i.description for i in items)


def test_true_duplicate_transaction_flagged():
    rows = _rows(
        {"description": "AIRTIME PURCHASE", "amount": 55.0, "value_date": "2024-02-01", "key": "bank:x:1"},
        {"description": "AIRTIME PURCHASE", "amount": 55.0, "value_date": "2024-02-01", "key": "bank:x:2"},
    )
    items = detect_anomalies(rows, TH)
    hit = next(i for i in items if "Duplicate transaction" in i.description)
    assert sorted(hit.evidence) == ["bank:x:1", "bank:x:2"]


def test_recurring_category_without_matching_amount_and_date_not_flagged():
    # Same narration recurring with different amounts/dates is normal traffic,
    # not a duplicate transaction.
    rows = _rows(
        {"description": "AIRTIME PURCHASE", "amount": 55.0, "value_date": "2024-02-01"},
        {"description": "AIRTIME PURCHASE", "amount": 60.0, "value_date": "2024-02-05"},
    )
    items = detect_anomalies(rows, TH)
    assert not any("Duplicate transaction" in i.description for i in items)


def test_same_day_round_trip_flagged():
    rows = _rows(
        {"description": "disbursement:L1", "amount": 1000.0, "value_date": "2024-03-01", "key": "tape:x:L1:disb"},
        {"description": "collections:L1", "amount": 1090.0, "value_date": "2024-03-01", "key": "tape:x:L1:coll"},
    )
    items = detect_anomalies(rows, TH)
    hit = next(i for i in items if "round-tripping" in i.description)
    assert sorted(hit.evidence) == ["tape:x:L1:coll", "tape:x:L1:disb"]


def test_normal_repayment_schedule_not_flagged_as_round_trip():
    rows = _rows(
        {"description": "disbursement:L1", "amount": 1000.0, "value_date": "2024-03-01"},
        {"description": "collections:L1", "amount": 1090.0, "value_date": "2024-05-01"},
    )
    items = detect_anomalies(rows, TH)
    assert not any("round-tripping" in i.description for i in items)


def test_volume_anomaly_carries_evidence():
    rows = _rows(
        {"description": "bank:x", "amount": 2000.0, "value_date": "2024-01-01", "key": "bank:x:1"},
        {"description": "bank:x", "amount": 2000.0, "value_date": "2024-01-02", "key": "bank:x:2"},
        {"description": "bank:x", "amount": 2000.0, "value_date": "2024-01-03", "key": "bank:x:3"},
        {"description": "bank:x", "amount": 40123.47, "value_date": "2024-01-04", "key": "bank:x:4"},
    )
    items = detect_anomalies(rows, TH)
    hit = next(i for i in items if "Volume anomaly" in i.description)
    assert hit.evidence == ["bank:x:4"]


def test_sequence_jump_flags_sudden_break_from_accounts_own_pattern():
    # The exact pattern from the feature request: 1k, 2k, 4k then a sudden
    # 1m on the same account -- even though 4k isn't 10x the portfolio
    # median here, 1m is >10x the account's own trailing average (~2.33k).
    rows = _rows(
        {"description": "x", "amount": 1000.0, "value_date": "2024-01-01", "account_ref": "acct-1"},
        {"description": "x", "amount": 2000.0, "value_date": "2024-01-02", "account_ref": "acct-1"},
        {"description": "x", "amount": 4000.0, "value_date": "2024-01-03", "account_ref": "acct-1"},
        {
            "description": "x", "amount": 1_000_000.0, "value_date": "2024-01-04",
            "account_ref": "acct-1", "key": "bank:acct-1:5",
        },
    )
    items = detect_anomalies(rows, TH)
    hit = next(i for i in items if "Pattern break" in i.description)
    assert hit.evidence == ["bank:acct-1:5"]


def test_sequence_jump_ignores_rows_without_an_account_ref():
    rows = _rows(
        {"description": "x", "amount": 1000.0, "value_date": "2024-01-01"},
        {"description": "x", "amount": 2000.0, "value_date": "2024-01-02"},
        {"description": "x", "amount": 4000.0, "value_date": "2024-01-03"},
        {"description": "x", "amount": 1_000_000.0, "value_date": "2024-01-04"},
    )
    items = detect_anomalies(rows, TH)
    assert not any("Pattern break" in i.description for i in items)


def test_sequence_jump_needs_a_real_trailing_pattern_first():
    # Only one prior transaction -- below sequence_jump_min_window (3) -- so
    # there's no established pattern to break yet.
    rows = _rows(
        {"description": "x", "amount": 1000.0, "value_date": "2024-01-01", "account_ref": "acct-1"},
        {"description": "x", "amount": 1_000_000.0, "value_date": "2024-01-02", "account_ref": "acct-1"},
    )
    items = detect_anomalies(rows, TH)
    assert not any("Pattern break" in i.description for i in items)


def test_off_hours_transaction_flagged_when_time_present():
    rows = _rows(
        {"description": "x", "amount": 500.0, "value_date": "2024-01-01T23:45:00", "key": "bank:x:1"},
    )
    items = detect_anomalies(rows, TH)
    hit = next(i for i in items if "Off-hours" in i.description)
    assert hit.evidence == ["bank:x:1"]


def test_bare_date_never_flagged_as_off_hours():
    # No time component at all -- nothing to judge off-hours from.
    rows = _rows({"description": "x", "amount": 500.0, "value_date": "2024-01-01"})
    items = detect_anomalies(rows, TH)
    assert not any("Off-hours" in i.description for i in items)


def test_daytime_transaction_not_flagged_as_off_hours():
    rows = _rows({"description": "x", "amount": 500.0, "value_date": "2024-01-01T14:30:00"})
    items = detect_anomalies(rows, TH)
    assert not any("Off-hours" in i.description for i in items)


def test_micro_splitting_flags_similar_size_same_day_same_account_group():
    rows = _rows(
        {"description": "x", "amount": 9800.0, "value_date": "2024-01-01", "account_ref": "acct-1", "key": "a:1"},
        {"description": "x", "amount": 9700.0, "value_date": "2024-01-01", "account_ref": "acct-1", "key": "a:2"},
        {"description": "x", "amount": 9900.0, "value_date": "2024-01-01", "account_ref": "acct-1", "key": "a:3"},
        # several small, unrelated transactions elsewhere keep the portfolio
        # median low, so the group's total is clearly material against it
        # rather than the median being dominated by the group itself.
        {"description": "y", "amount": 50.0, "value_date": "2024-01-01"},
        {"description": "y", "amount": 55.0, "value_date": "2024-01-02"},
        {"description": "y", "amount": 45.0, "value_date": "2024-01-03"},
        {"description": "y", "amount": 60.0, "value_date": "2024-01-04"},
        {"description": "y", "amount": 52.0, "value_date": "2024-01-05"},
    )
    items = detect_anomalies(rows, TH)
    hit = next(i for i in items if "structuring" in i.description)
    assert sorted(hit.evidence) == ["a:1", "a:2", "a:3"]


def test_micro_splitting_not_flagged_when_sizes_differ_too_much():
    rows = _rows(
        {"description": "x", "amount": 100.0, "value_date": "2024-01-01", "account_ref": "acct-1", "key": "a:1"},
        {"description": "x", "amount": 9000.0, "value_date": "2024-01-01", "account_ref": "acct-1", "key": "a:2"},
        {"description": "x", "amount": 9500.0, "value_date": "2024-01-01", "account_ref": "acct-1", "key": "a:3"},
        {"description": "y", "amount": 50.0, "value_date": "2024-01-01", "account_ref": "acct-2", "key": "b:1"},
    )
    items = detect_anomalies(rows, TH)
    assert not any("structuring" in i.description for i in items)


def test_micro_splitting_not_flagged_below_min_count():
    rows = _rows(
        {"description": "x", "amount": 9800.0, "value_date": "2024-01-01", "account_ref": "acct-1", "key": "a:1"},
        {"description": "x", "amount": 9700.0, "value_date": "2024-01-01", "account_ref": "acct-1", "key": "a:2"},
    )
    items = detect_anomalies(rows, TH)
    assert not any("structuring" in i.description for i in items)


if __name__ == "__main__":
    test_routine_round_amounts_are_not_flagged()
    test_large_round_amount_is_flagged()
    test_non_round_amount_never_flagged_even_if_large()
    test_true_duplicate_transaction_flagged()
    test_recurring_category_without_matching_amount_and_date_not_flagged()
    test_same_day_round_trip_flagged()
    test_normal_repayment_schedule_not_flagged_as_round_trip()
    test_volume_anomaly_carries_evidence()
    test_sequence_jump_flags_sudden_break_from_accounts_own_pattern()
    test_sequence_jump_ignores_rows_without_an_account_ref()
    test_sequence_jump_needs_a_real_trailing_pattern_first()
    test_off_hours_transaction_flagged_when_time_present()
    test_bare_date_never_flagged_as_off_hours()
    test_daytime_transaction_not_flagged_as_off_hours()
    test_micro_splitting_flags_similar_size_same_day_same_account_group()
    test_micro_splitting_not_flagged_when_sizes_differ_too_much()
    test_micro_splitting_not_flagged_below_min_count()
    print("anomaly tests OK")
