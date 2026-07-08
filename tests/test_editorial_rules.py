"""Tests for the `editorial-rules` verb: it reads the backend editorial catalog and renders
the per-language writing anchors, and with --bot-directive prints only the bot directive.
The backend HTTP call is stubbed by monkeypatching the module's `api`, so no stack is needed."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli" / "censurado.py"


def _load():
    spec = importlib.util.spec_from_file_location("censurado_editorial", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cz = _load()


def _stub_api(entries, want_lang="es"):
    # Assert the verb built the right request (GET /editorial-text?lang=<lang>) rather than
    # ignoring it, so the URL construction in _editorial_rows stays covered.
    body = json.dumps({"scope": "editorial", "lang": want_lang, "entries": entries}).encode()

    def _api(method, path, *a, **k):
        assert method == "GET", method
        assert path == f"/editorial-text?lang={want_lang}", path
        return (200, body)

    return _api


def test_render_excludes_the_bot_directive():
    # The rendered anchors block is for article writing; the bot directive (a bridge concern) is
    # never shown there even though it lives in the same scope.
    rows = {
        "lexicon.bans": json.dumps(["demoledor", "brutal"]),
        "lexicon.swaps": json.dumps({"polémico": "discutido"}),
        "attribution.example": "según X",
        "disclaimer.satire": "*Tómelo como un cuentillo de ciencia ficción.*",
        "bot.directive": "respondé en español rioplatense",
    }
    out = cz.render_editorial_rules("es", rows)
    assert "demoledor" in out and "polémico -> discutido" in out
    assert "según X" in out and "cuentillo de ciencia" in out
    assert "respondé en español" not in out


def test_verb_fetches_and_renders_anchors(monkeypatch, capsys):
    monkeypatch.setattr(cz, "api", _stub_api([
        {"key": "lexicon.bans", "value": json.dumps(["demoledor"])},
        {"key": "slop.phrases", "value": json.dumps(["en el mundo actual"])},
        {"key": "bot.directive", "value": "respondé en español rioplatense"},
    ]))
    rc = cz.cmd_editorial_rules(SimpleNamespace(lang="es", bot_directive=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "demoledor" in out and "en el mundo actual" in out
    assert "español" not in out  # the bot directive is excluded from the anchors render


def test_bot_directive_flag_prints_only_the_directive(monkeypatch, capsys):
    monkeypatch.setattr(cz, "api", _stub_api([
        {"key": "lexicon.bans", "value": json.dumps(["demoledor"])},
        {"key": "bot.directive", "value": "respondé en español rioplatense"},
    ]))
    rc = cz.cmd_editorial_rules(SimpleNamespace(lang="es", bot_directive=True))
    out = capsys.readouterr().out
    assert rc == 0 and out.strip() == "respondé en español rioplatense"
    assert "demoledor" not in out  # only the directive, no anchors


def test_bot_directive_flag_prints_nothing_when_absent(monkeypatch, capsys):
    # An unseeded language: --bot-directive prints nothing (exit 0), so the bridge keeps its
    # built-in fallback instead of a stray line.
    monkeypatch.setattr(cz, "api", _stub_api([], want_lang="en"))
    rc = cz.cmd_editorial_rules(SimpleNamespace(lang="en", bot_directive=True))
    out = capsys.readouterr().out
    assert rc == 0 and out.strip() == ""


def test_only_bot_directive_still_warns_no_anchors(monkeypatch, capsys):
    # A language seeded with ONLY bot.directive has no writing anchors, so the verb must still warn
    # (bot.directive is a bridge concern, excluded from the anchors render) rather than print an
    # empty block that implies anchors exist.
    monkeypatch.setattr(cz, "api", _stub_api(
        [{"key": "bot.directive", "value": "respondé en español"}], want_lang="fr"))
    rc = cz.cmd_editorial_rules(SimpleNamespace(lang="fr", bot_directive=False))
    cap = capsys.readouterr()
    assert rc == 0 and "no editorial anchors" in cap.err and cap.out == ""


def test_render_tolerates_a_non_string_ban(monkeypatch, capsys):
    # A hand-edited catalog with a non-string list element must not crash the render (it is
    # coerced to text), honoring _decode_json's "never crashes the render" guarantee.
    monkeypatch.setattr(cz, "api", _stub_api(
        [{"key": "lexicon.bans", "value": json.dumps(["demoledor", 42])}]))
    rc = cz.cmd_editorial_rules(SimpleNamespace(lang="es", bot_directive=False))
    out = capsys.readouterr().out
    assert rc == 0 and "demoledor" in out and "42" in out


def test_empty_catalog_warns_but_succeeds(monkeypatch, capsys):
    # No anchors for the language: the verb notes it on stderr and exits 0 (the walk still runs
    # with the language-agnostic rules), rather than failing the whole walk.
    monkeypatch.setattr(cz, "api", _stub_api([], want_lang="de"))
    rc = cz.cmd_editorial_rules(SimpleNamespace(lang="de", bot_directive=False))
    err = capsys.readouterr().err
    assert rc == 0 and "no editorial anchors" in err
