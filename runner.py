"""End-to-end runner for the Verifications Automation workflow.

Usage:
    python runner.py \
        --config config.yaml \
        --tape loan_tape.csv \
        --statements statement_a.pdf statement_b.csv \
        --ledger ledger.csv \
        --assets reported_assets.xlsx --asset-checks plates_peru.xlsx \
        --reported '{"collections": 12345, "disbursements": 6789, "cash_total": 100000}' \
        --out out/

All aggregation and reconciliation is deterministic (Phase 2). PDF statements
are parsed with rules (Phase 1); any rows below the confidence floor are
flagged for spot-check. A run report and the append-only run log are produced.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase0_foundations.config import load_config  # noqa: E402
from phase0_foundations.log import RunLog  # noqa: E402
from phase0_foundations.metrics import coverage as coverage_stats  # noqa: E402
from phase0_foundations.models import ExceptionItem, VerificationRun  # noqa: E402
from phase1_ingestion_parsing.assets import (  # noqa: E402
    load_expected_assets,
    load_registry_results,
)
from phase0_foundations.fx import FXConfig  # noqa: E402
from phase1_ingestion_parsing.extract import extract_pdf  # noqa: E402
from phase1_ingestion_parsing.fx_rates import FXFetchError, fetch_live_rates  # noqa: E402
from phase1_ingestion_parsing.ingest import (  # noqa: E402
    SHEET_LEDGER,
    SHEET_TAPE,
    load_and_normalize,
)
from phase2_verification_engine.assets import verify_asset_existence  # noqa: E402
from phase2_verification_engine.calculate import calculate_aggregates  # noqa: E402
from phase2_verification_engine.reconcile import estimate_gateway_fee, reconcile  # noqa: E402
from phase3_anomaly_reporting.anomaly import detect_anomalies  # noqa: E402
from phase3_anomaly_reporting.rank import rank_exceptions  # noqa: E402
from phase3_anomaly_reporting.reporter import build_report  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the verification workflow")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--tape", nargs="*", default=[], help="Loan-tape file(s)")
    parser.add_argument("--statements", nargs="*", default=[], help="Bank statement file(s) (pdf/csv)")
    parser.add_argument("--ledger", nargs="*", default=[], help="Ledger balance file(s)")
    parser.add_argument("--assets", nargs="*", default=[], help="Reported assets register file(s) (plate/borrower/expected_owner)")
    parser.add_argument("--asset-checks", nargs="*", default=[], help="Registry check result file(s) (e.g. vehicle_plate_peru output)")
    parser.add_argument("--reported", default="{}", help='JSON of reported totals, e.g. {"collections":100}')
    parser.add_argument(
        "--no-live-fx", action="store_true",
        help=(
            "Disable live FX rate lookup for non-base currencies found in the input. "
            "ON by default: the runner fetches live rates "
            "(fawazahmed0/currency-api, free, no key), falling back to config.yaml's "
            "static fx.rates table on failure. Pass this flag to force a reproducible "
            "run that never hits the network."
        ),
    )
    parser.add_argument("--out", default="out")
    args = parser.parse_args()

    cfg = load_config(args.config)
    log = RunLog(cfg.log_path)
    run_id = uuid.uuid4().hex[:12]
    run = VerificationRun(id=run_id, status="running", started_at=_now())

    log.append({"event": "run_started", "run_id": run_id, "inputs": vars(args)})

    try:
        # Phase 1: ingest tape + ledger; parse PDF statements + tabular statements.
        tape_rows = load_and_normalize(args.tape, sheet=SHEET_TAPE)
        ledger_rows = load_and_normalize(args.ledger, sheet=SHEET_LEDGER)

        bank_rows: list[dict] = []
        for s in args.statements:
            p = Path(s)
            if p.suffix.lower() == ".pdf":
                bank_rows.extend(extract_pdf(p, account_ref=p.stem))
            else:
                bank_rows.extend(load_and_normalize([p], sheet="bank"))

        all_rows = tape_rows + ledger_rows + bank_rows

        # SOP 1 FX normalization: live rates are fetched by default for every
        # non-base currency found in the input (same opt-out behavior as
        # app/streamlit_app.py's `_run_pipeline`). Pass --no-live-fx to skip
        # the network call and keep a byte-reproducible run against only
        # config.yaml's static fx.rates table.
        found_currencies = {(r.get("currency") or "").strip().upper() for r in all_rows}
        found_currencies.discard("")
        found_currencies.discard(cfg.fx.base_currency)

        effective_fx = cfg.fx
        live_fx_status: str | None = None
        if found_currencies and not args.no_live_fx:
            try:
                live_rates, as_of = fetch_live_rates(cfg.fx.base_currency, found_currencies)
                if live_rates:
                    effective_fx = FXConfig(base_currency=cfg.fx.base_currency, rates={**cfg.fx.rates, **live_rates})
                    live_fx_status = f"Live FX rates fetched for {', '.join(sorted(live_rates))} (as of {as_of})."
                else:
                    live_fx_status = (
                        f"Live FX source had no rate for {', '.join(sorted(found_currencies))}; "
                        "using config.yaml's static rates only."
                    )
            except FXFetchError as exc:
                live_fx_status = f"{exc} Using config.yaml's static rates only."

        # Asset existence verification: independent of the transaction sources
        # above — a reported register of pledged assets vs. a third-party
        # registry's own check (e.g. vehicle_plate_peru's plate lookup).
        expected_assets = load_expected_assets(args.assets)
        registry_results = load_registry_results(args.asset_checks)
        asset_coverage = (
            coverage_stats(sum(1 for a in expected_assets if a["plate"] in registry_results), len(expected_assets))
            if expected_assets
            else None
        )

        run.inputs = {
            "tape_files": args.tape,
            "statements": args.statements,
            "ledger_files": args.ledger,
            "asset_files": args.assets,
            "asset_check_files": args.asset_checks,
            "asset_coverage": asset_coverage,
            "live_fx_status": live_fx_status,
            **coverage_stats(len(all_rows), len(all_rows)),
        }

        # Phase 2: deterministic aggregation + reconciliation.
        aggregates = calculate_aggregates(all_rows, fx=effective_fx)
        run.aggregates = aggregates

        reported = json.loads(args.reported or "{}")
        recon = reconcile(aggregates, reported, cfg.thresholds, run_id=run_id)
        gateway_fee = estimate_gateway_fee(reported, aggregates, cfg.thresholds)
        run.aggregates["estimated_gateway_fee"] = gateway_fee["estimated_gateway_fee"]
        run.aggregates["estimated_gateway_fee_pct"] = gateway_fee["estimated_gateway_fee_pct"]
        run.aggregates["within_plausible_gateway_fee_range"] = gateway_fee["within_plausible_gateway_fee_range"]

        # Phase 3: anomaly detection + ranking.
        anomalies = detect_anomalies(all_rows, cfg.thresholds, run_id=run_id)
        asset_exceptions = verify_asset_existence(expected_assets, registry_results, run_id=run_id)
        exceptions: list[ExceptionItem] = rank_exceptions(recon + anomalies + asset_exceptions, cfg.thresholds)
        run.exceptions = exceptions
        run.status = "done"
        run.finished_at = _now()

        # Report + log.
        log.append({"event": "run_completed", "run_id": run_id, "aggregates": aggregates,
                    "exception_count": len(exceptions)})
        report_path = build_report(run, args.out, exceptions=exceptions, thresholds=cfg.thresholds)

        print(f"Run {run_id} done.")
        if live_fx_status:
            print(f"FX: {live_fx_status}")
        print(f"Aggregates: {aggregates}")
        print(f"Exceptions: {len(exceptions)}")
        print(f"Report: {report_path}")
        return 0
    except Exception as exc:  # noqa: BLE001 - surface for CLI
        run.status = "failed"
        log.append({"event": "run_failed", "run_id": run_id, "error": str(exc)})
        print(f"Run {run_id} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
