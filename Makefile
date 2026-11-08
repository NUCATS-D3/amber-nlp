# Developer checks. `make check` is the definition of done (AGENTS.md).

UV_EXTRAS := --extra dev --extra tracking --extra bundles
RUN := uv run --no-sync
PYTEST := $(RUN) pytest --strict-markers -ra

.DEFAULT_GOAL := help
.PHONY: help sync hooks lint format typecheck imports lock test-fast test check mutation

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

test-fast: ## Offline suite without slow tests (about 40 s)
	$(PYTEST) -m "not slow"

test: ## Every test, including slow ones
	$(PYTEST)

check: sync lint typecheck imports lock test-fast ## Definition of done
	@echo "check ok"

mutation: ## Mutation testing of the validation core (about 25 min); ARGS="--module grounding"
	$(RUN) python scripts/mutation_test.py $(ARGS)
