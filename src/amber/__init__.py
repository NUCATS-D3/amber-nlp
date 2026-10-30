"""amber-nlp: contracts, validation, and interfaces for evidence-grounded clinical extraction.

Importing ``amber`` loads only the dependency-free core. Optional MLflow plugins live under
``amber.mlflow_ext`` and require the ``tracking`` extra.
"""

from importlib.metadata import PackageNotFoundError, version


def _package_version() -> str:
    try:
        return version("amber")
    except PackageNotFoundError:
        return "0.0.0"


__version__ = _package_version()

__all__ = ["__version__"]
