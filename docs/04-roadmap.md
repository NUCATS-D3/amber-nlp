# Roadmap and milestones

Revised 2026-09-04. The first delivery spans M1–M3: one clinically useful note-level task with
validated evidence, a reproducible baseline, and a minimal correction workflow. Its purpose is
to test whether Amber reduces total expert effort at a declared quality target.

M4–M8 are conditional expansions. A runnable prototype or a failed clinical experiment is useful
progress, but neither demonstrates clinical acceptance. Record results and limitations without
lowering thresholds after seeing held-out outcomes. Model experiments produce an OSS-compatible
MLflow run and durable reports in permitted storage; offline kernel tests need no tracking server.

Use development cases for failure analysis and selection of later experiments. Track test-set
exposure across milestones; if test findings guide a change, use a fresh independent evaluation
for a confirmatory claim or label the repeated comparison exploratory.

## M0 — Workspace and interface scaffold (current)

Implemented: uv project, docs, synthetic fixtures, public Python facade, settings, info CLI,
optional FastAPI health/info routes, four interface smoke tests, and a minimal MLflow version tag
provider. Domain and extraction modules remain placeholders. CORAL ingestion is an audit utility;
its draft adapter is not a working domain integration. No clinical performance result exists.

## M1 — Task protocol and evidence kernel

Select one downstream clinical workflow and one note-level answer schema. Version a protocol with:

- Positive and explicit-negative answers, unanswered outcomes, failure handling, and evidence rules.
- Gold derivation and annotation coverage; confirm which CORAL annotations support the task and
  budget any additional expert labeling/adjudication. Consult the dataset documentation first.
- Patient/document split and dataset manifest/hash, adapter policy version, sensitivity, and
  permitted provider/telemetry/artifact destinations. Keep pseudo-labels separate from expert gold.
- Numeric quality, unsupported-claim, omission, automation-coverage, expert-time, and cost targets;
  metric denominators, uncertainty estimation, and the intended workload for amortizing setup cost.

Implement the schema contract, IDs, source immutability, exact `quote`, grounding failures,
`commit_claim`, explicit `CaseOutcome`, and policy rules. Reject unknown/out-of-scope references,
cycles, empty inference inputs, source-free support branches, and missing field evidence. Explicit
absence requires a clinical claim with evidence; no mention and runtime failure never become null
claims. Use one local DuckDB store with round-trippable sources, evidence, and outcomes, plus a
Parquet bundle export. Future stores remain behind interfaces.

Exit evidence: offline synthetic round-trips and adversarial tests for every invariant, including
Unicode/CRLF, repeated quotes, invalid offsets, outcome consistency, and provider policy. A separate
local CORAL integration report records mapping decisions and disagreements without redistributing
source data. Freeze policies before held-out scoring. Add fuzzy alignment only after a named
policy and false-alignment tests justify it; exact-only operation is sufficient for this exit.

## M2 — Fixed extraction baseline and clinical evaluation

Implement one capability-checked, permitted provider endpoint and enforce the policy before every
model/EDW call and every source-bearing telemetry/artifact transfer. Version task prompts through
the registry; use `amber.mlflow_ext` for the permitted tracking destination. The fixed pipeline
proposes structured values and candidate quotes, then invokes the shared quote/commit/outcome
functions. It has bounded retries and no agent planning loop.

Build evaluation alongside extraction: label correctness, omissions, structural/source validity,
expert-assessed semantic support, localization, outcomes by reason, failures, latency, model cost,
and automation coverage. Calibrate any automated judge separately; expert review is required for
the initial semantic-support assessment. Synthetic data validates execution; CORAL validates the
chosen clinical task within its scope and sample-size limits.

Exit evidence: a reproducible development baseline, source-linked Parquet output, an error review,
and a versioned evaluation report. Report patient/document-level uncertainty and subgroup counts;
40 gold notes do not establish broad clinical generalization. Record setup and expert review time.
Do not tune prompts, adapter rules, or thresholds against held-out cases.

## M3 — Minimal correction workflow and first delivery decision

Add source viewing, quote/evidence selection, value and outcome editing, accept/correct/reject,
append-only annotation events, and canonical Example export over the existing local store. Keep
the interface small; multi-user deployment, a general evidence-DAG viewer, and active-learning
queues are later work. Human-created spans follow the same source validation as tool quotes.

Compare manual authoring and assisted correction at matched task difficulty with independent
quality adjudication. Avoid having a reviewer author and then correct the same remembered case;
use a randomized/counterbalanced assignment where feasible. Count task definition, prompt work,
annotation, correction, adjudication, and review of failed cases. Preserve original split membership
when accepted cases become Examples; held-out corrections cannot train the evaluated system.

Exit evidence: freeze the chosen configuration and evaluate held-out cases against the M1 protocol.
Report expert minutes per accepted case, accepted-case quality, automation coverage, omissions,
unsupported claims, and total cost, including setup amortization at the intended workload. Decide
whether the first task meets its targets, needs a specific change, or needs more data. Completion
of the experiment does not imply clinical readiness. This decision precedes broad platform work.

## M4 — Extractor agent

If development-set errors suggest adaptive tool use could help, compare a bounded PydanticAI extractor
with M2's fixed pipeline. Use the same case split, model configuration, answer schema, grounding
rules, and permitted destinations. Declare call/token/retry budgets, record actual usage, and
keep the correction workflow available. Add only tools needed by the hypothesis under test.

Exit evidence: paired quality, support, omission, latency, cost, and expert-time results. Adopt the
agent only for a demonstrated benefit under the task protocol; retaining the fixed pipeline is
a valid outcome. Record prompt and orchestration differences so gains can be attributed.

## M5 — Reviewer agent and cascade escalation

If development-set conflicts or long-note errors justify it, compare extractor-plus-reviewer against
both preceding strategies under the same evaluation controls. Reviewers operate on sections/chunks
of one note and preserve offsets into the original Source. Inference chains retain verified leaves.

Trial targeted rules, sectioning, mention extraction, context, normalization, or deduplication only
for measured errors or cost. Template detection is not an annotation-coverage or relevance filter.
Choose escalation thresholds from development error/coverage curves; audit non-escalated cases
for confident omissions. Self-reported confidence alone is not an acceptance rule.

Exit evidence: incremental gains, conflict resolution errors, automation coverage, non-escalated
error/omission rates, and total cost. Ship only justified components. Add OMOP `NOTE_NLP` when the
downstream task needs it, with explicit identifier/concept mapping and conformance checks.

## M6 — Annotation app (evidence-DAG-first)

Expand M3 only in response to observed review needs: keyboard navigation, evidence-DAG viewing,
queues, independent annotation/adjudication, and shared access. FastAPI/React and SQLite/Postgres
are deployment options; introducing a new store requires migration and replay checks. Accepted
Examples remain the single annotated-data source of truth.

Exit evidence: review time and independently measured inter-annotator agreement, event replay,
source/evidence round-trips, and permitted access/storage behavior for the chosen deployment.

## M7 — Annotation ladder

Compare zero-shot, demonstrations, prompt optimization, distillation, or fine-tuning when data and
expected benefit justify the experiment. Use disjoint train/dev/test Examples, count teacher and
optimizer calls, and report expert effort as well as learning curves. Research recipes are starting
hypotheses, not guaranteed sample sizes or gains.

Start with one training configuration. Add another backend or MLX–PEFT conversion only for a
concrete portability need, with declared architecture/tokenizer/quantization support, tensor
round-trips, and behavioral checks. Add pyfunc packaging/registry integration when deployment needs it.

Exit evidence: quality/effort/cost gains over the selected simpler baseline on a frozen comparison.
Repeated test-set exposure must be disclosed and may require a fresh independent evaluation set.

## M8 — Verifier and hardening

Add an online verifier only if calibrated human comparisons show that it finds useful errors at
acceptable cost. Human semantic review already starts in M2. Validate queue routing, operational
failure handling, source-bearing telemetry policy, and artifact retention for the deployment.
Dagster or other orchestration requires a demonstrated operational need and a documented decision.

Exit evidence: operational acceptance results and verifier false-positive/false-negative rates,
including correlated errors with the extractor. A second model is not automatically independent.

## Later

PatientFact, temporal aggregation, patient-wide search, FHIR, additional tasks/institutions,
SpanTask adapters, and trajectory distillation remain later work. Preserve extension points in
the kernel without making these prerequisites for the first measured clinical task.

## Milestone changes from the initial scaffold

M1 now includes the task protocol and policy rules. The original M3 provider work moves into M2's
single-endpoint baseline; the broad original M2 cascade becomes conditional work in M5. Minimal
correction moves from M6 to M3, while M6 retains the expanded app. M4/M5 agent adoption and M7/M8
extensions are conditional on evidence. Earlier references to milestone scope should use this
revised sequence.
