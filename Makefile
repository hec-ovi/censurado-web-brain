# Censurado newsroom: the agentic CLI + prompt recipe (this repo) and the docker stack
# that runs the backend, generator, public site, and comfyui (the sibling code repos).
# Two lanes: the python package (install/test/lint the CLI + sweeps) and the compose
# stack (start/up/deploy). Run `make install` once, then `make test`; run `make start`
# once, then `make up`.
#
# `make` is optional: every stack target below just delegates to ./run.sh, which needs
# only bash + docker (no make, no apt). `./run.sh start`, `./run.sh up`, etc. do the same.

VENV := .venv
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

# Pass ARGS=--console to any `up*` target to stay in the FOREGROUND with the console
# attached, e.g. `make up ARGS=--console` (or, without make, `./run.sh up --console`).
ARGS ?=

.PHONY: install test lint fmt clean \
        bootstrap start build up up-gpu comfyui down ps logs generate init-perms config stack-clean serve deploy

# ----- python package (the CLI + the newsroom sweeps) -----
# Toolchain: uv (https://docs.astral.sh/uv). It provisions the interpreter and the
# virtualenv without needing a system python venv package.
install:          ## create the venv and install the package + dev tools
	uv venv $(VENV)
	VIRTUAL_ENV=$(VENV) uv pip install -e ".[dev]"

test:             ## run the whole suite (CLI + sweeps + compose wiring + prompt drift)
	$(PYTEST) tests

lint:             ## ruff check
	$(RUFF) check .

fmt:              ## ruff format
	$(RUFF) format .

clean:            ## remove the venv + python caches
	rm -rf $(VENV) .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# ----- docker stack: thin mirror of ./run.sh (the no-make, no-apt entry point) -----
bootstrap:        ## mint secrets + seed keys.json into .env (rotates the keys; run by hand)
	./run.sh bootstrap

start:            ## ONE command online: create .env + mint secrets if missing, then bring the fast lane up
	./run.sh start

build:            ## build all images (fast lane; add `--profile comfyui` for the GPU image)
	./run.sh build

init-perms:       ## run the perms one-shot by hand (compose runs it automatically on `up`)
	./run.sh init-perms

up:               ## bring up the FAST lane (publish/generate/site, no comfyui); ARGS=--console for foreground
	./run.sh up $(ARGS)

up-gpu:           ## the whole stack, adding comfyui (heavy, needs the GPU box); the fast lane comes up first
	./run.sh up-gpu $(ARGS)

comfyui:          ## start ONLY comfyui, e.g. after `make up` once the fast lane is already serving
	./run.sh comfyui $(ARGS)

down:             ## stop the stack
	./run.sh down

ps:               ## list services
	./run.sh ps

logs:             ## follow logs
	./run.sh logs

generate:         ## one-shot rebuild of the static site (normally the generate watcher does this on `up`)
	./run.sh generate

config:           ## validate the composed topology
	./run.sh config

stack-clean:      ## stop, DELETE the named volumes (site/gocache/comfyui) + any orphans
	./run.sh stack-clean

serve:            ## the 24/7 loop: stack + telegram bridge + agent fallback chain (needs node)
	./run.sh serve

deploy:           ## build a fresh snapshot and push it to Cloudflare Pages (live site)
	./run.sh deploy
