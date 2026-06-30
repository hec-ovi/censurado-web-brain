"""The source <-> author linking API (infra chunk 3), driven end to end.

This exercises the REAL entry point: ``create_app`` mounted under a FastAPI
``TestClient``, hit over HTTP. It seeds a couple of portals and a persona, then walks
the linking surface: GET the (empty) pool, PUT a validated set, GET it back, POST to
link one, DELETE to unlink one (both idempotent), and the error edges (an unknown
source id on PUT is 422 naming it; a missing persona/portal is 404). The KEY property
over C2's blind ``PATCH /personas/{id}`` is validation: a persona can never be linked
to a portal that does not exist in the registry.
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


def _seed(client: TestClient) -> None:
    """Two portals and one persona (no sources yet)."""
    assert client.post("/portals", json={"domain": "example.com"}).status_code == 201
    assert client.post("/portals", json={"domain": "news.com"}).status_code == 201
    persona = {
        "display_name": "Ada Lovelace",
        "beat": "tech",
        "who_i_am": "I cover chips.",
        "style": "dry",
    }
    assert client.post("/personas/direct", json=persona).status_code == 201


def test_link_lifecycle_get_put_post_delete(tmp_path):
    client = _client(tmp_path)
    _seed(client)

    # GET: the pool starts empty.
    got = client.get("/personas/ada-lovelace/sources")
    assert got.status_code == 200
    body = got.json()
    assert body["persona_id"] == "ada-lovelace"
    assert body["sources"] == []
    assert body["portals"] == []

    # PUT a validated set: both ids exist, so the set lands and resolves to the portals.
    put = client.put(
        "/personas/ada-lovelace/sources",
        json={"sources": ["example-com", "news-com"]},
    )
    assert put.status_code == 200
    pj = put.json()
    assert pj["sources"] == ["example-com", "news-com"]
    assert [p["id"] for p in pj["portals"]] == ["example-com", "news-com"]

    # GET reflects the PUT, and the persona's own `sources` field carries the same ids.
    assert client.get("/personas/ada-lovelace/sources").json()["sources"] == [
        "example-com",
        "news-com",
    ]
    assert client.get("/personas/ada-lovelace").json()["sources"] == ["example-com", "news-com"]

    # DELETE unlinks one; the other remains.
    deleted = client.delete("/personas/ada-lovelace/sources/example-com")
    assert deleted.status_code == 200
    assert deleted.json()["sources"] == ["news-com"]

    # POST links it back (idempotent add appends after the survivor).
    linked = client.post("/personas/ada-lovelace/sources/example-com")
    assert linked.status_code == 200
    assert linked.json()["sources"] == ["news-com", "example-com"]


def test_put_with_unknown_id_is_422_naming_it_and_writes_nothing(tmp_path):
    client = _client(tmp_path)
    _seed(client)

    resp = client.put(
        "/personas/ada-lovelace/sources",
        json={"sources": ["example-com", "ghost-com"]},
    )
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    j = resp.json()
    assert j["code"] == "unknown_source"
    assert "ghost-com" in j["detail"]  # the offending id is named

    # The rejected PUT wrote nothing: the pool is still empty.
    assert client.get("/personas/ada-lovelace/sources").json()["sources"] == []


def test_put_empty_list_clears_the_pool(tmp_path):
    client = _client(tmp_path)
    _seed(client)
    client.put("/personas/ada-lovelace/sources", json={"sources": ["example-com"]})

    cleared = client.put("/personas/ada-lovelace/sources", json={"sources": []})
    assert cleared.status_code == 200
    assert cleared.json()["sources"] == []


def test_put_dedups_repeated_ids(tmp_path):
    client = _client(tmp_path)
    _seed(client)
    resp = client.put(
        "/personas/ada-lovelace/sources",
        json={"sources": ["example-com", "example-com", "news-com"]},
    )
    assert resp.status_code == 200
    assert resp.json()["sources"] == ["example-com", "news-com"]  # collapsed, order kept


def test_post_link_is_idempotent(tmp_path):
    client = _client(tmp_path)
    _seed(client)

    first = client.post("/personas/ada-lovelace/sources/example-com")
    assert first.status_code == 200
    assert first.json()["sources"] == ["example-com"]

    # Linking the same source again is a success that does not duplicate it.
    again = client.post("/personas/ada-lovelace/sources/example-com")
    assert again.status_code == 200
    assert again.json()["sources"] == ["example-com"]


def test_delete_unlink_is_idempotent(tmp_path):
    client = _client(tmp_path)
    _seed(client)
    client.post("/personas/ada-lovelace/sources/example-com")

    once = client.delete("/personas/ada-lovelace/sources/example-com")
    assert once.status_code == 200 and once.json()["sources"] == []

    # Unlinking an absent source is a success, not a 404.
    twice = client.delete("/personas/ada-lovelace/sources/example-com")
    assert twice.status_code == 200 and twice.json()["sources"] == []


def test_post_link_unknown_portal_is_404(tmp_path):
    client = _client(tmp_path)
    _seed(client)
    resp = client.post("/personas/ada-lovelace/sources/ghost-com")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "portal_not_found"
    assert "ghost-com" in resp.json()["detail"]


def test_delete_unlink_tolerates_a_missing_portal(tmp_path):
    # A persona can carry a stale id (its portal was deleted). Unlinking it must clean the
    # link, NOT 404 on the missing portal -- DELETE 404s only on a missing persona.
    client = _client(tmp_path)
    _seed(client)
    client.put("/personas/ada-lovelace/sources", json={"sources": ["example-com"]})
    assert client.delete("/portals/example-com").status_code == 204  # portal gone, link stays

    # The stale link is visible (in sources, not portals) and can be unlinked.
    pool = client.get("/personas/ada-lovelace/sources").json()
    assert pool["sources"] == ["example-com"] and pool["portals"] == []
    cleaned = client.delete("/personas/ada-lovelace/sources/example-com")
    assert cleaned.status_code == 200 and cleaned.json()["sources"] == []


def test_routes_404_on_a_missing_persona(tmp_path):
    client = _client(tmp_path)
    _seed(client)  # the portal exists; the persona does not
    for method, path in (
        ("get", "/personas/ghost/sources"),
        ("post", "/personas/ghost/sources/example-com"),
        ("delete", "/personas/ghost/sources/example-com"),
    ):
        resp = getattr(client, method)(path)
        assert resp.status_code == 404
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["code"] == "persona_not_found"

    put = client.put("/personas/ghost/sources", json={"sources": ["example-com"]})
    assert put.status_code == 404 and put.json()["code"] == "persona_not_found"


def test_openapi_types_the_linking_responses(tmp_path):
    # The linking routes declare response_model, so the generated OpenAPI advertises
    # PersonaSourcesOut (a typed client + docs surface), not a bare 200.
    client = _client(tmp_path)
    spec = client.get("/openapi.json").json()
    assert "PersonaSourcesOut" in spec["components"]["schemas"]

    def _ref(method: str) -> str:
        content = spec["paths"]["/personas/{persona_id}/sources"][method]["responses"]["200"][
            "content"
        ]
        return content["application/json"]["schema"]["$ref"]

    assert _ref("get").endswith("/PersonaSourcesOut")
    assert _ref("put").endswith("/PersonaSourcesOut")
    item = spec["paths"]["/personas/{persona_id}/sources/{portal_id}"]
    assert item["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PersonaSourcesOut"
    )
    assert item["delete"]["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/PersonaSourcesOut")
