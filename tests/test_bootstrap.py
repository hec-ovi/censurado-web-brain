"""The one-command bootstrap: seed (idempotent), then run.

Seeding and the run share one connection, so the run sees the personas the seed just
wrote. The run step is injected here so the seed wiring is proven without the inference
pipeline: the headline assertion is that the runner is invoked AFTER seeding (the seeded
personas are visible to it). A real managed run end to end is covered by ``test_cli``.
"""

from __future__ import annotations

import json

from newsroom.bootstrap import bootstrap
from newsroom.config import Settings
from newsroom.cli import main
from newsroom.db import open_db
from newsroom.editorial import LocationStore, PortalStore, StyleStore
from newsroom.personas import PersonaStore

_DEFAULT_IDS = {"lara-arianna", "borge-luis-jorge", "glorieta-sadeta", "vector-omni"}


def _settings(tmp_path, **over) -> Settings:
    base = dict(persona_db_path=tmp_path / "brain.db")
    base.update(over)
    return Settings(**base)


def test_bootstrap_seed_only_populates_a_fresh_box(tmp_path):
    result = bootstrap(_settings(tmp_path), run=False)

    assert result["ran"] is False
    seeded = result["seeded"]
    assert seeded["location_created"] is True and seeded["style_created"] is True
    assert set(seeded["personas_created"]) == _DEFAULT_IDS
    assert "clarin-com" in seeded["portals_created"]

    # The rows really landed on disk.
    conn = open_db(tmp_path / "brain.db", check_same_thread=False)
    assert LocationStore(conn).get().region == "AR"
    assert {p.id for p in PersonaStore(conn).list()} >= _DEFAULT_IDS
    assert StyleStore(conn).active().version == 1
    assert "clarin.com" in PortalStore(conn).domains()


def test_bootstrap_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    bootstrap(settings, run=False)
    second = bootstrap(settings, run=False)

    assert second["seeded"]["personas_created"] == []
    assert set(second["seeded"]["personas_skipped"]) == _DEFAULT_IDS
    assert second["seeded"]["location_created"] is False
    assert second["seeded"]["style_created"] is False
    # No duplicate personas after two bootstraps.
    conn = open_db(tmp_path / "brain.db", check_same_thread=False)
    assert len({p.id for p in PersonaStore(conn).list()}) == len(PersonaStore(conn).list())


def test_bootstrap_runs_after_seeding_with_injected_runner(tmp_path):
    seen: dict = {}

    def runner(settings, conn, persona_store, *, mode, n):
        # When the runner fires, the seeded personas must already be visible to it,
        # so the manager will not hit the empty-persona early-return.
        seen["personas"] = {p.id for p in persona_store.list()}
        seen["mode"] = mode
        return {"run_id": "r1", "mode": mode, "status": "done",
                "assigned": 0, "published": 0, "failed": 0, "dropped": 0}

    result = bootstrap(_settings(tmp_path), run=True, mode="managed", runner=runner)

    assert result["ran"] is True
    assert seen["personas"] >= _DEFAULT_IDS  # seeded BEFORE the run
    assert seen["mode"] == "managed"
    assert result["run"]["status"] == "done"


def test_bootstrap_forwards_seed_overrides(tmp_path):
    result = bootstrap(_settings(tmp_path), run=False, personas=(), portals=())

    assert result["seeded"]["personas_created"] == []
    assert result["seeded"]["portals_created"] == []
    # Location and style are still seeded by the same call.
    assert result["seeded"]["location_created"] is True
    assert result["seeded"]["style_created"] is True


# ----- the CLI subcommand dispatch -----


def test_cli_bootstrap_no_run_seeds_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEWSROOM_PERSONA_DB_PATH", str(tmp_path / "brain.db"))

    code = main(["bootstrap", "--no-run"])

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ran"] is False
    assert "lara-arianna" in out["seeded"]["personas_created"]

    # A second invocation is a clean no-op (idempotent setup).
    code2 = main(["bootstrap", "--no-run"])
    assert code2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["seeded"]["personas_created"] == []
    assert "lara-arianna" in out2["seeded"]["personas_skipped"]


def test_cli_bare_run_invocation_is_unchanged_by_the_subcommand(tmp_path, monkeypatch):
    # Adding the bootstrap subcommand must not change the bare run parser: an unknown
    # mode is still rejected by argparse (proves the run path still parses normally).
    import pytest

    monkeypatch.setenv("NEWSROOM_PERSONA_DB_PATH", str(tmp_path / "brain.db"))
    with pytest.raises(SystemExit):
        main(["--mode", "turbo"])
