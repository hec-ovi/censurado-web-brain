# AGENTS.md

Agent-oriented map of `censurado-web-brain`. This repo is **the newsroom**: the agentic
CLI a driver walks to write and publish articles, the editorial prompt recipe it follows,
the maintenance sweeps that keep the corpus tidy, and the single Docker Compose that brings
up the whole Censurado system by building each service from its sibling code repo. It
re-implements none of those repos. It holds no data and runs no server or model: authors,
sources, and articles live only in the backend, and the CLI talks to it over HTTP.

This file explains what the newsroom does, its moving parts, and what each expects, then
**points to the authoritative contract files** in each repo rather than copying them. When a
contract here disagrees with the file it points to, the file wins: update this map.

For the internals of a given part, read that repo's own `README.md` / `AGENTS.md`
(`censurado-web-backend` and `censurado-web` each maintain theirs). This file stays at the
seam between them.

## The system in one pass

```
 cli agent ──POST /articles──► publish ──► sqlite ──► generate ──► site-data ──► site
             (operator token)  (only writer)         (watch)      (static)      (public nginx)
```

The agent reads its authors and their sources from the backend (`publish`) and the workflow
prompts from this repo's `prompts/` files on disk (the newsroom recipe, no server). The
operator panel is the human UI, folded into `publish` (same port); it edits content THROUGH
the backend, never writing the db directly.

Articles enter through one door, the write API (`publish`), the sole sqlite writer. A CLI
agent authors the piece itself and POSTs it, with no inference backend and no model budget
(see `cli/SKILL.md`); the authors and sources it reads come from the backend, and the
workflow/persona prompts are on-disk files in this repo (nothing runs a model). The static
site is **not** rebuilt on publish: a separate `generate` pass reads the db and materializes
the files that `site` (nginx) serves. The operator panel is served by `publish` itself (the
backend's embedded SPA); its edits go THROUGH the backend API. `comfyui` renders the optional
per-article hero image.

| Service | Built from | Role | Host bind |
|---|---|---|---|
| `publish` | `../censurado-web-backend` (`deploy/Dockerfile.publish`) | Write + read API and media store, the only sqlite writer, owns all content data (authors, sources, articles, layout); also serves the operator panel (embedded SPA + Go login, one gated login) | 127.0.0.1:8082 |
| `generate` | `../censurado-web` (pinned `golang` image, both repos mounted ro) | Static site builder, `-watch`es the db and rebuilds | resident |
| `init-perms` | busybox | One-shot: chown the site + data + gocache volumes to uid 65532 | none |
| `site` | nginx | Serves the static portal | **0.0.0.0:8080** |
| `comfyui` | `../comfyui-strix-docker` | Image generation, the art director's backend | 127.0.0.1:8188 |

Only `site` is exposed on all interfaces. Everything operational binds `127.0.0.1`;
reach it on the host or over an SSH tunnel.

## What it expects

- The sibling code repos checked out next to this one: `../censurado-web-backend`
  (publish + the admin panel), `../censurado-web` (generate + the public frontend),
  `../comfyui-strix-docker` (comfyui). The newsroom itself (CLI + prompts + sweeps + compose)
  is THIS repo.
- Docker + Compose v2.
- ComfyUI models on disk for `comfyui`.
- For `comfyui`: an AMD Strix Halo box (gfx1151) on a recent amdgpu kernel. The rest
  of the stack is plain HTTP services with no GPU need.
- Config in `.env` (copy from `.env.example`, then `./bootstrap.sh` fills the
  secrets). Contract: `.env.example` documents every field. `.env` and `keys.json`
  are gitignored and host-specific.
- For the maintenance sweeps (`censurado-brain`) and the local test suite: the Python
  package installed into a venv (`make install`, uv). The authoring CLI (`cli/censurado.py`)
  is stdlib-only and needs no install.
- Optional, only to publish the built site: Node.js 20+ and the wrangler CLI
  (`wrangler login` once). That covers the Cloudflare Pages path; if you publish
  somewhere else, you need that service's tool instead (see Operations -> Deploy).

## Cross-service contracts (what this repo pins)

These are the agreements that must hold **across** repos. Each points to where it
is actually defined and enforced.

### 1. The operator token (the one coupling)

A single publish key minted with **three** scopes: `articles:write`,
`articles:publish-any`, and `admin:write`. A CLI agent uses it to publish as any author;
`admin:write` additionally unlocks the operator edit lane (`PUT`/`DELETE /articles/{slug}`,
author/topic CRUD) that the panel edit lane and the topic cleanse drive. Since all content
data lives in the backend, this same token is also what gates the CLI's author and source
reads and writes (`personas`/`persona`/`create-author`/`sources`/`portals`/`remove-author`),
not just publishing articles.

- Minted and registered by `bootstrap.sh` (via `publish -gen-key`), which writes
  the token to `.env` (`NEWSROOM_OPERATOR_TOKEN`)
  and the SHA-256 **hash** to `keys.json` (the token itself is never stored).
- Token format, hashing, scope semantics: `../censurado-web-backend/internal/publish/auth.go`
  and `../censurado-web-backend/internal/publish/publish.go` (`ScopeWrite`, `ScopePublishAny`).
- Minting CLI: `../censurado-web-backend/cmd/censurado/publish/main.go` (`-gen-key`).
- Wiring: `bootstrap.sh`, `docker-compose.yml` (`publish` environment).

### 2. The publish API (POST /articles)

The only way articles enter the system. Authenticated, idempotent, single writer.

- Request: `Authorization: Bearer <token>`, required `Idempotency-Key` header, a
  strict JSON body (unknown fields rejected, 8 MiB cap). Body schema is
  `domain.PublishInput`: `title`, `body` (markdown), `author`, `section` required;
  `topics`, `slug`, `published_at`, `metadata` optional.
- Portada order is strictly `published_at` descending: the newest one is the lead
  headline. `published_at` defaults to now, so a fresh post takes the top slot. To
  add an article without moving the headline, backdate `published_at` to before the
  current front articles; it slots in lower instead of leading. See `cli/SKILL.md`.
- Responses: `201` created, `200` deduped/replayed, `401` missing/invalid token,
  `403` insufficient scope or author mismatch, `422` validation.
- Contract files: `../censurado-web-backend/internal/publish/publish.go` (handler),
  `../censurado-web-backend/domain/article.go` (`PublishInput`),
  `../censurado-web-backend/contracts/article.schema.json` (the JSON Schema).
- The same service also serves the JSON read API (`GET /authors`, `/topics`,
  `/articles`, `/articles/{slug}`, `/sources`) and the media store (`POST /media`,
  `GET /media/{name}`). Also exposes `GET /healthz`.
- **Operator mutation lane (behind `admin:write`).** The write route above is
  append-only, but the same service edits live articles in place: `PUT /articles/{slug}`
  (replace, permalink kept), `DELETE /articles/{slug}` (soft-delete),
  `POST /articles/{slug}/restore`, plus author/source/topic CRUD. The `censurado.py edit`
  verb drives it with the operator token; the panel drives it from the operator's session.

### 3. The newsroom recipe (the workflow + persona prompts)

The newsroom recipe is not a service. It is a set of on-disk FILES in this repo's
`prompts/` dir: the workflow step-gate nodes (`workflow/*.md` + `workflow/manifest.json`),
the persona synthesize prompt (`persona/synthesize.md`), and the editorial style guide
(`editorial/style.md`, read via the `style` verb). There is no server and no database. The
CLI (`cli/censurado.py`) reads and writes them directly from disk (verbs `step`, `prompt`,
`set-prompt`, `style`); git in this repo is their version history.

- The recipe dir resolves from `CENSURADO_PROMPTS_DIR` (default this repo's `prompts/`,
  a sibling of `cli/`); no env is set in `docker-compose.yml` for it.
- The enforced numeric floor is separate and travels WITH the CLI in
  `cli/workflow/parameters.json` (verb `set-floor`), not with the prompts.
- Authors and their sources are NOT here: they live in the backend (`publish`) and the CLI
  reads/writes them over HTTP with the operator token (`personas`/`persona`/`create-author`/
  `sources`/`portals`). There is no `personas.db` and no separate config service.
- Contract files: `prompts/` (the recipe), `cli/workflow/parameters.json` (the numeric
  floor), `cli/censurado.py` (the reader: `_prompt_path`, `cmd_step`).
- The agent-facing surface (the verb set, the routes that name verbs, the recipe files a verb
  reads, and the knobs) is frozen in `cli/CONTRACT.md` and enforced by
  `tests/test_cli_contract.py`. To change a verb, a routed reference, or a knob, edit both in
  the same commit.

### 4. Generate then serve (the "CDN" seam)

Publishing writes the db; it does **not** itself write the public site. A separate
`generate` pass materializes the static files. `generate` is a resident watcher
(`-watch`), so a publish appears on the portal within ~2s; standalone you run it on demand.

- `generate` opens the db read-only and writes static artifacts to the `site-data`
  volume: article permalinks at `/a/<slug>-<hash>/`, per-scope listing landings
  (the front page is the `latest` landing at `/latest/`), feeds (`feed.xml`,
  `atom.xml`, `feed.json`), `sitemap.xml`, JSON shards, and a purge manifest under
  `.generated/`. There is no root `/index.html`.
- It runs continuously (`restart: unless-stopped`); `make site` forces a one-shot
  rebuild. The generate watcher owns regeneration; no operator surface triggers a rebuild.
- `site` (nginx) serves `site-data` read-only. The config redirects `/`
  to `/latest/` so the origin works as the main page: `nginx/site.conf`.
- Contract files: `../censurado-web/cmd/censurado/generate/main.go`,
  `../censurado-web/internal/generate/` (`collectors.go`, `sitemap.go`),
  `../censurado-web/contracts/shard.schema.json`,
  `../censurado-web/contracts/manifest.schema.json`.

### 5. The operator panel (the one human surface)

The operator panel is folded into the `publish` backend: the Go backend serves a buildless
ES-module SPA plus a signed-cookie login (Go, via `go:embed`) on the same port as the publish
API, reached at `http://127.0.0.1:8082/`. It gates the whole surface behind that login and
talks to the backend only (same-origin); a browser session maps to the operator identity
inside the backend, so no operator Bearer is injected anywhere and the token never touches the
panel path. Six tabs: Articles (list/edit/delete/restore), Portada, Autores, Temas, Sources,
Status. Prompt and workflow node text is edited in this repo's git-tracked prompt files on
disk (via the CLI), not in the panel. Contract: `../censurado-web-backend/internal/adminweb/`
(embedded `static/` plus the Go login/session in `auth.go`, `gate.go`, `login.go`, `ui.go`).

### 6. ComfyUI (the art director's image backend)

`comfyui` renders per-article hero images (FLUX.2 klein). The CLI agent drives it directly
(`censurado.py image`, see `cli/skills/media/SKILL.md`), loading the render graph from
`cli/templates/flux2_klein_t2i.json`. The text-to-image path runs from a known-good render;
the reference (image-to-image) path is built but unproven on live hardware. ComfyUI build
context: `../comfyui-strix-docker/`.

## The parts (point files)

**The newsroom (this repo)**
- `cli/censurado.py` the agent-facing CLI (stdlib-only): publish/edit, media, image,
  tweet/truth capture, authors + sources over the backend, and the `step` gate.
- `cli/SKILL.md` + `cli/skills/` the resolver skill + fat sub-skills.
- `cli/workflow/parameters.json` the enforced numeric floor/caps the walk fills into nodes.
- `prompts/` the editorial recipe (workflow nodes + manifest, persona synthesize, style).
- `newsroom/` the maintenance CLI (`censurado-brain`): `status` (backend health probe),
  `topics cleanse`, `embeds recheck`. Installed package; needs httpx + the corpus helpers.
- `docker-compose.yml` the whole topology (services, network, volumes, ports).
- `.env.example` every config field and which are secrets.
- `bootstrap.sh` mints the operator key + panel login, seeds `keys.json`, fills `.env`,
  fixes the site volume perms.
- `Makefile` install/test/lint (python) + bootstrap/up/up-publish/site/generate/down/deploy.
- `nginx/site.conf` the public static-site server (root redirect to `/latest/`).
- `deploy/deploy-cdn.sh` + `deploy/CACHING.md` the Cloudflare Pages push + cache policy.
- `functions/` the Cloudflare Pages Function for article reactions (like/dislike + D1).
- `tests/` the local suite (CLI, sweeps, prompt drift, contracts, compose wiring).

**Data + API (`../censurado-web-backend`)**
- Publish + read API and media store: `internal/publish/`, `cmd/censurado/publish/`
  (the sole writer; `-gen-key` mints the operator token).
- Operator panel (embedded SPA + Go login, served on the publish port): `internal/adminweb/`.
- Domain + storage (the public libs the generator imports): `domain/`, `store/`,
  `content/`, `media/`.
- Publish contracts: `contracts/article.schema.json`, `contracts/batch-*.schema.json`.

**Generator + public frontend (`../censurado-web`)**
- Generator: `internal/generate/`, `cmd/censurado/generate/` (one-shot or `-watch`).
- Public frontend (HTML/CSS/JS, embedded at compile time): `internal/generate/templates/`,
  tested under `web/` (vitest).
- Shard/manifest contracts: `contracts/shard.schema.json`, `contracts/manifest.schema.json`.
- Imports the backend's shared libs via a `replace` directive; the generate
  container mounts both repos so it resolves.

**Media**
- `comfyui`: `../comfyui-strix-docker/` (hero-image rendering).

## Operations

```bash
cp .env.example .env               # then edit the GPU GIDs + COMFYUI_MODELS_PATH
./bootstrap.sh                     # mint secrets, seed keys.json, fill .env, fix volume perms
docker compose up -d               # the WHOLE stack (adds comfyui: needs the GPU box)
docker compose run --rm generate   # build the static site (served live by `site`)
```

**GPU-free publishing lane.** Everything a CLI agent needs to write, review, publish, and
serve runs with no GPU: only `comfyui` (hero images) needs the Strix Halo box, and nothing
in the publishing path depends on it. Bring up just that lane with an explicit service list:

```bash
docker compose run --rm init-perms                                # first run / after a wipe
docker compose up -d publish generate site    # no comfyui
```

**Deploy to the live site (optional, Cloudflare).** `make deploy` (or
`deploy/deploy-cdn.sh`) builds a fresh static snapshot at the production page size and
pushes it to Cloudflare Pages (elcensuradoweb.com). This path uses the wrangler CLI
(invoked as `npx -y wrangler`, so it wants Node.js 20+ on the host), authed once with
`wrangler login`; the host's `wrangler` skill covers its commands and config, and
`deploy/CACHING.md` documents the cache headers. Cloudflare Pages is the tested target
and effectively the default here, but it is only the publish step: `dist/` is plain
static files, so shipping elsewhere (a direct FTP push, another static host, an object
store) just means swapping `deploy-cdn.sh` for that service's own CLI or skill. Nothing
else in the stack depends on Cloudflare.

The `Makefile` wraps these (`make up`, `make site`, `make generate`, `make
init-perms`, `make test`) when `make` is installed; the raw commands above work
without it. After deleting volumes (`docker compose down -v`), run `docker compose
run --rm init-perms` once before the next generate so the site volume is writable.

- Operator panel: http://localhost:8082 (the human entry point, served by `publish`;
  localhost login defaults to `admin` and is prefilled by the panel).
- Public portal: http://localhost:8080 (redirects to the latest landing).
- Write API: http://localhost:8082. ComfyUI: http://localhost:8188.

Articles are authored by a CLI agent following `cli/SKILL.md`, then published to the
write API; the `generate` watcher refreshes the portal within ~2s. The backend holds the
authors and sources the agent reads; the workflow/persona prompts are on-disk files in this
repo. Nothing runs a batch model in-process.

Tests (no build, no GPU): `make test` (or `.venv/bin/pytest tests -q`). They cover the
authoring CLI, the maintenance sweeps, the editorial prompt drift-guards, the article-contract
mirror, the skill package, and the compose wiring over the real `docker compose config` parser
(the service set `comfyui`/`publish`/`site`/`generate`/`init-perms`, with no config-plane
service; `publish` serves the operator panel with no separate dependency; `site` the only
public port; `init-perms` first as a one-shot the writers wait on; `generate` a resident
watcher; the db and media on persistent host bind mounts; one shared network).

## Invariants and gotchas

- **Single writer.** Only `publish` writes the sqlite db. `generate` opens it
  read-only; the panel's edits go through the backend's operator API.
- **Publish is not the site.** A publish lands in the db immediately but is invisible
  on the portal until a `generate` pass. This split is intentional.
- **No data or server in this repo.** Authors, sources, and articles live only in the
  backend; the CLI reads/writes them over HTTP. Delete this repo and the backend still
  serves the whole site and the admin panel; you only lose the way to author new pieces.
- **Site volume ownership.** `generate` runs as the distroless nonroot
  uid (65532) but a fresh `site-data` volume initializes root-owned, so the first
  write fails. `init-perms` chowns it; `bootstrap.sh`, `make up`, and `make generate`
  all run it first. After a `make stack-clean` (which deletes volumes) this re-applies.
- **Only `site` is public.** Every other port binds `127.0.0.1`. There is no TLS or
  auth in front of the operational ports; add one before exposing them.
- **Secrets are host-local.** `.env` and `keys.json` are gitignored. `bootstrap.sh`
  is safe to re-run; it mints a fresh operator key and panel login each time.
