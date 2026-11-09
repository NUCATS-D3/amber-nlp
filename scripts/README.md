# Scripts

- `check_core_install.py` verifies a non-editable, core-only install. Run it with `python -I` in
  a minimal environment; see the [test guide](../tests/README.md#ci).
- `check_protected_changes.py` reads a unified diff and fails when golden records, the catalog,
  conformance fixtures, harness files (agent hooks, agent definitions, the review prompt,
  pre-commit config, `Makefile`, CI workflows, itself), existing test assertions or test names,
  any `conftest.py`, pytest configuration (`pyproject.toml` settings and entry points,
  `pytest.ini`, `tox.ini`, `setup.cfg`), `.gitattributes`, or the conformance pytest plugin
  change; when a test gains a line that skips, xfails, slow-marks, or stops tests or sets
  Hypothesis settings, or any Python file gains a collection hook; when a test module is renamed
  out of collection; or when a protected or test file shows up as a binary diff. The
  `contract-approved` label approves. CI runs it on every pull request. With `--staged` it
  checks the git index as a pre-commit hook, and `ALLOW_ORACLE_CHANGE=1` approves instead of the
  label.
- `phi_scan.py` scans every tracked and untracked (not ignored) file, except the third-party
  lockfiles in `EXCLUDED`, for identifier-shaped text (SSN, phone, non-reserved email, MRN-like,
  birth-date-like) and prints `path:line: pattern`, never the match. In `.beads/issues.jsonl`
  the `owner` field is skipped; issue text is scanned. A binary file is reported `unscannable`
  unless `UNSCANNABLE` pins it to the SHA-256 of reviewed content; line exceptions go in
  `ALLOWED`. Both lists are reviewed by a human (the script is protected). `make phi-scan`,
  `make check`, pre-commit, and CI run it.
- `mutation_test.py` runs mutmut over the validation core (`annotations`, `cases`, `examples`,
  `_graph_validation`, `grounding`) weekly in CI
  ([mutation.yml](../.github/workflows/mutation.yml), which uploads the report and summarizes
  the kill rate) and on demand, never as a gate. It copies the working tree to a scratch
  directory and uses `uv run --with mutmut`, so nothing in the project or lock file changes, and
  writes `mutation-report.json` (killed/survived counts, kill rate, survivors by module). The
  full core takes about 25 minutes with `--max-children 8`; `--module` narrows it. File a bead
  for each surviving mutant in invariant code, or note why it is equivalent. Runs: 2026-11-04,
  3,324 mutants, 85.8% killed, 472 survivors (amber-u4c.7 to amber-u4c.11).
- `mlflow_local.sh` starts a local MLflow server with the `tracking` extra, storing data in
  ignored `.mlflow/` paths.

Keep scripts thin; reusable behavior belongs in `src/amber/`.
