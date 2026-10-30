# Roadmap and milestones

> Historical source snapshot, consolidated 2026-10-07. The milestone order and status claims below
> are superseded by the [canonical roadmap](../04-roadmap.md), which preserves task-first M1–M3
> delivery and policy before source-bearing calls. See the [bundle index](README.md).

Ordered so that every milestone leaves something runnable and something measurable. Each milestone ends with an MLflow run you can point at. Revised 2026-10-07: M1 adds the vocabulary store; M2 is restructured around the fragment builder and a mention-detector bake-off.

## M0 — Workspace (this zip)

uv project, dependency groups, docs, empty package skeleton with module responsibilities, synthetic breast-pathology data (Strata's, Apache 2.0), CLAUDE.md for the coding agent. No implementation.

## M1 — Data model, grounding kernel, vocabulary store

`schemas.py` per `02-v1-schemas-and-tools.md` §1–§7 with validators (quote == text[start:end]; acyclic edges; evidence required on commit; concept fields standard and in-fragment). `quote()` with exact + fuzzy alignment and a grounding-failure result. Parquet/DuckDB tables.

Vocabulary store: load an Athena download (or the EDW vocabulary schema) into DuckDB; `descendants`, `is_a`, `upgrade`; record `vocabulary_release`. Tests run against a tiny synthetic vocabulary fixture (hand-made concepts, no SNOMED content), so CI never needs licensed data.

Tests: round-trips, invariants, alignment edge cases (whitespace, line breaks, OCR-ish noise), ancestor queries over a polyhierarchy fixture, "Maps to" upgrade of a deprecated concept.
Measure: none yet beyond tests.

## M2 — Fragments, deterministic cascade, detector bake-off

1. Fragment builder (§3a): `FragmentSpec` → `Fragment` (concepts, labels, lexicon from CONCEPT_SYNONYM + "Maps to" source names); SapBERT index and dictionary patterns built locally from it. A first real spec for the target domain (e.g. cardiology conditions + procedures + cardiac drugs), checked into the repo as a spec only.
2. Cascade tools: `sections`, `dedupe` (frequency-based fallback first; attribution-metadata module when EDW access lands), `find_mentions` with pluggable backends, `context` (ConText), `normalize` (SapBERT, label-restricted candidates, soft group boost, ancestor back-off), task rules for the synthetic tasks. OMOP `NOTE_NLP` view with standard and source concept ids.
3. Gold set: 50–100 target-domain notes annotated with mention spans, ConText attributes, and linked concepts (the M6 app does not exist yet; use a lightweight span tool and import into `Example.mentions`). This set is needed regardless of which detector wins.
4. Bake-off, same notes, same scorers:
   - GLiNER2.5-base zero-shot, with and without Fragment label descriptions
   - GLiNER-BioMed
   - fragment dictionary (medspaCy TargetMatcher)
   - LLM + `quote()` grounding (synthetic or de-identified notes only, per policy)
   - dictionary ∪ best model
   Plus: ConText vs. GLiNER2.5 span attributes on polarity/temporality; GLiNER2.5 tokenization check on clinical shorthand.
5. Decision record: the winning `find_mentions` default and attribute source go into the goals doc's Decisions list with the run ids.

Measure: `mention_detected` and `mention_attr_correct` per backend; `concept_linked` (@1, @5, back-off rate) on gold spans and end-to-end; cascade coverage (share of cases with a claim); CPU latency per note per backend.

Exit criterion: a chosen default detector and attribute source, each backed by an MLflow run comparing it to the alternatives.

## M3 — Provider layer and policy gate

`Provider`, `Zone`, `Sensitivity`, `Policy`; backends: OpenAI-compatible (covers vLLM, mlx-lm server, Ollama GGUF, Azure OpenAI, Databricks serving), mlx-lm in-process with Outlines for schema constraint, transformers in-process, local encoder detectors (GLiNER family, SapBERT). Capability flags. Gate stamps provenance.
Measure: a policy test matrix; a schema-constrained generation smoke test per backend.

## M4 — Extractor agent

PydanticAI agent with the tool belt; `commit_claim` with is-a checks against CONCEPT_ANCESTOR; `no_claim`; MLflow autolog tracing; prompt registry for task instructions and the agent prompt. Zero-shot on the synthetic tasks with a local model and with one hosted model (synthetic data, so any zone).
Measure: §11 scorers via `mlflow.genai.evaluate`; grounding-failure rate; tool-call counts; hierarchy-violation rejections at commit.

## M5 — Reviewer agent and cascade escalation

Section/chunk planning, delegation to the extractor, conflict reconciliation (section priority, temporality, concept hierarchy), note-level commit with InferenceEvidence chains. Escalation policy from the cascade, including detector-vs-linker type conflicts. First reconciliation against `structured` OMOP rows on the same concept ids, where EDW access allows.
Measure: escalation rate; claim accuracy cascade-only vs. cascade+reviewer; conflict rate.

## M6 — Annotation app (evidence-DAG-first)

FastAPI + relational store + React/TS. Source view with spans, claim panel, evidence DAG viewer, queue. Concept picker backed by the Fragment (search lexicon, browse ancestors/descendants). `annotation_events` log; gold by replay; `Example` export. The annotation-assistant agent is "run the extractor and load its CaseResult into the queue."
Measure: time per case; inter-annotator agreement on a pilot set.

## M7 — Annotation ladder

Demonstrations from Examples; `optimize_prompts` (GEPA) loop; TRL/mlx-lm fine-tuning of the extractor step with the Strata recipe as default; adapter ↔ PEFT converter with round-trip test; pyfunc model + registry.

Detector adaptation, if the M2 winner is a GLiNER-family model: silver data from fragment-dictionary matches over unlabeled notes plus LLM spans grounded by `quote()`; LoRA adapter per fragment; concepts withheld from silver data entirely; offset-based training format required (if the detector's format is string-only, export only strings unique within their source).
Measure: accuracy vs. number of examples, per rung, on the same test split; `heldout_concept_recall` for adapted detectors.

## M8 — Verifier and hardening

Verifier agent (second model) as online faithfulness check; review queue routing; LLM-judge scorer calibrated to human judgments; vocabulary-upgrade job (new Athena release → new fragment_id → `upgrade()` of stored concepts, logged and reversible); Dagster assets for extract → cases → run → evaluate → register.

## Later

Patient-level: PatientFact, temporal aggregation, `search` over a patient's sources, FHIR export. SpanTask via LangExtract for mention-dense schemas. Distillation of agent trajectories into small models. Relation extraction between mentions (finding site, laterality via GLiNER2.5 JointIE or similar) if M2/M5 error analysis shows pre-coordinated linking is insufficient.
