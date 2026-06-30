# Deploy

Run the brain and its console together.

## What runs

- **brain**: the FastAPI config-plane surface (authors, sources, prompts, editorial style), served by uvicorn on port 8000 inside the compose network. Its SQLite lives on the `brain-data` volume. It runs no model.
- **console**: nginx serving the buildless UI (`../frontend`) and reverse-proxying `/api` to the brain. Published on host port 8080.

The console has no auth of its own and the brain has state-changing POSTs, so keep this on an internal network or put an auth layer in front of port 8080 before exposing it.

## Start

```
cd deploy
cp .env.example .env        # set NEWSROOM_OPERATOR_TOKEN and the publish URL
docker compose up --build   # console on http://localhost:8080
```

The operator token must hold the platform scopes (`articles:write`, `articles:publish-any`, `admin:write`); that single key is the only coupling between the brain and the platform.

The default `NEWSROOM_PUBLISH_BASE_URL` points at the host platform via `host.docker.internal`. The compose maps that name to the host gateway (`extra_hosts`), so it resolves on Linux Docker Engine as well as Docker Desktop. Point it at wherever your publish service runs if it is not on the host.

## Seed a fresh box

Once the brain is up, `POST /bootstrap` (idempotent) loads the default style and location and lifts the prompt library into the editable store. It creates no authors and no sources; those stay operator-owned. An operator, or a CLI agent, creates authors via `POST /personas/direct` or the console.

Authoring and publishing are done by a CLI agent against the platform's `POST /articles`, following the harness `cli/AGENTS.md`. The brain does no writing of its own.

## Build the image alone

```
docker build -t censurado-web-brain ..
```

The image installs the package (pure-PyPI deps, no git or build toolchain) and bakes in `prompts/`; the database path and prompts dir are set by the compose environment.
