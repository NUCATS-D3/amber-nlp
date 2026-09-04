# Repository Guidelines

## Start Here

Before changing architecture or domain behavior, read these sources in order:

1. `docs/00-goals-and-architecture.md` — goals, boundaries, and design decisions.
2. `docs/02-v1-schemas-and-tools.md` — the v1 domain and tool contract.
3. `docs/04-roadmap.md` — milestone order and current scope.
4. `CLAUDE.md` — non-negotiable invariants and repository conventions.

`docs/03-strata-scaffold-notes.md` is historical reference only. Do not copy its structure.
When documents disagree, preserve the invariants in `CLAUDE.md` and the contract in
`docs/02-v1-schemas-and-tools.md`, then reconcile the stale document in the same change.

The repository is currently M0 plus a small interface scaffold. The Python facade, `info` CLI,
FastAPI application factory, health/info routes, settings, and their smoke tests are implemented.
Most schema, grounding, tool, agent, backend, storage, export, training, and evaluation modules
still contain responsibility docstrings rather than working behavior. Do not treat a module's
presence as evidence that its milestone is complete; check its implementation and tests.

CORAL v1.0 is the first evaluation dataset. Its local copy is credentialed, deidentified clinical
data under a PhysioNet data-use agreement, not a synthetic repository fixture. Follow the CORAL
rules below whenever work touches ingestion, schemas, grounding, splitting, or evaluation.

## Project Structure and Dependency Direction

Amber uses a `src` layout. Production code is under `src/amber/`, tests are under `tests/`, and
only synthetic fixtures belong in `examples/`. Seed prompt definitions live in `prompts/`, while
thin operational utilities live in `scripts/`.

Keep dependencies pointing inward:

- `schemas/`, `ids.py`, `grounding.py`, `graph.py`, and `policy.py` are the domain kernel. They must
  not import interfaces, services, agent runtimes, databases, or model implementations.
- `tools/` contains deterministic, Pydantic-typed functions shared by cascades and agents. It must
  remain importable without PydanticAI, model weights, or hardware-specific packages.
- `services/` owns use cases and coordinates domain objects, stores, and providers.
- `client.py` is the stable Python facade. CLI and API adapters call the facade/services instead
  of duplicating business logic.
- `cli/` and `api/` are delivery adapters. Keep HTTP and Click models at those boundaries.
- `agents/` and `backends/` contain optional runtime integrations behind capability-checked
  interfaces. `store/`, `export/`, and `mlflow_ext/` are outward adapters.

Keep `import amber` lightweight: it must not import FastAPI, PydanticAI, Transformers, MLX, or
other optional stacks. Import optional dependencies at their boundary and return an actionable
installation message when they are absent. Resolve environment configuration in `config.py` at
process boundaries and pass explicit settings into testable application code.

## Domain and Data-Model Rules

Enforce the repository invariants in code and adversarial tests:

- An `Inclusion` may be minted only by the quote/grounding path or an explicit human annotation
  path. Verified offsets always index the exact immutable `Source.text`; never normalize that text
  after computing `source_id`.
- `Mention.quote` must equal `Source.text[start:end]`. Use end-exclusive character offsets.
- Every non-rejected claim has evidence, unknown evidence IDs are rejected, field-level evidence
  policies are honored, and the evidence graph remains acyclic.
- Check provider zone against declared data sensitivity before every model or EDW call, then stamp
  both values into provenance. Never infer sensitivity from content.
- `Example` is the single annotated-data source of truth; demonstrations, evaluation records, and
  fine-tuning files are exports from it.
- Use Pydantic v2. Domain and interface data models use `ConfigDict(extra="forbid")`; use frozen
  models for value objects where mutation would violate provenance or identity. Settings are the
  deliberate boundary exception and may ignore unrelated environment fields.
- Keep model family, device, endpoint, chat-template, and quantization assumptions out of domain
  and service code. Callers must inspect backend capabilities.
- Use only the OSS MLflow API surface and centralize MLflow behavior in `amber.mlflow_ext`; treat
  Databricks as configuration, never as an imported SDK dependency.

Prompts are versioned registry assets seeded from checked-in YAML, not inline strings in agents.
Add dependencies to the narrowest applicable extra in `pyproject.toml`; core must remain free of
agent runtimes, model frameworks, web frameworks, and GPU requirements. Do not install `mlx` and
`gpu` together.

## Build and Development Commands

Use `uv` for Python environments and commands. The supported Python range is `>=3.11,<3.13`, with
3.11 as the lint/type-check target.

```sh
uv sync --extra dev
uv sync --extra dev --extra app             # API work
uv sync --extra dev --extra agents --extra nlp
uv run pytest
uv run pytest tests/test_interfaces.py
uv run pytest --cov=amber
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run amber info
uv run amber api serve                       # requires the app extra
```

Use `uv run ruff format <paths>` only when intentionally formatting files; use `--check` for
validation. Start with focused tests and changed-file linting, then run the relevant full checks
before handoff. API changes must be tested with the `app` extra installed so optional HTTP tests
do not pass merely by being skipped. Follow the local MLflow server command in `README.md` when a
task genuinely needs MLflow integration.

## Coding and Testing Conventions

Use four-space indentation, a 100-character line limit, `snake_case` for modules/functions/data,
and `PascalCase` for classes and Pydantic models. Ruff owns import ordering and formatting. Prefer
small modules with one responsibility and public APIs with explicit typed inputs and outputs.

Write pytest tests as `tests/test_*.py` with `test_*` functions. Default tests must run offline on
CPU using synthetic data. Mark tests that load models or require special hardware with `slow`,
`gpu`, or `mlx`. Do not hide required dependency failures with unconditional skips; use skips only
for genuinely optional interfaces and run those tests explicitly when changing the interface.
Every domain invariant needs a test that attempts to violate it. When adding an optional adapter,
also test that importing core Amber does not import that adapter's dependency.

## CORAL Dataset

The local documentation is
`data/CORAL_ expert-Curated medical Oncology Reports to Advance Language model inference v1.0.pdf`.
The canonical dataset citation is CORAL v1.0, DOI `10.13026/v69y-xa45`. Consult the documentation
and `data/coral/annotated/annotation.conf` before interpreting labels.

- `data/coral/annotated/` contains the expert-labeled gold set: 20 breast-cancer and 20 pancreatic-
  cancer progress notes in BRAT `.txt`/`.ann` pairs, plus `subject-info.csv` and BRAT configuration.
- `data/coral/unannotated/` contains 200 different notes and GPT-4-generated outputs. Those outputs
  are pseudo-labels, not expert gold; never mix them into the held-out gold evaluation silently.
- Split at the `coral_idx`/patient or document level before deriving examples. Never split mentions,
  relations, chunks, or generated exports independently, because that leaks source text.
- Treat each `.txt` file as immutable and authoritative. Read it without newline translation and
  interpret BRAT offsets as end-exclusive character offsets into that exact text. Do not clean,
  normalize, or overwrite raw notes or `.ann` files.
- CORAL includes discontinuous spans, attributes, directed and symmetric relations, section-skip
  annotations, and post-annotation redaction artifacts. Keep each reconciliation as an explicit,
  named, tested adapter policy; do not bury corpus-specific exceptions in generic domain models.
- A BRAT annotation's copied surface string may disagree with `text[start:end]`. Preserve the raw
  annotation for audit, but Amber mentions and inclusions must quote the source text. Classify and
  report mismatches rather than silently repairing offsets or text.
- Keep CORAL-specific parsing and mapping separate: `scripts/coral_ingest.py` audits the BRAT
  records, while `scripts/coral_adapter.py` contains draft mapping decisions that depend on the M1
  schemas. Move reusable code into `src/amber/` with synthetic tests as the adapter stabilizes.
- Declare CORAL inputs as `Sensitivity.deidentified`; deidentification does not make them public or
  synthetic. The provider policy gate still applies to every model call.

Repository and CI tests must not require access to CORAL. Build invented BRAT fixtures that cover
its format and edge cases without copying clinical text. Run corpus-dependent checks locally as a
separate integration step and report the dataset version, manifest/hash, split, and adapter policy
version with evaluation results.

## Data Safety and Working-Tree Hygiene

Never commit PHI, real clinical notes, credentials, `.env`, local databases, MLflow artifacts, or
model weights. `examples/` is synthetic-only. `data/` is for ignored local datasets; keep only its
placeholder under version control. CORAL access requires credentialing, the PhysioNet Credentialed
Health Data License 1.5.0, its data-use agreement, and required training. Do not redistribute its
raw notes, annotations, demographics, or reconstructable derivatives. Treat source text, offsets,
annotations, prompt versions, and provenance as coupled data: changes to one may require migrations
or regenerated derived outputs.

The worktree may contain user changes. Inspect `git status` before editing, preserve unrelated
work, and do not reformat or clean files outside the task. Keep scripts thin; reusable behavior
belongs in `src/amber/` and must be covered by tests.

## Commits and Pull Requests

Use an imperative commit subject and include the roadmap milestone when applicable, for example
`M1: enforce mention offsets`. Explain why in the body for non-trivial changes. Pull requests must
describe behavior, list validation commands and results, link the relevant issue or milestone, and
call out schema, prompt, dependency, configuration, data-format, or migration changes.
