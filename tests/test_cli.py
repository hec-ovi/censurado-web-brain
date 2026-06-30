"""The brain's management CLI (``newsroom.cli.main``), driven end to end.

These tests exercise the REAL entry point (``main`` with parsed argv) through to its side
effects: the source (portal) registry, the author (persona) registry + per-author source
links, the versioned house style + location editorial config, and the topic-cleanse
map-file lane. Each verb is the command-line parity of the brain's HTTP management API;
stores are injected over a temp brain DB so a verb's writes are visible to the next call.
"""

from __future__ import annotations

import json

from newsroom.cleanse import ArticleTopics
from newsroom.cli import _topics_main, main
from newsroom.db import open_db
from newsroom.editorial import (
    LocationStore,
    Portal,
    PortalStore,
    StyleGuide,
    StyleStore,
)
from newsroom.personas import Persona, PersonaStore


def test_cli_sources_add_list_update_disable_remove(tmp_path, capsys):
    # The `sources` verb group curates the portal registry from the command line,
    # mirroring the HTTP management API. One injected store backs every call (the real
    # PortalStore over a brain DB), so the verbs see each other's writes.
    store = PortalStore(open_db(tmp_path / "brain.db", check_same_thread=False))

    # add: a URL normalizes + derives the id, and the description rides along.
    code = main(
        ["sources", "add", "--domain", "https://www.clarin.com/", "--description", "Diario"],
        portal_store=store,
    )
    assert code == 0
    added = json.loads(capsys.readouterr().out)
    assert added["id"] == "clarin-com" and added["domain"] == "clarin.com"
    assert added["description"] == "Diario" and added["enabled"] is True

    # list: shows the source, total is right.
    assert main(["sources", "list"], portal_store=store) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["total"] == 1 and listed["portals"][0]["id"] == "clarin-com"

    # update: changes the description (and only that).
    assert main(
        ["sources", "update", "clarin-com", "--description", "Grupo Clarin"], portal_store=store
    ) == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["description"] == "Grupo Clarin"

    # disable: flips enabled.
    assert main(["sources", "disable", "clarin-com"], portal_store=store) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False

    # remove: deletes; a second remove reports nothing removed and exits non-zero.
    assert main(["sources", "remove", "clarin-com"], portal_store=store) == 0
    assert json.loads(capsys.readouterr().out)["removed"] is True
    assert main(["sources", "remove", "clarin-com"], portal_store=store) == 1
    assert json.loads(capsys.readouterr().out)["removed"] is False


def test_cli_sources_duplicate_domain_is_an_error(tmp_path, capsys):
    # A duplicate domain is rejected by the store and surfaced as a non-zero exit with a
    # JSON error, not a traceback.
    store = PortalStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    assert main(["sources", "add", "--domain", "clarin.com"], portal_store=store) == 0
    capsys.readouterr()
    code = main(["sources", "add", "--domain", "https://www.clarin.com/x"], portal_store=store)
    assert code == 1
    assert "already exists" in json.loads(capsys.readouterr().out)["error"]


def test_cli_sources_enable_happy_path(tmp_path, capsys):
    # `sources enable` flips a disabled source back on and prints the updated record.
    store = PortalStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    assert main(
        ["sources", "add", "--domain", "clarin.com", "--disabled"], portal_store=store
    ) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False

    assert main(["sources", "enable", "clarin-com"], portal_store=store) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is True


def test_cli_sources_verbs_on_unknown_id_exit_1_with_a_json_error(tmp_path, capsys):
    # update / enable / disable on an id that does not exist are surfaced as a JSON error
    # and a non-zero exit, never a traceback. The store has no such id.
    store = PortalStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    for argv in (
        ["sources", "update", "ghost", "--description", "x"],
        ["sources", "enable", "ghost"],
        ["sources", "disable", "ghost"],
    ):
        assert main(argv, portal_store=store) == 1
        assert "ghost" in json.loads(capsys.readouterr().out)["error"]


def test_cli_sources_unknown_subverb_exits_1_with_usage(tmp_path, capsys):
    # An unknown `sources` sub-verb prints a JSON error (not a usage crash) and exits 1.
    store = PortalStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    assert main(["sources", "teleport"], portal_store=store) == 1
    assert "teleport" in json.loads(capsys.readouterr().out)["error"]


def test_cli_sources_get_returns_one_source(tmp_path, capsys):
    # `sources get <id>` reads a single source by id, the CLI parity of GET /portals/{id}.
    store = PortalStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    assert main(["sources", "add", "--domain", "clarin.com"], portal_store=store) == 0
    capsys.readouterr()

    assert main(["sources", "get", "clarin-com"], portal_store=store) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["id"] == "clarin-com" and got["domain"] == "clarin.com"


def test_cli_sources_get_unknown_id_exits_1_with_a_json_error(tmp_path, capsys):
    # `sources get` on an unknown id is the CLI parity of the route's 404: a JSON error and
    # a non-zero exit, never a traceback.
    store = PortalStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    assert main(["sources", "get", "ghost"], portal_store=store) == 1
    assert "ghost" in json.loads(capsys.readouterr().out)["error"]


def test_cli_authors_add_get_list_update_remove(tmp_path, capsys):
    # The `authors` verb group curates the persona registry from the command line WITHOUT
    # a synthesis job, mirroring the HTTP management API. One injected store backs every
    # call (the real PersonaStore over a brain DB), so the verbs see each other's writes.
    store = PersonaStore(open_db(tmp_path / "brain.db", check_same_thread=False))

    # add: the id derives from the display name; required identity fields ride along.
    code = main(
        [
            "authors", "add",
            "--display-name", "Ada Lovelace",
            "--beat", "tech",
            "--who-i-am", "I cover chips.",
            "--style", "dry",
            "--about", "A bio.",
            "--sources", "clarin-com, lanacion-com",
        ],
        persona_store=store,
    )
    assert code == 0
    added = json.loads(capsys.readouterr().out)
    assert added["id"] == "ada-lovelace" and added["display_name"] == "Ada Lovelace"
    assert added["about"] == "A bio." and added["sources"] == ["clarin-com", "lanacion-com"]
    assert added["active"] is True

    # get: reads the row back by id.
    assert main(["authors", "get", "ada-lovelace"], persona_store=store) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["who_i_am"] == "I cover chips."

    # list: shows the author, total is right.
    assert main(["authors", "list"], persona_store=store) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["total"] == 1 and listed["personas"][0]["id"] == "ada-lovelace"

    # update: changes the bio and the beat (and only those).
    assert main(
        ["authors", "update", "ada-lovelace", "--about", "New bio.", "--beat", "world"],
        persona_store=store,
    ) == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["about"] == "New bio." and updated["beat"] == "world"
    assert updated["style"] == "dry"  # untouched

    # update --no-active soft-deactivates; the active filter then hides it.
    assert main(["authors", "update", "ada-lovelace", "--no-active"], persona_store=store) == 0
    assert json.loads(capsys.readouterr().out)["active"] is False
    assert main(["authors", "list"], persona_store=store) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0  # active-only by default
    assert main(["authors", "list", "--include-inactive"], persona_store=store) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 1

    # remove: deletes; a second remove reports nothing removed and exits non-zero.
    assert main(["authors", "remove", "ada-lovelace"], persona_store=store) == 0
    assert json.loads(capsys.readouterr().out)["removed"] is True
    assert main(["authors", "remove", "ada-lovelace"], persona_store=store) == 1
    assert json.loads(capsys.readouterr().out)["removed"] is False


def test_cli_authors_explicit_id_is_honored(tmp_path, capsys):
    # --id overrides the derived slug, so an operator can pin a stable author handle.
    store = PersonaStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    code = main(
        ["authors", "add", "--display-name", "Ada", "--beat", "tech",
         "--who-i-am", "x", "--style", "y", "--id", "ada-custom"],
        persona_store=store,
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["id"] == "ada-custom"


def test_cli_authors_duplicate_id_is_an_error(tmp_path, capsys):
    # A duplicate id is rejected by the store and surfaced as a non-zero exit with a JSON
    # error, not a traceback.
    store = PersonaStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    args = ["authors", "add", "--display-name", "Ada", "--beat", "tech",
            "--who-i-am", "x", "--style", "y"]
    assert main(args, persona_store=store) == 0
    capsys.readouterr()
    code = main(args, persona_store=store)
    assert code == 1
    assert "already exists" in json.loads(capsys.readouterr().out)["error"]


def test_cli_authors_invalid_beat_is_an_error(tmp_path, capsys):
    # An invalid beat (outside the section enum) is a store rejection: a JSON error and a
    # non-zero exit, never a crash.
    store = PersonaStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    code = main(
        ["authors", "add", "--display-name", "Ada", "--beat", "gossip",
         "--who-i-am", "x", "--style", "y"],
        persona_store=store,
    )
    assert code == 1
    assert "invalid beat" in json.loads(capsys.readouterr().out)["error"]


def test_cli_authors_verbs_on_unknown_id_exit_1_with_a_json_error(tmp_path, capsys):
    # get / update / remove on an id that does not exist are surfaced as a JSON error and a
    # non-zero exit, never a traceback. The store has no such id.
    store = PersonaStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    for argv in (
        ["authors", "get", "ghost"],
        ["authors", "update", "ghost", "--about", "x"],
    ):
        assert main(argv, persona_store=store) == 1
        assert "ghost" in json.loads(capsys.readouterr().out)["error"]
    # remove of a missing id is not an error message, but it does exit non-zero.
    assert main(["authors", "remove", "ghost"], persona_store=store) == 1
    assert json.loads(capsys.readouterr().out)["removed"] is False


def test_cli_authors_unknown_subverb_exits_1_with_usage(tmp_path, capsys):
    # An unknown `authors` sub-verb prints a JSON error (not a usage crash) and exits 1.
    store = PersonaStore(open_db(tmp_path / "brain.db", check_same_thread=False))
    assert main(["authors", "teleport"], persona_store=store) == 1
    assert "teleport" in json.loads(capsys.readouterr().out)["error"]


def _link_stores(tmp_path):
    """A persona store and a portal store over ONE brain DB connection, pre-seeded with a
    persona and two portals, for the `authors sources` linking tests."""
    conn = open_db(tmp_path / "brain.db", check_same_thread=False)
    persona_store = PersonaStore(conn)
    portal_store = PortalStore(conn)
    persona_store.create(
        Persona(id="ada", display_name="Ada", beat="tech", who_i_am="I cover chips.", style="dry")
    )
    portal_store.create(Portal(domain="clarin.com"))
    portal_store.create(Portal(domain="lanacion.com"))
    return persona_store, portal_store


def test_cli_authors_sources_get_set_add_remove(tmp_path, capsys):
    # The `authors sources` verb group curates an author's per-author source pool from the
    # command line, the CLI parity of the HTTP linking surface. Both stores back the calls.
    persona_store, portal_store = _link_stores(tmp_path)

    def _run(args):
        assert main(args, persona_store=persona_store, portal_store=portal_store) == 0
        return json.loads(capsys.readouterr().out)

    # get: the pool starts empty.
    assert _run(["authors", "sources", "get", "ada"]) == {
        "persona_id": "ada", "sources": [], "portals": []
    }

    # set: replaces the pool from --sources, validated, and resolves to the portals.
    set_out = _run(["authors", "sources", "set", "ada", "--sources", "clarin-com, lanacion-com"])
    assert set_out["sources"] == ["clarin-com", "lanacion-com"]
    assert [p["id"] for p in set_out["portals"]] == ["clarin-com", "lanacion-com"]

    # get reflects the set, and the persona's own `sources` field carries the same ids.
    assert _run(["authors", "sources", "get", "ada"])["sources"] == ["clarin-com", "lanacion-com"]
    assert persona_store.get("ada").sources == ["clarin-com", "lanacion-com"]

    # remove unlinks one; add links it back (idempotent, appended after the survivor).
    assert _run(["authors", "sources", "remove", "ada", "clarin-com"])["sources"] == ["lanacion-com"]
    assert _run(["authors", "sources", "add", "ada", "clarin-com"])["sources"] == [
        "lanacion-com", "clarin-com"
    ]


def test_cli_authors_sources_set_unknown_id_is_an_error_and_writes_nothing(tmp_path, capsys):
    # `set` with an id not in the portal registry is a non-zero exit with a JSON error that
    # names the id, and it must not partially write the pool.
    persona_store, portal_store = _link_stores(tmp_path)
    code = main(
        ["authors", "sources", "set", "ada", "--sources", "clarin-com,ghost-com"],
        persona_store=persona_store, portal_store=portal_store,
    )
    assert code == 1
    assert "ghost-com" in json.loads(capsys.readouterr().out)["error"]
    assert persona_store.get("ada").sources == []  # nothing written


def test_cli_authors_sources_add_unknown_portal_is_an_error(tmp_path, capsys):
    # `add` of a portal id that does not exist is rejected (non-zero exit, JSON error).
    persona_store, portal_store = _link_stores(tmp_path)
    code = main(
        ["authors", "sources", "add", "ada", "ghost-com"],
        persona_store=persona_store, portal_store=portal_store,
    )
    assert code == 1
    assert "ghost-com" in json.loads(capsys.readouterr().out)["error"]


def test_cli_authors_sources_remove_is_idempotent(tmp_path, capsys):
    # Unlinking an absent source is a success (exit 0), not an error.
    persona_store, portal_store = _link_stores(tmp_path)
    code = main(
        ["authors", "sources", "remove", "ada", "clarin-com"],
        persona_store=persona_store, portal_store=portal_store,
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["sources"] == []


def test_cli_authors_sources_on_unknown_author_is_an_error(tmp_path, capsys):
    # Every `authors sources` verb errors (exit 1) on an unknown persona id.
    persona_store, portal_store = _link_stores(tmp_path)
    for argv in (
        ["authors", "sources", "get", "ghost"],
        ["authors", "sources", "set", "ghost", "--sources", "clarin-com"],
        ["authors", "sources", "add", "ghost", "clarin-com"],
        ["authors", "sources", "remove", "ghost", "clarin-com"],
    ):
        assert main(argv, persona_store=persona_store, portal_store=portal_store) == 1
        assert "ghost" in json.loads(capsys.readouterr().out)["error"]


def test_cli_authors_sources_unknown_subverb_exits_1(tmp_path, capsys):
    # An unknown `authors sources` sub-verb prints a JSON error and exits 1.
    persona_store, portal_store = _link_stores(tmp_path)
    code = main(
        ["authors", "sources", "teleport"],
        persona_store=persona_store, portal_store=portal_store,
    )
    assert code == 1
    assert "teleport" in json.loads(capsys.readouterr().out)["error"]


# ----- editorial config (house style + location) -----


def _editorial_stores(tmp_path):
    """A StyleStore and a LocationStore over one brain DB connection, for the `editorial`
    verb tests. Both empty (no seeded production data)."""
    conn = open_db(tmp_path / "brain.db", check_same_thread=False)
    return StyleStore(conn), LocationStore(conn)


def _full_style_json() -> str:
    return json.dumps(
        {
            "voice": "Sober, attribute every claim.",
            "rules": [{"id": "attribute", "text": "Atribui.", "scope": "both"}],
            "lexicon": {"banned_terms": ["demoledor"], "preferred_swaps": {"polemico": "discutido"}},
            "sourcing": {"min_sources": 2, "require_attribution": True},
            "structure": {"headline": "breve"},
        }
    )


def test_cli_editorial_style_set_get_versions_promote(tmp_path, capsys):
    # The `editorial style` group reads/edits the VERSIONED house style. set publishes a
    # new active version; versions lists the history; promote is rollback.
    style_store, location_store = _editorial_stores(tmp_path)

    def _run(args):
        assert main(args, style_store=style_store, location_store=location_store) == 0
        return json.loads(capsys.readouterr().out)

    # set v1: publishes and auto-activates.
    v1 = _run(["editorial", "style", "set", "--json", _full_style_json(), "--created-by", "hector"])
    assert v1["version"] == 1 and v1["is_active"] is True and v1["created_by"] == "hector"

    # get: the active version round-trips the JSON fields.
    got = _run(["editorial", "style", "get"])
    assert got["version"] == 1 and got["lexicon"]["banned_terms"] == ["demoledor"]
    assert got["sourcing"]["min_sources"] == 2

    # set v2 with a different voice; it becomes active.
    doc2 = json.loads(_full_style_json())
    doc2["voice"] = "v2 voice"
    v2 = _run(["editorial", "style", "set", "--json", json.dumps(doc2)])
    assert v2["version"] == 2 and v2["is_active"] is True
    assert _run(["editorial", "style", "get"])["voice"] == "v2 voice"

    # versions: newest-first with exactly one active.
    versions = _run(["editorial", "style", "versions"])
    assert versions["total"] == 2
    assert [v["version"] for v in versions["versions"]] == [2, 1]

    # promote v1 = rollback.
    assert _run(["editorial", "style", "promote", "1"])["version"] == 1
    assert _run(["editorial", "style", "get"])["voice"].startswith("Sober")


def test_cli_editorial_style_get_on_empty_is_an_error(tmp_path, capsys):
    style_store, location_store = _editorial_stores(tmp_path)
    code = main(["editorial", "style", "get"], style_store=style_store, location_store=location_store)
    assert code == 1
    assert "no active style guide" in json.loads(capsys.readouterr().out)["error"]


def test_cli_editorial_style_promote_unknown_version_is_an_error(tmp_path, capsys):
    style_store, location_store = _editorial_stores(tmp_path)
    style_store.add_version(StyleGuide(voice="v1"), activate=True)
    code = main(
        ["editorial", "style", "promote", "99"],
        style_store=style_store, location_store=location_store,
    )
    assert code == 1
    assert "99" in json.loads(capsys.readouterr().out)["error"]


def test_cli_editorial_lexicon_get_set(tmp_path, capsys):
    # lexicon get/set: set derives a new active version with only the lexicon replaced.
    style_store, location_store = _editorial_stores(tmp_path)
    main(["editorial", "style", "set", "--json", _full_style_json()],
         style_store=style_store, location_store=location_store)
    capsys.readouterr()

    def _run(args):
        assert main(args, style_store=style_store, location_store=location_store) == 0
        return json.loads(capsys.readouterr().out)

    assert _run(["editorial", "lexicon", "get"])["banned_terms"] == ["demoledor"]

    # --banned-terms replaces just the banned list on the active lexicon.
    set_out = _run(["editorial", "lexicon", "set", "--banned-terms", "letal, brutal"])
    assert set_out["banned_terms"] == ["letal", "brutal"]
    # The preferred_swaps survived (only banned_terms was replaced), on a new version.
    assert _run(["editorial", "lexicon", "get"])["preferred_swaps"] == {"polemico": "discutido"}
    assert _run(["editorial", "style", "get"])["version"] == 2


def test_cli_editorial_sourcing_set_min_sources_merges(tmp_path, capsys):
    # sourcing set --min-sources merges onto the active sourcing, keeping the other flags.
    style_store, location_store = _editorial_stores(tmp_path)
    main(["editorial", "style", "set", "--json", _full_style_json()],
         style_store=style_store, location_store=location_store)
    capsys.readouterr()

    code = main(
        ["editorial", "sourcing", "set", "--min-sources", "3"],
        style_store=style_store, location_store=location_store,
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["min_sources"] == 3
    assert out["require_attribution"] is True  # merged, not clobbered

    assert main(["editorial", "sourcing", "get"],
                style_store=style_store, location_store=location_store) == 0
    assert json.loads(capsys.readouterr().out)["min_sources"] == 3


def test_cli_editorial_location_get_set(tmp_path, capsys):
    # location get returns the usable default; set applies only the named fields.
    style_store, location_store = _editorial_stores(tmp_path)

    assert main(["editorial", "location", "get"],
                style_store=style_store, location_store=location_store) == 0
    default = json.loads(capsys.readouterr().out)
    assert (default["region"], default["language"]) == ("AR", "es")
    assert default["ceid"] == "AR:es-419"

    assert main(["editorial", "location", "set", "--region", "ES", "--city", "Madrid"],
                style_store=style_store, location_store=location_store) == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["region"] == "ES" and updated["city"] == "Madrid"
    assert updated["language"] == "es"  # untouched
    assert updated["gl"] == "ES"


def test_cli_editorial_location_set_blank_required_is_an_error(tmp_path, capsys):
    style_store, location_store = _editorial_stores(tmp_path)
    code = main(
        ["editorial", "location", "set", "--region", "   "],
        style_store=style_store, location_store=location_store,
    )
    assert code == 1
    assert "region is required" in json.loads(capsys.readouterr().out)["error"]


def test_cli_editorial_unknown_group_and_subverb_exit_1(tmp_path, capsys):
    style_store, location_store = _editorial_stores(tmp_path)
    assert main(["editorial", "teleport"],
                style_store=style_store, location_store=location_store) == 1
    assert "teleport" in json.loads(capsys.readouterr().out)["error"]
    assert main(["editorial", "style", "teleport"],
                style_store=style_store, location_store=location_store) == 1
    assert "teleport" in json.loads(capsys.readouterr().out)["error"]


# ----- topics cleanse (the agent-supplied map-file lane, no inference) -----


def test_cli_topics_cleanse_map_file_dry_run_plans_the_remap(tmp_path, capsys):
    # `topics cleanse --map-file` is the non-LLM lane: a CLI agent supplies the canonical
    # {tag: canonical} map, and the verb fetches the corpus and PLANS the remap (dry-run by
    # default, writing nothing). The fetch + apply seams are injected so no network is hit.
    articles = [
        ArticleTopics(slug="a", topics=["ia", "inteligencia-artificial"]),
        ArticleTopics(slug="b", topics=["gpu"]),
    ]
    mapping = {"ia": "inteligencia artificial", "inteligencia-artificial": "inteligencia artificial"}
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps(mapping), encoding="utf-8")
    applied: list = []

    code = _topics_main(
        ["cleanse", "--map-file", str(map_file)],
        fetch=lambda: articles,
        apply=lambda plan: (applied.extend(plan), (len(plan), []))[1],
    )

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["mapping"]["ia"] == "inteligencia artificial"
    # Article a's two synonymous tags collapse to one canonical (it changes); b is untouched.
    changed = {p["slug"] for p in out["plan"]}
    assert changed == {"a"}
    assert out["plan"][0]["after"] == ["inteligencia artificial"]
    assert applied == []  # dry-run wrote nothing


def test_cli_topics_cleanse_map_file_apply_calls_the_apply_seam(tmp_path, capsys):
    # With --apply the verb runs the apply seam on the planned remap and reports the count.
    articles = [ArticleTopics(slug="a", topics=["ia"])]
    map_file = tmp_path / "m.json"
    map_file.write_text(json.dumps({"ia": "inteligencia artificial"}), encoding="utf-8")
    seen: dict = {}

    def fake_apply(plan):
        seen["n"] = len(plan)
        return len(plan), []

    code = _topics_main(
        ["cleanse", "--map-file", str(map_file), "--apply"],
        fetch=lambda: articles,
        apply=fake_apply,
    )

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is False and out["applied"] == 1 and out["failed"] == []
    assert seen["n"] == 1


def test_cli_topics_cleanse_without_a_map_is_an_identity_noop(tmp_path, capsys):
    # No --map-file means every tag maps to itself: the plan is empty (a no-op), so the
    # apply seam is never even reached.
    articles = [ArticleTopics(slug="a", topics=["ia", "gpu"])]
    called: list = []

    code = _topics_main(
        ["cleanse"],
        fetch=lambda: articles,
        apply=lambda plan: (called.append(1), (0, []))[1],
    )

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["plan"] == [] and out["articles_changed"] == 0
    assert called == []  # dry-run, nothing applied
