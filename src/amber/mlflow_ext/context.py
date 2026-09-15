"""Optional OSS MLflow run-context provider, currently emitting amber.version only.

MLflow discovers this entry point when tracking is installed; importing core Amber does not
load this module or require the tracking extra.
"""

try:
    from mlflow.tracking.context.abstract_context import RunContextProvider
except ModuleNotFoundError as exc:
    if exc.name != "mlflow":
        raise
    raise ImportError(
        "MLflow is optional. Install tracking dependencies with: uv sync --extra tracking"
    ) from exc


class AmberRunContextProvider(RunContextProvider):
    def in_context(self) -> bool:
        return True

    def tags(self) -> dict[str, str]:
        from amber import __version__

        return {"amber.version": __version__}
