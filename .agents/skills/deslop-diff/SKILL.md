---
name: deslop-diff
description: Use in amber-nlp at the end of a feature or before landing a branch, or when the user asks to simplify, clean up, or deslop a diff. Simplifies the branch diff without changing behavior or tests, keeps validation at amber's boundaries, and checks the diff against the charter and contracts.
---

# Deslop diff

Simplify what this branch added so the code says what it does once, in the right place.
Behavior and tests stay fixed; judge against [docs/charter.md](../../../docs/charter.md),
[docs/contracts.md](../../../docs/contracts.md), and the layout in AGENTS.md, not personal taste.

## Scope

Review only the branch diff (`git diff <base>...HEAD` plus uncommitted work you own). Leave
unrelated user changes alone. Do not refactor code the branch did not touch; file a Beads
issue instead.

## Remove

- Narrating comments that restate the code, and docstrings describing how rather than what.
- Single-use wrappers, pass-through functions, and aliases that add no name worth having.
- Helpers that duplicate one that exists elsewhere in `src/amber`; call the existing one.
- Re-validation inside the core of something a boundary already guarantees (the charter's
  rule: implementations call validation, never re-implement it).
- Swallowed exceptions and broad `except` clauses that hide failures; expected failures are
  results with codes, unexpected ones propagate.
- Debug leftovers, unused parameters, dead branches, and "for later" code with no consumer.
- Implementation detail leaked into `docs/contracts.md` or public docstrings.

## Keep

- Validation at load, commit, and transfer boundaries, even when current callers "cannot"
  violate it. Those checks are the contract, and payloads arrive from other packages and disk.
- Explicit, source-free error codes, and distinct outcomes for distinct situations.
- Comments explaining a non-obvious rule, with its contract section reference.

## Check boundaries

- Each new concept has one home that matches the AGENTS.md layout; update the layout if the
  branch added a module.
- The core imports only Pydantic, python-ulid, and the standard library; plugins import the
  core, never the reverse; `import amber` loads no optional stack.
- Nothing here belongs in lapidary, copal, or loupe (pipelines, prompts, rules, datasets, UI).

## Verify

Make each simplification a separate, reviewable step. After the pass:

```sh
git diff --stat -- tests/
uv run --no-sync pytest --strict-markers -ra -m "not slow"
uv run --no-sync ruff check . && uv run --no-sync ruff format --check .
uv run --no-sync mypy src
```

The test diff from this pass must be empty. If a simplification needs a test change, it
changes behavior: undo it or ask.

## Done when

- The fast suite, Ruff, and mypy pass with tests unchanged by this pass.
- Out-of-scope findings are filed in Beads, not left as TODOs.
- The summary lists what was removed and any boundary concern found.
