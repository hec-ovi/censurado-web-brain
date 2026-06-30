"""The atomic batch publish seam (POST /articles:batch), driven against the shared
fake. The fake mirrors the platform's batch contract (contracts/batch-*.schema.json):
bearer auth + the write/publish-any scope split, per-item strict decode, the required
per-item ``idempotency_key``, within-batch duplicate-key/slug rejection, content-hash
idempotency/dedup, and the all-or-nothing transaction (any invalid item -> 422, nothing
written). So these tests exercise the real wire contract end to end.

``publish_batch`` is the pure transport: the wire mapping, including the 422 and the
malformed-success guards. (The side-effecting ``publish_batch_assignments`` wrapper that
drove a run's articles + coverage is gone with the run pipeline; the CLI authoring agent
publishes directly now.)
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from jsonschema import Draft202012Validator

from newsroom.contracts.article import PublishArticleInput
from newsroom.contracts.schema import load_batch_request_schema
from newsroom.publish import BatchItem, build_payload, publish_batch
from newsroom.publish import client as client_mod


@dataclass
class Env:
    author: str


def _env() -> Env:
    return Env(author="ada-reporter")


def _article(env: Env, *, title, body=None, section="tech", topics=None, slug=None,
             metadata=None) -> PublishArticleInput:
    return PublishArticleInput(
        title=title, body=body or f"A grounded body about {title}.", author=env.author,
        section=section, topics=topics or ["chips"], slug=slug, metadata=metadata,
    )


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
    # Sent directly (bypassing any service de-collision), two items whose titles derive
    # to the same slug are rejected by the platform's within-batch unique-slug rule, so
    # the fake faithfully reproduces the clash a caller must prevent.
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


# ----- the vendored batch contract matches what the brain builds -----


def test_built_batch_request_validates_against_the_vendored_schema():
    env = _env()
    article = _article(env, title="Schema Check", topics=["chips", "ai"], metadata={"image": "/media/x.png"})
    body = {"articles": [{**build_payload(article), "idempotency_key": "run-0001"}]}
    # The exact wire body the client builds must validate against the vendored request
    # schema (additionalProperties:false + required idempotency_key), so a drifted
    # payload is caught here, not at the platform.
    Draft202012Validator(load_batch_request_schema()).validate(body)
