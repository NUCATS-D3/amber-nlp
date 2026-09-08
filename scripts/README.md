# Scripts

Shared operational utilities live here. `mlflow_local.sh` starts a local MLflow server.
Keep utilities thin; reusable library behavior belongs in `src/amber/`.

Dataset-specific preparation, execution, and evaluation belong in
[`experiments/<experiment>/`](../experiments/README.md). The CORAL audit and draft adapter
are in [`experiments/coral/scripts/`](../experiments/coral/scripts/).
