"""Tests for asset existence verification — ingestion (Phase 1) and the
reported-vs-registry comparison (Phase 2). Plain asserts, same style as
test_reconcile.py/test_metrics.py.
"""

import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase1_ingestion_parsing.assets import (  # noqa: E402
    load_expected_assets,
    load_registry_results,
)
from phase2_verification_engine.assets import verify_asset_existence  # noqa: E402


# ------------------------------------------------------------- ingestion


def test_load_expected_assets_detects_columns_by_keyword(tmp_path):
    df = pd.DataFrame(
        [
            {"Plate Number": "ABC-123", "Borrower": "Acme Motors", "Expected Owner": "Acme Motors Ltd"},
            {"Plate Number": "XYZ-999", "Borrower": "Beta Leasing", "Expected Owner": "Beta Leasing SA"},
        ]
    )
    path = tmp_path / "register.xlsx"
    df.to_excel(path, index=False)
    assets = load_expected_assets([path])
    assert [a["plate"] for a in assets] == ["ABC-123", "XYZ-999"]
    assert assets[0]["borrower"] == "Acme Motors"
    assert assets[0]["expected_owner"] == "Acme Motors Ltd"


def test_load_expected_assets_skips_rows_without_plate(tmp_path):
    df = pd.DataFrame([{"Plate Number": "", "Borrower": "Acme"}, {"Plate Number": "ABC-1", "Borrower": "Acme"}])
    path = tmp_path / "register.csv"
    df.to_csv(path, index=False)
    assets = load_expected_assets([path])
    assert [a["plate"] for a in assets] == ["ABC-1"]


def test_load_expected_assets_missing_plate_column_returns_nothing(tmp_path):
    df = pd.DataFrame([{"Borrower": "Acme", "Note": "no identifier column here"}])
    path = tmp_path / "register.csv"
    df.to_csv(path, index=False)
    assert load_expected_assets([path]) == []


def test_load_registry_results_reads_by_column_name(tmp_path):
    df = pd.DataFrame(
        [
            {
                "Plate": "ABC-123",
                "Status": "Found",
                "Propietario": "Acme Motors Ltd",
                "Estado": "Activo",
                "Marca": "Toyota",
                "Modelo": "Hilux",
                "Verified Date": "2026-08-20 10:00:00",
            }
        ]
    )
    path = tmp_path / "plates_peru.xlsx"
    df.to_excel(path, index=False)
    results = load_registry_results([path])
    assert set(results) == {"ABC-123"}
    r = results["ABC-123"]
    assert r["status"] == "Found"
    assert r["propietario"] == "Acme Motors Ltd"
    assert r["marca"] == "Toyota"


def test_load_registry_results_later_file_wins_for_same_plate(tmp_path):
    stale = tmp_path / "stale.xlsx"
    fresh = tmp_path / "fresh.xlsx"
    pd.DataFrame([{"Plate": "ABC-123", "Status": "No data", "Propietario": ""}]).to_excel(stale, index=False)
    pd.DataFrame([{"Plate": "ABC-123", "Status": "Found", "Propietario": "Acme Motors Ltd"}]).to_excel(
        fresh, index=False
    )
    results = load_registry_results([stale, fresh])
    assert results["ABC-123"]["status"] == "Found"


# --------------------------------------------------------- verification


def test_missing_registry_result_flags_not_checked():
    expected = [{"plate": "ABC-123", "borrower": "Acme", "expected_owner": ""}]
    exceptions = verify_asset_existence(expected, {}, run_id="r1")
    assert len(exceptions) == 1
    e = exceptions[0]
    assert e.kind == "asset"
    assert e.id == "r1:asset:not_checked:ABC-123"
    assert "unconfirmed" in e.description


def test_no_data_status_flags_check_failed():
    expected = [{"plate": "ABC-123", "borrower": "Acme", "expected_owner": ""}]
    registry = {"ABC-123": {"plate": "ABC-123", "status": "No data", "propietario": ""}}
    exceptions = verify_asset_existence(expected, registry, run_id="r1")
    assert len(exceptions) == 1
    assert exceptions[0].id == "r1:asset:check_failed:ABC-123"


def test_error_status_flags_check_failed():
    expected = [{"plate": "ABC-123", "borrower": "Acme", "expected_owner": ""}]
    registry = {"ABC-123": {"plate": "ABC-123", "status": "Error clicking search: timeout", "propietario": ""}}
    exceptions = verify_asset_existence(expected, registry, run_id="r1")
    assert len(exceptions) == 1
    assert exceptions[0].id == "r1:asset:check_failed:ABC-123"


def test_owner_mismatch_flags_exception():
    expected = [{"plate": "ABC-123", "borrower": "Acme", "expected_owner": "Acme Motors Ltd"}]
    registry = {"ABC-123": {"plate": "ABC-123", "status": "Found", "propietario": "Someone Else SA"}}
    exceptions = verify_asset_existence(expected, registry, run_id="r1")
    assert len(exceptions) == 1
    assert exceptions[0].id == "r1:asset:owner_mismatch:ABC-123"


def test_owner_match_is_case_and_whitespace_insensitive():
    expected = [{"plate": "ABC-123", "borrower": "Acme", "expected_owner": "  Acme Motors Ltd  "}]
    registry = {"ABC-123": {"plate": "ABC-123", "status": "Found", "propietario": "acme motors ltd"}}
    assert verify_asset_existence(expected, registry, run_id="r1") == []


def test_no_expected_owner_on_file_skips_owner_check():
    expected = [{"plate": "ABC-123", "borrower": "Acme", "expected_owner": ""}]
    registry = {"ABC-123": {"plate": "ABC-123", "status": "Found", "propietario": "Acme Motors Ltd"}}
    assert verify_asset_existence(expected, registry, run_id="r1") == []


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_load_expected_assets_detects_columns_by_keyword(tmp)
        test_load_expected_assets_skips_rows_without_plate(tmp)
        test_load_expected_assets_missing_plate_column_returns_nothing(tmp)
        test_load_registry_results_reads_by_column_name(tmp)
    with tempfile.TemporaryDirectory() as d:
        test_load_registry_results_later_file_wins_for_same_plate(Path(d))
    test_missing_registry_result_flags_not_checked()
    test_no_data_status_flags_check_failed()
    test_error_status_flags_check_failed()
    test_owner_mismatch_flags_exception()
    test_owner_match_is_case_and_whitespace_insensitive()
    test_no_expected_owner_on_file_skips_owner_check()
    print("assets tests OK")
