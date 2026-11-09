# Repository guidelines

## Start here

1. [docs/charter.md](docs/charter.md): what amber-nlp is and is not.
2. [docs/contracts.md](docs/contracts.md): what exists today.
3. [CLAUDE.md](CLAUDE.md): invariants and conventions.

`docs/history/` is pre-reset design input, not current plans.

## Definition of done

A task is done when all of these hold:

1. `make check` passes on the final tree. Quote its last two lines (the pytest summary and
   `check ok`) in your final message. Without that output, do not say the work passes.
2. The bead's acceptance criteria are met, each with the evidence: a test, a command and its
   output, or a review.
3. The `reviewer` agent has reviewed the branch, and every blocking finding is resolved or
   explained in the pull request (see [Review](#review)).

### Protected paths

These are the oracle. They judge your work, so you never change them:

- golden records, the conformance catalog, and conformance fixtures;
- the verification harness: Claude Code hooks and settings, the OpenCode plugin and config,
  agent definitions and the review prompt, `.pre-commit-config.yaml`, `Makefile`, CI workflows,
  and the guard script;
- every existing test assertion, `tests/**/conftest.py`, and the pytest configuration; added
  skip, xfail, or `slow` marks and Hypothesis settings in tests are flagged too.

The authoritative list is `_PROTECTED` in
[scripts/check_protected_changes.py](scripts/check_protected_changes.py). Do not modify these
paths, work around them, or copy them so an edited duplicate is used instead. If a protected
test or check looks wrong, stop and explain why in your reply or the bead; a human decides.

### Forbidden shortcuts

- Adding `skip`, `skipif`, `xfail`, or `importorskip` to make a failure go away.
- Loosening an assertion, tolerance, exception match, or Hypothesis strategy.
- Moving a test out of `make check` (a `slow` mark, `conftest.py`, pytest options), dropping
  parametrized cases, or lowering Hypothesis example counts.
- Special-casing a test input, fixture value, ID, or file name, or behaving differently under
  pytest or CI.
- Catching and suppressing an exception so a test passes.
- Adding `# type: ignore`, `# noqa`, or configuration that silences a real error.
- Regenerating golden records to match new output outside the contract-change skill and a
  human's approval. (Regression baselines live with consumers such as lapidary, which protect
  their own.)
- Setting `ALLOW_ORACLE_CHANGE`, committing with `--no-verify`, or adding the
  `contract-approved` label.

### Workflow

Work from the bead: `bd show <id>` gives the scope and acceptance criteria. Keep each change
small enough to review, commit whenever `make check` is green, and file anything outside the
bead as a new bead instead of doing it.

## Task tracking

Project tasks, blockers, and decisions live in Beads (`.beads/`, prefix `amber-`).

- `bd ready` finds unblocked work.
- `bd show <id>` before starting.
- `bd update <id> --claim` to take it.
- `bd close <id> --reason=...` when done.

File follow-up work with `bd create` instead of Markdown TODO lists. Never use `bd edit`, which
is interactive. Keep issue text free of clinical text, quotes, or offsets. Experiment work is
tracked in lapidary.

## Agent hooks

Two hooks in `.claude/hooks/` check agent work. OpenCode runs them through
`.opencode/plugins/amber-harness.ts`; Claude Code runs them through `.claude/settings.json`.

- After each edit to a Python file under `src/` or `tests/`, `post_edit.py` runs Ruff (with
  fixes) and, under `src/`, mypy. Failures are appended to the edit's result.
- When a top-level session ends a turn with changes under `src/` or `tests/`, `stop_gate.py` runs
  `make lint typecheck test-fast` (about 45 s). Failures come back as a "Stop gate" message, at
  most once in a row.

Fix what they report; never work around them.

## Development skills

Project skills live in `.agents/skills/` (linked from `.claude/skills/`). Use them when their
trigger applies:

| Skill | When | Done when |
|---|---|---|
| [invariant-tests](.agents/skills/invariant-tests/SKILL.md) | A change touches validation, grounding, IDs, the graph, outcomes, Examples, events, policy, bundles, or a load boundary | Each touched invariant has a test that failed before the change |
| [contract-change](.agents/skills/contract-change/SKILL.md) | A diff touches schemas, wire forms, ID derivation, protocols, golden records, or the conformance catalog or fixtures | Changes classified and approved; migration and conformance updated for breaking ones |
| [deslop-diff](.agents/skills/deslop-diff/SKILL.md) | A feature is finished, before landing | Simplified diff passes with tests unchanged by the pass |

## Review

Before landing a branch, get an independent review from the read-only `reviewer` agent
(`.opencode/agents/reviewer.md`; `.claude/agents/reviewer.md` for Claude Code). Both follow
[docs/review_prompt.md](docs/review_prompt.md) and reply `VERDICT: PASS` or `FAIL` with `path:line`
findings. Resolve every blocking finding or explain it in the pull request; never edit the prompt
or the agents to get a PASS.

## Layout and dependency direction

```
src/amber/schemas/        contract models (pure; no I/O)
src/amber/ids.py          canonical hashing and ULIDs
src/amber/grounding.py    exact grounding (the only Inclusion minting path today)
src/amber/graph.py        EvidenceGraph lifecycle; _graph_validation.py holds the checks
src/amber/cases.py        CaseResult creation/loading and outcome validation
src/amber/tools/          quote, mention, cite, commit_claim, no_claim: operations implementations call
src/amber/examples.py     Example and LabelRecord load boundary
src/amber/annotations.py  annotation-event replay, projections, Examples from events
src/amber/interfaces.py   protocols (Store, SourceResolver, PolicyGate, Exporter, Provider, Extractor, Scorer,
                          Labeler, ReviewBackend; Rule is re-exported from rules.py)
src/amber/scoring.py      score reports and the self-reference and move-on rules
src/amber/span_scoring.py strict/lenient mention-span P/R/F1 and attribute accuracy (experimental)
src/amber/regression.py   regression gate: a ScoreReport against a stored baseline (experimental)
src/amber/evaluation.py   tier-aware binary metric harness: gates, Wilson, bootstrap
src/amber/labeling.py     Labeler outputs, silver-only admission, import events from results
src/amber/rules.py        RuleSpec, rule reduction and derivation, replay of rule derivations
src/amber/review.py       review receipts and the reviewed-import rules (spans, provenance, tiers)
src/amber/extraction.py   shared extractor pieces: failed results, local provenance
src/amber/providers.py    provider requests and responses; the gated, budgeted call_provider
src/amber/reference/      synthetic reference implementations (not loaded by import amber)
src/amber/loupe/          loupe bridge: claim-mode adapter, source export, publication import
src/amber/conformance/    conformance catalog, fixtures, checks, report, pytest plugin
src/amber/bundles/        bundle tables, manifest, hashing, loading; Parquet I/O (bundles extra)
src/amber/wire.py         JSON forms that are not plain model dumps (VariableSet)
src/amber/mlflow_ext/     optional MLflow plugins (tracking extra)
tests/                    synthetic, offline tests
scripts/                  core-install check and local MLflow launcher
```

- The core (`schemas`, `ids`, `grounding`, `graph`, `cases`, `tools`) imports only Pydantic,
  python-ulid, and the standard library.
- Plugins import the core, never the reverse.
- `import amber` must not load MLflow or any optional stack. `lint-imports` enforces the core,
  framework, and plugin direction declared in `pyproject.toml`. Optional imports happen at the
  plugin boundary and raise an actionable install message when the dependency is missing.

## Commands

`make check` runs sync, lint, mypy, lint-imports, the lock check, and the fast test suite; `make
help` lists the other targets. The underlying commands:

```sh
uv sync --extra dev --extra tracking --extra bundles   # full development environment
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run lint-imports                       # dependency direction (pyproject [tool.importlinter])
uv lock --check
bash scripts/mlflow_local.sh              # local MLflow server in .mlflow/
```

For the core-only installation check, see [tests/README.md](tests/README.md#ci).

## Coding and testing

- Four-space indentation, 100-character lines. Ruff owns import order and formatting.
- `snake_case` for modules and functions, `PascalCase` for models.
- Tests are `tests/test_*.py`, run offline on CPU with invented data only.
- Every invariant has an adversarial test covering:
  - invalid bounds, Unicode and CRLF offsets, repeated quotes;
  - unknown IDs, missing field evidence, cycles, source-free branches;
  - outcome consistency;
  - disallowed policy combinations.
- When adding an optional plugin, extend the fresh-process core import test, the core-only
  install check, and the import-linter contracts in `pyproject.toml`.

## Data safety

- Never commit PHI, real or credentialed clinical text (including CORAL), credentials, local
  databases, MLflow artifacts, or model weights.
- Datasets live outside repositories and are registered in lapidary; `data/` and
  `experiments/` stay ignored here as a safety net.
- `examples/` is synthetic only.
- The worktree may contain user changes: check `git status` first and preserve unrelated work.

## Commits

Imperative subject, with a body explaining why for non-trivial changes. Call out contract,
dependency, or wire-format changes, and their migrations.

Work lands on `main` through pull requests. CI's "Protected contract artifacts" check fails a
pull request that changes a [protected path](#protected-paths) until a human adds the
`contract-approved` label. The same guard runs as a pre-commit hook (`make hooks`), where only a
human sets `ALLOW_ORACLE_CHANGE=1`. An agent may merge its own pull request once every check
passes and the protected-artifacts check passed without that label; anything that needs the
label waits for a human.
