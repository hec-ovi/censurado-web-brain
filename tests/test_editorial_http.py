"""The editorial-config management API (infra chunk 5), driven end to end.

This exercises the REAL entry point: ``create_app`` mounted under a FastAPI
``TestClient``, hit over HTTP. It walks the versioned house style (publish -> get ->
publish again -> versions -> promote/rollback), the lexicon and sourcing sub-resources
(which derive a new active version under the hood), and the single-row location
(default -> update -> reflect), plus the error edges (no active style is 404, a blank
required location field is 422, a wrong-typed body is the shared validation envelope)
and the OpenAPI typing.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from newsroom.brain import create_app
from newsroom.config import Settings


def _client(tmp_path) -> TestClient:
    settings = Settings(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url="http://127.0.0.1:1/v1",
    )
    return TestClient(create_app(settings=settings))


def _guide(**over) -> dict:
    """A small but complete style-guide body. Overridable per test; carries the lexicon
    and sourcing facets the sub-resource routes read."""
    body = {
        "voice": "Sober, plain, attribute every claim.",
        "exemplars": [{"label": "good", "text": "El Senado rechazo la ley.", "why": "factual"}],
        "rules": [{"id": "attribute", "text": "Atribui cada afirmacion.", "scope": "both"}],
        "lexicon": {"banned_terms": ["demoledor"], "preferred_swaps": {"polemico": "discutido"}},
        "sourcing": {"min_sources": 2, "require_attribution": True, "no_fabricated_quotes": True},
        "structure": {"headline": "breve y directo"},
        "created_by": "hector",
    }
    body.update(over)
    return body


# ----- style guide: versioned lifecycle -----


def test_style_get_on_empty_is_404(tmp_path):
    # A fresh box has no published version yet, so the active-style read is a 404 in the
    # shared problem+json envelope (not a fabricated empty guide).
    client = _client(tmp_path)
    resp = client.get("/editorial/style")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "not_found"


def test_style_publish_then_get_reflects_it(tmp_path):
    client = _client(tmp_path)

    created = client.post("/editorial/style", json=_guide())
    assert created.status_code == 201
    body = created.json()
    assert body["version"] == 1
    assert body["is_active"] is True  # the first version auto-activates
    assert body["created_by"] == "hector"
    assert body["voice"].startswith("Sober")
    assert body["lexicon"]["banned_terms"] == ["demoledor"]
    assert body["sourcing"]["min_sources"] == 2

    got = client.get("/editorial/style").json()
    assert got["version"] == 1
    assert got["rules"][0]["id"] == "attribute"


def test_style_second_publish_activates_and_versions_list_and_rollback(tmp_path):
    client = _client(tmp_path)
    client.post("/editorial/style", json=_guide(voice="v1 voice"))
    v2 = client.post("/editorial/style", json=_guide(voice="v2 voice")).json()
    assert v2["version"] == 2 and v2["is_active"] is True

    # The active read is now v2 (publish activates by default).
    assert client.get("/editorial/style").json()["voice"] == "v2 voice"

    # The version history is newest-first, with exactly one active.
    versions = client.get("/editorial/style/versions").json()
    assert versions["total"] == 2
    assert [v["version"] for v in versions["versions"]] == [2, 1]
    assert [v["is_active"] for v in versions["versions"]] == [True, False]

    # Promote (= rollback) to v1; v2 stays retrievable in the history.
    promoted = client.post("/editorial/style/versions/1/promote")
    assert promoted.status_code == 200
    assert promoted.json()["version"] == 1 and promoted.json()["is_active"] is True
    assert client.get("/editorial/style").json()["voice"] == "v1 voice"


def test_style_publish_staged_without_activating(tmp_path):
    # activate=False stages a version without moving the pointer: the active read stays on
    # the prior version until an explicit promote.
    client = _client(tmp_path)
    client.post("/editorial/style", json=_guide(voice="live"))
    staged = client.post("/editorial/style", json=_guide(voice="staged", activate=False)).json()
    assert staged["version"] == 2 and staged["is_active"] is False
    assert client.get("/editorial/style").json()["voice"] == "live"  # unchanged


def test_promote_unknown_version_is_404(tmp_path):
    client = _client(tmp_path)
    client.post("/editorial/style", json=_guide())
    resp = client.post("/editorial/style/versions/99/promote")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_publish_wrong_typed_body_is_validation_envelope_422(tmp_path):
    # A wrong-typed field (lexicon must be an object) rides the app-wide validation handler,
    # so it is the shared envelope (code == validation_failed), not FastAPI's default shape.
    client = _client(tmp_path)
    resp = client.post("/editorial/style", json={"lexicon": "not-an-object"})
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "validation_failed"


# ----- lexicon + sourcing sub-resources (derive a new active version) -----


def test_lexicon_get_and_put_derives_a_new_version(tmp_path):
    client = _client(tmp_path)
    client.post("/editorial/style", json=_guide())

    got = client.get("/editorial/style/lexicon").json()
    assert got["banned_terms"] == ["demoledor"]
    assert got["preferred_swaps"] == {"polemico": "discutido"}

    # PUT replaces the lexicon; under the hood a new active version is created.
    put = client.put(
        "/editorial/style/lexicon",
        json={"banned_terms": ["letal", "brutal"], "preferred_swaps": {}},
    )
    assert put.status_code == 200
    assert put.json()["banned_terms"] == ["letal", "brutal"]

    # The active style now carries the new lexicon on a bumped version, and the rest of the
    # guide (the voice) is preserved across the derived version.
    active = client.get("/editorial/style").json()
    assert active["version"] == 2
    assert active["lexicon"]["banned_terms"] == ["letal", "brutal"]
    assert active["voice"].startswith("Sober")


def test_lexicon_get_on_empty_is_404(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/editorial/style/lexicon")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_sourcing_get_and_put_changes_min_sources(tmp_path):
    # min_sources lives in the sourcing block; the sub-resource is how an operator tunes
    # the corroboration floor.
    client = _client(tmp_path)
    client.post("/editorial/style", json=_guide())

    got = client.get("/editorial/style/sourcing").json()
    assert got["min_sources"] == 2 and got["require_attribution"] is True

    put = client.put(
        "/editorial/style/sourcing",
        json={"min_sources": 3, "require_attribution": True, "no_fabricated_quotes": False},
    )
    assert put.status_code == 200
    assert put.json()["min_sources"] == 3

    # Reflected on the active style's sourcing block, on a new version.
    active = client.get("/editorial/style").json()
    assert active["version"] == 2
    assert active["sourcing"]["min_sources"] == 3
    assert active["sourcing"]["no_fabricated_quotes"] is False


def test_sourcing_put_on_empty_is_404(tmp_path):
    client = _client(tmp_path)
    resp = client.put("/editorial/style/sourcing", json={"min_sources": 2})
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


# ----- location: single editable row -----


def test_location_get_default_then_update_then_reflect(tmp_path):
    client = _client(tmp_path)

    # GET before anything is stored returns the usable default (Argentina, Spanish) plus
    # the derived Google-News views.
    default = client.get("/editorial/location").json()
    assert (default["region"], default["language"], default["ui_lang"]) == ("AR", "es", "es-419")
    assert default["ceid"] == "AR:es-419"

    # PUT a partial change: only the named fields move; unnamed ones keep their value.
    updated = client.put("/editorial/location", json={"region": "ES", "city": "Madrid"}).json()
    assert updated["region"] == "ES" and updated["city"] == "Madrid"
    assert updated["language"] == "es"  # untouched
    assert updated["gl"] == "ES"  # derived view tracks the change

    # GET reflects the stored row.
    got = client.get("/editorial/location").json()
    assert (got["region"], got["city"]) == ("ES", "Madrid")


def test_location_put_blank_required_field_is_422(tmp_path):
    # A blank required field (region) is a store rejection surfaced as 422 invalid_location.
    client = _client(tmp_path)
    resp = client.put("/editorial/location", json={"region": "   "})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_location"


def test_location_put_empty_body_is_a_noop_read(tmp_path):
    # An empty PUT returns the current value without a write, so a console "save" with no
    # edits never bumps updated_at.
    client = _client(tmp_path)
    first = client.put("/editorial/location", json={"city": "Cordoba"}).json()
    stamped = first["updated_at"]
    noop = client.put("/editorial/location", json={}).json()
    assert noop["city"] == "Cordoba"
    assert noop["updated_at"] == stamped  # untouched: no write


def test_openapi_types_the_editorial_responses(tmp_path):
    # response_model on every route, so the generated OpenAPI advertises the typed models
    # (a typed client + docs surface), not a bare 200.
    client = _client(tmp_path)
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in ("StyleGuideOut", "StyleVersionsOut", "LexiconOut", "SourcingOut", "LocationOut"):
        assert name in schemas

    def _ref(path: str, method: str, code: str = "200") -> str:
        content = spec["paths"][path][method]["responses"][code]["content"]
        return content["application/json"]["schema"]["$ref"]

    assert _ref("/editorial/style", "get").endswith("/StyleGuideOut")
    assert _ref("/editorial/style", "post", "201").endswith("/StyleGuideOut")
    assert _ref("/editorial/style/versions", "get").endswith("/StyleVersionsOut")
    assert _ref("/editorial/location", "get").endswith("/LocationOut")
