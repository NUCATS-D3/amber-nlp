# Contract v0.1 design

Status: draft in progress, 2026-10-30. This document is written in dependency order; each
section is reviewed as drafted. Sections marked *pending* are not yet written. The
[plan](../plan.md) describes the phases; the [charter](../charter.md) sets scope.

Examples use invented data only. Stand-in variables for the design checks are: CORAL current
progression (single boolean), a lapidary `ds_findings_v1` mention-level finding, and pathologic
stage group derived from T/N/M mentions (layered).

Sections: [1 Policy](#1-policy) · [2 Mentions and structured evidence](#2-mentions-and-structured-evidence) · [3 Layered
variables](#3-layered-variables) · [4 Annotated data](#4-annotated-data) · [5 Wire formats](#5-wire-formats) · [6 Interfaces](#6-interfaces) · 7 Conformance catalog (pending)

---

## 1. Policy

### 1.1 Purpose

Policy decides, before any data leaves the process, whether a **payload** with a declared
**sensitivity** may go to a **destination** with a declared **zone**, and records that decision
in provenance. It replaces nothing in the evidence contract: a permitted call still produces
candidates that must be grounded and committed.

Policy is pure data and pure functions in the core. It performs no I/O, holds no credentials,
and knows nothing about HTTP, SDKs, or MLflow. Enforcement points (lapidary's runner, loupe's
export, amber's MLflow plugins) call it; they are the only places that can actually move data.

### 1.2 Vocabulary

| Term | Meaning |
|---|---|
| **Sensitivity** | Declared class of a dataset's content: `synthetic`, `deidentified`, `limited`, `phi`. Never inferred from content. Already in `amber.schemas`. |
| **Zone** | Declared trust boundary of a destination: `local`, `institution`, `external_baa`, `external`. A property of a configured endpoint, not of a model family. Already in `amber.schemas`. |
| **Destination** | Anything data can be sent to: a model provider, a structured-data source (EDW query), a tracking/tracing server, an artifact store, a judge model, a review service, an export path. |
| **Payload kind** | What is being sent: `source_text`, `derived_text` (quotes, offsets, snippets, candidate spans), `labels_only` (values and IDs with no text), `metrics` (aggregates), `model_weights` (parameters or adapters trained on the data). |
| **Restriction** | A declared obligation that comes with a dataset or a destination beyond the zone matrix: a data-use agreement, a license term, an IRB stipulation, or a no-training clause. |
| **Decision** | The outcome of a check: `permitted` or `refused`, with a stable reason code, the matched rule, and the policy version. |

### 1.3 Models

```python
class Destination(BaseModel):                # frozen, extra="forbid"
    name: str                                # "vllm-lab-gpu", "mlflow-onprem", "loupe-institution"
    kind: Literal["model", "structured_source", "tracking", "artifact_store",
                  "judge", "review", "export"]
    zone: Zone
    accepts: frozenset[PayloadKind]          # payload kinds this destination may receive
    retains: bool                            # stores payloads beyond the request (default True)
    trains_on_inputs: bool = False           # vendor may use inputs for training
    tags: frozenset[str] = frozenset()       # free labels matched by restrictions, e.g. "nm-azure-baa"

class Provider(Destination):                 # kind == "model" or "structured_source"
    capabilities: frozenset[Literal["schema_constrained", "tools", "logprobs",
                                    "batch", "adapters"]]
    model: str | None                        # model identifier; None for structured sources

class Restriction(BaseModel):                # frozen
    id: str                                  # "physionet-chdl-1.5.0", "irb-STU0001234-no-vendor"
    kind: Literal["license", "dua", "irb", "contract", "internal"]
    forbids_zones: frozenset[Zone] = frozenset()
    forbids_kinds: frozenset[Destination.kind] = frozenset()
    forbids_training_on_inputs: bool = False
    requires_tags: frozenset[str] = frozenset()   # destination must carry all of these
    forbids_payloads: frozenset[PayloadKind] = frozenset()
    valid_until: datetime | None = None      # e.g. IRB approval end; checked against a supplied clock
    note: str                                # human-readable summary; no secrets, no PHI

class DataPolicy(BaseModel):                 # attached to a dataset manifest; frozen
    sensitivity: Sensitivity
    restrictions: tuple[Restriction, ...] = ()

class PolicyMatrix(BaseModel):               # per deployment; frozen
    version: str                             # semver of this deployment's policy configuration
    allowed: Mapping[Sensitivity, frozenset[Zone]]

class Decision(BaseModel):                   # frozen
    permitted: bool
    reason: Literal["ok", "zone_not_allowed", "payload_not_accepted",
                    "restriction_zone", "restriction_kind", "restriction_training",
                    "restriction_tag", "restriction_payload", "restriction_expired",
                    "unknown_sensitivity"]
    restriction_id: str | None
    policy_version: str
    destination: str
    zone: Zone
    sensitivity: Sensitivity
    payload: PayloadKind
    checked_at: datetime                     # the clock value supplied by the caller
```

The default matrix matches the existing invariant:

| Sensitivity | Permitted zones |
|---|---|
| `synthetic` | `local`, `institution`, `external_baa`, `external` |
| `deidentified` | `local`, `institution`, `external_baa` |
| `limited` | `local`, `institution` |
| `phi` | `local`, `institution` |

A deployment may **narrow** the default matrix but may not widen it without changing
`version` and recording the change; the conformance kit includes a check that the active matrix
is a subset of the default unless an explicit `widened_from_default: true` flag and a note are
present.

### 1.4 The check

```python
def check(matrix: PolicyMatrix, data: DataPolicy, destination: Destination,
          payload: PayloadKind, *, now: datetime) -> Decision
```

`now` is supplied by the enforcement point so the check stays deterministic and testable; the
core never reads the system clock.

Rules, applied in order; the first failure is the reason:

1. `data.sensitivity` must be in `matrix.allowed`; otherwise `unknown_sensitivity`.
2. `destination.zone` must be in `matrix.allowed[data.sensitivity]`; otherwise
   `zone_not_allowed`.
3. `payload` must be in `destination.accepts`; otherwise `payload_not_accepted`.
4. For each restriction on the data, in declared order:
   - if `valid_until` is set, `now` is not after it; otherwise `restriction_expired`;
   - the destination zone is not in `forbids_zones`;
   - the destination kind is not in `forbids_kinds`;
   - if `forbids_training_on_inputs`, the destination has `trains_on_inputs == False`;
   - the destination carries every tag in `requires_tags`;
   - the payload is not in `forbids_payloads`.
5. Otherwise `permitted` with reason `ok`.

`check` is total and deterministic. It never raises on a refusal; enforcement points raise
`PolicyViolation(decision)` themselves so the decision is always available for logging. A
refusal carries no source text.

**Payload downgrade is explicit.** A caller may not reclassify `derived_text` as `labels_only`
by stripping fields on its own judgement. The core provides one function,
`labels_only_view(result: CaseResult) -> LabelRecord`, that removes quotes, offsets, rationales,
and free text, and the conformance kit checks that its output contains no string that appears
in the source. Metrics are aggregates computed by scorers; they are `metrics` by construction.

**Model weights are payloads.** Parameters or adapters trained on a dataset carry that dataset's
`DataPolicy` (the most restrictive of all training datasets when several are combined) and pass
the same check as `model_weights` before being stored, logged, or shared. A destination that
accepts `model_weights` must be declared; the default artifact-store examples accept it only in
`local` and `institution`. Restrictions with `forbids_training_on_inputs` refuse the training
call itself, so weights that should not exist are never produced.

### 1.5 Where checks happen

| Enforcement point | Destination kind | Payload | Owner |
|---|---|---|---|
| Model call for extraction | `model` | `source_text` | Extractor implementation (lapidary) |
| Structured-source query | `structured_source` | `labels_only` (identifiers), result is new source data | Extractor implementation |
| MLflow run params/tags | `tracking` | `metrics` or `labels_only` | amber tracking plugin |
| MLflow traces with prompts | `tracking` | `source_text` | amber tracking plugin |
| Parquet tables as artifacts | `artifact_store` | `derived_text` (quote columns) | amber export/logging |
| LLM judge | `judge` | `derived_text` or `source_text` | Scorer implementation |
| Export to loupe | `review` | `source_text` | amber loupe bridge |
| Example/Dataset export | `export` or `artifact_store` | `derived_text` | amber dataset plugin |
| Adapter/model logging or registry | `artifact_store` | `model_weights` | training implementation (lapidary) via amber plugin |

**Audit.** Every `Decision`, permitted or refused, is an audit record. amber defines the record
and its `labels_only` serialization (it contains no source text by construction); the tracking
plugin logs decisions as run artifacts or tags, and each enforcement point is responsible for
persisting them where the deployment requires. amber does not implement an audit store.

Each enforcement point:

1. calls `check` with the dataset's `DataPolicy`, the destination, and the payload kind;
2. on refusal, raises `PolicyViolation` and records a `failed` outcome with
   `failure_kind="policy"` where a case is in progress;
3. on success, stamps `Provenance` as below, and proceeds.

A permitted model call does not authorize a tracking or review destination; each transfer is
checked separately, with its own destination record.

### 1.6 Provenance stamping

`Provenance` gains three fields:

```python
destination: str            # Destination.name that produced this node
policy_version: str         # PolicyMatrix.version in force
restriction_ids: tuple[str, ...] = ()   # restrictions that applied to the data
```

`zone` and `sensitivity` remain. `zone` is the destination's zone (where the inputs went);
`sensitivity` is the data's declared sensitivity. Human producers (`human:<user>`) stamp the
review service as the destination. Deterministic tools that send nothing anywhere use
`destination="local:<tool>"` with `zone=local`.

Existing `Provenance` records lacking the new fields fail validation after the change; this is a
breaking contract change and ships with a migration note (schema version bump in §5).

### 1.7 Dataset restrictions as data

Restrictions are declared on the dataset manifest (lapidary) and travel with the `DataPolicy`.
Examples, invented values:

```yaml
sensitivity: deidentified
restrictions:
  - id: physionet-chdl-1.5.0
    kind: license
    forbids_zones: [external]
    forbids_training_on_inputs: true
    note: "Credentialed Health Data License; no redistribution; online services per PhysioNet guidance"
  - id: coral-no-pseudolabel-mixing
    kind: internal
    forbids_payloads: []
    note: "Pseudo-labelled 200-note set never enters gold partitions (enforced by Labeler tier, noted here for audit)"
```

```yaml
sensitivity: phi
restrictions:
  - id: irb-STU0001234
    kind: irb
    forbids_zones: [external_baa, external]
    requires_tags: [nm-approved-endpoint]
    note: "IRB protocol permits approved institutional endpoints only"
```

The second example shows why the zone matrix alone is not enough: `phi` → `institution` is
permitted by the default matrix, but this IRB requires the specific tagged endpoints.

Restrictions never lower sensitivity. A deidentified derivative of a PHI dataset is a **new
dataset** with its own manifest, `DataPolicy`, and provenance link to its parent; the derivation
tool's output is not trusted to set `sensitivity` on the original.

### 1.8 What policy does not do

- It does not verify that a destination's declared zone is true. Deployment configuration is a
  trust boundary; the conformance kit tests the logic, not the infrastructure.
- It does not detect PHI in content. Sensitivity is declared.
- It does not authorize anything by itself. A `permitted` decision is necessary, not sufficient;
  the enforcement point still needs credentials and the human approvals outside the software.
- It does not cover agent tooling access to raw files. That is lapidary's guard script and
  OS-level separation, which are deliberately outside amber.

### 1.9 Design checks against the stand-in variables

- **CORAL progression:** `deidentified` data with the PhysioNet restriction. A `local` or
  `institution` model is permitted; an `external_baa` vendor is permitted by the matrix but
  refused if the deployment marks the vendor `trains_on_inputs=True`; `external` is refused by
  the restriction. Loupe hosted on an institutional MLflow server is a `review` destination in
  zone `institution`: permitted. Logging traces with prompts to a laptop MLflow is `tracking`
  in `local`: permitted; to a public MLflow would be `external`: refused.
- **Discharge summaries (`phi`, IRB restriction):** only tagged institutional endpoints; the
  Qwen vLLM server qualifies once tagged. Parquet tables with `quote` columns are
  `derived_text` and may go only to the institutional artifact store. `labels_only_view`
  output may go to the same tracking server as `labels_only`.
- **Stage group (layered):** the final inference step is a deterministic rule with
  `destination="local:ajcc_v8"`; no check is needed beyond stamping. Its inputs' provenance
  already carries the model destinations that produced the T/N/M mentions.

### 1.10 Conformance cases (policy)

1. Every cell of the default matrix: permitted and refused combinations.
2. A widened matrix without the explicit flag fails validation.
3. Payload not accepted by destination is refused even when the zone is allowed.
4. Each restriction rule refuses independently, with the matching `restriction_id`.
5. Restrictions apply in order; the first failure is reported.
6. `labels_only_view` output shares no substring of length ≥ 8 with the source text, across
   Unicode and CRLF sources.
7. A `Provenance` without `destination`/`policy_version` is rejected.
8. A `failed` outcome from a refusal has `failure_kind="policy"` and no claims.
9. `check` never raises and never includes source text in a `Decision`.
10. A deidentified derivative cannot be created with a sensitivity lower than its declared
    `DataPolicy` without a new dataset identity.
11. An expired restriction refuses with `restriction_expired`; the same call with an earlier
    `now` is permitted.
12. `model_weights` to an `external` destination is refused for `phi` data, and weights trained
    on two datasets carry the more restrictive policy.
13. A serialized `Decision` contains no source text and is accepted as a `labels_only` payload.

### 1.11 Decisions recorded

Reviewed and accepted 2026-10-30:

1. Restrictions may carry `valid_until`; `check` takes `now` from the caller.
2. `model_weights` is a payload kind; trained weights inherit the most restrictive training
   `DataPolicy` and pass the same gate.
3. `Decision` is the audit record; amber defines it and the tracking plugin logs it;
   persistence belongs to enforcement points.

---

## 2. Mentions and structured evidence

### 2.1 Purpose

A **mention** is a typed, attributed span of source text produced by a detector, a rule engine,
a model, or a human. It is the unit that lapidary scores at span level, that copal produces,
that loupe reviewers edit, and that layered variables (§3) build on. A mention is **not**
evidence by itself: evidence is minted only by grounding, and a claim cites an `Inclusion`
that may point back to the mention it came from.

**Structured evidence** is a verified row or field from a structured source (a lab value, a
medication order, a registry field). It is the second kind of source-backed leaf in the
evidence graph, alongside `Inclusion`.

### 2.2 Mention model

```python
class Attribute(BaseModel):                  # frozen
    value: Any                               # JSON value; the category decides its meaning
    evidence_id: str | None                  # Inclusion of the cue span ("no evidence of"), if any
    scope_evidence_id: str | None            # Inclusion of the scope the cue applied to, if any
    assigned_by: str                         # "rule:<ruleset>@<version>+<hash>", "model:<name>", "human:<user>"

class Mention(BaseModel):                    # frozen, extra="forbid"
    mention_id: str                          # sha256(source_id, start, end, mention_type)
    source_id: str
    start: int; end: int                     # end-exclusive code points into Source.text
    quote: str                               # == Source.text[start:end]; validated against the Source
    mention_type: str                        # consumer-declared type: "tnm_t", "finding", "medication"
    label: str | None                        # consumer-declared sub-label (e.g. "T2", "metastasis")
    attributes: Mapping[str, Attribute]      # keyed by category: "negation", "temporality", ...
    value: str | float | int | None          # captured or normalized value ("T2", 8, 3.4)
    unit: str | None
    concept: Concept | None                  # unchanged from the prior contract; optional
    section_category: str | None             # from the Source's sections, if any
    fragments: tuple[tuple[int, int], ...] = ()   # discontinuous parts; (start, end) is their hull
    inputs: tuple[str, ...] = ()             # producer-side evidence IDs (copal span IDs, model trace IDs)
    provenance: Provenance
```

Decisions behind the fields:

- **Attributes are an open mapping, not fixed enums.** The prior contract fixed
  `polarity`, `temporality`, `experiencer`. Those remain the *recommended* categories, and the
  conformance kit ships the standard value sets for them (`negation: true|false`,
  `temporality: current|historical|hypothetical|future`, `experiencer: patient|family|other`,
  `uncertainty: true|false`). But copal categories are opaque strings chosen by the rule set,
  and tasks need others (laterality, severity). A **task declares which attribute categories
  and value sets it requires** (§3); validation checks a mention against that declaration, not
  against a global enum. Absent category means *no attribution*, not a default value, matching
  copal's driver semantics.
- **Attributes carry their own evidence.** The cue ("no evidence of") and its scope are
  Inclusions, minted by the same grounding path. This is what lets a negated mention be
  *checked*: a reviewer or scorer can see the cue span, and §3 can require that a derived value
  which depends on an attribute cites the attribute's evidence.
- **`value` is captured text or a normalized value, never free text.** For `tnm_t` the value
  is the captured token `T2`; for a measurement it is the parsed number plus `unit`. How the
  value was derived is in `assigned_by`-style provenance on the mention, not implied.
- **Discontinuous mentions** keep their `fragments`; `mention_id` is computed over the hull so
  overlapping discontinuous and contiguous mentions have distinct identities only when their
  types differ. Quotes of fragments are validated individually.
- **Identity excludes attributes.** Two producers that find the same span with the same type
  produce the same `mention_id` and *different* attribute sets; that is a disagreement to
  reconcile, not two mentions. Per-producer records keep their own provenance.

Validation on construction (same guard as `Inclusion`): bounds, `quote` equality against the
authoritative `Source`, fragment bounds inside the hull, `section_category` present in the
Source's sections when set, and every `evidence_id` in attributes resolvable when the mention
is registered in a graph. A `Mention` cannot be built from a string alone.

### 2.3 Mentions and evidence

Mentions enter the evidence graph in two ways:

1. **As cited spans.** `cite(mention) -> Inclusion` mints an Inclusion over the mention's
   hull (or one per fragment) through the grounding path and sets `Inclusion.mention_id`. The
   graph records the mention alongside the inclusion so that `source_leaves()` can report the
   mention, its type, and its attributes with the span.
2. **As claim subjects.** A **mention-level task** (§3) has one claim per mention; its answer
   schema includes the mention's value and the attribute categories the task requires. The
   claim cites the mention's inclusion *and* the inclusions of every attribute it relies on.

Rule for attribute-dependent support: if a claim's value depends on an attribute (the stage M
claim is `M0` because the metastasis mention is negated), the claim's field evidence must
include the attribute's `evidence_id`. The graph validator checks this through the task's
declared attribute requirements: a required attribute without evidence is
`missing_attribute_evidence`. A mention attribute with no evidence (a model asserted "negated"
without a cue) is permitted on the mention but cannot satisfy a required attribute in a claim.

### 2.4 Mapping copal output to mentions

copal produces immutable spans with `start`, `end`, `quote`, `layer`, `label`, `rule_id`,
opaque `payload`, `attrs` (including `modifiers`), `inputs`, and rule-set provenance
`name@version+hash`. The mapping is a **declared adapter policy**, versioned and tested, not
an implicit conversion:

| copal | Mention | Notes |
|---|---|---|
| `start`, `end`, `quote` (code-point units) | `start`, `end`, `quote` | Re-validated against the amber `Source`; the amber Source text must be the exact copal `Text.source`. |
| `layer` or `label` per policy | `mention_type`, `label` | The policy declares which copal layer maps to which type. |
| named capture | `value` | e.g. capture `t` in `p?T(?P<t>[0-4][a-c]?)` → `value="T2"`. |
| `attrs.modifiers[category] = {value, trigger_id, scope_id}` | `attributes[category]` | `value` → `Attribute.value`; `trigger_id` → cue Inclusion via `cite`; `scope_id` → scope Inclusion; `assigned_by = "rule:<name>@<version>+<hash>"`. |
| sectionizer span containing the mention | `section_category` | Section spans also populate `Source.sections` at import. |
| `payload` | `concept` or `label` | The consumer's lexicon payload; mapped per policy (a concept record, or nothing). |
| span IDs of `inputs` | `inputs` | Document-local copal IDs, prefixed with the document key, kept for audit. |
| rule-set provenance, copal version, lexicon `build_hash` | `provenance.producer`, `provenance.version` | `producer = "rule:<ruleset>@<version>+<hash>"`; `destination = "local:copal"`; zone `local`. |

The adapter never repairs offsets. A copal span whose quote does not match the amber source
(wrong text version, normalization applied before copal) is reported as a mapping failure with
a source-free diagnostic, and no mention is created.

Units: copal returns code points by default; the adapter must request code points, never UTF-8
or UTF-16 units. The conformance kit includes emoji, combining marks, and CRLF sources.

### 2.5 Mentions from models and humans

- **Model-proposed mentions** (LangExtract, GLiNER, an LLM) arrive as candidate offsets or
  candidate strings. Candidate strings go through `quote()`; candidate offsets are validated
  against the Source and rejected on quote mismatch. Attributes asserted by the model without a
  cue get `evidence_id=None` and `assigned_by="model:<name>"`; such attributes cannot satisfy
  a required attribute in a claim (§2.3). A grounding failure is counted on the result, not
  silently dropped.
- **Human mentions** (loupe) are minted by the review bridge through the same grounding path
  with `assigned_by="human:<user>"`; the reviewer's cue selection, when present, becomes the
  attribute evidence. loupe's current `loupe-annotation-v1` (concept, four context axes,
  supporting text) maps directly: supporting text → attribute `evidence_id`.

### 2.6 Structured evidence

```python
class StructuredSourceMapping(BaseModel):    # frozen; declared per structured Source kind
    source_kind: SourceKind                  # lab | medication | problem | imaging | other
    fields: Mapping[str, Literal["string", "number", "datetime", "code"]]
    key_fields: tuple[str, ...]              # which fields identify a row

class StructuredEvidence(BaseModel):         # frozen; minted only through the structured path
    kind: Literal["structured"] = "structured"
    evidence_id: str                         # sha256(source_id, field, canonical value)
    source_id: str                           # a Source with record != None and text == None
    field: str
    value: Any                               # must equal Source.record[field] exactly
    datetime: datetime | None                # the row's effective time
```

- A structured `Source` holds the row as `record` (immutable JSON) and no `text`. Its
  `source_id` hashes the canonical record, so a changed row is a new source.
- `StructuredEvidence` is minted only by `structured_cite(source, field)` after checking that
  `field` is declared in the mapping for that `source_kind` and that `value` equals the
  record's field exactly. No code path constructs one directly, matching the `Inclusion` guard.
- The provider that fetched the row is a `structured_source` destination (§1); its policy
  decision is stamped on the row's `Provenance`. Fetching is implementation work (lapidary);
  amber only validates the result.
- Graph validation accepts `StructuredEvidence` as a source-backed leaf. `source_leaves()`
  returns both kinds.

### 2.7 Design checks against the stand-in variables

- **`ds_findings_v1` (discharge-summary finding):** copal dictionary match → mention type
  `finding`, label from the lexicon payload, `negation` and `temporality` attributes from the
  modifier driver with cue inclusions, `section_category` from the sectionizer. A
  mention-level task declares `requires_attributes={"negation": {true,false},
  "temporality": {...}}`. The mention claim cites the mention inclusion plus both cue
  inclusions. A model-only run (LangExtract) yields the same mention IDs when it finds the same
  spans, with `assigned_by="model:..."` attributes that are permitted on the mention but
  trigger `missing_attribute_evidence` if the task requires evidence for them; the task author
  chooses whether to require it.
- **Stage group leaves:** `tnm_t`, `tnm_n`, `tnm_m` mentions with captured `value`;
  `section_category="final_diagnosis"` required by the T/N/M tasks so a historical `T2` in the
  clinical history cannot be cited. The M mention `no distant metastasis` carries
  `negation=true` with a cue inclusion, and the `pathologic_M=M0` claim cites it.
- **CORAL progression:** unchanged; the task has no mentions, cites inclusions directly.
  Mentions are optional at the contract level.
- **A lab-backed variable (future):** `StructuredEvidence` over a lab row with
  `field="value"`; the mapping declares `value: number`, `datetime` from the row.

### 2.8 Conformance cases (mentions and structured evidence)

1. A mention with `quote != Source.text[start:end]` is rejected; likewise any fragment.
2. Direct construction without the grounding context is rejected (same guard as Inclusion).
3. Two producers, same span and type: same `mention_id`, distinct provenance records.
4. Overlapping and nested mentions of different types coexist; no automatic merging.
5. A discontinuous mention validates each fragment; hull outside the source is rejected.
6. `section_category` not present in `Source.sections` is rejected.
7. An attribute with `evidence_id` not registered in the graph is rejected at registration.
8. A claim that depends on a required attribute without attribute evidence fails with
   `missing_attribute_evidence`; with evidence it passes.
9. copal adapter: emoji, combining marks, and CRLF sources round-trip offsets exactly;
   a source with normalized whitespace fails mapping with a source-free diagnostic.
10. copal adapter: modifier `trigger_id`/`scope_id` become cue/scope inclusions whose quotes
    match copal's `get_span(...).quote`.
11. Model-proposed offsets with a quote mismatch are counted as grounding failures, not
    dropped silently and not repaired.
12. `StructuredEvidence` with a value differing from the record's field is rejected; an
    undeclared field is rejected; a structured Source with `text` is rejected.
13. A claim supported only by a `StructuredEvidence` leaf validates; a claim supported only by
    a mention with no inclusion fails `source_free_support`.

### 2.9 Decisions recorded

Reviewed and accepted 2026-10-30:

1. Required attributes demand cue evidence by default; a task may declare an attribute
   `lenient` to accept model- or human-asserted values without a cue. Scorers report the share
   of attribute values lacking evidence either way.
2. `Concept` keeps its prior optional fields (system, code, label, score). Vocabulary identity
   (release, fragment) stays with the deferred terminology design.
3. `CaseResult.mentions` is allowed in v0.1. Mentions are validated against the Source like
   evidence; every mention cited by a claim is in the result's closure; uncited mentions are
   retained as diagnostics for span-level scoring. Conformance adds: a result whose cited
   mention is missing from `mentions` fails to load; uncited mentions survive a round trip.

## 3. Layered variables

### 3.1 Purpose

A **variable** is a named question with an answer schema. Today one `EvidenceGraph` binds one
`Task`, so every claim in a graph has the same schema. Layered variables let one case hold
claims at several levels (mention, note, and later patient), each validated against its own
schema, with the higher levels deriving from the lower ones through recorded, checkable steps.
The single-task case stays a special case: a variable set with one variable and no
dependencies behaves exactly as the current graph does.

### 3.2 Variable set

```python
class AttributeRequirement(BaseModel):       # frozen
    category: str                            # "negation", "temporality", ...
    values: frozenset[Any] | None            # permitted values; None = any JSON value
    evidence: Literal["required", "lenient"] = "required"

class Variable(BaseModel):                   # frozen, extra="forbid"
    name: str                                # unique within the set: "pathologic_T", "stage_group"
    level: Literal["mention", "note"]        # "patient" reserved, not accepted in v0.1
    answer_model: type[AnswerModel]
    instructions: str
    evidence_policy: Literal["claim", "field"] = "claim"
    inputs: frozenset[str] = frozenset()     # names of variables this one may derive from
    mention_types: frozenset[str] = frozenset()   # mention-level only: which mention_type(s) it is about
    requires_attributes: tuple[AttributeRequirement, ...] = ()   # mention-level only
    requires_section: frozenset[str] = frozenset()   # cited spans must lie in these section categories
    final: bool = False                      # a task answer (reported), as opposed to an intermediate

class VariableSet(BaseModel):                # frozen
    name: str                                # "pathologic_staging"
    version: str                             # semver; changing any variable changes the version
    variables: Mapping[str, Variable]
    schema_ref: str                          # sha256 over the canonical serialization of the set
```

Validation of a `VariableSet`: names unique; every `inputs` name exists; the `inputs` relation
is acyclic; a mention-level variable has nonempty `mention_types` and no `inputs` from
note-level variables; at least one variable is `final`.

`Task` becomes the one-variable special case: `Task(name, answer_model, instructions,
evidence_policy)` is sugar for a `VariableSet` with one final note-level variable of that name.
Existing call sites keep working; `EvidenceGraph(source, task=...)` and
`EvidenceGraph(source, variables=...)` are both accepted, the first wrapping into the second.

### 3.3 Claims in a multi-variable graph

`Claim` gains `variable: str` (the variable name; for a one-variable set equal to `task`) and
keeps `schema_ref`, which now refers to that variable's answer model. Graph validation changes
from "every claim matches the graph's task" to:

1. `claim.variable` is in the set, and `claim.value` validates against that variable's
   `answer_model`; `schema_ref` matches.
2. For a mention-level claim: the claim's primary citation is an `Inclusion` with a
   `mention_id`, that mention's `mention_type` is in the variable's `mention_types`, and every
   `requires_attributes` entry is satisfied on the mention (value in `values`; cue evidence
   present and cited when `evidence == "required"`), otherwise `missing_attribute_evidence` or
   `attribute_value_not_permitted`.
3. If `requires_section` is set, every cited Inclusion lies inside a `Source.sections` span
   with one of those categories; otherwise `citation_outside_section`.
4. For a claim with `InferenceEvidence` inputs that are claims: each input claim's `variable`
   must be in this variable's declared `inputs`; otherwise `undeclared_dependency`. Inputs
   that are evidence IDs (inclusions, structured) are always permitted.
5. Existing rules are unchanged: edges, field roles, acyclicity across edges and inference
   inputs, source-backed leaves on every branch, case scope, one note.

Rule 4 is what prevents a stage-group claim from silently depending on a mention the set never
declared, and it is the hook for §3.4.

### 3.4 Derivations

A derivation is an `InferenceEvidence` whose rationale is a typed record rather than free
text. `InferenceEvidence` gains one optional field:

```python
class Derivation(BaseModel):                 # frozen
    kind: Literal["rule", "model", "human"]
    name: str                                # "ajcc_breast_stage_group", "gpt-x extraction", "human:<user>"
    version: str                             # rule table version, prompt version, or review revision
    inputs: Mapping[str, str]                # role -> input id: {"T": claim_id, "N": claim_id, "M": claim_id}
    parameters: Mapping[str, Any] = {}       # frozen JSON; e.g. {"edition": 8, "site": "breast"}
    replayable: bool                         # True only for kind == "rule"

class InferenceEvidence(...):
    ...
    derivation: Derivation | None = None     # None keeps the current free-rationale behaviour
```

Rules for derivations:

- `inputs` values must be a subset of the inference's existing `inputs` list (so the graph
  rules still apply) and the role names must be exactly those the deriving variable's rule
  expects; the rule's expected roles are part of the rule's registration (below).
- A **rule** derivation is replayable: amber defines a `Rule` protocol,
  `apply(inputs: Mapping[str, Claim | None], parameters) -> RuleResult`, where `RuleResult` is
  either a value for the deriving variable or an outcome status with a reason. Rule
  implementations live with the consumer (lapidary, a rule pack) and are registered by
  `name@version`; amber's conformance kit can **replay** a derivation when the rule is
  registered and checks that the replayed value equals the claim's value. A mismatch is
  `derivation_mismatch`. Unregistered rules are not replayed; the record is still validated
  structurally.
- A **model** derivation records prompt/model versions through `Provenance` as today and is
  never replayed.
- A **human** derivation records the review revision; never replayed.

A rule may also accept **structured evidence** and **inclusions** as inputs (role → evidence
id), so a rule such as "ECOG from an explicit score mention, else from functional-status
mentions" is expressible.

### 3.5 Outcomes per variable

`CaseResult` currently has one `outcome` and one `final_claim_ids` list. With several final
variables, each needs its own. The result gains:

```python
class VariableOutcome(BaseModel):            # frozen
    variable: str
    outcome: CaseOutcome                     # same model and rules as today
    final_claim_ids: tuple[str, ...]         # non-empty iff outcome.status == "answered"

class CaseResult(...):
    ...
    variable_outcomes: Mapping[str, VariableOutcome]   # one per final variable in the set
    outcome: CaseOutcome                     # case-level: answered iff every final variable answered;
                                             # failed iff any failed; otherwise the most severe non-answer
    final_claim_ids: ...                     # union of the per-variable final IDs (kept for compatibility)
```

The case-level outcome is **derived** from the per-variable outcomes by a fixed rule (failed >
conflicting_evidence > insufficient_evidence > not_mentioned > answered) and validated on load;
it cannot be set independently. For a one-variable set this reduces to today's behaviour.

Intermediate (non-final) variables do not get a `VariableOutcome`; their absence is visible
through the derivation that needed them (§3.6).

### 3.6 Outcome propagation through rules

When a rule's input variable has no claim (its mention-level question was `not_mentioned`,
`insufficient_evidence`, or the mention-level work was never run), the rule receives `None`
for that role. The rule itself decides, and that decision is **declared in the rule's
registration**, not improvised:

```python
class RuleSpec(BaseModel):                   # frozen; registered with the rule implementation
    name: str; version: str
    roles: Mapping[str, RoleSpec]            # RoleSpec: variable name or evidence kind, required: bool,
                                             #           on_missing: Literal["refuse", "default", "pass_none"]
    outputs: str                             # the deriving variable name
    defaults: Mapping[str, Any] = {}         # role -> default value when on_missing == "default"
```

- `refuse`: a missing required input makes the derived variable `insufficient_evidence` with
  reason naming the role. The rule is not applied.
- `default`: the declared default is used (the AJCC edition 8 breast table treats absent
  clinical M as `cM0`), and the derivation records that the default was used
  (`parameters["defaults_used"] = ["M"]`), so the derived claim's provenance shows it.
- `pass_none`: the rule receives `None` and handles it (ECOG fallback).

A derived claim whose derivation used a default is still a valid answered claim; scorers can
stratify by `defaults_used`. A derived claim can never be produced from an input variable that
ended in `conflicting_evidence`; that propagates as `conflicting_evidence` on the derived
variable, citing the same evidence.

### 3.7 Final claims and reporting

Only `final: true` variables are task answers. Scorers score final variables; intermediate
claims are support. `Example` (§4) stores gold per final variable and may store gold for
intermediate variables when annotated. `labels_only_view` (§1) emits one record per final
variable.

### 3.8 Design checks against the stand-in variables

- **CORAL progression:** `VariableSet("oncology_current_progression", variables={that one,
  final=True, level=note})`. No inputs, no mention types. Every existing test passes
  unchanged through the `Task` sugar; `variable_outcomes` has one entry equal to the case
  outcome.
- **`ds_findings_v1`:** one mention-level final variable per finding type, or one variable
  with `mention_types={"finding"}` whose answer model carries the label and the required
  attributes `negation` (required evidence) and `temporality` (lenient, by the task author's
  choice). Claims are per mention; a document's `variable_outcomes` for a mention-level
  variable is `answered` when at least one mention claim exists, `not_mentioned` with declared
  full review otherwise.
- **Stage group:** four mention-level variables (`pathologic_T`, `pathologic_N`,
  `pathologic_M`, `ajcc_edition`) with `requires_section={"final_diagnosis"}`, and one
  note-level final variable `stage_group` with `inputs={T, N, M, ajcc_edition}`. The rule
  `ajcc_breast_stage_group@8.0` declares roles T, N (required, refuse), M (default `M0`),
  edition (required, refuse). The derived claim's `InferenceEvidence.derivation` names the
  rule, the four input claim IDs, and `defaults_used=["M"]` when M was absent. Replay in the
  conformance kit recomputes the stage from the input claims and matches. A `T2` cited from the
  clinical history fails `citation_outside_section`; a claim citing a mention of type
  `tnm_n` for `pathologic_T` fails the mention-type check; an inference citing a
  `pathologic_T` claim from a `stage_group` variable whose `inputs` omit `pathologic_T` fails
  `undeclared_dependency`.
- **Deferred, checked for non-preclusion:** a `patient` level would add `inputs` across
  cases; the set model and derivation record need no change, only the graph's one-note scope
  rule, which is where patient aggregation will be introduced later.

### 3.9 Conformance cases (layered variables)

1. A one-variable set via `Task` produces identical validation results to the current graph on
   the full existing test suite.
2. A `VariableSet` with a dependency cycle, an unknown input name, a mention-level variable
   without mention types, or no final variable is rejected.
3. A claim whose `variable` is not in the set, or whose value fails that variable's schema, is
   rejected with `invalid_schema`.
4. A mention-level claim citing a mention of the wrong type is rejected.
5. Required attribute: missing value → rejected; value outside `values` → rejected; value
   present without cue evidence → `missing_attribute_evidence` unless lenient.
6. `requires_section`: a citation outside the declared sections is rejected; inside passes.
7. An inference citing a claim from an undeclared input variable fails `undeclared_dependency`.
8. A derivation whose `inputs` are not a subset of the inference's inputs is rejected.
9. Replay: a registered rule recomputes the derived value; a tampered derived value fails
   `derivation_mismatch`; an unregistered rule validates structurally and is not replayed.
10. `on_missing`: `refuse` yields `insufficient_evidence` naming the role; `default` records
    `defaults_used`; `pass_none` invokes the rule with `None`.
11. A `conflicting_evidence` input propagates to the derived variable and cites the same
    evidence; the derived claim is never created.
12. Case-level outcome is derived from variable outcomes by the fixed severity order; a
    payload asserting a different case-level outcome fails to load.
13. Two final variables, one answered and one `not_mentioned`: `final_claim_ids` contains only
    the answered one; `variable_outcomes` carries both.

### 3.10 Decisions recorded

Reviewed and accepted 2026-10-30:

1. The `Rule` protocol and `RuleSpec` are amber contract; rule implementations live in lapidary
   or a separate rule pack and register by `name@version`. amber ships only synthetic example
   rules for the conformance kit.
2. Intermediate (non-final) variables get no `VariableOutcome` in v0.1; their status is
   recoverable from derivations and the absence of claims.
3. Mention-level final variables get a document-level `VariableOutcome` with statuses
   `answered` (at least one mention claim), `not_mentioned` (zero claims and declared full
   review), `insufficient_evidence` (zero claims without full review, or skipped scope), and
   `failed`. `conflicting_evidence` is invalid at mention level. This maps directly to loupe's
   "no target mentions" confirmation versus an unsubmitted review. Conformance adds: a
   mention-level `conflicting_evidence` outcome is rejected; zero claims without declared full
   review cannot load as `not_mentioned`.

## 4. Annotated data

### 4.1 Purpose

`Example` is the single annotated-data record. Demonstrations, evaluation datasets, training
files, and loupe references are exports from it or imports into it; none is a second source of
truth. Every Example is derived from an append-only **annotation event** log, so gold can be
replayed, audited, and attributed, and so a later review can raise a reference tier without
rewriting history.

### 4.2 Reference tiers

```python
class ReferenceTier(StrEnum):
    adjudicated = "adjudicated"        # ≥2 independent reviews + adjudication under a protocol
    single_review = "single_review"    # one qualified reviewer, with exact evidence
    silver = "silver"                  # no human review: a teacher model or rule output
```

Rules, carried over from the CORAL protocol 1.1.0 and now contract:

- The tier is set per Example and per final variable; an Example with two final variables may
  be `adjudicated` for one and `silver` for the other.
- A tier may only **rise**, and only through new annotation events. Silver never becomes gold
  by relabeling.
- Confirmatory evaluation uses `adjudicated` only; results against lower tiers are reported as
  provisional (scorer responsibility, §6).
- Silver Examples record their producer's provenance (model, prompt versions, rule) so that a
  system is never scored against its own outputs: the scorer refuses when the producer
  provenance of a silver Example matches the configuration under evaluation.

### 4.3 Example

```python
class VariableReference(BaseModel):           # frozen
    variable: str
    tier: ReferenceTier
    outcome: CaseOutcome                      # answered | not_mentioned | conflicting_evidence | insufficient_evidence
    final_claim_ids: tuple[str, ...]          # nonempty iff answered; gold claims
    annotators: tuple[str, ...]               # "human:<user>" or "model:<name>" / "rule:<name@version>"
    adjudicator: str | None
    derived_from_events: tuple[str, ...]      # event IDs whose replay yields this reference

class Example(BaseModel):                     # frozen, extra="forbid"; loaded only through amber.examples
    example_id: str                           # ULID
    schema_version: str
    case_id: str
    variable_set: str; variable_set_version: str
    source_ids: tuple[str, ...]               # one in v0.1
    split: Literal["train", "dev", "test"] | None
    references: Mapping[str, VariableReference]   # one per final variable
    claims: tuple[Claim, ...]                 # gold and supporting claims (status gold|verified)
    mentions: tuple[Mention, ...]             # reviewed mentions, cited or not
    evidence: tuple[Evidence, ...]
    edges: tuple[EvidenceEdge, ...]
    created_at: datetime
```

Invariants (validated on load, with the authoritative Source and VariableSet supplied, as
`load_case_result` does today):

1. The retained graph validates completely under §2–§3 rules; every final claim is `gold`;
   supporting claims are `gold` or `verified`; no `proposed` or `rejected` claim is retained.
2. A reference's `outcome` is never `failed`: failed runs are diagnostics, not Examples.
3. `answered` references have nonempty final IDs naming retained gold claims; the other three
   outcomes have none. An abstention is never translated to a negative answer.
4. `split` is immutable once set; an Example cannot be re-created with a different split for
   the same case (the store rejects it; the conformance kit checks the rule).
5. `derived_from_events` is nonempty for `adjudicated` and `single_review`; for `silver` it
   names the import event that recorded the producer's result.
6. Mentions: every mention cited by a retained claim is present; uncited reviewed mentions may
   be present (they are span-level gold).

### 4.4 Annotation events

```python
class AnnotationEvent(BaseModel):             # frozen, append-only; ULID ids are time-ordered
    event_id: str
    schema_version: str
    case_id: str
    variable: str | None                      # None for case-level events
    actor: str                                # "human:<user>", "model:<name>", "rule:<name@version>", "tool:<name>"
    role: Literal["reviewer", "adjudicator", "importer", "system"]
    round: int                                # review round; independent streams share a round
    stream: str | None                        # independent review stream id, when blind review applies
    action: Literal[
        "mention.create", "mention.update", "mention.delete",
        "claim.create", "claim.update", "claim.set_status",
        "evidence.cite", "evidence.attach_structured", "edge.add", "edge.remove",
        "outcome.set",
        "review.open", "review.submit", "review.reopen",
        "adjudication.resolve", "import.result", "tier.set",
    ]
    target_id: str | None                     # mention/claim/evidence/edge id the action concerns
    before: Mapping[str, Any] | None          # labels_only view of the prior state
    after: Mapping[str, Any] | None           # labels_only view of the new state
    reason: str | None
    at: datetime
    provenance: Provenance                    # destination = the review service; policy stamped
    external_ref: str | None                  # loupe submission/revision/publication id, when applicable
```

Rules:

- Events are the record; current state is a **projection** rebuilt by replay. Replay is
  deterministic: the conformance kit replays a random event sequence twice and compares
  projections.
- `before`/`after` carry `labels_only` views (no quotes or offsets); the spans themselves are
  referenced by evidence/mention IDs that resolve in the store. This keeps the event log safe
  to transfer as `labels_only`.
- Blind review: events from different `stream`s in the same `round` are never visible to each
  other's actors; the projection for an actor includes only their own stream until
  `adjudication.resolve`. This is a store/projection rule, checked by the kit.
- Session timing for expert-effort measurement uses `review.open`/`review.submit` timestamps
  plus optional `review.focus`/`review.idle` events (added to the action set when loupe
  provides them); amber defines the actions, loupe emits them.

### 4.5 Status transitions

Claims:

| From | To | Action | Who |
|---|---|---|---|
| `proposed` | `verified` | `claim.set_status` | reviewer accepts a model/rule claim unchanged |
| `proposed` | `rejected` | `claim.set_status` | reviewer rejects |
| (none) | `proposed` | `claim.create` | reviewer authors a claim (human provenance) |
| `verified` / `proposed` | `gold` | `adjudication.resolve` or `tier.set` | adjudicator, or the single reviewer under a `single_review` tier |
| `gold` | `rejected` | `adjudication.resolve` with reason | adjudicator only; prior gold is retained in history |

A human **correction** is `claim.update` (new value and/or evidence) followed by the same
transitions; the updated claim gets a new `claim_id`, and the event links old and new. There
is no edit-in-place of a `gold` claim.

Mentions have `proposed` → `verified`/`rejected` and `verified` → `gold` with the same actions;
a claim can only be `gold` if every mention it cites is `gold` or `verified`.

### 4.6 Label-only evaluation records

Silver references that carry labels but no evidence (CORAL candidate rules, a classifier) are
not Examples: they cannot satisfy invariant 1. They are `LabelRecord`s:

```python
class LabelRecord(BaseModel):                 # frozen; produced by labels_only_view or by a Labeler
    case_id: str; variable: str
    tier: Literal["silver"]
    outcome_status: OutcomeStatus
    value: Mapping[str, Any] | None           # the answer value when answered
    producer: str; producer_version: str
    split: Literal["train","dev","test"] | None
```

Scorers accept Examples and LabelRecords; label correctness, coverage, omissions, and cost can
be scored against either, while evidence, localization, and semantic-support metrics require
Examples and are reported as `inconclusive` otherwise. A LabelRecord never upgrades to an
Example without a review that produces evidence.

### 4.7 Mapping loupe to events and Examples

The review bridge (§6, MLflow plugins) performs the mapping; the contract fixes it:

| loupe | amber |
|---|---|
| source record (`record_id` = `source_id`, exact text, metadata) | `Source` + `Case`; metadata carries split, sensitivity, variable set |
| review stream, round | `stream`, `round` |
| a saved revision (submission id) | one `review.submit` event with `external_ref` = submission id, preceded by the `mention.*`/`claim.*`/`evidence.*` events that the revision's diff implies; the adapter computes the diff against the prior revision |
| "no target mentions" confirmation with whole-note review | `outcome.set` with `not_mentioned` and `reviewed_source_ids` = the note |
| unsubmitted or partial review | no `review.submit`; projection outcome `insufficient_evidence` |
| adjudication decision `accepted` with selected revision | `adjudication.resolve` + `tier.set adjudicated`; final claims → `gold` |
| decision `unresolved` | `adjudication.resolve` with reason; tier unchanged; no Example exported |
| decision `excluded` | `adjudication.resolve` with reason; case excluded from the Example set, retained in events |
| publication id | `external_ref` on the resolve events; `derived_from_events` of the Example |
| single-stream workflow (move-on) | `tier.set single_review` by the reviewer on submit; no adjudication events |

The adapter's `build_result`/`validate_result` (§2.5) ensure every revision is a valid amber
payload before it is accepted, so the event diff never contains an ungrounded span.

### 4.8 Design checks against the stand-in variables

- **CORAL progression:** two blind streams, one adjudicator; the Example has one reference
  (`adjudicated`), the gold boolean claim, its inclusions, and `derived_from_events` naming
  both streams' submits and the resolve. A move-on freeze produces `single_review` references
  for some notes and `silver` LabelRecords from the candidate rules for others; the scorer
  marks the run provisional.
- **`ds_findings_v1`:** gold mentions with attributes and cue inclusions; mention-level gold
  claims; the reference outcome for a note with no findings is `not_mentioned` from the
  reviewer's confirmation. The gpt-6-sol importer produces `silver` Examples (they have
  evidence) with the model's provenance; scoring a gpt-6-sol configuration against them is
  refused.
- **Stage group:** references for `stage_group` (final) and, if the reviewer labeled them,
  gold T/N/M claims as supporting claims with their own `verified`/`gold` status; a reviewer
  correcting `M` creates a new M claim, the derived stage is re-derived by replaying the rule
  (§3.4) and recorded as a new claim with a human-triggered derivation event.

### 4.9 Conformance cases (annotated data)

1. An Example with a `failed` outcome, a `proposed` final claim, or a `rejected` retained claim
   fails to load.
2. `answered` with empty final IDs, or a non-answer with final IDs, fails to load.
3. An Example whose cited mention is missing from `mentions` fails; uncited mentions round-trip.
4. Tier can rise (`silver` → `single_review` → `adjudicated`) only with new events; a payload
   asserting a higher tier without events fails; a lower tier than the events support fails.
5. Split change for an existing case is rejected by the store.
6. Replay determinism: random event sequences produce identical projections; replaying a
   prefix produces the prefix's projection.
7. Blind streams: an actor's projection excludes other streams until resolve.
8. `before`/`after` contain no substring of length ≥ 8 from the source text.
9. A `gold` claim cannot be edited in place; a correction produces a new claim and a linked
   event.
10. A claim can be `gold` only if its cited mentions are `gold` or `verified`.
11. A `LabelRecord` cannot be loaded as an Example; a scorer scoring evidence metrics against
    LabelRecords reports `inconclusive`.
12. Scoring a configuration against silver Examples produced by the same configuration is
    refused.
13. loupe mapping fixtures: accepted → adjudicated Example; unresolved → no Example, events
    retained; excluded → no Example; no-mentions confirmation → `not_mentioned`; unsubmitted →
    `insufficient_evidence`.

### 4.10 Decisions recorded

Reviewed and accepted 2026-10-30:

1. A corrected claim gets a new `claim_id`; the `claim.update` event links old to new. Claim
   IDs cited by edges and derivations therefore never change meaning.
2. In a single-stream workflow the reviewer's `review.submit` sets `single_review`; the
   protocol's move-on decision is a separate case-level `tier.set` event by `role=system`
   carrying the decision reference, recorded before scoring.
3. The loupe bridge derives per-change events from revision diffs and stamps the revision
   checksum on `review.submit`. Fallback, if diffing is unreliable: one `review.submit` per
   revision carrying the full `labels_only` state. Both replay to the same projection, which
   the conformance kit checks.

## 5. Wire formats

### 5.1 Purpose

The wire format is the exact representation of contract records when they leave a process:
JSON for single records and bundles, Parquet tables for runs and annotated sets. lapidary
writes tables, loupe reads and writes JSON, MLflow stores both as artifacts, and all of them
must agree without sharing code. The Pydantic models conform to this section, not the reverse.

lapidary's L1 table design is the starting point; this section adopts its conventions and
extends them for §2–§4. Where this section and that plan differ, this section wins and the
plan is updated (lap-ecc.1).

### 5.2 Common rules

- **Schema version.** Every JSON record and every bundle manifest carries
  `schema_version: "amber/<major>.<minor>"`. v0.1 is `amber/0.1`. Readers reject a different
  major and accept a lower or equal minor (additive changes only). A reader never guesses.
- **IDs** are strings: content-addressed SHA-256 hex for `source_id`, `mention_id`, inclusion
  and structured `evidence_id`; ULIDs for `claim_id`, inference `evidence_id`, `event_id`,
  `example_id`.
- **Offsets** are end-exclusive Unicode code-point indices into the exact source text. Parquet
  `int64`, JSON integers.
- **Times** are RFC 3339 with offset in JSON; `timestamp[us, tz=UTC]` in Parquet.
- **JSON columns** in Parquet are UTF-8 strings containing canonical JSON (sorted keys, no
  whitespace, `ensure_ascii=False`), never Arrow structs, so column schemas survive answer-
  model changes.
- **Nulls** mean absent. A column that does not apply to a row is null; there are no sentinel
  strings.
- **Source text is never stored in tables.** `sources` carries `text_hash`; `quote` columns
  and attribute cue quotes do contain source text and classify the file as `derived_text`
  (§1). Every other table is `labels_only` by construction, except `annotation_events`, whose
  `before`/`after` are `labels_only` views and whose `reason` is free text written by a human
  (treated as `derived_text` conservatively).
- **Provenance** is flattened into `provenance_*` columns: `producer`, `model`,
  `prompt_versions` (JSON), `run_id`, `trace_id`, `zone`, `sensitivity`, `destination`,
  `policy_version`, `restriction_ids` (JSON list), `created_at` (timestamp), `version`.
  lapidary's `vocabulary_release` and `fragment_id` are **not** adopted (terminology deferred);
  consumers may add `x_`-prefixed columns, which amber readers ignore and never validate.

### 5.3 JSON records

The JSON form of each record is `model_dump(mode="json")` of the contract model, with these
fixed points that the conformance kit checks byte-for-byte on fixtures:

| Record | Required top-level keys |
|---|---|
| `CaseResult` | `schema_version, case_id, variable_set, variable_set_version, claims, mentions, evidence, edges, variable_outcomes, outcome, final_claim_ids, grounding_failures, tool_calls, escalated, trace_id` |
| `Example` | as §4.3 plus `schema_version` |
| `AnnotationEvent` | as §4.4 |
| `LabelRecord` | as §4.6 plus `schema_version` |
| `Decision` | as §1.3 plus `schema_version` |
| `VariableSet` | `schema_version, name, version, variables, schema_ref`; `answer_model` is serialized as its JSON Schema plus `schema_ref`, never as an import path alone |

Rules:

- Evidence records are a discriminated union on `kind` (`inclusion`, `structured`,
  `inference`); unknown kinds are rejected.
- `Derivation`, `Attribute`, and `VariableOutcome` are nested objects, not flattened.
- A JSON record is **self-describing but not self-validating**: loading requires the
  authoritative `Source` (via the resolver, §5.6) and the `VariableSet`, exactly as
  `load_case_result` requires them today. Readers that lack them may parse but must not mark
  the record as validated.

### 5.4 Parquet tables

A **bundle** is a directory:

```
<bundle>/
  manifest.json
  sources.parquet            required
  mentions.parquet           required (may be empty)
  claims.parquet             required
  evidence.parquet           required
  evidence_edges.parquet     required
  cases.parquet              required
  variable_outcomes.parquet  required
  annotation_events.parquet  optional (review/gold bundles)
  examples.parquet           optional (gold bundles): one row per (example, final variable)
  decisions.parquet          optional: policy audit records
```

Tables and columns (keys in bold; `→` is a foreign key):

**sources** — **source_id**, patient_id, kind, external_id?, datetime?, text_hash, record (JSON)?,
sections (JSON list of `{start,end,label,category?,is_template}`), meta (JSON).
`record` is the structured row for non-text sources; text sources have `record = null`.

**mentions** — **mention_id**, source_id →, start, end, quote, mention_type, label?, value (JSON)?,
unit?, attributes (JSON: `{category: {value, evidence_id?, scope_evidence_id?, assigned_by}}`),
concept (JSON)?, section_category?, fragments (JSON list)?, inputs (JSON list)?, status,
provenance_*.
Replaces lapidary's fixed `polarity/temporality/experiencer` columns with the open
`attributes` JSON; the standard categories are documented values inside it. lapidary's OMOP
link columns are not adopted in v0.1.

**claims** — **claim_id**, case_id, source_id →, patient_id, variable, variable_set,
variable_set_version, schema_ref, value (JSON), effective_datetime?, confidence?, status,
provenance_*.
`value` is never null: a missing answer is a `variable_outcomes` row, not a null claim.

**evidence** — **evidence_id**, kind, source_id →?, start?, end?, quote?, alignment?,
alignment_score?, mention_id →?, field?, value (JSON)?, datetime?, rationale?, inputs (JSON
list)?, derivation (JSON)?, trace_id?, span_id?, provenance_*.

**evidence_edges** — **claim_id →, evidence_id →, role?**, weight?.

**variable_outcomes** — **case_id, variable**, status, reason?, failure_kind?,
reviewed_source_ids (JSON list), evidence_ids (JSON list), final_claim_ids (JSON list),
provenance_*.

**annotation_events** — **event_id**, case_id, variable?, actor, role, round, stream?, action,
target_id?, before (JSON)?, after (JSON)?, reason?, at, external_ref?, provenance_*.

**examples** — **example_id, variable**, case_id, variable_set, variable_set_version, split?,
tier, outcome_status, final_claim_ids (JSON list), annotators (JSON list), adjudicator?,
derived_from_events (JSON list), created_at.
The Example's claims, mentions, evidence, and edges are the rows of the other tables whose
`case_id` matches and whose status is `gold`/`verified`; a gold bundle therefore contains only
retained records.

**decisions** — **decision_id**, checked_at, permitted, reason, restriction_id?, policy_version,
destination, zone, sensitivity, payload, case_id?, run_id?.

### 5.5 Manifest

```json
{
  "schema_version": "amber/0.1",
  "bundle_kind": "run" | "review" | "gold",
  "producer": {"name": "lapidary", "version": "0.3.0", "run_id": "..."},
  "variable_set": {"name": "...", "version": "...", "schema_ref": "..."},
  "dataset": {"name": "...", "manifest_hash": "...", "sensitivity": "deidentified",
              "restriction_ids": ["physionet-chdl-1.5.0"]},
  "policy_version": "nm-2026.10",
  "sources": {"count": 40, "text_hash_algorithm": "sha256"},
  "tables": {"cases": {"rows": 40, "sha256": "..."}, "claims": {"rows": 40, "sha256": "..."}, ...},
  "payload_kinds": {"evidence.parquet": "derived_text", "mentions.parquet": "derived_text",
                    "annotation_events.parquet": "derived_text", "claims.parquet": "labels_only", ...},
  "created_at": "2026-10-30T20:00:00Z"
}
```

The manifest is `labels_only`. `payload_kinds` is computed by the writer from the columns
present (any `quote`, cue quote, or free-text human column makes a file `derived_text`); the
reader recomputes and rejects a manifest that under-declares. Table hashes let a reader detect
a modified file before validation.

### 5.6 Source resolver

Tables carry `text_hash`, not text. Any validation that needs text (quote equality, bounds,
section membership, attribute cue quotes) takes a resolver:

```python
class SourceResolver(Protocol):
    def resolve(self, source_id: str) -> Source | None   # returns the authoritative Source or None
```

Rules:

- The loader calls the resolver for every `source_id` in `sources.parquet`, checks that
  `sha256(Source.text)` (or canonical `record`) equals `text_hash`, and otherwise fails with
  `source_hash_mismatch` naming the source ID only. A `None` result fails with
  `source_unavailable`.
- With a resolver, the loader performs full validation (§2–§4) and returns validated records.
  Without one, `load(..., resolver=None)` returns a **parsed, unvalidated** bundle whose records
  are wrapped so that `graph`/`cases`/`examples` boundaries refuse them; this exists for
  `labels_only` processing (metrics over claims, event replay for timing) and nothing else.
- Resolvers are consumer implementations: lapidary's reads the dataset at the manifest's
  location under its policy gate; loupe's reads the frozen source Dataset; a test resolver
  holds synthetic sources in memory. amber ships only the protocol and the in-memory one.
- The resolver is itself a `source_text` transfer when the dataset is restricted; calling it
  inside a process that already holds the data is `local`, and the enforcement point that
  constructed the resolver made that check.

### 5.7 Versioning and migration

- `amber/0.x` minors may add optional columns, JSON keys, enum values, or tables. They may not
  rename, remove, change types, or change ID derivations.
- A major change (`amber/1.0`) ships with: a migration note in `docs/migrations/`, a
  `migrate(bundle, to=...)` function for every supported path, updated fixtures in the
  conformance kit, and a conformance case that the old fixture migrated equals the new fixture.
- ID derivations are part of the format: changing `source_id` or `mention_id` inputs is a
  major change, because stored references would stop resolving.
- The `schema_ref` of an answer model is content-addressed; a changed answer model is a new
  `variable_set_version`, not a format change. Old bundles remain valid against their recorded
  set.
- Readers must preserve unknown `x_` columns and keys when rewriting a bundle.

### 5.8 Design checks against the stand-in variables

- **CORAL progression run bundle:** 40 sources (hash only), 0 mentions, ≤40 claims, inclusions
  and inferences, 40 `variable_outcomes` rows, no events. `evidence.parquet` is
  `derived_text` and stays in the restricted local path; `claims.parquet` is `labels_only`
  and may be logged to the institutional MLflow. Loading with lapidary's resolver revalidates
  every quote against `~/data/coral/v1.0` text without the bundle containing it.
- **`ds_findings_v1`:** mentions with `attributes` JSON holding negation/temporality and cue
  evidence IDs that resolve in `evidence.parquet`; claims per mention with `variable =
  "finding"`; one `variable_outcomes` row per note. The gpt-6-sol import produces a `gold`
  bundle with `tier = silver` in `examples.parquet`.
- **Stage group:** `claims.parquet` holds four mention-level rows and one `stage_group` row
  per note; the `stage_group` inference row carries `derivation` JSON naming the rule and
  input claim IDs; replay in the conformance kit reads only tables plus the registered rule.

### 5.9 Conformance cases (wire formats)

1. Fixture bundles for each stand-in round-trip (write → read → write) byte-identically.
2. A bundle with a different major `schema_version` is rejected; a lower minor loads.
3. Missing required table, missing key column, wrong column type, or duplicate key → rejected
   with a diagnostic naming the table and column only.
4. `text_hash` mismatch via the resolver → `source_hash_mismatch`; resolver `None` →
   `source_unavailable`; no resolver → unvalidated wrapper refused by graph/case/example
   boundaries.
5. A `quote` column present in a file declared `labels_only` in the manifest → rejected.
6. Table hash in the manifest differs from the file → rejected before validation.
7. Canonical JSON: a JSON column with unsorted keys or whitespace is rejected (writers must
   canonicalize; readers detect).
8. Unknown evidence `kind` or unknown event `action` → rejected.
9. `x_` columns survive a read/write cycle; non-prefixed unknown columns are rejected.
10. A claim row with null `value` is rejected.
11. The manifest's recomputed `payload_kinds` must equal the declared ones.
12. Migration: the `amber/0.1` fixture migrated to a synthetic `amber/0.2` fixture by the test
    migrator equals the hand-written target.

### 5.10 Decisions recorded

Reviewed and accepted 2026-10-30:

1. `case_id` is a ULID minted when the case is created. The case records the
   `(dataset, source_id, variable_set)` triple, which is the join key across runs; ID reuse is
   a consumer choice, not a requirement. `cases` therefore joins the table set: **case_id**,
   dataset, dataset_manifest_hash, source_id →, patient_id, variable_set, variable_set_version,
   sensitivity, split?, created_at.
2. Annotation events are always exported as `annotation_events.parquet`; no event database is
   part of the contract. loupe revisions in MLflow are the review store; the bridge derives the
   table.
3. Sections are a JSON list on the `sources` row; no separate sections table.

## 6. Interfaces

### 6.1 Purpose and rules

Interfaces are `typing.Protocol`s in `amber.interfaces`. amber ships the protocols, the
contract objects they exchange, the validation they must call, and one **synthetic reference
implementation** of each in the optional `reference` extra. Real implementations live in
lapidary (extractors, providers, stores, resolvers, rules, labelers), loupe (review backend),
and amber's own MLflow plugins (scorers, exporters, the loupe bridge).

Rules that apply to every interface:

- **Structural typing.** Implementations do not inherit from amber classes; they satisfy the
  protocol. amber never imports an implementation.
- **Contract objects in, contract objects out.** No interface exchanges dicts, DataFrames, or
  framework objects. Parquet and JSON appear only at the `Store`/`Exporter` boundary.
- **Validation is amber's.** An implementation produces candidates and calls amber's
  `quote`, `cite`, `commit_claim`, `no_claim`, `create_case_result`; it never constructs
  guarded records directly. The conformance kit detects direct construction.
- **Capabilities are declared, not inferred.** Each interface exposes a frozen `capabilities`
  declaration; callers check it before relying on a feature, and the kit checks that declared
  capabilities are true on synthetic inputs.
- **Policy before transfer.** Any interface that moves data accepts a `PolicyGate` and calls
  it before the transfer (§1.5). The reference implementations refuse to run without one.
- **Experimental until used.** A protocol is marked `@experimental` until a real consumer uses
  it and the kit covers it (charter rule); the first consumer is named below.

### 6.2 The protocols

```python
class PolicyGate(Protocol):
    matrix: PolicyMatrix
    def check(self, data: DataPolicy, destination: Destination, payload: PayloadKind,
              *, now: datetime) -> Decision: ...
    def require(self, data, destination, payload, *, now) -> Decision: ...   # raises PolicyViolation
```
Reference: `amber.policy.StaticGate(matrix)`. First consumer: lapidary runner (lap-ecc.4).

```python
class SourceResolver(Protocol):              # §5.6
    def resolve(self, source_id: str) -> Source | None: ...
```
Reference: `InMemoryResolver`. First consumer: lapidary dataset loader and loupe bridge.

```python
class Provider(Protocol):
    destination: Destination                 # kind == "model"; includes zone and capabilities
    def complete(self, request: ProviderRequest) -> ProviderResponse: ...
```
`ProviderRequest`: messages or prompt, optional JSON schema (requires `schema_constrained`),
optional tools (requires `tools`), budget (max tokens, timeout). `ProviderResponse`: text or
parsed JSON, token counts, latency, cost estimate, raw trace reference, and a
`ProviderFailure` variant (`parse`, `provider`, `budget`) that maps to `failure_kind`.
Providers do **not** see `Source` objects; the extractor decides what text to send and has
already passed the gate. Reference: `ScriptedProvider` (returns canned responses). First
consumer: lapidary LangExtract/vLLM method (lap-ecc.14).

```python
class Extractor(Protocol):
    name: str; version: str
    destination: Destination                 # the provider it will use, or local:<name>
    capabilities: frozenset[Literal["mentions", "note_claims", "derivations", "batch"]]
    def run(self, case: Case, source: Source, variables: VariableSet, *,
            gate: PolicyGate, budget: Budget, provenance: Provenance) -> CaseResult: ...
```
Contract: returns exactly one `CaseResult` per call, always, including on failure
(`outcome.failed` with a `failure_kind`); never raises for expected failures; records
`grounding_failures` and `tool_calls`; stays within `budget` (calls, tokens, retries, wall
time) and reports usage in the result's provenance. Everything in the result passed through
amber's tools. Reference: `DeterministicExtractor` (rule-like, synthetic). First consumer:
lapidary methods (lap-ecc.7).

```python
class Rule(Protocol):                        # §3.4
    spec: RuleSpec
    def apply(self, inputs: Mapping[str, Claim | Evidence | None],
              parameters: Mapping[str, Any]) -> RuleResult: ...
```
`RuleResult` is a value for the deriving variable, or an outcome status with reason. Rules are
pure: no I/O, no clock, no randomness; the kit replays them. Registry: `amber.rules.Registry`
keyed by `name@version`, populated by the consumer at startup. Reference: `AjccStub` (a tiny
invented staging table, clearly labeled non-clinical). First consumer: lapidary rule pack for
the stage-group stand-in.

```python
class Labeler(Protocol):
    name: str; version: str
    tier: ReferenceTier                      # the tier this labeler can produce (silver for models/rules)
    def label(self, case: Case, source: Source, variables: VariableSet, *,
              gate: PolicyGate, provenance: Provenance) -> Example | LabelRecord | LabelerFailure: ...
```
A `Labeler` wraps a teacher model, a rule set, or an importer of stored predictions, and
produces tiered references. A model or rule labeler may only declare `tier=silver`; human
tiers come from `ReviewBackend` imports. Reference: `ImportLabeler` over a synthetic JSON
file. First consumer: lapidary gpt-6-sol importer (lap-ecc.13).

```python
class Store(Protocol):
    capabilities: frozenset[Literal["events", "examples", "query", "transactions"]]
    def put_result(self, result: CaseResult) -> None: ...
    def get_result(self, case_id: str, *, resolver: SourceResolver) -> CaseResult: ...
    def append_events(self, events: Sequence[AnnotationEvent]) -> None: ...
    def events(self, case_id: str, *, stream: str | None = None) -> Sequence[AnnotationEvent]: ...
    def project(self, case_id: str, *, actor: str | None, resolver: SourceResolver) -> CaseResult: ...
    def put_example(self, example: Example) -> None: ...
    def get_example(self, example_id: str, *, resolver: SourceResolver) -> Example: ...
```
Contract: every `get_*`/`project` revalidates through amber's load boundaries with the
resolver; `append_events` is append-only (no update or delete); `project` honors blind
streams; `put_example` rejects a split change for an existing case and a tier that the events
do not support. Reference: `InMemoryStore`. First consumer: amber's Parquet bundle store (a
reference too) and lapidary's run artifacts.

```python
class Exporter(Protocol):
    format: str                              # "amber-bundle/0.1", "mlflow-dataset", "trl-jsonl", ...
    payload: PayloadKind                     # the most sensitive kind this export emits
    def export(self, items: Sequence[CaseResult | Example | LabelRecord], target: Destination, *,
               gate: PolicyGate, data: DataPolicy) -> ExportReceipt: ...
```
Contract: calls the gate with its declared `payload` before writing; `ExportReceipt` carries
the manifest hashes and the `Decision`. Reference: `BundleExporter` (writes §5 bundles).
First consumer: lapidary runner artifact logging (lap-ecc.10).

```python
class Scorer(Protocol):
    name: str
    needs: frozenset[Literal["examples", "label_records", "mentions", "traces"]]
    def score(self, predicted: Sequence[CaseResult], reference: Sequence[Example | LabelRecord],
              *, variables: VariableSet) -> ScoreReport: ...
```
`ScoreReport`: metrics with numerators and denominators, per-variable breakdown, the
reference **tier mix**, a `provisional: bool` that is true when any reference is below
`adjudicated`, and `inconclusive` entries for metrics the reference tier cannot support. A
scorer refuses (`self_reference`) when a silver reference's producer provenance matches the
predicted results' configuration. Scorers never see source text unless `needs` includes
`mentions` (span overlap requires offsets, not text). Reference: `StructuralValidityScorer`,
`LabelCorrectScorer`. First consumer: lapidary `lap eval` (lap-ecc.12).

```python
class ReviewBackend(Protocol):
    destination: Destination                 # kind == "review"
    def export_cases(self, cases: Sequence[tuple[Case, Source, CaseResult | None]], *,
                     variables: VariableSet, streams: Sequence[str], gate: PolicyGate,
                     data: DataPolicy) -> ReviewBatchReceipt: ...
    def import_references(self, receipt: ReviewBatchReceipt, *, resolver: SourceResolver
                          ) -> tuple[Sequence[Example], Sequence[AnnotationEvent]]: ...
```
Contract: `export_cases` sends sources (and proposals, when a `CaseResult` is given) after a
`source_text` gate check; `import_references` returns validated Examples and the full event
log derived per §4.7, never partially validated records. Reference: `FileReviewBackend`
(round-trips through JSON files, simulating a reviewer). First consumer: the amber loupe
bridge (amber-1ah.7, amber-1ah.2).

### 6.3 Shared value objects introduced by this section

`Budget` (max_calls, max_tokens, max_retries, max_seconds), `Usage` (actual counts, stamped
into result provenance), `ProviderRequest`/`ProviderResponse`/`ProviderFailure`,
`RuleResult`, `LabelerFailure`, `ExportReceipt`, `ReviewBatchReceipt`, `ScoreReport`. All
frozen, `extra="forbid"`, `labels_only` by construction except `ProviderRequest` (which is
`source_text` and never persisted by amber).

### 6.4 Design checks against the stand-in variables

- **CORAL progression (lapidary run):** `Extractor` = fixed pipeline over a `Provider`
  (vLLM, zone `institution`), gate = `StaticGate` with the CORAL `DataPolicy`; `Store` =
  bundle; `Exporter` logs `labels_only` tables to MLflow and keeps `derived_text` local;
  `Scorer` scores against adjudicated or single-review Examples from the `ReviewBackend`
  import and marks provisional when needed.
- **`ds_findings_v1`:** `Extractor` capabilities include `mentions`; `Labeler` =
  `ImportLabeler` over gpt-6-sol stored predictions (silver); `Scorer` with
  `needs={"mentions"}` computes span overlap; `self_reference` refuses scoring gpt-6-sol
  against its own silver.
- **Stage group:** `Extractor` with `derivations` capability calls registered `Rule`s through
  the registry; the kit replays the AJCC stub from the bundle alone.

### 6.5 Conformance cases (interfaces)

1. Each reference implementation satisfies its protocol under `typing` checks and passes the
   kit; a stub that constructs an `Inclusion` or `Claim` directly fails.
2. `Extractor` returns a `failed` result (never raises) on provider failure, parse failure,
   policy refusal, and budget exhaustion, each with the right `failure_kind`; usage never
   exceeds budget.
3. `Extractor` declaring `mentions` must emit at least one mention on a fixture containing a
   known span; declaring `derivations` must emit a `Derivation` on the stage fixture.
4. `Provider` without `schema_constrained` rejects a schema request with `ProviderFailure`
   rather than returning unparsed text.
5. `Rule.apply` is pure: two calls with equal inputs give equal results; a rule touching the
   clock or randomness fails the purity fixture.
6. `Labeler` declaring a human tier is rejected at registration; a silver Example from a
   `Labeler` carries the labeler's provenance.
7. `Store.append_events` rejects updates/deletes; `project` for a stream actor excludes other
   streams; `put_example` rejects split change and unsupported tier.
8. `Exporter` without a gate call (detected by a recording gate) fails; `ExportReceipt`
   hashes match the written bundle.
9. `Scorer` sets `provisional` iff any reference tier is below adjudicated; evidence metrics
   over `LabelRecord`s are `inconclusive`; `self_reference` refusal fires.
10. `ReviewBackend` round trip on the file reference: exported cases → simulated reviews →
    imported Examples whose events replay to the same projection.

### 6.6 Decisions recorded

Reviewed and accepted 2026-10-30:

1. `Extractor.run(case, ...)` is the contract. `run_many(cases) -> Iterator[CaseResult]` is
   optional behind the `batch` capability; the kit checks input order and exactly one result
   per case.
2. The extractor enforces `Budget` and reports `Usage`; `load_case_result` validates usage
   against the recorded budget and treats an over-budget result as `failed(budget)`.
3. `Provider` exposes `complete()` only in v0.1. Streaming and embeddings are future
   capabilities, added when a consumer needs them.

## 7. Conformance catalog

*Pending.*
