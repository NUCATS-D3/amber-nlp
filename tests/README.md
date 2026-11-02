# Tests and CI

The suite is flat and grouped by the boundary under test. All inputs are invented and offline.

| File | Covers |
|---|---|
| `test_interfaces.py` | Core imports load no optional stack; package version |
| `test_invariants.py` | Source identity and immutability, IDs, exact grounding, minting rejection |
| `test_mentions.py` | `mention()` and `cite()`: span grounding, fragments, sections, mention IDs, minting rejection |
| `test_mention_readings.py` | Readings and attributes; mention registration, payload revalidation, results |
| `test_variables.py` | Variable sets: validation, identity, `Task` sugar, multi-variable claims, dependencies |
| `test_mention_claims.py` | Mention-level claims: readings, span citation, required attributes, sections |
| `test_variable_outcomes.py` | Per-variable outcomes, `case_status`, case-level mirroring, variable-set binding |
| `test_examples.py` | Examples and label records: tiers and claim status, references, identity, split, mentions |
| `test_annotations.py` | Events, replay determinism and prefixes, blind streams, transitions, tiers, move-on, Examples from events |
| `test_answer_schemas.py` | Frozen, closed, strict `AnswerModel` subclasses |
| `test_claim_schemas.py` | Task, claim, inference, and edge records; canonical IDs; immutability; wire shapes |
| `test_graph_validation.py` | Full graph, context, and source validation; field citations; cycles; deep chains |
| `test_graph.py` | Graph state, atomic registration, traversal, snapshots, and restore |
| `test_commit.py` | Proposed answers, field citations, atomic failure, supporting-claim chains |
| `test_case_schemas.py` | Case membership and outcome consistency |
| `test_cases.py` | Final vs. supporting claims; result creation and load revalidation |
| `test_outcomes.py` | `no_claim` validation and immutable graph state |
| `test_tracking.py` | MLflow run-context entry point, missing-dependency handling, local launcher |

`_claim_helpers.py` defines `SyntheticFindingAnswer`, a neutral single-boolean answer used to
exercise the task-agnostic kernel.

## Local checks

```sh
uv sync --locked --extra dev --extra tracking
uv run --no-sync pytest --strict-markers -ra -m "not slow"
uv run --no-sync ruff check . && uv run --no-sync ruff format --check .
uv run --no-sync mypy src
uv lock --check
```

Without `tracking`, the MLflow entry-point test skips. A skipped plugin test is not evidence that
the plugin works.

## CI

[GitHub Actions](../.github/workflows/ci.yml) runs on Python 3.12 from `uv.lock`, with no
credentials or external services:

- **Core install.** A non-editable install with no extras, checked with
  [check_core_install.py](../scripts/check_core_install.py) under `python -I`. This verifies the
  absence of optional stacks, Unicode/CRLF grounding, commits, restore, and case results.
- **Full suite.** `dev` and `tracking` are installed; MLflow import is required before tests;
  then the suite, Ruff, formatting, and mypy run.

To reproduce the core check without replacing your environment:

```sh
core_check_env="$(mktemp -d)/venv"
UV_PROJECT_ENVIRONMENT="$core_check_env" uv sync --locked --no-default-groups --no-editable
UV_PROJECT_ENVIRONMENT="$core_check_env" uv run --no-sync python -I scripts/check_core_install.py
```
