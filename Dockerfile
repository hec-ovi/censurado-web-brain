# The brain image: the FastAPI config-plane surface, served by uvicorn. A plain
# Python package install, no build toolchain. The base is python:3.13-alpine, the SAME
# image the harness panel runs on (one python base shared by both, no separate uv
# image): the deps (fastapi, uvicorn, httpx, pydantic, pydantic-settings) are all
# pure-Python or ship musllinux wheels (pydantic-core), so pip compiles nothing. The
# dev toolchain is still uv (see the Makefile); only the image build uses pip.
FROM python:3.13-alpine

WORKDIR /app

# The package and everything it imports at runtime. prompts/ is NOT package data, so
# it is copied to a known path and pointed at by NEWSROOM_PROMPTS_DIR below. testkit/
# is test-only but is declared in [tool.setuptools].packages, so the wheel build needs
# the directory present; it is a couple of small Python files, negligible in the image.
COPY pyproject.toml README.md ./
COPY newsroom ./newsroom
COPY testkit ./testkit
COPY prompts ./prompts

# Install the package + its deps into the system interpreter (no venv layer).
RUN pip install --no-cache-dir .

# Runtime config: the SQLite lives on a mounted volume, prompts in the image.
ENV NEWSROOM_PERSONA_DB_PATH=/data/personas.db \
    NEWSROOM_PROMPTS_DIR=/app/prompts

EXPOSE 8000

# create_app is a factory (it reads settings from the environment); uvicorn builds it
# with --factory. Port 8000 is what the operator panel reaches in-network (brain:8000).
CMD ["uvicorn", "newsroom.brain:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
