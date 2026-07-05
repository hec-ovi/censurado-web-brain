"""Hermetic tests for cli/censurado.py: the publish toolkit's pure logic + CLI entry
point. No network, no DB, no ComfyUI: tweet/truth capture run from saved JSON fixtures
(--from-json), payload + graph builders are pure, and the image graph uses a fake
template. So `python3 -m pytest tests -q` covers the helper without the stack up."""
import importlib.util
import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli" / "censurado.py"


def _load():
    spec = importlib.util.spec_from_file_location("censurado", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cz = _load()

FX_TWEET = {"tweet": {
    "id": "2071372670411690152", "text": "Celebro esa decisión.",
    "url": "https://x.com/mauriciomacri/status/2071372670411690152",
    "created_at": "Sun Jun 28 23:18:23 +0000 2026", "created_timestamp": 1782688703,
    "author": {"screen_name": "mauriciomacri", "name": "Mauricio Macri",
               "avatar_url": "https://pbs.twimg.com/x.png", "verification": {"verified": True}},
    "replies": 1687, "retweets": 886, "likes": 8777, "views": 742148, "bookmarks": 151}}

TRUTH = {"id": "116834854601794123",
         "content": "<p>The Fake News &amp; Radical Left<br>are at it again!</p>",
         "url": "https://truthsocial.com/@realDonaldTrump/116834854601794123",
         "created_at": "2026-06-14T21:29:48.647Z",
         "replies_count": 100, "reblogs_count": 200, "favourites_count": 300,
         "account": {"username": "realDonaldTrump", "display_name": "Donald J. Trump",
                     "avatar": "https://truthsocial.com/av.jpg", "verified": True}}

FAKE_TEMPLATE = {
    "4": {"inputs": {"text": ""}},
    "9": {"inputs": {"noise_seed": 0}},
    "6": {"inputs": {"width": 0, "height": 0}},
    "7": {"inputs": {"width": 0, "height": 0, "steps": 0}},
}


def test_map_tweet_x():
    snap = cz.map_tweet(FX_TWEET["tweet"])
    assert snap["id"] == "2071372670411690152"
    assert snap["handle"] == "mauriciomacri"
    assert snap["name"] == "Mauricio Macri"
    assert snap["verified"] is True
    assert snap["views"] == 742148 and snap["bookmarks"] == 151
    assert snap["erased"] is False


def test_map_truth_social():
    snap = cz.map_truth(TRUTH)
    assert snap["handle"] == "realDonaldTrump"
    assert snap["name"] == "Donald J. Trump"
    # HTML stripped, entity decoded, <br> -> newline
    assert snap["text"] == "The Fake News & Radical Left\nare at it again!"
    # Truth Social has no views / bookmarks
    assert "views" not in snap and "bookmarks" not in snap
    assert snap["likes"] == 300 and snap["retweets"] == 200 and snap["replies"] == 100
    assert isinstance(snap["created_timestamp"], int) and snap["created_timestamp"] > 1_700_000_000


def test_build_image_graph_injects_prompt_seed_dims():
    g = cz.build_image_graph(FAKE_TEMPLATE, "PROMPT TEXT", 777, width=512, height=768, steps=6)
    assert g["4"]["inputs"]["text"] == "PROMPT TEXT"
    assert g["9"]["inputs"]["noise_seed"] == 777
    assert g["6"]["inputs"]["width"] == 512 and g["7"]["inputs"]["height"] == 768
    assert g["7"]["inputs"]["steps"] == 6
    # pure: the template passed in is not mutated
    assert FAKE_TEMPLATE["4"]["inputs"]["text"] == ""


def test_seed_is_stable():
    assert cz.seed_for("same prompt") == cz.seed_for("same prompt")
    assert cz.seed_for("a") != cz.seed_for("b")


def _pub_ns(**over):
    base = dict(author="author-a", title="Un título", section="politics",
                subtitle="Una bajada", description="Un standfirst.", body="# Cuerpo\n\ntexto",
                body_file="", topics="política argentina, javier milei", keywords="",
                slug="", published_at="", image="", image_alt="", youtube="",
                author_name="Autor A", author_bio="", author_avatar="", tweets_file="")
    base.update(over)
    return SimpleNamespace(**base)


def test_build_publish_payload_strict_shape():
    payload = cz.build_publish_payload(_pub_ns())
    assert set(["title", "body", "author", "section"]).issubset(payload)
    assert payload["section"] == "politics"  # provided, so no network persona lookup
    assert payload["topics"] == ["política argentina", "javier milei"]
    md = payload["metadata"]
    assert md["subtitle"] == "Una bajada" and md["description"] == "Un standfirst."
    assert md["author_name"] == "Autor A"


def test_cli_tweet_from_json(tmp_path):
    fix = tmp_path / "fx.json"
    fix.write_text(json.dumps(FX_TWEET))
    out = subprocess.run([sys.executable, str(CLI), "tweet", "2071372670411690152",
                          "--from-json", str(fix)], capture_output=True, text=True, check=True)
    snap = json.loads(out.stdout)
    assert snap["handle"] == "mauriciomacri" and snap["id"] == "2071372670411690152"


def test_cli_truth_from_json(tmp_path):
    fix = tmp_path / "truth.json"
    fix.write_text(json.dumps(TRUTH))
    out = subprocess.run([sys.executable, str(CLI), "truth", "116834854601794123",
                          "--from-json", str(fix)], capture_output=True, text=True, check=True)
    snap = json.loads(out.stdout)
    assert snap["handle"] == "realDonaldTrump"
    assert "views" not in snap


def test_cli_image_dry_run(tmp_path):
    tpl = tmp_path / "tpl.json"
    tpl.write_text(json.dumps(FAKE_TEMPLATE))
    out = subprocess.run([sys.executable, str(CLI), "image", "--prompt", "PROMPT TEXT",
                          "--template", str(tpl), "--dry-run"],
                         capture_output=True, text=True, check=True)
    graph = json.loads(out.stdout)
    assert graph["4"]["inputs"]["text"] == "PROMPT TEXT"


def test_cli_publish_dry_run_no_network(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("# Cuerpo\n\ntexto del cuerpo")
    # Truly hermetic: passing the byline (--author-name) and --section means
    # build_publish_payload has no missing field to fill, so it does NOT reach the
    # brain to resolve the persona. The dry run just assembles and prints the payload.
    out = subprocess.run([sys.executable, str(CLI), "publish", "--author", "author-a",
                          "--author-name", "Autor A",
                          "--title", "Un título", "--section", "politics",
                          "--subtitle", "Una bajada", "--body-file", str(body),
                          "--topics", "política argentina, javier milei", "--dry-run"],
                         capture_output=True, text=True, check=True)
    payload = json.loads(out.stdout)
    assert payload["author"] == "author-a" and payload["section"] == "politics"
    assert payload["metadata"]["subtitle"] == "Una bajada"
    assert payload["metadata"]["author_name"] == "Autor A"
    assert payload["body"].startswith("# Cuerpo")


# ---- preview prints the live permalink -------------------------------------------
# The whole point of `preview` for a driving agent is a URL it can report as DONE.
# The publish POST returns only {id, slug}; the permalink is /a/<slug>-<content_hash[:8]>/
# (the generator's rule), so preview does one follow-up read for the hash then confirms
# the page renders. These stub the single network chokepoint so no stack is needed.

def _preview_ns(**over):
    base = dict(dry_run=False, author="lara-arianna", author_name="Lara", author_bio="",
                author_avatar="", title="Un título", section="politics", subtitle="Una bajada",
                description="Un resumen de una línea.", body="cuerpo", body_file="", topics="",
                keywords="", slug="", published_at="", image="", image_alt="", youtube="",
                tweets_file="", idempotency="")
    base.update(over)
    return SimpleNamespace(**base)


def test_preview_refuses_without_subtitle_or_description(monkeypatch, capsys):
    posted = []
    monkeypatch.setattr(cz, "_req", lambda *a, **k: (posted.append(1), (201, b"{}"))[1])
    monkeypatch.setattr(cz, "token", lambda: "t")
    rc = cz.cmd_publish(_preview_ns(subtitle="", description=""))
    out = capsys.readouterr()
    assert rc == 1
    assert "subtitle" in out.err and "description" in out.err   # names both missing fields
    assert not posted                                   # refused before any POST


def test_preview_prints_live_permalink(monkeypatch, capsys):
    def fake_req(method, url, data=None, headers=None, timeout=60):
        if method == "POST" and url.endswith("/articles"):
            return 201, json.dumps({"id": "64", "slug": "mi-nota"}).encode()
        if method == "GET" and url.endswith("/articles/mi-nota"):
            return 200, json.dumps({"slug": "mi-nota", "content_hash": "deadbeef1234"}).encode()
        if method == "GET" and url == f"{cz.SITE}/a/mi-nota-deadbeef/":
            return 200, b"<html>live</html>"
        raise AssertionError(("unexpected request", method, url))

    monkeypatch.setattr(cz, "_req", fake_req)
    monkeypatch.setattr(cz, "token", lambda: "t")
    rc = cz.cmd_publish(_preview_ns())
    out = capsys.readouterr()
    assert rc == 0
    assert f"PREVIEW: {cz.SITE}/a/mi-nota-deadbeef/  [live now]" in out.out
    assert f"NEWEST: {cz.SITE}/latest/" in out.out


def test_preview_still_prints_url_before_the_site_regenerates(monkeypatch, capsys):
    # Hash resolves but the generator has not rebuilt yet: the permalink 404s. We must
    # still print the CORRECT url (flagged "still rendering"), not swallow it.
    monkeypatch.setattr(cz.time, "sleep", lambda *_: None)  # keep the poll instant

    def fake_req(method, url, data=None, headers=None, timeout=60):
        if method == "POST" and url.endswith("/articles"):
            return 201, json.dumps({"id": "7", "slug": "mi-nota"}).encode()
        if method == "GET" and url.endswith("/articles/mi-nota"):
            return 200, json.dumps({"content_hash": "abcd1234ef"}).encode()
        return 404, b"not yet"

    monkeypatch.setattr(cz, "_req", fake_req)
    monkeypatch.setattr(cz, "token", lambda: "t")
    rc = cz.cmd_publish(_preview_ns())
    out = capsys.readouterr()
    assert rc == 0
    assert f"PREVIEW: {cz.SITE}/a/mi-nota-abcd1234/  [still rendering" in out.out


def test_preview_survives_backend_drop_after_a_successful_publish(monkeypatch, capsys):
    # The article is already staged (201). If the publish backend blips on the follow-up read
    # for the permalink hash, that must NOT turn a successful publish into a traceback.
    def fake_req(method, url, data=None, headers=None, timeout=60):
        if method == "POST" and url.endswith("/articles"):
            return 201, json.dumps({"id": "9", "slug": "mi-nota"}).encode()
        raise urllib.error.URLError("Connection refused")   # the follow-up GET blips

    monkeypatch.setattr(cz, "_req", fake_req)
    monkeypatch.setattr(cz, "token", lambda: "t")
    rc = cz.cmd_publish(_preview_ns())          # must not raise
    out = capsys.readouterr()
    assert rc == 0
    assert f"NEWEST: {cz.SITE}/latest/" in out.out   # still reports where to look
    assert "PREVIEW:" not in out.out                 # URL just could not be resolved this instant


def test_preview_reports_failure_and_skips_url_on_http_error(monkeypatch, capsys):
    def fake_req(method, url, data=None, headers=None, timeout=60):
        if method == "POST" and url.endswith("/articles"):
            return 422, json.dumps({"code": "invalid", "detail": "bad section"}).encode()
        raise AssertionError("must not resolve a URL after a failed publish")

    monkeypatch.setattr(cz, "_req", fake_req)
    monkeypatch.setattr(cz, "token", lambda: "t")
    rc = cz.cmd_publish(_preview_ns())
    out = capsys.readouterr()
    assert rc == 1
    assert "preview FAILED -> HTTP 422" in out.err
    assert "PREVIEW:" not in out.out


# ---- image degrades gracefully when ComfyUI is down (the GPU-free lane) ------------

def test_image_skips_cleanly_when_comfyui_is_down(monkeypatch, capsys, tmp_path):
    tpl = tmp_path / "tpl.json"
    tpl.write_text(json.dumps(FAKE_TEMPLATE))

    def refused(method, url, data=None, headers=None, timeout=60):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(cz, "_req", refused)
    monkeypatch.setattr(cz, "token", lambda: "t")
    rc = cz.cmd_image(SimpleNamespace(template=str(tpl), dry_run=False, prompt="un heroe",
                                      alt="alt", seed=None, width=1344, height=768, steps=4))
    out = capsys.readouterr()
    assert rc == 0                       # best-effort: never a hard failure / traceback
    assert "IMAGE SKIPPED" in out.err
    assert "text-only" in out.err
    assert out.out == ""                 # no bogus {"image":...} for the model to attach


# ---- hero image is a parameter-out the model never transcribes -----------------------
# A small model truncates the 64-hex media hash when it hand-copies it, e.g.
# /media/1921e193_..._efae.png (-> 404). So `image` records the full path to
# $CENSURADO_WORK/image.json and `preview` attaches it itself; a truncated --image is refused.

_FULL_MEDIA = "/media/" + "a" * 64 + ".png"
_TRUNC_MEDIA = "/media/aaaaaaaa_..._aaaa.png"       # what a small model actually emits


def test_image_records_the_full_hero_path_to_workdir(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))
    monkeypatch.setattr(cz, "render_image", lambda *a, **k: (b"PNGBYTES", 42))
    monkeypatch.setattr(cz, "token", lambda: "t")
    monkeypatch.setattr(cz, "_req",
                        lambda m, u, data=None, headers=None, timeout=60:
                        (201, json.dumps({"url": _FULL_MEDIA}).encode()))
    rc = cz.cmd_image(SimpleNamespace(template="", dry_run=False, prompt="p", alt="una imagen",
                                      seed=None, width=1344, height=768, steps=4))
    assert rc == 0
    saved = json.loads((tmp_path / "image.json").read_text())
    assert saved["image"] == _FULL_MEDIA and saved["image_alt"] == "una imagen"


def test_preview_uses_workdir_hero_when_model_truncates_the_path(monkeypatch, tmp_path):
    (tmp_path / "image.json").write_text(json.dumps({"image": _FULL_MEDIA, "image_alt": "alt real"}))
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))
    payload = cz.build_publish_payload(_pub_ns(image=_TRUNC_MEDIA, image_alt=""))
    assert payload["metadata"]["image"] == _FULL_MEDIA          # the full path, not the truncated one
    assert payload["metadata"]["image_alt"] == "alt real"


def test_preview_honors_a_wellformed_explicit_image(monkeypatch, tmp_path):
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))         # no image.json here
    payload = cz.build_publish_payload(_pub_ns(image=_FULL_MEDIA, image_alt="mine"))
    assert payload["metadata"]["image"] == _FULL_MEDIA and payload["metadata"]["image_alt"] == "mine"


def test_preview_drops_a_truncated_image_when_no_hero_saved(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))         # empty: no image.json
    payload = cz.build_publish_payload(_pub_ns(image=_TRUNC_MEDIA, image_alt="x"))
    assert "image" not in payload.get("metadata", {})          # never store a broken /media path
    assert "malformed --image" in capsys.readouterr().err


# ---- tweet/truth cards are a parameter-out too: capture saves the snapshot, preview attaches it
# for exactly the {{tweet:id}} markers in the body, so the model never retypes the snapshot JSON.

def test_tweet_verb_saves_the_snapshot_to_workdir(monkeypatch, tmp_path):
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))
    fix = tmp_path / "fx.json"
    fix.write_text(json.dumps(FX_TWEET))
    cz.cmd_tweet(SimpleNamespace(ref="2071372670411690152", from_json=str(fix)))
    saved = json.loads((tmp_path / "tweets.json").read_text())
    assert [t["id"] for t in saved] == ["2071372670411690152"]


def test_preview_auto_attaches_the_captured_tweet_the_body_embeds(monkeypatch, tmp_path):
    (tmp_path / "tweets.json").write_text(json.dumps(
        [{"id": "123", "handle": "openai", "text": "hola"}, {"id": "999", "handle": "x", "text": "no"}]))
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))
    payload = cz.build_publish_payload(_pub_ns(body="Un texto.\n\n{{tweet:123}}\n\nmás."))
    ids = [t["id"] for t in payload["metadata"]["tweets"]]
    assert ids == ["123"]                                   # only the one the body embeds, not 999


def test_preview_attaches_no_tweet_when_body_has_no_marker(monkeypatch, tmp_path):
    (tmp_path / "tweets.json").write_text(json.dumps([{"id": "123", "handle": "x", "text": "t"}]))
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))
    payload = cz.build_publish_payload(_pub_ns(body="Sin ninguna cita de post."))
    assert "tweets" not in payload.get("metadata", {})


def test_preview_autofetches_a_marker_the_model_left_uncaptured(monkeypatch, tmp_path):
    # The model placed {{tweet:id}} with the right id but skipped `tweet <url>`, so no snapshot.
    # preview must fetch it from the id so the card still renders.
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))            # empty: no tweets.json
    monkeypatch.setattr(cz, "capture_tweet",
                        lambda ref, raw=None: {"id": str(ref), "handle": "openai", "text": "DevDay"})
    monkeypatch.setattr(cz.time, "sleep", lambda *_: None)
    monkeypatch.setattr(cz, "token", lambda: "t")
    posted = {}

    def fake_req(method, url, data=None, headers=None, timeout=60):
        if method == "POST" and url.endswith("/articles"):
            posted["payload"] = json.loads(data)
            return 201, json.dumps({"id": "1", "slug": "s"}).encode()
        return 200, json.dumps({"content_hash": "abcd1234"}).encode()   # follow-up GET + site poll

    monkeypatch.setattr(cz, "_req", fake_req)
    rc = cz.cmd_publish(_preview_ns(body="texto\n\n{{tweet:2049534651702956103}}\n\nfin"))
    assert rc == 0
    assert [t["id"] for t in posted["payload"]["metadata"]["tweets"]] == ["2049534651702956103"]


def test_preview_explicit_tweets_file_wins(monkeypatch, tmp_path):
    (tmp_path / "tweets.json").write_text(json.dumps([{"id": "123", "handle": "x"}]))
    tf = tmp_path / "explicit.json"
    tf.write_text(json.dumps([{"id": "777", "handle": "explicit"}]))
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))
    payload = cz.build_publish_payload(_pub_ns(body="{{tweet:123}}", tweets_file=str(tf)))
    assert [t["id"] for t in payload["metadata"]["tweets"]] == ["777"]


# ---- the data-facing agentic verbs: authors/sources live in the publish backend (Bearer-authed,
# admin:write); prompts are on-disk FILES under PROMPTS_DIR (git is their history). These exercise
# the plumbing with the single network chokepoint (_req) stubbed and PROMPTS_DIR pointed at a temp
# recipe, so the tests stay hermetic while proving the agent's tool surface targets the right
# endpoints and files.

def test_cli_parses_agentic_verbs():
    p = cz.build_parser()
    assert p.parse_args(["personas"]).fn is cz.cmd_personas
    assert p.parse_args(["persona", "author-a"]).fn is cz.cmd_persona
    assert p.parse_args(["create-author"]).fn is cz.cmd_create_author
    assert p.parse_args(["sources", "author-a"]).fn is cz.cmd_sources
    assert p.parse_args(["profile-topics", "author-a"]).fn is cz.cmd_profile_topics
    assert p.parse_args(["portals"]).fn is cz.cmd_portals
    assert p.parse_args(["prompt", "workflow/50-draft.md"]).fn is cz.cmd_prompt


def test_create_author_rejects_incomplete_persona(tmp_path):
    pj = tmp_path / "p.json"
    pj.write_text(json.dumps({"display_name": "Autor A"}))  # missing beat/who_i_am/style
    with pytest.raises(SystemExit):
        cz.cmd_create_author(SimpleNamespace(file=str(pj)))


def test_create_author_posts_the_transformed_authorinput(tmp_path, monkeypatch):
    # The agent writes the synthesize-prompt persona JSON; create-author transforms it into the
    # backend authorInput and POSTs /authors (Bearer-authed). No sources ride along (outlets are
    # wired separately with `sources --set`).
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op.tok")
    calls = []

    def fake_req(method, url, data=None, headers=None, timeout=60):
        calls.append((method, url, data, headers))
        return 201, json.dumps({"handle": "autor-a"}).encode()

    monkeypatch.setattr(cz, "_req", fake_req)
    pj = tmp_path / "p.json"
    pj.write_text(json.dumps({"display_name": "Autor A", "beat": "politics",
                              "who_i_am": "Soy Autor A.", "style": "Directo.",
                              "about": "Sobre Autor A.", "gender": "m",
                              "few_shots_pos": ["ejemplo"], "sources": ["portal-a"]}))
    assert cz.cmd_create_author(SimpleNamespace(file=str(pj))) == 0
    method, url, data, headers = calls[-1]
    assert method == "POST" and url.endswith("/authors")
    assert (headers or {}).get("Authorization") == "Bearer op.tok"
    sent = json.loads(data)
    assert sent["handle"] == "autor-a"                        # slugified from display_name
    assert sent["name"] == "Autor A"
    assert sent["bio"] == "Sobre Autor A." == sent["about"]   # about fills both bio and about
    assert sent["metadata"]["beat"] == "politics"
    assert sent["metadata"]["who_i_am"] == "Soy Autor A."
    assert sent["metadata"]["few_shots_pos"] == ["ejemplo"]
    assert "sources" not in sent                             # outlets are wired separately


def test_sources_set_replaces_via_put(monkeypatch):
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op.tok")
    calls = []
    monkeypatch.setattr(cz, "_req",
                        lambda m, u, data=None, headers=None, timeout=60: (calls.append((m, u, data)) or (200, b"{}")))
    cz.cmd_sources(SimpleNamespace(id="author-a", set="portal-a, portal-b"))
    method, url, data = calls[-1]
    assert method == "PUT" and url.endswith("/authors/author-a/sources")
    assert json.loads(data)["sources"] == ["portal-a", "portal-b"]


def test_profile_topics_set_is_a_read_modify_write(monkeypatch):
    # A set is a READ-MODIFY-WRITE: read the author's full record (GET /authors), then POST the
    # WHOLE row back with topics replaced first-class AND mirrored in metadata.profile_topics, so
    # the rest of the row (voice, bio, metadata tail) survives instead of being blanked.
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op.tok")
    existing = {"handle": "author-a", "name": "Autor A", "bio": "bio previa", "avatar": "",
                "gender": "f", "about": "sobre", "style": "estilo previo",
                "topics": ["viejo"],
                "metadata": {"beat": "politics", "who_i_am": "quien soy", "profile_topics": ["viejo"]}}
    calls = []

    def fake_req(method, url, data=None, headers=None, timeout=60):
        calls.append((method, url, data))
        if method == "GET":
            return 200, json.dumps({"authors": [existing]}).encode()
        return 200, b"{}"

    monkeypatch.setattr(cz, "_req", fake_req)
    cz.cmd_profile_topics(SimpleNamespace(id="author-a", set="politica, economia"))
    method, url, data = calls[-1]
    assert method == "POST" and url.endswith("/authors")
    sent = json.loads(data)
    assert sent["topics"] == ["politica", "economia"]                     # first-class
    assert sent["metadata"]["profile_topics"] == ["politica", "economia"]  # and in metadata
    # the pre-existing row is preserved, not blanked
    assert sent["name"] == "Autor A" and sent["bio"] == "bio previa"
    assert sent["style"] == "estilo previo" and sent["metadata"]["who_i_am"] == "quien soy"


def test_profile_topics_empty_set_clears_the_curated_list(monkeypatch):
    # --set "" drops both the first-class topics key and metadata.profile_topics, preserving the
    # rest of the row and the rest of the metadata.
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op.tok")
    existing = {"handle": "author-a", "name": "Autor A", "bio": "bio", "about": "sobre",
                "style": "estilo", "gender": "", "topics": ["viejo"],
                "metadata": {"beat": "politics", "who_i_am": "quien", "profile_topics": ["viejo"]}}
    calls = []

    def fake_req(method, url, data=None, headers=None, timeout=60):
        calls.append((method, url, data))
        if method == "GET":
            return 200, json.dumps({"authors": [existing]}).encode()
        return 200, b"{}"

    monkeypatch.setattr(cz, "_req", fake_req)
    cz.cmd_profile_topics(SimpleNamespace(id="author-a", set=""))
    method, url, data = calls[-1]
    assert method == "POST" and url.endswith("/authors")
    sent = json.loads(data)
    assert "topics" not in sent                                 # first-class topics dropped
    assert "profile_topics" not in sent.get("metadata", {})     # cleared from metadata too
    assert sent["metadata"]["beat"] == "politics"               # rest of metadata survives
    assert sent["name"] == "Autor A"                            # rest of the row survives


def test_profile_topics_show_reads_the_persona(monkeypatch, capsys):
    # show (no --set): read GET /authors, print the author's topics list as JSON.
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op.tok")
    monkeypatch.setattr(cz, "_req",
                        lambda m, u, data=None, headers=None, timeout=60:
                        (200, json.dumps({"authors": [{"handle": "author-a",
                                                       "topics": ["ia", "cripto"]}]}).encode()))
    assert cz.cmd_profile_topics(SimpleNamespace(id="author-a", set=None)) == 0
    assert json.loads(capsys.readouterr().out) == ["ia", "cripto"]


def test_cli_parses_portada_verb():
    assert cz.build_parser().parse_args(["portada", "2026-06-10"]).fn is cz.cmd_portada


def test_portada_set_json_posts_to_the_publish_backend(monkeypatch):
    # cmd_portada writes the day's front-page plan through api() (PUBLISH, Bearer-authed), so
    # stub that chokepoint and prove it POSTs the merged {date, entries, recomendado} to /portadas.
    calls = []

    def fake_api(method, path, payload=None, auth=True, base=None):
        calls.append((method, path, payload))
        return 200, json.dumps({"date": "2026-06-10", "ok": True}).encode()

    monkeypatch.setattr(cz, "api", fake_api)
    assert cz.cmd_portada(SimpleNamespace(
        date="2026-06-10",
        set_json='{"entries":[{"slug":"a","role":"important"}],"recomendado":["b"]}')) == 0
    method, url, body = calls[-1]
    assert method == "POST" and url.endswith("/portadas")
    assert body["date"] == "2026-06-10"  # the positional date is merged in
    assert body["entries"] == [{"slug": "a", "role": "important"}]
    assert body["recomendado"] == ["b"]


def test_portada_date_arg_wins_over_json_date(monkeypatch):
    calls = []
    monkeypatch.setattr(cz, "api",
                        lambda m, p, payload=None, auth=True, base=None: (calls.append((m, p, payload)) or (200, b"{}")))
    cz.cmd_portada(SimpleNamespace(date="2026-06-10", set_json='{"date":"1999-01-01","entries":[]}'))
    assert calls[-1][2]["date"] == "2026-06-10"


def test_portada_show_lists_via_get(monkeypatch):
    calls = []
    monkeypatch.setattr(cz, "api",
                        lambda m, p, payload=None, auth=True, base=None: (calls.append((m, p, payload)) or (200, b"[]")))
    cz.cmd_portada(SimpleNamespace(date="2026-06-10", set_json=None))
    method, url, body = calls[-1]
    assert method == "GET" and url.endswith("/portadas") and body is None


def test_portada_rejects_malformed_json(monkeypatch):
    # Malformed --set-json is agent input, so it fails soft: a clean ToolError naming the arg,
    # never a JSONDecodeError traceback. (main() turns this into an `ERROR: ...` line, exit 1.)
    monkeypatch.setattr(cz, "api",
                        lambda m, p, payload=None, auth=True, base=None: (200, b"{}"))
    with pytest.raises(cz.ToolError) as e:
        cz.cmd_portada(SimpleNamespace(date="2026-06-10", set_json="{not json"))
    assert "--set-json" in str(e.value) and "not valid JSON" in str(e.value)


def test_portada_rejects_non_object_json(monkeypatch):
    # A JSON list/scalar where an object is expected is caught by the type guard, not an
    # AttributeError on spec.get(...).
    monkeypatch.setattr(cz, "api",
                        lambda m, p, payload=None, auth=True, base=None: (200, b"{}"))
    with pytest.raises(cz.ToolError) as e:
        cz.cmd_portada(SimpleNamespace(date="2026-06-10", set_json="[1, 2, 3]"))
    assert "must be a JSON dict" in str(e.value)


def test_archive_light_output_includes_has_media(monkeypatch, capsys):
    # cmd_archive lists an author's pieces LIGHT via api() (GET /articles); prove it
    # carries the server-derived has_media flag per piece, which the portada layout
    # walk reads to alternate media and text cards without opening each body.
    articles = {
        "total": 2,
        "articles": [
            {"slug": "con-foto", "title": "Con foto", "section": "tech",
             "published_at": "2026-07-01T10:00:00Z", "topics": ["ia"],
             "has_media": True, "metadata": {"subtitle": "s", "description": "d"}},
            {"slug": "solo-texto", "title": "Solo texto", "section": "politica",
             "published_at": "2026-07-01T09:00:00Z", "topics": [],
             "has_media": False, "metadata": {}},
        ],
    }
    monkeypatch.setattr(cz, "api",
                        lambda m, p, payload=None, auth=True, base=None: (200, json.dumps(articles).encode()))
    rc = cz.cmd_archive(SimpleNamespace(author="vector-omni", q=None, since=None, until=None, limit=None))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert {a["slug"]: a["has_media"] for a in out["articles"]} == {"con-foto": True, "solo-texto": False}


def test_prompt_workflow_key_reads_the_node_file(monkeypatch, capsys, tmp_path):
    # A workflow/<node> key is a file under PROMPTS_DIR; `prompt` reads and prints it, no network.
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    assert cz.cmd_prompt(SimpleNamespace(key="workflow/40-outline.md")) == 0
    assert "Outline the piece from the ledger." in capsys.readouterr().out


def test_prompt_persona_key_reads_the_persona_file(monkeypatch, capsys, tmp_path):
    # A persona key reads the same way as a workflow node: a plain file under PROMPTS_DIR.
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    assert cz.cmd_prompt(SimpleNamespace(key="persona/synthesize.md")) == 0
    assert "SYNTH GUIDE" in capsys.readouterr().out


def test_cli_parses_style_verb():
    assert cz.build_parser().parse_args(["style"]).fn is cz.cmd_style


def test_style_reads_the_local_editorial_guide(monkeypatch, capsys, tmp_path):
    # `style` reads the prose guide at editorial/style.md under PROMPTS_DIR and prints it. No
    # network, no JSON: it is the qualitative voice/house-rules guide, not the numeric floor.
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    assert cz.cmd_style(SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "VOZ: declarativa" in out and "sin clickbait" in out


def test_style_errors_when_the_guide_is_missing(monkeypatch, tmp_path):
    # No editorial/style.md in the recipe: the verb fails soft with a ToolError, not a traceback.
    root = tmp_path / "prompts"
    (root / "editorial").mkdir(parents=True)
    monkeypatch.setattr(cz, "PROMPTS_DIR", root)
    with pytest.raises(cz.ToolError):
        cz.cmd_style(SimpleNamespace())


# ---- the Slice-2 write verbs: remove-author, set-floor, set-prompt --------------------

def test_cli_parses_slice2_verbs():
    p = cz.build_parser()
    assert p.parse_args(["remove-author", "lara-arianna"]).fn is cz.cmd_remove_author
    assert p.parse_args(["set-floor", "--min-sources", "8"]).fn is cz.cmd_set_floor
    assert p.parse_args(["set-prompt", "workflow/50-draft.md"]).fn is cz.cmd_set_prompt


def test_remove_author_refuses_without_yes(monkeypatch):
    monkeypatch.setattr(cz, "_req",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call the API without --yes")))
    with pytest.raises(SystemExit):
        cz.cmd_remove_author(SimpleNamespace(id="lara-arianna", yes=False))


def test_remove_author_deletes_the_persona_with_yes(monkeypatch, capsys):
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op.tok")
    calls = []

    def fake_req(method, url, data=None, headers=None, timeout=60):
        calls.append((method, url))
        return 204, b""

    monkeypatch.setattr(cz, "_req", fake_req)
    assert cz.cmd_remove_author(SimpleNamespace(id="lara-arianna", yes=True)) == 0
    assert calls == [("DELETE", cz.PUBLISH + "/authors/lara-arianna")]
    assert "tombstoned (204)" in capsys.readouterr().err


def test_remove_author_reports_not_found(monkeypatch, capsys):
    # There is no 409 in-use branch anymore: a missing author is a plain 404 the CLI reports.
    monkeypatch.setenv("NEWSROOM_OPERATOR_TOKEN", "op.tok")
    monkeypatch.setattr(cz, "_req",
                        lambda m, u, data=None, headers=None, timeout=60: (404, b'{"code":"not_found"}'))
    assert cz.cmd_remove_author(SimpleNamespace(id="lara-arianna", yes=True)) == 1
    assert "no such author (404)" in capsys.readouterr().err


def test_set_floor_edits_the_parameters_preserving_others(monkeypatch, capsys, tmp_path):
    (tmp_path / "parameters.json").write_text(json.dumps(
        {"MIN_SOURCES": 6, "MIN_PER_TYPE": 2, "RESPIN_PASSES": 2, "TOPIC_CAP": 12}))
    monkeypatch.setattr(cz, "_WORKFLOW_DIR", tmp_path)
    monkeypatch.setattr(cz, "_req",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("set-floor must not hit the brain")))
    assert cz.cmd_set_floor(SimpleNamespace(min_sources=8, min_per_type=None)) == 0
    data = json.loads((tmp_path / "parameters.json").read_text())
    assert data["MIN_SOURCES"] == 8               # changed
    assert data["MIN_PER_TYPE"] == 2              # untouched
    assert data["RESPIN_PASSES"] == 2 and data["TOPIC_CAP"] == 12  # the other parameters survive


def test_set_floor_requires_at_least_one_parameter(tmp_path, monkeypatch):
    monkeypatch.setattr(cz, "_WORKFLOW_DIR", tmp_path)
    with pytest.raises(SystemExit):
        cz.cmd_set_floor(SimpleNamespace(min_sources=None, min_per_type=None))


def test_set_prompt_workflow_key_writes_the_file_in_place(monkeypatch, tmp_path):
    # A workflow node is a file under PROMPTS_DIR: set-prompt writes it in place, no network.
    root = _recipe(tmp_path)
    monkeypatch.setattr(cz, "PROMPTS_DIR", root)
    rc = cz.cmd_set_prompt(SimpleNamespace(key="workflow/30-research.md", body="NEW BODY", body_file=""))
    assert rc == 0
    assert (root / "workflow" / "30-research.md").read_text() == "NEW BODY"


def test_set_prompt_rejects_a_new_node(monkeypatch, tmp_path):
    # Adding a brand-new node is a code change, not an edit: an unknown key under the recipe is
    # refused with a ToolError (fail soft), never a silent create.
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    with pytest.raises(cz.ToolError):
        cz.cmd_set_prompt(SimpleNamespace(key="workflow/does-not-exist.md", body="x", body_file=""))


import urllib.parse  # noqa: E402  (the archive query-string asserts below)

# ---- shared local recipe: the workflow prompts + manifest + persona/editorial guides are on-disk
# FILES under PROMPTS_DIR (git is their history). `_recipe` lays a controlled one down in a temp
# dir so step + doctor + prompt + style tests run hermetically against it, no real stack and no
# dependence on the real brain checkout. --------------------------------------------------------

_WF_MANIFEST = {
    "modes": {"single-article": ["15-pick-author", "30-research", "40-outline", "99-publish", "deploy"],
              "daily": ["10-batch-plan"]},
    "disabled": {"single-article": ["deploy"]},   # deploy disabled in single-article: proves per-mode skip
    "produces": {"30-research": "ledger.md"},
}
_WF_BODIES = {
    "workflow/00-mode.md": "Pick a mode: single-article or daily.",
    "workflow/15-pick-author.md": "Load the author and their sources.",
    "workflow/30-research.md": ("Research. floor {{MIN_SOURCES}}, per-lean {{MIN_PER_TYPE}}, "
                                "passes {{RESPIN_PASSES}}, cap {{TOPIC_CAP}}."),
    "workflow/40-outline.md": "Outline the piece from the ledger.",
    "workflow/99-publish.md": "Preview the finished piece.",
    # deploy is disabled in single-article but still a manifest node, so doctor requires its file:
    "workflow/deploy.md": "Deploy the finished edition.",
    "workflow/10-batch-plan.md": "Batch plan: sweep and assign.",
}


def _recipe(tmp_path, manifest=_WF_MANIFEST, bodies=_WF_BODIES):
    """Lay down a controlled newsroom recipe under tmp_path/prompts and return its root, for tests
    that point cz.PROMPTS_DIR at it: workflow/manifest.json + every node body, plus the persona and
    editorial guides. Every manifest node across all modes has a file on disk (including the
    per-mode-disabled ones like deploy), so cmd_doctor's node-files-present check passes."""
    root = tmp_path / "prompts"
    (root / "workflow").mkdir(parents=True)
    (root / "persona").mkdir()
    (root / "editorial").mkdir()
    (root / "workflow" / "manifest.json").write_text(json.dumps(manifest))
    for key, body in bodies.items():          # keys like "workflow/30-research.md"
        (root / key).write_text(body)
    (root / "persona" / "synthesize.md").write_text("SYNTH GUIDE")
    (root / "editorial" / "style.md").write_text("VOZ: declarativa. Reglas: sin clickbait.")
    return root


# ---- doctor: self-check the stack + skill package + the on-disk agentic recipe ----------------

def _doctor_req(services):
    """Fake _req for doctor: `services` maps a url-substring -> a status int (or an Exception to
    raise). doctor probes only publish /healthz, site /, and ComfyUI /system_stats over _req; the
    recipe checks read files under PROMPTS_DIR, so point that at a `_recipe` dir separately."""
    def fake_req(method, url, data=None, headers=None, timeout=60):
        for frag, resp in services.items():
            if frag in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp, b"ok"
        raise AssertionError(f"unexpected doctor probe: {url}")
    return fake_req


_ALL_UP = {"/healthz": 200, ":8080/": 302, "/system_stats": 200}


def test_cli_parses_doctor_verb():
    assert cz.build_parser().parse_args(["doctor"]).fn is cz.cmd_doctor


def test_doctor_all_green_over_the_real_package(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    monkeypatch.setattr(cz, "_req", _doctor_req(_ALL_UP))
    rc = cz.cmd_doctor(SimpleNamespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "[FAIL]" not in out
    assert "0 failure(s)" in out and "OK to operate." in out
    # it actually checked the real sub-skills and the on-disk recipe, not a stub
    assert "sub-skill authors routed + valid" in out
    assert "node files all present" in out
    assert "served by the brain" not in out


def test_doctor_fails_when_a_core_service_is_down(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    down = dict(_ALL_UP, **{"/healthz": urllib.error.URLError("refused")})
    monkeypatch.setattr(cz, "_req", _doctor_req(down))
    rc = cz.cmd_doctor(SimpleNamespace())
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAIL] publish backend" in out and "NOT ready" in out


def test_doctor_marks_comfyui_optional_when_down(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    down = dict(_ALL_UP, **{"/system_stats": urllib.error.URLError("refused")})
    monkeypatch.setattr(cz, "_req", _doctor_req(down))
    rc = cz.cmd_doctor(SimpleNamespace())
    out = capsys.readouterr().out
    assert rc == 0                                   # ComfyUI down must not fail the check
    assert "[WARN] ComfyUI" in out and "optional" in out


def test_set_prompt_persona_key_writes_the_file_in_place(monkeypatch, tmp_path):
    # Every prompt is a file: set-prompt writes the .md in place (git is the history), no network.
    root = _recipe(tmp_path)
    monkeypatch.setattr(cz, "PROMPTS_DIR", root)
    rc = cz.cmd_set_prompt(SimpleNamespace(key="persona/synthesize.md", body="GUIDE v4", body_file=""))
    assert rc == 0
    assert (root / "persona" / "synthesize.md").read_text() == "GUIDE v4"


# ---- the step gate: serve ONE workflow node at a time. The workflow prompts + manifest are
# on-disk FILES under PROMPTS_DIR, so `step` reads them directly (no server); these tests point
# cz.PROMPTS_DIR at a controlled `_recipe` and fill the numeric parameters from the real
# cli/workflow/parameters.json (bundled with the CLI).

_STEP_MANIFEST = {"modes": {"demo": ["a-one", "b-two", "c-three"], "batch": ["only-one"]},
                  "disabled": {"demo": ["b-two"]}}  # b-two disabled in demo, proves per-mode skip


def _ns(**over):
    base = dict(key="", mode="", list=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_cli_parses_step_verb():
    p = cz.build_parser()
    a = p.parse_args(["step"])
    assert a.fn is cz.cmd_step and a.key == "" and a.mode == "" and a.list is False
    a = p.parse_args(["step", "30-research", "--mode", "single-article"])
    assert a.key == "30-research" and a.mode == "single-article"
    assert p.parse_args(["step", "--list", "--mode", "daily"]).list is True


def test_fill_params_substitutes_and_is_null_safe():
    text = ("floor {{MIN_SOURCES}}, per-lean {{MIN_PER_TYPE}}, passes {{RESPIN_PASSES}}, "
            "cap {{TOPIC_CAP}}, keep {{UNKNOWN}}")
    out = cz.fill_params(
        text, {"MIN_SOURCES": 6, "MIN_PER_TYPE": 2, "RESPIN_PASSES": 2, "TOPIC_CAP": 12}
    )
    assert out == "floor 6, per-lean 2, passes 2, cap 12, keep {{UNKNOWN}}"


def test_mode_sequence_drops_disabled_and_rejects_unknown_mode():
    assert cz.mode_sequence(_STEP_MANIFEST, "demo") == ["a-one", "c-three"]  # b-two disabled in demo
    assert cz.mode_sequence(_STEP_MANIFEST, "batch") == ["only-one"]  # no disabled entry for batch
    with pytest.raises(SystemExit):
        cz.mode_sequence(_STEP_MANIFEST, "nope")


def test_mode_sequence_tolerates_a_flat_global_disabled_list():
    # A hand-edited manifest may use a flat list; it is treated as a global disable.
    m = {"modes": {"demo": ["a-one", "b-two"]}, "disabled": ["b-two"]}
    assert cz.mode_sequence(m, "demo") == ["a-one"]


def test_next_line_computes_next_then_done():
    seq = ["a-one", "c-three"]
    assert "step c-three --mode demo" in cz.next_line(seq, "a-one", "demo")
    assert cz.next_line(seq, "c-three", "demo").startswith("DONE")


def test_step_serves_first_node_and_next_from_the_recipe(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CENSURADO_WORK", raising=False)
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    assert cz.cmd_step(_ns(mode="single-article")) == 0
    out = capsys.readouterr().out
    assert "Load the author" in out                              # 15-pick-author body served
    assert "step 30-research --mode single-article" in out       # NEXT computed from the manifest


def test_step_stamps_the_current_node_key_and_position(monkeypatch, capsys, tmp_path):
    # A small-model driver must know which node it is ON, not only the NEXT one; the served node
    # stamps its own key + position (the Haiku end-to-end run showed a driver guessing node 1's id).
    monkeypatch.delenv("CENSURADO_WORK", raising=False)
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    assert cz.cmd_step(_ns(mode="single-article")) == 0
    out = capsys.readouterr().out
    assert "STEP 15-pick-author" in out          # the CURRENT node id, stamped up front
    assert "node 1 of" in out                     # and its position in the walk


def test_step_serves_the_portal_review_arrange_node(monkeypatch, capsys):
    # portal-review is a standalone single-node mode served straight from the REAL prompts (no
    # _recipe, no stack). It must carry the arrange rules a driver follows end to end: the day
    # loader, the no-gap orphan promotion, content-first pairing, and the --set-json write shape.
    monkeypatch.delenv("CENSURADO_WORK", raising=False)
    assert cz.cmd_step(_ns(mode="portal-review")) == 0
    low = capsys.readouterr().out.lower()
    assert "archive --day" in low          # the standalone day loader
    assert "never leave a gap" in low      # the no-gap orphan-promotion rule
    assert "content first" in low          # content pairing overrides the media checkerboard
    assert "--set-json" in low             # the portada write shape


def test_step_no_key_no_mode_serves_the_mode_picker(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    assert cz.cmd_step(_ns()) == 0
    out = capsys.readouterr().out
    assert out.strip()                                           # the picker body is served
    assert "when this step is done, run:" not in out            # ... with no COMPUTED next line


def test_step_list_matches_the_recipe_manifest(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    assert cz.cmd_step(_ns(mode="single-article", list=True)) == 0
    out = capsys.readouterr().out
    assert "15-pick-author" in out and "99-publish" in out
    assert "deploy" not in out                                   # deploy is disabled in single-article


def test_step_parameters_come_from_the_bundled_json():
    assert cz._fetch_params() == {
        "MIN_SOURCES": 6, "MIN_PER_TYPE": 2, "RESPIN_PASSES": 2, "TOPIC_CAP": 12}


def test_step_fills_every_setting_placeholder_in_a_served_node(monkeypatch, capsys, tmp_path):
    # 30-research carries {{MIN_SOURCES}}/{{MIN_PER_TYPE}}; the gate must leave none unfilled.
    monkeypatch.delenv("CENSURADO_WORK", raising=False)
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    assert cz.cmd_step(_ns(key="30-research", mode="single-article")) == 0
    out = capsys.readouterr().out
    assert "6" in out
    for leftover in ("{{MIN_SOURCES}}", "{{MIN_PER_TYPE}}", "{{RESPIN_PASSES}}", "{{TOPIC_CAP}}"):
        assert leftover not in out


def test_step_missing_node_is_fatal(monkeypatch, tmp_path):
    # an unknown node has no file under the recipe; the fetch turns that into a clear FATAL exit
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    with pytest.raises(SystemExit):
        cz._fetch_prompt_body("workflow/does-not-exist.md")


# ---- the ENFORCED gate: when the driver sets $CENSURADO_WORK, the step gate stops merely
# describing the walk and enforces it: an artifact gate (can't advance until the previous node
# saved its file) and a loop shield (can't re-fetch one node forever). Unset -> purely advisory.

def test_step_gate_blocks_advancing_without_the_required_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))
    with pytest.raises(SystemExit) as e:  # 40-outline needs 30-research's ledger.md first
        cz.cmd_step(_ns(key="40-outline", mode="single-article"))
    assert "BLOCKED" in str(e.value) and "30-research" in str(e.value)


def test_step_gate_lets_you_advance_once_the_artifact_exists(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))
    (tmp_path / "ledger.md").write_text("- fact one -> https://example.com/a\n- fact two -> https://x/b\n")
    assert cz.cmd_step(_ns(key="40-outline", mode="single-article")) == 0
    assert "Outline" in capsys.readouterr().out           # 40-outline body served


def test_step_gate_prints_the_artifact_target_for_a_producing_node(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))
    assert cz.cmd_step(_ns(key="30-research", mode="single-article")) == 0
    out = capsys.readouterr().out
    assert "ARTIFACT:" in out and "ledger.md" in out      # tells the model where to save


def test_step_gate_loop_shield_stops_refetching_the_same_node(monkeypatch, tmp_path):
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))
    for _ in range(cz._MAX_REFETCH):                       # a few re-fetches are allowed
        assert cz.cmd_step(_ns(key="15-pick-author", mode="single-article")) == 0
    with pytest.raises(SystemExit) as e:                   # one past the cap is stopped
        cz.cmd_step(_ns(key="15-pick-author", mode="single-article"))
    assert "STOP" in str(e.value)


def test_step_gate_is_advisory_when_no_work_dir_is_set(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CENSURADO_WORK", raising=False)
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    assert cz.cmd_step(_ns(key="40-outline", mode="single-article")) == 0  # no ledger needed
    assert "Outline" in capsys.readouterr().out


# ---- the archive verb: the repeat-news sweep's first stage. It lists an author's
# published articles LIGHT (title/subtitle/description/date, never a body) so the agent
# can judge repeats by date and description before spending context on a full article.

def test_cli_parses_archive_verb():
    p = cz.build_parser()
    a = p.parse_args(["archive", "author-a", "--q", "milei", "--since", "2026-06-01",
                      "--until", "2026-06-30", "--limit", "40"])
    assert a.fn is cz.cmd_archive and a.author == "author-a" and a.q == "milei"
    assert a.since == "2026-06-01" and a.until == "2026-06-30" and a.limit == 40


def test_cli_parses_archive_day_without_author():
    # `archive --day` needs no author (the whole day across authors), so the positional is optional.
    a = cz.build_parser().parse_args(["archive", "--day", "2026-07-01"])
    assert a.fn is cz.cmd_archive and a.author == "" and a.day == "2026-07-01"


def test_day_bound_extends_bare_dates_and_passes_timestamps():
    assert cz._day_bound("2026-06-01") == "2026-06-01T00:00:00Z"
    # the backend `to` filter is EXCLUSIVE (published_at < to), so an inclusive --until
    # day becomes the NEXT day's midnight, month/year rollovers included
    assert cz._day_bound("2026-06-01", end=True) == "2026-06-02T00:00:00Z"
    assert cz._day_bound("2026-06-30", end=True) == "2026-07-01T00:00:00Z"
    assert cz._day_bound("2026-12-31", end=True) == "2027-01-01T00:00:00Z"
    assert cz._day_bound("2026-06-01T12:30:00Z") == "2026-06-01T12:30:00Z"
    assert cz._day_bound("2026-06-01T12:30:00Z", end=True) == "2026-06-01T12:30:00Z"


def test_archive_lists_a_light_author_view(monkeypatch, capsys):
    calls = []
    listing = {"total": 2, "articles": [
        {"slug": "milei-veta-la-ley", "title": "Milei veta la ley", "section": "politics",
         "author": "author-a", "published_at": "2026-06-29T10:00:00Z",
         "topics": ["javier milei"],
         "metadata": {"subtitle": "Una bajada", "description": "Un standfirst."},
         "deleted": False, "content_hash": "h1"},
        {"slug": "otra-nota", "title": "Otra nota", "section": "politics",
         "author": "author-a", "published_at": "2026-06-28T10:00:00Z", "topics": [],
         "metadata": {}, "deleted": False, "content_hash": "h2"}]}

    def fake_api(method, path, payload=None, auth=True, base=None):
        calls.append((method, path))
        return 200, json.dumps(listing).encode()

    monkeypatch.setattr(cz, "api", fake_api)
    assert cz.cmd_archive(SimpleNamespace(author="author-a", q="milei",
                                          since="2026-06-01", until="2026-06-30",
                                          limit=40)) == 0
    method, path = calls[-1]
    assert method == "GET" and path.startswith("/articles?")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    assert qs["author"] == ["author-a"] and qs["q"] == ["milei"]
    # from = the day's start; to = the NEXT day's start (the backend `to` is exclusive,
    # so this keeps the whole --until day included)
    assert qs["from"] == ["2026-06-01T00:00:00Z"] and qs["to"] == ["2026-07-01T00:00:00Z"]
    assert qs["limit"] == ["40"]
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 2
    first = out["articles"][0]
    assert first["slug"] == "milei-veta-la-ley" and first["title"] == "Milei veta la ley"
    assert first["subtitle"] == "Una bajada" and first["description"] == "Un standfirst."
    assert first["published_at"] == "2026-06-29T10:00:00Z"
    assert "body" not in first and "content_hash" not in first  # LIGHT, stage one only
    assert out["articles"][1]["description"] == ""  # missing metadata coalesces clean


def test_archive_omits_empty_filters_from_the_query(monkeypatch):
    calls = []
    monkeypatch.setattr(cz, "api",
                        lambda m, p, payload=None, auth=True, base=None:
                        (calls.append((m, p)) or (200, b'{"total":0,"articles":[]}')))
    cz.cmd_archive(SimpleNamespace(author="author-a", q="", since="", until="", limit=0))
    _, path = calls[-1]
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    assert set(qs) == {"author"}  # no q/from/to/limit noise when unset


def test_archive_day_lists_the_whole_day_across_authors(monkeypatch, capsys):
    # `archive --day` (no author) is the portada arrange loader: every piece published that
    # UTC day across all authors, LIGHT with has_media, in one call. The day bounds it into
    # from = day start, to = NEXT day start (the backend `to` is exclusive), and sends NO author.
    calls = []
    listing = {"total": 2, "articles": [
        {"slug": "lead", "title": "Lead", "section": "politics", "author": "a-one",
         "published_at": "2026-07-01T20:00:00Z", "topics": ["milei"], "has_media": True,
         "metadata": {"subtitle": "dek", "description": "desc"}},
        {"slug": "text", "title": "Text", "section": "economy", "author": "b-two",
         "published_at": "2026-07-01T08:00:00Z", "topics": [], "has_media": False,
         "metadata": {}}]}

    def fake_api(method, path, payload=None, auth=True, base=None):
        calls.append((method, path))
        return 200, json.dumps(listing).encode()

    monkeypatch.setattr(cz, "api", fake_api)
    rc = cz.cmd_archive(SimpleNamespace(author="", day="2026-07-01", q="", since="", until="", limit=0))
    assert rc == 0
    method, path = calls[-1]
    assert method == "GET" and path.startswith("/articles?")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    assert "author" not in qs                       # the whole day, every author
    assert qs["from"] == ["2026-07-01T00:00:00Z"]   # day start
    assert qs["to"] == ["2026-07-02T00:00:00Z"]     # next day start, exclusive
    out = json.loads(capsys.readouterr().out)
    assert {a["slug"]: a["has_media"] for a in out["articles"]} == {"lead": True, "text": False}


def test_archive_exits_on_http_error(monkeypatch):
    monkeypatch.setattr(cz, "api",
                        lambda m, p, payload=None, auth=True, base=None: (401, b"{}"))
    with pytest.raises(SystemExit):
        cz.cmd_archive(SimpleNamespace(author="author-a", q="", since="", until="", limit=0))
