# Documentation

Start here to distinguish the implementation on disk from the planned clinical workflow.

## Reading order and authority

Before changing architecture or domain behavior, read:

1. [Goals and architecture](00-goals-and-architecture.md) — purpose, boundaries, and decisions.
2. [V1 contract](02-v1-schemas-and-tools.md) — binding domain fields, tools, and invariants.
3. [Roadmap](04-roadmap.md) — milestone order, scope, and exit evidence.
4. [Working agreement](../CLAUDE.md) — non-negotiable invariants and conventions.

[Repository guidelines](../AGENTS.md) cover development commands, dependency boundaries, and data
safety. If documents disagree, preserve the working agreement's invariants and the v1 contract,
then reconcile the stale document. A plan or module docstring is not evidence of implementation.

## Current implementation

Checked against code and tests on 2026-09-15. This is the canonical implementation-status summary;
update it when capabilities change rather than copying inventories into other documents.

M0's workspace and interfaces exist, and part of the M1 evidence kernel is implemented. M1 is not
complete. No extraction quality, clinical acceptance, or reduction in expert effort has been
demonstrated.

### Implemented

- [Current progression protocol v1.0.0](protocols/oncology_current_progression-v1.md): the
  note-level question, evidence and outcome rules, independent adjudication, fixed split policy,
  and prospective pilot gates. This is a research protocol document, not a clinical validation
  result, generated split manifest, or adjudicated gold dataset.
- Frozen, extra-forbidden [AnswerModel](../src/amber/schemas/answers.py) and
  [OncologyCurrentProgressionAnswer](../src/amber/schemas/oncology_current_progression.py), with a
  required strict boolean and exported task/version/scope/evidence-policy constants.
  [Schema tests](../tests/test_answer_schemas.py) cover validation and serialization. The constants
  declare field-level evidence requirements; they do not implement evidence-policy enforcement.
- Python facade: `Amber` and `create_client` in [client.py](../src/amber/client.py), environment
  [settings](../src/amber/config.py), and [SystemInfo](../src/amber/services/system.py).
- CLI: [amber info](../src/amber/cli/main.py), including `--json`, and
  [amber api serve](../src/amber/cli/server.py). The optional [FastAPI factory](../src/amber/api/app.py)
  exposes `/health`, `/api/v1/admin/health`, and `/api/v1/admin/info` with the default prefix.
- Kernel foundations: [canonical hashing and IDs](../src/amber/ids.py), frozen
  [Source and Section models](../src/amber/schemas/sources.py) with source-text identity and bounds
  checks, and [Provenance, Sensitivity, and Zone](../src/amber/schemas/provenance.py).
- Exact grounding: [Inclusion and GroundingFailure](../src/amber/schemas/evidence.py),
  [exact_quote](../src/amber/grounding.py), and the deterministic [quote tool](../src/amber/tools/quote.py).
  Repeated text requires an exact hint; returned offsets refer to the unchanged source text.
- [Core interface smoke tests](../tests/test_interfaces.py), separate optional
  [HTTP tests](../tests/test_api.py), [isolated settings tests](../tests/test_config.py), and
  [kernel invariant tests](../tests/test_invariants.py), including source identity/mutation,
  section bounds, Unicode/CRLF offsets, repeated quotes, grounding failures, and direct minting
  rejection. These cover the implemented subset, not every planned invariant.
- A minimal [MLflow run-context provider](../src/amber/mlflow_ext/context.py) emitting only
  `amber.version`, available through the optional `tracking` extra, and the experiment-local
  [CORAL annotation audit](../experiments/coral/audit.py).
  Its [BRAT parser](../experiments/coral/brat.py) is independently importable using only the
  standard library; [parser](../tests/test_coral_brat.py) and [CLI](../tests/test_coral_audit.py)
  regression tests use invented data. The existing script command remains a compatibility launcher.
  The parser diagnoses malformed, unknown, duplicate, conflicting, and dangling records;
  `--show 0` reports aggregate diagnostic counts. Annotation inventory completeness does not
  establish clinical review coverage, valid evidence bounds, or gold labels.
- Pure [CORAL progression-candidate rules](../experiments/coral/scripts/coral_current_progression.py),
  covered by [invented-BRAT tests](../tests/test_coral_current_progression.py). They reject incomplete
  inventories and unsafe relevant spans/skip boundaries, preserve semantic warnings, and require
  human review for every result. Candidates contain no quotes or offsets and never create gold;
  annotation absence is not a certification of complete clinical review.
- [CORAL split/manifest tooling](../experiments/coral/scripts/coral_current_progression_manifest.py),
  with [synthetic safety tests](../tests/test_coral_current_progression_manifest.py). It allocates
  deterministic 20/10/10 document splits with 10/5/5 per cancer type and writes restricted local
  manifests without replacing existing artifacts. Identical reruns verify the frozen payload;
  changed inputs or policies fail. The selected CORAL split is frozen locally, with input hashes,
  unique membership, split margins, and unchanged-byte/mtime reuse verified. The manifest remains
  ignored restricted data. The [preparation plan](superpowers/plans/2026-09-15-m1-current-progression-task-protocol.md)
  is complete; the [operator workflow](../experiments/coral/README.md#local-preparation-workflow)
  documents safe creation and verification.
- A four-dependency core, with storage, evaluation, and tracking dependencies selected through
  extras. See the [installation guide](../README.md#toolchain); selecting an extra does not
  implement the corresponding planned workflow.
- [CI checks](../.github/workflows/ci.yml) for core-only installation and the full synthetic suite
  on Python 3.11/3.12, plus lint, formatting, type, and lockfile checks. The
  [test guide](../tests/README.md) documents local reproduction and optional dependencies.

### Planned or incomplete

- Independent human review and adjudication for the current-progression task have not been
  performed by this implementation, and every clinical gate remains unevaluated. Completing
  protocol/schema/candidate/split preparation does not establish clinical performance or gold.
- Mentions, claims, structured/inference evidence, graph validation, field-level evidence policies,
  `commit_claim`, `CaseOutcome`, `Example`, and provider/destination policy enforcement remain
  unimplemented. Having sensitivity/zone enums does not implement the policy gate.
  The next proposed increment is the [in-memory claim-commit design](superpowers/specs/2026-09-16-m1-evidence-backed-claim-commits-design.md),
  awaiting written-design review before implementation.
- Persistence, exports, extraction, clinical evaluation, correction, agent/backend integrations,
  and training remain future work. Extraction/annotation routers are empty; no job queue is
  implemented. Distant-future docstring-only modules have been removed; their intended
  responsibilities and locations remain in the [roadmap](04-roadmap.md#deferred-implementation-locations).
- [CORAL's draft adapter](../experiments/coral/scripts/coral_adapter.py) imports domain classes that
  do not exist yet; it is not a working domain integration or a gold-generation path.
- [Prompt seeds](../prompts/README.md) are documented but no YAML seeds or `amber register-prompts`
  command exist. MLflow prompt helpers remain placeholders; model packaging remains deferred.

Use the [M1–M3 roadmap](04-roadmap.md) for remaining delivery requirements. Check code and tests
before claiming a feature or milestone is complete; synthetic tests cannot establish clinical
performance.

## Background and experiment documentation

- [State of the art](01-state-of-the-art.md) — background research, not the implementation contract.
- [Strata scaffold notes](03-strata-scaffold-notes.md) — historical reference only; do not copy its structure.
- [Experiments](../experiments/README.md) — experiment layout and reproducibility conventions.
- [CORAL workspace](../experiments/coral/README.md) — local setup, audit commands, and data restrictions.

CORAL is credentialed, deidentified clinical data, not a synthetic fixture. Keep its source data
and reconstructable derivatives in restricted, ignored local storage; follow the
[CORAL rules](../AGENTS.md#coral-dataset).
