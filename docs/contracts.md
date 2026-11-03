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
| `mention_id` | Canonical SHA-256 of `source_id`, `start`, `end`, `mention_type`, and `fragments` only when nonempty |
| structured `evidence_id` | Canonical SHA-256 (helper exists; the model is deferred) |
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
  `scope`. A task is the one-variable case of a `VariableSet` (`VariableSet.from_task`).
- **`Variable`**, **`VariableSet`**, **`AttributeRequirement`**: named questions with their own
  answer models, a level (`note`, `mention`, or the reserved `patient`), declared `inputs`,
  mention types, attribute and section requirements, and a `final` flag. A set has unique
  names, known and acyclic inputs, and at least one final variable. Its `schema_ref` is the
  canonical SHA-256 of its name and every variable field (answer models as JSON Schema), and
  never of its `version`, which is a label. Permitted attribute values are a tuple compared
  with strict types, so `True` and `1` stay distinct.
- **`Claim`**: a validated answer value, the set name (`task`) and `variable` it answers,
  that variable's `schema_ref`, the `reading_id` it relies on (mention-level variables only),
  effective time, confidence, status, and provenance. Claims are admitted only through graph validation.
- **Evidence**:
  - `Inclusion`: a verified source span. Only the grounding path can mint one. Inclusions are
    positions only, so one position has one `evidence_id` whatever cites it.
  - `InferenceEvidence`: a rationale with non-empty inputs, which are evidence IDs or
    non-rejected claim IDs.
  - `EvidenceEdge`: a claim → evidence link with an optional role and weight. A role is an
    answer-field name or `attribute:<category>`, which cites the cue of that attribute on the
    claim's reading and never counts toward field coverage.
  - `GroundingFailure`: an explicit grounding failure, never evidence.
- **`Mention`**: a typed, content-addressed span (`mention_type`, hull offsets and quote,
  optional `fragments` for discontinuous spans, and `section_category` derived from the
  innermost categorized `Source` section containing the hull). Only the grounding path can
  mint one. A mention is not evidence; claims cite the inclusions at its position.
- **`MentionReading`**: one producer's reading of a span (label, value, unit, optional
  `Concept`, attributes, producer-side inputs, status, provenance). Disagreement is several
  readings of one span; a correction is a new reading, never an edit.
- **`Attribute`**: an assessed category of a reading (`negation`, `temporality`, ...), keyed by
  category. Its value is a strict `str`, `bool`, or `int`; an absent category means not
  assessed. It may cite a cue inclusion and a scope inclusion, and `assigned_by` names another
  producer only when someone other than the reading's producer assigned it.
- **`Case`**: one note, one task (the variable set's name), declared sensitivity, the
  optional `dataset` it was drawn from, and the optional `split` (`train`, `dev`, `test`) of
  its `(dataset, source)` pair.
- **`CaseOutcome`**: one of `answered`, `not_mentioned`, `conflicting_evidence`,
  `insufficient_evidence`, or `failed`, with a reason, a failure kind, review coverage, and
  cited evidence.
- **`VariableOutcome`**: one final variable's `CaseOutcome` and its `final_claim_ids`
  (nonempty exactly when answered).
- **`CaseResult`**: the `run_id` (a ULID) that produced it, which every claim, inference,
  reading, and outcome's provenance must carry (`run_mismatch`); the variable set's name,
  version, and `variable_set_ref`; claims,
  evidence, edges, mentions and their readings; one `VariableOutcome` per final variable;
  counters; and a trace ID. When the set has one final variable, `outcome` and
  `final_claim_ids` equal its `VariableOutcome`; otherwise both are null.
  `amber.cases.case_status` returns the most severe variable status (failed, conflicting,
  insufficient, not mentioned, answered) and is never stored. Results are built and loaded only through `amber.cases`.

## Annotated data (`amber.schemas.examples`, `amber.examples`)

- **`ReferenceTier`**: `adjudicated`, `single_review`, or `silver`. Tier records how good a
  reference is; claim status records whether a person reviewed a claim.
- **`VariableReference`**: one final variable's reference: tier, outcome (never `failed`),
  final claim IDs (nonempty exactly when answered), annotators and adjudicator as producer
  references, and the event IDs whose replay yields it. `adjudicated` needs two or more human
  annotators and a human adjudicator; `single_review` one human annotator and no
  adjudicator; `silver` no human at all.
- **`Example`**: the single annotated-data format. A snapshot of a case's annotations as of
  one event: `example_id` (canonical SHA-256 of `case_id`, `variable_set_ref`,
  `as_of_event`), the variable set, `split`, one reference per final variable, and the
  retained claims, mentions, readings, evidence, and edges. Issued only by
  `amber.examples.load_example`, which revalidates the graph against the authoritative
  `Case`, `Source`, and `VariableSet` and checks that:
  - every final variable has a reference, and its outcome, citations, and final claims are
    consistent (non-answers have no final claims; mention-level references are never
    `conflicting_evidence`; `not_mentioned` needs full review);
  - claim and reading status follow the tier: under a human tier final claims are `gold` and
    supporting claims and cited readings `gold` or `verified`; under `silver` all are
    `proposed`; a record serving several references meets the strictest; no `rejected`
    record is retained, uncited readings keep their own status, and every retained claim
    serves a reference;
  - `split` equals the case's, `example_id` matches, and referenced events precede
    `as_of_event`.
- **`amber.labels.labels_only_view`**: the only sanctioned downgrade from `derived_text` to
  `labels_only`. For a `CaseResult` (with its authoritative `Case` and `VariableSet`) it gives
  one silver `LabelRecord` per final variable that did not fail, keeping the answer value only
  when the answer model is closed (`open_answer_fields` otherwise) and the final claims agree
  on it and on their producer. For annotation events it gives one `EventLabel` per event
  (`event_id`, `case_id`, `variable`, `actor`, `role`, `round`, `stream`, `action`,
  `target_id`, `at`, `external_ref`). `is_closed_schema` decides closedness: booleans,
  numbers, nulls, and strings limited by `enum`, `const`, or a date format.
- **`LabelRecord`**: a silver label without evidence (outcome status, value, one non-human
  producer, split). `load_label_record` checks it against the case and set and validates the
  value against the final variable's answer model. A LabelRecord never loads as an Example.

## Annotation events (`amber.schemas.annotations`, `amber.annotations`)

- **`AnnotationEvent`**: one append-only change to a case's annotations, ordered by its ULID:
  actor (producer reference; `human:` exactly for `reviewer` and `adjudicator`), role, round,
  blind `stream` (reviewers only), action, target, `before`/`after` without quotes or
  offsets, reason, time, provenance, and external reference. Event logs are `derived_text`.
- **`replay`** rebuilds a `Projection` of a case's events up to `as_of_event` as one actor may
  see it. It is deterministic, and replaying a prefix gives that prefix's projection. Events
  name mentions and inclusions by ID, resolved against caller-supplied spans and re-minted
  against the `Source`. A projection validates under the graph rules with any status.
  - **Blind review** (`visible_events`): before an untargeted `adjudication.resolve` for its
    round, a reviewer sees their own streams and stream-less events other than the
    adjudicator's; adjudicators, importers, and system actors see everything; afterwards
    everyone does.
  - **Transitions**: claims and readings are created `proposed` (by the human actor when the
    role is human); reviewers move `proposed` to `verified` or `rejected`; the adjudicator
    resolves to `gold` or `rejected` (`gold` to `rejected` needs a reason). A correction
    (`claim.update`, `reading.update`) is a new record linked by its event; the old one is
    rejected unless it is gold, which is never edited. A claim becomes gold only over
    `gold`/`verified` readings.
  - **Tiers** only rise: `silver` by importers or system, `single_review` by the reviewer,
    `adjudicated` by the adjudicator; a human tier promotes the variable's final claims to
    gold. A case-level `tier.set` is the protocol's move-on decision and needs
    `role="system"` and a `decision_ref`.
  - An `excluded` adjudication decision yields no Example; `unresolved` and `excluded` need a
    reason.
- **`example_from_projection`** is the one path from events to an `Example`: it drops
  rejected records and the inferences built only on them, attributes each reference from
  the events (reviewers who submitted, the resolving adjudicator, or the silver producer),
  and loads the result through `load_example`, which names any failing invariant.
  **`verify_example`** requires an Example to equal the replay of its events
  (`events_mismatch`), so a tier the events do not support is rejected.

## Policy (`amber.policy`)

Pure data and functions; no I/O, credentials, or clock (contract v0.1 §1).

- **`Destination`** (name, kind, zone, accepted payload kinds, retention, vendor training,
  tags) and **`ProviderDestination`** (adds capabilities and the model). Destination kinds
  include `training`, so training on a dataset is checked like any other transfer.
- **`Restriction`** and **`DataPolicy`**: a dataset's declared sensitivity and obligations;
  restrictions may expire against the caller's clock.
- **`PolicyMatrix`**: permitted zones per sensitivity. `DEFAULT_MATRIX` permits `synthetic`
  everywhere, `deidentified` up to `external_baa`, and `limited` and `phi` only `local` and
  `institution`. A matrix may narrow the default freely; widening needs
  `widened_from_default` and a note.
- **`check`**: total and deterministic; returns a **`Decision`** (the audit record, with a
  content-hash `decision_id`) and never raises on refusal. Rules apply in order: policy
  version against the `RunContext`, sensitivity, zone, accepted payload, then each restriction
  in declared order. `require` raises **`PolicyViolation`** carrying the decision.
- **`combine`**: the policy of data from several datasets (highest sensitivity, every
  restriction). **`stamp_provenance`**: `Provenance` for a node produced through a permitted
  transfer. **`RunContext`**: run ID, policy version, data policy, and dataset identity.

## Validation and operations

- `amber.grounding.exact_quote` and `amber.tools.quote.quote`: exact matching. Repeated text
  requires a hint, and the result is an `Inclusion` or a `GroundingFailure`.
- `amber.tools.mention.mention`: grounds a span's hull exactly as `quote` does and returns a
  `Mention` or a `GroundingFailure`. Fragments must be two or more ordered, separated spans
  whose hull is the grounded text. `amber.tools.mention.cite` re-grounds a mention and returns
  the inclusion at its hull, or one per fragment.
- `amber.graph.EvidenceGraph`: one source, one variable set (`task=` or `variables=`), one
  sensitivity. Patient-level variables are reserved and refused. It offers:
  - atomic registration and claim commits; `register_mention` takes a span with at least one
    reading, and `register_reading` adds a reading of a registered span;
  - complete validation: known in-scope references, field roles, acyclicity across edges and
    inference inputs, and source-backed leaves on every branch. Mentions are re-minted against
    the source; readings must name a registered span; attribute cues and scopes must be
    registered inclusions, and a scope must contain the mention's hull. Graphs hold only
    `proposed` and `rejected` readings and claims. Each claim validates against its own
    variable; no live claim's support reaches a rejected claim (inferences only rejected
    claims rely on may keep rejected inputs); a claim's inferences may consume claims of its
    own variable or of declared `inputs` only (`undeclared_dependency`). A mention-level claim names a registered,
    non-rejected reading of a span of a permitted `mention_type` (`missing_reading`), and
    cites the inclusions at the span's hull or every fragment (`uncited_mention`). Required
    attributes must be present with a permitted value, compared with strict types
    (`attribute_value_not_permitted`), and, unless `lenient`, have a cue cited under
    `attribute:<category>` (`missing_attribute_evidence`). With `requires_section`, every
    inclusion a claim cites directly or through its own inferences lies in a section of a
    listed category (`citation_outside_section`);
  - support traversal, and `mentions_at` / `readings_of` from a source leaf to the spans and
    readings at its position;
  - snapshot (format version 3, bound to the set's name, version, and `schema_ref`) and
    restore against authoritative context.
- `amber.tools.commit.commit_claim`: admits a proposed claim only with valid, complete support.
  It takes a `Task` or a variable name, and `reading_id` for mention-level variables.
- `amber.tools.outcomes.no_claim`: records the three clinical non-answers and never creates a
  null claim. Only runtimes record `failed`.
- `amber.cases.create_case_result` / `load_case_result`: build and revalidate results against
  caller-supplied `Case`, `Source`, and `Task` or `VariableSet`. Final claims must be
  non-rejected claims of their own variable; a mention-level variable never reports
  `conflicting_evidence`; loading against a set whose name, version, or `schema_ref` differs
  from the result's is `variable_set_mismatch`.
- `amber.graph_errors`: stable, source-free diagnostic codes.

Structural validity and source traceability do not establish semantic support or clinical
correctness. Those are measured separately.

## Interfaces (`amber.interfaces`) and references (`amber.reference`)

Protocols are structural and experimental until a real consumer uses them. Reference
implementations are synthetic and are not loaded by `import amber`.

- **`SourceResolver`**: `resolve(source_id) -> Source | None`. Reference: `InMemoryResolver`.
- **`Store`**: cases bound to their variable set, results keyed by `(case_id, run_id)`,
  append-only events with the spans they name, Examples, and label records. Every read
  revalidates through amber's load boundaries with the caller's resolver (`source_unavailable`
  when it returns nothing, `source_hash_mismatch` when it returns another source). Reference:
  `InMemoryStore`, which:
  - refuses a changed case, a second split for a `(dataset, source)` pair, and a second
    `schema_ref` for one variable-set `name@version`;
  - refuses a duplicate `(case_id, run_id)`, a duplicate or out-of-order event, and partial
    batches;
  - projects exactly as `replay` does, for every actor and `as_of_event`;
  - accepts an Example only after its events, with its case's split and a matching
    `example_id`, and only if it equals the replay of its events (`unsupported_tier`
    otherwise); `put_example` takes a resolver for that replay;
  - returns as `current_example` the snapshot at the case's latest event, or `None` while that
    snapshot is not a valid Example.

  `put_case`/`get_case` and `put_label_record`/`label_records` extend the design's listing,
  because split checks need the case and label records share its split.

## Conformance kit (`amber.conformance`)

- **Catalog**: the 101 cases of contract v0.1 §7 as data (`catalog.json`, generated from the
  design and checked against it), with `catalog_revision()` hashing every case's ID, level,
  targets, and text.
- **Fixtures**: deterministic synthetic sources, variable set, cases, results, spans, and
  event scenarios (silver import, single-review rise, blind streams, adjudication), with
  `fixtures_ref()` hashing them.
- **Checks**: implementation cases run against a consumer's declared targets and fail with
  source-free codes. Implemented today for `Store`: ANN-05, ANN-16, ANN-17, IFC-07, IFC-11,
  IFC-12. A store rejects by raising; the kit does not depend on its exception types.
- **`ConformanceReport`**: subject, amber version, declared targets, catalog version and
  revision, `fixtures_ref`, and per case `passed`, `failed` (with a code), `not_applicable`
  (core cases, which amber's own suite covers, and cases for undeclared targets), or
  `not_implemented` (applicable cases the kit cannot check yet, including every bundle
  case). `run_checks(subject)` builds one without pytest.
- **pytest plugin** (`conformance` extra; `pytest11` entry point, inert without
  `--amber-conformance`): a consumer returns a `Subject` (name, version, a factory per
  target) from `pytest_amber_conformance_subject(config)` in its root `conftest.py`, runs
  `pytest --amber-conformance --amber-conformance-report=report.json`, and gets one test per
  check plus the report.

## MLflow plugin (`tracking` extra)

- `amber.mlflow_ext.context.AmberRunContextProvider` adds the `amber.version` tag.

## Planned contract work

These items are tracked in Beads (`bd list --status=open`):

- the `PolicyGate` protocol and its reference implementation;
- claims that rely on readings and required attributes (`StructuredEvidence` is deferred to a
  later minor; contract v0.1 §2.6);
- derivations and rule replay;
- the remaining interfaces (`Extractor`, `Provider`, `Exporter`, `Scorer`, `ReviewBackend`,
  `Labeler`, `PolicyGate`);
- conformance checks for the remaining targets and for bundles;
- MLflow plugins for evaluation, datasets, the loupe bridge, and deployment.

Each item becomes stable only under the rules in the [charter](charter.md#rules-for-growth).
