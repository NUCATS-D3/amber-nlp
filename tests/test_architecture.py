"""Architecture contracts (amber-7q0.3, amber-7q0.15; ADRs 0001 and 0004): imports point down the
layers of .importlinter, every module has a layer, and amber imports no implementation."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import grimp
import pytest

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / ".importlinter"
_spec = importlib.util.spec_from_file_location("deps_graph", ROOT / "scripts" / "deps_graph.py")
assert _spec is not None and _spec.loader is not None
deps_graph = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deps_graph)

# (module that gains the import, the import, the edge lint-imports must report)
PLANTED = [
    ("ids", "import amber.schemas", "amber.ids -> amber.schemas"),
    ("schemas/sources", "import amber.grounding", "amber.schemas.sources -> amber.grounding"),
    ("grounding", "import amber.graph_errors", "amber.grounding -> amber.graph_errors"),
    ("graph", "import amber.policy", "amber.graph -> amber.policy"),
    ("policy", "import amber.annotations", "amber.policy -> amber.annotations"),
    ("scoring", "import amber.reference", "amber.scoring -> amber.reference"),
    ("reference/store", "import amber.conformance", "amber.reference.store -> amber.conformance"),
    ("loupe/adapter", "import amber.mlflow_ext", "amber.loupe.adapter -> amber.mlflow_ext"),
    ("grounding", "import lapidary", "amber.grounding -> lapidary"),
    ("mlflow_ext/context", "import loupe", "amber.mlflow_ext.context -> loupe"),
    ("scoring", "import torch.nn", "amber.scoring -> torch"),
    ("reference/extractor", "import anthropic", "amber.reference.extractor -> anthropic"),
    ("mlflow_ext/deployment", "import databricks.sdk", "amber.mlflow_ext.deployment -> databricks"),
    ("providers", "from google import genai", "amber.providers -> google"),
    ("extraction", "import boto3", "amber.extraction -> boto3"),
]


def _lint_imports(src: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(src)}
    command = [
        str(Path(sys.executable).parent / "lint-imports"),
        "--no-cache",
        "--config",
        str(CONFIG),
    ]
    return subprocess.run(
        command, cwd=src.parent, env=env, capture_output=True, text=True, check=False
    )


def test_downward_imports_only_and_no_implementations(tmp_path: Path) -> None:
    src = tmp_path / "src"
    shutil.copytree(
        ROOT / "src" / "amber", src / "amber", ignore=shutil.ignore_patterns("__pycache__")
    )
    for module, statement, _ in PLANTED:
        path = src / "amber" / f"{module}.py"
        path.write_text(path.read_text() + f"\n{statement}  # noqa: E402, F401 - planted\n")
    result = _lint_imports(src)
    assert result.returncode == 1, result.stdout + result.stderr
    for _, _, edge in PLANTED:
        assert edge in result.stdout, edge


def test_every_module_belongs_to_a_layer() -> None:
    units = [unit for layer in deps_graph.layers(CONFIG) for unit in layer]
    roots = {"amber", "amber.bundles"}
    unlayered = [
        module
        for module in sorted(grimp.build_graph("amber").modules)
        if module not in roots and not any(module == u or module.startswith(u + ".") for u in units)
    ]
    assert unlayered == []


def test_the_dependency_graph_document_is_current() -> None:
    current = (ROOT / "docs" / "architecture" / "deps.md").read_text()
    assert current == deps_graph.render(CONFIG), (
        "imports between modules changed: run `uv run --no-sync python scripts/deps_graph.py`"
        " and review the diff"
    )


@pytest.mark.parametrize("separator", ["|", ":"])
def test_layers_are_read_with_both_separators(tmp_path: Path, separator: str) -> None:
    config = tmp_path / "contracts.ini"
    config.write_text(
        f"[{deps_graph.CONTRACT}]\nlayers =\n    amber.a\n    amber.b {separator} amber.c\n"
    )
    assert deps_graph.layers(config) == [["amber.a"], ["amber.b", "amber.c"]]


def test_the_drawing_omits_only_implied_imports() -> None:
    edges = {("a", "b"), ("b", "c"), ("a", "c"), ("c", "d"), ("d", "c")}
    assert deps_graph.reduced(edges) == {("a", "b"), ("b", "c"), ("c", "d"), ("d", "c")}
