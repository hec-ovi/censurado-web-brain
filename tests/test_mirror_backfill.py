"""The one-time author backfill: push local personas' PUBLIC fields to the platform
author registry so the platform becomes authoritative. The orchestrator (pure) splits
pushed/failed and never aborts on one failure; the push client maps POST /authors; and
the ``mirror-authors`` CLI verb lists handles on --dry-run and pushes otherwise. Only
public fields cross the boundary; the prompt never leaves the brain.
"""

from __future__ import annotations

import json

import httpx

from newsroom.cli import _mirror_authors_main, main
from newsroom.db import open_db
from newsroom.mirror import (
    PushResult,
    backfill_web_authors,
    push_web_author,
)
from newsroom.personas import Persona, PersonaStore


def _persona(pid: str, **over) -> Persona:
    base = dict(
        id=pid,
        display_name=pid.title(),
        beat="politics",
        who_i_am=f"Prompt privado de {pid}.",
        style="Declarativas.",
        about=f"Bio de {pid}.",
        avatar_path=f"avatars/{pid}.png",
    )
    base.update(over)
    return Persona(**base)


# ----- orchestrator: pure split of pushed/failed -----


def test_backfill_pushes_public_fields_and_collects_failures():
    seen: list = []

    def push(persona: Persona) -> PushResult:
        seen.append((persona.id, persona.display_name, persona.about, persona.avatar_path))
        if persona.id == "broken":
            return PushResult(handle=persona.id, ok=False, status=500, code="store_error")
        return PushResult(handle=persona.id, ok=True, status=200)

    personas = [_persona("ada"), _persona("broken"), _persona("ida", beat="economics")]
    report = backfill_web_authors(personas, push)

    assert report.pushed == ["ada", "ida"]  # the loop continued past the failure
    assert report.failed == [{"handle": "broken", "code": "store_error", "detail": ""}]
    # The push only ever sees the public fields (id/name/bio/avatar), never the prompt.
    assert ("ada", "Ada", "Bio de ada.", "avatars/ada.png") in seen


# ----- push client: POST /authors mapping -----


def test_push_web_author_posts_full_scalar_body_and_maps_200(monkeypatch):
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(200, json={"handle": "ada", "name": "Ada"})

    monkeypatch.setattr("newsroom.mirror.client.httpx.post", fake_post)
    res = push_web_author(
        "http://web:8080/", "tok-admin", handle="ada", name="Ada", bio="Bio.",
        avatar="a.png", gender="femenino", about="Bio.", style="Declarativa.",
    )

    assert captured["url"] == "http://web:8080/authors"
    assert captured["headers"]["Authorization"] == "Bearer tok-admin"
    # The six scalar columns are ALWAYS sent, because the upsert replaces them wholesale:
    # a partial body would blank a column.
    assert captured["json"] == {
        "handle": "ada", "name": "Ada", "bio": "Bio.", "avatar": "a.png",
        "gender": "femenino", "about": "Bio.", "style": "Declarativa.",
    }
    assert res.ok is True and res.status == 200


def test_push_web_author_sends_topics_first_class_and_metadata_tail(monkeypatch):
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return httpx.Response(200, json={"handle": "ada"})

    monkeypatch.setattr("newsroom.mirror.client.httpx.post", fake_post)
    res = push_web_author(
        "http://web:8080/", "tok-admin", handle="ada", name="Ada",
        topics=["politica", "economia"],
        metadata={"beat": "politics", "who_i_am": "Soy Ada.", "few_shots_pos": ["ej"]},
    )

    # Curated topics are a first-class column now; the private tail (beat/who_i_am/few_shots)
    # rides in the metadata blob the platform stores verbatim.
    assert captured["json"]["topics"] == ["politica", "economia"]
    assert captured["json"]["metadata"] == {
        "beat": "politics", "who_i_am": "Soy Ada.", "few_shots_pos": ["ej"]
    }
    assert res.ok is True


def test_push_web_author_omits_topics_sources_metadata_when_empty(monkeypatch):
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return httpx.Response(200, json={"handle": "ada"})

    monkeypatch.setattr("newsroom.mirror.client.httpx.post", fake_post)
    push_web_author("http://web:8080", "tok", handle="ada", name="Ada")

    # With nothing curated the body is exactly the seven scalar keys: no topics, no metadata
    # blob, and no sources key (a nil pointer leaves the author_sources join untouched).
    assert set(captured["json"]) == {"handle", "name", "bio", "avatar", "gender", "about", "style"}
    assert "sources" not in captured["json"]


def test_push_web_author_sources_empty_list_is_an_explicit_detach(monkeypatch):
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return httpx.Response(200, json={"handle": "ada"})

    monkeypatch.setattr("newsroom.mirror.client.httpx.post", fake_post)
    push_web_author("http://web:8080", "tok", handle="ada", sources=[])

    # An explicit empty list is sent (detach everything), distinct from None (leave the join).
    assert captured["json"]["sources"] == []


def test_push_web_author_maps_a_problem_response(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return httpx.Response(403, json={"code": "insufficient_scope", "detail": "requires admin:write"})

    monkeypatch.setattr("newsroom.mirror.client.httpx.post", fake_post)
    res = push_web_author("http://web:8080", "tok", handle="ada")
    assert res.ok is False
    assert res.status == 403 and res.code == "insufficient_scope"


def test_push_web_author_returns_transport_error_on_raise(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("newsroom.mirror.client.httpx.post", fake_post)
    res = push_web_author("http://web:8080", "tok", handle="ada")
    assert res.ok is False and res.code == "transport_error"


# ----- CLI verb: mirror-authors -----


def _seed_two_personas(tmp_path):
    store = PersonaStore(open_db(tmp_path / "brain.db"))
    store.create(_persona("ada-lovelace"))
    store.create(_persona("noor-vega", beat="tech"))


def test_cli_mirror_authors_dry_run_lists_handles_without_pushing(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NEWSROOM_OPERATOR_TOKEN", raising=False)
    monkeypatch.setenv("NEWSROOM_PERSONA_DB_PATH", str(tmp_path / "brain.db"))
    _seed_two_personas(tmp_path)

    code = main(["mirror-authors", "--dry-run"])

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert set(out["pushed"]) == {"ada-lovelace", "noor-vega"}
    assert out["failed"] == []


def test_cli_mirror_authors_without_a_token_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NEWSROOM_OPERATOR_TOKEN", raising=False)
    monkeypatch.setenv("NEWSROOM_PERSONA_DB_PATH", str(tmp_path / "brain.db"))
    _seed_two_personas(tmp_path)

    code = main(["mirror-authors"])

    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert "operator token" in out["error"]


def test_mirror_authors_main_pushes_with_an_injected_push(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEWSROOM_PERSONA_DB_PATH", str(tmp_path / "brain.db"))
    _seed_two_personas(tmp_path)
    pushed: list = []

    def push(persona: Persona) -> PushResult:
        pushed.append(persona.id)
        ok = persona.id != "noor-vega"
        return PushResult(handle=persona.id, ok=ok, status=200 if ok else 500, code="" if ok else "store_error")

    code = _mirror_authors_main([], push=push)

    out = json.loads(capsys.readouterr().out)
    assert set(pushed) == {"ada-lovelace", "noor-vega"}
    assert out["pushed"] == ["ada-lovelace"]
    assert out["failed"][0]["handle"] == "noor-vega"
    assert code == 1  # a failed push is a non-zero exit
