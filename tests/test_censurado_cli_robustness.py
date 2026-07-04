"""Fail-soft tests for cli/censurado.py driven through the REAL entry point, cz.main([...]).

An LLM drives this CLI, and an LLM produces malformed JSON, wrong data types, nulls, missing
fields, unreadable paths, and non-ids. None of that may become a Python traceback or an infinite
hang: every verb must return a single clean, ACTIONABLE `ERROR: ...` line and a non-zero exit so
the model can read the error and correct itself. These tests assert exactly that, hermetically
(no stack, no network) by failing before any request or by stubbing the one network chokepoint.
"""
import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli" / "censurado.py"


def _load():
    spec = importlib.util.spec_from_file_location("censurado", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cz = _load()


def run(argv, capsys):
    """Invoke the CLI end to end; return (rc, stdout, stderr). main() never lets a traceback
    escape, so this always returns an int rc."""
    rc = cz.main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def _bad(rc, err, *must_contain):
    """Assert a clean agent-facing failure: non-zero exit, an ERROR line, NO traceback, and each
    expected phrase present."""
    assert rc == 1, f"expected exit 1, got {rc}"
    assert "Traceback" not in err, f"a traceback leaked to the model:\n{err}"
    assert "ERROR:" in err, f"no ERROR line:\n{err}"
    for phrase in must_contain:
        assert phrase in err, f"missing {phrase!r} in:\n{err}"


# ---- bad ids / refs (fail BEFORE any network call) --------------------------

def test_tweet_with_no_id_fails_before_the_network(capsys):
    rc, _out, err = run(["tweet", "just-a-handle"], capsys)
    _bad(rc, err, "no tweet id found", "just-a-handle")


def test_truth_with_no_id_fails_before_the_network(capsys):
    rc, _out, err = run(["truth", ""], capsys)
    _bad(rc, err, "no truth id found")


# ---- unreadable / missing files --------------------------------------------

def test_missing_body_file_is_a_clean_error(capsys):
    rc, _out, err = run(["preview", "--author", "a", "--title", "t", "--subtitle", "s",
                         "--description", "d", "--body-file", "/no/such/draft.md"], capsys)
    _bad(rc, err, "not found", "--body-file")


def test_from_json_missing_file_is_a_clean_error(capsys):
    rc, _out, err = run(["tweet", "123456", "--from-json", "/no/such/snap.json"], capsys)
    _bad(rc, err, "not found", "--from-json")


def test_media_missing_file_is_a_clean_error(capsys):
    rc, _out, err = run(["media", "/no/such/hero.png"], capsys)
    _bad(rc, err, "not found", "media file")


# ---- malformed / wrong-shape JSON inputs -----------------------------------

def test_malformed_tweets_file_is_a_clean_error(tmp_path, capsys):
    bad = tmp_path / "t.json"
    bad.write_text("{not: json]")
    rc, _out, err = run(["preview", "--author", "a", "--title", "t", "--subtitle", "s",
                         "--description", "d", "--section", "x", "--author-name", "n",
                         "--body", "hola", "--tweets-file", str(bad)], capsys)
    _bad(rc, err, "not valid JSON", "--tweets-file")


def test_tweets_file_must_be_a_list_not_an_object(tmp_path, capsys):
    obj = tmp_path / "t.json"
    obj.write_text(json.dumps({"id": "1"}))                     # an object where a list is needed
    rc, _out, err = run(["preview", "--author", "a", "--title", "t", "--subtitle", "s",
                         "--description", "d", "--section", "x", "--author-name", "n",
                         "--body", "hola", "--tweets-file", str(obj)], capsys)
    _bad(rc, err, "--tweets-file", "must be a JSON list")


def test_from_json_snapshot_must_be_an_object(tmp_path, capsys):
    arr = tmp_path / "s.json"
    arr.write_text("[1, 2, 3]")
    rc, _out, err = run(["tweet", "123456", "--from-json", str(arr)], capsys)
    _bad(rc, err, "--from-json", "must be a JSON dict")


def test_portada_malformed_set_json_is_a_clean_error(capsys):
    rc, _out, err = run(["portada", "2026-01-01", "--set-json", "{oops"], capsys)
    _bad(rc, err, "--set-json", "not valid JSON")


# ---- missing required content ----------------------------------------------

def test_empty_body_is_a_clean_error(capsys):
    rc, _out, err = run(["preview", "--author", "a", "--title", "t", "--subtitle", "s",
                         "--description", "d", "--section", "x", "--author-name", "n"], capsys)
    _bad(rc, err, "no article body")


# ---- network transport failures become a clean message, never a traceback ---

def test_network_down_is_a_clean_error(monkeypatch, capsys):
    # A token is present (read from the env), so the failure surfaces at the network call,
    # not at the token gate; the point is that a refused connection is a clean error.
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op.tok")

    def boom(*a, **k):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr(cz.urllib.request, "urlopen", boom)
    rc, _out, err = run(["personas"], capsys)
    _bad(rc, err, "cannot reach", "docker compose up")


def test_timeout_is_a_clean_error(monkeypatch, capsys):
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op.tok")

    def boom(*a, **k):
        raise TimeoutError("timed out")
    monkeypatch.setattr(cz.urllib.request, "urlopen", boom)
    rc, _out, err = run(["portals"], capsys)
    _bad(rc, err, "cannot reach")


# ---- garbage / wrong-shape SERVICE responses -------------------------------

def test_non_json_response_is_a_clean_error(monkeypatch, capsys):
    # The backend (or a proxy) returns an HTML error page with a 200; json.loads would traceback.
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op.tok")
    monkeypatch.setattr(cz, "_req", lambda *a, **k: (200, b"<html>maintenance</html>"))
    rc, _out, err = run(["personas"], capsys)
    _bad(rc, err, "not valid JSON")


def test_response_of_wrong_type_is_a_clean_error(monkeypatch, capsys):
    # A JSON list where the code expects an object: caught by the type guard, not an AttributeError.
    # `portals` calls _json(..., want=dict) on GET /sources, so it still guards the response shape.
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op.tok")
    monkeypatch.setattr(cz, "_req", lambda *a, **k: (200, b"[1, 2, 3]"))
    rc, _out, err = run(["portals"], capsys)
    _bad(rc, err, "must be a JSON dict")


# ---- the last-resort net: even an unexpected bug is reported, not dumped ----

def test_unexpected_exception_is_reported_not_raw(monkeypatch, capsys):
    def kaboom(_a):
        raise ValueError("some internal bug")
    monkeypatch.setattr(cz, "cmd_personas", kaboom)
    rc, _out, err = run(["personas"], capsys)
    assert rc == 1
    assert "Traceback" not in err
    assert "unexpected ValueError" in err and "bug in censurado.py" in err


# ---- a good path still returns 0 through main() -----------------------------

def test_happy_path_from_json_still_succeeds(tmp_path, capsys):
    fix = tmp_path / "fx.json"
    fix.write_text(json.dumps({"tweet": {"id": "20", "text": "hi",
                                         "author": {"screen_name": "jack", "name": "jack"}}}))
    rc, out, _err = run(["tweet", "20", "--from-json", str(fix)], capsys)
    assert rc == 0
    assert json.loads(out)["handle"] == "jack"


# ---- regressions from the adversarial audit + the live e2e ------------------

def test_short_tweet_id_is_accepted_not_rejected():
    # Real ids are 18-19 digits, but historic ones are short (Jack's first tweet is id 20). The
    # id validator must accept a bare numeric id of ANY length and pull it from a status URL.
    # (This is the bug the live end-to-end run caught: a `\d{6,}`-only check dropped id 20.)
    assert cz._status_id("20", "tweet") == "20"
    assert cz._status_id("https://x.com/jack/status/20") == "20"
    assert cz._status_id("https://truthsocial.com/@x/statuses/123456789") == "123456789"
    with pytest.raises(cz.ToolError):
        cz._status_id("just-a-handle", "tweet")


def test_tweets_file_of_non_objects_is_a_clean_error(tmp_path, capsys):
    # A list of ids/strings (a plausible model slip) must be caught, not an AttributeError later.
    bad = tmp_path / "t.json"
    bad.write_text(json.dumps(["123456", "789012"]))
    rc, _out, err = run(["preview", "--author", "a", "--title", "t", "--subtitle", "s",
                         "--description", "d", "--section", "x", "--author-name", "n",
                         "--body", "hola", "--tweets-file", str(bad)], capsys)
    _bad(rc, err, "--tweets-file", "OBJECTS")


def test_image_template_wrong_shape_is_a_clean_error(tmp_path, capsys):
    # Valid JSON, wrong shape (no flux2_klein nodes): must not KeyError. Uses the offline dry run.
    tpl = tmp_path / "tpl.json"
    tpl.write_text(json.dumps({"nope": {}}))
    rc, _out, err = run(["image", "--prompt", "un heroe", "--template", str(tpl), "--dry-run"], capsys)
    _bad(rc, err, "template", "flux2_klein")


def test_create_author_without_file_or_stdin_fails_fast(capsys):
    # No --file (default "") must NOT block reading an open stdin; it fails clean immediately.
    rc, _out, err = run(["create-author"], capsys)
    _bad(rc, err, "--file")


def test_truth_from_json_non_string_fields_do_not_crash(tmp_path, capsys):
    # A snapshot whose content/created_at are numbers (malformed) must map gracefully, not TypeError.
    fix = tmp_path / "s.json"
    fix.write_text(json.dumps({"id": "1", "content": 123, "created_at": 456,
                               "account": {"username": "x", "display_name": "X"}}))
    rc, out, err = run(["truth", "1", "--from-json", str(fix)], capsys)
    assert rc == 0 and "Traceback" not in err
    assert json.loads(out)["id"] == "1"
