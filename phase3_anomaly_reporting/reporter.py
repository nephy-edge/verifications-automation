"""Working papers + exception report generation (A3 step 6, B2/reporter.py).

Template-driven, deterministic. Produces a machine-readable JSON report and a
plain-text summary (Markdown-style) that a human can review. Supporting evidence
(stable record keys) is attached to each exception.
"""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Sequence

from phase0_foundations.config import Thresholds
from phase0_foundations.models import ExceptionItem, VerificationRun
from phase3_anomaly_reporting.rank import forensic_route, mandatory_fraud_referrals


def _render_statement_breakdown(inputs: dict) -> list[str]:
    """Render the statement-side breakdown section (computed by the watcher's
    `_statement_breakdown`) as Markdown. Returns [] when absent, so a caller
    that never populated it (e.g. tape-only runs, older reports) gets no
    section at all — backward compatible."""
    bd = inputs.get("statement_breakdown")
    if not bd:
        return []
    base = bd.get("totals", {}).get("fx_base_currency", "USD")
    lines = ["## Statement-side breakdown (independent bank/mobile vs tape)", ""]

    t = bd.get("totals", {})
    lines.append(
        f"- Totals ({base}): bank in {t.get('bank_in', 0):,.2f} / "
        f"bank out {t.get('bank_out', 0):,.2f} / total {t.get('bank_cash_total', 0):,.2f} "
        f"({t.get('bank_rows', 0)} bank rows)"
    )
    lines.append("")

    by_stmt = bd.get("by_statement") or {}
    if by_stmt:
        lines.append(f"### By statement ({base})")
        lines.append("")
        lines.append("| statement | rows | in | out |")
        lines.append("|---|---:|---:|---:|")
        for ref, s in sorted(by_stmt.items()):
            lines.append(f"| {ref} | {s['rows']} | {s['in']:,.2f} | {s['out']:,.2f} |")
        lines.append("")

    by_cat = bd.get("by_category") or {}
    if by_cat:
        lines.append(f"### By category ({base})")
        lines.append("")
        lines.append("| category | rows | in | out |")
        lines.append("|---|---:|---:|---:|")
        for cat, s in sorted(by_cat.items(), key=lambda kv: -(kv[1]["in"] + kv[1]["out"])):
            lines.append(f"| {cat} | {s['rows']} | {s['in']:,.2f} | {s['out']:,.2f} |")
        lines.append("")

    by_month = bd.get("by_month") or {}
    if by_month:
        lines.append(f"### By month ({base}) — tape vs bank")
        lines.append("")
        lines.append("| month | tape in | tape out | bank in | bank out |")
        lines.append("|---|---:|---:|---:|---:|")
        for m in sorted(by_month):
            b = by_month[m]
            if not b.get("bank_rows") and not b.get("tape_rows"):
                continue
            lines.append(
                f"| {m} | {b.get('tape_in', 0):,.0f} | {b.get('tape_out', 0):,.0f} | "
                f"{b.get('bank_in', 0):,.0f} | {b.get('bank_out', 0):,.0f} |"
            )
        lines.append("")
    return lines


def _markdown(run: VerificationRun, thresholds: Thresholds | None = None) -> str:
    lines: list[str] = []
    lines.append(f"# Verification report - run {run.id}")
    lines.append("")
    lines.append("## Independent aggregates (deterministic)")
    lines.append("")
    for k, v in run.aggregates.items():
        if isinstance(v, float):
            lines.append(f"- {k}: {v:,.2f}")
        else:
            lines.append(f"- {k}: {v}")
    lines.append("")

    inputs = run.inputs or {}
    population = inputs.get("population_size")
    ingested = inputs.get("rows_ingested")
    pct = inputs.get("coverage_pct")
    asset_coverage = inputs.get("asset_coverage")
    if (population is not None and ingested is not None and pct is not None) or asset_coverage:
        lines.append("## Coverage")
        lines.append("")
        if population is not None and ingested is not None and pct is not None:
            lines.append(f"- {ingested} of {population} transactions tested ({pct:.1f}%)")
        if asset_coverage:
            lines.append(
                f"- {asset_coverage['rows_ingested']} of {asset_coverage['population_size']} "
                f"reported assets checked against the registry ({asset_coverage['coverage_pct']:.1f}%)"
            )
        lines.append("")

    unmapped = run.aggregates.get("unmapped_currencies") or inputs.get("unmapped_currencies")
    if unmapped:
        base = run.aggregates.get("fx_base_currency", "the base currency")
        lines.append("## Currency")
        lines.append("")
        lines.append(
            f"No FX rate configured for: {', '.join(unmapped)} — these amounts were summed "
            f"unconverted against a {base} baseline. Add a rate under `fx.rates` in "
            "config.yaml before trusting totals that mix these currencies."
        )
        lines.append("")

    lines.extend(_render_statement_breakdown(inputs))

    lines.append(f"## Exceptions ({len(run.exceptions)})")
    lines.append("")
    if not run.exceptions:
        lines.append("No exceptions flagged.")
    for e in run.exceptions:
        line = f"- [{e.kind}] {e.description} -> status={e.status}"
        if e.evidence:
            line += f" (evidence: {', '.join(e.evidence)})"
        lines.append(line)
    lines.append("")

    if thresholds is not None:
        forensic = forensic_route(run.exceptions, thresholds)
        lines.append(
            f"## Forensic review queue (severity >= {thresholds.anomaly_score_high:.2f})"
        )
        lines.append("")
        if not forensic:
            lines.append("No exceptions at or above the forensic-review threshold.")
        for e in forensic:
            lines.append(f"- [{e.kind}] {e.description}")
        lines.append("")

    mandatory = mandatory_fraud_referrals(run.exceptions)
    lines.append("## Mandatory fraud referrals (intent-based, independent of severity ranking)")
    lines.append("")
    if not mandatory:
        lines.append("None.")
    for e in mandatory:
        lines.append(f"- [{e.kind}] {e.description}")
    lines.append("")

    lines.append("## Human review")
    lines.append("")
    pending = [e for e in run.exceptions if e.status == "pending"]
    lines.append(f"{len(pending)} exception(s) awaiting review and sign-off.")
    return "\n".join(lines)


def build_report(
    run: VerificationRun,
    out_dir: str | Path,
    exceptions: Sequence[ExceptionItem] | None = None,
    thresholds: Thresholds | None = None,
) -> Path:
    """Write the JSON + Markdown report for a run. Returns path to the JSON report.

    ``thresholds`` is optional (older call sites keep working without a
    forensic-review section), but passing it surfaces the A3/D1 forensic
    route (severity >= B3's ``anomaly_score_high``) in both report formats
    instead of leaving it computed-but-unsurfaced.
    """
    run.exceptions = list(exceptions) if exceptions is not None else run.exceptions
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    report = {
        "run_id": run.id,
        "status": run.status,
        "inputs": run.inputs,
        "aggregates": run.aggregates,
        "coverage": {
            "rows_ingested": run.inputs.get("rows_ingested"),
            "population_size": run.inputs.get("population_size"),
            "coverage_pct": run.inputs.get("coverage_pct"),
            "assets": run.inputs.get("asset_coverage"),
        },
        "exceptions": [
            {
                "id": e.id,
                "kind": e.kind,
                "severity": e.severity,
                "description": e.description,
                "evidence": e.evidence,
                "status": e.status,
            }
            for e in run.exceptions
        ],
    }
    if thresholds is not None:
        forensic = forensic_route(run.exceptions, thresholds)
        report["forensic_review"] = {
            "threshold": thresholds.anomaly_score_high,
            "count": len(forensic),
            "exception_ids": [e.id for e in forensic],
        }

    mandatory = mandatory_fraud_referrals(run.exceptions)
    report["mandatory_fraud_referrals"] = {
        "count": len(mandatory),
        "exception_ids": [e.id for e in mandatory],
    }

    json_path = out / f"run_{run.id}.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    md_path = out / f"run_{run.id}.md"
    md_path.write_text(_markdown(run, thresholds), encoding="utf-8")
    return json_path
