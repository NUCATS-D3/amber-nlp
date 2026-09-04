# amber: goals, principles, and ecosystem map

Name: **amber** — messy text hardened into structured, durable data with the evidence still visible inside. A verified span of source text preserved in a claim is an **inclusion** (the gemological term for what is trapped in amber). Structured-source evidence and inference steps keep their descriptive names.

Status: goals-first design note, 2026-09-04. Supersedes the Strata-anchored framing in `03-strata-scaffold-notes.md` (keep that doc for the v0.1 scaffold's mechanics; this one governs). Implementation happens in a separate workspace.

## Goals as stated

1. Clinical IE that relies on a minimal set of annotated examples; zero-shot in the limit.
2. Grounding is required: every note-level decision (ultimately patient-level) keeps evidence from the note or other source — mention spans and/or inference steps.
3. MLflow integration that works on open-source MLflow as well as Databricks; the tool is meant to be shared beyond one institution.
4. Pydantic schemas; local-model fine-tuning as an option; no constraint to particular models, from Apple-silicon training on a laptop to expensive GPU resources.
5. Output is structured tables with links to evidence.

## Principles derived from the goals

- Evidence is a first-class object, not a column. A decision without an evidence edge is invalid output, whether it came from a fine-tuned 8B model or a frontier API.
- Annotated examples are one asset serving every rung of the annotation ladder: demonstrations, prompt-optimizer training data, fine-tuning data, and the eval set. Design the example format once, with spans.
- Portability is the base model id + the tokenizer's chat template + an adapter; everything else (backend, hardware, quantization) is execution detail hidden behind a capability-flagged backend interface.
- MLflow usage is restricted to the OSS API surface; Databricks is a tracking URI and a registry naming convention, never a dependency.
- The primary evaluation questions are, in order: is the label right, does the cited evidence support it, and was the right evidence found.

## The data model (the heart of it)

Four node types and one edge type; every node has a stable id and a provenance stamp (model, prompt version, run id, timestamp).

- Source: a note (or later any record: lab, med order, imaging report). Fields: source_id, patient_id, type, datetime, text, section map.
- Mention: a grounded span in a source: source_id, start, end, quote, mention_type, normalized concept (optional, e.g. SNOMED/UMLS via SapBERT), attributes (negation, temporality, experiencer, certainty, value + unit). This maps almost one-to-one onto OMOP `NOTE_NLP` (snippet, offset, lexical_variant, note_nlp_concept_id, term_exists, term_temporal, term_modifiers, nlp_system), which becomes the standard export for mention-level output.
- Claim: a note-level structured decision typed by a pydantic model (the task's answer schema). Fields: claim_id, source_id, task, value (typed), confidence.
- PatientFact: an aggregation of claims across sources into a patient-level value, with the aggregation rule named (latest, any, majority, temporal window).
- Evidence edge: from a Claim or PatientFact to its support. Two kinds: Inclusion (mention_id, or a raw char interval verified to exist in the text) and InferenceEvidence (a step: rationale text, the inputs it consumed — mention ids, other claim ids — the model + prompt version, and a trace id). Chains of inference are just edges to prior claims, so provenance is a DAG.

Output tables: `claims`, `patient_facts`, `mentions`, `evidence` (edge table), plus an OMOP `NOTE_NLP` view generated from `mentions`. Evidence stays denormalized enough (quote, offsets, rationale text) that the tables are useful without MLflow being reachable; trace ids are links, not the record.

Grounding rule for generated spans: any span an LLM emits is aligned back to the source text (exact, then fuzzy within a tolerance); unalignable spans are dropped from evidence and counted as a grounding failure. This is LangExtract's behavior (`char_interval = None`) and is non-negotiable in zero-shot mode, where span hallucination is the dominant failure.

## Evidence kinds (revised)

Three, not two: Inclusion (a verified char interval in a text source), StructuredEvidence (a row/field in a structured source: lab result, med order, problem-list entry — the "other source" in goal 2), and InferenceEvidence (a reasoning step: rationale, the evidence and claims it consumed, model + prompt version, trace/span id). PatientFacts typically carry all three.

## Agentic architecture

The unit of work is a case: (patient or note) × (a question typed by a pydantic schema). An agent works the case with tools and can only finish by committing claims whose evidence edges point at objects produced by tools. That last constraint is the design: grounding by construction rather than by post-hoc checking. The agent never types a span; it calls `quote(source_id, text)` and gets back a verified interval or a failure.

Tool belt (non-LLM components are tools, which is where spaCy and friends live):
- `sections(source_id)` — medspaCy sectionizer / MedSlice-style; returns section spans and labels.
- `dedupe(source_id)` — TRACE-style template and copy-forward detection; marks boilerplate spans so evidence isn't drawn from them.
- `find_mentions(source_id, types)` — GLiNER-BioMed / OpenMed / scispaCy / medspaCy target rules; returns Mention objects with offsets.
- `context(mention_id)` — medspaCy ConText / negspacy: negation, temporality, experiencer, certainty.
- `normalize(mention_id)` — SapBERT → UMLS/SNOMED/RxNorm; returns candidates with scores.
- `quote(source_id, text)` — exact then fuzzy alignment; the only way to mint a Inclusion.
- `search(patient_id, query, kinds)` — BM25/embedding search across the patient's notes; returns source ids + snippets (snippets are candidates, not evidence, until quoted).
- `structured(patient_id, table, filters)` — EDW/OMOP lookups (labs, meds, problems); returns rows that become StructuredEvidence.
- `dates(text)` / `calc(expr)` — date normalization and arithmetic (durations, "3 months prior to").
- `commit_claim(schema, value, evidence_ids, rationale)` — validates against the pydantic model, requires ≥1 evidence id, records InferenceEvidence from the current trace.

Agent roles (start with the first two):
1. Note extractor: bounded, single source, fixed schema. Cheap enough to run at scale with a small local model.
2. Chart reviewer: patient-level. Plans: enumerate sources → search → delegate note extraction → reconcile conflicting claims → aggregate with temporal rules → commit PatientFacts with edges to the note-level claims.
3. Verifier: a separate pass (different model, or same model different prompt) that reads each claim's evidence and answers "does this support it?" — the online form of the evidence-faithfulness scorer. Disagreements go to a review queue.
4. Annotation assistant: proposes claims + evidence for the annotator to accept/correct; accepted records are new examples (active learning).

Cascade for cost: a deterministic pipeline (sections → mentions → context → rules) handles the bulk; escalate to the agent when the schema is unfilled, confidence is low, claims conflict, or the note is long. Log the escalation rate; it is the cost dial.

Runtime choice: PydanticAI fits every constraint — pydantic-typed tools and outputs with validation and retry, model-agnostic (hosted APIs and any OpenAI-compatible local endpoint: vLLM, mlx-lm server, Ollama GGUF), and MLflow ships `mlflow.pydantic_ai.autolog()` so every tool call is a trace span. DSPy is the alternative if the optimizer loop is the centerpiece; it can also be mixed in for the extractor step. Avoid heavier orchestration frameworks until a real need appears.

Fine-tuning in an agentic system: three things can be tuned — the extractor step alone (Strata recipe; cheapest and highest-yield), the agent's prompts via GEPA (no weights touched), or the small model on successful trajectories distilled from a frontier model (agentic distillation; makes an 8B model a competent tool user for this narrow domain). Tool-calling reliability on small local models is the constraint; constrain tool-call JSON with a schema-aware backend.

MLflow mapping for agents: traces are the natural record of InferenceEvidence (span ids point into them); `mlflow.genai.evaluate(predict_fn=agent)` runs the scorers over cases; trace-aware scorers check tool-call correctness (was `quote` used for every span? did `commit_claim` cite verified ids?); `optimize_prompts` treats agent prompts as the optimization target.

## The annotation ladder

Same examples, increasing budget:

1. Zero-shot: schema + instructions + definitions; grounding by alignment; use a reasoning model with self-consistency if quality demands (SDOH result: o4-mini matched fine-tuned BERT with guidelines-in-prompt + 50-shot + voting).
2. Few-shot by example: 5–20 grounded examples as demonstrations (LangExtract's mode).
3. Prompt optimization: 30–100 examples + scorers → `mlflow.genai.optimize_prompts` (GEPA; OSS MLflow ≥ 3.5) rewrites the registered prompt and registers the new version. Cheap, model-agnostic, and it keeps the model frozen.
4. Distillation: teacher (frontier or 70B) labels a large unlabeled pool with span-grounded QA; student (1–8B) is LoRA-tuned; 8B student beat its 70B teacher in the npj Digit Med result.
5. Direct fine-tune: ~100 examples per task, LoRA r=128 (the Strata recipe), human-level on report structuring.

The active-learning loop that feeds the ladder: model proposes claims + evidence → annotator accepts/corrects in a span-aware UI → the accepted record is a new example. Correcting is faster than authoring, and the correction is automatically grounded.

## Model and hardware spectrum

Three execution tiers behind one backend protocol with capability flags (`schema_constrained`, `adapters`, `logprobs`, `batch`):

- Apple silicon: `mlx-lm` for LoRA/DoRA/QLoRA training (chat and completions JSONL formats) and generation; Outlines' `mlx-lm` model class for JSON-schema-constrained decoding. Do not route structured output through Ollama's MLX engine: it silently ignores `format` (ollama#17013 / #16563); Ollama's GGUF path enforces schemas.
- GPU: transformers + PEFT (unsloth optional) for training; vLLM with LoRA adapters and xgrammar/outlines guided decoding for inference.
- Hosted APIs: structured outputs from Gemini / OpenAI / Anthropic, or Databricks model serving inside the institution's HIPAA boundary. PHI policy decides which of these are allowed per deployment; the tool treats it as a backend property.

Interchange: canonical adapter format is HF PEFT (`adapter_config.json` + safetensors). MLX saves its own `adapters.safetensors` with different key names; no official converter exists, but the mapping is a mechanical key rename plus scaling, so ship a converter both ways and test round-trips. Training data format is TRL-style prompt/completion messages, which `mlx-lm` reads natively as "chat".

## MLflow: OSS surface only

Use: tracking (runs, params, metrics, artifacts), prompt registry with `response_format` = pydantic schema, tracing, `mlflow.genai.evaluate` + `@scorer`, `optimize_prompts`, model registry via a custom pyfunc, `mlflow.run_context_provider` plugin for institutional tags (dataset hash, extract id, IRB). All available on OSS MLflow 3.x.

Avoid as dependencies: Databricks-only production monitoring / scheduled scorers, `databricks-agents` Agent Evaluation, managed judges. Registry naming differs (Unity Catalog three-part names on Databricks); abstract it behind one config key.

Evidence and traces: InferenceEvidence carries the rationale text and a trace id. Traces are the debugging and audit surface; the evidence table is the durable product.

## Evaluation that respects grounding

Three layers, each a scorer:

1. Label correctness: per-field exact / P / R / F1 with bootstrap CIs; patient-level accuracy after aggregation.
2. Evidence faithfulness: does the cited span or rationale actually support the claim? Human-judged on a sample; LLM-judge scorer for scale, calibrated against the human sample.
3. Evidence localization: span overlap with gold spans (exact, partial, token-level), and grounding-failure rate (unalignable spans).

Also track: parse-failure rate, and for aggregation, conflict rate across notes (claims disagree) since that is where patient-level errors concentrate.

## Decisions (2026-09-04)

1. Unit of analysis: note-level for v1. Patient-level stays in the design at the cost of three fields, not a subsystem: every Source carries `patient_id`; every Claim carries an `aggregation`-ready shape (typed value + effective datetime + evidence edges); PatientFact and the temporal aggregation rules are deferred. The reviewer's "enumerate sources" step iterates sections/chunks of one note in v1 and notes of one patient in v2 — same code path, different Source set.
2. Agents in v1: both the note extractor and the reviewer. In v1 the reviewer is scoped to one note: it plans over sections, delegates extraction per section/chunk, reconciles conflicting mentions and claims within the note (e.g. HPI vs. A&P), and commits the note-level claims. The verifier and annotation assistant follow once the app exists.
3. Export target: OMOP. `mentions` → `NOTE_NLP` view now; claims map to OMOP domain tables (condition_occurrence, drug_exposure, measurement, observation) with `note_nlp_id` back-links when patient-level lands. FHIR is a later exporter over the same tables.
4. PHI boundary: undecided (Databricks or Azure both possible; EDW access pending). Abstract it as a policy gate — see "Provider zones and data sensitivity" below.
5. Annotation: a custom app designed around evidence DAGs — see "Annotation app" below.

## Provider zones and data sensitivity

Two labels and one gate, so the PHI decision can be made later per deployment without touching pipeline code.

- Every model provider (backend) declares a `zone`: `local` (this machine), `institution` (Databricks model serving, an on-prem vLLM/Ollama, Azure OpenAI under the institution's BAA), `external_baa` (a vendor API covered by a BAA), `external` (no BAA). Zone is a property of the configured endpoint, not the model family: the same Llama can be `local` or `institution`.
- Every dataset declares a `sensitivity`: `synthetic`, `deidentified`, `limited` (LDS), `phi`.
- A policy table (per deployment, in config, logged as run tags) says which zones may see which sensitivity. Default policy: `phi`/`limited` → `local` or `institution` only; `deidentified` → adds `external_baa`; `synthetic` → anything. The runtime refuses a run whose provider zone is not allowed for the dataset's sensitivity, and records zone + sensitivity on every MLflow run and every claim's provenance stamp.
- A `deidentify` tool (Philter / OpenMed PHI models) can transform a source to a lower sensitivity class with its own provenance, if a workflow needs an external model on real notes.
- Same abstraction covers the EDW itself: the `structured` tool is a provider with a zone; Databricks vs. Azure vs. local Parquet extracts are endpoint configuration.

## Annotation app

Purpose: the human is a tool in the loop. The app is where the annotation assistant's proposals are corrected into examples, where the verifier's disagreements are adjudicated, and where gold spans and gold claims for evaluation are minted — one app, one data model, three queues.

Core screens:
- Source view: note text with section shading, template/copy-forward spans dimmed, mentions highlighted by type, and the selected claim's evidence spans emphasized. Span editing (drag boundaries, split, merge), add-a-span-as-evidence, and a "quote" action that mints a verified span exactly the way the agent's tool does.
- Claim panel: the pydantic schema rendered as a form (fields, enums, units), the current value, confidence, and the evidence list. Accept / correct / reject per claim; corrections require evidence.
- Evidence DAG: a compact layered view per claim — spans and structured rows at the leaves, inference steps as nodes with their rationale, prior claims as inputs. Clicking a node highlights its span in the source or opens the structured row. Not a general graph editor; a viewer with "add evidence" and "remove edge".
- Queue: cases ordered by uncertainty, disagreement (model vs. model, annotator vs. annotator, verifier vs. extractor), and schema coverage; the active-learning policy is pluggable and logged.

Data model: the same four tables plus an append-only `annotation_events` log (who, when, case, action, before/after JSON). Gold is derived by replay, which gives inter-annotator agreement, per-annotator provenance, and the ability to re-derive gold when a schema changes. Examples for the ladder (demonstrations, optimizer data, fine-tuning data, eval sets) are exports of accepted cases in the canonical example format with spans.

Stack: FastAPI + a relational store (SQLite for a single annotator, Postgres shared) + a React/TypeScript front end; a keyboard-first UI. The app and the pipeline share the pydantic models and the evidence-edge schema so a claim round-trips without translation.

## Known tensions

- Zero-shot vs grounding: the less supervision, the more span hallucination; alignment verification and a grounding-failure metric are what make zero-shot honest.
- Long notes and multi-note records: chunk → extract mentions → claims per note → aggregate. Evidence must survive aggregation (PatientFact edges point at the note-level claims and their spans).
- Normalization (SapBERT → SNOMED/UMLS) is its own stage with its own evidence (the lexical variant) and its own error rate; keep it separable.
- Annotation UI is on the critical path for "minimal examples"; without a span-aware correction loop the ladder's bottom rungs never get climbed.

## Open questions (remaining)

1. Which structured EDW tables the `structured` tool exposes first (labs, meds, problem list), and their OMOP vs. Epic Clarity shape.
2. Whether v1 ships the deterministic cascade before the agents or alongside them.
3. Package/app naming and repo home.

## Sources

- LangExtract: https://github.com/google/langextract
- PydanticAI: https://ai.pydantic.dev ; MLflow PydanticAI tracing: mlflow.pydantic_ai.autolog (MLflow 3.x)
- medspaCy: https://github.com/medspacy/medspacy ; GLiNER-BioMed: https://arxiv.org/abs/2504.00676 ; OpenMed: https://github.com/maziyarpanahi/openmed ; TRACE: https://arxiv.org/html/2604.16364
- OMOP CDM v5.4 NOTE_NLP: https://ohdsi.github.io/CommonDataModel/cdm54.html ; OHDSI NLP WG schema: https://www.ohdsi.org/wp-content/uploads/2016/09/NLPrepresentationschemaforOMOP.docx.pdf
- mlx-lm LoRA: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md
- Outlines mlx-lm: https://dottxt-ai.github.io/outlines/latest/features/models/mlxlm/
- Ollama MLX engine ignores structured output: https://github.com/ollama/ollama/issues/17013
- MLflow optimize_prompts (GEPA): https://mlflow.org/docs/3.5.1/genai/prompt-registry/optimize-prompts/
- MLflow custom scorers: https://mlflow.org/docs/latest/genai/eval-monitor/scorers/custom/
- MLflow plugins: https://mlflow.org/docs/latest/ml/plugins/
- Reasoning LLMs for SDOH (self-consistency): https://arxiv.org/html/2604.13502v2
- Synthetic data distillation: https://www.nature.com/articles/s41746-025-01681-4
- Fine-tuned LMs, human-level IE (Strata): https://www.nature.com/articles/s41598-025-28767-z
