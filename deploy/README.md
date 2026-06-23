# Deploy

Run the brain and its console together, and trigger a managed run on a schedule.

## What runs

- **brain**: the FastAPI surface and the run engine, served by uvicorn on port 8000 inside the compose network. Its SQLite lives on the `brain-data` volume.
- **console**: nginx serving the buildless UI (`../frontend`) and reverse-proxying `/api` to the brain. Published on host port 8080.
- **inference** (optional, profile `local-llm`): a llama.cpp/Vulkan server for the local decensored Gemma. Off by default because it needs a Vulkan GPU.

The console has no auth of its own and the brain has state-changing POSTs, so keep this on an internal network or put an auth layer in front of port 8080 before exposing it.

## Start

```
cd deploy
cp .env.example .env        # set NEWSROOM_OPERATOR_TOKEN and the two URLs
docker compose up --build   # console on http://localhost:8080
```

The operator token must hold BOTH platform scopes (`articles:write` and `articles:publish-any`); that single key is the only coupling between the brain and the platform.

The default `NEWSROOM_PUBLISH_BASE_URL` points at the host platform via `host.docker.internal`. The compose maps that name to the host gateway (`extra_hosts`), so it resolves on Linux Docker Engine as well as Docker Desktop. Point it at wherever your publish service runs if it is not on the host.

To run the bundled local model, drop a GGUF into `deploy/models/`, set `NEWSROOM_INFERENCE_BASE_URL=http://inference:8080/v1` in `.env`, and start with the profile:

```
docker compose --profile local-llm up --build
```

The inference service sets `GGML_VK_PREFER_HOST_MEMORY=1` so the model serves from shared system memory (GTT, the whole unified pool on an APU) rather than the small VRAM carveout. It needs `/dev/dri`, so it only runs on a box with a Vulkan GPU (a Strix Halo / gfx1151 is the reference).

## Trigger

Automation in v1 is a host crontab calling the one-shot run command. The brain is trigger-blind: the command picks a `mode` (the entire trigger surface) and exits with a status the schedule can act on.

```
censurado-brain --mode managed     # the full automated run
```

It prints a one-line JSON summary and exits `0` on a clean run, `2` if some article finalized but failed to publish (kept and re-publishable), `1` if the run itself failed. See `crontab.example` for a three-a-day schedule that execs this inside the running brain container; copy it, edit the path and times, and install with `crontab`.

A file-backed SQLite here uses WAL plus a busy timeout, so a one-shot run is safe alongside the live brain that serves the console.

## Build the image alone

```
docker build -t censurado-web-brain ..
```

The image installs the package (the research tool is a pinned git dependency, cloned at build time) and bakes in `prompts/`; the database path and prompts dir are set by the compose environment.
