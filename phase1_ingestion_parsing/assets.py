"""Asset-existence ingestion (A2 "Asset Existence Verification").

Two independent sources feed this check, mirroring the reported/calculated
split used everywhere else in the workflow:

- **Reported**: a collateral register or loan-tape extract claiming which
  assets (e.g. vehicle plates) are pledged, by whom, and to whom they
  should be registered. Column names vary by exporter, so
  `load_expected_assets` detects them by keyword the same way
  `ingest.py.detect_bank_schema` does for statements.
- **Independent**: a third-party registry's own findings, produced
  separately (see `vehicle_plate_peru/checker.py`, which drives Peru's
  plate registry and writes one row per plate). `load_registry_results`
  reads that file by column name only — there is no code coupling to how
  the check was performed, the same way a PDF bank statement is read
  without importing the bank's own tooling.

Neither loader does any judgement; a plate present in one source and not
the other is left for `phase2_verification_engine.assets.verify_asset_existence`
to flag.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import pandas as pd


def _clean_str(value: Any) -> str:
    """Cell value to a stripped string; None/NaN (pandas' empty-cell reads
    for both CSV and Excel) become "" rather than the literal text 'nan'."""
    if value is None or (isinstance(value, float) and value != value):  # NaN != NaN
        return ""
    return str(value).strip()


def _find_col(columns: Any, *needles: str) -> str | None:
    """First column whose lowercased name contains any of `needles`, in order."""
    lower_map = {str(c).strip().lower(): c for c in columns}
    for needle in needles:
        for lc, orig in lower_map.items():
            if needle in lc:
                return orig
    return None


def _read_tabular(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".csv", ".txt"):
        return pd.read_csv(path)
    return pd.read_excel(path)


def load_expected_assets(files: Sequence[str | Path]) -> list[dict[str, Any]]:
    """Reported side: one row per pledged asset a register/tape claims.

    Detects a plate/registration/asset-id column, a borrower column, and an
    expected-owner column by keyword. Rows with no recognizable identifier
    are dropped; the caller has nothing to compare them against anyway.
    """
    out: list[dict[str, Any]] = []
    for f in files:
        path = Path(f)
        if not path.exists():
            continue
        try:
            df = _read_tabular(path)
        except Exception:
            continue
        plate_col = _find_col(df.columns, "plate", "registration", "asset_id", "asset id")
        if not plate_col:
            continue
        borrower_col = _find_col(df.columns, "borrower")
        owner_col = _find_col(df.columns, "expected_owner", "expected owner", "owner", "propietario")
        for record in df.to_dict("records"):
            plate = _clean_str(record.get(plate_col))
            if not plate:
                continue
            out.append(
                {
                    "plate": plate,
                    "borrower": _clean_str(record.get(borrower_col)) if borrower_col else "",
                    "expected_owner": _clean_str(record.get(owner_col)) if owner_col else "",
                    "source_file": path.name,
                }
            )
    return out


# Column-role keywords for the registry-result headers `vehicle_plate_peru
# .checker.save_results_to_excel` writes (Status, Propietario, Estado, Marca,
# Modelo, Verified Date, ...). Matched by keyword, not position, so a result
# file from a different registry/tool still works as long as it names its
# columns sensibly.
_RESULT_FIELDS: dict[str, tuple[str, ...]] = {
    "status": ("status",),
    "propietario": ("propietario", "owner"),
    "estado": ("estado",),
    "marca": ("marca", "make"),
    "modelo": ("modelo", "model"),
    "verified_date": ("verified date", "verified_date"),
}


def load_registry_results(files: Sequence[str | Path]) -> dict[str, dict[str, Any]]:
    """Independent side: the registry's own findings, keyed by plate.

    Column A of the source file is taken as the plate; later files (or later
    rows for the same plate) overwrite earlier ones, so re-checking a plate
    supersedes its stale result rather than duplicating it.
    """
    results: dict[str, dict[str, Any]] = {}
    for f in files:
        path = Path(f)
        if not path.exists():
            continue
        try:
            df = pd.read_excel(path) if path.suffix.lower() != ".csv" else pd.read_csv(path)
        except Exception:
            continue
        if df.empty or len(df.columns) == 0:
            continue
        plate_col = df.columns[0]
        field_cols = {key: _find_col(df.columns, *needles) for key, needles in _RESULT_FIELDS.items()}
        for record in df.to_dict("records"):
            plate = _clean_str(record.get(plate_col))
            if not plate:
                continue
            results[plate] = {
                "plate": plate,
                **{
                    key: (_clean_str(record.get(col)) if col else "")
                    for key, col in field_cols.items()
                },
            }
    return results
