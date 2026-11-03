"""Core-only import boundary tests."""

import subprocess
import sys

import amber


def test_importing_core_does_not_import_optional_stacks() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import amber, amber.cases, amber.graph, amber.schemas, amber.policy, "
            "amber.tools.commit, amber.tools.quote, amber.tools.outcomes, sys; "
            "from amber.policy import DEFAULT_MATRIX, check, combine; "
            "from amber.graph import EvidenceGraph; "
            "from amber.schemas import Case, CaseOutcome, CaseResult, Claim, Provenance, Task; "
            "from amber.tools.commit import commit_claim; "
            "assert not {'fastapi', 'click', 'mlflow', 'pandas', 'pyarrow', 'duckdb', "
            "'numpy', 'scipy', 'sklearn', 'pydantic_ai', 'transformers', 'mlx'} "
            "& sys.modules.keys(); "
            "assert not {'amber.reference', 'amber.conformance', 'pytest'} & sys.modules.keys()",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_package_exposes_a_version() -> None:
    assert isinstance(amber.__version__, str)
    assert amber.__version__
