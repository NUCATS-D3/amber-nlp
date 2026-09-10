# Repository Guidelines

## Start Here

Before changing architecture or domain behavior, read these sources in order:

1. `docs/00-goals-and-architecture.md` — goals, boundaries, and design decisions.
2. `docs/02-v1-schemas-and-tools.md` — the v1 domain and tool contract.
3. `docs/04-roadmap.md` — milestone order and current scope.
4. `CLAUDE.md` — non-negotiable invariants and repository conventions.

`docs/03-strata-scaffold-notes.md` is historical reference only. Do not copy its structure.
`docs/01-state-of-the-art.md` is background research, not the implementation contract.
When documents disagree, preserve the invariants in `CLAUDE.md` and the contract in
`docs/02-v1-schemas-and-tools.md`, then reconcile the stale document in the same change.

## Current Implementation and Scope

The repository is M0 plus a small interface scaffold. Check implementations and tests before
claiming a milestone or feature is complete; much of the documentation describes planned behavior.

- Implemented: `Amber`, `create_client`, `SystemInfo`, environment settings, `amber info`
  (including `--json`), `amber api serve`, and the optional FastAPI application factory.
  HTTP routes are `/health`, `/api/v1/admin/health`, and `/api/v1/admin/info` with the default
  prefix.
- `api/v1/routers/extraction.py` and `annotation.py` are empty routers. `api/jobs.py` is a
  responsibility docstring. There is no extraction API, annotation workflow, or job queue yet.
- Domain schemas, IDs, grounding, graph validation, policy enforcement, tools, agents, backends,
  storage, exports, training, and evaluation are placeholders. The MLflow run-context provider
  exists but emits only `amber.version`; prompt/model helpers are placeholders.
- `tests/test_interfaces.py` contains four smoke tests. `tests/test_invariants.py` and
  `tests/conftest.py` contain docstrings, not invariant tests or shared fixtures.
- `prompts/` contains a README only. YAML seeds and the documented `amber register-prompts`
  command do not exist yet.

M1 is next: define one clinical task and acceptance protocol, then implement the evidence kernel
and one local persistence path with synthetic tests. M2 adds one permitted provider and a fixed
extraction baseline; M3 adds minimal correction and measures total expert effort. Agents/reviewers,
broad NLP, and additional backends are conditional on measured benefit. V1 remains note-scoped:
reviewers work over sections/chunks of one note. PatientFact, patient aggregation, and FHIR are
deferred; preserve patient IDs and effective datetimes without implementing those subsystems.

CORAL v1.0 is the first evaluation dataset. Its local copy is credentialed, deidentified clinical
data under a PhysioNet data-use agreement, not a synthetic repository fixture. Follow the CORAL
rules below whenever work touches ingestion, schemas, grounding, splitting, or evaluation.

## Project Structure and Dependency Direction

Amber uses a `src` layout. Production code is under `src/amber/`, tests are under `tests/`, and
only synthetic fixtures belong in `examples/`. Seed prompt definitions live in `prompts/`, while
shared operational utilities live in `scripts/`. Each dataset experiment lives under
`experiments/<experiment>/`, containing its scripts, evaluation code, and ignored local `data/`
and `outputs/`. Keep reusable behavior in `src/amber/`; core code must not import experiments.

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
  Services depend on store/provider protocols rather than concrete database or model packages.

Keep `import amber` lightweight: it must not import FastAPI, PydanticAI, Transformers, MLX, or
other optional stacks. Import optional dependencies at their boundary and return an actionable
installation message when they are absent. Resolve environment configuration in `config.py` at
process boundaries and pass explicit settings into testable application code.

## Configuration and Interface Boundaries

`Settings` reads `.env` and `AMBER_*` environment variables and ignores unrelated fields. Its
current fields are `environment`, `api_prefix`, `api_host`, `api_port`, and `log_level`; defaults
are development, `/api/v1`, `127.0.0.1`, port 8000, and info logging. Configuration examples for
providers and institutional tags in `.env.example` do not establish working integrations.

- `get_settings()` is cached. Tests that change environment variables must clear its cache before
  and after use. Use explicit `Settings(_env_file=None, ...)` and controlled environment variables
  when a test must be isolated from local configuration.
- Keep `create_client(settings=...)` and `create_app(settings=..., client=...)` injectable.
  The API stores its facade in `app.state.amber` and obtains it through `api/deps.py`.
- Compose versioned routes in `api/v1/router.py` under the configured prefix; keep `/health`
  independent of external providers. Add use cases to services before exposing CLI/HTTP adapters.

## Domain and Data-Model Rules

Enforce the repository invariants in code and adversarial tests:

- An `Inclusion` may be minted only by the quote/grounding path or an explicit human annotation
  path. Verified offsets always index the exact immutable `Source.text`; never normalize that text
  after computing `source_id`.
- `Mention.quote` must equal `Source.text[start:end]`. Use end-exclusive character offsets.
- Centralize canonical hashing and ULID creation in `ids.py` according to the schema contract.
  Fuzzy alignment must return offsets and a quote from the original source, or a grounding
  failure. Search snippets and generated quotes are candidates until verified.
- Every non-rejected claim has evidence, unknown evidence IDs are rejected, field-level evidence
  policies are honored, and the evidence graph remains acyclic.
  Validate the full support closure on commit/load, including case scope and nonempty inference
  inputs. Every branch reaches verified text or structured-source evidence; structural validity
  and source traceability do not establish semantic support.
- Keep clinical absence as an evidence-backed answer. `CaseOutcome` distinguishes not mentioned,
  conflicting evidence, insufficient evidence, and execution failure. `no_claim` never creates a
  null-valued Claim. Final-claim IDs identify task answers separately from supporting claims in
  CaseResult/Example, and failed runs cannot be gold Examples.
- Check provider zone against declared data sensitivity before every model or EDW call, then stamp
  both values into provenance. Never infer sensitivity from content.
  Default policy permits `phi`/`limited` only in `local` or `institution`; `deidentified` also
  permits `external_baa`; `synthetic` permits all zones.
  Source-bearing telemetry, judges, artifact stores, and annotation services need destination
  checks too; model access does not authorize unrelated transfers or override dataset terms.
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
`gpu` together or use `--all-extras`. Preserve the Transformers `<5` / TRL `<=0.24` compatibility
pins unless the incompatibility is resolved and tested. Ollama's MLX engine cannot be assumed to
enforce JSON schemas. Do not add orchestration beyond PydanticAI without a written reason in
`docs/`.

## Build and Development Commands

Use `uv` for Python environments and commands. The supported Python range is `>=3.11,<3.13`, with
3.11 pinned in `.python-version` and used as the lint/type-check target. `uv.lock` is tracked;
update it with dependency changes and preserve it during unrelated work.

There are two distinct dev declarations in `pyproject.toml`: the `dev` extra supplies Ruff, mypy,
coverage, and other development tools; the default `dev` dependency group supplies pytest and
HTTPX. `uv sync` alone does not select the full development extra. HTTP interface tests need both
the `app` extra and HTTPX from the dev group.

```sh
uv sync --extra dev
uv sync --extra dev --extra app             # API work
uv sync --extra dev --extra agents --extra nlp
uv run pytest
uv run --extra dev --extra app pytest tests/test_interfaces.py
uv run pytest --cov=amber
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run amber info
uv run amber info --json
uv run amber api serve                       # requires the app extra
```

Use `uv run ruff format <paths>` only when intentionally formatting files; use `--check` for
validation. Start with focused tests and changed-file linting, then run the relevant full checks
before handoff. API changes must be tested with the `app` extra installed so optional HTTP tests
do not pass merely by being skipped. For documentation-only changes, check referenced paths and
commands and run `git diff --check`; do not add tests just to exercise unchanged code.
Follow the local MLflow server command in `README.md` or use `bash scripts/mlflow_local.sh` when a
task genuinely needs MLflow integration; the script keeps its database and artifacts in `.mlflow/`.

## Notebooks

Notebooks in this repository are marimo notebooks: pure Python programs represented as reactive
DAGs. Variable names must be unique across cells; prefix cell-local values with an underscore.
All notebook edits must pass `uvx marimo check` before the turn ends.

## Coding and Testing Conventions

Use four-space indentation, a 100-character line limit, `snake_case` for modules/functions/data,
and `PascalCase` for classes and Pydantic models. Ruff owns import ordering and formatting. Prefer
small modules with one responsibility and public APIs with explicit typed inputs and outputs.

Write pytest tests as `tests/test_*.py` with `test_*` functions. Default tests must run offline on
CPU using synthetic data. Mark tests that load models or require special hardware with `slow`,
`gpu`, or `mlx`. Do not hide required dependency failures with unconditional skips; use skips only
for genuinely optional interfaces and run those tests explicitly when changing the interface.
Every domain invariant needs a test that attempts to violate it. Include invalid bounds, Unicode
and CRLF offsets, ambiguous/repeated quotes, unknown evidence IDs, missing field evidence, graph
cycles, and disallowed provider/sensitivity combinations as the relevant kernel code is added.
Cover source-free support branches and answer/abstention/failure consistency. Clinical experiments
also measure semantic support, omissions, automation coverage, and total expert effort; passing
kernel tests does not demonstrate clinical usefulness.
When adding an optional adapter, test in a fresh process that importing core Amber does not import
that adapter's dependency; the existing import test covers FastAPI only.

## CORAL Dataset

The local documentation is in `experiments/coral/data/`, named
`CORAL_ expert-Curated medical Oncology Reports to Advance Language model inference v1.0.pdf`.
The canonical dataset citation is CORAL v1.0, DOI `10.13026/v69y-xa45`. Consult the documentation
and `experiments/coral/data/raw/annotated/annotation.conf` before interpreting labels.

- `experiments/coral/data/raw/annotated/` contains the expert-labeled gold set: 20 breast-cancer
  and 20 pancreatic-cancer progress notes in BRAT `.txt`/`.ann` pairs, plus `subject-info.csv`
  and BRAT configuration.
- `experiments/coral/data/raw/unannotated/` contains 200 different notes and GPT-4-generated
  outputs. Those outputs are pseudo-labels, not expert gold; never mix them into the held-out
  gold evaluation silently.
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
  The ingest script's `redacted` classification is a same-length mismatch heuristic, not proof
  that offsets are valid. Validate bounds separately before using any span as evidence.
- Keep CORAL-specific parsing and mapping separate: experiment script `coral_ingest.py` audits
  the BRAT records, while `coral_adapter.py` contains draft mapping decisions that depend on the M1
  schemas. Move reusable code into `src/amber/` with synthetic tests as the adapter stabilizes.
  Both scripts live in `experiments/coral/scripts/`.
  The adapter currently imports schema classes that do not exist, leaves IDs empty, lacks fragment
  grouping, and has an empty manual-fix table. Its docstrings are not acceptance criteria;
  validate and version its widening, modality, section, and relation policies before using it.
- Declare CORAL inputs as `Sensitivity.deidentified`; deidentification does not make them public or
  synthetic. The provider policy gate still applies to every model call.

Repository and CI tests must not require access to CORAL. Build invented BRAT fixtures that cover
its format and edge cases without copying clinical text. Run corpus-dependent checks locally as a
separate integration step and report the dataset version, manifest/hash, split, and adapter policy
version with evaluation results.

For a local aggregate audit without printing example spans, use:

```sh
uv run python experiments/coral/scripts/coral_ingest.py \
  experiments/coral/data/raw/annotated --show 0
```

The default audit prints mismatch text. `--category` and `--dump-unparsed` can also expose raw
annotations; `--jsonl` exports source quotes and offsets. Keep such output in ignored local paths
under the dataset's access restrictions, out of commits, shared logs, and PR descriptions. The
JSONL is an audit export, not a validated Amber `Example`; for discontinuous annotations, its
concatenated quote is not necessarily the source slice between its outer `start` and `end`.

## Data Safety and Working-Tree Hygiene

Never commit PHI, real clinical notes, credentials, `.env`, local databases, MLflow artifacts, or
model weights. `examples/` is synthetic-only. Experiment datasets and generated artifacts belong
in ignored `experiments/<experiment>/data/` and `outputs/`; keep only their placeholders under
version control. The root `data/` remains ignored for other local data. CORAL access requires
credentialing, the PhysioNet Credentialed Health Data License 1.5.0, its data-use agreement, and
required training. Do not redistribute its
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
