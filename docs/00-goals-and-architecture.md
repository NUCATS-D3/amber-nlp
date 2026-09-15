# amber: goals, principles, and ecosystem map

Name: **amber** — messy text hardened into structured, durable data with the evidence still visible inside. A verified span of source text preserved in a claim is an **inclusion** (the gemological term for what is trapped in amber). Structured-source evidence and inference steps keep their descriptive names.

Status: revised plan, 2026-09-04. Supersedes the Strata-anchored framing in
`03-strata-scaffold-notes.md`. This document describes the intended architecture; see
[Current implementation](README.md#current-implementation) for what exists on disk and what
remains planned.

## Goals as stated

1. Clinical IE that reduces total expert effort at a task-specific quality target. Minimize annotated examples where useful, including zero-shot experiments, while counting task definition, prompt development, annotation, correction, and adjudication time.
2. Every clinical claim retains verifiable source evidence, directly or through inference steps. Measure whether that evidence supports the claim as well as whether its source location is valid. Record abstention and execution failure explicitly.
3. MLflow integration that works on open-source MLflow as well as Databricks; the tool is meant to be shared beyond one institution.
4. Pydantic schemas; local-model fine-tuning as an option; no constraint to particular models, from Apple-silicon training on a laptop to expensive GPU resources.
5. Output is structured tables with links to evidence that serve a named downstream workflow. Define acceptable errors, coverage, and review effort with that workflow's users before optimizing the system.

## First experiment and success criteria

Choose one clinically useful note-level task, one permitted provider, one persistence path, and
one table export. The first experiment is a fixed extraction pipeline with the same quote and
commit validation used by later agents, plus a minimal correction interface. Agent orchestration,
additional NLP stages, and backend breadth require measured benefit before adoption.

Before model or prompt selection, version a task protocol describing the answer schema, explicit
negative answers, abstention rules, annotation coverage, intended workflow, and gold derivation.
Set numeric acceptance thresholds for correctness, unsupported claims, omissions, automation
coverage, expert minutes per accepted case, and cost. The first task is now defined in the
[current progression protocol v1.0.0](protocols/oncology_current_progression-v1.md), including
task-specific pilot gates. These are prospective research targets; none has been clinically
demonstrated, and the task schema, candidate tooling, and clinical evaluation remain subsequent work.

CORAL v1.0 (DOI `10.13026/v69y-xa45`) is the first clinical evaluation dataset. Its 40 expert-labeled
notes support a pilot, not a broad generalization claim. Consult its documentation and BRAT
configuration before selecting labels; confirm that the selected annotations support the chosen
note-level answers. Any additional expert adjudication counts toward annotation effort. The 200
other notes and their GPT-4 pseudo-labels are categorically excluded from the current-progression
slice, including all train, development, and test partitions.

Split by patient/document before deriving examples. Freeze the adapter policies and split manifest
before held-out evaluation; resample patients/documents for uncertainty estimates. Synthetic
fixtures test the software offline. They do not establish clinical performance. Clinical inputs
and reconstructable outputs remain restricted local data, with declared deidentified sensitivity.

## Principles derived from the goals

- Evidence is a first-class object. Every non-rejected clinical claim needs evidence; every branch of its support graph must terminate in verified text or structured-source evidence. A rationale alone cannot establish a clinical fact.
- Annotated examples are one asset serving every rung of the annotation ladder: demonstrations, prompt-optimizer training data, fine-tuning data, and the eval set. Design the example format once, with spans.
- Portability records the base model, tokenizer/chat template, adapter, and execution configuration. Capability-checked interfaces isolate backend details; equivalent behavior across configurations must be tested.
- MLflow usage is restricted to the OSS API surface; Databricks is a tracking URI and a registry naming convention, never a dependency.
- Evaluate label correctness, semantic support, evidence localization, and omissions separately. Report expert effort and automation coverage alongside quality so abstaining on difficult cases cannot hide errors.

## The data model (the heart of it)

The v1 kernel contains sources, mentions, claims, and three evidence kinds connected by evidence
edges. Stable IDs and provenance make source lineage inspectable. `CaseOutcome` records whether a
task was answered, unanswered, or failed; it is separate from clinical claims. The binding fields
are in `02-v1-schemas-and-tools.md`.

- Source: a note (or later any record: lab, med order, imaging report). Fields: source_id, patient_id, type, datetime, text, section map.
- Mention: a grounded span in a source: source_id, start, end, quote, mention_type, normalized concept (optional, e.g. SNOMED/UMLS via SapBERT), attributes (negation, temporality, experiencer, certainty, value + unit). This maps almost one-to-one onto OMOP `NOTE_NLP` (snippet, offset, lexical_variant, note_nlp_concept_id, term_exists, term_temporal, term_modifiers, nlp_system), which becomes the standard export for mention-level output.
- Claim: a note-level structured decision typed by a pydantic model (the task's answer schema). Fields: claim_id, source_id, task, value (typed), confidence.
- PatientFact (later): an aggregation of claims across sources into a patient-level value, with the aggregation rule named. Patient identity and effective time are preserved in v1; aggregation behavior is deferred.
- Evidence edge: from a Claim to an Inclusion, StructuredEvidence, or InferenceEvidence. Inference inputs reference known evidence or claims within the allowed case scope. Validate acyclicity, source reachability, and field-level requirements at commit and load boundaries.

V1 output tables include `sources`, `claims`, `mentions`, `evidence`, `evidence_edges`, and `cases`
with their outcomes. Add `annotation_events` with the correction workflow. Start with these
durable tables; implement the OMOP `NOTE_NLP` export when its mappings are validated for the task.
Quotes, offsets, and rationales remain useful without MLflow being reachable; trace IDs are links.

Grounding rule: `quote` aligns candidate text to the immutable source and returns its actual slice
or a grounding failure. Start with exact alignment. Add fuzzy alignment only with a named policy
and measured false-alignment behavior on ambiguous, repeated, negated, and temporally qualified
text; offsets and quotes always refer to the original source. Failed alignment cannot support a
claim. Neither exact nor fuzzy alignment proves the clinical interpretation is correct.

## Evidence kinds (revised)

Inclusion is a verified interval in a text source; StructuredEvidence is a verified field/value in
a structured source; InferenceEvidence records an explicit rationale and the evidence or claims
it consumes. Inference inputs must be nonempty and every support branch must reach a source leaf.

Three guarantees remain distinct: structural validity (schema, IDs, acyclicity), source
traceability (verified leaves and scope), and semantic support (correct interpretation). The first
two are enforced by the kernel; semantic support is evaluated against task guidance and expert
judgment. Explicit absence is an evidence-backed answer. Not mentioned, conflicting evidence,
insufficient evidence, and execution failure are separate case outcomes, never fabricated null
claims. A not-mentioned outcome records complete review of the declared task scope.

## Execution architecture and agent experiments

The v1 unit of work is one note × a question typed by a Pydantic schema. The fixed pipeline and
agents share `quote`, `commit_claim`, and explicit outcome recording. Models propose candidate
values and quotes; only the grounding path mints Inclusions. The commit boundary enforces the
same evidence requirements regardless of the execution strategy.

First establish structured extraction followed by grounding and commit validation. Then compare
a bounded extractor agent and extractor-plus-reviewer on the same cases, model configuration,
task definitions, and evidence rules. Declare call/token/retry budgets and report their actual
usage. Select strategies on development data; run the frozen comparison on held-out cases.

Candidate tool belt; implement only what the first task or a measured error pattern requires:

- `sections(source_id)` — medspaCy sectionizer / MedSlice-style; returns section spans and labels.
- `dedupe(source_id)` — marks template/copy-forward candidates. Template status alone does not exclude clinical evidence. Annotation coverage, duplicate content, and clinical relevance are separate policies.
- `find_mentions(source_id, types)` — GLiNER-BioMed / OpenMed / scispaCy / medspaCy target rules; returns Mention objects with offsets.
- `context(mention_id)` — medspaCy ConText / negspacy: negation, temporality, experiencer, certainty.
- `normalize(mention_id)` — SapBERT → UMLS/SNOMED/RxNorm; returns candidates with scores.
- `quote(source_id, text)` — exact alignment with an optional validated fuzzy policy; the model-facing minter of Inclusions. Human annotations use the same source validation.
- `search(patient_id, query, kinds)` — v1 searches sections/chunks of the current note; patient-wide search is deferred. Snippets are candidates until quoted.
- `structured(patient_id, table, filters)` — EDW/OMOP lookups (labs, meds, problems); returns rows that become StructuredEvidence.
- `dates(text)` / `calc(expr)` — date normalization and arithmetic (durations, "3 months prior to").
- `commit_claim(schema, value, evidence_ids, rationale)` — validates against the pydantic model, requires ≥1 evidence id, records InferenceEvidence from the current trace.

Agent roles, introduced only when the corresponding comparison is justified:

1. Note extractor: bounded, single source, fixed schema. Measure whether its quality and cost support the intended scale and selected model.
2. Reviewer: note-level in v1. Plans over sections/chunks, delegates extraction, and reconciles conflicts with source evidence. Patient-wide review and PatientFacts are later work.
3. Verifier: a separate pass (different model, or same model different prompt) that reads each claim's evidence and answers "does this support it?" — the online form of the evidence-faithfulness scorer. Disagreements go to a review queue.
4. Annotation assistant: proposes claims + evidence for the annotator to accept/correct; accepted records are new examples (active learning).

Cascade for cost is a hypothesis: task rules or targeted NLP may resolve some cases cheaply.
Measure errors among non-escalated cases, including confident omissions. Choose escalation
thresholds from development-set error/coverage curves; self-reported confidence is not calibrated
reliability. Report error rates, abstention, automation coverage, and total cost together.

PydanticAI is the planned runtime for the bounded agent experiment, with MLflow tracing through
`amber.mlflow_ext`. The fixed baseline does not require an agent loop. Verify the selected
endpoint's structured-output and tool capabilities; avoid additional orchestration frameworks
until a documented need appears.

Later optimization candidates include extractor fine-tuning, prompt optimization, and trajectory
distillation. Their benefit and cost are task-specific. Evaluate tool-calling reliability and
clinical quality separately; valid JSON does not establish either successful tool use or support.

MLflow mapping for agents: traces are the natural record of InferenceEvidence (span ids point into them); `mlflow.genai.evaluate(predict_fn=agent)` runs the scorers over cases; trace-aware scorers check tool-call correctness (was `quote` used for every span? did `commit_claim` cite verified ids?); `optimize_prompts` treats agent prompts as the optimization target.

## The annotation ladder

Use the same canonical Example format, with disjoint train/dev/test membership. These are
experimental options, not mandatory stages or promises about required sample sizes:

1. Zero-shot: schema, instructions, and definitions with no task demonstrations. Count the effort spent producing and revising the instructions.
2. Few-shot: add grounded training examples. Report their number and any voting/self-consistency calls; the cited SDOH setup with 50 examples belongs here.
3. Prompt optimization: optimize registered prompts on train/dev cases and retain a held-out test set. Include optimizer calls and expert scorer calibration in the cost.
4. Distillation: generate teacher labels in a separately identified pool and measure student performance. The cited study's reported gains vary by metric; do not assume the student improves every outcome.
5. Fine-tuning: compare a task-specific adapter against the strongest simpler baseline. Treat the Strata recipe and sample sizes as starting hypotheses; validate the chosen training configuration.

The correction loop turns accepted/corrected cases into Examples with annotation events and
verified spans. Test whether correction is faster than manual authoring at equal quality. Count
review of incorrect proposals, setup effort, and adjudication; do not reuse held-out corrections
as demonstrations or optimization data within the same evaluation.

## Model and hardware spectrum

Long-term execution options behind a capability-checked protocol (`schema_constrained`, `adapters`,
`logprobs`, `batch`, `tools`). Start with one permitted endpoint; these are not all first-release
requirements:

- Apple silicon: `mlx-lm` for LoRA/DoRA/QLoRA training (chat and completions JSONL formats) and generation; Outlines' `mlx-lm` model class for JSON-schema-constrained decoding. Do not route structured output through Ollama's MLX engine: it silently ignores `format` (ollama#17013 / #16563); Ollama's GGUF path enforces schemas.
- GPU: transformers + PEFT (unsloth optional) for training; vLLM with LoRA adapters and xgrammar/outlines guided decoding for inference.
- Hosted APIs: structured outputs from Gemini / OpenAI / Anthropic, or Databricks model serving inside the institution's HIPAA boundary. PHI policy decides which of these are allowed per deployment; the tool treats it as a backend property.

Interchange is deferred until two concrete training/inference configurations need it. HF PEFT is
the intended interchange target, but MLX conversion must validate architecture, target modules,
tensor shapes, scaling, tokenizer/chat template, and quantization assumptions for supported
configurations. Require tensor round-trips and behavioral comparisons; do not assume a generic
key rename is sufficient. Training files are versioned exports of Examples for each runtime.

## MLflow: OSS surface only

Start with one permitted OSS-compatible tracking destination, versioned prompts, run metadata, and
evaluation reports. Add tracing for the execution path under test. Prompt optimization, pyfunc
packaging, and model registry workflows follow demonstrated needs. Centralize all integrations
in `amber.mlflow_ext` and verify the supported API versions at implementation time.

Avoid as dependencies: Databricks-only production monitoring / scheduled scorers, `databricks-agents` Agent Evaluation, managed judges. Registry naming differs (Unity Catalog three-part names on Databricks); abstract it behind one config key.

Evidence and traces: InferenceEvidence carries the rationale text and a trace id. Traces are the debugging and audit surface; the evidence table is the durable product.

## Evaluation that respects grounding

Keep these dimensions separate in the first clinical report:

1. Label correctness: per-field exact / P / R / F1, explicit-negative performance, and missed answerable cases; bootstrap by patient/document rather than by mention.
2. Evidence faithfulness: does the cited span or rationale actually support the claim? Human-judged on a sample; LLM-judge scorer for scale, calibrated against the human sample.
3. Evidence localization: span overlap with gold spans (exact, partial, token-level), and grounding-failure rate (unalignable spans).

Also report structural/source-validation failures, unsupported-claim rate, abstention by reason,
execution failures, within-note conflicts, latency, total model cost, and expert correction time.
Define automation coverage as eligible cases completed without expert intervention divided by all
eligible cases, and show quality on that subset alongside omissions on the full set. Compare with
manual authoring under matched case complexity; report setup costs separately and amortized over
the intended workload. Human semantic review starts in the first experiment; an LLM judge is
optional and requires calibration on separate human judgments.

Every report identifies dataset/version/hash, patient/document split, task/schema, adapter and
annotation-coverage policies, prompt/model/backend versions, budgets, thresholds, and denominators.
Keep clinical reports and artifacts in permitted storage. Insufficient precision or sample size
is an experiment result, not a reason to silently relax the acceptance target.

## Decisions (2026-09-04)

1. Unit of analysis: note-level for v1. Preserve patient IDs and effective times; patient aggregation and its temporal reconciliation remain separate later work.
2. Execution: fixed extraction first; bounded extractor and note reviewer are subsequent controlled comparisons. Include them in a usable release only when they improve the declared quality/effort/cost tradeoff.
3. Export: one durable evidence-linked table bundle first. OMOP `NOTE_NLP` remains the v1 interoperability target after validating identifiers and concepts. Patient-level domain tables and FHIR are deferred.
4. PHI boundary: undecided (Databricks or Azure both possible; EDW access pending). Abstract it as a policy gate — see "Provider zones and data sensitivity" below.
5. Annotation: a minimal correction interface accompanies the first clinical experiment. The full evidence-DAG application follows measured workflow needs.

## Provider zones and data sensitivity

Two labels and one gate, so the PHI decision can be made later per deployment without touching pipeline code.

- Every model provider (backend) declares a `zone`: `local` (this machine), `institution` (Databricks model serving, an on-prem vLLM/Ollama, Azure OpenAI under the institution's BAA), `external_baa` (a vendor API covered by a BAA), `external` (no BAA). Zone is a property of the configured endpoint, not the model family: the same Llama can be `local` or `institution`.
- Every dataset declares a `sensitivity`: `synthetic`, `deidentified`, `limited` (LDS), `phi`.
- A policy table (per deployment, in config, logged as run tags) says which zones may see which sensitivity. Default policy: `phi`/`limited` → `local` or `institution` only; `deidentified` → adds `external_baa`; `synthetic` → anything. The runtime refuses a run whose provider zone is not allowed for the dataset's sensitivity, and records zone + sensitivity on every MLflow run and every claim's provenance stamp.
- A future `deidentify` tool would create a separate immutable derivative with provenance. Any change in declared sensitivity or permitted destinations requires an explicit dataset policy decision.
- Same abstraction covers the EDW itself: the `structured` tool is a provider with a zone; Databricks vs. Azure vs. local Parquet extracts are endpoint configuration.

Apply destination checks before sending source-bearing data to tracking/tracing servers, evaluation
judges, artifact stores, or annotation services as well. A permitted model endpoint does not
authorize an unrelated telemetry destination. Log only permitted payloads; treat quotes, offsets,
and reconstructable derivatives under the input's restrictions. Deidentification or a BAA does
not override dataset license/access terms, and an automatic deidentifier does not by itself
authorize lowering a dataset's declared sensitivity.

## Annotation app

Purpose: the human is a tool in the loop. The app is where the annotation assistant's proposals are corrected into examples, where the verifier's disagreements are adjudicated, and where gold spans and gold claims for evaluation are minted — one app, one data model, three queues.

Start in M3 with source viewing, evidence selection, value/outcome editing, accept/correct/reject,
append-only events, and export to Example. Measure authoring and correction time plus adjudicated
quality. A general graph viewer, active-learning queues, multi-user deployment, and additional
agents are later extensions; they are not prerequisites for the first usability measurement.

Core screens:

- Source view: note text with section shading, template/copy-forward spans dimmed, mentions highlighted by type, and the selected claim's evidence spans emphasized. Span editing (drag boundaries, split, merge), add-a-span-as-evidence, and a "quote" action that mints a verified span exactly the way the agent's tool does.
- Claim panel: the pydantic schema rendered as a form (fields, enums, units), the current value, confidence, and the evidence list. Accept / correct / reject per claim; corrections require evidence.
- Evidence DAG: a compact layered view per claim — spans and structured rows at the leaves, inference steps as nodes with their rationale, prior claims as inputs. Clicking a node highlights its span in the source or opens the structured row. Not a general graph editor; a viewer with "add evidence" and "remove edge".
- Queue: cases ordered by uncertainty, disagreement (model vs. model, annotator vs. annotator, verifier vs. extractor), and schema coverage; the active-learning policy is pluggable and logged.

Data model: the durable tables from the contract plus an append-only `annotation_events` log
(who, when, case, action, before/after). Accepted Examples include grounded claims or adjudicated
unanswered outcomes. Events support replay and provenance; inter-annotator agreement still
requires independent annotations and a defined adjudication protocol. Schema changes may require
explicit migrations and renewed review rather than automatic relabeling.

Start with a local correction interface over the same persisted bundle and Pydantic models.
FastAPI + SQLite and a React/TypeScript front end remain options for the expanded app, with
Postgres only when shared deployment is needed. Avoid building a second persistence system for
the first experiment; keep source text and evidence round-trippable through the existing store.

## Known tensions

- Limited supervision: measure unsupported claims and omissions at each supervision level; valid
  offsets alone cannot establish that zero-shot extraction meets the task's clinical needs.
- Long notes and multi-note records: chunk → extract mentions → claims per note → aggregate. Evidence must survive aggregation (PatientFact edges point at the note-level claims and their spans).
- Normalization (SapBERT → SNOMED/UMLS) is its own stage with its own evidence (the lexical variant) and its own error rate; keep it separable.
- Expert effort: a span-aware correction loop must demonstrate a time saving at matched quality;
  a small demonstration set can still require substantial prompt work and adjudication.

## Open questions (remaining)

1. How much additional independent review and adjudication CORAL needs to establish gold answers
   under the current progression protocol; candidate annotations alone do not supply task gold.
2. Whether the first task can meet its protocol's quality, coverage, cost, and expert-effort gates
   in the small pilot; all gates remain unevaluated until the M2/M3 measurements exist.
3. Which permitted provider and tracking/artifact destinations are available under the dataset's terms.
4. Whether measured errors justify an agent, reviewer, targeted NLP/normalization, or additional training after the fixed baseline.
5. Later: which EDW tables and deployment/storage requirements justify expanding the first implementation.

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
