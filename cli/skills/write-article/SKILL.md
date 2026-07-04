---
name: write-article
description: >-
  Write and publish ONE Censurado news article in a persona's voice by walking the gated
  editorial step-gate one node at a time. Use when the user asks to write, create, or cover
  a single news story, or when a batch hands you one queued assignment.
---

# Write one article (the gated walk)

Do NOT write the whole article in one pass. That single habit is the thing to avoid: it
buries the later rules and skips the gates. Instead walk the editorial workflow ONE node at a
time. Each node tells you exactly what that step needs and ends with the exact NEXT command.

## Walk it
1. Start the walk:

       python3 cli/censurado.py step --mode single-article

2. Do EXACTLY what the one node says, and nothing from a node you have not fetched yet.
3. Run the `NEXT:` command it prints. Repeat, node by node, through to publish.

Never fetch nodes ahead, never batch several nodes into one action, and never stage the piece
to the site until the final node tells you to. Mirror the nodes in your own todo list (one in
progress, mark it done before the next) so you hold your place across the long walk.

The workflow nodes are on-disk files in this repo's `prompts/` (the single source of truth for
the prompts), so `step` needs no server, just this repo. Your DATA lives in
the backend: the nodes will tell you to load an author with `censurado.py personas` /
`persona <id>` (which read the backend), and publishing needs the backend up.

## Each node is WORK YOU DO, not a button you press
Fetching a node does NOT complete it. The node is instructions for YOU to carry out with your
own reasoning and tools (web search, writing) before you move on. Some nodes must SAVE a file
to your scratch dir `$CENSURADO_WORK` and the gate will not let you advance until that file
exists (it prints the exact path as `ARTIFACT: ...`):

- `30-research` -> write your source ledger to `$CENSURADO_WORK/ledger.md` (each key fact + its
  source URL). Use web search to find real sources first.
- `50-draft` -> write the full article body to `$CENSURADO_WORK/draft.md`.

If you try to skip ahead you will get a `BLOCKED:` message naming the step to go back and do.
If you re-fetch the same node over and over you will get a `STOP:` message: do the work
instead of re-fetching. At the preview node, pass the body you saved: `--body-file
$CENSURADO_WORK/draft.md`.

## The verbs the walk uses
The nodes hand you the exact command at each step; these are the ones you will run:

- Author: `python3 cli/censurado.py personas` , `... persona <id>` (load voice + beat).
- Tweet / Truth card (OPTIONAL): `python3 cli/censurado.py tweet <url>` , `... truth <url>`. A live
  X post needs NONE of this: just write `{{tweet:<id>}}` in the body and `preview` fetches the
  card. Use these verbs only to pin a snapshot the auto-fetch cannot reach (a deleted X post, or
  any Truth Social post).
- Hero image (node 95): `python3 cli/censurado.py image --prompt "..." --alt "..."`.
- Preview to the site (node 99): `python3 cli/censurado.py preview --author <id> --title "..." --body-file draft.md ...`.

Run any verb with `--help` for its flags. `preview`/`publish` build the strict JSON for you
from these flags, so you never hand-build the request. The field contract and the widget
markers you place in the body are below.

## The publish contract (what `preview`/`publish` sends)
`title`, `body` (markdown), `author` (the persona slug), and `section` are REQUIRED; also
write a `--subtitle` (the dek) and a one-line `--description` (the standfirst), preview needs
both. The hero image, byline, dek, SEO terms, and widget snapshots all live inside `metadata`
(never as top-level keys): `subtitle`, `description`, `author_name` (the visible byline),
`author_bio`, `author_avatar`, `image` + `image_alt` (the hero still), `youtube` (a lead
video), `keywords`, and the `tweets` bag. `preview` places these for you from its flags.

Response: `201` wrote a new article, `200` a dedup replay (nothing written), both returning
`{"id","slug"}`. Dedup identity is the trimmed `title + body + author + section` only, so
re-previewing the same four fields writes nothing; to publish a genuinely new piece the title
or body must differ. The portada orders by `published_at` (newest first): omit it to take the
top headline slot, or set an OLDER `--published-at` (RFC3339 `Z`) to slot the piece in lower
without moving the current lead.

## Rich widgets (inline body markers)
Drop a marker on its OWN line in the body and the static generator expands it:

| marker | renders | needs |
|--------|---------|-------|
| `{{video:<id-or-url>}}` | a responsive YouTube or self-hosted `.mp4` embed | nothing |
| `{{relacionado:<slug>}}` | a "related article" card | the backend slug of an OLDER existing article |
| `{{tweet:<id>}}` | an X / Truth Social card that survives the post being deleted | nothing for a live X post (`preview` auto-fetches the card from the id); an explicit `truth` capture only for a Truth Social post |

Three distinct media slots, do not confuse them: the portada CARD thumbnail is `metadata.image`
(else the first body `{{video:}}` poster, else text-only); the article HERO is `metadata.image`
(a still) or `metadata.youtube` (a lead video); the body markers render inline where you place
them. A `{{tweet:<id>}}` for a live X post needs NOTHING extra: write the marker exactly like
`{{relacionado:}}` or `{{video:}}` and `preview` auto-fetches the card from the id (keyless, via
fxtwitter). Capture explicitly ONLY to pin a snapshot the auto-fetch cannot reach: a since-deleted
X post, or a Truth Social post (auto-fetch is X-only). Then run `censurado.py tweet <url>` (X) or
`truth <url>` (Truth Social) and pass `--tweets-file` so the `{{tweet:<id>}}` id matches a captured
snapshot. Embed a post whenever your prose names one. Crafting counts (guidance, not enforced):
`{{relacionado:}}` at most two, `{{video:}}` at most three, `{{tweet:}}` no cap but every card
must earn its place; `topics` up to `TOPIC_CAP` (the tag cap in `cli/workflow/parameters.json`,
which the `step` walk fills into the finalize node), always naming the proper-noun entities the
piece is about, not only abstract themes.

## Reading X and Truth Social posts (keyless)
When the story names a public post, or an author's outlet is an X account, read it with NO API
key. Find the post with web search (a news page that embeds it exposes the `x.com/.../status/<id>`
URL; searching a handle plus the topic surfaces what that account posted). For an X post that is
all you need: drop `{{tweet:<id>}}` in the body and `preview` fetches the card. Capture explicitly
only to pin a since-deleted X post, or any Truth Social post, with
`censurado.py tweet <status-url-or-id>` (X, via fxtwitter) or `truth <status-id-or-url>` (Truth
Social). The keyless path is the default, so a normal run needs no token.

## Show the user, then preview (never publish to production)
`preview` only stages the piece to the LOCAL site (`localhost:8080`), not the public
internet. At the final node, show the user the full draft (title, subtitle, description,
body, section, topics, and any widgets), then `preview` it so they can see it live-rendered.
The command prints a `PREVIEW: <url>  [live now]` line on success: that URL is your result,
report it back (do not construct a link yourself). Going public is a SEPARATE `make deploy`,
never deploy without an explicit yes.
