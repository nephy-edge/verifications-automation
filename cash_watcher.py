"""SOP 1 / Option A — loan-tape change-detection watcher.

Polls the Redshift Query API (`redshift-api`) for each borrower's loan tape,
computes a watermark (a content fingerprint) from the fetched rows, and when the
watermark differs from the last-seen one, re-runs the deterministic verification
pipeline headlessly, writing a working paper + audit-log entry for that borrower.

Reuses the same ingestion/aggregation/reconciliation/anomaly/report code as the
Streamlit app's `_run_pipeline`, but without the UI (the Streamlit module is
imported nowhere here, so this runs in a bare CLI/CI/cron context).

Usage:
    .\\.venv\\Scripts\\python.exe cash_watcher.py --once              # poll once, exit
    .\\.venv\\Scripts\\python.exe cash_watcher.py --interval 600     # loop forever
    .\\.venv\\Scripts\\python.exe cash_watcher.py --config config.yaml
    .\\.venv\\Scripts\\python.exe cash_watcher.py --dry-run          # detect, don't run
    .\\.venv\\Scripts\\python.exe cash_watcher.py --borrower leasy   # watch one

Operational notes:
- Read-only by construction: it only SELECTs via the redshift-api gateway (which
  enforces SELECT-only + schema allowlist + row caps server-side). No DML/DDL.
- A cut-off guard (`sop1.watcher.cut_off_date`) stops a post-cut-off tape update
  from silently becoming the "as-of" balance: "hard_stop" refuses to run;
  "backdate" runs but marks the run post-cut-off in the audit log. The guard
  uses each borrower's real date column (resolved through `loan_tape_columns`),
  not a hardcoded `begin_date`, so borrowers like payjoy (`origination_date`) or
  metafin (`emi_begin_date`) are guarded correctly.
- The watermark is computed over the fetched rows of the tape. When
  `tape_fetch_limit` is `0` (default "full"), the watcher requests the entire
  tape and the server returns everything it allows (bounded only by the API's
  own MAX_RESULTS_LIMIT), so change detection covers the full population. A
  positive `tape_fetch_limit` instead restricts each poll to the first N rows,
  making the watcher a *sampled* change detector (may miss changes outside the
  window).
- This watcher only ingests the *tape* side. It does NOT pull bank/mobile
  statements yet, so the report's reconciliation section is marked as not
  verified against an independent source; wire `Bank Access/` open-banking in to
  close that side.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase0_foundations.config import Config, load_config  # noqa: E402
from phase0_foundations.fx import FXConfig  # noqa: E402
from phase0_foundations.log import RunLog  # noqa: E402
from phase0_foundations.metrics import coverage as coverage_stats  # noqa: E402
from phase0_foundations.models import ExceptionItem, VerificationRun  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_rows(rows: list[dict]) -> str:
    """Stable content watermark for change detection: row count + a SHA-256 over
    the canonicalized JSON of every row. Triggered by a change in *what* the
    tape contains, not a reordering of rows — /loan-tape returns Spectrum scan
    order, which is not guaranteed stable between polls of the same tape, so we
    sort the canonical rows before hashing.

    Note: this watermarks only the fetched window (see module docstring on the
    sampled-detector trade-off).
    """
    canonical_rows = sorted(
        json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows
    )
    canonical = f"{len(rows)}|" + "|".join(canonical_rows)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _api_get(api_url: str, api_key: str, path: str, *, timeout: float = 130) -> dict:
    if not api_key:
        raise RuntimeError("REDSHIFT_API_KEY is not configured")
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}{path}",
        headers={"X-API-Key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The API puts its own error detail in the JSON body (e.g. "Query
        # failed: ..." on a DB outage, or "Unknown borrower ..."). Surface that
        # instead of the bare "HTTP Error 500/404" so a failure is diagnosable
        # from the watcher's log line alone, without hunting the API logs.
        body = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body).get("detail", body)
        except json.JSONDecodeError:
            detail = body
        raise RuntimeError(f"HTTP {exc.code} from {path}: {detail}") from exc


def _fetch_borrowers(api_url: str, api_key: str) -> list[str]:
    data = _api_get(api_url, api_key, "/borrowers")
    return list(data.get("borrowers") or [])


def _FULL_TAPE_LIMIT() -> int:
    return 1_000_000


def _effective_tape_limit(cfg: Config) -> int:
    """Resolve the per-poll fetch limit. `tape_fetch_limit: 0` means "fetch the
    full tape" (the server's own MAX_RESULTS_LIMIT still bounds the rows actually
    returned); a positive value is passed through as the requested limit."""
    limit = cfg.sop1.tape_fetch_limit
    return limit if limit > 0 else _FULL_TAPE_LIMIT()


def _fetch_tape(api_url: str, api_key: str, borrower: str, limit: int) -> list[dict]:
    qs = urllib.parse.urlencode({"borrower": borrower, "limit": str(int(limit))})
    data = _api_get(api_url, api_key, f"/loan-tape?{qs}")
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    return [dict(zip(columns, row, strict=False)) for row in rows]


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"borrowers": {}}
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"borrowers": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)


def _latest_date(rows: list[dict], column: str) -> str | None:
    """Latest (lexicographically greatest) date value for ``column`` across rows.
    ``column`` must already be resolved to this borrower's real date field."""
    dates = [r.get(column) for r in rows if r.get(column)]
    return max(dates) if dates else None


def _detect_new_statements(cfg: Config, state: dict, borrowers: list[str]) -> set[str]:
    """Borrowers whose Drive inbox holds statement files not yet ingested.

    This makes a statement upload a first-class change trigger: even when the
    loan tape is unchanged, a borrower with an un-ingested file in its
    configured drop-folder gets a run, so the new independent side is reconciled
    and emailed without a human. Read-only (only lists the folder); tolerant —
    an unconfigured/inaccessible inbox degrades to tape-only rather than
    erroring. The folder is the per-borrower configured one
    (bank_statements.folder_by_borrower / inbox_folder), never "any folder".
    """
    bs = cfg.sop1.bank_statements
    if not bs.enabled:
        return set()
    try:
        from phase0_foundations import drive_inbox
    except Exception:  # noqa: BLE001 - Drive unavailable -> no statement triggers
        return set()
    new: set[str] = set()
    for borrower in borrowers:
        folder = bs.folder_for(borrower)
        if not folder:
            continue
        try:
            files = drive_inbox.list_new_statements(folder)
        except Exception as exc:  # noqa: BLE001 - inbox outage must not fail the poll
            print(f"[poll] {borrower}: statement inbox check failed: {exc}", file=sys.stderr)
            continue
        ingested = set(state["borrowers"].get(borrower, {}).get("ingested_statements") or [])
        if any(f.fingerprint not in ingested for f in files):
            new.add(borrower)
    return new


def detect_changes(cfg: Config) -> tuple[list[str], dict, dict[str, list[dict]]]:
    """Poll the held borrower tapes, compare watermarks against the state file,
    record newly-seen watermarks, and return
    (changed_borrowers, state, borrower_rows) where `borrower_rows` maps each
    borrower to its just-fetched rows (so a caller can run without a second fetch).

    The per-borrower date column is resolved through the loan-tape column mapping
    so the cut-off guard uses the borrower's real date field, not a hardcoded
    `begin_date` (payjoy -> origination_date, metafin -> emi_begin_date, etc.).
    """
    api_url = os.getenv("REDSHIFT_API_URL", cfg.services.api_url)
    api_key = os.getenv(cfg.services.api_key_env, "")
    state = _load_state(Path(cfg.sop1.state_file))
    if cfg.sop1.borrowers:
        borrows = cfg.sop1.borrowers
    else:
        try:
            borrows = _fetch_borrowers(api_url, api_key)
        except Exception as exc:  # noqa: BLE001 - a transient API outage must not
            # kill a long-running watcher; report and skip this poll.
            print(f"[poll] could not fetch borrower list: {exc}", file=sys.stderr)
            _save_state(Path(cfg.sop1.state_file), state)
            return [], state, {}
    changed: list[str] = []
    borrower_rows: dict[str, list[dict]] = {}
    state.setdefault("borrowers", {})
    for borrower in borrows:
        try:
            rows = _fetch_tape(api_url, api_key, borrower, _effective_tape_limit(cfg))
        except Exception as exc:  # noqa: BLE001
            print(f"[poll] {borrower} error: {exc}", file=sys.stderr)
            continue
        borrower_rows[borrower] = rows
        column_map = cfg.loan_tape_columns.resolve(borrower)
        date_col = column_map.get("begin_date", "begin_date")
        watermark = _hash_rows(rows)
        prev = state["borrowers"].get(borrower)
        if prev is None or prev.get("watermark") != watermark:
            changed.append(borrower)
        entry = {
            "watermark": watermark,
            "rows": len(rows),
            "latest_date": _latest_date(rows, date_col),
            "date_column": date_col,
            "last_seen_at": _now(),
            "last_run_at": prev.get("last_run_at") if prev else None,
            "run_status": prev.get("run_status") if prev else None,
        }
        if prev and prev.get("ingested_statements"):
            # Keep the Drive drop-folder idempotency record across polls. The
            # rebuilt entry must not forget which statement files were already
            # downloaded, or every change-triggered run would re-download and
            # re-parse every inbox file as if it were new (the cross-file dedup
            # masks the resulting double-count, but files should be downloaded
            # once, not once per detected change).
            entry["ingested_statements"] = prev["ingested_statements"]
        state["borrowers"][borrower] = entry
    # Statement-driven trigger: a new statement upload is a change even when the
    # tape is unchanged, so the independent side gets reconciled + emailed
    # without a human. Tolerant: an unconfigured/inaccessible inbox just adds no
    # statement-triggered runs (tape-only), never an error.
    for borrower in sorted(_detect_new_statements(cfg, state, borrows)):
        if borrower not in changed:
            changed.append(borrower)
            print(f"[poll] {borrower}: new statement(s) in inbox -> run")
    # Persist the newly-computed watermarks now so a later poll reloads them and
    # correctly sees unchanged tapes as unchanged (the watcher must not re-run a
    # borrower just because an earlier poll's state was never written).
    _save_state(Path(cfg.sop1.state_file), state)
    return changed, state, borrower_rows


def _headless_run(
    cfg: Config, borrower: str, tape_rows: list[dict], reported: dict,
    bank_rows: list[dict] | None = None,
) -> VerificationRun:
    """Deterministic pipeline for one borrower's tape, mirroring the Streamlit
    dropdown path (normalize -> aggregate -> reconcile -> anomaly -> report).

    `bank_rows` is the independent bank/mobile statement side (from the Drive
    drop-folder). When present, the reconciliation compares *independent* bank
    in/outflows (calculated) against the tape's reported collections/
    disbursements — a real cross-check. When absent (no bank side), the report
    is explicitly marked "NOT VERIFIED" rather than falsely asserting the tape
    ties out to outflows/inflows.
    """
    run_id = uuid.uuid4().hex[:12]
    log = RunLog(cfg.log_path)
    run = VerificationRun(id=run_id, status="running", started_at=_now())
    log.append({"event": "watcher_run_started", "run_id": run_id, "borrower": borrower,
                "source": "cash_watcher"})
    try:
        from phase1_ingestion_parsing.ingest import normalize_loan_tape_row
        from phase1_ingestion_parsing.fx_rates import FXFetchError, fetch_live_rates
        from phase2_verification_engine.calculate import calculate_aggregates
        from phase2_verification_engine.reconcile import reconcile
        from phase3_anomaly_reporting.anomaly import detect_anomalies
        from phase3_anomaly_reporting.rank import rank_exceptions
        from phase3_anomaly_reporting.reporter import build_report

        column_map = cfg.loan_tape_columns.resolve(borrower)
        negative_sign = cfg.loan_tape_columns.uses_negative_sign(borrower)
        tape_rows = [
            r
            for rec in tape_rows
            for r in normalize_loan_tape_row(
                rec, account_ref="redshift", columns=column_map, negative_sign=negative_sign,
            )
        ]
        bank_rows = bank_rows or []

        effective_fx = cfg.fx
        found = {(r.get("currency") or "").strip().upper() for r in tape_rows + bank_rows}
        found.discard("")
        found.discard(cfg.fx.base_currency)
        live_fx_status: str | None = None
        if found:
            try:
                live_rates, as_of = fetch_live_rates(cfg.fx.base_currency, found)
                if live_rates:
                    effective_fx = FXConfig(base_currency=cfg.fx.base_currency,
                                            rates={**cfg.fx.rates, **live_rates})
                    live_fx_status = f"Live FX fetched for {', '.join(sorted(live_rates))} (as of {as_of})"
            except FXFetchError as exc:
                live_fx_status = f"{exc} using static rates"

        tape_agg = calculate_aggregates(tape_rows, fx=effective_fx)
        bank_agg = calculate_aggregates(bank_rows, fx=effective_fx)

        # Reported side = the loan tape's own disclosure (or explicitly supplied
        # totals). Calculated side = the independent bank/mobile side when
        # present, else the tape itself (surfaced as not-verified, never silently
        # treated as a real cross-check).
        _reported = reported or {
            "collections": tape_agg["collections"],
            "disbursements": tape_agg["disbursements"],
            "cash_total": tape_agg.get("cash_total", 0.0),
        }
        independent_source_present = bool(bank_rows)
        calculated = bank_agg if independent_source_present else tape_agg

        # SOP-1 Step 4 specifies "Flag discrepancies > 0.01%" -- 100x tighter
        # than the general cash_balance_variance (1%) used everywhere else in
        # the app. Only the cash check's tolerance is swapped for this run;
        # every other threshold (anomaly scoring, disbursement/collections
        # variance) stays the shared config as-is.
        cash_thresholds = dataclasses.replace(
            cfg.thresholds, cash_balance_variance=cfg.sop1.cash_discrepancy_pct,
        )
        recon = reconcile(calculated, _reported, cash_thresholds, run_id=run_id)
        anomalies = detect_anomalies(tape_rows + bank_rows, cfg.thresholds, run_id=run_id)
        exceptions: list[ExceptionItem] = rank_exceptions(recon + anomalies, cfg.thresholds)

        run.inputs = {
            "tape_source": "cash_watcher",
            "borrower": borrower,
            **coverage_stats(len(tape_rows) + len(bank_rows), len(tape_rows) + len(bank_rows)),
            "live_fx_status": live_fx_status,
            "independent_source_present": independent_source_present,
            "bank_statement_count": len(bank_rows),
            "statement_breakdown": _statement_breakdown(tape_rows, bank_rows, effective_fx),
            "reconciliation_scope": (
                "NOT VERIFIED — watcher ingested the loan tape only; no bank/mobile "
                "statement side was supplied, so reported-vs-independent reconciliation "
                "was not performed (report does not assert the tape ties out to outflows/inflows)."
            ) if not independent_source_present else "verified against independent bank/mobile statement side",
        }
        run.aggregates = tape_agg
        run.aggregates["calculated_collections"] = calculated.get("collections", 0.0)
        run.aggregates["calculated_disbursements"] = calculated.get("disbursements", 0.0)
        run.aggregates["calculated_cash_total"] = calculated.get("cash_total", 0.0)
        run.exceptions = exceptions
        run.status = "done"
        run.finished_at = _now()

        report_path = build_report(run, cfg.out_dir / borrower, exceptions=exceptions,
                                   thresholds=cfg.thresholds)
        log.append({"event": "watcher_run_completed", "run_id": run_id, "borrower": borrower,
                    "report": str(report_path), "exceptions": len(exceptions),
                    "independent_source_present": independent_source_present})
        print(f"[run] {borrower} -> {report_path} ({len(exceptions)} exceptions, "
              f"independent={independent_source_present})")
        return run
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.finished_at = _now()
        log.append({"event": "watcher_run_failed", "run_id": run_id, "borrower": borrower,
                    "error": str(exc)})
        print(f"[run] {borrower} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return run


def _bank_row_dedup_key(row: dict) -> tuple:
    """Content identity for a parsed bank row, used to de-duplicate the same
    transaction appearing across multiple statement files (overlapping periods
    or re-uploads). Amount is rounded to cents, direction lowercased, and the
    description normalised (case + non-alphanumerics stripped) so OCR noise
    (e.g. `TXN-..` vs `TxN-..`) does not defeat a match."""
    amount = row.get("amount")
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        amount = None
    desc = re.sub(r"[^A-Za-z0-9]", "", str(row.get("description") or "")).lower()
    return (
        str(row.get("value_date") or ""),
        amount,
        str(row.get("direction") or "").lower(),
        desc,
    )


def _dedupe_bank_rows(rows: list[dict]) -> list[dict]:
    """Drop bank rows that carry the same transaction identity, keeping the
    first occurrence (and its `key`) so downstream evidence IDs stay valid and
    a transaction shared across two statements is not double-counted."""
    seen: set = set()
    out: list[dict] = []
    for row in rows:
        key = _bank_row_dedup_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _month_key(value: str | None) -> str:
    """Bucket a canonical value_date (ISO YYYY-MM-DD or the two-date BBVA
    layout's DD-MM-YYYY) into a 'YYYY-MM' month key for the statement-side
    per-month breakdown."""
    if not value:
        return "(undated)"
    s = str(value).strip()
    iso = re.match(r"(\d{4})-(\d{2})-\d{2}", s)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}"
    two = re.match(r"(\d{2})-(\d{2})-(\d{4})", s)
    if two:
        return f"{two.group(3)}-{two.group(2)}"
    return "(undated)"


def _bank_category(row: dict) -> str:
    """Coarse, deterministic classification of a bank row for the statement-side
    breakdown. Real operating accounts carry payment-processor collections
    (e.g. Kushki) and internal treasury transfers (e.g. Leasy "OP FX/TC PF")
    that are not operating revenue/expense — surfacing them separately keeps the
    reconciliation honest rather than lumping treasury flows in with collections."""
    d = str(row.get("description") or "").upper()
    if "LEASY" in d and ("OP FX" in d or "OP TC" in d or "BIE" in d):
        return "leasy internal transfer (funding)"
    if "KUSHKI" in d:
        return "kushki (payment processor)"
    if "ITF" in d:
        return "itf tax"
    if "COMISION" in d or "COMISI" in d:
        return "bank fee"
    return "other"


def _statement_breakdown(
    tape_rows: list[dict], bank_rows: list[dict], fx: FXConfig,
) -> dict:
    """Per-statement / per-category / per-month breakdown of the independent
    (bank) side vs the tape, all converted to the FX base currency. Mirrors
    calculate_aggregates' conversion so the figures tie to the report's
    calculated_* totals. Deterministic; surfaces in the working paper."""
    from phase1_ingestion_parsing.ingest import DIR_IN
    from phase0_foundations.fx import convert_to_base

    def usd(r: dict) -> float:
        conv, _ = convert_to_base(float(r.get("amount") or 0), r.get("currency") or "", fx)
        return conv

    by_statement: dict[str, dict] = {}
    by_category: dict[str, dict] = {}
    by_month: dict[str, dict] = {}

    def add(store: dict, key: str, row: dict) -> None:
        bucket = store.setdefault(key, {"in": 0.0, "out": 0.0, "rows": 0})
        bucket["rows"] += 1
        amt = usd(row)
        if row.get("direction") == DIR_IN:
            bucket["in"] += amt
        else:
            bucket["out"] += amt

    for r in bank_rows:
        ref = str(r.get("account_ref") or "?")
        add(by_statement, ref, r)
        add(by_category, _bank_category(r), r)
        m = _month_key(r.get("value_date"))
        bm = by_month.setdefault(m, {"tape_in": 0.0, "tape_out": 0.0, "bank_in": 0.0,
                                     "bank_out": 0.0, "tape_rows": 0, "bank_rows": 0})
        bm["bank_rows"] += 1
        amt = usd(r)
        if r.get("direction") == DIR_IN:
            bm["bank_in"] += amt
        else:
            bm["bank_out"] += amt

    for ev in tape_rows:
        m = _month_key(ev.get("value_date"))
        bm = by_month.setdefault(m, {"tape_in": 0.0, "tape_out": 0.0, "bank_in": 0.0,
                                     "bank_out": 0.0, "tape_rows": 0, "bank_rows": 0})
        bm["tape_rows"] += 1
        amt = usd(ev)
        if ev.get("direction") == DIR_IN:
            bm["tape_in"] += amt
        else:
            bm["tape_out"] += amt

    def totals(store: dict) -> dict:
        return {
            "rows": sum(v["rows"] for v in store.values()),
            "in": sum(v["in"] for v in store.values()),
            "out": sum(v["out"] for v in store.values()),
        }

    bank_totals = totals(by_statement)
    return {
        "by_statement": by_statement,
        "by_category": by_category,
        "by_month": by_month,
        "totals": {
            "bank_rows": bank_totals["rows"],
            "bank_in": bank_totals["in"],
            "bank_out": bank_totals["out"],
            "bank_cash_total": bank_totals["in"] + bank_totals["out"],
            "fx_base_currency": fx.base_currency,
        },
    }


def _fetch_bank_statement_rows(
    cfg: Config, borrower: str, state: dict,
) -> tuple[list[dict], int]:
    """Fetch any *new* statement files from the Drive drop-folder for `borrower`,
    parse them into normalized bank/mobile rows, and retain the raw files.

    Returns (bank_rows, retained_new_count). New files are those whose
    (file_id, modifiedTime) fingerprint is not already recorded as ingested in the
    borrower's state; each is downloaded, parsed, and its raw bytes retained under
    out/<borrower>/statements/ as the audit trail. Every *retained* file (new or
    previously ingested) is re-parsed on each call, so a retried run reconciles
    against the same independent data without a second Drive download.

    The accumulated rows are de-duplicated by transaction identity
    (date, amount, direction, normalised description) before returning, so the
    same transaction appearing in more than one statement file (overlapping
    periods, re-uploads, or a file parsed via both the retained and download
    paths in one cycle) is counted once rather than double-counted.

    If the Drive inbox is not configured/enabled/authorized, returns ([], 0) and
    the run stays tape-only rather than crashing.
    """
    bs = cfg.sop1.bank_statements
    if not bs.enabled or not bs.folder_for(borrower):
        return [], 0
    try:
        from phase0_foundations import drive_inbox
        from phase1_ingestion_parsing.extract import extract_pdf
        from phase1_ingestion_parsing.ingest import SHEET_BANK, load_and_normalize
    except Exception as exc:  # noqa: BLE001
        print(f"[bank] {borrower}: Drive inbox unavailable: {exc}", file=sys.stderr)
        return [], 0

    retained_dir = cfg.out_dir / borrower / "statements"
    retained_dir.mkdir(parents=True, exist_ok=True)
    bank_rows: list[dict] = []
    retained_new = 0

    # 1) Always re-parse already-retained local files so a retry has the same
    #    independent data (no re-download).
    for path in sorted(retained_dir.iterdir()):
        if not path.is_file():
            continue
        try:
            if path.suffix.lower() == ".pdf":
                bank_rows.extend(extract_pdf(path, account_ref=path.name))
            else:
                bank_rows.extend(load_and_normalize([path], sheet=SHEET_BANK))
        except Exception as exc:  # noqa: BLE001 - a bad retained file must not block the rest
            print(f"[bank] {borrower}: failed to re-parse retained {path.name}: {exc}", file=sys.stderr)

    # 2) Download + retain only new inbox files (fingerprint not yet ingested).
    if bs.enabled and bs.folder_for(borrower):
        folder = bs.folder_for(borrower)
        try:
            files = drive_inbox.list_new_statements(folder)
        except Exception as exc:  # noqa: BLE001 - missing/expired token must not crash the run
            print(f"[bank] {borrower}: could not list Drive inbox: {exc}", file=sys.stderr)
            return bank_rows, 0

        ingested = set(state["borrowers"].get(borrower, {}).get("ingested_statements") or [])
        newly_ingested: list[str] = []
        for f in files:
            if f.fingerprint in ingested:
                continue
            try:
                content = drive_inbox.download_file(f.file_id, f.name)
                # The fingerprint is an ISO timestamp (contains ':' etc. —
                # invalid in Windows filenames), so sanitize before using it in
                # the retained-file path.
                safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", f.fingerprint)
                path = retained_dir / f"{safe}__{f.name}"
                path.write_bytes(content)
                if path.suffix.lower() == ".pdf":
                    bank_rows.extend(extract_pdf(path, account_ref=f.name))
                else:
                    bank_rows.extend(load_and_normalize([path], sheet=SHEET_BANK))
                retained_new += 1
                newly_ingested.append(f.fingerprint)
            except Exception as exc:  # noqa: BLE001 - a bad file must not block the rest
                print(f"[bank] {borrower}: failed to ingest {f.name}: {exc}", file=sys.stderr)

        if newly_ingested:
            # Persist ingested fingerprints so these files are not re-downloaded later.
            state["borrowers"].setdefault(borrower, {})["ingested_statements"] = sorted(
                ingested | set(newly_ingested)
            )
            _save_state(Path(cfg.sop1.state_file), state)

    # De-duplicate the same transaction appearing in multiple statement files
    # (overlapping periods / re-uploads / a file re-parsed via the retained path
    # and the download path in the same cycle). Keeps the first occurrence.
    merged = _dedupe_bank_rows(bank_rows)
    if len(merged) != len(bank_rows):
        print(f"[bank] {borrower}: dropped {len(bank_rows) - len(merged)} "
              f"duplicate row(s) across {len(set(r.get('account_ref') for r in bank_rows))} "
              f"statement file(s); {len(merged)} unique row(s)")
    return merged, retained_new


def _cut_off_status(cfg: Config, borrower_state: dict) -> str:
    """Evaluate the cut-off guard for a borrower's just-detected change.
    Returns "ok" or "post_cut_off". Uses the borrower's resolved date column
    (stored as `latest_date` by detect_changes)."""
    co = (cfg.sop1.cut_off_date or "").strip()
    if not co:
        return "ok"
    latest = (borrower_state.get("latest_date") or "")[:10]
    if not latest or latest <= co:
        return "ok"
    return "post_cut_off"


def _send_run_email(cfg: Config, borrower: str, run: VerificationRun) -> None:
    """Email the run's working paper to the configured recipients via the Gmail
    API (OAuth; see phase0_foundations.emailer / gmail_send). The email carries
    a well-typed HTML body plus the .docx working paper and .json attachments;
    the disk write remains the audit trail (docx/md/json all persisted). Skipped
    silently when not configured; a genuine mail failure is logged, never fatal."""
    ec = cfg.sop1.email
    if not ec.enabled:
        return
    run_dir = cfg.out_dir / borrower
    try:
        from phase0_foundations.report_docx import build_report_docx, email_body_html

        docx_path = build_report_docx(run_dir, run)
        body_html = email_body_html(run)
    except Exception as exc:  # noqa: BLE001 - a docx/render failure must not fail the run
        print(f"[mail] {borrower}: could not build report email: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return
    js = run_dir / f"run_{run.id}.json"
    try:
        from phase0_foundations.emailer import send_report_email

        ok = send_report_email(
            ec, [docx_path, js], f"{borrower} verification run {run.id}",
            body_html=body_html,
        )
        if ok:
            print(f"[mail] {borrower}: working paper emailed to {', '.join(ec.to_addrs)}")
        else:
            print(f"[mail] {borrower}: skipped (email not configured)", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - mail outage must not fail the run
        print(f"[mail] {borrower}: send failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def _poll_once(cfg: Config, dry_run: bool) -> int:
    changed, state, borrower_rows = detect_changes(cfg)
    for borrower in changed:
        bstate = state["borrowers"][borrower]
        status = _cut_off_status(cfg, bstate)
        if status == "post_cut_off" and cfg.sop1.cut_off_mode == "hard_stop":
            print(f"[guard] {borrower}: post-cut-off change -> hard stop (no run), "
                  f"latest {bstate.get('latest_date')}")
            bstate["last_run_at"] = _now()
            bstate["post_cut_off"] = True
            _save_state(Path(cfg.sop1.state_file), state)
            continue
        if dry_run:
            print(f"[dry-run] change detected: {borrower} ({bstate.get('rows')} rows, "
                  f"latest {bstate.get('latest_date')})")
            continue
        if cfg.sop1.run_on_change:
            tape = borrower_rows.get(borrower)
            if tape is None:
                tape = _fetch_tape(os.getenv("REDSHIFT_API_URL", cfg.services.api_url),
                                   os.getenv(cfg.services.api_key_env, ""),
                                   borrower, _effective_tape_limit(cfg))
            # Independent side: pull any new statement files from the Drive
            # drop-folder and parse them (retaining the raw files as the audit
            # trail). Bank files already ingested are re-parsed from local
            # retained copies so a retried run has the same independent data
            # without a second download.
            bank_rows, retained = _fetch_bank_statement_rows(cfg, borrower, state)
            if retained:
                print(f"[bank] {borrower}: ingested {retained} new statement file(s)")
            run = _headless_run(cfg, borrower, tape, dict(cfg.sop1.reported), bank_rows=bank_rows)
            if run.status == "done":
                bstate["post_cut_off"] = status == "post_cut_off"
                bstate["last_run_at"] = _now()
                bstate["run_status"] = "done"
                _send_run_email(cfg, borrower, run)
            else:
                # The run failed: do NOT treat this change as consumed. Revert the
                # stored watermark so the next poll re-detects and retries. Bank
                # statements already ingested are retained on disk, so the retry
                # re-parses them locally (see _fetch_bank_statement_rows).
                print(f"[retry] {borrower}: run failed ({run.status}); will retry next poll")
                bstate.pop("watermark", None)
                bstate["run_status"] = "failed"
            _save_state(Path(cfg.sop1.state_file), state)
    if not changed:
        print(f"[poll] no changes ({_now()})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Loan-tape change-detection watcher (SOP 1)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--interval", type=int, default=None,
                        help="Poll interval seconds (overrides config)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Detect changes but do not run the pipeline")
    parser.add_argument("--borrower", action="append", default=None,
                        help="Watch only this borrower (repeatable; overrides config)")
    parser.add_argument("--drive-sync", action="store_true",
                        help="Sync out/ to a Google Drive app folder before and after "
                             "each poll (for ephemeral runners like GitHub Actions: "
                             "restores state from the last run and persists this one)")
    args = parser.parse_args()

    # Load the project's .env so REDSHIFT_API_KEY / REDSHIFT_API_URL /
    # GOOGLE_OAUTH_INBOX_TOKEN_JSON are picked up without manual export (the
    # Streamlit app already calls load_dotenv; a bare CLI run must too).
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 - dotenv is optional at runtime
        pass

    cfg = load_config(args.config)
    if args.borrower:
        cfg.sop1.borrowers = list(args.borrower)
    interval = args.interval if args.interval is not None else cfg.sop1.interval_seconds

    def sync_pull() -> None:
        if not args.drive_sync:
            return
        try:
            from phase0_foundations import drive_store

            n = drive_store.pull_dir(cfg.out_dir)
            print(f"[sync] pulled {n} file(s) from Drive")
        except Exception as exc:  # noqa: BLE001 - a sync failure must never kill the watcher
            print(f"[sync] pull failed (continuing with local state): {exc}", file=sys.stderr)

    def sync_push() -> None:
        if not args.drive_sync:
            return
        try:
            from phase0_foundations import drive_store

            n = drive_store.push_dir(cfg.out_dir)
            print(f"[sync] pushed {n} file(s) to Drive")
        except Exception as exc:  # noqa: BLE001
            print(f"[sync] push failed (state stays local): {exc}", file=sys.stderr)

    if args.once:
        sync_pull()
        rc = _poll_once(cfg, args.dry_run)
        sync_push()
        return rc

    print(f"[watcher] polling every {interval}s "
          + ("(dry-run, no runs)" if args.dry_run else "") + ". Ctrl-C to stop.")
    sync_pull()
    try:
        while True:
            _poll_once(cfg, args.dry_run)
            sync_push()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[watcher] stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
