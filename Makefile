VENV := .venv
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

# Toolchain: uv (https://docs.astral.sh/uv). It provisions the interpreter and the
# virtualenv without needing a system python venv package.

.PHONY: install test lint fmt clean

install:
	uv venv $(VENV)
	VIRTUAL_ENV=$(VENV) uv pip install -e ".[dev]"

test:
	$(PYTEST)

lint:
	$(RUFF) check .

fmt:
	$(RUFF) format .

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
