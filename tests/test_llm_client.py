"""Tests for phase0_foundations/llm_client.py — mocked HTTP, no live calls."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0_foundations.config import LLMConfig  # noqa: E402
from phase0_foundations import llm_client  # noqa: E402


def _mock_response(payload: dict):
    def _urlopen(*_args, **_kwargs):
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        return cm
    return _urlopen


def test_configured_provider_empty_when_unset():
    assert llm_client.configured_provider(LLMConfig(provider="")) == ""


def test_configured_provider_normalizes_case():
    assert llm_client.configured_provider(LLMConfig(provider="OpenAI")) == "openai"


def test_configured_provider_rejects_unknown_provider():
    assert llm_client.configured_provider(LLMConfig(provider="mistral")) == ""


def test_chat_raises_when_no_provider_configured():
    try:
        llm_client.chat([{"role": "user", "content": "hi"}], cfg=LLMConfig(provider=""))
        raise AssertionError("expected LLMError")
    except llm_client.LLMError:
        pass


def test_chat_raises_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        llm_client.chat([{"role": "user", "content": "hi"}], cfg=LLMConfig(provider="openai"))
        raise AssertionError("expected LLMError")
    except llm_client.LLMError:
        pass


def test_chat_openai_text_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    payload = {"choices": [{"message": {"content": "the answer is 42", "role": "assistant"}}]}
    with patch("urllib.request.urlopen", side_effect=_mock_response(payload)):
        result = llm_client.chat(
            [{"role": "user", "content": "what is the answer?"}],
            cfg=LLMConfig(provider="openai", model="gpt-4o-mini"),
        )
    assert result.text == "the answer is 42"
    assert result.tool_calls == []


def test_chat_openai_parses_tool_calls(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    payload = {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "function": {"name": "query_redshift", "arguments": json.dumps({"sql": "SELECT 1", "limit": 10})},
                }],
            }
        }]
    }
    with patch("urllib.request.urlopen", side_effect=_mock_response(payload)):
        result = llm_client.chat(
            [{"role": "user", "content": "drill down"}],
            cfg=LLMConfig(provider="openai"),
            tools=[{"type": "function", "function": {"name": "query_redshift", "parameters": {}}}],
        )
    assert result.text is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "query_redshift"
    assert result.tool_calls[0].arguments == {"sql": "SELECT 1", "limit": 10}


def test_chat_deepinfra_uses_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di-test")
    payload = {"choices": [{"message": {"content": "deepseek says hi", "role": "assistant"}}]}
    captured = {}

    def _urlopen(req, *_args, **_kwargs):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        return cm

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        result = llm_client.chat(
            [{"role": "user", "content": "hi"}],
            cfg=LLMConfig(provider="deepinfra"),
        )
    assert result.text == "deepseek says hi"
    assert captured["url"] == "https://api.deepinfra.com/v1/openai/chat/completions"
    assert captured["auth"] == "Bearer di-test"


def test_chat_deepinfra_defaults_to_deepseek_v4_flash_model(monkeypatch):
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di-test")
    payload = {"choices": [{"message": {"content": "ok"}}]}
    captured = {}

    def _urlopen(req, *_args, **_kwargs):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        return cm

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        llm_client.chat([{"role": "user", "content": "hi"}], cfg=LLMConfig(provider="deepinfra"))
    assert captured["body"]["model"] == "deepseek-ai/DeepSeek-V4-Flash-0731"


def test_deepinfra_completions_url_defaults_to_openai_compatible_base():
    assert llm_client._deepinfra_completions_url(LLMConfig(provider="deepinfra")) == (
        "https://api.deepinfra.com/v1/openai/chat/completions"
    )


def test_deepinfra_completions_url_readds_missing_openai_segment():
    # DeepInfra's confirmed working base is .../v1/openai (docs.deepinfra.com/quickstart);
    # a bare .../v1/ override still needs that segment, so it's added rather than
    # silently sending requests to a URL that 404s.
    cfg = LLMConfig(provider="deepinfra", deepinfra_base_url="https://api.deepinfra.com/v1/")
    assert llm_client._deepinfra_completions_url(cfg) == "https://api.deepinfra.com/v1/openai/chat/completions"


def test_deepinfra_completions_url_respects_full_override():
    cfg = LLMConfig(provider="deepinfra", deepinfra_base_url="https://proxy.internal/deepinfra/v1/openai")
    assert llm_client._deepinfra_completions_url(cfg) == (
        "https://proxy.internal/deepinfra/v1/openai/chat/completions"
    )


def test_chat_deepinfra_respects_explicit_model_override(monkeypatch):
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di-test")
    payload = {"choices": [{"message": {"content": "ok"}}]}
    captured = {}

    def _urlopen(req, *_args, **_kwargs):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        return cm

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        llm_client.chat(
            [{"role": "user", "content": "hi"}],
            cfg=LLMConfig(provider="deepinfra", model="deepseek-ai/DeepSeek-R1"),
        )
    assert captured["body"]["model"] == "deepseek-ai/DeepSeek-R1"


def test_chat_raises_when_deepinfra_key_missing(monkeypatch):
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    try:
        llm_client.chat([{"role": "user", "content": "hi"}], cfg=LLMConfig(provider="deepinfra"))
        raise AssertionError("expected LLMError")
    except llm_client.LLMError:
        pass


def test_assistant_message_deepinfra_uses_openai_shape_not_anthropic():
    result = llm_client.ChatResult(
        text=None,
        tool_calls=[llm_client.ToolCall(id="call_1", name="query_redshift", arguments={"sql": "SELECT 1"})],
    )
    msg = llm_client.assistant_message("deepinfra", result)
    assert msg["role"] == "assistant"
    assert msg["tool_calls"][0]["id"] == "call_1"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"sql": "SELECT 1"}


def test_tool_result_message_deepinfra_uses_openai_shape_not_anthropic():
    call = llm_client.ToolCall(id="call_1", name="query_redshift", arguments={})
    msg = llm_client.tool_result_message("deepinfra", call, '{"rows": []}')
    assert msg == {"role": "tool", "tool_call_id": "call_1", "content": '{"rows": []}'}


def test_chat_anthropic_text_and_tool_use(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    payload = {
        "content": [
            {"type": "text", "text": "let me check that"},
            {"type": "tool_use", "id": "toolu_1", "name": "query_redshift", "input": {"sql": "SELECT 1"}},
        ]
    }
    with patch("urllib.request.urlopen", side_effect=_mock_response(payload)):
        result = llm_client.chat(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
            cfg=LLMConfig(provider="anthropic"),
        )
    assert result.text == "let me check that"
    assert result.tool_calls[0].id == "toolu_1"
    assert result.tool_calls[0].arguments == {"sql": "SELECT 1"}


def test_assistant_message_openai_roundtrip():
    result = llm_client.ChatResult(
        text=None,
        tool_calls=[llm_client.ToolCall(id="call_1", name="query_redshift", arguments={"sql": "SELECT 1"})],
    )
    msg = llm_client.assistant_message("openai", result)
    assert msg["tool_calls"][0]["id"] == "call_1"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"sql": "SELECT 1"}


def test_tool_result_message_anthropic_shape():
    call = llm_client.ToolCall(id="toolu_1", name="query_redshift", arguments={})
    msg = llm_client.tool_result_message("anthropic", call, '{"rows": []}')
    assert msg["role"] == "user"
    assert msg["content"][0]["tool_use_id"] == "toolu_1"


if __name__ == "__main__":
    test_configured_provider_empty_when_unset()
    test_configured_provider_normalizes_case()
    test_configured_provider_rejects_unknown_provider()
    test_chat_raises_when_no_provider_configured()
    print("llm_client tests OK (run via pytest for the monkeypatch-dependent cases)")
