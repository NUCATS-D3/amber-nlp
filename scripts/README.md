# Scripts

- `check_core_install.py` verifies a non-editable, core-only install. Run it with `python -I` in
  a minimal environment; see the [test guide](../tests/README.md#ci).
- `check_protected_changes.py` reads a unified diff and fails when golden records, the
  catalog, conformance fixtures, or existing test assertions change, unless the pull request
  carries the `contract-approved` label. CI runs it on every pull request.
- `mlflow_local.sh` starts a local MLflow server with the `tracking` extra, storing data in
  ignored `.mlflow/` paths.

Keep scripts thin; reusable behavior belongs in `src/amber/`.
