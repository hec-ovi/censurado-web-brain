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

    personas = [_persona("lara"), _persona("broken"), _persona("borge", beat="economics")]
    report = backfill_web_authors(personas, push)

    assert report.pushed == ["lara", "borge"]  # the loop continued past the failure
    assert report.failed == [{"handle": "broken", "code": "store_error", "detail": ""}]
    # The push only ever sees the public fields (id/name/bio/avatar), never the prompt.
    assert ("lara", "Lara", "Bio de lara.", "avatars/lara.png") in seen


# ----- push client: POST /authors mapping -----


def test_push_web_author_posts_public_body_and_maps_200(monkeypatch):
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(200, json={"handle": "lara", "name": "Lara"})

    monkeypatch.setattr("newsroom.mirror.client.httpx.post", fake_post)
    res = push_web_author(
        "http://web:8080/", "tok-admin", handle="lara", name="Lara", bio="Bio.", avatar="a.png"
    )

    assert captured["url"] == "http://web:8080/authors"
    assert captured["headers"]["Authorization"] == "Bearer tok-admin"
    assert captured["json"] == {"handle": "lara", "name": "Lara", "bio": "Bio.", "avatar": "a.png"}
    assert res.ok is True and res.status == 200


def test_push_web_author_maps_a_problem_response(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return httpx.Response(403, json={"code": "insufficient_scope", "detail": "requires admin:write"})

    monkeypatch.setattr("newsroom.mirror.client.httpx.post", fake_post)
    res = push_web_author("http://web:8080", "tok", handle="lara")
    assert res.ok is False
    assert res.status == 403 and res.code == "insufficient_scope"


def test_push_web_author_returns_transport_error_on_raise(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("newsroom.mirror.client.httpx.post", fake_post)
    res = push_web_author("http://web:8080", "tok", handle="lara")
    assert res.ok is False and res.code == "transport_error"


# ----- CLI verb: mirror-authors -----


def _seed_two_personas(tmp_path):
    store = PersonaStore(open_db(tmp_path / "brain.db"))
    store.create(_persona("lara-arianna"))
    store.create(_persona("vector-omni", beat="tech"))


def test_cli_mirror_authors_dry_run_lists_handles_without_pushing(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NEWSROOM_OPERATOR_TOKEN", raising=False)
    monkeypatch.setenv("NEWSROOM_PERSONA_DB_PATH", str(tmp_path / "brain.db"))
    _seed_two_personas(tmp_path)

    code = main(["mirror-authors", "--dry-run"])

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert set(out["pushed"]) == {"lara-arianna", "vector-omni"}
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
        ok = persona.id != "vector-omni"
        return PushResult(handle=persona.id, ok=ok, status=200 if ok else 500, code="" if ok else "store_error")

    code = _mirror_authors_main([], push=push)

    out = json.loads(capsys.readouterr().out)
    assert set(pushed) == {"lara-arianna", "vector-omni"}
    assert out["pushed"] == ["lara-arianna"]
    assert out["failed"][0]["handle"] == "vector-omni"
    assert code == 1  # a failed push is a non-zero exit
