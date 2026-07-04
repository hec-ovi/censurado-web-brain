# Censurado newsroom: the agentic CLI + prompt recipe (this repo) and the docker stack
# that runs the backend, generator, public site, and comfyui (the sibling code repos).
# Two lanes: the python package (install/test/lint the CLI + sweeps) and the compose
# stack (bootstrap/up/deploy). Run `make install` once, then `make test`; run
# `make bootstrap` once, then `make up-publish`.

VENV := .venv
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

.PHONY: install test lint fmt clean \
        bootstrap build up up-publish down ps logs site generate init-perms config stack-clean deploy

# ----- python package (the CLI + the newsroom sweeps) -----
# Toolchain: uv (https://docs.astral.sh/uv). It provisions the interpreter and the
# virtualenv without needing a system python venv package.
install:          ## create the venv and install the package + dev tools
	uv venv $(VENV)
	VIRTUAL_ENV=$(VENV) uv pip install -e ".[dev]"

test:             ## run the whole suite (CLI + sweeps + compose wiring + prompt drift)
	$(PYTEST) tests -q

lint:             ## ruff check
	$(RUFF) check .

fmt:              ## ruff format
	$(RUFF) format .

clean:            ## remove the venv + python caches
	rm -rf $(VENV) .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# ----- docker stack (backend + generator + public site + comfyui) -----
bootstrap:        ## mint secrets + seed keys.json into .env
	./bootstrap.sh

build:            ## build all images
	docker compose build

init-perms:       ## run the perms one-shot by hand (compose runs it automatically on `up`)
	docker compose run --rm init-perms

up:               ## bring the whole stack up (detached; adds comfyui, needs the GPU box)
	docker compose up -d

up-publish:       ## bring up only the GPU-free publishing lane (no comfyui)
	docker compose up -d publish generate site

down:             ## stop the stack
	docker compose down

ps:               ## list services
	docker compose ps

logs:             ## follow logs
	docker compose logs -f

generate: init-perms  ## one-shot rebuild of the static site (normally the generate watcher does this on `up`)
	docker compose run --rm --no-deps generate go run ./cmd/censurado/generate -out /site -page-size 6

site:             ## (re)start the public server + the generate watcher
	docker compose up -d generate site

config:           ## validate the composed topology
	docker compose config -q && echo OK

stack-clean:      ## stop, DELETE the named volumes (site/gocache/comfyui) + any orphans
	docker compose down -v --remove-orphans

deploy:           ## build a fresh snapshot and push it to Cloudflare Pages (live site)
	./deploy/deploy-cdn.sh
