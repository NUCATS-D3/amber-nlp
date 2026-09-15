# v1 specification: data model, tools, agents, tables

Status: spec, 2026-09-04. This is the contract the implementation is built against. Code blocks are specification, not implementation — field names, types, and invariants are binding; method bodies are illustrative. Read `00-goals-and-architecture.md` first.

Scope of v1: note-level extraction with verified evidence, explicit outcomes, a provider/destination
policy gate, durable tables, and a minimal correction workflow. Establish a fixed pipeline before
comparing optional extractor/reviewer agents. OMOP `NOTE_NLP` remains the interoperability target;
the first experiment can use the native table export. See the revised M1–M3 delivery in the roadmap.

All domain models use Pydantic v2 with `ConfigDict(extra="forbid")`; identity/provenance-bearing
value objects are frozen. Cross-object validation uses the exact source and evidence store at
construction, commit, and load boundaries. The blocks below abbreviate that validation machinery.
See [Current implementation](README.md#current-implementation) for the implemented subset and
remaining work. Specification blocks do not establish that a model or runtime migration exists.

## 1. Identifiers and provenance

All ids are strings. Node ids are content-addressed where the content is deterministic (sources, mentions, structured rows) and ULIDs where it is not (claims, inference steps, annotation events). Content addressing makes re-runs idempotent and makes evidence edges stable across pipeline versions.

```python
class Provenance(BaseModel):
    """Stamped on every node. Answers: what produced this, under which policy, when."""
    producer: str                 # "tool:sections@medspacy-1.3" | "agent:note_extractor" | "human:<user_id>"
    model: str | None = None      # provider model id, e.g. "vllm:meta-llama/Llama-3.1-8B-Instruct+adapter:abc123"
    prompt_versions: dict[str, str] = {}   # role -> "prompts:/name/3"
    run_id: str | None = None     # MLflow run
    trace_id: str | None = None   # MLflow trace
    zone: Zone                    # provider zone the producer ran in (see §7)
    sensitivity: Sensitivity      # data sensitivity of the inputs it saw
    created_at: datetime
    version: str                  # package version
```

## 2. Sources

```python
class SourceKind(str, Enum):
    note = "note"; lab = "lab"; medication = "medication"; problem = "problem"; imaging = "imaging"; other = "other"

class Section(BaseModel):
    start: int; end: int
    label: str                    # local header text as found
    category: str | None = None   # normalized category (SecTag/LOINC-DO-derived set)
    is_template: bool = False     # marked by dedupe()

class Source(BaseModel):
    source_id: str                # sha256(patient_id, kind, external_id, text)
    patient_id: str
    kind: SourceKind
    external_id: str | None       # note id / accession / order id in the originating system
    datetime: datetime | None     # clinically effective time (note signed, specimen collected)
    text: str | None              # for text kinds; None for structured
    record: dict[str, Any] | None # for structured kinds: the row as loaded
    sections: list[Section] = []
    meta: dict[str, Any] = {}     # note type, author role, encounter id, etc.
```

Invariant: `text` is immutable once a `source_id` is minted; every offset in the system is a char offset into exactly this string (no whitespace normalization after minting).

## 3. Mentions

```python
class Polarity(str, Enum): positive="positive"; negated="negated"; uncertain="uncertain"
class Temporality(str, Enum): current="current"; historical="historical"; hypothetical="hypothetical"; future="future"
class Experiencer(str, Enum): patient="patient"; family="family"; other="other"

class Concept(BaseModel):
    system: str                   # "SNOMED" | "UMLS" | "RxNorm" | "LOINC"
    code: str
    label: str
    score: float | None = None    # linker confidence

class Mention(BaseModel):
    mention_id: str               # sha256(source_id, start, end, mention_type)
    source_id: str
    start: int; end: int          # char offsets, end exclusive
    quote: str                    # == source.text[start:end]  (validated)
    mention_type: str             # "problem" | "medication" | "procedure" | "lab" | "finding" | ...
    polarity: Polarity = Polarity.positive
    temporality: Temporality = Temporality.current
    experiencer: Experiencer = Experiencer.patient
    certainty: float | None = None
    value: str | float | None = None; unit: str | None = None   # for measurements
    concept: Concept | None = None
    section_category: str | None = None
    provenance: Provenance
```

Invariant: `quote == source.text[start:end]` is checked at construction; a Mention cannot exist for text that is not in the source.

## 4. Claims and answer schemas

A task's answer type is a pydantic model. The base class carries the evidence-bearing conventions; task authors subclass it.

```python
class AnswerModel(BaseModel):
    """Subclass per task. Fields are the extracted values. Any field may be annotated
    with Evidence[...] to require evidence for that field specifically."""
    model_config = ConfigDict(extra="forbid")

class Claim(BaseModel):
    claim_id: str                 # ULID
    source_id: str
    patient_id: str
    task: str                     # task name; also the prompt-registry key
    schema_ref: str               # "module:Class@<schema_hash>"
    value: dict[str, Any]         # AnswerModel instance, dumped; validated against schema_ref on load
    effective_datetime: datetime | None   # when the claimed state holds (defaults to source.datetime)
    confidence: float | None
    status: Literal["proposed", "verified", "rejected", "gold"] = "proposed"
    provenance: Provenance
```

`effective_datetime` and `patient_id` preserve inputs needed for later patient-level aggregation;
temporal reconciliation still requires its own design and evaluation.

### Case outcomes and abstention

`Claim.value` remains a validated dictionary. Explicit clinical absence is a task-defined value
with evidence, such as an explicitly negated finding. Unanswered tasks and execution failures are
recorded separately; `no_claim` never constructs a Claim with `value=None`.

```python
class CaseOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal[
        "answered", "not_mentioned", "conflicting_evidence", "insufficient_evidence", "failed"
    ]
    reason: str | None = None
    failure_kind: Literal[
        "policy", "provider", "parse", "grounding", "validation", "budget", "internal"
    ] | None = None
    reviewed_source_ids: list[str] = []
    evidence_ids: list[str] = []
    provenance: Provenance
```

| status | Meaning and requirement |
|---|---|
| `answered` | At least one final, non-rejected claim satisfies the task schema and evidence policy. Explicit negative answers belong here. |
| `not_mentioned` | No relevant mention found after complete review of the declared task scope in every case source. This is a statement about the reviewed record, not clinical absence. |
| `conflicting_evidence` | Applicable source evidence disagrees and the task rules do not resolve it. Cite the conflicting evidence. |
| `insufficient_evidence` | Available evidence or review coverage cannot support a complete answer. Do not turn partial review into a not-mentioned outcome. |
| `failed` | Policy refusal, provider/parse/grounding/validation failure, or exhausted execution budget prevented completion. The runtime records the cause; this is not a clinical answer. |

Non-answered outcomes require a nonempty reason and no final claims. `reviewed_source_ids` lists
only case sources whose entire task-defined scope was reviewed; record partial coverage in the
execution audit. For `not_mentioned`, this list must cover every case source. Outcome evidence IDs
must be known, within scope, and source-backed; conflicting-evidence outcomes require them.
Execution failures cannot become gold outcomes in an Example. The first task uses complete
task-level answers or explicit outcomes; partial field completion is not silently scored as success.
`failure_kind` is required exactly for failed outcomes, providing stable failure-rate categories
without parsing the free-text reason. The runtime sets failures; `no_claim` cannot declare one.

## 5. Evidence

Vocabulary: an **Inclusion** is a verified span of source text (amber's namesake); **StructuredEvidence** is a row/field from a structured source; **InferenceEvidence** is a reasoning step. All three are `Evidence`.

```python
class Inclusion(BaseModel):
    kind: Literal["inclusion"] = "inclusion"
    evidence_id: str              # sha256(source_id, start, end)
    source_id: str; start: int; end: int; quote: str
    mention_id: str | None = None # when the span is a known Mention
    alignment: Literal["exact", "fuzzy"] ; alignment_score: float

class StructuredEvidence(BaseModel):
    kind: Literal["structured"] = "structured"
    evidence_id: str              # sha256(source_id, field, value)
    source_id: str                # a structured Source
    field: str; value: Any
    datetime: datetime | None

class InferenceEvidence(BaseModel):
    kind: Literal["inference"] = "inference"
    evidence_id: str              # ULID
    rationale: str = Field(min_length=1)
    inputs: list[str] = Field(min_length=1)  # known evidence_ids and/or non-rejected claim_ids
    trace_id: str | None; span_id: str | None   # MLflow trace/span of the step
    provenance: Provenance

Evidence = Annotated[Inclusion | StructuredEvidence | InferenceEvidence, Field(discriminator="kind")]

class EvidenceEdge(BaseModel):
    """claim -> evidence. `role` lets a field-level requirement be checked."""
    claim_id: str
    evidence_id: str
    role: str | None = None       # field name in the answer schema this evidence supports, or None for whole-claim
    weight: float | None = None
```

Invariants, checked on commit and when loading a graph:

- Every non-rejected Claim has at least one EvidenceEdge; required answer fields have their own
  evidence roles. A whole-claim edge cannot satisfy a missing field-specific requirement.
- `Inclusion` is minted only by `quote`/grounding or the explicit human annotation path. Bounds and
  quote equality refer to the exact immutable Source text. StructuredEvidence must match the
  referenced Source record's field/value under a declared structured-source mapping.
- All references are known and remain within the case's declared source/patient scope. Referenced
  claims must be non-rejected and individually valid; raw Mention IDs are not evidence IDs.
- Traverse both claim-to-evidence edges and inference inputs when checking acyclicity. Inference
  inputs are nonempty; every support branch must end in an Inclusion or StructuredEvidence verified
  against a Source. Empty rationales or source-free inference leaves cannot establish a fact.
- Retain the complete support closure needed to inspect and revalidate final claims independently
  of an external tracing server. These checks enforce structure and source traceability; semantic
  support still requires task-specific evaluation, including negation, time, and experiencer.

Fuzzy alignment is optional behind a named policy. An alignment score measures matching behavior,
not clinical confidence; ambiguous or unsafe matches return GroundingFailure. Synthetic tests
must attempt structurally valid but semantically wrong citations as scorer cases, alongside kernel
tests for invalid bounds, repeated text, unknown IDs, cycles, and source-free inference chains.

## 6. Tasks and examples

```python
class Task(BaseModel):
    name: str
    answer_model: type[AnswerModel]
    instructions: str             # definitions and guidelines; goes into the prompt
    evidence_policy: Literal["claim", "field"] = "claim"   # require evidence per claim or per field
    scope: Literal["note", "patient"] = "note"

class Example(BaseModel):
    """One annotated case. The single asset that serves demonstrations, prompt
    optimization, fine-tuning, and evaluation."""
    example_id: str
    task: str
    source_ids: list[str]
    claims: list[Claim]           # status == "gold"; includes required supporting claims
    final_claim_ids: list[str]    # gold task answers, not intermediate support claims
    outcome: CaseOutcome
    evidence: list[Evidence]
    edges: list[EvidenceEdge]
    split: Literal["train", "dev", "test"] | None
    annotators: list[str]
    derived_from_events: list[str]   # annotation_event ids (see §10)
```

Exports from `Example`: demonstrations (prompt-ready), TRL prompt/completion JSONL and mlx-lm chat JSONL (identical content), `mlflow.genai` evaluation records (`inputs`/`outputs`/`expectations`).

`answered` Examples have nonempty `final_claim_ids` referencing their gold claims. Adjudicated
not-mentioned/conflict/insufficient-evidence Examples have no final claims, retain any relevant
evidence/supporting gold claims, and satisfy the outcome rules above. Failed runs are diagnostics,
not annotated Examples. Final IDs distinguish answers from intermediate claims for scoring and
export; validate the distinction on load. Exporters preserve outcome labels and never translate
abstention to a negative clinical answer.

Split at the patient/document level before deriving Examples. Demonstrations, optimization, and
training use only their assigned partitions. Corpus adapters must separately declare annotation
coverage and mapping policies; template flags do not define the gold-scoring scope.

## 7. Provider zones and sensitivity

```python
class Zone(str, Enum): local="local"; institution="institution"; external_baa="external_baa"; external="external"
class Sensitivity(str, Enum): synthetic="synthetic"; deidentified="deidentified"; limited="limited"; phi="phi"

class Provider(BaseModel):
    name: str                     # "mlx-local", "vllm-lab-gpu", "databricks-serving", "azure-openai"
    zone: Zone
    kind: Literal["openai_compatible", "mlx", "transformers", "vllm", "anthropic", "gemini"]
    endpoint: str | None; model: str
    capabilities: set[Literal["schema_constrained", "adapters", "logprobs", "batch", "tools"]]

class Policy(BaseModel):
    allowed: dict[Sensitivity, set[Zone]]   # default: phi/limited -> {local, institution}; deidentified adds external_baa; synthetic -> all

def check(policy, provider, dataset_sensitivity) -> None: raises PolicyViolation
```

The gate runs before any model or EDW call and stamps `zone`/`sensitivity` into `Provenance`.
Datasets carry `sensitivity` in their manifest; it is never inferred. Start with one permitted
provider and validate capabilities at its boundary, including structured output and tool use
where required.

Tracking/tracing servers, judges, artifact stores, and annotation services also declare their
destination zone and allowed payloads in deployment configuration. Check each source-bearing
transfer before sending; keep sink policy separate from model-specific Provider fields. Preserve
dataset license/access restrictions for quotes, offsets, and derived outputs even when the zone
matrix permits the destination. A model-provider approval does not authorize unrelated telemetry.

## 8. Tools (agent-callable)

All tools are plain Python functions with Pydantic-typed arguments and returns, shared by the
fixed pipeline, task rules, and optional agents. Register them with the agent runtime only at that
boundary. Each returns provenance-stamped nodes or explicit outcomes. The initial tool subset is
quote, commit, and outcome recording; additional NLP tools are conditional on task evidence.

```python
def sections(source_id) -> list[Section]                      # medspaCy sectionizer (+ MedSlice-style model later)
def dedupe(source_id) -> list[Section]                        # marks template/copy-forward spans (TRACE-style)
def find_mentions(source_id, types: list[str]) -> list[Mention]   # GLiNER-BioMed | OpenMed | medspaCy rules; backend configurable
def context(mention_id) -> Mention                            # ConText/negspacy attributes filled in
def normalize(mention_id, systems: list[str]) -> list[Concept]   # SapBERT candidates
def quote(source_id, text: str, hint_start: int | None = None) -> Inclusion | GroundingFailure   # exact first; optional validated fuzzy policy; human annotation uses the same grounding checks
def search(patient_id, query: str, kinds: list[SourceKind]) -> list[Hit]   # v1: sections/chunks of the current note; v2: patient's sources; Hits are candidates, not evidence
def structured(patient_id, table: str, filters: dict) -> list[StructuredEvidence]   # provider-gated like models
def dates(text: str, anchor: datetime | None) -> list[DateSpan]
def calc(expr: str) -> float | str
def commit_claim(task: str, value: dict, evidence_ids: list[str], rationale: str, field_roles: dict[str, list[str]] | None = None) -> Claim
    # validates value against the task's AnswerModel; requires ≥1 evidence id (per field if evidence_policy == "field");
    # rejects unknown/out-of-scope evidence; records a nonempty InferenceEvidence and edges;
    # validates the full support DAG, source leaves, and field roles before committing
def no_claim(reason: Literal["not_mentioned", "conflicting_evidence", "insufficient_evidence"], rationale: str, reviewed_source_ids: list[str], evidence_ids: list[str]) -> CaseOutcome
    # validates scope, review coverage, and cited evidence; never creates a null-valued Claim
```

## 9. Execution strategies and results

All strategies expose `run(case) -> CaseResult`. Establish a fixed structured-extraction baseline
before comparing bounded agents. PydanticAI is the planned agent runtime; use registered,
version-loaded prompts and centralize tracing in `amber.mlflow_ext`. Capability checks, policy,
grounding, and commit validation apply to every strategy.

```python
class Case(BaseModel):
    case_id: str; task: str; patient_id: str; source_ids: list[str]; sensitivity: Sensitivity

class CaseResult(BaseModel):
    case_id: str
    claims: list[Claim]; evidence: list[Evidence]; edges: list[EvidenceEdge]
    final_claim_ids: list[str]
    outcome: CaseOutcome
    mentions: list[Mention]
    grounding_failures: int; tool_calls: int; escalated: bool
    trace_id: str | None
```

`final_claim_ids` is a subset of the non-rejected claims in the result. It is nonempty exactly when
the outcome is answered; every final answer must satisfy the task contract. Supporting/intermediate
claims may be retained on any outcome but cannot be silently exported or scored as final answers.
The result contains the evidence/claim closure needed to validate those retained claims. The
runtime records failed outcomes and their causes rather than turning execution errors into
not-mentioned results. Claimed complete review is auditable from the execution record; its
semantic completeness is tested on gold answerable cases.

- Fixed baseline: one source/task, versioned prompt, structured candidate values and quotes,
  shared quote/commit validation, bounded retries, and explicit outcomes. No planning loop.
- Extractor experiment: same task/model/split and evidence rules, with bounded adaptive tool use.
  Terminates with final committed claims or a validated `no_claim` outcome; exhausted budgets and
  execution errors become failed outcomes. Use only the tools needed by the comparison.
- Reviewer experiment: plans over sections/chunks of the same immutable note, preserves absolute
  source offsets, and reconciles source-backed claims. Unresolved conflicts produce an outcome
  citing the conflicting evidence. Patient-wide reconciliation remains deferred.
- Cascade experiment: add task rules or targeted NLP when measured quality/cost justifies them.
  Calibrate escalation on development error/coverage curves and audit non-escalated omissions;
  confidence alone is not a reliable routing threshold.

Declare call/token/retry budgets and record actual usage per strategy. Freeze selection before
held-out comparison and report quality, support, omissions, expert time, and cost together.

## 10. Tables (durable output)

| table | key | columns |
|---|---|---|
| `sources` | source_id | patient_id, kind, external_id, datetime, text_hash, meta |
| `mentions` | mention_id | source_id, start, end, quote, mention_type, polarity, temporality, experiencer, certainty, value, unit, concept_system, concept_code, concept_label, section_category, provenance_* |
| `claims` | claim_id | source_id, patient_id, task, schema_ref, value (JSON), effective_datetime, confidence, status, provenance_* |
| `evidence` | evidence_id | kind, source_id, start, end, quote, mention_id, field, value, rationale, inputs (JSON), trace_id, span_id, provenance_* |
| `evidence_edges` | (claim_id, evidence_id, role) | weight |
| `annotation_events` | event_id | case_id, claim_id (nullable for case-outcome edits), actor, action, before (JSON), after (JSON), at |
| `cases` | case_id | task, patient_id, source_ids (JSON), sensitivity, status, final_claim_ids (JSON), outcome (CaseOutcome JSON), escalated, trace_id, run_id |

Storage: start with one local DuckDB store and a Parquet table-bundle export. The initial correction
workflow uses this store and append-only annotation events; SQLite/Postgres are optional later
adapters with migration/replay tests. `provenance_*` is flattened Provenance. Case `status` records
workflow state separately from the clinical/execution `outcome`. Retain immutable source text or
records in permitted storage keyed by source_id; source hashes alone cannot revalidate offsets.
Exports include the required support closure and a manifest linking the exact source versions.

OMOP `NOTE_NLP` view over `mentions`: `note_nlp_id ← mention_id`, `note_id ← source.external_id`, `section_concept_id ← map(section_category)`, `snippet ← quote (± context window)`, `offset ← start`, `lexical_variant ← quote`, `note_nlp_concept_id ← concept (standard)`, `note_nlp_source_concept_id ← concept (source)`, `nlp_system ← provenance.producer`, `nlp_date/datetime ← provenance.created_at`, `term_exists ← polarity != negated`, `term_temporal ← temporality`, `term_modifiers ← "experiencer=…;certainty=…;value=…;unit=…"`.

## 11. Evaluation

Scorers (MLflow `@scorer`, all OSS):

- `label_correct[task, field]`, `claim_all_correct[task]` — exact match against gold claims; dataset-level P/R/F1 with bootstrap CIs logged as run metrics.
- `evidence_faithful[task]` — does the cited evidence support the claim? Begin with expert judgment recorded as annotation events; add an optional LLM judge calibrated against separate human judgments.
- `evidence_localized[task]` — span overlap between cited spans and gold spans (exact / partial / token F1).
- `grounding_failure_rate`, `parse_failure_rate`, `escalation_rate`, `conflict_rate` — process metrics from `CaseResult`.
- Structural/source checks: quote equality, permitted minting path (tool or human annotation),
  known/in-scope IDs, field roles, nonempty inference inputs, source-leaf reachability, and DAG
  validity. Trace-aware checks are supplemental; human gold does not require an agent tool span.

Score final claims only as task answers; inspect their entire support closure for validity and
semantic support. Measure not-mentioned/conflict/insufficient-evidence outcomes against adjudicated
gold separately from execution failures. Count omissions on gold-answerable cases even when the
system abstains or fails. Report every rate's denominator and both full-set quality and quality
among automatically completed cases; automation coverage is their share of all eligible cases.

The first task protocol defines numeric acceptance thresholds before selection/evaluation. Record
setup, prompt work, annotation, correction, and adjudication time, plus model/optimizer/teacher cost
and latency. Compare expert minutes per accepted case with manual authoring at matched quality,
including failed-case review and setup amortization at the intended workload. Human semantic
assessment begins with the baseline; calibrate any LLM judge on separate human judgments.

Clinical reports identify dataset/version/hash, patient/document split, task/schema, adapter and
coverage policies, prompt/model/backend versions, budgets, thresholds, and sample counts. Bootstrap
at the patient/document level; keep CORAL pseudo-labels separate from its 40-note expert gold set.
Do not tune on held-out cases or use their corrected Examples in the evaluated training workflow.

## 12. MLflow mapping

| amber object | MLflow surface |
|---|---|
| Task instructions + answer schema | prompt registry: `<prefix>.<task>`, `response_format` = answer model |
| Agent system prompts | prompt registry: `<prefix>.agent.<role>` |
| Fine-tuned adapter + base ref + tasks | custom pyfunc, registered model (UC three-part name on Databricks via one config key) |
| Case run | trace (autolog); `CaseResult` summary as span attributes |
| Batch of cases | run: params (provider, zone, sensitivity, dataset hash, prompt versions), metrics (§11), artifacts (tables as Parquet) |
| Examples | `mlflow.genai` evaluation dataset (create_dataset) + JSONL exports |
| Prompt optimization | `optimize_prompts` (GEPA) over `<prefix>.<task>` with the scorers in §11 |
| Institutional tags | `mlflow.run_context_provider` plugin: dataset hash, extract id, IRB id, zone, sensitivity |

## 13. Non-goals for v1

Patient-level aggregation and PatientFact; FHIR; multi-language; and PHI de-identification as a
product feature remain outside v1. Multi-backend training, adapter conversion, the expanded app,
and verifier/annotation-assistant agents are conditional later work. A broad NLP tool belt or an
agent loop is not a prerequisite for the first clinical experiment.
