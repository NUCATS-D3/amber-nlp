# Scripts

- `check_core_install.py` verifies a non-editable, core-only install. Run it with `python -I` in
  a minimal environment; see the [test guide](../tests/README.md#ci).
- `mlflow_local.sh` starts a local MLflow server with the `tracking` extra, storing data in
  ignored `.mlflow/` paths.

Keep scripts thin; reusable behavior belongs in `src/amber/`.
