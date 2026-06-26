"""The author mirror. The platform owns author existence and the public fields; the
brain keeps the private prompt. The client maps the GET /authors JSON (dropping
tombstoned and handle-less rows); the reconcile (pure, no network) creates inactive
shells for web-only handles, refreshes public fields WITHOUT touching the prompt,
soft-deactivates a handle that left the platform, and reactivates one that returns.
An empty registry is a no-op so a fresh or unreachable platform never empties the
newsroom.
"""

from __future__ import annotations

import httpx
import pytest

from newsroom.db import open_db
from newsroom.mirror import WebAuthor, fetch_web_authors, reconcile_personas
from newsroom.personas import Persona, PersonaStore


@pytest.fixture
def store() -> PersonaStore:
    return PersonaStore(open_db(":memory:"))


def _persona(**over) -> Persona:
    base = dict(
        id="lara-arianna",
        display_name="Lara Arianna",
        beat="politics",
        who_i_am="Sos Lara Arianna, cronista politica.",
        style="Declarativas claras.",
        about="Cubre el poder.",
        avatar_path="avatars/lara.png",
    )
    base.update(over)
    return Persona(**base)


# ----- client: GET /authors JSON mapping -----


def test_fetch_web_authors_maps_live_rows_and_drops_deleted(monkeypatch):
    captured: dict = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "authors": [
                    {
                        "handle": "lara-arianna",
                        "name": "Lara Arianna",
                        "bio": "Cubre el poder.",
                        "avatar": "avatars/lara.png",
                        "deleted": False,
                    },
                    {"handle": "ghost", "name": "Ghost", "deleted": True},  # tombstoned
                    {"handle": "", "name": "No Handle"},  # empty handle
                ]
            },
        )

    monkeypatch.setattr("newsroom.mirror.client.httpx.get", fake_get)
    authors = fetch_web_authors("http://web:8080/", "tok-123")

    assert captured["url"] == "http://web:8080/authors"
    assert captured["headers"]["Authorization"] == "Bearer tok-123"
    assert authors == [
        WebAuthor("lara-arianna", "Lara Arianna", "Cubre el poder.", "avatars/lara.png")
    ]


def test_fetch_web_authors_raises_on_non_2xx(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr("newsroom.mirror.client.httpx.get", fake_get)
    with pytest.raises(httpx.HTTPError):
        fetch_web_authors("http://web:8080", "tok")


# ----- reconcile: pure policy -----


def test_empty_registry_is_a_noop(store):
    store.create(_persona())
    result = reconcile_personas(store, [])
    assert result.skipped is True
    assert [p.id for p in store.list()] == ["lara-arianna"]  # nothing deactivated


def test_refreshes_public_fields_without_touching_the_prompt(store):
    store.create(_persona(about="old bio", avatar_path="old.png"))
    result = reconcile_personas(
        store, [WebAuthor("lara-arianna", "Lara A.", "new bio from web", "new.png")]
    )
    got = store.get("lara-arianna")
    assert got.display_name == "Lara A."
    assert got.about == "new bio from web"
    assert got.avatar_path == "new.png"
    # The prompt is untouched.
    assert got.who_i_am == "Sos Lara Arianna, cronista politica."
    assert got.style == "Declarativas claras."
    assert got.beat == "politics"
    assert result.refreshed == ["lara-arianna"]


def test_empty_web_field_does_not_blank_a_good_local_value(store):
    store.create(_persona(about="good local bio", avatar_path="good.png"))
    reconcile_personas(store, [WebAuthor("lara-arianna", name="Lara", bio="", avatar="")])
    got = store.get("lara-arianna")
    assert got.about == "good local bio"  # not wiped by the empty web bio
    assert got.avatar_path == "good.png"
    assert got.display_name == "Lara"


def test_creates_an_inactive_shell_for_an_unknown_handle(store):
    result = reconcile_personas(store, [WebAuthor("nuevo", "Nuevo Autor", "bio", "a.png")])
    shell = store.get("nuevo")
    assert shell is not None
    assert shell.active is False  # no prompt yet -> cannot write
    assert shell.who_i_am == "" and shell.style == ""
    assert shell.display_name == "Nuevo Autor" and shell.about == "bio"
    assert store.list() == []  # invisible to the run path
    assert result.created == ["nuevo"]


def test_soft_deactivates_a_handle_that_left_the_platform(store):
    store.create(_persona(id="lara-arianna"))
    store.create(_persona(id="borge", display_name="Borge", beat="economics"))
    # Only lara is still on the platform.
    result = reconcile_personas(store, [WebAuthor("lara-arianna", "Lara Arianna")])
    borge = store.get("borge")
    assert borge is not None  # tombstoned, NOT deleted
    assert borge.active is False
    assert borge.who_i_am != ""  # prompt preserved
    assert result.deactivated == ["borge"]
    assert [p.id for p in store.list()] == ["lara-arianna"]


def test_reactivates_a_returning_handle(store):
    store.create(_persona(id="lara-arianna", active=False))  # previously deactivated
    result = reconcile_personas(store, [WebAuthor("lara-arianna", "Lara Arianna")])
    assert store.get("lara-arianna").active is True
    assert result.reactivated == ["lara-arianna"]


def test_an_inactive_shell_is_not_reactivated_without_a_prompt(store):
    # A shell (empty prompt) stays inactive even though it is on the platform; its
    # public fields are still refreshed.
    store.create(_persona(id="shell", who_i_am="", style="", active=False))
    result = reconcile_personas(store, [WebAuthor("shell", "Shell", "bio2", "")])
    got = store.get("shell")
    assert got.active is False
    assert got.about == "bio2"
    assert "shell" not in result.reactivated
