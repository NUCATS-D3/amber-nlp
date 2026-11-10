# Developer checks. `make check` is the definition of done (AGENTS.md).

UV_EXTRAS := --extra dev --extra tracking --extra bundles
RUN := uv run --no-sync
PYTEST := $(RUN) pytest --strict-markers -ra

.DEFAULT_GOAL := help
.PHONY: help sync hooks lint format typecheck imports lock phi-scan test-fast test check ratchet quality-fast deps quality diff-coverage quality-rebaseline mutation

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
	$(PYTEST) -n 4 -m "not slow"

test: ## Every test, including slow ones
	$(PYTEST)

check: sync lint typecheck imports lock phi-scan test-fast quality diff-coverage ## Definition of done
	@echo "check ok"

# New code is measured from the merge base with RATCHET_BASE; set it without an origin/main.
RATCHET_BASE ?= origin/main

ratchet: ## Violation counts against quality/baseline.json; new code must be clean
	$(RUN) python quality/ratchet.py --base $(RATCHET_BASE)

quality-fast: lint imports ratchet ## Ruff, import contracts, and the ratchet (seconds)

# deptry scans src/ only (scripts/ and quality/ are dev tooling); DEP002=amber ignores the
# reference extra's self-reference, amber[bundles].
deps: ## Declared dependencies match src imports (deptry); print dependency changes
	$(RUN) deptry src --optional-dependencies-dev-groups dev --known-first-party amber --per-rule-ignores DEP002=amber
	$(RUN) python quality/dep_gate.py --base $(RATCHET_BASE)

quality: quality-fast deps ## Every cleanliness check; slower ones join here (amber-7q0.9)

# Coverage reads only the pinned, empty config, so pyproject settings cannot omit files; the
# sys.monitoring tracer keeps the run near 60 s. Not in the stop hook. PYTHONPATH=src makes
# coverage measure src/amber even where amber is installed non-editable (CI); without it,
# diff-cover finds no data for the diff and passes, so the recipe refuses such a report. The
# conformance plugin (a pytest11 entry point) would import amber before coverage starts, leaving
# top-level lines unmeasured, so this run leaves it out; pytester sessions still load it.
COVERAGE := .coverage-report
diff-coverage: ## Changed lines in src are at least 90% covered (fast suite again, with coverage)
	PYTHONPATH=src COVERAGE_CORE=sysmon COVERAGE_FILE=$(COVERAGE)/data $(PYTEST) -n 4 -m "not slow" -p no:amber_conformance --cov=amber --cov-config=quality/pinned-config.toml --cov-report=xml:$(COVERAGE)/coverage.xml --cov-report=
	@grep -q 'filename="src/amber/' $(COVERAGE)/coverage.xml || { echo "diff-coverage: the report does not measure src/amber"; exit 1; }
	set -o pipefail; $(RUN) coverage report --data-file=$(COVERAGE)/data --rcfile=quality/pinned-config.toml --format=total | sed 's/^/total coverage (reported, not gated): /; s/$$/%/'
	$(RUN) diff-cover $(COVERAGE)/coverage.xml --compare-branch=$(RATCHET_BASE) --include-untracked --fail-under=90

quality-rebaseline: ## HUMAN ONLY: rewrite quality/baseline.json (ALLOW_ORACLE_CHANGE=1; only tightens)
	$(RUN) python quality/ratchet.py --write-baseline

mutation: ## Mutation testing of the validation core (about 25 min); ARGS="--module grounding"
	$(RUN) python scripts/mutation_test.py $(ARGS)
