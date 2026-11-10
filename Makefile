# Developer checks. `make check` is the definition of done (AGENTS.md).

UV_EXTRAS := --extra dev --extra tracking --extra bundles
RUN := uv run --no-sync
PYTEST := $(RUN) pytest --strict-markers -ra

.DEFAULT_GOAL := help
.PHONY: help sync hooks lint format typecheck imports lock phi-scan test-fast test check ratchet quality-fast quality quality-rebaseline mutation

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

sync: ## Install the locked development environment
	uv sync --locked $(UV_EXTRAS)

hooks: ## Install the pre-commit hooks, including the oracle guard
	$(RUN) pre-commit install

lint: ## Ruff lint and format check
	$(RUN) ruff check .
	$(RUN) ruff format --check .

format: ## Apply Ruff fixes and formatting
	$(RUN) ruff check --fix .
	$(RUN) ruff format .

typecheck: ## mypy over src
	$(RUN) mypy src

imports: ## Dependency direction (import-linter)
	$(RUN) lint-imports

lock: ## uv.lock matches pyproject.toml
	uv lock --check

phi-scan: ## Identifier-shaped text in tests, examples, conformance, docs
	python3 scripts/phi_scan.py

test-fast: ## Offline suite without slow tests, on parallel workers (about 40 s)
	$(PYTEST) -n auto -m "not slow"

test: ## Every test, including slow ones
	$(PYTEST)

check: sync lint typecheck imports lock phi-scan test-fast quality ## Definition of done
	@echo "check ok"

# New code is measured from the merge base with RATCHET_BASE; set it without an origin/main.
RATCHET_BASE ?= origin/main

ratchet: ## Violation counts against quality/baseline.json; new code must be clean
	$(RUN) python quality/ratchet.py --base $(RATCHET_BASE)

quality-fast: lint imports ratchet ## Ruff, import contracts, and the ratchet (seconds)

quality: quality-fast ## Every cleanliness check; slower ones join here (amber-7q0.6-.9)

quality-rebaseline: ## HUMAN ONLY: rewrite quality/baseline.json (ALLOW_ORACLE_CHANGE=1; only tightens)
	$(RUN) python quality/ratchet.py --write-baseline

mutation: ## Mutation testing of the validation core (about 25 min); ARGS="--module grounding"
	$(RUN) python scripts/mutation_test.py $(ARGS)
