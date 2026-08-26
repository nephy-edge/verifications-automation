"""Derived workflow metrics read from the run log + persisted reports.

Pure, deterministic computations over ``RunLog.read()`` output (and, where the
log events don't carry enough context, the per-run report JSON) so the audit
tab and the report generator can surface coverage, lead times, and borrower
recency without duplicating knowledge of the log's shape.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from phase0_foundations.log import RunLog


def coverage(rows_ingested: int, population_size: int) -> dict[str, Any]:
    """Coverage of the tested population as {rows_ingested, population_size, coverage_pct}.

    ``coverage_pct`` is ``rows_ingested / population_size * 100``; an empty
    population is vacuously fully covered (100.0), so a 0-row run still reports
    coherently.
    """
    pct = (rows_ingested / population_size * 100.0) if population_size else 100.0
    return {
        "rows_ingested": rows_ingested,
        "population_size": population_size,
        "coverage_pct": round(pct, 2),
    }


def _run_id(event: dict[str, Any]) -> str | None:
    """The run an event belongs to: its own run_id, or the run_id embedded as
    the prefix of an exception_id (sign_off events carry exception_id only)."""
    rid = event.get("run_id")
    if rid:
        return str(rid)
    eid = str(event.get("exception_id") or "")
    if ":" in eid:
        return eid.split(":", 1)[0]
    return None


def _parse_ts(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts))
    except ValueError:
        return None


def lead_times(log: RunLog) -> list[dict[str, Any]]:
    """Per-run elapsed duration from its ``run_started`` ts to its last ``sign_off`` ts.

    Each row is {run_id, started, signed_off, elapsed_seconds}; runs that were
    started but never signed off carry ``signed_off=None`` /
    ``elapsed_seconds=None``.
    """
    started: dict[str, str] = {}
    signed_off: dict[str, str] = {}
    for e in log.read():
        if e.get("event") == "run_started":
            started.setdefault(str(e.get("run_id")), e.get("ts"))
        elif e.get("event") == "sign_off":
            run_id = _run_id(e)
            if run_id:
                signed_off[run_id] = e.get("ts")

    rows: list[dict[str, Any]] = []
    for run_id, start_ts in started.items():
        end_ts = signed_off.get(run_id)
        elapsed_seconds = None
        start, end = _parse_ts(start_ts), _parse_ts(end_ts)
        if start is not None and end is not None:
            elapsed_seconds = round((end - start).total_seconds(), 3)
        rows.append(
            {
                "run_id": run_id,
                "started": start_ts,
                "signed_off": end_ts,
                "elapsed_seconds": elapsed_seconds,
            }
        )
    return rows


def _borrower_for_run(out_root: Path, run_id: str) -> str:
    """inputs.borrower from a run's persisted report, default 'uploaded'.

    The run_completed log event doesn't carry inputs, so the borrower is read
    from the report JSON (out/<run_id>/run_<run_id>.json, with the flat
    out/run_<run_id>.json layout as fallback). Runs without a recorded borrower
    (CLI or file-upload tapes) fall back to "uploaded".
    """
    candidates = (out_root / run_id / f"run_{run_id}.json", out_root / f"run_{run_id}.json")
    for path in candidates:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            borrower = (data.get("inputs") or {}).get("borrower")
            if borrower:
                return str(borrower)
        except Exception:
            continue
    return "uploaded"


def last_verification_per_borrower(log: RunLog, out_root: Path) -> list[dict[str, Any]]:
    """Most recent completed run per borrower, oldest first.

    Reads all ``run_completed`` events, resolves each run's borrower from its
    persisted report inputs, keeps the latest run per borrower, and sorts by
    verification time ascending so the borrowers verified longest ago surface
    at the top.
    """
    latest_by_borrower: dict[str, dict[str, Any]] = {}
    for e in log.read():
        if e.get("event") != "run_completed":
            continue
        run_id = str(e.get("run_id") or "")
        if not run_id:
            continue
        ts = e.get("ts")
        borrower = _borrower_for_run(out_root, run_id)
        latest = latest_by_borrower.get(borrower)
        if latest is None or (ts or "") > (latest.get("last_verified") or ""):
            latest_by_borrower[borrower] = {
                "borrower": borrower,
                "run_id": run_id,
                "last_verified": ts,
            }
    rows = list(latest_by_borrower.values())
    rows.sort(key=lambda r: r.get("last_verified") or "")
    return rows
