# The brain image: the FastAPI config-plane surface, served by uvicorn.
# Buildless on the frontend side; this side is a plain Python package install.
FROM python:3.11-slim

# uv, the project's toolchain, copied from its official image rather than
# pip-installed. No git or build toolchain needed: the deps are pure-PyPI wheels.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# The package and everything it imports at runtime. prompts/ is NOT package data, so
# it is copied to a known path and pointed at by NEWSROOM_PROMPTS_DIR below.
COPY pyproject.toml uv.lock README.md ./
COPY newsroom ./newsroom
COPY testkit ./testkit
COPY prompts ./prompts

# Install into the system interpreter (no venv layer in the image).
RUN uv pip install --system --no-cache .

# Runtime config: the SQLite lives on a mounted volume, prompts in the image.
ENV NEWSROOM_PERSONA_DB_PATH=/data/personas.db \
    NEWSROOM_PROMPTS_DIR=/app/prompts

EXPOSE 8000

# create_app is a factory (it reads settings from the environment); uvicorn builds it
# with --factory. Port 8000 is the console's reverse-proxy upstream (brain:8000).
CMD ["uvicorn", "newsroom.brain:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
