"""The atomic batch publish seam (POST /articles:batch), driven against the shared
fake. The fake mirrors the platform's batch contract (contracts/batch-*.schema.json):
bearer auth + the write/publish-any scope split, per-item strict decode, the required
per-item ``idempotency_key``, within-batch duplicate-key/slug rejection, content-hash
idempotency/dedup, and the all-or-nothing transaction (any invalid item -> 422, nothing
written). So these tests exercise the real wire contract end to end.

Two layers:
  * ``publish_batch`` (pure transport): the wire mapping, including the 422 and the
    malformed-success guards;
  * ``publish_batch_assignments`` (the side-effecting step): local pre-checks that keep
    a locally-invalid item OUT of the atomic batch, the fan-out of mark-published +
    coverage on success (idempotent across a replay), and the atomic-failure path.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from jsonschema import Draft202012Validator

from newsroom.contracts.article import PublishArticleInput
from newsroom.contracts.hashing import content_hash, idempotency_key
from newsroom.contracts.schema import load_batch_request_schema, load_batch_response_schema
from newsroom.db import open_db
from newsroom.manager.coverage import CoverageStore
from newsroom.personas import Persona, PersonaStore
from newsroom.publish import (
    BatchItem,
    build_payload,
    publish_batch,
    publish_batch_assignments,
)
from newsroom.publish import client as client_mod
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
    run = store.create_run(mode="managed", n_requested=3)
    return Env(store=store, coverage=CoverageStore(conn), run_id=run.id, author=persona.id)


def _article(env: Env, *, title, body=None, section="tech", topics=None, slug=None,
             metadata=None) -> PublishArticleInput:
    return PublishArticleInput(
        title=title, body=body or f"A grounded body about {title}.", author=env.author,
        section=section, topics=topics or ["chips"], slug=slug, metadata=metadata,
    )


def _ready(env: Env, article: PublishArticleInput):
    """Create an assignment and finalize it through the store, so its persisted
    content_hash + idempotency_key match the article (as the pipeline leaves it)."""
    a = env.store.create_assignment(run_id=env.run_id, persona_id=env.author, section=article.section)
    chash = content_hash(article.title, article.body, article.author, article.section)
    env.store.finalize_assignment(
        a.id, final_body=article.body, content_hash=chash,
        idempotency_key=idempotency_key(a.id, chash), ledger_digest="sha256:abc",
    )
    return env.store.get_assignment(a.id), article


# ----- publish_batch_assignments: the happy path -----


def test_batch_publishes_all_ready_articles_in_one_request(fake):
    env = _env()
    pairs = [_ready(env, _article(env, title=t)) for t in ("Chips Ship", "GPU Surge", "RAM Glut")]

    results = publish_batch_assignments(
        pairs, store=env.store, base_url=fake.base_url, token="op-token", coverage=env.coverage,
    )

    assert len(results) == 3 and all(r.ok and r.id for r in results)
    assert all(not r.replayed for r in results)  # all freshly created
    # ONE batch request carried all three; the single endpoint was never touched.
    assert len(fake.state.batch_requests) == 1
    assert fake.state.batch_requests[0]["status"] == 201
    assert len(fake.state.batch_requests[0]["items"]) == 3
    assert fake.state.publish_requests == []
    # Every assignment was marked published and recorded exactly one coverage row.
    for assignment, _ in pairs:
        assert env.store.get_assignment(assignment.id).status == "published"
    headlines = {c.headline for c in env.coverage.recent(limit=10)}
    assert headlines == {"Chips Ship", "GPU Surge", "RAM Glut"}


def test_batch_carries_per_item_idempotency_keys(fake):
    env = _env()
    pairs = [_ready(env, _article(env, title=t)) for t in ("One", "Two")]

    publish_batch_assignments(pairs, store=env.store, base_url=fake.base_url, token="op-token")

    sent = fake.state.batch_requests[0]["items"]
    # No Idempotency-Key header for a batch; each item carries its own persisted key.
    assert [it["idempotency_key"] for it in sent] == [a.idempotency_key for a, _ in pairs]


def test_batch_replay_is_idempotent(fake):
    env = _env()
    pairs = [_ready(env, _article(env, title=t)) for t in ("Alpha", "Beta")]

    first = publish_batch_assignments(
        pairs, store=env.store, base_url=fake.base_url, token="op-token", coverage=env.coverage,
    )
    # A crash-replay rebuilds the rows and resends the same batch with the same keys.
    refreshed = [(env.store.get_assignment(a.id), art) for a, art in pairs]
    second = publish_batch_assignments(
        refreshed, store=env.store, base_url=fake.base_url, token="op-token", coverage=env.coverage,
    )

    assert all(r.status == 201 and not r.replayed for r in first)
    assert all(r.status == 200 and r.replayed for r in second)  # all deduplicated
    assert {r.id for r in first} == {r.id for r in second}  # same articles, not new ones
    assert len(fake.state.by_hash) == 2  # still only two articles exist
    assert len(env.coverage.recent(limit=10)) == 2  # coverage recorded once, not doubled


def test_batch_with_two_identical_articles_creates_one_and_dedups_the_other(fake):
    # Two DISTINCT assignments whose articles are byte-identical (same content_hash,
    # different idempotency_key) in ONE POST: the platform creates the first and dedups
    # the second against it within the same transaction. One article is minted, but two
    # ready assignments each record their own coverage row (coverage is per-assignment).
    env = _env()
    a = _ready(env, _article(env, title="Same Story", body="Identical body."))
    b = _ready(env, _article(env, title="Same Story", body="Identical body."))

    results = publish_batch_assignments(
        [a, b], store=env.store, base_url=fake.base_url, token="op-token", coverage=env.coverage,
    )

    assert len(fake.state.batch_requests) == 1 and fake.state.batch_requests[0]["status"] == 201
    assert results[0].ok and not results[0].replayed  # created
    assert results[1].ok and results[1].replayed      # deduplicated against the first
    assert results[0].id == results[1].id             # same platform article
    assert len(fake.state.by_hash) == 1               # only one article minted
    assert len(env.coverage.recent(limit=10)) == 2    # but two assignments published


def test_batch_mixes_created_and_deduplicated(fake):
    env = _env()
    first_pair = _ready(env, _article(env, title="Already Out"))
    publish_batch_assignments([first_pair], store=env.store, base_url=fake.base_url, token="op-token")

    # A later batch that re-includes the published one plus a fresh one: the first
    # comes back deduplicated, the second created, and the batch status is 201.
    fresh_pair = _ready(env, _article(env, title="Brand New"))
    refreshed_first = (env.store.get_assignment(first_pair[0].id), first_pair[1])
    results = publish_batch_assignments(
        [refreshed_first, fresh_pair], store=env.store, base_url=fake.base_url, token="op-token",
    )

    assert results[0].replayed is True and results[1].replayed is False
    assert results[0].ok and results[1].ok
    assert fake.state.batch_requests[-1]["status"] == 201  # at least one created


# ----- local pre-checks keep a bad item OUT of the atomic batch -----


def test_batch_excludes_a_stray_section_and_publishes_the_rest(fake):
    env = _env()
    good_a = _ready(env, _article(env, title="Good One"))
    # model_construct bypasses the section validator, simulating a payload that reached
    # publish with an off-vocabulary section.
    stray = PublishArticleInput.model_construct(
        title="Stray", body="b", author=env.author, section="sports", topics=[], slug=None,
        metadata=None,
    )
    stray_pair = _ready(env, _article(env, title="Stray"))
    stray_pair = (stray_pair[0], stray)
    good_b = _ready(env, _article(env, title="Good Two"))

    results = publish_batch_assignments(
        [good_a, stray_pair, good_b], store=env.store, base_url=fake.base_url, token="op-token",
        coverage=env.coverage,
    )

    # The stray item never went over the wire (it would 422 the whole atomic batch);
    # only the two valid ones were sent, and they published.
    assert len(fake.state.batch_requests[0]["items"]) == 2
    assert results[0].ok and results[2].ok
    assert results[1].ok is False and results[1].code == "invalid_section"


def test_batch_excludes_a_drifted_body(fake):
    env = _env()
    finalized = _article(env, title="Finalized")
    assignment, _ = _ready(env, finalized)
    drifted = _article(env, title="Finalized", body="A DIFFERENT body than was finalized.")
    other = _ready(env, _article(env, title="Other"))

    results = publish_batch_assignments(
        [(assignment, drifted), other], store=env.store, base_url=fake.base_url, token="op-token",
    )

    assert results[0].ok is False and results[0].code == "content_hash_drift"
    assert results[1].ok is True
    assert len(fake.state.batch_requests[0]["items"]) == 1  # only the clean one was sent


def test_batch_decollides_a_duplicate_slug_so_the_batch_still_lands(fake):
    # Two ready articles that both chose the slug "scoop" would 422 the atomic batch
    # (the platform dedups on the derived slug). The service pins the later one to a
    # unique slug so the platform stores it distinctly; both publish.
    env = _env()
    a = _ready(env, _article(env, title="First Scoop", slug="scoop"))
    b = _ready(env, _article(env, title="Second Scoop", slug="scoop"))

    results = publish_batch_assignments(
        [a, b], store=env.store, base_url=fake.base_url, token="op-token",
    )

    assert all(r.ok for r in results)
    sent = fake.state.batch_requests[0]["items"]
    assert sent[0]["slug"] == "scoop"
    assert sent[1]["slug"] == "scoop-2"  # de-collided to a unique slug
    assert len(fake.state.batch_requests) == 1


def test_batch_decollides_a_derived_slug_collision(fake):
    # Neither article sets an explicit slug, but both titles slugify to the same value.
    # The platform would 422 the atomic batch on the derived-slug clash; the service
    # predicts the derived slug and de-collides the later one so both publish.
    env = _env()
    a = _ready(env, _article(env, title="Chips Ship", body="Body one."))
    b = _ready(env, _article(env, title="Chips ship!", body="Body two, different."))

    results = publish_batch_assignments(
        [a, b], store=env.store, base_url=fake.base_url, token="op-token",
    )

    assert all(r.ok for r in results)
    sent = fake.state.batch_requests[0]["items"]
    assert "slug" not in sent[0]  # first keeps its derived slug (no explicit slug sent)
    assert sent[1]["slug"] == "chips-ship-2"  # second pinned to a unique slug
    # Two distinct articles really landed (different bodies -> different content hashes).
    assert len(fake.state.by_hash) == 2


# ----- the atomic failure path: nothing written, every item marked for the caller -----


def test_batch_author_mismatch_fails_the_whole_batch(fake):
    # A write-only key locked to "agent-a" cannot author as the personas. Every item
    # 422s on author binding, so NOTHING is written and every result is ok=False with a
    # per-item code (the caller marks them publish_failed).
    env = _env()
    pairs = [_ready(env, _article(env, title=t)) for t in ("X", "Y")]

    results = publish_batch_assignments(
        pairs, store=env.store, base_url=fake.base_url, token="agent-a-token", coverage=env.coverage,
    )

    assert all(r.ok is False and r.status == 422 for r in results)
    assert all(r.code == "author_mismatch" for r in results)
    assert fake.state.by_hash == {}  # atomic: nothing written
    assert fake.state.batch_requests[0]["status"] == 422
    # The side-effecting wrapper left the rows publishable and recorded no coverage.
    for assignment, _ in pairs:
        assert env.store.get_assignment(assignment.id).status == "ready"
    assert env.coverage.recent(limit=10) == []


def test_batch_insufficient_scope_fails_atomically(fake):
    env = _env()
    pairs = [_ready(env, _article(env, title="Z"))]
    results = publish_batch_assignments(
        pairs, store=env.store, base_url=fake.base_url, token="noscope-token",
    )
    assert results[0].ok is False and results[0].status == 403
    assert results[0].code == "insufficient_scope"
    assert fake.state.by_hash == {}


def test_batch_transport_failure_returns_typed_results(fake):
    env = _env()
    pairs = [_ready(env, _article(env, title=t)) for t in ("P", "Q")]
    # Point at a port nothing listens on -> connection refused, not a raise.
    results = publish_batch_assignments(
        pairs, store=env.store, base_url="http://127.0.0.1:1", token="op-token", timeout=2,
    )
    assert all(r.ok is False and r.code == "transport_error" for r in results)
    for assignment, _ in pairs:
        assert env.store.get_assignment(assignment.id).status == "ready"


def test_empty_batch_makes_no_request(fake):
    env = _env()
    results = publish_batch_assignments([], store=env.store, base_url=fake.base_url, token="op-token")
    assert results == []
    assert fake.state.batch_requests == []  # no POST for an empty batch


# ----- the lock is held around the store block, never across the POST -----


def test_batch_holds_the_lock_around_its_store_block_not_the_post(fake):
    # The POST must precede any lock acquisition (the hard never-lock-across-the-wire
    # rule): __enter__ asserts the batch request has ALREADY landed.
    import threading

    class _OrderingLock:
        def __init__(self, fake):
            self.enters = 0
            self.exits = 0
            self._inner = threading.Lock()
            self._fake = fake

        def __enter__(self):
            assert len(self._fake.state.batch_requests) == 1, "POST must precede lock acquisition"
            self.enters += 1
            self._inner.acquire()
            return self

        def __exit__(self, *exc):
            self._inner.release()
            self.exits += 1
            return False

    env = _env()
    pairs = [_ready(env, _article(env, title=t)) for t in ("L1", "L2")]
    lock = _OrderingLock(fake)

    results = publish_batch_assignments(
        pairs, store=env.store, base_url=fake.base_url, token="op-token", coverage=env.coverage,
        lock=lock,
    )

    assert all(r.ok for r in results)
    # One store block per published item, each under the lock, balanced enter/exit.
    assert lock.enters == 2 and lock.enters == lock.exits


# ----- publish_batch (pure transport) wire mapping -----


def _bi(env, title, key) -> BatchItem:
    return BatchItem(assignment_id=f"as-{key}", article=_article(env, title=title), idempotency_key=key)


def test_publish_batch_empty_makes_no_request(fake):
    res = publish_batch([], base_url=fake.base_url, token="op-token")
    assert res.ok and res.status == 200 and res.items == []
    assert fake.state.batch_requests == []


def test_publish_batch_duplicate_key_within_batch_is_a_422(fake):
    env = _env()
    items = [_bi(env, "One", "dup"), _bi(env, "Two", "dup")]
    res = publish_batch(items, base_url=fake.base_url, token="op-token")

    assert res.ok is False and res.status == 422 and res.code == "validation_failed"
    assert all(not it.ok for it in res.items)  # atomic: every item failed
    # The named offending index carries the specific code.
    assert res.items[1].code == "duplicate_idempotency_key"
    assert fake.state.by_hash == {}  # nothing written


def test_publish_batch_malformed_success_wrong_count_is_not_a_phantom_publish(monkeypatch):
    # A 201 whose results count does not match the items submitted is not a usable
    # success: the whole batch is reported failed rather than reporting phantom ids.
    env = _env()
    items = [_bi(env, "One", "k1"), _bi(env, "Two", "k2")]
    monkeypatch.setattr(
        client_mod.httpx, "post",
        lambda *_a, **_k: httpx.Response(201, json={"results": [{"index": 0, "id": "1", "slug": "s", "status": "created"}]}),
    )
    res = publish_batch(items, base_url="http://portal", token="op-token")
    assert res.ok is False and res.code == "malformed_success"
    assert all(not it.ok for it in res.items)


def test_publish_batch_per_item_unknown_status_is_not_a_phantom_publish(monkeypatch):
    # A 201 with the RIGHT result count but a per-item status outside {created,
    # deduplicated} (or a missing id) is also not a usable success: the per-index guard
    # fails the whole batch rather than reporting a non-final or id-less publish.
    env = _env()
    items = [_bi(env, "One", "k1"), _bi(env, "Two", "k2")]
    monkeypatch.setattr(
        client_mod.httpx, "post",
        lambda *_a, **_k: httpx.Response(201, json={"results": [
            {"index": 0, "id": "1", "slug": "s", "status": "created"},
            {"index": 1, "id": "2", "slug": "t", "status": "queued"},  # unknown status
        ]}),
    )
    res = publish_batch(items, base_url="http://portal", token="op-token")
    assert res.ok is False and res.code == "malformed_success"
    assert all(not it.ok for it in res.items)


# ----- fake fidelity: the per-item validation matches the real platform handler -----


def test_batch_missing_idempotency_key_reports_its_own_code(fake):
    # The platform reports a blank/missing per-item key with code "missing_idempotency_key"
    # (its own code), not the generic validation_failed.
    env = _env()
    item = BatchItem(assignment_id="a", article=_article(env, title="No Key"), idempotency_key="")
    res = publish_batch([item], base_url=fake.base_url, token="op-token")
    assert res.ok is False and res.status == 422
    assert res.items[0].code == "missing_idempotency_key"
    assert fake.state.by_hash == {}


def test_batch_duplicate_key_is_caught_before_author_binding(fake):
    # Precedence: the platform checks the within-batch duplicate key (and registers the
    # key) BEFORE the author check. With a write-only key whose author matches neither
    # item, the FIRST item fails author_mismatch but its key is still registered, so the
    # SECOND item (same key) is the duplicate, not another author_mismatch.
    env = _env()
    items = [_bi(env, "One", "dup"), _bi(env, "Two", "dup")]
    res = publish_batch(items, base_url=fake.base_url, token="agent-a-token")
    assert res.ok is False and res.status == 422
    assert res.items[0].code == "author_mismatch"
    assert res.items[1].code == "duplicate_idempotency_key"
    assert fake.state.by_hash == {}


def test_batch_rejects_a_derived_slug_collision_at_the_wire(fake):
    # Sent directly (bypassing the service de-collision), two items whose titles derive
    # to the same slug are rejected by the platform's within-batch unique-slug rule, so
    # the fake faithfully reproduces the clash the service exists to prevent.
    env = _env()
    items = [
        BatchItem("a", _article(env, title="Chips Ship", body="Body A."), "ka"),
        BatchItem("b", _article(env, title="chips ship!", body="Body B, different."), "kb"),
    ]
    res = publish_batch(items, base_url=fake.base_url, token="op-token")
    assert res.ok is False and res.status == 422
    assert res.items[1].code == "duplicate_slug"
    assert fake.state.by_hash == {}


def test_batch_over_the_item_cap_is_too_many_items(fake):
    env = _env()
    fake.state.batch_max_items = 2
    items = [_bi(env, f"T{i}", f"k{i}") for i in range(3)]
    res = publish_batch(items, base_url=fake.base_url, token="op-token")
    assert res.ok is False and res.status == 422 and res.code == "too_many_items"
    assert fake.state.by_hash == {}  # nothing written


# ----- the vendored batch contract matches what the brain builds/expects -----


def test_built_batch_request_validates_against_the_vendored_schema(fake):
    env = _env()
    article = _article(env, title="Schema Check", topics=["chips", "ai"], metadata={"image": "/media/x.png"})
    body = {"articles": [{**build_payload(article), "idempotency_key": "run-0001"}]}
    # The exact wire body the client builds must validate against the vendored request
    # schema (additionalProperties:false + required idempotency_key), so a drifted
    # payload is caught here, not at the platform.
    Draft202012Validator(load_batch_request_schema()).validate(body)


def test_batch_response_shape_validates_against_the_vendored_schema(fake):
    env = _env()
    pairs = [_ready(env, _article(env, title=t)) for t in ("R1", "R2")]
    publish_batch_assignments(pairs, store=env.store, base_url=fake.base_url, token="op-token")
    response = {"results": fake.state.batch_requests[0]["results"]}
    Draft202012Validator(load_batch_response_schema()).validate(response)
