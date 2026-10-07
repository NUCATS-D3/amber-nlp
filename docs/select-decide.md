# Select–decide: inputs-only inference and the `decide` capability

Status: proposal, 2026-09-17; authority clarified during consolidation on 2026-10-07. Nothing here
is implemented or adopted into the binding contract. Read the
[evaluation](select-decide-evaluation.md) before using the proposed APIs or milestone changes;
it identifies required revisions. The [canonical roadmap](04-roadmap.md) retains M1–M3's scope.

## Motivation

- **Selection-Inference (SI)** (Creswell, Shanahan, Higgins, ICLR 2023) alternates two steps.
  - The *selection* step picks facts from the context by scoring existing units, not by generating text.
  - The *inference* step derives a conclusion while seeing only the selection, so the reasoning trace is causal rather than decorative.
  - The paper's diagnostic study found LLM reasoning degrades with distractor facts, with facts that must come from memory, and with more steps.
- **The prospective gap.** Amber currently has no extraction/model-call pipeline. In the planned
  full-note baseline, cited `InferenceEvidence.inputs` would not by itself establish what context
  the model received. The proposed comparison tests a controlled evidence-restricted request
  builder; it cannot establish entailment merely by recording a context-policy flag.
- **TypeSafe Jev** (System One models, launched 2026-09-15) returns typed decisions with probabilities and confidence instead of text. There are three question types: Choice, Score, and Noul (yes/no).
  - Its "line-by-line search" pattern is a Choice over line IDs, which is SI selection.
  - Its "citation check" pattern is a Choice (supports / contradicts / says nothing) over a claim plus its cited context, which is inputs-only verification.
  - The interface is worth adopting. The vendor is not a dependency (see Zones).

## Principles (add to goals doc)

1. **Inputs-only inference.** A model call that produces an inference step receives only that step's declared inputs, plus task instructions and the answer schema. `InferenceEvidence.inputs` is then the complete context of the step.
2. **Select, don't generate, where units exist.** A selected unit ID mints an exact `Inclusion` deterministically. `quote` remains the minter for sub-unit spans.
3. **Background knowledge must be citable.** Knowledge the note does not state becomes `StructuredEvidence` from a terminology source rather than uncited model knowledge. Examples: drug → indication, finding → condition, guideline thresholds.
4. **Vendor-neutral decisions.** Typed decisions go through a provider capability. No pipeline code depends on a specific vendor.

## Spec changes

### §2 Sources

- Add `SourceKind.terminology`. Rows are relations such as `RxNorm:may_treat` or `SNOMED:is_a`, loaded from local terminology tables.
- Add `Unit`, the selectable segment of a text source (a sentence or line):

```python
class Unit(BaseModel):
    unit_id: str              # sha256(source_id, start, end)
    source_id: str
    start: int; end: int
    section_category: str | None = None
    is_template: bool = False
```

### §5 Evidence

- Add `"selected"` to `Inclusion.alignment` (score 1.0). There are now three minters of `Inclusion`: `quote`, `select`, and a human in the app.
- Widen `InferenceEvidence.rationale` from `str` to `str | DecisionRecord`:

```python
class DecisionRecord(BaseModel):
    kind: Literal["choice", "score", "noul"]
    question: str
    options: dict[str, str | None] | None      # choice options / score levels / noul criteria
    answer: str | float | bool
    probabilities: dict[str, float] | None
    confidence: float | None
    provider: str
    calibration: str | None = None             # artifact id of the calibration map used, if any
```

- Add a field to `InferenceEvidence` that records whether principle 1 held for this step:

```python
context_policy: Literal["inputs_only", "full_source"]
```

  Steps built on `decide` are `inputs_only`. Generative extractor steps are `full_source` until they are migrated.

### §7 Providers and zones

- Add `"decide"` to the provider capability flags, and add `max_options: int | None` to `Provider`.
- Define the request and answer types for `decide`:

```python
class ChoiceQ(BaseModel):
    kind: Literal["choice"] = "choice"
    instructions: str
    options: dict[str, str | None]             # id -> description; len <= provider.max_options

class ScoreQ(BaseModel):
    kind: Literal["score"] = "score"
    instructions: str
    levels: list[str]                          # 2-10 ordered descriptions

class NoulQ(BaseModel):
    kind: Literal["noul"] = "noul"
    instructions: str
    true: str
    false: str

Question = Annotated[ChoiceQ | ScoreQ | NoulQ, Field(discriminator="kind")]

class DecideRequest(BaseModel):
    state: str | dict[str, Any] | list[Any]
    questions: dict[str, Question]

class DecideAnswer(BaseModel):
    kind: Literal["choice", "score", "noul"]
    value: str | float | bool
    probabilities: dict[str, float] | None
    confidence: float | None

def decide(provider: Provider, request: DecideRequest) -> dict[str, DecideAnswer]: ...
```

Backends:

| backend | zone | notes |
|---|---|---|
| `logprob` (default) | local / institution | vLLM, mlx-lm, or transformers. Options get single-token labels. Probabilities are the next-token distribution restricted to those labels. Calibration uses temperature scaling fit on the dev split and is stored as an MLflow artifact. `confidence` is the calibrated max probability. |
| `typesafe` (Jev) | external | Hosted API only, closed weights, early access. No self-host path or BAA found as of 2026-09-17. Choice is capped at 255 options. Vendor calibration and benchmarks are self-reported. |
| `llm_emulated` | per endpoint | A hosted LLM with structured output that is asked to report probabilities. Uncalibrated; use for development only. |

Policy: under the default policy, `typesafe` is allowed for `synthetic` data only. It would need a BAA to move to `external_baa`.

### §8 Tools

```python
def units(source_id, section: str | None = None) -> list[Unit]

def select(source_id, question: str, within: list[str] | None = None,
           k: int = 3, min_prob: float = 0.2) -> SelectResult
    # One decide call:
    #   - Choice over unit ids (the "where" question)
    #   - Noul "does any unit answer this?" (the answerability question)
    # If there are more units than max_options, run two passes: section/window first, then units within it.
    # Multi-span: take the top-k units; keep a unit if a per-candidate Noul (or its Choice mass) >= min_prob.
    # Returns Inclusions (alignment="selected") and the answerability probability.

def decide_field(task, field, evidence_ids) -> DecisionRecord
    # The state is the quotes/rows of evidence_ids only, plus the field's instructions.
    # The pydantic field type determines the question:
    #   Enum / Literal -> Choice
    #   bool -> Noul
    #   Annotated ordinal -> Score
    # Numeric, date, and free-text fields: dates() / value parsers / regex generate candidates
    # from the selected units, then a Choice picks among them.

def verify(claim_id) -> DecisionRecord
    # Choice {supports, contradicts, insufficient}.
    # The state is the claim value plus its cited evidence only.

def terminology(concept: Concept, relation: str) -> list[StructuredEvidence]
```

- `commit_claim` accepts a `DecisionRecord` as the rationale and sets `context_policy`.

### §9 Cascade and agents

Add a select–decide path to the deterministic cascade, ahead of agent escalation:

1. Run `sections`, then `dedupe`, then `units`.
2. For each field, run `select`.
   - If the answer is not answerable, the field becomes a `no_claim` candidate.
   - Otherwise, run `decide_field` on the selected inclusions.
3. Run `commit_claim` with the `DecisionRecord`.
4. Run `verify`.

Escalate to the reviewer when any of these hold:

- answerability falls in the ambiguous band;
- decision confidence is below threshold;
- `verify` does not return `supports`;
- sections conflict;
- the field type is not decidable.

Thresholds are config, logged as run params, and tuned on dev.

The reviewer's reconciliation step uses `decide` over the conflicting claims, with inputs set to exactly those claims.

### §11 Evaluation

Add these scorers:

- `inputs_only_rate`: the share of `InferenceEvidence` with `context_policy == "inputs_only"`.
- `selection_recall@k` against gold spans, and answerability AUROC against gold no-claim cases.
- Calibration (ECE and reliability curves) per provider and field. This is required before a confidence threshold is used outside dev.
- `verify_agreement` with the human faithfulness sample. This is what calibrates `verify` as the implementation of `evidence_faithful`.
- `escalation_rate` as a function of threshold (the cost curve).

## Roadmap deltas

- **M1:** `Unit`, `DecisionRecord`, `alignment="selected"`, `context_policy`, `SourceKind.terminology`.
- **M2:** `units` tool; select–decide path in the cascade on the `logprob` backend.
- **M3:** the `decide` capability; the `logprob` backend with calibration; `typesafe` behind `zone=external`; `llm_emulated`.
- **M4:** comparison on the synthetic tasks of three approaches:
  - the generative extractor;
  - select–decide on the local backend;
  - select–decide on Jev.

  Measure label accuracy, grounding-failure rate, calibration, latency, and cost.
- **M8:** the verifier starts as the `verify` tool; a second-model verifier comes later.

## Open questions

- **Unit granularity.** Should units be sentences or lines? Clinical notes are line-structured (lists, key: value pairs, flowsheet dumps).
- **Multi-span selection.** Per-candidate Noul or a Choice mass threshold?
- **Logprob calibration.** How well calibrated is label-token scoring on small local models, and is temperature scaling enough?
- **Candidate generation.** How far can it go for numeric, date, and free-text fields before falling back to the generative extractor?
- **Jev.** Is there a BAA or self-host roadmap? Is there independent calibration evidence? What are PhysioNet's rules for sending MIMIC data through an external service?

## References

- Creswell, Shanahan, Higgins. Selection-Inference: Exploiting Large Language Models for Interpretable Logical Reasoning. ICLR 2023. https://openreview.net/forum?id=3Pf3Wg6o-A4
- Creswell, Shanahan. Faithful Reasoning Using Large Language Models. 2022 (follow-up that adds a halter and search).
- TypeSafe System One docs: https://docs.typesafe.ai/concepts/system-one
- Line-by-line search cookbook: https://docs.typesafe.ai/cookbooks/semantic_find.md
- Citation check cookbook: https://docs.typesafe.ai/cookbooks/citation_check.md
- Pre-parsed value extraction cookbook: https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook.md
- Deployment caveats (hosted only, closed weights): https://www.modemguides.com/blogs/ai-news/jev-typesafe-reality-check-run-locally
