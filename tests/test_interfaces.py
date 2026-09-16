"""Core-only smoke tests for imports, the library facade, and the CLI."""

import json
import subprocess
import sys

import pytest
from click.testing import CliRunner

from amber import Amber, create_client
from amber.cli import cli
from amber.config import Settings

pytestmark = pytest.mark.usefixtures("isolated_settings")


def test_importing_core_does_not_import_optional_stacks() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import amber, amber.graph, amber.schemas, amber.tools.commit, amber.tools.quote, sys; "
            "from amber.graph import EvidenceGraph; "
            "from amber.schemas import Claim, Provenance, Task; "
            "from amber.tools.commit import commit_claim; "
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
    client = create_client(Settings(_env_file=None, environment="test"))

    assert isinstance(client, Amber)
    assert client.info().name == "amber"
    assert client.info().environment == "test"


def test_cli_info_uses_library_facade() -> None:
    result = CliRunner().invoke(cli, ["info", "--json"], env={"AMBER_ENVIRONMENT": "test"})

    assert result.exit_code == 0
    assert json.loads(result.output)["name"] == "amber"
    assert json.loads(result.output)["environment"] == "test"
