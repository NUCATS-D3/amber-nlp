"""Smoke-test a non-editable, core-only install: run with ``python -I``.

No pytest, dataset, model, or service is needed. An environment containing optional
stacks intentionally fails this check; use a separate minimal environment.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import amber
from amber import create_client
from amber.config import Settings
from amber.schemas import Inclusion, Source, SourceKind
from amber.tools.quote import quote


def main() -> None:
    assert sys.flags.isolated, "Run this check with python -I"
    assert Path(amber.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), (
        "Amber must be installed non-editably in this environment"
    )
    optional = (
        "fastapi",
        "mlflow",
        "pandas",
        "pyarrow",
        "duckdb",
        "numpy",
        "scipy",
        "sklearn",
        "pydantic_ai",
        "transformers",
        "mlx",
        "pytest",
        "marimo",
    )
    available = [name for name in optional if importlib.util.find_spec(name) is not None]
    assert not available, f"Expected a core-only environment; found {available}"

    # Keep the smoke check independent of a developer's configuration, too.
    for name in tuple(os.environ):
        if name.upper().startswith("AMBER_"):
            del os.environ[name]
    client = create_client(Settings(_env_file=None, environment="test"))
    assert client.info().environment == "test"
    source = Source.create(
        patient_id="synthetic-patient",
        kind=SourceKind.note,
        external_id="synthetic-note",
        datetime=None,
        text="Alpha βeta\r\n",
        record=None,
    )
    evidence = quote(source.source_id, "βeta\r\n", sources={source.source_id: source})
    assert isinstance(evidence, Inclusion)
    assert (evidence.start, evidence.end, evidence.quote) == (6, 12, "βeta\r\n")

    with TemporaryDirectory(prefix="amber-core-smoke-") as cwd:
        output = subprocess.check_output(
            [str(Path(sys.executable).parent / "amber"), "info", "--json"],
            cwd=cwd,
            text=True,
        )
    info = json.loads(output)
    assert info["name"] == "amber"
    assert info["environment"] == "development"
    print("Core-only installed facade, grounding, and CLI checks passed.")


if __name__ == "__main__":
    main()
