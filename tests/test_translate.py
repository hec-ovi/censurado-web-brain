"""Tests for the `translate` verb: the one-time, per-language site localization loop.

Read mode diffs each text scope's base language against the target and prints a JSON job of only
the missing keys; --apply reads the completed job and upserts only the still-missing rows,
never overwriting an existing translation. The backend is stubbed by monkeypatching the module's
`api`, so no stack (and no real LLM) is needed: the "translation" is simulated by filling in the
job's `value` fields, exactly as the driving agent would."""
import importlib.util
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli" / "censurado.py"


def _load():
    spec = importlib.util.spec_from_file_location("censurado_translate", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cz = _load()

_PATH = re.compile(r"^/(frontend|panel|editorial)-text(?:\?lang=(.+))?$")


def _make_api(catalog, posts, get_status=200, post_status=200):
    """A fake `api` over an in-memory catalog `{(scope, lang): {key: value}}`. GET returns the
    scope+lang rows in the backend's wire shape; POST records the upsert AND reflects it into the
    catalog, so a re-read (and thus a second apply) sees it, exercising real idempotency. A
    non-200 `get_status`/`post_status` simulates a backend that is down or rejects the write (its
    body is a JSON dict WITHOUT `entries`, the shape that must still abort, not parse to empty)."""
    def _api(method, path, payload=None, *a, **k):
        m = _PATH.match(path)
        assert m, f"unexpected path {path!r}"
        scope, lang = m.group(1), (m.group(2) or "en")
        if method == "GET":
            if get_status != 200:
                return (get_status, json.dumps({"error": "down"}).encode())
            rows = catalog.get((scope, lang), {})
            entries = [{"key": key, "value": val} for key, val in rows.items()]
            return (200, json.dumps({"scope": scope, "lang": lang, "entries": entries}).encode())
        if method == "POST":
            if post_status != 200:
                return (post_status, json.dumps({"error": "boom"}).encode())
            posts.append((scope, payload["lang"], payload["key"], payload["value"]))
            catalog.setdefault((scope, payload["lang"]), {})[payload["key"]] = payload["value"]
            return (200, b"{}")
        raise AssertionError(f"unexpected method {method!r}")
    return _api


def _job_of(mod_out):
    return json.loads(mod_out)


def _read(monkeypatch, capsys, catalog, lang):
    posts = []
    monkeypatch.setattr(cz, "api", _make_api(catalog, posts))
    rc = cz.cmd_translate(SimpleNamespace(lang=lang, apply=False, file=""))
    out = capsys.readouterr()
    return rc, _job_of(out.out), out.err, posts


def _apply(monkeypatch, capsys, catalog, lang, job):
    posts = []
    monkeypatch.setattr(cz, "api", _make_api(catalog, posts))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(job)))
    rc = cz.cmd_translate(SimpleNamespace(lang=lang, apply=True, file="-"))
    err = capsys.readouterr().err
    return rc, err, posts


def _fill(job, fn=lambda scope, e: f"{scope}:{e['key']}"):
    """Simulate the agent filling every entry's `value` (the mocked 'LLM' step)."""
    for sc in job["scopes"]:
        for e in sc["entries"]:
            e["value"] = fn(sc["scope"], e)
    return job


def test_job_diffs_each_scope_and_carries_source_and_op(monkeypatch, capsys):
    catalog = {
        ("frontend", "en"): {"nav.world": "World", "nav.tech": "Technology", "cta.more": "Read more"},
        ("frontend", "pt"): {"nav.world": "Mundo"},  # already translated -> excluded
        ("panel", "en"): {"btn.save": "Save"},
        ("editorial", "es"): {"lexicon.bans": json.dumps(["demoledor"]), "attribution.example": "según X"},
    }
    rc, job, _err, _posts = _read(monkeypatch, capsys, catalog, "pt")
    assert rc == 0 and job["target_lang"] == "pt"
    byscope = {s["scope"]: s for s in job["scopes"]}

    assert byscope["frontend"]["op"] == "translate" and byscope["frontend"]["source_lang"] == "en"
    fkeys = {e["key"]: e for e in byscope["frontend"]["entries"]}
    assert set(fkeys) == {"nav.tech", "cta.more"}  # nav.world already present -> excluded
    assert fkeys["nav.tech"]["source"] == "Technology" and fkeys["nav.tech"]["value"] == ""

    assert byscope["panel"]["op"] == "translate"
    assert byscope["editorial"]["op"] == "generate" and byscope["editorial"]["source_lang"] == "es"
    ekeys = {e["key"]: e for e in byscope["editorial"]["entries"]}
    assert ekeys["lexicon.bans"]["source"] == json.dumps(["demoledor"])  # JSON structure preserved verbatim


def test_a_base_language_target_skips_its_own_base_scopes(monkeypatch, capsys):
    # Localizing INTO en: frontend/panel are base-en (skipped, nothing to translate into itself),
    # but editorial's base is es, so English editorial anchors ARE generated.
    catalog = {
        ("frontend", "en"): {"nav.world": "World"},
        ("panel", "en"): {"btn.save": "Save"},
        ("editorial", "es"): {"lexicon.bans": json.dumps(["demoledor"])},
    }
    _rc, job, _err, _posts = _read(monkeypatch, capsys, catalog, "en")
    scopes = {s["scope"] for s in job["scopes"]}
    assert scopes == {"editorial"}
    assert job["scopes"][0]["op"] == "generate"


def test_read_prints_valid_json_to_stdout(monkeypatch, capsys):
    catalog = {("frontend", "en"): {"a": "A"}}
    rc, job, _err, _posts = _read(monkeypatch, capsys, catalog, "de")
    assert rc == 0 and job["target_lang"] == "de"
    assert job["scopes"][0]["entries"][0]["key"] == "a"


def test_fully_translated_language_yields_empty_job_and_warns(monkeypatch, capsys):
    catalog = {
        ("frontend", "en"): {"a": "A"}, ("frontend", "pt"): {"a": "AA"},
        ("panel", "en"): {"b": "B"}, ("panel", "pt"): {"b": "BB"},
        ("editorial", "es"): {"c": "C"}, ("editorial", "pt"): {"c": "CC"},
    }
    rc, job, err, _posts = _read(monkeypatch, capsys, catalog, "pt")
    assert rc == 0 and job["scopes"] == []
    assert "nothing to localize" in err


def test_apply_upserts_only_missing_rows_with_the_target_lang(monkeypatch, capsys):
    catalog = {
        ("frontend", "en"): {"a": "A", "b": "B"},
        ("frontend", "pt"): {"a": "AA"},   # a already translated
        ("editorial", "es"): {"c": json.dumps(["x"])},
    }
    # First read the job, fill it, then apply against the SAME catalog.
    _rc, job, _err, _posts = _read(monkeypatch, capsys, catalog, "pt")
    _fill(job)
    rc, err, posts = _apply(monkeypatch, capsys, catalog, "pt", job)
    assert rc == 0
    # Only the missing keys are posted, each to its scope with lang=pt; "a" (already present) is not.
    assert ("frontend", "pt", "b", "frontend:b") in posts
    assert ("editorial", "pt", "c", "editorial:c") in posts
    assert not any(p[2] == "a" for p in posts)
    # read mode already dropped the present key "a" from the job, so apply sees nothing to skip.
    assert "wrote 2 new row(s), skipped 0 already-present" in err


def test_apply_never_overwrites_an_existing_translation(monkeypatch, capsys):
    # A hand-edited/pre-existing target row must survive an apply that carries a value for the same key.
    catalog = {("frontend", "en"): {"a": "A"}, ("frontend", "pt"): {"a": "HAND-EDITED"}}
    job = {"target_lang": "pt", "scopes": [
        {"scope": "frontend", "op": "translate", "source_lang": "en", "target_lang": "pt",
         "entries": [{"key": "a", "source": "A", "value": "ROBOT"}]}]}
    rc, err, posts = _apply(monkeypatch, capsys, catalog, "pt", job)
    assert rc == 0 and posts == []            # nothing posted
    assert catalog[("frontend", "pt")]["a"] == "HAND-EDITED"
    assert "skipped 1 already-present" in err


def test_apply_is_idempotent_on_a_second_run(monkeypatch, capsys):
    catalog = {("frontend", "en"): {"a": "A", "b": "B"}}
    _rc, job, _err, _posts = _read(monkeypatch, capsys, catalog, "pt")
    _fill(job)
    _rc1, _err1, posts1 = _apply(monkeypatch, capsys, catalog, "pt", job)
    assert len(posts1) == 2                    # first run writes both
    _rc2, err2, posts2 = _apply(monkeypatch, capsys, catalog, "pt", job)
    assert posts2 == []                        # second run writes nothing
    assert "wrote 0 new row(s), skipped 2 already-present" in err2


def test_apply_skips_blank_values(monkeypatch, capsys):
    catalog = {("frontend", "en"): {"a": "A", "b": "B"}}
    job = {"target_lang": "pt", "scopes": [
        {"scope": "frontend", "op": "translate", "source_lang": "en", "target_lang": "pt",
         "entries": [{"key": "a", "source": "A", "value": "AA"},
                     {"key": "b", "source": "B", "value": "   "}]}]}
    rc, err, posts = _apply(monkeypatch, capsys, catalog, "pt", job)
    assert rc == 0 and [p[2] for p in posts] == ["a"]
    assert "1 left blank" in err


def test_apply_skips_a_repeated_key_within_one_job(monkeypatch, capsys):
    catalog = {("frontend", "en"): {"a": "A"}}
    job = {"target_lang": "pt", "scopes": [
        {"scope": "frontend", "op": "translate", "source_lang": "en", "target_lang": "pt",
         "entries": [{"key": "a", "source": "A", "value": "first"},
                     {"key": "a", "source": "A", "value": "second"}]}]}
    rc, _err, posts = _apply(monkeypatch, capsys, catalog, "pt", job)
    assert rc == 0 and [p[3] for p in posts] == ["first"]   # the duplicate is skipped, not re-posted


def test_apply_reads_a_job_from_a_file_path(monkeypatch, capsys, tmp_path):
    # The --file <path> branch (not just --file - stdin): a job written to disk applies the same.
    catalog = {("frontend", "en"): {"a": "A", "b": "B"}}
    _rc, job, _err, _posts = _read(monkeypatch, capsys, catalog, "pt")
    _fill(job)
    job_path = tmp_path / "job-pt.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    posts = []
    monkeypatch.setattr(cz, "api", _make_api(catalog, posts))
    rc = cz.cmd_translate(SimpleNamespace(lang="pt", apply=True, file=str(job_path)))
    err = capsys.readouterr().err
    assert rc == 0 and {p[2] for p in posts} == {"a", "b"}
    assert "wrote 2 new row(s)" in err


def test_apply_aborts_on_a_backend_write_failure(monkeypatch):
    # A non-200 POST must abort loudly, never report a write it did not persist (false success).
    catalog = {("frontend", "en"): {"a": "A"}}
    job = {"target_lang": "pt", "scopes": [
        {"scope": "frontend", "op": "translate", "source_lang": "en", "target_lang": "pt",
         "entries": [{"key": "a", "source": "A", "value": "AA"}]}]}
    monkeypatch.setattr(cz, "api", _make_api(catalog, [], post_status=500))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(job)))
    with pytest.raises(cz.ToolError, match="upsert failed"):
        cz.cmd_translate(SimpleNamespace(lang="pt", apply=True, file="-"))


def test_read_aborts_when_the_catalog_read_fails(monkeypatch):
    # A non-200 GET must abort, not silently treat the catalog as empty (which would emit a
    # full-catalog job for an already-populated language).
    monkeypatch.setattr(cz, "api", _make_api({}, [], get_status=500))
    with pytest.raises(cz.ToolError, match="cannot read the .* text catalog"):
        cz.cmd_translate(SimpleNamespace(lang="pt", apply=False, file=""))


def test_apply_aborts_when_the_target_read_fails(monkeypatch):
    # On apply the same guard matters MORE: an empty `have` from a failed GET would defeat the
    # never-overwrite skip and let apply clobber real rows. It must abort instead.
    job = {"target_lang": "pt", "scopes": [
        {"scope": "frontend", "op": "translate", "source_lang": "en", "target_lang": "pt",
         "entries": [{"key": "a", "source": "A", "value": "AA"}]}]}
    monkeypatch.setattr(cz, "api", _make_api({}, [], get_status=500))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(job)))
    with pytest.raises(cz.ToolError, match="cannot read the .* text catalog"):
        cz.cmd_translate(SimpleNamespace(lang="pt", apply=True, file="-"))


def test_apply_refuses_a_job_pulled_for_another_language(monkeypatch):
    # A wrong-language file must be refused, never landing one language's translations under
    # another (apply writes the command's <lang> and ignores the file's own target_lang).
    catalog = {("frontend", "en"): {"a": "A"}}
    job = {"target_lang": "de", "scopes": [
        {"scope": "frontend", "op": "translate", "source_lang": "en", "target_lang": "de",
         "entries": [{"key": "a", "source": "A", "value": "GERMAN"}]}]}
    posts = []
    monkeypatch.setattr(cz, "api", _make_api(catalog, posts))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(job)))
    with pytest.raises(cz.ToolError, match="pulled for language 'de'"):
        cz.cmd_translate(SimpleNamespace(lang="pt", apply=True, file="-"))
    assert posts == []


def test_apply_accepts_a_minimal_job_without_target_lang(monkeypatch):
    # The target_lang guard fires only when the job names one, so a minimal hand-built job
    # (no target_lang) still applies against the command's <lang>.
    catalog = {("frontend", "en"): {"a": "A"}}
    job = {"scopes": [{"scope": "frontend", "entries": [{"key": "a", "value": "AA"}]}]}
    posts = []
    monkeypatch.setattr(cz, "api", _make_api(catalog, posts))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(job)))
    rc = cz.cmd_translate(SimpleNamespace(lang="pt", apply=True, file="-"))
    assert rc == 0 and [p[2] for p in posts] == ["a"]


def test_apply_rejects_an_unknown_scope(monkeypatch):
    catalog = {}
    monkeypatch.setattr(cz, "api", _make_api(catalog, []))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"target_lang": "pt", "scopes": [{"scope": "bogus", "entries": []}]})))
    with pytest.raises(cz.ToolError, match="unknown scope"):
        cz.cmd_translate(SimpleNamespace(lang="pt", apply=True, file="-"))


def test_apply_needs_a_file():
    with pytest.raises(cz.ToolError, match="--apply needs"):
        cz.cmd_translate(SimpleNamespace(lang="pt", apply=True, file=""))


def test_apply_rejects_a_job_without_scopes(monkeypatch):
    monkeypatch.setattr(cz, "api", _make_api({}, []))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"target_lang": "pt"})))
    with pytest.raises(cz.ToolError, match='"scopes" list'):
        cz.cmd_translate(SimpleNamespace(lang="pt", apply=True, file="-"))


def test_empty_target_language_is_rejected():
    with pytest.raises(cz.ToolError, match="target language code"):
        cz.cmd_translate(SimpleNamespace(lang="  ", apply=False, file=""))


def test_verb_is_registered_and_wired():
    parser = cz.build_parser()
    ns = parser.parse_args(["translate", "pt", "--apply", "--file", "-"])
    assert ns.fn is cz.cmd_translate and ns.lang == "pt" and ns.apply is True and ns.file == "-"
