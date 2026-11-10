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
  the identifier scan, the OpenCode SDK check (`tools/opencode-check/`), the guard script, the
  lint, type, and architecture config (`ruff.toml`, `mypy.ini`, `.importlinter`), and the quality
  ratchet and its baseline (`quality/`);
- every existing test assertion and test name, any `conftest.py`, pytest configuration
  (`pyproject.toml`, `pytest.ini`, `tox.ini`, `setup.cfg`), `.gitattributes`, and the
  conformance pytest plugin; added lines that skip, xfail, slow-mark, or stop tests, Hypothesis
  settings, and renaming a test module out of collection are flagged too.

The authoritative list is `_PROTECTED` in
[scripts/check_protected_changes.py](scripts/check_protected_changes.py). Do not modify these
paths, work around them, or copy them so an edited duplicate is used instead. If a protected
test or check looks wrong, stop and explain why in your reply or the bead; a human decides.

OpenCode (`.opencode/opencode.json`) and Claude Code (`.claude/settings.json`) ask a human
before an agent's edit or write tool touches a protected file, or a file that could switch these
rules off: any `opencode.json(c)` or `.opencode/` directory (agents, modes, plugins, config,
including in parent directories), `.claude/`, and both tools' home config
(`~/.config/opencode/`, `~/.claude/`), in both harnesses; OpenCode also asks for its system
directories. The OpenCode rules sit in `.opencode/opencode.json` because the closest `.opencode`
config is merged last. Approve only with **Allow once**: **Allow always** saves an `edit *`
allow rule for the project, which overrides every one of these prompts (list saved rules with
`opencode api get /api/permission/saved`). Line-level protection (test assertions, pytest lines
in `pyproject.toml`) stays with the guard. The rules cannot see shell commands (`sed -i`, `cp`,
`>`), OpenCode config passed through `OPENCODE_CONFIG*` environment variables, or Claude Code
runs whose permission mode skips prompts; never use those to change a protected path. The
pre-commit guard and CI's protected-artifacts check catch any such change in a diff.
`tests/test_agent_permissions.py` keeps both configs in step with the guard's list.

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
  `contract-approved` or `dependency-change` label.

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
  fixes) and, under `src/`, mypy and the import contracts. Failures are appended to the edit's
  result.
- When a top-level session ends a turn with changes under `src/` or `tests/`, `stop_gate.py` runs
  `make lint typecheck test-fast imports ratchet` (about 50 s; the tests run on parallel
  workers). Failures come back as a "Stop gate" message, at most once in a row.

Fix what they report; never work around them. The plugin and the `reviewer` agent's permissions
were verified with OpenCode 2.2.5; CI (`opencode-harness.yml`) type-checks the plugin and decodes
the agents' permission rules against that pinned SDK (`tools/opencode-check/`). When OpenCode
is upgraded, bump the pins there and re-verify the hooks live.

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
src/amber/graph_errors.py source-free codes for GraphValidationError
src/amber/cases.py        CaseResult creation/loading and outcome validation
src/amber/tools/          quote, mention, cite, commit_claim, no_claim: operations implementations call
src/amber/examples.py     Example and LabelRecord load boundary
src/amber/labels.py       the sanctioned derived_text -> labels_only views
src/amber/policy.py       declared data-handling policy and the transfer check (pure)
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
  framework, and plugin direction declared in `.importlinter`. Optional imports happen at the
  plugin boundary and raise an actionable install message when the dependency is missing.

## Commands

`make check` runs sync, lint, mypy, lint-imports, the lock check, the identifier scan, the
fast test suite, and `make quality` (Ruff, the import contracts, the ratchet, deptry, and the
dependency diff; pass `RATCHET_BASE=<ref>` without an `origin/main`); `make help` lists the
other targets. The underlying commands:

```sh
uv sync --extra dev --extra tracking --extra bundles   # full development environment
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src                           # config in mypy.ini
uv run lint-imports                       # dependency direction (.importlinter)
make ratchet                              # violation counts may not rise (quality/baseline.json)
uv lock --check
bash scripts/mlflow_local.sh              # local MLflow server in .mlflow/
```

For the core-only installation check, see [tests/README.md](tests/README.md#ci).

## Design rules

> **Draft (amber-7q0.11), pending human review.** Invariants and the framework boundary live in
> [CLAUDE.md](CLAUDE.md); calling validation, never copying it, in the [charter](docs/charter.md).

- Imports follow `.importlinter`'s layers ([ADR 0001](docs/decisions/0001-dependency-layering.md));
  placing a new module, moving one, or adding an `ignore_imports` entry is a human edit.
- Search `src/amber` before writing, and extend the module that owns the concept (see the layout
  above) rather than adding a parallel one.
- No new abstraction (base class, protocol, factory, registry, or configuration option) without
  two production callers in `src/amber` in the same change; tests don't count. A new interface
  instead follows CLAUDE.md's stability rule, with one reference implementation (ADR 0004).
- The core (the `core` contract in `.importlinter`) touches no files, environment, network, or
  logging; reading and writing belong to plugins and callers.
- Keep validation at load, commit, and transfer boundaries even when callers "cannot" violate
  it, including re-grounding spans (ADR 0003). Code past a boundary trusts it.
- Expected failures carry source-free codes: results (`GroundingFailure`, `LabelerFailure`,
  failed outcomes) or a module's `ValueError` subclass (`GraphValidationError`). Messages never
  include source text; never catch broadly or swallow an error to make something pass.
- Records asserting verified content (`Inclusion`, `Mention`, `Claim`, `CaseResult`, `Example`,
  and any new one) have one creation or load path and refuse `model_construct` (ADR 0003).
- Delete replaced code in the same change: no `_old` or `_v2` copies, shims, or commented-out
  code. Breaking contract changes ship a migration note (contract-change skill) instead.
- Comments explain why, citing the contract section or ADR. New dependencies, including
  development tools, need human approval.
- Accepted ADRs in [docs/decisions/](docs/decisions/) are not reopened without discussion.

## Coding and testing

- Four-space indentation, 100-character lines. Ruff owns import order and formatting.
- Every `# noqa`, `# type: ignore`, and `# pragma: no cover` counts against the ratchet
  (`make ratchet`), and a new one needs a trailing justification: `# noqa: E741 - reason`, or
  `# type: ignore[code]  # - reason` (mypy allows only a comment after an ignore). File-level
  `# ruff: noqa`, `# flake8: noqa`, and `# mypy:` directives never count as justified. Added
  files and added lines must have no Ruff finding and, under `src/` and `quality/`, no
  `mypy --strict` error. A function whose `def` line is new may not exceed complexity 10, and no
  module's most complex function may get worse. For this the ratchet applies wider Ruff rules
  than `make lint` (docstrings on public API, argument and branch counts, blind excepts, prints
  in `src/`; see `quality/ruff-ratchet.toml`), and counts dead code (vulture) and duplicated
  blocks (pylint). Code only tests use is not dead; a real false positive goes in the protected
  `quality/vulture_whitelist.py`. Run `make ratchet` before finishing.
- `snake_case` for modules and functions, `PascalCase` for models.
- Tests are `tests/test_*.py`, run offline on CPU with invented data only.
- Every invariant has an adversarial test covering:
  - invalid bounds, Unicode and CRLF offsets, repeated quotes;
  - unknown IDs, missing field evidence, cycles, source-free branches;
  - outcome consistency;
  - disallowed policy combinations.
- When adding an optional plugin, extend the fresh-process core import test, the core-only
  install check, and the import-linter contracts in `.importlinter`.

## Data safety

- Never commit PHI, real or credentialed clinical text (including CORAL), credentials, local
  databases, MLflow artifacts, or model weights.
- Datasets live outside repositories and are registered in lapidary; `data/` and
  `experiments/` stay ignored here as a safety net.
- `examples/` is synthetic only. `make phi-scan` (part of `make check`, pre-commit, and CI) fails
  on identifier-shaped text (SSNs, phone numbers, emails outside reserved test domains, MRN- or
  birth-date-like values) anywhere in the repository, including Beads issue text; only package
  authors' emails in the npm lockfile and each Beads issue's creator email are skipped. Use
  invented values that do not look like identifiers, or `example.invalid` addresses.
- The worktree may contain user changes: check `git status` first and preserve unrelated work.

## Commits

Imperative subject, with a body explaining why for non-trivial changes. Call out contract,
dependency, or wire-format changes, and their migrations.

Work lands on `main` through pull requests. CI's "Protected contract artifacts" check fails a
pull request that changes a [protected path](#protected-paths) until a human adds the
`contract-approved` label. The same guard runs as a pre-commit hook (`make hooks`), where only a
human sets `ALLOW_ORACLE_CHANGE=1`. The same check fails on any change to declared or locked
dependencies (`quality/dep_gate.py`; `make deps` prints the diff) until a human adds the
`dependency-change` label. An agent may merge its own pull request once every check passes and
the protected-artifacts check passed without either label; anything that needs a label waits
for a human.
