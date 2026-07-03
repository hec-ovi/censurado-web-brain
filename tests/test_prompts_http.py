"""The prompt-library management API, driven end to end.

This exercises the REAL entry point: ``create_app`` mounted under a FastAPI ``TestClient``,
hit over HTTP. Every prompt is a plain FILE in the shipped library, whatever its key prefix:
the agentic-workflow step-gate nodes (``workflow/*``, plus the ``workflow/manifest.json``
node-order config) AND the author-voice / house-style prompts (``persona/*`` etc.). A read
serves the on-disk file; a publish WRITES the file in place. Git is the version history: there
is NO database copy of any prompt, so there are no versions, no active pointer, no promote.

Because a publish writes the repo tree, every write test points ``prompts_dir`` at a tmp
library so the edit lands there, never in the real repo. The error edges are covered too:
an unknown key (404 on read, 422 on publish), a blank key/body (422), and path traversal
refused on BOTH the read and the write side.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from newsroom.brain import create_app
from newsroom.config import Settings


def _client(tmp_path) -> TestClient:
    """A client on the real shipped prompts_dir. Safe for READS and the LIST; never POST here
    (a publish writes the file in place, and these keys are the real repo tree)."""
    return TestClient(create_app(settings=Settings(persona_db_path=tmp_path / "brain.db")))


def _lib_client(tmp_path):
    """A client whose ``prompts_dir`` is a CONTROLLED tmp library: one workflow node, the
    ``manifest.json`` node-order config, and one persona prompt on disk, plus a secret file
    OUTSIDE it to prove the traversal guard. Returns (client, lib_path) so a test can read back
    a file the API wrote."""
    lib = tmp_path / "prompts"
    (lib / "workflow").mkdir(parents=True)
    (lib / "workflow" / "50-draft.md").write_text("DISK DRAFT BODY\n", encoding="utf-8")
    (lib / "workflow" / "manifest.json").write_text('{"modes": {}}\n', encoding="utf-8")
    (lib / "persona").mkdir(parents=True)
    (lib / "persona" / "synthesize.md").write_text("PERSONA DISK BODY\n", encoding="utf-8")
    (tmp_path / "secret.md").write_text("SECRET OUTSIDE THE LIBRARY\n", encoding="utf-8")
    settings = Settings(persona_db_path=tmp_path / "brain.db", prompts_dir=lib)
    return TestClient(create_app(settings=settings)), lib


# ----- reads: serve the on-disk file (any key, whatever its prefix) -----


def test_get_workflow_node_reads_the_disk_file(tmp_path):
    client, _ = _lib_client(tmp_path)
    got = client.get("/prompts/template", params={"key": "workflow/50-draft.md"})
    assert got.status_code == 200
    body = got.json()
    assert body == {"key": "workflow/50-draft.md", "body": "DISK DRAFT BODY\n"}


def test_the_db_versioning_routes_are_gone(tmp_path):
    # The prompt DB store was removed: prompts are files, git is the history. The old
    # versions/promote endpoints must not exist, so a future dual-write resurrection of a DB
    # version store behind these paths fails the suite regardless of its response-model name.
    client, _ = _lib_client(tmp_path)
    assert client.get("/prompts/versions", params={"key": "workflow/50-draft.md"}).status_code == 404
    assert client.post("/prompts/versions/1/promote").status_code in (404, 405)


def test_get_persona_prompt_reads_the_disk_file(tmp_path):
    client, _ = _lib_client(tmp_path)
    got = client.get("/prompts/template", params={"key": "persona/synthesize.md"})
    assert got.status_code == 200
    assert got.json() == {"key": "persona/synthesize.md", "body": "PERSONA DISK BODY\n"}


# ----- publish: write the file in place (any key) -----


def test_publish_workflow_node_writes_the_file_in_place(tmp_path):
    client, lib = _lib_client(tmp_path)
    resp = client.post(
        "/prompts/template", json={"key": "workflow/50-draft.md", "body": "EDITED DRAFT"}
    )
    assert resp.status_code == 201
    assert resp.json() == {"key": "workflow/50-draft.md", "body": "EDITED DRAFT"}
    # The edit hit the actual file on disk...
    assert (lib / "workflow" / "50-draft.md").read_text(encoding="utf-8") == "EDITED DRAFT"
    # ...and the read serves it back.
    read = client.get("/prompts/template", params={"key": "workflow/50-draft.md"}).json()
    assert read["body"] == "EDITED DRAFT"
    # A second publish overwrites in place.
    client.post("/prompts/template", json={"key": "workflow/50-draft.md", "body": "AGAIN"})
    assert (lib / "workflow" / "50-draft.md").read_text(encoding="utf-8") == "AGAIN"


def test_publish_persona_prompt_writes_the_file_in_place(tmp_path):
    # A persona / house-style prompt is a file too: a publish edits it in place, no store.
    client, lib = _lib_client(tmp_path)
    resp = client.post(
        "/prompts/template", json={"key": "persona/synthesize.md", "body": "OPERATOR VOICE"}
    )
    assert resp.status_code == 201
    assert resp.json() == {"key": "persona/synthesize.md", "body": "OPERATOR VOICE"}
    assert (lib / "persona" / "synthesize.md").read_text(encoding="utf-8") == "OPERATOR VOICE"
    active = client.get("/prompts/template", params={"key": "persona/synthesize.md"}).json()
    assert active["body"] == "OPERATOR VOICE"


def test_publish_can_edit_the_manifest_json_node(tmp_path):
    # The step order (manifest.json) is a prompt file too: editing layout writes the file.
    client, lib = _lib_client(tmp_path)
    resp = client.post(
        "/prompts/template",
        json={"key": "workflow/manifest.json", "body": '{"modes": {"daily": ["10-batch-plan"]}}'},
    )
    assert resp.status_code == 201
    assert "daily" in (lib / "workflow" / "manifest.json").read_text(encoding="utf-8")


# ----- error edges -----


def test_publish_unknown_key_is_422(tmp_path):
    # Editing is in place; adding a brand-new prompt is a code change, not an API write.
    client, _ = _lib_client(tmp_path)
    resp = client.post("/prompts/template", json={"key": "workflow/does-not-exist.md", "body": "x"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_prompt"


def test_publish_blank_body_is_422(tmp_path):
    # A blank body would neuter the prompt; refuse it (and leave the file untouched).
    client, lib = _lib_client(tmp_path)
    resp = client.post("/prompts/template", json={"key": "workflow/50-draft.md", "body": "   "})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_prompt"
    assert (lib / "workflow" / "50-draft.md").read_text(encoding="utf-8") == "DISK DRAFT BODY\n"


def test_publish_empty_key_is_422(tmp_path):
    client, _ = _lib_client(tmp_path)
    resp = client.post("/prompts/template", json={"key": "   ", "body": "x"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_prompt"


def test_get_unknown_key_is_404(tmp_path):
    client, _ = _lib_client(tmp_path)
    resp = client.get("/prompts/template", params={"key": "workflow/does-not-exist.md"})
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "prompt_not_found"


# ----- path traversal refused on BOTH sides -----


def test_publish_refuses_path_traversal(tmp_path):
    # A key that escapes prompts_dir must never write a file outside the library.
    client, _ = _lib_client(tmp_path)
    resp = client.post("/prompts/template", json={"key": "workflow/../../secret.md", "body": "PWNED"})
    assert resp.status_code == 422
    assert (tmp_path / "secret.md").read_text(encoding="utf-8") == "SECRET OUTSIDE THE LIBRARY\n"


def test_read_refuses_path_traversal(tmp_path):
    # A key escaping prompts_dir must never read a file outside the library.
    client, _ = _lib_client(tmp_path)
    resp = client.get("/prompts/template", params={"key": "../secret.md"})
    assert resp.status_code == 404
    assert "SECRET" not in resp.text


# ----- GET /prompts: the on-disk library listing -----


def test_list_prompts_unions_the_shipped_disk_keys_on_a_fresh_box(tmp_path):
    # The shipped prompts/ tree ships the workflow step-gate nodes plus persona/synthesize:
    # the listing is one row per file, each just {key, body}, sorted for stable output.
    client = _client(tmp_path)
    listing = client.get("/prompts").json()
    assert listing["total"] >= 11
    by_key = {t["key"]: t for t in listing["templates"]}
    assert "workflow/95-image.md" in by_key
    assert "persona/synthesize.md" in by_key
    assert set(by_key["workflow/95-image.md"]) == {"key", "body"}
    keys = [t["key"] for t in listing["templates"]]
    assert keys == sorted(keys)  # sorted for stable output


def test_list_prompts_reflects_an_edit_in_place(tmp_path):
    # After a publish, the listing serves the edited body (a file, not a stored version).
    client, _ = _lib_client(tmp_path)
    client.post("/prompts/template", json={"key": "persona/synthesize.md", "body": "OPERATOR VOICE"})
    listing = client.get("/prompts").json()
    rows = [t for t in listing["templates"] if t["key"] == "persona/synthesize.md"]
    assert len(rows) == 1
    assert rows[0]["body"] == "OPERATOR VOICE"
    # The workflow node in the same lib still lists its file body.
    wf = next(t for t in listing["templates"] if t["key"] == "workflow/50-draft.md")
    assert wf["body"] == "DISK DRAFT BODY\n"


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


def test_openapi_types_the_prompt_responses(tmp_path):
    client = _client(tmp_path)
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in ("PromptOut", "PromptListOut"):
        assert name in schemas
    # The versioned-store response model is gone with the DB copy.
    assert "PromptVersionsOut" not in schemas
