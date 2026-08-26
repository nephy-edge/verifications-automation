"""Streamlit UI for the Verifications Automation workflow.

Wraps the phase0-5 engine: upload sources, run the deterministic pipeline
(ingest -> reconcile -> anomaly detect -> rank -> report), then review,
approve/reject, and sign off exceptions. Every review action is appended to
the same `verifications.log.jsonl` the CLI runner writes to.

Tabs: Run verification | Review & sign-off | Audit log & hardening
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# `streamlit run app/streamlit_app.py` puts app/ on sys.path, not the project
# root, so sibling packages (phase0_foundations, phase1_..., etc.) aren't
# importable without this regardless of the invocation's working directory.
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from phase0_foundations.config import Config, load_config
from phase0_foundations.log import RunLog
from phase0_foundations.metrics import (
    coverage as coverage_stats,
    last_verification_per_borrower,
    lead_times,
)
from phase0_foundations.models import ExceptionItem, VerificationRun
from phase1_ingestion_parsing.assets import load_expected_assets, load_registry_results
from phase1_ingestion_parsing.vehicle_verify import (
    CANONICAL_FIELDS,
    COUNTRY_CONFIG,
    classify_expected,
    make_client,
    verified_download_df,
)

from phase1_ingestion_parsing.cashmap import match_accounts, parse_cash_map
from phase1_ingestion_parsing.extract import (
    assess_confidence,
    detect_shape,
    extract_pdf,
    extract_pdf_balance,
    extract_pdf_text,
)
from phase1_ingestion_parsing.ingest import (
    SHEET_BANK,
    SHEET_LEDGER,
    SHEET_MOBILE,
    SHEET_TAPE,
    extract_tabular_balance,
    load_and_normalize,
    normalize_loan_tape_row,
)
from phase2_verification_engine.assets import verify_asset_existence
from phase2_verification_engine.calculate import calculate_aggregates
from phase2_verification_engine.reconcile import reconcile
from phase2_verification_engine.transaction_match import build_match_report, match_transactions
from phase3_anomaly_reporting.anomaly import detect_anomalies
from phase3_anomaly_reporting.rank import rank_exceptions
from phase3_anomaly_reporting.reporter import build_report
from phase4_human_review import approval
from phase5_feedback_hardening.harden import (
    HardeningLog,
    exception_pattern,
    promote_to_rule,
    record_feedback,
)

# ---------------------------------------------------------------- shared look
# Originally reused verbatim from the workspace's streamlit_design skill (to
# match the sibling "Bank Access" tool) via a path outside this project. That
# broke the moment this project is deployed on its own — see design_assets/ —
# so the three files are vendored here instead of read from the parent
# monorepo, keeping this project deployable standalone.
_SKILL_ASSETS = BASE_DIR / "design_assets"
_THEME_CSS = (_SKILL_ASSETS / "theme.css").read_text(encoding="utf-8")
_MASTHEAD_HTML = (_SKILL_ASSETS / "masthead.html").read_text(encoding="utf-8")
_COVER_HTML = (_SKILL_ASSETS / "cover.html").read_text(encoding="utf-8")

st.set_page_config(
    page_title="Verifications Automation",
    page_icon=":mag:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.html(
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>"
    "<link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700"
    "&family=IBM+Plex+Mono:wght@400;500&display=swap\" rel=\"stylesheet\">"
    f"<style>{_THEME_CSS}</style>"
)
st.html(
    _MASTHEAD_HTML.replace("{ACTIVE_TAB}", "Verifications Automation").replace(
        "{LIVE_DATE}", datetime.now().strftime("%d %b %Y")
    )
)
st.html(
    _COVER_HTML.replace("{TITLE}", "Verifications Automation").replace(
        "{SUB}",
        "Full-population reconciliation, anomaly detection, and sign-off &mdash; "
        "deterministic by design, human in the loop.",
    )
)

# --------------------------------------------------------------------- config
load_dotenv(BASE_DIR / ".env")
CFG: Config = load_config(BASE_DIR / "config.yaml")
LOG_PATH = BASE_DIR / CFG.log_path
OUT_ROOT = BASE_DIR / CFG.out_dir
RUN_LOG = RunLog(LOG_PATH)

# Redshift Query API (redshift-api sibling) for the loan-tape dropdown.
REDSHIFT_API_URL = os.getenv("REDSHIFT_API_URL", "http://127.0.0.1:8001").rstrip("/")
REDSHIFT_API_KEY = os.getenv("REDSHIFT_API_KEY", "")


def _api_get(path: str, *, timeout: float = 130) -> dict[str, Any]:
    """GET an authenticated endpoint on the Redshift Query API.

    `timeout` defaults well above the API's server-side statement timeout
    (120s) so a large loan-tape query has time to return before this client
    gives up.
    """
    if not REDSHIFT_API_KEY:
        raise RuntimeError("REDSHIFT_API_KEY is not configured")
    req = urllib.request.Request(
        f"{REDSHIFT_API_URL}{path}",
        headers={"X-API-Key": REDSHIFT_API_KEY},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


@st.cache_data(ttl=300)
def _fetch_borrowers() -> list[str]:
    """List borrowers available from the loan-tape API."""
    data = _api_get("/borrowers")
    return list(data.get("borrowers") or [])


def _fetch_loan_tape_rows(
    borrower: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch one borrower's loan tape from the API as raw record dicts.

    `date_from`/`date_to` filter loans by their begin_date (YYYY-MM-DD) so a
    large tape can be narrowed to the period of interest before being loaded
    into the pipeline. `limit` caps how many rows are transferred.
    """
    params = {"borrower": borrower, "limit": str(int(limit))}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    qs = urllib.parse.urlencode(params)
    data = _api_get(f"/loan-tape?{qs}")
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    return [dict(zip(columns, row)) for row in rows]


def _loan_tape_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a borrower's raw loan-tape records (as returned by the
    Redshift Query API) into a short summary for display.

    The staging tables return money columns as strings, so they are coerced to
    float here. Blank/absent values count as 0 in the money totals. `status`,
    `branch`, and `days_past_due` drive the cohort and delinquency breakdowns.
    """
    df = pd.DataFrame(records)
    if df.empty:
        return {}

    def _num(col: str) -> pd.Series:
        if col not in df.columns:
            return pd.Series(0.0, index=df.index)
        return pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    principal = _num("principal_amount")
    total = _num("total_loan_amount")
    prin_out = _num("principal_outstanding")
    int_out = _num("interest_outstanding")
    fee_out = _num("fee_outstanding")
    pen_out = _num("penalty_outstanding")
    outstanding = prin_out + int_out + fee_out + pen_out
    collected = (total - outstanding).clip(lower=0.0)

    summary: dict[str, Any] = {
        "loan_count": int(df["loan_id"].notna().sum()),
        "principal_amount": float(principal.sum()),
        "total_loan_amount": float(total.sum()),
        "principal_outstanding": float(prin_out.sum()),
        "interest_outstanding": float(int_out.sum()),
        "fee_outstanding": float(fee_out.sum()),
        "penalty_outstanding": float(pen_out.sum()),
        "total_outstanding": float(outstanding.sum()),
        "collected_to_date": float(collected.sum()),
    }
    for col in ("status", "branch"):
        if col in df.columns:
            cleaned = df[col].replace("", "(blank)").fillna("(blank)")
            summary[f"by_{col}"] = cleaned.value_counts().to_dict()
    if "days_past_due" in df.columns:
        dpd = _num("days_past_due")
        summary["delinquent_loans"] = int((dpd > 0).sum())
        summary["n_dpd_30"] = int(((dpd >= 30) & (dpd < 60)).sum())
        summary["n_dpd_60"] = int(((dpd >= 60) & (dpd < 90)).sum())
        summary["n_dpd_90"] = int((dpd >= 90).sum())
    return summary


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_duration(seconds: float | None) -> str:
    """Human-readable elapsed time for the audit tab (e.g. '2h 3m 5s')."""
    if seconds is None:
        return "not signed off"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{sec}s")
    return " ".join(parts)


def _save_upload(uploaded_file, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / uploaded_file.name
    p.write_bytes(uploaded_file.getvalue())
    return p


def _run_pipeline(
    tape_files, bank_files, ledger_files, mobile_files, cash_map_files=(), tape_records=None,
) -> tuple[VerificationRun, Path]:
    run_id = uuid.uuid4().hex[:12]
    run_dir = OUT_ROOT / run_id
    uploads_dir = run_dir / "uploads"

    RUN_LOG.append({"event": "run_started", "run_id": run_id, "source": "streamlit"})

    tape_paths = [_save_upload(f, uploads_dir) for f in tape_files]
    ledger_paths = [_save_upload(f, uploads_dir) for f in ledger_files]
    mobile_paths = [_save_upload(f, uploads_dir) for f in mobile_files]
    bank_paths = [_save_upload(f, uploads_dir) for f in bank_files]
    # A cash map is the IO's account-structure/routing writeup (A2) — a
    # questionnaire, not a transaction ledger. Nothing to parse for amounts;
    # it's saved alongside the run as a reference document for the human
    # reviewer, same as the working papers it sits next to.
    cash_map_paths = [_save_upload(f, uploads_dir) for f in cash_map_files]

    # The loan tape is either uploaded files *or* a borrower selected from the
    # dropdown (fetched from the Redshift Query API). When the dropdown is used,
    # `tape_records` carries the already-fetched raw tape rows; we derive the
    # same disbursement/collection events the file path would.
    taper_src = "dropdown" if tape_records else "upload"
    tape_rows = (
        [r for rec in tape_records for r in normalize_loan_tape_row(rec, account_ref="redshift")]
        if tape_records
        else load_and_normalize(tape_paths, sheet=SHEET_TAPE)
    )
    ledger_rows = load_and_normalize(ledger_paths, sheet=SHEET_LEDGER)

    def _load_statement(p: Path, sheet: str) -> dict[str, Any]:
        """One bank/mobile statement's own rows, closing balance (if
        recoverable), and raw text (PDFs only — used to match it against a
        cash-map account by bank name)."""
        if p.suffix.lower() == ".pdf":
            text = extract_pdf_text(p)
            return {
                "filename": p.name,
                "account_ref": p.stem,
                "rows": extract_pdf(p, account_ref=p.stem),
                "balance": extract_pdf_balance(p),
                "text": text,
                "shape": detect_shape(text),
            }
        return {
            "filename": p.name,
            "account_ref": p.stem,
            "rows": load_and_normalize([p], sheet=sheet),
            "balance": extract_tabular_balance(p),
            "text": "",
            "shape": {},
        }

    bank_statements = [_load_statement(p, SHEET_BANK) for p in bank_paths]
    mobile_statements = [_load_statement(p, SHEET_MOBILE) for p in mobile_paths]
    bank_rows: list[dict] = [r for s in bank_statements for r in s["rows"]]
    bank_balances: list[float] = [s["balance"] for s in bank_statements if s["balance"] is not None]
    mobile_rows: list[dict] = [r for s in mobile_statements for r in s["rows"]]

    # A cash map (A2) names accounts by role (and sometimes by bank), never
    # by account number — so a statement can only be matched to a candidate
    # account, not asserted with certainty. Parsed per file and merged so a
    # human reviewer confirms the mapping in the Results section below.
    cash_map_accounts: list[dict[str, Any]] = []
    for p in cash_map_paths:
        try:
            parsed_map = parse_cash_map(p)
        except Exception:
            continue
        for acct in parsed_map.get("accounts", []):
            cash_map_accounts.append({**acct, "borrower_name": parsed_map.get("borrower_name", "")})

    statement_summaries: list[dict[str, Any]] = []
    for s in bank_statements + mobile_statements:
        agg = calculate_aggregates(s["rows"])
        candidates = (
            match_accounts(s["text"], s["filename"], cash_map_accounts) if cash_map_accounts else []
        )
        statement_summaries.append(
            {
                "filename": s["filename"],
                "cash_in": agg["cash_in"],
                "cash_out": agg["cash_out"],
                "transaction_count": agg["transaction_count"],
                "closing_balance": s["balance"],
                "candidate_accounts": candidates,
                "shape": s.get("shape") or {},
            }
        )

    # Reported side = the loan tape's own disclosure (collections/disbursements)
    # and the ledger's own cash balance. Calculated side = independently summed
    # from bank + mobile-money inflows/outflows (A5 methodology) — except cash,
    # which is a point-in-time balance, not a sum of transaction amounts: it
    # comes from the statement's own closing balance (extracted separately),
    # summed across accounts if more than one statement was uploaded.
    tape_agg = calculate_aggregates(tape_rows)
    ledger_agg = calculate_aggregates(ledger_rows)
    indep_agg = calculate_aggregates(bank_rows + mobile_rows)

    reported = {
        "collections": tape_agg["collections"],
        "disbursements": tape_agg["disbursements"],
        "cash_total": ledger_agg["cash_total"],
    }
    calculated = {
        "collections": indep_agg["collections"],
        "disbursements": indep_agg["disbursements"],
        "cash_total": sum(bank_balances) if bank_balances else 0.0,
    }

    # B3: rows below the PDF-confidence floor need a human spot-check rather
    # than being trusted silently — flag them (an unmapped-layout PDF now
    # surfaces as one confidence:0.0 row instead of contributing nothing).
    assess_confidence(bank_rows, CFG.thresholds.pdf_confidence_floor)
    spot_check_rows = [r for r in bank_rows if r.get("needs_spot_check")]

    all_rows = tape_rows + ledger_rows + mobile_rows + bank_rows

    # A reconciliation needs both sides present; a missing side is a data gap,
    # not a 100%/"inf" variance finding, so don't run checks that can't yet be
    # backed by evidence. Anomaly detection has no such precondition.
    has_tape = bool(tape_rows)
    has_ledger = bool(ledger_rows)
    has_independent = bool(bank_rows or mobile_rows)
    has_independent_balance = bool(bank_balances)
    recon_ready = {
        "collections": has_tape and has_independent,
        "disbursements": has_tape and has_independent,
        "cash": has_ledger and has_independent_balance,
    }
    recon_all = reconcile(calculated, reported, CFG.thresholds, run_id=run_id)
    recon = [e for e in recon_all if recon_ready.get(e.id.rsplit(":", 1)[-1], True)]
    skipped_recon = [k for k, ready in recon_ready.items() if not ready]

    anomalies = detect_anomalies(all_rows, CFG.thresholds, run_id=run_id)
    exceptions = rank_exceptions(recon + anomalies, CFG.thresholds)

    borrower = st.session_state.get("tape_borrower") if tape_records else "uploaded"
    population_size = len(all_rows)

    run = VerificationRun(
        id=run_id,
        status="done",
        inputs={
            "tape_files": [p.name for p in tape_paths],
            "bank_files": [p.name for p in bank_paths],
            "ledger_files": [p.name for p in ledger_paths],
            "mobile_files": [p.name for p in mobile_paths],
            "cash_map_files": [p.name for p in cash_map_paths],
            "tape_source": taper_src,
            "borrower": borrower,
            **coverage_stats(len(all_rows), population_size),
            "has_tape": has_tape,
            "has_ledger": has_ledger,
            "has_independent": has_independent,
            "has_independent_balance": has_independent_balance,
            "spot_check_descriptions": [r["description"] for r in spot_check_rows],
            "skipped_reconciliation": skipped_recon,
            "cash_map_accounts": cash_map_accounts,
            "statement_summaries": statement_summaries,
        },
        aggregates={
            "reported_collections": reported["collections"],
            "calculated_collections": calculated["collections"],
            "reported_disbursements": reported["disbursements"],
            "calculated_disbursements": calculated["disbursements"],
            "reported_cash_total": reported["cash_total"],
            "calculated_cash_total": calculated["cash_total"],
            "transaction_count": len(all_rows),
        },
        exceptions=exceptions,
        started_at=_now(),
        finished_at=_now(),
    )
    build_report(run, run_dir, exceptions=exceptions)
    RUN_LOG.append(
        {
            "event": "run_completed",
            "run_id": run_id,
            "aggregates": run.aggregates,
            "exception_count": len(exceptions),
        }
    )
    return run, run_dir


def _list_past_runs() -> list[str]:
    if not OUT_ROOT.exists():
        return []
    return sorted(
        (p.name for p in OUT_ROOT.iterdir() if p.is_dir() and (p / f"run_{p.name}.json").exists()),
        reverse=True,
    )


def _load_run(run_id: str) -> tuple[VerificationRun, Path]:
    import json

    run_dir = OUT_ROOT / run_id
    data = json.loads((run_dir / f"run_{run_id}.json").read_text(encoding="utf-8"))
    run = VerificationRun(
        id=data["run_id"],
        status=data["status"],
        inputs=data.get("inputs", {}),
        aggregates=data.get("aggregates", {}),
        exceptions=[ExceptionItem(**e) for e in data.get("exceptions", [])],
    )
    return run, run_dir


def _persist_run(run: VerificationRun, run_dir: Path) -> None:
    build_report(run, run_dir, exceptions=run.exceptions)


def _signed_off_ids() -> set[str]:
    return {
        e.get("exception_id")
        for e in RUN_LOG.read()
        if e.get("event") == "sign_off" and e.get("approved")
    }


# --------------------------------------------------------------------- state
if "active_run" not in st.session_state:
    st.session_state["active_run"] = None
    st.session_state["active_run_dir"] = None
# A widget's own session_state key can only be set *before* that widget is
# instantiated in a given script run — so a run just completed can't poke
# "run_picker" directly (the sidebar widget below already exists by then).
# It sets this plain flag instead; consumed here, ahead of the widget.
if st.session_state.pop("_reset_run_picker", False):
    st.session_state["run_picker"] = "(current session)"

with st.sidebar:
    st.subheader("Runs")
    past_runs = _list_past_runs()
    options = ["(current session)"] + past_runs
    # Explicit key + explicit button: merely having a past run *selected* must
    # never load it. Without a key, Streamlit remembers the last selection
    # across every rerun and would silently reload (and overwrite) a fresh
    # run the moment anything else on the page triggers a rerun.
    choice = st.selectbox("Load a run", options, index=0, key="run_picker")
    if choice != "(current session)" and st.button("Load selected run"):
        run, run_dir = _load_run(choice)
        st.session_state["active_run"] = run
        st.session_state["active_run_dir"] = run_dir
        st.rerun()

    st.divider()
    st.subheader("Thresholds")
    st.caption("Single source of truth: `config.yaml` (B3).")
    th = CFG.thresholds
    st.text(f"Collections variance   > {th.collections_variance:.0%}")
    st.text(f"Disbursement tolerance > ${th.disbursement_abs:,.0f} or {th.disbursement_pct:.0%}")
    st.text(f"Cash balance variance  > {th.cash_balance_variance:.0%}")
    st.text(f"Anomaly forensic route >= {th.anomaly_score_high:.2f}")
    st.text(f"PDF confidence floor   < {th.pdf_confidence_floor:.0%}")

tab_run, tab_assets, tab_review, tab_audit, tab_match = st.tabs(
    [
        "Run verification",
        "Asset & vehicle verification",
        "Review & sign-off",
        "Audit log & hardening",
        "Transaction matching",
    ]
)

# ------------------------------------------------------------------ Run tab
with tab_run:
    st.subheader("1. Upload sources")
    col1, col2 = st.columns(2)
    with col1:
        tape_files = st.file_uploader(
            "Loan tape (reported collections/disbursements)",
            type=["csv", "xlsx", "xls"],
            accept_multiple_files=True,
            key="tape_up",
        )
        ledger_files = st.file_uploader(
            "Ledger balances (reported cash balance)",
            type=["csv", "xlsx", "xls"],
            accept_multiple_files=True,
            key="ledger_up",
        )
    with col2:
        bank_files = st.file_uploader(
            "Bank statements (independent cash evidence)",
            type=["csv", "xlsx", "xls", "pdf"],
            accept_multiple_files=True,
            key="bank_up",
        )
        mobile_files = st.file_uploader(
            "Mobile money statements (independent cash evidence)",
            type=["csv", "xlsx", "xls"],
            accept_multiple_files=True,
            key="mobile_up",
        )

    cash_map_files = st.file_uploader(
        "Cash map (reference only — IO's account/routing writeup; not parsed for amounts)",
        type=["pdf", "txt", "json", "html", "docx"],
        accept_multiple_files=True,
        key="cashmap_up",
    )

    st.subheader("1b. Loan tape from Redshift (alternative to file upload)")
    try:
        borrowers = _fetch_borrowers()
    except Exception as exc:  # noqa: BLE001 - surface API connection errors
        st.error(f"Could not reach the Redshift Query API: {exc}")
        borrowers = []
    c1, c2, c3 = st.columns([3, 2, 1])
    with c1:
        selected_borrower = st.selectbox(
            "Select a borrower to load their loan tape",
            options=borrowers or ["(no borrowers available)"],
            key="borrower_dd",
        )
    with c2:
        date_range = st.date_input(
            "Begin-date range (optional)",
            value=[],
            key="tape_date_range",
        )
    with c3:
        st.write("")
        st.write("")
        max_rows = st.number_input(
            "Max rows",
            min_value=1,
            max_value=500000,
            value=1000,
            step=100,
            key="tape_max_rows",
        )

    if st.button("Load loan tape", disabled=not borrowers, key="load_tape"):
        date_from = date_range[0].isoformat() if len(date_range) > 0 else None
        date_to = date_range[1].isoformat() if len(date_range) > 1 else None
        with st.spinner(f"Fetching {selected_borrower} loan tape from Redshift..."):
            records = _fetch_loan_tape_rows(
                selected_borrower,
                date_from=date_from,
                date_to=date_to,
                limit=max_rows,
            )
        st.session_state["tape_records"] = records
        st.session_state["tape_borrower"] = selected_borrower
        st.session_state["tape_filters"] = {
            "date_from": date_from,
            "date_to": date_to,
            "limit": max_rows,
        }
        st.rerun()

    loaded_tape_records = st.session_state.get("tape_records")
    if loaded_tape_records:
        tape_borrower = st.session_state.get("tape_borrower", "?")
        tape_filters = st.session_state.get("tape_filters", {})
        filter_desc = []
        if tape_filters.get("date_from"):
            filter_desc.append(f"begin ≥ {tape_filters['date_from']}")
        if tape_filters.get("date_to"):
            filter_desc.append(f"begin ≤ {tape_filters['date_to']}")
        if tape_filters.get("limit"):
            filter_desc.append(f"cap {tape_filters['limit']:,} rows")
        suffix = f" ({'; '.join(filter_desc)})" if filter_desc else ""
        st.success(
            f"Loaded **{len(loaded_tape_records):,}** loan rows for **{tape_borrower}**"
            f"{suffix} from Redshift — this will be used as the loan tape."
        )

        st.divider()
        st.subheader(f"Loan tape — {tape_borrower} summary")
        summary = _loan_tape_summary(loaded_tape_records)
        if summary:
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Loans", f"{summary['loan_count']:,}")
            c2.metric("Total principal", f"{summary['principal_amount']:,.0f}")
            c3.metric("Total loan amount", f"{summary['total_loan_amount']:,.0f}")
            c4.metric("Total outstanding", f"{summary['total_outstanding']:,.0f}")
            c5.metric("Collected to date", f"{summary['collected_to_date']:,.0f}")

            r1, r2, r3 = st.columns(3)
            r1.metric("Principal outstanding", f"{summary['principal_outstanding']:,.0f}")
            r2.metric("Interest outstanding", f"{summary['interest_outstanding']:,.0f}")
            r3.metric("Fees outstanding", f"{summary['fee_outstanding']:,.0f}")

            if "delinquent_loans" in summary:
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("Delinquent (DPD > 0)", f"{summary['delinquent_loans']:,}")
                d2.metric("DPD 30–59", f"{summary.get('n_dpd_30', 0):,}")
                d3.metric("DPD 60–89", f"{summary.get('n_dpd_60', 0):,}")
                d4.metric("DPD ≥ 90", f"{summary.get('n_dpd_90', 0):,}")

            b1, b2 = st.columns(2)
            with b1:
                st.markdown("**By status**")
                st.dataframe(
                    pd.DataFrame.from_dict(summary.get("by_status", {}), orient="index", columns=["Loans"]),
                    width="stretch",
                )
            with b2:
                st.markdown("**By branch**")
                st.dataframe(
                    pd.DataFrame.from_dict(summary.get("by_branch", {}), orient="index", columns=["Loans"]),
                    width="stretch",
                )

        st.markdown("**Full loan tape**")
        st.caption(
            f"{len(loaded_tape_records):,} row(s) x {len(loaded_tape_records[0])} columns."
        )
        st.dataframe(pd.DataFrame(loaded_tape_records), width="stretch", height=400)

    st.subheader("2. Run")
    can_run = bool(
        tape_files or bank_files or ledger_files or mobile_files or loaded_tape_records
    )
    if st.button("Run verification", disabled=not can_run, type="primary"):
        with st.spinner("Ingesting, reconciling, and detecting anomalies..."):
            run, run_dir = _run_pipeline(
                tape_files or [],
                bank_files or [],
                ledger_files or [],
                mobile_files or [],
                cash_map_files or [],
                tape_records=loaded_tape_records,
            )
        st.session_state["active_run"] = run
        st.session_state["active_run_dir"] = run_dir
        st.session_state["_reset_run_picker"] = True  # picked up above, before the sidebar widget, next rerun
        st.success(f"Run {run.id} complete — {len(run.exceptions)} exception(s) flagged.")

    run: VerificationRun | None = st.session_state["active_run"]
    if run is not None:
        st.subheader("3. Results")
        with st.expander("What am I looking at?"):
            st.markdown(
                "Each figure below is one side of a comparison, not a standalone total:\n\n"
                "- **Reported** is what a source *claims* on its own: the loan tape's own "
                "collections/disbursements, the ledger's own cash balance.\n"
                "- **Calculated** is what this tool *independently derives* from separate "
                "evidence — bank and mobile-money statements — with no arithmetic "
                "borrowed from the reported side.\n\n"
                "**Reported and calculated should land close together.** A gap beyond the "
                "threshold (B3: 2% for collections, $100/5% for disbursements, 1% for cash) "
                "is exactly what shows up as a reconciliation exception in the table below "
                "— it means the tape/ledger's own numbers don't match what the bank "
                "evidence actually shows, not that something crashed.\n\n"
                "**Cash total is a balance, not a sum of transactions.** Collections and "
                "disbursements are sums of many transactions (correctly, since that's what "
                "they are); cash total is a single point-in-time balance — the ledger's "
                "stated balance vs. the bank statement's closing balance 'as of' the same "
                "date. It only ever comes from a real balance figure: the ledger's own "
                "reported balance, and the statement's own closing balance — read directly "
                "off a balance column (PDF's running-balance column, or a CSV/Excel column "
                "detected by name, e.g. 'Running Balance'), never added up from transactions. "
                "A statement with no balance column at all has no closing balance to "
                "recover, so that side shows 'no data uploaded' rather than a number that "
                "would just be transaction volume mislabeled as a balance.\n\n"
                "**Why reported and calculated rarely match exactly even on real data:** the "
                "tape/ledger and the bank/mobile evidence often don't cover the *same scope* "
                "— e.g. a tape filtered to one status, or a bank account that funds more "
                "than the loans in front of you. A variance tells you to check scope before "
                "assuming an error."
            )
        a = run.aggregates
        inputs = run.inputs or {}
        # Unknown (e.g. a run reloaded from disk, whose inputs weren't persisted)
        # defaults to "ready" so older runs don't show a spurious gap warning.
        has_tape = inputs.get("has_tape", True)
        has_ledger = inputs.get("has_ledger", True)
        has_independent = inputs.get("has_independent", True)
        has_independent_balance = inputs.get("has_independent_balance", True)

        def _fmt(value: float, ready: bool) -> str:
            return f"{value:,.2f}" if ready else "— no data uploaded"

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("Collections — reported (tape)", _fmt(a.get("reported_collections", 0), has_tape))
            st.metric(
                "Collections — calculated (bank/mobile)",
                _fmt(a.get("calculated_collections", 0), has_independent),
            )
        with m2:
            st.metric("Disbursements — reported (tape)", _fmt(a.get("reported_disbursements", 0), has_tape))
            st.metric(
                "Disbursements — calculated (bank/mobile)",
                _fmt(a.get("calculated_disbursements", 0), has_independent),
            )
        with m3:
            st.metric("Cash total — reported (ledger)", _fmt(a.get("reported_cash_total", 0), has_ledger))
            st.metric(
                "Cash total — calculated (statement balance)",
                _fmt(a.get("calculated_cash_total", 0), has_independent_balance),
            )
        st.caption(f"{a.get('transaction_count', 0):,} canonical records ingested across all sources.")

        cash_map_files = inputs.get("cash_map_files") or []
        if cash_map_files:
            st.caption(
                f"Cash map reference(s) attached for manual review: {', '.join(cash_map_files)} "
                "— not parsed for amounts (it's a questionnaire, not a ledger)."
            )

        cash_map_accounts = inputs.get("cash_map_accounts") or []
        statement_summaries = inputs.get("statement_summaries") or []
        if cash_map_files and statement_summaries:
            st.subheader("Statement → cash-map account mapping")
            if not cash_map_accounts:
                st.caption(
                    "The uploaded cash map didn't yield any recognizable account "
                    "entries to map statements to (check it has a '4.2' account-listing "
                    "answer, or an `accounts` array for the structured JSON schema)."
                )
            else:
                st.caption(
                    "Best-effort match against the uploaded cash map. Most cash maps carry "
                    "no account number — only a role description and sometimes a bank name "
                    "— so this is a suggestion for you to confirm, not an assertion."
                )
                for i, s in enumerate(statement_summaries):
                    candidates = s.get("candidate_accounts") or cash_map_accounts
                    shape = s.get("shape") or {}
                    if shape.get("layout"):
                        st.caption(
                            f"Detected: **{shape.get('bank') or 'unknown bank'}** · "
                            f"currency **{shape.get('currency') or '?'}** · statement "
                            f"layout `{shape.get('layout')}`"
                        )
                    labels = [
                        f"{c.get('label') or c.get('id')}"
                        + (f" ({c['borrower_name']})" if c.get("borrower_name") else "")
                        for c in candidates
                    ]
                    choice_idx = st.selectbox(
                        f"**{s['filename']}** is which cash-map account?",
                        options=range(len(labels)),
                        format_func=lambda idx, labels=labels: labels[idx],
                        key=f"acct_map_{run.id}_{i}",
                    )
                    chosen = candidates[choice_idx]
                    st.markdown(f"_{chosen.get('description', 'No description available.')}_")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Money in (this statement)", f"{s['cash_in']:,.2f}")
                    c2.metric("Money out (this statement)", f"{s['cash_out']:,.2f}")
                    c3.metric(
                        "Closing balance",
                        f"{s['closing_balance']:,.2f}" if s.get("closing_balance") is not None else "n/a",
                    )
                    st.divider()

        skipped = inputs.get("skipped_reconciliation")
        if skipped:
            reasons = []
            if ("collections" in skipped or "disbursements" in skipped):
                if not has_tape:
                    reasons.append("no loan tape uploaded")
                if not has_independent:
                    reasons.append("no bank/mobile statements to independently sum")
            if "cash" in skipped:
                if not has_ledger:
                    reasons.append("no ledger uploaded")
                if has_independent and not has_independent_balance:
                    reasons.append(
                        "the uploaded bank statement has no column recognizable as "
                        "a running/closing balance, so there's no closing balance "
                        "to extract"
                    )
            st.warning(
                f"Reconciliation skipped for **{', '.join(skipped)}** — "
                f"{'; '.join(reasons)}. Only anomaly detection ran on what was "
                "uploaded; upload the missing source(s) and re-run for a real "
                "reconciliation."
            )

        spot_check = inputs.get("spot_check_descriptions") or []
        if spot_check:
            st.warning(
                f"**{len(spot_check)} bank record(s) need a manual spot-check** "
                f"(below the {CFG.thresholds.pdf_confidence_floor:.0%} confidence "
                "floor — B3) before you rely on this run's bank-side figures:\n\n"
                + "\n".join(f"- {d}" for d in spot_check)
            )

        if run.exceptions:
            df = pd.DataFrame(
                [
                    {
                        "Severity": e.severity,
                        "Kind": e.kind,
                        "Description": e.description,
                        "Status": e.status,
                    }
                    for e in run.exceptions
                ]
            )
            st.dataframe(df, width='stretch', hide_index=True)
        else:
            st.info("No exceptions flagged.")

# ------------------------------------------- Asset & vehicle verification
with tab_assets:
    st.subheader("Asset & vehicle verification")
    st.caption(
        "Independent of the Run tab — a separate reported-vs-independent comparison, not "
        "combined into the tape/bank/mobile reconciliation. Reported side: a collateral "
        "register naming pledged assets (plate/registration, borrower, expected owner). "
        "Independent side: a registry check on each plate — by default looked up live "
        "against a registry API (Verifik; mock data until `VERIFIK_TOKEN` is set), or "
        "supplied as an already-run registry-check file if you have one on hand."
    )

    country_code = st.selectbox(
        "Country (for registry lookups)",
        options=list(COUNTRY_CONFIG),
        format_func=lambda c: f"{COUNTRY_CONFIG[c].name} ({c})",
        key="asset_country",
    )
    asset_cfg = COUNTRY_CONFIG[country_code]
    asset_extra_inputs: dict[str, str] = {}
    if asset_cfg.extra_inputs:
        st.caption(f"{asset_cfg.name} needs extra details before lookup (applied to every plate below):")
        asset_extra_inputs = {
            k: st.text_input(k.replace("_", " ").title(), key=f"asset_extra_{k}")
            for k in asset_cfg.extra_inputs
        }

    verify_mode = st.radio(
        "What do you want to do?",
        [
            "Verify a reported asset register (bulk, owner check)",
            "Quick lookup / spot check (no register needed)",
        ],
        key="asset_mode",
    )

    if verify_mode.startswith("Verify a reported"):
        registry_source = st.radio(
            "Registry data source",
            ["Live lookup (recommended)", "Upload a pre-run registry-check file"],
            horizontal=True,
            key="asset_registry_source",
        )

        ac1, ac2 = st.columns(2)
        with ac1:
            asset_files = st.file_uploader(
                "Reported assets register (plate / borrower / expected owner)",
                type=["csv", "xlsx", "xls"],
                accept_multiple_files=True,
                key="assets_up",
            )
        asset_check_files = None
        with ac2:
            if registry_source == "Upload a pre-run registry-check file":
                asset_check_files = st.file_uploader(
                    "Registry check results (independent evidence)",
                    type=["csv", "xlsx", "xls"],
                    accept_multiple_files=True,
                    key="asset_checks_up",
                )
            else:
                st.caption(
                    f"Each reported plate will be looked up live against {asset_cfg.name}'s "
                    "registry when you run verification below."
                )

        if st.button("Run asset verification", disabled=not asset_files, type="primary"):
            asset_dir = OUT_ROOT / "_asset_uploads"
            asset_paths = [_save_upload(f, asset_dir) for f in asset_files]
            expected_assets = load_expected_assets(asset_paths)

            if registry_source == "Upload a pre-run registry-check file":
                asset_check_paths = [_save_upload(f, asset_dir) for f in (asset_check_files or [])]
                registry_results = load_registry_results(asset_check_paths)
            else:
                client = make_client()
                registry_results = {}
                with st.spinner(
                    f"Looking up {len(expected_assets)} plate(s) against {asset_cfg.name}'s registry..."
                ):
                    for a in expected_assets:
                        plate = a["plate"]
                        try:
                            result = client.lookup(country_code, plate, asset_extra_inputs or None)
                            status = result.get("status") or (
                                "Found" if result.get("brand") or result.get("owner") else "No data"
                            )
                        except Exception as e:
                            result = {}
                            status = f"Error: {str(e)[:60]}"
                        registry_results[plate] = {
                            "plate": plate,
                            "status": status,
                            "propietario": result.get("owner", ""),
                            "marca": result.get("brand", ""),
                            "modelo": result.get("model", ""),
                            "verified_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        }

            asset_exceptions = verify_asset_existence(expected_assets, registry_results)
            st.session_state["asset_result"] = {
                "summaries": [
                    {
                        "plate": a["plate"],
                        "borrower": a.get("borrower", ""),
                        "expected_owner": a.get("expected_owner", ""),
                        "status": registry_results.get(a["plate"], {}).get("status", "(not checked)"),
                        "registered_owner": registry_results.get(a["plate"], {}).get("propietario", ""),
                        "brand": registry_results.get(a["plate"], {}).get("marca", ""),
                        "model": registry_results.get(a["plate"], {}).get("modelo", ""),
                        "verified_date": registry_results.get(a["plate"], {}).get("verified_date", ""),
                    }
                    for a in expected_assets
                ],
                "exceptions": asset_exceptions,
                "coverage": (
                    coverage_stats(
                        sum(1 for a in expected_assets if a["plate"] in registry_results), len(expected_assets)
                    )
                    if expected_assets
                    else None
                ),
            }

        asset_result = st.session_state.get("asset_result")
        if asset_result:
            summaries = asset_result["summaries"]
            cov = asset_result["coverage"] or {}
            st.caption(
                f"{cov.get('rows_ingested', 0)} of {len(summaries)} reported asset(s) checked against "
                f"the registry ({cov.get('coverage_pct', 0):.1f}%)."
            )
            st.dataframe(
                pd.DataFrame(summaries).rename(
                    columns={
                        "plate": "Plate",
                        "borrower": "Borrower",
                        "expected_owner": "Expected owner",
                        "status": "Registry status",
                        "registered_owner": "Registered owner",
                        "brand": "Brand",
                        "model": "Model",
                        "verified_date": "Verified date",
                    }
                ),
                width="stretch",
                hide_index=True,
            )

            if asset_result["exceptions"]:
                st.subheader(f"Exceptions ({len(asset_result['exceptions'])})")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {"Severity": e.severity, "Description": e.description, "Status": e.status}
                            for e in asset_result["exceptions"]
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.success("No asset exceptions.")
        else:
            st.info("Upload a reported assets register and run verification to see results.")

    else:
        st.caption(
            "Look up vehicles against the registry by plate, no collateral register needed — "
            "for ad hoc spot checks. Bulk via Excel upload, with an optional expected-vehicle "
            "column to classify Match/Partial/Mismatch."
        )

        mode = st.radio(
            "Input mode",
            ["Single plate", "Excel upload"],
            horizontal=True,
            key="veh_mode",
        )

        single_plates: list[str] = []
        uploaded_df: Any = None
        plate_col: Any = None
        expected_col: Any = None
        expected_label: str | None = None

        if mode == "Single plate":
            raw = st.text_input(
                "Plate(s) — comma or newline separated",
                placeholder="e.g. ABC-123, BCD-456",
                key="veh_single_raw",
            )
            single_plates = [p.strip() for p in raw.replace(",", "\n").splitlines() if p.strip()]
            st.caption(f"Ready to check: {len(single_plates)} plate(s).")
        else:
            upl = st.file_uploader(
                "Workbook with plates (and optionally an expected-vehicle column)",
                type=["xlsx", "xls", "csv"],
                key="veh_upload",
            )
            if upl is not None:
                try:
                    if upl.name.lower().endswith(".csv"):
                        uploaded_df = pd.read_csv(upl)
                    else:
                        uploaded_df = pd.read_excel(upl)
                except Exception as e:
                    st.error(f"Could not read workbook: {e}")
                    uploaded_df = None
            if uploaded_df is not None and not uploaded_df.empty:
                st.caption("Preview (first rows):")
                st.dataframe(uploaded_df.head(5), width="stretch", hide_index=True)
                cols = [str(c) for c in uploaded_df.columns]
                plate_col = st.selectbox("Plate column", options=cols, key="veh_plate_col")
                expected_col = st.selectbox(
                    "Expected vehicle column (optional — enables Match/Partial/Mismatch)",
                    options=["(none)"] + cols,
                    index=0,
                    key="veh_expected_col",
                )
                expected_label = None if expected_col == "(none)" else expected_col

        def _row_for_plate(
            plate: str,
            expected_desc: str = "",
            expected_label: str | None = None,
        ) -> dict[str, Any]:
            record = {k: "" for k in CANONICAL_FIELDS}
            record["plate"] = plate
            try:
                result = make_client().lookup(country_code, plate, asset_extra_inputs or None)
                record.update(result)
                record["status"] = result.get("status") or (
                    "Found" if result.get("brand") or result.get("owner") else "No data"
                )
                record["verdict"] = (
                    classify_expected(expected_desc, result.get("brand", ""), result.get("model", ""))
                    if expected_desc
                    else ""
                )
                record["expected_vehicle"] = expected_desc if (expected_desc and expected_label) else ""
            except Exception as e:
                record["status"] = f"Error: {str(e)[:60]}"
                record["verdict"] = ""
            record["verified_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return record

        if st.button("Run vehicle verification", type="primary", key="veh_run"):
            rows: list[dict[str, Any]] = []
            if mode == "Single plate":
                rows = [_row_for_plate(p) for p in single_plates]
            elif uploaded_df is not None and plate_col is not None:
                for _, rec in uploaded_df.iterrows():
                    plate = str(rec.get(plate_col, "")).strip()
                    if not plate:
                        continue
                    expected_desc = (
                        str(rec.get(expected_col, "")).strip() if expected_col and expected_col != "(none)" else ""
                    )
                    rows.append(_row_for_plate(plate, expected_desc, expected_label))
            else:
                st.warning("Provide plates (text or a workbook with a plate column) before running.")

            if rows:
                st.session_state["veh_result"] = {"rows": rows, "expected_label": expected_label}

        veh_result = st.session_state.get("veh_result")
        if veh_result:
            rows = veh_result["rows"]
            df = pd.DataFrame(verified_download_df(rows, veh_result["expected_label"]))
            cols_show = [
                c for c in df.columns if c != "Expected vehicle" or (c == "Expected vehicle" and veh_result["expected_label"])
            ]
            st.dataframe(df[cols_show], width="stretch", hide_index=True)

            found = sum(1 for r in rows if r.get("status") and not str(r["status"]).startswith(("Error", "No data", "")))
            not_found = sum(1 for r in rows if r.get("status") == "No data")
            errors = sum(1 for r in rows if str(r.get("status", "")).startswith("Error"))
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("Found", found)
            mc2.metric("Not found", not_found)
            mc3.metric("Errors", errors)
            mc4.metric("Total", len(rows))

            if veh_result["expected_label"]:
                issues = [r for r in rows if r.get("verdict") in ("Mismatch", "Partial Match")]
                if issues:
                    st.subheader(f"Issues Found ({len(issues)})")
                    st.dataframe(
                        pd.DataFrame(verified_download_df(issues, veh_result["expected_label"])),
                        width="stretch",
                        hide_index=True,
                    )
                else:
                    st.success("All expected vehicles matched the registry.")

            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Verification Results", index=False)
            st.download_button(
                "Download verification results (.xlsx)",
                data=buffer.getvalue(),
                file_name=f"vehicle_verification_{country_code}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="veh_download",
            )
        else:
            st.info("Choose a country, enter plates (or upload a workbook), then run.")

# --------------------------------------------------------------- Review tab
with tab_review:
    run = st.session_state["active_run"]
    run_dir = st.session_state["active_run_dir"]
    if run is None:
        st.info("Run a verification (or load a past run from the sidebar) to review its exceptions.")
    else:
        st.subheader(f"Run {run.id} — exceptions")
        signed_off = _signed_off_ids()

        pending = [e for e in run.exceptions if e.status in ("pending", "reviewed")]
        decided = [e for e in run.exceptions if e.status in ("approved", "rejected")]

        if not pending and not decided:
            st.info("No exceptions on this run.")

        for item in pending:
            with st.expander(f"[{item.kind}] {item.description}", expanded=False):
                st.caption(f"severity {item.severity:.2f} · status: {item.status}")
                reviewer = st.text_input("Reviewer", key=f"reviewer_{item.id}")
                note = st.text_area("Note", key=f"note_{item.id}")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Approve", key=f"approve_{item.id}", disabled=not reviewer):
                        approval.approved(item, reviewer, note, log=RUN_LOG)
                        record_feedback(RUN_LOG, exception_pattern(item), "approved", reviewer)
                        _persist_run(run, run_dir)
                        st.rerun()
                with c2:
                    if st.button("Reject", key=f"reject_{item.id}", disabled=not reviewer):
                        approval.reject(item, reviewer, note, log=RUN_LOG)
                        record_feedback(RUN_LOG, exception_pattern(item), "rejected", reviewer)
                        _persist_run(run, run_dir)
                        st.rerun()

        if decided:
            st.divider()
            st.subheader("Decided — awaiting Head of Risk sign-off")
            for item in decided:
                already = item.id in signed_off
                cols = st.columns([5, 2, 2])
                cols[0].markdown(
                    f"**[{item.status}]** {item.description}  \n"
                    f"<span style='font-size:.78rem;color:#666'>reviewer: {item.reviewer or '—'}"
                    f"{' · ' + item.review_note if item.review_note else ''}</span>",
                    unsafe_allow_html=True,
                )
                signer = cols[1].text_input("Signer", key=f"signer_{item.id}", label_visibility="collapsed",
                                             placeholder="Head of Risk")
                if already:
                    cols[2].success("Signed off")
                elif cols[2].button("Sign off", key=f"signoff_{item.id}", disabled=not signer):
                    approval.sign_off(item, signer, appr=True, log=RUN_LOG)
                    st.rerun()

# ---------------------------------------------------------------- Audit tab
with tab_audit:
    st.subheader("Run log")
    events = RUN_LOG.read()
    if events:
        df = pd.DataFrame(events[-100:])
        st.dataframe(df, width='stretch', hide_index=True)
    else:
        st.info("No runs logged yet.")

    st.subheader("Lead time — run start to final sign-off")
    lead = lead_times(RUN_LOG)
    if lead:
        df_lead = pd.DataFrame(lead)
        df_lead["elapsed"] = df_lead["elapsed_seconds"].map(_format_duration)
        st.dataframe(df_lead[["run_id", "started", "signed_off", "elapsed"]], width='stretch', hide_index=True)
    else:
        st.caption("No signed-off runs recorded yet.")

    st.subheader("Last verification per borrower")
    last_ver = last_verification_per_borrower(RUN_LOG, OUT_ROOT)
    if last_ver:
        st.dataframe(pd.DataFrame(last_ver)[["borrower", "run_id", "last_verified"]], width='stretch', hide_index=True)
        st.caption("Oldest verification first — borrowers not verified in the longest surface at the top.")
    else:
        st.caption("No completed runs logged yet.")

    st.subheader("Hardening log — LLM/judgement steps promoted to rules (B5)")
    hardening = HardeningLog(RUN_LOG).entries()
    if hardening:
        st.dataframe(pd.DataFrame(hardening), width='stretch', hide_index=True)
    else:
        st.caption("No promotions recorded yet.")

    with st.expander("Record a hardening promotion"):
        pattern = st.text_input("Pattern spotted", key="hard_pattern")
        change = st.text_input("Change made", key="hard_change")
        effect = st.text_input("Effect", key="hard_effect")
        if st.button("Record", disabled=not (pattern and change), key="hard_record"):
            promote_to_rule(RUN_LOG, pattern, change, effect)
            st.success("Recorded.")
            st.rerun()

# ------------------------------------------------------ Transaction matching
with tab_match:
    st.subheader("Detailed transaction matching (optional, best-effort)")
    st.caption(
        "reconcile.py (the Run tab) answers 'do the totals line up' — this answers 'which "
        "specific transaction has no counterpart on the other side.' It matches on each "
        "record's own description text, exact first then partial (substring) — useful when a "
        "narration echoes something from the reported side (e.g. a loan id), but most real "
        "statement pairs share little text verbatim. A low match rate here isn't itself an "
        "anomaly — it's a separate, on-demand report, not part of the main exception queue."
    )
    mc1, mc2 = st.columns(2)
    with mc1:
        match_reported_file = st.file_uploader(
            "Reported source (loan tape / ledger)",
            type=["csv", "xlsx", "xls"],
            key="match_reported_up",
        )
    with mc2:
        match_independent_file = st.file_uploader(
            "Independent source (bank / mobile statement)",
            type=["csv", "xlsx", "xls", "pdf"],
            key="match_independent_up",
        )

    if st.button("Run matching", disabled=not (match_reported_file and match_independent_file)):
        match_dir = OUT_ROOT / "_match_uploads"
        reported_path = _save_upload(match_reported_file, match_dir)
        independent_path = _save_upload(match_independent_file, match_dir)
        reported_rows = load_and_normalize([reported_path], sheet=SHEET_TAPE)
        independent_rows = (
            extract_pdf(independent_path, account_ref=independent_path.stem)
            if independent_path.suffix.lower() == ".pdf"
            else load_and_normalize([independent_path], sheet=SHEET_BANK)
        )
        result = match_transactions(reported_rows, independent_rows)
        st.session_state["match_result"] = result

    result = st.session_state.get("match_result")
    if result:
        m1, m2, m3 = st.columns(3)
        m1.metric("Matched", len(result["matched"]))
        m2.metric("In reported only", len(result["unmatched_reported"]))
        m3.metric("In independent only", len(result["unmatched_independent"]))

        report_df = build_match_report(result)
        st.dataframe(report_df, width="stretch", hide_index=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            report_df.to_excel(writer, sheet_name="Reconciliation", index=False)
        st.download_button(
            "Download reconciliation report (.xlsx)",
            data=buffer.getvalue(),
            file_name="transaction_match_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.info("Upload both sources and run matching to see results.")
