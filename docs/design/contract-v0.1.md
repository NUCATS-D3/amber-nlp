# Contract v0.1 design

Status: draft in progress, 2026-10-30. This document is written in dependency order; each
section is reviewed as drafted. Sections marked *pending* are not yet written. The
[plan](../plan.md) describes the phases; the [charter](../charter.md) sets scope.

Examples use invented data only. Stand-in variables for the design checks are: CORAL current
progression (single boolean), a lapidary `ds_findings_v1` mention-level finding, and pathologic
stage group derived from T/N/M mentions (layered).

Sections: [1 Policy](#1-policy) · [2 Mentions and structured evidence](#2-mentions-and-structured-evidence) · 3 Layered
variables (pending) · 4 Annotated data (pending) · 5 Wire formats (pending) · 6 Interfaces
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

*Pending.*

## 4. Annotated data

*Pending.*

## 5. Wire formats

*Pending.*

## 6. Interfaces

*Pending.*

## 7. Conformance catalog

*Pending.*
