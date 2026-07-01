"""The source (portal) management API (infra chunk 1), driven end to end.

This exercises the REAL entry point: ``create_app`` mounted under a FastAPI
``TestClient``, hit over HTTP. It walks the full lifecycle of a source
(create -> list -> get -> patch -> enable/disable -> delete -> 404) and the error
edges (duplicate domain, missing id, invalid feed_type, the query-param filters),
asserting the new ``description`` field round-trips through every read.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from newsroom.brain import create_app
from newsroom.config import Settings


def _client(tmp_path) -> TestClient:
    settings = Settings(
        persona_db_path=tmp_path / "brain.db",
    )
    return TestClient(create_app(settings=settings))


def test_source_lifecycle_create_list_get_patch_toggle_delete(tmp_path):
    client = _client(tmp_path)

    # Create: a URL normalizes to a bare domain and derives the id; description rides along.
    created = client.post(
        "/portals",
        json={
            "domain": "https://www.example.com/",
            "description": "Diario nacional",
            "feed_urls": ["https://example.com/rss"],
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["id"] == "example-com"
    assert body["domain"] == "example.com"
    assert body["description"] == "Diario nacional"  # the new field round-trips on create
    assert body["feed_urls"] == ["https://example.com/rss"]
    assert body["enabled"] is True

    # List: sees it, total is right, and the description survives the list read.
    listed = client.get("/portals")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] == 1
    assert payload["portals"][0]["id"] == "example-com"
    assert payload["portals"][0]["description"] == "Diario nacional"

    # Get one.
    got = client.get("/portals/example-com")
    assert got.status_code == 200
    assert got.json()["description"] == "Diario nacional"

    # Patch: description + ownership_group persist; unnamed fields are untouched.
    patched = client.patch(
        "/portals/example-com",
        json={"description": "Grupo Example flagship", "ownership_group": "group-a"},
    )
    assert patched.status_code == 200
    pj = patched.json()
    assert pj["description"] == "Grupo Example flagship"
    assert pj["ownership_group"] == "group-a"
    assert pj["feed_urls"] == ["https://example.com/rss"]  # unchanged

    # Disable then enable flips the flag.
    assert client.post("/portals/example-com/disable").json()["enabled"] is False
    assert client.post("/portals/example-com/enable").json()["enabled"] is True

    # Delete: 204, then a get is 404.
    deleted = client.delete("/portals/example-com")
    assert deleted.status_code == 204
    assert client.get("/portals/example-com").status_code == 404


def test_duplicate_domain_is_409(tmp_path):
    client = _client(tmp_path)
    assert client.post("/portals", json={"domain": "example.com"}).status_code == 201
    dup = client.post("/portals", json={"domain": "https://www.example.com/otra"})
    assert dup.status_code == 409
    assert dup.json()["code"] == "duplicate_domain"


def test_get_missing_is_404(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/portals/ghost")
    assert resp.status_code == 404
    assert resp.json()["code"] == "portal_not_found"


def test_patch_missing_is_404(tmp_path):
    client = _client(tmp_path)
    resp = client.patch("/portals/ghost", json={"description": "x"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "portal_not_found"


def test_create_missing_domain_is_422(tmp_path):
    client = _client(tmp_path)
    # No domain at all -> Pydantic validation rejects the body before the handler.
    assert client.post("/portals", json={"description": "no domain"}).status_code == 422


def test_invalid_feed_type_on_create_is_422(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/portals", json={"domain": "example.com", "feed_type": "telepathy"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_portal"


def test_invalid_feed_type_on_patch_is_422(tmp_path):
    client = _client(tmp_path)
    client.post("/portals", json={"domain": "example.com"})
    resp = client.patch("/portals/example-com", json={"feed_type": "telepathy"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_portal"


def test_openapi_types_the_portal_responses(tmp_path):
    # The refactor declares response_model on the portal routes, so the generated OpenAPI
    # advertises PortalOut / PortalListOut (a typed client + docs surface), not a bare 200.
    client = _client(tmp_path)
    spec = client.get("/openapi.json").json()
    assert "PortalOut" in spec["components"]["schemas"]
    assert "PortalListOut" in spec["components"]["schemas"]

    def _ref(path: str, method: str) -> str:
        content = spec["paths"][path][method]["responses"]["200"]["content"]
        return content["application/json"]["schema"]["$ref"]

    assert _ref("/portals", "get").endswith("/PortalListOut")
    assert _ref("/portals/{portal_id}", "get").endswith("/PortalOut")
    # POST advertises 201 + PortalOut.
    created = spec["paths"]["/portals"]["post"]["responses"]["201"]["content"]
    assert created["application/json"]["schema"]["$ref"].endswith("/PortalOut")


def test_enable_disable_on_missing_id_is_404_problem_json(tmp_path):
    # The toggle routes 404 on an unknown id, in the shared problem+json envelope, just
    # like the read/patch paths -- so the operator panel gets one error shape to branch on.
    client = _client(tmp_path)
    for verb in ("enable", "disable"):
        resp = client.post(f"/portals/ghost/{verb}")
        assert resp.status_code == 404
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["code"] == "portal_not_found"


def test_patch_empty_body_returns_current_state_without_bumping_updated_at(tmp_path):
    # An empty PATCH is a no-op read: it returns the current portal and must NOT touch
    # updated_at (no write happened), so a panel "save" with no edits is idempotent.
    client = _client(tmp_path)
    created = client.post("/portals", json={"domain": "example.com"}).json()
    before = created["updated_at"]

    patched = client.patch("/portals/example-com", json={})
    assert patched.status_code == 200
    pj = patched.json()
    assert pj["id"] == "example-com"
    assert pj["updated_at"] == before  # untouched: no write, no timestamp bump


def test_error_responses_are_problem_json_with_a_code(tmp_path):
    # Both a hand-mapped error (404) and a body-validation error (missing required
    # `domain`) must now carry the application/problem+json content-type AND a machine
    # `code`. The missing-domain case rides the app-wide validation handler, so it is the
    # shared envelope (code == "validation_failed"), not FastAPI's default {"detail": [...]}.
    client = _client(tmp_path)

    not_found = client.get("/portals/ghost")
    assert not_found.status_code == 404
    assert not_found.headers["content-type"] == "application/problem+json"
    assert not_found.json()["code"] == "portal_not_found"

    missing_domain = client.post("/portals", json={"description": "no domain"})
    assert missing_domain.status_code == 422
    assert missing_domain.headers["content-type"] == "application/problem+json"
    assert missing_domain.json()["code"] == "validation_failed"


def test_pagination_past_the_end_is_an_empty_window_with_full_total(tmp_path):
    # An offset/limit window past the end returns no portals but still reports the full
    # `total`, so a client paging off the end gets an empty page, not a wrong count.
    client = _client(tmp_path)
    client.post("/portals", json={"domain": "example.com"})
    client.post("/portals", json={"domain": "news.com"})

    page = client.get("/portals", params={"limit": 10, "offset": 5}).json()
    assert page["portals"] == []
    assert page["total"] == 2


def test_enabled_filter_and_pagination(tmp_path):
    client = _client(tmp_path)
    client.post("/portals", json={"domain": "example.com"})
    client.post("/portals", json={"domain": "news.com", "enabled": False})
    client.post("/portals", json={"domain": "blog.example"})

    # enabled=true filter excludes the disabled one; total reflects the filtered set.
    only_enabled = client.get("/portals", params={"enabled": True}).json()
    assert only_enabled["total"] == 2
    assert [p["id"] for p in only_enabled["portals"]] == ["example-com", "blog-example"]

    only_disabled = client.get("/portals", params={"enabled": False}).json()
    assert [p["id"] for p in only_disabled["portals"]] == ["news-com"]

    # Pagination: total stays the full count; the window honors limit/offset.
    page = client.get("/portals", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3
    assert len(page["portals"]) == 1
    assert page["portals"][0]["id"] == "news-com"  # oldest-first order
