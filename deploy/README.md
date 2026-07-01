# Deploy

Run the brain on its own.

## What runs

- **brain**: the FastAPI config-plane surface (authors, sources, prompts, editorial style), served by uvicorn on port 8000. Its SQLite lives on the `brain-data` volume. It runs no model.

The brain has no auth of its own and has state-changing POSTs, so it is published on `127.0.0.1:8000` only. Keep it on an internal network or put an auth layer in front before exposing it off-box. In the full stack it sits behind the harness operator panel, which gates it behind a login.

## Start

```
cd deploy
cp .env.example .env        # set NEWSROOM_OPERATOR_TOKEN and the publish URL
docker compose up --build   # brain on http://127.0.0.1:8000
```

The operator token must hold the platform scopes (`articles:write`, `articles:publish-any`, `admin:write`); that single key is the only coupling between the brain and the platform.

The default `NEWSROOM_PUBLISH_BASE_URL` points at the host platform via `host.docker.internal`. The compose maps that name to the host gateway (`extra_hosts`), so it resolves on Linux Docker Engine as well as Docker Desktop. Point it at wherever your publish service runs if it is not on the host.

## Seed a fresh box

Once the brain is up, `POST /bootstrap` (idempotent) loads the default style and location and lifts the prompt library into the editable store. It creates no authors and no sources; those stay operator-owned. An operator, or a CLI agent, creates authors via `POST /personas/direct`.

Authoring and publishing are done by a CLI agent against the platform's `POST /articles`, following the harness `cli/AGENTS.md`. The brain does no writing of its own.

## Build the image alone

```
docker build -t censurado-web-brain ..
```

The image installs the package (pure-PyPI deps, no git or build toolchain) and bakes in `prompts/`; the database path and prompts dir are set by the compose environment.
