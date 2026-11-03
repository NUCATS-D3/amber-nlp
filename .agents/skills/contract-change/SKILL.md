---
name: contract-change
description: Use in amber-nlp when a diff touches src/amber/schemas, src/amber/wire.py, canonical ID derivation, the conformance golden records or catalog, conformance fixtures, or a protocol in src/amber/interfaces.py, or when a test in tests/test_wire.py or tests/test_conformance.py fails. Classifies the change, completes the versioning obligations, and gets approval before accepting new golden bytes.
---

# Contract change

The contract is amber-nlp's product. Consumers (lapidary, loupe, MLflow plugins) depend on its
records, IDs, and wire bytes, and the charter requires every breaking change to ship with a
migration note and an updated conformance kit
([charter](../../../docs/charter.md#rules-for-growth)).

## Pinned artifacts

These are amber's snapshots. Never regenerate or edit them to make a test pass.

- `src/amber/conformance/golden/*.json`: wire bytes and key order (WIR-14), regenerated only
  with `amber.conformance.golden.write_golden`.
- `src/amber/conformance/catalog.json`: the conformance catalog, checked against
  `docs/design/contract-v0.1.md`; `catalog_revision()` hashes it.
- `src/amber/conformance/fixtures.py`: `fixtures_ref()` hashes it.
- Canonical ID derivations in `amber.ids` and the schemas: a change re-identifies stored data.

## 1. Classify every change

For each changed model, protocol, ID derivation, or golden file, explain the change in one
line and classify it:

| Class | Meaning | Examples |
| --- | --- | --- |
| additive | old payloads still load and produce the same IDs | new optional field with a default, new protocol method on an experimental interface |
| breaking | some old payload fails to load, loads differently, or gets a different ID | new required field, renamed key, tightened validator, changed hash input |
| accidental | not intended by the task | key order drift, a default that serializes differently, a timestamp format change |

Revert accidental changes. Ask when a classification is unclear; tightened validation is
breaking even when it looks like a bug fix.

## 2. Meet the obligations

- **Additive:** update `docs/contracts.md`. Mark new fields and interfaces experimental until a
  real consumer uses them and a conformance case covers them.
- **Breaking:** all of the above, plus in the same change:
  - a section in `docs/migrations/` naming who is affected and how to upgrade, in the style of
    `docs/migrations/contract-v0.1.md`;
  - a `schema_version` decision for the affected records;
  - updated conformance fixtures, checks, and catalog cases, with the catalog still matching
    the design document;
  - adversarial tests for the new rule (use the `invariant-tests` skill).
- **Interfaces:** a new or changed protocol updates `amber.conformance` checks for that target
  and the reference implementation in `amber.reference`.

## 3. Get approval, then regenerate

Present the classified list to the user and wait for approval before regenerating golden
records or editing the catalog. After approval:

```sh
uv run --no-sync python -c "from pathlib import Path; from amber.conformance.golden import write_golden; write_golden(Path('src/amber/conformance/golden'))"
git diff --stat src/amber/conformance/
uv run --no-sync pytest --strict-markers -ra -m "not slow"
```

Check that only the approved records changed. Mention the contract or wire-format change and
its migration in the commit body.

## Done when

- Every change is classified, no accidental change remains, and the user approved the set.
- Breaking changes have a migration note, schema-version decision, and conformance update.
- `docs/contracts.md` describes the new state.
- The fast suite, Ruff, and mypy pass.
