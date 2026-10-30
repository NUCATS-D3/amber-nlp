# CLAUDE.md: working agreement for amber-nlp

Read [docs/charter.md](docs/charter.md) first, then [docs/contracts.md](docs/contracts.md).
amber-nlp is the framework: contracts, validation, interfaces, a conformance kit, and MLflow
plugins. It contains no experiments, datasets, extractors, prompts, rules, UI, or CLI. Those
belong in lapidary, copal, and loupe. Track work in Beads (`bd ready`); see AGENTS.md.

## Invariants (enforce in code and tests, not in comments)

1. **Grounding by construction.** An `Inclusion` is minted only by the grounding path or an
   explicit human-review path. No other code may construct one. Every claim that is not
   `rejected` has at least one evidence edge; `commit_claim` refuses otherwise.
2. **Immutable offsets.** Offsets index the immutable `Source.text` (end-exclusive code points).
   Never normalize text after a `source_id` is minted. A quote always equals
   `text[start:end]`.
3. **The evidence graph is a DAG.** Commit and load validation checks:
   - every claim/evidence reference, case scope, non-empty inference inputs, and field roles;
   - that every support branch reaches a verified Inclusion or StructuredEvidence. A rationale
     alone never grounds a fact.

   Semantic support is evaluated separately from graph validity.
4. **Policy.** Provider zone × declared data sensitivity is checked before every model or EDW
   call and stamped into `Provenance`. Source-bearing transfers (telemetry, judges, artifact
   stores, review services) are checked against their destination policy and the dataset's
   terms. Sensitivity is declared, never inferred.
5. **MLflow.** Use the OSS API surface only. No `databricks-*` imports; Databricks is
   configuration.
6. **Model-agnostic.** Nothing in `src/amber` assumes a model family, chat template, device, or
   implementation. Interfaces declare capabilities; callers check them.
7. **One annotated-data format.** `Example` is the single annotated-data format.
   Demonstrations, evaluation records, and training files are exports from it.
8. **Explicit outcomes.** Clinical absence is an evidence-backed answer. `not_mentioned`,
   `conflicting_evidence`, `insufficient_evidence`, and `failed` are distinct outcomes.
   `no_claim` never creates a null claim. Final-claim IDs separate answers from supporting
   claims, and failed runs are never gold.
9. **Framework boundary.** amber-nlp never imports lapidary, copal, loupe, model runtimes, or
   datasets. Task-specific answer models, protocols, and rules live with the consumer.
10. **Stability.** A contract field or interface becomes stable only when a real consumer uses
    it and the conformance kit covers it. Breaking changes ship with a migration note and an
    updated conformance kit.

## Conventions

- Python 3.12 only, and `uv` for everything. The core depends on Pydantic and python-ulid;
  optional plugins go in narrow extras.
- Pydantic v2 everywhere, with `ConfigDict(extra="forbid")` on data models and frozen value
  objects.
- Every invariant has a synthetic, offline test that tries to violate it.
- Commit messages: imperative subject, with a body that explains why.

## What not to do

- Don't add implementations "for later". Add a module only with working code, tests, and a
  consumer.
- Don't put PHI, real notes, credentials, or restricted dataset derivatives in the repository.
