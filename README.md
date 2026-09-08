# amber

AMBER - A Mention Binds Every Record

Messy clinical text, hardened into structured data with the evidence still visible inside.
Clinical extraction pipelines and optional agents share verified quotes, evidence validation,
explicit outcomes, and OSS-compatible MLflow tracking. The goal is to reduce total expert effort
at a declared clinical quality target.

This is milestone M0 plus the first interface scaffold: the design, contract, toolchain, public
Python facade, CLI, and optional FastAPI application. M1 builds the evidence kernel; M2–M3 deliver
one fixed extraction baseline and a minimal correction experiment before broader agent work.
Clinical performance has not yet been measured. Start with `docs/`.

```
docs/
  00-goals-and-architecture.md   goals, principles, data model, agentic architecture, decisions
  01-state-of-the-art.md         field survey (living doc)
  02-v1-schemas-and-tools.md     the v1 contract: pydantic models, tools, agents, tables, OMOP, MLflow mapping
  03-strata-scaffold-notes.md    notes from a prior scaffold (reference only)
  04-roadmap.md                  milestones M0–M8
CLAUDE.md                        working agreement for the coding agent
pyproject.toml                   uv-managed; extras: agents, nlp, langextract, train, mlx, gpu, app, dagster, dev
src/amber/                      core library and public Amber facade
src/amber/services/             use cases shared by every interface
src/amber/cli/                  Click command groups
src/amber/api/                  optional FastAPI application and versioned routes
examples/synthetic_breast_pathology/   synthetic reports + labels (from Strata, Apache 2.0; see NOTICE)
experiments/                     one folder per dataset experiment: scripts, eval, local data, outputs
scripts/                         shared operational utilities
prompts/                         seed prompts to register in the MLflow prompt registry
tests/
```

## Toolchain

```sh
uv python install 3.11
uv sync --extra dev                       # core + dev
uv sync --extra dev --extra agents                     # provider/agent integrations
uv sync --extra dev --extra agents --extra nlp          # when targeted NLP is needed
uv sync --extra dev --extra agents --extra nlp --extra mlx     # on a Mac
uv sync --extra dev --extra agents --extra nlp --extra train --extra gpu   # Linux GPU box
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run amber info
uv run amber api serve                    # requires --extra app
```

`mlx` and `gpu` are declared as conflicting extras. Torch resolves from PyPI (CUDA builds on Linux, CPU/MPS on macOS). For a CPU-only Linux box add `--index https://download.pytorch.org/whl/cpu` or a `[[tool.uv.index]]` entry.

MLflow: run `uv run mlflow server --backend-store-uri sqlite:///mlflow.db --artifacts-destination ./mlartifacts` locally, or set `MLFLOW_TRACKING_URI` (and, on Databricks, `MLFLOW_REGISTRY_URI=databricks-uc`). See `.env.example`.

## Interfaces

Use the same application facade from Python, the CLI, or FastAPI:

```python
from amber import create_client

amber = create_client()
print(amber.info())
```

The API is created with `amber.api.create_app`; its health endpoints are `/health` and
`/api/v1/admin/health`. FastAPI and Uvicorn stay in the optional `app` dependency extra so
`import amber` remains lightweight.

## Where to begin

Use [`experiments/`](experiments/README.md) for runs of Amber on particular datasets.
[`experiments/coral/`](experiments/coral/README.md) contains the initial CORAL workspace,
including its audit scripts, draft adapter, and places for evaluation code and ignored local
data and outputs.

`docs/04-roadmap.md` M1–M3: define one task and its acceptance criteria, implement the evidence
kernel (`quote`, commit validation, and explicit outcomes), then compare a fixed extraction and
correction workflow with manual authoring. Measure correctness, evidence support, omissions,
automation coverage, expert time, and cost. Additional agents and backends require demonstrated
benefit on that protocol.
