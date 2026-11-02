# Contracts

This document describes the contract that amber-nlp implements today. Planned additions are
listed at the end and tracked in Beads. The pre-reset specification is preserved in
[history/02-v1-schemas-and-tools.md](history/02-v1-schemas-and-tools.md) as design input; it is
not binding where it describes pipelines, agents, tables, or tools that amber-nlp no longer owns.

Every model is Pydantic v2 with `extra="forbid"`. Value objects are frozen, and nested values
resist mutation. Offsets are end-exclusive Python code points into the immutable
`Source.text`.

## Identity (`amber.ids`)

Content-addressed IDs hash canonical JSON as defined by RFC 8785 (`canonical_json`,
`canonical_sha256`): keys sorted by UTF-16 code units, ECMAScript number form, no Unicode
normalization. Timestamps must be timezone-aware and hash as UTC with six fractional digits and
`Z`. NaN, infinities, lone surrogates, and integers beyond ±(2⁵³−1) are rejected.

| Identifier | Derivation |
|---|---|
| `source_id` | Canonical SHA-256 of `patient_id`, `kind`, `external_id`, `text` |
| Inclusion `evidence_id` | Canonical SHA-256 of `source_id`, `start`, `end` |
| `mention_id`, structured `evidence_id` | Canonical SHA-256 (helpers exist; the models are planned) |
| `claim_id`, inference `evidence_id` | Canonical ULID |
| `schema_ref` | `module:Class@sha256(model JSON schema)` for an answer model |

**Producer references** (`parse_producer`) name who produced or decided something:
`rule:<name>@<version>[+<hash>]`, `model:<name>[@<version>]`, `human:<user>`, or
`tool:<name>[@<version>]`. `producer_configuration` returns the `model:`/`rule:` keys used to
refuse scoring a configuration against its own silver references; rule hashes are ignored.

## Models (`amber.schemas`)

- **`Source`**, **`Section`**, **`SourceKind`**: an immutable note or record, with
  bounds-checked sections. `Source.create` computes the ID; the text is never normalized
  afterward.
- **`Provenance`**, **`Zone`**, **`Sensitivity`**: a producer reference, model, prompt
  versions, run and trace IDs, the destination that produced the node (`local:<tool>` for
  tools that send nothing anywhere, which must be zone `local`), the policy version, the
  restrictions that applied, zone, declared sensitivity, time, and version.
- **`AnswerModel`**: a frozen, closed base class for task answers. Task-specific answer models
  live in the projects that define the tasks.
- **`Task`**: name, answer model, instructions, `evidence_policy` (`claim` or `field`), and
  `scope`. Only `note` is accepted today.
- **`Claim`**: a validated answer value, `schema_ref`, effective time, confidence, status, and
  provenance. Claims are admitted only through graph validation.
- **Evidence**:
  - `Inclusion`: a verified source span. Only the grounding path can mint one.
  - `InferenceEvidence`: a rationale with non-empty inputs, which are evidence IDs or
    non-rejected claim IDs.
  - `EvidenceEdge`: a claim → evidence link with an optional field role and weight.
  - `GroundingFailure`: an explicit grounding failure, never evidence.
- **`Case`**: one note, one task, and declared sensitivity.
- **`CaseOutcome`**: one of `answered`, `not_mentioned`, `conflicting_evidence`,
  `insufficient_evidence`, or `failed`, with a reason, a failure kind, review coverage, and
  cited evidence.
- **`CaseResult`**: claims, evidence, edges, explicit `final_claim_ids`, the outcome, counters,
  and a trace ID. Results are built and loaded only through `amber.cases`.

## Validation and operations

- `amber.grounding.exact_quote` and `amber.tools.quote.quote`: exact matching. Repeated text
  requires a hint, and the result is an `Inclusion` or a `GroundingFailure`.
- `amber.graph.EvidenceGraph`: one source, one task, one sensitivity. It offers:
  - atomic registration and claim commits;
  - complete validation: known in-scope references, field roles, acyclicity across edges and
    inference inputs, and source-backed leaves on every branch;
  - support traversal;
  - snapshot and restore against authoritative context.
- `amber.tools.commit.commit_claim`: admits a proposed claim only with valid, complete support.
- `amber.tools.outcomes.no_claim`: records the three clinical non-answers and never creates a
  null claim. Only runtimes record `failed`.
- `amber.cases.create_case_result` / `load_case_result`: build and revalidate results against
  caller-supplied `Case`, `Source`, and `Task`.
- `amber.graph_errors`: stable, source-free diagnostic codes.

Structural validity and source traceability do not establish semantic support or clinical
correctness. Those are measured separately.

## MLflow plugin (`tracking` extra)

- `amber.mlflow_ext.context.AmberRunContextProvider` adds the `amber.version` tag.

## Planned contract work

These items are tracked in Beads (`bd list --status=open`):

- the policy gate: `Provider`, the policy matrix, and destination checks;
- `Mention` and `MentionReading` (`StructuredEvidence` is deferred to a later minor; contract
  v0.1 §2.6);
- `Example` with reference tiers;
- annotation events and status transitions;
- layered variables over multi-task evidence graphs;
- interfaces (`Extractor`, `Store`, `Exporter`, `Scorer`, `ReviewBackend`, `Labeler`);
- the conformance kit;
- MLflow plugins for evaluation, datasets, the loupe bridge, and deployment.

Each item becomes stable only under the rules in the [charter](charter.md#rules-for-growth).
