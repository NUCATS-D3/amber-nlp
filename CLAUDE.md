# CLAUDE.md — working agreement for this repository

Read, in order, before writing code: `docs/00-goals-and-architecture.md` (why), `docs/02-v1-schemas-and-tools.md` (the contract), `docs/04-roadmap.md` (what's next). `docs/01-state-of-the-art.md` is background on the field; `docs/03-strata-scaffold-notes.md` is a prior scaffold kept for reference only — do not copy its structure.

## Invariants (enforce in code and tests, not in comments)

1. Grounding by construction. `Inclusion` is minted only by the `quote` tool or by a human in the app. No other code path may construct one. A claim in any status other than `rejected` has at least one evidence edge; `commit_claim` refuses otherwise.
2. Offsets are into the immutable `Source.text`. Never normalize whitespace after a `source_id` is minted. `Mention.quote == text[start:end]` is validated on construction.
3. The evidence graph is a DAG. `commit_claim` checks acyclicity.
4. Provider zone × data sensitivity is checked before every model or EDW call and stamped into `Provenance`. Datasets carry sensitivity explicitly; it is never inferred.
5. MLflow: OSS API surface only (tracking, prompt registry, tracing, `mlflow.genai.evaluate` + `@scorer`, `optimize_prompts`, pyfunc models, run-context provider). No `databricks-*` imports; Databricks is a tracking URI and a registry naming convention behind one config key.
6. Model-agnostic. Nothing in `src/amber` may assume a model family, a chat template, or a device. Backends declare capabilities; callers check them.
7. `Example` is the single annotated-data format. Demonstrations, optimizer data, fine-tuning JSONL, and evaluation records are exports from it, never separate sources of truth.

## Conventions

- Python 3.11+, `uv` for everything (`uv sync --extra dev`, `uv run pytest`, `uv run ruff check`). Add dependencies to the right extra in `pyproject.toml`; core stays model- and GPU-free.
- Pydantic v2 everywhere; `model_config = ConfigDict(extra="forbid")` on data-model classes.
- One module per responsibility as laid out in `src/amber/` docstrings; keep the deterministic tools importable without the agent runtime, and the agent runtime importable without any model weights.
- Tests: unit tests must run on CPU with no network; mark `slow`, `gpu`, `mlx` as appropriate. Every invariant above has a test that tries to violate it.
- Prompts live in the MLflow prompt registry; a checked-in YAML under `prompts/` is the seed, and the code loads by version, never by inline string.
- Log to MLflow through `amber.mlflow_ext` helpers so runs, traces, and tags stay consistent; no ad hoc `mlflow.log_*` scattered through agents.
- Commit messages: imperative subject, body explains the why; reference the milestone (M1–M8) from the roadmap.

## What not to do

- Don't add an orchestration framework beyond PydanticAI without a written reason in `docs/`.
- Don't hard-code column names (`Accession Number`, `Report Text`) — the data contract is `Source`.
- Don't call `transformers` ≥ 5 with TRL ≤ 0.24 (see pin comment); don't route structured output through Ollama's MLX engine (it ignores JSON schemas).
- Don't put PHI, real notes, or credentials in the repo. `examples/` is synthetic only.
