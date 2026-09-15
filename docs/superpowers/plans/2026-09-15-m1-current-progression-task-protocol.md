# M1 Current-Progression Task Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the protocol-first `oncology_current_progression` vertical slice: a strict binary
answer schema, a versioned clinical review protocol, and non-authoritative CORAL candidate and
document-split tooling.

**Architecture:** Keep the reusable answer base and task schema in the dependency-free domain
package. Keep all CORAL interpretation, candidate derivation, hashing, and split generation inside
the CORAL experiment, with a pure decision module separated from its local-data command. The
protocol is authoritative for human adjudication; BRAT-derived candidates are only stratification
and review aids and never become Amber gold automatically.

**Tech Stack:** Python 3.11, Pydantic v2, stdlib dataclasses/hashlib/itertools/json/pathlib, Click,
pytest, Ruff, mypy, and existing CORAL BRAT parsing utilities.

**Spec:** `docs/02-v1-schemas-and-tools.md` §§4 and 6, constrained by
`docs/00-goals-and-architecture.md`, `docs/04-roadmap.md`, `CLAUDE.md`, and the approved task brief
captured in “Required behavior” below.

**Review revision:** Incorporates the plan review: separate clinical decisions from candidate
warnings, make temporal assumptions explicit, preserve parser diagnostics, allocate split quotas
globally, enforce manifest immutability, and state CORAL's selection on documented progression.
The follow-up review clarifies not-mentioned outcomes and freezes metric denominators,
point-estimate gates, uncertainty reporting, and inconclusive-result handling.

**Progress (2026-09-15):** Tasks 1–3 are complete: the
[versioned protocol](../../protocols/oncology_current_progression-v1.md) is documented and reviewed,
and the strict public answer schema and CORAL parser diagnostics are implemented and tested.
Tasks 4–6 remain unimplemented.
No split manifest, adjudicated task gold, or clinical gate result has been produced by these
checkpoints. Answer validation does not enforce evidence requirements.

## Global Constraints

- This is an M1 protocol/schema/tooling slice only. Do not add extraction models, model calls,
  prompts, APIs, persistence, annotation UI, or automatic gold generation.
- Use task name `oncology_current_progression`, protocol version `1.0.0`, split seed `20260915`,
  and exact document counts `train=20`, `dev=10`, `test=10`.
- The answer is a required strict boolean field named `progression_or_recurrence`.
- The task is note-scoped over the whole immutable note except explicit CORAL `SectionSkip`
  regions. Evidence policy is field-level.
- CORAL v1.0, DOI `10.13026/v69y-xa45`, is restricted deidentified data. Never emit note text,
  quotes, offsets, annotations, demographics, or reconstructable derivatives to tracked files,
  shared logs, or test fixtures.
- Read `.txt` with `newline=""`; preserve its exact bytes/characters and treat BRAT offsets as
  end-exclusive. Validate bounds separately from copied annotation text.
- Local manifests and adjudication artifacts stay under ignored `experiments/coral/data/` or
  `experiments/coral/outputs/`. Tests use invented BRAT only.
- Candidate derivation is visibly non-authoritative. It must not construct `Claim`, `Example`, or
  any object marked gold.
- An affirmed annotation establishes polarity, not currentness. Every candidate with assumed
  currentness carries `temporality_unverified`; only human review establishes temporal applicability.
- Annotation inventory completeness and clinical scope review are distinct. A parser cannot certify
  that a reviewer examined the entire note.
- Freeze each cancer type at 10 train / 5 dev / 5 test documents; allocate positive-candidate quotas
  across both cancer types before assigning documents. Reject repeated patient identities.
- An existing manifest is immutable. Identical reruns reuse it; incompatible reruns fail without
  changing it. Never write a manifest beneath `data/raw/` or over any input file.
- The 200 unannotated notes and their GPT-4 pseudo-labels are excluded from this slice.
- Preserve unrelated untracked `.omx/` and `docs/slides/` content.
- Use Pydantic v2 `ConfigDict(extra="forbid", frozen=True)` for answer value objects.
- Keep `import amber` lightweight and do not introduce a new dependency.
- Run focused tests before full offline checks. Run the local aggregate CORAL audit without span
  output as a separate restricted-data integration check.

## Required behavior

The clinical protocol governs the adjudicated answer:

1. Reviewers decide which statements apply to the patient's cancer at the note's effective time.
   Historical events, future plans, other experiencers, and uncertainty alone do not establish a
   current answer. Their mere presence does not invalidate independent, applicable evidence.
2. Applicable current positive and negative evidence that remains contradictory after review yields
   `conflicting_evidence`, with evidence for both sides. Different times alone are not a conflict.
3. Supported current progression/recurrence yields `answered true`; supported current
   stability/remission or explicitly negated progression/recurrence yields `answered false`.
4. Unresolved ambiguity, incomplete review, or only historical/future/uncertain/hospice evidence
   yields `insufficient_evidence` when a current answer cannot be supported.
5. Only a complete human review of the declared scope with no relevant evidence can establish a
   gold `not_mentioned` outcome. Clinical/execution failure never becomes a gold outcome.

Candidate tooling proposes review aids under a separate conservative adapter policy:

1. Preserve all parsing diagnostics. Incomplete inventory, duplicate/conflicting records, invalid
   relevant spans, or unresolved skipped regions block an answer candidate and produce
   `insufficient_evidence` with stable diagnostic flags; unreadable input fails the CLI safely.
2. Candidate polarity comes from `DiseaseStateVal` and `NegationModalityVal`. `HistoryVal` is not
   defined for `DiseaseState` in CORAL v1.0 and must not be invented as a current/history attribute.
3. Affirmed or negated disease-state signals may seed an answer but always carry
   `temporality_unverified`. Missing modality adds `missing_modality`. No inference from either
   flag establishes currentness, evidence, or gold.
4. Preserve warnings about uncertain, past, future, hospice, and non-patient signals. These signals
   do not vote as positive/negative evidence or automatically veto an otherwise usable seed.
   Opposing usable seeds propose conflict; one direction proposes an answer; warning-only records
   propose insufficient evidence. Human review can revise any proposal.
5. No relevant annotations in a complete inventory proposes `not_mentioned` with
   `annotation_absence_only`; this is explicitly not certification of clinical review coverage.

Synthetic mixed-timeline examples for the protocol: historical recurrence plus current remission
is `false`; historical stability plus current progression is `true`. Candidate tooling must not
pretend it can recognize every historical statement from BRAT polarity alone.

## File map

- Create `docs/protocols/oncology_current_progression-v1.md`: authoritative task definition,
  annotation/adjudication procedure, split freeze, metrics, gates, and limitations.
- Create `src/amber/schemas/answers.py`: reusable frozen `AnswerModel` base only.
- Create `src/amber/schemas/oncology_current_progression.py`: strict task answer plus the four
  task constants; no registry abstraction.
- Modify `src/amber/schemas/__init__.py`: public exports for the two answer models and task
  constants.
- Create `tests/test_answer_schemas.py`: strict construction, serialization, mutation, and extra
  field tests.
- Modify `experiments/coral/brat.py`: preserve record-level diagnostics and detect
  incomplete, duplicate, conflicting, and dangling records without exposing source data.
- Modify `experiments/coral/audit.py`: report aggregate parser diagnostics through the audit CLI.
- Extend `tests/test_coral_brat.py` and `tests/test_coral_audit.py`: existing invented-BRAT
  parser and CLI regression tests. Import parser types/functions from `experiments.coral.brat`;
  `scripts/coral_ingest.py` is only a compatibility launcher.
- Create `experiments/coral/scripts/coral_current_progression.py`: pure CORAL annotation-to-candidate
  decision logic with no file writes.
- Create `experiments/coral/scripts/coral_current_progression_manifest.py`: local-only CLI for
  dataset discovery, hashes, deterministic split assignment, and manifest writing.
- Create `tests/test_coral_current_progression.py`: invented BRAT candidate-rule tests.
- Create `tests/test_coral_current_progression_manifest.py`: invented file-tree manifest and split
  tests.
- Modify `experiments/coral/README.md`: document candidate/manifest commands and their
  non-authoritative, restricted-data status.

---

### Task 1: Version the clinical task and acceptance protocol

**Files:**

- Create: `docs/protocols/oncology_current_progression-v1.md`

**Interfaces:**

- Consumes: task contract and outcome meanings from `docs/02-v1-schemas-and-tools.md`.
- Produces: the authoritative human-readable protocol named by
  `ONCOLOGY_CURRENT_PROGRESSION_PROTOCOL_VERSION = "1.0.0"` in Task 2.

- [x] **Step 1: Create the protocol document with frozen identity and scope**

Start the document with this exact metadata table:

```markdown
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
```

State that the question is: “Does this note support current progression or recurrence of the
patient's cancer?” Define “current” as the clinical state asserted at the note's effective time,
not every historical event mentioned in the note. State that skipped text is outside annotation
scope but any candidate overlapping it is escalated rather than silently discarded.

- [x] **Step 2: Write the answer and outcome decision table**

Include all of these rows and requirements:

| Decision | Record | Required interpretation |
|---|---|---|
| Positive | answered, `true` | Current, actual progression or recurrence of the patient's cancer, supported by exact evidence. |
| Explicit negative | answered, `false` | Current stability/remission or explicitly negated progression/recurrence, supported by exact evidence. |
| Conflict | `conflicting_evidence` | Qualifying current positive and negative evidence coexist and protocol rules do not resolve them; cite both sides. |
| Insufficient | `insufficient_evidence` | A current answer remains unsupported after reviewing applicable evidence, or unresolved ambiguity/damage/coverage prevents completion. Historical, future, uncertain, or hospice-only evidence is insufficient on its own. |
| Not mentioned | `not_mentioned` | Complete review of the declared scope found no evidence relevant to the task. Relevant evidence that cannot establish a current answer belongs under insufficient evidence. |
| Failure | `failed` | Policy, provider, parse, grounding, validation, budget, or internal execution prevented completion; never a clinical label. |

Explicitly distinguish an evidence-backed `false` answer from `not_mentioned`. Prohibit a null-valued
claim. Require exact end-exclusive source spans for the sole answer field and prohibit rationales,
generated text, copied BRAT surface strings, or search snippets from serving as evidence by
themselves.

Use these distinctions consistently in the protocol and reviewer examples: a complete review
finding no relevant evidence is `not_mentioned`; historical recurrence alone is
`insufficient_evidence`; historical recurrence plus supported current remission is `answered false`.
Incomplete review is insufficient even if no relevant evidence has been found so far. The absence
of an answerable current fact must never be used as the definition of not mentioned.

- [x] **Step 3: Document the CORAL candidate mapping and its limits**

Read the local CORAL documentation and `annotation.conf` before freezing the mapping. The declared
entity is `DiseaseState`; `DiseaseProgression`, `Remission`, and `Hospice` occur in configuration
references but are not declared entity types. Treat those legacy names as requiring review rather
than using them as invented gold fixtures. `DiseaseStateVal=progression-recurrence` is the positive
seed; `stability` and `remission` are negative seeds when affirmed; negated progression is a negative
seed. Negated stability/remission does not imply progression. Preserve `progression-others`,
`others`, and `hospice` as warning-only signals under this conservative candidate policy. A human
may still establish an answer from their source context; these adapter choices do not redefine
the clinical task.

`NegationModalityVal=affirmed` is not a temporal assertion. `HistoryVal` has values `history|new`
and applies to tumor characteristics/biomarkers, not `DiseaseState`; never manufacture a
`HistoryVal=current` fixture. Affirmed and negated seeds require `temporality_unverified`, and
absent modality also requires `missing_modality`. Preserve relevant temporal-relation presence as
`temporal_relation_unresolved`; interpreting dates/relations and currentness requires human
review in this slice. No general temporal resolver is added.

Keep annotation warnings separate from final outcomes: a past-event or uncertain annotation does
not automatically veto independently supported current evidence. Include the two mixed-timeline
examples in “Required behavior,” and require reviewers to document the temporal applicability of
their selected evidence. Malformed data or an unresolved skip boundary can still prevent a complete
review; do not silently ignore damaged evidence.

State in a highlighted warning:

```markdown
> Candidate labels are non-authoritative stratification and review aids. They never create gold
> Claims or Examples. Gold requires independent human review and adjudication under this protocol.
```

- [x] **Step 4: Document gold creation and leakage controls**

Require two qualified reviewers to independently assign answer/outcome and exact evidence. Send
any answer, outcome, or material evidence-span disagreement to a third oncology reviewer. Preserve
all decisions and adjudication provenance locally, but exclude adjudication records from this
implementation slice.

Freeze the 20/10/10 document-level split before deriving examples. Group by `coral_idx`/patient or
document identity so no note, patient, mention, relation, span, chunk, or generated derivative can
cross partitions. Prohibit tuning on test documents and prohibit using held-out corrections in the
evaluated training workflow.

Use the frozen manifest as the membership authority after first creation. Subsequent candidate
policy or annotation changes do not authorize repartitioning. The initial tooling rejects changed
inputs against an existing manifest; a future explicit revision must retain assignments for known
patients and preserve the prior artifact and test-exposure history.

- [x] **Step 5: Document pilot gates, reporting, and limitations**

Record these gates verbatim:

- at least 95% automated-answer precision and zero held-out false positives;
- at least 85% recall;
- at most 15% omissions;
- at least 60% automation coverage;
- zero unsupported accepted claims;
- zero test execution failures;
- at most $0.50 model cost per note;
- at most 2 expert minutes per accepted case; and
- at least 25% expert-time reduction versus manual authoring at matched quality.

**Freeze the measurement population and denominators.** These are definitions for the protocol;
implementing evaluators remains M2/M3 work. Score the frozen test partition separately from train
and development results, without pooling them to meet a gate. Let:

- `N` be all eligible test notes fixed before execution (10 for the CORAL pilot). Keep abstentions,
  failures, and gold non-answer outcomes in this denominator. Require adjudicated reference
  outcomes for every eligible note; missing gold makes the evaluation incomplete.
- `B` be notes with an adjudicated binary answer, `P` those with gold `true`, and `Q` those with
  gold `false`, so `B = P + Q`.
- `A` be notes receiving a complete automatic binary task answer before case-specific human
  intervention. Retries inside the frozen execution budget are permitted; human-corrected answers
  cannot enter `A`. Execution failures and automatic abstentions are not automatic binary answers.
- `C` be automatic binary answers exactly matching the adjudicated binary value. Either boolean
  answer on a gold non-answer case is incorrect. Evidence validity is measured separately below.
- `A_pos` be automatic `true` answers; `TP` is the subset with gold `true`.
  Define `FP = A_pos - TP`, including positive answers on gold non-answer cases. This conservative
  false-positive count does not reclassify those reference outcomes as clinical negatives.
- `O` be gold-binary notes receiving no complete automatic binary answer. Abstentions, execution
  failures, and outputs rejected by validation count as omissions. Wrong boolean answers count
  as errors in correctness/recall, not as omissions.
- `K` be cases accepted after the evaluated assisted workflow, including correction and independent
  quality review. Accepted non-answer outcomes may count toward `K`; execution failures cannot.
  Acceptance criteria and the manual comparison must use the same quality standard.
- `J` be accepted binary final claims and `U` those lacking valid source grounding or semantic
  support for the answer field. Inspect all accepted final claims, including corrected ones.

Publish the following names and formulas; do not interchange binary-answer correctness and
positive predictive value:

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

Count retries, failed calls, and any model-based verification in evaluated-run model cost. Report
development/optimizer/teacher expenditure and worst-case per-note cost separately so the run mean
does not obscure total research cost. Automatic clinical non-answer outcomes are evaluated against
gold by reason, but do not increase binary automation coverage.

For expert-time gates, include task definition, prompt development, annotation, correction,
adjudication, and failed-case review attributed to each workflow. Declare the allocation of shared
setup/reference-building effort before the comparison and count it once, without omitting it from
the overall effort accounting. Charge the allocated setup effort fully to the observed pilot;
the amortization workload for gating is the actual evaluated workload. Larger-workload projections
may be reported separately with their assumed workload, but cannot substitute for the pilot gate.
Measure manual and assisted time on a randomized or counterbalanced assignment where feasible,
avoiding same-reviewer recall from authoring and correcting the same note. If no matched-quality
manual comparison exists yet, the reduction gate remains unevaluated.

**Apply gates to observed point estimates and counts, not confidence bounds.** Label a threshold
that is met as an observed pilot result, never as evidence that population precision or recall
meets that threshold. Each gate has status `met`, `unmet`, or `inconclusive`:

- A ratio with a zero denominator is undefined (`NA`), never zero or one by convention. Missing
  measurements are also `NA`. A gate depending on them is inconclusive.
- `P = 0` makes recall inconclusive; `Q = 0` makes the zero-false-positive gate inconclusive, even
  when `FP = 0`. Do not reshuffle held-out notes to obtain missing classes. PPV with `A_pos = 0` is
  undefined, but PPV is descriptive and does not add a separate gate.
- No automatic answers makes precision undefined and coverage zero; coverage is therefore unmet.
  No accepted cases makes expert minutes per accepted case undefined. A missing or nonpositive
  manual time baseline makes time reduction inconclusive.
- Overall status is unmet if any assessable gate is violated, met only when every required gate
  is assessable and met, and inconclusive otherwise. List every unmet/inconclusive gate. Missing
  gold, an unattempted eligible case, or incomplete required measurements prohibits an overall met
  result; other observed violations must still be reported.
- Software completion in M1 does not establish clinical acceptance: these performance gates are
  unevaluated until the corresponding M2/M3 measurements exist.

**Report uncertainty independently of the gate decision.** Use 95% patient/document bootstrap
intervals with 10,000 resamples and a separate RNG initialized with seed `20260915`. Resample whole
test-note records with replacement; keep all outputs, gold, costs, and review events for a note
together. Preserve pairing/assignment when estimating manual-versus-assisted time differences.
Keep allocated fixed setup effort fixed across resamples. Never resample mentions independently,
retrain on resamples, or change the frozen membership.

Compute percentile intervals for defined resampled metrics; report how many replicates have an
undefined denominator and exclude those replicates without substituting values. When replicates
are excluded, label the interval as conditional on a defined denominator. If the observed metric
is undefined or no resample is valid, report no interval. Also report a 95% Wilson binomial interval
for each defined case-level proportion, because bootstrap intervals can collapse at zero observed
errors or 100% observed success. Present numerator/denominator counts with every interval and
state that ten held-out notes provide limited information. Neither a collapsed bootstrap interval
nor a met point-estimate gate establishes clinical validation or a population-level guarantee.

Include these arithmetic examples in the protocol to make denominator choices reviewable:

- With 10 eligible notes, 4 gold positives, 4 gold negatives, and 2 gold non-answer cases, suppose
  six automatic answers are correct (3 true, 3 false) and the remaining four notes are abstained.
  Precision is `6/6 = 100%`, PPV is `3/3 = 100%`, recall is `3/4 = 75%`, omissions are
  `2/8 = 25%`, and coverage is `6/10 = 60%`. Recall and omissions are unmet despite perfect
  correctness among answered cases.
- A partition with no gold positives has undefined recall. Zero observed false positives does not
  fill in that missing measurement, and the frozen partition must be retained.

The CORAL documentation says patients were
selected for documented disease progression, tabular staging data, and an available medical
oncology note. State this selection on progression explicitly; having documented progression in
the patient's history does not establish progression at the sampled note's effective time.
The 40-note pilot cannot establish prevalence, clinical validation, deployment safety, or broad
generalization. Require reporting dataset version, manifest hash, split, protocol and adapter
versions, sample counts, exclusions, and expert effort.

- [x] **Step 6: Validate and commit the protocol**

Run:

```bash
rg -n "oncology_current_progression|1.0.0|20260915|95%|85%|15%|60%|0.50|2 expert|25%" \
  docs/protocols/oncology_current_progression-v1.md
git diff --check -- docs/protocols/oncology_current_progression-v1.md
git add docs/protocols/oncology_current_progression-v1.md
git commit -m "M1: define current progression protocol"
```

Expected: every frozen identifier and threshold is present; the three outcome examples distinguish
no relevant evidence, historical-only evidence, and supported current remission. The metric table
and arithmetic examples use the same denominators, and undefined values cannot produce a met
overall result. `git diff --check` prints nothing. Validate these documentation examples directly;
do not add an evaluator or executable clinical acceptance tests in this M1 slice.

---

### Task 2: Add the strict public answer schema

**Files:**

- Create: `src/amber/schemas/answers.py`
- Create: `src/amber/schemas/oncology_current_progression.py`
- Modify: `src/amber/schemas/__init__.py`
- Test: `tests/test_answer_schemas.py`

**Interfaces:**

- Consumes: Pydantic v2 and protocol version `1.0.0` from Task 1.
- Produces: `AnswerModel`, `OncologyCurrentProgressionAnswer`,
  `ONCOLOGY_CURRENT_PROGRESSION_TASK`, `ONCOLOGY_CURRENT_PROGRESSION_PROTOCOL_VERSION`,
  `ONCOLOGY_CURRENT_PROGRESSION_SCOPE`, and `ONCOLOGY_CURRENT_PROGRESSION_EVIDENCE_POLICY`.

- [x] **Step 1: Write failing schema tests**

Create `tests/test_answer_schemas.py` with these cases:

```python
import pytest
from pydantic import ValidationError

from amber.schemas import (
    ONCOLOGY_CURRENT_PROGRESSION_EVIDENCE_POLICY,
    ONCOLOGY_CURRENT_PROGRESSION_PROTOCOL_VERSION,
    ONCOLOGY_CURRENT_PROGRESSION_SCOPE,
    ONCOLOGY_CURRENT_PROGRESSION_TASK,
    OncologyCurrentProgressionAnswer,
)


def test_current_progression_answer_accepts_only_a_required_strict_boolean() -> None:
    answer = OncologyCurrentProgressionAnswer(progression_or_recurrence=True)
    assert answer.model_dump() == {"progression_or_recurrence": True}

    for payload in ({}, {"progression_or_recurrence": 1}, {"progression_or_recurrence": "true"}):
        with pytest.raises(ValidationError):
            OncologyCurrentProgressionAnswer.model_validate(payload)


def test_current_progression_answer_is_frozen_and_forbids_extras() -> None:
    answer = OncologyCurrentProgressionAnswer(progression_or_recurrence=False)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        OncologyCurrentProgressionAnswer(
            progression_or_recurrence=False,
            rationale="not part of the answer",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError, match="frozen"):
        answer.progression_or_recurrence = True  # type: ignore[misc]


def test_current_progression_task_metadata_is_frozen_in_code() -> None:
    assert ONCOLOGY_CURRENT_PROGRESSION_TASK == "oncology_current_progression"
    assert ONCOLOGY_CURRENT_PROGRESSION_PROTOCOL_VERSION == "1.0.0"
    assert ONCOLOGY_CURRENT_PROGRESSION_SCOPE == "note"
    assert ONCOLOGY_CURRENT_PROGRESSION_EVIDENCE_POLICY == "field"
```

- [x] **Step 2: Run the tests to verify the public API is absent**

Run:

```bash
uv run pytest tests/test_answer_schemas.py -q
```

Expected: collection fails because the new exports do not exist.

- [x] **Step 3: Implement the minimal answer models and constants**

Create `src/amber/schemas/answers.py`:

```python
"""Base class for task-specific answer value objects."""

from pydantic import BaseModel, ConfigDict


class AnswerModel(BaseModel):
    """Frozen, closed base for values stored in task claims."""

    model_config = ConfigDict(extra="forbid", frozen=True)
```

Create `src/amber/schemas/oncology_current_progression.py`:

```python
"""Answer contract for the first M1 oncology task."""

from typing import Final, Literal

from pydantic import Field

from amber.schemas.answers import AnswerModel

ONCOLOGY_CURRENT_PROGRESSION_TASK: Final = "oncology_current_progression"
ONCOLOGY_CURRENT_PROGRESSION_PROTOCOL_VERSION: Final = "1.0.0"
ONCOLOGY_CURRENT_PROGRESSION_SCOPE: Final[Literal["note"]] = "note"
ONCOLOGY_CURRENT_PROGRESSION_EVIDENCE_POLICY: Final[Literal["field"]] = "field"


class OncologyCurrentProgressionAnswer(AnswerModel):
    progression_or_recurrence: bool = Field(strict=True)
```

Re-export all six names from `src/amber/schemas/__init__.py` and add them to `__all__`. Do not add
a `Task` model, registry, lookup dictionary, prompt, or import from an interface package.

- [x] **Step 4: Run focused tests and static checks**

Run:

```bash
uv run pytest tests/test_answer_schemas.py -q
uv run ruff check src/amber/schemas tests/test_answer_schemas.py
uv run mypy src/amber/schemas
```

Expected: all commands pass.

- [x] **Step 5: Commit the public schema**

```bash
git add src/amber/schemas/answers.py \
  src/amber/schemas/oncology_current_progression.py \
  src/amber/schemas/__init__.py tests/test_answer_schemas.py
git commit -m "M1: add current progression answer schema"
```

---

### Task 3: Preserve parser completeness and ambiguity diagnostics

**Files:**

- Modify: `experiments/coral/brat.py`, `experiments/coral/audit.py`
- Test: `tests/test_coral_brat.py`, `tests/test_coral_audit.py`

**Interfaces:**

- Consumes: raw BRAT records and unchanged source text through existing `parse_ann`/`read_text`.
- Produces: `ParseDiagnostic`, `Document.diagnostics`, and
  `Document.annotation_inventory_complete`; preserves existing `Document.unparsed` audit records.

- [x] **Step 1: Add raw-record regression tests**

Use pytest temporary files containing invented text and BRAT; parse them through `parse_ann`.
Require diagnostics for truncated entity/attribute records, nonnumeric offsets, duplicate IDs,
duplicate or contradictory attributes for one target/type, unknown records, and dangling targets.
Parameterize each diagnostic test with reversed record order where the syntax permits it.

```python
def test_truncated_entity_cannot_report_complete_inventory(tmp_path):
    ann = tmp_path / "invented.ann"
    ann.write_text("T1\tDiseaseState 0 5\n", encoding="utf-8")
    doc = parse_ann(ann, "alpha")
    assert not doc.annotation_inventory_complete
    assert {item.code for item in doc.diagnostics} == {"malformed_record"}
    assert doc.unparsed


def test_duplicate_id_is_not_a_silent_replacement(tmp_path):
    ann = tmp_path / "invented.ann"
    ann.write_text(
        "T1\tDiseaseState 0 5\talpha\nT1\tDiseaseState 6 10\tbeta\n",
        encoding="utf-8",
    )
    doc = parse_ann(ann, "alpha beta")
    assert not doc.annotation_inventory_complete
    assert "duplicate_id" in {item.code for item in doc.diagnostics}
```

Add Unicode/CRLF byte fixtures, a valid discontinuous annotation, and valid forward references.
Verify copied-quote mismatch classification remains separate from bounds validation. Malformed
input must not print the raw record or expose it through an uncaught exception.

- [x] **Step 2: Verify the parser tests fail for missing diagnostics**

Run `uv run pytest tests/test_coral_brat.py tests/test_coral_audit.py -q`. Expected: new tests fail
because diagnostics and inventory completeness are not implemented.

- [x] **Step 3: Add the diagnostic contract and record validation**

```python
@dataclass(frozen=True)
class ParseDiagnostic:
    code: Literal[
        "malformed_record", "unknown_record", "duplicate_id",
        "duplicate_attribute", "conflicting_attribute", "dangling_reference",
    ]
    record_id: str | None
    line_number: int
```

Add `diagnostics: list[ParseDiagnostic] = field(default_factory=list)` to `Document`.
Its `annotation_inventory_complete` property is true exactly when both `diagnostics` and
`unparsed` are empty. The property describes parse integrity, never human review coverage.

Track unique IDs before dictionary insertion (BRAT `*` relation markers are not unique IDs).
Preserve duplicate/malformed raw records in the restricted in-memory audit; diagnose them and
prevent candidate consumers from treating either conflicting version as authoritative. Validate
references after collecting all records so forward references remain legal. Retain the complete
attribute list; detect repeated `(target, type)` pairs before constructing any dictionary. Exact
duplicates get `duplicate_attribute`; differing values get `conflicting_attribute`.

Catch record-format errors locally, retain their raw audit records, and emit fixed diagnostic
codes rather than exception text containing the record. Keep supported comments/normalizations as
explicitly recognized record types. Do not alter raw files, copied quotes, or source text.
Preserve CORAL's single empty trailing tab field on metadata-only records through the named
`empty metadata tail` adapter policy; nonempty surplus fields remain malformed. Cover this legacy
format with invented fixtures, without copying corpus records or rewriting inputs.
Preserve the parser module's warning that same-length `redacted` mismatches are a surface-mismatch
heuristic, not proof of valid offsets; require independent bounds checks.

- [x] **Step 4: Verify safe audit behavior and commit**

Run:

```bash
uv run pytest tests/test_coral_brat.py tests/test_coral_audit.py -q
uv run ruff check experiments/coral/brat.py experiments/coral/audit.py \
  tests/test_coral_brat.py tests/test_coral_audit.py
git add experiments/coral/brat.py experiments/coral/audit.py \
  tests/test_coral_brat.py tests/test_coral_audit.py
git commit -m "M1: preserve CORAL parser diagnostics"
```

Expected: raw-record regression tests pass. Audit output with `--show 0` contains only aggregate
diagnostic counts, and the existing explicit local audit-export options retain their behavior.

---

### Task 4: Derive non-authoritative CORAL candidates

**Files:**

- Create: `experiments/coral/scripts/coral_current_progression.py`
- Test: `tests/test_coral_current_progression.py`

**Interfaces:**

- Consumes: `Document`, `Entity`, `Attribute`, and parse diagnostics from Task 3 without changing
  raw text. Reject incomplete annotation inventories before deriving a clinical-value seed.
- Produces:
  `derive_current_progression_candidate(doc: Document) -> CurrentProgressionCandidate` and immutable
  dataclasses `CandidateSignal` and `CurrentProgressionCandidate`.

- [ ] **Step 1: Define the immutable candidate result contract in failing tests**

In `tests/test_coral_current_progression.py`, load the experiment script using pytest's scoped
`monkeypatch.syspath_prepend` for `experiments/coral/scripts`. Use invented BRAT files passed
through `parse_ann` for integration cases and constructed values only for isolated reducer tests.
Assert this public shape:

```python
candidate = derive_current_progression_candidate(document)
assert candidate.disposition in {
    "answered",
    "not_mentioned",
    "conflicting_evidence",
    "insufficient_evidence",
}
assert candidate.value is True or candidate.value is False or candidate.value is None
assert all(signal.ann_id.startswith("T") for signal in candidate.signals)
assert "non_authoritative" in candidate.flags
assert "clinical_review_required" in candidate.flags
assert not candidate.clinical_scope_reviewed
assert (candidate.disposition == "answered") == (type(candidate.value) is bool)
```

The dataclasses must be frozen and contain no source text or copied quote:

```python
@dataclass(frozen=True)
class CandidateSignal:
    ann_id: str
    direction: Literal["positive", "negative", "review"]
    reason: str


@dataclass(frozen=True)
class CurrentProgressionCandidate:
    doc_id: str
    disposition: Literal[
        "answered", "not_mentioned", "conflicting_evidence", "insufficient_evidence"
    ]
    value: bool | None
    signals: tuple[CandidateSignal, ...]
    flags: tuple[str, ...]
    annotation_inventory_complete: bool
    clinical_scope_reviewed: Literal[False] = False
```

- [ ] **Step 2: Add parameterized tests for all value/modality rules**

Use helper builders that generate invented text such as `"synthetic progression statement"` and
derive exact offsets from that string. Cover this table:

| Entity/value | Actual BRAT modality | Expected candidate |
|---|---|---|
| `DiseaseState`, `progression-recurrence` | `affirmed` | answered `true`, `temporality_unverified` |
| `DiseaseState`, `stability` or `remission` | `affirmed` | answered `false`, `temporality_unverified` |
| `DiseaseState`, `progression-recurrence` | `negated` | answered `false`, `temporality_unverified` |
| `DiseaseState`, `stability` or `remission` | `negated` | insufficient; negated remission does not prove progression |
| relevant entity alone | `uncertain_in_present` or `uncertain_in_past` | insufficient |
| relevant entity alone | `planned_in_future` or `hypothetical_in_future` | insufficient |
| `DiseaseState`, `hospice`, `progression-others`, or `others`, alone | `affirmed` | insufficient |
| `DiseaseProgression`, `Remission`, or `Hospice`, alone | any | insufficient, `unsupported_entity_type` |
| progression with missing modality | absent | answered `true`, `missing_modality`, `temporality_unverified` |
| `DiseaseState` with `HistoryVal=history` or `current` | any | insufficient, `invalid_attribute_target` |
| no relevant entity | complete annotation inventory | not mentioned, `annotation_absence_only`; clinical scope remains unreviewed |

Also assert that a qualifying positive plus a qualifying negative returns conflict with both
annotation IDs, regardless of entity order.

Add mixed-signal tests: affirmed remission plus an `uncertain_in_past` progression signal retains
the negative seed and historical warning; affirmed progression plus a future-plan signal retains
the positive seed and warning. Add a synthetic historical statement whose annotation is merely
`affirmed`: its candidate must retain `temporality_unverified` because the adapter cannot infer
historical/current applicability from that attribute. A temporal relation to a `Datetime` entity
adds `temporal_relation_unresolved`; no date parsing or temporal-resolution claim is made.

- [ ] **Step 3: Add adversarial scope and span tests**

Create invented cases for each of the following and expect `insufficient_evidence` plus a stable
flag: out-of-bounds span (`malformed_span`), reversed/empty span (`malformed_span`), discontinuous
relevant span (`discontinuous_span`), relevant span overlapping `SectionSkip` (`skipped_overlap`),
malformed `SectionSkip` (`incomplete_scope`), missing `DiseaseStateVal` (`missing_disease_state`),
and family/other experiencer alone (`non_patient_experiencer`). Parse malformed, duplicate,
conflicting-attribute, and dangling-reference BRAT through Task 3 and assert every such document
produces insufficient evidence with `incomplete_annotation_inventory`, never not mentioned.

Test source-surface mismatches independently of bounds: valid bounds with a copied-quote mismatch
retain the mismatch classification and block a seed with `source_surface_mismatch`; the `redacted`
heuristic never certifies span validity. Apply that blocker to relevant disease-state or skip
annotations, not unrelated entity types. Any invalid relevant interval or unresolved skip blocks
a candidate answer. Warnings for an otherwise well-formed other-experiencer/past/future signal do
not suppress a usable seed.

Add one Unicode/CRLF case whose computed end-exclusive offsets select exactly the invented source
substring. Add a case with a valid relevant span adjacent to, but not overlapping, a skip region;
it must retain its normal answer. Overlap is `a_start < b_end and b_start < a_end`, so touching
boundaries do not overlap.

- [ ] **Step 4: Run tests to verify the candidate module is absent**

Run:

```bash
uv run pytest tests/test_coral_current_progression.py -q
```

Expected: collection fails because `coral_current_progression` does not exist.

- [ ] **Step 5: Implement annotation indexing and span/scope validation**

In `coral_current_progression.py`, define private helpers named `_attributes_by_target`,
`_valid_intervals`, `_overlaps`, `_skip_intervals`, and `_classify_entity`. Their exact interfaces
are, respectively, `Document -> dict[str, dict[str, str | None]]`,
`(Entity, int) -> bool`, two `(int, int)` intervals to `bool`,
`Document -> (tuple of intervals, scope-complete bool)`, and
`(Entity, attribute mapping) -> (positive/negative/review/ignore, reason, flags)`.

Here the skip helper's boolean means skip-boundary integrity, not clinical review completeness.
Only call `_attributes_by_target` after the diagnostics gate has rejected duplicate or conflicting
attributes. Validate candidate-specific attribute names, allowed values, and target types against
the explicit CORAL mapping; unknown modality must not fall back to affirmed.

`_valid_intervals` accepts only ordered nonempty spans satisfying
`0 <= start < end <= len(doc.text)`. `_skip_intervals` keeps fragments separate and reports scope
incomplete if any skip fragment is invalid. Never widen a skip region or normalize source text.

- [ ] **Step 6: Implement the deterministic rule precedence**

`derive_current_progression_candidate` must:

1. reject incomplete annotation inventories and structurally unsafe evidence/skip intervals with an
   insufficient candidate before using any polarity seed;
2. classify `DiseaseState` annotations according to the table; preserve the three legacy entity
   names as unsupported review signals, and report missing/invalid required attributes;
3. treat missing/invalid required attributes, relevant source-surface mismatches, discontinuous
   relevant spans, and skipped overlaps as blockers alongside parse/bounds errors. Missing modality
   is the explicit allowed assumption, not a missing-required-attribute error. Blockers prevent an answer;
   well-formed past/future/uncertain/hospice/non-patient signals produce warnings and do not vote;
4. propose conflict when both positive and negative usable seeds exist; otherwise propose the
   single available direction while retaining every semantic warning and temporal-assumption flag;
5. propose insufficient when only warning signals exist; and
6. propose not mentioned only when the annotation inventory and skip boundaries are intact and
   no relevant signals exist. Add `annotation_absence_only`, never set clinical scope reviewed.

Sort signals by `ann_id` and flags lexicographically so results do not depend on dictionary or BRAT
record order. Include `non_authoritative` and `clinical_review_required` in every result. Assert
that non-answered dispositions have `value=None`, answered values are strict booleans, and parse
failures cannot be marked complete. Do not import `Claim`, `Example`, or gold schema types.

- [ ] **Step 7: Run focused candidate tests and lint**

Run:

```bash
uv run pytest tests/test_coral_current_progression.py -q
uv run ruff check experiments/coral/scripts/coral_current_progression.py \
  tests/test_coral_current_progression.py
```

Expected: all candidate rules and adversarial cases pass.

- [ ] **Step 8: Commit the candidate derivation**

```bash
git add experiments/coral/scripts/coral_current_progression.py \
  tests/test_coral_current_progression.py
git commit -m "M1: derive CORAL progression candidates"
```

---

### Task 5: Generate a restricted local manifest and frozen split

**Files:**

- Create: `experiments/coral/scripts/coral_current_progression_manifest.py`
- Test: `tests/test_coral_current_progression_manifest.py`

**Interfaces:**

- Consumes: `parse_ann`, `read_text`, and `derive_current_progression_candidate`.
- Produces:
  `sha256_file(path: Path) -> str`,
  `allocate_candidate_quotas(positive_counts: Mapping[str, int]) -> dict[str, tuple[int, int, int]]`,
  `assign_document_splits(rows: Sequence[ManifestInput], seed: int = 20260915) -> dict[str, str]`,
  `build_manifest(root: Path) -> dict[str, object]`, and a Click command that creates or verifies
  one immutable JSON manifest. No rebalancing or overwrite mode is included.

- [ ] **Step 1: Write failing hash and split tests**

Create 40 synthetic `ManifestInput` records: 20 breast and 20 pancreatic, each with a unique
`coral_idx`, and with both positive and non-positive pre-adjudication candidates in each cancer
type. Assert:

```python
first = assign_document_splits(rows, seed=20260915)
second = assign_document_splits(tuple(reversed(rows)), seed=20260915)
assert first == second
assert Counter(first.values()) == {"train": 20, "dev": 10, "test": 10}
assert set(first) == {row.group_id for row in rows}
```

Assert exactly 10/5/5 documents for each cancer type, no document appears in two splits, and a
changed seed changes assignments for this synthetic pool. Reject duplicate document IDs,
`coral_idx`, or `group_id` before allocation. CORAL v1.0 has one sampled note per patient; supporting
multiple documents per patient is outside this fixed 40-document splitter, and rejection prevents
leakage. Reject non-40-document or non-20-per-cancer inputs rather than relaxing frozen counts.

Add the review regression: two positive and eighteen other candidates in each cancer type must
retain 10/5/5 cancer margins and global positive counts 2/1/1. Test every pair of positive counts
from 0 through 20 across the two cancer types, including empty and singleton strata. Assert quotas
are feasible, meet both margins, and produce the minimum specified integer objective. Permute
document order and stratum insertion order; the full document-to-split map must be identical.

For `sha256_file`, write invented CRLF and Unicode bytes and compare its result with
`hashlib.sha256(raw_bytes).hexdigest()`.

- [ ] **Step 2: Write failing manifest safety tests**

Build a temporary invented dataset tree containing `.txt`/`.ann` pairs,
`subject-info.csv`, and `annotation.conf`. Exercise the Click command and assert the JSON contains:

```json
{
  "dataset": {"name": "CORAL", "version": "1.0", "doi": "10.13026/v69y-xa45"},
  "sensitivity": "deidentified",
  "task": "oncology_current_progression",
  "protocol_version": "1.0.0",
  "adapter_version": "current-progression-candidate-1.0.0",
  "split_seed": 20260915,
  "split_counts": {"train": 20, "dev": 10, "test": 10}
}
```

Each document entry may contain only `doc_id`, `coral_idx`, `cancer_type`,
`progression_candidate`, `candidate_disposition`, `split`, and relative `.txt`/`.ann` paths plus
SHA-256 hashes. Assert recursively that keys named `text`, `quote`, `spans`, `start`, `end`,
`attributes`, or `adjudication` are absent. Capture stdout and assert invented note text is absent.

Assert the CLI rejects output paths outside resolved `experiments/coral/data/manifests/` and
`experiments/coral/outputs/`, paths under any input tree or `data/raw/`, symlinks into those trees,
aliases of input files, and non-JSON destinations. Assert it rejects orphan files, duplicate IDs,
unknown cancer types, missing subject rows, non-unique `coral_idx`, and any discovery beneath
`unannotated/`. Use a temporary experiment tree via monkeypatching the module's project-root
constant so tests never write into the real dataset directories.

Freeze tests must prove all of the following:

- identical reruns succeed without changing the existing manifest bytes or modification time;
- changed `.txt`, `.ann`, subject/config hashes, candidate values, protocol/adapter versions, seed,
  or split membership fail without changing the existing artifact;
- a corrupt manifest or a manifest with an invalid self-hash is rejected without replacement;
- all raw inputs retain their original byte hashes after every rejected write;
- creation racing with another writer cannot overwrite the first valid artifact; and
- an interrupted write never leaves a partial manifest at the final destination.

- [ ] **Step 3: Run tests to verify the manifest module is absent**

Run:

```bash
uv run pytest tests/test_coral_current_progression_manifest.py -q
```

Expected: collection fails because the manifest module does not exist.

- [ ] **Step 4: Implement byte hashes and manifest input discovery**

Define an immutable input record:

```python
@dataclass(frozen=True)
class ManifestInput:
    doc_id: str
    group_id: str
    coral_idx: str
    cancer_type: Literal["breast", "pancreatic"]
    progression_candidate: bool
    candidate_disposition: str
    txt_path: Path
    ann_path: Path
```

Hash files by reading bytes in chunks. Discover only paired files below the supplied annotated
root. Read `subject-info.csv` with `csv.DictReader`, explicitly map each document to `coral_idx`,
and fail closed on missing or duplicate identities. The documentation specifies that `coral_idx`
matches the note filename without its extension; preserve it as a string and use it as `group_id`.
Map immediate corpus directories explicitly: `breastca -> breast`, `pdac -> pancreatic`. Reject
unknown directories instead of inferring cancer type from note text. Treat only `answered true`
as `progression_candidate=True`; all other pre-adjudication outcomes are the other stratum, never
a clinical-negative label. Report aggregate warning/diagnostic counts with these candidate counts.

- [ ] **Step 5: Implement stable stratified 20/10/10 assignment**

Validate one document per unique `group_id` and twenty patients per cancer before allocation.
Use explicit orders: cancer types `("breast", "pancreatic")`, candidate strata `(True, False)`,
and splits `("train", "dev", "test")`. Per-cancer capacities are `(10, 5, 5)`.

Compute all quotas before assigning any document. For a cancer with `P` positive candidates,
enumerate integer triples `(t, d, e)` satisfying `t + d + e == P` and componentwise bounds
`(0, 0, 0) <= (t, d, e) <= (10, 5, 5)`. Other-candidate quotas are the capacity minus that triple.
Choose the pair of cancer-specific triples using this complete deterministic objective:

```python
import itertools
from collections.abc import Mapping


def allocate_candidate_quotas(
    positive_counts: Mapping[str, int],
) -> dict[str, tuple[int, int, int]]:
    cancers = ("breast", "pancreatic")
    capacities = (10, 5, 5)
    if set(positive_counts) != set(cancers):
        raise ValueError("exactly breast and pancreatic counts are required")
    if any(type(count) is not int or not 0 <= count <= 20 for count in positive_counts.values()):
        raise ValueError("candidate counts must be integers from 0 through 20")

    def options(count: int) -> list[tuple[int, int, int]]:
        return [
            (train, dev, count - train - dev)
            for train in range(11)
            for dev in range(6)
            if 0 <= count - train - dev <= 5
        ]

    def loss(quota: tuple[int, ...], count: int) -> int:
        return sum(
            (4 * q - weight * count) ** 2
            for q, weight in zip(quota, (2, 1, 1), strict=True)
        )

    def objective(pair: tuple[tuple[int, int, int], ...]) -> tuple[int, int, tuple[int, ...]]:
        total = tuple(sum(q[i] for q in pair) for i in range(3))
        global_loss = loss(total, sum(positive_counts.values()))
        cell_loss = 0
        for cancer, positive in zip(cancers, pair, strict=True):
            count = positive_counts[cancer]
            other = tuple(capacities[i] - positive[i] for i in range(3))
            cell_loss += loss(positive, count) + loss(other, 20 - count)
        return global_loss, cell_loss, tuple(value for quota in pair for value in quota)

    pairs = itertools.product(*(options(positive_counts[cancer]) for cancer in cancers))
    selected = min(pairs, key=objective)
    return dict(zip(cancers, selected, strict=True))
```

The objective first balances global candidate counts, then the four cancer/candidate cells,
then resolves remaining ties lexicographically. Integer
arithmetic avoids floating-point tie ambiguity; the bounded enumeration requires no solver.

Sort groups inside each stratum by this key, with `group_id` as a secondary tie-breaker:

```python
hashlib.sha256(f"{seed}\0{group_id}".encode()).hexdigest()
```

Slice each sorted stratum into train/dev/test using its precomputed quotas. Assert exact 20/10/10
global counts, 10/5/5 per-cancer counts, unique complete membership, and candidate cell counts
matching the selected quotas. Record quota counts and split-policy version
`current-progression-split-1.0.0` in the manifest. Never recompute membership to improve balance
after adjudication or after a manifest has been frozen.

- [ ] **Step 6: Implement safe JSON generation and CLI output**

Build a canonical JSON-compatible dictionary with sorted document entries. Use UTF-8,
`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`, and `allow_nan=False` for the
canonical bytes used for hashing/comparison; presentation output may use `indent=2` and a final
newline. Include SHA-256 for every `.txt`, `.ann`, `subject-info.csv`, and
`annotation.conf`, plus a manifest hash computed from the canonical payload before adding the hash
field. Include aggregate candidate/split/stratum counts, never annotations or source slices.

Expose:

```text
uv run python experiments/coral/scripts/coral_current_progression_manifest.py \
  experiments/coral/data/raw/annotated \
  --output experiments/coral/data/manifests/oncology-current-progression-v1.json
```

Resolve the output and require a `.json` destination beneath `data/manifests/` or `outputs/` for
this experiment, outside every input tree. Reject symlink escapes and input-file aliases before
creating directories or opening a file for writing.

If the destination exists, validate its self-hash, required fields, identities, versions, input
hashes, quotas, and partition membership. Compare its canonical payload with the proposed payload.
An identical rerun reuses it without any write; every difference or invalid existing file is an
error. No `--force`, truncate, replace, or automatic resplit is allowed. Document that the selected
manifest is the freeze authority: changing its filename is not permission to repartition exposed
patients. Successor versions require an explicit future migration that retains known assignments
and links the original manifest; implementing that migration is outside this slice.

For first creation, write and flush a temporary file in the destination directory, then publish
atomically without replacement (for example, `os.link` to the final path followed by temporary-file
cleanup). If another writer wins, apply the existing-file validation rule. Never use an
unconditional `os.replace` over the destination. Print success only after the complete artifact is
durable. Clean up only the temporary file created by this invocation.

Print only the output path, manifest hash, document count, split counts, and aggregate candidate
or diagnostic counts. Never print annotations, source text, quotes, offsets, or raw exception
messages. Input/parse/write failures produce a nonzero exit and a fixed safe error category.

- [ ] **Step 7: Run focused manifest tests and lint**

Run:

```bash
uv run pytest tests/test_coral_current_progression_manifest.py -q
uv run ruff check experiments/coral/scripts/coral_current_progression_manifest.py \
  tests/test_coral_current_progression_manifest.py
```

Expected: all quota, ordering, leakage, malformed input, hashing, immutable-rerun, and output-safety
tests pass.

- [ ] **Step 8: Commit the manifest tooling**

```bash
git add experiments/coral/scripts/coral_current_progression_manifest.py \
  tests/test_coral_current_progression_manifest.py
git commit -m "M1: freeze CORAL progression split tooling"
```

Do not add the generated manifest.

---

### Task 6: Document the local workflow and verify the vertical slice

**Files:**

- Modify: `experiments/coral/README.md`

**Interfaces:**

- Consumes: protocol, schema, parser diagnostics, candidate derivation, and manifest command from
  Tasks 1–5.
- Produces: a safe operator workflow and verification record; no dataset-derived tracked artifact.

- [ ] **Step 1: Update the CORAL experiment README**

Describe the new answer schema and task-specific candidate/manifest commands, linking the protocol.
Retain the warning that the broader `coral_adapter.py` still depends on unfinished domain schemas;
these task-specific commands do not complete that adapter. Explain that:

- candidate labels are non-authoritative and cannot create gold;
- affirmed disease-state annotations do not establish currentness, and annotation inventory
  completeness does not establish full clinical review;
- the command reads only the 40 expert-labeled annotated notes;
- the manifest is restricted local metadata and remains ignored;
- the first successful manifest freezes membership; identical reruns verify it, and changed inputs
  or policies fail without overwriting it or silently creating a replacement split;
- normal output contains counts and hashes but no clinical text, quotes, offsets, or annotations;
- two-reviewer plus third-reviewer adjudication is a future human workflow, not implemented here;
  and
- extraction, APIs, persistence, model evaluation, and automatic gold creation remain out of scope.

- [ ] **Step 2: Run focused schema and CORAL tests**

```bash
uv run pytest tests/test_answer_schemas.py \
  tests/test_coral_brat.py tests/test_coral_audit.py \
  tests/test_coral_current_progression.py \
  tests/test_coral_current_progression_manifest.py -q
```

Expected: all tests pass using invented data only.

- [ ] **Step 3: Run the full offline quality suite**

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
git diff --check
```

Expected: every command exits zero. If formatting changes are required, format only the changed
Python paths and rerun the complete list.

- [ ] **Step 4: Run the restricted aggregate CORAL integration checks**

First run the existing no-span audit exactly as follows:

```bash
uv run python experiments/coral/scripts/coral_ingest.py \
  experiments/coral/data/raw/annotated --show 0
```

Then generate the local manifest:

```bash
uv run python experiments/coral/scripts/coral_current_progression_manifest.py \
  experiments/coral/data/raw/annotated \
  --output experiments/coral/data/manifests/oncology-current-progression-v1.json
```

Expected: 40 documents are reported; no example spans or source text are printed; split counts are
20/10/10 globally and 10/5/5 per cancer type. Repeat the manifest command once and verify that the
existing artifact is reused byte-for-byte with unchanged modification time. If real corpus
diagnostics prevent completion, report the safe aggregate failure and keep the policy intact;
never weaken validation to produce the expected counts. Record only aggregate counts and command
status in the handoff. Do not paste corpus annotations, text, offsets, document metadata, or local
manifest contents into commits or shared logs.

- [ ] **Step 5: Prove generated restricted artifacts are untracked**

```bash
git check-ignore -v \
  experiments/coral/data/manifests/oncology-current-progression-v1.json
git status --short
```

Expected: `.gitignore` matches the manifest. Status shows only the planned source, test, and
documentation changes, plus the user's pre-existing untracked `.omx/` and `docs/slides/` content.

- [ ] **Step 6: Commit the workflow documentation**

```bash
git add experiments/coral/README.md
git commit -m "M1: document CORAL progression preparation"
```

## Completion criteria

- The protocol contains all answer/outcome rules, gold review requirements, frozen split details,
  pilot gates, explicit denominators, point-estimate versus interval semantics, inconclusive-result
  rules, and selection on documented progression. Not mentioned requires no relevant evidence;
  historical-only evidence remains insufficient.
- The public schema accepts only a required strict boolean and is frozen/extra-forbidden.
- Parser diagnostics prevent missing or contradictory records from masquerading as complete
  inventory. Candidate warnings remain distinct from clinical decisions and carry explicit
  temporal assumptions and unreviewed-scope status; no gold is created.
- Split generation is order-independent and document/patient-safe, with exact 20/10/10 global and
  10/5/5 per-cancer counts and globally optimized candidate quotas.
- Manifests are byte-hashed and immutable; identical reruns preserve the file, incompatible reruns
  fail, and writes cannot touch raw inputs or escape the allowed ignored output directories.
- All repository checks pass and the restricted local integration emits aggregate output only.
- No model, API, database, prompt, extraction runtime, annotation record, or clinical data is added.
