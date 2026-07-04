"""The one-time personas.db -> platform move: the pure field map, the orchestration,
the read-back verify (against what was actually pushed), and the ``migrate`` CLI verb.

The HTTP is not exercised here (the wire body is covered in test_mirror_backfill.py); a
``FakeRegistry`` stands in for the platform, mirroring the backend's upsert semantics
faithfully (blank-field defaults, wholesale metadata replace, the source-join dedupe/trim,
join hydration, the tombstone flag) so the orchestration + verify are driven end to end
without a network.
"""

from __future__ import annotations

import json

from newsroom.cli import _migrate_main
from newsroom.editorial.portals import Portal, normalize_domain, portal_slug
from newsroom.migrate import (
    author_fields,
    has_content,
    norm_slugs,
    run_move,
    should_tombstone,
    source_fields,
    verify_move,
)
from newsroom.personas.store import Persona


def _persona(pid: str, **over) -> Persona:
    base = dict(
        id=pid,
        display_name=pid.replace("-", " ").title(),
        beat="politics",
        who_i_am=f"Soy {pid}, prompt privado.",
        style="Frases declarativas, presente.",
        about=f"Bio pública de {pid}.",
        language="español rioplatense",
        gender="femenino",
        few_shots_pos=[f"{pid} bueno 1", f"{pid} bueno 2"],
        few_shots_neg=[f"{pid} malo 1"],
        sources=["clarin-com", "pagina12-com-ar"],
        profile_topics=["politica", "economia"],
        avatar_path=f"/media/{pid}.png",
        active=True,
    )
    base.update(over)
    return Persona(**base)


def _empty_shell(pid: str) -> Persona:
    """An inactive persona with NO content (the case that is safe to skip)."""
    return Persona(
        id=pid, display_name=pid.title(), beat="politics", who_i_am="", style="",
        about="", few_shots_pos=[], few_shots_neg=[], sources=[], profile_topics=[],
        active=False,
    )


def _portal(domain: str, **over) -> Portal:
    base = dict(domain=domain, id=portal_slug(domain), lean="neutral", feed_type="auto")
    base.update(over)
    return Portal(**base)


def _two_portals() -> list[Portal]:
    return [Portal(domain="clarin-com", id="clarin-com", lean="right"),
            Portal(domain="pagina12-com-ar", id="pagina12-com-ar", lean="left")]


# ----- a faithful in-memory stand-in for the platform registry -----


class FakeRegistry:
    """Mimics the backend's author/source upsert: blank fields take the server default,
    metadata is replaced wholesale, an author's source join is deduped/trimmed then hydrated
    back on read, and a delete flips the tombstone flag without dropping the row."""

    def __init__(self, *, drop_author_field: str | None = None):
        self.authors: dict = {}
        self.sources: dict = {}
        self.joins: dict = {}
        self.article_counts: dict = {}
        self._drop = drop_author_field  # simulate a lossy backend for the hard-fail test
        self.source_calls: list = []
        self.author_calls: list = []

    def seed_author(self, handle: str, **row) -> None:
        self.authors[handle] = {"handle": handle, "metadata": {}, "deleted": False, **row}

    # seams passed to run_move (they receive the field-map kwargs directly)
    def push_source(self, kw):
        from newsroom.mirror.client import PushResult

        self.source_calls.append(kw)
        domain = normalize_domain(kw["domain"])
        slug = kw.get("slug") or portal_slug(domain)
        self.sources[slug] = {
            "slug": slug,
            "domain": domain,
            "homepage": kw.get("homepage", ""),
            "description": kw.get("description", ""),
            "feed_urls": list(kw.get("feed_urls") or []),
            "feed_type": kw.get("feed_type") or "auto",
            "language": kw.get("language") or "es",
            "ownership_group": kw.get("ownership_group", ""),
            "lean": kw.get("lean") or "neutral",
            "enabled": kw["enabled"] if kw.get("enabled") is not None else True,
            "status": kw.get("status") or "unknown",
            "last_checked": kw.get("last_checked", ""),
            "last_ok": kw.get("last_ok", ""),
            "metadata": dict(kw.get("metadata") or {}),
            "deleted": False,
        }
        return PushResult(handle=slug, ok=True, status=200)

    def push_author(self, kw):
        from newsroom.mirror.client import PushResult

        self.author_calls.append(kw)
        handle = kw["handle"]
        row = {
            "handle": handle,
            "name": kw.get("name", ""),
            "bio": kw.get("bio", ""),
            "avatar": kw.get("avatar", ""),
            "gender": kw.get("gender", ""),
            "about": kw.get("about", ""),
            "style": kw.get("style", ""),
            "topics": list(kw.get("topics") or []),
            "metadata": dict(kw.get("metadata") or {}),  # replaced wholesale
            "deleted": False,  # an upsert clears any tombstone
        }
        if self._drop:
            row[self._drop] = ""  # a lossy backend silently dropped this column
        self.authors[handle] = row
        if kw.get("sources") is not None:  # the join is deduped/trimmed like the backend does
            self.joins[handle] = norm_slugs(kw["sources"])
        return PushResult(handle=handle, ok=True, status=200)

    def tombstone(self, handle):
        from newsroom.mirror.client import PushResult

        if handle in self.authors:
            self.authors[handle]["deleted"] = True
        return PushResult(handle=handle, ok=True, status=204)

    def count_articles(self, handle):
        return self.article_counts.get(handle, 0)

    def read_authors(self):
        out = []
        for handle, row in self.authors.items():
            r = dict(row)
            r["sources"] = sorted(self.joins.get(handle, []))
            out.append(r)
        return out

    def read_sources(self):
        return [dict(s) for s in self.sources.values()]

    def seams(self) -> dict:
        return {
            "push_source": self.push_source,
            "push_author": self.push_author,
            "tombstone": self.tombstone,
            "read_authors": self.read_authors,
            "read_sources": self.read_sources,
            "count_articles": self.count_articles,
        }


# ----- pure field maps -----


def test_author_fields_is_the_full_column_map():
    p = _persona("lara-arianna", gender="femenino")
    f = author_fields(p)
    assert f["handle"] == "lara-arianna"
    assert f["name"] == "Lara Arianna"
    # about lands in BOTH bio and about (layer 1 renders bio; about is the promoted column).
    assert f["bio"] == p.about and f["about"] == p.about
    assert f["style"] == p.style and f["gender"] == "femenino" and f["avatar"] == p.avatar_path
    # profile_topics is first-class AND kept in metadata for the layer-1 overlay.
    assert f["topics"] == ["politica", "economia"]
    assert f["metadata"]["profile_topics"] == ["politica", "economia"]
    # the private tail is in metadata, few-shots as JSON arrays.
    assert f["metadata"]["who_i_am"] == p.who_i_am
    assert f["metadata"]["beat"] == "politics"
    assert f["metadata"]["language"] == "español rioplatense"
    assert f["metadata"]["few_shots_pos"] == p.few_shots_pos
    assert f["metadata"]["few_shots_neg"] == p.few_shots_neg
    # the source set rides as the join pointer.
    assert f["sources"] == ["clarin-com", "pagina12-com-ar"]


def test_author_fields_omits_profile_topics_metadata_when_uncurated():
    f = author_fields(_persona("ada", profile_topics=[]))
    assert f["topics"] == []
    assert "profile_topics" not in f["metadata"]


def test_author_fields_normalizes_source_slugs():
    f = author_fields(_persona("ada", sources=[" clarin-com ", "clarin-com", "", "pagina12-com-ar"]))
    assert f["sources"] == ["clarin-com", "pagina12-com-ar"]  # trimmed, de-duped, blanks dropped


def test_source_fields_sends_slug_and_enabled_explicitly():
    f = source_fields(_portal("infowars.com", lean="right", enabled=False))
    assert f["slug"] == "infowars-com"  # the exact id the author join resolves against
    assert f["enabled"] is False        # explicit, so the platform default (true) cannot re-enable
    assert f["lean"] == "right" and f["feed_type"] == "auto"


def test_should_tombstone_policy():
    assert should_tombstone(_persona("retired", active=False, who_i_am="Prompt viejo.")) is True
    assert should_tombstone(_empty_shell("shell")) is False
    assert should_tombstone(_persona("live", active=True)) is False
    # an inactive persona with content but a BLANK who_i_am is still carried (not skipped).
    assert has_content(_persona("x", active=False, who_i_am="")) is True
    assert should_tombstone(_persona("x", active=False, who_i_am="")) is True


# ----- orchestration + verify -----


def test_run_move_seeds_sources_then_authors_and_verifies_full_round_trip():
    # lara's sources are in REVERSED order vs the backend's sorted echo, so this also
    # proves the join verify is order-independent (author_sources is a set).
    personas = [_persona("lara-arianna", sources=["pagina12-com-ar", "clarin-com"]),
                _persona("vector-omni", beat="tech", gender="masculino")]
    portals = _two_portals()
    reg = FakeRegistry()

    report = run_move(personas, portals, base_url="http://web", token="tok", **reg.seams())

    assert report.ok is True, report.discrepancies + [str(f) for f in report.failures]
    assert report.discrepancies == []
    assert report.failures == []
    # sources were pushed before any author (the join must resolve against existing rows).
    assert reg.source_calls and reg.author_calls
    assert set(report.sources_pushed) == {"clarin-com", "pagina12-com-ar"}
    assert set(report.authors_pushed) == {"lara-arianna", "vector-omni"}
    # the join hydrated back onto each author.
    assert sorted(reg.joins["lara-arianna"]) == ["clarin-com", "pagina12-com-ar"]


def test_run_move_tombstones_retired_identity_and_skips_empty_shell():
    personas = [
        _persona("live-one"),
        _persona("retired-one", active=False, who_i_am="Prompt privado retirado."),
        _empty_shell("blank-shell"),
    ]
    reg = FakeRegistry()

    report = run_move(personas, _two_portals(), base_url="http://web", token="tok", **reg.seams())

    assert report.ok is True, report.discrepancies + [str(f) for f in report.failures]
    assert report.tombstoned == ["retired-one"]
    assert report.skipped_inactive == ["blank-shell"]
    assert reg.authors["retired-one"]["deleted"] is True
    assert "blank-shell" not in reg.authors


def test_run_move_leaves_a_retired_persona_live_when_its_backend_author_has_articles():
    personas = [_persona("has-articles", active=False, who_i_am="Retirado localmente.")]
    reg = FakeRegistry()
    reg.article_counts["has-articles"] = 5  # the handle still has published content

    report = run_move(personas, _two_portals(), base_url="http://web", token="tok", **reg.seams())

    assert report.ok is True, report.discrepancies
    assert report.tombstoned == []
    assert "has-articles" in report.authors_pushed
    assert reg.authors["has-articles"]["deleted"] is False  # not hidden
    assert any("published articles" in w for w in report.warnings)


def test_run_move_warns_on_backend_only_metadata_it_would_overwrite():
    personas = [_persona("lara-arianna")]
    reg = FakeRegistry()
    reg.seed_author("lara-arianna", metadata={"beat": "politics", "twitter": "@lara"})

    report = run_move(personas, _two_portals(), base_url="http://web", token="tok", **reg.seams())

    assert any("twitter" in w and "OVERWRITE" in w for w in report.warnings)
    # the move still completes (personas.db is authoritative); the warning is non-fatal.
    assert report.ok is True, report.discrepancies


def test_run_move_is_idempotent_on_rerun():
    personas = [_persona("lara-arianna")]
    reg = FakeRegistry()

    first = run_move(personas, _two_portals(), base_url="http://web", token="tok", **reg.seams())
    second = run_move(personas, _two_portals(), base_url="http://web", token="tok", **reg.seams())

    assert first.ok is True and second.ok is True
    assert len(reg.authors) == 1 and len(reg.sources) == 2  # keyed on handle/slug, no dupes


def test_run_move_hard_fails_when_the_backend_drops_a_field():
    personas = [_persona("lara-arianna")]
    reg = FakeRegistry(drop_author_field="style")  # the voice silently lost on write

    report = run_move(personas, _two_portals(), base_url="http://web", token="tok", **reg.seams())

    assert report.ok is False
    assert any("style" in d for d in report.discrepancies)


def test_run_move_hard_fails_on_a_dangling_source_link():
    # the persona references a source slug that has NO matching portal.
    personas = [_persona("lara-arianna", sources=["clarin-com", "ghost-outlet-com"])]
    reg = FakeRegistry()

    report = run_move(personas, [Portal(domain="clarin-com", id="clarin-com")],
                      base_url="http://web", token="tok", **reg.seams())

    assert report.ok is False
    assert any("ghost-outlet-com" in d and "no registered source" in d for d in report.discrepancies)


def test_run_move_hard_fails_when_metadata_gains_a_stray_key():
    personas = [_persona("lara-arianna")]
    reg = FakeRegistry()
    # a backend that merges instead of replacing would leave a stale key; full-equality catches it.
    orig = reg.push_author

    def merging_push(kw):
        res = orig(kw)
        reg.authors[kw["handle"]]["metadata"]["stale"] = "leftover"
        return res

    seams = reg.seams()
    seams["push_author"] = merging_push
    report = run_move(personas, _two_portals(), base_url="http://web", token="tok", **seams)

    assert report.ok is False
    assert any("metadata" in d for d in report.discrepancies)


def test_verify_move_flags_a_missing_source():
    expected_sources = {"clarin-com": source_fields(Portal(domain="clarin-com", id="clarin-com"))}
    problems = verify_move(expected_sources, {}, {}, sources_by_slug={})
    assert any("clarin-com" in p and "missing" in p for p in problems)


# ----- the migrate CLI verb -----


def test_cli_migrate_dry_run_lists_without_contacting(capsys):
    personas = [_persona("live-one"), _empty_shell("shell")]
    portals = [Portal(domain="clarin-com", id="clarin-com")]

    code = _migrate_main(["--dry-run"], personas=personas, portals=portals)

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["ok"] is True
    assert out["sources_pushed"] == ["clarin-com"]
    assert out["authors_pushed"] == ["live-one"]
    assert out["skipped_inactive"] == ["shell"]


def test_cli_migrate_returns_zero_on_a_clean_move(monkeypatch, capsys):
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "tok-admin")
    personas = [_persona("lara-arianna")]
    reg = FakeRegistry()

    code = _migrate_main([], personas=personas, portals=_two_portals(), seams=reg.seams())

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["discrepancies"] == []


def test_cli_migrate_returns_nonzero_when_verify_fails(monkeypatch, capsys):
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "tok-admin")
    personas = [_persona("lara-arianna")]
    reg = FakeRegistry(drop_author_field="style")

    code = _migrate_main([], personas=personas, portals=_two_portals(), seams=reg.seams())

    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["discrepancies"]


def test_cli_migrate_without_token_fails(monkeypatch, capsys):
    monkeypatch.delenv("NEWSROOM_OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("NEWSROOM_ADMIN_TOKEN", raising=False)
    personas = [_persona("lara-arianna")]
    portals = [Portal(domain="clarin-com", id="clarin-com")]

    code = _migrate_main([], personas=personas, portals=portals)

    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert "token" in out["error"]
