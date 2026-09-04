# amber

Messy clinical text, hardened into structured data with the evidence still visible inside. Grounded clinical information extraction: agents with a constrained tool belt, an evidence DAG behind every decision, and MLflow (open-source or Databricks) for prompts, models, traces, and evaluation.

This is milestone M0: the design, the contract, the toolchain, and an empty package skeleton. Nothing is implemented yet. Start with `docs/`.

```
docs/
  00-goals-and-architecture.md   goals, principles, data model, agentic architecture, decisions
  01-state-of-the-art.md         field survey (living doc)
  02-v1-schemas-and-tools.md     the v1 contract: pydantic models, tools, agents, tables, OMOP, MLflow mapping
  03-strata-scaffold-notes.md    notes from a prior scaffold (reference only)
  04-roadmap.md                  milestones M0–M8
CLAUDE.md                        working agreement for the coding agent
pyproject.toml                   uv-managed; extras: agents, nlp, langextract, train, mlx, gpu, app, dagster, dev
src/amber/                      package skeleton (module docstrings state responsibilities)
examples/synthetic_breast_pathology/   synthetic reports + labels (from Strata, Apache 2.0; see NOTICE)
prompts/                         seed prompts to register in the MLflow prompt registry
tests/
```

## Toolchain

```sh
uv python install 3.11
uv sync --extra dev                       # core + dev
uv sync --extra dev --extra agents --extra nlp          # M2–M5 work
uv sync --extra dev --extra agents --extra nlp --extra mlx     # on a Mac
uv sync --extra dev --extra agents --extra nlp --extra train --extra gpu   # Linux GPU box
uv run pytest
uv run ruff check . && uv run ruff format .
```

`mlx` and `gpu` are declared as conflicting extras. Torch resolves from PyPI (CUDA builds on Linux, CPU/MPS on macOS). For a CPU-only Linux box add `--index https://download.pytorch.org/whl/cpu` or a `[[tool.uv.index]]` entry.

MLflow: run `uv run mlflow server --backend-store-uri sqlite:///mlflow.db --artifacts-destination ./mlartifacts` locally, or set `MLFLOW_TRACKING_URI` (and, on Databricks, `MLFLOW_REGISTRY_URI=databricks-uc`). See `.env.example`.

## Where to begin

`docs/04-roadmap.md` M1: the data model and the grounding kernel (`quote`). Everything else depends on those two.
