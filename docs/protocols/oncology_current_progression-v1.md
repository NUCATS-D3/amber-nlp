# Oncology current progression or recurrence protocol

| Field | Value |
|---|---|
| Task | `oncology_current_progression` |
| Protocol version | `1.0.0` |
| Answer schema | `OncologyCurrentProgressionAnswer` |
| Unit of review | One CORAL progress note |
| Scope | Whole immutable note excluding explicit `SectionSkip` intervals |
| Evidence policy | Exact source-backed evidence for `progression_or_recurrence` |
| Dataset | CORAL v1.0 (`10.13026/v69y-xa45`) expert-labeled 40-note set |
| Sensitivity | `deidentified` |
| Split | 20 train / 10 dev / 10 test documents, seed `20260915` |
| Intended use | Retrospective research workflow only |

Status: versioned M1 research protocol with implemented answer-schema, candidate, and manifest
tooling. The restricted-data split is frozen locally, with input hashes and unchanged-byte/mtime
reuse verified. Clinical extraction, evaluation, and correction remain subsequent work.
All performance gates are unevaluated. This protocol is not
clinical validation, expert endorsement, a gold-generation result, or evidence of deployment safety.

## Question and review scope

The task asks: **“Does this note support current progression or recurrence of the patient's
cancer?”** “Current” means the clinical state asserted at the note's effective time, not every
historical event mentioned in the note.

Review the whole immutable note except intervals explicitly annotated as `SectionSkip`. The CORAL
documentation describes narrative-section annotation while excluding direct copy-forward
radiology/pathology text and marking skipped sections explicitly. Skipped text is outside the
annotation scope, but a candidate that overlaps a skip interval must be escalated for review rather
than silently discarded. Malformed data or an unresolved skip boundary can prevent complete review.

The answer has one required strict boolean field, `progression_or_recurrence`. It has no null,
default, or extra answer fields. It records a supported current clinical state, not whether
progression or recurrence appears anywhere in the note and not whether the patient has ever
experienced it. The [OncologyCurrentProgressionAnswer schema](../../src/amber/schemas/oncology_current_progression.py)
validates answer shape only; it does not establish source grounding or semantic support.

## Decisions and outcomes

| Decision | Record | Required interpretation |
|---|---|---|
| Positive | answered, `true` | Current, actual progression or recurrence of the patient's cancer, supported by exact evidence. |
| Explicit negative | answered, `false` | Current stability/remission or explicitly negated progression/recurrence, supported by exact evidence. |
| Conflict | `conflicting_evidence` | Qualifying current positive and negative evidence coexist and protocol rules do not resolve them; cite both sides. |
| Insufficient | `insufficient_evidence` | A current answer remains unsupported after reviewing applicable evidence, or unresolved ambiguity/damage/coverage prevents completion. Historical, future, uncertain, or hospice-only evidence is insufficient on its own. |
| Not mentioned | `not_mentioned` | Complete review of the declared scope found no evidence relevant to the task. Relevant evidence that cannot establish a current answer belongs under insufficient evidence. |
| Failure | `failed` | Policy, provider, parse, grounding, validation, budget, or internal execution prevented completion; never a clinical label. |

An evidence-backed `false` answer is a clinical answer and is distinct from `not_mentioned`.
`not_mentioned` means a complete review found no task-relevant evidence; it must not be defined as
merely the absence of an answerable current fact. If review is incomplete, the outcome is
`insufficient_evidence`, even when no relevant evidence has been found so far. No decision creates
a null-valued Claim. A failed execution is never a clinical answer or a gold Example.

### Evidence requirements

Every answered Claim requires exact, end-exclusive character spans into the immutable source text,
with evidence assigned to the sole answer field, `progression_or_recurrence`. Each quote must equal
the original source slice at its recorded offsets. Rationales, generated text, copied BRAT surface
strings, and search snippets are candidates or audit material; none can serve as evidence by itself.
The selected evidence must semantically support the boolean answer, not merely match source text.

For a conflict, cite exact evidence for both current interpretations. Every non-answer outcome
records its reason. Its `reviewed_source_ids` include only sources whose entire task-defined scope
was reviewed; partial review is documented separately in the execution audit. `not_mentioned`
requires complete review of every case source, while `insufficient_evidence` can result from
incomplete review. Reviewers must document why selected evidence is temporally applicable at the
note's effective time. A `failed` outcome is set only by the runtime, never by clinical review or
candidate mapping.

## CORAL candidate mapping

This mapping is a conservative way to find cases for review. It is not a rule for deriving gold.
The CORAL BRAT configuration declares `DiseaseState` as an entity. Although
`DiseaseProgression`, `Remission`, and `Hospice` occur in configuration references, they are not
declared entity types; encountering those legacy names requires review rather than treating them as
invented gold fixtures.

| Candidate signal | Candidate treatment |
|---|---|
| `DiseaseStateVal=progression-recurrence` with `affirmed` modality | Positive seed with `temporality_unverified`. |
| `DiseaseStateVal=progression-recurrence` with absent modality | Positive seed with `missing_modality` and `temporality_unverified`. |
| Affirmed `DiseaseStateVal=stability` or `DiseaseStateVal=remission` | Negative seed with `temporality_unverified`. |
| Negated `DiseaseStateVal=progression-recurrence` | Negative seed with `temporality_unverified`. |
| Negated stability or remission | Warning only; it does not imply progression. |
| `uncertain_in_present`, `uncertain_in_past`, `planned_in_future`, or `hypothetical_in_future` | Warning only; insufficient by itself. |
| Family or other experiencer | `non_patient_experiencer` warning only; insufficient by itself. |
| `DiseaseStateVal=progression-others`, `others`, or `hospice` | Warning only; never sufficient for a boolean answer by itself. |
| Legacy undeclared `DiseaseProgression`, `Remission`, or `Hospice` entity name | `unsupported_entity_type` warning; insufficient by itself. |
| Any `HistoryVal` attached to `DiseaseState` | `invalid_attribute_target`; insufficient by itself. |

The usable-seed rows assume patient or absent experiencer. An explicit family or other experiencer
makes the signal warning-only regardless of its disease-state value or modality.

`NegationModalityVal=affirmed` is not a temporal assertion. `HistoryVal` has the values
`history|new` and applies to tumor characteristics and biomarkers, not `DiseaseState`; this
protocol never manufactures a `HistoryVal=current` annotation. Affirmed and negated candidate
seeds require the warning `temporality_unverified`. A seed with absent modality requires
`temporality_unverified` and also `missing_modality`. Presence of a relevant temporal relation receives
`temporal_relation_unresolved`. Interpreting dates, relations, and currentness requires human review
in this slice; no general temporal resolver is introduced.

Warnings remain separate from final outcomes. A past-event or uncertain annotation does not veto
independently supported current evidence. A human reviewer may establish either answer from source
context; the adapter policy does not redefine the clinical task.

### Candidate inventory and proposal rules

Annotation-inventory completeness and human clinical scope review are separate facts. A parser can
establish that it inventoried the annotation records; it cannot certify that a human examined the
whole note. Candidate tooling preserves all parsing diagnostics and applies these fail-closed rules:

- Incomplete inventory, malformed records, duplicate or conflicting records, and dangling
  references block an answer candidate with `insufficient_evidence` and
  `incomplete_annotation_inventory`, while preserving the underlying stable parser diagnostics.
- Invalid relevant spans block an answer candidate with `insufficient_evidence` and the applicable
  stable diagnostic, including `malformed_span`, `discontinuous_span`, or
  `source_surface_mismatch`.
- A relevant candidate overlapping `SectionSkip` blocks an answer candidate with
  `insufficient_evidence` and `skipped_overlap`. A malformed or otherwise unresolved skip boundary
  blocks it with `insufficient_evidence` and `incomplete_scope`.
- Missing disease-state values and non-patient-only signals produce `insufficient_evidence` with
  `missing_disease_state` and `non_patient_experiencer`, respectively.

After those blockers, usable seeds in only one direction propose the corresponding binary answer;
opposing usable positive and negative seeds propose `conflicting_evidence` and preserve identifiers
from both sides. Warning-only records propose `insufficient_evidence` when no usable seed exists.
When warning-only context accompanies a usable seed, retain the warning without vetoing the seed.
These are candidate proposals only: human review can revise any of them.

A complete annotation inventory with no relevant annotations may only propose `not_mentioned` with
`annotation_absence_only`. That diagnostic explicitly means annotation absence only; it does not
certify human clinical-scope review and cannot itself create a gold `not_mentioned` outcome. An
incomplete inventory can never propose `not_mentioned`.

> Candidate labels are non-authoritative stratification and review aids. They never create gold
> Claims or Examples. Gold requires independent human review and adjudication under this protocol.

### Required behavior examples

All examples below are invented and contain no CORAL source text.

- A complete review finds no evidence relevant to the task: record `not_mentioned`.
- The only relevant evidence describes recurrence years before the note, with no supported current
  state: record `insufficient_evidence`, not `not_mentioned` and not `answered true`.
- Evidence describes historical recurrence and independently supports current remission: record
  `answered false`, citing the current-remission span. Historical recurrence does not override the
  supported current state.
- Evidence describes historical stability and independently supports current progression: record
  `answered true`, citing the current-progression span. Historical stability does not override the
  supported current state.
- An uncertain or historical progression candidate coexists with independently supported current
  actual progression: record `answered true` if the current evidence establishes the answer. The
  warning on the other candidate does not veto it.
- Qualifying current progression and current remission are both supported and the protocol cannot
  resolve them: record `conflicting_evidence` and cite both sides.
- Review stops before all applicable note scope is examined: record `insufficient_evidence`, even
  if no relevant evidence has been found so far.

## Gold creation and leakage controls

Two qualified reviewers independently assign the answer or outcome and select exact source
evidence. Any disagreement in answer, outcome, or a material evidence-span boundary goes to a third
oncology reviewer. Preserve all reviewer decisions and adjudication provenance in permitted local
storage. Adjudication records are explicitly outside this implementation slice.

Freeze the split before deriving Examples. The 40 documents are divided by cancer type so each
cancer type contributes 10 train, 5 development, and 5 test documents, producing 20 train,
10 development, and 10 test documents overall with seed `20260915`. Before assigning individual
documents, allocate positive-candidate quotas jointly across both cancer types while preserving
those per-cancer-type counts. Freeze that stratification policy and its allocation with the
manifest. Group by `coral_idx`/patient or document identity. Initial tooling rejects any repeated
patient identity, not merely an identity that would cross partitions. No note, patient, mention,
relation, span, chunk, or generated derivative may cross partitions.

The immutable manifest becomes the membership authority after its first creation. Local manifests
and adjudication artifacts must remain in ignored paths under `experiments/coral/data/` or
`experiments/coral/outputs/`. They must never be written beneath `experiments/coral/data/raw/` or
over any input file. Changes to candidate policy, adapter policy, or annotation do not authorize
repartitioning. Initial tooling must reject changed inputs against an existing manifest. A future
explicit manifest revision must retain assignments for every known patient, preserve the prior
manifest artifact, and preserve the test-exposure history.

Do not tune on test documents. Held-out corrections cannot be used as demonstrations, optimizer
data, training data, or otherwise enter the evaluated training workflow. Split membership remains
attached to accepted Examples and every derived export.

## Pilot acceptance gates

The frozen gates are:

- at least 95% automated-answer precision and zero held-out false positives;
- at least 85% recall;
- at most 15% omissions;
- at least 60% automation coverage;
- zero unsupported accepted claims;
- zero test execution failures;
- at most $0.50 model cost per note;
- at most 2 expert minutes per accepted case; and
- at least 25% expert-time reduction versus manual authoring at matched quality.

These are M1 protocol definitions, not measured results. Evaluator implementation belongs to
M2/M3. Score the frozen test partition separately; train and development results may not be pooled
with it to satisfy a gate.

### Measurement population and denominators

- `N` is all eligible test notes fixed before execution: 10 for this CORAL pilot. Abstentions,
  failures, and gold non-answer outcomes remain in `N`. Every eligible note requires an adjudicated
  reference outcome; missing gold makes evaluation incomplete.
- `B` is notes with an adjudicated binary answer. `P` is the subset with gold `true`, and `Q` is
  the subset with gold `false`, so `B = P + Q`.
- `A` is notes receiving a complete automatic binary task answer before case-specific human
  intervention. Retries within the frozen execution budget are permitted. Human-corrected answers,
  execution failures, and automatic abstentions do not enter `A`.
- `C` is automatic binary answers exactly matching the adjudicated binary value. Either boolean
  answer on a gold non-answer case is incorrect. Evidence validity is measured separately.
- `A_pos` is automatic `true` answers. `TP` is the subset of `A_pos` with gold `true`.
  `FP = A_pos - TP`, including positive answers on gold non-answer cases. This conservative count
  does not reclassify those reference outcomes as clinical negatives.
- `O` is gold-binary notes receiving no complete automatic binary answer. Abstentions, execution
  failures, and outputs rejected by validation are omissions. Wrong boolean answers are errors in
  correctness and recall, not omissions.
- `K` is cases accepted after the evaluated assisted workflow, including correction and independent
  quality review. Accepted non-answer outcomes may enter `K`; execution failures may not. The
  assisted and manual workflows use the same acceptance quality standard.
- `J` is accepted binary final Claims. `U` is the subset lacking valid source grounding or semantic
  support for `progression_or_recurrence`. Inspect every accepted final Claim, including corrected
  Claims.

| Metric | Definition | Pilot rule |
|---|---|---|
| Automated-answer precision (binary-answer correctness) | `C / A`; both boolean values are included | At least 0.95 |
| Positive predictive value (PPV) | `TP / A_pos` | Report separately; no additional numeric PPV gate |
| Held-out false positives | `FP` | Zero; require `Q > 0` to assess performance on explicit negatives |
| Positive recall | `TP / P`; missed positives include false answers, abstentions, and failures | At least 0.85 |
| Omission rate | `O / B` | At most 0.15 |
| Automation coverage | `A / N`; report correct automatic yield `C / N` separately | At least 0.60 |
| Unsupported accepted claims | `U`, also report `U / J` | Zero; `J` must be positive for this gate to be assessed |
| Execution failures | Number of eligible test executions ending in `failed`, by failure kind | Zero; every eligible note must have an attempted run |
| Mean model cost per note | Total cost of all model calls for the evaluated run divided by `N` | At most $0.50 |
| Expert minutes per accepted case | Total attributed expert minutes `T / K` | At most 2 |
| Expert-time reduction | `1 - (T_assisted / K_assisted) / (T_manual / K_manual)` at matched quality | At least 0.25 |

Automated-answer precision is binary-answer correctness, not PPV. Automatic clinical non-answer
outcomes are scored against gold by reason but do not increase binary automation coverage. Count
retries, failed calls, and model-based verification in evaluated-run model cost. Report development,
optimizer, and teacher expenditure and worst-case per-note cost separately so the run mean does not
hide total research cost.

For expert-time gates, `T` includes task definition, prompt development, annotation, correction,
adjudication, and failed-case review attributed to the workflow. Declare allocation of shared
setup and reference-building effort before comparison and count it once without omitting it from
overall effort. Charge allocated setup fully to the observed pilot: the actual evaluated workload
is the gating amortization workload. A larger-workload projection may be shown separately with its
assumption but cannot replace the pilot gate.

Use randomized or counterbalanced manual-versus-assisted assignment where feasible, and avoid
having a reviewer author and then correct the same remembered note. If there is no matched-quality
manual comparison, the expert-time-reduction gate is unevaluated.

### Gate status and undefined measurements

Apply gates to observed point estimates and counts, not confidence bounds. A threshold that is met
is an observed pilot result, never evidence that the population meets that threshold. Each gate is
`met`, `unmet`, or `inconclusive`:

- A zero denominator or missing measurement yields `NA`, never zero or one. A dependent gate is
  inconclusive.
- `P = 0` makes recall inconclusive. `Q = 0` makes the zero-false-positive gate inconclusive even
  if `FP = 0`; do not reshuffle held-out notes to obtain missing classes. PPV is undefined when
  `A_pos = 0`, but it is descriptive and adds no gate.
- If there are no automatic answers, precision is undefined and coverage is zero, so coverage is
  unmet. If there are no accepted cases, expert minutes per accepted case is undefined. A missing
  or nonpositive manual-time baseline makes expert-time reduction inconclusive.
- Overall status is `unmet` if any assessable gate is violated, `met` only when every required gate
  is assessable and met, and `inconclusive` otherwise. Report every unmet or inconclusive gate.
  Missing gold, an unattempted eligible case, or incomplete required measurements prohibits an
  overall `met` result; report any other observed violations as well.
- M1 software completion cannot establish clinical acceptance. All performance gates remain
  unevaluated until the required M2/M3 measurements exist.

### Uncertainty

Report uncertainty independently of gate decisions. Use 95% patient/document bootstrap intervals
with 10,000 resamples and a separate random-number generator initialized with seed `20260915`.
Resample whole test-note records with replacement, keeping each note's outputs, gold, costs, and
review events together. Preserve pairing and assignment for manual-versus-assisted time estimates.
Keep allocated fixed setup effort fixed across resamples. Never resample mentions independently,
retrain on resamples, or alter frozen membership.

For each defined resampled metric, compute a percentile interval. Report how many replicates have
an undefined denominator and exclude those replicates without substituting values; label such an
interval as conditional on a defined denominator. If the observed metric is undefined or no
resample is valid, report no interval. Also report a 95% Wilson binomial interval for every defined
case-level proportion because bootstrap intervals can collapse at zero observed errors or 100%
observed success. Give numerator/denominator counts with every interval and state that ten held-out
notes provide limited information. Neither a collapsed interval nor a met point-estimate gate is
clinical validation or a population-level guarantee.

### Arithmetic examples

With 10 eligible notes, 4 gold positives, 4 gold negatives, and 2 gold non-answer cases, suppose six
automatic answers are correct (3 true and 3 false) and the remaining four notes are abstained.
Precision is `6/6 = 100%`, PPV is `3/3 = 100%`, recall is `3/4 = 75%`, omissions are
`2/8 = 25%`, and coverage is `6/10 = 60%`. Recall and omissions are unmet despite perfect
correctness among answered cases.

A partition with no gold positives has undefined recall. Zero observed false positives does not
fill in that missing measurement, and the frozen partition must be retained.

## Reporting and limitations

Every report identifies the dataset version, immutable manifest hash, split, protocol version,
adapter-policy version, sample counts, exclusions, provider and destination policy, and expert
effort. Report every gate with its numerator, denominator, point estimate or count, status, and
uncertainty where defined. Report train, development, and frozen-test findings separately.

The CORAL documentation states that patients were selected for documented disease progression,
tabular staging data, and an available medical oncology note. This selection on progression means
the pilot is not a prevalence sample. Documented progression in a patient's history does not
establish progression at the sampled note's effective time. CORAL's 200 additional notes and their
GPT-4-generated outputs are categorically excluded from this current-progression slice, including
all train, development, and test partitions.

The expert-labeled sample has only 40 notes, with 10 held out here. It cannot establish prevalence,
clinical validation, deployment safety, human expert endorsement, or broad generalization. Its
restricted, credentialed access requires the applicable data-use agreement and license and the
required training. Source text, offsets, annotations, and reconstructable derivatives remain in
permitted local storage and are not repository fixtures or shared report content. This document
defines a retrospective research protocol only; no metric has yet been measured and no gate has
yet been met.

## Protocol sources

- [CORAL v1.0, DOI `10.13026/v69y-xa45`](https://doi.org/10.13026/v69y-xa45), dataset
  documentation pages 2–3 and 6.
- Access-controlled local CORAL BRAT
  [`annotation.conf`](../../experiments/coral/data/raw/annotated/annotation.conf), consulted for
  entity, attribute, and relation declarations; no clinical records are redistributed here.
- [Amber v1 task, evidence, outcome, and evaluation contract](../02-v1-schemas-and-tools.md).
