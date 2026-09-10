"""Tests for Phase 3 rank.py — severity ranking, forensic routing, and
mandatory (severity-independent) fraud referral."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations.config import Thresholds  # noqa: E402
from phase0_foundations.models import ExceptionItem  # noqa: E402
from phase3_anomaly_reporting.rank import (  # noqa: E402
    forensic_route,
    mandatory_fraud_referrals,
    rank_exceptions,
)

TH = Thresholds()


def _exc(id_, severity, kind="anomaly"):
    return ExceptionItem(id=id_, kind=kind, severity=severity, description="x")


def test_rank_exceptions_normalizes_to_0_1_and_sorts_descending():
    items = [_exc("a", 0.2), _exc("b", 0.8), _exc("c", 0.5)]
    ranked = rank_exceptions(items, TH)
    assert [e.id for e in ranked] == ["b", "c", "a"]
    assert ranked[0].severity == 1.0
    assert ranked[-1].severity == 0.0


def test_forensic_route_only_returns_items_at_or_above_threshold():
    items = [_exc("a", 0.9), _exc("b", 0.5)]
    assert [e.id for e in forensic_route(items, TH)] == ["a"]


def test_mandatory_fraud_referrals_selects_by_id_marker_not_severity():
    # A round-trip finding with LOW severity (post-normalization, or just
    # raw) must still be referred -- mandatory referral is about the kind of
    # finding, not where its severity landed.
    items = [
        _exc("run1:anom:roundtrip:0", severity=0.1),
        _exc("run1:anom:microsplit:1", severity=0.2),
        _exc("run1:anom:seqjump:2", severity=0.3),
        _exc("run1:anom:round:3", severity=0.99),  # high severity, not intent-based -> not mandatory
        _exc("run1:recon:collections", severity=0.99, kind="reconciliation"),
    ]
    referred_ids = {e.id for e in mandatory_fraud_referrals(items)}
    assert referred_ids == {"run1:anom:roundtrip:0", "run1:anom:microsplit:1", "run1:anom:seqjump:2"}


def test_mandatory_fraud_referrals_survives_relative_severity_normalization():
    # The scenario this exists for: a round-trip (raw severity 0.8) shares a
    # run with something scored higher, so relative normalization could push
    # it below the forensic threshold even though it's still a round-trip.
    items = [_exc("run1:anom:roundtrip:0", severity=0.8), _exc("run1:anom:round:1", severity=1.0)]
    ranked = rank_exceptions(items, TH)
    roundtrip = next(e for e in ranked if e.id == "run1:anom:roundtrip:0")
    assert roundtrip.severity < TH.anomaly_score_high  # normalized below the forensic cut
    # ...but mandatory_fraud_referrals still catches it regardless.
    assert roundtrip.id in {e.id for e in mandatory_fraud_referrals(ranked)}


def test_mandatory_fraud_referrals_empty_when_no_intent_based_findings():
    items = [_exc("run1:anom:round:0", 0.9), _exc("run1:recon:collections", 0.9, kind="reconciliation")]
    assert mandatory_fraud_referrals(items) == []


if __name__ == "__main__":
    test_rank_exceptions_normalizes_to_0_1_and_sorts_descending()
    test_forensic_route_only_returns_items_at_or_above_threshold()
    test_mandatory_fraud_referrals_selects_by_id_marker_not_severity()
    test_mandatory_fraud_referrals_survives_relative_severity_normalization()
    test_mandatory_fraud_referrals_empty_when_no_intent_based_findings()
    print("rank tests OK")
