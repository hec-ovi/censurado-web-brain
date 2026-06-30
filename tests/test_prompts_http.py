"""The prompt-library management API (phase B1), driven end to end.

This exercises the REAL entry point: ``create_app`` mounted under a FastAPI ``TestClient``,
hit over HTTP. It walks the versioned prompt store (publish -> get -> publish again ->
versions -> promote/rollback), the library overview (one row per key), and the error edges
(an unknown key is 404, a blank key is 422). It also covers ``seed_prompts`` lifting the
real ``prompts/`` tree into the table idempotently.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from newsroom.brain import create_app
from newsroom.config import Settings, load_settings
from newsroom.db import open_db
from newsroom.editorial import PromptStore
from newsroom.editorial.seeds import seed_prompts


def _client(tmp_path) -> TestClient:
    settings = Settings(
        persona_db_path=tmp_path / "brain.db",
    )
    return TestClient(create_app(settings=settings))


# ----- versioned lifecycle -----


def test_publish_creates_v1_and_it_is_active(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/prompts/template",
        json={"key": "journalist/draft.md", "body": "draft v1", "created_by": "hector"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["key"] == "journalist/draft.md"
    assert body["version"] == 1
    assert body["is_active"] is True  # the first version of a key auto-activates
    assert body["created_by"] == "hector"
    assert body["body"] == "draft v1"


def test_second_publish_same_key_creates_v2_and_flips_active(tmp_path):
    client = _client(tmp_path)
    client.post("/prompts/template", json={"key": "journalist/draft.md", "body": "v1"})
    v2 = client.post("/prompts/template", json={"key": "journalist/draft.md", "body": "v2"}).json()
    assert v2["version"] == 2 and v2["is_active"] is True

    # The active read is now v2 (publish activates by default).
    active = client.get("/prompts/template", params={"key": "journalist/draft.md"}).json()
    assert active["version"] == 2 and active["body"] == "v2"


def test_get_template_returns_the_active_body(tmp_path):
    client = _client(tmp_path)
    client.post("/prompts/template", json={"key": "manager/triage.md", "body": "triage body"})
    got = client.get("/prompts/template", params={"key": "manager/triage.md"})
    assert got.status_code == 200
    assert got.json()["body"] == "triage body"


def test_versions_lists_newest_first_with_active_flags_and_total(tmp_path):
    client = _client(tmp_path)
    for i in range(3):
        client.post("/prompts/template", json={"key": "journalist/enrich.md", "body": f"v{i}"})

    versions = client.get("/prompts/versions", params={"key": "journalist/enrich.md"}).json()
    assert versions["total"] == 3
    assert [v["version"] for v in versions["versions"]] == [3, 2, 1]  # newest first
    assert [v["is_active"] for v in versions["versions"]] == [True, False, False]

    page = client.get(
        "/prompts/versions", params={"key": "journalist/enrich.md", "limit": 1, "offset": 1}
    ).json()
    assert page["total"] == 3  # the count before the window, not the page size
    assert [v["version"] for v in page["versions"]] == [2]


def test_promote_reverts_to_an_older_version(tmp_path):
    client = _client(tmp_path)
    client.post("/prompts/template", json={"key": "journalist/research.md", "body": "v1 body"})
    client.post("/prompts/template", json={"key": "journalist/research.md", "body": "v2 body"})

    promoted = client.post("/prompts/versions/1/promote")
    assert promoted.status_code == 200
    assert promoted.json()["version"] == 1 and promoted.json()["is_active"] is True
    # The active read rolled back to v1; v2 stays retrievable in the history.
    active = client.get("/prompts/template", params={"key": "journalist/research.md"}).json()
    assert active["version"] == 1 and active["body"] == "v1 body"


def test_publish_staged_without_activating(tmp_path):
    # activate=False stages a version without moving the key's pointer.
    client = _client(tmp_path)
    client.post("/prompts/template", json={"key": "manager/triage.md", "body": "live"})
    staged = client.post(
        "/prompts/template", json={"key": "manager/triage.md", "body": "staged", "activate": False}
    ).json()
    assert staged["version"] == 2 and staged["is_active"] is False
    active = client.get("/prompts/template", params={"key": "manager/triage.md"}).json()
    assert active["body"] == "live"  # unchanged


def test_list_prompts_one_row_per_key(tmp_path):
    client = _client(tmp_path)
    # Two keys, the first with two versions: the listing shows ONE row per key (its active).
    client.post("/prompts/template", json={"key": "journalist/draft.md", "body": "d1"})
    client.post("/prompts/template", json={"key": "journalist/draft.md", "body": "d2"})
    client.post("/prompts/template", json={"key": "persona/synthesize.md", "body": "s1"})

    listing = client.get("/prompts").json()
    assert listing["total"] == 2
    by_key = {t["key"]: t for t in listing["templates"]}
    assert set(by_key) == {"journalist/draft.md", "persona/synthesize.md"}
    assert by_key["journalist/draft.md"]["version"] == 2  # the active version of the key
    assert all(t["is_active"] for t in listing["templates"])


# ----- error edges -----


def test_get_template_unknown_key_is_404(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/prompts/template", params={"key": "nope/missing.md"})
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "prompt_not_found"


def test_publish_empty_key_is_422(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/prompts/template", json={"key": "   ", "body": "x"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_prompt"


def test_promote_unknown_version_is_404(tmp_path):
    client = _client(tmp_path)
    client.post("/prompts/template", json={"key": "journalist/draft.md", "body": "v1"})
    resp = client.post("/prompts/versions/99/promote")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_publish_empty_body_is_422(tmp_path):
    # A blank body would silently neuter the stage that loads it once runs read the store.
    client = _client(tmp_path)
    resp = client.post("/prompts/template", json={"key": "journalist/draft.md", "body": "   "})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_prompt"


# ----- store-level safety branch (not reachable from the route, which derives key from version) -----


def test_promote_rejects_a_version_from_a_different_key():
    conn = open_db(":memory:")
    store = PromptStore(conn)
    a = store.add_version("journalist/draft.md", "a-body")
    store.add_version("manager/triage.md", "b-body")
    # Promoting key "manager/triage.md" to a version that belongs to "journalist/draft.md" must fail.
    with pytest.raises(KeyError):
        store.promote("manager/triage.md", a.version)


def test_openapi_types_the_prompt_responses(tmp_path):
    client = _client(tmp_path)
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in ("PromptOut", "PromptListOut", "PromptVersionsOut"):
        assert name in schemas


# ----- seed_prompts: lift the real prompts/ tree into the table -----


def test_seed_prompts_lifts_real_files_and_is_idempotent():
    conn = open_db(":memory:")
    created, skipped = seed_prompts(conn)
    assert skipped == []
    # Every <role>/<name>.md under prompts/ landed as an active v1 of its key.
    assert "journalist/research.md" in created
    assert "persona/synthesize.md" in created
    store = PromptStore(conn)
    keys = store.list_keys()
    assert set(keys) == set(created)
    # version is a GLOBAL autoincrement, so each seeded key is on its own (single) version.
    assert store.active("persona/synthesize.md") is not None
    assert len(store.list_versions("persona/synthesize.md")) == 1

    # A second call is a no-op: everything is now skipped, nothing re-created.
    again_created, again_skipped = seed_prompts(conn)
    assert again_created == []
    assert set(again_skipped) == set(created)
    # No duplicate versions crept in.
    assert len(store.list_versions("persona/synthesize.md")) == 1


def test_seed_prompts_body_matches_the_real_file():
    conn = open_db(":memory:")
    seed_prompts(conn)
    body = PromptStore(conn).active("persona/synthesize.md").body
    on_disk = (load_settings().prompts_dir / "persona" / "synthesize.md").read_text(encoding="utf-8")
    assert body == on_disk


# ----- on-disk fallback (the shipped prompts serve before any seeder runs) -----


def _client_with_prompt_lib(tmp_path):
    """A client whose prompts_dir is a CONTROLLED on-disk library: one shipped prompt under
    it, plus a secret .md OUTSIDE it to prove the traversal guard. The store starts empty."""
    lib = tmp_path / "prompts"
    (lib / "journalist").mkdir(parents=True)
    (lib / "journalist" / "draft.md").write_text("DISK DRAFT BODY", encoding="utf-8")
    (tmp_path / "secret.md").write_text("SECRET OUTSIDE THE LIBRARY", encoding="utf-8")
    settings = Settings(persona_db_path=tmp_path / "brain.db", prompts_dir=lib)
    return TestClient(create_app(settings=settings))


def test_get_template_falls_back_to_on_disk_when_store_is_empty(tmp_path):
    # The store has no version for the key, but the prompt ships on disk: serve it, marked v0.
    client = _client_with_prompt_lib(tmp_path)
    resp = client.get("/prompts/template", params={"key": "journalist/draft.md"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["body"] == "DISK DRAFT BODY"
    assert body["version"] == 0
    assert body["created_by"] == "disk"
    assert body["is_active"] is True


def test_store_version_overrides_the_on_disk_fallback(tmp_path):
    # Publishing a version makes the store win: the disk default is the override target, not
    # the other way round.
    client = _client_with_prompt_lib(tmp_path)
    client.post("/prompts/template", json={"key": "journalist/draft.md", "body": "OPERATOR EDIT"})
    active = client.get("/prompts/template", params={"key": "journalist/draft.md"}).json()
    assert active["body"] == "OPERATOR EDIT"
    assert active["version"] >= 1
    assert active["created_by"] != "disk"


def test_unknown_key_is_404_even_with_the_disk_fallback(tmp_path):
    client = _client_with_prompt_lib(tmp_path)
    resp = client.get("/prompts/template", params={"key": "journalist/does-not-exist.md"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "prompt_not_found"


def test_disk_fallback_refuses_path_traversal(tmp_path):
    # A key escaping prompts_dir must never read a file outside the library.
    client = _client_with_prompt_lib(tmp_path)
    resp = client.get("/prompts/template", params={"key": "../secret.md"})
    assert resp.status_code == 404
    assert "SECRET" not in resp.text
