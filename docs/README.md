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

## Design extensions and proposals

- [Terminology and mention detection](05-terminology-and-mention-detection.md) — consolidated
  conditional design for OMOP vocabulary integration, fragments, detectors, and contextual
  linking; no new domain fields or detector default are adopted.
- [Select–decide proposal](select-decide.md) and [evaluation](select-decide-evaluation.md) — an
  unadopted evidence-restricted execution hypothesis and the requirements for a bounded comparison.
- [Contextual entity-linking reading guide](context-aware-biomedical-entity-linking.md) — candidate
  retrieval, disambiguation, and evaluation references, not a mandatory baseline suite.
- [October 7 review](notes/2026-10-07-documentation-review.md) — consolidation decisions,
  assessment, next work, and verification limits.

The [October source bundle](amber-docs-2026-10-07/README.md) is retained as history. Its useful
additions are consolidated above and in the canonical survey; its old roadmap and conflicting
schema rules are superseded. Maintain current decisions in the canonical docs, rather than
editing two live versions. Dated plans and notes record their original scope and results.

## Current implementation

Checked against code and tests on 2026-09-16; source and test-file inventory rechecked on
2026-10-07, followed by synthetic verification of the case-outcome/result increment. Restricted
data was not inspected. This is the canonical implementation-status summary; update it when
capabilities change rather than copying inventories into other documents.

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
  declare field-level evidence requirements, enforced by the graph validator described below.
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
- Immutable claim prerequisites: explicit [Task](../src/amber/schemas/tasks.py), guarded
  [Claim records](../src/amber/schemas/claims.py), and inference/edge records in the
  [evidence schemas](../src/amber/schemas/evidence.py). Nested values, inference inputs, and
  provenance prompt versions resist ordinary mutation while preserving JSON shapes. Canonical
  schema references and ULID validation are covered by [schema tests](../tests/test_claim_schemas.py).
  Claims are admitted only through complete graph validation, not standalone construction.
- Complete internal [graph validation](../src/amber/_graph_validation.py) with
  [source-safe diagnostic codes](../src/amber/graph_errors.py): one note/task/sensitivity context,
  exact inclusion revalidation, exclusions, field citations, complete references, cycles, and
  source-backed support across every retained component. Only proposed/rejected claims are
  admitted, and rejected claims cannot support inference. [Synthetic tests](../tests/test_graph_validation.py)
  include deep iterative chains. Structural validity does not establish semantic support.
- Public [EvidenceGraph lifecycle](../src/amber/graph.py): validated registration, read-only or
  detached views, complete support traversal, and deterministic in-memory snapshot/restore.
  Restore requires the authoritative Source, Task, sensitivity, and exclusions and revalidates
  all evidence; snapshots contain source quotes and are never printed by the graph. Registration
  failures leave graph state unchanged. [Lifecycle tests](../tests/test_graph.py) cover forged
  copies, ownership, idempotency, corrupt payloads, and strict snapshot context/version checks.
  No file/database persistence is added.
- Atomic [claim commits](../src/amber/tools/commit.py) delegate to graph-owned staging and full
  validation before publishing a proposed Claim, rationale inference, and field-specific edges.
  Explicit negative answers require the same evidence as positive ones. Provenance and source
  datetime defaults are retained; rejected operations leave no partial state.
  [End-to-end tests](../tests/test_commit.py) distinguish structural validity from semantic support
  and exercise atomic failures, supporting-claim chains, and snapshot round-trips.
- Immutable [Case, CaseOutcome, and guarded CaseResult](../src/amber/schemas/cases.py), with
  exactly one note/task context, distinct clinical non-answers and failures, required non-answer
  reasons, and failure categories only for failures. The
  [case-result boundary](../src/amber/cases.py) builds detached results and loads serialized
  results against authoritative caller-supplied Source/Task/Case context. It revalidates every
  retained graph component, scope, sensitivity, outcome citations, and explicit final-claim IDs.
  Answered outcomes require known non-rejected final claims; other outcomes retain supporting
  claims without promoting them to answers. Only the graph's proposed/rejected statuses and
  exact Inclusion/InferenceEvidence records are admitted; mentions remain an empty list.
- The deterministic [no_claim tool](../src/amber/tools/outcomes.py) validates not-mentioned,
  conflict, and insufficient-evidence outcomes without changing graph state or minting a null
  Claim. Not-mentioned requires declared complete review of the case note; conflict requires
  known source-backed citations. Runtime callers construct failures with a stable failure kind.
  [Schema](../tests/test_case_schemas.py), [result](../tests/test_cases.py), and
  [outcome-tool](../tests/test_outcomes.py) tests cover exact Unicode/CRLF quotes, forged copies,
  mutation, full support closure, safe load failures, and answer/abstention/failure consistency.
  Review declarations and structurally valid conflict citations do not establish clinical
  completeness, disagreement, or semantic support. No execution audit or gold path is added.
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
- A lightweight core, with storage, evaluation, and tracking dependencies selected through
  extras. See the [installation guide](../README.md#toolchain); selecting an extra does not
  implement the corresponding planned workflow.
- [CI checks](../.github/workflows/ci.yml) for core-only installation and the full synthetic suite
  on Python 3.11/3.12, plus lint, formatting, type, and lockfile checks. The
  [test guide](../tests/README.md) documents local reproduction and optional dependencies.

### Planned or incomplete

- Independent human review and adjudication for the current-progression task have not been
  performed by this implementation, and every clinical gate remains unevaluated. Completing
  protocol/schema/candidate/split preparation does not establish clinical performance or gold.
- Mentions, structured evidence, `Example`, human status review, and
  provider/destination policy enforcement remain unimplemented. Having sensitivity/zone enums
  or checking provenance sensitivity does not implement the policy gate. The
  [in-memory claim-commit design](superpowers/specs/2026-09-16-m1-evidence-backed-claim-commits-design.md)
  and [checkpoint plan](superpowers/plans/2026-09-16-m1-evidence-backed-claim-commits.md)
  describe the bounded evidence slice; M1 is still incomplete.
- Persistence, exports, extraction, clinical evaluation, correction, agent/backend integrations,
  and training remain future work. Extraction/annotation routers are empty; no job queue is
  implemented. Distant-future docstring-only modules have been removed; their intended
  responsibilities and locations remain in the [roadmap](04-roadmap.md#deferred-implementation-locations).
- [CORAL's draft adapter](../experiments/coral/scripts/coral_adapter.py) imports domain classes that
  do not exist yet; it is not a working domain integration or a gold-generation path.
- [Prompt seeds](../prompts/README.md) are documented but no YAML seeds or `amber register-prompts`
  command exist. MLflow prompt helpers remain placeholders; model packaging remains deferred.
- Terminology stores/fragments, mention detectors, contextual linking, and select–decide are
  design proposals without implementations or accepted schema migrations. The October
  documentation consolidation does not complete these capabilities or alter M1–M3's scope.

Use the [M1–M3 roadmap](04-roadmap.md) for remaining delivery requirements. Check code and tests
before claiming a feature or milestone is complete; synthetic tests cannot establish clinical
performance.

## Synthetic kernel usage

The [installed core smoke example](../scripts/check_core_install.py) shows the complete invented-data
workflow; [the test guide](../tests/README.md#ci) gives the isolated installation command. Supply one
explicit `Source`, `Task`, and `Sensitivity` to `EvidenceGraph`, ground a quote against that exact
source, and register the resulting `Inclusion`. Then use the [commit tool](../src/amber/tools/commit.py)
with explicit provenance and field evidence:

```python
claim = commit_claim(
    task,
    {"progression_or_recurrence": False},
    [inclusion.evidence_id],
    "Interpretation of an invented example only.",
    {"progression_or_recurrence": [inclusion.evidence_id]},
    graph=graph,
    provenance=provenance,
)
assert claim.status == "proposed"
```

An omitted effective datetime uses the source datetime. Claims require a nullable `confidence`
field in their serialized record; the tool fills it with `None` when the caller omits it.
`graph.to_payload()` is source-bearing, even though it performs no I/O. Do not log or commit real
snapshots. Restore with `EvidenceGraph.from_payload(...)` and the same authoritative source, task,
sensitivity, and exclusions. Failed registration or commit leaves the graph unchanged.

The quote/commit path proves structural validity and source traceability, not clinical correctness.
Case outcomes are recorded separately below. It does not certify complete note review, authorize
transfers, or assign gold status. Use invented data until provider/destination policy and clinical
integration are designed.

### Synthetic case outcomes and results

After committing the invented claim above, record final-answer identity explicitly:

```python
from amber.cases import create_case_result, load_case_result
from amber.schemas import Case, CaseOutcome
from amber.tools.outcomes import no_claim

case = Case(
    case_id="synthetic-case-1",
    task=task.name,
    patient_id=graph.source.patient_id,
    source_ids=[graph.source.source_id],
    sensitivity=graph.sensitivity,
)
result = create_case_result(
    case,
    graph=graph,
    outcome=CaseOutcome(status="answered", provenance=provenance),
    final_claim_ids=[claim.claim_id],
)
restored = load_case_result(
    result.model_dump(mode="json"),
    case=case,
    source=graph.source,
    task=task,
    excluded_spans=graph.excluded_spans,
)
assert restored.final_claim_ids == [claim.claim_id]

outcome = no_claim(
    "insufficient_evidence",
    "The invented review stopped before the complete task scope was examined.",
    [],
    [],
    case=case,
    graph=graph,
    provenance=provenance,
)
diagnostic = create_case_result(case, graph=graph, outcome=outcome, final_claim_ids=[])
assert diagnostic.claims  # supporting claims may remain
assert diagnostic.final_claim_ids == []
```

`reviewed_source_ids` names only notes whose complete task-defined scope the caller declares
reviewed; it is required to cover the case note for `not_mentioned`. Partial review stays
unlisted. The boundary checks this declaration's scope consistency, not whether a human or model
actually reviewed every relevant statement. Do not infer clinical absence from a non-answer.

The result is an immutable, source-bearing in-memory record. `model_dump(mode="json")` performs
no I/O; keep real payloads out of logs and commits. Use `load_case_result` to deserialize, rather
than standalone `CaseResult.model_validate`. Supply the authoritative case, source, task, and
exclusions again; payloads cannot select an answer model or relax source scope. Ordinary copies
are not a substitute for revalidation. Case errors and graph errors expose stable diagnostic
codes without source text. Provider policy, persistence, execution audits, `Example` creation,
human verification/gold status, and clinical scoring remain subsequent work.

## Background and experiment documentation

- [Progress note — 2026-09-16](notes/2026-09-16-project-progress.md) — dated verification results,
  assessment, and recommended next work; the status summary above remains authoritative.
- [Documentation review — 2026-10-07](notes/2026-10-07-documentation-review.md) — consolidation
  rationale, terminology/detection critique, and recommended next increment.
- [State of the art](01-state-of-the-art.md) — background research, not the implementation contract.
- [Strata scaffold notes](03-strata-scaffold-notes.md) — historical reference only; do not copy its structure.
- [Experiments](../experiments/README.md) — experiment layout and reproducibility conventions.
- [CORAL workspace](../experiments/coral/README.md) — local setup, audit commands, and data restrictions.

CORAL is credentialed, deidentified clinical data, not a synthetic fixture. Keep its source data
and reconstructable derivatives in restricted, ignored local storage; follow the
[CORAL rules](../AGENTS.md#coral-dataset).
