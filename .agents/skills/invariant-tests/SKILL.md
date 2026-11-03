---
name: invariant-tests
description: Use in amber-nlp when a change adds or modifies validation, grounding, IDs and hashing, the evidence graph, cases and outcomes, Examples, annotation events, policy, bundles, or any load boundary. Writes adversarial and property tests first and proves they fail without the change.
---

# Invariant tests

amber-nlp's correctness is its invariants ([CLAUDE.md](../../../CLAUDE.md#invariants-enforce-in-code-and-tests-not-in-comments)).
A regression is an invariant that some input can now violate. This skill makes every change
that touches one arrive with tests that would have caught its violation.

## 1. Name what the change must refuse

Before writing code, list the invariants the change touches and, for each, the inputs that
must be refused or must behave identically. Work through the attack checklist from AGENTS.md
and keep the items that apply:

- invalid bounds; Unicode (astral code points, combining marks, surrogates) and CRLF offsets;
  repeated quotes that need a hint;
- unknown IDs, missing field evidence, cycles, source-free branches, rejected support;
- outcome consistency: non-answers with final claims, `failed` as gold, empty answered sets;
- disallowed policy combinations: zone × sensitivity, payload kinds, restrictions;
- load boundaries: a payload valid on its own but inconsistent with the authoritative `Case`,
  `Source`, or `VariableSet`.

## 2. Write the failing tests first

- Put tests in the file `tests/README.md` assigns to that boundary. Use invented text only.
- Assert the stable, source-free code (`graph_errors`, `ReviewImportCode`, policy `reason`),
  not exception message text.
- Run the new tests against the unchanged code and confirm they fail for the expected reason.
  A test that passes before the fix proves nothing; strengthen it or drop it.

## 3. Add a property where the input space is large

Use Hypothesis (already a dev dependency; see `tests/test_policy.py`) when examples cannot
cover the space. Prefer relations that must hold for all inputs:

| Relation | Example |
| --- | --- |
| Round trip | `model_dump_json` → load → dump gives identical bytes; bundle write → read is lossless |
| Hash stability | key order and equal content give one canonical hash; any field change gives another |
| Offset faithfulness | for any text with CRLF or astral characters, `text[start:end] == quote` |
| Prefix determinism | replaying events up to `as_of_event` equals replaying that prefix |
| Totality | `check` returns a `Decision` for every input and never carries source text |
| Refusal | random graphs with a cycle or a source-free branch never validate |

Keep strategies small and bounded. A property failure must shrink to an input that names
what broke; add the shrunk case as an explicit example test (`@example`) once fixed.

## 4. Keep diagnostics source-free

Test names, assertion messages, and codes never contain note text or offsets from real data.
Synthetic text in a test may be printed; production diagnostics may not.

## 5. Implement and verify

```sh
uv run --no-sync pytest --strict-markers -ra -m "not slow"
uv run --no-sync ruff check . && uv run --no-sync ruff format --check .
uv run --no-sync mypy src
```

If an existing test fails, fix the code. Changing or deleting an existing assertion is a
contract decision: stop and ask, and use the `contract-change` skill if the user agrees.

## Done when

- Each listed invariant has a test that failed before the change and passes after it.
- No existing assertion was weakened or removed without approval.
- The fast suite, Ruff, and mypy pass.
- `tests/README.md` lists any new test file or newly covered area.
