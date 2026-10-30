# v1 specification: data model, tools, agents, tables

> Historical source snapshot, consolidated 2026-10-07. The schema and authority statements below
> are superseded by the [binding v1 contract](../02-v1-schemas-and-tools.md). In particular,
> `no_claim` never creates a null Claim; outcomes and final-claim IDs remain required. See the
> [bundle index](README.md) before using any proposed fields.

Status: spec, 2026-09-04; terminology (OMOP vocabularies) and mention-detector changes 2026-10-07. This is the contract the implementation is built against. Code blocks are specification, not implementation — field names, types, and invariants are binding; method bodies are illustrative. Read `00-goals-and-architecture.md` first.

Scope of v1: note-level; extractor + reviewer agents; OMOP `NOTE_NLP` export; OMOP Standardized Vocabularies as the concept store; provider-zone policy gate; annotation app data model (the app UI itself is a separate milestone but shares these models).

## 1. Identifiers and provenance

All ids are strings. Node ids are content-addressed where the content is deterministic (sources, mentions, structured rows, fragments) and ULIDs where it is not (claims, inference steps, annotation events). Content addressing makes re-runs idempotent and makes evidence edges stable across pipeline versions.

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
    vocabulary_release: str | None = None   # OMOP vocabulary version (VOCABULARY.vocabulary_version for "None"), when terminology was used
    fragment_id: str | None = None          # Fragment.fragment_id (§3a) the producer read labels/lexicon from
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
    record: dict[str, Any] | None # for structured kinds: the row as loaded (OMOP CDM shape by default)
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
    """An OMOP concept. Identity is concept_id; vocabulary_id/concept_code keep the native code."""
    concept_id: int               # OMOP standard concept_id (the link target)
    vocabulary_id: str            # "SNOMED" | "RxNorm" | "RxNorm Extension" | "LOINC" | ...
    concept_code: str             # native code in that vocabulary
    label: str                    # CONCEPT.concept_name
    domain_id: str                # OMOP domain; routes claims to CDM tables, not used as a detector label
    concept_class_id: str         # e.g. "Disorder", "Clinical Finding", "Procedure", "Ingredient"
    standard: bool = True         # standard_concept == 'S'; non-standard only appears as source_concept
    source_concept_id: int | None = None   # the non-standard concept whose name matched, when linking went via "Maps to"
    matched_term: str | None = None        # the lexicon entry that won (concept_name, synonym, or source-concept name)
    score: float | None = None    # linker confidence

    # compatibility views (read-only): system -> vocabulary_id, code -> concept_code

class Mention(BaseModel):
    mention_id: str               # sha256(source_id, start, end, mention_type)
    source_id: str
    start: int; end: int          # char offsets, end exclusive
    quote: str                    # == source.text[start:end]  (validated)
    mention_type: str             # a Fragment label (§3a), e.g. "problem" | "medication" | "procedure" | "lab" | "finding"
    polarity: Polarity = Polarity.positive
    temporality: Temporality = Temporality.current
    experiencer: Experiencer = Experiencer.patient
    certainty: float | None = None
    value: str | float | None = None; unit: str | None = None   # for measurements
    concept: Concept | None = None
    section_category: str | None = None
    detector: str | None = None   # find_mentions backend(s) that produced it, e.g. "gliner2.5-base-v1", "dict", "dict+gliner2.5"
    provenance: Provenance
```

Invariants: `quote == source.text[start:end]` is checked at construction; a Mention cannot exist for text that is not in the source. If `concept` is set, `concept.standard` is true and `concept.concept_id` is in the Fragment named by `provenance.fragment_id`.

## 3a. Terminology fragments

Terminology comes from the OMOP Standardized Vocabularies (CONCEPT, CONCEPT_ANCESTOR, CONCEPT_RELATIONSHIP, CONCEPT_SYNONYM, VOCABULARY), loaded from an Athena download into DuckDB or read from the EDW vocabulary schema. A Fragment is the model-agnostic unit every detector and linker reads from. amber ships the builder, never fragment contents (SNOMED/CPT4 licensing).

```python
class FragmentSpec(BaseModel):
    """Declarative, versioned in the repo. The only thing a site edits."""
    name: str                                 # "cardiology-v1"
    roots: list[int]                          # ancestor concept_ids; descendants via CONCEPT_ANCESTOR (inclusive)
    domains: list[str]                        # e.g. ["Condition", "Procedure", "Drug", "Measurement"]
    exclude_roots: list[int] = []
    labels: dict[str, LabelSpec]              # detector label -> definition (below)
    source_vocabularies: list[str] = ["ICD10CM", "ICD9CM"]   # non-standard vocabularies whose names, via "Maps to", extend the lexicon
    min_term_len: int = 3

class LabelSpec(BaseModel):
    """A detector label: coarse, human-curated, defined over concept_class_id and/or roots."""
    description: str                          # natural-language description passed to detectors that accept one
    concept_class_ids: list[str] = []         # e.g. ["Disorder", "Clinical Finding"]
    roots: list[int] = []                     # optional narrower roots within the fragment
    groups: dict[str, list[int]] = {}         # optional curated mid-level groupings -> root concept_ids (soft re-rank only)

class LexiconEntry(BaseModel):
    term: str
    concept_id: int                           # standard target
    source_concept_id: int | None             # set when the term came from a non-standard concept via "Maps to"
    term_kind: Literal["concept_name", "synonym", "source_name"]

class Fragment(BaseModel):
    fragment_id: str                          # sha256(spec JSON, vocabulary_release)
    spec: FragmentSpec
    vocabulary_release: str
    concept_ids: frozenset[int]               # standard concepts in scope
    label_of: dict[int, str]                  # concept_id -> detector label
    lexicon: list[LexiconEntry]
    # derived, built lazily, never shipped:
    #   sapbert_index (per label), dictionary matcher patterns, label descriptions for detectors

def build_fragment(spec: FragmentSpec, vocab: VocabularyStore) -> Fragment
def descendants(concept_id: int, vocab) -> set[int]          # CONCEPT_ANCESTOR, inclusive
def is_a(child: int, ancestor: int, vocab) -> bool
def upgrade(concept_id: int, old_release: str, new_release: str, vocab) -> int | None   # follow "Maps to" for deprecated concepts; None if retired
```

Rules:
- `domain_id` routes claims to CDM tables; detector labels come from `LabelSpec` (concept_class_id and/or roots), never from domain_id.
- `groups` are curated, not raw SNOMED children; they may be used as a soft re-ranking signal in `normalize`, never as a hard candidate filter. Group membership uses `descendants()` so polyhierarchy is respected.
- SNOMED attribute relationships (e.g. "Has finding site") are read from CONCEPT_RELATIONSHIP only after the builder verifies they are present in the loaded release; otherwise unavailable.
- A vocabulary bump produces a new `fragment_id`. Stored mentions and claims are re-pointed only via `upgrade()`, logged as an annotation_event with actor `tool:vocab_upgrade`.

## 4. Claims and answer schemas

A task's answer type is a pydantic model. The base class carries the evidence-bearing conventions; task authors subclass it.

```python
class AnswerModel(BaseModel):
    """Subclass per task. Fields are the extracted values. Any field may be annotated
    with Evidence[...] to require evidence for that field specifically. A field typed
    ConceptField[root=<concept_id>] must hold a standard concept_id that is_a(root)."""
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

`effective_datetime` and `patient_id` are the two fields that make v2 patient-level aggregation a query rather than a migration. Concept-typed fields use the same concept_id space as `structured` rows, so text and structured evidence reconcile by join.

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
    concept_id: int | None = None # the row's standard concept (e.g. condition_concept_id), when it has one
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
    fragment: str | None = None   # FragmentSpec.name the task's concept fields and mentions draw from

class Example(BaseModel):
    """One annotated case. The single asset that serves demonstrations, prompt
    optimization, fine-tuning, and evaluation."""
    example_id: str
    task: str
    source_ids: list[str]
    claims: list[Claim]           # status == "gold"
    mentions: list[Mention] = []  # gold mentions with offsets and attributes (needed for detector training and the M2 bake-off)
    evidence: list[Evidence]
    edges: list[EvidenceEdge]
    split: Literal["train", "dev", "test"] | None
    annotators: list[str]
    derived_from_events: list[str]   # annotation_event ids (see §9)
```

Exports from `Example`: demonstrations (prompt-ready), TRL prompt/completion JSONL and mlx-lm chat JSONL (identical content), `mlflow.genai` evaluation records (`inputs`/`outputs`/`expectations`), and detector training data with character offsets (one exporter per detector format; offset-less formats are only exported for strings that occur once in the source).

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

The gate runs before any provider call and stamps `zone`/`sensitivity` into `Provenance`. Datasets carry `sensitivity` in their manifest; it is never inferred. Local encoder detectors (GLiNER family, SapBERT) are providers with zone `local` or `institution`.

## 8. Tools (agent-callable)

All tools are plain Python functions with pydantic-typed arguments and returns, registered with the agent runtime; the same functions are used by the deterministic cascade. Each returns provenance-stamped nodes.

```python
def sections(source_id) -> list[Section]                      # medspaCy sectionizer (+ MedSlice-style model later)
def dedupe(source_id) -> list[Section]                        # marks template/copy-forward spans (TRACE-style)
def find_mentions(source_id, types: list[str], fragment: str) -> list[Mention]
    # types are Fragment labels. Backend configurable, no default until the M2 bake-off:
    #   "gliner2.5" (fastino boundary, AutoExtractor, include_spans=True, *_long for long notes),
    #   "gliner-biomed", "openmed", "dict" (medspaCy TargetMatcher over Fragment.lexicon), "llm" (LLM + quote()),
    #   or a union, e.g. "dict+gliner2.5". Union policy: overlapping spans merge (longest wins); dictionary-only
    #   hits keep lower confidence; Mention.detector records the contributors.
    # Backends translate labels/descriptions from the Fragment; no backend holds its own label config.
def context(mention_id) -> Mention                            # ConText/negspacy attributes filled in (default for attributes)
def normalize(mention_id, fragment: str, k: int = 5) -> list[Concept]
    # SapBERT over Fragment.lexicon, candidates restricted to concepts whose label == mention_type;
    # curated groups may boost scores (never filter); below threshold, back off to the lowest common
    # ancestor of the top-k within the fragment (recorded as such). Returns standard concepts with
    # source_concept_id/matched_term set.
def quote(source_id, text: str, hint_start: int | None = None) -> Inclusion | GroundingFailure   # exact, then fuzzy (rapidfuzz partial ratio ≥ threshold); the ONLY minter of Inclusion
def search(patient_id, query: str, kinds: list[SourceKind]) -> list[Hit]   # v1: sections/chunks of the current note; v2: patient's sources; Hits are candidates, not evidence
def structured(patient_id, table: str, filters: dict) -> list[StructuredEvidence]
    # OMOP CDM tables by default (condition_occurrence, drug_exposure, measurement, observation);
    # filters may use descendants(concept_id); provider-gated like models
def dates(text: str, anchor: datetime | None) -> list[DateSpan]
def calc(expr: str) -> float | str
def commit_claim(task: str, value: dict, evidence_ids: list[str], rationale: str, field_roles: dict[str, list[str]] | None = None) -> Claim
    # validates value against the task's AnswerModel; requires ≥1 evidence id (per field if evidence_policy == "field");
    # rejects unknown evidence ids; checks every ConceptField with is_a(value, root) via CONCEPT_ANCESTOR and
    # rejects claims whose concept fields contradict the hierarchy (hierarchy consistency lives here, not in a model);
    # records an InferenceEvidence(rationale, inputs=evidence_ids, trace ids) and the edges; checks acyclicity
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
- Reviewer (v1, note-scoped): plans over sections/chunks, calls the extractor per unit, reconciles conflicts (same task, different values) using section priority, temporality, and concept hierarchy (a claim of a descendant concept refines rather than conflicts with its ancestor), commits the note-level claims with InferenceEvidence whose `inputs` are the per-unit claim ids. v2 swaps units for sources of a patient and adds temporal aggregation.
- Cascade: `run_deterministic(case)` (sections → dedupe → find_mentions → context → normalize → task-specific rules) produces claims with `confidence`; escalate to the reviewer when: no claim, confidence < threshold, conflicting claims, a detector-vs-linker type conflict (mention_type disagrees with the linked concept's label), or note length > limit. `escalated` is logged per case.

## 10. Tables (durable output)

| table | key | columns |
|---|---|---|
| `sources` | source_id | patient_id, kind, external_id, datetime, text_hash, meta |
| `mentions` | mention_id | source_id, start, end, quote, mention_type, polarity, temporality, experiencer, certainty, value, unit, concept_id, vocabulary_id, concept_code, concept_label, domain_id, source_concept_id, matched_term, link_kind (exact / ancestor_backoff), link_score, detector, section_category, provenance_* |
| `claims` | claim_id | source_id, patient_id, task, schema_ref, value (JSON), effective_datetime, confidence, status, provenance_* |
| `evidence` | evidence_id | kind, source_id, start, end, quote, mention_id, field, value, concept_id, rationale, inputs (JSON), trace_id, span_id, provenance_* |
| `evidence_edges` | (claim_id, evidence_id, role) | weight |
| `annotation_events` | event_id | case_id, claim_id, actor, action, before (JSON), after (JSON), at |
| `cases` | case_id | task, patient_id, source_ids (JSON), sensitivity, status, escalated, trace_id, run_id |
| `fragments` | fragment_id | name, spec (JSON), vocabulary_release, n_concepts, n_lexicon, built_at |

Storage: Parquet/DuckDB for pipeline output; the app uses SQLite (single annotator) or Postgres (shared) with the same columns. `provenance_*` is the flattened Provenance (including vocabulary_release and fragment_id). Fragment contents (lexicon, indexes) are site-local build artifacts, not output tables.

OMOP `NOTE_NLP` view over `mentions`: `note_nlp_id ← mention_id`, `note_id ← source.external_id`, `section_concept_id ← map(section_category)`, `snippet ← quote (± context window)`, `offset ← start`, `lexical_variant ← quote`, `note_nlp_concept_id ← concept_id (standard)`, `note_nlp_source_concept_id ← source_concept_id (0 when the match was on a standard concept's own name or synonym)`, `nlp_system ← provenance.producer + detector`, `nlp_date/datetime ← provenance.created_at`, `term_exists ← polarity != negated`, `term_temporal ← temporality`, `term_modifiers ← "experiencer=…;certainty=…;value=…;unit=…;vocab=<vocabulary_release>"`.

## 11. Evaluation

Scorers (MLflow `@scorer`, all OSS):
- `label_correct[task, field]`, `claim_all_correct[task]` — exact match against gold claims; dataset-level P/R/F1 with bootstrap CIs logged as run metrics. Concept fields also report hierarchical credit (exact / ancestor-or-descendant within N levels via CONCEPT_ANCESTOR).
- `evidence_faithful[task]` — does each cited evidence support the claim? LLM-judge scorer with a human-calibration set; human judgments recorded as annotation events.
- `evidence_localized[task]` — span overlap between cited spans and gold spans (exact / partial / token F1).
- `mention_detected[label]` — mention P/R/F1 against gold mentions (exact and overlap), per detector backend; `mention_attr_correct[attr]` — polarity/temporality/experiencer accuracy, ConText vs. any model attribute head.
- `concept_linked` — accuracy@1 and @k of `normalize` on gold-detected spans (isolates linking from detection), plus end-to-end detected-and-linked F1; ancestor back-off rate.
- `heldout_concept_recall` — recall on concepts withheld from any silver training data (generalization check for adapted detectors).
- `grounding_failure_rate`, `parse_failure_rate`, `escalation_rate`, `conflict_rate` — process metrics from `CaseResult`.
- Trace-aware: `spans_minted_via_quote` (every Inclusion has a `quote` tool span in the trace), `commit_cited_known_ids`.

## 12. MLflow mapping

| amber object | MLflow surface |
|---|---|
| Task instructions + answer schema | prompt registry: `<prefix>.<task>`, `response_format` = answer model |
| Agent system prompts | prompt registry: `<prefix>.agent.<role>` |
| Fine-tuned adapter + base ref + tasks | custom pyfunc, registered model (UC three-part name on Databricks via one config key) |
| Detector adapter (e.g. GLiNER2.5 LoRA) | logged artifact + registered model; params include fragment_id and vocabulary_release |
| Fragment | run params/tags: fragment_id, spec name, vocabulary_release; spec JSON as artifact (contents are not logged) |
| Case run | trace (autolog); `CaseResult` summary as span attributes |
| Batch of cases | run: params (provider, zone, sensitivity, dataset hash, prompt versions, fragment_id, detector), metrics (§11), artifacts (tables as Parquet) |
| Examples | `mlflow.genai` evaluation dataset (create_dataset) + JSONL exports |
| Prompt optimization | `optimize_prompts` (GEPA) over `<prefix>.<task>` with the scorers in §11 |
| Institutional tags | `mlflow.run_context_provider` plugin: dataset hash, extract id, IRB id, zone, sensitivity, vocabulary_release |

## 13. Non-goals for v1

Patient-level aggregation and PatientFact; FHIR export; the verifier and annotation-assistant agents (data model supports them; they follow the app); multi-language; PHI de-identification as a product feature (a `deidentify` tool may exist for workflow use); SNOMED-native features outside OMOP (ECL, refsets, post-coordination); relation extraction between mentions (e.g. finding site), deferred until linking error analysis shows pre-coordinated linking is insufficient.
