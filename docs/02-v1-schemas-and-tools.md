# v1 specification: data model, tools, agents, tables

Status: spec, 2026-09-04. This is the contract the implementation is built against. Code blocks are specification, not implementation — field names, types, and invariants are binding; method bodies are illustrative. Read `00-goals-and-architecture.md` first.

Scope of v1: note-level; extractor + reviewer agents; OMOP `NOTE_NLP` export; provider-zone policy gate; annotation app data model (the app UI itself is a separate milestone but shares these models).

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

`effective_datetime` and `patient_id` are the two fields that make v2 patient-level aggregation a query rather than a migration.

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
    rationale: str
    inputs: list[str]             # evidence_ids and/or claim_ids consumed by this step
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

Invariants: a Claim in status other than `rejected` has ≥1 EvidenceEdge. `Inclusion` is only minted by the `quote` tool or by a human in the app. `InferenceEvidence.inputs` may reference claim_ids, which is how chains form; the edge graph must be acyclic (checked on commit).

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
    claims: list[Claim]           # status == "gold"
    evidence: list[Evidence]
    edges: list[EvidenceEdge]
    split: Literal["train", "dev", "test"] | None
    annotators: list[str]
    derived_from_events: list[str]   # annotation_event ids (see §9)
```

Exports from `Example`: demonstrations (prompt-ready), TRL prompt/completion JSONL and mlx-lm chat JSONL (identical content), `mlflow.genai` evaluation records (`inputs`/`outputs`/`expectations`).

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

The gate runs before any provider call and stamps `zone`/`sensitivity` into `Provenance`. Datasets carry `sensitivity` in their manifest; it is never inferred.

## 8. Tools (agent-callable)

All tools are plain Python functions with pydantic-typed arguments and returns, registered with the agent runtime; the same functions are used by the deterministic cascade. Each returns provenance-stamped nodes.

```python
def sections(source_id) -> list[Section]                      # medspaCy sectionizer (+ MedSlice-style model later)
def dedupe(source_id) -> list[Section]                        # marks template/copy-forward spans (TRACE-style)
def find_mentions(source_id, types: list[str]) -> list[Mention]   # GLiNER-BioMed | OpenMed | medspaCy rules; backend configurable
def context(mention_id) -> Mention                            # ConText/negspacy attributes filled in
def normalize(mention_id, systems: list[str]) -> list[Concept]   # SapBERT candidates
def quote(source_id, text: str, hint_start: int | None = None) -> Inclusion | GroundingFailure   # exact, then fuzzy (rapidfuzz partial ratio ≥ threshold); the ONLY minter of Inclusion
def search(patient_id, query: str, kinds: list[SourceKind]) -> list[Hit]   # v1: sections/chunks of the current note; v2: patient's sources; Hits are candidates, not evidence
def structured(patient_id, table: str, filters: dict) -> list[StructuredEvidence]   # provider-gated like models
def dates(text: str, anchor: datetime | None) -> list[DateSpan]
def calc(expr: str) -> float | str
def commit_claim(task: str, value: dict, evidence_ids: list[str], rationale: str, field_roles: dict[str, list[str]] | None = None) -> Claim
    # validates value against the task's AnswerModel; requires ≥1 evidence id (per field if evidence_policy == "field");
    # rejects unknown evidence ids; records an InferenceEvidence(rationale, inputs=evidence_ids, trace ids) and the edges; checks acyclicity
```

## 9. Agents

Runtime: PydanticAI (`mlflow.pydantic_ai.autolog()` for tracing). Each agent is a function `run(case) -> CaseResult` with a system prompt registered in the MLflow prompt registry and loaded by version.

```python
class Case(BaseModel):
    case_id: str; task: str; patient_id: str; source_ids: list[str]; sensitivity: Sensitivity

class CaseResult(BaseModel):
    case_id: str
    claims: list[Claim]; evidence: list[Evidence]; edges: list[EvidenceEdge]
    mentions: list[Mention]
    grounding_failures: int; tool_calls: int; escalated: bool
    trace_id: str | None
```

- Note extractor: one source, one task. Tools: sections, dedupe, find_mentions, context, normalize, quote, dates, calc, commit_claim. Terminates on commit or on an explicit `no_claim(reason)` (which is itself recorded as a Claim with `value=None` and an InferenceEvidence, so "nothing found" is auditable).
- Reviewer (v1, note-scoped): plans over sections/chunks, calls the extractor per unit, reconciles conflicts (same task, different values) using section priority and temporality, commits the note-level claims with InferenceEvidence whose `inputs` are the per-unit claim ids. v2 swaps units for sources of a patient and adds temporal aggregation.
- Cascade: `run_deterministic(case)` (sections → dedupe → find_mentions → context → task-specific rules) produces claims with `confidence`; escalate to the reviewer when: no claim, confidence < threshold, conflicting claims, or note length > limit. `escalated` is logged per case.

## 10. Tables (durable output)

| table | key | columns |
|---|---|---|
| `sources` | source_id | patient_id, kind, external_id, datetime, text_hash, meta |
| `mentions` | mention_id | source_id, start, end, quote, mention_type, polarity, temporality, experiencer, certainty, value, unit, concept_system, concept_code, concept_label, section_category, provenance_* |
| `claims` | claim_id | source_id, patient_id, task, schema_ref, value (JSON), effective_datetime, confidence, status, provenance_* |
| `evidence` | evidence_id | kind, source_id, start, end, quote, mention_id, field, value, rationale, inputs (JSON), trace_id, span_id, provenance_* |
| `evidence_edges` | (claim_id, evidence_id, role) | weight |
| `annotation_events` | event_id | case_id, claim_id, actor, action, before (JSON), after (JSON), at |
| `cases` | case_id | task, patient_id, source_ids (JSON), sensitivity, status, escalated, trace_id, run_id |

Storage: Parquet/DuckDB for pipeline output; the app uses SQLite (single annotator) or Postgres (shared) with the same columns. `provenance_*` is the flattened Provenance.

OMOP `NOTE_NLP` view over `mentions`: `note_nlp_id ← mention_id`, `note_id ← source.external_id`, `section_concept_id ← map(section_category)`, `snippet ← quote (± context window)`, `offset ← start`, `lexical_variant ← quote`, `note_nlp_concept_id ← concept (standard)`, `note_nlp_source_concept_id ← concept (source)`, `nlp_system ← provenance.producer`, `nlp_date/datetime ← provenance.created_at`, `term_exists ← polarity != negated`, `term_temporal ← temporality`, `term_modifiers ← "experiencer=…;certainty=…;value=…;unit=…"`.

## 11. Evaluation

Scorers (MLflow `@scorer`, all OSS):
- `label_correct[task, field]`, `claim_all_correct[task]` — exact match against gold claims; dataset-level P/R/F1 with bootstrap CIs logged as run metrics.
- `evidence_faithful[task]` — does each cited evidence support the claim? LLM-judge scorer with a human-calibration set; human judgments recorded as annotation events.
- `evidence_localized[task]` — span overlap between cited spans and gold spans (exact / partial / token F1).
- `grounding_failure_rate`, `parse_failure_rate`, `escalation_rate`, `conflict_rate` — process metrics from `CaseResult`.
- Trace-aware: `spans_minted_via_quote` (every Inclusion has a `quote` tool span in the trace), `commit_cited_known_ids`.

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

Patient-level aggregation and PatientFact; FHIR export; the verifier and annotation-assistant agents (data model supports them; they follow the app); multi-language; PHI de-identification as a product feature (a `deidentify` tool may exist for workflow use).
