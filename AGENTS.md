# AGENTS.md

A map of `censurado-web-brain` for an agent (human or model) working in it. It says what each part does and points at the file that is the contract; it does not restate the code.

The brain is the newsroom CONFIG PLANE. It runs no model. It stores the authors, the sources each one reads, and the editorial style in one SQLite, and serves them plus the file-based prompt library over an HTTP API to a CLI agent and to the harness operator panel. The CLI agent does the writing and publishes to the platform's `POST /articles` itself; the editorial bar and the publish contract live in the harness `cli/SKILL.md`.

One process boundary matters: the brain serves HTTP. Everything else is in-process packages whose contract is the function signature.

```
[panel / CLI agent] --HTTP--> [ Brain (FastAPI): authors, sources, prompts, editorial style ]
```

Two invariants, enforced by tests:
- **No output-length cap, ever.** No `max_tokens` / `max_words` / "in N words" anywhere. The brain calls no model, so there is nothing to cap; the rule still stands if that ever changes.
- **One publish seam.** The brain and the platform meet only at the platform HTTP API. The brain owns authors and prompts; the platform stores `author` as a free string and has no persona concept.

## Components

### Brain HTTP surface
The FastAPI app: the config-plane API. Authors, sources, editorial style, the prompt library, a backend-connection probe, and the two lifecycle actions (seed a fresh box, mirror authors).
- → `newsroom/brain/app.py` (`create_app`, the inline persona routes, `/health`)
- → `newsroom/brain/routes/` (`personas.py`, `portals.py`, `editorial.py`, `prompts.py`, `status.py`, `admin.py`)
- → `newsroom/config.py` (`Settings`: all `NEWSROOM_*` env; no length setting by policy)

### Personas (brain-owned identities)
Typed CRUD over the brain's SQLite. A persona's `id` becomes the article `author` at the publish seam. Created via `POST /personas/direct` (pure persist, no model); an empty database has zero personas.
- → `newsroom/personas/store.py` (`Persona`, `PersonaStore`, `open_store`, `slugify`)

### Editorial (sources, style, location, the seeders)
The source registry and per-author source links, the versioned house style guide (voice, exemplars, the vetoed-term lexicon, the sourcing floor), and the publication location. The seeders load the defaults on bootstrap (style and location) and create no authors or portals.
- → `newsroom/editorial/portals.py` (`Portal`, `PortalStore`)
- → `newsroom/editorial/style.py` (`StyleGuide`, `StyleStore`) + `default_style.json`
- → `newsroom/editorial/location.py` (`Location`, `LocationStore`, `DEFAULT_LOCATION`)
- → `newsroom/editorial/seeds.py` (`seed_all`; `DEFAULT_PERSONAS=()`, `DEFAULT_PORTALS=()`)

### Prompts (the agnostic workflow text)
`.md` files with `{{TOKEN}}` placeholders, no length caps, split by key prefix into two families. The `prompts/workflow` set is the editorial step-gate loop the CLI `step` verb walks one node at a time (research, outline, draft, the six-dimension `60-evaluate`, respin, factcheck, enrich, accents-and-entities, finalize, image), ordered by `workflow/manifest.json`; it reads the style parameters via `{{MIN_SOURCES}}` / `{{RESPIN_PASSES}}` / `{{TOPIC_CAP}}`. Those workflow nodes are FILE-BASED: `GET /prompts/template` serves the file, a publish WRITES it in place, and git is their history (no DB versions), so the brain is their single source of truth and the CLI `step` walk fetches them live. `persona/synthesize.md` (author voice) is the only non-workflow prompt; like the workflow nodes it is FILE-BASED (a read serves the file, a publish writes it in place, git is its history). `GET /prompts` lists them all. The brain serves the text raw; the CLI agent fills the placeholders.
- → `newsroom/prompts.py` (`load_prompt`, `render`)
- → `prompts/workflow/*.md`, `prompts/workflow/manifest.json`, `prompts/persona/synthesize.md`

### Database
The brain-owned SQLite: connection + schema (personas, editorial config, the versioned style table). The persona content hash that derives slugs lives in `contracts/hashing.py`.
- → `newsroom/db.py` (`SCHEMA`, `open_db`)

### Mirror + cleanse
`mirror` reconciles the platform author registry into the local personas on bootstrap (best-effort: a down platform is a no-op, so it never empties the newsroom). `cleanse` remaps topic tags from a map file.
- → `newsroom/mirror/` (`fetch_web_authors`, `reconcile_personas`, `ReconcileResult`)
- → `newsroom/cleanse/` (topic-tag cleanup)

### Contracts (the cross-repo seam, vendored)
The article and batch shapes are pinned copies of the platform schemas, governed by a drift test. The brain pins its own closed section enum (the platform treats `section` as a free string).
- → `newsroom/contracts/article.py`, `schema.py`, `hashing.py`, `slug.py`, `sections.py`
- → `newsroom/contracts/vendored/v1/*.schema.json` (do not hand-edit)

### Tests
Every test drives a real entry point through to its side effect: the brain's FastAPI app in-process via `TestClient`, with the outbound backend calls (the platform publish/read seam) checked against the pinned contract.
- → `tests/`

### Infra
- → `Dockerfile` (uvicorn serving `create_app --factory`; pure-PyPI deps, no git or build toolchain)
- → `deploy/docker-compose.yml` (brain) and `deploy/.env.example`

## Operating the running brain

No auth; treat it as a trusted-network service. In the harness it is published on `127.0.0.1:8085` and the CLI agent calls its routes directly (no `/api` prefix); the operator panel reaches it in-network as `brain:8000` and gates it behind a login. Standalone it binds `127.0.0.1:8722`. Errors come back as `application/problem+json`.

- **Authors:** `GET /personas`, `GET /personas/{id}` (the full record: `who_i_am`, `about`, `style`, `few_shots_*`, `sources`, ...), `POST /personas/direct` (create), `PATCH` / `DELETE /personas/{id}`.
- **Sources:** `/portals` (registry CRUD + enable/disable), `GET|PUT /personas/{id}/sources` (the per-author pool). Co-owned outlets share an `ownership_group` and count once in the corroboration gate.
- **Editorial:** `/editorial/style` (versioned house style; the `structure` block carries the `respin_passes` and `topic_cap` workflow parameters, changed by publishing a full version with `POST /editorial/style`), `/editorial/style/lexicon` (the vetoed terms), `/editorial/style/sourcing` (the `min_sources` floor plus the `require_attribution` and `no_fabricated_quotes` flags), `/editorial/location`.
- **Prompts:** `/prompts` (list), `GET /prompts/template?key=...` (serves the current file body), `POST /prompts/template` (writes the `.md`/`.json` in place; git is its history).
- **Lifecycle:** `POST /bootstrap` (seed style/location; idempotent; creates no authors or sources), `POST /mirror/authors` (backfill), `GET /status/backend`, `GET /health`.

To AUTHOR and publish an article, use the harness `cli/SKILL.md` (the resolver) and its `write-article` sub-skill: the publish contract, the editorial bar, and the operator token live there.
