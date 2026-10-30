# amber-nlp charter

Adopted 2026-10-29. This charter supersedes the M1–M8 roadmap. The earlier plan is preserved at
the git tag `pre-reset` and summarized in [history](history/README.md).

## Purpose

amber-nlp is the shared framework for evidence-grounded clinical information extraction. It
defines what an extraction result *is* and how it is checked, so that the implementations stay
replaceable: rules, models, agents, and human review. It also provides MLflow plugins that work
with any conforming implementation, for evaluation, human-in-the-loop review, and deployment.

amber-nlp does not extract anything by itself.

## Ecosystem

| Project | Owns | Depends on |
|---|---|---|
| **amber-nlp** | Contracts, validation, interfaces, conformance kit, MLflow plugins | Pydantic and python-ulid in core; MLflow only in plugin extras |
| **lapidary** | Experiments and datasets (CORAL, institutional corpora), task protocols, provider adapters, extractors, baselines, run reports | amber-nlp, copal, loupe |
| **copal** | Exact-offset text views, matchers, and rule composition | nothing from amber |
| **loupe** | Human review, adjudication, and reference publication as an MLflow app | amber-nlp contracts, through the review bridge |

Dependencies point toward amber-nlp's core contracts. amber-nlp never imports lapidary, copal,
loupe, a model runtime, or a dataset.

## What amber-nlp owns

1. **Contracts.** Versioned Pydantic models and wire formats (JSON, Parquet table schemas):
   - `Source`, `Section`, `Mention`, `Claim`;
   - the evidence kinds and `EvidenceEdge`;
   - `Case`, `CaseOutcome`, `CaseResult`;
   - `Example` with reference tiers;
   - task and variable specifications;
   - `Provenance`, `Sensitivity`, `Zone`.

   The contract also covers canonical IDs and hashing.
2. **Validation.** Exact grounding, evidence-graph validation, outcome consistency, and the
   zone × sensitivity policy rules are part of the contract. Implementations call them; they
   never re-implement them.
3. **Interfaces.** Protocols that implementations satisfy:
   - `Extractor` (`run(case) -> CaseResult`);
   - `Provider` with capabilities;
   - `PolicyGate` and `Store`;
   - `Exporter` and `Scorer`;
   - `ReviewBackend`;
   - `Labeler` (produces tiered Examples).
4. **Conformance kit.** Adversarial fixtures and checks, shipped as a pytest plugin, that any
   implementation runs against its own outputs.
5. **MLflow plugins** (optional extras, OSS API only):
   - evaluation scorers;
   - run-context tags;
   - `Example` ↔ MLflow Dataset conversion;
   - the loupe review bridge;
   - a pyfunc deployment wrapper for any `Extractor`.
6. **One minimal reference implementation** per interface, in an optional extra and synthetic
   only. It exists to exercise the conformance kit, not to be used clinically.

## Non-goals

- No pipelines, agents, prompts, provider SDK integrations, or training code. These belong in
  lapidary.
- No rule engine or lexicons. copal provides the engine; clinical rules belong in lapidary or a
  rule pack.
- No UI, web API, or CLI. Review is loupe; deployment is MLflow serving.
- No datasets, task protocols, gold labels, or clinical results. These belong in lapidary.
- No patient-level aggregation until a contract design adopts it.

## Rules for growth

- **Stability.** An interface or contract field becomes stable only when a real consumer (a
  lapidary experiment, loupe, or an MLflow plugin) uses it and the conformance kit covers it.
  Until then it is marked experimental.
- **Versioning.** The package uses semver, and contract models carry schema versions. A
  breaking change needs a migration note and an updated conformance kit in the same change.
- **Invariants.** The invariants in [CLAUDE.md](../CLAUDE.md) are enforced by validation code
  and adversarial tests, not by documentation.
- **Toolchain.** Python 3.12 only and `uv`. The MLflow plugin extras follow loupe's tested
  MLflow range.
- **Tracking.** Work is tracked in Beads (`bd ready`); experiment work is tracked in
  lapidary's tracker.

## Reset decisions (2026-10-29)

1. The project restarted in place: history and the tested kernel were kept, and the prior
   state was tagged `pre-reset`.
2. loupe stays a separate package that implements `ReviewBackend`; amber-nlp provides the
   bridge.
3. A minimal synthetic reference implementation lives in an amber-nlp extra. Real providers
   and extractors live in lapidary.
4. The FastAPI app, the CLI, and the settings facade were removed.
5. The project supports Python 3.12 only.
