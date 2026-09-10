"""Output 8 — Interactive auditor NL querying.

Lets a verification officer ask a question in plain language about the
current run and get a grounded answer: the run's already-computed aggregates,
coverage, and flagged exceptions are handed to the LLM as-is, with a hard
instruction never to recompute or "correct" a figure (same discipline as
`phase3_anomaly_reporting/prompts/anomaly_prompt.txt`). If the run summary
doesn't answer the question, the model may call `query_redshift` — a
read-only, schema-allowlisted, row-capped drill-down into the warehouse via
the sibling redshift-api service (see redshift-api/app/main.py `/query`).

The actual HTTP call for that tool is injected as `run_sql` so this module
stays decoupled from the app's auth/URL wiring and is trivially testable with
a fake.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

from phase0_foundations import llm_client
from phase0_foundations.config import Config
from phase0_foundations.models import VerificationRun

MAX_TOOL_ROUNDS = 4
MAX_EXCEPTIONS_IN_CONTEXT = 25

_SYSTEM_PROMPT = """You are a verification analyst assistant helping a human auditor
interpret the results of an already-completed, deterministic verification run.

You are given the run's independently-calculated aggregates, reconciliation
coverage, and flagged exceptions (with rules-based severity scores) as JSON.

Hard constraints:
- Treat every number in the run JSON as given and final. Do NOT recompute,
  re-derive, or "correct" any aggregate, variance, or severity score.
- Do NOT make credit decisions or recommend client communications.
- When you cite a figure or exception, reference its field name or exception
  id so the auditor can find it in the working paper.
- If the run JSON doesn't answer the question and a query_redshift tool is
  available, you may call it to pull underlying ledger/loan-tape rows —
  state plainly what you queried and show the auditor the SQL you ran.
- If you still don't know, say so. Never guess a number.
"""

_QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_redshift",
        "description": (
            "Run a read-only SELECT/WITH query against the warehouse via the "
            "redshift-api gateway (dev_/uat_/prd_ana__/dbt_source schemas "
            "only; DDL/DML is rejected server-side). Use it to drill into "
            "raw ledger/loan-tape rows that aren't in the run summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT or WITH statement."},
                "limit": {"type": "integer", "description": "Max rows to return (default 100)."},
            },
            "required": ["sql"],
        },
    },
}


@dataclass
class ToolCallLog:
    sql: str
    limit: int
    row_count: int | None
    error: str | None = None


@dataclass
class AnswerResult:
    text: str
    tool_calls: list[ToolCallLog] = field(default_factory=list)


def _run_context_json(run: VerificationRun, max_exceptions: int = MAX_EXCEPTIONS_IN_CONTEXT) -> str:
    exceptions = sorted(run.exceptions, key=lambda e: e.severity, reverse=True)
    truncated = len(exceptions) > max_exceptions
    shown = exceptions[:max_exceptions]
    payload = {
        "run_id": run.id,
        "status": run.status,
        "aggregates": run.aggregates,
        "exceptions": [
            {
                "id": e.id,
                "kind": e.kind,
                "severity": e.severity,
                "description": e.description,
                "status": e.status,
                "reviewer": e.reviewer,
            }
            for e in shown
        ],
        "exception_count_total": len(run.exceptions),
        "exceptions_truncated": truncated,
    }
    return json.dumps(payload, default=str)


def answer_question(
    question: str,
    *,
    run: VerificationRun,
    cfg: Config,
    history: list[dict[str, str]] | None = None,
    run_sql: Callable[[str, int], dict[str, Any]] | None = None,
) -> AnswerResult:
    """Answer `question` about `run`, optionally drilling into the warehouse.

    `run_sql(sql, limit) -> {"columns": [...], "rows": [...], "row_count": n}`
    executes the query (e.g. against redshift-api's `/query`). Pass None to
    disable drill-down and answer from the run summary alone.

    Raises `llm_client.LLMError` if no provider is configured or the call
    fails — the caller (Streamlit tab) surfaces this rather than fabricating
    an answer.
    """
    provider = llm_client.configured_provider(cfg.llm)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "system", "content": f"Current run JSON:\n{_run_context_json(run)}"},
    ]
    messages.extend(history or [])
    messages.append({"role": "user", "content": question})

    tools = [_QUERY_TOOL] if run_sql is not None else None
    tool_log: list[ToolCallLog] = []

    for _ in range(MAX_TOOL_ROUNDS):
        result = llm_client.chat(messages, cfg=cfg.llm, tools=tools)
        if not result.tool_calls:
            return AnswerResult(text=result.text or "(no answer returned)", tool_calls=tool_log)

        messages.append(llm_client.assistant_message(provider, result))
        for call in result.tool_calls:
            if call.name != "query_redshift" or run_sql is None:
                output: dict[str, Any] = {"error": "query_redshift is not available in this session"}
                tool_log.append(ToolCallLog(sql=call.arguments.get("sql", ""), limit=0, row_count=None,
                                             error=output["error"]))
            else:
                sql = str(call.arguments.get("sql", ""))
                limit = int(call.arguments.get("limit") or 100)
                try:
                    output = run_sql(sql, limit)
                    tool_log.append(ToolCallLog(sql=sql, limit=limit, row_count=output.get("row_count")))
                except Exception as exc:  # noqa: BLE001 - surface the DB/API error to the model and the log
                    output = {"error": str(exc)}
                    tool_log.append(ToolCallLog(sql=sql, limit=limit, row_count=None, error=str(exc)))
            messages.append(llm_client.tool_result_message(provider, call, json.dumps(output, default=str)))

    return AnswerResult(
        text="Reached the query-round limit without a final answer — try narrowing the question.",
        tool_calls=tool_log,
    )
