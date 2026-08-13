# The schedule executor's runtime. Build context is the REPO ROOT (see the
# compose service), so the pipeline's own pyproject resolves the dependencies and
# the two lanes (host CLI, container) cannot drift. The repo itself is bind-
# mounted at runtime, so code changes need no rebuild, only dependency changes do.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# The pipeline's deps (dbos, httpx, pydantic) from the repo's pyproject, plus the
# websearch-skill CLI the research nodes shell out to.
COPY pyproject.toml /tmp/deps/pyproject.toml
# The skill installs from the GitHub tarball (no git in the slim image).
RUN uv pip install --system -r /tmp/deps/pyproject.toml \
    && uv pip install --system https://github.com/hec-ovi/websearch-skill/archive/refs/heads/main.tar.gz \
    && rm -rf /tmp/deps

ENV PYTHONUNBUFFERED=1
WORKDIR /newsroom
CMD ["python3", "automation/executor/executor.py"]
