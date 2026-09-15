"""Smoke tests for the library, CLI, and optional HTTP interfaces."""

import json
import subprocess
import sys

import pytest
from click.testing import CliRunner

from amber import Amber, create_client
from amber.cli import cli
from amber.config import Settings


def test_importing_core_does_not_import_optional_stacks() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import amber, amber.schemas, amber.tools.quote, sys; "
            "assert not {'fastapi', 'mlflow', 'pandas', 'pyarrow', 'duckdb', "
            "'numpy', 'scipy', 'sklearn', 'pydantic_ai', 'transformers', 'mlx'} "
            "& sys.modules.keys()",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_library_facade_reports_system_info() -> None:
    client = create_client(Settings(environment="test"))

    assert isinstance(client, Amber)
    assert client.info().name == "amber"
    assert client.info().environment == "test"


def test_cli_info_uses_library_facade() -> None:
    result = CliRunner().invoke(cli, ["info", "--json"], env={"AMBER_ENVIRONMENT": "test"})

    assert result.exit_code == 0
    assert json.loads(result.output)["name"] == "amber"


def test_api_health_and_info() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from amber.api import create_app

    app = create_app(settings=Settings(environment="test"))
    http = TestClient(app)

    assert http.get("/health").json() == {"status": "ok"}
    response = http.get("/api/v1/admin/info")
    assert response.status_code == 200
    assert response.json()["environment"] == "test"
