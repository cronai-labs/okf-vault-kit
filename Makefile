# One frontend, same verbs everywhere. CI calls these targets — `check` on the offline matrix,
# `test-qmd` on the end-to-end lane — so local and CI cannot drift.
# Tests run on the *minimum* supported Python: 3.13 hid a 3.11-only failure once already.
PY      ?= 3.11
MCP     ?= mcp>=2,<3
# Pinned on purpose: a linter that moves on its own turns an unrelated CI run red, and the rule set
# in pyproject.toml is written against one version. The version moves in a commit, never on a whim.
RUFF    ?= ruff@0.16.7
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

lint: ## ruff, a byte-compile on the floor Python, and the template vault
	uvx $(RUFF) check .
	uv run --python $(PY) python -m compileall -q kit.py kitlib.py kitgraph.py kitrecon.py kitproviders.py mcp testkit tests
	uv run --python $(PY) kit.py validate --vault vault --strict

fmt: ## no formatter is configured; say so rather than pretend
	@echo "no formatter configured — ruff lints, it does not reformat; style is reviewed"

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

# `--tag` names the version being generated. Without it git-cliff sees an untagged tree and
# writes `[Unreleased]` -- and because the tag is created *after* this commit, the released
# changelog would permanently describe itself as unreleased. That is how v0.1.0 shipped. (#43)
#
# It only means that for a version that has not shipped yet, hence the refusal: with v$(VERSION)
# already tagged, git-cliff labels the commits *since* that tag with it too and emits a second
# `## [$(VERSION)]` section above the real one. Bump VERSION first.
changelog: ## regenerate CHANGELOG.md for the version in VERSION
	@git rev-parse -q --verify "refs/tags/v$(VERSION)" >/dev/null 2>&1 && { \
	  echo "v$(VERSION) is already tagged -- bump VERSION before regenerating the changelog,"; \
	  echo "or git-cliff will file the commits since that tag under v$(VERSION) as well."; \
	  exit 1; } || true
	uvx git-cliff --config cliff.toml --tag v$(VERSION) --output CHANGELOG.md

next-version: ## what the next tag would be, from the commits since the last one
	@next=$$(uvx git-cliff --config cliff.toml --bumped-version 2>/dev/null); \
	  if [ -z "$$(git tag -l 'v*')" ]; then \
	    echo "$$next"; \
	    echo "(no tag exists yet, so this is cliff.toml's initial_tag -- the first release, not a bump)"; \
	  elif [ "$${next#v}" = "$(VERSION)" ]; then \
	    echo "no releasable changes since v$(VERSION) -- only non-releasing commit types have landed."; \
	    echo "(git-cliff returns the current version when nothing bumps it; that is not a next tag.)"; \
	  else \
	    echo "$$next"; \
	  fi

clean: ## remove everything the tooling generates
	rm -rf $(DIST) build .venv **/__pycache__ .kit vault/.kit .ruff_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
