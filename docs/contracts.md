# Contracts

This document describes the contract that amber-nlp implements today. Planned additions are
listed at the end and tracked in Beads. The pre-reset specification is preserved in
[history/02-v1-schemas-and-tools.md](history/02-v1-schemas-and-tools.md) as design input; it is
not binding where it describes pipelines, agents, tables, or tools that amber-nlp no longer owns.
Upgrading code or stored payloads from before contract v0.1 is covered in
[migrations/contract-v0.1.md](migrations/contract-v0.1.md).

Every model is Pydantic v2 with `extra="forbid"`. Value objects are frozen, and nested values
resist mutation. Offsets are end-exclusive Python code points into the immutable
`Source.text`.

## Identity (`amber.ids`)

Content-addressed IDs hash canonical JSON as defined by RFC 8785 (`canonical_json`,
`canonical_sha256`): keys sorted by UTF-16 code units, ECMAScript number form, no Unicode
normalization. Timestamps must be timezone-aware and hash as UTC with six fractional digits and
`Z`. NaN, infinities, lone surrogates, and integers beyond ±(2⁵³−1) are rejected. Because the
ECMAScript form writes a float such as `5.575263392078219e16` without a fraction, canonical
text is read back with `parse_canonical_json`, which returns integer literals beyond that range
as floats, so parsing and re-serializing gives the same text (bundle JSON columns use it).

| Identifier | Derivation |
|---|---|
| `source_id` | Canonical SHA-256 of `patient_id`, `kind`, `external_id`, `text`, and `record` only when set |
| Inclusion `evidence_id` | Canonical SHA-256 of `source_id`, `start`, `end` |
| `mention_id` | Canonical SHA-256 of `source_id`, `start`, `end`, `mention_type`, and `fragments` only when nonempty |
| structured `evidence_id` | Canonical SHA-256 (helper exists; the model is deferred) |
| `claim_id`, inference `evidence_id` | Canonical ULID |
| `schema_ref` | `module:Class@sha256(model JSON schema)` for an answer model |

**Producer references** (`parse_producer`) name who produced or decided something:
`rule:<name>@<version>[+<hash>]`, `model:<name>[@<version>]`, `human:<user>`, or
`tool:<name>[@<version>]`. `producer_configuration` returns the `model:`/`rule:` keys used to
refuse scoring a configuration against its own silver references; rule hashes are ignored.

## Wire format (`amber.schemas._wire`, `amber.wire`)

- Every JSON record (`CaseResult`, `Example`, `AnnotationEvent`, `LabelRecord`, `Decision`,
  VariableSet JSON) carries `schema_version: "amber/0.1"`. Readers accept this major at a
  lower or equal minor, normalize it, and reject anything else.
- JSON timestamps are UTC with `Z` and six fractional digits; naive times keep their ISO form.
- A record's JSON is its `model_dump_json()`; golden fixtures in
  `amber/conformance/golden/` pin the bytes and key order (WIR-14).
- `variable_set_json` writes answer models as their JSON Schema plus `schema_ref`;
  `load_variable_set` rebuilds the set only from the matching answer-model classes
  (`unknown_answer_model`, `answer_model_mismatch`, `variable_set_mismatch`).

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
    non-rejected claim IDs, and an optional `derivation` (experimental), omitted from JSON
    when unset so records without one serialize exactly as before.
  - `Derivation` (experimental): `kind` (`rule`, `model`, `human`), `name`, `version`,
    `inputs` (role → every input ID it consumed, possibly none), `parameters` (rule
    configuration only), and `defaults_used` (role → value assumed). Every listed ID must be
    among the inference's inputs; only `rule` derivations use defaults, and a defaulted role
    lists no inputs.
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
  It also records the run's `Budget` (`max_calls`, `max_tokens`, `max_retries`, `max_seconds`)
  and `Usage`; a result whose usage exceeds its budget must have every variable `failed` with
  `failure_kind="budget"`, or it fails with `budget_exceeded`.
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
  one event: `example_id` (canonical SHA-256 of the content, without `example_id` and
  `schema_version`, record lists in canonical order: `amber.ids.example_id`), the variable
  set, `split`, one reference per final variable, and the retained claims, mentions,
  readings, evidence, and edges. Any edit to a value, status, tier, outcome, attribution, or
  timestamp fails with `example_id_mismatch`, checked after all other validation.
  `amber.examples.mint_example` validates the same way and assigns the content ID (used by
  `example_from_projection`, and to re-key Examples). Issued only by `load_example` and
  `mint_example`; loading revalidates the graph against the authoritative `Case`, `Source`,
  and `VariableSet` and checks that:
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
- `EvidenceGraph.commit_claim(..., derivation=...)` (and the `commit_claim` tool) records a
  derivation on the claim's inference; with one, `evidence_ids` may name claims too.

- `EvidenceGraph(..., ids=...)` mints claim and inference IDs from the given source (each must
  be a canonical ULID, `invalid_schema` otherwise); `amber.ids.seeded_ulids(seed)` is a
  reproducible source, so the same commits give the same graph.

## loupe adapter (`amber.loupe`, experimental)

`AmberClaimsAdapter` is loupe's `AnnotationAdapter` for claim and outcome review of
note-level variables, loaded by loupe from `"adapter": "amber.loupe:AmberClaimsAdapter"`;
amber never imports loupe. Its `project_configuration` names the variable set by import path
and pins its `schema_ref`, and gives the dataset, manifest hash, sensitivity, policy version,
and the review destination and zone stamped on provenance. Each loupe record must be the
amber note source (`record_id` equal to the `source_id` of its text with
`metadata.patient_id` and `metadata.external_id`); `metadata.case_id` and `metadata.split`
complete the case.

- `build_result(request, example, inventory, configuration_sha256)` takes the reviewer, a
  whole-note confirmation, and one entry per final variable: `answered` with claims (value,
  rationale, evidence spans as code-point offsets plus quote, answer fields mapped to evidence
  indexes), or `not_mentioned`, `insufficient_evidence`, or `conflicting_evidence` (with
  evidence) and a reason; `failed` is never a review outcome. Every quote must equal the note
  text at its offsets, and the result is built only through `exact_quote`, `commit_claim`,
  `no_claim`, and `create_case_result`, with `human:` provenance and the review destination.
- IDs come from a seed over the record, configuration, reviewer, and normalized request, and
  provenance carries a fixed time, so the same request always builds the same bytes (loupe
  rebuilds at publication).
- The result (`amber-loupe-claims-v1`) keeps loupe's envelope: `processing_state` (`partial`
  when any variable is `insufficient_evidence`), `issues`, `mentions` (the normalized
  per-variable editor items, which `editable` returns unchanged), `case_result` (the
  `CaseResult` JSON), `record_id`, `source_sha256`, and `producer`.
- `validate_result` accepts a stored result only if rebuilding it from its own items gives
  identical bytes and its `CaseResult` loads at amber's boundary. `binding()` hashes the amber
  version, adapter version, variable set, and manifest hash.
- Errors are `AdapterError` with a source-free code (`quote_mismatch`, `invalid_span`,
  `missing_field_evidence`, `invalid_outcome`, `source_identity_mismatch`, ...).
  Mention-level variables are refused (`unsupported_variable_level`).

Export and import (`amber.loupe.reference`):

- `source_manifest(cases, *, name, variables, project_configuration, streams, synthetic,
  gate, data, destination, now)` builds a loupe source manifest for claim mode after a
  `source_text` decision for the `review` destination: one record per note (`record_id` =
  `source_id`, exact text, metadata with patient, external ID, case, split, sensitivity,
  dataset, and variable set; no labels).
- `import_reference(reference, *, resolver, variables)` imports a `loupe-reference-v1`
  publication. The publication's `snapshot_sha256` and `adapter_sha256` must match its content
  and the installed adapter; each record's text must be its resolved source; every considered
  review is rebuilt by the adapter and reloaded at amber's boundary, so quotes are regrounded
  and tampered values, offsets, or failed outcomes are refused. Reviews become reviewer events
  in their streams (claims with support, outcomes, `review.submit` with the submission ID).
  With one stream the reviewer sets `single_review`; otherwise the adjudicator rejects the
  unselected reviews' claims, sets the selected outcomes, resolves with the publication hash
  as `external_ref`, and sets `adjudicated`. `unresolved` and `excluded` records are resolved
  with their reason and give a `RecordDiagnostic` instead of an Example. Event IDs increase
  per case and event times are ordinal, so imports are reproducible. Returns
  `ImportedReference(examples, events, spans, diagnostics)`; `ReferenceError` codes are
  source-free.
- `freeze_manifest` returns a manifest in loupe's frozen form (`loupe-source-v1`, per-record
  `source_sha256`, `snapshot_sha256`); loupe's tests check it matches loupe's `freeze_source`.
- `proposal_request(result, *, reviewer)` turns a note-level `CaseResult` into the claim-mode
  request that reproduces it (final claims with their rationale, evidence offsets, and field
  links; non-answers with reasons; conflicts with their evidence), or `None` when any final
  variable failed. `proposals_file(frozen, results, *, producer, streams,
  configuration_frozen, gate, data, destination, now)` decides `derived_text` for the review
  destination and builds a `loupe-proposals-v1` file of adapter results keyed by record ID,
  bound to the frozen snapshot hash (loupe validates each one and suppresses test-split
  proposals until the configuration is frozen).
- `LoupeReviewBackend` implements `ReviewBackend` over these files: `export_cases` writes the
  frozen `source.json` into a batch directory and, when cases carry results, `proposals.json`
  for `loupe setup --proposals` (it needs `proposal_streams` and a `producer`); and
  `import_references` reads the publication's `reference.json` placed beside it.

## Rules (`amber.rules`, experimental)

- `RoleSpec` (`source`: a variable or `evidence:inclusion`; `cardinality` `one` or `many`;
  `on_missing` `refuse`, `default`, or `pass_none`) and `RuleSpec` (`name`, `version`, roles,
  `outputs`, and `defaults` exactly for the `default` roles). `RuleSpec.check(variables)`
  requires the output variable to exist and to declare each role's variable among its
  `inputs` (`undeclared_dependency` otherwise). `RuleResult` is a value or an unanswered status with a
  reason. `Registry` keys rules by `name@version` and refuses a second spec under one key.
- `derive(graph, rule, *, provenance, parameters, upstream, evidence)` gathers each role's
  candidates (non-rejected claims of its variable, or the named inclusions) and reduces them:
  `many` gets every candidate in ID order; `one` gets the lowest-ID candidate when values are
  equal (all of them listed) and is a conflict when they differ. A conflict gives
  `conflicting_evidence` citing every conflicting claim's source leaves and the rule is not
  applied; a missing `refuse` role gives `insufficient_evidence` naming it; `default` passes
  and records the declared default; `pass_none` passes `None` or `()`. Otherwise the rule's
  value is committed with a `rule` derivation, or its status returned as a `DerivedOutcome`.
  `derive_all` runs rules in dependency order and propagates conflicts through intermediate
  variables.
- `replay_derivations(result_or_example, *, registry, variables)` recomputes every
  registered rule derivation (roles must equal the spec's, defaults must be declared and
  equal, inputs must belong to the role's variable, a `one` role's listed values must agree)
  and compares the value with the derived claims; any difference is `derivation_mismatch`.
  Unregistered rules and model or human derivations are not replayed. A variable role must
  list exactly the record's non-rejected claims of its variable, so an omitted candidate (which
  could hide a conflict) or a claim added or rejected after the derivation, for example in
  review, fails replay until the rule is applied again. Bundles carry derivations in the `evidence` table's
  `derivation` column.

Structural validity and source traceability do not establish semantic support or clinical
correctness. Those are measured separately.

## Bundles (`amber.bundles`; Parquet files need the `bundles` extra)

- A bundle is one dataset and one variable set: a manifest plus the §5.4 tables (`sources`,
  `mentions`, `mention_readings`, `claims`, `evidence`, `evidence_edges`, `cases`,
  `variable_outcomes`, and per kind `results` (run), `examples` (gold), or
  `annotation_events` (review); `decisions` optional). Tables hold `text_hash`, never full
  source text.
- `build_bundle` assembles one from validated records: a `run` bundle holds the results of
  `producer.run_id`; a `gold` bundle one Example per case, with optional events; a `review`
  bundle events and the spans they name. Cases must match the manifest's dataset,
  sensitivity, and variable set, one case per source.
- `check_bundle` rejects missing tables or columns, wrong types, nulls, duplicate keys,
  unknown non-`x_` columns, a `content_sha256` that differs from the key-sorted canonical
  rows, payload kinds other than those derived from the schemas and answer models, and case
  rows outside the manifest's scope. It runs before any validation.
- `load_bundle` with a resolver checks every source against its `text_hash`
  (`source_unavailable`, `source_hash_mismatch`), re-mints spans, and revalidates results,
  Examples (against their events when present), events, and decisions through amber's
  boundaries. Without a resolver it returns an `UnvalidatedBundle` whose rows those
  boundaries refuse.
- `amber.bundles.parquet.write_bundle` / `read_bundle`: JSON columns as canonical JSON
  strings (non-canonical JSON is rejected), timestamps as `timestamp[us, UTC]`, rows sorted
  by key, optional per-file `file_sha256`, and `x_` columns preserved.
- Results and Examples list their records in key order, so equal content has one JSON form.
- **`amber-labels/0.1`** (`build_labels_bundle`, `load_labels`): bundle kind `labels`, holding
  only files that are `labels_only` by construction (`cases`, `results`, `examples`,
  `evidence_edges`, `decisions`, `claims` when every answer model is closed, and
  `label_records` from `labels_only_view`). It is for tracking servers, cannot be validated
  against sources, and any other or `derived_text` table is rejected. Variable sets with open
  answer models cannot produce one.

## Interfaces (`amber.interfaces`) and references (`amber.reference`)

Protocols are structural and experimental until a real consumer uses them. Reference
implementations are synthetic and are not loaded by `import amber`. They are for synthetic data
only: every entry point that sees a case, data policy, run, or result (the store's `put_case`,
the extractor's and labeler's runs, scorers, `FileReviewBackend.export_cases`, and
`LocalExporter.export`) raises `ReferenceUseError` (`synthetic_only`) for any other
sensitivity. They ship with the core; `amber[reference]` installs what all of them need
(`bundles`, for `LocalExporter`).

- **`PolicyGate`**: `check`/`require` against a matrix. Reference: `amber.policy.StaticGate`.
  `PolicyViolation` carries the first refusal and, for multi-file transfers, every decision.
- **`Provider`**: a model endpoint with a `ProviderDestination` (zone, model, declared
  capabilities: `schema_constrained`, `tools`, `logprobs`, `batch`, `adapters`) and
  `complete(ProviderRequest) -> ProviderResponse | ProviderFailure`. It never sees a `Source`,
  and refuses a request needing an undeclared capability rather than answering with
  unconstrained text. Reference: `ScriptedProvider` (canned outcomes, offline).
- **`amber.providers.call_provider`**: the enforcement point for every model call. It requires
  a permitting decision for `source_text` to the provider's destination (`PolicyViolation`
  otherwise, before anything is sent), refuses missing capabilities without sending
  (`capability_missing`), parses schema responses (`parse`/`invalid_json`), retries only
  retryable failures and only while the `Budget` allows (`max_retries`; none without a
  budget), stops at `max_calls` or once usage exceeds the budget (`budget`), and stamps the
  `Provenance` for nodes built from the response. `ProviderFailure.kind` (`parse`,
  `provider`, `budget`) is the result's `failure_kind`; `UsageMeter` accumulates the run's
  `Usage`.
- **`Extractor`**: `name`, `version`, declared `destinations` and `capabilities` (`mentions`,
  `note_claims`, `derivations`, `batch`), and `run(case, source, variables, *, gate, budget,
  context) -> CaseResult`. It returns exactly one result per case and never raises for an
  expected failure: a policy refusal, provider or parse failure, or exhausted budget is a
  result whose final variables are `failed` with that `failure_kind`, stamped from the
  `RunContext`. With `batch`, `run_many` returns one result per case in input order.
  `amber.extraction` provides `failed_result` and `local_provenance`. Reference:
  `DeterministicExtractor`, which asks its provider for a schema-constrained answer per
  note-level variable, grounds the quote with `quote()`, and records it with `commit_claim` or
  `no_claim`; a context whose sensitivity differs from the case's is a runner error and raises.
- **`Scorer`**: `name`, `needs`, and `score(predicted, reference, *, variables) ->
  ScoreReport`. `amber.scoring` defines `ScoreReport` (metrics as numerator and denominator
  with a kind and optional variable, the `example_id`s scored against, the tier mix,
  `provisional` exactly when a reference is below `adjudicated`, and `inconclusive` metrics)
  and the rules every scorer applies: `refuse_self_reference` (a silver reference sharing the
  predictions' `model:`/`rule:` configuration, rule hash ignored, including attribute
  `assigned_by`) and `require_move_on` (under a move-on decision every `single_review`
  snapshot includes it). References: `StructuralValidityScorer` (outcome mix, failure kinds,
  grounding failures) and `LabelCorrectScorer` (label accuracy, coverage, omissions, tokens
  per case, and evidence overlap against Examples, `inconclusive` against label records).
- **`Labeler`**: `name`, `version`, `tier`, and `label(case, source, variables, *, gate,
  context)` returning `(events, Example)`, a `LabelRecord`, or a source-free `LabelerFailure`
  (`amber.labeling`). Models, rules, and importers are `silver`; `admit_labeler` rejects a
  labeler declaring a human tier (`human_tier`). An Example's events begin with
  `import.result` and replay to it. `import_events` turns a teacher's validated `CaseResult`
  into that log (`import.result`; `mention.create` and `reading.create` per live reading; one
  `claim.create` per live claim with its inclusions, inferences, and edges in dependency
  order; `outcome.set` and `tier.set` silver per variable) plus its spans and Example, and
  refuses a result with a `failed` variable; `spans_of` lists the spans an Example's events
  name. Reference: `ImportLabeler`, which reads stored results or label records from a JSON
  file, revalidates them against the authoritative case, source, and variable set, and
  returns `missing`, `unsupported`, `invalid`, or `failed_result` failures otherwise.
- **`ReviewBackend`**: a `review` destination, `export_cases(cases, *, variables, streams,
  gate, data) -> ReviewBatchReceipt`, and `import_references(receipt, *, resolver) ->
  (examples, events)`. Export decides `source_text` for every case and `derived_text` for
  every proposal (`CaseResult`) before sending anything; the receipt (`amber.review`,
  `labels_only`) lists the batch, destination, location, variable set, cases, streams, and
  decisions. Import returns validated Examples and the full event log, or raises
  `ReviewImportError` (`receipt_mismatch`, `invalid_batch`, `invalid_spans`,
  `source_unavailable`, `foreign_provenance`, or `invalid_events` with the replay code);
  nothing partially validated is returned. `amber.review` holds the shared rules:
  `load_spans` re-mints span rows against the authoritative source, every event must carry
  the review destination and the batch's policy version, and `reviewed_example` gives an
  Example only for a complete projection whose references all have human tiers (unsubmitted,
  unresolved, excluded, and silver-only cases keep their events and give none). Reference:
  `FileReviewBackend`, which round-trips through a batch directory of JSON files (staged and
  renamed into place), with `amber.reference.review.simulate_reviews` as a synthetic review
  service: a proposal is imported and accepted unchanged, a case without one is confirmed
  `not_mentioned`; one stream gives `single_review`, several give blind submissions and an
  adjudication (`accepted`, `unresolved`, or `excluded`).
- **`Rule`** (`amber.rules`, re-exported here): `spec: RuleSpec` and a pure `apply(inputs,
  parameters) -> RuleResult`. Reference: `AjccStub`, an invented, non-clinical stub table
  over the conformance fixtures' `STAGE_VARIABLES`.
- **`SourceResolver`**: `resolve(source_id) -> Source | None`. Reference: `InMemoryResolver`.
- **`Exporter`**: `export(items, target, *, gate, data, now) -> ExportReceipt`. It derives each
  output file's payload kind from its schema, gates every file (the manifest included) before
  writing anything, and writes all of them or none; a refusal raises `PolicyViolation` with
  every decision. `ExportReceipt` lists each file's payload kind, decision, and
  `content_sha256`, and where the export went. Reference: `amber.reference.exporter.LocalExporter`
  (`bundles` extra), writing `amber-bundle/0.1` (run or gold bundles) or `amber-labels/0.1`
  directories through a staging directory.
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
  source-free codes. Implemented today for `Store` (ANN-05, ANN-16, ANN-17, IFC-07, IFC-11,
  IFC-12), `Provider` (IFC-04, IFC-12), `Extractor` (IFC-02, IFC-03, IFC-12, IFC-13,
  POL-08, MEN-11; the kit supplies scripted providers to the consumer's extractor factory and,
  for capability fixtures, answers every schema request with a minimal instance whose strings
  are the known span), `Scorer` (IFC-09, ANN-11,
  ANN-12, ANN-17), `Labeler` (IFC-06), `ReviewBackend` (IFC-10; the factory returns
  the backend and a callable that completes the reviews for a receipt), and `Rule` (VAR-09,
  IFC-05; the factory returns the rule, a result with its derivations, and the variable set;
  purity runs `apply` twice and once more with the common clock and randomness entry points
  raising). A case
  shared by several targets records which declared targets failed it, so `conforms(target)`
  is per target, and is reported `passed` only when every declared target it applies to was
  checked. A store rejects by raising; the kit does not depend on its exception types.
- **Bundle checks** (`amber.conformance.bundle`, `bundles` extra): `run_bundle_checks(directory,
  *, variables, resolver=None)` runs the bundle cases against a written bundle. The reader's
  and `check_bundle`'s diagnostics fail the case they belong to (WIR-02 version, WIR-03
  structure, WIR-05 and WIR-11 payload kinds, WIR-06 hashes and case scope, WIR-07 canonical
  JSON, WIR-09 unknown columns); WIR-01 requires key-sorted rows and an unchanged write and
  read (`x_` columns included); WIR-08 and WIR-10 check evidence kinds, event actions, and
  claim values directly. With a resolver, WIR-04 requires every record to revalidate (any load
  failure is reported there with the boundary's code, except `budget_exceeded` under WIR-13)
  and, for run bundles, WIR-13 requires the revalidated results to rebuild identical tables;
  without one both are `not_applicable`. Cases left undecided by an earlier failure fail with
  `blocked`. WIR-12 (migration) is `not_implemented`.
- **`ConformanceReport`**: subject (`{kind: "implementation", name, version}` or `{kind:
  "bundle", manifest_content_sha256}`, the SHA-256 of the manifest's canonical JSON), amber
  version, declared targets, catalog version and revision, `fixtures_ref`, and per case
  `passed`, `failed` (with a code), `not_applicable` (core cases, which amber's own suite
  covers; bundle cases in an implementation report and the reverse; and cases for undeclared
  targets), or `not_implemented` (applicable cases the kit cannot check yet).
  `run_checks(subject)` builds one without pytest; `conforms()` without a target asks whether
  anything failed.
- **pytest plugin** (`conformance` extra; `pytest11` entry point, inert without
  `--amber-conformance`): a consumer returns a `Subject` (name, version, a factory per
  target) from `pytest_amber_conformance_subject(config)` in its root `conftest.py`, runs
  `pytest --amber-conformance --amber-conformance-report=report.json`, and gets one test per
  check plus the report. With `--amber-conformance-bundle=DIR` and a
  `pytest_amber_conformance_bundle(config)` hook returning a `BundleUnderTest` (variable set,
  optional resolver), it instead runs one test per bundle case and writes the bundle report.

## Evaluation harness (`amber.evaluation`)

A binary-answer harness following CORAL protocol 1.1.0: counts `N`, `B`, `P`, `Q`, `A`, `C`,
`A_pos`, `TP`, `FP`, `O`, execution failures by kind, unattempted cases, and missing gold;
measures (precision, PPV, recall, omission rate, coverage, correct yield, false positives
defined only when `Q > 0`, execution failures, mean cost per eligible case); and
consumer-supplied `GateSpec`s. A zero denominator is undefined, never 0 or 1. Each gate is
`met`, `unmet`, or `inconclusive` on the observed value; any reference below `adjudicated`
makes it provisional, and a provisional pass is `inconclusive` with `observed_pass` set.
Evidence measures are inconclusive against label-only references. The overall status is
`unmet` if any gate is violated and `met` only when every gate is met with complete gold and
attempts. Wilson intervals accompany every defined case-level proportion, and a seeded
(20260915) case-level bootstrap reports percentile intervals with counts of undefined
replicates. `gold_from` and `prediction_from` adapt Examples, label records, and results.

## MLflow plugins (`tracking` extra)

- `amber.mlflow_ext.context.AmberRunContextProvider` adds the `amber.version` tag.
- `amber.mlflow_ext.evaluation.validity_scorer`: an OSS `@scorer` that revalidates a
  `CaseResult` JSON against its case, source, and variable set and returns a boolean
  `Feedback` with a source-free rationale code.
- `amber.mlflow_ext.evaluation.log_evaluation`: logs an `EvaluationReport` or `ScoreReport`
  (values, numerators, denominators, intervals as metrics; gate statuses, provisional flag,
  and tier mix as tags) to the active run, after the gate permits both `metrics` and
  `labels_only` to the destination.
- `amber.mlflow_ext.tracking`: gated run and trace logging. `local_tracking(directory)`
  points MLflow at a SQLite store on this machine and returns the `local:mlflow` tracking
  destination (accepting `metrics` and `labels_only` unless told otherwise), the default for
  development. Every function checks the destination is `tracking` and, for a `local` one,
  that MLflow's tracking URI is a local file or SQLite store (`TrackingError`:
  `not_a_tracking_destination`, `tracking_uri_not_local`, `no_active_run`).
  `log_run(context, *, providers, gate, destination, now, prompt_versions, adapter_policy,
  producer)` logs the run ID, dataset and manifest hash, policy version, sensitivity,
  restrictions, prompt versions, adapter policy, and each provider's kind, zone, model, and
  capabilities as `labels_only` params after the gate permits them; `log_decisions` stores
  decisions as `amber/decisions.json`. `traced_provider_call` wraps `call_provider` in an
  MLflow span: `labels_only` must be permitted before the call, the span carries model, zone,
  attempts, tokens, and failure or refusal, and the prompt and response are attached only when
  `source_text` is permitted to the tracking destination (`amber.content` says `included` or
  `redacted`).
- `amber.mlflow_ext.datasets`: Examples as MLflow GenAI evaluation datasets. Each record has
  `inputs` (`case_id`, `example_id`, and the note `text` only when a resolver is passed as
  `texts`) and `expectations` (`amber_example`, the full Example JSON, plus per final variable
  `{tier, status, values}`). `create_examples_dataset` requires one variable set and gates
  `derived_text` (and `source_text` with text) before creating anything; tags record the
  variable set and tier counts. `load_examples_dataset` / `records_to_examples` reload every
  record through `load_example` against the authoritative case, source, and variable set
  (`DatasetError`: `invalid_record`, `unknown_case`, `source_unavailable`,
  `variable_set_mismatch`); loading checks structure, grounding, and tiers, and with the stored
  `events` per case each Example must also equal the replay of its events, which is what pins
  reviewed values.

## Planned contract work

These items are tracked in Beads (`bd list --status=open`):

- structured evidence (deferred to a later minor; contract v0.1 §2.6);
- conformance checks for the remaining targets and for bundles;
- MLflow plugins for evaluation, datasets, the loupe bridge, and deployment.

Each item becomes stable only under the rules in the [charter](charter.md#rules-for-growth).
