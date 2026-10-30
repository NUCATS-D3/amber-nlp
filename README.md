# amber-nlp

Contracts, validation, and interfaces for evidence-grounded clinical information extraction,
plus MLflow plugins for evaluation, human review, and deployment.

amber-nlp defines what an extraction result is: sources, claims, evidence graphs, and explicit
outcomes. It also checks that results satisfy that contract. Implementations live elsewhere:

- **lapidary**: experiments, extractors, and providers;
- **copal**: rules;
- **loupe**: human review.

See the [charter](docs/charter.md).

## Status

The contract and validation core is implemented and tested:

- exact grounding;
- evidence-graph validation (acyclic, source-backed, with field-level citations);
- claim commits;
- explicit case outcomes and `CaseResult`.

The policy gate, `Mention`, `Example`, the interfaces, the conformance kit, and most MLflow
plugins are planned. See [contracts](docs/contracts.md) and `bd ready`.

## Use

```python
from amber.graph import EvidenceGraph
from amber.tools.commit import commit_claim
from amber.tools.quote import quote
```

[scripts/check_core_install.py](scripts/check_core_install.py) runs a complete invented-data
walkthrough: ground a quote, commit a claim, record outcomes, and build and reload a
`CaseResult`.

## Development

```sh
uv sync --extra dev --extra tracking
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Python 3.12 only. The core depends on Pydantic and python-ulid; the `tracking` extra adds
MLflow 3.17.x. See [AGENTS.md](AGENTS.md) and the [test guide](tests/README.md).

The pre-reset project (FastAPI app, CLI, CORAL experiment, M1–M8 roadmap) is tagged `pre-reset`
and summarized in [docs/history](docs/history/README.md).
