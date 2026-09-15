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

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from phase0_foundations.config import Config, VehicleRegistryConfig, load_config

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
# value; `name` the human label. Order controls the dropdown. BO/CR/PY/US are
# countries Verifik documents vehicle endpoints for but this codebase has not
# yet confirmed against a real live response (see each CountryConfig): they
# show in the selector but the live client refuses them until confirmed.


def _registry_cfg() -> VehicleRegistryConfig | None:
    """Load the vehicle_registry section from config.yaml (None if unavailable).

    Read lazily on first use so the module stays importable/tests stay green
    whether or not a `config.yaml` is on disk; env overrides still take
    precedence over whatever this returns.
    """
    try:
        return load_config().vehicle_registry
    except FileNotFoundError:
        return None


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


COUNTRY_CONFIG: dict[str, CountryConfig] = {
    "PE": CountryConfig(
        code="PE",
        name="Peru",
        # Confirmed against Verifik's own docs (docs.verifik.co/vehicle-validation/
        # peru/peruvian-vehicle): GET /v2/pe/vehiculo/placa?plate=... returns
        # English field names, nested under "data" — no owner/color/status field
        # at all (that would need a separate, unconfirmed SUNARP/"Full ID"
        # product). `chasisSerial` is the closest thing to a VIN this endpoint has.
        primary_endpoint="vehiculo/placa",
        field_map={
            "plate": "plate",
            "brand": "brand",
            "model": "model",
            "year": "year",
            "chasisserial": "vin",
        },
    ),
    "MX": CountryConfig(
        code="MX",
        name="Mexico",
        # UNCONFIRMED: GET /v2/mx/vehiculo/placa consistently returned
        # "500 InternalServerError: 501" for three different test plates —
        # either the path is wrong or this product isn't on this account's
        # plan. field_map below is the best publicly-documented guess,
        # not verified against a real response; VerifikClient._call
        # refuses this country until a real 200 is actually observed.
        primary_endpoint="vehiculo/placa",
        field_map={
            "plate": "plate",
            "make": "brand",
            "model": "model",
            "year": "year",
            "vin": "vin",
            "owner": "owner",
            "status": "status",
        },
    ),
    "CO": CountryConfig(
        code="CO",
        name="Colombia",
        # Confirmed with a real, authenticated live call (not docs): GET
        # /v2/co/runt/vehicle-by-plate-simplified?plate=&documentType=&
        # documentNumber= -> {"data": {"plate": ..., "vehicle": {"marca",
        # "linea", "modelo" (this is the YEAR, not a model — RUNT quirk),
        # "color", "estadoDelVehiculo", "noVin", ...}}}. No owner field —
        # RUNT ties ownership to the document number you queried with,
        # doesn't hand back a name.
        primary_endpoint="runt/vehicle-by-plate-simplified",
        field_map={
            "plate": "plate",
            "marca": "brand",
            "linea": "model",
            "modelo": "year",
            "novin": "vin",
            "color": "color",
            "estadodelvehiculo": "status",
        },
        extra_inputs=("document_type", "document_number"),
    ),
    "CL": CountryConfig(
        code="CL",
        name="Chile",
        # Confirmed with a real, authenticated live call: GET /v2/cl/
        # vehicle?plate=... -> {"data": {"plate","mark" (not "brand"!),
        # "model","year","chasisNumber","color","owner","rut","fines",
        # "type",...}}. The only country confirmed to actually return an
        # owner name (a real live test returned a genuine company name).
        primary_endpoint="vehicle",
        field_map={
            "plate": "plate",
            "mark": "brand",
            "model": "model",
            "year": "year",
            "chasisnumber": "vin",
            "owner": "owner",
            "color": "color",
        },
    ),
    "AR": CountryConfig(
        code="AR",
        name="Argentina",
        # Confirmed with a real, authenticated live call (twice, two
        # plates): GET /v2/ar/vehicle?plate=... -> {"data": {"plate",
        # "brand","model","year","type","version",...}}. No owner, no
        # color, no VIN/chassis field on this endpoint at all.
        primary_endpoint="vehicle",
        field_map={
            "plate": "plate",
            "brand": "brand",
            "model": "model",
            "year": "year",
        },
    ),
    "BR": CountryConfig(
        code="BR",
        name="Brazil",
        # Confirmed with a real, authenticated live call: GET /v2/br/
        # vehicle?plate=... -> {"data": {"plate","brand","model",
        # "modelYear" (the real year field — confirmed by the API's own
        # error text on a bad plate: "..._does_not_have_modelYear"),
        # "color",...}}. No owner field; "chassis" appears in Verifik's
        # docs but was empty/absent on the one real success observed, so
        # left unmapped rather than guessed.
        primary_endpoint="vehicle",
        field_map={
            "plate": "plate",
            "brand": "brand",
            "model": "model",
            "modelyear": "year",
            "color": "color",
        },
    ),
    "EC": CountryConfig(
        code="EC",
        name="Ecuador",
        # UNCONFIRMED: GET /v2/ec/vehiculo/placa/multas?plate=... is a
        # real, reachable endpoint (auth accepted, clean structured 404
        # for an unregistered plate) but no test plate returned an
        # actual 200, so the success shape below is sourced from public
        # docs only, not a real observed response. It's also a *fines*
        # endpoint, not a general vehicle-info one — no owner field, and
        # brand/model come bundled in one "model" string rather than
        # separate fields. VerifikClient._call refuses this country
        # until a real 200 is actually observed.
        primary_endpoint="vehiculo/placa/multas",
        field_map={
            "plate": "plate",
            "model": "model",
            "year": "year",
            "status": "status",
        },
    ),
    "BO": CountryConfig(
        code="BO",
        name="Bolivia",
        # UNCONFIRMED: Verifik documents GET /v2/bo/vehicle?plate=...
        # (Bolivia - Vehicle Information) and v2/bo/vehicle-soat. No
        # real live response has been observed here, so this field_map
        # is a best-effort guess consistent with the other South
        # American "vehicle" endpoints (plate/brand/model/year/color).
        # VerifikClient._call refuses this country until a real 200.
        primary_endpoint="vehicle",
        field_map={
            "plate": "plate",
            "brand": "brand",
            "model": "model",
            "year": "year",
            "color": "color",
        },
    ),
    "CR": CountryConfig(
        code="CR",
        name="Costa Rica",
        # UNCONFIRMED: Verifik documents GET /v2/cr/vehicle?plate=...
        # (Costa Rica - Vehicle Information). No real live response
        # observed here; best-effort field_map from the generic
        # endpoint skeleton. VerifikClient._call refuses until a real
        # 200 is observed.
        primary_endpoint="vehicle",
        field_map={
            "plate": "plate",
            "brand": "brand",
            "model": "model",
            "year": "year",
            "color": "color",
        },
    ),
    "PY": CountryConfig(
        code="PY",
        name="Paraguay",
        # UNCONFIRMED: Verifik documents GET /v2/py/vehicle?plate=...
        # (Paraguay - Vehicle Information). No real live response
        # observed here; best-effort field_map. VerifikClient._call
        # refuses until a real 200 is observed.
        primary_endpoint="vehicle",
        field_map={
            "plate": "plate",
            "brand": "brand",
            "model": "model",
            "year": "year",
        },
    ),
    "US": CountryConfig(
        code="US",
        name="United States",
        # UNCONFIRMED: Verifik documents GET /v2/usa/vehicle?plate=...
        # and v2/usa/vehicle-by-vin. US registries return VIN (a field
        # this codebase already surfaces), plus make/model/year. No real
        # live response observed here — best-effort field_map.
        # VerifikClient._call refuses until a real 200 is observed.
        primary_endpoint="vehicle",
        field_map={
            "plate": "plate",
            "make": "brand",
            "model": "model",
            "year": "year",
            "vin": "vin",
            "color": "color",
        },
    ),
}


def _clean_str(value: Any) -> str:
    """Cell/leaf value to a stripped string; None/NaN become '' not 'nan'."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _norm(value: str) -> str:
    """Lowercase and strip non-alphanumerics from a string for matching."""
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


def clean_plate(plate: str) -> str:
    """Normalize a plate for lookup: uppercase, alphanumeric only.

    Verifik's own docs are explicit that a plate must be submitted "without
    spaces or points" — a plate typed/uploaded as "ABC-123", "abc 123", or
    "ABC.123" all resolve to the same lookup key "ABC123" so dashes, dots,
    and spaces never cause a spurious miss against the live registry.
    """
    return "".join(ch for ch in plate.strip().upper() if ch.isalnum())


def flatten_json(node: Any) -> dict[str, str]:
    """Recursively flatten nested JSON into a leaf-field map.

    Keys are normalized leaf names (`_norm`), values become strings, and
    a duplicate field resolves to the *shallowest* (outermost) occurrence —
    ties broken by first-seen. This is the "first-occurrence-wins" rule that
    holds per country even when a response nests the same field at several
    depths (e.g. an outer `marca` plus one inside a wrapper object).
    """
    out: dict[str, tuple[int, str]] = {}

    def walk(node: Any, depth: int) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key = _norm(key)
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
                else:
                    text = _clean_str(value)
                    if not text:
                        continue
                    if key not in out or out[key][0] > depth:
                        out[key] = (depth, text)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth)

    walk(node, 0)

    return {key: text for key, (_depth, text) in out.items()}


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
        # Matches the real Verifik response shape (see CountryConfig.field_map's
        # comment) so the mock exercises the same no-owner/no-color/no-status gap
        # the live client actually has, rather than a friendlier fiction.
        "PE": {
            "data": {
                "plate": "{PLATE}",
                "use": "PARTICULAR",
                "type": "AUTOMOVIL",
                "brand": "Toyota",
                "model": "Corolla",
                "year": "2021",
                "engineSerial": "HR123456789J",
                "chasisSerial": "JT2AE09W5M0123456",
                "seats": "5",
                "validFormat": True,
                "serial": "JT2AE09W5M0123456",
            },
            "signature": {"dateTime": "August 1, 2022 5:23 PM", "message": "Certified by Verifik.co"},
        },
        # MX and EC are still unconfirmed against a real response (see their
        # CountryConfig comments) — these two samples remain best-effort guesses.
        "MX": {
            "ok": True,
            "data": {"plate": "{PLATE}", "make": "Nissan", "model": "Sentra", "year": "2020"},
        },
        # CO/CL/AR/BR below mirror the real shape confirmed by an actual live
        # call (see each CountryConfig comment), not a guess.
        "CO": {
            "data": {
                "plate": "{PLATE}",
                "documentType": "CC",
                "documentNumber": "123456789",
                "vehicle": {
                    "marca": "Renault",
                    "linea": "Logan",
                    "modelo": "2019",
                    "color": "Gris",
                    "estadoDelVehiculo": "ACTIVO",
                    "noVin": "8A1BSCD1234567890",
                },
            }
        },
        "CL": {
            "data": {
                "plate": "{PLATE}",
                "mark": "Chevrolet",
                "model": "Sail",
                "year": "2018",
                "chasisNumber": "9BWZZZ377VT004251",
                "owner": "Transportes Andina Ltda.",
                "color": "Gris",
                "fines": "NO POSEE MULTAS",
            },
            "signature": {"dateTime": "August 1, 2022 5:23 PM", "message": "Certified by Verifik.co"},
        },
        "AR": {
            "data": {
                "plate": "{PLATE}",
                "brand": "Volkswagen",
                "model": "Gol",
                "year": "2015",
                "type": "AUTOMOVIL",
            },
            "signature": {"dateTime": "August 1, 2022 5:23 PM", "message": "Certified by Verifik.co"},
        },
        "BR": {
            "data": {
                "plate": "{PLATE}",
                "brand": "Fiat",
                "model": "Uno",
                "modelYear": "2017",
                "color": "Branco",
            },
            "signature": {"dateTime": "August 1, 2022 5:23 PM", "message": "Certified by Verifik.co"},
        },
        "EC": {"data": {"plate": "{PLATE}", "model": "Accent 1.6", "year": "2016", "status": "ASIGNADO"}},
        # BO/CR/PY/US below are best-effort guesses for country coverage added
        # while no real plate is available — their success shape is UNCONFIRMED
        # (see each CountryConfig). The mock still lets the flatten/field-map
        # and Match bucketing be exercised for these codes.
        "BO": {
            "data": {"plate": "{PLATE}", "brand": "Toyota", "model": "Hilux", "year": "2019", "color": "Blanco"},
            "signature": {"dateTime": "August 1, 2022 5:23 PM", "message": "Certified by Verifik.co"},
        },
        "CR": {
            "data": {"plate": "{PLATE}", "brand": "Hyundai", "model": "Tucson", "year": "2020", "color": "Gris"},
            "signature": {"dateTime": "August 1, 2022 5:23 PM", "message": "Certified by Verifik.co"},
        },
        "PY": {
            "data": {"plate": "{PLATE}", "brand": "Chevrolet", "model": "Onix", "year": "2021"},
            "signature": {"dateTime": "August 1, 2022 5:23 PM", "message": "Certified by Verifik.co"},
        },
        "US": {
            "data": {
                "plate": "{PLATE}",
                "make": "Ford",
                "model": "F-150",
                "year": "2022",
                "vin": "1FTFW1E53NFA12345",
                "color": "Black",
            },
            "signature": {"dateTime": "August 1, 2022 5:23 PM", "message": "Certified by Verifik.co"},
        },
    }

    def lookup(self, country: str, plate: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
        cfg = COUNTRY_CONFIG[country]
        sample = self._SAMPLES.get(country, {})
        flat = flatten_json(_deep_replace(sample, "{PLATE}", clean_plate(plate)))
        return apply_field_map(flat, cfg)


class VerifikClient:
    """Real Verifik registry client.

    READ BEFORE ENABLING FOR A NEW COUNTRY: the contract (exact request
    method/params, header, response shape) must be confirmed with a real,
    authenticated call before `_call` handles it — public docs alone aren't
    trustworthy enough here (docs.verifik.co's own pages contradicted each
    other on Mexico's response shape during development — one fabricated an
    "owner" field that a second, independently-rendered source flatly denied
    existed). Every country below marked "confirmed" was checked against a
    real live response, not just documentation.

    Confirmed via a real live call:
      PE: GET /v2/pe/vehiculo/placa?plate=      -> data.{plate,brand,model,year,chasisSerial,...}, no owner/color/status
      CO: GET /v2/co/runt/vehicle-by-plate-simplified?plate=&documentType=&documentNumber= -> data.vehicle.{marca,linea,modelo(=year),color,estadoDelVehiculo,noVin}, no owner
      CL: GET /v2/cl/vehicle?plate=              -> data.{plate,mark,model,year,chasisNumber,owner,color,...} — the only one with real owner data
      AR: GET /v2/ar/vehicle?plate=              -> data.{plate,brand,model,year,type,version}, no owner/color/vin
      BR: GET /v2/br/vehicle?plate=              -> data.{plate,brand,model,modelYear,color,...}, no owner

    Still unconfirmed (raise NotImplementedError rather than guess):
      MX: same path pattern as PE, but 3 different test plates all returned a
          consistent "500 InternalServerError: 501" — either the path is wrong
          or this product isn't enabled on this account's plan.
      EC: endpoint is reachable and auth is accepted (a clean structured 404
          came back for an unregistered plate), but no test plate ever
          produced a real 200 to confirm the success shape against.
      BO/CR/PY/US: Verifik documents vehicle endpoints for these (v2/bo/vehicle,
          v2/cr/vehicle, v2/py/vehicle, v2/usa/vehicle) but no real live
          response has been observed here yet — confirm the success shape with
          a real plate before adding them to _DEFAULT_CONFIRMED_PATHS.
    """

    # Read at call time, not at class-definition time: `make_client()` is
    # typically called well after `load_dotenv()` runs, but the *module*
    # import (and so this class body) happens before it — a class-level
    # `os.getenv(...)` here would freeze empty even with a real token on disk.
    def __init__(self, registry: VehicleRegistryConfig | None = None):
        # Prefer a config explicitly injected by the caller (e.g. the app's
        # already-loaded `CFG.vehicle_registry`) over re-scanning config.yaml
        # from the CWD, so the client never drifts from the app's settings.
        self._injected_registry = registry

    def _reg(self) -> VehicleRegistryConfig | None:
        return self._injected_registry or _registry_cfg()

    @property
    def _base_url(self) -> str:
        cfg = self._reg()
        default = cfg.base_url if cfg else "https://api.verifik.co"
        env_name = cfg.base_url_env if cfg else "VERIFIK_BASE_URL"
        return os.getenv(env_name, default).rstrip("/")

    @property
    def _token(self) -> str:
        cfg = self._reg()
        env_name = cfg.token_env if cfg else "VERIFIK_TOKEN"
        return os.getenv(env_name, "")

    @property
    def _timeout(self) -> int:
        cfg = self._reg()
        return cfg.timeout_seconds if cfg else 15

    def lookup(
        self,
        country: str,
        plate: str,
        extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self._token:
            cfg = self._reg()
            env_name = cfg.token_env if cfg else "VERIFIK_TOKEN"
            raise RuntimeError(
                f"{env_name} is not configured — set it in the app's .env/secrets "
                "to use the live registry API."
            )
        cfg = COUNTRY_CONFIG[country]
        response = self._call(cfg, clean_plate(plate), extra or {})
        flat = flatten_json(response)
        return apply_field_map(flat, cfg)

    # Country code -> (URL path segment after /v2/<cc>/, extra query params to
    # forward beyond `plate`). Only countries confirmed via a real live call.
    # Defaults live here; `config.yaml` (`vehicle_registry.confirmed_paths`)
    # overrides/extends them at runtime so endpoint changes are config-only.
    _DEFAULT_CONFIRMED_PATHS: dict[str, tuple[str, tuple[str, ...]]] = {
        "PE": ("vehiculo/placa", ()),
        "CO": ("runt/vehicle-by-plate-simplified", ("document_type", "document_number")),
        "CL": ("vehicle", ()),
        "AR": ("vehicle", ()),
        "BR": ("vehicle", ()),
    }
    # extra_inputs keys use snake_case; Verifik's own query params are camelCase.
    _DEFAULT_PARAM_RENAME = {"document_type": "documentType", "document_number": "documentNumber"}

    def _confirmed_paths(self) -> dict[str, tuple[str, tuple[str, ...]]]:
        cfg = self._reg()
        paths = dict(self._DEFAULT_CONFIRMED_PATHS)
        if cfg:
            for code, vp in cfg.confirmed_paths.items():
                if not vp.path:
                    continue
                paths[code] = (vp.path, tuple(vp.extra))
        return paths

    def _param_rename(self) -> dict[str, str]:
        cfg = self._reg()
        if cfg:
            return dict(cfg.param_rename) or dict(self._DEFAULT_PARAM_RENAME)
        return dict(self._DEFAULT_PARAM_RENAME)

    def _call(self, cfg: CountryConfig, plate: str, extra: dict[str, str]) -> Any:
        confirmed = self._confirmed_paths()
        if cfg.code not in confirmed:
            raise NotImplementedError(
                f"VerifikClient._call not yet confirmed for {cfg.code} — see the VerifikClient "
                "docstring for what was actually tried and why it isn't wired up live."
            )
        path, extra_keys = confirmed[cfg.code]
        param_rename = self._param_rename()
        params = {"plate": plate}
        for key in extra_keys:
            if extra.get(key):
                params[param_rename.get(key, key)] = extra[key]
        url = f"{self._base_url}/v2/{cfg.code.lower()}/{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"Verifik API returned {e.code}: {body}") from e


def _deep_replace(node: Any, needle: str, replacement: str) -> Any:
    """Recursively substitute `needle` with `replacement` in str leaves."""
    if isinstance(node, dict):
        return {k: _deep_replace(v, needle, replacement) for k, v in node.items()}
    if isinstance(node, list):
        return [_deep_replace(v, needle, replacement) for v in node]
    if isinstance(node, str):
        return node.replace(needle, replacement)
    return node


def make_client(config: Config | None = None) -> VehicleClient:
    """Live client when a token is configured, mock otherwise.

    PE/CO/CL/AR/BR are confirmed against a real live call (see VerifikClient
    docstring); MX, EC, BO, CR, PY and US will raise a clear
    NotImplementedError per lookup once live, rather than silently returning
    mock data next to a real token.

    `config` (optional) lets a caller hand over the already-loaded `Config` so
    the live client reuses the app's `vehicle_registry` settings instead of
    re-scanning config.yaml from the CWD.
    """
    registry = config.vehicle_registry if config else None
    client = VerifikClient(registry)
    if client._token:
        return client
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


def verified_download_df(
    rows: list[dict[str, Any]],
    expected_col: str | None = None,
) -> list[dict[str, Any]]:
    """Rows re-keyed for the Excel/verification sheet, in display column order.

    The first column is always the plate; remaining canonical fields follow,
    then the verification verdict + timestamp. `expected_col` is truthy when
    the caller wants an 'Expected vehicle' column included, sourced from each
    row's own `expected_vehicle` value (not the column name itself).
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
                "Expected vehicle": row.get("expected_vehicle", "") if expected_col else "",
                "Verified Date": row.get("verified_date", ""),
            }
        )
    return out
