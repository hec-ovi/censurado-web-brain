"""The step-gate workflow library: the per-node prompts under ``workflow/`` and the
``workflow/manifest.json`` order config are served from the shipped prompt library so the
CLI ``step`` verb can walk them one at a time.

These start the real brain over a FRESH db with the shipped prompts dir and prove: the
manifest JSON is served (the ``.json`` shipped-prompt path), every node the manifest names
exists on disk, the manifest is listed alongside the node bodies, and the once-orphaned
``80-enrich`` copy-edit node is now wired into the article loop.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from newsroom.brain import create_app
from newsroom.config import Settings


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(settings=Settings(persona_db_path=tmp_path / "brain.db")))


def test_manifest_json_is_served_as_a_prompt(tmp_path):
    # The shipped-prompt read now allows .json, so the manifest rides in the same library
    # as the node bodies and is fetchable by the step verb.
    client = _client(tmp_path)
    resp = client.get("/prompts/template", params={"key": "workflow/manifest.json"})
    assert resp.status_code == 200
    manifest = json.loads(resp.json()["body"])
    assert "modes" in manifest and "single-article" in manifest["modes"]
    assert set(resp.json()) == {"key", "body"}  # served from disk, no DB version fields


def test_every_manifest_node_exists_on_disk(tmp_path):
    client = _client(tmp_path)
    manifest = json.loads(
        client.get("/prompts/template", params={"key": "workflow/manifest.json"}).json()["body"]
    )
    referenced = {key for seq in manifest["modes"].values() for key in seq}
    assert referenced, "the manifest references no nodes"
    for key in sorted(referenced):
        resp = client.get("/prompts/template", params={"key": f"workflow/{key}.md"})
        assert resp.status_code == 200, f"manifest names a missing node: workflow/{key}.md"
        assert resp.json()["body"].strip(), f"node workflow/{key}.md is empty"


def test_orphan_enrich_is_wired_into_the_article_loop(tmp_path):
    client = _client(tmp_path)
    manifest = json.loads(
        client.get("/prompts/template", params={"key": "workflow/manifest.json"}).json()["body"]
    )
    # The former orphan (journalist/enrich.md) is now the compression/proofread node and
    # sits in the per-article sequence, between factcheck and finalize.
    seq = manifest["modes"]["single-article"]
    assert "80-enrich" in seq
    assert seq.index("75-factcheck") < seq.index("80-enrich") < seq.index("90-finalize")


def test_get_prompts_lists_the_workflow_nodes_and_manifest(tmp_path):
    client = _client(tmp_path)
    keys = {t["key"] for t in client.get("/prompts").json()["templates"]}
    assert "workflow/manifest.json" in keys
    assert "workflow/00-mode.md" in keys
    assert "workflow/99-publish.md" in keys


def test_batch_mode_stops_after_the_plan(tmp_path):
    # A scheduled batch walks only the plan node and stops; the per-article walks run
    # separately (queue + isolated walks), so daily must NOT inline the drafting nodes.
    client = _client(tmp_path)
    manifest = json.loads(
        client.get("/prompts/template", params={"key": "workflow/manifest.json"}).json()["body"]
    )
    assert manifest["modes"]["daily"] == ["10-batch-plan"]
    assert "50-draft" not in manifest["modes"]["daily"]


def test_normalize_topics_is_a_single_node_maintenance_mode(tmp_path):
    # Curating author profile topics is its own one-shot mode (like deploy), NOT a step in
    # the article-drafting loop: it walks a single node and stops.
    client = _client(tmp_path)
    manifest = json.loads(
        client.get("/prompts/template", params={"key": "workflow/manifest.json"}).json()["body"]
    )
    assert manifest["modes"]["normalize-topics"] == ["normalize-topics"]
    # It must not have leaked into an article mode's sequence.
    assert "normalize-topics" not in manifest["modes"]["single-article"]
    node = client.get("/prompts/template", params={"key": "workflow/normalize-topics.md"})
    assert node.status_code == 200 and "profile-topics" in node.json()["body"]


def test_portal_review_is_a_single_node_maintenance_mode(tmp_path):
    # Curating the per-day front page is its own one-shot mode (like normalize-topics and
    # deploy), NOT a step in the article-drafting loop: it walks a single node and stops.
    client = _client(tmp_path)
    manifest = json.loads(
        client.get("/prompts/template", params={"key": "workflow/manifest.json"}).json()["body"]
    )
    assert manifest["modes"]["portal-review"] == ["portal-review"]
    # It must not have leaked into the article-drafting sequence.
    assert "portal-review" not in manifest["modes"]["single-article"]
    node = client.get("/prompts/template", params={"key": "workflow/portal-review.md"})
    assert node.status_code == 200 and "portada" in node.json()["body"]


def test_deploy_node_is_wired_last_but_disabled_by_default(tmp_path):
    # Deploy (production push) is plugged in as the last step of the article modes, but
    # disabled per-mode by default (safe: an article walk ends at publish), and it has its
    # own one-shot `deploy` mode for deploying once after a batch.
    client = _client(tmp_path)
    assert client.get("/prompts/template", params={"key": "workflow/deploy.md"}).status_code == 200
    manifest = json.loads(
        client.get("/prompts/template", params={"key": "workflow/manifest.json"}).json()["body"]
    )
    assert manifest["modes"]["single-article"][-1] == "deploy"     # wired as the last step
    assert manifest["disabled"]["single-article"] == ["deploy"]    # but off by default
    assert manifest["modes"]["deploy"] == ["deploy"]               # the deploy-once mode
