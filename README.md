<h1 align="center">censurado-web-brain</h1>

<p align="center">
  <strong>The Censurado newsroom: a host-run agentic CLI plus the on-disk prompt recipe that drives the rest of the system, and the one Docker Compose that wires the Go data/API backend (which owns all content data and serves the operator admin panel), the static-site generator, the public site, and ComfyUI. It holds no data and runs no server or model: authors, sources, and articles all live in the backend, and the CLI talks to it over HTTP.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/compose-one_stack-2496ED?logo=docker&logoColor=white" alt="Docker Compose" />
  <img src="https://img.shields.io/badge/AMD-Strix_Halo-ED1C24?logo=amd&logoColor=white" alt="AMD Strix Halo" />
  <img src="https://img.shields.io/badge/License-MIT-blue" alt="License" />
</p>

---

## What this is

This repo is the newsroom: the agentic CLI a driver walks to write and publish articles (`cli/`), the editorial prompt recipe it walks through (`prompts/`), the maintenance sweeps that keep the corpus tidy (`newsroom/`, the `censurado-brain` command), and the single `docker-compose.yml` that brings up the whole pipeline. It re-implements none of the other repos: every service builds from a sibling repo's own Dockerfile (or pulls an image), and this repo owns the wiring between them, the one secret that couples them, and the recipe the agent follows.

There is no database and no server here. Content (authors, sources, articles, layout, topics) lives only in the backend, and the CLI reads and writes it over the backend's HTTP API. Delete this repo and the backend still serves the whole site and its admin panel; what you lose is the way to author new pieces.

This repo is the source of truth for how the system runs. If a claim about ports, flows, or contracts conflicts with the code and compose here, the code wins. Read this README, `AGENTS.md`, and `docker-compose.yml` first.

It expects the code repos checked out next to it:

```
workspace/
  censurado-web-brain/        <- you are here (the newsroom: CLI + prompts + sweeps + compose)
  censurado-web-backend/      <- data + API: sqlite store, publish/read API, media, admin panel
  censurado-web/              <- static-site generator + the public frontend (templates/CSS/JS)
  comfyui-strix-docker/       <- ComfyUI on ROCm (image generation)
```

The code repositories, all under [github.com/hec-ovi](https://github.com/hec-ovi):

- [censurado-web-backend](https://github.com/hec-ovi/censurado-web-backend) - data + API (sqlite store, publish/read API, media) and the operator admin panel.
- [censurado-web](https://github.com/hec-ovi/censurado-web) - static-site generator + public frontend.
- [comfyui-strix-docker](https://github.com/hec-ovi/comfyui-strix-docker) - ComfyUI on ROCm, the art director's render backend.

## The pieces

| Service     | Built from                  | Role                                                | Host bind |
|-------------|-----------------------------|-----------------------------------------------------|-----------|
| `publish`   | [censurado-web-backend](https://github.com/hec-ovi/censurado-web-backend) | Write + read API, media store, the only sqlite writer, owns all content data (authors, sources, articles, layout), AND serves the operator admin panel (gated SPA over Articles, Portada, Autores, Temas, Sources, Status) | 127.0.0.1:8082 |
| `generate`  | [censurado-web](https://github.com/hec-ovi/censurado-web) | Static-site builder, watches the db and rebuilds    | none |
| `site`      | nginx                       | Serves the generated static portal                  | **8080** |
| `comfyui`   | [comfyui-strix-docker](https://github.com/hec-ovi/comfyui-strix-docker) | Image generation, the art director's render backend | 127.0.0.1:8188 |

Only `site` is exposed on `0.0.0.0`. Everything operational binds `127.0.0.1`; reach it on the host or over an SSH tunnel. The `publish` image builds from `censurado-web-backend`; `generate` runs the pinned `golang` image over both `censurado-web` (the generator) and `censurado-web-backend` (the shared `domain`/`store` libraries it imports), mounted read-only.

## How it flows

```
 cli agent  ──POST /articles──►  publish  ──►  sqlite  ──►  generate  ──►  site-data  ──►  site
            (operator token,      (only            (watches db,                          (public)
             3 scopes)             writer)          rebuilds)
```

A CLI agent authors each piece and POSTs it, reading the authors and their sources from the backend and the workflow prompts from this repo's `prompts/` files on disk (the newsroom recipe; nothing runs a model). The single coupling between the agent and the backend is one operator token: a publish key minted with three scopes, `articles:write`, `articles:publish-any`, and `admin:write` (the last unlocks the author/source reads and writes plus the in-place edit lane and the topic cleanse that change articles which already exist). `bootstrap.sh` mints it, registers its SHA-256 hash in `keys.json` (the token itself is never stored), and writes the token into `.env`. The operator admin panel needs no token of its own: it is served by the backend, and a browser login maps to the operator identity in-process.

The static site is not rebuilt inside the publish request: `generate` is a separate pass that reads the db and materializes the files `site` serves. It runs with `-watch`, so a publish shows up on the portal within a couple of seconds; standalone you run it on demand.

### The newsroom (this repo)

- `cli/censurado.py` is the agent-facing CLI: publish/edit an article, upload media, render a hero, capture a tweet/truth snapshot, read and curate authors and sources over the backend, and walk the editorial `step` gate. It is stdlib-only (no install), so a driver runs it directly.
- `cli/SKILL.md` is the resolver skill that routes a CLI agent to the right fat sub-skill under `cli/skills/` (write-article, daily-batch, authors, sources, portada, prompts, media, deploy, websearch).
- `prompts/` is the editorial recipe: the `workflow/*` step-gate nodes + `manifest.json`, the `persona/synthesize.md` author guide, and `editorial/style.md`. Plain files, git is their history, no server and no database.
- `newsroom/` is the maintenance CLI (`censurado-brain`): a backend health probe (`status`) plus the `topics cleanse` and `embeds recheck` sweeps. It needs the package installed (httpx + the corpus helpers); the authoring CLI does not.

### ComfyUI

A CLI agent renders a hero image per article on `comfyui` (FLUX.2 klein) via `cli/censurado.py image` (see [cli/skills/media/SKILL.md](cli/skills/media/SKILL.md)), loading the render graph from `cli/templates/flux2_klein_t2i.json`. The reference-conditioned path is built but still unproven on live hardware; the plain text-to-image path runs from a known-good render.

## Using it, step by step

Prerequisites: Docker + Compose v2, the code repos checked out as siblings (see above), ComfyUI models on disk, and for `comfyui` an AMD Strix Halo box (gfx1151) on a recent amdgpu kernel. The rest of the stack is plain HTTP with no GPU need. A `Makefile` wraps every command below (`make up`, `make site`, `make test`, ...); the raw `docker compose` form works without `make`.

**1. Configure.** Copy the template and edit the non-secret machine values (GPU group ids):

```bash
cp .env.example .env      # edit RENDER_GID, VIDEO_GID, COMFYUI_MODELS_PATH
```

**2. Bootstrap the secrets.** This mints the operator publish key (scopes `articles:write`, `articles:publish-any`, and `admin:write`), writes it into `.env`, seeds `keys.json` with the token hash, and fixes the volume permissions. It also configures the localhost-only operator panel login (default token: `admin`) and writes a matching hash:

```bash
./bootstrap.sh
```

**3. Bring the stack up.** The first run builds the service images and downloads the Go module graph, so it is slow; later runs are fast:

```bash
docker compose up -d
docker compose ps          # everything Up; `generate` may take a minute
```

**No GPU?** `docker compose up -d` also starts `comfyui`, which needs the Strix Halo box.
Nothing in the write/review/publish/serve path depends on it, so bring up just that lane
with an explicit service list (or `make up-publish`):

```bash
docker compose up -d publish generate site   # no comfyui
```

You get the full CLI publishing lane (write, review, edit, serve) with no GPU. You only
lose the per-article image generation.

**4. Check it is alive.**

```bash
curl -s http://localhost:8082/healthz          # publish API -> ok
```

Open the operator panel at http://localhost:8082 (log in with the panel token from step 2). The backend serves it, and it manages the backend's content directly, so it works with only the backend up. The public portal at http://localhost:8080 is empty until the first article exists.

**5. Get articles in.** A CLI agent writes and publishes through the publish API, quota-free. Point a CLI agent (Claude, Codex, Hermes, OpenClaw, or any equivalent) at **[cli/SKILL.md](cli/SKILL.md)** and it walks the newsroom workflow one step at a time (`python3 cli/censurado.py step`): pick a mode, load the author's voice from the backend, research and cross-source, draft, clear the editorial gates, then show you the draft and ask before publishing. The step gate serves one node at a time, so the agent cannot skip a gate or one-shot the piece; the node text and the step order live in this repo's git-tracked `prompts/workflow/` files. It POSTs once, on approval. No model budget, nothing infers in-process. A piece can also be edited in place after it is live (the operator token carries `admin:write`), and an empty author database is filled by the same agent first.

**6. See it on the portal.** The `generate` service watches the db and rebuilds the static site within ~2s of a publish, so just refresh http://localhost:8080. To force a one-shot rebuild: `make generate`.

**7. Operate.** The panel (served by the backend on 8082) is the single operator surface: it lists and edits articles, curates the portada, creates and edits authors (name, voice/style, gender, topics, attached sources), derives topics, and manages sources, all against the backend behind one login. It talks to the backend directly and binds `127.0.0.1`.

**8. Tear down.** `docker compose down` stops everything but keeps the data (the db and media are host bind mounts under the neutral data dir `${CENSURADO_DATA_DIR:-../censurado-data}`, at `.../db` and `.../media`, which lives outside every code repo). To wipe the named volumes too: `docker compose down -v`, then `docker compose run --rm init-perms` before the next `up` so the site volume is writable again.

`bootstrap.sh` is safe to re-run; it mints a fresh operator key and panel login each time.

## Deploy to the live site

The portal at `localhost:8080` is a static snapshot of the local db. To push it to the live
site (elcensuradoweb.com, on Cloudflare Pages):

```bash
wrangler login            # once (OAuth)
# set CLOUDFLARE_ACCOUNT_ID in .env (your account id; see .env.example)
make deploy               # or ./deploy/deploy-cdn.sh
```

`deploy/deploy-cdn.sh` builds a fresh snapshot at the production page size, copies the
media, writes the root redirect and the cache headers, and pushes to Pages. The cache
policy (assets are re-fetched (no-store), media is immutable) is documented in [deploy/CACHING.md](deploy/CACHING.md).

## Running without the GPU box, or without Docker

The stack splits cleanly along the GPU line:

- **No GPU (no Strix Halo).** Everything except `comfyui` runs on any machine with
  Docker. Use `make up-publish` for the full write / review / publish / serve lane; you lose
  only per-article image generation. Publish text-only pieces, or attach images generated
  elsewhere via `POST /media`.
- **No ComfyUI.** Skip `comfyui` and publish without a hero image (or upload one); the
  per-article image step is simply skipped when ComfyUI is absent.
- **No Docker at all.** You cannot run the stack locally. Run it on a machine that has
  Docker and reach the APIs over a tunnel, or host the backend in the cloud and point your
  local CLI at it.

## Configuration

All config lives in `.env` (see `.env.example`). The non-secret defaults target the reference Strix Halo box (`/home/hec/models/comfyui`, render GID 990, video GID 44); change them for your machine. The secret fields are filled by `bootstrap.sh` and should not be hand-edited. `.env` and `keys.json` are gitignored. The maintenance sweeps read a couple of optional `NEWSROOM_*` vars (the backend base URL and an edit key), both with working defaults; see `.env.example`.

## What is left out, on purpose

- The litestream backup sidecar that `censurado-web-backend` ships for production. Add it from that repo's own compose if you want off-site backups; this compose keeps to the running system.
- TLS and auth in front of the operational ports. They bind `127.0.0.1` instead. Put a real auth layer in front before exposing any of them.

## Pending features (roadmap)

Not built yet, captured here so we can pick them up. Nothing below blocks the current pipeline.

- **Topic normalization as a skill** (brain). A CLI + skill pass that reads an author's articles, has the model detect topic variants of one entity ("milei" / "Milei" / "Javier-Milei"), and merges them to a single canonical tag across the articles and the author's main topics. The rewrite mechanics exist today (`censurado-brain topics cleanse --map-file --apply`); what is missing is the skill wiring and the automatic variant detection (today the map is supplied by hand).
- **Agentic importance arrange at the end of a batch** (brain). When a driver runs the full scheduler batch, the last step arranges the portada by importance. This is agent-driven, not an automatic ranking function. The portada is a matrix grid, and the skill rule is: alternate articles that carry media (image or video) with the ones that do not so media never clumps, give a very important article its own full-width single row, keep the rest in two-column rows, and never leave a gap (always fill both cells of a two-column row). Example shape: `[x]`, `[x, o]`, `[o, x]`, `[x]`, where a lone `x` is a full-width single row and a pair is two columns with the media cell alternating side row to row.
- **Drag-and-drop layout organizer** (backend panel). The portada organizer reorders with up/down buttons today; a visual drag-and-drop swap is a nice-to-have.
- **Analytics / BI dashboard** (backend panel). One surface for growth: a most-popular-topics chart (filtered totals, built to scale to thousands of topics), authors ranked by likes, authors with the fewest articles, and statistical/growth modeling. Note: author-likes needs a reactions data source the backend does not hold yet (reactions live in the downstream Cloudflare Pages reactions function).
- **Rebel Forge integration.** Integrate the Rebel Forge functionality (a separate GitHub repo). Pending, scope defined when picked up.

## Tests

```bash
make install                 # once: create the venv, install the package + dev tools (uv)
make test                    # the whole suite (or: .venv/bin/pytest tests -q)
```

The suite runs locally, no CI. It covers the authoring CLI (the tweet/truth snapshot mapping, the fail-soft error handling, the local step gate and its artifact enforcement), the maintenance sweeps (status probe, topic cleanse, embeds recheck), the editorial prompt drift-guards (the parameters stay client-filled placeholders, the anti-slop rules survive, every manifest node exists on disk), the article-contract mirror (hashing, slug, sections, schema drift), the skill package (the resolver routes only to sub-skills that exist), and the compose wiring via `docker compose config` (the real parser: the service set with no config-plane service, `site` the only public port, `generate` a resident watcher, the db and media on persistent bind mounts). No images are built and no GPU is needed.

## Layout

```
cli/censurado.py       the agent-facing CLI (publish/edit, media, image, tweet, authors, sources, step)
cli/SKILL.md           the control skill (resolver): routes a CLI agent to the right sub-skill
cli/skills/            the fat sub-skills (write-article, daily-batch, authors, sources, ...)
cli/workflow/          the enforced numeric floor/caps (parameters.json) the walk fills into nodes
cli/templates/         the ComfyUI render graph (flux2_klein)
prompts/               the editorial recipe: workflow step-gate nodes + manifest, persona + editorial
newsroom/              the maintenance CLI (censurado-brain): status probe + topic cleanse + embeds recheck
docker-compose.yml     the whole stack (services, network, volumes, ports)
AGENTS.md              agent-oriented map: the cross-service contracts and pointers
deploy/                deploy-cdn.sh (push to Cloudflare Pages) + CACHING.md (cache policy)
.env.example           config template (copy to .env)
bootstrap.sh           mint secrets, seed keys.json, fill .env, fix volume perms
mint-panel-login.sh    rotate or set just the operator panel login token
Makefile               install / test / lint + bootstrap / up / up-publish / down / deploy
nginx/site.conf        the public static-site server (root redirects to /latest/)
functions/             the Cloudflare Pages Function for article reactions (like/dislike + D1)
tests/                 the local suite (CLI, sweeps, prompt drift, contracts, compose wiring)
```
For the seam between the repos (the operator token, the publish API, generate then serve, the newsroom recipe), see [AGENTS.md](AGENTS.md). To have a CLI agent write articles in a persona's voice and publish them, see [cli/SKILL.md](cli/SKILL.md). For a part's internals, read that repo's own README.
