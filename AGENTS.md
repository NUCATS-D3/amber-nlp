# Repository guidelines

## Start here

1. [docs/charter.md](docs/charter.md): what amber-nlp is and is not.
2. [docs/contracts.md](docs/contracts.md): what exists today.
3. [CLAUDE.md](CLAUDE.md): invariants and conventions.

`docs/history/` is pre-reset design input, not current plans.

## Task tracking

Project tasks, blockers, and decisions live in Beads (`.beads/`, prefix `amber-`).

- `bd ready` finds unblocked work.
- `bd show <id>` before starting.
- `bd update <id> --claim` to take it.
- `bd close <id> --reason=...` when done.

File follow-up work with `bd create` instead of Markdown TODO lists. Never use `bd edit`, which
is interactive. Keep issue text free of clinical text, quotes, or offsets. Experiment work is
tracked in lapidary.

## Layout and dependency direction

```
src/amber/schemas/        contract models (pure; no I/O)
src/amber/ids.py          canonical hashing and ULIDs
src/amber/grounding.py    exact grounding (the only Inclusion minting path today)
src/amber/graph.py        EvidenceGraph lifecycle; _graph_validation.py holds the checks
src/amber/cases.py        CaseResult creation/loading and outcome validation
src/amber/tools/          quote, mention, cite, commit_claim, no_claim: operations implementations call
src/amber/examples.py     Example and LabelRecord load boundary
src/amber/annotations.py  annotation-event replay, projections, Examples from events
src/amber/interfaces.py   protocols (Store, SourceResolver, PolicyGate, Exporter, Provider, Extractor, Scorer,
                          Labeler)
src/amber/scoring.py      score reports and the self-reference and move-on rules
src/amber/evaluation.py   tier-aware binary metric harness: gates, Wilson, bootstrap
src/amber/labeling.py     Labeler outputs, silver-only admission, import events from results
src/amber/extraction.py   shared extractor pieces: failed results, local provenance
src/amber/providers.py    provider requests and responses; the gated, budgeted call_provider
src/amber/reference/      synthetic reference implementations (not loaded by import amber)
src/amber/conformance/    conformance catalog, fixtures, checks, report, pytest plugin
src/amber/bundles/        bundle tables, manifest, hashing, loading; Parquet I/O (bundles extra)
src/amber/wire.py         JSON forms that are not plain model dumps (VariableSet)
src/amber/mlflow_ext/     optional MLflow plugins (tracking extra)
tests/                    synthetic, offline tests
scripts/                  core-install check and local MLflow launcher
```

- The core (`schemas`, `ids`, `grounding`, `graph`, `cases`, `tools`) imports only Pydantic,
  python-ulid, and the standard library.
- Plugins import the core, never the reverse.
- `import amber` must not load MLflow or any optional stack. Optional imports happen at the
  plugin boundary and raise an actionable install message when the dependency is missing.

## Commands

```sh
uv sync --extra dev --extra tracking --extra bundles   # full development environment
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv lock --check
bash scripts/mlflow_local.sh              # local MLflow server in .mlflow/
```

For the core-only installation check, see [tests/README.md](tests/README.md#ci).

## Coding and testing

- Four-space indentation, 100-character lines. Ruff owns import order and formatting.
- `snake_case` for modules and functions, `PascalCase` for models.
- Tests are `tests/test_*.py`, run offline on CPU with invented data only.
- Every invariant has an adversarial test covering:
  - invalid bounds, Unicode and CRLF offsets, repeated quotes;
  - unknown IDs, missing field evidence, cycles, source-free branches;
  - outcome consistency;
  - disallowed policy combinations.
- When adding an optional plugin, extend the fresh-process core import test and the core-only
  install check.

## Data safety

- Never commit PHI, real or credentialed clinical text (including CORAL), credentials, local
  databases, MLflow artifacts, or model weights.
- Datasets live outside repositories and are registered in lapidary; `data/` and
  `experiments/` stay ignored here as a safety net.
- `examples/` is synthetic only.
- The worktree may contain user changes: check `git status` first and preserve unrelated work.

## Commits

Imperative subject, with a body explaining why for non-trivial changes. Call out contract,
dependency, or wire-format changes, and their migrations.
