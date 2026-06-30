"""The one-command bootstrap: seed the editorial config (idempotent) + mirror authors.

Seeding is find-or-create, so a re-run never clobbers an operator's edits. The shipped
defaults carry NO authors or sources (``DEFAULT_PERSONAS``/``DEFAULT_PORTALS`` are
empty), so a plain bootstrap seeds only the author-agnostic config (location, style,
prompts); these tests pass explicit synthetic personas to exercise the persona and
mirror paths. The platform author registry is mirrored into the local personas after
the seed (best-effort: a down platform is a skipped no-op, never an emptied newsroom).
"""

from __future__ import annotations

import json

import httpx

from newsroom.bootstrap import bootstrap
from newsroom.config import Settings
from newsroom.cli import main
from newsroom.db import open_db
from newsroom.editorial import LocationStore, PortalStore, StyleStore
from newsroom.mirror import WebAuthor
from newsroom.personas import Persona, PersonaStore

# Synthetic authors that drive the seed + mirror paths; the shipped defaults are empty.
_SEED_PERSONAS = (
    Persona(id="ada-lovelace", display_name="Ada Lovelace", beat="politics",
            who_i_am="Sos Ada Lovelace, cronista politica.", style="Declarativas claras."),
    Persona(id="ida-rivers", display_name="Ida Rivers", beat="economics",
            who_i_am="Sos Ida Rivers, cronista de economia.", style="Cifras primero."),
    Persona(id="cory-bell", display_name="Cory Bell", beat="world",
            who_i_am="Sos Cory Bell, cronista internacional.", style="Contexto breve."),
    Persona(id="noor-vega", display_name="Noor Vega", beat="tech",
            who_i_am="Sos Noor Vega, cronista de tecnologia.", style="Concreto y verificable."),
)
_SEED_IDS = {p.id for p in _SEED_PERSONAS}


def _settings(tmp_path, **over) -> Settings:
    base = dict(persona_db_path=tmp_path / "brain.db")
    base.update(over)
    return Settings(**base)


def test_bootstrap_seeds_agnostic_config_but_no_authors_by_default(tmp_path):
    result = bootstrap(_settings(tmp_path))

    seeded = result["seeded"]
    assert seeded["location_created"] is True and seeded["style_created"] is True
    # The shipped defaults invent no authors or sources.
    assert seeded["personas_created"] == []
    assert seeded["portals_created"] == []

    # The agnostic rows really landed; personas/portals stay empty.
    conn = open_db(tmp_path / "brain.db", check_same_thread=False)
    assert LocationStore(conn).get().region == "AR"
    assert StyleStore(conn).active().version == 1
    assert PersonaStore(conn).list() == []
    assert PortalStore(conn).list() == []


def test_bootstrap_seeds_explicit_personas(tmp_path):
    result = bootstrap(_settings(tmp_path), personas=_SEED_PERSONAS, portals=())

    assert set(result["seeded"]["personas_created"]) == _SEED_IDS
    conn = open_db(tmp_path / "brain.db", check_same_thread=False)
    assert {p.id for p in PersonaStore(conn).list()} == _SEED_IDS


def test_bootstrap_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    bootstrap(settings, personas=_SEED_PERSONAS)
    second = bootstrap(settings, personas=_SEED_PERSONAS)

    assert second["seeded"]["personas_created"] == []
    assert set(second["seeded"]["personas_skipped"]) == _SEED_IDS
    assert second["seeded"]["location_created"] is False
    assert second["seeded"]["style_created"] is False
    # No duplicate personas after two bootstraps.
    conn = open_db(tmp_path / "brain.db", check_same_thread=False)
    assert len({p.id for p in PersonaStore(conn).list()}) == len(PersonaStore(conn).list())


# ----- the platform author mirror runs as part of bootstrap -----


def test_bootstrap_reconciles_personas_from_web(tmp_path):
    # Seed creates the synthetic authors; the platform registry then refreshes one public
    # bio, omits one handle (soft-deactivate), and adds a web-only author (inactive
    # shell). The reconcile runs AFTER the seed and is reflected on disk.
    def fetch():
        return [
            WebAuthor("ada-lovelace", "Ada Lovelace", "Bio nueva de la plataforma", "ada.png"),
            WebAuthor("ida-rivers", "Ida Rivers", "", ""),
            WebAuthor("cory-bell", "Cory Bell", "", ""),
            # noor-vega intentionally absent -> deactivated
            WebAuthor("nuevo-web", "Nuevo Web", "creado en la web", ""),
        ]

    result = bootstrap(_settings(tmp_path), personas=_SEED_PERSONAS, fetch_authors=fetch)

    rec = result["reconciled"]
    assert rec["skipped"] is False
    assert "ada-lovelace" in rec["refreshed"]
    assert rec["created"] == ["nuevo-web"]
    assert rec["deactivated"] == ["noor-vega"]

    store = PersonaStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    # Public field refreshed, private prompt preserved.
    ada = store.get("ada-lovelace")
    assert ada.about == "Bio nueva de la plataforma"
    assert ada.who_i_am.startswith("Sos Ada Lovelace")
    # Dropped from the platform -> inactive, prompt kept (not deleted).
    vega = store.get("noor-vega")
    assert vega.active is False and vega.who_i_am != ""
    # The web-only author is an inactive shell with no prompt.
    shell = store.get("nuevo-web")
    assert shell.active is False and shell.who_i_am == ""
    # The active set sees only the prompted, still-listed personas.
    active_ids = {p.id for p in store.list()}
    assert {"ada-lovelace", "ida-rivers", "cory-bell"} <= active_ids
    assert "noor-vega" not in active_ids and "nuevo-web" not in active_ids


def test_bootstrap_skips_reconcile_when_the_platform_is_unreachable(tmp_path):
    def boom():
        raise httpx.ConnectError("platform down")

    result = bootstrap(_settings(tmp_path), personas=_SEED_PERSONAS, fetch_authors=boom)

    assert result["reconciled"]["skipped"] is True
    # The newsroom was NOT emptied: all seeded personas remain active.
    store = PersonaStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    assert {p.id for p in store.list()} == _SEED_IDS


# ----- the CLI subcommand dispatch -----


def test_cli_bootstrap_seeds_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEWSROOM_PERSONA_DB_PATH", str(tmp_path / "brain.db"))

    code = main(["bootstrap"])

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    # The shipped defaults carry no authors, so a plain CLI bootstrap seeds the
    # author-agnostic config and zero personas.
    assert out["seeded"]["personas_created"] == []
    assert out["seeded"]["location_created"] is True

    # A second invocation is a clean no-op (idempotent setup).
    code2 = main(["bootstrap"])
    assert code2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["seeded"]["personas_created"] == []
    assert out2["seeded"]["location_created"] is False
