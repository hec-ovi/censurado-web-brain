"""The publish client (Step 7), driven against the shared fake's ``/articles``.

The fake mirrors the live platform handler's observable contract (bearer auth, the
write/publish-any scope split incl. the 403, the required Idempotency-Key header,
strict decode, author binding, and content-hash idempotency/dedup), so these tests
exercise the real wire contract end to end. The build-plan contracts for Step 7:

  (a) a key missing ``articles:write`` -> 403 insufficient_scope;
  (b) a finalized body POSTs once -> {id, slug};
  (c) replaying the same finalized assignment -> 200, no double-publish;
  (d) a stray section is rejected LOCALLY, before any POST;
  (e) ``metadata.newsroom`` is present in the payload and accepted.

Plus the author-binding 403 (why the operator key needs publish-any), the
transport-failure degrade, and the side-effecting wrapper (mark published + record
one coverage row, idempotent across a replay).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from newsroom.contracts.article import PublishArticleInput
from newsroom.contracts.hashing import content_hash, idempotency_key
from newsroom.db import open_db
from newsroom.manager.coverage import CoverageStore
from newsroom.personas import Persona, PersonaStore
from newsroom.publish import build_payload, publish_article, publish_assignment
from newsroom.runs import RunStore


@dataclass
class Env:
    store: RunStore
    coverage: CoverageStore
    run_id: str
    author: str


def _env() -> Env:
    conn = open_db(":memory:")
    personas = PersonaStore(conn)
    store = RunStore(conn)
    persona = personas.create(Persona(
        display_name="Ada Reporter", beat="tech", who_i_am="I cover chips.", style="dry",
    ))
    run = store.create_run(mode="managed", n_requested=1)
    return Env(store=store, coverage=CoverageStore(conn), run_id=run.id, author=persona.id)


def _article(env: Env, *, title="The Chip Ships", body="A grounded body.", section="tech",
             topics=None, metadata=None) -> PublishArticleInput:
    return PublishArticleInput(
        title=title, body=body, author=env.author, section=section,
        topics=topics or ["chips"], metadata=metadata,
    )


def _ready_assignment(env: Env, article: PublishArticleInput):
    """Create an assignment and finalize it through the store, so its persisted
    content_hash + idempotency_key match the article (as the pipeline leaves it)."""
    a = env.store.create_assignment(run_id=env.run_id, persona_id=env.author, section=article.section)
    chash = content_hash(article.title, article.body, article.author, article.section)
    env.store.finalize_assignment(
        a.id, final_body=article.body, content_hash=chash,
        idempotency_key=idempotency_key(a.id, chash), ledger_digest="sha256:abc",
    )
    return env.store.get_assignment(a.id)


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


# ----- the side-effecting wrapper -----


def test_publish_assignment_marks_published_and_records_coverage(fake):
    env = _env()
    article = _article(env, topics=["chips", "gpu"])
    assignment = _ready_assignment(env, article)

    result = publish_assignment(article, assignment=assignment, store=env.store,
                                base_url=fake.base_url, token="op-token", coverage=env.coverage)

    assert result.ok is True
    row = env.store.get_assignment(assignment.id)
    assert row.status == "published"
    assert row.published_id == result.id
    recent = env.coverage.recent(limit=10)
    assert len(recent) == 1
    assert recent[0].headline == "The Chip Ships"
    assert recent[0].section == "tech"
    assert recent[0].topics == ["chips", "gpu"]
    assert recent[0].published_id == result.id


def test_publish_records_entities_so_a_reworded_dup_is_caught(fake):
    # The publish path is the ONLY place a coverage row is written, so it must carry the
    # assignment's entities. Without them the stored fingerprint has no entity tokens and
    # a later, REWORDED headline about the same event scores as NEW and gets republished
    # (the silent bug the de-dup channel was meant to prevent). This drives the real
    # publish path and pins the last link (row -> coverage) plus the end-to-end effect.
    from newsroom.manager.coverage import classify, fingerprint
    from newsroom.manager.types import Triage

    env = _env()
    article = _article(env, title="Milei anuncia un fuerte recorte del gasto", topics=["economia"])
    a = env.store.create_assignment(
        run_id=env.run_id, persona_id=env.author, section=article.section,
        entities=["Javier Milei", "Casa Rosada"],
    )
    chash = content_hash(article.title, article.body, article.author, article.section)
    env.store.finalize_assignment(
        a.id, final_body=article.body, content_hash=chash,
        idempotency_key=idempotency_key(a.id, chash), ledger_digest="sha256:abc",
    )
    assignment = env.store.get_assignment(a.id)

    result = publish_assignment(article, assignment=assignment, store=env.store,
                                base_url=fake.base_url, token="op-token", coverage=env.coverage)
    assert result.ok is True

    row = env.coverage.recent(limit=1)[0]
    assert row.entities == ["Javier Milei", "Casa Rosada"]  # persisted, not silently dropped

    # A later run surfaces a REWORDED headline about the SAME event (same entities). Now
    # that the entity channel is alive it must NOT come back as NEW.
    candidate = fingerprint(headline="El Gobierno presenta un ajuste del gasto publico",
                            entities=["Javier Milei", "Casa Rosada"])
    verdict = classify(candidate, env.coverage.recent(limit=10),
                       duplicate_threshold=0.6, followup_threshold=0.3)
    assert verdict.triage is not Triage.NEW
    assert verdict.matched is not None


def test_publish_assignment_does_not_double_record_coverage_on_replay(fake):
    env = _env()
    article = _article(env)
    assignment = _ready_assignment(env, article)

    publish_assignment(article, assignment=assignment, store=env.store,
                       base_url=fake.base_url, token="op-token", coverage=env.coverage)
    # A second publish (e.g. a crash-replay) returns the idempotent 200 and must not
    # add a second coverage row. Re-fetch the assignment: it is now "published".
    refreshed = env.store.get_assignment(assignment.id)
    again = publish_assignment(article, assignment=refreshed, store=env.store,
                               base_url=fake.base_url, token="op-token", coverage=env.coverage)

    assert again.replayed is True
    assert len(env.coverage.recent(limit=10)) == 1  # still exactly one


def test_publish_assignment_holds_the_lock_around_its_store_block(fake):
    # When a lock is given (the brain's shared-connection lock), the side-effecting
    # store block runs under it, so a concurrent request-loop read never races the
    # publish writes. CRUCIALLY the POST is NOT under the lock: __enter__ asserts the
    # publish request has ALREADY landed, so a regression wrapping the network call in
    # the lock (the HARD never-lock-across-network rule) would fail this test.
    import threading

    class _OrderingLock:
        def __init__(self, fake):
            self.enters = 0
            self.exits = 0
            self._inner = threading.Lock()
            self._fake = fake

        def __enter__(self):
            assert len(self._fake.state.publish_requests) == 1, "POST must precede lock acquisition"
            self.enters += 1
            self._inner.acquire()
            return self

        def __exit__(self, *exc):
            self._inner.release()
            self.exits += 1
            return False

    env = _env()
    article = _article(env)
    assignment = _ready_assignment(env, article)
    lock = _OrderingLock(fake)

    result = publish_assignment(article, assignment=assignment, store=env.store,
                                base_url=fake.base_url, token="op-token", coverage=env.coverage,
                                lock=lock)

    assert result.ok is True
    assert lock.enters == 1 and lock.enters == lock.exits  # store block ran once, under the lock
    assert env.store.get_assignment(assignment.id).status == "published"


def test_publish_assignment_skips_side_effects_on_failure(fake):
    # On a failed publish the wrapper must leave the assignment "ready" and record no
    # coverage, so a later run can replay it.
    env = _env()
    article = _article(env)
    assignment = _ready_assignment(env, article)

    result = publish_assignment(article, assignment=assignment, store=env.store,
                                base_url=fake.base_url, token="noscope-token", coverage=env.coverage)

    assert result.ok is False and result.status == 403
    row = env.store.get_assignment(assignment.id)
    assert row.status == "ready"  # not marked published
    assert row.published_id is None
    assert env.coverage.recent(limit=10) == []  # no coverage row written


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
