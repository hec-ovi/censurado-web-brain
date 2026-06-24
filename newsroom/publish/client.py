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

__all__ = [
    "PublishResult",
    "publish_article",
    "build_payload",
    "DEFAULT_TIMEOUT",
    "key_for",
    "local_publish_check",
    "BatchItem",
    "BatchItemResult",
    "BatchResult",
    "publish_batch",
]

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


def key_for(assignment: Assignment, content_hash_hex: str) -> str:
    """The content-derived idempotency key. Prefer the one persisted at finalize (so
    a replay presents the identical key); mint it from the article's content hash
    only as a fallback when the assignment was not finalized through the store."""
    if assignment.idempotency_key:
        return assignment.idempotency_key
    return idempotency_key(assignment.id, content_hash_hex)


def local_publish_check(
    article: PublishArticleInput, assignment: Assignment
) -> PublishResult | None:
    """The two checks that reject an article BEFORE any POST, shared by the single and
    batch paths. Returns a typed failure to skip the wire, or None when the article is
    safe to send.

      * a stray ``section`` (not in the harness enum) would mint an orphan
        ``/section/<slug>`` page, so it is rejected locally;
      * a body that has DRIFTED from the finalized content the persisted key was minted
        from would, under that key, hit the platform's content-hash dedup as a 422
        ``idempotency_key_reused`` (and in an atomic batch would fail every sibling
        too), so the client enforces the key<->content invariant rather than trusting
        the caller."""
    if not is_valid_section(article.section):
        return PublishResult(
            ok=False, status=0, code="invalid_section",
            detail=f"section {article.section!r} is not one of the harness sections",
        )
    chash = content_hash(article.title, article.body, article.author, article.section)
    if assignment.content_hash and assignment.content_hash != chash:
        return PublishResult(
            ok=False, status=0, code="content_hash_drift",
            detail="article content does not match the finalized idempotency key",
        )
    return None


def publish_article(
    article: PublishArticleInput,
    *,
    assignment: Assignment,
    base_url: str,
    token: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> PublishResult:
    """POST one finalized article to the platform and map the response to a typed
    result. Runs the local pre-checks first (no POST on a stray section or a drifted
    body). A transport failure returns a typed result rather than raising: the
    assignment stays ``ready`` and the content-derived key makes a later replay
    exactly-once."""
    local = local_publish_check(article, assignment)
    if local is not None:
        return local

    chash = content_hash(article.title, article.body, article.author, article.section)
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": key_for(assignment, chash),
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


# ----- the atomic batch publish seam (POST /articles:batch) -----
#
# The platform exposes a batch endpoint that commits a whole run's ready articles in
# ONE transaction: it validates every item first, then stores all of them or none
# (any invalid item -> 422, nothing written). There is no Idempotency-Key header for
# a batch (one header cannot carry N keys), so each item carries its own content-
# derived key in the body. A resend of the same batch is idempotent per item. This is
# the same wire contract as ``/articles`` per field, just N at once and all-or-nothing.


@dataclass
class BatchItem:
    """One finalized article to submit in a batch, paired with the assignment it came
    from and the per-item idempotency key (the batch moves the key out of the header
    and into each item)."""

    assignment_id: str
    article: PublishArticleInput
    idempotency_key: str


@dataclass
class BatchItemResult:
    """The outcome of one item within a batch. ``ok`` is True for a created or
    deduplicated item; ``replayed`` marks the idempotent ``deduplicated`` status.
    Because the batch is atomic, on any batch-level failure EVERY item is ``ok=False``
    (nothing was written). The result is realigned to its assignment by INPUT ORDER (the
    caller zips it against the submitted items); ``assignment_id`` and ``index`` (the
    position in the SENT batch) are carried as diagnostics, not the matching key."""

    assignment_id: str
    index: int
    ok: bool
    id: str | None = None
    slug: str | None = None
    replayed: bool = False
    code: str = ""
    detail: str = ""


@dataclass
class BatchResult:
    """The typed outcome of one ``POST /articles:batch``. ``ok`` is True only when the
    whole batch landed (201 with at least one created, or 200 when every item
    deduplicated). ``items`` carries one result per SUBMITTED item, in input order, so
    the caller fans the side effects (mark published / mark failed) back to the right
    assignment. On failure ``code``/``detail`` carry the batch-level problem
    (``validation_failed`` for a 422, ``transport_error`` / ``malformed_success`` for
    the locally-detected cases) and every item is ``ok=False``."""

    ok: bool
    status: int
    items: list[BatchItemResult]
    code: str = ""
    detail: str = ""


def publish_batch(
    items: list[BatchItem],
    *,
    base_url: str,
    token: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> BatchResult:
    """POST a list of finalized articles to the platform's atomic batch endpoint and
    map the response to typed per-item results.

    The batch is ALL-OR-NOTHING: the platform validates every item before any write,
    so on a 422 nothing is stored and every item comes back ``ok=False``. Each item
    carries its own content-derived ``idempotency_key`` (there is no batch header), so
    a resend of the same batch is idempotent per item and never double-publishes. A
    transport fault returns a typed failure rather than raising (mirroring
    ``publish_article``): the assignments stay ``ready`` and a later replay is
    exactly-once via the per-item keys.

    An EMPTY batch is a no-op: it returns a successful empty result WITHOUT a POST (the
    contract accepts an empty array and writes nothing, so there is nothing to send)."""
    if not items:
        return BatchResult(ok=True, status=200, items=[])

    payload = {
        "articles": [
            {**build_payload(it.article), "idempotency_key": it.idempotency_key}
            for it in items
        ]
    }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/articles:batch",
            headers=headers, json=payload, timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return _batch_failure(items, status=0, code="transport_error", detail=str(exc))
    return _map_batch_response(items, resp)


def _batch_failure(
    items: list[BatchItem],
    *,
    status: int,
    code: str,
    detail: str = "",
    per_index: dict[int, dict] | None = None,
) -> BatchResult:
    """Build an all-failed batch result. ``per_index`` (from a 422 error list) attaches
    each named item's specific code/detail; the rest carry the batch-level code, so a
    finalized-but-unpublished article is observable with the most specific reason."""
    per_index = per_index or {}
    return BatchResult(
        ok=False, status=status, code=code, detail=detail,
        items=[
            BatchItemResult(
                assignment_id=it.assignment_id, index=i, ok=False,
                code=str(per_index.get(i, {}).get("code", "")) or code,
                detail=str(per_index.get(i, {}).get("detail", "")) or detail,
            )
            for i, it in enumerate(items)
        ],
    )


def _map_batch_response(items: list[BatchItem], resp: httpx.Response) -> BatchResult:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    status = resp.status_code

    if status in (200, 201):
        results = body.get("results")
        if not isinstance(results, list) or len(results) != len(items):
            return _batch_failure(
                items, status=status, code="malformed_success",
                detail="batch success body did not carry one result per item",
            )
        by_index: dict[int, dict] = {
            r["index"]: r
            for r in results
            if isinstance(r, dict) and isinstance(r.get("index"), int)
        }
        mapped: list[BatchItemResult] = []
        for i, it in enumerate(items):
            r = by_index.get(i)
            article_id = r.get("id") if isinstance(r, dict) else None
            st = r.get("status") if isinstance(r, dict) else None
            if not article_id or st not in ("created", "deduplicated"):
                # A 2xx whose per-item entry lacks an id or a known status is not a
                # usable success: fail the whole batch rather than report a phantom
                # publish (the single client's malformed_success guard, per item).
                return _batch_failure(
                    items, status=status, code="malformed_success",
                    detail=f"batch result for index {i} is missing id/status",
                )
            mapped.append(BatchItemResult(
                assignment_id=it.assignment_id, index=i, ok=True,
                id=str(article_id),
                slug=(str(r.get("slug")) if r.get("slug") is not None else None),
                replayed=(st == "deduplicated"),
            ))
        return BatchResult(ok=True, status=status, items=mapped)

    # Non-2xx: the batch is atomic, so NOTHING was written. Carry the batch-level code
    # and map each named per-item error (index -> code/detail) onto its item.
    code = str(body.get("code", "")) or f"http_{status}"
    detail = str(body.get("detail", ""))
    per_index: dict[int, dict] = {}
    errors = body.get("errors")
    if isinstance(errors, list):
        for e in errors:
            if isinstance(e, dict) and isinstance(e.get("index"), int):
                per_index[e["index"]] = e
    return _batch_failure(items, status=status, code=code, detail=detail, per_index=per_index)
