# syntax=docker/dockerfile:1
#
# Telegram -> headless agent bridge. Ships both supported agents (codex + agy) and the
# stdlib-only router. It does NOT manage model auth: codex (OpenAI/ChatGPT) and agy
# (Google Gemini) are logged in OUTSIDE the bridge, and their credential dirs (~/.codex,
# ~/.gemini) are mounted in at runtime. The container runs as your host uid (set in
# compose) so those mounted credentials are readable, and HOME points at the writable
# /data mount so the CLIs' own caches land there (the root filesystem is read-only).
ARG BASE_IMAGE=node:22-bookworm-slim
FROM ${BASE_IMAGE}

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates git \
 && rm -rf /var/lib/apt/lists/*

# QR rendering for the admin panel (pure-python, zero-dependency).
RUN pip3 install --no-cache-dir --break-system-packages segno

# The two agents. Auth is provided at runtime via mounted ~/.codex and ~/.gemini; this
# only installs the CLIs. Override with the TG_AGENT_INSTALL build arg if needed.
ARG TG_AGENT_INSTALL="npm i -g @openai/codex @google/gemini-cli && ln -sf $(which gemini) /usr/local/bin/agy"
RUN if [ -n "$TG_AGENT_INSTALL" ]; then sh -eu -c "$TG_AGENT_INSTALL"; fi

COPY router.py /app/router.py

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    REPO_DIR=/repo

# cwd is the mounted repo so `python3 cli/censurado.py` resolves and codex/agy read
# AGENTS.md -> cli/SKILL.md. No USER here: compose sets `user:` to the host uid so the
# mounted credential dirs are readable.
WORKDIR /repo
ENTRYPOINT ["python3", "/app/router.py"]
