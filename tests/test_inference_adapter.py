"""The inference adapter speaks the OpenAI dialect, gates capabilities, never caps
output length, retries connection drops once, and resolves roles to backends.

Adapter-vs-fake tests drive the real shared fake over HTTP. Retry tests monkeypatch
httpx (no fake needed). Resolution tests drive the env spine via monkeypatch.
"""

from __future__ import annotations

import httpx
import pytest

import newsroom.inference.adapter as adapter
from newsroom.inference import ChatRequest, chat, endpoints_distinct, resolve
from newsroom.inference.provider import ProviderConfig


def _cfg(fake, **over) -> ProviderConfig:
    base = dict(
        role="default",
        provider="local",
        base_url=f"{fake.base_url}/v1",
        model="local-gemma",
        supports_thinking=True,
        supports_grammar=True,
        max_stops=8,
    )
    base.update(over)
    return ProviderConfig(**base)


def _msgs() -> list[dict]:
    return [{"role": "user", "content": "Write about today's news."}]


# ---------------- adapter against the fake ----------------


def test_returns_scripted_content(fake):
    fake.state.script_chat("The model speaks.")
    r = chat(ChatRequest(messages=_msgs()), cfg=_cfg(fake))
    assert r.content == "The model speaks."
    assert r.finish_reason == "stop"
    assert r.provider == "local"
    assert r.model == "local-gemma"


def test_never_sends_a_length_cap(fake):
    chat(ChatRequest(messages=_msgs()), cfg=_cfg(fake))
    body = fake.state.chat_requests[-1]["body"]
    assert "max_tokens" not in body
    assert "max_output_tokens" not in body
    assert "max_completion_tokens" not in body
    # The fake's teardown guard also asserts no accepted request carried a cap.


def test_sends_model_messages_temperature(fake):
    chat(ChatRequest(messages=_msgs(), temperature=0.3), cfg=_cfg(fake))
    body = fake.state.chat_requests[-1]["body"]
    assert body["model"] == "local-gemma"
    assert body["temperature"] == 0.3
    assert body["messages"] == _msgs()


def test_thinking_sent_when_supported(fake):
    chat(ChatRequest(messages=_msgs(), thinking=True), cfg=_cfg(fake, supports_thinking=True))
    body = fake.state.chat_requests[-1]["body"]
    assert body.get("chat_template_kwargs") == {"enable_thinking": True}


def test_thinking_omitted_when_unsupported(fake):
    chat(ChatRequest(messages=_msgs(), thinking=True), cfg=_cfg(fake, supports_thinking=False))
    assert "chat_template_kwargs" not in fake.state.chat_requests[-1]["body"]


def test_grammar_sent_when_supported(fake):
    chat(ChatRequest(messages=_msgs(), grammar="root ::= object"), cfg=_cfg(fake, supports_grammar=True))
    assert fake.state.chat_requests[-1]["body"].get("grammar") == "root ::= object"


def test_grammar_omitted_when_unsupported(fake):
    chat(ChatRequest(messages=_msgs(), grammar="root ::= object"), cfg=_cfg(fake, supports_grammar=False))
    assert "grammar" not in fake.state.chat_requests[-1]["body"]


def test_stop_truncated_to_max_stops(fake):
    chat(ChatRequest(messages=_msgs(), stop=["a", "b", "c", "d", "e", "f"]), cfg=_cfg(fake, max_stops=4))
    assert fake.state.chat_requests[-1]["body"]["stop"] == ["a", "b", "c", "d"]


def test_stop_kept_whole_when_max_stops_zero(fake):
    chat(ChatRequest(messages=_msgs(), stop=["a", "b", "c", "d", "e"]), cfg=_cfg(fake, max_stops=0))
    assert fake.state.chat_requests[-1]["body"]["stop"] == ["a", "b", "c", "d", "e"]


def test_tool_calls_are_parsed(fake):
    fake.state.script_chat(
        "",
        tool_calls=[{"function": {"name": "web_search", "arguments": '{"q": "election results"}'}}],
    )
    r = chat(ChatRequest(messages=_msgs(), tools=[{"type": "function"}]), cfg=_cfg(fake))
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0].name == "web_search"
    assert r.tool_calls[0].arguments == {"q": "election results"}


def test_malformed_tool_arguments_degrade_to_empty(fake):
    fake.state.script_chat("", tool_calls=[{"function": {"name": "x", "arguments": "{not json"}}])
    r = chat(ChatRequest(messages=_msgs()), cfg=_cfg(fake))
    assert r.tool_calls[0].arguments == {}


def test_usage_passed_through(fake):
    fake.state.script_chat("ok", usage={"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12})
    r = chat(ChatRequest(messages=_msgs()), cfg=_cfg(fake))
    assert r.usage == {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12}


def test_api_key_sets_bearer_header(fake):
    r = chat(ChatRequest(messages=_msgs()), cfg=_cfg(fake, api_key="sk-test"))
    assert r.finish_reason == "stop"
    assert fake.state.chat_requests[-1]["headers"]["authorization"] == "Bearer sk-test"


def test_no_auth_header_when_no_api_key(fake):
    chat(ChatRequest(messages=_msgs()), cfg=_cfg(fake, api_key=""))
    assert "authorization" not in fake.state.chat_requests[-1]["headers"]


# ---------------- retry policy (httpx monkeypatched) ----------------


class _Resp:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}


def _static_cfg() -> ProviderConfig:
    return ProviderConfig(role="default", provider="local", base_url="http://x/v1", model="m")


def test_one_connection_drop_is_retried(monkeypatch):
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def _post(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("container restarting")
        return _Resp()

    monkeypatch.setattr(adapter.httpx, "post", _post)
    r = chat(ChatRequest(messages=_msgs()), cfg=_static_cfg())
    assert r.content == "ok"
    assert calls["n"] == 2


def test_persistent_connection_failure_raises(monkeypatch):
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def _post(url, **kw):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    monkeypatch.setattr(adapter.httpx, "post", _post)
    with pytest.raises(httpx.ConnectError):
        chat(ChatRequest(messages=_msgs()), cfg=_static_cfg())
    assert calls["n"] == 2


def test_timeouts_are_never_retried(monkeypatch):
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def _post(url, **kw):
        calls["n"] += 1
        raise httpx.ReadTimeout("box is busy")

    monkeypatch.setattr(adapter.httpx, "post", _post)
    with pytest.raises(httpx.ReadTimeout):
        chat(ChatRequest(messages=_msgs()), cfg=_static_cfg())
    assert calls["n"] == 1


# ---------------- role / provider resolution ----------------


def _clear_inference_env(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith(("NEWSROOM_INFERENCE_", "NEWSROOM_ROLE_")):
            monkeypatch.delenv(key, raising=False)


def test_default_local_capability_defaults(monkeypatch):
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("NEWSROOM_INFERENCE_PROVIDER", "local")
    cfg = resolve("default")
    assert cfg.supports_thinking and cfg.supports_grammar and cfg.max_stops == 8


def test_openai_capability_defaults(monkeypatch):
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("NEWSROOM_INFERENCE_PROVIDER", "openai")
    cfg = resolve("default")
    assert not cfg.supports_thinking and not cfg.supports_grammar and cfg.max_stops == 4


def test_hermes_provider_recognized(monkeypatch):
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("NEWSROOM_INFERENCE_PROVIDER", "hermes")
    cfg = resolve("default")
    assert cfg.provider == "hermes" and not cfg.supports_grammar


def test_role_override_beats_shared_default(monkeypatch):
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("NEWSROOM_INFERENCE_BASE_URL", "http://local:8080/v1")
    monkeypatch.setenv("NEWSROOM_ROLE_EVALUATOR_BASE_URL", "http://cloud:9000/v1")
    assert resolve("drafter").base_url == "http://local:8080/v1"
    assert resolve("evaluator").base_url == "http://cloud:9000/v1"


def test_endpoints_distinct_when_evaluator_overridden(monkeypatch):
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("NEWSROOM_INFERENCE_BASE_URL", "http://local:8080/v1")
    monkeypatch.setenv("NEWSROOM_ROLE_EVALUATOR_BASE_URL", "http://cloud:9000/v1")
    assert endpoints_distinct("drafter", "evaluator") is True


def test_endpoints_same_when_only_local(monkeypatch):
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("NEWSROOM_INFERENCE_BASE_URL", "http://local:8080/v1")
    # No evaluator override: both roles fall back to the shared default.
    assert endpoints_distinct("drafter", "evaluator") is False


def test_capability_env_override(monkeypatch):
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("NEWSROOM_INFERENCE_PROVIDER", "local")
    monkeypatch.setenv("NEWSROOM_INFERENCE_SUPPORTS_GRAMMAR", "false")
    assert resolve("default").supports_grammar is False
