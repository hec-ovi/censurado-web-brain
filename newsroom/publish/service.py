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

from newsroom.contracts.article import PublishArticleInput
from newsroom.manager.coverage import CoverageStore
from newsroom.publish.client import DEFAULT_TIMEOUT, PublishResult, publish_article
from newsroom.runs import Assignment, RunStore

__all__ = ["publish_assignment"]


def publish_assignment(
    article: PublishArticleInput,
    *,
    assignment: Assignment,
    store: RunStore,
    base_url: str,
    token: str,
    coverage: CoverageStore | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> PublishResult:
    """Publish a finalized assignment and record the outcome. Returns the typed
    ``PublishResult`` unchanged; the side effects (mark published, record coverage)
    fire only on a created/replayed success and only once per assignment."""
    result = publish_article(
        article, assignment=assignment, base_url=base_url, token=token, timeout=timeout
    )
    if not (result.ok and result.id):
        return result

    current = store.get_assignment(assignment.id)
    already_published = current is not None and current.status == "published"

    # Record coverage FIRST, then mark published LAST. The two are separate commits on
    # the shared connection, so order them to make the only crash window benign: a
    # failure between them leaves the assignment "ready" (a re-run replays the
    # idempotent 200 and re-records), rather than "published" with a coverage row that
    # the already-published guard could never backfill. A clean replay still records
    # nothing twice, because by then the assignment is already "published".
    if coverage is not None and not already_published:
        coverage.record(
            section=article.section,
            headline=article.title,
            topics=list(article.topics),
            slug=result.slug,
            published_id=result.id,
            assignment_id=assignment.id,
            content_hash=assignment.content_hash,
            published_at=article.published_at,
        )
    store.mark_published(assignment.id, published_id=result.id)
    return result
