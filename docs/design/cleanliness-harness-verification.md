# Cleanliness harness: verification

Bead amber-7q0.13 (epic amber-7q0). Each acceptance criterion of
[the spec](cleanliness-harness-spec.md#11-acceptance-criteria-for-this-setup-task), adapted to
amber, checked on 2026-11-10 against `main` at `308c727` in a fresh worktree with its own
environment (`uv sync --locked --extra dev --extra tracking --extra bundles`). Every planted
breakage was reverted, and the worktree was removed afterwards.

A clean checkout passes `make check` (149 s cold): `2148 passed`, `ratchet ok: 63 metrics at or
under the baseline`, `dep_gate: no dependency changes`, `diff_budget: 0 lines in 0 files`, total
coverage 95%, `check ok`.

| # | Criterion (adapted) | Command | Result |
|---|---|---|---|
| 1 | `ruff.toml`, `.importlinter`, `mypy.ini`, `quality/` (ratchet, baseline, Ruff rules, pinned config, whitelist, dependency gate, diff budget), and the cleanliness reviewer and prompt are protected | an edit to each of the 13 files, run through `scripts/check_protected_changes.py` | all 13 flagged; exit 1 without a label, 0 with `contract-approved` |
| 2 | The import graph is documented and the contracts match the real structure | `tests/test_architecture.py` | 6 passed: every module has a layer, `docs/architecture/deps.md` is current, 15 planted forbidden imports are reported |
| 3 | `quality/baseline.json` comes from the current code in human commits, and `make quality` passes on a clean checkout | `git log -- quality/baseline.json`; `make check` | three commits by Will Thompson (#43, #45, #50); 63 metrics; `make check` passes |
| 4 | Complexity over 10 in a new file fails `make quality-fast` | a function of complexity 12 in a new `amber.tools` module | exit 2: `complexity:src/amber/tools/_tangled.py rose 10 -> 12`, `ruff:C901 rose 38 -> 39` with `file:line` |
| 5 | An upward import fails `lint-imports` | `import amber.policy` in `amber.graph` | exit 2: core and layers contracts broken, `amber.graph -> amber.policy (l.665)` |
| 6 | A `# noqa` without a justification fails the ratchet | `WIDTH = 3  # noqa: E501` in `amber.ids` | exit 2: `suppressions:unjustified rose 162 -> 163` with `src/amber/ids.py:321` |
| 7 | A new Ruff violation in an existing file fails with old → new and `file:line` | an unused local in `amber.ids` | exit 2: `ruff:F841 rose 0 -> 1`, `src/amber/ids.py:324:F841` |
| 8a | Fixing a legacy violation prints "ratchet can be tightened" | the one legacy RUF005, rewritten | exit 0: `ratchet can be tightened: ruff:RUF005 1 -> 0` |
| 8b | `make quality-rebaseline` refuses to loosen | with a new violation, `make quality-rebaseline` (no `ALLOW_ORACLE_CHANGE`; agents never set it) | exit 2: "rewriting the baseline is for humans"; the baseline was untouched. With the variable set, `tests/test_ratchet.py::test_rebaseline_refuses_to_loosen` covers the refusal |
| 9 | Adding a dependency fails the dependency gate unless approved (adapted: CI enforces with the `dependency-change` label; `make deps` warns locally, decided by Will Thompson) | `requests>=2` added; `quality/dep_gate.py --enforce` | exit 2 without the label, 0 with it; the diff lists `+ project.dependencies: requests>=2` |
| 10 | A copied 10-line block in `src/` is caught by the duplication check | a 10-line function of `amber.annotations` copied into `amber.tools` | exit 2: `duplicate-code rose 4 -> 6`, `amber.annotations:124, amber.tools._copied:9` |
| 11 | Untested new lines push diff-cover below 90% and fail `make diff-coverage` | an untested function in a new `amber.tools` module | exit 2: `src/amber/tools/_untested.py (0.0%): Missing lines 4,6-8`. A CI-shaped (non-editable) install fails the same way (#53) |
| 12 | A diff over 800 lines fails the diff budget unless approved (adapted: CI enforces with the `large-change` label; `make budget` warns locally) | a 900-line test file; `quality/diff_budget.py --enforce` | exit 2 without the label, 0 with it |
| 13 | ADRs exist for the layering and at least two other decisions in the code | `docs/decisions/` | ADRs 0001–0004 (layering, code-point offsets, grounding mints spans, contracts not implementations), accepted by Will Thompson |
| 14 | `AGENTS.md` has a Design rules section | `AGENTS.md` | present, accepted by Will Thompson on 2026-11-10 |
| 15 | The cleanliness reviewer reports a planted single-use factory and `except Exception: pass` | `opencode run --agent cleanliness-reviewer` on a local commit adding both (OpenCode 2.2.5) | FAIL with both as blockers (items 1 and 3, citing the Design rules), plus placement and scope; the worktree was unchanged |
| 16 | The stop hook stays under ~90 s | the real `.claude/hooks/stop_gate.py` with a change under `tests/` | exit 0 in 41 s and 48 s |

## Deviations from the spec, all decided by Will Thompson

- mypy (ratcheted `mypy --strict` error count) instead of pyright; ADRs in `docs/decisions/`.
- Complexity from Ruff's C901 rather than radon; modules at or under 10 may grow to 10.
- The spec's Ruff families live in the ratchet-only `quality/ruff-ratchet.toml`, so `make lint`
  and the editing hooks keep `ruff.toml`; PLC0415, D205, D209, and ANN401 are left out.
- Vulture at confidence 60 (it rates unused functions 60%), with tests counted as uses.
- Agent protection is an "ask" prompt plus the guard and CI labels, not "deny".
- The dependency gate and diff budget block in CI through labels and only warn locally.
- `make diff-coverage` reruns the suite with coverage, in `make check` and CI but not the stop
  hook; `docs/architecture/deps.md` (Mermaid) replaces `deps.svg`.

## Open follow-ups

amber-7q0.16 (Ruff's default excludes, notebooks, `no_type_check`), .17 (`--loosen` and
zero-count codes), .19 (stop hook for Python outside `src/` and `tests/`), .20 (remove the four
dead items vulture found), .21 (the cleanliness prompt on test-only references and pragma
swaps), .22 (`.gitignore` hiding code from the checks), and amber-u4c.17 (the conformance plugin
under pytest-xdist).
