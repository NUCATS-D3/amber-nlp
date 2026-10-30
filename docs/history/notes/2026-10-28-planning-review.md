# Planning review and Beads migration — 2026-10-28

I re-evaluated all planning documents against the code at `6334b89`. This dated note records the
assessment and how the initial [Beads](../README-pre-reset.md#planning-and-task-tracking) issues were
derived. It does not track status: run `bd ready` / `bd show <id>`, and use the
[implementation inventory](../README-pre-reset.md#current-implementation) for what exists.

## Assessment

The task-first M1–M3 sequence and the evidence-kernel design are sound and should not change.
The kernel slice already runs: exact grounding, graph validation, atomic commits, and explicit
case outcomes. The risks are in sequencing and ownership rather than design:

1. **Human gold is the critical path, and nobody owned it.** Clinical evaluation in M2 and M3
   needs dual independent review plus adjudication of all 40 frozen notes. No document assigned
   reviewers, chose a capture method, or scheduled the work. This is now a P1 epic that runs in
   parallel with M1 engineering.
2. **Gold capture and the M3 tool depend on each other.** Gold needs a way to record exact
   evidence, while the roadmap puts the authoring interface in M3, after M2's evaluation. The
   capture method is an explicit early decision. One option is to build the M3 tool's author mode
   early. Gold reviewers or cases must stay separate from the timing study.
3. **Provider choice is a terms question as well as a zone question.** Under the default matrix,
   deidentified CORAL may go to an `external_baa` endpoint. The PhysioNet license and its guidance
   on online services must also permit that endpoint. Decide this early, because it determines
   hardware or vendor setup.
4. **M1 still lacks** the policy gate, the annotated-data contract (status transitions,
   annotation events, `Example`), the DuckDB store and Parquet export, a working CORAL adapter,
   and the local integration report. Status transitions and events need a contract amendment
   before code.
5. **Mention and StructuredEvidence** are in the contract but are not needed for the selected
   task. Whether M1 exit requires them is filed as a decision rather than assumed.
6. **Hygiene.**
   - `evaluate.py`, `policy.py`, `backends/*`, and `mlflow_ext/prompts.py` are docstring-only
     placeholders. The empty `agents/`, `app/`, and `train/` directories remain. This
     contradicts AGENTS.md.
   - An uncommitted edit adds `semchunk` to the core dependencies, but nothing imports it.
7. **Statistical power.** The pilot's test set has 10 notes. The gates can be met or unmet, but
   they give limited information. The effort comparison is especially thin once it is split into
   counterbalanced arms. Report this as a feasibility pilot, as the protocol already requires.

## Document dispositions

| Document | Disposition |
|---|---|
| `loupe-implementation-plan.md` (untracked, 2026-10-07) | Moved to the [October bundle](../amber-docs-2026-10-07/README.md) as history: it follows that bundle's superseded roadmap. Replaced by the [human-review design](../06-annotation-app.md) (see addendum) |
| `superpowers/plans/*` | Marked completed; historical records only |
| Notes from 2026-09-16 and 2026-10-07 | Unchanged dated records. Their "recommended next work" lists are superseded by Beads (item 1, case outcomes, is done) |
| Goals, contract, roadmap, protocol | Still authoritative. Added links to the human-review design and Beads; no scope changes |
| `architecture-status.md` | Kept as the visual companion to the inventory |

## Initial Beads structure

| Epic | Priority | Contents |
|---|---|---|
| M1: complete task protocol and evidence kernel | P1 | policy gate, event/status contract, Example, DuckDB store, Parquet export, CORAL adapter and report, Mention decision, placeholder cleanup, exit verification |
| Gold reference for the current-progression pilot | P1 | capture method, reviewer recruitment, dual review, adjudication into Examples |
| M2: fixed extraction baseline | P2 | provider selection, adapter, prompts, pipeline, tracking, evaluator, semantic-support review, development baseline |
| M3: minimal correction workflow | P2 | front-end spike, correction services, UI, effort-study design, held-out run, delivery decision |
| Conditional expansions | P4 | select–decide, terminology, M4–M8; blocked by the development baseline or the M3 decision |

The separate `semchunk` dependency chore is not under an epic. Dependencies encode the order
described above. Tasks that can start now: the policy gate, the contract amendment, the CORAL
adapter, the gold-capture decision, reviewer recruitment, provider selection, prompt seeding, the
front-end spike, and the hygiene chores.

## Verification

This was a documentation and tracking change; no code changed. I checked every link in the
changed documents. `bd dep cycles` reported no cycles. No restricted CORAL records, manifests, or
annotations were read.

## Addendum: review tool decision

Later the same day, human review was assigned to the author's `mlflow-span-review` MLflow app
instead of a custom app or Label Studio. The [human-review design](../06-annotation-app.md) has the
integration boundary:

- Amber supplies an `AnnotationAdapter` that validates through the kernel, plus source export and
  reference import.
- The plugin needs a claim/outcome editor mode, proposals for correct mode, timing capture,
  restricted-data readiness, and a standalone release.

Beads changes:

- The gold-capture decision and the front-end spike were closed as decided.
- The M3 UI and services tasks were repurposed.
- Plugin work is tracked with the `external:mlflow-span-review` label.

This addendum does not reflect the 1.6M-note IRB task, the plan to complement Brim, or the
teacher-to-student distillation direction raised in the same discussion. Those still need goals,
roadmap, and Beads updates once the task details are known.

## Addendum: scope decisions

- **CORAL first.** Work concentrates on the CORAL rehearsal. The owner is preparing the
  IRB-approved institutional corpus (about 1.6M notes). Its protocol, the Brim-complement and
  distillation direction, and scale work are deferred to a Beads backlog issue until the corpus is
  ready.
- **Loupe is out of scope here.** Whether the separate loupe workbench reuses `mlflow-span-review`
  is a decision for that project.

## Addendum: reference tiers

- **Default.** Two independent reviews plus adjudication remains the default reference.
- **Move-on option.** The owner may proceed at any point with `single_review` or silver
  references, recorded per note before scoring.
- **Provisional results.** Results against those references are reported as `provisional`.
- **Where specified.** Protocol 1.1.0 holds the rules; the contract notes the planned per-Example
  tier.
