"""Independent asset-existence verification (A2 "Asset Existence Verification").

Compares the reported side (a collateral register/loan-tape's claimed
assets, see `phase1_ingestion_parsing.assets.load_expected_assets`) against
an independent third-party registry check
(`phase1_ingestion_parsing.assets.load_registry_results`) and flags an
exception wherever the two disagree — the same shape as `reconcile.py`
flagging a figure mismatch. Deterministic rules only, no judgement.

Three findings, all `kind="asset"`:
  - not_checked: a reported asset with no registry result at all.
  - check_failed: a registry check ran but returned no usable record (the
    lookup errored, or the plate wasn't found).
  - owner_mismatch: the registry's own registered owner doesn't match the
    name on file for that asset.
"""

from __future__ import annotations

from typing import Any

from phase0_foundations.models import ExceptionItem

# Registry `status` values meaning the lookup itself produced nothing usable
# (see vehicle_plate_peru.checker.verify_plate_on_website: 'Found' | 'No
# data' | an 'Error ...' message).
_NO_RECORD_STATUSES = {"no data", ""}


def _norm(value: str) -> str:
    """Case/whitespace-insensitive comparison key for a name."""
    return " ".join(value.strip().lower().split())


def verify_asset_existence(
    expected: list[dict[str, Any]],
    registry_results: dict[str, dict[str, Any]],
    run_id: str = "",
) -> list[ExceptionItem]:
    exceptions: list[ExceptionItem] = []
    tag = f"{run_id}:asset" if run_id else "asset"

    for asset in expected:
        plate = asset["plate"]
        result = registry_results.get(plate)

        if result is None:
            exceptions.append(
                ExceptionItem(
                    id=f"{tag}:not_checked:{plate}",
                    kind="asset",
                    severity=0.9,
                    description=(
                        f"Asset {plate} ({asset.get('borrower') or 'unknown borrower'}) is on the "
                        "reported register but has no registry check on file — existence unconfirmed."
                    ),
                    evidence=[f"expected:{plate}"],
                )
            )
            continue

        status = result.get("status", "").strip().lower()
        has_owner = bool(result.get("propietario"))
        if status.startswith("error") or (not has_owner and status in _NO_RECORD_STATUSES):
            exceptions.append(
                ExceptionItem(
                    id=f"{tag}:check_failed:{plate}",
                    kind="asset",
                    severity=0.7,
                    description=(
                        f"Registry check for asset {plate} did not return a usable record "
                        f"(status: '{result.get('status') or 'no data'}') — needs a manual re-check."
                    ),
                    evidence=[f"registry:{plate}"],
                )
            )
            continue

        expected_owner = asset.get("expected_owner", "")
        registered_owner = result.get("propietario", "")
        if expected_owner and registered_owner and _norm(expected_owner) != _norm(registered_owner):
            exceptions.append(
                ExceptionItem(
                    id=f"{tag}:owner_mismatch:{plate}",
                    kind="asset",
                    severity=0.85,
                    description=(
                        f"Asset {plate}: registry owner '{registered_owner}' does not match the "
                        f"expected owner '{expected_owner}' on file."
                    ),
                    evidence=[f"expected:{plate}", f"registry:{plate}"],
                )
            )

    return exceptions
