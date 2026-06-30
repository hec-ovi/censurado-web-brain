"""The publish client (Step 7), driven against the shared fake's ``/articles``.

The fake mirrors the live platform handler's observable contract (bearer auth, the
write/publish-any scope split incl. the 403, the required Idempotency-Key header, strict
decode, author binding, and content-hash idempotency/dedup), so these tests exercise the
real wire contract end to end. The build-plan contracts for Step 7:

  (a) a key missing ``articles:write`` -> 403 insufficient_scope;
  (b) a finalized body POSTs once -> {id, slug};
  (c) replaying the same finalized assignment -> 200, no double-publish;
  (d) a stray section is rejected LOCALLY, before any POST;
  (e) ``metadata.newsroom`` is present in the payload and accepted.

Plus the author-binding 403 (why the operator key needs publish-any) and the
transport-failure degrade. The ``Assignment`` is the in-memory carrier the caller builds
(its content_hash + idempotency_key anchor the publish); there is no run-records store.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from newsroom.contracts.article import PublishArticleInput
from newsroom.contracts.hashing import content_hash, idempotency_key
from newsroom.publish import build_payload, publish_article
from newsroom.runs import Assignment


@dataclass
class Env:
    run_id: str
    author: str


def _env() -> Env:
    return Env(run_id="run-1", author="ada-reporter")


def _article(env: Env, *, title="The Chip Ships", body="A grounded body.", section="tech",
             topics=None, metadata=None) -> PublishArticleInput:
    return PublishArticleInput(
        title=title, body=body, author=env.author, section=section,
        topics=topics or ["chips"], metadata=metadata,
    )


def _ready_assignment(env: Env, article: PublishArticleInput) -> Assignment:
    """Build an in-memory assignment finalized to the article, so its content_hash +
    idempotency_key match (as the caller leaves it before publishing)."""
    aid = "asg-1"
    chash = content_hash(article.title, article.body, article.author, article.section)
    return Assignment(
        id=aid, run_id=env.run_id, persona_id=env.author, section=article.section,
        angle="", status="ready", created_at="2026-01-01T00:00:00+00:00",
        final_body=article.body, content_hash=chash,
        idempotency_key=idempotency_key(aid, chash), ledger_digest="sha256:abc",
    )


# ----- (a) the scope split -----


def test_key_without_write_scope_gets_insufficient_scope(fake):
    env = _env()
    article = _article(env)
    assignment = _ready_assignment(env, article)

    result = publish_article(article, assignment=assignment, base_url=fake.base_url, token="noscope-token")

    assert result.ok is False
    assert result.status == 403
    assert result.code == "insufficient_scope"


def test_write_only_key_cannot_author_as_another_persona(fake):
    # The operator key needs publish-any to author as a persona; a write-only key
    # bound to "agent-a" is rejected when it tries to publish as "ada-reporter".
    env = _env()
    article = _article(env)
    assignment = _ready_assignment(env, article)

    result = publish_article(article, assignment=assignment, base_url=fake.base_url, token="agent-a-token")

    assert result.ok is False
    assert result.status == 403
    assert result.code == "author_mismatch"


# ----- (b) a fresh publish -----


def test_finalized_article_publishes_once(fake):
    env = _env()
    article = _article(env)
    assignment = _ready_assignment(env, article)

    result = publish_article(article, assignment=assignment, base_url=fake.base_url, token="op-token")

    assert result.ok is True
    assert result.status == 201
    assert result.replayed is False
    assert result.id and result.slug
    assert len(fake.state.publish_requests) == 1
    # The platform received the content-derived key the harness persisted at finalize,
    # and recomputed the SAME content hash from the body the harness sent.
    assert fake.state.publish_requests[0]["key"] == assignment.idempotency_key
    assert fake.state.publish_requests[0]["content_hash"] == assignment.content_hash


# ----- (c) idempotent replay, no double-publish -----


def test_replaying_the_same_assignment_is_idempotent(fake):
    env = _env()
    article = _article(env)
    assignment = _ready_assignment(env, article)

    first = publish_article(article, assignment=assignment, base_url=fake.base_url, token="op-token")
    second = publish_article(article, assignment=assignment, base_url=fake.base_url, token="op-token")

    assert first.status == 201 and first.replayed is False
    assert second.status == 200 and second.replayed is True
    assert second.id == first.id and second.slug == first.slug  # same article, not a new one
    assert len(fake.state.publish_requests) == 2  # both POSTed...
    assert len(fake.state.by_hash) == 1  # ...but only one article exists
    # The replay is driven by the PERSISTED content-derived key (the doc's crash-
    # recovery mechanism), not merely the platform's content-hash backstop: both POSTs
    # carried the identical key.
    keys = {r["key"] for r in fake.state.publish_requests}
    assert keys == {assignment.idempotency_key}


# ----- (d) a stray section is rejected locally, before any POST -----


def test_stray_section_is_rejected_before_posting(fake):
    env = _env()
    # model_construct bypasses the model's own section validator, simulating a
    # payload that reached the client with an off-vocabulary section.
    article = PublishArticleInput.model_construct(
        title="T", body="B", author=env.author, section="sports", topics=[], metadata=None,
    )
    assignment = _ready_assignment(env, _article(env))

    result = publish_article(article, assignment=assignment, base_url=fake.base_url, token="op-token")

    assert result.ok is False
    assert result.code == "invalid_section"
    assert result.status == 0
    assert fake.state.publish_requests == []  # nothing was sent over the wire


# ----- (e) metadata.newsroom provenance -----


def test_metadata_newsroom_is_sent_and_accepted(fake):
    env = _env()
    article = _article(env, metadata={"newsroom": {"run_id": env.run_id, "sweeps": 2}})
    assignment = _ready_assignment(env, article)

    result = publish_article(article, assignment=assignment, base_url=fake.base_url, token="op-token")

    assert result.ok is True
    sent = fake.state.publish_requests[0]["payload"]
    assert sent["metadata"]["newsroom"]["run_id"] == env.run_id
    assert sent["metadata"]["newsroom"]["sweeps"] == 2


# ----- payload shaping + transport degrade -----


def test_build_payload_omits_unset_optionals():
    payload = build_payload(PublishArticleInput(
        title="T", body="B", author="ada-reporter", section="tech",
    ))
    assert "slug" not in payload  # None optional dropped
    assert "published_at" not in payload
    assert "metadata" not in payload
    assert payload["topics"] == []  # an empty list is sent (valid), not dropped


def test_transport_failure_returns_a_typed_result(fake):
    env = _env()
    article = _article(env)
    assignment = _ready_assignment(env, article)
    # Point at a port nothing is listening on -> connection refused, not a raise.
    result = publish_article(article, assignment=assignment,
                             base_url="http://127.0.0.1:1", token="op-token", timeout=2)
    assert result.ok is False
    assert result.code == "transport_error"
    assert result.status == 0


# ----- the content-hash <-> key invariant -----


def test_article_drifted_from_the_finalized_key_is_refused_before_posting(fake):
    # The persisted key was minted from the finalized content; an article whose body
    # drifted from it must be refused, not POSTed under a key that contradicts it
    # (which the platform would 422 as idempotency_key_reused).
    env = _env()
    finalized = _article(env)
    assignment = _ready_assignment(env, finalized)
    drifted = _article(env, body="A DIFFERENT body than the one that was finalized.")

    result = publish_article(drifted, assignment=assignment, base_url=fake.base_url, token="op-token")

    assert result.ok is False
    assert result.code == "content_hash_drift"
    assert result.status == 0
    assert fake.state.publish_requests == []  # nothing sent over the wire


def test_faithfully_reconstructed_article_replays_idempotently(fake):
    # A fresh process (after a crash) rebuilds an EQUIVALENT article object rather than
    # reusing the same instance; the content-derived key makes that replay idempotent,
    # proving idempotency does not depend on in-memory object identity.
    env = _env()
    assignment = _ready_assignment(env, _article(env))
    first = publish_article(_article(env), assignment=assignment, base_url=fake.base_url, token="op-token")
    second = publish_article(_article(env), assignment=assignment, base_url=fake.base_url, token="op-token")

    assert first.status == 201 and second.status == 200 and second.replayed is True
    assert second.id == first.id
    assert len(fake.state.by_hash) == 1
    assert {r["key"] for r in fake.state.publish_requests} == {assignment.idempotency_key}


# ----- response mapping corner cases -----


def test_id_less_2xx_is_not_treated_as_success():
    from newsroom.publish.client import _map_response

    good = _map_response(httpx.Response(201, json={"id": "art_1", "slug": "s"}))
    assert good.ok is True and good.id == "art_1" and good.replayed is False

    no_id = _map_response(httpx.Response(200, json={}))
    assert no_id.ok is False and no_id.code == "malformed_success"

    not_json = _map_response(httpx.Response(201, text="not json at all"))
    assert not_json.ok is False and not_json.code == "malformed_success"
