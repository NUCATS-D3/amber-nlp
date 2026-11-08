# Compiled Clinical IE: TL;DR

A short summary of [compiled-clinical-ie.md](compiled-clinical-ie.md) (revision 4). It is a research proposal, not a contract. The work happens in lapidary (experiments), copal (rule engine), and loupe (review), and every artifact it produces must conform to amber contracts. Section links (§) point into the full document.

## The idea in one paragraph

Use an LLM at build time to write clinical extraction rules, then run the rules without the
LLM. Experts first write a guideline and label a small gold set. GEPA, an LLM-driven
evolutionary optimizer, then asks an LLM for one small edit to a copal rule set at a time. It
keeps the edits that score better against gold. The rules that come out are deterministic, run
on CPU inside the PHI boundary, label the enterprise data warehouse (EDW) at scale, and supply
training labels for a small model. copal rules fire on exact spans and feed higher-level rules,
so every answer comes with the record of how it was reached. That record (its *derivation*) is
an amber evidence graph, which means grounding comes by construction.

The closest prior work is EvoPool (2026), which evolves labeling functions against a validation
set and distills them into a model. In short, this proposal is EvoPool for clinical spans, with
clinician notes as feedback, compiled into a hierarchical rule language.

## Terms

| Term | Meaning |
|---|---|
| copal | Rust rule engine in the medspaCy/ConText tradition: span matchers, modifiers, rule composition |
| GEPA | Reflective evolutionary optimizer: evaluate a candidate, show the errors to an LLM, take its proposed change |
| Teacher | The LLM extraction program (DSPy extractor plus reviewer) |
| Student | A small distilled span tagger, such as BioClinical ModernBERT |
| Gold / silver | Expert-reviewed references / unreviewed references from the teacher or rules |
| dev / test | Gold splits. Evolution is scored on dev; test is never seen by any optimizer |
| Fitness | The score evolution maximizes |
| Cross-fitted teacher | A teacher tuned without the dev note it is labeling, so its output cannot leak that note's gold |
| L1–L4 | Rule levels: L1 mentions, L2 modifiers (assertion, temporality), L3 note-level answer, L4 patient-level roll-up |
| Abstain / silent | The rules found the target but explicitly decline to answer, citing a span and reason / the rules found no target |
| IAA | Inter-annotator agreement, the ceiling for every F1 |

## An example

A synthetic note ([§6.1](compiled-clinical-ie.md#61-hierarchical-rules-as-evidence-graphs)):

> `Prior echo (2019): LVEF 55%. Today: LVEF estimated at 30-35%.`

- **L1** finds two EF mentions, 55 and 30–35.
- **L2** marks the first as historical, using the cue `Prior echo (2019)`.
- **L3** takes the current EF and answers *EF category = reduced*.

Each step is recorded, so the claim traces back to exact spans in the note.

## Principles

- **Expert effort comes first:** a guideline, a scoring protocol, and a small gold set.
- **Gold is the only fitness signal.** Teacher silver supplies feedback and training volume,
  never fitness.

## Claims

The pipeline shape (LLM → programmatic labels → distilled model) already exists, so the
novelty claims are narrow ([§7](compiled-clinical-ie.md#7-novelty-claims)). The headline is
N1 to N3.

| Claim | Question | Phase |
|---|---|---|
| N1 Span-level evolution | Do evolved span rules, with assertion and temporality, beat one-shot LLM rules and engineer-written rules? | 1 |
| N2 Expert reasoning as signal | Is a free-text clinician note worth more than the same time spent labeling? | 1 |
| N3 Hierarchical rule language | Do hierarchical rules, whose derivations clinicians review, beat flat rules or Python? | 2 |
| N7 Hybrid student | Does a student for L1/L2 plus copal for L3 beat either alone? | 2 |
| N4 EHR shift | Do silent and abstention rates catch drift early, and how cheap is recompiling? | 3 |
| N5 Patient roll-up | Can L4 rules match an LLM reading the full record? (Needs a patient-level contract) | 3 |
| N6 Economics | How much accuracy survives compilation, and at what cost? | Throughout |

## Pipeline

| Stage | What happens |
|---|---|
| 0 Define | Experts fix the schema, guideline, scoring protocol, abstain semantics, and a template for their notes |
| 1 Gold | dev: experts correct teacher pre-annotations and write timed notes. test: labeled from scratch and frozen |
| 2 Teacher | The teacher is tuned with GEPA on dev. Cross-fitted copies supply the teacher evidence the compiler sees |
| 3 Compile | GEPA edits one level of an `{L1, L2, L3}` rule set at a time. After each edit, deterministic clean-up prunes, deduplicates, enforces a precision floor, and rejects note-specific literals. Each score is F0.5 (precision weighted twice as heavily as recall) over a shard of about five notes |
| 4 Label | The best rule set runs over the EDW. Abstentions and a sample of silent notes go to the teacher; the most informative notes go to experts as new dev gold |
| 5 Distill | The student takes over finding mentions (L1/L2); copal keeps combining them (L3), so every claim stays replayable |
| 6 Evaluate | Every artifact is scored on test gold. Silent and abstention rates are monitored and trigger new gold and recompilation |

A silent output on a note that has a gold target counts as a false negative, never as an
abstention ([§6.2](compiled-clinical-ie.md#62-explicit-abstention)).

## Phases and gates

Each gate is preregistered, so a weak result redirects the work
([§8](compiled-clinical-ie.md#8-phasing-and-decision-gates)).

| Phase | Scope | Gate to continue |
|---|---|---|
| 0 | copal Python bindings, rule-set text format, explicit abstain; scoring protocol; IAA pilot | All work on synthetic notes, and IAA meets its threshold |
| 1 (paper 1) | N1 and N2, flat copal rules only | N1: evolved rules beat the better baseline by more than about 2 F1. N2: expert notes beat shuffled notes and guideline excerpts at matched hours |
| 2 (paper 2) | N3 with a clinician reviewability study; N7 | Hierarchical copal with derivation output exists |
| 3 | N4, N5, N6 at scale | A patient-level contract is adopted, or N5 stays in lapidary |

## Paper 1

Details in [§9](compiled-clinical-ie.md#9-paper-1-design).

- **Tasks:** LVEF value and category (near the ceiling for simple rules, so the easy anchor),
  and HF symptoms with negation, temporality, and experiencer (the hard task).
- **Corpora:** EDW notes, plus one shareable credentialed corpus such as MIMIC.
- **Gold:** about 300 notes per task per corpus, split into dev (~150) and test (~150), plus an
  unlabeled reserve pool for matching expert-hours.
- **Baselines:** the teacher (B1), engineer-written rules (B2), one-shot LLM rules (B3), and
  EvoPool (B4).
- **Evolved arms.** Each arm changes what feedback the LLM sees about errors:

  | Arm | Feedback |
  |---|---|
  | E1 | Labels only, plus extra labels equal to the time spent writing notes |
  | E2 | E1 plus cross-fitted teacher evidence |
  | E3 | E2 plus guideline excerpts (control for general knowledge) |
  | E4 | E2 plus expert notes |
  | E5 | E2 plus shuffled expert notes (control for extra text) |

- **Headline result:** a cost-per-F1-point curve for expert time. A negative N2 result is still
  reported.

## Validity safeguards

[§10](compiled-clinical-ie.md#10-validity-safeguards) gives the threat each one answers.

- Cross-fit the teacher so its evidence does not leak dev gold.
- Label test from scratch, to avoid anchoring on the teacher's pre-annotations.
- Send actively selected notes to dev only; test stays frozen.
- Match arms on measured expert-hours.
- Run 3 to 5 GEPA seeds per arm and use a paired bootstrap over notes.
- Report confirmatory results on adjudicated test gold only, never on a system's own silver.
- Reject rule literals that would memorize notes.

## Main risks

Full list in [§11](compiled-clinical-ie.md#11-risks-and-mitigations).

- **PHI in GEPA's run files.** The evaluator returns note IDs, not text; logging and caching
  stay off; the reflection LLM and `run_dir` stay inside the PHI boundary.
- **GEPA API churn.** Pin `gepa==0.1.4`.
- **Abstaining on everything hard.** Coverage and abstention calibration are optimized
  objectives.
- **Dependence on unbuilt copal features.** Paper 1 needs only flat rules.
- **Scope creep across seven claims.** Preregistered gates.

## Open questions

From [§12](compiled-clinical-ie.md#12-open-questions):

- How abstain and silent outputs map to amber outcomes.
- Which copal features each phase needs.
- Whether accepting score-neutral edits lets harmful ones accumulate.
- Whether the shareable HF-symptom corpus's terms allow the reflection-LLM setup.
- How to record provenance for an aggregated frontier of rule sets.
- Recruitment, sample size, and IRB for the reviewability study.
