# scriptorium — common dev targets.
#
# Everything runs in a container, so a checkout needs nothing installed but
# Docker. Run `make help` for the list.

IMAGE   ?= scriptorium:dev
PY      ?= python:3.12-slim
# Run the repo inside $(PY) with the dev dependencies installed.
INPY     = docker run --rm -v "$(CURDIR)":/w -w /w $(PY) sh -c \
             'pip install -q -r requirements-dev.txt && $(1)'

SHELL := /bin/bash
# -u catches typos in variable names, -o pipefail stops a failing command in a
# pipe being masked by the one after it. -e is omitted so multi-command
# recipes still report at the end.
.SHELLFLAGS := -u -o pipefail -c

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: test
test: ## Run the test suite.
	@$(call INPY,python -m pytest tests/ -q)

.PHONY: lint
lint: ## Check formatting and lint rules.
	@$(call INPY,ruff check .)

.PHONY: fmt
fmt: ## Apply ruff's formatting.
	@$(call INPY,ruff format .)

.PHONY: check
check: lint test ## Lint and test, the same gate CI applies.

.PHONY: image
image: ## Build the container image.
	docker build -t $(IMAGE) .

.PHONY: pins
pins: image ## Verify requirements.txt still matches what the image installs.
	@docker run --rm --entrypoint pip $(IMAGE) freeze | sort > /tmp/scriptorium-freeze
	@grep -v '^#' requirements.txt | grep -v '^$$' | sort > /tmp/scriptorium-pins
	@diff -u /tmp/scriptorium-pins /tmp/scriptorium-freeze && echo "pins are exact"

.PHONY: langs
langs: ## Regenerate the ISO 639 table from the registry.
	python3 scripts/gen_langs.py > scriptorium/_langtable.py

.PHONY: audit
audit: ## Report known vulnerabilities in the pinned dependencies.
	@$(call INPY,pip install -q pip-audit && pip-audit -r requirements.txt || true)

.PHONY: run
run: image ## Run against a Stash at $$STASH_URL, draining the queue once.
	docker run --rm --network host \
	  -e STASH_URL="$${STASH_URL:-http://127.0.0.1:9999}" \
	  -e STASH_API_KEY="$${STASH_API_KEY:-}" \
	  -e RUN_ONCE=1 -e DRY_RUN=1 \
	  -v "$${MEDIA:-/data}":/data $(IMAGE)

.PHONY: clean
clean: ## Remove caches.
	rm -rf .pytest_cache .ruff_cache __pycache__ scriptorium/__pycache__ tests/__pycache__
