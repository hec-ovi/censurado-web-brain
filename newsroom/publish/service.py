"""The publish step with its side effects: POST the article, then record the
outcome in the brain's own stores.

This is the deterministic COLLECT -> PUBLISH tail of a run (architecture doc B.2):
it runs after the per-article pipeline, over the assignments that reached ``ready``.
On a created or replayed success it marks the assignment ``published`` and writes
ONE ``coverage`` row, so the manager's next run sees the freshly published story in
its coverage memory (Step 6) and will not republish it. Coverage is recorded only
the first time an assignment reaches ``published``, so a crash-replay (which returns
the idempotent 200) does not double-record.
"""

from __future__ import annotations

import threading
from contextlib import nullcontext

from newsroom.contracts.article import PublishArticleInput
from newsroom.contracts.hashing import content_hash
from newsroom.contracts.slug import derive_slug
from newsroom.manager.coverage import CoverageStore
from newsroom.publish.client import (
    DEFAULT_TIMEOUT,
    BatchItem,
    PublishResult,
    key_for,
    local_publish_check,
    publish_article,
    publish_batch,
)
from newsroom.runs import Assignment, RunStore

__all__ = ["publish_assignment", "publish_batch_assignments"]


def _unique_slug(base: str, taken: set[str]) -> str:
    """A deterministic slug not in ``taken``, formed by suffixing ``base`` with the
    smallest ``-N`` (N >= 2). ``base`` is already a derived slug, so ``base-2`` is itself
    a valid slug the platform stores verbatim."""
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _record_publish_success(
    article: PublishArticleInput,
    *,
    assignment: Assignment,
    store: RunStore,
    published_id: str,
    slug: str | None,
    coverage: CoverageStore | None,
    lock: threading.Lock | None,
) -> None:
    """The publish side effects, shared by the single and batch paths: record coverage
    FIRST, then mark published LAST, both under ``lock`` (the brain's shared-connection
    lock) and only on a usable success.

    The two are separate commits, so this order makes the only crash window benign: a
    failure between them leaves the assignment ``ready`` (a re-run replays the
    idempotent 200 and re-records), rather than ``published`` with no coverage row the
    already-published guard could never backfill. Coverage is recorded only the first
    time the assignment reaches ``published``, so a clean replay records nothing twice.
    NEVER call this with the network POST still in flight: the lock is held here only,
    never across the wire."""
    guard = lock if lock is not None else nullcontext()
    with guard:
        current = store.get_assignment(assignment.id)
        already_published = current is not None and current.status == "published"
        if coverage is not None and not already_published:
            coverage.record(
                section=article.section,
                headline=article.title,
                topics=list(article.topics),
                entities=list(assignment.entities),  # the de-dup signature, persisted at last
                slug=slug,
                published_id=published_id,
                assignment_id=assignment.id,
                content_hash=assignment.content_hash,
                published_at=article.published_at,
            )
        store.mark_published(assignment.id, published_id=published_id)


def publish_assignment(
    article: PublishArticleInput,
    *,
    assignment: Assignment,
    store: RunStore,
    base_url: str,
    token: str,
    coverage: CoverageStore | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    lock: threading.Lock | None = None,
) -> PublishResult:
    """Publish a finalized assignment and record the outcome. Returns the typed
    ``PublishResult`` unchanged; the side effects (mark published, record coverage)
    fire only on a created/replayed success and only once per assignment.

    ``lock`` (when given) serializes the brain's single shared SQLite connection. It
    is held ONLY around the store block (the read + the two writes), NEVER across the
    POST, exactly like the pipeline and synthesis seams: the network call must not
    block a concurrent request-loop read of the same connection."""
    result = publish_article(
        article, assignment=assignment, base_url=base_url, token=token, timeout=timeout
    )
    if not (result.ok and result.id):
        return result

    _record_publish_success(
        article, assignment=assignment, store=store, published_id=result.id,
        slug=result.slug, coverage=coverage, lock=lock,
    )
    return result


def publish_batch_assignments(
    pairs: list[tuple[Assignment, PublishArticleInput]],
    *,
    store: RunStore,
    base_url: str,
    token: str,
    coverage: CoverageStore | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    lock: threading.Lock | None = None,
) -> list[PublishResult]:
    """Publish a run's ready articles in ONE atomic batch and record the per-article
    side effects. ``pairs`` is ``(Assignment, PublishArticleInput)`` in publish order;
    the return is one ``PublishResult`` per input pair IN THE SAME ORDER, so the caller
    builds its per-assignment outcomes exactly as for the single path.

    Three per-article outcomes are possible, mirroring the single path but under one
    POST:

      * a local pre-check failure (stray section, content/key drift) EXCLUDES the item
        from the batch (it would 422 the whole atomic batch) and returns a typed local
        failure, the same code ``publish_article`` would return;
      * the batch lands (201/200): each sent item is marked published and gets one
        coverage row (idempotent across a replay via the per-item key);
      * the batch fails (422/auth/transport): NOTHING was written, so every sent item's
        result is ``ok=False`` and the caller marks it ``publish_failed``.

    The network POST is NEVER under ``lock``: only the per-assignment store block is
    (inside ``_record_publish_success``), exactly like ``publish_assignment``, so a
    concurrent request-loop read never races the publish writes and the hard
    never-lock-across-the-wire rule holds."""
    results: list[PublishResult | None] = [None] * len(pairs)
    # (original index, BatchItem, article, assignment) for each item we will send.
    sendable: list[tuple[int, BatchItem, PublishArticleInput, Assignment]] = []
    seen_slugs: set[str] = set()

    for i, (assignment, article) in enumerate(pairs):
        local = local_publish_check(article, assignment)
        if local is not None:
            results[i] = local
            continue
        chash = content_hash(article.title, article.body, article.author, article.section)
        key = key_for(assignment, chash)
        # Defensive slug de-collision. The platform rejects a duplicate slug WITHIN a
        # batch, which would 422 the whole atomic batch and block an otherwise-
        # publishable run. It dedups on the DERIVED permalink slug (slugify(slug) else
        # slugify(title) else the content-hash prefix), so a clash can arise three ways:
        # two explicit slugs, an explicit slug matching another's title-derived slug, or
        # two titles deriving to the same slug. Predict that derived slug for every item
        # and, on a collision, pin the later one to a unique explicit slug so the
        # platform stores it distinctly; the first item keeps the slug it would derive.
        effective = derive_slug(
            title=article.title, body=article.body, author=article.author,
            section=article.section, slug=article.slug,
        )
        send_article = article
        if effective in seen_slugs:
            unique = _unique_slug(effective, seen_slugs)
            send_article = article.model_copy(update={"slug": unique})
            seen_slugs.add(unique)
        else:
            seen_slugs.add(effective)
        sendable.append((i, BatchItem(assignment.id, send_article, key), article, assignment))

    batch = publish_batch(
        [item for (_, item, _, _) in sendable], base_url=base_url, token=token, timeout=timeout
    )

    # publish_batch returns one item per submitted BatchItem, in the order we passed
    # them, so zipping realigns each result with its original pair.
    for (orig_i, _item, article, assignment), item_result in zip(sendable, batch.items):
        if item_result.ok and item_result.id:
            _record_publish_success(
                article, assignment=assignment, store=store, published_id=item_result.id,
                slug=item_result.slug, coverage=coverage, lock=lock,
            )
            # Per-item HTTP status mirrors the single path (created -> 201, deduplicated
            # -> 200), so PublishResult.status means the same thing on both paths even
            # when one 201 batch mixes created and deduplicated items.
            results[orig_i] = PublishResult(
                ok=True, status=(200 if item_result.replayed else 201), id=item_result.id,
                slug=item_result.slug, replayed=item_result.replayed,
            )
        else:
            results[orig_i] = PublishResult(
                ok=False, status=batch.status,
                code=item_result.code or batch.code, detail=item_result.detail or batch.detail,
            )

    return [r for r in results if r is not None]
