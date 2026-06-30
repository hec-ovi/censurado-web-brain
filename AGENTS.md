# AGENTS.md

A map of `censurado-web-brain` for an agent (human or model) working in it. It says what each part does and points at the file that is the contract; it does not restate the code.

The brain is the newsroom CONFIG PLANE. It runs no model. It stores the authors, the sources each one reads, the editorial style, and the versioned prompt library in one SQLite, and serves them over an HTTP API to the operator console and to a CLI agent. The CLI agent does the writing and publishes to the platform's `POST /articles` itself; the editorial bar and the publish contract live in the harness `cli/AGENTS.md`.

One process boundary matters: the brain serves HTTP. Everything else is in-process packages whose contract is the function signature.

```
[console / CLI agent] --HTTP--> [ Brain (FastAPI): authors, sources, prompts, editorial style ]
```

Two invariants, enforced by tests:
- **No output-length cap, ever.** No `max_tokens` / `max_words` / "in N words" anywhere. The brain calls no model, but the guard in `testkit/assertions.py` stays so any future call is checked.
- **One publish seam.** The brain and the platform meet only at the platform HTTP API. The brain owns authors and prompts; the platform stores `author` as a free string and has no persona concept.

## Components

### Brain HTTP surface
The FastAPI app: the config-plane API. Authors, sources, editorial style, the prompt library, a backend-connection probe, and the two lifecycle actions (seed a fresh box, mirror authors).
- → `newsroom/brain/app.py` (`create_app`, the inline persona routes, `/health`)
- → `newsroom/brain/routes/` (`personas.py`, `portals.py`, `editorial.py`, `prompts.py`, `status.py`, `admin.py`)
- → `newsroom/config.py` (`Settings`: all `NEWSROOM_*` env; no length setting by policy)

### Personas (brain-owned identities)
Typed CRUD over the brain's SQLite. A persona's `id` becomes the article `author` at the publish seam. Created via `POST /personas/direct` (pure persist, no model) or the console; an empty database has zero personas.
- → `newsroom/personas/store.py` (`Persona`, `PersonaStore`, `open_store`, `slugify`)

### Editorial (sources, style, location, the prompt store, the seeders)
The source registry and per-author source links, the versioned house style guide (voice, exemplars, the vetoed-term lexicon, the sourcing floor), the publication location, and the versioned prompt store. The seeders load the defaults on bootstrap (style, location, the on-disk prompt library) and create no authors or portals.
- → `newsroom/editorial/portals.py` (`Portal`, `PortalStore`)
- → `newsroom/editorial/style.py` (`StyleGuide`, `StyleStore`) + `default_style.json`
- → `newsroom/editorial/location.py` (`Location`, `LocationStore`, `DEFAULT_LOCATION`)
- → `newsroom/editorial/prompts_store.py` (`PromptStore`, `PromptTemplate`)
- → `newsroom/editorial/seeds.py` (`seed_all`, `seed_prompts`; `DEFAULT_PERSONAS=()`, `DEFAULT_PORTALS=()`)

### Prompts (the agnostic workflow text)
Versioned `.md` files with `{{TOKEN}}` placeholders, no length caps. They ship in the image and serve as-is: `GET /prompts/template` falls back to the on-disk file when the store has no version, so the prompts work on a fresh box and the store is a pure override layer.
- → `newsroom/prompts.py` (`load_prompt`, `render`)
- → `prompts/journalist/*.md`, `prompts/manager/triage.md`, `prompts/persona/synthesize.md`, `prompts/art_director/illustrate.md`

### Coverage memory
What has already been covered, so a sweep can skip duplicates. A store plus the triage type.
- → `newsroom/manager/coverage.py` (`CoverageStore`), `newsroom/manager/types.py` (`Triage`)

### Runs store + identity
The runs/assignments store and the content-hash idempotency anchor the publish transport uses.
- → `newsroom/runs/store.py` (`RunStore`, `Run`, `Assignment`)
- → `newsroom/contracts/hashing.py` (`content_hash` over title/body/author/section; `idempotency_key`)
- → `newsroom/db.py` (the SQLite schema)

### Publish + media transport
Raw-HTTP helpers to the platform: `publish_article` / `publish_batch` (build the payload, POST `/articles` or `/articles:batch`) and `upload_media` (raw PNG bytes to `/media`). A transport library the brain ships; the CLI agent is the primary publisher.
- → `newsroom/publish/client.py` (`publish_article`, `publish_batch`, `build_payload`)
- → `newsroom/publish/media.py` (`upload_media`, `MediaAsset`)

### Imagery (the ComfyUI client)
Drives a local ComfyUI running FLUX.2 klein: `ComfyClient` hides the submit/poll/fetch protocol; `graph.py` fills a checked-in template by node id. Used by an operator or a script, not auto-triggered.
- → `newsroom/imagery/comfy_client.py` (`ComfyClient`, `ComfyError`)
- → `newsroom/imagery/graph.py` (`build_graph`, `TEMPLATES`) + `templates/flux2_klein_t2i.json`

### Mirror + cleanse
`mirror` reconciles the platform author registry into the local personas on bootstrap (best-effort: a down platform is a no-op, so it never empties the newsroom). `cleanse` remaps topic tags from a map file.
- → `newsroom/mirror/` (`fetch_web_authors`, `reconcile_personas`, `ReconcileResult`)
- → `newsroom/cleanse/` (topic-tag cleanup)

### Contracts (the cross-repo seam, vendored)
The article and batch shapes are pinned copies of the platform schemas, governed by a drift test. The brain pins its own closed section enum (the platform treats `section` as a free string).
- → `newsroom/contracts/article.py`, `schema.py`, `hashing.py`, `slug.py`, `sections.py`
- → `newsroom/contracts/vendored/v1/*.schema.json` (do not hand-edit)

### Frontend (the operator console)
Buildless vanilla ES modules served by nginx; talks to the brain only over `/api` (nginx strips the prefix), and previews hero images proxied via `/media/`.
- → `frontend/src/` (`api.js`, `components/`), `frontend/nginx.conf`

### Tests + the shared fake
Every test drives a real entry point through to its side effect against one in-repo fake that stands in for the platform (`/articles`, `/articles:batch`, `/media`) and ComfyUI (`/prompt`, `/history`, `/view`).
- → `testkit/fake_server.py`, `testkit/assertions.py` (the no-cap guard)
- → `tests/`

### Infra
- → `Dockerfile` (uvicorn serving `create_app --factory`; pure-PyPI deps, no git or build toolchain)
- → `deploy/docker-compose.yml` (brain + console) and `deploy/.env.example`

## Operating the running brain

No auth; treat it as a trusted-network service. In the harness it sits behind the console nginx at `http://127.0.0.1:8083/api/<path>` (the container listens on `:8000`); standalone it binds `127.0.0.1:8722`. Errors come back as `application/problem+json`.

- **Authors:** `GET /personas`, `GET /personas/{id}` (the full record: `who_i_am`, `about`, `style`, `few_shots_*`, `sources`, ...), `POST /personas/direct` (create), `PATCH` / `DELETE /personas/{id}`.
- **Sources:** `/portals` (registry CRUD + enable/disable), `GET|PUT /personas/{id}/sources` (the per-author pool). Co-owned outlets share an `ownership_group` and count once in the corroboration gate.
- **Editorial:** `/editorial/style` (versioned house style), `/editorial/style/lexicon` (the vetoed terms), `/editorial/style/sourcing` (the `min_sources` floor and corroboration threshold), `/editorial/location`.
- **Prompts:** `/prompts` (list), `GET /prompts/template?key=...` (the active version, with the on-disk fallback), `POST /prompts/template` (publish a version), `/prompts/versions` + `/prompts/versions/{version}/promote`.
- **Lifecycle:** `POST /bootstrap` (seed style/location/prompts; idempotent; creates no authors or sources), `POST /mirror/authors` (backfill), `GET /status/backend`, `GET /health`.

To AUTHOR and publish an article, use the harness `cli/AGENTS.md`: the publish contract, the editorial bar, and the operator token live there.
