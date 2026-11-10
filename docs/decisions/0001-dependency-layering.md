# ADR 0001: Core, framework, and plugin layering

- Status: accepted (Will Thompson, 2026-11-09)
- Date: 2026-11-09
- Related: [charter](../charter.md), [AGENTS.md layout](../../AGENTS.md),
  [module graph](../architecture/deps.md), amber-7q0.2, amber-7q0.3

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
   core and schema. Imports the core and the core's own dependencies; never a plugin or an
   optional stack, even indirectly.
3. **Plugins**: `amber.mlflow_ext`, `amber.bundles.parquet`, `amber.conformance`,
   `amber.reference`, `amber.loupe`. Import the framework and the core, and load optional stacks
   at their boundary with an actionable install message. A plugin imports only the plugins
   below it: `amber.conformance` over `amber.reference` over three independent plugins,
   `amber.mlflow_ext`, `amber.loupe`, and `amber.bundles.parquet` (the conformance kit uses the
   reference implementations, and both use Parquet bundles). Refined by amber-7q0.3.

`import amber` and `import amber.bundles` load no plugin or optional stack.

## Consequences

- import-linter enforces the three rules as forbidden contracts (`make imports`, part of
  `make check` and CI). A core module that imports a framework module or a listed optional
  stack fails the build. The contracts name what is forbidden; they are not an allowlist of
  permitted packages.
- `scripts/check_core_install.py` verifies a core-only, non-editable install in CI, and the
  fresh-process import test in `tests/test_interfaces.py` checks that `import amber` stays light.
- A `layers` contract in `.importlinter` (amber-7q0.3) orders every module, plugins over
  framework over core, with finer layers inside each: the core is a strict stack (`ids`,
  `schemas`, then `grounding` and `graph_errors`, `_graph_validation`, `graph`, `cases`,
  `tools`), and the framework has five levels whose modules may import each other but never a
  level above. [docs/architecture/deps.md](../architecture/deps.md) draws the graph.
- A new module must be placed in a layer, and the contracts updated in the same change;
  `tests/test_architecture.py` fails while any module has no layer, and plants upward imports
  to prove each contract holds.

## Alternatives considered

- **One flat package with lazy imports everywhere.** Relies on discipline rather than a check,
  and an eager import anywhere breaks core-only installs silently.
- **Separate distributions per layer.** Stronger isolation, but versioning and releasing several
  packages costs more than the contracts do while there is one maintainer team.
