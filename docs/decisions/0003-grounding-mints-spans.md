# ADR 0003: Only grounding mints inclusions and mentions

- Status: proposed
- Date: 2026-11-09
- Related: [CLAUDE.md invariant 1](../../CLAUDE.md), amber-bpr.8 (Mention scope), amber-7q0.2

## Context

An `Inclusion` asserts that a quote occurs at given offsets of a source, and a `Mention` asserts
the same for a typed span. Everything downstream (claims, citations, scoring, review) trusts those
assertions. If any code could construct one directly, a model's hallucinated quote or a forged
offset could enter the evidence graph looking verified.

## Decision

- `Inclusion` and `Mention` can only be minted inside `amber.grounding` (`exact_quote`,
  `exact_mention`, `cite_mention`), after the quote has been checked against the source.
- Their validators require a private context object held by the minting function; plain
  construction, `model_validate`, and `model_construct` are refused.
- Every other path that receives spans (graph validation, bundle loading, the loupe adapter, and
  the human-review import) re-grounds them through those functions rather than trusting stored
  offsets. Human review therefore adds no second minting path: a reviewed span is grounded like
  any other. The same private-context pattern guards `Claim`, `CaseResult`, and `Example`, which
  have their own single load or creation boundaries.

## Consequences

- `src/amber/schemas/evidence.py` (`_INCLUSION_MINT_CONTEXT`) and
  `src/amber/schemas/mentions.py` (`_MENTION_MINT_CONTEXT`) enforce the rule; `_mint_inclusion`
  and `_mint_mention` are called only from `src/amber/grounding.py`.
- `tests/test_invariants.py::test_inclusion_cannot_be_constructed_outside_grounding` and
  `tests/test_mentions.py::test_mention_cannot_be_constructed_outside_grounding` try the bypasses;
  the conformance kit's grounding and wire cases check implementations from the outside.
- Implementations must call the grounding tools and handle `GroundingFailure`; they cannot
  "fix up" a span by constructing it.
- A future fuzzy-alignment policy (amber-ndt.1, deferred) would be a new named grounding path,
  decided by its own ADR, not a relaxation of this one.

## Alternatives considered

- **Validate spans when a claim is committed.** Catches forgeries later and leaves unverified
  spans in memory, logs, and exports in between.
- **Trust spans from human review.** Simpler, but a reviewer tool bug would produce unverified
  evidence; re-grounding costs little.
