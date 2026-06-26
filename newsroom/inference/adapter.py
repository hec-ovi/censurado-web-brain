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

Retry policy: every call is retried up to ``_MAX_ATTEMPTS`` times on a transient
backend failure (a connection-level drop or timeout, or a cloud HTTP 429/5xx) with
exponential backoff that honors ``Retry-After``. "It did not answer" (a timeout, a
dropped connection, a rate-limit / overload status) is exactly the case we re-send;
a non-transient 4xx is a real answer and raises immediately.

Pacing (cooldown): after EVERY answer we actually receive (prose, a tool call, any
shape), ``chat()`` sleeps ``NEWSROOM_INFERENCE_COOLDOWN`` seconds (default 20, set 0
to disable) before returning, so a caller cannot fire the next request inside the
cooldown window. This throttles the whole harness under a cloud free tier's request-
and token-rate limits (e.g. Gemini: 15 RPM, 250k TPM) without any caller needing to
know. The same cooldown wraps the pydantic-ai structured seams via
``retry_transient``, so both inference paths in the harness are paced identically.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field

import httpx

from .provider import ProviderConfig, resolve

__all__ = ["ToolCall", "ChatRequest", "ChatResponse", "chat", "retry_transient", "DEFAULT_TIMEOUT"]

DEFAULT_TIMEOUT = float(os.getenv("NEWSROOM_LLM_TIMEOUT", "180"))


def _log(message: str) -> None:
    """Write an observability line to STDERR, the brain's log stream. Never stdout:
    the automation CLI reserves stdout for its machine-readable JSON run summary, so a
    stray stdout line would corrupt it (and any caller parsing the summary)."""
    print(message, file=sys.stderr, flush=True)


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
# Attempts and backoff cap are env-tunable (like the cooldown) because a cloud free
# tier's throttling is bursty: a 429 is a per-minute TPM bucket, so the backoff must
# be able to reach ~60s to outlast the window, and we want a few more attempts than
# the local case ever needed. Defaults: 8 attempts, cap 60s -> waits 1,2,4,8,16,32,60.
_MAX_ATTEMPTS = max(1, int(os.getenv("NEWSROOM_INFERENCE_MAX_ATTEMPTS", "8")))
_BACKOFF_CAP = float(os.getenv("NEWSROOM_INFERENCE_BACKOFF_CAP", "60"))
_TRANSIENT_NET = (
    httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError,
    httpx.WriteError, httpx.PoolTimeout, httpx.ReadTimeout, httpx.ConnectTimeout,
)


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """Backoff seconds: honor a Retry-After header (capped), else exponential up to
    ``_BACKOFF_CAP``."""
    if retry_after:
        try:
            return min(float(retry_after), _BACKOFF_CAP)
        except (TypeError, ValueError):
            pass
    return float(min(2 ** attempt, _BACKOFF_CAP))


def _cooldown_seconds() -> float:
    """The post-answer pacing delay in seconds (env ``NEWSROOM_INFERENCE_COOLDOWN``,
    default 20). Read per call so it can be tuned per process and silenced (0) in
    tests; a bad value falls back to the default rather than raising."""
    try:
        return max(0.0, float(os.getenv("NEWSROOM_INFERENCE_COOLDOWN", "20")))
    except (TypeError, ValueError):
        return 20.0


def _cooldown(model: str = "") -> None:
    """Sleep the post-answer cooldown once (a no-op when it is 0). Called after every
    received inference answer, on both inference paths, so nothing fires inside the
    cooldown window. Logs a line (stderr) so the pacing is observable in the brain logs."""
    secs = _cooldown_seconds()
    if secs > 0:
        _log(f"[inference] cooldown {secs:.0f}s after {model or 'answer'}")
        time.sleep(secs)


def retry_transient(fn, *, attempts: int = _MAX_ATTEMPTS):
    """Call ``fn`` and retry transient backend failures (network drops / timeouts,
    HTTP 429/5xx) with exponential backoff. Non-transient errors (a parse/validation
    failure, a 4xx other than 429) raise immediately. On success the post-answer
    cooldown is applied, so the pydantic-ai seams are paced exactly like ``chat()``.
    Used to wrap the pydantic-ai structured seams, which carry the failing HTTP status
    on the exception's ``status_code``."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            result = fn()
        except _TRANSIENT_NET as exc:
            last = exc
        except Exception as exc:
            if getattr(exc, "status_code", None) not in _RETRY_STATUS:
                raise
            last = exc
        else:
            _cooldown()
            return result
        if attempt == attempts - 1:
            break
        delay = _retry_delay(attempt)
        reason = getattr(last, "status_code", None) or type(last).__name__
        _log(f"[inference] transient {reason}; attempt {attempt + 1}/{attempts} failed, retry in {delay:.0f}s")
        time.sleep(delay)
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
        except _TRANSIENT_NET as exc:
            if attempt == _MAX_ATTEMPTS - 1:
                raise
            delay = _retry_delay(attempt)
            _log(f"[inference] transient {type(exc).__name__} on {cfg.model}; "
                 f"attempt {attempt + 1}/{_MAX_ATTEMPTS} failed, retry in {delay:.0f}s")
            time.sleep(delay)
            continue
        if resp.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS - 1:
            delay = _retry_delay(attempt, resp.headers.get("retry-after"))
            _log(f"[inference] HTTP {resp.status_code} on {cfg.model}; "
                 f"attempt {attempt + 1}/{_MAX_ATTEMPTS} failed, retry in {delay:.0f}s")
            time.sleep(delay)
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
    response = ChatResponse(
        content=(msg.get("content") or "").strip(),
        tool_calls=calls,
        finish_reason=choice.get("finish_reason", ""),
        usage=data.get("usage") or {},
        provider=cfg.provider,
        model=cfg.model,
    )
    # Log token usage so per-article cost is observable (summable from the logs).
    u = data.get("usage") or {}
    _log(f"[usage] {cfg.model} total={u.get('total_tokens')} "
         f"(prompt={u.get('prompt_tokens')} completion={u.get('completion_tokens')})")
    # Pace after the answer (any shape: prose or a tool call) so the next inference
    # request cannot fire inside the cooldown window. See module docstring.
    _cooldown(cfg.model)
    return response
