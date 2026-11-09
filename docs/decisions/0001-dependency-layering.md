# ADR 0001: Core, framework, and plugin layering

- Status: proposed
- Date: 2026-11-09
- Related: [charter](../charter.md), [AGENTS.md layout](../../AGENTS.md), amber-7q0.2, amber-7q0.3

## Context

amber is installed by consumers that want only the contracts and validation, and by consumers
that also want MLflow, Parquet bundles, the conformance kit, or the loupe bridge. Optional stacks
are heavy, and a contract module that imported one by accident would force it on every consumer.
The charter also requires that implementations call validation rather than re-implement it,
which only works if validation sits in a layer everything can import.

## Decision

amber has three layers, and imports only point downward:

1. **Core**: `amber.schemas`, `ids`, `grounding`, `graph`, `graph_errors`, `_graph_validation`,
   `cases`, `tools`. Imports only Pydantic, python-ulid, and the standard library.
2. **Framework**: annotations, examples, labels, interfaces, policy, providers, extraction,
   scoring, span scoring, regression, evaluation, labeling, review, rules, wire, and the bundle
   core and schema. Imports the core and the standard library; never a plugin or an optional
   stack, even indirectly.
3. **Plugins**: `amber.mlflow_ext`, `amber.bundles.parquet`, `amber.conformance`,
   `amber.reference`, `amber.loupe`. Import the framework and the core, and load optional stacks
   at their boundary with an actionable install message.

`import amber` and `import amber.bundles` load no plugin or optional stack.

## Consequences

- import-linter enforces the three rules as forbidden contracts (`make imports`, part of
  `make check` and CI). A core module that imports a framework module or an optional stack
  fails the build.
- `scripts/check_core_install.py` verifies a core-only, non-editable install in CI, and the
  fresh-process import test in `tests/test_interfaces.py` checks that `import amber` stays light.
- A new module must be placed in a layer, and the contracts updated in the same change.
- The contracts are written as forbidden lists rather than import-linter `layers`. amber-7q0.3
  reviews the real structure and decides whether a `layers` or `independence` contract fits.

## Alternatives considered

- **One flat package with lazy imports everywhere.** Relies on discipline rather than a check,
  and an eager import anywhere breaks core-only installs silently.
- **Separate distributions per layer.** Stronger isolation, but versioning and releasing several
  packages costs more than the contracts do while there is one maintainer team.
