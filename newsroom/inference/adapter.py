"""The completion adapter: one ``chat()`` over the OpenAI Chat-Completions dialect.

Typed in (``ChatRequest``), typed out (``ChatResponse``). The same call backs every
role; only the messages and the resolved provider differ. Adapted from the proven
gamentic client, with three deliberate changes for this harness:

  * NO output-length cap, ever. There is no ``max_tokens`` field on ``ChatRequest``
    and ``chat()`` never puts one (or any length cap) in the payload. The shared
    test fake rejects any such key with HTTP 422, so a regression fails loudly.
  * Capability gating includes ``supports_grammar`` (GBNF), alongside
    ``supports_thinking`` and the stop-list budget. A backend lacking a capability
    simply does not receive that field; the caller inspects the resolved config to
    pick a fallback path (e.g. prose-then-parse when grammar is unavailable).
  * Role-based resolution (see ``provider.resolve``) so the evaluator can target a
    different endpoint than the drafter.

Retry policy (from gamentic, seen live): one retry on connection-level failures
only. A redeploy of the llama.cpp container kills in-flight requests and a fresh
connection a beat later succeeds. Timeouts are NOT retried (a timeout means the box
is busy; retrying doubles the pain) and HTTP status errors are real answers.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import httpx

from .provider import ProviderConfig, resolve

__all__ = ["ToolCall", "ChatRequest", "ChatResponse", "chat", "DEFAULT_TIMEOUT"]

DEFAULT_TIMEOUT = float(os.getenv("NEWSROOM_LLM_TIMEOUT", "180"))


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class ChatRequest:
    """A completion request. Note the absence of any length cap, by design."""

    messages: list[dict]
    temperature: float = 0.8
    tools: list[dict] | None = None
    tool_choice: str = "auto"
    stop: list[str] | None = None
    thinking: bool = False
    grammar: str | None = None  # GBNF; only sent when the provider supports it


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    provider: str = ""
    model: str = ""


# Transient backend failures to retry: network drops plus cloud HTTP 429/5xx. A
# rate-limited or momentarily-unavailable backend (e.g. Gemini free tier: 429 TPM
# throttles, intermittent 503) must not drop an article on a single blip.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 6
_TRANSIENT_NET = (
    httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError,
    httpx.WriteError, httpx.PoolTimeout, httpx.ReadTimeout, httpx.ConnectTimeout,
)


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """Backoff seconds: honor a Retry-After header (capped), else exponential."""
    if retry_after:
        try:
            return min(float(retry_after), 60.0)
        except (TypeError, ValueError):
            pass
    return float(min(2 ** attempt, 30))


def retry_transient(fn, *, attempts: int = _MAX_ATTEMPTS):
    """Call ``fn`` and retry transient backend failures (network drops, HTTP 429/5xx)
    with exponential backoff. Non-transient errors (a parse/validation failure, a 4xx
    other than 429) raise immediately. Used to wrap the pydantic-ai structured seams,
    which carry the failing HTTP status on the exception's ``status_code``."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except _TRANSIENT_NET as exc:
            last = exc
        except Exception as exc:
            if getattr(exc, "status_code", None) not in _RETRY_STATUS:
                raise
            last = exc
        if attempt == attempts - 1:
            break
        time.sleep(_retry_delay(attempt))
    assert last is not None
    raise last


def chat(
    request: ChatRequest,
    *,
    cfg: ProviderConfig | None = None,
    role: str = "default",
    timeout: float = DEFAULT_TIMEOUT,
) -> ChatResponse:
    """Run one chat completion. Resolves the provider from ``role`` unless ``cfg``
    is given (tests pass an explicit config pointed at the fake)."""
    cfg = cfg or resolve(role)

    payload: dict = {
        "model": cfg.model,
        "messages": request.messages,
        "temperature": request.temperature,
    }
    # Deliberately no max_tokens / length field of any kind.
    if request.tools:
        payload["tools"] = request.tools
        payload["tool_choice"] = request.tool_choice
    if request.stop:
        payload["stop"] = request.stop[: cfg.max_stops] if cfg.max_stops > 0 else request.stop
    if request.thinking and cfg.supports_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": True}
    if request.grammar and cfg.supports_grammar:
        payload["grammar"] = request.grammar

    url = f"{cfg.base_url}/chat/completions"
    kwargs: dict = {"json": payload, "timeout": timeout}
    if cfg.api_key:
        kwargs["headers"] = {"Authorization": f"Bearer {cfg.api_key}"}

    resp = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = httpx.post(url, **kwargs)
        except _TRANSIENT_NET:
            if attempt == _MAX_ATTEMPTS - 1:
                raise
            time.sleep(_retry_delay(attempt))
            continue
        if resp.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_retry_delay(attempt, resp.headers.get("retry-after")))
            continue
        break
    resp.raise_for_status()
    data = resp.json()
    choice = data["choices"][0]
    msg = choice.get("message", {})

    calls: list[ToolCall] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except json.JSONDecodeError:
            args = {}
        calls.append(ToolCall(name=fn.get("name", ""), arguments=args))

    # Only message.content is consumed; any message.reasoning_content (a hybrid
    # model's thinking output) is intentionally ignored, matching the gamentic client.
    return ChatResponse(
        content=(msg.get("content") or "").strip(),
        tool_calls=calls,
        finish_reason=choice.get("finish_reason", ""),
        usage=data.get("usage") or {},
        provider=cfg.provider,
        model=cfg.model,
    )
