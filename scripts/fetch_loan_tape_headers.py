"""Diagnostic script: print the real column names (and a sample row) the
Redshift Query API actually returns for a borrower's loan tape.

Exists because `_loan_tape_summary()` in app/streamlit_app.py reads specific
column names (principal_amount, total_loan_amount, principal_outstanding,
interest_outstanding, fee_outstanding, penalty_outstanding) and silently
treats a missing or non-numeric column as 0 rather than erroring — so a
naming mismatch between what the live API returns and what that function
expects shows up as "0" everywhere in the app's summary tiles, not as an
error. This bypasses the UI to show exactly what the API sends back.

Usage:
    python scripts/fetch_loan_tape_headers.py <borrower> [--limit N]

Reads REDSHIFT_API_URL / the api_key_env var from .env and config.yaml,
same as the app itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv  # noqa: E402

from phase0_foundations.config import load_config  # noqa: E402

load_dotenv(BASE_DIR / ".env")
CFG = load_config(BASE_DIR / "config.yaml")

API_URL = os.getenv("REDSHIFT_API_URL", CFG.services.api_url).rstrip("/")
API_KEY = os.getenv(CFG.services.api_key_env, "")

# The columns app/streamlit_app.py's _loan_tape_summary() actually reads —
# compare the real header list below against these to spot a naming mismatch.
EXPECTED_MONEY_COLS = (
    "loan_id",
    "principal_amount",
    "total_loan_amount",
    "principal_outstanding",
    "interest_outstanding",
    "fee_outstanding",
    "penalty_outstanding",
)


def api_get(path: str, timeout: float = 130) -> dict:
    if not API_KEY:
        raise RuntimeError(f"{CFG.services.api_key_env} is not set (check your .env)")
    req = urllib.request.Request(f"{API_URL}{path}", headers={"X-API-Key": API_KEY})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("borrower", nargs="?", help="Borrower name (omit to list available borrowers)")
    parser.add_argument("--limit", type=int, default=5, help="Rows to fetch (default 5 — headers are the point)")
    args = parser.parse_args()

    if not args.borrower:
        data = api_get("/borrowers")
        borrowers = data.get("borrowers") or []
        print(f"{len(borrowers)} borrower(s) available:")
        for b in borrowers:
            print(f"  - {b}")
        print("\nRe-run with a borrower name to fetch its loan tape headers.")
        return

    qs = urllib.parse.urlencode({"borrower": args.borrower, "limit": str(args.limit)})
    data = api_get(f"/loan-tape?{qs}")
    columns = data.get("columns") or []
    rows = data.get("rows") or []

    print(f"Borrower: {args.borrower}")
    print(f"Columns returned ({len(columns)}):")
    for c in columns:
        flag = "  <-- expected by _loan_tape_summary()" if c in EXPECTED_MONEY_COLS else ""
        print(f"  - {c}{flag}")

    missing = [c for c in EXPECTED_MONEY_COLS if c not in columns]
    if missing:
        print(f"\nExpected by _loan_tape_summary() but NOT present in the response: {missing}")
    else:
        print("\nAll columns _loan_tape_summary() expects are present by name.")

    if rows:
        print(f"\nFirst row ({len(rows[0])} values):")
        print(json.dumps(dict(zip(columns, rows[0], strict=False)), indent=2, default=str))
    else:
        print("\nNo rows returned.")


if __name__ == "__main__":
    main()
