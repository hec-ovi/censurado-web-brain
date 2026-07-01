"""The author (persona) MANAGEMENT API (infra chunk 2), driven end to end.

This exercises the REAL entry point: ``create_app`` mounted under a FastAPI
``TestClient``, hit over HTTP. It walks the full lifecycle of a directly-created
persona (direct-create -> get -> patch -> delete -> 404) and the error edges
(duplicate id, missing id, invalid beat). Create is a pure persist (``POST
/personas/direct``); the brain runs no model, so nothing here spends an LLM call.
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


def _new_persona(**over) -> dict:
    body = {
        "display_name": "Ada Lovelace",
        "beat": "tech",
        "who_i_am": "I cover chips and the people who make them.",
        "style": "dry, precise, a little wry",
    }
    body.update(over)
    return body


def test_persona_lifecycle_direct_create_get_patch_delete(tmp_path):
    client = _client(tmp_path)

    # Direct-create: id derives from display_name, no synthesis job runs.
    created = client.post("/personas/direct", json=_new_persona(about="A bio.", sources=["example-com"]))
    assert created.status_code == 201
    body = created.json()
    assert body["id"] == "ada-lovelace"
    assert body["display_name"] == "Ada Lovelace"
    assert body["beat"] == "tech"
    assert body["about"] == "A bio."
    assert body["sources"] == ["example-com"]
    assert body["active"] is True

    # Get (the inline read route) sees the freshly-created row.
    got = client.get("/personas/ada-lovelace")
    assert got.status_code == 200
    assert got.json()["who_i_am"] == "I cover chips and the people who make them."

    # Patch: only named fields change; the rest are untouched.
    patched = client.patch(
        "/personas/ada-lovelace",
        json={"about": "An updated bio.", "style": "blunt"},
    )
    assert patched.status_code == 200
    pj = patched.json()
    assert pj["about"] == "An updated bio."
    assert pj["style"] == "blunt"
    assert pj["beat"] == "tech"  # unchanged
    assert pj["sources"] == ["example-com"]  # unchanged

    # active can be toggled through the same patch surface.
    deactivated = client.patch("/personas/ada-lovelace", json={"active": False})
    assert deactivated.status_code == 200 and deactivated.json()["active"] is False

    # Delete: 204, then a get is 404.
    deleted = client.delete("/personas/ada-lovelace")
    assert deleted.status_code == 204
    assert client.get("/personas/ada-lovelace").status_code == 404


def test_explicit_id_is_honored_on_direct_create(tmp_path):
    client = _client(tmp_path)
    created = client.post("/personas/direct", json=_new_persona(id="ada-custom"))
    assert created.status_code == 201
    assert created.json()["id"] == "ada-custom"


def test_duplicate_id_is_409(tmp_path):
    client = _client(tmp_path)
    assert client.post("/personas/direct", json=_new_persona()).status_code == 201
    dup = client.post("/personas/direct", json=_new_persona())
    assert dup.status_code == 409
    assert dup.json()["code"] == "duplicate_id"
    assert dup.headers["content-type"] == "application/problem+json"


def test_invalid_beat_on_create_is_422(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/personas/direct", json=_new_persona(beat="gossip"))
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_beat"


def test_missing_required_field_on_create_is_422_validation(tmp_path):
    client = _client(tmp_path)
    # No display_name -> Pydantic validation rejects the body before the handler, via the
    # app-wide handler, so it is the shared envelope (code == "validation_failed").
    resp = client.post("/personas/direct", json={"beat": "tech", "who_i_am": "x", "style": "y"})
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "validation_failed"


def test_patch_missing_is_404(tmp_path):
    client = _client(tmp_path)
    resp = client.patch("/personas/ghost", json={"about": "x"})
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["code"] == "persona_not_found"


def test_patch_invalid_beat_is_422(tmp_path):
    client = _client(tmp_path)
    client.post("/personas/direct", json=_new_persona())
    resp = client.patch("/personas/ada-lovelace", json={"beat": "gossip"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_persona"


def test_patch_empty_body_returns_current_state_without_bumping_updated_at(tmp_path):
    # An empty PATCH is a no-op read: it returns the current persona and must NOT touch
    # updated_at (no write happened), so a panel "save" with no edits is idempotent.
    client = _client(tmp_path)
    created = client.post("/personas/direct", json=_new_persona()).json()
    before = created["updated_at"]

    patched = client.patch("/personas/ada-lovelace", json={})
    assert patched.status_code == 200
    pj = patched.json()
    assert pj["id"] == "ada-lovelace"
    assert pj["updated_at"] == before  # untouched: no write, no timestamp bump


def test_delete_missing_is_404(tmp_path):
    client = _client(tmp_path)
    resp = client.delete("/personas/ghost")
    assert resp.status_code == 404
    assert resp.json()["code"] == "persona_not_found"


def test_openapi_types_the_persona_management_responses(tmp_path):
    # The management router declares response_model on its routes, so the generated
    # OpenAPI advertises PersonaOut (a typed client + docs surface), not a bare 200/201.
    client = _client(tmp_path)
    spec = client.get("/openapi.json").json()
    assert "PersonaOut" in spec["components"]["schemas"]

    # POST /personas/direct advertises 201 + PersonaOut.
    created = spec["paths"]["/personas/direct"]["post"]["responses"]["201"]["content"]
    assert created["application/json"]["schema"]["$ref"].endswith("/PersonaOut")

    # PATCH /personas/{persona_id} advertises 200 + PersonaOut.
    patched = spec["paths"]["/personas/{persona_id}"]["patch"]["responses"]["200"]["content"]
    assert patched["application/json"]["schema"]["$ref"].endswith("/PersonaOut")


def test_synthesized_persona_persists_and_fetches_back_intact_on_empty_db(tmp_path):
    # The create-author contract end to end: a fresh box ships ZERO personas; the operator's
    # CLI agent synthesizes a persona OFFLINE (the shape prompts/persona/synthesize.md
    # produces) and POSTs it to the pure-persist route; a fetch then returns every authored
    # field intact. No model runs in the brain at any step.
    client = _client(tmp_path)

    # A fresh DB carries nothing about who writes: zero personas in tracked code.
    listing = client.get("/personas")
    assert listing.status_code == 200
    assert listing.json() == {"personas": [], "total": 0}

    # The synthesized author. synthesize.md fills who_i_am/about/style/few_shots/sources; the
    # operator supplies display_name + beat from the seed brief.
    payload = {
        "display_name": "Ada Lovelace",
        "beat": "tech",
        "who_i_am": "Soy Ada Lovelace. Cubro semiconductores y la gente que los fabrica.",
        "about": "Ada Lovelace cubre la industria de los chips.",
        "style": "Frases declarativas, cifras primero, sin adjetivos de relleno.",
        "few_shots_pos": [
            {"prompt": "Una fabrica anuncia una linea nueva", "good": "La planta sumara 2.000 obleas por mes."}
        ],
        "few_shots_neg": [
            {"prompt": "Una fabrica anuncia una linea nueva", "bad": "En un giro demoledor, vuelven a sorprender."}
        ],
        "sources": ["example-com"],
    }

    created = client.post("/personas/direct", json=payload)
    assert created.status_code == 201
    assert created.json()["id"] == "ada-lovelace"

    # The fetch round-trips every authored field verbatim: the persist endpoint loses nothing.
    fetched = client.get("/personas/ada-lovelace")
    assert fetched.status_code == 200
    got = fetched.json()
    for field in ("who_i_am", "about", "style", "beat", "sources", "few_shots_pos", "few_shots_neg"):
        assert got[field] == payload[field], field
    assert got["language"] == "español neutro"  # the create default rides through
    assert got["active"] is True

    # The box now holds exactly that one operator-created author, and nothing else.
    after = client.get("/personas").json()
    assert after["total"] == 1
    assert [p["id"] for p in after["personas"]] == ["ada-lovelace"]
