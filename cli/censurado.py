#!/usr/bin/env python3
"""censurado.py: the mechanical plumbing for the Censurado publish/edit lane.

A CLI agent writes the article itself (see cli/SKILL.md, the editorial bar) and uses
this helper ONLY for the boilerplate that is identical every time and easy to get wrong:
reading the operator token from .env (never printed), building strict JSON, the
POST /articles publish, the GET -> modify -> PUT edit round-trip, media upload, turning
a tweet or a Trump "truth" into the exact snapshot object the {{tweet:id}} card needs,
and rendering an art-directed hero image through ComfyUI.

It re-implements no editorial judgement. WHAT to write, WHERE a widget marker goes, and
HOW to art-direct an image stay with the agent (see cli/skills/media/SKILL.md for the
image brief). This only removes the per-article throwaway script. Stdlib only, no install.

It is also the agent's data-facing toolbox: all authors and sources live in the backend
(the single content store), and the prompts are on-disk FILES, so an agent discovers them at
runtime: `personas` lists the authors that exist, `persona <id>` fetches one author's full
record (voice + beat + sources), `prompt <key>` reads a workflow prompt file, `portals`/
`sources` read and wire an author's outlets, and `create-author` persists a new author the
agent wrote from the synthesize prompt. An empty backend means zero authors until the
operator (or an agent) creates one. Authors/sources go over the SAME publish backend as
articles (Bearer-authed, admin:write), so there is no separate config service to run.

The `step` verb is the workflow's step gate: it serves ONE newsroom node at a time (the
`workflow/*` nodes, ordered by `workflow/manifest.json`), fills the numeric parameters, and
prints the exact NEXT command, so the agent walks the editorial loop step by step instead of
reading the whole brief and one-shotting it. The agentic-workflow prompts (the nodes + the
manifest + the persona/editorial prompts) are the newsroom recipe: plain FILES under this
repo's `prompts/` dir (git is their version history). `step`/`prompt`/`set-prompt` read
and write those files directly (no server), resolving the dir from `CENSURADO_PROMPTS_DIR`
(default: this repo's `prompts/`, a sibling of this cli/ dir). The numeric parameters travel
WITH this package (`cli/workflow/parameters.json`). See cli/SKILL.md (the resolver + sub-skills)
for the agent surface, `python3 cli/censurado.py <cmd> --help` for flags.
"""
import argparse, hashlib, html, json, os, re, shutil, subprocess, sys, tempfile, time, unicodedata, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# Repo-relative defaults so a fresh clone runs with no edits: the repo root is the parent
# of this cli/ dir, and the ComfyUI graph template ships beside this script under
# cli/templates/. Both stay overridable by env, so a non-standard layout just sets the var.
_REPO = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ.get("CENSURADO_ENV", str(_REPO / ".env")))
# The newsroom recipe (workflow nodes + manifest + persona/editorial prompts) is a set of
# on-disk files in this repo's prompts/ dir, a sibling of this cli/ dir. The step gate reads
# them locally (no server); override the dir for a non-standard layout.
PROMPTS_DIR = Path(os.environ.get(
    "CENSURADO_PROMPTS_DIR", str(_REPO / "prompts")))
FLUX_TEMPLATE = Path(os.environ.get(
    "CENSURADO_FLUX_TEMPLATE",
    str(Path(__file__).resolve().parent / "templates" / "flux2_klein_t2i.json")))
# flux2_klein node ids: positive prompt, noise seed, latent dims, scheduler.
_N_POS, _N_NOISE, _N_LATENT, _N_SCHED = "4", "9", "6", "7"


def _env_value(key, default=""):
    """Read a plain scalar from the environment, falling back to a `KEY=value` line in .env,
    then to `default`. A trailing inline `# comment` and surrounding quotes are stripped so a
    `.env.example`-style annotated line still parses. token() reuses this scan for the
    operator secret, adding its FATAL-on-missing."""
    v = os.environ.get(key, "").strip()
    if v:
        return v
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                v = line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
                if v:
                    return v
    return default


def _local(var, port_key, default_port):
    """Where a local service listens. The explicit `CENSURADO_*` var wins; otherwise follow the
    HOST PORT the stack was actually started on, which is the `*_PORT` value in .env (the same
    one docker-compose binds). Hardcoding host ports here meant that moving a service off a
    default port (a collision with another app on the box is the common case) left the CLI
    probing the wrong port: `status` reported the portal down, and every `preview` printed a
    link that was not the site."""
    explicit = os.environ.get(var, "").strip()
    if explicit:
        return explicit.rstrip("/")
    port = _env_value(port_key, str(default_port))
    if not port.isdigit():
        port = str(default_port)
    return f"http://127.0.0.1:{port}"


PUBLISH = _local("CENSURADO_PUBLISH", "PUBLISH_PORT", 8082)
COMFY = _local("CENSURADO_COMFY", "COMFYUI_PORT", 8188)
# The generated static site (nginx). `preview` resolves a staged article's live permalink
# here so the operator (and a driving agent) can open it; overridable for a non-local site.
SITE = _local("CENSURADO_SITE", "SITE_PORT", 8123)

# The PUBLIC production origin (Cloudflare Pages). `status` probes it to answer "did the last
# deploy land / is the live site up". CENSURADO_PUBLIC_URL wins, else DEPLOY_BASE_URL from the
# env/.env (the same var deploy-cdn.sh reads), else the project default. Trailing / trimmed.
PUBLIC_URL = (os.environ.get("CENSURADO_PUBLIC_URL", "").strip()
              or _env_value("DEPLOY_BASE_URL", "https://elcensuradoweb.com")).rstrip("/")


def token():
    """Operator token from $NEWSROOM_OPERATOR_TOKEN or .env (the same tolerant scan as
    _env_value, so a quoted, indented, or `# comment`-annotated line still parses). Never
    printed."""
    t = _env_value("NEWSROOM_OPERATOR_TOKEN")
    if not t:
        sys.exit("FATAL: no NEWSROOM_OPERATOR_TOKEN (set it in the env or .env)")
    return t


class ToolError(Exception):
    """An agent-facing failure: a short, ACTIONABLE message the model can read and correct from.
    main() turns it into a single `ERROR: ...` line plus a non-zero exit, never a traceback and
    never a hang. Raise it (via fail()) at every boundary that can meet bad input, an unreachable
    service, or a malformed response. It subclasses Exception (not SystemExit) so the existing
    best-effort `except Exception` guards still degrade gracefully around it."""


def fail(msg):
    """Abort the current verb with a clean agent-facing message (raises ToolError)."""
    raise ToolError(msg)


def _req(method, url, data=None, headers=None, timeout=60):
    """One HTTP round-trip. Returns (status, body_bytes) for ANY HTTP response, including 4xx/5xx,
    so callers branch on the code. A transport failure (service down, DNS, refused, timeout) is not
    a status: it becomes a clean ToolError instead of a traceback or a hang (every call has a finite
    timeout)."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", None) or e
        fail(f"cannot reach {url} ({reason}). Is the stack up? From the repo root run "
             f"`docker compose up -d publish generate site` (plus the comfy "
             f"service for image work), then retry.")


def _read_file(path, what="file", binary=False):
    """Read a caller-supplied file, turning the filesystem's exceptions into ONE clean, actionable
    ToolError (empty path / missing / is-a-directory / unreadable / bad encoding). Never leaks a
    traceback for a mistyped --*-file."""
    if not path:
        fail(f"no {what} path given.")
    try:
        p = Path(path)
        return p.read_bytes() if binary else p.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"{what} not found: {path!r} (paths are read from where you run the CLI). "
             f"Check the path and retry.")
    except IsADirectoryError:
        fail(f"{what} is a directory, not a file: {path!r}.")
    except (PermissionError, OSError, UnicodeDecodeError) as e:
        fail(f"cannot read {what} {path!r}: {e}.")


def _json(data, what="value", want=None):
    """Parse JSON (str or bytes) with a clean ToolError on failure, and OPTIONALLY assert the
    top-level type (want=dict, want=list, or a tuple of types). This is the single guard for every
    place that would otherwise `json.loads(...)` model-supplied text or a service response and
    traceback on garbage, a non-JSON error page, or the wrong shape (a list where a dict is
    expected). Returns the parsed value."""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", "replace")
    try:
        val = json.loads(data)
    except (json.JSONDecodeError, TypeError) as e:
        snippet = data if isinstance(data, str) else str(data)
        snippet = (snippet[:160] + "...") if len(snippet) > 160 else snippet
        fail(f"{what} is not valid JSON ({e}). Got: {snippet!r}")
    if want is not None and not isinstance(val, want):
        names = want.__name__ if isinstance(want, type) else "/".join(t.__name__ for t in want)
        fail(f"{what} must be a JSON {names}, but got a {type(val).__name__}.")
    return val


def api(method, path, payload=None, auth=True, base=None):
    headers = {}
    if auth:
        headers["Authorization"] = "Bearer " + token()
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    return _req(method, (base or PUBLISH) + path, data=data, headers=headers)


def _split(v):
    """Comma-separated string or list -> a clean list of trimmed, non-empty items."""
    if not v:
        return []
    out = v if isinstance(v, list) else re.split(r"\s*,\s*", v.strip())
    return [x for x in (s.strip() for s in out) if x]


def _slugify(text):
    """A stable ASCII slug from a display name, for a NEW author's handle when the persona
    JSON carries no explicit id: transliterate accents, lowercase, collapse every run of
    non-alphanumerics to one hyphen, trim. Mirrors the platform's slug convention closely
    enough for a fresh handle (existing authors already carry their id)."""
    norm = unicodedata.normalize("NFKD", text or "")
    ascii_ = norm.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_)).strip("-")


def _authors(include_deleted=False):
    """The backend author registry (GET /authors, Bearer-authed). Returns the list of author
    records (authorJSON: handle/name/bio/avatar/gender/about/style/topics/sources/metadata).
    The backend owns all authors now; there is no separate config service. It has no single-
    author route, so a caller that wants one author filters this list by handle."""
    path = "/authors" + ("?include_deleted=true" if include_deleted else "")
    st, body = api("GET", path)
    if st != 200:
        fail(f"cannot read the author registry (GET /authors HTTP {st}). Is the backend up at "
             f"{PUBLISH} and the operator token valid?")
    return _json(body, "the authors response", want=dict).get("authors", [])


def _author(handle, include_deleted=False):
    """One author's full record by handle, or {} when absent. The backend has no single-
    author GET, so this reads the registry and filters."""
    for a in _authors(include_deleted=include_deleted):
        if isinstance(a, dict) and a.get("handle") == handle:
            return a
    return {}


def _author_json_to_input(rec):
    """Map an author's read-back record (authorJSON) to a full authorInput for a READ-
    MODIFY-WRITE re-send. The backend has no partial update: POST /authors replaces every
    scalar column wholesale, so an edit must re-send the COMPLETE row (name/bio/avatar/
    gender/about/style + topics + the metadata tail) or it blanks whatever it leaves out.
    The sources pointer is OMITTED (a nil pointer leaves the author_sources join untouched;
    edit the join with `sources --set`)."""
    body = {
        "handle": rec.get("handle", ""),
        "name": rec.get("name", ""),
        "bio": rec.get("bio", ""),
        "avatar": rec.get("avatar", ""),
        "gender": rec.get("gender", ""),
        "about": rec.get("about", ""),
        "style": rec.get("style", ""),
    }
    if rec.get("topics"):
        body["topics"] = list(rec["topics"])
    meta = rec.get("metadata")
    if isinstance(meta, dict) and meta:
        body["metadata"] = dict(meta)
    return body


def _frontmatter_map(text):
    """The leading `--- ... ---` YAML frontmatter of a SKILL.md as a {key: scalar} map (only
    the simple scalars name/description are needed, so no YAML dependency). `doctor` uses this
    to check each sub-skill's name matches its directory."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out, key = {}, None
    for line in m.group(1).splitlines():
        top = re.match(r"^([A-Za-z_][\w-]*):\s?(.*)$", line)
        if top:
            key = top.group(1)
            out[key] = top.group(2).strip()
        elif key and line.strip():                      # a folded/continued scalar (>- block)
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


# ---- snapshot capture (the fiddly, generalistic part) -----------------------

def _status_id(ref, kind="post"):
    """The numeric status id from a raw id or an x.com / truthsocial.com status URL, or a clean
    error if the ref carries none (a common model slip: a handle, a search phrase, an empty string).
    Fails BEFORE any network call, so a bad ref never becomes a wasted request or a cryptic 404.
    Accepts a bare numeric id of ANY length (real ids are 18-19 digits, but historic ones are short,
    e.g. Jack's first tweet is id 20), and pulls the id out of a `.../status/<id>` (or Mastodon
    `.../statuses/<id>`) URL."""
    s = str(ref or "").strip()
    if s.isdigit():
        return s
    m = re.search(r"/status(?:es)?/(\d+)", s) or re.search(r"(\d{6,})", s)
    if not m:
        fail(f"no {kind} id found in {ref!r}. Pass the numeric status id, or the full status URL "
             f"(e.g. https://x.com/<user>/status/<id>).")
    return m.group(1)


def map_tweet(t):
    """An fxtwitter `.tweet` object -> the {{tweet:id}} snapshot (X)."""
    a = t.get("author") or {}
    if not isinstance(a, dict):
        a = {}
    snap = {
        "id": str(t.get("id", "")),
        "handle": a.get("screen_name", ""),
        "name": a.get("name", ""),
        "text": t.get("text", ""),
        "url": t.get("url", ""),
        "avatar": a.get("avatar_url", ""),
        "created_at": t.get("created_at", ""),
        "created_timestamp": t.get("created_timestamp"),
        "verified": bool((a.get("verification") or {}).get("verified", False)),
        "erased": False,
    }
    for out, src in (("replies", "replies"), ("retweets", "retweets"),
                     ("likes", "likes"), ("views", "views"), ("bookmarks", "bookmarks")):
        if t.get(src) is not None:
            snap[out] = t[src]
    return {k: v for k, v in snap.items() if v is not None}


def capture_tweet(ref, raw=None):
    if raw is None:
        st, body = _req("GET", "https://api.fxtwitter.com/i/status/" + _status_id(ref, "tweet"),
                        headers={"User-Agent": "Mozilla/5.0"})
        if st != 200:
            fail(f"fxtwitter returned HTTP {st} for {ref!r}. Check the X post id/URL is real and "
                 f"public (it may be deleted, private, or the id is wrong).")
        raw = _json(body, "the fxtwitter response", want=dict)
    if not isinstance(raw, dict):
        fail("the saved fxtwitter snapshot must be a JSON object.")
    t = raw.get("tweet", raw)
    if not isinstance(t, dict):
        fail("the fxtwitter response carries no usable tweet object.")
    return map_tweet(t)


def _strip_html(s):
    s = s if isinstance(s, str) else ""
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</p>\s*<p>", "\n\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def _iso_epoch(iso):
    if not isinstance(iso, str) or not iso:
        return None
    s = re.sub(r"\.\d+", "", iso.replace("Z", "+00:00"))
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except ValueError:
        return None


def map_truth(s):
    """A Truth Social (Mastodon) status -> the {{tweet:id}} snapshot.
    Truth Social carries no views or bookmarks, so those stats are simply omitted."""
    a = s.get("account") or {}
    if not isinstance(a, dict):
        a = {}
    snap = {
        "id": str(s.get("id", "")),
        "handle": a.get("username", ""),
        "name": a.get("display_name", ""),
        "text": _strip_html(s.get("content", "")),
        "url": s.get("url", ""),
        "avatar": a.get("avatar", "") or a.get("avatar_static", ""),
        "created_at": s.get("created_at", ""),
        "created_timestamp": _iso_epoch(s.get("created_at", "")),
        "verified": bool(a.get("verified", False)),
        "erased": False,
    }
    for out, src in (("replies", "replies_count"), ("retweets", "reblogs_count"),
                     ("likes", "favourites_count")):
        if s.get(src) is not None:
            snap[out] = s[src]
    return {k: v for k, v in snap.items() if v is not None}


def capture_truth(ref, raw=None):
    if raw is None:
        # Truth Social's read API is keyless but Cloudflare-gated on the user-agent.
        st, body = _req("GET", "https://truthsocial.com/api/v1/statuses/" + _status_id(ref, "truth"),
                        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        if st != 200:
            fail(f"truthsocial returned HTTP {st} for {ref!r} (Cloudflare may have escalated). "
                 f"Fall back to the keyless trumpstruth.org feed for the id, or its JSON mirror.")
        raw = _json(body, "the truthsocial response", want=dict)
    if not isinstance(raw, dict):
        fail("the saved Truth Social snapshot must be a JSON object.")
    return map_truth(raw)


def persona(author):
    """One author's backend record (authorJSON, or {} when absent), used to fill the byline
    and section from the author registry when the publish command omits them."""
    return _author(author)


# ---- art-directed hero image (ComfyUI / FLUX.2) -----------------------------

def seed_for(text):
    """A stable seed from the brief, so re-rendering the same prompt reproduces."""
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16) % (2**31)


def build_image_graph(template, prompt, seed, width=1344, height=768, steps=4):
    """The flux2_klein graph with the art director's prompt, seed and dims injected.
    Default 1344x768 is a wide landscape (~16:9) so heroes read wide, not tall, and
    fill the site's hero band without top/bottom cropping. Pure: deep-copies the
    template, never mutates it."""
    g = json.loads(json.dumps(template))
    try:
        g[_N_POS]["inputs"]["text"] = prompt
        g[_N_NOISE]["inputs"]["noise_seed"] = seed
        for n in (_N_LATENT, _N_SCHED):
            g[n]["inputs"]["width"] = width
            g[n]["inputs"]["height"] = height
        g[_N_SCHED]["inputs"]["steps"] = steps
    except (KeyError, TypeError, IndexError):
        fail(f"the ComfyUI template is valid JSON but missing the expected flux2_klein nodes "
             f"({_N_POS}/{_N_NOISE}/{_N_LATENT}/{_N_SCHED}); check --template or {FLUX_TEMPLATE}.")
    return g


def render_image(prompt, seed=None, width=1344, height=768, steps=4, template_path=None):
    """POST the graph to ComfyUI, poll history, fetch the PNG bytes. Returns (png, seed)."""
    tpl = _json(_read_file(template_path or FLUX_TEMPLATE, "the ComfyUI template"),
                "the ComfyUI template", want=dict)
    if seed is None:
        seed = seed_for(prompt)
    graph = build_image_graph(tpl, prompt, seed, width, height, steps)
    cid = hashlib.sha1(prompt.encode()).hexdigest()[:8]
    st, body = _req("POST", COMFY + "/prompt",
                    data=json.dumps({"prompt": graph, "client_id": cid}).encode(),
                    headers={"Content-Type": "application/json"})
    if st != 200:
        sys.exit(f"FATAL: comfy /prompt HTTP {st}: {body.decode('utf-8', 'replace')[:200]}")
    pid = _json(body, "the ComfyUI submit response", want=dict).get("prompt_id")
    if not pid:
        fail("ComfyUI accepted the graph but returned no prompt_id.")
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(2)
        _s, hb = _req("GET", f"{COMFY}/history/{pid}", timeout=30)
        hist = _json(hb, "the ComfyUI history", want=dict)
        if pid in hist and hist[pid].get("outputs"):
            for node in hist[pid]["outputs"].values():
                for img in node.get("images", []):
                    q = urllib.parse.urlencode({"filename": img["filename"],
                                                "subfolder": img.get("subfolder", ""),
                                                "type": img.get("type", "output")})
                    _s, png = _req("GET", f"{COMFY}/view?{q}", timeout=60)
                    return png, seed
            sys.exit("FATAL: comfy returned no image in outputs")
    sys.exit("FATAL: comfy render timed out")


# ---- payload builders -------------------------------------------------------

# A real self-hosted media path is `/media/<sha256-hex>.<ext>` (the hash is 64 hex chars).
# A small model that hand-copies it into a command truncates the opaque hash, e.g.
# `/media/1921e193_..._efae.png`, which then 404s. This matches ONLY a well-formed path, so a
# mistyped/elided one is rejected and the real one (saved by the `image` verb) is used instead.
_MEDIA_RE = re.compile(r"^/media/[0-9a-f]{16,}\.[A-Za-z0-9]+$")


def _hero_from_work():
    """The hero the `image` verb saved to $CENSURADO_WORK/image.json (its full media path + alt),
    or {}. This is how the long opaque filename reaches `preview` WITHOUT the model retyping it."""
    w = _work_dir()
    if not w:
        return {}
    p = w / "image.json"
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _resolve_hero(a):
    """Decide the (image, image_alt) to publish. The media filename is an output the model must
    never transcribe. So: honor a WELL-FORMED explicit --image; otherwise fall back to the hero
    the `image` verb saved to $CENSURADO_WORK/image.json; and NEVER store a truncated/mistyped
    path (drop it and warn instead, so a piece publishes text-only rather than with a broken img)."""
    img = (getattr(a, "image", "") or "").strip()
    alt = (getattr(a, "image_alt", "") or "").strip()
    if img and _MEDIA_RE.match(img):
        return img, alt
    hero = _hero_from_work()
    if hero.get("image"):
        return hero["image"], (alt or hero.get("image_alt", "") or "")
    if img:
        sys.stderr.write(f"note: ignoring a malformed --image {img!r} (looks truncated); publishing "
                         f"without a hero. Re-run `image` and it auto-attaches at preview.\n")
    return "", ""


_CARD_TYPES = ("text", "image", "youtube", "video")


def _yt_id(val):
    """The 11-char YouTube id from a bare id or a youtu.be/watch/shorts/embed URL, or ""."""
    val = (val or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", val):
        return val
    m = re.search(r"(?:youtu\.be/|[?&]v=|/embed/|/shorts/)([A-Za-z0-9_-]{11})", val)
    return m.group(1) if m else ""


def _first_body_video(body):
    """The first `{{video:<val>}}` marker's value in the body, or ""."""
    m = re.search(r"\{\{\s*video\s*:\s*([^}]+?)\s*\}\}", body or "")
    return m.group(1).strip() if m else ""


def _build_card(a, hero_img, hero_alt, body=""):
    """Build the front-page CARD (metadata.card = {type, src, alt}), the LISTING preview,
    DECOUPLED from the body (a piece whose body embeds several videos can still carry a `text`
    card). EVERY published article gets an explicit card in one unified format: `--card-type` is
    honored verbatim when given, OTHERWISE the type is DERIVED from the piece's own media so the
    stored shape is always consistent, an image hero -> image, a --youtube or a body
    {{video:<id>}} -> youtube (src = the id), else text. For a media card with no explicit
    --card-src the piece's own media is borrowed (the resolved hero still, or the youtube id)."""
    ct = (getattr(a, "card_type", "") or "").strip().lower()
    if ct and ct not in _CARD_TYPES:
        fail(f"--card-type must be one of {'|'.join(_CARD_TYPES)} (got {ct!r}).")
    yt = (getattr(a, "youtube", "") or "").strip()
    body_vid = _first_body_video(body)
    if not ct:                                   # derive so the stored card format is uniform
        if hero_img:
            ct = "image"
        elif yt or _yt_id(body_vid):
            ct = "youtube"
        elif body_vid:
            ct = "video"
        else:
            ct = "text"
    card = {"type": ct}
    if ct == "text":
        return card
    src = (getattr(a, "card_src", "") or "").strip()
    if not src:
        if ct == "youtube":
            src = _yt_id(yt) or yt or _yt_id(body_vid) or body_vid
        else:                                    # image, or a self-hosted video poster still
            src = hero_img or (body_vid if ct == "video" else "")
    if src:
        card["src"] = src
    alt = (getattr(a, "card_alt", "") or "").strip() or hero_alt
    if alt:
        card["alt"] = alt
    return card


def _save_snapshot_to_work(snap):
    """Append a captured post snapshot (X or Truth) to $CENSURADO_WORK/tweets.json so `preview`
    attaches its `{{tweet:id}}` card without the model handing the snapshot JSON around. Dedup by
    id (a re-capture replaces the earlier one)."""
    w = _work_dir()
    if not w or not snap.get("id"):
        return
    w.mkdir(parents=True, exist_ok=True)
    p = w / "tweets.json"
    try:
        arr = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
        if not isinstance(arr, list):
            arr = []
    except Exception:
        arr = []
    arr = [t for t in arr if str(t.get("id")) != str(snap["id"])]
    arr.append(snap)
    p.write_text(json.dumps(arr, ensure_ascii=False), encoding="utf-8")


def _tweets_from_work(body):
    """The post snapshots the `tweet`/`truth` verbs saved to $CENSURADO_WORK, kept to exactly the
    ones the body embeds with `{{tweet:<id>}}`. This is how a captured card reaches `preview`
    without the model retyping the snapshot (the same parameter-out trick as the hero image)."""
    w = _work_dir()
    if not w:
        return []
    p = w / "tweets.json"
    if not p.is_file():
        return []
    try:
        arr = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    ids = set(re.findall(r"\{\{tweet:([0-9]+)\}\}", body or ""))
    return [t for t in arr if str(t.get("id")) in ids]


def build_publish_payload(a):
    body = _read_file(a.body_file, "--body-file") if a.body_file else (a.body or "")
    if not body.strip():
        fail('no article body: pass the markdown with --body-file <path> (or --body "...").')
    name, bio, avatar, section = a.author_name, a.author_bio, a.author_avatar, a.section
    if a.author and (not name or not section):  # fill byline/section from the author registry
        p = persona(a.author)
        meta = p.get("metadata") or {}
        name = name or p.get("name", "")
        bio = bio or p.get("bio", "") or p.get("about", "")
        avatar = avatar or p.get("avatar", "")
        section = section or (meta.get("beat", "") if isinstance(meta, dict) else "")
    if not (section or "").strip():
        fail(f"no section for this article: pass --section, or use an --author whose beat sets it "
             f"(author {a.author!r} resolved no beat).")
    meta = {}
    if a.subtitle: meta["subtitle"] = a.subtitle
    if a.description: meta["description"] = a.description
    if name: meta["author_name"] = name
    if bio: meta["author_bio"] = bio
    if avatar: meta["author_avatar"] = avatar
    hero_img, hero_alt = _resolve_hero(a)   # never trust a hand-copied media hash
    if hero_img: meta["image"] = hero_img
    if hero_alt: meta["image_alt"] = hero_alt
    caption = (getattr(a, "image_caption", "") or "").strip()  # site-rendered, not baked in
    credit = (getattr(a, "image_credit", "") or "").strip()
    if caption: meta["image_caption"] = caption
    if credit: meta["image_credit"] = credit
    card = _build_card(a, hero_img, hero_alt, body)  # the front-page card, always explicit (derived if unset)
    if card: meta["card"] = card
    if a.youtube: meta["youtube"] = a.youtube
    if a.keywords: meta["keywords"] = _split(a.keywords)
    tweets = (_json(_read_file(a.tweets_file, "--tweets-file"), "--tweets-file", want=list)
              if a.tweets_file else _tweets_from_work(body))
    if tweets and not all(isinstance(t, dict) for t in tweets):
        fail("--tweets-file must be a JSON array of snapshot OBJECTS (each a captured post), not "
             "ids or strings. Capture with `censurado.py tweet <url>`, or just put {{tweet:<id>}} "
             "in the body and let preview auto-fetch the card.")
    if tweets: meta["tweets"] = tweets
    payload = {"title": a.title, "body": body, "author": a.author, "section": section}
    if _split(a.topics): payload["topics"] = _split(a.topics)
    if a.slug: payload["slug"] = a.slug
    if a.published_at: payload["published_at"] = a.published_at
    if meta: payload["metadata"] = meta
    return payload


# ---- commands ---------------------------------------------------------------

def cmd_get(a):
    st, body = api("GET", "/articles/" + a.slug)
    sys.stdout.write(body.decode("utf-8", "replace"))
    return 0 if st == 200 else 1


def _day_bound(v, end=False):
    """A --since/--until value to RFC3339. A bare YYYY-MM-DD gets the day's start; for
    --until it becomes the NEXT day's start, because the backend `to` filter is exclusive
    (published_at < to), so the whole named day stays included. A full timestamp passes
    through untouched (for --until that is an exclusive bound)."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        if end:
            return (datetime.fromisoformat(v) + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        return v + "T00:00:00Z"
    return v


def cmd_archive(a):
    """List published articles from the backend read API, LIGHT (no bodies):
    slug, published_at, title, subtitle, description, section, topics, card_type. This is
    the repeat-news sweep's first stage: judge candidates by title, description, and DATE
    against the event being covered, and read a candidate's full body with `get <slug>`
    only when real doubt remains, so the sweep stays cheap on context. `card_type` (the
    server-derived label for what the piece's CARD shows: text/image/youtube/video) also
    feeds the portada layout: it says what the card is, without opening each body.

    With `--day YYYY-MM-DD` and no author it lists a whole UTC day across EVERY author: the
    loader the portada arrange (`step --mode portal-review`) reads to lay out that day's
    front page (title, subtitle/dek, description, card_type per piece)."""
    author = getattr(a, "author", "") or ""
    day = getattr(a, "day", "") or ""
    since, until = (day, day) if day else (a.since, a.until)
    params = {}
    if author: params["author"] = author
    if a.q: params["q"] = a.q
    if since: params["from"] = _day_bound(since)
    if until: params["to"] = _day_bound(until, end=True)
    if a.limit: params["limit"] = str(a.limit)
    st, body = api("GET", "/articles?" + urllib.parse.urlencode(params))
    if st != 200:
        sys.exit(f"FATAL: archive HTTP {st}: {body.decode('utf-8', 'replace')[:200]}")
    data = _json(body, "the archive response", want=dict)
    out = {"total": data.get("total", 0), "articles": []}
    for it in data.get("articles", []):
        if not isinstance(it, dict):
            continue
        md = it.get("metadata") or {}
        if not isinstance(md, dict):
            md = {}
        out["articles"].append({
            "slug": it.get("slug", ""),
            "published_at": it.get("published_at", ""),
            "title": it.get("title", ""),
            "subtitle": md.get("subtitle", ""),
            "description": md.get("description", ""),
            "section": it.get("section", ""),
            "topics": it.get("topics", []),
            "card_type": it.get("card_type", ""),
        })
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_sections(a):
    """Consult the LIVE section vocabulary: the distinct `section` values actually present
    on non-deleted articles, each with its article count, most-used first. `section` is a
    FREE STRING with no registry table (unlike authors/topics), so THIS is the only
    authoritative list of what sections exist right now. A renamed or removed section keeps
    whatever old orphan articles still carry it (reachable by author or by scrolling older
    days) and simply shows here until its last piece is retagged or deleted, so a stale value
    in the list is expected, not a bug.

    The same call returns the author and topic distributions (GET /articles:facets), so the
    section picture and who publishes into it come back together. `--authors`/`--topics` narrow
    the print to one axis; the default prints all three. Note the display LABEL (e.g. Spanish
    "Política") is a render-time concern in the generator; the value stored here is the URL
    slug the site files under (e.g. `politics`)."""
    st, body = api("GET", "/articles:facets")
    if st != 200:
        fail(f"cannot read the section facets (GET /articles:facets HTTP {st}). Is the backend "
             f"up at {PUBLISH} and the operator token valid?")
    data = _json(body, "the facets response", want=dict)
    axes = {k: data.get(k, []) for k in ("sections", "authors", "topics")}
    want = {"sections": getattr(a, "sections", False), "authors": getattr(a, "authors", False),
            "topics": getattr(a, "topics", False)}
    if any(want.values()):           # narrow to the explicitly requested axes
        axes = {k: v for k, v in axes.items() if want[k]}
    print(json.dumps(axes, ensure_ascii=False, indent=2))
    return 0


def _await_preview_url(slug, tries=6, delay=1.0):
    """Resolve the LOCAL permalink `/a/<slug>-<hash8>/` for a just-staged article and
    best-effort wait for the static generator to render it. The publish response carries
    only {id, slug}; the permalink's hash suffix is `content_hash[:8]` (generator rule in
    censurado-web/internal/generate/url.go), fetched with one follow-up read. The generator
    rebuilds the site a beat after the write, so we poll the URL briefly. Returns
    (url, live): `url` is the correct permalink ("" if slug/hash cannot be resolved), and
    `live` is True once the page returns 200."""
    if not slug:
        return "", False
    try:
        st, body = api("GET", "/articles/" + slug)
    except (ToolError, urllib.error.URLError, ConnectionError, TimeoutError):
        # A backend hiccup in the beat between the 201 and this read must not turn a
        # SUCCESSFUL publish into a traceback: degrade to "URL not resolved yet" (the caller
        # prints NEWEST and the piece is already staged), same guard as the site poll below.
        return "", False
    if st != 200:
        return "", False
    try:
        ch = json.loads(body).get("content_hash", "")
    except Exception:
        ch = ""
    if not ch:
        return "", False
    url = f"{SITE}/a/{slug}-{ch[:8]}/"
    for _ in range(max(1, tries)):
        try:
            s, _b = _req("GET", url, timeout=10)
        except Exception:
            s = 0
        if s == 200:
            return url, True
        time.sleep(delay)
    return url, False


def cmd_publish(a):
    payload = build_publish_payload(a)
    if a.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    missing = [f for f, v in (("description", a.description),)
               if not (v or "").strip()]
    if missing:
        sys.stderr.write("preview needs a one-line --description (the bajada). "
                         "Add it, then run preview again.\n")
        return 1
    # Any tweet card the body embeds ({{tweet:id}}) whose snapshot is not attached yet: fetch it
    # now from the id, so a correct marker always renders even if the model forgot to run `tweet`.
    meta = payload.setdefault("metadata", {})
    have = {str(t.get("id")) for t in (meta.get("tweets") or [])}
    fetched = []
    for tid in sorted(set(re.findall(r"\{\{tweet:([0-9]+)\}\}", payload.get("body", ""))) - have):
        try:
            snap = capture_tweet(tid)
        except (SystemExit, Exception):
            sys.stderr.write(f"note: could not fetch the card for tweet {tid}; publishing without it.\n")
            continue
        _save_snapshot_to_work(snap)
        fetched.append(snap)
    if fetched:
        meta["tweets"] = (meta.get("tweets") or []) + fetched
    idem = a.idempotency or f"{a.author}-{int(time.time())}-{os.getpid()}"
    st, body = _req("POST", PUBLISH + "/articles", data=json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer " + token(),
                             "Content-Type": "application/json", "Idempotency-Key": idem})
    sys.stdout.write(body.decode("utf-8", "replace") + "\n")
    if st not in (200, 201):
        sys.stderr.write(f"preview FAILED -> HTTP {st} (nothing was staged)\n")
        return 1
    # The hero the `image` verb saved to the work dir is SPENT once the piece is staged: delete
    # it so a later ad-hoc preview of a DIFFERENT piece cannot inherit this one's hero from the
    # shared default work dir. That leak attached one article's image to the next (a video piece
    # got the previous article's still). A walk renders a fresh hero per article, so this is safe;
    # to re-attach on a re-preview, pass --image or re-run `image`.
    try:
        (_work_dir() / "image.json").unlink()
    except (FileNotFoundError, OSError):
        pass
    slug = ""
    try:
        slug = (json.loads(body) or {}).get("slug", "")
    except Exception:
        pass
    url, live = _await_preview_url(slug)
    sys.stderr.write(f"preview OK -> HTTP {st}. Staged to the LOCAL site {SITE} (debug-only, NOT "
                     f"public; going live is a separate `publicar --yes`).\n")
    if url:
        state = "live now" if live else "staged; the site repaints in a few seconds -- just open it"
        print(f"PREVIEW: {url}  [{state}]")
    print(f"NEWEST: {SITE}/latest/")
    return 0


def cmd_edit(a):
    st, body = api("GET", "/articles/" + a.slug)
    if st != 200:
        sys.exit(f"FATAL: cannot read {a.slug}: HTTP {st}")
    art = _json(body, f"the article {a.slug!r}", want=dict)
    if a.body_file:
        art["body"] = _read_file(a.body_file, "--body-file")
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        art[k] = _split(v) if k == "topics" else v
    meta = art.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    for kv in a.meta or []:
        k, _, v = kv.partition("=")
        meta[k] = v
    if getattr(a, "meta_json", ""):
        # --meta merges STRINGS only (k=v), so the structured metadata surfaces (the front-page
        # `card` object, `keywords`, `tweets`) were unreachable from an edit. This merges a JSON
        # object into metadata at the top level: {"card": {...}} replaces the whole card.
        meta.update(_json(a.meta_json, "--meta-json", want=dict))
    if a.tweets_file:
        meta["tweets"] = _json(_read_file(a.tweets_file, "--tweets-file"), "--tweets-file", want=list)
    if a.published_at:
        art["published_at"] = a.published_at
    missing = [k for k in ("title", "body", "author", "section") if not art.get(k)]
    if missing:
        fail(f"the fetched article {a.slug!r} is missing required field(s): {', '.join(missing)}; "
             f"cannot re-publish it as an edit.")
    payload = {"title": art["title"], "body": art["body"],
               "author": art["author"], "section": art["section"]}
    if art.get("topics"): payload["topics"] = art["topics"]
    if art.get("published_at"): payload["published_at"] = art["published_at"]
    if meta: payload["metadata"] = meta
    if a.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    st, body = api("PUT", "/articles/" + a.slug, payload)
    sys.stdout.write(body.decode("utf-8", "replace") + "\n")
    sys.stderr.write(f"edit -> HTTP {st}\n")
    return 0 if st == 200 else 1


def cmd_unpublish(a):
    """Remove an article from the site (DELETE /articles/<slug>). SOFT delete: the article is
    tombstoned, so it drops from every listing (latest, its day bucket, its section, the
    author's profile) and its permalink 404s after the next regenerate, but it stays restorable
    (POST /articles/<slug>/restore). Guarded by --yes. Response: 204 removed; 404 no such article."""
    if not a.yes:
        sys.exit(f"REFUSED: confirm removing an article with --yes:\n"
                 f"  python3 cli/censurado.py unpublish {a.slug} --yes")
    st, body = api("DELETE", "/articles/" + a.slug)
    if st == 204:
        sys.stderr.write(f"unpublish {a.slug} -> tombstoned (204). It leaves the site on the next "
                         f"regenerate. Restore with `POST /articles/{a.slug}/restore` if needed.\n")
        return 0
    if st == 404:
        sys.stderr.write(f"unpublish {a.slug} -> no such article (404)\n")
        return 1
    sys.stdout.write(body.decode("utf-8", "replace") + "\n")
    sys.stderr.write(f"unpublish {a.slug} -> HTTP {st}\n")
    return 1


def cmd_portada(a):
    """Write or read the per-day front-page plan (the portada) in the publish backend
    (admin:write, Bearer-authenticated). With --set-json, POST the
    day's ORDER to /portadas: the JSON object provides `entries` (a list of {"slug","role"})
    and an optional `recomendado` list of slugs; the positional date is merged in and wins
    over any date in the JSON. entries[0] is the day's lead (full width by position, role
    ignored there); role "important" makes a non-lead card span the full row (a double card),
    "" is a half-row single (singles pair up two per row on desktop). Without --set-json, GET
    /portadas and print the list (there is no single-date GET route; listing is fine)."""
    if a.set_json is not None:
        spec = _json(a.set_json, "--set-json", want=dict)
        payload = {"date": a.date,
                   "entries": spec.get("entries", []),
                   "recomendado": spec.get("recomendado", [])}
        st, body = api("POST", "/portadas", payload)
    else:
        st, body = api("GET", "/portadas")
    sys.stdout.write(body.decode("utf-8", "replace") + "\n")
    return 0 if 200 <= st < 300 else 1


def cmd_recomendado(a):
    """Read or set the site's single GLOBAL 'Recomendado' list: the fixed editor's-pick
    rail on the FRONT PAGE. It is ONE ordered list of article slugs (up to 10), NOT tied
    to any day: it persists on the front page every day until you change it, and replaces
    the old auto-computed rail. Any slug may be recommended, including pieces from previous
    days; a slug whose article does not exist is dropped at render, and duplicates/blanks
    are removed on save.

    With `--set "slug-a,slug-b,..."` PUT the list (order preserved, at most 10). With
    `--clear` empty it (the front page then shows the Recomendado widget with no items, so
    the layout holds). With no flag, GET and print the current list. Writes need admin:write."""
    if a.clear or a.set_slugs is not None:
        slugs = [] if a.clear else _split(a.set_slugs)
        st, body = api("PUT", "/recomendado", {"slugs": slugs})
        if st != 200:
            fail(f"cannot set recomendado (PUT /recomendado HTTP {st}): "
                 f"{body.decode('utf-8', 'replace')[:200]}")
    else:
        st, body = api("GET", "/recomendado")
        if st != 200:
            fail(f"cannot read recomendado (GET /recomendado HTTP {st}). Is the backend up "
                 f"at {PUBLISH} and the operator token valid?")
    sys.stdout.write(body.decode("utf-8", "replace") + "\n")
    return 0


def cmd_media(a):
    data = _read_file(a.file, "the media file", binary=True)
    ext = a.file.rsplit(".", 1)[-1].lower()
    ct = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
          "gif": "image/gif", "mp4": "video/mp4"}.get(ext, "application/octet-stream")
    st, body = _req("POST", PUBLISH + "/media", data=data,
                    headers={"Authorization": "Bearer " + token(), "Content-Type": ct})
    sys.stdout.write(body.decode("utf-8", "replace") + "\n")
    return 0 if st in (200, 201) else 1


def cmd_image(a):
    tpl_path = Path(a.template) if a.template else None
    if a.dry_run:
        tpl = _json(_read_file(tpl_path or FLUX_TEMPLATE, "the ComfyUI template"),
                    "the ComfyUI template", want=dict)
        seed = a.seed if a.seed is not None else seed_for(a.prompt)
        print(json.dumps(build_image_graph(tpl, a.prompt, seed, a.width, a.height, a.steps), indent=1))
        return 0
    try:
        png, seed = render_image(a.prompt, a.seed, a.width, a.height, a.steps, tpl_path)
    except (ToolError, urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        # ComfyUI unreachable (the GPU-free lane runs no :8188). The hero step is
        # best-effort by design, so skip cleanly instead of crashing: tell the caller to
        # publish text-only and NOT pass --image. Exit 0 so a batch chain keeps going.
        sys.stderr.write(
            f"IMAGE SKIPPED: ComfyUI not reachable at {COMFY} ({exc}). Hero rendering is off "
            f"in this lane; this step is best-effort. Publish the piece text-only and do NOT "
            f"pass --image on the preview step.\n")
        return 0
    st, body = _req("POST", PUBLISH + "/media", data=png,
                    headers={"Authorization": "Bearer " + token(), "Content-Type": "image/png"})
    if st not in (200, 201):
        sys.exit(f"FATAL: media upload HTTP {st}: {body.decode('utf-8', 'replace')[:200]}")
    url = _json(body, "the media upload response", want=dict).get("url", "")
    if not url:
        fail("the media upload succeeded but the response carried no url.")
    # Record the hero for `preview` to attach itself: the full media hash is opaque and a small
    # model truncates it if asked to retype it, so it becomes a parameter-out, not model text.
    w = _work_dir()
    if w is not None:
        w.mkdir(parents=True, exist_ok=True)
        (w / "image.json").write_text(
            json.dumps({"image": url, "image_alt": a.alt, "seed": seed}), encoding="utf-8")
        sys.stderr.write("Hero saved. The next preview attaches it for you.\n")
    print(json.dumps({"image": url, "image_alt": a.alt, "seed": seed, "bytes": len(png)},
                     ensure_ascii=False))
    return 0


def cmd_tweet(a):
    raw = _json(_read_file(a.from_json, "--from-json"), "--from-json", want=dict) if a.from_json else None
    snap = capture_tweet(a.ref, raw)
    _save_snapshot_to_work(snap)
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    if _work_dir() and snap.get("id"):
        sys.stderr.write("Saved. Put {{tweet:%s}} in the body where you discuss this post; "
                         "preview attaches the card.\n" % snap["id"])
    return 0


def cmd_truth(a):
    raw = _json(_read_file(a.from_json, "--from-json"), "--from-json", want=dict) if a.from_json else None
    snap = capture_truth(a.ref, raw)
    _save_snapshot_to_work(snap)
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    if _work_dir() and snap.get("id"):
        sys.stderr.write("Saved. Put {{tweet:%s}} in the body where you discuss this post; "
                         "preview attaches the card.\n" % snap["id"])
    return 0


def cmd_personas(a):
    """List the author ids (handles) that exist in the backend registry."""
    for p in _authors():
        print(p.get("handle") if isinstance(p, dict) else p)
    return 0


def cmd_persona(a):
    """Fetch ONE author's full record (name/bio/avatar/gender/about/style/topics/sources +
    the metadata tail beat/who_i_am/language/few_shots) so the agent writes in that voice.
    All authors live in the backend; this reads the registry and prints the matching one
    (the backend has no single-author route, so it filters the list by handle)."""
    rec = _author(a.id)
    if not rec:
        sys.stderr.write(f"persona {a.id!r} -> not found in the author registry\n")
        return 1
    sys.stdout.write(json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
    return 0


def _persona_json_to_author_input(payload):
    """Transform an agent-written persona JSON (the synthesize-prompt shape: display_name/
    beat/who_i_am/style/about/gender/few_shots_*/...) into the backend's authorInput. The
    public scalars go first-class (about fills BOTH bio and about, the way the platform
    renders bio publicly); the private tail (beat/who_i_am/language/few_shots) rides in the
    metadata blob; the handle comes from an explicit id/handle, else it is slugified from the
    display name. Returns (handle, body). Source outlets are NOT wired here: the persona
    JSON's `sources` are free-text suggestions, while the backend join needs real source
    slugs, so an author's outlets are attached separately with `sources <id> --set ...`."""
    handle = (payload.get("handle") or payload.get("id") or _slugify(payload.get("display_name", ""))).strip()
    about = payload.get("about", "")
    raw_topics = payload.get("profile_topics")
    topics = raw_topics if isinstance(raw_topics, list) else _split(raw_topics)
    metadata = {}
    for key in ("beat", "who_i_am", "language", "few_shots_pos", "few_shots_neg"):
        val = payload.get(key)
        if val not in (None, "", [], {}):
            metadata[key] = val
    if topics:
        metadata["profile_topics"] = topics
    body = {
        "handle": handle,
        "name": payload.get("display_name", ""),
        "bio": about,
        "about": about,
        "avatar": payload.get("avatar_path", "") or payload.get("avatar", ""),
        "gender": payload.get("gender", ""),
        "style": payload.get("style", ""),
    }
    if topics:
        body["topics"] = topics
    if metadata:
        body["metadata"] = metadata
    return handle, body


def cmd_create_author(a):
    """Persist an agent-authored persona into the backend author registry (POST /authors),
    the home for all authors. The agent writes the JSON itself following the synthesize
    prompt (`prompt persona/synthesize.md`); no model runs server-side. Required fields:
    display_name, beat, who_i_am, style; about/gender/avatar_path/few_shots are optional. The
    handle is derived from the display name (or an explicit `id`). Wire the author's outlets
    afterwards with `sources <handle> --set <slug,slug>` (real backend source slugs)."""
    if a.file == "-":                       # explicit stdin only; a bare "" must never block on an
        raw = sys.stdin.read()              # open pipe with no EOF (that would hang the agent)
    elif a.file:
        raw = _read_file(a.file, "the persona JSON file")
    else:
        fail("no persona JSON given: pass --file <path>, or --file - to read it from stdin.")
    payload = _json(raw, "the persona JSON", want=dict)
    missing = [k for k in ("display_name", "beat", "who_i_am", "style") if not payload.get(k)]
    if missing:
        sys.exit(f"FATAL: persona JSON missing required field(s): {', '.join(missing)}")
    handle, body = _persona_json_to_author_input(payload)
    if not handle:
        fail('could not derive a handle: give an explicit "id", or a non-empty "display_name".')
    # The walk needs the author's language to load the right per-language editorial anchors
    # (`editorial-rules <language>`). synthesize.md solicits it as required; if a JSON omits it,
    # default to Spanish (the live language) and say so, so an author is never languageless.
    meta = body.setdefault("metadata", {})
    if not meta.get("language"):
        meta["language"] = "es"
        sys.stderr.write("note: the persona JSON had no `language`; defaulting to \"es\". Set it "
                         "explicitly (synthesize solicits it) so the walk loads the right "
                         "per-language editorial rules.\n")
    st, rb = api("POST", "/authors", body)
    sys.stdout.write(rb.decode("utf-8", "replace") + "\n")
    sys.stderr.write(f"create-author {handle} -> HTTP {st}\n")
    return 0 if st in (200, 201) else 1


_AUTHOR_PUBLIC = ("name", "bio", "about", "avatar", "gender", "style")
_AUTHOR_META = ("beat", "who_i_am", "language", "few_shots_pos", "few_shots_neg")


def _byline_of(rec):
    """The three public byline fields an article stores its own copy of."""
    return {"author_name": rec.get("name", ""),
            "author_bio": rec.get("bio", "") or rec.get("about", ""),
            "author_avatar": rec.get("avatar", "")}


def cmd_sync_byline(a):
    """Push an author's CURRENT byline (display name, public bio, picture) onto the articles
    they already published. Every article stores its OWN copy of those three fields, taken at
    publish time, so the piece keeps its byline even if the author is later retired. The cost of
    that copy is drift: change the picture or rewrite the bio and every earlier article still
    shows the old one, which is why an author's small byline photo can differ from the one on
    their profile page. This re-syncs them.

    It reads the author record, lists their articles, and re-publishes in place only the ones
    whose copy differs (each is a GET -> modify -> PUT, body and everything else untouched).
    `--dry-run` reports what would change without writing. Prints a JSON summary."""
    rec = _author(a.id)
    if not rec:
        fail(f"author {a.id!r} is not in the registry (list them with `personas`).")
    want = _byline_of(rec)
    limit = a.limit or 500
    st, body = api("GET", "/articles?" + urllib.parse.urlencode({"author": a.id, "limit": str(limit)}))
    if st != 200:
        fail(f"cannot list the author's articles (GET /articles HTTP {st}). Is the backend up "
             f"at {PUBLISH} and the operator token valid?")
    listing = _json(body, "the archive response", want=dict)
    out = {"author": a.id, "byline": want, "scanned": 0, "changed": [], "failed": [],
           "dry_run": bool(a.dry_run)}
    for item in listing.get("articles", []):
        slug = item.get("slug", "") if isinstance(item, dict) else ""
        if not slug:
            continue
        out["scanned"] += 1
        st, body = api("GET", "/articles/" + slug)
        if st != 200:
            out["failed"].append(slug)
            continue
        art = _json(body, f"the article {slug!r}", want=dict)
        meta = art.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        stale = {k: v for k, v in want.items() if (meta.get(k, "") or "") != (v or "")}
        if not stale:
            continue
        out["changed"].append({"slug": slug, "fields": sorted(stale)})
        if a.dry_run:
            continue
        for key, value in want.items():
            if value:
                meta[key] = value
            else:
                meta.pop(key, None)
        payload = {"title": art.get("title", ""), "body": art.get("body", ""),
                   "author": art.get("author", ""), "section": art.get("section", "")}
        if art.get("topics"): payload["topics"] = art["topics"]
        if art.get("published_at"): payload["published_at"] = art["published_at"]
        payload["metadata"] = meta
        st, _b = api("PUT", "/articles/" + slug, payload)
        if st != 200:
            out["failed"].append(slug)
            out["changed"].pop()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if out["failed"]:
        sys.stderr.write(f"sync-byline {a.id}: {len(out['failed'])} article(s) could not be "
                         f"updated: {', '.join(out['failed'])}\n")
        return 1
    verb = "would update" if a.dry_run else "updated"
    sys.stderr.write(f"sync-byline {a.id}: {verb} {len(out['changed'])} of {out['scanned']} "
                     f"article(s).\n")
    return 0


def cmd_edit_author(a):
    """Modify an EXISTING author in place: the public byline fields (display name, bio/about,
    avatar picture, gender, style), the private tail the walk reads (beat, who_i_am, language,
    few_shots), and the curated profile topics. The backend has no partial update (POST /authors
    replaces every column), so this is a READ-MODIFY-WRITE: it reads the author's full record,
    applies only the fields you name, and re-sends the complete row, so nothing you leave out is
    blanked. `--set about=...` fills BOTH bio and about, the way the platform renders the public
    bio. Source outlets are a separate join: change them with `sources <id> --set`."""
    rec = _author(a.id)
    if not rec:
        fail(f"author {a.id!r} is not in the registry (list them with `personas`). To add a new "
             f"one use `create-author --file <persona.json>`.")
    body = _author_json_to_input(rec)
    meta = dict(body.get("metadata") or {})
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        if k not in _AUTHOR_PUBLIC:
            fail(f"--set {k!r} is not an author field. Public fields: {', '.join(_AUTHOR_PUBLIC)}. "
                 f"The private tail ({', '.join(_AUTHOR_META)}) goes through --meta/--meta-json, "
                 f"topics through --profile-topics, outlets through `sources --set`.")
        body[k] = v
        if k == "about":                 # about IS the public bio on the site; keep them in step
            body["bio"] = v
    for kv in a.meta or []:
        k, _, v = kv.partition("=")
        if k not in _AUTHOR_META:
            fail(f"--meta {k!r} is not part of the author's private tail "
                 f"({', '.join(_AUTHOR_META)}). Public fields go through --set.")
        meta[k] = v
    if getattr(a, "meta_json", ""):      # few_shots_* are arrays of objects, unreachable via k=v
        patch = _json(a.meta_json, "--meta-json", want=dict)
        bad = [k for k in patch if k not in _AUTHOR_META]
        if bad:
            fail(f"--meta-json carries non-author key(s): {', '.join(sorted(bad))}. Allowed: "
                 f"{', '.join(_AUTHOR_META)}.")
        meta.update(patch)
    if a.profile_topics is not None:
        topics = _split(a.profile_topics)
        if topics:
            body["topics"] = topics
            meta["profile_topics"] = topics
        else:
            body.pop("topics", None)
            meta.pop("profile_topics", None)
    if meta:
        body["metadata"] = meta
    else:
        body.pop("metadata", None)
    if a.dry_run:
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 0
    st, rb = api("POST", "/authors", body)
    sys.stdout.write(rb.decode("utf-8", "replace") + "\n")
    sys.stderr.write(f"edit-author {a.id} -> HTTP {st}\n")
    return 0 if st in (200, 201) else 1


def cmd_sources(a):
    """Show an author's attached source slugs, or with --set REPLACE them (a full set, not a
    merge). The outlets an author reads live in the backend author_sources join, per author;
    the slugs must be real backend source ids (list them with `portals`)."""
    if a.set is not None:
        st, body = api("PUT", "/authors/" + a.id + "/sources", {"sources": _split(a.set)})
    else:
        st, body = api("GET", "/authors/" + a.id + "/sources")
    sys.stdout.write(body.decode("utf-8", "replace") + "\n")
    return 0 if st == 200 else 1


def cmd_profile_topics(a):
    """Show an author's curated public-profile topics, or with --set REPLACE them (a full
    set, not a merge; --set "" clears them). When present they override the generator's
    auto-computed topic union on the author's profile page. The backend has no partial
    update, so a set is a READ-MODIFY-WRITE: it reads the author's full record and re-sends
    every field with the new topics, so the rest of the row (voice, bio, metadata tail) is
    preserved, never blanked. Topics land first-class AND in metadata.profile_topics (the
    public site still overlays from there). They reach the live site after it regenerates."""
    rec = _author(a.id)
    if not rec:
        sys.stderr.write(f"profile-topics {a.id!r} -> not found in the author registry\n")
        return 1
    if a.set is None:
        print(json.dumps(rec.get("topics", []), ensure_ascii=False))
        return 0
    topics = _split(a.set)
    body = _author_json_to_input(rec)
    meta = body.get("metadata") or {}
    if topics:
        body["topics"] = topics
        meta["profile_topics"] = topics
    else:
        body.pop("topics", None)
        meta.pop("profile_topics", None)
    if meta:
        body["metadata"] = meta
    else:
        body.pop("metadata", None)
    st, rb = api("POST", "/authors", body)
    sys.stdout.write(rb.decode("utf-8", "replace") + "\n")
    sys.stderr.write(f"profile-topics {a.id} -> HTTP {st}\n")
    return 0 if st in (200, 201) else 1


def cmd_topics(a):
    """Inventory the distinct free-text article tags across the published corpus: each tag with
    how many articles carry it and the slugs that do, sorted by count then name. This is the
    READ side of topic normalization: run it to SEE the tag surface and spot naming variants
    (`Javier-Milei` vs `milei`, singular/plural, alternate spellings) before merging them. The
    MERGE side is two writers, `censurado-brain topics cleanse` for article tags and
    `profile-topics --set` for an author's profile chips. LIGHT: reads the listing, no bodies."""
    limit = getattr(a, "limit", 0) or 1000
    st, body = api("GET", "/articles?" + urllib.parse.urlencode({"limit": str(limit)}))
    if st != 200:
        sys.exit(f"FATAL: topics HTTP {st}: {body.decode('utf-8', 'replace')[:200]}")
    data = _json(body, "the topics response", want=dict)
    fetched = [it for it in data.get("articles", []) if isinstance(it, dict)]
    total = data.get("total", len(fetched))
    tags = {}
    for it in fetched:
        slug = it.get("slug", "")
        for tag in it.get("topics", []) or []:
            if not isinstance(tag, str) or not tag.strip():
                continue
            rec = tags.setdefault(tag, {"tag": tag, "count": 0, "slugs": []})
            rec["count"] += 1
            rec["slugs"].append(slug)
    ordered = sorted(tags.values(), key=lambda r: (-r["count"], r["tag"].lower()))
    out = {"total_tags": len(ordered), "articles_scanned": len(fetched),
           "articles_total": total, "topics": ordered}
    if total > len(fetched):
        sys.stderr.write(
            f"note: scanned {len(fetched)} of {total} articles; raise --limit for the full inventory\n")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_remove_topic(a):
    """Tombstone a topic in the operator registry (DELETE /topics/<slug>). SOFT delete: it
    drops the stale row off the `/topics` facet index, restorable via the panel. Use it to
    reconcile the registry after a `topic-cleanse` merge, when a variant slug was folded into
    a canonical one and its registry row lingers. It does NOT touch article tags (those move
    via the cleanse) or author chips (those move via profile-topics). Guarded by --yes.
    Response: 204 removed; 404 no such topic. The slug is the one the Temas tab / GET /topics
    shows (already slugified)."""
    if not a.yes:
        sys.exit(f"REFUSED: confirm removing a topic with --yes:\n"
                 f"  python3 cli/censurado.py remove-topic {a.slug} --yes")
    st, body = api("DELETE", "/topics/" + urllib.parse.quote(a.slug, safe=""))
    if st in (200, 204):
        sys.stderr.write(f"remove-topic {a.slug} -> tombstoned ({st})\n")
        return 0
    if st == 404:
        sys.stderr.write(f"remove-topic {a.slug} -> no such topic (404)\n")
        return 1
    sys.stdout.write(body.decode("utf-8", "replace") + "\n")
    sys.stderr.write(f"remove-topic {a.slug} -> HTTP {st}\n")
    return 1


def cmd_portals(a):
    """List the source slugs available to attach to an author (GET /sources). These are the
    ids `sources <handle> --set` links against."""
    st, body = api("GET", "/sources")
    if st != 200:
        fail(f"cannot read the source registry (GET /sources HTTP {st}).")
    for s in _json(body, "the sources response", want=dict).get("sources", []):
        print(s.get("slug") if isinstance(s, dict) else s)
    return 0


def cmd_prompt(a):
    """Read a prompt's body by key (e.g. workflow/50-draft.md or persona/synthesize.md). Every
    prompt is a file in the recipe dir (PROMPTS_DIR; git is the version history), so a read
    returns the current file. `step` walks the workflow/* nodes; `set-prompt` edits any prompt."""
    sys.stdout.write(_fetch_prompt_body(a.key) + "\n")
    return 0


def cmd_style(a):
    """Read the newsroom's editorial style guide (voice, lexicon, house rules): the prose
    recipe at `editorial/style.md` under the prompts dir. NOTE: this is the QUALITATIVE guide,
    NOT the numeric floor the WALK enforces. The enforced parameters (MIN_SOURCES/
    MIN_PER_TYPE/RESPIN_PASSES/TOPIC_CAP) live in cli/workflow/parameters.json, which `step`
    fills into the node placeholders; `set-floor` sets MIN_SOURCES/MIN_PER_TYPE, and the tag
    cap and respin budget are edited in parameters.json directly. Use `style` for the
    voice/rules; use parameters.json for the enforced numeric bar."""
    path = _prompt_path("editorial/style.md")
    if path is None or not path.is_file():
        fail(f"no editorial style guide at {PROMPTS_DIR / 'editorial' / 'style.md'}. It is a "
             f"prompt file in the recipe; check this repo's prompts/ dir (or set "
             f"CENSURADO_PROMPTS_DIR).")
    sys.stdout.write(_read_file(path, "the editorial style guide").rstrip() + "\n")
    return 0


def _editorial_rows(lang):
    """The editorial-config catalog for a language as a {key: value} map, read from the
    backend editorial_text scope (GET /editorial-text?lang=<lang>). The values are plain
    text or JSON-encoded (a list or an object); the caller decodes per key. An empty map
    means no anchors are seeded for that language yet."""
    st, body = api("GET", "/editorial-text?lang=" + urllib.parse.quote(lang))
    if st != 200:
        fail(f"cannot read the editorial catalog (GET /editorial-text HTTP {st}). Is the backend "
             f"up at {PUBLISH} and the operator token valid?")
    entries = _json(body, "the editorial-text response", want=dict).get("entries", [])
    return {e.get("key"): e.get("value", "") for e in entries
            if isinstance(e, dict) and e.get("key")}


def _decode_json(v, want):
    """Decode a JSON-encoded editorial value to `want` (list or dict), or an empty one on
    any parse/type mismatch, so a hand-edited row never crashes the render."""
    try:
        val = json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return want()
    return val if isinstance(val, want) else want()


def render_editorial_rules(lang, rows):
    """Render the per-language editorial anchors into a readable block the walk applies:
    the banned lexicon and swaps, the orthography charset + examples, the slop phrases and
    candor tics to avoid, and the plain-text attribution and satire-disclaimer exemplars.
    The `bot.directive` row (the Telegram bridge's directive) is intentionally not shown:
    it is not an article-writing anchor. Only the keys present are printed, so a partial
    catalog renders cleanly."""
    lines = [f"EDITORIAL ANCHORS for language: {lang}",
             "(the language-specific list the workflow applies; the rules themselves stay in "
             "the prompt nodes. Re-run this verb whenever you need the exact list.)", ""]
    bans = _decode_json(rows.get("lexicon.bans", ""), list)
    if bans:
        lines.append("Banned words (never use): " + ", ".join(str(b) for b in bans))
    swaps = _decode_json(rows.get("lexicon.swaps", ""), dict)
    if swaps:
        lines.append("Preferred swaps: " + ", ".join(f"{k} -> {v}" for k, v in swaps.items()))
    slop = _decode_json(rows.get("slop.phrases", ""), list)
    if slop:
        lines.append("Slop phrases to avoid: " + ", ".join(f'"{p}"' for p in slop))
    tics = _decode_json(rows.get("slop.candor_tics", ""), list)
    if tics:
        lines.append("Candor tics to avoid: " + ", ".join(f'"{p}"' for p in tics))
    charset = rows.get("orthography.charset", "")
    ex = _decode_json(rows.get("orthography.examples", ""), dict)
    if charset or ex:
        ortho = "Orthography: " + charset
        if ex:
            ortho += " (e.g. " + ", ".join(f"{k} -> {v}" for k, v in ex.items()) + ")"
        lines.append(ortho)
    attr = rows.get("attribution.example", "")
    if attr:
        lines.append(f'Attribution: name every source as plain text, e.g. "{attr}", never as a link.')
    disc = rows.get("disclaimer.satire", "")
    if disc:
        lines.append(f"Satire/opinion/fiction disclaimer: ONE italic line, e.g. {disc}")
    return "\n".join(lines) + "\n"


def cmd_editorial_rules(a):
    """Read the newsroom's per-language editorial anchors from the backend (the operator-
    editable editorial catalog): the banned lexicon and preferred swaps, the orthography
    charset + worked examples, the slop phrases and candor tics to avoid, and the plain-text
    attribution and satire-disclaimer exemplars for a language. The workflow prompts state
    the language-agnostic RULES; this verb supplies the language-SPECIFIC list they apply.
    Run it once you know your author's `language` (from `persona <id>`), and re-run it any
    time you need the exact list. The anchors are DB rows, not hardcoded, so a new language
    is added by seeding/translating its rows, never by editing the prompts.

    With --bot-directive it prints ONLY the Telegram bot's response directive (the
    `bot.directive` row): the register the bridge prepends so the agent answers in the portal's
    voice. That prints nothing (exit 0) when the row is absent, so the bridge falls back to its
    built-in default. The bridge calls it with the flag at startup and caches the result."""
    rows = _editorial_rows(a.lang)
    if getattr(a, "bot_directive", False):
        directive = rows.get("bot.directive", "").strip()
        if directive:
            sys.stdout.write(directive + "\n")
        return 0
    # bot.directive is a bridge concern, not a writing anchor (render_editorial_rules skips it), so
    # it does not count toward "this language has anchors" (a language may be seeded for the bot
    # before its writing anchors exist).
    if not any(k != "bot.directive" for k in rows):
        sys.stderr.write(
            f"no editorial anchors are seeded for language {a.lang!r}. The prompt nodes still carry "
            f"the language-agnostic rules, so proceed; ask the operator to seed or translate this "
            f"language's editorial rows in the backend so its specific lexicon and orthography apply.\n")
        return 0
    sys.stdout.write(render_editorial_rules(a.lang, rows))
    return 0


# The text catalogs a one-time language localization walks, and the base language each is
# authored in. frontend_text / panel_text are authored in English and TRANSLATED faithfully.
# The editorial anchors have NO English base (a language's slop/ban lexicon is language-native,
# not a translation of English words), so they are GENERATED from the seeded Spanish rows.
TRANSLATE_SCOPES = (
    ("frontend", "en", "translate"),
    ("panel", "en", "translate"),
    ("editorial", "es", "generate"),
)


def _text_rows(scope, lang):
    """The {key: value} map for one text scope and language, read over the HTTP API
    (GET /<scope>-text?lang=<lang>). An empty map means that language is unseeded for the
    scope. `translate` uses it to read a base language and to diff it against a target."""
    st, body = api("GET", f"/{scope}-text?lang=" + urllib.parse.quote(lang))
    if st != 200:
        fail(f"cannot read the {scope} text catalog (GET /{scope}-text HTTP {st}). Is the "
             f"backend up at {PUBLISH} and the operator token valid?")
    entries = _json(body, f"the {scope}-text response", want=dict).get("entries", [])
    return {e.get("key"): e.get("value", "") for e in entries
            if isinstance(e, dict) and e.get("key")}


def _translate_job(target):
    """Build the localization JOB for a target language: for each text scope, the base rows
    that have NO row yet in `target` (idempotent, an already-translated key is never re-listed).
    Each scope carries its base language and its `op` (`translate` = faithful UI string;
    `generate` = author the language's OWN editorial anchors, preserving JSON structure but
    never a literal word-for-word translation). A scope whose base language IS the target is
    skipped (there is nothing to localize a base language into itself)."""
    scopes = []
    for scope, base, op in TRANSLATE_SCOPES:
        if base == target:
            continue
        base_rows = _text_rows(scope, base)
        have = _text_rows(scope, target)
        missing = [{"key": k, "source": base_rows[k], "value": ""}
                   for k in base_rows if k not in have]
        if missing:
            scopes.append({"scope": scope, "op": op, "source_lang": base,
                           "target_lang": target, "entries": missing})
    return {"target_lang": target, "scopes": scopes}


def _translate_apply(a, target):
    """APPLY mode: read the completed job JSON (each entry's `value` filled in) and upsert only
    the rows MISSING in the target. It re-reads the target catalog per scope, so an existing
    translation (even one a human hand-edited) is NEVER overwritten: that is the idempotent,
    never-clobber guarantee. Blank values and already-present keys are skipped and counted."""
    raw = sys.stdin.read() if a.file == "-" else _read_file(a.file, "the completed translation JSON")
    job = _json(raw, "the completed translation JSON", want=dict)
    # The job carries the language it was pulled for; apply writes rows for the command's <lang>
    # and ignores the file's own target_lang, so a mismatched file would silently land one
    # language's translations under another. Refuse it loudly instead (guard only when the job
    # names a target_lang, so a minimal hand-built job still applies).
    job_lang = job.get("target_lang")
    if isinstance(job_lang, str) and job_lang.strip() and job_lang.strip() != target:
        fail(f"this job was pulled for language {job_lang.strip()!r} but you passed {target!r} to "
             f"--apply. Apply the job with its own language (`translate {job_lang.strip()} --apply "
             f"--file ...`), or pull a fresh job for {target!r}.")
    scopes = job.get("scopes")
    if not isinstance(scopes, list):
        fail('the completed translation JSON needs a "scopes" list (the exact shape '
             '`translate <lang>` printed, with each entry\'s "value" filled in).')
    valid = {s for s, _, _ in TRANSLATE_SCOPES}
    wrote = skipped = blank = 0
    for sc in scopes:
        if not isinstance(sc, dict):
            continue
        scope = sc.get("scope")
        if scope not in valid:
            fail(f"unknown scope {scope!r} in the job; expected one of {sorted(valid)}.")
        # Re-read the target rows so apply never overwrites an existing translation, even for a
        # hand-built or re-submitted job. `have` also grows as we write, so a key repeated later
        # in the same job is skipped rather than posted twice.
        have = _text_rows(scope, target)
        for e in sc.get("entries", []) or []:
            if not isinstance(e, dict):
                continue
            key = (e.get("key") or "").strip()
            value = e.get("value", "")
            if not key:
                continue
            if not isinstance(value, str) or not value.strip():
                blank += 1
                continue
            if key in have:
                skipped += 1
                continue
            st, body = api("POST", f"/{scope}-text",
                           {"key": key, "lang": target, "value": value})
            if st != 200:
                fail(f"upsert failed for {scope}/{key} (POST /{scope}-text HTTP {st}): "
                     f"{body.decode('utf-8', 'replace')[:200]}")
            have[key] = value
            wrote += 1
    sys.stderr.write(
        f"translate {target}: wrote {wrote} new row(s), skipped {skipped} already-present, "
        f"{blank} left blank. Re-run `translate {target}` to see what remains.\n")
    return 0


def cmd_translate(a):
    """Localize the whole site into a new language, once per language. WITHOUT --apply it prints
    a JSON JOB: for every UI/editorial text scope, the base rows that have no `<lang>` row yet,
    each with its `source` value and an empty `value` for you to fill. The `frontend`/`panel`
    scopes are `translate` (render the English UI string faithfully in the target language); the
    `editorial` scope is `generate` (author that language's OWN banned lexicon, slop phrases and
    orthography, preserving each value's JSON structure, since a language's slop is not a
    translation of Spanish slop). Fill every `value`, then pipe the completed JSON back with
    `translate <lang> --apply --file -` (or a scratch file path). Apply upserts ONLY the missing
    rows and never overwrites an existing translation, so it is safe to re-run: a second pass
    just fills whatever is still blank. Needs the operator token (automatic)."""
    target = (a.lang or "").strip()
    if not target:
        fail("give a target language code, e.g. `translate pt` (or `de`, `zh`).")
    if getattr(a, "apply", False):
        if not a.file:
            fail("--apply needs the completed job JSON: pass --file <path> (or --file - for stdin).")
        return _translate_apply(a, target)
    job = _translate_job(target)
    if not job["scopes"]:
        sys.stderr.write(
            f"nothing to localize into {target!r}: every catalog already has a {target} row for "
            f"every key. Nothing to do.\n")
    sys.stdout.write(json.dumps(job, ensure_ascii=False, indent=2) + "\n")
    return 0


def cmd_remove_author(a):
    """Tombstone an author in the backend (DELETE /authors/<handle>). It is a SOFT delete:
    the author drops off the public author list, but its record and article bylines are kept, and
    it is restorable (POST /authors/<handle>/restore, or by re-creating it). Guarded by
    --yes. Response: 204 removed; 404 no such author."""
    if not a.yes:
        sys.exit(f"REFUSED: confirm removing an author with --yes:\n"
                 f"  python3 cli/censurado.py remove-author {a.id} --yes")
    st, body = api("DELETE", "/authors/" + a.id)
    if st == 204:
        sys.stderr.write(f"remove-author {a.id} -> tombstoned (204). Restore with "
                         f"`POST /authors/{a.id}/restore` if needed.\n")
        return 0
    if st == 404:
        sys.stderr.write(f"remove-author {a.id} -> no such author (404)\n")
        return 1
    sys.stdout.write(body.decode("utf-8", "replace") + "\n")
    sys.stderr.write(f"remove-author {a.id} -> HTTP {st}\n")
    return 1


def cmd_set_floor(a):
    """Set the sourcing floor the editorial WALK enforces: MIN_SOURCES (the cross-source
    corroboration floor) and/or MIN_PER_TYPE (the per-lean minimum, left/neutral/right).
    These are the numeric parameters bundled with this package (cli/workflow/parameters.json),
    the SAME file `step` fills the {{MIN_SOURCES}}/{{MIN_PER_TYPE}} placeholders from, so the
    change takes effect on the next walk. It rewrites a package file, so commit it to version the
    parameters (they travel with the package, not as live server config)."""
    if a.min_sources is None and a.min_per_type is None:
        sys.exit("FATAL: nothing to set (pass --min-sources and/or --min-per-type)")
    if a.min_sources is not None and a.min_sources < 1:
        sys.exit("FATAL: --min-sources must be >= 1")
    if a.min_per_type is not None and a.min_per_type < 0:
        sys.exit("FATAL: --min-per-type must be >= 0")
    path = _WORKFLOW_DIR / "parameters.json"
    data = (_json(_read_file(path, "parameters.json"), "parameters.json", want=dict)
            if path.is_file() else {})
    changed = []
    if a.min_sources is not None:
        data["MIN_SOURCES"] = a.min_sources
        changed.append(f"MIN_SOURCES={a.min_sources}")
    if a.min_per_type is not None:
        data["MIN_PER_TYPE"] = a.min_per_type
        changed.append(f"MIN_PER_TYPE={a.min_per_type}")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    sys.stderr.write(f"set-floor -> {', '.join(changed)} written to {path} "
                     f"(commit it to version the parameters)\n")
    return 0


def cmd_set_prompt(a):
    """Edit a prompt in place: WRITE the .md/.json under the recipe dir (PROMPTS_DIR; git is
    its version history). The next read or `step` walk serves the edit. Editing a
    `workflow/<node>` changes the walk; editing e.g. `persona/synthesize.md` changes the
    author-synthesis guide. Commit the change in this repo. Adding a brand-new prompt is a
    code change (create the file, and a manifest entry for a workflow node), not an edit."""
    body = _read_file(a.body_file, "--body-file") if a.body_file else a.body
    if not body:
        sys.exit("FATAL: empty body (pass --body-file or --body)")
    path = _prompt_path(a.key)
    if path is None:
        fail(f"invalid prompt key {a.key!r}: must be a .md or .json path under the recipe dir.")
    if not path.is_file():
        fail(f"no prompt {a.key!r} to edit under {PROMPTS_DIR}; adding a new one is a code "
             f"change (create the file, plus a manifest entry for a workflow node).")
    path.write_text(body, encoding="utf-8")
    sys.stdout.write(json.dumps({"key": a.key, "bytes": len(body.encode("utf-8"))}, ensure_ascii=False) + "\n")
    sys.stderr.write(f"set-prompt {a.key} -> wrote {path}\n")
    return 0


# A mainstream browser UA for liveness probes: the public origin is behind Cloudflare, which
# blocks the default urllib agent with a 403 bot challenge. Local services ignore it.
_PROBE_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/125.0 Safari/537.36")


def _probe(url, ok_set, timeout=5):
    """One NON-raising liveness GET. Returns (up, code, detail): `up` is True when the HTTP
    status is in `ok_set`, `code` is the status (0 if the service could not be reached at all),
    `detail` is a short human note. A transport failure (connection refused, DNS, timeout) is a
    down verdict, NEVER a raise: `status` must report on every service, not abort on the first
    one that happens to be down. Any HTTP response (even 404/500) proves the service is at least
    reachable, so a 404 is `reachable, HTTP 404` not `unreachable`."""
    # A browser-like UA: the public origin sits behind Cloudflare, which 403s the default
    # `Python-urllib` agent as a bot -- that would misreport a healthy live site as "down".
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": _PROBE_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, 0, f"unreachable ({getattr(e, 'reason', None) or e})"
    return code in ok_set, code, f"HTTP {code}"


def cmd_up(a):
    """Bring the LOCAL stack up so the portal serves, by running the repo's own runner
    (run.sh) FOR you. Two lanes:
      - `up`     the fast lane every task needs: backend + generator + local site, no GPU.
      - `up-gpu` the SAME plus ComfyUI (the image lane). Heavy; only when the task renders
                 NEW hero images, and only on the GPU box.
    Both are idempotent (an already-up stack is fine), create .env + secrets on first run,
    and WAIT until the site actually serves, so a 0 exit means the stack is usable
    (re-check anytime with `status`). Everything this starts is LOCAL (localhost): nothing
    goes public until the separate `publicar --yes`.

    This verb is the ONE sanctioned way for an agent to start the stack: you still never
    run `./run.sh`, `docker`, or `make` yourself. If it fails, relay the exact last error
    line to the human and STOP -- no retry loops, no chmod, no reading the scripts."""
    script = _REPO / "run.sh"
    if not script.is_file():
        fail(f"stack runner not found at {script} (expected run.sh in the repo root).")
    lane = "up-gpu" if a.gpu else "up"
    sys.stdout.write(f"Bringing the local stack up (lane: {lane}); a first run can take a "
                     "few minutes while it builds...\n")
    sys.stdout.flush()
    try:
        proc = subprocess.run(["bash", str(script), lane], cwd=str(_REPO), check=False)
    except Exception as exc:
        fail(f"could not launch the stack runner ({exc}). Relay this to the human and stop; "
             "do not run docker or ./run.sh yourself.")
    if proc.returncode != 0:
        sys.stderr.write(f"up FAILED (run.sh {lane} exited {proc.returncode}). Relay the last "
                         "line above to the human and STOP. Do NOT retry in a loop, run docker "
                         "commands, chmod, or read the scripts.\n")
        return 1
    sys.stdout.write("up OK. "
                     + ("Whole stack incl. ComfyUI (image lane) is serving. "
                        if a.gpu else
                        "Core stack is serving (no image lane; `up-gpu` exists but only for "
                        "rendering NEW hero images). ")
                     + f"Local site {SITE} - API + panel {PUBLISH}. All LOCAL: going public "
                       "is only ever the separate `publicar --yes`.\n")
    return 0


def cmd_status(a):
    """Is the portal ONLINE and serving? A read-only liveness report over the four services the
    operator asks about, each probed INDEPENDENTLY (one down service never hides another), with an
    [OK]/[WARN]/[FAIL] line per service and a one-line verdict:
      - backend  (publish API, /healthz): owns all content and serves the panel. CORE.
      - site     (local preview site): the local portal you `preview` to. CORE.
      - public   (the deployed production origin): the live public site. WARN if down -- a
                 deploy has not landed yet (or none was made), which is not a stack failure.
      - comfyui  (the image lane): WARN if down -- optional, only image work needs it.
    `--local-only` skips the public-internet probe; `--json` prints the machine-readable verdict.

    Exits 0 when the CORE (backend + local site) are up, 1 otherwise (WARNs never fail). It NEVER
    writes, deploys, or regenerates anything. To confirm a SPECIFIC piece is live you do NOT need
    this verb: a successful `preview` already staged it and prints its `PREVIEW:` permalink -- that
    link is the confirmation, just open it (the generator watcher repaints within a few seconds).
    Do NOT loop on generate/deploy, and do NOT re-check a piece over and over, to force it."""
    checks = [
        ("backend", PUBLISH + "/healthz", {200}, True),
        ("site", SITE + "/", {200, 301, 302}, True),
        ("comfyui", COMFY + "/system_stats", {200}, False),
    ]
    if not a.local_only and PUBLIC_URL:
        checks.append(("public", PUBLIC_URL + "/", {200, 301, 302}, False))

    services, rows, core_down = {}, [], False
    for label, url, ok_set, core in checks:
        up, code, detail = _probe(url, ok_set)
        level = "OK" if up else ("FAIL" if core else "WARN")
        if not up and core:
            core_down = True
        note = "" if up else ("" if core else " - optional/not-yet")
        rows.append((level, f"{label:<8} {url} ({detail}){note}"))
        services[label] = {"up": up, "status": code, "url": url, "core": core}

    ready = not core_down
    if a.json:
        print(json.dumps({"ready": ready, "services": services,
                          "public_url": PUBLIC_URL or None},
                         ensure_ascii=False, indent=2))
        return 0 if ready else 1
    for level, text in rows:
        sys.stdout.write(f"  [{level}] {text}\n")
    sys.stdout.write("  --- " + ("ONLINE: backend + local site are serving."
                                 if ready else "OFFLINE: a CORE service is down.")
                     + " A piece's own `PREVIEW:` link (printed by preview) is its confirmation; "
                       "do NOT run generate/deploy to check.\n")
    return 0 if ready else 1


def cmd_deploy(a):
    """Publish the WHOLE current local snapshot to the PUBLIC production site (Cloudflare Pages)
    by running deploy/deploy-cdn.sh, so the operating agent NEVER has to touch a shell script,
    make target, or the generator: `publicar` is a verb like every other. This is the ONE public,
    irreversible, outward-facing action, so it REFUSES without --yes (the publicar sub-skill still
    requires you to show the human what will go live and get an explicit yes first).

    It streams the script's own output, and on the script's `FATAL:` early-exit (CLOUDFLARE_ACCOUNT_ID
    unset, or a half-set reactions binding) it relays that exact line and stops -- do NOT try to
    work around it, fix perms, regenerate, or read the deploy/generator source; relay the message to
    the human and let them complete the one setup step, then re-run `publicar`. On success it prints
    the public origin and reminds you to VERIFY by opening the piece's link, never by re-deploying."""
    if not a.yes:
        sys.stderr.write("publicar publishes the WHOLE snapshot to the PUBLIC site "
                         f"{PUBLIC_URL or '(production)'}. This is irreversible and outward-facing. "
                         "Show the human what will go live, get an explicit yes, then re-run with "
                         "--yes.\n")
        return 1
    script = _REPO / "deploy" / "deploy-cdn.sh"
    if not script.is_file():
        fail(f"deploy script not found at {script} (expected deploy/deploy-cdn.sh in the repo).")
    sys.stdout.write(f"Publishing the local snapshot to {PUBLIC_URL or 'production'}...\n")
    sys.stdout.flush()
    try:
        proc = subprocess.run(["bash", str(script)], cwd=str(_REPO), check=False)
    except Exception as exc:
        fail(f"could not launch the deploy script ({exc}). This is the one infra action; if it "
             "cannot run, tell the human -- do not improvise a workaround.")
    if proc.returncode != 0:
        sys.stderr.write(f"publicar FAILED (the publish script exited {proc.returncode}). Relay the "
                         "last line above to the human and let them fix that ONE step, then re-run "
                         "`publicar --yes`. Do NOT run generate, init-perms, chmod, or read the "
                         "generator source.\n")
        return 1
    sys.stdout.write(f"publicar OK. Live at {PUBLIC_URL or 'the production origin'}. Verify by "
                     "opening the piece's public link (NOT by publishing again); plain `status` "
                     "confirms the public origin is serving.\n")
    return 0


def cmd_doctor(a):
    """Self-check the unified skill end to end and print an [OK]/[WARN]/[FAIL] report:
      1. stack services up (the publish backend + site are core; ComfyUI is optional, image
         lane only). Authors/sources are the backend; there is no separate config service.
      2. the skill package (the resolver routes only to sub-skills that exist and carry
         frontmatter whose name matches the directory),
      3. the agentic recipe (the prompts dir exists; workflow/manifest.json parses; every
         manifest node file is present on disk; cli/workflow/parameters.json parses; the
         editorial/style.md and persona/synthesize.md files the verbs read are present).
    Exits 1 if any check FAILs, 0 otherwise (warnings do not fail). Never raises: an
    unreachable service or a missing file is reported, not thrown."""
    rows = []

    def add(level, text):
        rows.append((level, text))

    here = Path(__file__).resolve().parent

    # 1) stack services
    for label, method, url, ok_set, optional in (
        ("publish backend", "GET", PUBLISH + "/healthz", {200}, False),
        ("site", "GET", SITE + "/", {200, 301, 302}, False),
        ("ComfyUI (image lane)", "GET", COMFY + "/system_stats", {200}, True),
    ):
        try:
            st, _ = _req(method, url, timeout=5)
            up, detail = st in ok_set, f"HTTP {st}"
        except Exception as exc:
            up, detail = False, str(exc)
        add("OK" if up else ("WARN" if optional else "FAIL"),
            f"{label} {url} ({detail})" + (" - optional" if optional and not up else ""))

    # 2) skill package: resolver -> real sub-skills with valid frontmatter
    resolver = here / "SKILL.md"
    if not resolver.is_file():
        add("FAIL", "resolver cli/SKILL.md is missing")
        routed = []
    else:
        routed = sorted(set(re.findall(r"skills/([\w-]+)/SKILL\.md", resolver.read_text(encoding="utf-8"))))
    for name in routed:
        f = here / "skills" / name / "SKILL.md"
        if not f.is_file():
            add("FAIL", f"resolver routes to skills/{name}/ but the file is missing")
            continue
        fm = _frontmatter_map(f.read_text(encoding="utf-8"))
        if fm.get("name") != name:
            add("FAIL", f"skills/{name}/SKILL.md frontmatter name={fm.get('name')!r} != dir {name!r}")
        elif not fm.get("description"):
            add("FAIL", f"skills/{name}/SKILL.md has no description")
        else:
            add("OK", f"sub-skill {name} routed + valid")

    # 3) the agentic recipe: on-disk files under the prompts dir (no server)
    if PROMPTS_DIR.is_dir():
        add("OK", f"recipe dir {PROMPTS_DIR}")
    else:
        add("FAIL", f"recipe dir {PROMPTS_DIR} is missing (expected this repo's prompts/; set "
                    f"CENSURADO_PROMPTS_DIR to override)")
    manifest = None
    mpath = _prompt_path("workflow/manifest.json")
    if mpath is not None and mpath.is_file():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
            add("OK", "workflow/manifest.json parses")
        except Exception as exc:
            add("FAIL", f"workflow/manifest.json does not parse: {exc}")
    else:
        add("FAIL", f"no workflow/manifest.json under {PROMPTS_DIR}")
    if manifest is not None:
        need = {"00-mode"} | {k for keys in (manifest.get("modes") or {}).values() for k in keys}
        missing = sorted(k for k in need
                         if not (lambda p: p is not None and p.is_file())(_prompt_path(f"workflow/{k}.md")))
        if missing:
            add("FAIL", f"workflow node files missing on disk: {', '.join(missing)}")
        else:
            add("OK", f"workflow: {len(manifest.get('modes') or {})} modes, "
                      f"{len(need)} node files all present")
    try:
        params = _fetch_params()
        add("OK", "parameters.json parses: " + ", ".join(f"{k}={v}" for k, v in params.items()))
    except Exception as exc:
        add("FAIL", f"parameters.json does not parse: {exc}")
    # recipe files a wired verb reads directly (style -> editorial/style.md, the persona-synthesis
    # prompt create-author follows). A missing one is a silent verb dead-end, so surface it here.
    for label, key in (("editorial/style.md (the `style` verb)", "editorial/style.md"),
                       ("persona/synthesize.md (author synthesis)", "persona/synthesize.md")):
        fp = _prompt_path(key)
        if fp is not None and fp.is_file() and fp.read_text(encoding="utf-8").strip():
            add("OK", f"recipe file {label} present")
        else:
            add("FAIL", f"recipe file {label} missing/empty under {PROMPTS_DIR}")

    for level, text in rows:
        sys.stdout.write(f"  [{level}] {text}\n")
    fails = sum(1 for lv, _ in rows if lv == "FAIL")
    warns = sum(1 for lv, _ in rows if lv == "WARN")
    sys.stdout.write(f"  --- {fails} failure(s), {warns} warning(s). "
                     + ("NOT ready to operate.\n" if fails else "OK to operate.\n"))
    return 1 if fails else 0


# ---- the step gate (serve ONE workflow node at a time) ----------------------
# The newsroom workflow is served one node at a time so the agent never holds the whole
# playbook at once (which is what lets a model collapse the steps into one pass and skip
# the editorial gates). Each `step` call reads ONE node body from this repo's prompts/ dir
# (the single source of truth for the workflow prompt files), fills the numeric parameters
# from cli/workflow/parameters.json, prints it, and appends the exact NEXT command computed
# from workflow/manifest.json (same prompts/ dir). The gate keeps no session: the agent carries
# its position by passing the node key back. The prompts are files; the CLI owns the parameters.


# The numeric editorial parameters travel WITH the CLI/skill: cli/workflow/parameters.json holds
# the enforced floor/cap the WALK fills into the nodes. The workflow PROMPTS (nodes + manifest)
# are file-based under this repo's prompts/ dir, not here. Override the parameters dir with
# CENSURADO_WORKFLOW_DIR (used by the tests).
_WORKFLOW_DIR = Path(os.environ.get(
    "CENSURADO_WORKFLOW_DIR", str(Path(__file__).resolve().parent / "workflow")))


def _prompt_path(key):
    """Resolve a prompt key (its relative path with forward slashes, e.g.
    'workflow/50-draft.md') to a file under PROMPTS_DIR, or None if the key is not a
    .md/.json or would escape the dir (path-traversal guard)."""
    if not (key.endswith(".md") or key.endswith(".json")):
        return None
    root = PROMPTS_DIR.resolve()
    candidate = root.joinpath(*key.split("/")).resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate


def _fetch_prompt_body(key):
    """One prompt body by key (e.g. 'workflow/50-draft.md', 'workflow/manifest.json', or
    'persona/synthesize.md'), read from the on-disk recipe under PROMPTS_DIR (this repo's
    prompts/ dir; git is the version history). No server is involved: the walk reads files."""
    path = _prompt_path(key)
    if path is None:
        sys.exit(f"FATAL: invalid prompt key {key!r} (must be a .md/.json path under the recipe dir)")
    if not path.is_file():
        sys.exit(f"FATAL: no prompt {key!r} under {PROMPTS_DIR}. Set CENSURADO_PROMPTS_DIR if this "
                 f"repo's prompts/ dir lives elsewhere.")
    return _read_file(path, f"the prompt {key!r}")


def _fetch_params():
    """The numeric editorial parameters {NAME: value} for placeholder filling, read from the
    recipe's cli/workflow/parameters.json (versioned with the CLI). A MISSING file yields an empty
    map and placeholders are left as-is; a MALFORMED file fails with a clear error rather than
    silently dropping the enforced floor."""
    path = _WORKFLOW_DIR / "parameters.json"
    if not path.is_file():
        return {}
    data = _json(_read_file(path, "parameters.json"), "parameters.json", want=dict)
    return {k: v for k, v in data.items() if v is not None}


def fill_params(text, params):
    """Substitute {{MIN_SOURCES}} / {{MIN_PER_TYPE}} / {{RESPIN_PASSES}} / {{TOPIC_CAP}} with the
    live values. Pure and null-safe: an absent parameter leaves its placeholder untouched."""
    for name, val in params.items():
        text = text.replace("{{" + name + "}}", str(val))
    return text


def mode_sequence(manifest, mode):
    """The ordered, ENABLED node keys for `mode` from the manifest (disabled nodes dropped).
    `disabled` is a per-mode map ({mode: [keys]}); a flat list is tolerated as a global
    disable for a hand-edited manifest."""
    modes = manifest.get("modes") or {}
    if mode not in modes:
        sys.exit(f"FATAL: unknown mode {mode!r}; known modes: {', '.join(sorted(modes)) or '(none)'}")
    seq = modes[mode]
    if not isinstance(seq, list):
        sys.exit(f"FATAL: workflow manifest mode {mode!r} is not a list of node keys")
    cfg = manifest.get("disabled") or {}
    disabled = set(cfg.get(mode, []) if isinstance(cfg, dict) else cfg)
    return [k for k in seq if k not in disabled]


def next_line(seq, key, mode):
    """The NEXT command after serving `key` in `mode`, or DONE at the end of the sequence."""
    if key in seq:
        i = seq.index(key)
        if i + 1 < len(seq):
            return (f"NEXT: when this step is done, run: "
                    f"python3 cli/censurado.py step {seq[i + 1]} --mode {mode}")
    return "DONE: this walk is complete."


_MAX_REFETCH = 4  # a node may be re-served a few times (e.g. the evaluate/respin loop); beyond
                  # that it is a spin, not progress, and the loop shield stops it.


# The scratch dir the walk (and the image/tweet verbs) reads and writes when the driver did not
# set $CENSURADO_WORK. Having a stable default (never None) means the agent NEVER has to know the
# env var exists or hunt for a directory: the walk always prints it and always enforces the
# artifact gate. A fresh walk wipes it (see cmd_step) so a serial batch reuses it one piece at a
# time. A driver that wants per-article isolation can still set $CENSURADO_WORK itself.
_DEFAULT_WORK = Path(tempfile.gettempdir()) / "censurado-work"


def _work_dir():
    """The scratch dir the walk writes its artifacts (ledger.md, draft.md, image.json, tweets.json)
    to. It is $CENSURADO_WORK when the driver set it, else the stable default `_DEFAULT_WORK`. It is
    NEVER None, so the artifact gate + loop shield always run and every verb resolves the SAME dir
    without the agent threading anything."""
    w = os.environ.get("CENSURADO_WORK", "").strip()
    return Path(w) if w else _DEFAULT_WORK


def _gate(key, seq, produces, workdir, mode):
    """When a work dir is set, ENFORCE the walk instead of merely describing it: (1) a LOOP SHIELD
    that refuses to keep re-serving the same node forever, and (2) an ARTIFACT GATE that blocks
    advancing to a node until the PREVIOUS node actually wrote its required file. Either prints a
    directive and exits non-zero (the driver feeds that back to the model); a pass returns None."""
    workdir.mkdir(parents=True, exist_ok=True)
    counts_f = workdir / ".step-counts.json"
    counts = {}
    if counts_f.is_file():
        try:
            counts = json.loads(counts_f.read_text())
        except (json.JSONDecodeError, OSError):
            counts = {}
        if not isinstance(counts, dict):
            counts = {}
    counts[key] = counts.get(key, 0) + 1
    counts_f.write_text(json.dumps(counts))
    if counts[key] > _MAX_REFETCH:
        need = produces.get(key)
        hint = f" If its job is not done, do it and save {workdir / need}." if need else ""
        sys.exit(f"STOP: you have fetched node {key} {counts[key]} times without moving on. Stop "
                 f"re-fetching it: do what it asks, then run the NEXT command it printed.{hint}")
    if key in seq:
        i = seq.index(key)
        if i > 0:
            prev = seq[i - 1]
            need = produces.get(prev)
            path = workdir / need if need else None
            if path is not None and not (path.is_file() and path.stat().st_size > 20):
                sys.exit(f"BLOCKED: you cannot start {key} yet. The previous step {prev} must first "
                         f"do its work and save it to {path}. Go back: run `python3 cli/censurado.py "
                         f"step {prev} --mode {mode}`, complete it, write {path}, then advance to {key}.")


def cmd_step(a):
    """Serve one workflow node (the step gate). No key + no mode: the mode picker (node 00).
    --mode with no key: the first node of that mode. A key: that node, plus the NEXT line. The
    node bodies and manifest are read from this repo's prompts/workflow/ (PROMPTS_DIR) and the
    numeric parameters from cli/workflow/parameters.json; no server is involved. When the driver
    sets $CENSURADO_WORK, the gate ENFORCES the walk (artifact gate + loop shield) instead of only
    describing it, and tells each producing node where to save its artifact."""
    if a.list:
        manifest = _json(_fetch_prompt_body("workflow/manifest.json"), "the workflow manifest", want=dict)
        modes = manifest.get("modes") or {}
        for mode in ([a.mode] if a.mode else sorted(modes)):
            print(f"# {mode}")
            for k in mode_sequence(manifest, mode):
                print(f"  {k}")
        return 0

    params = _fetch_params()
    if not a.key and not a.mode:  # the entry: the mode picker (node 00)
        sys.stdout.write(fill_params(_fetch_prompt_body("workflow/00-mode.md"), params).rstrip() + "\n")
        return 0

    manifest = (_json(_fetch_prompt_body("workflow/manifest.json"), "the workflow manifest", want=dict)
                if a.mode else {})
    seq = mode_sequence(manifest, a.mode) if a.mode else []
    key = a.key or (seq[0] if seq else None)
    if key is None:
        sys.exit(f"FATAL: mode {a.mode!r} has no enabled steps")
    produces = manifest.get("produces") or {}
    workdir = _work_dir()
    # A FRESH walk (mode given, no explicit node = the agent started the walk) that is using the
    # shared default scratch clears it first, so the previous article's ledger/draft/counts cannot
    # leak into this one. A driver that set $CENSURADO_WORK owns its own dir, so leave that alone.
    defaulting = not os.environ.get("CENSURADO_WORK", "").strip()
    if a.mode and not a.key and defaulting and workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    if a.mode:
        _gate(key, seq, produces, workdir, a.mode)  # always enforce; may print a directive and exit

    body = fill_params(_fetch_prompt_body(f"workflow/{key}.md"), params)
    if a.mode and key in seq:
        # Stamp the CURRENT node key + position so a small-model driver always knows where it is
        # in the walk (the NEXT line only names the next node); it must not have to guess its id.
        sys.stdout.write(f"STEP {key}  [{a.mode}, node {seq.index(key) + 1} of {len(seq)}]\n\n")
        # Name the scratch dir up front so the agent writes there directly and never runs echo/ls/
        # doctor to hunt for it (a weak driver otherwise wanders looking for where its files go).
        sys.stdout.write(f"WORK DIR: {workdir}  (your scratch for this piece; save the file each "
                         f"ARTIFACT line names here, nothing else on disk is yours to touch)\n\n")
    sys.stdout.write(body.rstrip() + "\n\n")
    if produces.get(key):
        sys.stdout.write(f"ARTIFACT: before you run NEXT, save this step's output to "
                         f"{workdir / produces[key]} (that file is required to advance).\n\n")
    sys.stdout.write((next_line(seq, key, a.mode) if a.mode
                      else "NEXT: re-run with --mode <mode> so the next step can be computed.") + "\n")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="censurado.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="read an article as JSON")
    g.add_argument("slug")
    g.set_defaults(fn=cmd_get)

    ar = sub.add_parser("archive",
                        help="list published articles light (title/description/date, no bodies): "
                             "an author's archive, or a whole day across authors with --day")
    ar.add_argument("author", nargs="?", default="",
                    help="author id/slug whose archive to list; omit (with --day) for the whole day across authors")
    ar.add_argument("--day", default="",
                    help="all articles published on this UTC day (YYYY-MM-DD) across every author; "
                         "the loader the portada arrange reads")
    ar.add_argument("--q", default="", help="free-text filter (an entity or theme), passed to the backend")
    ar.add_argument("--since", default="", help="only articles published at/after (YYYY-MM-DD or RFC3339)")
    ar.add_argument("--until", default="",
                    help="only articles published up to this day inclusive (YYYY-MM-DD), "
                         "or strictly before an RFC3339 instant")
    ar.add_argument("--limit", type=int, default=0, help="cap the list (0 = backend default)")
    ar.set_defaults(fn=cmd_archive)

    se = sub.add_parser("sections",
                        help="consult the LIVE section vocabulary: distinct section values on "
                             "non-deleted articles with counts (also authors + topics)")
    se.add_argument("--sections", action="store_true",
                    help="print ONLY the section vocabulary (the topic axis is long: on a big corpus the full report runs to tens of KB, which is a lot of context to spend on \"what sections exist\")")
    se.add_argument("--authors", action="store_true", help="print only the author distribution")
    se.add_argument("--topics", action="store_true", help="print only the topic distribution")
    se.set_defaults(fn=cmd_sections)

    pub = sub.add_parser("preview", aliases=["previsualizar"],
                         help="stage an article to your LOCAL preview site (the link is printed; debug-only, "
                              "NOT public); going live is the separate `publicar --yes` verb")
    pub.add_argument("--author", required=True, help="persona slug; byline+section auto-filled from it")
    pub.add_argument("--title", required=True)
    pub.add_argument("--section", default="")
    pub.add_argument("--subtitle", default="")
    pub.add_argument("--description", default="")
    pub.add_argument("--body", default="")
    pub.add_argument("--body-file", dest="body_file", default="", help="read the markdown body from a file")
    pub.add_argument("--topics", default="", help="comma-separated tags (themes + named entities)")
    pub.add_argument("--keywords", default="", help="comma-separated SEO keywords")
    pub.add_argument("--slug", default="")
    pub.add_argument("--published-at", dest="published_at", default="", help="RFC3339; omit for now")
    pub.add_argument("--image", default="")
    pub.add_argument("--image-alt", dest="image_alt", default="")
    pub.add_argument("--image-caption", dest="image_caption", default="",
                     help="optional epígrafe the SITE renders as text under the hero "
                          "(never baked into the image pixels)")
    pub.add_argument("--image-credit", dest="image_credit", default="",
                     help="optional source credit under the hero, e.g. 'Prensa Municipalidad de X'")
    pub.add_argument("--card-type", dest="card_type", default="",
                     help="what the front-page CARD shows: text|image|youtube|video (authored, "
                          "separate from the body). Omit to derive it from the hero/media.")
    pub.add_argument("--card-src", dest="card_src", default="",
                     help="the card's media ref (a /media path for an image or a video poster, or "
                          "a YouTube id/url); defaults to the hero still, or --youtube for a youtube card")
    pub.add_argument("--card-alt", dest="card_alt", default="", help="alt text for the card image/poster")
    pub.add_argument("--youtube", default="")
    pub.add_argument("--author-name", dest="author_name", default="")
    pub.add_argument("--author-bio", dest="author_bio", default="")
    pub.add_argument("--author-avatar", dest="author_avatar", default="")
    pub.add_argument("--tweets-file", dest="tweets_file", default="", help="JSON array of snapshots")
    pub.add_argument("--idempotency", default="")
    pub.add_argument("--dry-run", action="store_true", help="print the payload, do not POST")
    pub.set_defaults(fn=cmd_publish)

    e = sub.add_parser("edit", help="GET -> modify -> PUT an existing article in place")
    e.add_argument("slug")
    e.add_argument("--set", action="append", help="top-level field, e.g. --set title='...'")
    e.add_argument("--meta", action="append", help="metadata field, e.g. --meta subtitle='...'")
    e.add_argument("--meta-json", dest="meta_json", default="",
                   help="merge a JSON object into metadata, for the values --meta cannot express "
                        "as a string: the front-page card, e.g. "
                        "--meta-json '{\"card\":{\"type\":\"image\",\"src\":\"/media/x.png\"}}'")
    e.add_argument("--body-file", dest="body_file", default="", help="replace the body from a file")
    e.add_argument("--tweets-file", dest="tweets_file", default="", help="set metadata.tweets from a JSON array")
    e.add_argument("--published-at", dest="published_at", default="")
    e.add_argument("--dry-run", action="store_true", help="print the payload, do not PUT")
    e.set_defaults(fn=cmd_edit)

    up = sub.add_parser("unpublish",
                        help="remove an article from the site (needs --yes; soft tombstone, restorable)")
    up.add_argument("slug", help="article slug to take down")
    up.add_argument("--yes", action="store_true",
                    help="confirm the removal (soft-delete; restorable via the API)")
    up.set_defaults(fn=cmd_unpublish)

    m = sub.add_parser("media", help="upload an image/video, print {url}")
    m.add_argument("file")
    m.set_defaults(fn=cmd_media)

    im = sub.add_parser("image", help="render an art-directed FLUX.2 hero (ComfyUI) and upload it")
    im.add_argument("--prompt", required=True, help="the art-directed image prompt (see cli/skills/media/SKILL.md)")
    im.add_argument("--alt", default="", help="short alt text (in the author's language) for accessibility")
    im.add_argument("--seed", type=int, default=None, help="override the stable per-prompt seed")
    im.add_argument("--width", type=int, default=1344, help="wide landscape by default (~16:9); heroes read wide, not tall")
    im.add_argument("--height", type=int, default=768)
    im.add_argument("--steps", type=int, default=4)
    im.add_argument("--template", default="", help="ComfyUI graph template path (default: flux2_klein)")
    im.add_argument("--dry-run", action="store_true", help="print the graph, do not render")
    im.set_defaults(fn=cmd_image)

    tw = sub.add_parser("tweet", help="capture an X post as a {{tweet:id}} snapshot (fxtwitter, keyless)")
    tw.add_argument("ref", help="status id or URL")
    tw.add_argument("--from-json", dest="from_json", default="", help="map a saved fxtwitter response instead of fetching")
    tw.set_defaults(fn=cmd_tweet)

    tr = sub.add_parser("truth", help="capture a Trump Truth Social post as a {{tweet:id}} snapshot (keyless)")
    tr.add_argument("ref", help="status id or URL")
    tr.add_argument("--from-json", dest="from_json", default="", help="map a saved Truth Social response instead of fetching")
    tr.set_defaults(fn=cmd_truth)

    pe = sub.add_parser("personas", help="list author ids (handles) in the backend registry")
    pe.set_defaults(fn=cmd_personas)

    pr = sub.add_parser("persona", help="fetch ONE author's full record as JSON (voice/beat/sources)")
    pr.add_argument("id", help="author id/slug")
    pr.set_defaults(fn=cmd_persona)

    ca = sub.add_parser("create-author",
                        help="persist an agent-authored persona into the backend author registry")
    ca.add_argument("--file", default="",
                    help="persona JSON path (display_name/beat/who_i_am/style required), or '-' to read stdin; required (omitting it errors)")
    ca.set_defaults(fn=cmd_create_author)

    ea = sub.add_parser("edit-author",
                        help="modify an EXISTING author in place (read-modify-write): voice, bio, "
                             "avatar picture, beat, language, few-shots, profile topics")
    ea.add_argument("id", help="author id/handle")
    ea.add_argument("--set", action="append",
                    help=f"public field, one of {'|'.join(_AUTHOR_PUBLIC)}, e.g. "
                         "--set avatar=/media/<hash>.png (upload it first with `media`)")
    ea.add_argument("--meta", action="append",
                    help=f"private tail field, one of {'|'.join(_AUTHOR_META)}, e.g. "
                         "--meta beat=politics")
    ea.add_argument("--meta-json", dest="meta_json", default="",
                    help="merge a JSON object into the private tail (the only way to set the "
                         "few_shots_pos / few_shots_neg arrays)")
    ea.add_argument("--profile-topics", dest="profile_topics", default=None,
                    help='comma-separated profile topics (replaces the set); "" clears them')
    ea.add_argument("--dry-run", action="store_true", help="print the author row, do not POST")
    ea.set_defaults(fn=cmd_edit_author)

    sb = sub.add_parser("sync-byline",
                        help="push an author's current name/bio/picture onto the articles they "
                             "already published (each stores its own copy, so an edit leaves the "
                             "old byline behind)")
    sb.add_argument("id", help="author id/handle")
    sb.add_argument("--limit", type=int, default=0, help="cap the articles scanned (0 = 500)")
    sb.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    sb.set_defaults(fn=cmd_sync_byline)

    rma = sub.add_parser("remove-author", help="tombstone an author in the backend (needs --yes; soft, restorable)")
    rma.add_argument("id", help="author id/slug")
    rma.add_argument("--yes", action="store_true", help="confirm the delete (it is not undoable via the API)")
    rma.set_defaults(fn=cmd_remove_author)

    so = sub.add_parser("sources", help="show or (with --set) replace an author's attached sources")
    so.add_argument("id", help="author id/handle")
    so.add_argument("--set", default=None,
                    help="comma-separated source slugs to attach (replaces the set); omit to just show")
    so.set_defaults(fn=cmd_sources)

    ptp = sub.add_parser("profile-topics",
                         help="show or (with --set) replace an author's curated public-profile topics")
    ptp.add_argument("id", help="author id/slug")
    ptp.add_argument("--set", default=None,
                     help='comma-separated topics for the profile page (replaces the set); "" clears; omit to show')
    ptp.set_defaults(fn=cmd_profile_topics)

    tp = sub.add_parser("topics",
                        help="inventory distinct article tags across the corpus (count + slugs per tag); "
                             "the read side of topic normalization")
    tp.add_argument("--limit", type=int, default=0, help="cap the articles scanned (0 = 1000)")
    tp.set_defaults(fn=cmd_topics)

    rmt = sub.add_parser("remove-topic",
                         help="tombstone a topic in the /topics registry (soft, restorable); reconciles the "
                              "facet index after a topic-cleanse merge")
    rmt.add_argument("slug", help="the registry topic slug to remove (as shown by the Temas tab / GET /topics)")
    rmt.add_argument("--yes", action="store_true", help="confirm the delete")
    rmt.set_defaults(fn=cmd_remove_topic)

    pda = sub.add_parser("portada",
                         help="write or read the per-day front-page plan (portada) in the publish backend")
    pda.add_argument("date", help="YYYY-MM-DD")
    pda.add_argument("--set-json", dest="set_json", default=None,
                     help='JSON object {"entries":[{"slug","role"}]} to POST; omit to list all. '
                          '(The front-page Recomendado rail is a separate GLOBAL list: see the recomendado verb.)')
    pda.set_defaults(fn=cmd_portada)

    rec = sub.add_parser("recomendado",
                         help="read or set the GLOBAL front-page Recomendado list (up to 10 slugs; persists across days)")
    rec.add_argument("--set", dest="set_slugs", default=None,
                     help="comma-separated article slugs, in order (up to 10); replaces the whole list")
    rec.add_argument("--clear", action="store_true",
                     help="empty the list (the front page still shows the widget, with no items)")
    rec.set_defaults(fn=cmd_recomendado)

    po = sub.add_parser("portals", help="list available source slugs (the ids to attach to authors)")
    po.set_defaults(fn=cmd_portals)

    pt = sub.add_parser("prompt", help="fetch a workflow prompt template by key (e.g. workflow/50-draft.md)")
    pt.add_argument("key", help="prompt key, e.g. persona/synthesize.md or workflow/50-draft.md")
    pt.set_defaults(fn=cmd_prompt)

    spp = sub.add_parser("set-prompt",
                         help="edit a prompt in place (writes the recipe .md/.json file; git is its history)")
    spp.add_argument("key", help="e.g. workflow/50-draft.md or persona/synthesize.md (an existing prompt file)")
    spp.add_argument("--body-file", dest="body_file", default="", help="read the new body from a file")
    spp.add_argument("--body", default="", help="the new body inline (use --body-file for long text)")
    spp.set_defaults(fn=cmd_set_prompt)

    sy = sub.add_parser("style",
                        help="read the editorial style guide (voice/house rules); the per-language "
                             "lexicon/orthography live in `editorial-rules`, numbers in parameters.json")
    sy.set_defaults(fn=cmd_style)

    er = sub.add_parser("editorial-rules",
                        help="read a language's editorial anchors (banned lexicon/swaps, orthography, "
                             "slop phrases, attribution/disclaimer exemplars) from the backend")
    er.add_argument("lang", nargs="?", default="es",
                    help="language code of the author you write as (its `language`); defaults to es")
    er.add_argument("--bot-directive", dest="bot_directive", action="store_true",
                    help="print ONLY the Telegram bot response directive (the bridge reads this)")
    er.set_defaults(fn=cmd_editorial_rules)

    tl = sub.add_parser("translate",
                        help="localize the site into a new language: print the untranslated UI + "
                             "editorial rows for <lang>, then --apply the filled-in JSON (idempotent)")
    tl.add_argument("lang", help="target language code to localize into, e.g. pt, de, zh")
    tl.add_argument("--apply", action="store_true",
                    help="APPLY mode: read the completed job JSON (--file) and upsert only the "
                         "missing rows (never overwrites an existing translation)")
    tl.add_argument("--file", default="",
                    help="in --apply mode, the completed job JSON path (or - to read stdin)")
    tl.set_defaults(fn=cmd_translate)

    sfl = sub.add_parser("set-floor",
                         help="set the sourcing floor the walk enforces (MIN_SOURCES / MIN_PER_TYPE) in parameters.json")
    sfl.add_argument("--min-sources", dest="min_sources", type=int, default=None,
                     help="cross-source corroboration floor (>= 1)")
    sfl.add_argument("--min-per-type", dest="min_per_type", type=int, default=None,
                     help="per-lean minimum, left/neutral/right (>= 0)")
    sfl.set_defaults(fn=cmd_set_floor)

    up = sub.add_parser("up",
                        help="bring the LOCAL stack up (backend + generator + site, no GPU) and wait "
                             "until it serves; the ONE sanctioned way to start the stack")
    up.set_defaults(fn=cmd_up, gpu=False)

    ug = sub.add_parser("up-gpu",
                        help="bring the WHOLE local stack up incl. ComfyUI (the image lane; heavy, "
                             "GPU box only, needed only to render NEW hero images)")
    ug.set_defaults(fn=cmd_up, gpu=True)

    st = sub.add_parser("status",
                        help="is the portal ONLINE? liveness of backend + local site + comfyui + the "
                             "public deploy (a piece's own PREVIEW: link is its confirmation)")
    st.add_argument("--local-only", dest="local_only", action="store_true",
                    help="skip the public-internet probe (local stack only)")
    st.add_argument("--json", action="store_true", help="print the machine-readable verdict")
    st.set_defaults(fn=cmd_status)

    dp = sub.add_parser("publicar", aliases=["publish", "deploy"],
                        help="PUBLISH the whole local snapshot to the PUBLIC production site / go live "
                             "(needs --yes). This is what publicar / publish mean; `preview` is local-only")
    dp.add_argument("--yes", action="store_true",
                    help="confirm the public, irreversible publish (required)")
    dp.set_defaults(fn=cmd_deploy)

    dr = sub.add_parser("doctor",
                        help="self-check the stack, the skill package, and the on-disk agentic recipe")
    dr.set_defaults(fn=cmd_doctor)

    stp = sub.add_parser("step",
                         help="serve ONE workflow node at a time (the step gate); prints the NEXT command")
    stp.add_argument("key", nargs="?", default="",
                     help="node key, e.g. 30-research; omit for the mode picker (or the first node with --mode)")
    stp.add_argument("--mode", default="",
                     help="workflow mode (run `step --list` to see all): single-article, single-author, "
                          "authors, institucional (a lighter formal lane for an unsigned/official piece), "
                          "daily, weekly, last-hour, redactor, publicar, normalize-topics, portal-review, "
                          "topic-cleanse")
    stp.add_argument("--list", action="store_true",
                     help="print the node sequence for the mode (or all modes), without serving a body")
    stp.set_defaults(fn=cmd_step)
    return p


def main(argv=None):
    """Parse and dispatch, with ONE top-level net so the LLM never sees a traceback: a ToolError
    prints as `ERROR: <what> - <how to fix>`; any other unexpected exception is still caught and
    reported as a clean internal-error line (a bug in this CLI, not the model's input). argparse's
    own usage errors keep their SystemExit(2) path. Deliberate agent directives raised via
    `sys.exit(...)` (BLOCKED/STOP/FATAL) pass through unchanged."""
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except ToolError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except KeyboardInterrupt:
        sys.stderr.write("ERROR: interrupted.\n")
        return 130
    except Exception as e:  # last resort: report, never dump a traceback at the model
        sys.stderr.write(f"ERROR: unexpected {type(e).__name__}: {e}\n"
                         "(this is a bug in censurado.py, not your input; the command did not "
                         "complete. Re-check your arguments, or report it.)\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
