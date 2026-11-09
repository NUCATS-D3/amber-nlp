# ADR 0004: amber owns contracts; implementations live elsewhere

- Status: proposed
- Date: 2026-11-09
- Related: [charter](../charter.md) (adopted 2026-10-29), [history](../history/README.md),
  [CLAUDE.md invariants 6 and 9](../../CLAUDE.md), amber-7q0.2

## Context

Before the reset, amber-nlp grew pipelines, prompts, rules, and experiment code alongside its data
models, and changes to one experiment rippled into the contracts. Several consumers now share the
contracts: lapidary's experiments, loupe's review app, copal's matchers, and MLflow plugins.
Each needs the contracts to change slowly and deliberately while the implementations change fast.

## Decision

amber-nlp owns what an extraction result is and how it is checked: versioned contracts and wire
formats, canonical IDs, validation (grounding, the evidence graph, outcomes, policy), protocols,
the conformance kit, MLflow plugins, and one minimal synthetic reference implementation per
interface. It does not own extractors, prompts, rule packs, provider SDK integrations, training
code, datasets, gold labels, task protocols, UI, or a CLI; those live in lapidary, copal, and
loupe, which depend on amber and never the reverse.

## Consequences

- amber never imports lapidary, copal, loupe, a model runtime, or a dataset; nothing in
  `src/amber` assumes a model family or device. import-linter and the core-install check cover
  the import side; review covers the rest.
- A contract field or interface becomes stable only when a real consumer uses it and the
  conformance kit covers it; breaking changes ship with a migration note and an updated kit
  (the contract-change skill).
- Work that needs an extractor or gold data is handed off: for example, the extraction-quality
  harness went to lapidary as lap-6ej, while amber provides the span scorer and regression gate.
- `amber.reference` implementations refuse non-synthetic data, so they cannot become production
  code by accident.

## Alternatives considered

- **One repository for contracts and experiments.** Faster to change both at once, which is the
  problem: experiments would keep reshaping the contracts.
- **Contracts as a schema-only package (JSON Schema, no validation code).** Each consumer would
  re-implement grounding and graph checks, and they would drift.
