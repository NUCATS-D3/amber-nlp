# Documentation review and consolidation — 2026-10-07

Reviewed the canonical architecture, contract, roadmap, working agreement, current-progression
protocol, implementation inventory, September handoff, select–decide proposal/evaluation,
contextual linking guide, and [October design bundle](../amber-docs-2026-10-07/README.md).
Presentation source notes were checked for their distinction between proposed and implemented
behavior. This is a dated assessment, not another live status inventory.

## Assessment

Amber's strongest design choice is a common evidence boundary for fixed extraction, human review,
and later agents. Keep that boundary and complete the first measured clinical workflow. The
October additions offer a useful terminology integration direction, but their surrounding text
reintroduces the broader scaffold-first plan that the September contract replaced. Importing the
bundle wholesale would undo outcome safety and postpone the clinical and expert-effort test.

My recommendation is to prefer OMOP vocabulary integration when a task needs normalized concepts,
keep detectors interchangeable, and measure retrieval separately from clinical inference. Neither
OMOP nor a GLiNER variant should become a dependency of the current boolean progression baseline.
The project's immediate constraint is a complete evaluable workflow and independent task gold,
rather than a lack of candidate NLP methods.

## Consolidation decisions

| Material | Disposition and reason |
|---|---|
| Shared terminology assets and native/OMOP identifiers | Retain as conditional design; useful for an OMOP-backed task and reproducible linking |
| Swappable detectors, gold-span linker evaluation, dictionaries versus models | Retain as experimental direction; adoption requires task-level quality and effort evidence |
| Vocabulary store in M1 and broad detector bake-off in M2 | Defer; preserve outcomes/policy/persistence, fixed baseline, and minimal correction in M1–M3 |
| New Concept/Fragment/Provenance/Example fields | Require a future explicit contract and migration design; existing fields stay binding |
| Exact plus mandatory fuzzy grounding | Preserve exact-only sufficiency; fuzzy requires a named, tested false-alignment policy |
| Null Claim for `no_claim`, missing outcomes/final IDs | Reject; preserve evidence-backed negatives and distinct unanswered/failure outcomes |
| Policy in M3 after detector/LLM experiments | Reject dependency order; policy applies before every source-bearing provider and sink call |
| Mandatory agents, patient reviewer, expanded app, Dagster | Preserve conditional scope and note-level v1 boundaries |
| New research/model claims | Add as qualified research leads; no fresh primary-source verification or Amber performance claim |
| Original bundle | Preserve with historical status and redirects; canonical documents remain the only live authority |

The [conditional terminology/detection design](../05-terminology-and-mention-detection.md) now
contains the consolidated requirements and experiment gates. The goals, contract, roadmap, and
survey link to it; the index distinguishes current design, unadopted proposals, and history.

## Issues to resolve before terminology implementation

1. **Immutable identity:** hashing only spec JSON plus a release string omits local vocabulary
   differences and builder policy. Bind fragments and indexes to snapshot manifests and versions.
   Concept migrations must preserve original assertions rather than mutate frozen claims or gold.
2. **Polyhierarchy and mapping ambiguity:** one detector label per concept and one replacement ID
   per retired concept are insufficient. Define overlapping labels and one-to-many/unresolved
   mappings. `Maps to` does not prove a safe cross-release clinical replacement.
3. **Candidate loss:** hard type restriction prevents recovery from a detector-label mistake;
   longest-span merging loses distinct mentions. Compare fallback retrieval and preserve overlap
   until a named reconciliation policy resolves it. Short clinical terms need an explicit policy.
4. **Clinical meaning:** hierarchy validity and shared concept IDs do not establish semantic support.
   Ancestor back-off can conceal unresolved ambiguity; score it separately from exact linking.
   Mention polarity, effective time, experiencer, and contrary evidence remain essential.
5. **Gold and cost:** CORAL BRAT annotations are not exhaustive OMOP-linked gold. The proposed
   50–100-note annotation program is a separate workload, not a free extension of the frozen
   40-note progression pilot. Preserve patient/document splits and count all new labeling effort.
6. **Interop and rights:** Amber's string mention/source IDs do not directly satisfy OMOP integer
   identifiers. Define stable mapping tables and loss policies, including uncertain assertion
   states and discontinuous spans. Check vocabulary-specific license and artifact terms rather
   than treating every Athena-derived object as freely distributable.

## Select–decide and contextual linking

The [select–decide assessment](../select-decide-evaluation.md) remains sound. Its strongest next
test is one evidence-restricted boolean decision versus the full-note baseline, with an
expert-selected-evidence comparison to isolate selector errors. A controlled request builder can
establish what context was sent, but cannot prove entailment or eliminate model background
knowledge. Selection completeness and contradictory evidence are more consequential than adding
a generic probability API now.

The [contextual linking guide](../context-aware-biomedical-entity-linking.md) complements the
terminology proposal: an ontology limits the candidate space; a contextual reranker resolves
ambiguity within it. Measure candidate recall before reranker accuracy. Its broad literature
suite is a menu for a task-driven experiment, not a requirement to implement five linking systems.

## Recommended next work

1. Finish the minimal `CaseOutcome`/`CaseResult` boundary, explicit final claims, and executable
   answer/abstention/failure consistency using the existing graph validator.
2. Implement provider and source-bearing destination policy, then one validated local
   persistence/export path and the required annotated-data boundary.
3. Arrange independent progression-task adjudication alongside engineering. The fixed provider
   baseline and minimal correction comparison then test the frozen clinical and expert-time gates.
4. Use development errors to choose a bounded select–decide or mention/linking experiment. A
   negative or inconclusive experiment is useful evidence; do not broaden scope to avoid it.

## Verification and limitations

Source and test-file inspection on 2026-10-07 supports the existing inventory: graph lifecycle and
atomic proposed-claim commits exist; `cases.py`, `policy.py`, persistence/export, and the provider
adapter remain placeholders. The current facade offers system information. No terminology,
detector, extraction, adjudication, or clinical-effort workflow was found. See
[Current implementation](../README.md#current-implementation) for the canonical inventory.

The September handoff records 517 passing tests on each supported Python version; those tests
were not rerun for this documentation-only change. Validation here checked 14 changed/new
documentation files, 123 local links, and nine heading anchors; final newlines, whitespace, and
code fences passed. `git diff --check` passed for tracked edits, and the file scan also covered
untracked documentation. No restricted clinical records, raw annotations, local split manifests,
or vocabulary contents were read or modified.

Public OMOP/GLiNER documentation retrieval was attempted, but DNS was unavailable in the sandbox
and automatic approval review failed with a 404 from its configured service. No escalated
network request executed. Upstream APIs, benchmark numbers, and licensing particulars remain
verification tasks; this review establishes local consistency, not new external facts.

Existing user edits to `pyproject.toml` and `uv.lock` were preserved. The local dependency edit adds
`semchunk` to core, while no production usage was found. Reconsider the narrowest dependency extra
when its actual chunking integration is designed; installing it does not implement note coverage,
global-offset validation, or an extraction pipeline.
