# censurado-web-brain

The newsroom config plane for Censurado. It stores the authors (their personas), the source outlets each one reads, the editorial style guide, and the versioned prompt library, and serves all of it over an HTTP API to two clients: the operator console (a small web UI) and a CLI agent that does the actual writing. The brain runs no model of its own.

A CLI agent (Claude, Codex, or similar) reads an author and the workflow prompts from here, writes the article with its own model, and publishes it straight to the [backend](https://github.com/hec-ovi/censurado-web-backend)'s `POST /articles`. The [generator](https://github.com/hec-ovi/censurado-web) renders the backend's store into the static public site, and the whole stack runs together via the [harness](https://github.com/hec-ovi/censurado-web-harness).

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)
![Status: early](https://img.shields.io/badge/status-early-orange.svg)

The backend has no concept of personas (its article schema takes only an `author` string). Authors live only here, in this repo's SQLite, never in the backend and never in tracked code: an empty database has zero authors until an operator (or an agent) creates one.

## What it holds

- **Authors (personas).** Each author's identity: `who_i_am`, `style`, `about`, `beat`, few-shot examples, avatar, and language. Created on demand via `POST /personas/direct` or the console; no model runs server side, the agent writes the persona JSON itself.
- **Sources (portals).** The outlets each author reads, for grounding and cross-checking. Registered per deployment, linked per author.
- **Editorial style.** A versioned house style guide (voice, exemplars, the vetoed-term lexicon, the sourcing floor), with an operator-editable active version.
- **Prompt library.** The workflow prompts (`prompts/persona`, `prompts/journalist`, `prompts/manager`, `prompts/art_director`) as versioned `.md`. They ship in the image and serve as-is from disk; the versioned store is an override layer an operator can edit from the console.
- **Coverage memory.** What has already been covered, so a sweep can skip duplicates.

## How it fits the rest of the system

- The brain serves authors, sources, prompts, and style over HTTP (the console's nginx reverse-proxies `/api` to it). It writes nothing to the public site.
- A CLI agent reads an author (`GET /personas/{id}`) and the prompts (`GET /prompts/template?key=...`), writes the article itself, and publishes to the backend's `POST /articles`. The editorial bar, the run modes, and the art direction live with the agent (the harness `cli/AGENTS.md`), not here.
- The brain ships a ComfyUI client and a publish/media transport, so an operator or a script can render a hero image and upload it, but the brain triggers no generation on its own.
- One operator token (the `articles:write` + `articles:publish-any` + `admin:write` key) is the only coupling between the brain and the backend.

## Run it

Bring up the brain and its console together (the console serves the UI and reverse-proxies `/api` to the brain):

```
cd deploy && cp .env.example .env   # set NEWSROOM_OPERATOR_TOKEN and the URLs
docker compose up --build           # brain + console on http://localhost:8080
```

Seed a fresh box once with `POST /bootstrap` (idempotent): it loads the default style and location and lifts the prompt library into the editable store. It creates no authors and no sources; those stay operator-owned. The shipped prompts serve from disk even before you bootstrap, so a CLI agent can read them on a clean box. For the full stack (backend, generator, site, ComfyUI) use the [harness](https://github.com/hec-ovi/censurado-web-harness); to author a single article from a CLI agent, follow the harness `cli/AGENTS.md`.

## Layout

```
newsroom/
  brain/        the FastAPI app: authors, sources, editorial, prompts, status, bootstrap
  personas/     the persona store (own SQLite, brain-owned)
  editorial/    portals and sources, the style guide, location, the prompt store, the seeders
  manager/      the coverage store (what has been covered) and the triage type
  runs/         the runs and assignments store (the publish idempotency anchor)
  publish/      the raw-HTTP publish client and the media (image) uploader
  imagery/      the ComfyUI client and the FLUX.2 klein workflow template
  mirror/       the brain-to-backend author backfill and reconcile
  cleanse/      topic-tag cleanup
  contracts/    the vendored publish contracts, section enum, content hash, slug derivation
  cli.py        the operator CLI entry point (censurado-brain)
frontend/       the console: buildless vanilla JS + nginx, talks to the brain over /api
prompts/        versioned .md prompts (persona, manager, journalist, art_director)
testkit/        the shared in-repo fake (publish + media + ComfyUI), used by the tests
tests/          end-to-end tests that drive the real entry points
deploy/         docker compose (brain + console) and the trigger example
AGENTS.md       the operational map of the codebase
```

## Develop

The toolchain is [uv](https://docs.astral.sh/uv).

```
make install   # create .venv and install the package with dev deps
make test      # run the suite
make lint      # ruff
```

Every test hits a real entry point (an HTTP route or a CLI invocation) through to its side effect, not a mock of an internal function.

## Principles

- **Authors are data, not code.** Persona identity and the outlets an author reads live only in the database, created by an operator or an agent. A fresh clone with an empty database has no authors and no sources.
- **No model here.** The brain holds the newsroom's configuration and serves it; the writing is done by a CLI agent's own model. Image generation runs on a local ComfyUI when an operator or script asks for it.
- **No output-length caps.** Nothing in this repo sets a token, word, or sentence ceiling on a model.

## License

MIT. See [LICENSE](LICENSE).
