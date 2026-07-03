"""The prompt-library management API, driven end to end.

This exercises the REAL entry point: ``create_app`` mounted under a FastAPI ``TestClient``,
hit over HTTP. It covers the two prompt families the router splits by key prefix:

* AGENTIC WORKFLOW nodes (``workflow/*``) are FILE-BASED: a read serves the on-disk file,
  a publish WRITES the file in place (git is the version history, not the DB), and they
  never enter the versioned store. Every workflow test points ``prompts_dir`` at a tmp
  library so a write lands there, never in the real repo tree.
* Author-voice / house-style prompts (``persona/*`` etc.) are STORE-VERSIONED: publish ->
  get -> publish again -> versions -> promote/rollback, with the shipped disk body as the
  fallback the store overrides.

Plus ``seed_prompts`` lifting the real ``persona/`` files into the store while leaving the
file-based ``workflow/`` nodes out, and the error edges (unknown key 404, blank key/body 422,
path traversal refused).
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
    """A client on the real shipped prompts_dir. Safe for STORE writes (they land in the tmp
    DB) and for READS; never POST a ``workflow/`` key here (that would write the repo tree)."""
    return TestClient(create_app(settings=Settings(persona_db_path=tmp_path / "brain.db")))


def _lib_client(tmp_path):
    """A client whose ``prompts_dir`` is a CONTROLLED tmp library: one workflow node and one
    persona prompt on disk, plus a secret file OUTSIDE it to prove the traversal guard. The
    store starts empty. Returns (client, lib_path) so a test can read back a file the API wrote."""
    lib = tmp_path / "prompts"
    (lib / "workflow").mkdir(parents=True)
    (lib / "workflow" / "50-draft.md").write_text("DISK DRAFT BODY\n", encoding="utf-8")
    (lib / "workflow" / "manifest.json").write_text('{"modes": {}}\n', encoding="utf-8")
    (lib / "persona").mkdir(parents=True)
    (lib / "persona" / "synthesize.md").write_text("PERSONA DISK BODY\n", encoding="utf-8")
    (tmp_path / "secret.md").write_text("SECRET OUTSIDE THE LIBRARY\n", encoding="utf-8")
    settings = Settings(persona_db_path=tmp_path / "brain.db", prompts_dir=lib)
    return TestClient(create_app(settings=settings)), lib


# ----- workflow nodes: file-based (write the .md in place, never a DB version) -----


def test_get_workflow_node_reads_the_disk_file(tmp_path):
    client, _ = _lib_client(tmp_path)
    got = client.get("/prompts/template", params={"key": "workflow/50-draft.md"})
    assert got.status_code == 200
    body = got.json()
    assert body["body"] == "DISK DRAFT BODY\n"
    assert body["version"] == 0  # file-based, never a stored version
    assert body["created_by"] == "disk"
    assert body["is_active"] is True


def test_publish_workflow_node_writes_the_file_in_place(tmp_path):
    client, lib = _lib_client(tmp_path)
    resp = client.post(
        "/prompts/template",
        json={"key": "workflow/50-draft.md", "body": "EDITED DRAFT", "created_by": "hector"},
    )
    assert resp.status_code == 201
    out = resp.json()
    assert out["version"] == 0 and out["created_by"] == "disk"  # a file write, not a version
    # The edit hit the actual file on disk...
    assert (lib / "workflow" / "50-draft.md").read_text(encoding="utf-8") == "EDITED DRAFT"
    # ...and the read serves it back.
    assert client.get("/prompts/template", params={"key": "workflow/50-draft.md"}).json()["body"] == "EDITED DRAFT"
    # A second publish overwrites in place; still no versions accrue.
    client.post("/prompts/template", json={"key": "workflow/50-draft.md", "body": "AGAIN"})
    assert (lib / "workflow" / "50-draft.md").read_text(encoding="utf-8") == "AGAIN"


def test_publish_can_edit_the_manifest_json_node(tmp_path):
    # The step order (manifest.json) is a workflow node too: editing layout writes the file.
    client, lib = _lib_client(tmp_path)
    resp = client.post(
        "/prompts/template",
        json={"key": "workflow/manifest.json", "body": '{"modes": {"daily": ["10-batch-plan"]}}'},
    )
    assert resp.status_code == 201
    assert "daily" in (lib / "workflow" / "manifest.json").read_text(encoding="utf-8")


def test_publish_unknown_workflow_node_is_422(tmp_path):
    # Editing is in place; adding a brand-new node is a code change, not an API write.
    client, _ = _lib_client(tmp_path)
    resp = client.post("/prompts/template", json={"key": "workflow/does-not-exist.md", "body": "x"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_prompt"


def test_publish_blank_workflow_body_is_422(tmp_path):
    # A blank body would neuter the node; refuse it (and leave the file untouched).
    client, lib = _lib_client(tmp_path)
    resp = client.post("/prompts/template", json={"key": "workflow/50-draft.md", "body": "   "})
    assert resp.status_code == 422
    assert (lib / "workflow" / "50-draft.md").read_text(encoding="utf-8") == "DISK DRAFT BODY\n"


def test_publish_workflow_refuses_path_traversal(tmp_path):
    # A workflow key that escapes prompts_dir must never write a file outside the library.
    client, tmp_secret = _lib_client(tmp_path)
    resp = client.post("/prompts/template", json={"key": "workflow/../../secret.md", "body": "PWNED"})
    assert resp.status_code == 422
    assert (tmp_path / "secret.md").read_text(encoding="utf-8") == "SECRET OUTSIDE THE LIBRARY\n"


def test_workflow_node_never_enters_the_store(tmp_path):
    # Even after an edit, a workflow key has no store versions: it is file-based, full stop.
    client, _ = _lib_client(tmp_path)
    client.post("/prompts/template", json={"key": "workflow/50-draft.md", "body": "EDITED"})
    versions = client.get("/prompts/versions", params={"key": "workflow/50-draft.md"}).json()
    assert versions["total"] == 0
    # And the listing still marks it v0/disk (a file, not a stored override).
    listing = client.get("/prompts").json()
    row = next(t for t in listing["templates"] if t["key"] == "workflow/50-draft.md")
    assert row["version"] == 0 and row["created_by"] == "disk"


# ----- non-workflow keys: versioned store (author voice, house style) -----


def test_publish_creates_v1_and_it_is_active(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/prompts/template",
        json={"key": "persona/test-voice.md", "body": "voice v1", "created_by": "hector"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["key"] == "persona/test-voice.md"
    assert body["version"] == 1
    assert body["is_active"] is True  # the first version of a key auto-activates
    assert body["created_by"] == "hector"
    assert body["body"] == "voice v1"


def test_second_publish_same_key_creates_v2_and_flips_active(tmp_path):
    client = _client(tmp_path)
    client.post("/prompts/template", json={"key": "persona/test-voice.md", "body": "v1"})
    v2 = client.post("/prompts/template", json={"key": "persona/test-voice.md", "body": "v2"}).json()
    assert v2["version"] == 2 and v2["is_active"] is True
    active = client.get("/prompts/template", params={"key": "persona/test-voice.md"}).json()
    assert active["version"] == 2 and active["body"] == "v2"


def test_versions_lists_newest_first_with_active_flags_and_total(tmp_path):
    client = _client(tmp_path)
    for i in range(3):
        client.post("/prompts/template", json={"key": "persona/test-voice.md", "body": f"v{i}"})
    versions = client.get("/prompts/versions", params={"key": "persona/test-voice.md"}).json()
    assert versions["total"] == 3
    assert [v["version"] for v in versions["versions"]] == [3, 2, 1]  # newest first
    assert [v["is_active"] for v in versions["versions"]] == [True, False, False]
    page = client.get(
        "/prompts/versions", params={"key": "persona/test-voice.md", "limit": 1, "offset": 1}
    ).json()
    assert page["total"] == 3  # the count before the window, not the page size
    assert [v["version"] for v in page["versions"]] == [2]


def test_promote_reverts_to_an_older_version(tmp_path):
    client = _client(tmp_path)
    client.post("/prompts/template", json={"key": "persona/test-voice.md", "body": "v1 body"})
    client.post("/prompts/template", json={"key": "persona/test-voice.md", "body": "v2 body"})
    promoted = client.post("/prompts/versions/1/promote")
    assert promoted.status_code == 200
    assert promoted.json()["version"] == 1 and promoted.json()["is_active"] is True
    active = client.get("/prompts/template", params={"key": "persona/test-voice.md"}).json()
    assert active["version"] == 1 and active["body"] == "v1 body"


def test_publish_staged_without_activating(tmp_path):
    # activate=False stages a version without moving the key's pointer.
    client = _client(tmp_path)
    client.post("/prompts/template", json={"key": "persona/test-voice.md", "body": "live"})
    staged = client.post(
        "/prompts/template", json={"key": "persona/test-voice.md", "body": "staged", "activate": False}
    ).json()
    assert staged["version"] == 2 and staged["is_active"] is False
    active = client.get("/prompts/template", params={"key": "persona/test-voice.md"}).json()
    assert active["body"] == "live"  # unchanged


def test_store_version_overrides_the_on_disk_fallback(tmp_path):
    # A persona prompt ships on disk AND can be overridden in the store: the store wins.
    client, _ = _lib_client(tmp_path)
    disk = client.get("/prompts/template", params={"key": "persona/synthesize.md"}).json()
    assert disk["version"] == 0 and disk["created_by"] == "disk"  # ships on disk first
    client.post("/prompts/template", json={"key": "persona/synthesize.md", "body": "OPERATOR EDIT"})
    active = client.get("/prompts/template", params={"key": "persona/synthesize.md"}).json()
    assert active["body"] == "OPERATOR EDIT"
    assert active["version"] >= 1
    assert active["created_by"] != "disk"


def test_list_prompts_one_row_per_key(tmp_path):
    # Two stored keys, the first with two versions: the listing shows ONE row per key (its
    # active), no duplicate disk row.
    client = _client(tmp_path)
    client.post("/prompts/template", json={"key": "persona/test-a.md", "body": "a1"})
    client.post("/prompts/template", json={"key": "persona/test-a.md", "body": "a2"})
    client.post("/prompts/template", json={"key": "persona/test-b.md", "body": "b1"})
    listing = client.get("/prompts").json()
    keys = [t["key"] for t in listing["templates"]]
    assert keys.count("persona/test-a.md") == 1
    assert keys.count("persona/test-b.md") == 1
    by_key = {t["key"]: t for t in listing["templates"]}
    assert by_key["persona/test-a.md"]["version"] == 2  # the active version of the key
    assert by_key["persona/test-a.md"]["created_by"] != "disk"  # stored, not a disk row
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
    client.post("/prompts/template", json={"key": "persona/test-voice.md", "body": "v1"})
    resp = client.post("/prompts/versions/99/promote")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_publish_empty_body_is_422(tmp_path):
    # A blank stored body would silently neuter the stage that loads it.
    client = _client(tmp_path)
    resp = client.post("/prompts/template", json={"key": "persona/test-voice.md", "body": "   "})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_prompt"


# ----- store-level safety branch (not reachable from the route, which derives key from version) -----


def test_promote_rejects_a_version_from_a_different_key():
    conn = open_db(":memory:")
    store = PromptStore(conn)
    a = store.add_version("persona/test-a.md", "a-body")
    store.add_version("persona/test-b.md", "b-body")
    with pytest.raises(KeyError):
        store.promote("persona/test-b.md", a.version)


def test_openapi_types_the_prompt_responses(tmp_path):
    client = _client(tmp_path)
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in ("PromptOut", "PromptListOut", "PromptVersionsOut"):
        assert name in schemas


# ----- seed_prompts: lift the persona/style files, leave the file-based workflow out -----


def test_seed_prompts_lifts_persona_but_not_workflow():
    conn = open_db(":memory:")
    created, skipped = seed_prompts(conn)
    assert skipped == []
    # Persona/style prompts are stored; workflow nodes are file-based and never seeded.
    assert "persona/synthesize.md" in created
    assert not any(k.startswith("workflow/") for k in created)
    store = PromptStore(conn)
    keys = store.list_keys()
    assert set(keys) == set(created)
    assert not any(k.startswith("workflow/") for k in keys)
    assert store.active("persona/synthesize.md") is not None
    assert len(store.list_versions("persona/synthesize.md")) == 1
    # A second call is a no-op: everything is now skipped, nothing re-created.
    again_created, again_skipped = seed_prompts(conn)
    assert again_created == []
    assert set(again_skipped) == set(created)
    assert len(store.list_versions("persona/synthesize.md")) == 1


def test_seed_prompts_body_matches_the_real_file():
    conn = open_db(":memory:")
    seed_prompts(conn)
    body = PromptStore(conn).active("persona/synthesize.md").body
    on_disk = (load_settings().prompts_dir / "persona" / "synthesize.md").read_text(encoding="utf-8")
    assert body == on_disk


# ----- on-disk read guards (the shipped prompts serve before any seeder runs) -----


def test_unknown_key_is_404_even_with_the_disk_fallback(tmp_path):
    client, _ = _lib_client(tmp_path)
    resp = client.get("/prompts/template", params={"key": "workflow/does-not-exist.md"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "prompt_not_found"


def test_disk_read_refuses_path_traversal(tmp_path):
    # A key escaping prompts_dir must never read a file outside the library.
    client, _ = _lib_client(tmp_path)
    resp = client.get("/prompts/template", params={"key": "../secret.md"})
    assert resp.status_code == 404
    assert "SECRET" not in resp.text


# ----- GET /prompts disk-union (a fresh box lists every shipped prompt) -----


def test_list_prompts_unions_the_shipped_disk_keys_on_a_fresh_box(tmp_path):
    # The store is empty (no seeder), but the shipped prompts/ tree ships the workflow
    # step-gate nodes plus persona/synthesize: the listing is the union, each marked v0/disk.
    client = _client(tmp_path)
    listing = client.get("/prompts").json()
    assert listing["total"] >= 11
    by_key = {t["key"]: t for t in listing["templates"]}
    assert "workflow/95-image.md" in by_key
    assert "persona/synthesize.md" in by_key
    assert by_key["workflow/95-image.md"]["version"] == 0
    assert by_key["workflow/95-image.md"]["created_by"] == "disk"
    assert by_key["persona/synthesize.md"]["version"] == 0
    assert by_key["persona/synthesize.md"]["created_by"] == "disk"
    keys = [t["key"] for t in listing["templates"]]
    assert keys == sorted(keys)  # sorted for stable output


def test_list_prompts_persona_override_shows_once_as_a_stored_version(tmp_path):
    # A stored persona override appears EXACTLY once with version > 0, while the file-based
    # workflow nodes stay v0/disk.
    client, _ = _lib_client(tmp_path)
    client.post(
        "/prompts/template",
        json={"key": "persona/synthesize.md", "body": "OPERATOR VOICE", "created_by": "hector"},
    )
    listing = client.get("/prompts").json()
    rows = [t for t in listing["templates"] if t["key"] == "persona/synthesize.md"]
    assert len(rows) == 1
    row = rows[0]
    assert row["version"] > 0
    assert row["created_by"] != "disk"
    assert row["body"] == "OPERATOR VOICE"
    # The workflow node in the same lib is still file-based.
    wf = next(t for t in listing["templates"] if t["key"] == "workflow/50-draft.md")
    assert wf["version"] == 0 and wf["created_by"] == "disk"


def test_list_prompts_ignores_a_stray_non_md_json_file(tmp_path):
    # A controlled library: shipped .md/.json plus a stray file. Only .md/.json are keys.
    lib = tmp_path / "prompts"
    (lib / "workflow").mkdir(parents=True)
    (lib / "workflow" / "50-draft.md").write_text("DISK DRAFT", encoding="utf-8")
    (lib / "workflow" / "notes.txt").write_text("NOT A PROMPT", encoding="utf-8")
    (lib / "README").write_text("NOT A PROMPT EITHER", encoding="utf-8")
    settings = Settings(persona_db_path=tmp_path / "brain.db", prompts_dir=lib)
    client = TestClient(create_app(settings=settings))
    listing = client.get("/prompts").json()
    keys = {t["key"] for t in listing["templates"]}
    assert keys == {"workflow/50-draft.md"}
    assert listing["total"] == 1
