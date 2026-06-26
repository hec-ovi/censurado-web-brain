"""The agnostic API surface (infra chunk 7), driven end to end through the real app.

This exercises the cross-cutting surface ``create_app`` must expose for a browser admin
UI and a generated client SDK, all over a FastAPI ``TestClient``:

  * CORS -- a browser client served from another origin clears the preflight and reads
    the allow-origin header; the allow-list is env-configurable.
  * The future-auth seam -- ONE app-wide dependency that, when enabled, guards every
    inline route AND every router at once (a one-place change).
  * OpenAPI metadata + tags -- title/version/description are set and every operation is
    grouped under a resource tag (none left under the default group).
  * Typed responses on the inline lifecycle routes -- the core flows carry a response
    model in the schema, like the infra routers.
  * The two management routes that were CLI-only -- ``POST /mirror/authors`` (the
    backfill push) and ``POST /bootstrap`` (seed a fresh box) -- now drivable over HTTP.
"""

from __future__ import annotations

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from newsroom.brain import create_app
from newsroom.config import Settings

_ADA = {
    "display_name": "Ada Lovelace",
    "beat": "tech",
    "who_i_am": "I cover chips.",
    "style": "dry",
}


def _settings(tmp_path, **over) -> Settings:
    base = dict(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url="http://127.0.0.1:1/v1",
    )
    base.update(over)
    return Settings(**base)


def _client(tmp_path, **over) -> TestClient:
    return TestClient(create_app(settings=_settings(tmp_path, **over)))


# ----- CORS -----


def test_cors_allows_a_configured_origin_on_a_simple_request(tmp_path):
    # A browser GET carrying an allowed Origin gets the matching access-control-allow-origin
    # header back, so the response is readable cross-origin (the default list covers the
    # local Vite/Next dev origins).
    client = _client(tmp_path)
    resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_cors_preflight_is_answered_for_a_configured_origin(tmp_path):
    # The OPTIONS preflight a browser sends before a POST is answered with the CORS grant,
    # so the real POST is allowed to proceed.
    client = _client(tmp_path)
    resp = client.options(
        "/personas/direct",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "POST" in resp.headers["access-control-allow-methods"]


def test_cors_origins_are_env_configurable(tmp_path):
    # The allow-list is driven by NEWSROOM_CORS_ORIGINS (a comma-separated string here):
    # the named origin is granted, an unlisted one gets no allow-origin header.
    client = _client(tmp_path, cors_origins="http://admin.test")
    granted = client.get("/health", headers={"Origin": "http://admin.test"})
    assert granted.headers["access-control-allow-origin"] == "http://admin.test"

    denied = client.get("/health", headers={"Origin": "http://evil.test"})
    assert "access-control-allow-origin" not in denied.headers


# ----- the future-auth seam (one place, whole surface) -----


def test_default_auth_seam_is_a_noop(tmp_path):
    # Out of the box the seam allows everything (the brain has no auth today).
    client = _client(tmp_path)
    assert client.get("/health").status_code == 200
    assert client.get("/portals").status_code == 200


def test_auth_seam_guards_inline_and_router_routes_in_one_place(tmp_path):
    # Enabling auth is a ONE-place change: passing an auth dependency to create_app guards
    # every route at once -- both an inline lifecycle route (/health, GET /personas) AND
    # every included router (/portals, /status/backend, /editorial/location) -- without
    # touching a single handler.
    async def deny() -> None:
        raise HTTPException(status_code=401, detail="auth required")

    client = TestClient(create_app(settings=_settings(tmp_path), auth_dependency=deny))
    for path in (
        "/health",
        "/personas",
        "/portals",
        "/status/backend",
        "/editorial/location",
    ):
        assert client.get(path).status_code == 401, path


# ----- OpenAPI metadata + tags -----


def test_openapi_carries_title_version_and_description(tmp_path):
    spec = _client(tmp_path).get("/openapi.json").json()
    info = spec["info"]
    assert info["title"] == "censurado-web-brain"
    assert info["version"]  # set from the package version, not the empty default
    assert "newsroom" in info["description"].lower()


def test_every_operation_is_tagged_by_resource(tmp_path):
    spec = _client(tmp_path).get("/openapi.json").json()

    def tags(path: str, method: str) -> list[str]:
        return spec["paths"][path][method].get("tags", [])

    # Inline routes are tagged (system / personas / runs)...
    assert "system" in tags("/health", "get")
    assert "personas" in tags("/personas", "get")
    assert "runs" in tags("/runs/{run_id}", "get")
    # ...and the routers carry their resource tag.
    assert "personas" in tags("/personas/direct", "post")
    assert "portals" in tags("/portals", "get")
    assert "runs" in tags("/runs", "get")
    assert "editorial" in tags("/editorial/style", "get")
    assert "status" in tags("/status/backend", "get")
    assert "management" in tags("/mirror/authors", "post")
    assert "management" in tags("/bootstrap", "post")

    # No operation falls under the implicit 'default' group: every one is grouped.
    methods = {"get", "post", "put", "patch", "delete"}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method in methods:
                assert op.get("tags"), f"{method.upper()} {path} has no OpenAPI tag"


def test_openapi_types_the_inline_lifecycle_routes(tmp_path):
    # The core inline flows now declare a response_model, so a generated client gets typed
    # responses for the main surface (not a bare empty object).
    spec = _client(tmp_path).get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in (
        "HealthOut",
        "JobAcceptedOut",
        "JobStatusOut",
        "PersonaListOut",
        "RunAcceptedOut",
        "RunDetailOut",
        "MirrorAuthorsOut",
        "BootstrapOut",
    ):
        assert name in schemas, name

    def ref(path: str, method: str, code: str) -> str:
        content = spec["paths"][path][method]["responses"][code]["content"]
        return content["application/json"]["schema"]["$ref"]

    assert ref("/health", "get", "200").endswith("/HealthOut")
    assert ref("/personas", "get", "200").endswith("/PersonaListOut")
    assert ref("/personas/{persona_id}", "get", "200").endswith("/PersonaOut")
    assert ref("/personas", "post", "202").endswith("/JobAcceptedOut")
    assert ref("/runs", "post", "202").endswith("/RunAcceptedOut")
    assert ref("/articles/from-link", "post", "202").endswith("/RunAcceptedOut")
    assert ref("/runs/{run_id}", "get", "200").endswith("/RunDetailOut")


# ----- list_personas pagination (the inline collection) -----


def test_list_personas_is_paginated(tmp_path):
    client = _client(tmp_path)
    for i in range(3):
        body = {**_ADA, "display_name": f"Author {i}", "id": f"author-{i}"}
        assert client.post("/personas/direct", json=body).status_code == 201

    full = client.get("/personas").json()
    assert full["total"] == 3 and len(full["personas"]) == 3

    page = client.get("/personas", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3  # the count before the window
    assert len(page["personas"]) == 1

    past = client.get("/personas", params={"limit": 10, "offset": 5}).json()
    assert past["personas"] == [] and past["total"] == 3


# ----- POST /mirror/authors (the backfill push, HTTP parity of `mirror-authors`) -----


def test_mirror_authors_dry_run_lists_handles_without_pushing(tmp_path):
    client = _client(tmp_path)
    assert client.post("/personas/direct", json=_ADA).status_code == 201

    resp = client.post("/mirror/authors", params={"dry_run": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert body["pushed"] == ["ada-lovelace"]
    assert body["failed"] == []


def test_mirror_authors_pushes_public_fields_with_a_token(tmp_path, monkeypatch):
    captured: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.append(json)
        return httpx.Response(200, json={"handle": json["handle"]})

    monkeypatch.setattr("newsroom.mirror.client.httpx.post", fake_post)
    client = TestClient(
        create_app(
            settings=_settings(
                tmp_path, publish_base_url="http://backend.test:8080", operator_token="op-token"
            )
        )
    )
    assert client.post("/personas/direct", json={**_ADA, "about": "A bio."}).status_code == 201

    resp = client.post("/mirror/authors")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pushed"] == ["ada-lovelace"]
    assert body["dry_run"] is False
    # Only the PUBLIC fields crossed the boundary (no prompt).
    assert captured == [{"handle": "ada-lovelace", "name": "Ada Lovelace", "bio": "A bio.", "avatar": ""}]


def test_mirror_authors_without_a_token_is_503(tmp_path):
    client = _client(tmp_path)  # default settings carry no operator token
    assert client.post("/personas/direct", json=_ADA).status_code == 201

    resp = client.post("/mirror/authors")  # a real push needs the credential
    assert resp.status_code == 503
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "not_configured"


# ----- POST /bootstrap (seed a fresh box, HTTP parity of `bootstrap`) -----


def test_bootstrap_seed_only_provisions_a_fresh_box(tmp_path):
    client = _client(tmp_path)

    resp = client.post("/bootstrap", json={"run": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ran"] is False
    assert "lara-arianna" in body["seeded"]["personas_created"]
    # No platform token configured -> the author reconcile is a skipped no-op.
    assert body["reconciled"]["skipped"] is True

    # The seeded personas are now visible over the API on the app's own connection.
    listed = client.get("/personas").json()
    assert listed["total"] >= 4
    assert "lara-arianna" in [p["id"] for p in listed["personas"]]


def test_bootstrap_with_run_and_an_unknown_mode_is_422(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/bootstrap", json={"run": True, "mode": "turbo"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_mode"
