"""The shared fake faithfully models both seams it stands in for.

These tests pin the fake's behavior against the platform's observable publish
contract and the no-output-cap rule for inference. Every later step reuses this
fake, so its fidelity is load-bearing.
"""

from __future__ import annotations

import httpx
import pytest

OP = {"Authorization": "Bearer op-token"}
AGENT = {"Authorization": "Bearer agent-a-token"}
NOSCOPE = {"Authorization": "Bearer noscope-token"}
IDEM = "Idempotency-Key"


def _article(**overrides) -> dict:
    base = {
        "title": "A Headline",
        "body": "Some **markdown** body.",
        "author": "newsroom-operator",
        "section": "tech",
    }
    base.update(overrides)
    return base


# ---------------- inference: /v1/chat/completions ----------------


def test_health_boots(fake):
    r = httpx.get(f"{fake.base_url}/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_chat_default_response(fake):
    r = httpx.post(fake.chat_url, json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "(scripted-default)"


def test_chat_scripted_response(fake):
    fake.state.script_chat("hello from the script")
    r = httpx.post(fake.chat_url, json={"model": "m", "messages": []})
    assert r.json()["choices"][0]["message"]["content"] == "hello from the script"


def test_chat_records_request_body(fake):
    httpx.post(fake.chat_url, json={"model": "m", "messages": [{"role": "user", "content": "x"}]})
    assert fake.state.chat_requests[-1]["body"]["model"] == "m"


def test_chat_rejects_length_cap(fake):
    r = httpx.post(fake.chat_url, json={"model": "m", "messages": [], "max_tokens": 16})
    assert r.status_code == 422
    assert r.json()["error"] == "forbidden_length_cap"
    assert "max_tokens" in r.json()["keys"]


def test_chat_rejects_aliased_length_cap(fake):
    r = httpx.post(fake.chat_url, json={"model": "m", "messages": [], "num_predict": 8})
    assert r.status_code == 422
    assert "num_predict" in r.json()["keys"]


# ---------------- publish: /articles ----------------


def test_publish_missing_token(fake):
    r = httpx.post(fake.articles_url, headers={IDEM: "k1"}, json=_article())
    assert r.status_code == 401
    assert r.json()["code"] == "missing_token"


def test_publish_unknown_token(fake):
    r = httpx.post(fake.articles_url, headers={"Authorization": "Bearer nope", IDEM: "k1"}, json=_article())
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_token"


def test_publish_insufficient_scope(fake):
    r = httpx.post(fake.articles_url, headers={**NOSCOPE, IDEM: "k1"}, json=_article(author="nobody"))
    assert r.status_code == 403
    assert r.json()["code"] == "insufficient_scope"


def test_publish_missing_idempotency_key(fake):
    r = httpx.post(fake.articles_url, headers=OP, json=_article())
    assert r.status_code == 400
    assert r.json()["code"] == "missing_idempotency_key"


def test_publish_unknown_field_is_400(fake):
    r = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=_article(surprise="x"))
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_json"


def test_publish_author_mismatch_for_agent_key(fake):
    r = httpx.post(fake.articles_url, headers={**AGENT, IDEM: "k1"}, json=_article(author="someone-else"))
    assert r.status_code == 403
    assert r.json()["code"] == "author_mismatch"


def test_publish_agent_key_authoring_self_ok(fake):
    r = httpx.post(fake.articles_url, headers={**AGENT, IDEM: "k1"}, json=_article(author="agent-a"))
    assert r.status_code == 201


def test_publish_operator_may_author_anyone(fake):
    r = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=_article(author="joshua"))
    assert r.status_code == 201
    assert set(r.json()) == {"id", "slug"}


def test_publish_missing_required_is_422(fake):
    r = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=_article(title="   "))
    assert r.status_code == 422
    assert r.json()["code"] == "validation_failed"
    assert r.json()["fields"] == {"title": "required"}


def test_publish_idempotent_replay_same_key_same_body(fake):
    body = _article(author="joshua")
    first = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=body)
    second = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=body)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json() == second.json()


def test_publish_idempotency_conflict_same_key_different_body(fake):
    httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=_article(title="One", author="joshua"))
    r = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=_article(title="Two", author="joshua"))
    assert r.status_code == 422
    assert r.json()["code"] == "idempotency_key_reused"


def test_publish_content_dedup_across_keys(fake):
    body = _article(author="joshua")
    first = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=body)
    # A different key but identical content dedups to the same article (201 then 200).
    second = httpx.post(fake.articles_url, headers={**OP, IDEM: "k2"}, json=body)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


# ---------------- inference: full cap-alias matrix ----------------

CAP_ALIASES = [
    "max_tokens",
    "maxTokens",
    "max_completion_tokens",
    "maxCompletionTokens",
    "max_output_tokens",
    "maxOutputTokens",
    "max_new_tokens",
    "n_predict",
    "num_predict",
    "max_length",
    "max_words",
]


@pytest.mark.parametrize("cap_key", CAP_ALIASES)
def test_chat_rejects_every_length_cap_alias(fake, cap_key):
    r = httpx.post(fake.chat_url, json={"model": "m", "messages": [], cap_key: 8})
    assert r.status_code == 422
    assert r.json()["error"] == "forbidden_length_cap"
    assert cap_key in r.json()["keys"]


def test_chat_rejects_nested_length_cap(fake):
    r = httpx.post(fake.chat_url, json={"model": "m", "messages": [], "options": {"num_predict": 4}})
    assert r.status_code == 422
    assert "num_predict" in r.json()["keys"]


def test_chat_rejects_malformed_json(fake):
    r = httpx.post(
        fake.chat_url, headers={"content-type": "application/json"}, content=b"{ not json"
    )
    assert r.status_code == 400


def test_chat_rejects_non_object_body(fake):
    r = httpx.post(fake.chat_url, json=["not", "an", "object"])
    assert r.status_code == 400


def test_chat_scripted_finish_reason_round_trips(fake):
    fake.state.script_chat("partial output", finish_reason="length")
    r = httpx.post(fake.chat_url, json={"model": "m", "messages": []})
    assert r.json()["choices"][0]["finish_reason"] == "length"
    assert r.json()["choices"][0]["message"]["content"] == "partial output"


# ---------------- publish: type strictness (Go decoder fidelity) ----------------


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("title", 123),
        ("body", True),
        ("section", 5),
        ("slug", 9),
        ("topics", "notalist"),
        ("topics", [1, 2]),
        ("metadata", []),
        ("metadata", "x"),
        ("published_at", "not-a-date"),
        ("published_at", 123),
    ],
)
def test_publish_rejects_wrong_typed_field(fake, field, bad_value):
    body = _article(author="joshua")
    body[field] = bad_value
    r = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=body)
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_json"


def test_publish_null_author_operator_is_422_required(fake):
    # Go: null author -> "" -> NewArticle 422 author:required (publish-any bypasses binding).
    body = _article()
    body["author"] = None
    r = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=body)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_failed"
    assert r.json()["fields"] == {"author": "required"}


def test_publish_null_author_agent_is_403_mismatch(fake):
    # Go: null author -> "" -> binding "" != "agent-a" -> 403 author_mismatch.
    body = _article()
    body["author"] = None
    r = httpx.post(fake.articles_url, headers={**AGENT, IDEM: "k1"}, json=body)
    assert r.status_code == 403
    assert r.json()["code"] == "author_mismatch"


def test_publish_numeric_author_is_400(fake):
    # Go: numeric author -> UnmarshalTypeError -> 400 invalid_json (before any branch).
    body = _article()
    body["author"] = 123
    r = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=body)
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_json"


def test_publish_rejects_malformed_json(fake):
    r = httpx.post(
        fake.articles_url,
        headers={**OP, IDEM: "k1", "content-type": "application/json"},
        content=b"{ not json",
    )
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_json"


def test_publish_rejects_non_object_body(fake):
    r = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=[1, 2, 3])
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_json"


# ---------------- publish: accepted optional fields + slug derivation ----------------


def test_publish_accepts_optional_fields(fake):
    body = _article(
        author="joshua",
        topics=["ai", "policy"],
        metadata={"newsroom": {"run_id": "r1"}},
        published_at="2026-06-23T10:00:00Z",
    )
    r = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=body)
    assert r.status_code == 201


def test_publish_honors_provided_slug(fake):
    body = _article(author="joshua", slug="my-custom-slug")
    r = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=body)
    assert r.status_code == 201
    assert r.json()["slug"] == "my-custom-slug"


def test_publish_slug_fallback_when_title_unsluggable(fake):
    body = _article(author="joshua", title="!!!")
    r = httpx.post(fake.articles_url, headers={**OP, IDEM: "k1"}, json=body)
    assert r.status_code == 201
    assert len(r.json()["slug"]) == 12  # content-hash prefix fallback
