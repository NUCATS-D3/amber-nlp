# Scripts

- `check_core_install.py` verifies a non-editable, core-only install. Run it with `python -I` in
  a minimal environment; see the [test guide](../tests/README.md#ci).
- `check_protected_changes.py` reads a unified diff and fails when golden records, the
  catalog, conformance fixtures, harness files (agent hooks, agent definitions, the review prompt, pre-commit config, `Makefile`, CI
  workflows, itself), or existing test assertions change, unless the pull request carries the
  `contract-approved` label. CI runs it on every pull request. With `--staged` it checks the
  git index as a pre-commit hook, and `ALLOW_ORACLE_CHANGE=1` approves instead of the label.
- `mutation_test.py` runs mutmut over the validation core (`annotations`, `cases`,
  `examples`, `_graph_validation`, `grounding`) once per epic, never in CI. It copies the
  working tree to a scratch directory and uses `uv run --with mutmut`, so nothing in the
  project or lock file changes, and writes `mutation-report.json` (killed/survived counts,
  kill rate, survivors by module). The full core takes about 25 minutes with
  `--max-children 8`; `--module` narrows it. File a bead for each surviving mutant in
  invariant code, or note why it is equivalent. Runs: 2026-11-04, 3,324 mutants, 85.8%
  killed, 472 survivors (amber-u4c.7 to amber-u4c.11).
- `mlflow_local.sh` starts a local MLflow server with the `tracking` extra, storing data in
  ignored `.mlflow/` paths.

Keep scripts thin; reusable behavior belongs in `src/amber/`.
