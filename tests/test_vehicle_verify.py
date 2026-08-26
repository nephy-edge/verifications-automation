"""Tests for the pluggable vehicle-verification module (phase1, registry API).

Covers the three things that carry the real logic: recursive response
flattening (first/shallowest-occurrence-wins), per-country field-map
application (e.g. Colombia's RUNT `modelo` is the year, `linea` the model),
and Match/Partial/Mismatch/No-data bucketing against an expected vehicle.
Plain asserts, same style as test_assets.py/test_reconcile.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase1_ingestion_parsing.vehicle_verify import (  # noqa: E402
    COUNTRY_CONFIG,
    MATCH,
    MISMATCH,
    NO_DATA,
    PARTIAL,
    classify_expected,
    flatten_json,
    make_client,
    verified_download_df,
)


# ------------------------------------------------------------- flattening


def test_flatten_picks_shallowest_occurrence():
    flat = flatten_json({"a": {"b": "x", "c": [{"b": "Y"}]}, "b": "first"})
    assert flat["b"] == "first"  # outer wins over nested
    assert "c" not in flat       # containers emit no leaf key


def test_flatten_unwraps_nested_wrappers():
    flat = flatten_json({"vehiculo": {"marca": "Toyota", "detalle": {"modelo": "Corolla", "marca": "Nissan"}}})
    assert flat["marca"] == "Toyota"
    assert flat["modelo"] == "Corolla"


# ------------------------------------------------------- per-country field maps


def test_colombia_maps_modelo_as_year_and_linea_as_model():
    result = make_client().lookup("CO", "LIVE1", {"document_type": "CC", "document_number": "123"})
    assert result["plate"] == "LIVE1"
    assert result["year"] == "2019"      # RUNT "modelo" is actually the year
    assert result["model"] == "Logan"    # "linea" is the model
    assert result["owner"] == "Carlos Gomez"


def test_every_country_has_a_primary_endpoint_and_plate_roundtrip():
    for code, cfg in COUNTRY_CONFIG.items():
        assert cfg.primary_endpoint
        result = make_client().lookup(code, "LIVE1")
        assert result["plate"] == "LIVE1"


def test_extra_inputs_only_declared_where_needed():
    assert COUNTRY_CONFIG["CO"].extra_inputs == ("document_type", "document_number")
    assert COUNTRY_CONFIG["PE"].extra_inputs == ()


# --------------------------------------------------------------- bucketing


def test_match_requires_brand_and_model_agreement():
    assert classify_expected("Toyota Corolla", "Toyota", "Corolla") == MATCH
    assert classify_expected("Toyota Corolla", "Toyota", "Corolla GLX") == MATCH  # containment


def test_partial_when_only_one_attribute_agrees():
    assert classify_expected("Renault Clio", "Renault", "Logan") == PARTIAL  # brand shared only


def test_mismatch_against_a_different_vehicle():
    assert classify_expected("Ford Fiesta", "Toyota", "Corolla") == MISMATCH


def test_no_data_when_nothing_returned():
    assert classify_expected("Toyota Corolla", "", "") == NO_DATA


# --------------------------------------------------------------- download df


def test_download_df_has_display_columns():
    df = verified_download_df(
        [{"plate": "A", "brand": "B", "verdict": "Match", "verified_date": "d"}],
        "expected",
    )
    for key in ("Plate", "Brand", "Model", "Verdict", "Expected vehicle", "Verified Date"):
        assert key in df[0]
