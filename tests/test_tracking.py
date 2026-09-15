"""Optional tracking boundary tests; no server, model, or source data is used."""

import json
import os
import subprocess
import sys
import textwrap
from importlib.metadata import entry_points
from pathlib import Path

import pytest


@pytest.mark.parametrize("missing_module", ["mlflow", "missing_transitive_dependency"])
def test_tracking_import_distinguishes_missing_extra_from_broken_install(
    missing_module: str,
) -> None:
    # Block the optional import in a fresh process even when tracking is installed.
    code = textwrap.dedent(
        f"""
        import importlib.abc
        import importlib
        import sys

        class MissingMLflow(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname == "mlflow":
                    raise ModuleNotFoundError("unavailable dependency", name={missing_module!r})

        sys.meta_path.insert(0, MissingMLflow())
        try:
            importlib.import_module("amber.mlflow_ext.context")
        except ImportError as error:
            if {missing_module!r} == "mlflow":
                assert "uv sync --extra tracking" in str(error), str(error)
            else:
                assert isinstance(error, ModuleNotFoundError)
                assert error.name == {missing_module!r}
                assert "uv sync --extra tracking" not in str(error)
        else:
            raise AssertionError("tracking imported without its dependency")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_tracking_entry_point_supplies_version_tag() -> None:
    pytest.importorskip("mlflow")
    from amber import __version__

    provider_entry = next(iter(entry_points(group="mlflow.run_context_provider", name="amber")))
    provider = provider_entry.load()()
    assert provider.in_context() is True
    assert provider.tags() == {"amber.version": __version__}


def test_local_launcher_selects_tracking_and_local_artifacts(tmp_path: Path) -> None:
    # Capture the real launcher's command at the external-process boundary without
    # installing packages or starting a server during an offline unit test.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    fake_uv.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "scripts/mlflow_local.sh"
    result = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "run",
        "--extra",
        "tracking",
        "mlflow",
        "server",
        "--host",
        "127.0.0.1",
        "--port",
        "5000",
        "--backend-store-uri",
        "sqlite:///.mlflow/mlflow.db",
        "--artifacts-destination",
        ".mlflow/artifacts",
    ]
    assert (tmp_path / ".mlflow").is_dir()
