"""Provider-agnostic LLM chat client (Output 8: interactive auditor NL querying).

Talks to OpenAI, Anthropic, or DeepInfra (e.g. for DeepSeek models) over
their plain REST APIs via stdlib `urllib` (no SDK dependency, matching the
`urllib`-only convention already used for Verifik and the Redshift Query API
— see vehicle_verify.py / streamlit_app.py). DeepInfra exposes an
OpenAI-compatible `/v1/openai/chat/completions` endpoint, so it reuses the
same request/response handling as OpenAI (`_chat_openai`) with a different
base URL, default model, and API key env var.

Provider and model are read from `config.yaml`'s `llm` block (LLMConfig).
The API key is never stored in config; it comes from the provider's
conventional env var (OPENAI_API_KEY / ANTHROPIC_API_KEY / DEEPINFRA_API_KEY).

Tool calling is normalized to a single OpenAI-style tool schema
(`{"type": "function", "function": {...}}`) on the way in; the two provider
wire formats are translated to/from `ChatResult` here so callers (e.g.
phase4_human_review/query.py) never branch on provider.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from phase0_foundations.config import LLMConfig


class LLMError(RuntimeError):
    """Raised when the configured provider/model/key is missing, or the API call fails."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResult:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


_PROVIDER_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
}

_DEFAULT_MODEL = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
    # Override via config.yaml's llm.model for another catalog entry — see
    # https://deepinfra.com/deepseek.
    "deepinfra": "deepseek-ai/DeepSeek-V4-Flash-0731",
}

# Providers whose wire format is OpenAI's chat-completions schema (request
# and response shape, including tool calling). Anthropic is the only
# non-member so far; keep this in sync with any new OpenAI-compatible
# provider added to _PROVIDER_ENV.
_OPENAI_COMPATIBLE = {"openai", "deepinfra"}

_OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

# DeepInfra's confirmed OpenAI-compatible base (per its own quickstart docs:
# https://docs.deepinfra.com/quickstart) is "https://api.deepinfra.com/v1/openai"
# — the SDK-style base a client appends "/chat/completions" to. Overridable via
# config.yaml's llm.deepinfra_base_url (e.g. to point at a proxy); if you pass
# just "https://api.deepinfra.com/v1" the "/openai" segment is still required
# by DeepInfra's API, so it's re-added rather than silently hitting a 404.
_DEFAULT_DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"


def _deepinfra_completions_url(cfg: LLMConfig) -> str:
    base = (cfg.deepinfra_base_url or _DEFAULT_DEEPINFRA_BASE_URL).rstrip("/")
    if not base.endswith("/openai"):
        base = f"{base}/openai"
    return f"{base}/chat/completions"


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LLMError(f"LLM API error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"LLM API unreachable: {exc}") from exc


def _api_key(provider: str) -> str:
    env_name = _PROVIDER_ENV[provider]
    key = os.getenv(env_name, "")
    if not key:
        raise LLMError(f"{env_name} is not set — required for llm.provider='{provider}'")
    return key


def configured_provider(cfg: LLMConfig) -> str:
    """Normalized provider name, or '' if NL querying is disabled (rules-only)."""
    provider = (cfg.provider or "").strip().lower()
    return provider if provider in _PROVIDER_ENV else ""


def chat(
    messages: list[dict[str, Any]],
    *,
    cfg: LLMConfig,
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    timeout: float = 60,
) -> ChatResult:
    """Send one chat turn to the configured provider.

    Raises LLMError if no provider is configured, the API key env var is
    unset, or the HTTP call fails — callers should surface this to the user
    rather than falling back to a guessed answer.
    """
    provider = configured_provider(cfg)
    if not provider:
        raise LLMError(
            "No LLM provider configured — set llm.provider to 'openai', "
            "'anthropic', or 'deepinfra' in config.yaml to enable NL querying."
        )
    model = cfg.model or _DEFAULT_MODEL[provider]
    key = _api_key(provider)
    temp = cfg.parse_temperature if temperature is None else temperature

    if provider in _OPENAI_COMPATIBLE:
        base_url = _deepinfra_completions_url(cfg) if provider == "deepinfra" else _OPENAI_CHAT_COMPLETIONS_URL
        return _chat_openai(
            messages, model=model, key=key, tools=tools,
            temperature=temp, max_tokens=cfg.max_tokens, timeout=timeout,
            base_url=base_url,
        )
    return _chat_anthropic(
        messages, model=model, key=key, tools=tools,
        temperature=temp, max_tokens=cfg.max_tokens, timeout=timeout,
    )


def _chat_openai(messages, *, model, key, tools, temperature, max_tokens, timeout, base_url) -> ChatResult:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    data = _post_json(
        base_url,
        {"Authorization": f"Bearer {key}"},
        payload,
        timeout,
    )
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    calls = [
        ToolCall(
            id=tc["id"],
            name=tc["function"]["name"],
            arguments=json.loads(tc["function"].get("arguments") or "{}"),
        )
        for tc in (msg.get("tool_calls") or [])
    ]
    return ChatResult(text=msg.get("content"), tool_calls=calls, raw=data)


def _chat_anthropic(messages, *, model, key, tools, temperature, max_tokens, timeout) -> ChatResult:
    # Anthropic takes `system` separately from the turn history.
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [m for m in messages if m["role"] != "system"]
    payload: dict[str, Any] = {
        "model": model,
        "system": system,
        "messages": turns,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ]
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        payload,
        timeout,
    )
    content = data.get("content") or []
    text_parts = [b["text"] for b in content if b.get("type") == "text"]
    calls = [
        ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
        for b in content
        if b.get("type") == "tool_use"
    ]
    return ChatResult(text="\n".join(text_parts) or None, tool_calls=calls, raw=data)


def assistant_message(provider: str, result: ChatResult) -> dict[str, Any]:
    """Provider-native form of `result`, to append to `messages` before the
    matching tool-result message(s) on the next round."""
    if provider in _OPENAI_COMPATIBLE:
        msg: dict[str, Any] = {"role": "assistant", "content": result.text}
        if result.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in result.tool_calls
            ]
        return msg

    blocks: list[dict[str, Any]] = []
    if result.text:
        blocks.append({"type": "text", "text": result.text})
    for tc in result.tool_calls:
        blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
    return {"role": "assistant", "content": blocks}


def tool_result_message(provider: str, call: ToolCall, content: str) -> dict[str, Any]:
    """Provider-native message carrying the result of executing `call`."""
    if provider in _OPENAI_COMPATIBLE:
        return {"role": "tool", "tool_call_id": call.id, "content": content}
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": call.id, "content": content}],
    }
