"""AmberRunContextProvider — entry point mlflow.run_context_provider (see pyproject).
Tags: amber.version, dataset hash, EDW extract id, IRB id, zone, sensitivity.
"""

from mlflow.tracking.context.abstract_context import RunContextProvider


class AmberRunContextProvider(RunContextProvider):
    def in_context(self) -> bool:
        return True

    def tags(self) -> dict[str, str]:
        from amber import __version__

        return {"amber.version": __version__}
