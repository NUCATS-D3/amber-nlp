# Strata-exemplar scaffold notes (historical; the scaffold was called "clinex" before the rename to amber)

Status: v0.1 scaffold built and tested 2026-09-04 (see 'v0.1 build notes' at the end). Companion to `clinical-ie-state-of-the-art.md`. Working name is a placeholder; rename before first tag.

## Decision: build our own, credit Strata

Strata (YalaLab, Apache 2.0, Sci Rep 2025) is ~1,500 lines. What is worth keeping from it: (1) the task abstraction — a question, its ground-truth columns, and a pair of functions mapping labels to model text and back; (2) the validated recipe — 4-bit base, LoRA r=128 / alpha=2r on q,k,v,o,gate,up,down, lr 1e-4, 3 epochs, adamw_8bit, linear schedule, a fixed response prefix ("My answer is:"), JSON-shaped answers, ≤100 annotated reports per task; (3) a synthetic breast-pathology example that demonstrates zero-shot → fine-tuned improvement. Everything else is glue around unsloth/TRL that has rotted (pinned 2024 unsloth commit, pre-SFTConfig TRL API, chat templates hand-coded for three model families, hardcoded CUDA, unbatched sampled generation, `Accession Number` as a magic string). Upstream is dormant (last commit 2025-03-19, no releases), so a fork would carry old structure without any upstream to pull from. We keep the three things above, port the example as a regression test, retain the Apache notice, cite the paper, and design the rest for Northwestern's needs.

## What changes relative to Strata

- Tasks are pydantic models, not Python files loaded by path. The answer model doubles as the JSON schema for constrained decoding and as the `response_format` for the MLflow prompt registry.
- Two task kinds: `LabelTask` (one structured answer per report; Strata's paradigm; v0.1) and `SpanTask` (a list of extractions each grounded to a character interval; LangExtract's paradigm; v0.2).
- Inference is batched and greedy by default, through a `transformers` backend (CPU/GPU, dev and tests) and a `vllm` backend with LoRA adapters (production). Only generated tokens are decoded; the chat template comes from the tokenizer with an override hook.
- Metrics (per column and per task: exact match, precision, recall, F1, bootstrap CIs) are plain functions exposed through MLflow scorers.
- Every run, model, prompt, and dataset is versioned in MLflow (self-hosted server). Dagster orchestrates; MLflow records.
- No WandB. No unsloth in the core dependency set; it is an optional extra on the training path only.

## Package layout

```
amber/
  schemas.py        Task, LabelTask, SpanTask, TaskSet (YAML + pydantic answer models)
  data.py           load CSV/JSON/parquet; build train messages and inference prompts; dataset hashing
  train.py          TRL SFTTrainer + PEFT LoRA; unsloth optional; MLflow autolog of loss; logs adapter + model
  infer.py          Extractor(tasks, backend) -> DataFrame; TransformersBackend, VLLMBackend
  metrics.py        per-column / per-task metrics, bootstrap CIs
  grounding.py      align answer strings to source char intervals (v0.2; LangExtract alignment or own)
  mlflow_ext/
    model.py        ClinicalExtractorModel (pyfunc): adapter + base ref + TaskSet + parser; predict(df)
    scorers.py      @scorer functions + evaluate(predictions, taskset) -> mlflow.genai.evaluate
    prompts.py      sync TaskSet prompts to the MLflow prompt registry (with response_format)
    context.py      RunContextProvider entry point: git sha, dataset hash, amber version, EDW extract id, IRB id
  cli.py            amber train | predict | evaluate | register-prompts
examples/strata_reproduction/   synthetic data + tasks.yaml + config; regression test
tests/
```

## MLflow extension points used

| Surface | Kind | Use |
|---|---|---|
| Custom pyfunc model | model layer | The registered unit: base model reference + LoRA adapter + TaskSet + parsers. `predict(reports_df) -> structured columns`. Wraps `mlflow.transformers` PEFT logging (adapter-only, Hub revision recorded). |
| `@scorer` + `mlflow.genai.evaluate` | GenAI eval | Per-row Feedback per task/column with rationale (raw response, parse error), aggregate P/R/F1; evaluation UI. Primary eval surface. |
| Prompt registry (`mlflow.genai.register_prompt`) | GenAI | Each task's preamble/question/response prefix as a versioned prompt with `response_format` = the pydantic answer model. Lineage: prompt version → train run → model version → eval run. |
| Tracing (`@mlflow.trace`) | GenAI | Spans per report: prompt build → generate → parse → ground. Feeds scorers and debugging. |
| `mlflow.run_context_provider` entry point | plugin | Auto-tags on every run from any launcher (CLI, Dagster, notebook). |
| HF `MLflowCallback` (`report_to=["mlflow"]`) | integration | Training loss/lr per step. |

Not used: tracking/artifact-store plugins (standard Postgres + S3/MinIO server), deployment plugins (serve via vLLM + pyfunc), project backends (Dagster does orchestration), classic `mlflow.model_evaluator` (add later as a thin adapter over the same metric functions if a consumer needs `mlflow.evaluate`).

## LangExtract's role

Component, not framework. Use it for `SpanTask`: few-shot-by-example prompting, chunking/parallel/multi-pass, and char-interval alignment (ungrounded extractions surface as `char_interval = None`). Provider: its OpenAI provider pointed at a vLLM OpenAI-compatible endpoint, or a small custom provider via `@router.register` for LoRA-adapter routing; community `langextract-vllm` exists but is single-contributor. Not used for `LabelTask` (judgment-per-report answers have no single justifying span) or for training.

## Data contract

Input: a table with `report_id` (configurable column name), `report_text`, and optional ground-truth columns per task. Output: same keys plus `<task>__response` (raw), `<task>__<field>_pred`, `<task>__parse_ok`, and for SpanTask `<task>__spans` (JSON). Dataset hash = sha256 over sorted (report_id, report_text, gt columns) → logged as a run tag and param.

## Roadmap

- v0.1 (this scaffold): LabelTask, transformers backend, TRL+PEFT training, metrics, pyfunc model, scorers + genai.evaluate, prompt-registry sync, run-context provider, CLI, Strata example reproduced on CPU with a tiny model, unit tests.
- v0.2: vLLM backend with LoRA and JSON-schema constrained decoding; SpanTask via LangExtract; grounding for LabelTask evidence fields; Dagster asset definitions (`extract → dataset → train → evaluate → register`).
- v0.3: distillation path (teacher generates span-grounded QA → student fine-tune, per npj Digit Med 2025); self-consistency voting option for zero-shot reasoning models; note-structure preprocessing (sectioning, TRACE-style template removal) as an optional stage.
- Later: classic `mlflow.evaluate` evaluator adapter; multi-question-per-prompt mode; LLM-judge scorers for free-text fields.

## Open questions

- Package name and repo home (I.AIM GitHub org?).
- Report identifier semantics across EDW sources (accession vs note id vs encounter).
- Which base models to standardize on first (Llama-3.1-8B-Instruct per paper; Qwen3-8B and Gemma-3-12B as candidates).
- Whether to persist base-model weights into MLflow (`persist_pretrained_model`) for reproducibility or rely on an internal HF mirror.

## v0.1 build notes (2026-09-04)

Built and delivered as `amber-v0.1.zip` / `amber-v0.1.bundle` (~2,000 lines incl. tests). All of the v0.1 roadmap items are in: pydantic LabelTask/TaskSet (inline `fields:` or Python answer models), TRL+PEFT training with completion-only loss (unsloth optional), batched greedy transformers backend, metrics with bootstrap CIs, pyfunc `ClinicalExtractorModel`, `@scorer`s + `mlflow.genai.evaluate`, idempotent prompt-registry sync with `response_format` = answer schema, `mlflow.run_context_provider` entry-point plugin, click CLI (train / predict / evaluate / register-prompts), Strata example ported, 7 tests passing (unit + CPU end-to-end against sqlite and against a local `mlflow server`).

Verified in a local MLflow 3.16 server: training loss per step via HF MLflowCallback; adapter + config + preprocessed data as artifacts; logged model round-trips through `mlflow.pyfunc.load_model`; eval runs nest under the train run via `--parent-run`; scorer aggregates (`<task>_all_correct/mean`) and dataset metrics (`<task>/f1`, `overall/all_correct`, per-column) on the same run; prompts `smoke_strata.source` / `.cancer` with the pydantic schema attached; one trace per (task, split).

Findings worth remembering:
- transformers must stay <5 while TRL is <=0.24 (unsloth's ceiling): under transformers 5, `apply_chat_template(tokenize=True)` returns a BatchEncoding, TRL's prompt/completion prefix check fails, and completion-only loss silently degrades to full-sequence loss. Pinned in pyproject with a comment.
- Hub access is blocked from the build sandbox, so the CPU smoke test uses a random-init tiny Llama built by `tests/tiny_model.py`; it proves plumbing only. Real reproduction (Llama-3.1-8B-Instruct, 4-bit, r=128) needs a GPU box with Hub or mirror access.
- MLflow 3.16 prompt registry accepts a pydantic class as `response_format`; comparing `properties`/`required` of the stored schema is enough for idempotent re-registration.

Next: v0.2 items (vLLM backend with LoRA + guided decoding, SpanTask via LangExtract, Dagster assets), plus rename the package and pick a repo home.
