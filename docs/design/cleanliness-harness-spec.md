# Spec: Code Cleanliness Harness for Agent-Driven Development

## Purpose

Set up automated and review-based enforcement of code **cleanliness**: modularity, simplicity, and agreed design principles. It should work alongside the existing correctness harness (`make check`, hooks, eval gate, reviewer). This spec is a companion to `verification-harness-spec.md` and reuses its conventions: `make` targets, protected paths, `ALLOW_ORACLE_CHANGE`, `.claude/` hooks, and CI.

The project is Python, possibly with a Rust core. Adapt paths and package names (`<package>` below) to the real repo.

## Guiding principles (must hold after setup)

1. **Enforce what can be measured; review what can't.** Tools handle complexity, layering, dead code, duplication, dependencies, types and coverage. A rubric-driven reviewer handles design judgment.
2. **New code is held to the full standard; legacy code is ratcheted.** New files must be clean. Existing files may keep their current violations but must not get worse.
3. **Suppressions count as violations.** `# noqa`, `# type: ignore`, and `# pragma: no cover` are tracked and ratcheted like any other violation.
4. **Thresholds catch outliers; they don't shape every function.** Keep limits moderate, and have the reviewer check explicitly for code fragmented just to satisfy a metric.
5. **Design principles are written down** (`AGENTS.md` design rules + ADRs), so the agent and the reviewer work from the same reference.
6. **The agent can't edit the quality config, baselines or rubric.** Same protection model as the correctness oracle.

---

## 1. Files and layout

```
.
├── ruff.toml                      # lint config, separate from pyproject so it can be protected (PROTECTED)
├── .importlinter                  # architecture contracts (PROTECTED)
├── quality/
│   ├── ratchet.py                 # collects violation counts, compares to baseline (PROTECTED)
│   ├── baseline.json              # committed violation counts (PROTECTED)
│   ├── diff_budget.py             # PR size / scope check (PROTECTED)
│   ├── dep_gate.py                # detects dependency changes (PROTECTED)
│   └── vulture_whitelist.py       # known false positives for vulture (PROTECTED)
├── docs/
│   └── adr/
│       ├── 0000-template.md
│       └── 0001-<first-decision>.md
├── AGENTS.md                      # gains a "Design rules" section (PROTECTED section — see §9)
└── .claude/
    └── agents/
        └── cleanliness-reviewer.md   # rubric-driven reviewer subagent (PROTECTED)
```

Add every PROTECTED path above to the `.claude/settings.json` deny rules and to the pre-commit protected-path guard from the verification spec.

Move the ruff config out of `pyproject.toml` into `ruff.toml`. Then the lint rules can be protected without locking `pyproject.toml`, whose dependency changes are handled separately by `dep_gate.py`.

---

## 2. Lint rules (`ruff.toml`)

Starting config. Tune thresholds after the first baseline run, but keep the rule families.

```toml
line-length = 100
target-version = "py311"   # match the project

[lint]
select = [
  "E", "W", "F",     # pycodestyle, pyflakes
  "I",               # import sorting
  "N",               # naming
  "UP",              # modern syntax
  "B",               # bugbear
  "SIM",             # simplifications
  "C90",             # mccabe complexity
  "PL",              # pylint-derived (branches, args, statements, returns, magic values)
  "RET",             # return consistency
  "ARG",             # unused arguments
  "ERA",             # commented-out code
  "BLE",             # blind except
  "TRY",             # exception anti-patterns
  "PIE",             # unnecessary constructs
  "PTH",             # pathlib over os.path
  "T20",             # print statements
  "ANN",             # type annotations
  "D",               # docstrings (public API)
  "PGH",             # blanket noqa / type: ignore
  "RUF",             # incl. RUF100 unused noqa
]
ignore = [
  "D100", "D104", "D105", "D107",   # module/package/magic/__init__ docstrings
  "TRY003",                          # long messages in exceptions (too noisy)
]

[lint.mccabe]
max-complexity = 10

[lint.pylint]
max-args = 6
max-branches = 12
max-statements = 50
max-returns = 6

[lint.pydocstyle]
convention = "google"

[lint.per-file-ignores]
"tests/**" = ["D", "ANN", "PLR2004", "ARG", "S101"]
"scripts/**" = ["T20", "D"]
```

Requirements:
- `PGH` and `RUF100` must stay enabled. Blanket suppressions and stale `noqa` comments are failures.
- Any **new** `# noqa: <code>` or `# type: ignore[<code>]` must carry a trailing justification (`# noqa: PLR0913 — mirrors external API signature`). Enforce this in `ratchet.py` (see §5) by counting suppressions without a justification as a separate, zero-tolerance metric.

### Types
- `pyright` in **strict** mode for `src/` (set in `pyrightconfig.json` or `[tool.pyright]`), `standard` for `tests/`.
- If strict mode produces too many errors on legacy code, use `strict = ["src/<package>/<new_or_clean_modules>"]` and widen it over time. The ratchet tracks the total error count.

---

## 3. Architecture contracts (`.importlinter`)

Use `import-linter`. Define the layering explicitly; the template below is a starting point to adapt to the real module structure, which must first be documented in an ADR (see §8).

```ini
[importlinter]
root_package = <package>
include_external_packages = True

[importlinter:contract:layers]
name = Layered architecture (higher may import lower, never the reverse)
type = layers
layers =
    <package>.api
    <package>.pipeline
    <package>.rules
    <package>.core

[importlinter:contract:core-pure]
name = core has no heavy or framework dependencies
type = forbidden
source_modules =
    <package>.core
forbidden_modules =
    spacy
    torch
    transformers
    pandas

[importlinter:contract:independent-components]
name = sibling components do not import each other
type = independence
modules =
    <package>.rules.matchers
    <package>.rules.context
    <package>.rules.sections
```

Requirements:
- `lint-imports` runs in `make quality-fast` and in CI.
- If a contract is violated in the current code, either fix it or add an `ignore_imports` entry with a comment linking to a tracking issue. Humans approve each entry.
- During setup, generate a dependency graph (`pydeps <package> --max-bacon 2 -o docs/architecture/deps.svg`) so a human can review the actual structure before the contracts are finalized.

---

## 4. Dead code, duplication, dependencies, coverage

### 4.1 Dead code: `vulture`
- `vulture src/ quality/vulture_whitelist.py --min-confidence 80`
- False positives (e.g. entry points, plugin hooks, pydantic validators) go in `vulture_whitelist.py`, which is protected; humans add entries.

### 4.2 Duplication: pylint `duplicate-code`
- `pylint --disable=all --enable=duplicate-code --min-similarity-lines=8 --ignore-imports=yes src/`
- Count duplicate blocks; ratchet the count. Any new duplicate block in changed files fails `make quality`.

### 4.3 Dependencies: `deptry` + dependency gate
- `deptry .` must pass: no unused, missing, or transitive-only dependencies.
- `quality/dep_gate.py`: compares `[project.dependencies]` and optional/dev dependency groups (and `Cargo.toml` dependencies, if present) against `origin/main`. If any dependency is added, removed or version-bumped, fail **unless** `ALLOW_DEP_CHANGE=1` is set locally or the PR carries a `dependency-change` label in CI. Print the diff of dependencies.

### 4.4 Coverage on changed lines: `diff-cover`
- `pytest --cov=src/<package> --cov-report=xml`
- `diff-cover coverage.xml --compare-branch=origin/main --fail-under=90`
- Global coverage is reported but not gated. The changed-line coverage is the gate.

---

## 5. Ratchet (`quality/ratchet.py`)

The core mechanism for legacy code: violations may stay but must not increase.

### Collected metrics
| Metric | Source |
|---|---|
| ruff violations, **per rule code** | `ruff check --exit-zero --output-format json` |
| pyright errors | `pyright --outputjson` |
| vulture findings | `vulture ... ` (parse output) |
| duplicate-code blocks | pylint output |
| `# noqa` count | grep over `src/` |
| `# type: ignore` count | grep over `src/` |
| `# pragma: no cover` count | grep over `src/` |
| unjustified suppressions | suppressions without a trailing `—` / `--` justification; **tolerance 0** |
| max cyclomatic complexity per module | `radon cc -j src/` |
| import-linter `ignore_imports` entries | parse `.importlinter` |

### Behavior
- Compare current counts to `quality/baseline.json`.
- **Fail (exit 2)** if any metric increases. Print each regressed metric with old → new values and, for ruff/pyright, the specific new violations (file:line:code).
- **New files** (added relative to `origin/main`) must have **zero** ruff violations, pyright errors, vulture findings and unjustified suppressions, whatever the baseline says.
- If any metric **decreased**, print `ratchet can be tightened: <metric> <old> → <new>` and exit 0. It does not update the baseline itself.
- `make quality-rebaseline` writes a new baseline. It requires `ALLOW_ORACLE_CHANGE=1` and refuses to run if any metric increased (rebaselining can only tighten). Loosening requires editing `baseline.json` by hand in a human commit.

---

## 6. Diff budget (`quality/diff_budget.py`)

Large, sprawling diffs are where agent drift hides.

- Compare the working tree (or PR head) to `origin/main`.
- Exclude: lockfiles, generated files, `eval/predictions/`, test data/fixtures, `docs/architecture/*.svg`.
- Thresholds (configurable at the top of the script):
  - **warn** when > 400 changed lines or > 12 files touched
  - **fail** when > 800 changed lines or > 25 files touched, unless `ALLOW_LARGE_CHANGE=1` or the PR has a `large-change` label
  - **warn** when a change touches more than 3 top-level subpackages of `<package>` (scope creep signal)
- Output: a short summary of lines/files changed per subpackage.

---

## 7. Rust core (if present)

In `Cargo.toml`:

```toml
[lints.rust]
unsafe_code = "forbid"          # or "deny" with documented exceptions
unused = "deny"

[lints.clippy]
all = { level = "deny", priority = -1 }
pedantic = { level = "warn", priority = -1 }
# allow pedantic lints that prove noisy, each with a comment:
module_name_repetitions = "allow"
must_use_candidate = "allow"
```

Targets:
- `cargo fmt --check`
- `cargo clippy --all-targets -- -D warnings`
- `cargo machete` (unused crates)
- `cargo deny check` (licenses, duplicate versions, advisories), with a committed `deny.toml`

Include the Rust targets in `make quality` when a `Cargo.toml` is present.

---

## 8. Design documentation

### 8.1 ADRs (`docs/adr/`)
Template (`0000-template.md`):

```markdown
# ADR NNNN: <title>
- Status: proposed | accepted | superseded by NNNN
- Date: YYYY-MM-DD

## Context
## Decision
## Consequences
## Alternatives considered
```

During setup, the agent **drafts** (status `proposed`) ADRs for decisions that are visible in the current code, for example:
- the module layering enforced in `.importlinter`
- how rules/patterns are represented (data vs. code)
- the span/offset representation (character offsets into the original text, etc.)
- the boundary between the Rust core and Python

Humans accept, edit or reject them. The agent must not mark ADRs `accepted`.

### 8.2 "Design rules" section in `AGENTS.md`
Add a short section (aim for under 25 lines). Draft from the accepted ADRs and existing conventions; humans finalize. Example shape:

```markdown
## Design rules
- Follow the layering in `.importlinter`; never add `ignore_imports` entries.
- Prefer extending an existing module or helper over creating a new one. Search before writing.
- No new abstraction (base class, protocol, factory, registry, config option) unless it has at least two concrete uses in this change or an accepted ADR calls for it.
- Functions in `core` are pure: no I/O, no global state, no logging side effects.
- Errors: raise specific exceptions; never catch broadly to make things pass. Let unexpected errors propagate.
- Delete replaced code in the same change. No compatibility shims, `_old`/`_v2` copies, or commented-out code.
- Comments explain *why*, not *what*.
- Stay in scope: unrelated cleanups go in a separate change.
- New dependencies require human approval.
- See `docs/adr/` for decisions that should not be reopened without discussion.
```

---

## 9. Cleanliness reviewer subagent (`.claude/agents/cleanliness-reviewer.md`)

A read-only reviewer that runs in a fresh context. It can be combined with the correctness reviewer from the verification spec or kept separate. Separate is preferred, since the focus differs.

```markdown
---
name: cleanliness-reviewer
description: Reviews a diff for design quality, modularity and agent-typical code smells against AGENTS.md design rules and docs/adr. Read-only.
tools: Read, Grep, Glob, Bash
---

You review code changes for design quality. You do not edit files.
Only run read-only commands (git diff, git log, grep, the quality reports).

Inputs: `git diff origin/main...HEAD`, AGENTS.md (Design rules), docs/adr/,
PLAN.md, and the output of `make quality`.

Check each item and report concrete findings with file:line:

1. Speculative generality — abstractions, parameters, config options or extension
   points with only one use.
2. Parallel implementations — new helpers/classes that duplicate or near-duplicate
   existing ones (search the codebase before concluding).
3. Defensive clutter — broad try/except, redundant None/type checks, swallowed
   errors, fallback values that hide failures.
4. Leftovers — old code paths, shims, `_old`/`_v2` copies, commented-out code,
   unused parameters kept "for compatibility".
5. Pass-through wrappers — classes or functions that only forward calls.
6. Metric gaming — functions split into fragments that make no sense on their own,
   just to satisfy complexity/size limits; suppressions with weak justifications.
7. Layering and placement — code in the wrong module even if import-linter passes;
   leaks of framework types into core.
8. Naming and interface clarity — names that don't match behavior; public APIs that
   expose internals.
9. Comment quality — comments narrating what code does; missing *why* for
   non-obvious choices.
10. Scope — changes unrelated to PLAN.md's current task.
11. ADR conflicts — changes that contradict an accepted ADR.

Output:
- Verdict: PASS | PASS WITH NITS | FAIL
- Findings: list of {severity: blocker|major|nit, file:line, rule #, explanation,
  suggested direction (one sentence, no code)}
- Do not give scores. Do not restate the diff. Ignore pure formatting (ruff handles it).
```

Usage requirement for `AGENTS.md`: before declaring a non-trivial task done, the implementing agent invokes `cleanliness-reviewer` and addresses all blocker and major findings, or explains in its final message why a finding doesn't apply.

---

## 10. Make targets and hook integration

Add to the `Makefile`:

```make
quality-fast:       ## ruff, lint-imports, ratchet (seconds)
quality:            ## quality-fast + pyright strict, vulture, duplication, deptry, dep_gate, diff_budget, rust lints
diff-coverage:      ## pytest --cov + diff-cover vs origin/main
quality-rebaseline: ## HUMAN ONLY; requires ALLOW_ORACLE_CHANGE=1; only tightens
```

Update `check` so it includes cleanliness:

```make
check: lint typecheck test gate quality diff-coverage
```

Hooks (extend the existing `.claude/hooks/`):
- `post_edit.py`: already runs ruff and pyright on the edited file, and now picks up the expanded `ruff.toml` automatically. Also run `lint-imports` when the edited file is under `src/`. Exit 2 on failure.
- `stop_gate.py`: add `make quality-fast` after the fast tests. Keep the full `make quality` and `diff-coverage` for CI if they make the Stop hook too slow (target: Stop hook under ~90 s total).

CI: run `make quality` and `make diff-coverage` on every PR. Post the ratchet summary and the diff-budget summary as a PR comment or artifact. Enforce the `dependency-change` and `large-change` labels as described above.

---

## 11. Acceptance criteria for this setup task

- [ ] `ruff.toml`, `.importlinter`, `quality/`, ADR template and the reviewer subagent exist, and all are on the protected-path list (deny rules + pre-commit guard).
- [ ] `docs/architecture/deps.svg` is generated, and the import-linter contracts match the real structure (or violations are listed as `ignore_imports` with comments, pending human approval).
- [ ] `quality/baseline.json` is generated from the current code (human-approved commit), and `make quality` passes on a clean checkout.
- [ ] Adding a function with complexity > 10 in a **new** file fails `make quality-fast`.
- [ ] Adding an upward import (e.g. `core` → `pipeline`) fails `lint-imports`.
- [ ] Adding `# noqa` without a justification fails the ratchet.
- [ ] Adding a new ruff violation to an **existing** file fails the ratchet with an old → new count and the file:line.
- [ ] Fixing a legacy violation prints "ratchet can be tightened", and `make quality-rebaseline` refuses to loosen.
- [ ] Adding a dependency to `pyproject.toml` fails `dep_gate.py` unless `ALLOW_DEP_CHANGE=1`.
- [ ] Copy-pasting a 10-line block within `src/` is caught by the duplication check.
- [ ] Untested new lines push `diff-cover` below 90% and fail `make diff-coverage`.
- [ ] A synthetic diff over 800 lines fails `diff_budget.py` unless `ALLOW_LARGE_CHANGE=1`.
- [ ] Draft ADRs (status `proposed`) exist for the layering and at least two other decisions visible in the code.
- [ ] `AGENTS.md` has a draft "Design rules" section, marked for human review.
- [ ] Invoking `cleanliness-reviewer` on a branch with a deliberately planted smell (e.g. a single-use factory plus a broad `except Exception: pass`) reports both as findings.
- [ ] The Stop hook still completes in under ~90 s.

## Out of scope

- Fixing existing legacy violations (the ratchet allows them; cleanup is separate work).
- Accepting ADRs or finalizing the design rules (human decisions).
- Tuning thresholds beyond the defaults (do it after the first baseline, by human commit).
