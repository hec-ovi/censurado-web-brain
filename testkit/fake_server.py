"""The one shared in-repo fake, reused by every Step's end-to-end test.

It stands in for the two services the brain talks to:

  * ``POST /v1/chat/completions`` - the inference backend (OpenAI Chat-Completions
    dialect). Responses are scriptable; every request body is recorded; and any
    request carrying an output-length cap (``max_tokens`` and friends) is REJECTED
    with HTTP 422, so the global no-cap rule is enforced at the wire.

  * ``POST /articles`` - a faithful stand-in for the platform publish path. It
    mirrors the live handler's observable contract (``internal/publish``): bearer
    auth, the ``articles:write`` / ``articles:publish-any`` scope split (incl. the
    403 ``insufficient_scope`` path), the required ``Idempotency-Key`` header,
    strict unknown-field rejection (the Go decoder's ``DisallowUnknownFields`` ->
    ``400 invalid_json``), author binding, required-field validation, and
    content-hash idempotency/dedup using the SAME ``content_hash`` the harness
    mints. It also models the Go decoder's TYPE strictness: a well-formed JSON
    body with a wrong-typed field (a numeric title, a string ``topics``, a
    non-object ``metadata``, a non-RFC3339 ``published_at``) is rejected with
    ``400 invalid_json``, exactly as ``encoding/json`` decoding into the typed
    ``PublishInput`` would, and a JSON ``null`` for a string field becomes ``""``
    (Go's zero value) rather than the literal "None". The one modeled
    simplification is the Markdown safety gate (the ``unrenderable_body`` 422
    path), which is out of scope for the seam tests.

This is a TEST fixture, not a product module: nothing under ``newsroom/`` imports
it. The check ORDER below is deliberately identical to ``publish.go`` ServeHTTP so
the fake fails for the same reason the real handler would.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from newsroom.contracts.hashing import content_hash
from testkit.assertions import length_cap_keys_in

# ----- the platform's observable contract, pinned from the live source -----

SCOPE_WRITE = "articles:write"
SCOPE_PUBLISH_ANY = "articles:publish-any"

# Allowed top-level keys, from contracts/article.schema.json (additionalProperties:false).
_ALLOWED_ARTICLE_KEYS = frozenset(
    {"title", "body", "author", "section", "topics", "slug", "published_at", "metadata"}
)
_REQUIRED_ARTICLE_KEYS = ("title", "body", "author", "section")


@dataclass
class KeyConfig:
    """A registered API key: the author it is bound to and the scopes it holds."""

    author: str
    scopes: list[str]

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass
class ScriptedChat:
    """One queued chat completion response."""

    content: str
    finish_reason: str = "stop"
    tool_calls: list | None = None
    usage: dict | None = None


def default_keys() -> dict[str, KeyConfig]:
    """The three keys every test starts with.

    * ``op-token``        - the operator key: BOTH scopes, may author as anyone.
    * ``agent-a-token``   - an agent key: write-only, locked to author ``agent-a``.
    * ``noscope-token``   - a key with no scopes, to exercise insufficient_scope.
    """

    return {
        "op-token": KeyConfig(
            author="newsroom-operator", scopes=[SCOPE_WRITE, SCOPE_PUBLISH_ANY]
        ),
        "agent-a-token": KeyConfig(author="agent-a", scopes=[SCOPE_WRITE]),
        "noscope-token": KeyConfig(author="nobody", scopes=[]),
    }


@dataclass
class FakeState:
    """Mutable state a test inspects and scripts. One instance per fake app."""

    keys: dict[str, KeyConfig] = field(default_factory=default_keys)
    chat_script: list[ScriptedChat] = field(default_factory=list)
    chat_requests: list[dict] = field(default_factory=list)
    publish_requests: list[dict] = field(default_factory=list)
    # idempotency ledger: idem_key -> {content_hash, id, slug}
    ledger: dict[str, dict] = field(default_factory=dict)
    # content-hash dedup index: content_hash -> {id, slug, author}
    by_hash: dict[str, dict] = field(default_factory=dict)
    # A gate to simulate a slow model: when cleared, the chat handler blocks before
    # responding, so a test can prove a caller did NOT wait for the completion. It
    # defaults open (set), so every other test is unaffected. threading.Event is
    # used (not asyncio) because the fake's loop and the test live in different
    # threads, and the handler awaits it off-loop via run_in_threadpool.
    chat_gate: threading.Event = field(default_factory=threading.Event)
    _seq: int = 0

    def __post_init__(self) -> None:
        self.chat_gate.set()  # open by default; tests opt into holding

    # --- scripting / inspection helpers ---

    def hold_chat(self) -> None:
        """Make the next chat completion block until ``release_chat`` is called."""
        self.chat_gate.clear()

    def release_chat(self) -> None:
        """Release a held chat completion (and leave the gate open)."""
        self.chat_gate.set()

    def script_chat(
        self,
        content: str,
        finish_reason: str = "stop",
        tool_calls: list | None = None,
        usage: dict | None = None,
    ) -> None:
        """Queue one assistant response for the next chat completion call."""
        self.chat_script.append(
            ScriptedChat(
                content=content, finish_reason=finish_reason, tool_calls=tool_calls, usage=usage
            )
        )

    def add_key(self, token: str, author: str, scopes: list[str]) -> None:
        self.keys[token] = KeyConfig(author=author, scopes=list(scopes))

    def next_id(self) -> str:
        self._seq += 1
        return f"art_{self._seq:06d}"


def _slugify(text: str) -> str:
    """A simple slug stand-in (the returned slug only needs to be stable)."""
    out = []
    prev_dash = False
    for ch in text.strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def _bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    prefix = "Bearer "
    if len(authorization) > len(prefix) and authorization[: len(prefix)].lower() == prefix.lower():
        return authorization[len(prefix) :].strip()
    return ""


def _problem(status: int, code: str, detail: str | None = None, fields: dict | None = None):
    body: dict = {"status": status, "code": code}
    if detail:
        body["detail"] = detail
    if fields:
        body["fields"] = fields
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")


def _str_field(value: object) -> str:
    """Coerce a validated string field: a JSON null becomes "" (Go's zero value)."""
    return "" if value is None else str(value)


def _is_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _type_violation(payload: dict) -> str | None:
    """Return a message if any field's JSON type would fail Go's typed decode.

    Mirrors decoding into ``domain.PublishInput``: string fields accept a string
    or null; ``topics`` accepts an array of strings or null; ``metadata`` accepts
    an object or null; ``published_at`` accepts an RFC3339 date-time string or
    null. Any other JSON type is an ``UnmarshalTypeError`` -> 400 invalid_json.
    """
    for key in ("title", "body", "author", "section", "slug"):
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            return f"field {key!r} must be a string"
    topics = payload.get("topics")
    if topics is not None and (
        not isinstance(topics, list)
        or any(item is not None and not isinstance(item, str) for item in topics)
    ):
        return "field 'topics' must be an array of strings"
    metadata = payload.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return "field 'metadata' must be an object"
    published = payload.get("published_at")
    if published is not None and (not isinstance(published, str) or not _is_datetime(published)):
        return "field 'published_at' must be an RFC3339 date-time string"
    return None


def create_fake_app(state: FakeState | None = None) -> tuple[FastAPI, FakeState]:
    """Build the fake FastAPI app and return it with its mutable state."""

    state = state or FakeState()
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        try:
            body = json.loads(await request.body())
        except json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "invalid_json"})

        headers = dict(request.headers)
        offenders = length_cap_keys_in(body)
        if offenders:
            state.chat_requests.append({"body": body, "status": 422, "capped": True, "headers": headers})
            return JSONResponse(
                status_code=422,
                content={"error": "forbidden_length_cap", "keys": offenders},
            )

        scripted = state.chat_script.pop(0) if state.chat_script else ScriptedChat("(scripted-default)")
        state.chat_requests.append({"body": body, "status": 200, "capped": False, "headers": headers})
        # Simulate a slow model when a test has held the gate. Awaited off the event
        # loop so the wait never blocks the fake from serving other requests.
        if not state.chat_gate.is_set():
            await run_in_threadpool(state.chat_gate.wait)
        message: dict = {"role": "assistant", "content": scripted.content}
        if scripted.tool_calls is not None:
            message["tool_calls"] = scripted.tool_calls
        return {
            "id": f"chatcmpl-fake-{len(state.chat_requests)}",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "fake-model"),
            "choices": [{"index": 0, "message": message, "finish_reason": scripted.finish_reason}],
            "usage": scripted.usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    @app.post("/articles")
    async def articles(request: Request):
        # 1. authenticate (order mirrors publish.go authenticate()).
        token = _bearer(request.headers.get("authorization"))
        if not token:
            return _problem(401, "missing_token")
        ident = state.keys.get(token)
        if ident is None:
            return _problem(401, "invalid_token")
        if not ident.has_scope(SCOPE_WRITE):
            return _problem(403, "insufficient_scope", detail=f"requires {SCOPE_WRITE}")

        # 2. idempotency key required.
        key = (request.headers.get("idempotency-key") or "").strip()
        if not key:
            return _problem(400, "missing_idempotency_key", detail="Idempotency-Key header is required")

        # 3. strict decode: parse error or unknown top-level field -> 400 invalid_json.
        raw = await request.body()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return _problem(400, "invalid_json", detail=str(exc))
        if not isinstance(payload, dict):
            return _problem(400, "invalid_json", detail="body must be a JSON object")
        unknown = set(payload) - _ALLOWED_ARTICLE_KEYS
        if unknown:
            return _problem(400, "invalid_json", detail=f"unknown field(s): {sorted(unknown)}")

        # 3b. type strictness: Go decodes into a typed struct, so a wrong-typed
        # field is a decode error -> 400 invalid_json (a JSON null is allowed and
        # becomes the zero value, handled by _str_field below).
        type_problem = _type_violation(payload)
        if type_problem is not None:
            return _problem(400, "invalid_json", detail=type_problem)

        # 4. author binding: only a publish-any key may author as someone else.
        author = _str_field(payload.get("author")).strip()
        if not ident.has_scope(SCOPE_PUBLISH_ANY) and author != ident.author:
            return _problem(403, "author_mismatch", detail="author must match the authenticated key")

        # 5. required-field validation (mirrors NewArticle: non-empty after trim).
        fields = {
            k: "required"
            for k in _REQUIRED_ARTICLE_KEYS
            if not _str_field(payload.get(k)).strip()
        }
        if fields:
            return _problem(422, "validation_failed", fields=fields)

        title = _str_field(payload.get("title")).strip()
        body_md = _str_field(payload.get("body")).strip()
        section = _str_field(payload.get("section")).strip()
        chash = content_hash(title, body_md, author, section)
        state.publish_requests.append({"key": key, "content_hash": chash, "payload": payload})

        # 6. idempotency: same key + different content -> conflict; same -> replay.
        prev = state.ledger.get(key)
        if prev is not None:
            if prev["content_hash"] != chash:
                return _problem(422, "idempotency_key_reused", detail="this key was used for a different article")
            return JSONResponse(status_code=200, content={"id": prev["id"], "slug": prev["slug"]})

        # 7. content-hash dedup across keys (the platform's Upsert dedup).
        existing = state.by_hash.get(chash)
        if existing is not None:
            state.ledger[key] = {"content_hash": chash, "id": existing["id"], "slug": existing["slug"]}
            return JSONResponse(status_code=200, content={"id": existing["id"], "slug": existing["slug"]})

        # 8. brand-new article.
        article_id = state.next_id()
        slug = _slugify(str(payload.get("slug") or title)) or chash[:12]
        record = {"id": article_id, "slug": slug, "author": author}
        state.by_hash[chash] = record
        state.ledger[key] = {"content_hash": chash, "id": article_id, "slug": slug}
        return JSONResponse(status_code=201, content={"id": article_id, "slug": slug})

    return app, state
