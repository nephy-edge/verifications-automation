"""Tests for phase4_human_review/query.py (Output 8 — NL querying), with
`llm_client.chat` mocked so no real provider call is made."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations import llm_client  # noqa: E402
from phase0_foundations.config import Config, LLMConfig  # noqa: E402
from phase0_foundations.models import ExceptionItem, VerificationRun  # noqa: E402
from phase4_human_review import query  # noqa: E402


def _cfg(provider="openai"):
    cfg = Config()
    cfg.llm = LLMConfig(provider=provider, model="gpt-4o-mini")
    return cfg


def _run(n_exceptions=2):
    return VerificationRun(
        id="run123",
        status="done",
        aggregates={"calculated_collections": 12466.98, "reported_collections": 0},
        exceptions=[
            ExceptionItem(id=f"run123:anom:{i}", kind="anomaly", severity=0.9 - i * 0.1,
                          description=f"exception {i}")
            for i in range(n_exceptions)
        ],
    )


def test_answer_question_returns_text_with_no_tool_calls():
    fake_result = llm_client.ChatResult(text="Calculated collections are $12,466.98.", tool_calls=[])
    with patch.object(llm_client, "chat", return_value=fake_result) as mock_chat:
        result = query.answer_question(
            "What are calculated collections?", run=_run(), cfg=_cfg(),
        )
    assert result.text == "Calculated collections are $12,466.98."
    assert result.tool_calls == []
    # No run_sql was passed, so no tool should have been offered to the model.
    _, kwargs = mock_chat.call_args
    assert kwargs["tools"] is None


def test_answer_question_offers_tool_only_when_run_sql_given():
    fake_result = llm_client.ChatResult(text="no drilldown needed", tool_calls=[])
    with patch.object(llm_client, "chat", return_value=fake_result) as mock_chat:
        query.answer_question(
            "anything", run=_run(), cfg=_cfg(), run_sql=lambda sql, limit: {"rows": []},
        )
    _, kwargs = mock_chat.call_args
    assert kwargs["tools"] is not None
    assert kwargs["tools"][0]["function"]["name"] == "query_redshift"


def test_answer_question_executes_tool_call_and_returns_final_text():
    call = llm_client.ToolCall(id="call_1", name="query_redshift", arguments={"sql": "SELECT 1", "limit": 5})
    first = llm_client.ChatResult(text=None, tool_calls=[call])
    second = llm_client.ChatResult(text="Found 3 matching rows.", tool_calls=[])
    executed_sql = {}

    def fake_run_sql(sql, limit):
        executed_sql["sql"] = sql
        executed_sql["limit"] = limit
        return {"columns": ["id"], "rows": [[1], [2], [3]], "row_count": 3}

    with patch.object(llm_client, "chat", side_effect=[first, second]):
        result = query.answer_question(
            "drill into the ledger", run=_run(), cfg=_cfg(), run_sql=fake_run_sql,
        )

    assert executed_sql == {"sql": "SELECT 1", "limit": 5}
    assert result.text == "Found 3 matching rows."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].row_count == 3
    assert result.tool_calls[0].error is None


def test_answer_question_logs_tool_error_without_crashing():
    call = llm_client.ToolCall(id="call_1", name="query_redshift", arguments={"sql": "SELECT * FROM x"})
    first = llm_client.ChatResult(text=None, tool_calls=[call])
    second = llm_client.ChatResult(text="That query failed; here's what I know without it.", tool_calls=[])

    def failing_run_sql(sql, limit):
        raise RuntimeError("Schema 'x' is not in the approved allowlist")

    with patch.object(llm_client, "chat", side_effect=[first, second]):
        result = query.answer_question(
            "drill into x", run=_run(), cfg=_cfg(), run_sql=failing_run_sql,
        )

    assert "allowlist" in result.tool_calls[0].error
    assert result.text.startswith("That query failed")


def test_answer_question_stops_after_max_rounds():
    call = llm_client.ToolCall(id="call_1", name="query_redshift", arguments={"sql": "SELECT 1"})
    always_calls = llm_client.ChatResult(text=None, tool_calls=[call])
    with patch.object(llm_client, "chat", return_value=always_calls):
        result = query.answer_question(
            "loop forever", run=_run(), cfg=_cfg(), run_sql=lambda sql, limit: {"row_count": 0},
        )
    assert "query-round limit" in result.text
    assert len(result.tool_calls) == query.MAX_TOOL_ROUNDS


def test_run_context_json_truncates_large_exception_lists():
    run = _run(n_exceptions=query.MAX_EXCEPTIONS_IN_CONTEXT + 5)
    ctx = query._run_context_json(run)  # noqa: SLF001 - testing the truncation contract directly
    import json as _json
    parsed = _json.loads(ctx)
    assert parsed["exceptions_truncated"] is True
    assert len(parsed["exceptions"]) == query.MAX_EXCEPTIONS_IN_CONTEXT
    assert parsed["exception_count_total"] == query.MAX_EXCEPTIONS_IN_CONTEXT + 5
    # highest severity kept, not an arbitrary slice
    assert parsed["exceptions"][0]["severity"] >= parsed["exceptions"][-1]["severity"]


if __name__ == "__main__":
    test_answer_question_returns_text_with_no_tool_calls()
    test_answer_question_offers_tool_only_when_run_sql_given()
    test_answer_question_executes_tool_call_and_returns_final_text()
    test_answer_question_logs_tool_error_without_crashing()
    test_answer_question_stops_after_max_rounds()
    test_run_context_json_truncates_large_exception_lists()
    print("query tests OK")
