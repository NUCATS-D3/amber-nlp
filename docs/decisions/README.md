# Decisions

Architecture decision records (ADRs) for amber-nlp. Each records one decision that should not be
reopened without discussion. [AGENTS.md](../../AGENTS.md) will point agents here (amber-7q0.11),
and the cleanliness reviewer (amber-7q0.12) will check changes against accepted ADRs.
Copy [0000-template.md](0000-template.md) for a new one.

Agents draft ADRs with status `proposed`. Only a human marks one `accepted` or `superseded`.
Larger designs stay in [docs/design/](../design/); Beads `decision` issues (`bd list --all
--type=decision`) record choices made during tracked work, and an ADR links to them.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-dependency-layering.md) | Core, framework, and plugin layering | accepted |
| [0002](0002-code-point-offsets.md) | Offsets are end-exclusive code points into immutable source text | accepted |
| [0003](0003-grounding-mints-spans.md) | Only grounding mints inclusions and mentions | accepted |
| [0004](0004-contracts-not-implementations.md) | amber owns contracts; implementations live elsewhere | accepted |
