# Contract v0.1 design

Status: draft in progress, 2026-10-30. This document is written in dependency order; each
section is reviewed as drafted. Sections marked *pending* are not yet written. The
[plan](../plan.md) describes the phases; the [charter](../charter.md) sets scope.

Examples use invented data only. Stand-in variables for the design checks are: CORAL current
progression (single boolean), a lapidary `ds_findings_v1` mention-level finding, and pathologic
stage group derived from T/N/M mentions (layered).

Sections: [1 Policy](#1-policy) · 2 Mentions and structured evidence (pending) · 3 Layered
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

*Pending.*

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
