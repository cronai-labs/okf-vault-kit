# One frontend, same verbs everywhere. CI calls these targets — `check` on the offline matrix,
# `test-qmd` on the end-to-end lane — so local and CI cannot drift.
# Tests run on the *minimum* supported Python: 3.13 hid a 3.11-only failure once already.
PY      ?= 3.11
MCP     ?= mcp<2
VERSION := $(shell cat VERSION)
DIST    ?= dist

.PHONY: help setup build test test-qmd lint fmt check run release clean

help:
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | expand -t22

setup: ## install the toolchain and show what is missing
	@command -v uv >/dev/null || { echo "install uv first: https://docs.astral.sh/uv/"; exit 1; }
	uv run --python $(PY) kit.py doctor

build: ## nothing to compile; prove the CLI imports and parses
	uv run --python $(PY) kit.py --version
	uv run --python $(PY) kit.py --help >/dev/null

test: ## the offline suite, on the minimum supported Python
	uv run --python $(PY) --with '$(MCP)' kit.py test

test-qmd: ## the end-to-end lane; needs qmd on PATH
	uv run --python $(PY) kit.py test -p "test_e2e_qmd.py"

lint: ## byte-compile everything and check the template vault
	uv run --python $(PY) python -m compileall -q kit.py kitlib.py kitgraph.py kitrecon.py kitproviders.py mcp tests
	uv run --python $(PY) kit.py validate --vault vault --strict

fmt: ## no formatter is configured; say so rather than pretend
	@echo "no formatter configured — style is reviewed, not enforced"

check: build lint test ## everything CI runs

run: ## the first thing a new user should see
	uv run --python $(PY) kit.py doctor

release: check ## build the distributable zip and its checksum
	@git diff --quiet || { echo "working tree is dirty; commit first"; exit 1; }
	@git diff --cached --quiet || { echo "staged changes are not in HEAD; commit first"; exit 1; }
	@tag=$$(git describe --exact-match --tags HEAD 2>/dev/null); \
	  [ "$$tag" = "v$(VERSION)" ] || { echo "HEAD is not tagged v$(VERSION) (found: $${tag:-no tag}); tag it first: git tag v$(VERSION)"; exit 1; }
	@rm -rf $(DIST) && mkdir -p $(DIST)
	@git archive --format=zip --prefix=okf-vault-kit-$(VERSION)/ \
	  -o $(DIST)/okf-vault-kit-$(VERSION).zip HEAD \
	  $$(git ls-files | grep -v '^\.github/')
	@cd $(DIST) && shasum -a 256 okf-vault-kit-$(VERSION).zip > okf-vault-kit-$(VERSION).zip.sha256
	@echo && ls -l $(DIST) && cat $(DIST)/okf-vault-kit-$(VERSION).zip.sha256

clean: ## remove everything the tooling generates
	rm -rf $(DIST) build .venv **/__pycache__ .kit vault/.kit
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
