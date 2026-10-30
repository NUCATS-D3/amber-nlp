# Contract v0.1 design

Status: draft in progress, 2026-10-30. This document is written in dependency order; each
section is reviewed as drafted. Sections marked *pending* are not yet written. The
[plan](../plan.md) describes the phases; the [charter](../charter.md) sets scope.

Examples use invented data only. Stand-in variables for the design checks are: CORAL current
progression (single boolean), a lapidary `ds_findings_v1` mention-level finding, and pathologic
stage group derived from T/N/M mentions (layered).

Sections: [1 Policy](#1-policy) · [2 Mentions and structured evidence](#2-mentions-and-structured-evidence) · [3 Layered
variables](#3-layered-variables) · 4 Annotated data (pending) · 5 Wire formats (pending) · 6 Interfaces
(pending) · 7 Conformance catalog (pending)

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

*Pending.*

## 5. Wire formats

*Pending.*

## 6. Interfaces

*Pending.*

## 7. Conformance catalog

*Pending.*
