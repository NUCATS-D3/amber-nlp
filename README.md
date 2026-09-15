# AMBER - A Mention Binds Every Record

Messy clinical text, hardened into structured data with the evidence still visible inside.
Clinical extraction pipelines and optional agents share verified quotes, evidence validation,
explicit outcomes, and OSS-compatible MLflow tracking. The goal is to reduce total expert effort
at a declared clinical quality target.

Start with the [documentation index](docs/README.md) and its
[current implementation summary](docs/README.md#current-implementation) to distinguish working
capabilities from planned behavior. The [M1–M3 roadmap](docs/04-roadmap.md) leads from the task
protocol and evidence kernel to a fixed extraction baseline and a minimal correction experiment.

```
docs/README.md                   documentation index, reading order, and implementation status
CLAUDE.md                        working agreement for the coding agent
pyproject.toml                   uv-managed dependencies and optional extras
src/amber/                      core library and public Amber facade
src/amber/services/             use cases shared by every interface
src/amber/cli/                  Click command groups
src/amber/api/                  optional FastAPI application and versioned routes
examples/synthetic_breast_pathology/   synthetic reports + labels (from Strata, Apache 2.0; see NOTICE)
experiments/                     one folder per dataset experiment: scripts, eval, local data, outputs
scripts/                         shared operational utilities
prompts/                         planned seed prompts for the MLflow prompt registry
tests/
```

## Toolchain

```sh
uv python install 3.11
uv sync --no-default-groups               # minimal core, without development dependencies
uv sync --extra dev                       # core + dev
uv sync --extra dev --extra app --extra tracking       # full offline test/type-check setup
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

Core requires only Pydantic, pydantic-settings, python-ulid, and Click. Optional extras can be
combined with repeated `--extra` flags:

| Extra | Dependencies / purpose |
|---|---|
| `storage` | pandas, PyArrow, DuckDB for planned persistence and table exports |
| `evaluation` | NumPy, SciPy, scikit-learn for planned metrics and analysis |
| `tracking` | OSS MLflow and PyYAML for tracking and planned prompt assets |
| `nlp` | Optional NLP stack, including RapidFuzz; core grounding remains exact-only |

Extras install dependencies; they do not implement the planned features. Some stacks also bring
these libraries transitively (for example, MLflow includes analytics dependencies). Existing
agent, training, hardware, app, and notebook extras remain available.

The default `dev` dependency group installs pytest, HTTPX, and marimo; the separate `dev` extra
adds lint/type-check and other development tools. Use `--no-default-groups` with both `uv sync`
and `uv run` for a minimal runtime. For example: `uv run --no-default-groups amber info`.

MLflow is opt-in: use `uv sync --extra tracking` or run `bash scripts/mlflow_local.sh` from the
repository root. The launcher selects `tracking` and keeps its database/artifacts in `.mlflow/`.
For a remote server, set `MLFLOW_TRACKING_URI` (and, on Databricks,
`MLFLOW_REGISTRY_URI=databricks-uc`); select `tracking` for MLflow commands and integrations.
See `.env.example` for configuration examples, not implemented institutional integrations.

## Tests and CI

See the [test guide](tests/README.md) for focused commands and settings isolation. GitHub Actions
checks a minimal installed package and the full synthetic suite on Python 3.11/3.12, with HTTP
and tracking extras installed. Ruff, formatting, mypy, and lockfile checks run without clinical
data, models, or external services; dependency installation requires network access.

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

For implementation work, follow the [documentation reading order](docs/README.md#reading-order-and-authority)
and [roadmap](docs/04-roadmap.md). The index links the current-progression plan and separates the
implemented kernel foundations from the remaining M1 work. Additional agents and backends require
demonstrated benefit on the clinical protocol.
