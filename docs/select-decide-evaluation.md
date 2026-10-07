# Evaluation of the select–decide proposal

Date: 2026-09-17. Evaluates [select-decide.md](select-decide.md) against the current
contracts, implementation, task protocol, and cited primary documentation. This is an
assessment, not an adopted change to the specification or roadmap.

I support the core idea, but would revise this before folding it into the binding plan.
Selecting source spans and restricting subsequent inference to them could strengthen Amber's
auditability. The proposal currently overstates that guarantee, leaves important clinical
failure modes unresolved, and expands the first delivery substantially.

The main issues, in priority order:

1. **Selection completeness needs its own design.** The proposed cascade can select supporting
   evidence, produce a confident answer, and pass verification while missing contradictory
   evidence elsewhere. A historical progression statement and a current remission statement
   are a concrete example. A verifier restricted to the selection cannot detect the omission.

   Define how selection preserves temporal qualifiers, retrieves opposing evidence, respects
   `SectionSkip`, and records review coverage. Low answerability cannot by itself distinguish
   `not_mentioned`, `insufficient_evidence`, and `conflicting_evidence`. The
   [existing protocol](protocols/oncology_current_progression-v1.md) requires those distinctions.
   The [SI paper](https://ar5iv.labs.arxiv.org/html/2205.09712), meanwhile, explicitly assumes
   questions are definitively answerable from their context; it does not establish the proposed
   abstention behavior.

2. **`inputs_only` must describe enforced execution, not a caller-supplied assertion.** The
   proposed request contract accepts arbitrary `state`; nothing connects that state to
   `InferenceEvidence.inputs`. Using `decide` therefore cannot automatically establish the flag.

   Assemble inference requests from resolved evidence IDs at a controlled service boundary.
   Retain an auditable record of the rendered inputs, prompt/schema versions, and any history
   or additional context. Distinguish information the model *received* from evidence cited as
   *support*. This establishes restricted context; it does not prove entailment or eliminate
   learned background knowledge. `inputs_only_rate` should measure verified execution records.

3. **Choice probability is not per-unit relevance probability.** The proposed multi-span rule
   treats Choice mass and per-candidate yes/no probability as interchangeable. They answer
   different questions. Six equally useful units could each receive approximately `0.167`
   Choice probability and all fail `min_prob=0.2`. Conversely, one irrelevant candidate receives
   probability 1 under a one-option normalized distribution.

   Use Choice for ranking and separately evaluated relevance/support decisions for inclusion.
   Define explicit none/insufficient behavior and how multiple windows are searched. Selecting
   one section first can discard the only contradictory evidence.

4. **Keep selection within the existing grounding path.** Adding a third minter and
   `alignment="selected"` is unnecessary. Selection describes how a span was found; alignment
   describes how its source match was verified.

   Resolve the selected unit against the authoritative source, then invoke
   [exact grounding](../src/amber/grounding.py) with its starting offset. Keep
   `alignment="exact"` and record selection separately. This also avoids producing different
   Inclusion records with the same content-addressed ID when one span is obtained through both
   `quote` and `select`; the [current graph](../src/amber/graph.py) rejects conflicting records
   for an existing ID.

5. **The decision API needs stronger, consistent semantics.** The proposed answer models permit
   combinations such as a Choice with a boolean answer or a Score with arbitrary text. Specify
   discriminated answer types, request/response correspondence, option membership, finite bounded
   probabilities, normalization, and immutable records.

   TypeSafe's documentation also exposes differences that need explicit adaptation:

   - [Noul](https://docs.typesafe.ai/primitives/noul) returns `P(yes)`, with no separate confidence.
   - [Score](https://docs.typesafe.ai/primitives/score) returns a probability-weighted mean of
     level indices. That is not automatically a valid ordinal clinical answer.
   - [Confidence](https://docs.typesafe.ai/confidence) summarizes distribution shape; it is not
     defined as the proposed local backend's calibrated maximum probability.

   Keep raw scores, vendor confidence, and calibrated correctness estimates distinct. A local
   `logprobs` capability must also guarantee access to every required label score; truncated
   top-token results are insufficient.

6. **Terminology is a separate domain expansion.** Adding `SourceKind.terminology` does not
   resolve how patient-independent knowledge enters the current patient/source-scoped graph.
   It needs versioned source identity, permitted reference scope, relation provenance, and
   reproducible local records.

   More fundamentally, "drug may treat condition" does not establish that this patient has that
   condition. A terminology citation cannot make that inference valid. Defer this until a
   specific task requires it, then define the permitted clinical inference explicitly. The
   current progression task does not justify making terminology infrastructure an M1 prerequisite.

7. **The roadmap has a dependency inversion and adds substantial scope.** The proposal's M2 uses
   `decide` and the logprob backend, while M3 introduces them. The cascade also assumes sectioning,
   deduplication, verification, and reviewer escalation that are currently conditional later work.

   Preserve the [first delivery's](04-roadmap.md) task, persistence, fixed baseline, correction,
   and expert-effort measurement. Introduce one bounded select–decide comparison with one
   permitted endpoint. Three backends, generic field compilation, and terminology support should
   not become prerequisites.

8. **The evaluation needs clinical and statistical safeguards.** The proposed metrics are useful
   diagnostics, but synthetic comparisons cannot establish clinical benefit. The frozen CORAL
   development partition contains only ten notes: per-provider/per-field calibration curves will
   be highly uncertain, and many selections from the same note do not supply independent cases.

   Define answerability from adjudicated task outcomes; annotation absence is not gold
   unanswerability. Report verifier false acceptance of unsupported claims, rather than agreement
   alone. Measure selection completeness and evidence length, since returning large spans can
   improve overlap while increasing review effort. Retain omissions, automation coverage, and
   total expert time as adoption criteria.

There is also a factual correction in the motivation: "the model call itself saw the whole note"
describes a prospective baseline limitation. The
[current implementation](README.md#current-implementation) has no extraction/model-call pipeline.

My recommended first experiment is the existing boolean progression task: deterministic units,
exact grounding, evidence-restricted decisions, explicit coverage/outcome handling, and one
provider. Compare it with the planned full-note baseline using the same model and cases. Include
an experiment using expert-selected evidence to separate selection failures from decision
failures. That would test the proposal's central hypothesis before committing Amber to the
broader API and infrastructure.
