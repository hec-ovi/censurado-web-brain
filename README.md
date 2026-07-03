# censurado-web-brain

The newsroom config plane for Censurado. It stores the authors (their personas), the source outlets each one reads, the editorial style guide, and the versioned prompt library, and serves all of it over an HTTP API to a CLI agent that does the actual writing and to the harness operator panel. The brain runs no model of its own.

A CLI agent (Claude, Codex, or similar) reads an author and the workflow prompts from here, writes the article with its own model, and publishes it straight to the [backend](https://github.com/hec-ovi/censurado-web-backend)'s `POST /articles`. The [generator](https://github.com/hec-ovi/censurado-web) renders the backend's store into the static public site, and the whole stack runs together via the [harness](https://github.com/hec-ovi/censurado-web-harness).

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)
![Status: early](https://img.shields.io/badge/status-early-orange.svg)

The backend has no concept of personas (its article schema takes only an `author` string). Authors live only here, in this repo's SQLite, never in the backend and never in tracked code: an empty database has zero authors until an operator (or an agent) creates one.

## What it holds

- **Authors (personas).** Each author's identity: `who_i_am`, `style`, `about`, `beat`, few-shot examples, avatar, and language. Created on demand via `POST /personas/direct`; no model runs server side, the agent writes the persona JSON itself.
- **Sources (portals).** The outlets each author reads, for grounding and cross-checking. Registered per deployment, linked per author.
- **Editorial style.** A versioned house style guide (voice, exemplars, the vetoed-term lexicon) plus the workflow parameters the step-gate prompts read: `sourcing.min_sources` (the independent-source floor, default 5), `structure.respin_passes` (default 2), and `structure.topic_cap` (default 12). An operator edits the active version over the API; the prior versions stay restorable.
- **Prompt library.** 19 `.md` prompts in two dirs (`prompts/workflow`, `prompts/persona`), plus `prompts/workflow/manifest.json`. The `prompts/workflow` set is the editorial step-gate the CLI agent walks one node at a time (`00-mode` through `99-publish`: mode, batch-plan, pick-author, research, outline, draft, the six-dimension `60-evaluate`, respin, factcheck, enrich, accents-and-entities, finalize, image), plus the one-shot maintenance modes (`deploy`, `normalize-topics`, `portal-review`). It carries the style parameters as `{{MIN_SOURCES}}` / `{{RESPIN_PASSES}}` / `{{TOPIC_CAP}}` placeholders the CLI agent fills. The `prompts/workflow` nodes are file-based: the brain serves the file and a publish writes it in place (git is their history), so the brain is their single source of truth and the CLI `step` walk fetches them live. `prompts/persona/synthesize.md` (author voice) is the only non-workflow prompt; like the workflow nodes it is a file the brain serves and a publish writes in place (git is its history).

## How it fits the rest of the system

- The brain serves authors, sources, prompts, and style over HTTP; in the harness the CLI agent calls its routes on `127.0.0.1:8085` and the operator panel reaches it in-network as `brain:8000`. It writes nothing to the public site.
- A CLI agent reads an author (`GET /personas/{id}`) and the prompts (`GET /prompts/template?key=...`), writes the article itself, and publishes to the backend's `POST /articles`. The editorial bar and the art direction live with the agent (the harness `cli/AGENTS.md`), not here.
- One operator token (the `articles:write` + `articles:publish-any` + `admin:write` key) is the only coupling between the brain and the backend.

## Run it

Bring up the brain on its own:

```
cd deploy && cp .env.example .env   # set NEWSROOM_OPERATOR_TOKEN and the URLs
docker compose up --build           # brain on http://127.0.0.1:8000
```

Seed a fresh box once with `POST /bootstrap` (idempotent): it loads the default style and location and lifts the prompt library into the editable store. It creates no authors and no sources; those stay operator-owned. The shipped prompts serve from disk even before you bootstrap, so a CLI agent can read them on a clean box. For the full stack (backend, generator, site, ComfyUI) use the [harness](https://github.com/hec-ovi/censurado-web-harness); to author a single article from a CLI agent, follow the harness `cli/AGENTS.md`.

## Layout

```
newsroom/
  brain/        the FastAPI app: authors, sources, editorial, prompts, status, bootstrap
  personas/     the persona store (own SQLite, brain-owned)
  editorial/    portals and sources, the style guide, location, the prompt store, the seeders
  mirror/       the brain-to-backend author backfill and reconcile
  cleanse/      topic-tag cleanup
  contracts/    the vendored publish contracts, section enum, content hash, slug derivation
  cli.py        the operator CLI entry point (censurado-brain)
prompts/        versioned .md prompts (workflow step-gate + persona)
testkit/        the shared in-repo fake (publish seam + chat backend), used by the tests
tests/          end-to-end tests that drive the real entry points
deploy/         docker compose (brain) and the trigger example
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

- **Authors live only in the database.** Persona identity and the outlets an author reads are created by an operator or an agent and are never in tracked code. A fresh clone with an empty database has no authors and no sources.
- **No model here.** The brain holds the newsroom's configuration and serves it. The writing is done by the CLI agent's own model, and image generation by the agent's local ComfyUI (in the harness); the brain itself runs nothing.
- **No output-length caps.** Nothing in this repo sets a token, word, or sentence ceiling on a model.

## License

MIT. See [LICENSE](LICENSE).
