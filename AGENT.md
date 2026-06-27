# AGENT.md: operating the newsroom brain

This is the operator blueprint for any CLI agent (Claude Code, Codex, Grok, a script,
a cron job) that drives this newsroom to produce or manage articles. It is the how-to:
what the brain is, the quality bar every article must clear, the tools you have, and the
exact way to run a batch, write a single article, or read and edit the authors and their
sources.

It is deliberately author-agnostic. It teaches how the system works (the format, the
passes, how sources corroborate) without naming any specific persona, so it stays true
whatever roster a deployment carries.

If you are editing the brain's CODE, read `AGENTS.md` instead (the codebase map). This
file is about OPERATING the running brain. The two are siblings: `AGENTS.md` is the map
of the machine, `AGENT.md` is the manual for driving it.

---

## 0. Resolver: find what you need

| You want to... | Go to | One line |
|---|---|---|
| Understand what the brain is | [1](#1-what-the-brain-is) | An agentic newsroom behind one HTTP API. |
| Internalize the quality bar (do this first) | [2](#2-the-quality-bar-non-negotiable) | The format, attention, formality, the passes. |
| Know how an article is actually built | [3](#3-how-an-article-is-built-the-pipeline) | research, corroborate, outline, draft, evaluate, enrich, fact-check, respin x2, finalize. |
| Run a full or scoped batch | [4.A](#4a-lane-a--drive-the-brain) | `POST /runs` with a mode. |
| Have the brain write one article from a brief | [4.A](#4a-lane-a--drive-the-brain) | `POST /articles/from-link`. |
| Author and publish one article yourself | [4.B](#4b-lane-b--author-and-publish-directly) | Write to the bar, `POST /articles`. |
| Read, discuss, or edit an author | [5](#5-authors-and-sources) | `GET/PATCH /personas/{id}`. |
| Manage sources and the corroboration pool | [5](#5-authors-and-sources) | `/portals`, `/personas/{id}/sources`. |
| Shape house style, lexicon, sourcing floor, prompts | [6](#6-editorial-controls) | `/editorial/*`, `/prompts/*`. |
| Know the hard invariants | [7](#7-ground-rules) | No auth on the brain; append-only platform; no length caps. |
| Grab an endpoint or a curl | [8](#8-quick-reference) | Tables and examples. |

Everything is reachable over one HTTP API with no authentication on the brain itself
(see [7](#7-ground-rules)). In the bundled harness the brain sits behind the operator
console's nginx proxy at `http://127.0.0.1:8083/api/<path>` (the container listens on
`:8000`); standalone it binds `127.0.0.1:8722`. Paths below are the brain's native paths
(prefix with `/api` when you go through the console proxy).

---

## 1. What the brain is

The brain is an agentic newsroom. Journalist personas (each owns one beat) research the
day's news, write full articles in their own voice, an art director gives each a hero
image, and the brain publishes them to the public portal over one HTTP contract. It is a
single FastAPI process: a deterministic outer workflow (code owns the edges and bounds
every loop) hosting bounded model loops (the writing happens inside the nodes).

Two boundaries matter to you:
- The **brain API** (this document's main surface): triggers runs, owns personas,
  sources, prompts, house style.
- The **platform / publish API** (a separate service): the only place articles are
  stored and served. The brain publishes to it; you can also publish to it directly
  ([4.B](#4b-lane-b--author-and-publish-directly)).

You will work in one of two lanes:
- **Lane A, drive the brain:** ask the brain to research and write, autonomously
  (`POST /runs`) or from a brief you hand it (`POST /articles/from-link`). The quality
  bar is built into the pipeline.
- **Lane B, author yourself:** you write the article and POST it to the platform. Here
  YOU are the journalist, and you must clear the quality bar by hand.

Either way the bar in section 2 is the law.

---

## 2. The quality bar (non-negotiable)

Our single strength is **attention**. We respect the reader's attention and we earn it.
Every article concentrates a lot of information into a small space and delivers it in
layers, so a reader gets value at any depth and can stop wherever they like. Before you
write a line, answer one question: **why would someone read this?** If you cannot say
what is genuinely interesting or consequential here, the piece is not ready. It must be
sold as deeply interesting, and then it must pay that off.

### 2.1 The format: title, subtitle, summary, more, full context

Every article is built in attention-staged layers. Each layer is DIFFERENT from the
others and carries new, dense information. Never restate an earlier layer in more words.

1. **Title.** Short and concrete (aim for five words or fewer). Drawn from the piece, not
   a fresh angle. It makes a reader want to open it.
2. **Subtitle (the dek).** One line that sharpens the title and answers "why read this."
   It does not repeat the title's words.
3. **Summary.** A few self-contained sentences that deliver the whole story densely. A
   reader who stops here still knows what happened and why it matters. Highest
   information per word.
4. **More about this.** The developing detail: the facts, numbers, names, and the one or
   two competing claims the summary could only gesture at.
5. **Full context.** The background, history, and second-order consequences that fill the
   gaps for the reader who wants the whole thing. This is where you close.

This is felt in the writing, NOT announced. Do not print the words "Summary" or
"Context" as labels, and do not make it read like a filled-in template. The reader should
move from headline to dek to a dense opening to deeper detail to full background without
ever being told which layer they are in.

How the layers map to the published fields (see [3](#3-how-an-article-is-built-the-pipeline)
and [4.B](#4b-lane-b--author-and-publish-directly)):
- title -> `title`
- subtitle -> `metadata.subtitle`
- summary -> `metadata.description`
- more about this + full context -> the `body` (Markdown), staged in that order.

### 2.2 Formality and craft

Write like a senior professional, never like an assistant. Be exact with tense, subjects,
names, titles, dates, and figures. Attribute every claim. Decide what is important, state
the reason the article exists, and cut everything that is not signal.

### 2.3 Avoid the AI-slop tells

No "in today's fast-paced world", no "it's important to note", no hollow both-sides
hedging, no padding, no three adjectives standing in for a fact, no throwaway "como si"
metaphors, no closing paragraph that just restates the opening. If a sentence sounds good
read aloud but says nothing, cut it.

### 2.4 Cross-validate the facts (at least five sources)

Ground the central facts across **at least five different source articles**, the same
story or closely related ones. They may come from the same outlet, but prefer different
platforms: a fact that holds across several independent outlets is far stronger than one
carried by a single source. Never invent a source, a statistic, or a quote, and never
cite a URL you did not actually use.

### 2.5 Group fast-moving stories

Some feeds publish many items per hour about one event (a single conflict, ruling, or
release, reported and re-reported, then updated). Do not write one article per wire item.
Cluster the items that are the same story or developments of it, gather the latest across
the cluster, reconcile what changed, and write ONE current piece.

### 2.6 The two-pass self-respin

After the article is written and grounded, **re-spin it twice**. Each pass you
interrogate your own draft and rewrite for form only, keeping every grounded fact and
citation:

- Is this redundant? What repeats across the sections?
- What can be improved? What is noise and what is signal? Cut the noise.
- Is the wording correct? Are we repeating the same words? Tighten them.
- Does it carry AI-slop cliche (the tells in 2.3)? Remove every one.
- Does it look professional and human? If not, make it so.
- Does it honor the layered format in 2.1, each part dense and distinct?

Two passes, every article. In Lane A the pipeline does this for you (`respin x2`). In
Lane B you do it yourself before you publish.

---

## 3. How an article is built (the pipeline)

When the brain writes (Lane A), one assignment runs through this fixed sequence. Each
stage spends from a shared per-article budget; if the budget runs out the assignment is
DROPPED, never truncated. Understand it whether you drive it or imitate it by hand.

```
corroboration gate -> outline -> [draft -> evaluate] xN -> enrich -> fact-check
                    -> respin x2 -> finalize -> (author stamp) -> (hero image)
```

1. **Corroboration gate** (before any draft token): a pure-code check that the research
   ledger rests on enough INDEPENDENT sources (co-owned outlets count once). Under-
   corroborated assignments are dropped up front. Off for the direct-from-brief path
   ([4.A](#4a-lane-a--drive-the-brain)), where you vouched for the source.
2. **Outline:** plan the staged layers (2.1) and the "why read this" hook.
3. **Draft / evaluate sweeps:** the journalist drafts in voice; a senior-editor pass
   returns PASS or REVISE with the failing sections. Bounded by a max sweep count, an
   editor PASS, and a no-progress stall.
4. **Enrich:** one persona-blind copy pass for clarity and structure.
5. **Fact-check:** every citation must trace to an approved source; unresolved markers
   and altered names or dates are corrected or removed.
6. **Respin x2:** the author re-spins its own article in its own voice against the
   anti-slop and format rubric (2.6), preserving facts and citations.
7. **Finalize:** structure the finished piece into the publish payload. The model authors
   `title`, `subtitle`, `summary`, `body`, `topics`, and an optional `slug`; `author` and
   `section` are set authoritatively (never model-chosen). The dek lands in
   `metadata.subtitle` and the summary in `metadata.description`.
8. **Author stamp + hero image:** the byline (`author_name`, `author_bio`,
   `author_avatar`) is stamped into metadata; the art director best-effort renders a hero
   image into `metadata.image`. Both live outside the content hash, so they never change
   the article's identity.

Prompts for every stage are versioned `.md` templates under `prompts/` (journalist/*,
manager/triage, art_director/illustrate), editable live ([6](#6-editorial-controls)).

---

## 4. Writing articles

### 4.A Lane A: drive the brain

The brain researches and writes; you pick the trigger. Both triggers are asynchronous:
they return `202` with a `run_id` and a `Location`, then you poll `GET /runs/{run_id}`
until the run reaches `done`, `done_with_errors`, or `failed`.

**Batch run, `POST /runs`.** One `mode` is the entire trigger; it resolves once into a
scope and all modes then run the identical path.

| Mode | What it does |
|---|---|
| `managed` | Full automated run. The manager triages live news against recent coverage and assigns up to N stories across ALL authors. The corroboration floor from house style is armed. |
| `express` | A quick, smaller capped batch (a lower default N), otherwise identical to managed. |
| `manual` | Operator-driven. Honors an explicit `n` and a `persona_ids` subset; unknown ids are skipped. Use this to run one or a few named authors. |

```json
POST /runs
{"mode": "managed", "n": 4, "persona_ids": ["<persona-id>"], "images": true}
```
`mode` is required; `n`, `persona_ids`, and `images` are optional (omit `images` to use
the deployment default). N is clamped to the configured maximum.

**One article from a brief, `POST /articles/from-link`.** One named author writes one
article from a brief and/or links you supply, BYPASSING the manager. The links you give
are fetched and seeded into the ledger as primary sources, then the author researches
outward from the brief. Because you vouched for the source, the corroboration gate is
forced off; every other gate (evaluate, fact-check, finalize) still runs, and a piece
that grounds in nothing is still dropped.

```json
POST /articles/from-link
{"persona_id": "<persona-id>", "brief": "what to cover", "links": ["https://..."],
 "focus": "the specific angle", "images": false}
```
`persona_id` is required; supply a `brief` and/or at least one link.

Use the CLI instead of HTTP if you prefer: `censurado-brain --mode managed --n 4`
(bare invocation is the run-once automation entry), or `censurado-brain direct --persona
<id> --brief "..." --link https://...`. Exit codes: `0` done, `2` done with errors, `1`
failed.

### 4.B Lane B: author and publish directly

When you write the article yourself (the quota-free path: no brain run, no model budget),
you become the journalist and you must clear the entire bar in [2](#2-the-quality-bar-non-negotiable)
by hand: the staged format, the formality, no slop, five-source cross-validation, and the
two self-respin passes BEFORE you publish. Then POST it to the platform.

This is append-only. There is no edit and no delete; to change a published piece you
publish a new slug. Every write needs the operator token
(`Authorization: Bearer <NEWSROOM_OPERATOR_TOKEN>`), which must carry both the
`articles:write` and `articles:publish-any` scopes. In the bundled harness the write API
is a separate service (see the harness `cli/AGENTS.md` for the exact local port and the
token); the platform endpoints and the article schema are below.

**The article payload** (`POST /articles`, strict: unknown top-level keys are rejected):

| Field | Required | Notes |
|---|---|---|
| `title` | yes | 1 to 300 chars. The short headline (2.1). |
| `body` | yes | Markdown, no maximum length. Carries "more about this" then "full context". |
| `author` | yes | The persona slug (the byline). |
| `section` | yes | The beat. |
| `topics` | no | List of short tags, unique. |
| `slug` | no | `^[a-z0-9]+(?:-[a-z0-9]+)*$`; auto-derived from the title if omitted. |
| `published_at` | no | RFC3339. |
| `metadata` | no | Open bag; recognized keys below. |

Recognized `metadata` keys (everything else is stored and ignored): `subtitle` (the dek,
2.1), `description` (the summary, 2.1), `author_name`, `author_bio`, `author_avatar`
(a `/media/...` URL), `image` (a `/media/...` or absolute URL), `image_alt`, `youtube`.
Put the dek in `metadata.subtitle` and the summary in `metadata.description` so the
portal renders them as the card subtitle and the article standfirst.

`section` is validated against the harness's closed set (`tech`, `world`, `politics`,
`economics`) before the brain publishes; the platform itself treats `section` as a free
string, so a direct publish may use another section where a deployment expects one (for
example a literary section).

**Headers and idempotency.** `POST /articles` requires an `Idempotency-Key` header unique
per distinct article. The platform's identity is a content hash over the trimmed
`title + body + author + section` ONLY (slug, topics, metadata do not affect it):
re-posting the same four fields returns `200` and writes nothing, even under a new key.
So a retry is safe, and a genuine correction means new content (a new slug), not a second
key for the same bytes.

**Batch, `POST /articles:batch`.** Atomic all-or-nothing. No `Idempotency-Key` header;
each item carries an `idempotency_key` FIELD in its body. Any invalid item fails the whole
batch with a per-item error list and nothing is written.

**Media, `POST /media`.** Raw image bytes as the body (not multipart, not base64), with
`Content-Type` (for example `image/png`) and the bearer token. Content-addressed, no
idempotency key. Returns `{"url": "/media/<sha256>.png", ...}`; put that url in
`metadata.image`.

---

## 5. Authors and sources

You can read, discuss, and edit the roster. A persona is the byline: its `id` becomes the
article `author`, and its voice fields steer the writing.

**Read an author.** `GET /personas` lists them (filter by beat with `?beat=`, includes
inactive); `GET /personas/{id}` returns the full record: `display_name`, `beat`,
`who_i_am` (first-person identity, what they cover and refuse), `about` (the public
byline bio, published as `author_bio`), `style` (concrete voice notes the drafter
follows), `few_shots_pos` / `few_shots_neg` (positive and negative voice exemplars),
`sources` (the linked source pool), `avatar_path`, `active`. Read these before you discuss
or change an author so your edits respect the existing voice.

**Edit an author.** `PATCH /personas/{id}` with only the fields you want to change (every
field is optional; an empty body is a no-op). Use it to refine `about`, `who_i_am`,
`style`, or the few-shot exemplars when you and the operator agree on a sharper voice.
Create from explicit fields with `POST /personas/direct` (no model call), or synthesize a
new one from a seed brief with `POST /personas` (async, then poll
`GET /personas/jobs/{id}`).

**Sources and the corroboration pool.** An author only researches the sources linked to
it, and cross-source corroboration across those is what drives relevance. Manage the
registry with `/portals` (`GET` list, `POST` create, `PATCH` update,
`POST /portals/{id}/enable|disable`); each portal carries a `domain`, optional `feed_urls`,
a `language`, and an `ownership_group` (outlets sharing a non-empty group count as ONE
independent source in the corroboration gate, which is why co-owned mastheads should
share a group). Link sources to an author with `PUT /personas/{id}/sources` (replaces the
whole pool) or `POST /personas/{id}/sources/{portal_id}` (idempotent add of one). A few
trusted, independent outlets per author beats a long noisy list.

Everything here is also available on the CLI (`censurado-brain authors ...`,
`censurado-brain sources ...`) over the same database.

---

## 6. Editorial controls

These shape every article the brain writes, so know they exist before you debug a voice.

- **House style** (`/editorial/style`): the publication's voice, exemplars, and rules,
  stored as immutable versions. `POST` publishes a new version (carrying the lexicon and
  sourcing facets forward); `GET /editorial/style/versions` and the promote endpoint let
  you roll back.
- **Lexicon** (`/editorial/style/lexicon`): banned terms and preferred swaps. A banned
  term forces a revise inside the pipeline.
- **Sourcing** (`/editorial/style/sourcing`): the `min_sources` floor, the corroboration
  gate's threshold (blank turns the gate off, which is different from zero). This is the
  enforced side of "cross-validate" (2.4).
- **Location** (`/editorial/location`): the publication's place and language, used to
  scope news search.
- **Prompts** (`/prompts`): every pipeline stage is a versioned prompt template. A key is
  a path like `journalist/draft.md` and rides as a `?key=` query param (reads) or in the
  POST body, never in the URL path. `POST /prompts/template` publishes a new version that
  takes effect on the next run; promote rolls back. Editing these is how you tune the
  format, the attention discipline, and the respin rubric without touching code.

---

## 7. Ground rules

- **The brain API has no authentication.** Every brain route is open; treat the brain as
  a trusted-network service. Only the platform / publish API requires the bearer operator
  token. Errors come back as `application/problem+json` with a `code` and `detail`.
- **The platform is append-only and idempotent.** Identity is the content hash over
  trimmed `title + body + author + section`. Re-posting identical content is a safe no-op;
  a correction is a new slug, never an in-place edit.
- **Never cap output length.** No token, word, sentence, or character limit on any
  article or any model call, ever. Length is shaped by what to include or leave out, never
  by a ceiling. Only loop counts and the per-article token budget are bounded, and a
  single generation always finishes.
- **`author` and `section` are authoritative.** When the brain finalizes, the byline is
  the persona id and the section is the assignment's, never model-chosen. Respect the same
  when you publish directly.
- **Respect the voice.** Read an author's `who_i_am`, `about`, `style`, and few-shots
  before writing or editing in their name. The point of personas is that pieces do NOT
  collapse into one generic voice.

---

## 8. Quick reference

Brain endpoints (native paths; add `/api` through the console proxy):

```
GET    /health
POST   /personas                         seed -> 202 + poll (synthesize)
GET    /personas/jobs/{job_id}           poll synthesis
GET    /personas[?beat=&limit=&offset=]  list (incl. inactive)
GET    /personas/{id}                    full record
POST   /personas/direct                  create from explicit fields
PATCH  /personas/{id}                    partial edit
DELETE /personas/{id}
GET    /personas/{id}/sources            linked pool
PUT    /personas/{id}/sources            replace pool  {"sources":[...]}
POST   /personas/{id}/sources/{portal}   link one (idempotent)
DELETE /personas/{id}/sources/{portal}   unlink one
GET    /portals[?enabled=&limit=&offset=]
POST   /portals                          {"domain": "...", ...}
PATCH  /portals/{id}
POST   /portals/{id}/enable | /disable
POST   /runs                             {"mode": "...", "n"?, "persona_ids"?, "images"?}
POST   /articles/from-link               {"persona_id": "...", "brief"?, "links"?, "focus"?}
GET    /runs[?status=]                    list runs
GET    /runs/{run_id}                     one run + assignment statuses
GET/POST /editorial/style                 + /versions, /versions/{v}/promote
GET/PUT  /editorial/style/lexicon | /sourcing
GET/PUT  /editorial/location
GET    /prompts                           library
GET    /prompts/template?key=...          active body
POST   /prompts/template                  publish a new version
GET    /status/backend                    brain -> platform probe
POST   /bootstrap                         idempotent seed (+ optional run)
POST   /mirror/authors[?dry_run=]         push bylines to the platform registry
```

Platform / publish endpoints (separate service, bearer token; see harness `cli/AGENTS.md`
for the local port):

```
POST /articles          one article + Idempotency-Key header
POST /articles:batch    {"articles":[{...,"idempotency_key":"..."}]}  atomic
POST /media             raw image bytes -> {"url":"/media/<sha>.png", ...}
```

Trigger a managed batch (through the harness console proxy):

```bash
curl -s -X POST http://127.0.0.1:8083/api/runs \
  -H 'content-type: application/json' \
  -d '{"mode":"managed","n":4,"images":true}'
# -> 202 {"run_id":"...","mode":"managed","status":"running"}
# then poll:
curl -s http://127.0.0.1:8083/api/runs/<run_id>
```

Read and refine an author:

```bash
curl -s http://127.0.0.1:8083/api/personas/<id>
curl -s -X PATCH http://127.0.0.1:8083/api/personas/<id> \
  -H 'content-type: application/json' \
  -d '{"about":"...","style":"..."}'
```

For the deep design rationale and the file-level contracts, see `AGENTS.md` and
`docs/research/`. For the local ports, tokens, and the direct-publish walkthrough, see the
harness `cli/AGENTS.md`.
