# Roadmap and milestones

Ordered so that every milestone leaves something runnable and something measurable. Each milestone ends with an MLflow run you can point at.

## M0 — Workspace (this zip)

uv project, dependency groups, docs, empty package skeleton with module responsibilities, synthetic breast-pathology data (Strata's, Apache 2.0), CLAUDE.md for the coding agent. No implementation.

## M1 — Data model and grounding kernel

`schemas.py` per `02-v1-schemas-and-tools.md` §1–§7 with validators (quote == text[start:end]; acyclic edges; evidence required on commit). `quote()` with exact + fuzzy alignment and a grounding-failure result. Parquet/DuckDB tables. Tests: round-trips, invariants, alignment edge cases (whitespace, line breaks, OCR-ish noise).
Measure: none yet beyond tests.

## M2 — Deterministic cascade

`sections`, `dedupe` (frequency-based fallback first; attribution-metadata module when EDW access lands), `find_mentions` (GLiNER-BioMed default; OpenMed/medspaCy alternates), `context`, `normalize` (SapBERT), task rules for the synthetic tasks. OMOP `NOTE_NLP` view.
Measure: mention P/R on the synthetic set; cascade coverage (share of cases with a claim).

## M3 — Provider layer and policy gate

`Provider`, `Zone`, `Sensitivity`, `Policy`; backends: OpenAI-compatible (covers vLLM, mlx-lm server, Ollama GGUF, Azure OpenAI, Databricks serving), mlx-lm in-process with Outlines for schema constraint, transformers in-process. Capability flags. Gate stamps provenance.
Measure: a policy test matrix; a schema-constrained generation smoke test per backend.

## M4 — Extractor agent

PydanticAI agent with the tool belt; `commit_claim`; `no_claim`; MLflow autolog tracing; prompt registry for task instructions and the agent prompt. Zero-shot on the synthetic tasks with a local model and with one hosted model (synthetic data, so any zone).
Measure: §11 scorers via `mlflow.genai.evaluate`; grounding-failure rate; tool-call counts.

## M5 — Reviewer agent and cascade escalation

Section/chunk planning, delegation to the extractor, conflict reconciliation, note-level commit with InferenceEvidence chains. Escalation policy from the cascade.
Measure: escalation rate; claim accuracy cascade-only vs. cascade+reviewer; conflict rate.

## M6 — Annotation app (evidence-DAG-first)

FastAPI + relational store + React/TS. Source view with spans, claim panel, evidence DAG viewer, queue. `annotation_events` log; gold by replay; `Example` export. The annotation-assistant agent is "run the extractor and load its CaseResult into the queue."
Measure: time per case; inter-annotator agreement on a pilot set.

## M7 — Annotation ladder

Demonstrations from Examples; `optimize_prompts` (GEPA) loop; TRL/mlx-lm fine-tuning of the extractor step with the Strata recipe as default; adapter ↔ PEFT converter with round-trip test; pyfunc model + registry.
Measure: accuracy vs. number of examples, per rung, on the same test split.

## M8 — Verifier and hardening

Verifier agent (second model) as online faithfulness check; review queue routing; LLM-judge scorer calibrated to human judgments; Dagster assets for extract → cases → run → evaluate → register.

## Later

Patient-level: PatientFact, temporal aggregation, `search` over a patient's sources, FHIR export. SpanTask via LangExtract for mention-dense schemas. Distillation of agent trajectories into small models.
