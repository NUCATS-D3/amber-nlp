# Contract v0.1 design

Status: **accepted** 2026-11-02. All seven sections were accepted individually on 2026-10-30;
the whole-document review on 2026-11-02 revised every section, recorded each revision in that
section's "Decisions recorded" list, and the owner signed off on the revised whole.
Implementation is tracked in Beads under `amber-ndt` and the interface, conformance, and MLflow
epics. Written in dependency order. The
[plan](../plan.md) describes the phases; the [charter](../charter.md) sets scope.

Examples use invented data only. Stand-in variables for the design checks are: CORAL current
progression (single boolean), a lapidary `ds_findings_v1` mention-level finding, and pathologic
stage group derived from T/N/M mentions (layered).

Sections: [1 Policy](#1-policy) · [2 Mentions and structured evidence](#2-mentions-and-structured-evidence) · [3 Layered
variables](#3-layered-variables) · [4 Annotated data](#4-annotated-data) · [5 Wire formats](#5-wire-formats) · [6 Interfaces](#6-interfaces) · [7 Conformance catalog](#7-conformance-catalog)

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
| **Restriction** | A declared obligation that comes with a dataset, beyond the zone matrix: a data-use agreement, a license term, an IRB stipulation, or a no-training clause. |
| **Decision** | The outcome of a check: `permitted` or `refused`, with a stable reason code, the matched rule, and the policy version. |

### 1.3 Models

```python
class Destination(BaseModel):                # frozen, extra="forbid"
    name: str                                # "vllm-lab-gpu", "mlflow-onprem", "loupe-institution"
    kind: Literal["model", "structured_source", "tracking", "artifact_store",
                  "judge", "review", "export", "training"]
    zone: Zone
    accepts: frozenset[PayloadKind]          # payload kinds this destination may receive
    retains: bool = True                     # stores payloads beyond the request
    trains_on_inputs: bool = False           # the vendor may train its own models on what it receives
    tags: frozenset[str] = frozenset()       # free labels matched by restrictions, e.g. "nm-azure-baa"

class ProviderDestination(Destination):      # kind == "model" or "structured_source"
    capabilities: frozenset[Literal["schema_constrained", "tools", "logprobs",
                                    "batch", "adapters"]]
    model: str | None                        # model identifier; None for structured sources

class Restriction(BaseModel):                # frozen
    id: str                                  # "physionet-chdl-1.5.0", "irb-STU0001234-no-vendor"
    kind: Literal["license", "dua", "irb", "contract", "internal"]
    forbids_zones: frozenset[Zone] = frozenset()
    forbids_kinds: frozenset[Destination.kind] = frozenset()
    forbids_training_on_inputs: bool = False # refuses destinations with trains_on_inputs (vendor training only)
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
    widened_from_default: bool = False       # must be True if any cell exceeds the default matrix
    note: str | None = None                  # required when widened_from_default; why, and who approved

class Decision(BaseModel):                   # frozen
    decision_id: str                         # sha256 over the canonical JSON of every other field
    permitted: bool
    reason: Literal["ok", "zone_not_allowed", "payload_not_accepted",
                    "restriction_zone", "restriction_kind", "restriction_training",
                    "restriction_tag", "restriction_payload", "restriction_expired",
                    "unknown_sensitivity", "policy_version_mismatch"]
    restriction_id: str | None
    policy_version: str
    destination: str
    destination_kind: str                    # Destination.kind
    zone: Zone
    sensitivity: Sensitivity
    payload: PayloadKind
    dataset: str | None                      # from the RunContext, when one was supplied
    run_id: str | None
    case_id: str | None
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
          payload: PayloadKind, *, now: datetime, context: RunContext | None = None,
          case_id: str | None = None) -> Decision
```

`now` is supplied by the enforcement point so the check stays deterministic and testable; the
core never reads the system clock.

Rules, applied in order; the first failure is the reason:

0. If a `context` is supplied, `context.policy_version` must equal `matrix.version`; otherwise
   `policy_version_mismatch`. A run is never checked against a different policy than the one
   it records.
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
by stripping fields on its own judgement. The core provides `labels_only_view`, with two forms:
`labels_only_view(result: CaseResult) -> LabelRecord` removes quotes, offsets, rationales, and
free text; `labels_only_view(events)` keeps only who did what and when (§4.4). The conformance
kit checks that neither output contains a string that appears in the source. Metrics are
aggregates computed by scorers; they are `metrics` by construction.

**Combining policies.** When data from several datasets is combined, its policy is
`combine(policies) -> DataPolicy`: the highest sensitivity in the order `synthetic <
deidentified < limited < phi`, and **all** restrictions, in input order with duplicate IDs
removed. Two restrictions with the same `id` but different content are an error. Restrictions
are obligations, not levels, so none is dropped as "less strict".

**Model weights are payloads.** Parameters or adapters trained on a dataset carry that dataset's
`DataPolicy` (`combine` of all training datasets' policies when several are used) and pass
the same check as `model_weights` before being stored, logged, or shared. A destination that
accepts `model_weights` must be declared; the default artifact-store examples accept it only in
`local` and `institution`.

**Two kinds of training.** *Vendor training* is a destination using what it receives to train
its own models; `Destination.trains_on_inputs` declares it and `forbids_training_on_inputs`
refuses it. *Our own training* (fine-tuning, adapters, distillation) is an ordinary transfer:
the training job is a destination of kind `training`, usually `local` or `institution`, that
receives `source_text` or `derived_text`. The trainer checks the gate before training starts,
so a dataset whose terms forbid derived models says `forbids_kinds: [training]`, and weights
that should not exist are never produced.

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
| Training or fine-tuning on the data | `training` | `source_text` or `derived_text` | training implementation (lapidary) |
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

**Producer references.** Every field that names who produced or decided something uses one
grammar, validated by the core and parsed by `amber.ids.parse_producer`:

```
rule:<name>@<version>[+<hash>]     # <hash>: content hash of the rule set (e.g. copal build hash)
model:<name>[@<version>]           # deployment or model id; prompt versions stay in Provenance
human:<user>                       # stable reviewer id, never a display name
tool:<name>[@<version>]            # deterministic tools and importers
```

It applies to `Provenance.producer`, `Attribute.assigned_by`, `AnnotationEvent.actor`, and
`VariableReference.annotators`/`adjudicator`; a string that does not parse is rejected.
Equality is exact string equality, with one exception: the scorer's `self_reference` check
(§4.2) compares rule references without `+<hash>`, so a rebuilt rule set with the same
`name@version` still counts as the same configuration. `Derivation.name` is a bare name,
because `Derivation.kind` and `.version` carry the rest. Destination names (`local:copal`,
`local:<tool>`) are a separate namespace: they say where data went, not who produced it.

### 1.7 Dataset restrictions as data

Restrictions are declared on the dataset manifest (lapidary) and travel with the `DataPolicy`.
Examples, invented values:

```yaml
sensitivity: deidentified
restrictions:
  - id: physionet-chdl-1.5.0
    kind: license
    forbids_zones: [external]
    forbids_training_on_inputs: true      # vendors may not train on the notes
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
  in `local`: permitted; to a public MLflow would be `external`: refused. A local LoRA
  fine-tune is a `training` destination in `local`: permitted, because the PhysioNet
  restriction forbids only vendor training. Whether the licence permits local fine-tuning is
  a judgement for the data owner; if it did not, the restriction would add
  `forbids_kinds: [training]` and the fine-tune would be refused before it started.
- **Discharge summaries (`phi`, IRB restriction):** only tagged institutional endpoints; the
  Qwen vLLM server qualifies once tagged. Parquet tables with `quote` columns are
  `derived_text` and may go only to the institutional artifact store. `labels_only_view`
  output may go to the same tracking server as `labels_only`.
- **Stage group (layered):** the final inference step is a deterministic rule with
  `destination="local:ajcc_v8"`; no check is needed beyond stamping. Its inputs' provenance
  already carries the model destinations that produced the T/N/M mentions.

### 1.10 Conformance cases (policy)

1. Every cell of the default matrix: permitted and refused combinations.
2. A widened matrix without `widened_from_default: true`, or with the flag but no `note`,
   fails validation.
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
    on two datasets carry `combine` of their policies.
13. A serialized `Decision` contains no source text and is accepted as a `labels_only` payload.
14. Producer references: each of the four forms parses; an unprefixed name, an unknown
    prefix, `rule:` without `@<version>`, or an empty segment is rejected in every field that
    takes one; `rule:a@1+h1` and `rule:a@1+h2` are equal for `self_reference` and unequal
    otherwise.
15. (Catalog `POL-15`.) Each transfer is checked separately: a recording gate sees one
    decision per destination and payload kind, and a decision permitting a model call is
    never reused for a tracking or export transfer.
16. (Catalog `POL-16`.) Training: a `training` destination is refused when a restriction
    lists `training` in `forbids_kinds` and permitted otherwise; `forbids_training_on_inputs`
    refuses a destination with `trains_on_inputs=True` and never refuses a `training`
    destination by itself.
17. (Catalog `POL-17`.) `combine`: the result has the highest sensitivity and every
    restriction in input order with duplicate IDs removed; two restrictions with one `id` and
    different content are an error; combining a policy with itself returns it unchanged.
18. (Catalog `POL-18`.) Decision context: with a `RunContext` whose `policy_version` differs
    from `matrix.version`, `check` refuses with `policy_version_mismatch`; a `Decision` carries
    `dataset`, `run_id`, `case_id`, and `destination_kind` when supplied; `decision_id` is the
    same for equal inputs and `now`; a `decisions.parquet` row has exactly the `Decision`
    fields.

### 1.11 Decisions recorded

Reviewed and accepted 2026-10-30:

1. Restrictions may carry `valid_until`; `check` takes `now` from the caller.
2. `model_weights` is a payload kind; trained weights inherit the most restrictive training
   `DataPolicy` and pass the same gate. *("Most restrictive" is defined by `combine`, decision
   6.)*
3. `Decision` is the audit record; amber defines it and the tracking plugin logs it;
   persistence belongs to enforcement points.

Reviewed and accepted 2026-11-02 (whole-document review):

4. Producer references use one validated grammar (`rule:`, `model:`, `human:`, `tool:`;
   §1.6) in every producer, actor, annotator, and `assigned_by` field. `self_reference`
   ignores the rule `+<hash>`; everything else compares exact strings. `Derivation.name` is
   bare; destination names are a separate namespace.
5. Vendor training and our own training are separate. `forbids_training_on_inputs` refuses only
   destinations with `trains_on_inputs=True`. Our own training is a transfer to a new
   destination kind, `training`, gated before it starts; derived models are forbidden with
   `forbids_kinds: [training]`.
6. `combine(policies)` defines a combined `DataPolicy`: highest sensitivity, all restrictions
   (input order, duplicate IDs removed, conflicting duplicates an error).
7. `Decision` gains `decision_id` (content hash), `destination_kind`, `dataset`, `run_id`, and
   `case_id`, and `decisions.parquet` matches it exactly. `check` takes an optional
   `RunContext` and refuses with `policy_version_mismatch` when its version differs from the
   matrix.
8. `PolicyMatrix` gains `widened_from_default` and `note` (required when widened);
   `Destination.retains` defaults to `True`; restrictions come with datasets only.

---

## 2. Mentions and structured evidence

### 2.1 Purpose

A **mention** is a typed span of source text found by a detector, a rule engine, a model, or a
human. It is the unit that lapidary scores at span level, that copal produces, that loupe
reviewers edit, and that layered variables (§3) build on. A mention is **not** evidence by
itself: evidence is minted only by grounding, and a claim cites the `Inclusion` at the
mention's position.

A mention has two parts with different identities:

- the **span** (`Mention`): where the text is and what type it is. It is content-addressed, so
  every producer that finds the same span with the same type refers to the same `mention_id`;
- a **reading** (`MentionReading`): one producer's interpretation of that span (label, value,
  attributes, concept) with its own provenance and review status. A span may have any number
  of readings; disagreement between producers is several readings of one span, and a human
  correction is a new reading, never an edit.

**Structured evidence**, a cited field of a structured row (a lab value, a medication order),
is deferred to a later minor (§2.6). In v0.1 the only source-backed leaf is `Inclusion`.

### 2.2 Mention model

```python
class Attribute(BaseModel):                  # frozen
    value: str | bool | int                  # strict JSON scalar; never null; the category decides its meaning
    evidence_id: str | None                  # Inclusion of the cue span ("no evidence of"), if any
    scope_evidence_id: str | None            # Inclusion of the cue's scope, if any; must contain the mention
    assigned_by: str | None = None           # None = the reading's producer; else another producer reference

class Mention(BaseModel):                    # frozen, extra="forbid"; the span, shared by all producers
    mention_id: str                          # sha256(source_id, start, end, mention_type[, fragments])
    source_id: str
    start: int; end: int                     # end-exclusive code points into Source.text
    quote: str                               # == Source.text[start:end]; validated against the Source
    mention_type: str                        # consumer-declared type: "tnm_t", "finding", "medication"
    fragments: tuple[tuple[int, int], ...] = ()   # discontinuous parts; (start, end) is their hull
    section_category: str | None             # from the Source's sections, if any

class MentionReading(BaseModel):             # frozen, extra="forbid"; one producer's reading of a span
    reading_id: str                          # ULID
    mention_id: str                          # the Mention this reading interprets
    label: str | None                        # consumer-declared sub-label (e.g. "T2", "metastasis")
    value: str | float | int | None          # captured or normalized value ("T2", 8, 3.4)
    unit: str | None
    concept: Concept | None                  # unchanged from the prior contract; optional
    attributes: Mapping[str, Attribute]      # keyed by category: "negation", "temporality", ...
    inputs: tuple[str, ...] = ()             # producer-side evidence IDs (copal span IDs, model trace IDs)
    status: Literal["proposed", "verified", "rejected", "gold"]
    provenance: Provenance
```

Decisions behind the fields:

- **Attributes are an open mapping, not fixed enums.** The prior contract fixed
  `polarity`, `temporality`, `experiencer`. Those remain the *recommended* categories, and the
  conformance kit ships the standard value sets for them (`negation: true|false`,
  `temporality: current|historical|hypothetical|future`, `experiencer: patient|family|other`,
  `uncertainty: true|false`). But copal categories are opaque strings chosen by the rule set,
  and tasks need others (laterality, severity). A **task declares which attribute categories
  and value sets it requires** (§3); validation checks a reading against that declaration, not
  against a global enum. Absent category means *not assessed*, not a default value, matching
  copal's driver semantics; an assessed category always has a value (`negation=False` is
  "looked, not negated"). A required category that is absent fails the requirement.
- **Attribute values are strict scalars.** `str`, `bool`, or `int`; never `null`, `float`,
  lists, or objects. Values compare with strict types, so `True` never equals `1` (Python's
  `True == 1` would otherwise let a boolean satisfy an integer value set). Graded or measured
  quantities belong in the reading's `value`/`unit`, not in an attribute.
- **Attributes carry their own evidence.** The cue ("no evidence of") and its scope are
  Inclusions, minted by the same grounding path. This is what lets a negated mention be
  *checked*: a reviewer or scorer can see the cue span, and §3 can require that a derived value
  which depends on an attribute cites the attribute's evidence.
- **The scope is what ties a cue to a mention.** A cue shows that a negating (or historical,
  or family) phrase exists; the scope shows how far it reaches. When `scope_evidence_id` is
  set, the mention's hull must lie inside the scope inclusion, otherwise the reading is
  rejected with `attribute_outside_scope`. This catches a real cue attached to the wrong
  mention (in "no evidence of distant metastasis, but regional nodes are positive", a negation
  on "regional nodes" whose scope ends at "but"). The scope need not contain the cue
  (post-negation: "metastasis was ruled out"). It is optional, shown to reviewers, and never
  required in a claim's citations: containment is checked once, on the reading.
- **`value` is captured text or a normalized value, never free text.** For `tnm_t` the value
  is the captured token `T2`; for a measurement it is the parsed number plus `unit`. How the
  value was derived is recorded by the reading's provenance (and, for copal, the declared
  adapter policy's capture), not implied.
- **`assigned_by` is set only when an attribute came from someone other than the reading's
  producer:** another component of a composed pipeline, or a value carried over unchanged
  into a human correction. `None` means the reading's own producer assigned it; setting it
  equal to that producer is rejected as redundant, so the field always signals a real
  difference.
- **The span holds only what every producer must agree on.** Position, quote, type, fragments,
  and section are facts about the source; anything a producer could get wrong or disagree
  about (label, value, unit, concept, attributes) is on the reading. `section_category` is
  derived from the span and the Source's sections, so it is a span fact.
- **Discontinuous mentions** keep their `fragments` on the span. `mention_id` hashes
  `fragments` when it is nonempty, so a discontinuous mention and a contiguous one with the
  same hull and type have distinct identities; contiguous mentions hash exactly as before.
  Quotes of fragments are validated individually.
- **Span identity excludes readings.** Two producers that find the same span with the same
  type produce one `Mention` and two `MentionReading`s; differing readings are a disagreement
  to reconcile, not two mentions. This mirrors `Inclusion` (content-addressed, shared) and
  `Claim` (ULID, provenance, lifecycle).
- **Readings are never edited.** A correction, including a reviewer changing one attribute, is
  a new reading; the annotation event links old to new (§4.5), as for claims.

**Minting.** A `Mention` is minted only by the `mention()` tool, beside `quote()`:

```python
def mention(source_id: str, text: str, hint_start: int | None, mention_type: str, *,
            fragments: tuple[tuple[int, int], ...] = (),
            sources: Mapping[str, Source]) -> Mention | GroundingFailure
```

It applies the same guard as `Inclusion`: bounds, `quote` equality against the authoritative
`Source` (for the hull and for each fragment), fragments inside the hull, and
`section_category` derived from the Source's sections. Cue and scope spans are not mentions;
they are ordinary inclusions from `quote(source_id, text, hint_start)`. A `MentionReading`
holds no source text of its own and is validated when it is registered in a graph:
`mention_id` resolvable, every `evidence_id` and `scope_evidence_id` in its attributes
resolvable, and the mention's hull inside each attribute's scope when one is set. A `Mention`
in a graph or result has at least one reading.

Because every record is frozen, construction follows the references: ground cue and scope
inclusions with `quote()`; ground the span with `mention()`; build the reading with those IDs;
register evidence, then the mention, then its readings.

### 2.3 Mentions and evidence

Mentions enter the evidence graph in two ways:

1. **As cited spans.** `cite(mention) -> tuple[Inclusion, ...]` returns the inclusion over
   the mention's hull, or one per fragment, through the grounding path. Inclusions are
   positions only: they carry no `mention_id`, so one position has one inclusion ID however
   many mentions, cues, or scopes share it. The link from evidence to a mention goes through
   the claim's `reading_id` and the reading's span; `source_leaves()` reports, for each
   inclusion, the mentions and readings registered at that position.
2. **As claim subjects.** A **mention-level task** (§3) has one claim per mention. The claim
   names the one reading it relies on in `Claim.reading_id` (required for mention-level
   variables, forbidden for note-level ones), and its answer schema includes that reading's
   value and the attribute categories the task requires. The reading's span is in the claim's
   source; the claim cites the inclusion(s) at the span; and the claim never relies on a
   `rejected` reading.

Rule for attribute-dependent support: if a claim's value depends on an attribute (the stage M
claim is `M0` because the metastasis mention is negated), the claim cites the attribute's cue
with an edge whose role is `attribute:<category>`:

```python
EvidenceEdge(claim_id=m_claim, evidence_id=cue_inclusion_id, role="attribute:negation")
```

`attribute:` roles are a separate namespace from answer-field roles: they are used under both
evidence policies and do not count toward field coverage under `evidence_policy="field"`. The
same cue may also be cited with a field role when an answer field rests on it. The graph
validator checks the task's declared attribute requirements: for each requirement with
`evidence == "required"`, the claim's reading must carry the category with a cue
`evidence_id`, and the claim must have the `attribute:<category>` edge to exactly that
inclusion; otherwise `missing_attribute_evidence`. An attribute with no cue (a model asserted
"negated") is permitted on the reading but cannot satisfy a required attribute in a claim.

### 2.4 Mapping copal output to mentions

copal produces immutable spans with `start`, `end`, `quote`, `layer`, `label`, `rule_id`,
opaque `payload`, `attrs` (including `modifiers`), `inputs`, and rule-set provenance
`name@version+hash`. The mapping is a **declared adapter policy**, versioned and tested, not
an implicit conversion. Each mapped copal span yields one `Mention` (reused if the span already
exists) and one `MentionReading` with `status="proposed"`:

| copal | amber | Notes |
|---|---|---|
| `start`, `end`, `quote` (code-point units) | `Mention.start`, `.end`, `.quote` | Re-validated against the amber `Source`; the amber Source text must be the exact copal `Text.source`. |
| `layer` or `label` per policy | `Mention.mention_type`, `MentionReading.label` | The policy declares which copal layer maps to which type. |
| named capture | `MentionReading.value` | e.g. capture `t` in `p?T(?P<t>[0-4][a-c]?)` → `value="T2"`. |
| `attrs.modifiers[category] = {value, trigger_id, scope_id}` | `MentionReading.attributes[category]` | `value` → `Attribute.value`; `trigger_id` → cue Inclusion via `quote(get_span(trigger_id).quote, hint_start=start)`; `scope_id` → scope Inclusion the same way; `assigned_by = None` (the rule set is the reading's producer). |
| sectionizer span containing the mention | `Mention.section_category` | Section spans also populate `Source.sections` at import. |
| `payload` | `MentionReading.concept` or `.label` | The consumer's lexicon payload; mapped per policy (a concept record, or nothing). |
| span IDs of `inputs` | `MentionReading.inputs` | Document-local copal IDs, prefixed with the document key, kept for audit. |
| rule-set provenance, copal version, lexicon `build_hash` | `MentionReading.provenance.producer`, `.version` | `producer = "rule:<ruleset>@<version>+<build_hash>"` (§1.6); `destination = "local:copal"`; zone `local`. |

The adapter never repairs offsets. A copal span whose quote does not match the amber source
(wrong text version, normalization applied before copal) is reported as a mapping failure with
a source-free diagnostic, and no mention is created.

Units: copal returns code points by default; the adapter must request code points, never UTF-8
or UTF-16 units. The conformance kit includes emoji, combining marks, and CRLF sources.

### 2.5 Mentions from models and humans

- **Model-proposed mentions** (LangExtract, GLiNER, an LLM) arrive as candidate offsets or
  candidate strings. Both go through `mention()`: candidate strings without a hint, candidate
  offsets as `hint_start`, rejected on quote mismatch. Each grounded candidate yields the span
  (shared if another producer already found it) and a reading carrying the model's
  provenance. Attributes asserted by the model without a cue get `evidence_id=None`; such
  attributes cannot satisfy a required attribute in a claim (§2.3). When a pipeline combines
  producers (model spans, rule negation), the reading's provenance is the pipeline and each
  attribute from another component sets `assigned_by` to that component. A grounding failure
  is counted on the result, not silently dropped.
- **Human mentions** (loupe) are minted by the review bridge through the same grounding path,
  in readings with `producer="human:<user>"`. Accepting a proposed reading unchanged moves it
  to `verified`; any change produces a new human reading of the same span. Attributes the
  reviewer set have `assigned_by=None`; attributes carried over unchanged from the corrected
  reading keep the original producer in `assigned_by`, so scorers can tell reviewed values
  from inherited ones. The reviewer's cue selection, when present, becomes the attribute
  evidence. loupe's current `loupe-annotation-v1` (concept, four context axes, supporting
  text) maps directly: supporting text → attribute `evidence_id`.

### 2.6 Structured evidence (deferred)

Structured evidence, a cited field of a structured row such as a lab value or a medication
order, is **deferred to a later minor**. No v0.1 stand-in uses it, and a v0.1 case cannot cite
it: a case holds exactly one note and all evidence must come from the case's sources. Following
the charter's "experimental until used" rule, its details are designed when a consumer needs
them. v0.1 reserves what that design must not have to undo:

- **Evidence kind.** `structured` is reserved in the evidence discriminated union. In 0.1 a
  `structured` evidence payload is rejected with `unsupported_evidence`, as any other
  unsupported evidence is today.
- **Sources.** `Source.record` stays: a structured `Source` holds its row as immutable JSON in
  `record` and has no `text`. Its `source_id` includes the canonical `record` (§5.2) whenever
  `record` is set, so a changed row is a new source and two rows never share an ID. Note
  sources hash exactly as before.
- **Case scope.** The expected shape is a case with one note plus `structured_source_ids` (rows
  of the same patient that may be cited), leaving the one-note rule intact.
- **Still to design** when a consumer appears: how a row's schema is identified and linked to
  its `Source`, field types (including codes), the exact value comparison, the effective time,
  and where the fetch's policy decision is recorded.

### 2.7 Design checks against the stand-in variables

- **`ds_findings_v1` (discharge-summary finding):** copal dictionary match → mention type
  `finding`, label from the lexicon payload, `negation` and `temporality` attributes from the
  modifier driver with cue inclusions, `section_category` from the sectionizer. A
  mention-level task declares `requires_attributes={"negation": {true,false},
  "temporality": {...}}`. The mention claim cites the mention inclusion plus both cue
  inclusions. A model-only run (LangExtract) yields the same mention IDs when it finds the same
  spans, plus its own readings, whose cue-less model attributes are permitted on the reading
  but
  trigger `missing_attribute_evidence` if the task requires evidence for them; the task author
  chooses whether to require it.
- **Stage group leaves:** `tnm_t`, `tnm_n`, `tnm_m` mentions with captured `value`;
  `section_category="final_diagnosis"` required by the T/N/M tasks so a historical `T2` in the
  clinical history cannot be cited. A reading of the M mention `no distant metastasis`
  carries `negation=true` with a cue inclusion, and the `pathologic_M=M0` claim cites it. If a
  rule reads `pT2` as `T2` and a model reads it as `T2a`, the span has two readings, and the
  `pathologic_T` claim relies on one of them.
- **CORAL progression:** unchanged; the task has no mentions, cites inclusions directly.
  Mentions are optional at the contract level.
- **A lab-backed variable (deferred, §2.6):** not expressible in v0.1; the reserved shape is a
  case with the note plus the lab row in `structured_source_ids`, citing the row's `value`.

### 2.8 Conformance cases (mentions and structured evidence)

1. A mention with `quote != Source.text[start:end]` is rejected; likewise any fragment.
2. Direct construction without the grounding context is rejected (same guard as Inclusion).
3. Two producers, same span and type: one `Mention`, two `MentionReading`s with distinct
   `reading_id`s and provenance; differing values or attributes are both retained.
4. Overlapping and nested mentions of different types coexist; no automatic merging.
5. A discontinuous mention validates each fragment; hull outside the source is rejected.
6. `section_category` not present in `Source.sections` is rejected.
7. An attribute with `evidence_id` not registered in the graph is rejected at registration.
8. A claim that depends on a required attribute without attribute evidence fails with
   `missing_attribute_evidence`; with the cue cited under role `attribute:<category>` it
   passes; the cue cited only under a field role, or a different inclusion under the
   attribute role, still fails; `attribute:` edges do not satisfy field coverage.
9. copal adapter: emoji, combining marks, and CRLF sources round-trip offsets exactly;
   a source with normalized whitespace fails mapping with a source-free diagnostic.
10. copal adapter: modifier `trigger_id`/`scope_id` become cue/scope inclusions whose quotes
    match copal's `get_span(...).quote`.
11. Model-proposed offsets with a quote mismatch are counted as grounding failures, not
    dropped silently and not repaired.
12. Structured evidence is deferred: an evidence payload with `kind="structured"` is rejected
    with `unsupported_evidence`; a structured `Source` (with `record`) whose `source_id` does
    not include its canonical record is rejected, and two rows differing only in `record` get
    different `source_id`s; note-source IDs are unchanged.
13. A claim supported only by a mention with no inclusion fails `source_free_support`.
14. (Catalog `MEN-15`.) A reading whose `mention_id` is not registered is rejected; a
    `Mention` with no reading in a graph or result is rejected.
15. (Catalog `MEN-16`.) `mention_id`: a contiguous mention hashes exactly as
    `(source_id, start, end, mention_type)`; a discontinuous mention with the same hull and
    type has a different ID; two discontinuous mentions differing only in fragments have
    different IDs.
16. (Catalog `MEN-17`.) An attribute whose `scope_evidence_id` does not contain the mention's
    hull is rejected with `attribute_outside_scope`; a scope that contains the mention but not
    the cue (post-negation) is accepted; an attribute without a scope is not checked.
17. (Catalog `MEN-18`.) Two mentions of different types at one position, a cue, and a plain
    `quote()` of that position all resolve to one inclusion with one `evidence_id`; an
    inclusion payload carrying `mention_id` is rejected.
18. (Catalog `MEN-19`.) An attribute value of `null`, a float, a list, or an object is
    rejected; `True` does not satisfy a requirement with `values={1}`, and `1` does not satisfy
    `values={True}`; an absent required category fails the requirement.
19. (Catalog `MEN-20`.) An attribute whose `assigned_by` equals the reading's producer is
    rejected; a human correction keeps `assigned_by` on carried-over attributes and `None` on
    the attributes the reviewer changed.
20. (Catalog `MEN-21`.) A mention-level claim without `reading_id`, with an unregistered or
    `rejected` reading, or with a reading whose span is in another source or is not cited, is
    rejected; a note-level claim with `reading_id` is rejected; two claims relying on two
    different readings of one span each validate against their own reading's attributes.

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

Reviewed and accepted 2026-11-02 (whole-document review):

4. A mention is split into a content-addressed span (`Mention`) and per-producer
   `MentionReading`s (ULID, provenance, status). Label, value, unit, concept, attributes, and
   inputs are on the reading; corrections are new readings. Results and Examples carry both
   (`mentions` and `mention_readings`).
5. `fragments` stay on the span and enter `mention_id` when nonempty; contiguous mention IDs
   are unchanged.
6. `Attribute.scope_evidence_id` stays optional. When set, the mention's hull must lie inside
   it (`attribute_outside_scope`); claims are not required to cite it.
7. Inclusions are positions only: `Inclusion.mention_id` is removed (code and
   `evidence.parquet`), so one position has one inclusion ID. Claims reach a mention through
   their reading. `cite(mention)` returns the hull or fragment inclusions.
8. Spans are minted by a `mention()` tool beside `quote()`; cues and scopes are ordinary
   `quote()` inclusions. Construction order: cue/scope inclusions, span, reading,
   registration.
9. `Attribute.value` and `AttributeRequirement.values` are `str | bool | int`, compared with
   strict types; `null` is forbidden; an absent category means not assessed.
10. `Attribute.assigned_by` is optional: `None` means the reading's producer; it is set only
    for attributes from another pipeline component or carried over into a human correction,
    and setting it equal to the reading's producer is rejected.
11. A mention-level claim names its reading in `Claim.reading_id` (required for mention-level
    variables, forbidden for note-level ones). Attribute cues are cited with edge role
    `attribute:<category>`, a namespace separate from answer-field roles that does not count
    toward field coverage.
12. Structured evidence is deferred to a later minor (§2.6): v0.1 cannot cite it because a
    case holds one note. The `structured` evidence kind, `Source.record`, and the
    `structured_source_ids` case shape are reserved; 0.1 rejects structured evidence with
    `unsupported_evidence`. MEN-12 and MEN-13 test the 0.1 behaviour.
13. A structured `Source`'s `source_id` includes its canonical `record` whenever `record` is
    set; note-source IDs are unchanged. Today's `ids.source_id` omits the record, so all rows
    of one patient, kind, and external ID collide; this is a code bug, tracked in Beads.

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
    values: frozenset[str | bool | int] | None   # permitted values, strict-type match; None = any value
    evidence: Literal["required", "lenient"] = "required"

class Variable(BaseModel):                   # frozen, extra="forbid"
    name: str                                # unique within the set: "pathologic_T", "stage_group"
    level: Literal["mention", "note", "patient"]   # "patient" reserved: constructible, rejected by graphs in v0.1
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
    version: str                             # semver label for people; schema_ref is the identity
    variables: Mapping[str, Variable]
    schema_ref: str                          # sha256 over the canonical JSON of the set without schema_ref
```

Validation of a `VariableSet`: names unique; every `inputs` name exists; the `inputs` relation
is acyclic; a mention-level variable has nonempty `mention_types` and no `inputs` from
note-level variables; at least one variable is `final`. A set containing a `patient`-level
variable can be constructed, but building a graph with it fails with `invalid_context`, as a
patient-scope `Task` does today.

**Identity.** `schema_ref` identifies a set's content: answer models enter it as their JSON
Schema (§5.3), and every other field as canonical JSON. `version` is a label for people.
`CaseResult`, `Example`, and `cases.parquet` record `variable_set_ref` alongside name and
version; loading with a supplied set whose `schema_ref` differs fails with
`variable_set_mismatch`, even when name and version match. A store, registry, or bundle reader
that sees one `name@version` with two different `schema_ref`s rejects the second with
`variable_set_version_conflict`, so editing a set without bumping its version is always caught.

`Task` becomes the one-variable special case: `Task(name, answer_model, instructions,
evidence_policy, scope)` is sugar for a `VariableSet` with one final variable of that name
whose `level` is the task's `scope`. Existing call sites keep working;
`EvidenceGraph(source, task=...)` and `EvidenceGraph(source, variables=...)` are both
accepted, the first wrapping into the second.

### 3.3 Claims in a multi-variable graph

`Claim` gains `variable: str` (the variable name; for a one-variable set equal to `task`) and
`reading_id: str | None` (§2.3), and keeps `schema_ref`, which now refers to that variable's
answer model. `EvidenceEdge.role` is either an answer-field name or `attribute:<category>`.
Graph validation changes from "every claim matches the graph's task" to:

1. `claim.variable` is in the set, and `claim.value` validates against that variable's
   `answer_model`; `schema_ref` matches.
2. For a mention-level claim: `reading_id` is set and registered (otherwise
   `missing_reading`); the reading is not `rejected` and its span is in the claim's source; the
   claim cites the inclusion(s) at that span (hull, or every fragment); the span's
   `mention_type` is in the variable's `mention_types`; and every `requires_attributes` entry
   is satisfied on that reading (category present, value in `values`, and, when
   `evidence == "required"`, a cue `evidence_id` cited with role `attribute:<category>`),
   otherwise `missing_attribute_evidence` or `attribute_value_not_permitted`. Attributes on
   other readings of the same span never satisfy the requirement. A note-level claim with
   `reading_id` set is rejected.
3. If `requires_section` is set, every cited Inclusion lies inside a `Source.sections` span
   with one of those categories; otherwise `citation_outside_section`.
4. For a claim with `InferenceEvidence` inputs that are claims: each input claim's `variable`
   must be in this variable's declared `inputs`; otherwise `undeclared_dependency`. Inputs
   that are evidence IDs (inclusions) are always permitted.
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
    name: str                                # bare name: "ajcc_breast_stage_group", "gpt-x", "<user>"
    version: str                             # rule table version, prompt version, or review revision
    inputs: Mapping[str, tuple[str, ...]]    # role -> every input id the role consumed (§3.6)
    parameters: Mapping[str, Any] = {}       # rule configuration only; e.g. {"edition": 8, "site": "breast"}
    defaults_used: Mapping[str, Any] = {}    # role -> default value actually applied (rule only)

class InferenceEvidence(...):
    ...
    derivation: Derivation | None = None     # None keeps the current free-rationale behaviour
```

Rules for derivations:

- Every ID in `inputs` must be in the inference's existing `inputs` list (so the graph rules
  still apply) and the role names must be exactly those the deriving variable's rule expects;
  the rule's expected roles are part of the rule's registration (§3.6).
- A **rule** derivation is replayable: amber defines a `Rule` protocol,
  `apply(inputs: Mapping[str, RoleInput], parameters) -> RuleResult`, where `RoleInput` is
  `Claim | Evidence | None` for a `one` role and `tuple[Claim | Evidence, ...]` for a `many`
  role (§3.6), and `RuleResult` is either a value for the deriving variable or an outcome
  status with a reason. Rule
  implementations live with the consumer (lapidary, a rule pack) and are registered by
  `name@version`; amber's conformance kit can **replay** a derivation when the rule is
  registered and checks that the replayed value equals the claim's value. A mismatch is
  `derivation_mismatch`. Unregistered rules are not replayed; the record is still validated
  structurally. Replay applies exactly when `kind == "rule"` and the rule is registered; there
  is no separate flag.
- **Defaults are part of the record, not the configuration.** `parameters` holds only what
  configures the rule; `defaults_used` holds, per defaulted role, the value that was assumed.
  A role may appear in `defaults_used` only if its `inputs` tuple is empty, and only rule
  derivations use defaults. When the rule is registered, the role's `on_missing` must be
  `default` and the value must equal `RuleSpec.defaults[role]`, otherwise
  `derivation_mismatch`. Replay calls `apply(inputs, parameters)` with each defaulted role
  set to its recorded value; the rule never sees `defaults_used` itself. An unregistered
  rule's record still shows what was assumed.
- A **model** derivation records prompt/model versions through `Provenance` as today and is
  never replayed.
- A **human** derivation records the review revision; never replayed.

A rule may also accept **inclusions** as inputs (role → evidence IDs), and structured
evidence once it lands (§2.6), so a rule such as "ECOG from an explicit score mention, else
from functional-status mentions" is expressible.

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
    run_id: str                              # ULID minted by the runner; equals provenance.run_id on every node
    budget: Budget | None                    # limits the run was given (§6.3)
    usage: Usage | None                      # what the run actually used; checked against budget on load
    outcome: CaseOutcome | None              # one-variable sets only: that variable's outcome
    final_claim_ids: tuple[str, ...] | None  # one-variable sets only: that variable's final IDs
```

`variable_outcomes` is the record of what was answered. For a one-variable set (every `Task`),
`outcome` and `final_claim_ids` must equal that variable's `VariableOutcome` exactly, so
today's code and payloads keep working. For a set with several final variables both are `null`:
a case-level `CaseOutcome` would need a reason, failure kind, evidence, and provenance that no
producer created, and `final_claim_ids` cannot be both a union and "non-empty iff answered".

For filtering, a pure helper `case_status(result) -> OutcomeStatus` returns the most severe
variable status by a fixed order (failed > conflicting_evidence > insufficient_evidence >
not_mentioned > answered). It is computed on demand and never stored, so no stored field can
disagree with it.

Intermediate (non-final) variables do not get a `VariableOutcome`; their absence is visible
through the derivation that needed them (§3.6).

### 3.6 Outcome propagation through rules

A mention-level variable has one claim per mention, so a role can match zero, one, or several
claims in a case (a note stating `pT2` in the final diagnosis and again in a synoptic table
has two `pathologic_T` claims). How many claims a role takes, and what happens when it has
none, is **declared in the rule's registration**, not improvised:

```python
class RoleSpec(BaseModel):                   # frozen
    source: str                              # a variable name, or "evidence:inclusion" ("evidence:structured" reserved)
    cardinality: Literal["one", "many"] = "one"
    on_missing: Literal["refuse", "default", "pass_none"] = "refuse"

class RuleSpec(BaseModel):                   # frozen; registered with the rule implementation
    name: str; version: str
    roles: Mapping[str, RoleSpec]
    outputs: str                             # the deriving variable name
    defaults: Mapping[str, Any] = {}         # role -> default value when on_missing == "default"
```

**Reduction.** Before applying a rule, amber gathers each role's candidates: the non-rejected
claims of the role's variable in the case (or the evidence named for an evidence role). For a
`many` role the rule receives all of them as a tuple, ordered by ID; an empty tuple counts as
missing. For a `one` role amber reduces them by a fixed rule:

| Candidates | Result for the role |
|---|---|
| none | **missing**: `on_missing` applies |
| one, or several whose values are equal (canonical JSON of `Claim.value`) | **one input**: the rule receives the candidate with the lowest ID; the derivation lists every candidate under the role |
| several with different values | **conflict**: the rule is not applied |

Rules never see a conflict and never resolve one. A derived variable with a conflicting role is
`conflicting_evidence`, and its outcome cites the evidence of every conflicting claim. The
conflict propagates: a rule whose input variable is itself conflicting treats that role as a
conflict, so a final variable reports `conflicting_evidence` citing the original evidence even
when intermediate variables sit in between. This is where conflicts between mentions surface;
mention-level variables themselves never carry `conflicting_evidence` (§3.10 decision 3).

**Missing inputs.** A role is missing when it has no candidates (the mention-level question was
`not_mentioned` or `insufficient_evidence`, or the mention-level work was never run):

- `refuse`: the derived variable is `insufficient_evidence` with reason naming the role. The
  rule is not applied.
- `default`: the declared default is used (the AJCC edition 8 breast table treats absent
  clinical M as `cM0`), and the derivation records the value it assumed
  (`defaults_used={"M": "M0"}`), so the derived claim's provenance shows it.
- `pass_none`: the rule receives `None` (or an empty tuple for a `many` role) and handles it
  (ECOG fallback).

A derived claim whose derivation used a default is still a valid answered claim; scorers can
stratify by the roles in `defaults_used`. A derived claim is never produced when any role is
in conflict.

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
  `ajcc_breast_stage_group@8.0` declares roles T, N, and edition (`one`, `refuse`) and M
  (`one`, `default` `M0`). The derived claim's `InferenceEvidence.derivation` names the rule,
  the input claim IDs per role, and `defaults_used={"M": "M0"}` when M was absent. Replay in the
  conformance kit recomputes the stage from the input claims and matches. Two `pathologic_T`
  claims both `T2` reduce to one input and the derivation lists both; `T2` and `T3` make
  `stage_group` `conflicting_evidence`, citing the evidence of both T claims, and no stage is
  derived. A `T2` cited from the
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
    the assumed value in `defaults_used`; `pass_none` invokes the rule with `None` (empty
    tuple for `many`).
11. Reduction of a `one` role: candidates with different values make the derived variable
    `conflicting_evidence`, citing the evidence of every conflicting claim, and no derived
    claim is created; the conflict propagates through an intermediate variable to the final
    one. Equal values reduce to the lowest-ID candidate and the derivation lists all of them.
    A `many` role receives every candidate in ID order. Rejected claims are never candidates.
12. One-variable set: a payload whose `outcome` or `final_claim_ids` differs from the
    variable's `VariableOutcome`, or is `null`, fails to load. Several final variables: a
    payload with a non-null `outcome` or `final_claim_ids` fails to load. `case_status`
    follows the fixed severity order.
13. Two final variables, one answered and one `not_mentioned`: `variable_outcomes` carries
    both, with final IDs only on the answered one; case-level fields are `null`; `case_status`
    is `not_mentioned`.
14. (Catalog `VAR-16`.) `defaults_used`: a defaulted role with non-empty `inputs`, defaults on
    a model or human derivation, or (for a registered rule) a role whose `on_missing` is not
    `default` or whose value differs from `RuleSpec.defaults` is rejected; replay supplies the
    recorded default and the rule never receives `defaults_used`; an unregistered rule's
    defaults round-trip unchanged.
15. (Catalog `VAR-17`.) Set identity: `schema_ref` changes when any variable field or answer
    model changes and not when `version` alone changes; loading a result against a set with the
    same name and version but a different `schema_ref` fails with `variable_set_mismatch`; a
    store seeing one `name@version` with two `schema_ref`s fails with
    `variable_set_version_conflict`. A `patient`-level variable or `Task(scope="patient")` is
    constructible, and building a graph with it fails with `invalid_context`.

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

Reviewed and accepted 2026-11-02 (whole-document review):

4. Rule roles declare `cardinality` (`one` or `many`). For `one`, amber reduces the role's
   candidates by a fixed rule: none → missing (`on_missing`), equal values → one input,
   different values → conflict. A conflict makes the derived variable `conflicting_evidence` and propagates
   through intermediate variables; rules never resolve conflicts. `Derivation.inputs` maps each
   role to a tuple of IDs. `RoleSpec` is a model (`source`, `cardinality`, `on_missing`); its
   former `required` flag is dropped because `on_missing="refuse"` expresses it. `Rule.apply`
   takes `RoleInput` values in both §3.4 and §6.2.
5. `CaseResult.outcome` and `final_claim_ids` exist only for one-variable sets, where they
   equal that variable's `VariableOutcome`; with several final variables they are `null` and
   `variable_outcomes` is the only record. `case_status(result)` computes the severity summary
   on demand and is never stored.
6. `Derivation.defaults_used` maps each defaulted role to the value assumed; `parameters` holds
   only rule configuration; `replayable` is removed (replay applies exactly to registered
   rule derivations).
7. `Task.scope` maps to `Variable.level`, which gains `patient` as a reserved value:
   constructible, rejected by graphs with `invalid_context`, matching today's code.
8. A set's `schema_ref` is its identity and `version` is a label. Results, Examples, and
   `cases.parquet` record `variable_set_ref`; a mismatch on load is `variable_set_mismatch`, and
   one `name@version` with two refs is `variable_set_version_conflict`.

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
  system is never scored against its own outputs: the scorer refuses when a silver
  reference's configuration intersects the configuration under evaluation (configurations as
  defined for `Scorer`, §6.2; rule references compared without `+<hash>`, §1.6).

### 4.3 Example

```python
class VariableReference(BaseModel):           # frozen
    variable: str
    tier: ReferenceTier
    outcome: CaseOutcome                      # answered | not_mentioned | conflicting_evidence | insufficient_evidence
    final_claim_ids: tuple[str, ...]          # nonempty iff answered; gold claims
    annotators: tuple[str, ...]               # producer references (§1.6)
    adjudicator: str | None                   # producer reference, normally human:<user>
    derived_from_events: tuple[str, ...]      # event IDs whose replay yields this reference

class Example(BaseModel):                     # frozen, extra="forbid"; loaded only through amber.examples
    example_id: str                           # sha256(case_id, variable_set_ref, as_of_event)
    schema_version: str
    case_id: str
    variable_set: str; variable_set_version: str; variable_set_ref: str
    as_of_event: str                          # last event included in the replay that produced this snapshot
    source_ids: tuple[str, ...]               # one in v0.1
    split: Literal["train", "dev", "test"] | None   # must equal the Case's split (invariant 4)
    references: Mapping[str, VariableReference]   # one per final variable
    claims: tuple[Claim, ...]                 # final and supporting claims; status set by tier (invariant 1)
    mentions: tuple[Mention, ...]             # spans of the retained readings
    mention_readings: tuple[MentionReading, ...]  # retained readings, cited or not; status set by tier
    evidence: tuple[Evidence, ...]
    edges: tuple[EvidenceEdge, ...]
    created_at: datetime
```

**Snapshots.** An Example is the projection of a case's events up to and including
`as_of_event`. Its `example_id` is derived from `(case_id, variable_set_ref, as_of_event)`, so
anyone replaying the same events gets the same ID and any earlier snapshot can be rebuilt
exactly from the event prefix. A rising tier, a correction, or an adjudicator's rejection
appends events and so yields a new snapshot; old snapshots stay valid and addressable. The
**current** Example for a case is the snapshot at its latest event. Score reports record the
`example_id`s they scored against, so a result always names the exact gold it used.

Invariants (validated on load, with the authoritative Source and VariableSet supplied, as
`load_case_result` does today):

1. The retained graph validates completely under §2–§3 rules, and claim status follows the
   tier of the reference a claim serves. Claim status records whether a person reviewed the
   claim; tier records how good the reference is; the two are never conflated.

   | Tier | Final claims | Supporting claims and cited readings |
   |---|---|---|
   | `adjudicated`, `single_review` | `gold` | `gold` or `verified` |
   | `silver` | `proposed` | `proposed` |

   A silver claim is never `verified` or `gold`, because no one reviewed it. A supporting claim
   or reading that serves several references meets the strictest of their requirements. No
   `rejected` claim or reading is retained.
2. A reference's `outcome` is never `failed`: failed runs are diagnostics, not Examples.
3. `answered` references have nonempty final IDs naming retained gold claims; the other three
   outcomes have none. An abstention is never translated to a negative answer.
4. `split` belongs to the `(dataset, source_id)` pair and is recorded on the `Case`; an
   Example's (and a LabelRecord's) `split` must equal it. Once set it never changes, and a
   store rejects two cases with the same `(dataset, source_id)` but different splits, so a
   note cannot be `train` under one variable set and `test` under another.
5. `derived_from_events` is nonempty for `adjudicated` and `single_review`; for `silver` it
   names the import event that recorded the producer's result.
6. Mentions: every mention cited by a retained claim is present, with the reading that claim
   relies on. Uncited readings may be present with any status except `rejected`; they serve no
   reference, so their own status says whether a person reviewed them, and span-level scorers
   treat `gold`/`verified` readings as gold and `proposed` ones as silver. Every retained span
   has at least one retained reading.

### 4.4 Annotation events

```python
class AnnotationEvent(BaseModel):             # frozen, append-only; ULID ids are time-ordered
    event_id: str
    schema_version: str
    case_id: str
    variable: str | None                      # None for case-level events
    actor: str                                # producer reference (§1.6)
    role: Literal["reviewer", "adjudicator", "importer", "system"]
    round: int                                # review round; independent streams share a round
    stream: str | None                        # independent review stream id, when blind review applies
    action: Literal[
        "mention.create",
        "reading.create", "reading.update", "reading.set_status",
        "claim.create", "claim.update", "claim.set_status",
        "evidence.cite", "edge.add", "edge.remove",   # "evidence.attach_structured" reserved (§2.6)
        "outcome.set",
        "review.open", "review.submit", "review.reopen",
        "adjudication.resolve", "import.result", "tier.set",
    ]
    target_id: str | None                     # mention/reading/claim/evidence/edge id the action concerns
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
- `before`/`after` carry no quotes or offsets; spans are referenced by evidence and mention
  IDs that resolve in the store. They can still carry source text: a reading's captured
  `value` or a string answer field. Together with the free-text `reason`, that makes the event
  log `derived_text`. For uses that need only who did what and when (expert-effort timing,
  tracking), `labels_only_view(events)` returns one record per event with `event_id`,
  `case_id`, `variable`, `actor`, `role`, `round`, `stream`, `action`, `target_id`, `at`, and
  `external_ref`, and drops `before`, `after`, and `reason`; its output is `labels_only`.
- Blind review: visibility depends on the actor's role, and is a store/projection rule checked
  by the kit. Until `adjudication.resolve` for the case and round, a `reviewer` in stream S sees
  events from S and events with no stream (such as imported proposals), and never another
  stream's; an `adjudicator` sees every stream in the round, since resolving requires it;
  `system` and `importer` see everything. After resolve, every actor sees every stream.
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

Mention readings follow the same table with `reading.*` actions: `proposed` →
`verified`/`rejected`, `verified` → `gold`, and a correction is `reading.update`, which creates
a new reading linked to the old one. Spans have no status: `mention.create` registers a span
the first time any reading needs it, and spans are never updated or deleted. Removing a
mention is rejecting its readings; changing its offsets or type is a different span with a new
reading. A claim can only be `gold` if every reading it relies on is `gold` or `verified`.

### 4.6 Label-only evaluation records

Silver references that carry labels but no evidence (CORAL candidate rules, a classifier) are
not Examples: they cannot satisfy invariant 1. They are `LabelRecord`s:

```python
class LabelRecord(BaseModel):                 # frozen; produced by labels_only_view or by a Labeler
    case_id: str; variable: str
    variable_set: str; variable_set_version: str; variable_set_ref: str
    tier: Literal["silver"]
    outcome_status: OutcomeStatus
    value: Mapping[str, Any] | None           # the answer value when answered
    producer: str                             # producer reference (§1.6), version included
    split: Literal["train","dev","test"] | None   # must equal the Case's split
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
| a saved revision (submission id) | one `review.submit` event with `external_ref` = submission id, preceded by the `mention.*`/`reading.*`/`claim.*`/`evidence.*` events that the revision's diff implies; the adapter computes the diff against the prior revision |
| "no target mentions" confirmation with whole-note review | `outcome.set` with `not_mentioned` and `reviewed_source_ids` = the note |
| unsubmitted or partial review | no `review.submit`; projection outcome `insufficient_evidence` |
| adjudication decision `accepted` with selected revision | `adjudication.resolve` + `tier.set adjudicated`; final claims → `gold` |
| decision `unresolved` | `adjudication.resolve` with reason; tier unchanged; no Example exported |
| decision `excluded` | `adjudication.resolve` with reason; case excluded from the Example set, retained in events |
| publication id | `external_ref` on the resolve events; `derived_from_events` of the Example |
| single-stream workflow (move-on) | `tier.set single_review` by the reviewer on submit; no adjudication events |

The bridge builds each revision through amber's tools (`quote`, `mention`, `commit_claim`)
and validates it at amber's load boundary before accepting it (`ReviewBackend`, §6.2), so the
event diff never contains an ungrounded span.

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

1. An Example with a `failed` outcome, a `rejected` retained claim or reading, a `proposed`
   final claim under a human tier, or a `verified`/`gold` claim under a silver reference fails
   to load; a silver reference with `proposed` claims loads; a supporting claim serving an
   adjudicated and a silver reference must be `gold` or `verified`.
2. `answered` with empty final IDs, or a non-answer with final IDs, fails to load.
3. An Example whose cited mention is missing from `mentions` fails; uncited mentions round-trip.
4. Tier can rise (`silver` → `single_review` → `adjudicated`) only with new events; a payload
   asserting a higher tier without events fails; a lower tier than the events support fails.
5. Split: a store rejects a second case for the same `(dataset, source_id)` with a different
   split, a split change for an existing case, and an Example or LabelRecord whose split
   differs from its case's.
6. Replay determinism: amber's replay of a random event sequence gives identical projections
   on every run, and replaying a prefix gives the prefix's projection. (Catalog `ANN-16` adds:
   a store's `project` matches amber's replay for every actor and `as_of_event`.)
7. Blind streams: before resolve, a reviewer's projection includes their own stream and
   stream-less events and excludes other streams; an adjudicator's includes every stream in
   the round; after resolve every actor sees every stream.
8. `labels_only_view(events)` contains no `before`, `after`, or `reason`, and no substring of
   length ≥ 8 from the source text; a reading's captured value appearing in an event's
   `after` keeps `annotation_events` classified as `derived_text`.
9. A `gold` claim cannot be edited in place; a correction produces a new claim and a linked
   event.
10. A claim can be `gold` only if the readings it relies on are `gold` or `verified`; a span
    is never updated or deleted, and a reading correction produces a new linked reading.
11. A `LabelRecord` cannot be loaded as an Example; a scorer scoring evidence metrics against
    LabelRecords reports `inconclusive`.
12. Scoring a configuration against silver Examples produced by the same configuration is
    refused.
13. loupe mapping fixtures: accepted → adjudicated Example; unresolved → no Example, events
    retained; excluded → no Example; no-mentions confirmation → `not_mentioned`; unsubmitted →
    `insufficient_evidence`.
14. (Catalog `ANN-15`.) Snapshot identity: replaying the same events gives the same
    `example_id`; appending an event (a tier rise, a correction) gives a new `example_id`, and
    replaying the prefix reproduces the old snapshot exactly.
15. (Catalog `ANN-17`.) A store rejects an Example whose `example_id` does not match its
    `(case_id, variable_set_ref, as_of_event)`; `current_example` returns the snapshot at the
    latest event; a `ScoreReport` lists the `example_id`s it scored against.
16. (Catalog `ANN-18`.) A move-on `tier.set` must have `role=system` and a decision reference,
    or it is rejected; a `single_review` snapshot scored under move-on must include that event
    (`as_of_event` at or after it).

### 4.10 Decisions recorded

Reviewed and accepted 2026-10-30:

1. A corrected claim gets a new `claim_id`; the `claim.update` event links old to new. Claim
   IDs cited by edges and derivations therefore never change meaning.
2. In a single-stream workflow the reviewer's `review.submit` sets `single_review`; the
   protocol's move-on decision is a separate case-level `tier.set` event by `role=system`
   carrying the decision reference, recorded before scoring.
3. The loupe bridge derives per-change events from revision diffs and stamps the revision
   checksum on `review.submit`. Fallback, if diffing is unreliable: one `review.submit` per
   revision carrying the full state. Both replay to the same projection, which
   the conformance kit checks.

Reviewed and accepted 2026-11-02 (whole-document review):

4. Claim status records review; tier records reference quality. Under human tiers final claims
   are `gold` and supporting claims and cited readings `gold`/`verified`; under `silver` they
   stay `proposed`. A record serving several references meets the strictest requirement.
   Uncited readings keep their own status. Gold bundles select rows by case, not by status.
5. An Example is a snapshot: `example_id = sha256(case_id, variable_set_ref, as_of_event)`.
   New events yield new snapshots; old ones are reproducible from the event prefix. The current
   Example is the snapshot at the case's latest event (`Store.current_example`), and score
   reports pin the `example_id`s they used.
6. Blind-review visibility is by role: reviewers see their own stream and stream-less events,
   adjudicators see every stream in the round, `system`/`importer` see everything, and
   everyone sees everything after resolve.
7. Annotation events are `derived_text` (captured values in `before`/`after`, free-text
   `reason`); `labels_only_view(events)` keeps who did what and when and is `labels_only`.
   `mention_readings` is listed as `derived_text` in §5.2.
8. `split` belongs to `(dataset, source_id)` and lives on `Case`; Examples and LabelRecords
   must match it, and a source cannot have two splits across variable sets.
9. `LabelRecord` uses one producer reference (§1.6) and records the variable set's name,
   version, and `variable_set_ref`. §4.7 cites amber's tools and load boundary instead of the
   undefined `build_result`/`validate_result`.

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
  `evidence_id`, and `example_id` (§4.3); ULIDs for `claim_id`, `reading_id`,
  inference `evidence_id`, `event_id`.
- **Offsets** are end-exclusive Unicode code-point indices into the exact source text. Parquet
  `int64`, JSON integers.
- **Times** are RFC 3339 in UTC with `Z` and exactly six fractional digits in JSON
  (`2026-10-30T20:00:00.000000Z`); `timestamp[us, tz=UTC]` in Parquet.
- **Canonical JSON** is RFC 8785 (JSON Canonicalization Scheme): keys sorted, no whitespace,
  numbers in ECMAScript form, UTF-8 without escaping non-ASCII. Timestamps inside it use the
  form above. Strings are never Unicode-normalized, because they can be source text whose
  offsets must survive. Every content-addressed ID (§5.7) and every hash below is computed
  over canonical JSON, so any implementation, in any language, reproduces them. For strings
  and integers, which are all that today's IDs hash, this equals the current `ids.py` output.
- **JSON columns** in Parquet are UTF-8 strings containing canonical JSON, never Arrow
  structs, so column schemas survive answer-model changes.
- **Row order and content hashes.** Writers sort every table by its key columns. A table's
  `content_sha256` is the SHA-256 of its rows in that order, each row the canonical JSON of
  `{column: value}` (JSON columns as their parsed values), joined by `\n`. Two writers produce
  the same `content_sha256` for the same content even when their Parquet bytes differ
  (library version, compression, row groups); file bytes are never compared across writers.
- **Scope.** A bundle holds one dataset (`manifest_hash`) and one variable set
  (`schema_ref`); every `cases` row must match the manifest, and a mixed bundle is rejected.
  The manifest's single `sensitivity` and `restriction_ids` therefore apply to every row.
- **Nulls** mean absent. A column that does not apply to a row is null; there are no sentinel
  strings.
- **Full source text is never stored in tables**; `sources` carries `text_hash`. Fragments of
  it can be, and each file's payload kind (§1) is derived from its **schema**, never from
  column names or a scan of its content:
  - A field is *closed* when every value comes from a fixed set: IDs, numbers, booleans,
    timestamps, enum values defined by the contract, producer references, or strings an
    answer model's JSON Schema limits with `enum`, `const`, or `format: date|date-time`.
  - A file is `labels_only` only if every field, including every field inside its JSON
    columns, is closed. Any open string makes it `derived_text`.
  - For `claims.parquet` the answer models of the bundle's variable set decide: a boolean or
    enum answer keeps it `labels_only`; a free-text answer field makes it `derived_text`.

  By this rule `evidence`, `mentions`, and `mention_readings` (quotes, captured values),
  `annotation_events` (`before`/`after`, `reason`), `sources` (section labels as written in
  the note, free-form `meta`), and `variable_outcomes` (free-text `reason`) are always
  `derived_text`. `cases`, `results`, `evidence_edges`, `examples`, and `decisions` are
  `labels_only`.
  Data headed for a `labels_only` destination goes through `labels_only_view` (§1.4).
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
| `CaseResult` | `schema_version, case_id, run_id, variable_set, variable_set_version, variable_set_ref, claims, mentions, mention_readings, evidence, edges, variable_outcomes, outcome, final_claim_ids, grounding_failures, tool_calls, escalated, trace_id, budget, usage` |
| `Example` | as §4.3 plus `schema_version` |
| `AnnotationEvent` | as §4.4 |
| `LabelRecord` | as §4.6 plus `schema_version` |
| `Decision` | as §1.3 plus `schema_version` |
| `VariableSet` | `schema_version, name, version, variables, schema_ref`; `answer_model` is serialized as its JSON Schema plus `schema_ref`, never as an import path alone |

Rules:

- Evidence records are a discriminated union on `kind` (`inclusion`, `inference`); unknown
  kinds are rejected, and the reserved `structured` kind is rejected with
  `unsupported_evidence` in 0.1 (§2.6).
- `Derivation`, `Attribute`, and `VariableOutcome` are nested objects, not flattened.
- `CaseResult.outcome` and `final_claim_ids` are always present as keys; they are `null` when
  the variable set has several final variables (§3.5).
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
  mention_readings.parquet   required (may be empty)
  claims.parquet             required
  evidence.parquet           required
  evidence_edges.parquet     required
  cases.parquet              required
  variable_outcomes.parquet  required
  results.parquet            required in run bundles; optional otherwise
  annotation_events.parquet  optional (review/gold bundles)
  examples.parquet           optional (gold bundles): one row per (example, final variable)
  decisions.parquet          optional: policy audit records
```

Tables and columns (keys in bold; `→` is a foreign key):

**sources** — **source_id**, patient_id, kind, external_id?, datetime?, text_hash, record (JSON)?,
sections (JSON list of `{start,end,label,category?,is_template}`), meta (JSON).
`record` is the structured row for non-text sources; text sources have `record = null`.

**mentions** — **mention_id**, source_id →, start, end, quote, mention_type, fragments (JSON
list)?, section_category?.
One row per span, shared by every reading of it; no status or provenance.

**mention_readings** — **reading_id**, mention_id →, case_id, label?, value (JSON)?, unit?,
attributes (JSON: `{category: {value, evidence_id?, scope_evidence_id?, assigned_by?}}`),
concept (JSON)?, inputs (JSON list)?, status, provenance_*.
Replaces lapidary's fixed `polarity/temporality/experiencer` columns with the open
`attributes` JSON; the standard categories are documented values inside it. lapidary's OMOP
link columns are not adopted in v0.1. `value` may be captured source text, so the file is
`derived_text`.

**claims** — **claim_id**, case_id, source_id →, patient_id, variable, variable_set,
variable_set_version, schema_ref, reading_id →?, value (JSON), effective_datetime?,
confidence?, status, provenance_*.
`value` is never null: a missing answer is a `variable_outcomes` row, not a null claim.

**evidence** — **evidence_id**, kind, source_id →?, start?, end?, quote?, alignment?,
alignment_score?, field?, value (JSON)?, datetime?, rationale?, inputs (JSON list)?,
derivation (JSON)?, trace_id?, span_id?, provenance_*.
`field`, `value`, and `datetime` are reserved for structured evidence (§2.6) and are null in
0.1.

**evidence_edges** — **claim_id →, evidence_id →, role?**, weight?.
`role` is an answer-field name or `attribute:<category>`.

**variable_outcomes** — **case_id, variable**, status, reason?, failure_kind?,
reviewed_source_ids (JSON list), evidence_ids (JSON list), final_claim_ids (JSON list),
provenance_*.

**results** — **case_id →, run_id**, grounding_failures, tool_calls, escalated, trace_id?,
budget (JSON)?, usage (JSON)?.
One row per `CaseResult`: the run-level fields that belong to no claim or evidence. `run_id`
equals the manifest's `producer.run_id`. Every field is closed, so the table is `labels_only`.
Together with the other tables it reconstructs the `CaseResult` exactly, and the loader applies
the budget check (§6.6) to it.

**annotation_events** — **event_id**, case_id, variable?, actor, role, round, stream?, action,
target_id?, before (JSON)?, after (JSON)?, reason?, at, external_ref?, provenance_*.

**examples** — **example_id, variable**, case_id, variable_set, variable_set_version,
variable_set_ref, as_of_event →, split?, tier, outcome_status, final_claim_ids (JSON list),
annotators (JSON list), adjudicator?, derived_from_events (JSON list), created_at.
A gold bundle contains only retained records (§4.3 invariant 1), so an Example's claims,
mention readings, evidence, and edges are the rows of the other tables whose `case_id`
matches, and its mentions are the spans those readings point to. Status is not used to select
rows: a silver reference's claims are retained as `proposed`.

**decisions** — **decision_id**, checked_at, permitted, reason, restriction_id?, policy_version,
destination, destination_kind, zone, sensitivity, payload, dataset?, run_id?, case_id?.
Exactly the fields of `Decision` (§1.3).

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
  "tables": {"cases": {"rows": 40, "content_sha256": "...", "file_sha256": "..."}, ...},
  "payload_kinds": {"evidence.parquet": "derived_text", "mentions.parquet": "derived_text",
                    "mention_readings.parquet": "derived_text", "sources.parquet": "derived_text",
                    "variable_outcomes.parquet": "derived_text",
                    "annotation_events.parquet": "derived_text", "claims.parquet": "labels_only",
                    "cases.parquet": "labels_only",
                    "results.parquet": "labels_only", ...},
  "created_at": "2026-10-30T20:00:00Z"
}
```

The manifest is `labels_only`. `payload_kinds` is computed by the writer from each file's
schema and the variable set's answer models (§5.2); the reader recomputes it the same way and
rejects a manifest that under-declares. The reader recomputes each table's `content_sha256`
(§5.2) and rejects a mismatch before validation; `file_sha256` is optional and checks only
that this particular copy is unmodified.

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
- ID derivations are part of the format: changing the inputs of any content-addressed ID
  (`source_id`, `mention_id`, inclusion `evidence_id`, `example_id`, the set's
  `schema_ref`) or the canonical JSON rules (§5.2) is a major change, because stored
  references would stop resolving. The answer-model `schema_ref` keeps today's form,
  `module:qualname@digest`; because it includes the Python import path, renaming a module
  changes it even when the schema is unchanged.
- The `schema_ref` of an answer model, and the set's `schema_ref`, are content-addressed; a
  changed variable is a new set `schema_ref` and needs a new `variable_set_version` (§3.2), not
  a format change. Old bundles remain valid against their recorded set.
- Readers must preserve unknown `x_` columns and keys when rewriting a bundle.

### 5.8 Design checks against the stand-in variables

- **CORAL progression run bundle:** 40 sources (hash only), 0 mentions, ≤40 claims, inclusions
  and inferences, 40 `variable_outcomes` rows, no events. `evidence.parquet` is
  `derived_text` and stays in the restricted local path; `claims.parquet` is `labels_only`
  because the answer model is a single boolean, and may be logged to the institutional
  MLflow. `variable_outcomes.parquet` is `derived_text` (free-text `reason`), so outcomes
  reach MLflow through `labels_only_view`. Loading with lapidary's resolver revalidates
  every quote against `~/data/coral/v1.0` text without the bundle containing it.
- **`ds_findings_v1`:** one `mentions` row per span and one `mention_readings` row per
  producer, with `attributes` JSON holding negation/temporality and cue evidence IDs that
  resolve in `evidence.parquet`; claims per mention with `variable = "finding"` and
  `reading_id`; cue edges with role `attribute:negation`; one `variable_outcomes` row per note. The gpt-6-sol import produces a `gold`
  bundle with `tier = silver` in `examples.parquet`.
- **Stage group:** `claims.parquet` holds four mention-level rows and one `stage_group` row
  per note; the `stage_group` inference row carries `derivation` JSON naming the rule and
  input claim IDs; replay in the conformance kit reads only tables plus the registered rule.

### 5.9 Conformance cases (wire formats)

1. Fixture bundles for each stand-in round-trip (write → read → write) with identical
   `content_sha256` for every table and byte-identical JSON records; Parquet file bytes may
   differ between writers. Rows are written sorted by key.
2. A bundle with a different major `schema_version` is rejected; a lower minor loads.
3. Missing required table, missing key column, wrong column type, or duplicate key → rejected
   with a diagnostic naming the table and column only.
4. `text_hash` mismatch via the resolver → `source_hash_mismatch`; resolver `None` →
   `source_unavailable`; no resolver → unvalidated wrapper refused by graph/case/example
   boundaries.
5. Payload kind is derived from schemas: a file declared `labels_only` that has a `quote`
   column, an open string inside a JSON column, or (for `claims`) an answer model with an
   unconstrained string field → rejected; a claims table whose answer models are boolean or
   enum-only is `labels_only`; `sources` with section labels and `variable_outcomes` with a
   `reason` are `derived_text`.
6. A table whose recomputed `content_sha256` differs from the manifest → rejected before
   validation; so is a `file_sha256`, when present, that differs from the file. A `cases` row
   whose dataset or variable set differs from the manifest → rejected.
7. Canonical JSON follows RFC 8785: a JSON column with unsorted keys, whitespace, a
   non-ECMAScript number form (`1e-07`), or a timestamp not in UTC `Z` with six fractional
   digits is rejected; strings are compared without Unicode normalization; fixture IDs
   computed by a second, independent JCS implementation match amber's.
8. Unknown evidence `kind` or unknown event `action` → rejected.
9. `x_` columns survive a read/write cycle; non-prefixed unknown columns are rejected.
10. A claim row with null `value` is rejected.
11. The manifest's recomputed `payload_kinds` must equal the declared ones.
12. Migration: the `amber/0.1` fixture migrated to a synthetic `amber/0.2` fixture by the test
    migrator equals the hand-written target.
13. (Catalog `WIR-13`.) A `CaseResult` written to a bundle and read back equals the original,
    including `run_id`, `grounding_failures`, `tool_calls`, `escalated`, `trace_id`,
    `budget`, and `usage`; a run bundle without `results.parquet` is rejected; a result whose
    `usage` exceeds its `budget` loads only if it records `failed` with
    `failure_kind="budget"`, and otherwise fails with `budget_exceeded`; loading never changes
    an outcome.
14. (Catalog `WIR-14`.) Each §5.3 record's JSON form has exactly its required top-level keys
    and matches its fixture byte-for-byte; parsing and re-serializing gives the same bytes.

### 5.10 Decisions recorded

Reviewed and accepted 2026-10-30:

1. `case_id` is a ULID minted when the case is created. The case records the
   `(dataset, source_id, variable_set)` triple, which is the join key across runs; ID reuse is
   a consumer choice, not a requirement. `cases` therefore joins the table set: **case_id**,
   dataset, dataset_manifest_hash, source_id →, patient_id, variable_set, variable_set_version,
   variable_set_ref, sensitivity, split?, created_at.
2. Annotation events are always exported as `annotation_events.parquet`; no event database is
   part of the contract. loupe revisions in MLflow are the review store; the bridge derives the
   table.
3. Sections are a JSON list on the `sources` row; no separate sections table.

Reviewed and accepted 2026-11-02 (whole-document review):

4. A file's payload kind is derived from its schema: only closed fields (IDs, numbers,
   booleans, timestamps, contract enums, producer references, answer-model strings limited by
   `enum`/`const`/date formats) keep it `labels_only`. `claims.parquet` follows the variable
   set's answer models; `sources`, `variable_outcomes`, `evidence`, `mentions`,
   `mention_readings`, and `annotation_events` are `derived_text`.
5. `CaseResult` gains `budget` and `usage` fields (not provenance), and run-level fields live
   in a new `labels_only` table `results.parquet`, required in run bundles, so a bundle
   reconstructs a `CaseResult` exactly and the budget check runs on bundles.
6. Tables are hashed by content, not bytes: rows sorted by key, each row canonical JSON,
   giving `content_sha256` in the manifest. `file_sha256` is optional per copy. Round trips
   compare content hashes and JSON bytes, never Parquet bytes.
7. Canonical JSON is RFC 8785 with UTC `Z` timestamps at microsecond precision and no
   Unicode normalization. Existing IDs (strings and integers only) are unchanged;
   `ids.canonical_sha256` changes for floats and datetimes. The answer-model `schema_ref`
   keeps its import-path prefix for now.
8. A bundle holds one dataset and one variable set; every `cases` row must match the
   manifest.

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
  `quote`, `mention`, `cite`, `commit_claim`, `no_claim`, `create_case_result`; it never constructs
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
              *, now: datetime, context: RunContext | None = None,
              case_id: str | None = None) -> Decision: ...
    def require(self, data, destination, payload, *, now, context=None,
                case_id=None) -> Decision: ...   # raises PolicyViolation
```
Reference: `amber.policy.StaticGate(matrix)`. First consumer: lapidary runner (lap-ecc.4).

```python
class SourceResolver(Protocol):              # §5.6
    def resolve(self, source_id: str) -> Source | None: ...
```
Reference: `InMemoryResolver`. First consumer: lapidary dataset loader and loupe bridge.

```python
class Provider(Protocol):
    destination: ProviderDestination         # §1.3: zone, capabilities, model
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
    destinations: frozenset[Destination]     # everything it may contact, including local:<name>
    capabilities: frozenset[Literal["mentions", "note_claims", "derivations", "batch"]]
    def run(self, case: Case, source: Source, variables: VariableSet, *,
            gate: PolicyGate, budget: Budget, context: RunContext) -> CaseResult: ...

class RunContext(BaseModel):                 # frozen; supplied by the runner
    run_id: str
    policy_version: str
    data: DataPolicy                         # sensitivity and restrictions of the dataset
    dataset: str; dataset_manifest_hash: str
```
Contract: returns exactly one `CaseResult` per call, always, including on failure (every final
variable `failed` with a `failure_kind`); never raises for expected failures; records
`grounding_failures` and `tool_calls`; stays within `budget` (calls, tokens, retries, wall time)
and records `budget` and `usage` on the result. A result whose usage exceeds its budget must say
so itself as `failed(budget)`; loading never rewrites an outcome (§6.6 decision 8). Everything
in the result passed through amber's tools, which stamp each node's `Provenance` from the
`RunContext` and the destination that produced the node; implementations never assemble
provenance by hand. `destinations` lets a runner check every destination with the gate before
starting; each transfer is still checked when it happens, and contacting an undeclared
destination fails conformance. Reference: `DeterministicExtractor` (rule-like, synthetic). First
consumer: lapidary methods (lap-ecc.7).

```python
class Rule(Protocol):                        # §3.4
    spec: RuleSpec
    def apply(self, inputs: Mapping[str, RoleInput],   # RoleInput, reduction: §3.6
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
              gate: PolicyGate, context: RunContext) -> LabelerOutput: ...

LabelerOutput = tuple[Sequence[AnnotationEvent], Example] | LabelRecord | LabelerFailure
```
A `Labeler` wraps a teacher model, a rule set, or an importer of stored predictions, and
produces tiered references. An Example always comes with the events it was replayed from: an
`import.result` event plus the `mention.*`/`reading.*`/`claim.*`/`evidence.*` events the import
implies, with the Example's `as_of_event` the last of them (§4.3). A `LabelRecord` is not
event-derived and needs none. A model or rule labeler may only declare `tier=silver`; human
tiers come from `ReviewBackend` imports. Reference: `ImportLabeler` over a synthetic JSON
file. First consumer: lapidary gpt-6-sol importer (lap-ecc.13).

```python
class Store(Protocol):
    capabilities: frozenset[Literal["events", "examples", "query", "transactions"]]
    def put_result(self, result: CaseResult) -> None: ...
    def get_result(self, case_id: str, run_id: str, *, resolver: SourceResolver) -> CaseResult: ...
    def runs(self, case_id: str) -> Sequence[str]: ...        # run_ids with a result for this case
    def append_events(self, events: Sequence[AnnotationEvent]) -> None: ...
    def events(self, case_id: str, *, stream: str | None = None) -> Sequence[AnnotationEvent]: ...
    def project(self, case_id: str, *, actor: str | None, as_of_event: str | None = None,
                resolver: SourceResolver) -> Projection: ...
    def put_example(self, example: Example) -> None: ...
    def get_example(self, example_id: str, *, resolver: SourceResolver) -> Example: ...
    def current_example(self, case_id: str, *, resolver: SourceResolver) -> Example | None: ...
```
Contract: every `get_*`/`project` revalidates through amber's load boundaries with the resolver;
results are keyed by `(case_id, run_id)` and `put_result` rejects a duplicate key, so runs of
different methods on the same cases coexist; `append_events` is append-only (no update or
delete); `project` honors blind streams by role (§4.4); `put_example` rejects a split that
differs from the case's `(dataset, source_id)` split, a tier that the events do not support, and
an `example_id` that does not match its `(case_id, variable_set_ref, as_of_event)` or whose
`as_of_event` is not already stored (events are appended first); `current_example` returns the
snapshot at the case's latest event (§4.3).

`Projection` is the replayed state of a case's events up to `as_of_event` (default: the
latest), as seen by `actor` under the blind-review rules (§4.4): `case_id`,
`variable_set_ref`, `as_of_event`, `actor`, claims, mentions, readings, evidence, edges, and per
final variable the outcome and tier so far, either of which may be unset. A projection
validates under the §2–§3 graph rules but not the Example invariants, since work in progress
need not satisfy them. An Example is a projection that does: `Example.from_projection(p)`
returns the Example or names the failing invariant. Stores, review backends, and labelers use
this one replay path. Reference: `InMemoryStore`. First consumer:
amber's Parquet bundle store (a reference too) and lapidary's run artifacts.

```python
class Exporter(Protocol):
    format: str                              # "amber-bundle/0.1", "amber-labels/0.1", "mlflow-dataset", ...
    def export(self, items: Sequence[CaseResult | Example | LabelRecord], target: Destination, *,
               gate: PolicyGate, data: DataPolicy, now: datetime) -> ExportReceipt: ...
```
Contract: an exporter has no fixed payload kind. `export()` derives each output file's kind
from its schema (§5.2) and calls the gate once per file before writing anything. Export is
atomic: if any file is refused, nothing is written and the exporter raises `PolicyViolation`
carrying every decision. `ExportReceipt` lists each file with its payload kind, `Decision`,
and `content_sha256`.

Two amber formats cover the common split between local and shared storage:

- **`amber-bundle/0.1`** is the complete §5 bundle; it usually contains `derived_text` files
  and so goes to local or institutional storage.
- **`amber-labels/0.1`** contains only files that are `labels_only` by construction: `cases`,
  `results`, `evidence_edges`, `examples`, `decisions`, `claims` when every answer model is
  closed (§5.2), and `labels_only_view` output for outcomes (`LabelRecord`s) and events. It has
  the same manifest form and is what goes to a tracking server. It is never a substitute for a
  bundle: it cannot be validated against sources.

Reference: `BundleExporter` and `LabelsExporter`. First consumer: lapidary runner artifact
logging (lap-ecc.10).

```python
class Scorer(Protocol):
    name: str
    needs: frozenset[Literal["examples", "label_records", "mentions", "traces"]]
    def score(self, predicted: Sequence[CaseResult], reference: Sequence[Example | LabelRecord],
              *, variables: VariableSet) -> ScoreReport: ...
```
`ScoreReport`: metrics with numerators and denominators, per-variable breakdown, the
`example_id`s scored against, the reference **tier mix**, a `provisional: bool` that is true
when any reference is below `adjudicated`, and `inconclusive` entries for metrics the reference
tier cannot support. A scorer refuses (`self_reference`) when the predicted results and a
silver reference share a configuration. A **configuration** is the set of `model:` and `rule:`
producer references on a record's claims, readings, and attribute `assigned_by` values, with
rule `+<hash>` removed (§1.6); `human:` and `tool:` references do not count. The scorer refuses
if the predicted results' configuration and any silver reference's configuration intersect.
Scorers never see source text unless
`needs` includes `mentions` (span overlap requires offsets, not text). Reference:
`StructuralValidityScorer`, `LabelCorrectScorer`. First consumer: lapidary `lap eval`
(lap-ecc.12).

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

`Budget` (max_calls, max_tokens, max_retries, max_seconds), `Usage` (actual counts, recorded
on `CaseResult.usage`), `ProviderRequest`/`ProviderResponse`/`ProviderFailure`,
`RuleResult`, `LabelerOutput`/`LabelerFailure`, `Projection`, `ExportReceipt`,
`ReviewBatchReceipt`, `ScoreReport`. All frozen and `extra="forbid"`. All are `labels_only` by
construction except `ProviderRequest` (`source_text`, never persisted by amber) and
`Projection` and `LabelerOutput`, which carry claims, readings, and evidence and are
`derived_text`.

### 6.4 Design checks against the stand-in variables

- **CORAL progression (lapidary run):** `Extractor` = fixed pipeline over a `Provider`
  (vLLM, zone `institution`), gate = `StaticGate` with the CORAL `DataPolicy`; `Store` =
  bundle; one `Exporter` writes the full `amber-bundle/0.1` locally and another logs
  `amber-labels/0.1` to MLflow;
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
   policy refusal, and budget exhaustion, each with the right `failure_kind`; an over-budget
   result records `failed(budget)` itself; contacting a destination not in `destinations`
   fails; every node's provenance carries the `RunContext`'s `run_id` and `policy_version`.
3. `Extractor` declaring `mentions` must emit at least one mention on a fixture containing a
   known span; declaring `derivations` must emit a `Derivation` on the stage fixture.
4. `Provider` without `schema_constrained` rejects a schema request with `ProviderFailure`
   rather than returning unparsed text.
5. `Rule.apply` is pure: two calls with equal inputs give equal results; a rule touching the
   clock or randomness fails the purity fixture.
6. `Labeler` declaring a human tier is rejected at registration; a silver Example from a
   `Labeler` carries the labeler's provenance and comes with events that replay to it,
   beginning with `import.result`; `put_example` before those events are appended is
   rejected.
7. `Store.append_events` rejects updates/deletes; `project` for a stream actor excludes other
   streams; `put_example` rejects split change and unsupported tier; `project` returns a
   `Projection` that may fail the Example invariants (an unsubmitted review) while
   `Example.from_projection` names the failing invariant, and for a finished case
   `from_projection(project(...))` equals the stored Example.
8. `Exporter` must gate every output file (checked with a recording gate); if one file is
   refused, nothing is written and every decision is reported; `ExportReceipt` lists each
   file's payload kind, decision, and `content_sha256`, matching what was written; an
   `amber-labels/0.1` export containing any `derived_text` file is rejected.
9. `Scorer` sets `provisional` iff any reference tier is below adjudicated; evidence metrics
   over `LabelRecord`s are `inconclusive`; `self_reference` fires when the predicted and silver
   configurations share a `model:` or `rule:` reference (rule hash ignored), including one that
   appears only in an attribute's `assigned_by`, and does not fire on shared `human:` or
   `tool:` references.
10. `ReviewBackend` round trip on the file reference: exported cases → simulated reviews →
    imported Examples whose events replay to the same projection.
11. (Catalog `IFC-11`.) Runs: two results for one case with different `run_id`s are both
    stored and retrievable, and `runs(case_id)` lists both; a second result with the same
    `(case_id, run_id)` is rejected; a result whose nodes' `provenance.run_id` differs from its
    `run_id`, or a bundle row whose `run_id` differs from the manifest's, is rejected.
12. (Catalog `IFC-12`.) Every declared capability works on a synthetic fixture; a request that
    needs an undeclared capability is refused, never partly served.
13. (Catalog `IFC-13`.) With `batch` declared, `run_many` returns exactly one result per case,
    in input order, each equal to what `run` gives for that case.

### 6.6 Decisions recorded

Reviewed and accepted 2026-10-30:

1. `Extractor.run(case, ...)` is the contract. `run_many(cases) -> Iterator[CaseResult]` is
   optional behind the `batch` capability; the kit checks input order and exactly one result
   per case.
2. The extractor enforces `Budget` and reports `Usage`; `load_case_result` validates usage
   against the recorded budget and treats an over-budget result as `failed(budget)`.
   *(Superseded in part by decision 8: loading rejects rather than rewrites.)*
3. `Provider` exposes `complete()` only in v0.1. Streaming and embeddings are future
   capabilities, added when a consumer needs them.

Reviewed and accepted 2026-11-02 (whole-document review):

4. `CaseResult` carries `run_id`, which every node's provenance and the bundle manifest must
   match. Stores key results by `(case_id, run_id)`, reject duplicates, and list a case's runs,
   so runs of different methods on the same cases coexist.
5. `Exporter` has no fixed payload kind: it gates every output file by its schema-derived
   kind, and export is atomic (any refusal writes nothing). `amber-labels/0.1` is a separate
   format holding only `labels_only` files, for tracking servers.
6. `Labeler` returns `(events, Example)`, a `LabelRecord`, or a `LabelerFailure`; Examples
   always arrive with the events they replay from, and stores require those events first.
7. `Store.project` returns a `Projection`: event-replayed state that is graph-valid but not
   necessarily Example-valid. `Example.from_projection` is the single path to an Example.
8. Loading never rewrites an outcome. A result whose `usage` exceeds its `budget` loads only
   if it already records `failed(budget)`; otherwise `budget_exceeded`. This supersedes the
   "treats as `failed(budget)`" part of decision 2.
9. The §1.3 endpoint model is `ProviderDestination`; `Provider` is only the protocol, whose
   `destination` is a `ProviderDestination`.
10. `Extractor` declares `destinations` (all it may contact) instead of one `destination`, and
    `run`/`label` take a `RunContext` instead of a `Provenance`; amber's tools stamp node
    provenance from it.
11. A `self_reference` configuration is the set of `model:`/`rule:` producer references on
    claims, readings, and `assigned_by`, rule hash removed; scoring is refused on any overlap
    with a silver reference's configuration.

## 7. Conformance catalog

### 7.1 Purpose and structure

The catalog is the complete list of checks that define conformance to contract v0.1. It is
implemented as the pytest plugin `amber.conformance` (Phase 2) and its IDs are stable: a
consumer reports "amber conformance 0.1: 95/101, failing MEN-09, WIR-04". Changing a case's
meaning requires a new ID; retired IDs are never reused.

Each case has a **level**:

- **core**: exercised against amber itself on synthetic fixtures; runs in amber's CI; a
  consumer cannot fail it.
- **implementation**: exercised against a consumer's implementation of an interface, or
  against records it produced, through the plugin's fixtures (`amber_extractor`,
  `amber_store`, `amber_review_backend`, ...). The consumer selects which interfaces it
  implements; cases for other interfaces are skipped and reported as not applicable, never as
  passed.
- **bundle**: exercised against a written bundle plus a resolver; usable on any run output.

**Selectable targets.** A consumer declares which of these it implements: the ten §6
protocols (`PolicyGate`, `SourceResolver`, `Provider`, `Extractor`, `Rule`, `Labeler`, `Store`,
`Exporter`, `Scorer`, `ReviewBackend`) and the **copal adapter** (§2.4).

**"Applies to"** follows the level, so every case has exactly one level:

- a `core` case shows `—`: it tests amber's own code, and no consumer selects or fails it;
- an `implementation` case names the selectable targets whose fixtures run it, and is reported
  `not_applicable` unless the consumer declared at least one of them;
- a `bundle` case shows `—`, or `SourceResolver` when it needs one.

A check that touches both amber and an implementation is split into a `core` case and an
`implementation` case.

The catalog contains 101 cases. Every case uses invented data; no fixture contains clinical
text. Fixtures include Unicode (non-BMP, combining marks), CRLF, repeated quotes, empty and
whitespace-only sources, and the three stand-in variable sets.

### 7.2 Policy (§1)

| ID | Level | Applies to | Case |
|---|---|---|---|
| `POL-01` | core | — | Every cell of the default matrix: permitted and refused combinations. |
| `POL-02` | core | — | A widened matrix without `widened_from_default: true`, or with the flag but no `note`, fails validation. |
| `POL-03` | core | — | Payload not accepted by destination is refused even when the zone is allowed. |
| `POL-04` | core | — | Each restriction rule refuses independently, with the matching `restriction_id`. |
| `POL-05` | core | — | Restrictions apply in order; the first failure is reported. |
| `POL-06` | core | — | `labels_only_view` output shares no substring of length ≥ 8 with the source text, across Unicode and CRLF sources. |
| `POL-07` | core | — | A `Provenance` without `destination`/`policy_version` is rejected. |
| `POL-08` | implementation | Extractor | A `failed` outcome from a refusal has `failure_kind="policy"` and no claims. |
| `POL-09` | core | — | `check` never raises and never includes source text in a `Decision`. |
| `POL-10` | core | — | A deidentified derivative cannot be created with a sensitivity lower than its declared `DataPolicy` without a new dataset identity. |
| `POL-11` | core | — | An expired restriction refuses with `restriction_expired`; the same call with an earlier `now` is permitted. |
| `POL-12` | core | — | `model_weights` to an `external` destination is refused for `phi` data, and weights trained on two datasets carry `combine` of their policies. |
| `POL-13` | core | — | A serialized `Decision` contains no source text and is accepted as a `labels_only` payload. |
| `POL-14` | core | — | Producer references: each of the four forms parses; an unprefixed name, an unknown prefix, `rule:` without `@<version>`, or an empty segment is rejected in every field that takes one; `rule:a@1+h1` and `rule:a@1+h2` are equal for `self_reference` and unequal otherwise. |
| `POL-15` | implementation | Extractor, Exporter | Each transfer is checked separately: a recording gate sees one decision per destination and payload kind, and a decision permitting a model call is never reused for a tracking or export transfer. |
| `POL-16` | core | — | Training: a `training` destination is refused when a restriction lists `training` in `forbids_kinds` and permitted otherwise; `forbids_training_on_inputs` refuses a destination with `trains_on_inputs=True` and never refuses a `training` destination by itself. |
| `POL-17` | core | — | `combine`: the result has the highest sensitivity and every restriction in input order with duplicate IDs removed; two restrictions with one `id` and different content are an error; combining a policy with itself returns it unchanged. |
| `POL-18` | core | — | Decision context: with a `RunContext` whose `policy_version` differs from `matrix.version`, `check` refuses with `policy_version_mismatch`; a `Decision` carries `dataset`, `run_id`, `case_id`, and `destination_kind` when supplied; `decision_id` is the same for equal inputs and `now`; a `decisions.parquet` row has exactly the `Decision` fields. |

### 7.3 Mentions and structured evidence (§2)

| ID | Level | Applies to | Case |
|---|---|---|---|
| `MEN-01` | core | — | A mention with `quote != Source.text[start:end]` is rejected; likewise any fragment. |
| `MEN-02` | core | — | Direct construction without the grounding context is rejected (same guard as Inclusion). |
| `MEN-03` | core | — | Two producers, same span and type: one `Mention`, two `MentionReading`s with distinct `reading_id`s and provenance; differing values or attributes are both retained. |
| `MEN-04` | core | — | Overlapping and nested mentions of different types coexist; no automatic merging. |
| `MEN-05` | core | — | A discontinuous mention validates each fragment; hull outside the source is rejected. |
| `MEN-06` | core | — | `section_category` not present in `Source.sections` is rejected. |
| `MEN-07` | core | — | An attribute with `evidence_id` not registered in the graph is rejected at registration. |
| `MEN-08` | core | — | A claim that depends on a required attribute without attribute evidence fails with `missing_attribute_evidence`; with the cue cited under role `attribute:<category>` it passes; the cue cited only under a field role, or a different inclusion under the attribute role, still fails; `attribute:` edges do not satisfy field coverage. |
| `MEN-09` | implementation | copal adapter | copal adapter: emoji, combining marks, and CRLF sources round-trip offsets exactly; a source with normalized whitespace fails mapping with a source-free diagnostic. |
| `MEN-10` | implementation | copal adapter | copal adapter: modifier `trigger_id`/`scope_id` become cue/scope inclusions whose quotes match copal's `get_span(...).quote`. |
| `MEN-11` | implementation | Extractor | Model-proposed offsets with a quote mismatch are counted as grounding failures, not dropped silently and not repaired. |
| `MEN-12` | core | — | Structured evidence is deferred: an evidence payload with `kind="structured"` is rejected with `unsupported_evidence`; a structured `Source` (with `record`) whose `source_id` does not include its canonical record is rejected, and two rows differing only in `record` get different `source_id`s; note-source IDs are unchanged. |
| `MEN-13` | core | — | A claim supported only by a mention with no inclusion fails `source_free_support`. |
| `MEN-14` | core | — | A CaseResult whose cited mention is missing from `mentions` fails to load; uncited mentions survive a round trip. |
| `MEN-15` | core | — | A reading whose `mention_id` is not registered is rejected; a `Mention` with no reading in a graph or result is rejected. |
| `MEN-16` | core | — | `mention_id`: a contiguous mention hashes exactly as `(source_id, start, end, mention_type)`; a discontinuous mention with the same hull and type has a different ID; two discontinuous mentions differing only in fragments have different IDs. |
| `MEN-17` | core | — | An attribute whose `scope_evidence_id` does not contain the mention's hull is rejected with `attribute_outside_scope`; a scope that contains the mention but not the cue (post-negation) is accepted; an attribute without a scope is not checked. |
| `MEN-18` | core | — | Two mentions of different types at one position, a cue, and a plain `quote()` of that position all resolve to one inclusion with one `evidence_id`; an inclusion payload carrying `mention_id` is rejected. |
| `MEN-19` | core | — | An attribute value of `null`, a float, a list, or an object is rejected; `True` does not satisfy a requirement with `values={1}`, and `1` does not satisfy `values={True}`; an absent required category fails the requirement. |
| `MEN-20` | core | — | An attribute whose `assigned_by` equals the reading's producer is rejected; a human correction keeps `assigned_by` on carried-over attributes and `None` on the attributes the reviewer changed. |
| `MEN-21` | core | — | A mention-level claim without `reading_id`, with an unregistered or `rejected` reading, or with a reading whose span is in another source or is not cited, is rejected; a note-level claim with `reading_id` is rejected; two claims relying on two different readings of one span each validate against their own reading's attributes. |

### 7.4 Layered variables (§3)

| ID | Level | Applies to | Case |
|---|---|---|---|
| `VAR-01` | core | — | A one-variable set via `Task` produces identical validation results to the current graph on the full existing test suite. |
| `VAR-02` | core | — | A `VariableSet` with a dependency cycle, an unknown input name, a mention-level variable without mention types, or no final variable is rejected. |
| `VAR-03` | core | — | A claim whose `variable` is not in the set, or whose value fails that variable's schema, is rejected with `invalid_schema`. |
| `VAR-04` | core | — | A mention-level claim citing a mention of the wrong type is rejected. |
| `VAR-05` | core | — | Required attribute: missing value → rejected; value outside `values` → rejected; value present without cue evidence → `missing_attribute_evidence` unless lenient. |
| `VAR-06` | core | — | `requires_section`: a citation outside the declared sections is rejected; inside passes. |
| `VAR-07` | core | — | An inference citing a claim from an undeclared input variable fails `undeclared_dependency`. |
| `VAR-08` | core | — | A derivation whose `inputs` are not a subset of the inference's inputs is rejected. |
| `VAR-09` | implementation | Rule | Replay: a registered rule recomputes the derived value; a tampered derived value fails `derivation_mismatch`; an unregistered rule validates structurally and is not replayed. |
| `VAR-10` | core | — | `on_missing`: `refuse` yields `insufficient_evidence` naming the role; `default` records the assumed value in `defaults_used`; `pass_none` invokes the rule with `None` (empty tuple for `many`). |
| `VAR-11` | core | — | Reduction of a `one` role: candidates with different values make the derived variable `conflicting_evidence`, citing the evidence of every conflicting claim, and no derived claim is created; the conflict propagates through an intermediate variable to the final one. Equal values reduce to the lowest-ID candidate and the derivation lists all of them. A `many` role receives every candidate in ID order. Rejected claims are never candidates. |
| `VAR-12` | core | — | One-variable set: a payload whose `outcome` or `final_claim_ids` differs from the variable's `VariableOutcome`, or is `null`, fails to load. Several final variables: a payload with a non-null `outcome` or `final_claim_ids` fails to load. `case_status` follows the fixed severity order. |
| `VAR-13` | core | — | Two final variables, one answered and one `not_mentioned`: `variable_outcomes` carries both, with final IDs only on the answered one; case-level fields are `null`; `case_status` is `not_mentioned`. |
| `VAR-14` | core | — | A mention-level `conflicting_evidence` outcome is rejected. |
| `VAR-15` | core | — | Zero mention claims without declared full review cannot load as `not_mentioned`. |
| `VAR-16` | core | — | `defaults_used`: a defaulted role with non-empty `inputs`, defaults on a model or human derivation, or (for a registered rule) a role whose `on_missing` is not `default` or whose value differs from `RuleSpec.defaults` is rejected; replay supplies the recorded default and the rule never receives `defaults_used`; an unregistered rule's defaults round-trip unchanged. |
| `VAR-17` | core | — | Set identity: `schema_ref` changes when any variable field or answer model changes and not when `version` alone changes; loading a result against a set with the same name and version but a different `schema_ref` fails with `variable_set_mismatch`; a store seeing one `name@version` with two `schema_ref`s fails with `variable_set_version_conflict`. A `patient`-level variable or `Task(scope="patient")` is constructible, and building a graph with it fails with `invalid_context`. |

### 7.5 Annotated data (§4)

| ID | Level | Applies to | Case |
|---|---|---|---|
| `ANN-01` | core | — | An Example with a `failed` outcome, a `rejected` retained claim or reading, a `proposed` final claim under a human tier, or a `verified`/`gold` claim under a silver reference fails to load; a silver reference with `proposed` claims loads; a supporting claim serving an adjudicated and a silver reference must be `gold` or `verified`. |
| `ANN-02` | core | — | `answered` with empty final IDs, or a non-answer with final IDs, fails to load. |
| `ANN-03` | core | — | An Example whose cited mention is missing from `mentions` fails; uncited mentions round-trip. |
| `ANN-04` | core | — | Tier can rise (`silver` → `single_review` → `adjudicated`) only with new events; a payload asserting a higher tier without events fails; a lower tier than the events support fails. |
| `ANN-05` | implementation | Store | Split: a store rejects a second case for the same `(dataset, source_id)` with a different split, a split change for an existing case, and an Example or LabelRecord whose split differs from its case's. |
| `ANN-06` | core | — | Replay determinism: amber's replay of a random event sequence gives identical projections on every run, and replaying a prefix gives the prefix's projection. |
| `ANN-07` | core | — | Blind streams: before resolve, a reviewer's projection includes their own stream and stream-less events and excludes other streams; an adjudicator's includes every stream in the round; after resolve every actor sees every stream. |
| `ANN-08` | core | — | `labels_only_view(events)` contains no `before`, `after`, or `reason`, and no substring of length ≥ 8 from the source text; a reading's captured value appearing in an event's `after` keeps `annotation_events` classified as `derived_text`. |
| `ANN-09` | core | — | A `gold` claim cannot be edited in place; a correction produces a new claim and a linked event. |
| `ANN-10` | core | — | A claim can be `gold` only if the readings it relies on are `gold` or `verified`; a span is never updated or deleted, and a reading correction produces a new linked reading. |
| `ANN-11` | implementation | Scorer | A `LabelRecord` cannot be loaded as an Example; a scorer scoring evidence metrics against LabelRecords reports `inconclusive`. |
| `ANN-12` | implementation | Scorer | Scoring a configuration against silver Examples produced by the same configuration is refused. |
| `ANN-13` | implementation | ReviewBackend | loupe mapping fixtures: accepted → adjudicated Example; unresolved → no Example, events retained; excluded → no Example; no-mentions confirmation → `not_mentioned`; unsubmitted → `insufficient_evidence`. |
| `ANN-14` | implementation | ReviewBackend | Per-change events derived from a revision diff and a single full-state `review.submit` event replay to the same projection. |
| `ANN-15` | core | — | Snapshot identity: replaying the same events gives the same `example_id`; appending an event (a tier rise, a correction) gives a new `example_id`, and replaying the prefix reproduces the old snapshot exactly. |
| `ANN-16` | implementation | Store | A store's `project` returns the same projection as amber's replay of the events it holds, for every actor and every `as_of_event`. |
| `ANN-17` | implementation | Store, Scorer | A store rejects an Example whose `example_id` does not match its `(case_id, variable_set_ref, as_of_event)`; `current_example` returns the snapshot at the latest event; a `ScoreReport` lists the `example_id`s it scored against. |
| `ANN-18` | core | — | A move-on `tier.set` must have `role=system` and a decision reference, or it is rejected; a `single_review` snapshot scored under move-on must include that event (`as_of_event` at or after it). |

### 7.6 Wire formats (§5)

| ID | Level | Applies to | Case |
|---|---|---|---|
| `WIR-01` | bundle | — | Fixture bundles for each stand-in round-trip (write → read → write) with identical `content_sha256` for every table and byte-identical JSON records; Parquet file bytes may differ between writers. Rows are written sorted by key. |
| `WIR-02` | bundle | — | A bundle with a different major `schema_version` is rejected; a lower minor loads. |
| `WIR-03` | bundle | — | Missing required table, missing key column, wrong column type, or duplicate key → rejected with a diagnostic naming the table and column only. |
| `WIR-04` | bundle | SourceResolver | `text_hash` mismatch via the resolver → `source_hash_mismatch`; resolver `None` → `source_unavailable`; no resolver → unvalidated wrapper refused by graph/case/example boundaries. |
| `WIR-05` | bundle | — | Payload kind is derived from schemas: a file declared `labels_only` that has a `quote` column, an open string inside a JSON column, or (for `claims`) an answer model with an unconstrained string field → rejected; a claims table whose answer models are boolean or enum-only is `labels_only`; `sources` with section labels and `variable_outcomes` with a `reason` are `derived_text`. |
| `WIR-06` | bundle | — | A table whose recomputed `content_sha256` differs from the manifest → rejected before validation; so is a `file_sha256`, when present, that differs from the file. A `cases` row whose dataset or variable set differs from the manifest → rejected. |
| `WIR-07` | bundle | — | Canonical JSON follows RFC 8785: a JSON column with unsorted keys, whitespace, a non-ECMAScript number form (`1e-07`), or a timestamp not in UTC `Z` with six fractional digits is rejected; strings are compared without Unicode normalization; fixture IDs computed by a second, independent JCS implementation match amber's. |
| `WIR-08` | bundle | — | Unknown evidence `kind` or unknown event `action` → rejected. |
| `WIR-09` | bundle | — | `x_` columns survive a read/write cycle; non-prefixed unknown columns are rejected. |
| `WIR-10` | bundle | — | A claim row with null `value` is rejected. |
| `WIR-11` | bundle | — | The manifest's recomputed `payload_kinds` must equal the declared ones. |
| `WIR-12` | bundle | — | Migration: the `amber/0.1` fixture migrated to a synthetic `amber/0.2` fixture by the test migrator equals the hand-written target. |
| `WIR-13` | bundle | — | A `CaseResult` written to a bundle and read back equals the original, including `run_id`, `grounding_failures`, `tool_calls`, `escalated`, `trace_id`, `budget`, and `usage`; a run bundle without `results.parquet` is rejected; a result whose `usage` exceeds its `budget` loads only if it records `failed` with `failure_kind="budget"`, and otherwise fails with `budget_exceeded`; loading never changes an outcome. |
| `WIR-14` | core | — | Each §5.3 record's JSON form has exactly its required top-level keys and matches its fixture byte-for-byte; parsing and re-serializing gives the same bytes. |

### 7.7 Interfaces (§6)

| ID | Level | Applies to | Case |
|---|---|---|---|
| `IFC-01` | core | — | Each reference implementation satisfies its protocol under `typing` checks and passes the kit; a stub that constructs an `Inclusion` or `Claim` directly fails. |
| `IFC-02` | implementation | Extractor, Provider | `Extractor` returns a `failed` result (never raises) on provider failure, parse failure, policy refusal, and budget exhaustion, each with the right `failure_kind`; an over-budget result records `failed(budget)` itself; contacting a destination not in `destinations` fails; every node's provenance carries the `RunContext`'s `run_id` and `policy_version`. |
| `IFC-03` | implementation | Extractor | `Extractor` declaring `mentions` must emit at least one mention on a fixture containing a known span; declaring `derivations` must emit a `Derivation` on the stage fixture. |
| `IFC-04` | implementation | Provider | `Provider` without `schema_constrained` rejects a schema request with `ProviderFailure` rather than returning unparsed text. |
| `IFC-05` | implementation | Rule | `Rule.apply` is pure: two calls with equal inputs give equal results; a rule touching the clock or randomness fails the purity fixture. |
| `IFC-06` | implementation | Labeler | `Labeler` declaring a human tier is rejected at registration; a silver Example from a `Labeler` carries the labeler's provenance and comes with events that replay to it, beginning with `import.result`; `put_example` before those events are appended is rejected. |
| `IFC-07` | implementation | Store | `Store.append_events` rejects updates/deletes; `project` for a stream actor excludes other streams; `put_example` rejects split change and unsupported tier; `project` returns a `Projection` that may fail the Example invariants (an unsubmitted review) while `Example.from_projection` names the failing invariant, and for a finished case `from_projection(project(...))` equals the stored Example. |
| `IFC-08` | implementation | Exporter, PolicyGate | `Exporter` must gate every output file (checked with a recording gate); if one file is refused, nothing is written and every decision is reported; `ExportReceipt` lists each file's payload kind, decision, and `content_sha256`, matching what was written; an `amber-labels/0.1` export containing any `derived_text` file is rejected. |
| `IFC-09` | implementation | Scorer | `Scorer` sets `provisional` iff any reference tier is below adjudicated; evidence metrics over `LabelRecord`s are `inconclusive`; `self_reference` fires when the predicted and silver configurations share a `model:` or `rule:` reference (rule hash ignored), including one that appears only in an attribute's `assigned_by`, and does not fire on shared `human:` or `tool:` references. |
| `IFC-10` | implementation | ReviewBackend | `ReviewBackend` round trip on the file reference: exported cases → simulated reviews → imported Examples whose events replay to the same projection. |
| `IFC-11` | implementation | Store | Runs: two results for one case with different `run_id`s are both stored and retrievable, and `runs(case_id)` lists both; a second result with the same `(case_id, run_id)` is rejected; a result whose nodes' `provenance.run_id` differs from its `run_id`, or a bundle row whose `run_id` differs from the manifest's, is rejected. |
| `IFC-12` | implementation | Provider, Extractor, Store | Every declared capability works on a synthetic fixture; a request that needs an undeclared capability is refused, never partly served. |
| `IFC-13` | implementation | Extractor | With `batch` declared, `run_many` returns exactly one result per case, in input order, each equal to what `run` gives for that case. |

### 7.8 Reporting

The plugin emits a `ConformanceReport` (labels_only):

- `subject`: what was tested, either `{kind: "implementation", name, version}` or
  `{kind: "bundle", manifest_content_sha256}`;
- the amber version and the consumer's declared targets (§7.1);
- `catalog_version` (the contract minor) and `catalog_revision`, the SHA-256 of the canonical
  JSON (§5.2) of every case's ID, level, targets, and text;
- `fixtures_ref`, the SHA-256 of the fixture set;
- per case `passed | failed | not_applicable`, with a source-free diagnostic code on failure.

Two reports are comparable only when their `catalog_revision` and `fixtures_ref` match, so
cases can still be added within a minor without two "0.1" reports silently meaning different
things. lapidary logs it as a run artifact; loupe publishes it
with a release. A consumer claims conformance only for interfaces with zero failures.

### 7.9 Decisions recorded

Reviewed and accepted 2026-10-30:

1. Consumers declare the interfaces they implement; cases for undeclared interfaces are
   reported as `not_applicable`. There is no auto-detection.
2. The catalog version equals the contract minor. Cases may be added within a minor;
   consumers report against the catalog version they ran.

Reviewed and accepted 2026-11-02 (whole-document review):

3. Level determines "Applies to": `core` cases show `—`, `implementation` cases name the
   selectable targets that run them, `bundle` cases show `—` or `SourceResolver`. §7.1 lists
   the selectable targets (ten protocols and the copal adapter). Mixed checks are split: ANN-06
   and ANN-15 are core, with their store and scorer halves in ANN-16 and ANN-17. IDs were
   edited in place because no report against them exists yet.
4. Five promised checks gain cases: `WIR-14` (JSON record fixtures), `IFC-12` (every declared
   capability), `IFC-13` (`run_many` order and parity), `POL-15` (each transfer gated
   separately), and `ANN-18` (move-on `tier.set` by `system`, included in scored snapshots).
5. `ConformanceReport` names its `subject` and carries `catalog_revision` and `fixtures_ref`
   content hashes; reports are comparable only when both match.
