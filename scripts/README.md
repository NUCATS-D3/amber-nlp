# Scripts

Shared operational utilities live here. Run `bash scripts/mlflow_local.sh` from the repository
root to start a local MLflow server; it selects the `tracking` extra and stores the database
and artifacts in ignored `.mlflow/` paths.
`recreate_cmux_workspace.sh` recreates the repository's cmux workspace group and split layout;
run it with an optional project-directory argument, or without one to use the repository root.
Keep utilities thin; reusable library behavior belongs in `src/amber/`.

Dataset-specific preparation, execution, and evaluation belong in
[`experiments/<experiment>/`](../experiments/README.md). The CORAL audit and draft adapter
are in [`experiments/coral/scripts/`](../experiments/coral/scripts/).
