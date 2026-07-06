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


@pytest.fixture(autouse=True)
def _isolate_default_work(monkeypatch, tmp_path):
    """Redirect the module's default scratch dir into a per-test temp dir, so a walk with no
    $CENSURADO_WORK set (which now defaults + enforces) never touches the real /tmp/censurado-work."""
    monkeypatch.setattr(cz, "_DEFAULT_WORK", tmp_path / "_default_work")


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


def test_build_publish_payload_card_authored():
    # An authored --card-type builds metadata.card: the front-page card, decoupled from the body.
    p = cz.build_publish_payload(_pub_ns(card_type="youtube", card_src="eD099-68xvw"))
    assert p["metadata"]["card"] == {"type": "youtube", "src": "eD099-68xvw"}


def test_build_publish_payload_card_text_carries_no_media():
    # A text card shows no picture, even when the body embeds a post/video (card != content).
    p = cz.build_publish_payload(_pub_ns(card_type="text", body="texto\n\n{{tweet:1}}"))
    assert p["metadata"]["card"] == {"type": "text"}


def test_build_publish_payload_card_image_borrows_hero_when_no_src():
    # An image card with no explicit --card-src borrows the resolved hero still + its alt.
    p = cz.build_publish_payload(_pub_ns(card_type="image", image=_FULL_MEDIA, image_alt="una ilustración"))
    assert p["metadata"]["card"] == {"type": "image", "src": _FULL_MEDIA, "alt": "una ilustración"}


def test_build_publish_payload_derives_text_card_when_no_media():
    # No --card-type and no media: preview still writes an explicit card (unified format) = text.
    assert cz.build_publish_payload(_pub_ns()).get("metadata", {})["card"] == {"type": "text"}


def test_build_publish_payload_derives_youtube_card_from_body_video():
    # No --card-type: a body {{video:<id>}} derives a youtube card carrying the id, so the listing
    # can show the poster (this is the treguatango case that had no metadata media).
    p = cz.build_publish_payload(_pub_ns(body="texto\n\n{{video:eD099-68xvw}}\n\nfin"))
    assert p["metadata"]["card"] == {"type": "youtube", "src": "eD099-68xvw"}


def test_build_publish_payload_derives_image_card_from_hero():
    # No --card-type but a hero image: derives an image card borrowing the hero + its alt.
    p = cz.build_publish_payload(_pub_ns(image=_FULL_MEDIA, image_alt="una ilustración"))
    assert p["metadata"]["card"] == {"type": "image", "src": _FULL_MEDIA, "alt": "una ilustración"}


def test_build_publish_payload_card_type_invalid_rejected():
    with pytest.raises(cz.ToolError):
        cz.build_publish_payload(_pub_ns(card_type="reel"))


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
    # still print the CORRECT url (flagged "staged", the confirmation), not swallow it.
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
    assert f"PREVIEW: {cz.SITE}/a/mi-nota-abcd1234/  [staged" in out.out


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


def test_archive_light_output_includes_card_type(monkeypatch, capsys):
    # cmd_archive lists an author's pieces LIGHT via api() (GET /articles); prove it
    # carries the server-derived card_type LABEL per piece (what the card shows), which the
    # portada layout walk reads to alternate media and text cards without opening each body.
    articles = {
        "total": 2,
        "articles": [
            {"slug": "con-foto", "title": "Con foto", "section": "tech",
             "published_at": "2026-07-01T10:00:00Z", "topics": ["ia"],
             "card_type": "image", "metadata": {"subtitle": "s", "description": "d"}},
            {"slug": "solo-texto", "title": "Solo texto", "section": "politica",
             "published_at": "2026-07-01T09:00:00Z", "topics": [],
             "card_type": "text", "metadata": {}},
        ],
    }
    monkeypatch.setattr(cz, "api",
                        lambda m, p, payload=None, auth=True, base=None: (200, json.dumps(articles).encode()))
    rc = cz.cmd_archive(SimpleNamespace(author="vector-omni", q=None, since=None, until=None, limit=None))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert {a["slug"]: a["card_type"] for a in out["articles"]} == {"con-foto": "image", "solo-texto": "text"}


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


def test_step_serves_the_topic_cleanse_walk(monkeypatch, capsys):
    # topic-cleanse is a standalone single-node mode served from the REAL prompts (no _recipe,
    # no stack). It must carry both merge halves and the safety gates a driver follows.
    monkeypatch.delenv("CENSURADO_WORK", raising=False)
    assert cz.cmd_step(_ns(mode="topic-cleanse")) == 0
    low = capsys.readouterr().out.lower()
    assert "topics cleanse" in low and "--apply" in low   # article half (hash-safe brain writer)
    assert "profile-topics" in low                          # author-chip half
    assert "no model runs" in low                           # detection is agent-side
    assert "over-merg" in low                               # the over-merge hazard gate


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


def test_step_gate_enforces_by_default_without_an_env_work_dir(monkeypatch, tmp_path):
    # With no $CENSURADO_WORK the gate is NOT advisory any more: it defaults the scratch dir and
    # enforces, so skipping ahead still BLOCKS (a weak driver otherwise blows past the ledger/draft).
    monkeypatch.delenv("CENSURADO_WORK", raising=False)
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    with pytest.raises(SystemExit) as e:  # 40-outline still needs 30-research's ledger.md first
        cz.cmd_step(_ns(key="40-outline", mode="single-article"))
    assert "BLOCKED" in str(e.value) and "30-research" in str(e.value)


def test_step_prints_the_work_dir_up_front_when_unset(monkeypatch, capsys, tmp_path):
    # The served node names the scratch dir so a weak driver writes there directly instead of
    # running echo/ls/doctor to hunt for it (the observed failure mode).
    monkeypatch.delenv("CENSURADO_WORK", raising=False)
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    assert cz.cmd_step(_ns(mode="single-article")) == 0
    out = capsys.readouterr().out
    assert "WORK DIR:" in out and str(cz._DEFAULT_WORK) in out


def test_step_fresh_walk_start_wipes_the_default_scratch(monkeypatch, capsys, tmp_path):
    # Starting a fresh walk on the shared default dir clears a prior piece's artifacts, so a serial
    # batch cannot advance article 2 on article 1's leftover ledger/draft.
    monkeypatch.delenv("CENSURADO_WORK", raising=False)
    monkeypatch.setattr(cz, "PROMPTS_DIR", _recipe(tmp_path))
    cz._DEFAULT_WORK.mkdir(parents=True, exist_ok=True)
    stale = cz._DEFAULT_WORK / "ledger.md"
    stale.write_text("- old article's ledger that must not leak -> https://x/y\n")
    assert cz.cmd_step(_ns(mode="single-article")) == 0  # start the walk (no explicit node)
    assert not stale.exists()  # the fresh start wiped it


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
    # UTC day across all authors, LIGHT with card_type, in one call. The day bounds it into
    # from = day start, to = NEXT day start (the backend `to` is exclusive), and sends NO author.
    calls = []
    listing = {"total": 2, "articles": [
        {"slug": "lead", "title": "Lead", "section": "politics", "author": "a-one",
         "published_at": "2026-07-01T20:00:00Z", "topics": ["milei"], "card_type": "youtube",
         "metadata": {"subtitle": "dek", "description": "desc"}},
        {"slug": "text", "title": "Text", "section": "economy", "author": "b-two",
         "published_at": "2026-07-01T08:00:00Z", "topics": [], "card_type": "text",
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
    assert {a["slug"]: a["card_type"] for a in out["articles"]} == {"lead": "youtube", "text": "text"}


def test_cli_parses_topics_verb():
    a = cz.build_parser().parse_args(["topics", "--limit", "50"])
    assert a.fn is cz.cmd_topics and a.limit == 50
    assert cz.build_parser().parse_args(["topics"]).limit == 0  # 0 -> cmd default 1000


def test_topics_inventories_distinct_tags_with_counts_and_slugs(monkeypatch, capsys):
    # `topics` aggregates the free-text article tags across the corpus: per-tag count + the
    # slugs carrying it, sorted by count then name. The read side of topic normalization: it
    # surfaces variants (here `Javier-Milei` alongside `milei`) so an operator can plan a merge.
    calls = []
    listing = {"total": 3, "articles": [
        {"slug": "a", "topics": ["milei", "economia"]},
        {"slug": "b", "topics": ["milei", "Javier-Milei"]},
        {"slug": "c", "topics": ["economia", "", "milei"]},
    ]}

    def fake_api(method, path, payload=None, auth=True, base=None):
        calls.append((method, path))
        return 200, json.dumps(listing).encode()

    monkeypatch.setattr(cz, "api", fake_api)
    assert cz.cmd_topics(SimpleNamespace(limit=0)) == 0
    method, path = calls[-1]
    assert method == "GET" and path.startswith("/articles?")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    assert qs["limit"] == ["1000"]  # 0 -> the default cap
    out = json.loads(capsys.readouterr().out)
    assert out["total_tags"] == 3 and out["articles_scanned"] == 3
    # Sorted by count desc, then name: milei(3), economia(2), Javier-Milei(1).
    assert [t["tag"] for t in out["topics"]] == ["milei", "economia", "Javier-Milei"]
    milei = out["topics"][0]
    assert milei["count"] == 3 and milei["slugs"] == ["a", "b", "c"]
    assert all(t["tag"] for t in out["topics"])  # blank tags dropped, not counted


def test_topics_warns_when_the_corpus_exceeds_the_scan(monkeypatch, capsys):
    # If total > scanned, the inventory is partial; it must SAY so on stderr, not silently
    # truncate (a merge planned off a partial surface would miss variants).
    listing = {"total": 500, "articles": [{"slug": "a", "topics": ["milei"]}]}
    monkeypatch.setattr(cz, "api",
                        lambda m, p, payload=None, auth=True, base=None: (200, json.dumps(listing).encode()))
    cz.cmd_topics(SimpleNamespace(limit=1))
    assert "scanned 1 of 500" in capsys.readouterr().err


def test_cli_parses_remove_topic_verb():
    a = cz.build_parser().parse_args(["remove-topic", "javier-milei", "--yes"])
    assert a.fn is cz.cmd_remove_topic and a.slug == "javier-milei" and a.yes is True


def test_remove_topic_refuses_without_yes(monkeypatch):
    # Guarded like remove-author: without --yes it exits and issues NO request.
    calls = []
    monkeypatch.setattr(cz, "api", lambda *args, **kw: calls.append(args) or (204, b""))
    with pytest.raises(SystemExit):
        cz.cmd_remove_topic(SimpleNamespace(slug="javier-milei", yes=False))
    assert not calls  # nothing reached the backend


def test_remove_topic_tombstones_via_delete(monkeypatch):
    calls = []

    def fake_api(method, path, payload=None, auth=True, base=None):
        calls.append((method, path))
        return 204, b""

    monkeypatch.setattr(cz, "api", fake_api)
    assert cz.cmd_remove_topic(SimpleNamespace(slug="javier-milei", yes=True)) == 0
    assert calls[-1] == ("DELETE", "/topics/javier-milei")


def test_remove_topic_reports_a_missing_slug(monkeypatch):
    monkeypatch.setattr(cz, "api", lambda *a, **k: (404, b""))
    assert cz.cmd_remove_topic(SimpleNamespace(slug="nope", yes=True)) == 1


def test_archive_exits_on_http_error(monkeypatch):
    monkeypatch.setattr(cz, "api",
                        lambda m, p, payload=None, auth=True, base=None: (401, b"{}"))
    with pytest.raises(SystemExit):
        cz.cmd_archive(SimpleNamespace(author="author-a", q="", since="", until="", limit=0))


# ----- status (the stack-liveness verb) -----
# `status` probes the four services independently and never raises: `_probe` is the single
# transport chokepoint, so these stub `cz._probe` and drive the verb through to its exit code +
# printed report. No stack, no network. (A specific piece is confirmed by preview's PREVIEW:
# link, not by status, so status carries no per-article --slug path anymore.)

def _status_ns(**over):
    base = dict(local_only=False, json=False)
    base.update(over)
    return SimpleNamespace(**base)


def _probe_map(mapping, default=(True, 200, "HTTP 200")):
    """A fake `_probe` that decides by a substring of the probed url: mapping is
    {url_substring: (up, code)}; the first match wins, else `default`."""
    def fake(url, ok_set, timeout=5):
        for frag, (up, code) in mapping.items():
            if frag in url:
                return up, code, (f"HTTP {code}" if code else "unreachable (refused)")
        return default
    return fake


def test_status_all_up_exits_0(monkeypatch, capsys):
    monkeypatch.setattr(cz, "_probe", _probe_map({}))  # everything up
    monkeypatch.setattr(cz, "PUBLIC_URL", "https://pub.example")
    rc = cz.cmd_status(_status_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "ONLINE" in out
    assert "[OK] backend" in out and "[OK] site" in out


def test_status_backend_down_is_offline_exit_1(monkeypatch, capsys):
    # The backend is CORE: down -> FAIL + OFFLINE + exit 1, even though the others are up.
    monkeypatch.setattr(cz, "_probe", _probe_map({"healthz": (False, 0)}))
    monkeypatch.setattr(cz, "PUBLIC_URL", "https://pub.example")
    rc = cz.cmd_status(_status_ns())
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAIL] backend" in out and "OFFLINE" in out


def test_status_public_and_comfy_down_are_warn_not_fail(monkeypatch, capsys):
    # public + comfy are non-core: down -> WARN, core still up -> ONLINE + exit 0.
    monkeypatch.setattr(cz, "_probe",
                        _probe_map({"pub.example": (False, 0), "system_stats": (False, 0)}))
    monkeypatch.setattr(cz, "PUBLIC_URL", "https://pub.example")
    rc = cz.cmd_status(_status_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "[WARN] public" in out and "[WARN] comfyui" in out and "ONLINE" in out


def test_status_local_only_skips_public_probe(monkeypatch, capsys):
    seen = []
    def fake(url, ok_set, timeout=5):
        seen.append(url)
        return True, 200, "HTTP 200"
    monkeypatch.setattr(cz, "_probe", fake)
    monkeypatch.setattr(cz, "PUBLIC_URL", "https://pub.example")
    rc = cz.cmd_status(_status_ns(local_only=True))
    assert rc == 0
    assert not any("pub.example" in u for u in seen)  # public origin never probed
    assert "public" not in capsys.readouterr().out


def test_status_has_no_per_article_slug_flag():
    # The per-article `status --slug` verification was removed: a successful `preview` prints the
    # piece's own PREVIEW: link, which is the confirmation. status is now stack-liveness only, so
    # the parser must reject --slug rather than silently accept a dead flag.
    with pytest.raises(SystemExit):
        cz.build_parser().parse_args(["status", "--slug", "mi-nota"])


def test_status_json_shape(monkeypatch, capsys):
    monkeypatch.setattr(cz, "_probe", _probe_map({"pub.example": (False, 0)}))
    monkeypatch.setattr(cz, "PUBLIC_URL", "https://pub.example")
    rc = cz.cmd_status(_status_ns(json=True))
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["ready"] is True
    assert "article_ok" not in data and "article" not in data  # per-article verify is gone
    assert data["services"]["backend"]["up"] is True
    assert data["services"]["public"]["up"] is False


# ----- deploy (the one infra verb; wraps deploy/deploy-cdn.sh) -----

def test_deploy_refuses_without_yes(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(cz.subprocess, "run", lambda *a, **k: called.append(a) or None)
    rc = cz.cmd_deploy(SimpleNamespace(yes=False))
    err = capsys.readouterr().err
    assert rc == 1
    assert "--yes" in err and not called  # refused before launching the script


def test_deploy_runs_script_and_reports_success(monkeypatch, capsys):
    captured = {}
    def fake_run(cmd, cwd=None, check=False):
        captured["cmd"], captured["cwd"] = cmd, cwd
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cz.subprocess, "run", fake_run)
    monkeypatch.setattr(cz, "PUBLIC_URL", "https://pub.example")
    rc = cz.cmd_deploy(SimpleNamespace(yes=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["cmd"][0] == "bash" and captured["cmd"][1].endswith("deploy/deploy-cdn.sh")
    # steers verification to opening the piece's link (+ plain status), not to re-deploying
    assert "deploy OK" in out and "public link" in out and "status" in out


def test_deploy_relays_failure_without_workaround(monkeypatch, capsys):
    monkeypatch.setattr(cz.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=2))
    rc = cz.cmd_deploy(SimpleNamespace(yes=True))
    err = capsys.readouterr().err
    assert rc == 1
    assert "FAILED" in err
    # the failure guidance must forbid the exact workarounds the looping agent tried
    assert "Do NOT run generate" in err and "chmod" in err


def test_preview_consumes_the_work_hero_so_it_cannot_leak(monkeypatch, tmp_path):
    # After a successful stage, the work-dir image.json is deleted so a LATER preview of a
    # different piece cannot inherit this one's hero from the shared default work dir (the leak
    # that attached one article's still to the next, video, piece).
    monkeypatch.setenv("CENSURADO_WORK", str(tmp_path))
    (tmp_path / "image.json").write_text(
        json.dumps({"image": "/media/" + "a" * 64 + ".png", "image_alt": "x"}))
    monkeypatch.setattr(cz, "_req",
                        lambda *a, **k: (201, json.dumps({"id": "1", "slug": "s"}).encode()))
    monkeypatch.setattr(cz, "token", lambda: "t")
    assert cz.cmd_publish(_preview_ns()) == 0
    assert not (tmp_path / "image.json").exists()  # consumed on publish
