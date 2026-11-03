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
| `test_store.py` | Store protocol and `InMemoryStore`: splits, runs, append-only events, projections, Examples, resolver |
| `test_conformance_bundle.py` | Bundle conformance: WIR cases against written bundles, diagnostics mapped to cases, blocked cases, bundle reports, plugin bundle mode (needs bundles) |
| `test_conformance.py` | Conformance catalog, fixtures, checks against the references and broken stores, providers, extractors, scorers, labelers, review backends, and rules, report, pytest plugin |
| `test_labels.py` | `labels_only_view` for results and events, schema closedness, no source text in output |
| `test_wire.py` | JSON records: golden bytes and keys, schema versions, timestamps, budgets, VariableSet JSON |
| `test_bundles.py` | Bundles: Parquet round trips, structure, hashes, payload kinds, resolver loading, `x_` columns (needs pyarrow) |
| `test_export.py` | `StaticGate`, `LocalExporter` per-file gating and atomic writes, `amber-labels/0.1` bundles (needs pyarrow) |
| `test_providers.py` | Provider records, `ScriptedProvider`, `call_provider` gating, capabilities, parsing, retries, budgets |
| `test_extraction.py` | `DeterministicExtractor` end to end with a scripted provider: every outcome kind, budgets, batches, persistence |
| `test_scoring.py` | Score reports, self-reference refusal, move-on inclusion, reference scorers |
| `test_labeling.py` | Labelers: silver-only admission, import events that replay to the Example, ImportLabeler outcomes |
| `test_evaluation.py` | Binary metric harness: protocol arithmetic, P=0, Q=0, no answers, provisional and label-only gates, Wilson and bootstrap |
| `test_mlflow_evaluation.py` | MLflow validity scorer and gated report logging (needs tracking) |
| `test_derivations.py` | Derivation records: inputs within the inference, defaults only on rule derivations and empty roles, unset derivations omitted from JSON |
| `test_review.py` | Review backends: file round trips (single review, adjudication, unresolved, excluded, unreviewed), export gating, import refusals |
| `test_rules.py` | Rules: specs and registry, reduction (equal, conflicting, many, rejected), on_missing, conflict propagation, replay and tampering, events and bundles |
| `test_sources.py` | Source identity: structured rows hash their record, note IDs unchanged |
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
uv sync --locked --extra dev --extra tracking --extra bundles
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
