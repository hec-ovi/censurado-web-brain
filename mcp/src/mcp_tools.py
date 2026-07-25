"""The tool surface: every content operation an agent needs, one tool per operation.

Each tool declares a JSON Schema for its input and maps to exactly one `censurado.py` verb
(the doctor is the exception: it composes several). The descriptions carry the operating
knowledge a bare agent has no other way to get, because an agent wired to this server has no
repo, no shell, and no skill files: what it reads here is all it knows.
"""
from __future__ import annotations

import json

from mcp_doctor import Doctor, report_text
from mcp_runner import SLOW_TIMEOUT, RunnerError, envelope

# ---- schema helpers ---------------------------------------------------------


def _obj(props: dict, required=()) -> dict:
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


def _s(desc: str, **extra) -> dict:
    return {"type": "string", "description": desc, **extra}


def _b(desc: str, default: bool = False) -> dict:
    return {"type": "boolean", "description": desc, "default": default}


def _i(desc: str, **extra) -> dict:
    return {"type": "integer", "description": desc, **extra}


def _list(desc: str, items=None) -> dict:
    return {"type": "array", "description": desc, "items": items or {"type": "string"}}


RESULT_SCHEMA = {
    "type": "object",
    "description": "The verb's result: exit code, both streams, and the parsed JSON when the "
                   "verb printed JSON.",
    "properties": {
        "ok": {"type": "boolean"},
        "verb": {"type": "string"},
        "exit_code": {"type": "integer"},
        "stdout": {"type": "string"},
        "stderr": {"type": "string"},
        "data": {"description": "The parsed stdout, present only when the verb printed JSON."},
    },
    "required": ["ok", "verb", "exit_code", "stdout", "stderr"],
    "additionalProperties": False,
}

DOCTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "ready": {"type": "boolean"},
        "deep": {"type": "boolean"},
        "counts": {"type": "object", "properties": {
            "ok": {"type": "integer"}, "warn": {"type": "integer"}, "fail": {"type": "integer"}},
            "required": ["ok", "warn", "fail"], "additionalProperties": False},
        "checks": {"type": "array", "items": {
            "type": "object",
            "properties": {"group": {"type": "string"}, "check": {"type": "string"},
                           "level": {"type": "string", "enum": ["OK", "WARN", "FAIL"]},
                           "detail": {"type": "string"}},
            "required": ["group", "check", "level", "detail"], "additionalProperties": False}},
        "summary": {"type": "string"},
        "next_action": {"type": "string"},
    },
    "required": ["ready", "deep", "counts", "checks", "summary"],
    "additionalProperties": False,
}


# ---- argument plumbing ------------------------------------------------------


def _csv(value) -> str:
    """A list or a comma string, both to the comma string the CLI flags take."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip()


def _flags(args: dict, mapping: dict, argv: list) -> list:
    """Append `--flag value` for each present, non-empty argument."""
    for key, flag in mapping.items():
        value = args.get(key)
        if value is None or value == "":
            continue
        argv += [flag, str(value)]
    return argv


def _refused(verb: str, message: str) -> dict:
    """A refusal shaped like a verb result, so a caller reads one envelope either way."""
    return envelope([verb], 2, "", "REFUSED: " + message)


# ---- handlers: health and lifecycle ----------------------------------------


def h_doctor(args, runner):
    return Doctor(runner).report(deep=bool(args.get("deep")))


def h_stack_status(args, runner):
    argv = ["status", "--json"]
    if args.get("local_only"):
        argv.append("--local-only")
    return runner.run(argv)


def h_stack_up(args, runner):
    return runner.run(["up-gpu" if args.get("gpu") else "up"], timeout=SLOW_TIMEOUT)


def h_site_publish(args, runner):
    if args.get("confirm") is not True:
        return _refused("publicar", "site_publish pushes the whole local snapshot to the PUBLIC "
                                    "internet. Show the human what will go live, get an explicit "
                                    "yes, then call it again with confirm=true.")
    return runner.run(["publicar", "--yes"], timeout=SLOW_TIMEOUT)


# ---- handlers: articles -----------------------------------------------------


def h_article_list(args, runner):
    argv = ["archive"]
    if args.get("author"):
        argv.append(str(args["author"]))
    argv = _flags(args, {"day": "--day", "q": "--q", "since": "--since", "until": "--until"}, argv)
    if args.get("limit"):
        argv += ["--limit", str(int(args["limit"]))]
    return runner.run(argv)


def h_article_get(args, runner):
    return runner.run(["get", str(args["slug"])])


def h_article_create(args, runner):
    body_file = runner.write_temp(str(args["body"]), suffix=".md")
    try:
        argv = ["preview", "--author", str(args["author"]), "--title", str(args["title"]),
                "--description", str(args["description"]), "--body-file", str(body_file)]
        argv = _flags(args, {
            "section": "--section", "subtitle": "--subtitle", "slug": "--slug",
            "published_at": "--published-at", "image": "--image", "image_alt": "--image-alt",
            "image_caption": "--image-caption", "image_credit": "--image-credit",
            "card_type": "--card-type", "card_src": "--card-src", "card_alt": "--card-alt",
            "youtube": "--youtube",
        }, argv)
        for key, flag in (("topics", "--topics"), ("keywords", "--keywords")):
            value = _csv(args.get(key))
            if value:
                argv += [flag, value]
        if args.get("dry_run"):
            argv.append("--dry-run")
        return runner.run(argv)
    finally:
        body_file.unlink(missing_ok=True)


def h_article_update(args, runner):
    argv = ["edit", str(args["slug"])]
    body_file = None
    if args.get("body"):
        body_file = runner.write_temp(str(args["body"]), suffix=".md")
        argv += ["--body-file", str(body_file)]
    try:
        for key in ("title", "section", "author"):
            if args.get(key):
                argv += ["--set", f"{key}={args[key]}"]
        if args.get("topics") is not None:
            argv += ["--set", "topics=" + _csv(args["topics"])]
        if args.get("published_at"):
            argv += ["--published-at", str(args["published_at"])]
        metadata = args.get("metadata")
        if metadata:
            if not isinstance(metadata, dict):
                return _refused("edit", "metadata must be a JSON object, e.g. "
                                        '{"subtitle": "...", "card": {"type": "text"}}.')
            argv += ["--meta-json", json.dumps(metadata, ensure_ascii=False)]
        if args.get("dry_run"):
            argv.append("--dry-run")
        return runner.run(argv)
    finally:
        if body_file is not None:
            body_file.unlink(missing_ok=True)


def h_article_delete(args, runner):
    if args.get("confirm") is not True:
        return _refused("unpublish", "removing an article takes it off the site. Confirm with "
                                     "the human, then call again with confirm=true (the delete "
                                     "is soft and restorable).")
    return runner.run(["unpublish", str(args["slug"]), "--yes"])


def h_sections_list(args, runner):
    argv = ["sections"]
    if args.get("axis") == "authors":
        argv.append("--authors")
    elif args.get("axis") == "topics":
        argv.append("--topics")
    return runner.run(argv)


# ---- handlers: front-page layout -------------------------------------------


def h_portada_get(args, runner):
    return runner.run(["portada", str(args.get("date") or "1970-01-01")])


def h_portada_set(args, runner):
    entries = args.get("entries")
    if not isinstance(entries, list) or not entries:
        return _refused("portada", "entries must be a non-empty ordered list of "
                                   '{"slug": "...", "role": ""} objects.')
    plan = {"entries": []}
    for item in entries:
        if not isinstance(item, dict) or not item.get("slug"):
            return _refused("portada", f"each entry needs a slug: got {item!r}.")
        role = str(item.get("role") or "")
        if role not in ("", "important"):
            return _refused("portada", f'role must be "" (single, half row) or "important" '
                                       f'(double, full row); got {role!r}.')
        plan["entries"].append({"slug": str(item["slug"]), "role": role})
    return runner.run(["portada", str(args["date"]), "--set-json",
                       json.dumps(plan, ensure_ascii=False)])


def h_recomendado_get(args, runner):
    return runner.run(["recomendado"])


def h_recomendado_set(args, runner):
    slugs = args.get("slugs")
    if not isinstance(slugs, list):
        return _refused("recomendado", "slugs must be an array (an empty array clears the rail).")
    if len(slugs) > 10:
        return _refused("recomendado", f"the rail holds at most 10 slugs, got {len(slugs)}.")
    if not slugs:
        return runner.run(["recomendado", "--clear"])
    return runner.run(["recomendado", "--set", _csv(slugs)])


# ---- handlers: authors ------------------------------------------------------


def h_author_list(args, runner):
    return runner.run(["personas"])


def h_author_get(args, runner):
    return runner.run(["persona", str(args["id"])])


def h_author_create(args, runner):
    persona = {k: v for k, v in args.items() if v not in (None, "", [], {})}
    missing = [k for k in ("display_name", "beat", "who_i_am", "style") if not persona.get(k)]
    if missing:
        return _refused("create-author", "the persona needs " + ", ".join(missing) +
                                         ". Read the synthesize recipe first with "
                                         "prompt_get(key='persona/synthesize.md').")
    path = runner.write_temp(json.dumps(persona, ensure_ascii=False, indent=2), suffix=".json")
    try:
        return runner.run(["create-author", "--file", str(path)])
    finally:
        path.unlink(missing_ok=True)


def h_author_update(args, runner):
    argv = ["edit-author", str(args["id"])]
    for key in ("name", "bio", "about", "avatar", "gender", "style"):
        if args.get(key) is not None:
            argv += ["--set", f"{key}={args[key]}"]
    for key in ("beat", "who_i_am", "language"):
        if args.get(key) is not None:
            argv += ["--meta", f"{key}={args[key]}"]
    tail = {k: args[k] for k in ("few_shots_pos", "few_shots_neg") if args.get(k) is not None}
    if tail:
        argv += ["--meta-json", json.dumps(tail, ensure_ascii=False)]
    if args.get("profile_topics") is not None:
        argv += ["--profile-topics", _csv(args["profile_topics"])]
    if args.get("dry_run"):
        argv.append("--dry-run")
    if len(argv) == 2:
        return _refused("edit-author", "name at least one field to change (or call author_get "
                                       "first to see what the author currently carries).")
    return runner.run(argv)


def h_author_delete(args, runner):
    if args.get("confirm") is not True:
        return _refused("remove-author", "retiring an author is a content decision. Confirm with "
                                         "the human, then call again with confirm=true.")
    return runner.run(["remove-author", str(args["id"]), "--yes"])


def h_author_sources_get(args, runner):
    return runner.run(["sources", str(args["id"])])


def h_author_sources_set(args, runner):
    sources = args.get("sources")
    if not isinstance(sources, list):
        return _refused("sources", "sources must be an array of source slugs from "
                                   "source_catalog (it REPLACES the author's whole set).")
    return runner.run(["sources", str(args["id"]), "--set", _csv(sources)])


def h_source_catalog(args, runner):
    return runner.run(["portals"])


# ---- handlers: topics -------------------------------------------------------


def h_topics_inventory(args, runner):
    argv = ["topics"]
    if args.get("limit"):
        argv += ["--limit", str(int(args["limit"]))]
    return runner.run(argv)


def h_topic_remove(args, runner):
    if args.get("confirm") is not True:
        return _refused("remove-topic", "confirm the removal with confirm=true (soft, "
                                        "restorable; it only drops the registry row).")
    return runner.run(["remove-topic", str(args["slug"]), "--yes"])


# ---- handlers: media --------------------------------------------------------


def h_media_upload(args, runner):
    path, blob = args.get("path"), args.get("base64_data")
    if bool(path) == bool(blob):
        return _refused("media", "pass exactly one of path (a file already on the host) or "
                                 "base64_data with filename (bytes you carry yourself).")
    if path:
        return runner.run(["media", str(path)])
    import base64 as _b64
    try:
        raw = _b64.b64decode(str(blob), validate=True)
    except (ValueError, TypeError) as exc:
        return _refused("media", f"base64_data does not decode ({exc}).")
    try:
        temp = runner.write_temp_bytes(raw, str(args.get("filename") or "upload.png"))
    except RunnerError as exc:
        return _refused("media", str(exc))
    try:
        return runner.run(["media", str(temp)])
    finally:
        temp.unlink(missing_ok=True)


def h_image_generate(args, runner):
    argv = ["image", "--prompt", str(args["prompt"])]
    argv = _flags(args, {"alt": "--alt"}, argv)
    for key, flag in (("seed", "--seed"), ("width", "--width"),
                      ("height", "--height"), ("steps", "--steps")):
        if args.get(key) is not None:
            argv += [flag, str(int(args[key]))]
    if args.get("dry_run"):
        argv.append("--dry-run")
    return runner.run(argv, timeout=SLOW_TIMEOUT)


# ---- handlers: the editorial recipe ----------------------------------------


def h_editorial_style(args, runner):
    return runner.run(["style"])


def h_editorial_rules(args, runner):
    return runner.run(["editorial-rules", str(args.get("lang") or "es")])


def h_prompt_get(args, runner):
    return runner.run(["prompt", str(args["key"])])


def h_prompt_set(args, runner):
    path = runner.write_temp(str(args["body"]), suffix=".md")
    try:
        return runner.run(["set-prompt", str(args["key"]), "--body-file", str(path)])
    finally:
        path.unlink(missing_ok=True)


def h_workflow_step(args, runner):
    argv = ["step"]
    if args.get("node"):
        argv.append(str(args["node"]))
    if args.get("mode"):
        argv += ["--mode", str(args["mode"])]
    if args.get("list"):
        argv.append("--list")
    return runner.run(argv)


def h_workflow_save(args, runner):
    """Write one walk artifact into the scratch dir the step gate watches. This is the only
    filesystem write this server offers, and it is confined to that dir."""
    name = str(args.get("artifact") or "")
    try:
        path = runner.write_temp_bytes(str(args["content"]).encode("utf-8"), name)
    except RunnerError as exc:
        return _refused("step", str(exc))
    target = runner.work_dir / name
    path.replace(target)
    return envelope(["step"], 0, json.dumps({"saved": str(target), "bytes": target.stat().st_size}),
                    f"Saved {target}. The step gate accepts it as the node's artifact.")


# ---- the registry -----------------------------------------------------------

TOOLS = [
    # -- health and lifecycle
    {
        "name": "doctor",
        "title": "Verify the whole stack",
        "description": "Full preflight before you operate: config and operator token, every "
                       "service, the authenticated content reads the other tools depend on, the "
                       "image lane, the media store, the deploy lane, and the editorial recipe. "
                       "Returns one row per check plus a verdict. Run it first in a session, and "
                       "again whenever a tool fails for a reason you cannot explain. deep=true "
                       "also renders a real image, uploads a probe file, and checks the deploy "
                       "login (slower, and it writes a probe image to the media store).",
        "inputSchema": _obj({"deep": _b("Exercise the image render, media upload, and deploy "
                                        "login for real instead of only checking they are wired.")}),
        "outputSchema": DOCTOR_SCHEMA,
        "handler": h_doctor,
        "text": lambda result: report_text(result),
    },
    {
        "name": "stack_status",
        "title": "Is the portal online",
        "description": "Liveness of the backend, the local site, the image lane, and the public "
                       "production origin. Cheap; use it to answer 'are you online' and 'did my "
                       "change land'. If a core service is down, call stack_up.",
        "inputSchema": _obj({"local_only": _b("Skip the public-internet probe.")}),
        "handler": h_stack_status,
    },
    {
        "name": "stack_up",
        "title": "Start the local stack",
        "description": "Bring the local stack up and wait until it serves. Idempotent. Call it "
                       "at most ONCE: if it fails, relay the error to the human and stop. Use "
                       "gpu=true only when you need to render new hero images (heavier, and it "
                       "needs the GPU box). Everything it starts is local; nothing goes public.",
        "inputSchema": _obj({"gpu": _b("Also start the image lane (ComfyUI).")}),
        "handler": h_stack_up,
    },
    {
        "name": "site_publish",
        "title": "Publish to the public site",
        "description": "Deploy the whole local snapshot to the PUBLIC production site. This is "
                       "the only outward-facing action here and it is irreversible: everything "
                       "currently staged goes live at once. Show the human what will go live and "
                       "get an explicit yes first, then pass confirm=true. Creating or editing "
                       "an article does NOT need this; the local site repaints on its own.",
        "inputSchema": _obj({"confirm": _b("The human explicitly approved going live.")},
                            required=["confirm"]),
        "handler": h_site_publish,
    },
    # -- articles
    {
        "name": "article_list",
        "title": "List articles",
        "description": "Published articles without their bodies (slug, date, title, subtitle, "
                       "description, section, topics, card_type). This is how you find slugs: "
                       "for a day's front page use day=YYYY-MM-DD, for one author pass author. "
                       "Never work from remembered slugs.",
        "inputSchema": _obj({
            "author": _s("Author handle whose archive to list."),
            "day": _s("One UTC day (YYYY-MM-DD) across every author."),
            "q": _s("Free-text filter on an entity or theme."),
            "since": _s("Only articles published at or after this day (YYYY-MM-DD)."),
            "until": _s("Only articles published up to this day, inclusive (YYYY-MM-DD)."),
            "limit": _i("Cap the list (0 uses the backend default).", minimum=0),
        }),
        "handler": h_article_list,
    },
    {
        "name": "article_get",
        "title": "Read one article",
        "description": "The full article as stored, body and metadata included. Read it before "
                       "you edit, so you change what is actually there.",
        "inputSchema": _obj({"slug": _s("The article slug.")}, required=["slug"]),
        "handler": h_article_get,
    },
    {
        "name": "article_create",
        "title": "Create an article",
        "description": "Write a new article and stage it to the LOCAL site (it is NOT public "
                       "until site_publish). You write the body yourself, in markdown, in the "
                       "author's voice: read editorial_style and editorial_rules for the "
                       "author's language, and author_get for the persona, BEFORE drafting. The "
                       "byline and section come from the author unless you override them. If you "
                       "rendered a hero with image_generate just before, it attaches by itself. "
                       "The newest article leads the front page: to add one without taking the "
                       "lead, backdate published_at.",
        "inputSchema": _obj({
            "author": _s("Author handle (from author_list); sets the byline and the section."),
            "title": _s("The headline. It must be accurate to the body, not a tease."),
            "description": _s("The one-line standfirst under the headline (required)."),
            "body": _s("The article body, in markdown."),
            "section": _s("Override the author's beat as the section."),
            "subtitle": _s("Optional dek shown under the headline."),
            "topics": _list("Tags: themes plus named entities."),
            "keywords": _list("SEO keywords."),
            "slug": _s("Override the derived URL slug."),
            "published_at": _s("RFC3339 timestamp; omit for now. Backdate to avoid taking the "
                               "front-page lead."),
            "image": _s("Hero image path returned by image_generate or media_upload."),
            "image_alt": _s("Alt text for the hero, in the author's language."),
            "image_caption": _s("Caption the site renders under the hero."),
            "image_credit": _s("Credit line under the hero."),
            "card_type": _s("What the front-page card shows; omit to derive it from the media.",
                            enum=["text", "image", "youtube", "video"]),
            "card_src": _s("The card's media reference (a media path, or a YouTube id)."),
            "card_alt": _s("Alt text for the card image."),
            "youtube": _s("YouTube id or URL for a video piece."),
            "dry_run": _b("Print the payload without staging anything."),
        }, required=["author", "title", "description", "body"]),
        "handler": h_article_create,
    },
    {
        "name": "article_update",
        "title": "Edit an article in place",
        "description": "Change a live article, keeping its permalink. Only the fields you pass "
                       "change; everything else is preserved. metadata is merged key by key, so "
                       "you can retitle the card or fix the standfirst without resending the "
                       "rest. Read the article first with article_get.",
        "inputSchema": _obj({
            "slug": _s("The article to edit."),
            "title": _s("New headline."),
            "body": _s("Replacement body, in markdown."),
            "section": _s("New section."),
            "author": _s("New author handle."),
            "topics": _list("Replacement tag list."),
            "published_at": _s("New RFC3339 timestamp; this MOVES the piece in front-page order."),
            "metadata": {"type": "object",
                         "description": "Metadata keys to merge: subtitle, description, "
                                        "image, image_alt, image_caption, image_credit, and the "
                                        "card object "
                                        '{"type": "text|image|youtube|video", "src": "...", '
                                        '"alt": "..."}.'},
            "dry_run": _b("Print the payload without writing."),
        }, required=["slug"]),
        "handler": h_article_update,
    },
    {
        "name": "article_delete",
        "title": "Take an article down",
        "description": "Remove an article from the site. The delete is soft (restorable through "
                       "the operator panel), but the piece leaves every listing. Requires "
                       "confirm=true.",
        "inputSchema": _obj({"slug": _s("The article to take down."),
                             "confirm": _b("The human approved the removal.")},
                            required=["slug", "confirm"]),
        "handler": h_article_delete,
    },
    {
        "name": "sections_list",
        "title": "The live section vocabulary",
        "description": "The section values actually in use, with counts, plus the author and "
                       "topic distributions. Section is a free string with no registry, so this "
                       "is the only authoritative list of what sections exist.",
        "inputSchema": _obj({"axis": _s("Narrow the report to one axis.",
                                        enum=["all", "authors", "topics"])}),
        "handler": h_sections_list,
    },
    # -- layout
    {
        "name": "portada_get",
        "title": "Read the front-page plans",
        "description": "The stored per-day front-page plans. There is no single-date read, so "
                       "this lists them and you pick the day you care about.",
        "inputSchema": _obj({"date": _s("Ignored by the backend listing; pass the day you are "
                                        "working on for your own clarity.")}),
        "handler": h_portada_get,
    },
    {
        "name": "portada_set",
        "title": "Arrange a day's front page",
        "description": "Set the ORDER and card sizes for one day. entries is the ordered list "
                       "and array order IS page order. entries[0] is that day's lead: it renders "
                       "full width by position, so leave its role empty. role 'important' makes a "
                       "card span the full row (a double card); role '' is a half-row single, and "
                       "singles pair two per row. Never leave a gap: the singles between two "
                       "full-row cards must come out even, so promote a lone trailing single to "
                       "'important'. Same-day articles you leave out still append after your "
                       "entries as singles. Pull slugs from article_list(day=...), never from "
                       "memory: a slug that does not exist that day is dropped silently and your "
                       "intended lead changes.",
        "inputSchema": _obj({
            "date": _s("The day to arrange (YYYY-MM-DD)."),
            "entries": _list("Ordered entries, one per card.", items={
                "type": "object",
                "properties": {"slug": _s("An article published that day."),
                               "role": _s("'' for a single (half row), 'important' for a double "
                                          "(full row).", enum=["", "important"])},
                "required": ["slug"], "additionalProperties": False}),
        }, required=["date", "entries"]),
        "handler": h_portada_set,
    },
    {
        "name": "recomendado_get",
        "title": "Read the recommended rail",
        "description": "The global front-page 'Recomendado' rail: one ordered list of up to 10 "
                       "slugs that persists across days until you change it.",
        "inputSchema": _obj({}),
        "handler": h_recomendado_get,
    },
    {
        "name": "recomendado_set",
        "title": "Set the recommended rail",
        "description": "Replace the global recommended rail, in order, up to 10 slugs from any "
                       "day. An empty array clears it (the widget stays, with no items). This is "
                       "not part of a day's portada: it is one list for the whole site.",
        "inputSchema": _obj({"slugs": _list("Article slugs, in the order they should appear.")},
                            required=["slugs"]),
        "handler": h_recomendado_set,
    },
    # -- authors
    {
        "name": "author_list",
        "title": "List authors",
        "description": "The author handles that exist in the registry. Every article is bylined "
                       "by one of these, so start here when you do not know who should sign a "
                       "piece, then read the one you pick with author_get.",
        "inputSchema": _obj({}),
        "handler": h_author_list,
    },
    {
        "name": "author_get",
        "title": "Read an author",
        "description": "One author's full record: public byline fields plus the private tail the "
                       "newsroom writes from (beat, who_i_am, language, few-shot exemplars) and "
                       "the attached sources. Read this before writing in that voice.",
        "inputSchema": _obj({"id": _s("Author handle.")}, required=["id"]),
        "handler": h_author_get,
    },
    {
        "name": "author_create",
        "title": "Create an author",
        "description": "Persist a new fictional persona. You write the persona yourself: read "
                       "prompt_get(key='persona/synthesize.md') first, which is the recipe for "
                       "what a good persona carries. Personas are openly fictional and must "
                       "never impersonate a real person. Attach the author's outlets afterwards "
                       "with author_sources_set.",
        "inputSchema": _obj({
            "display_name": _s("The byline name."),
            "beat": _s("The author's default section (their beat)."),
            "who_i_am": _s("First person, private: background, what they cover, what they refuse."),
            "style": _s("Concrete voice notes a drafting model can follow."),
            "about": _s("First-person PUBLIC bio for the byline and the about page."),
            "language": _s("Language code the author writes in, e.g. es, en, pt."),
            "gender": _s("Grammatical gender for the byline, where the language needs it."),
            "handle": _s("Explicit id; derived from the display name when omitted."),
            "avatar_path": _s("The author portrait: a /media path from image_generate (see its portrait recipe) or media_upload."),
            "profile_topics": _list("Curated topics for the public profile page."),
            "few_shots_pos": _list("Positive exemplars: {prompt, good} objects.",
                                   items={"type": "object"}),
            "few_shots_neg": _list("Negative exemplars: {prompt, bad} objects.",
                                   items={"type": "object"}),
            "sources": _list("Free-text outlets the persona leans on (suggestions only; the "
                             "real wiring is author_sources_set)."),
        }, required=["display_name", "beat", "who_i_am", "style"]),
        "handler": h_author_create,
    },
    {
        "name": "author_update",
        "title": "Edit an author",
        "description": "Change an existing author in place: the public fields (name, bio/about, "
                       "portrait, gender, style), the private tail the newsroom writes from "
                       "(beat, who_i_am, language, few-shot exemplars), and the profile topics. "
                       "Only what you pass changes. To change the portrait, upload or render the "
                       "picture first, then pass its path as avatar.",
        "inputSchema": _obj({
            "id": _s("Author handle to edit."),
            "name": _s("New byline name."),
            "about": _s("New public bio (also becomes the site's bio field)."),
            "bio": _s("Public bio, when you want it to differ from about."),
            "avatar": _s("The author portrait: a /media path from image_generate (see its portrait recipe) or media_upload. This is how you change an author picture."),
            "gender": _s("Grammatical gender for the byline."),
            "style": _s("New voice notes."),
            "beat": _s("New default section."),
            "who_i_am": _s("New private self-description."),
            "language": _s("New language code."),
            "few_shots_pos": _list("Replacement positive exemplars.", items={"type": "object"}),
            "few_shots_neg": _list("Replacement negative exemplars.", items={"type": "object"}),
            "profile_topics": _list("Replacement profile topics; an empty array clears them."),
            "dry_run": _b("Print the resulting author row without writing."),
        }, required=["id"]),
        "handler": h_author_update,
    },
    {
        "name": "author_delete",
        "title": "Retire an author",
        "description": "Tombstone an author. Soft and restorable, but their byline leaves the "
                       "site. Their published articles stay. Requires confirm=true.",
        "inputSchema": _obj({"id": _s("Author handle."),
                             "confirm": _b("The human approved retiring this author.")},
                            required=["id", "confirm"]),
        "handler": h_author_delete,
    },
    {
        "name": "author_sources_get",
        "title": "Read an author's outlets",
        "description": "The source outlets attached to one author: what that author reads and\n"
                       "cites. Sourcing rules are per author, so check this before you send\n"
                       "someone to cover a beat they have no outlets for.",
        "inputSchema": _obj({"id": _s("Author handle.")}, required=["id"]),
        "handler": h_author_sources_get,
    },
    {
        "name": "author_sources_set",
        "title": "Set an author's outlets",
        "description": "REPLACE the author's whole set of outlets. Slugs must come from "
                       "source_catalog; anything else is rejected.",
        "inputSchema": _obj({"id": _s("Author handle."),
                             "sources": _list("Source slugs from source_catalog.")},
                            required=["id", "sources"]),
        "handler": h_author_sources_set,
    },
    {
        "name": "source_catalog",
        "title": "Available source outlets",
        "description": "Every source slug that can be attached to an author. These ids are the only\n"
                       "values author_sources_set accepts, so read this before wiring an author\n"
                       "to new outlets.",
        "inputSchema": _obj({}),
        "handler": h_source_catalog,
    },
    # -- topics
    {
        "name": "topics_inventory",
        "title": "Inventory the tags",
        "description": "Every distinct article tag with its count and the slugs carrying it. Use "
                       "it to spot naming variants of one entity before you normalize them.",
        "inputSchema": _obj({"limit": _i("Cap the articles scanned (0 scans 1000).", minimum=0)}),
        "handler": h_topics_inventory,
    },
    {
        "name": "topic_remove",
        "title": "Drop a topic from the index",
        "description": "Tombstone a stale topic in the registry index. It does not touch article "
                       "tags or author chips. Soft and restorable. Requires confirm=true.",
        "inputSchema": _obj({"slug": _s("Registry topic slug."),
                             "confirm": _b("The human approved the removal.")},
                            required=["slug", "confirm"]),
        "handler": h_topic_remove,
    },
    # -- media
    {
        "name": "media_upload",
        "title": "Upload an image or video",
        "description": "Put a file in the media store and get back the path to use as a hero, a "
                       "card image, or an author portrait. Pass base64_data with a filename when "
                       "you carry the bytes yourself, or path when the file is already on the "
                       "host.",
        "inputSchema": _obj({
            "base64_data": _s("The file's bytes, base64 encoded."),
            "filename": _s("Plain filename with its extension, e.g. portrait.png. The extension "
                           "sets the content type."),
            "path": _s("Absolute path to a file already on the host."),
        }),
        "handler": h_media_upload,
    },
    {
        "name": "image_generate",
        "title": "Render a hero image",
        "description": "Art-direct and render an image through the local image lane, then upload "
                       "it and return its media path. Write the prompt as a brief in this order: "
                       "subject, then arrangement, then style and medium, then light and mood. "
                       "There are no negative prompts, so describe what you want rather than what "
                       "you do not. It must never depict a real identifiable person.\n"
                       "TWO JOBS, TWO SHAPES:\n"
                       "1. An article HERO is wide (leave width and height at their defaults) and "
                       "is stylized art, not a staged news photo. The render is remembered, so "
                       "the next article_create attaches it as that piece's hero automatically.\n"
                       "2. An AUTHOR PORTRAIT follows one house recipe every author on this site "
                       "uses: head and shoulders on a pure black background, the FACE NEVER "
                       "READABLE (lost in shadow, with a thin neon rim light tracing only the "
                       "edge of the head and shoulders, or a hard backlight glowing through the "
                       "hair), dark clothing, single light source, low key, high contrast, "
                       "cinematic studio photography. Render it PORTRAIT shaped: width 768, "
                       "height 1024. Vary the light color, hair, clothing and age per author so "
                       "the roster is not cloned. A portrait is NOT auto-attached: pass the media "
                       "path it returns to author_update(avatar=...) or author_create"
                       "(avatar_path=...).\n"
                       "Needs the image lane up (stack_up with gpu=true).",
        "inputSchema": _obj({
            "prompt": _s("The art-directed image brief."),
            "alt": _s("Short alt text, in the author's language."),
            "seed": _i("Override the stable per-prompt seed to get a different take."),
            "width": _i("Pixel width; heroes read wide (default 1344).", minimum=64),
            "height": _i("Pixel height (default 768).", minimum=64),
            "steps": _i("Sampler steps (default 4).", minimum=1),
            "dry_run": _b("Return the render graph without rendering."),
        }, required=["prompt"]),
        "handler": h_image_generate,
    },
    # -- the editorial recipe
    {
        "name": "editorial_style",
        "title": "The editorial style guide",
        "description": "The newsroom's voice and house rules. Read this before you write "
                       "anything that will carry a byline.",
        "inputSchema": _obj({}),
        "handler": h_editorial_style,
    },
    {
        "name": "editorial_rules",
        "title": "Language-specific editorial anchors",
        "description": "One language's banned lexicon and swaps, orthography, slop phrases, and "
                       "the attribution and disclaimer wording. Pass the language of the author "
                       "you are writing as.",
        "inputSchema": _obj({"lang": _s("Language code, e.g. es, en. Defaults to es.")}),
        "handler": h_editorial_rules,
    },
    {
        "name": "prompt_get",
        "title": "Read a newsroom prompt",
        "description": "Read one prompt file of the newsroom recipe, e.g. "
                       "persona/synthesize.md (how to write a persona) or workflow/50-draft.md "
                       "(one node of the writing walk).",
        "inputSchema": _obj({"key": _s("Prompt key, e.g. persona/synthesize.md.")},
                            required=["key"]),
        "handler": h_prompt_get,
    },
    {
        "name": "prompt_set",
        "title": "Rewrite a newsroom prompt",
        "description": "Replace a prompt file's body. This changes how the newsroom writes from "
                       "then on, so read the current text first and keep the node's contract "
                       "(what it must produce) intact.",
        "inputSchema": _obj({"key": _s("Prompt key to rewrite."),
                             "body": _s("The new full body.")},
                            required=["key", "body"]),
        "handler": h_prompt_set,
    },
    {
        "name": "workflow_step",
        "title": "Walk the editorial workflow",
        "description": "Serve ONE node of the gated editorial walk at a time and print the exact "
                       "next step. This is the quality path for writing: it enforces the "
                       "sourcing floor, the accurate-headline gate, and the evaluate and respin "
                       "loop. Start with mode alone to get the first node, then pass the node it "
                       "names. When a node asks you to save an artifact, save it with "
                       "workflow_save under the exact filename it names, or the gate will not "
                       "let you advance.",
        "inputSchema": _obj({
            "mode": _s("Workflow mode, e.g. single-article, institucional, daily, redactor, "
                       "portal-review, topic-cleanse. Call with list=true to see them all."),
            "node": _s("Node key to serve, e.g. 30-research. Omit to get the mode's first node."),
            "list": _b("Print the node sequence instead of a node body."),
        }),
        "handler": h_workflow_step,
    },
    {
        "name": "workflow_save",
        "title": "Save a walk artifact",
        "description": "Save the file a workflow node asked for (ledger.md, draft.md, and the "
                       "like) into the walk's scratch dir. Use the exact filename the node named. "
                       "This is the only file this server writes, and it never touches anything "
                       "outside that dir.",
        "inputSchema": _obj({"artifact": _s("Plain filename the node named, e.g. draft.md."),
                             "content": _s("The file's full contents.")},
                            required=["artifact", "content"]),
        "handler": h_workflow_save,
    },
]

BY_NAME = {t["name"]: t for t in TOOLS}


def public_tools() -> list:
    """The tool list as the protocol serves it (handlers and text hooks stripped)."""
    out = []
    for tool in TOOLS:
        entry = {"name": tool["name"], "title": tool["title"],
                 "description": tool["description"], "inputSchema": tool["inputSchema"]}
        if tool.get("outputSchema"):
            entry["outputSchema"] = tool["outputSchema"]
        else:
            entry["outputSchema"] = RESULT_SCHEMA
        out.append(entry)
    return out


def validate_args(tool: dict, args: dict) -> str:
    """Fail closed on the way in. MCP clients validate against inputSchema, but a tool that
    trusts that would hand unchecked argv to a subprocess: check required keys, reject unknown
    ones, and check the few types that change how the argv is built."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    schema = tool["inputSchema"]
    props = schema.get("properties", {})
    unknown = sorted(set(args) - set(props))
    if unknown:
        return (f"unknown argument(s): {', '.join(unknown)}. This tool takes: "
                f"{', '.join(sorted(props)) or 'no arguments'}")
    for key in schema.get("required", []):
        if args.get(key) in (None, ""):
            return f"missing required argument: {key}"
    for key, value in args.items():
        want = props[key].get("type")
        if value is None or want is None:
            continue
        if want == "array" and not isinstance(value, list):
            return f"{key} must be an array"
        if want == "object" and not isinstance(value, dict):
            return f"{key} must be an object"
        if want == "boolean" and not isinstance(value, bool):
            return f"{key} must be true or false"
        if want == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return f"{key} must be an integer"
        if want == "string" and not isinstance(value, str):
            return f"{key} must be a string"
        enum = props[key].get("enum")
        if enum and value not in enum:
            return f"{key} must be one of: {', '.join(str(e) for e in enum)}"
    return ""
