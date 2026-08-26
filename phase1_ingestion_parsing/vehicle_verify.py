"""Vehicle verification via a pluggable registry API.

A faster, CAPTCHA-free alternative to the browser-based plate checker
(`vehicle_plate_peru/checker.py`). Instead of driving Selenium against a
public registry page and pausing for image challenges, it asks a paid
third-party registry API (Verifik, api.verifik.co) for the same record.

Design is deliberately decoupled so the section is testable end-to-end with
no credentials:

- Each country owns its own primary endpoint, its own field map (translating
  the country's response field names onto the one canonical set), and any
  optional extra inputs/endpoints the primary lookup cannot satisfy.
- Every response is flattened recursively (nested JSON, arbitrary depth,
  first-occurrence-wins keyed on the leaf field name) before the field map is
  applied, because each country nests its response differently.
- A `VehicleClient` protocol is the seam: the tab runs against
  `MockVehicleClient` by default (returns canned nested responses so the
  flatten/field-map/Match bucketing can be exercised with no API token). To go
  live, set `VERIFIK_TOKEN` in `.env`/Streamlit secrets and point
  `make_client()` at `VerifikClient`.

The canonical record returned by any client is a flat dict with the fields in
`CANONICAL_FIELDS`; the caller never sees country-specific raw shapes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

# Canonical column set every country's response is mapped onto. `plate` is the
# lookup key; the rest are the attributes surfaced to the user.
CANONICAL_FIELDS = [
    "plate",
    "brand",
    "model",
    "year",
    "vin",
    "owner",
    "color",
    "status",
]

# Country codes the section understands. `code` is the UI-facing selector
# value; `name` the human label.
_COUNTRY_ORDER = ["PE", "MX", "CO", "CL", "AR", "BR", "EC"]


@dataclass(frozen=True)
class CountryConfig:
    code: str
    name: str
    primary_endpoint: str
    # Source field name (as it appears once flattened/leaf-keyed) -> canonical
    # field. A country may reuse a source key for a different canonical slot
    # (e.g. Colombia's RUNT "modelo" is actually the year; "linea" the model).
    field_map: dict[str, str]
    # Optional extra inputs the user must supply for this country before its
    # primary/via lookups can run (e.g. Colombia's RUNT is keyed to the owner's
    # document type + number, not just the plate). Shown only when non-empty.
    extra_inputs: tuple[str, ...] = ()
    # Optional extra billed endpoints for data the primary lookup can't return
    # (e.g. Chile's SOAP registration-status lookup).
    extra_endpoints: tuple[str, ...] = ()


COUNTRY_CONFIG: dict[str, CountryConfig] = {
    code: _cfg
    for code, _cfg in [
        (
            "PE",
            CountryConfig(
                code="PE",
                name="Peru",
                primary_endpoint="vehiculo/placa",
                field_map={
                    "placa": "plate",
                    "marca": "brand",
                    "modelo": "model",
                    "ano": "year",
                    "anio": "year",
                    "vin": "vin",
                    "nserie": "vin",
                    "propietario": "owner",
                    "color": "color",
                    "estado": "status",
                },
            ),
        ),
        (
            "MX",
            CountryConfig(
                code="MX",
                name="Mexico",
                primary_endpoint="vehiculo/placa",
                field_map={
                    "placa": "plate",
                    "marca": "brand",
                    "modelo": "model",
                    "anio": "year",
                    "niv": "vin",
                    "propietario": "owner",
                    "color": "color",
                    "estado": "status",
                },
            ),
        ),
        (
            "CO",
            CountryConfig(
                code="CO",
                name="Colombia",
                primary_endpoint="fasecolda/values-by-plate",
                field_map={
                    "placa": "plate",
                    "marca": "brand",
                    "linea": "model",
                    "modelo": "year",
                    "vin": "vin",
                    "propietario": "owner",
                    "color": "color",
                    "estado": "status",
                },
                extra_inputs=("document_type", "document_number"),
                extra_endpoints=("runt/lookup",),
            ),
        ),
        (
            "CL",
            CountryConfig(
                code="CL",
                name="Chile",
                primary_endpoint="vehiculo/placa",
                field_map={
                    "patente": "plate",
                    "marca": "brand",
                    "modelo": "model",
                    "ano": "year",
                    "vin": "vin",
                    "propietario": "owner",
                    "color": "color",
                    "estado": "status",
                },
                extra_endpoints=("registro/estado",),
            ),
        ),
        (
            "AR",
            CountryConfig(
                code="AR",
                name="Argentina",
                primary_endpoint="vehiculo/dominio",
                field_map={
                    "dominio": "plate",
                    "marca": "brand",
                    "modelo": "model",
                    "ano": "year",
                    "vin": "vin",
                    "titular": "owner",
                    "color": "color",
                    "estado": "status",
                },
            ),
        ),
        (
            "BR",
            CountryConfig(
                code="BR",
                name="Brazil",
                primary_endpoint="veiculo/placa",
                field_map={
                    "placa": "plate",
                    "marca": "brand",
                    "modelo": "model",
                    "ano": "year",
                    "chassi": "vin",
                    "proprietario": "owner",
                    "cor": "color",
                    "situacao": "status",
                },
            ),
        ),
        (
            "EC",
            CountryConfig(
                code="EC",
                name="Ecuador",
                primary_endpoint="vehiculo/placa",
                field_map={
                    "placa": "plate",
                    "marca": "brand",
                    "modelo": "model",
                    "ano": "year",
                    "vin": "vin",
                    "propietario": "owner",
                    "color": "color",
                    "estado": "status",
                },
            ),
        ),
    ]
}


def _clean_str(value: Any) -> str:
    """Cell/leaf value to a stripped string; None/NaN become '' not 'nan'."""
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN
        return ""
    return str(value).strip()


def _leaf_key(key: str) -> str:
    """Lowercase and strip non-alphanumerics from a JSON key for matching."""
    return "".join(ch for ch in key.strip().lower() if ch.isalnum())


def flatten_json(node: Any) -> dict[str, str]:
    """Recursively flatten nested JSON into a leaf-field map.

    Keys are normalized leaf names (`_leaf_key`), values become strings, and
    a duplicate field resolves to the *shallowest* (outermost) occurrence —
    ties broken by first-seen. This is the "first-occurrence-wins" rule that
    holds per country even when a response nests the same field at several
    depths (e.g. an outer `marca` plus one inside a wrapper object).
    """
    out: dict[str, tuple[int, int, str]] = {}
    _counter = {"n": 0}

    def walk(node: Any, depth: int) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key = _leaf_key(key)
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
                else:
                    text = _clean_str(value)
                    if not text:
                        continue
                    if key not in out or out[key][0] > depth:
                        out[key] = (depth, _counter["n"], text)
                        _counter["n"] += 1
        elif isinstance(node, list):
            for item in node:
                walk(item, depth)

    walk(node, 0)

    return {key: data[2] for key, data in sorted(out.items(), key=lambda kv: (kv[1][0], kv[1][1]))}


def apply_field_map(flat: dict[str, str], cfg: CountryConfig) -> dict[str, str]:
    """Map flattened leaf keys onto the canonical field set."""
    mapped = {field: "" for field in CANONICAL_FIELDS}
    for source_key, canonical in cfg.field_map.items():
        if source_key in flat and flat[source_key] and not mapped[canonical]:
            mapped[canonical] = flat[source_key]
    return mapped


class VehicleClient(Protocol):
    """Seam between the tab and any registry backend."""

    def lookup(
        self,
        country: str,
        plate: str,
        extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Return one canonical record dict (see CANONICAL_FIELDS)."""
        ...


class MockVehicleClient:
    """Canned client: returns plausible nested responses per country.

    Used by default so the section is fully exercisable (flatten, field maps,
    Match/Partial/Mismatch bucketing) without any registry credential. Each
    response is deliberately nested differently + uses that country's own
    field names, exactly what the real API returns.
    """

    _SAMPLES: dict[str, dict[str, Any]] = {
        "PE": {
            "success": True,
            "vehiculo": {
                "placa": "{PLATE}",
                "marca": "Toyota",
                "modelo": "Corolla",
                "anio": "2021",
                "propietario": "Maria Perez",
                "color": "Blanco",
                "estado": "activo",
            },
        },
        "MX": {
            "ok": True,
            "data": {"placa": "{PLATE}", "marca": "Nissan", "niv": "3N1AB7AP3LY123456", "anio": "2020"},
        },
        "CO": {
            "runt": {
                "placa": "{PLATE}",
                "marca": "Renault",
                "linea": "Logan",
                "modelo": "2019",
                "propietario": "Carlos Gomez",
            }
        },
        "CL": {"registro": {"patente": "{PLATE}", "marca": "Chevrolet", "modelo": "Sail", "ano": "2018"}},
        "AR": {"dominio": {"dominio": "{PLATE}", "marca": "Volkswagen", "modelo": "Gol", "titular": "Ana Diaz"}},
        "BR": {"veiculo": {"placa": "{PLATE}", "marca": "Fiat", "modelo": "Uno", "ano": "2017"}},
        "EC": {"vehiculo": {"placa": "{PLATE}", "marca": "Hyundai", "modelo": "Accent", "ano": "2016"}},
    }

    def lookup(self, country: str, plate: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
        cfg = COUNTRY_CONFIG[country]
        sample = self._SAMPLES.get(country, {})
        flat = flatten_json(_deep_replace(sample, "{PLATE}", plate))
        return apply_field_map(flat, cfg)


class VerifikClient:
    """Real Verifik registry client.

    READ BEFORE ENABLING: the live contract (exact request method/body, header
    name or expected response shape per endpoint in `COUNTRY_CONFIG`) must be
    confirmed against api.verifik.co before this is used in anger — the mock
    response shapes above are illustrative, not the real wire format.

    Plumbed but intentionally not wired-up: the tab defaults to
    `MockVehicleClient` so nothing here needs a token. To go live:
      1. Set `VERIFIK_TOKEN` (Bearer token) in `verifications-automation/.env`
         or Streamlit secrets.
      2. Fill `_call`'s request against the real endpoint + confirm the
         response->canonical mapping (flatten + `apply_field_map` already do
         the heavy lifting once the JSON comes back as nested data).
      3. Flip `make_client()` to return `VerifikClient()`.

    Left as a self-documenting scaffold on purpose: guessing the wire format
    would ship a broken integration, so the endpoint call is the only part
    kept open.
    """

    BASE_URL = os.getenv("VERIFIK_BASE_URL", "https://api.verifik.co").rstrip("/")
    TOKEN = os.getenv("VERIFIK_TOKEN", "")

    def lookup(
        self,
        country: str,
        plate: str,
        extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.TOKEN:
            raise RuntimeError(
                "VERIFIK_TOKEN is not configured — set it in the app's .env/secrets "
                "to use the live registry API."
            )
        cfg = COUNTRY_CONFIG[country]
        response = self._call(cfg, plate, extra or {})
        flat = flatten_json(response)
        return apply_field_map(flat, cfg)

    def _call(self, cfg: CountryConfig, plate: str, extra: dict[str, str]) -> Any:
        # Scaffold: `cfg.primary_endpoint`, `plate`, and `extra` are all we
        # know for sure. The exact request envelope + how to unwrap the nested
        # response belongs here once verified against the live API.
        raise NotImplementedError(
            f"VerifikClient._call not yet implemented for {cfg.code} — fill in the "
            f"real request/response mapping for endpoint '{cfg.primary_endpoint}', "
            "then this returns the nested JSON for flatten_json() to handle."
        )


def _deep_replace(node: Any, needle: str, replacement: str) -> Any:
    """Recursively substitute `needle` with `replacement` in str leaves."""
    if isinstance(node, dict):
        return {k: _deep_replace(v, needle, replacement) for k, v in node.items()}
    if isinstance(node, list):
        return [_deep_replace(v, needle, replacement) for v in node]
    if isinstance(node, str):
        return node.replace(needle, replacement)
    return node


# Flip this to `VerifikClient()` to go live (see VerifikClient docstring).
def make_client() -> VehicleClient:
    return MockVehicleClient()


# ------------------------------------------------------------- cross-checking
# Bucketed comparison of the API's returned Brand+Model against an "expected
# vehicle" column, if the user supplied one. String containment in either
# direction counts as an agreement (partial), the same tolerance as the
# owner/asset cross-check in `verify_asset_existence`.
MATCH = "Match"
PARTIAL = "Partial Match"
MISMATCH = "Mismatch"
NO_DATA = "No data"


def _norm(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


def _matches(left: str, right: str) -> bool:
    """True if neither side is empty and one contains the other (either dir)."""
    if not left or not right:
        return False
    return left in right or right in left


def classify_match(expected_brand: str, expected_model: str, brand: str, model: str) -> str:
    """Bucket an API result against an expected vehicle description.

    Returns one of `MATCH` / `PARTIAL` / `MISMATCH` / `NO_DATA`.
    """
    if not (brand and model):
        return NO_DATA
    eb, em = _norm(expected_brand), _norm(expected_model)
    b, m = _norm(brand), _norm(model)
    if eb and em and _matches(eb, b) and _matches(em, m):
        return MATCH
    if (eb and _matches(eb, b)) or (em and _matches(em, m)):
        return PARTIAL
    if eb or em:
        return MISMATCH
    return NO_DATA


def cross_check(
    expected_brand: str,
    expected_model: str,
    result: dict[str, str],
) -> str:
    """Classify one API result against the expected vehicle; NO_DATA otherwise."""
    return classify_match(expected_brand, expected_model, result.get("brand", ""), result.get("model", ""))


def classify_expected(expected: str, brand: str, model: str) -> str:
    """Classify an API result against a single free-text expected description.

    The "expected vehicle" column in an upload is one string (e.g. "Renault
    Logan"), not separate Brand/Model cells, so match against the combined
    returned description: full containment in either direction is a `MATCH`,
    any shared word is `PARTIAL`, other matches `MISMATCH`, and an empty
    returned description is `NO_DATA`.
    """
    if not (brand and model):
        return NO_DATA
    returned_tokens = [t for t in (_norm(brand) + " " + _norm(model)).split() if t]
    expected_clean = _norm(expected)
    if not expected_clean:
        return MISMATCH if returned_tokens else NO_DATA
    returned = "".join(returned_tokens)
    # Full containment either direction -> Match.
    if expected_clean in returned or returned in expected_clean:
        return MATCH
    # Any single returned token contained in the expected description -> partial.
    if any(tok and tok in expected_clean for tok in returned_tokens):
        return PARTIAL
    return MISMATCH


def bucket_status(result: dict[str, str]) -> str:
    """Found / Not found / Error label for a canonical record."""
    return result.get("status", "").strip() or ("Found" if result.get("brand") or result.get("owner") else "No data")


def verified_download_df(
    rows: list[dict[str, Any]],
    expected_col: str | None = None,
) -> "list[dict[str, Any]]":
    """Rows re-keyed for the Excel/verification sheet, in display column order.

    The first column is always the plate; remaining canonical fields follow,
    then the verification verdict + timestamp. `expected_col`, when set, is the
    user's chosen 'expected vehicle' source column carried through on each row.
    """
    out = []
    for row in rows:
        out.append(
            {
                "Plate": row.get("plate", ""),
                "Brand": row.get("brand", ""),
                "Model": row.get("model", ""),
                "Year": row.get("year", ""),
                "VIN": row.get("vin", ""),
                "Owner": row.get("owner", ""),
                "Color": row.get("color", ""),
                "Status": row.get("status", ""),
                "Verdict": row.get("verdict", ""),
                "Expected vehicle": expected_col if expected_col else "",
                "Verified Date": row.get("verified_date", ""),
            }
        )
    return out
