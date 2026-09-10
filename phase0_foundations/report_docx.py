"""Working paper as a formatted Word document + a well-typed email body.

The watcher emails its report after every change-triggered run. Instead of the
raw Markdown, the email carries a polished .docx working paper (title, run
metadata, aggregates, statement-side breakdown, exception summary, forensic
queue) plus the machine-readable .json, and the email body itself is a clean
HTML summary rather than a bare "see attached" line.

`build_report_docx` is deterministic (numbers only ever come from the run's
own aggregates/inputs/exceptions) and `email_body_html` mirrors the same data
in email-friendly HTML.
"""

from __future__ import annotations

import html
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from docx import Document

from phase0_foundations.models import VerificationRun


def _fmt(x) -> str:
    return f"{x:,.2f}" if isinstance(x, float) else str(x)


def _exceptions_summary(run: VerificationRun) -> tuple[int, list[tuple[str, int]], list[dict]]:
    """(total, [(kind, count)], top-by-severity rows) from run.exceptions."""
    total = len(run.exceptions)
    kinds = Counter(e.kind for e in run.exceptions)
    top = sorted(run.exceptions, key=lambda e: (e.severity, e.id), reverse=True)[:20]
    rows = [
        {
            "severity": e.severity,
            "kind": e.kind,
            "description": e.description,
        }
        for e in top
    ]
    return total, sorted(kinds.items(), key=lambda kv: -kv[1]), rows


def _add_metadata_table(doc: Document, run: VerificationRun) -> None:
    inputs = run.inputs or {}
    rows = [
        ("Run id", run.id),
        ("Status", run.status),
        ("Borrower", inputs.get("borrower", "")),
        ("Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
        ("Independent source present", inputs.get("independent_source_present")),
        ("Bank statement rows", inputs.get("bank_statement_count")),
        ("Coverage", f"{inputs.get('coverage_pct')}%"),
        ("FX base currency", run.aggregates.get("fx_base_currency", "")),
        ("Currencies seen", ", ".join(run.aggregates.get("currencies_seen") or [])),
        ("Reconciliation scope", inputs.get("reconciliation_scope", "")),
    ]
    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    for k, v in rows:
        cells = table.add_row().cells
        cells[0].text = str(k)
        cells[1].text = str(v)


def _add_key_value_table(doc: Document, title: str, mapping: dict) -> None:
    if not mapping:
        return
    doc.add_heading(title, level=2)
    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    for k, v in mapping.items():
        cells = table.add_row().cells
        cells[0].text = str(k)
        cells[1].text = _fmt(v)


def _add_breakdown(doc: Document, run: VerificationRun) -> None:
    bd = (run.inputs or {}).get("statement_breakdown")
    if not bd:
        return
    base = bd.get("totals", {}).get("fx_base_currency", "USD")
    doc.add_heading("Statement-side breakdown (independent bank/mobile vs tape)", level=1)

    t = bd.get("totals", {})
    _add_key_value_table(doc, f"Totals ({base})", {
        "Bank in": t.get("bank_in", 0),
        "Bank out": t.get("bank_out", 0),
        "Bank cash total": t.get("bank_cash_total", 0),
        "Bank rows": t.get("bank_rows", 0),
    })

    def _rows_table(store: dict, title: str, cols: tuple[str, str, str, str]) -> None:
        if not store:
            return
        doc.add_heading(title, level=2)
        table = doc.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        for i, h in enumerate(cols):
            hdr[i].text = h
        for key, s in sorted(store.items()):
            cells = table.add_row().cells
            cells[0].text = str(key)
            cells[1].text = str(s.get("rows", 0))
            cells[2].text = _fmt(s.get("in", 0))
            cells[3].text = _fmt(s.get("out", 0))

    _rows_table(bd.get("by_statement", {}), f"By statement ({base})",
                ("statement", "rows", "in", "out"))
    _rows_table(bd.get("by_category", {}), f"By category ({base})",
                ("category", "rows", "in", "out"))

    by_month = bd.get("by_month", {})
    if by_month:
        doc.add_heading(f"By month ({base}) — tape vs bank", level=2)
        table = doc.add_table(rows=1, cols=5)
        table.style = "Table Grid"
        for i, h in enumerate(("month", "tape in", "tape out", "bank in", "bank out")):
            table.rows[0].cells[i].text = h
        for m in sorted(by_month):
            b = by_month[m]
            if not b.get("bank_rows") and not b.get("tape_rows"):
                continue
            cells = table.add_row().cells
            cells[0].text = m
            cells[1].text = f"{b.get('tape_in', 0):,.0f}"
            cells[2].text = f"{b.get('tape_out', 0):,.0f}"
            cells[3].text = f"{b.get('bank_in', 0):,.0f}"
            cells[4].text = f"{b.get('bank_out', 0):,.0f}"


def _add_exceptions(doc: Document, run: VerificationRun) -> None:
    total, kinds, top = _exceptions_summary(run)
    doc.add_heading(f"Exceptions ({total})", level=1)
    if total == 0:
        doc.add_paragraph("No exceptions flagged.")
        return

    doc.add_heading("By kind", level=2)
    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    for i, h in enumerate(("kind", "count")):
        table.rows[0].cells[i].text = h
    for kind, n in kinds:
        cells = table.add_row().cells
        cells[0].text = kind
        cells[1].text = str(n)

    doc.add_heading(f"Highest severity ({len(top)} of {total} shown)", level=2)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    for i, h in enumerate(("severity", "kind", "description")):
        table.rows[0].cells[i].text = h
    for r in top:
        cells = table.add_row().cells
        cells[0].text = f"{r['severity']:.2f}"
        cells[1].text = r["kind"]
        cells[2].text = r["description"]
    if total > len(top):
        doc.add_paragraph(f"{total - len(top)} lower-severity exception(s) not listed — "
                          "see the .json working paper for the full list.")


def build_report_docx(out_dir: str | Path, run: VerificationRun) -> Path:
    """Write the working paper as a formatted .docx next to the .json/.md."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.core_properties.title = f"Verification report - run {run.id}"

    inputs = run.inputs or {}
    doc.add_heading("Verification Report", level=0)
    sub = doc.add_paragraph()
    sub.add_run(f"{inputs.get('borrower', '')} — run {run.id}").bold = True
    sub.add_run(f"  ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})")

    doc.add_heading("Run metadata", level=1)
    _add_metadata_table(doc, run)

    doc.add_heading("Independent aggregates (deterministic)", level=1)
    _add_key_value_table(doc, "Aggregates", {
        k: v for k, v in run.aggregates.items()
        if not isinstance(v, (list, dict))
    })

    _add_breakdown(doc, run)
    _add_exceptions(doc, run)

    doc.add_heading("Human review", level=1)
    pending = [e for e in run.exceptions if e.status == "pending"]
    doc.add_paragraph(f"{len(pending)} exception(s) awaiting review and sign-off.")

    path = out / f"run_{run.id}.docx"
    doc.save(str(path))
    return path


def email_body_html(run: VerificationRun) -> str:
    """A well-typed HTML summary of the run for the email body."""
    inputs = run.inputs or {}
    ag = run.aggregates
    total = len(run.exceptions)
    bd = inputs.get("statement_breakdown", {})
    t = bd.get("totals", {})
    base = t.get("fx_base_currency", "USD")

    def esc(x) -> str:
        return html.escape(str(x))

    rows = ""
    reported_vs_calc = [
        ("Collections", ag.get("collections"), ag.get("calculated_collections")),
        ("Disbursements", ag.get("disbursements"), ag.get("calculated_disbursements")),
        ("Cash total", ag.get("cash_total"), ag.get("calculated_cash_total")),
    ]
    for label, reported, calc in reported_vs_calc:
        rows += (f"<tr><td>{esc(label)}</td>"
                 f"<td>{_fmt(reported)}</td>"
                 f"<td>{_fmt(calc)}</td></tr>")

    return f"""<html><body style="font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #222;">
<h2 style="margin-bottom:4px;">Verification Report — {esc(inputs.get('borrower',''))}</h2>
<p style="margin-top:0; color:#666;">run {esc(run.id)} · status {esc(run.status)}</p>
<p>Independent source present: <b>{esc(inputs.get('independent_source_present'))}</b>
 ({esc(inputs.get('bank_statement_count'))} bank statement rows) ·
 coverage {esc(inputs.get('coverage_pct'))}% · FX base {esc(ag.get('fx_base_currency'))}</p>
<table style="border-collapse:collapse;" cellpadding="6" cellspacing="0">
<tr style="background:#f0f0f0;"><th align="left">Metric</th><th align="right">Reported (tape)</th><th align="right">Calculated (statements)</th></tr>
{rows}
</table>
<h3>Statement-side totals ({esc(base)})</h3>
<p>Bank in <b>{_fmt(t.get('bank_in', 0))}</b> · bank out <b>{_fmt(t.get('bank_out', 0))}</b> ·
 bank cash total <b>{_fmt(t.get('bank_cash_total', 0))}</b></p>
<h3>Exceptions</h3>
<p><b>{total}</b> flagged — <a href="#">see the attached working paper (.docx) for the full detail</a>.</p>
<hr style="border:none;border-top:1px solid #ddd;"/>
<p style="color:#888;font-size:12px;">Generated by the Verifications loan-tape watcher (SOP 1).</p>
</body></html>"""
