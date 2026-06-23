"""The publish client: raw HTTP to the platform publish seam (architecture doc B.0).

This is one of the harness's two real process boundaries. The decision deferred
from Plan 1 is resolved here in favor of raw HTTP over shelling the platform CLI:
the brain stack stays fully isolated (no Go binary to install, pin, or locate),
and the harness already vendors the schema, the section enum, and the content hash,
so it owns the local validation the CLI would otherwise provide. The admin's own
publish path proved this exact shape.

The contract this client honors, verified field for field against the platform:

  * the operator key holds BOTH scopes (``articles:write`` + ``articles:publish-any``);
    ``publish-any`` ALONE 403s ``insufficient_scope`` (the platform checks ``write``
    first), so the key authors as any persona only when both are present.
  * the ``Idempotency-Key`` header is the CONTENT-derived key minted at finalize
    (``hash(assignment_id + content_hash)``) and persisted on the assignment BEFORE
    the POST, so a post-finalize crash replays the byte-identical body to the same
    key and the platform returns a true idempotent 200, never a double publish.
  * ``section`` is validated LOCALLY against the harness enum before any POST: a
    stray section would silently mint an orphan ``/section/<slug>`` page on the
    public site, so it is rejected here rather than sent.

No output-length cap is involved anywhere; this layer only transports a finished,
already-validated article.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from newsroom.contracts.article import PublishArticleInput
from newsroom.contracts.hashing import content_hash, idempotency_key
from newsroom.contracts.sections import is_valid_section
from newsroom.runs import Assignment

__all__ = ["PublishResult", "publish_article", "build_payload", "DEFAULT_TIMEOUT"]

DEFAULT_TIMEOUT = float(os.getenv("NEWSROOM_PUBLISH_TIMEOUT", "30"))


@dataclass
class PublishResult:
    """The typed outcome of one publish attempt. ``ok`` is True for a created (201)
    or replayed (200) article; ``replayed`` distinguishes the idempotent 200 from a
    fresh 201. On failure ``code``/``detail`` carry the platform's problem detail (or
    ``invalid_section`` / ``transport_error`` for the two locally-detected cases)."""

    ok: bool
    status: int
    id: str | None = None
    slug: str | None = None
    replayed: bool = False
    code: str = ""
    detail: str = ""


def build_payload(article: PublishArticleInput) -> dict:
    """The strict publish body. ``exclude_none`` drops unset optionals (``slug``,
    ``published_at``) so we never send a JSON null the platform would coerce to "";
    ``metadata`` (with the reserved ``newsroom`` provenance namespace, when finalize
    stamped it) is kept, since it is the one open extension point the schema allows."""
    return article.model_dump(exclude_none=True)


def _key_for(assignment: Assignment, content_hash_hex: str) -> str:
    """The content-derived idempotency key. Prefer the one persisted at finalize (so
    a replay presents the identical key); mint it from the article's content hash
    only as a fallback when the assignment was not finalized through the store."""
    if assignment.idempotency_key:
        return assignment.idempotency_key
    return idempotency_key(assignment.id, content_hash_hex)


def publish_article(
    article: PublishArticleInput,
    *,
    assignment: Assignment,
    base_url: str,
    token: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> PublishResult:
    """POST one finalized article to the platform and map the response to a typed
    result. Validates ``section`` locally first (no POST on a stray section). A
    transport failure returns a typed result rather than raising: the assignment
    stays ``ready`` and the content-derived key makes a later replay exactly-once."""
    if not is_valid_section(article.section):
        return PublishResult(
            ok=False, status=0, code="invalid_section",
            detail=f"section {article.section!r} is not one of the harness sections",
        )

    # The persisted idempotency key was minted at finalize from the article's content
    # hash. If the article handed to publish has drifted from that finalized content
    # (a re-run that re-derived it differently), POSTing it under the persisted key
    # would hit the platform's content-hash dedup as a 422 idempotency_key_reused. So
    # the client ENFORCES the invariant the seam promises rather than trusting the
    # caller: refuse here when the content does not match the key.
    chash = content_hash(article.title, article.body, article.author, article.section)
    if assignment.content_hash and assignment.content_hash != chash:
        return PublishResult(
            ok=False, status=0, code="content_hash_drift",
            detail="article content does not match the finalized idempotency key",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": _key_for(assignment, chash),
    }
    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/articles", headers=headers,
            json=build_payload(article), timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return PublishResult(ok=False, status=0, code="transport_error", detail=str(exc))
    return _map_response(resp)


def _map_response(resp: httpx.Response) -> PublishResult:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    status = resp.status_code
    if status in (200, 201):
        article_id = body.get("id")
        if not article_id:
            # A 2xx with no article id is not a usable success: do not let a caller
            # that trusts ``ok`` alone treat a body-less response as a real publish.
            return PublishResult(
                ok=False, status=status, code="malformed_success",
                detail="platform returned success with no article id",
            )
        return PublishResult(
            ok=True, status=status, id=article_id, slug=body.get("slug"),
            replayed=(status == 200),
        )
    return PublishResult(
        ok=False, status=status, code=str(body.get("code", "")), detail=str(body.get("detail", "")),
    )
