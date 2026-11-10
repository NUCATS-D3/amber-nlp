"""The dependency gate (amber-7q0.7): any change to declared or locked dependencies is printed;
CI refuses it until a human adds the dependency-change label."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "quality" / "dep_gate.py"
_spec = importlib.util.spec_from_file_location("dep_gate", SCRIPT)
assert _spec is not None and _spec.loader is not None
dep_gate = importlib.util.module_from_spec(_spec)
sys.modules["dep_gate"] = dep_gate
_spec.loader.exec_module(dep_gate)

PYPROJECT = """[project]
name = "pkg"
description = "A package."
dependencies = ["pydantic>=2.7", "Python_Ulid>=2"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[dependency-groups]
docs = ["mkdocs"]

[build-system]
requires = ["hatchling"]
"""
LOCK = """version = 1

[[package]]
name = "pydantic"
version = "2.7.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.example.invalid/pydantic-2.7.0.tar.gz", hash = "sha256:aaaa" }

[[package]]
name = "pytest"
version = "8.0.0"
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> Path:
    root = tmp_path_factory.mktemp("gate") / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT)
    (root / "uv.lock").write_text(LOCK)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Gate Test")
    _git(root, "config", "user.email", "gate@example.invalid")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "switch", "-q", "-c", "work")
    capsys.readouterr()
    return root


def _gate(repo: Path, *args: str) -> int:
    return int(dep_gate.main(["--root", str(repo), "--base", "main", *args]))


def _edit(repo: Path, old: str, new: str, name: str = "pyproject.toml") -> None:
    path = repo / name
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_no_dependency_change_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _edit(repo, "A package.", "Another package.")
    assert _gate(repo, "--enforce") == 0
    assert "no dependency changes" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("old", "new", "line"),
    [
        (
            '"Python_Ulid>=2"]',
            '"Python_Ulid>=2", "requests>=2"]',
            "+ project.dependencies: requests>=2",
        ),
        ('"pydantic>=2.7", ', "", "- project.dependencies: pydantic>=2.7"),
        (
            "pydantic>=2.7",
            "pydantic>=2.8",
            "~ project.dependencies: pydantic>=2.7 -> pydantic>=2.8",
        ),
        ('dev = ["pytest>=8"]', 'dev = ["pytest>=8", "ruff"]', "+ optional-dependencies.dev: ruff"),
        (
            'dev = ["pytest>=8"]',
            'dev = ["pytest>=8"]\nbig = ["torch"]',
            "+ optional-dependencies.big: torch",
        ),
        ('docs = ["mkdocs"]', 'docs = ["mkdocs", "mike"]', "+ dependency-groups.docs: mike"),
        ('requires = ["hatchling"]', 'requires = ["hatchling", "x"]', "+ build-system.requires: x"),
    ],
    ids=["added", "removed", "re-versioned", "extra", "new-extra", "group", "build"],
)
def test_each_dependency_change_is_printed(
    repo: Path, capsys: pytest.CaptureFixture[str], old: str, new: str, line: str
) -> None:
    _edit(repo, old, new)
    assert _gate(repo) == 0
    assert line in capsys.readouterr().out


def test_a_locked_version_change_is_a_dependency_change(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _edit(repo, 'version = "8.0.0"', 'version = "8.1.0"', name="uv.lock")
    assert _gate(repo, "--enforce") == 2
    out = capsys.readouterr().out
    assert re.search(r"~ uv\.lock: pytest 8\.0\.0 \(\w{12}\) -> 8\.1\.0 \(\w{12}\)", out), out


def test_names_compare_normalized(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _edit(repo, '"Python_Ulid>=2"', '"python-ulid>=2"')
    assert _gate(repo, "--enforce") == 0, capsys.readouterr().out


def test_ci_refuses_a_change_without_the_label(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _edit(repo, '"Python_Ulid>=2"]', '"Python_Ulid>=2", "requests>=2"]')
    assert _gate(repo, "--enforce", "--labels", "contract-approved") == 2
    out = capsys.readouterr().out
    assert "+ project.dependencies: requests>=2" in out
    assert "dependency-change" in out


def test_ci_accepts_a_change_with_the_label(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _edit(repo, '"Python_Ulid>=2"]', '"Python_Ulid>=2", "requests>=2"]')
    assert _gate(repo, "--enforce", "--labels", "contract-approved,dependency-change") == 0
    assert "approved by the dependency-change label" in capsys.readouterr().out


def test_locally_a_change_is_a_warning(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _edit(repo, '"Python_Ulid>=2"]', '"Python_Ulid>=2", "requests>=2"]')
    assert _gate(repo) == 0
    assert "warning" in capsys.readouterr().out


def test_committed_changes_count_too(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _edit(repo, '"Python_Ulid>=2"]', '"Python_Ulid>=2", "requests>=2"]')
    _git(repo, "commit", "-qam", "add requests")
    assert _gate(repo, "--enforce") == 2


def test_an_unknown_base_is_an_error(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert dep_gate.main(["--root", str(repo), "--base", "nowhere"]) == 1
    assert "nowhere" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            'source = { registry = "https://pypi.org/simple" }',
            'source = { git = "https://example.invalid/fork?tag=v2" }',
        ),
        ('hash = "sha256:aaaa"', 'hash = "sha256:bbbb"'),
    ],
    ids=["source", "hash"],
)
def test_a_lock_source_or_hash_change_at_the_same_version_is_a_change(
    repo: Path, capsys: pytest.CaptureFixture[str], old: str, new: str
) -> None:
    _edit(repo, old, new, name="uv.lock")
    assert _gate(repo, "--enforce") == 2
    assert "~ uv.lock: pydantic 2.7.0" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("old", "new", "name"),
    [
        (
            'version = "8.0.0"\n',
            'version = "8.0.0"\n\n[[package]]\nname = "x"\nversion = "1"\n',
            "uv.lock",
        ),
        ('[[package]]\nname = "pytest"\nversion = "8.0.0"\n', "", "uv.lock"),
        ('"pydantic>=2.7"', "\"pydantic>=2.7; python_version >= '3.12'\"", "pyproject.toml"),
        ('"pydantic>=2.7"', '"pydantic[email]>=2.7"', "pyproject.toml"),
        (
            '"pydantic>=2.7"',
            '"torch @ https://example.invalid/t.whl", "pydantic>=2.7"',
            "pyproject.toml",
        ),
        (
            '"pydantic>=2.7", "Python_Ulid>=2"]',
            '"pydantic @ https://example.invalid/p.whl", "pydantic>=2.7", "Python_Ulid>=2"]',
            "pyproject.toml",
        ),
        ('docs = ["mkdocs"]', 'docs = ["mkdocs", {include-group = "other"}]', "pyproject.toml"),
        (
            "[build-system]",
            '[tool.uv.sources]\npydantic = { git = "https://example.invalid/f" }\n\n[build-system]',
            "pyproject.toml",
        ),
        (
            "[build-system]",
            '[tool.hatch.build.hooks.custom]\ndependencies = ["x"]\n\n[build-system]',
            "pyproject.toml",
        ),
    ],
    ids=[
        "lock-added",
        "lock-removed",
        "marker",
        "extra",
        "new-name",
        "same-name-twice",
        "include-group",
        "uv-sources",
        "hatch-hook",
    ],
)
def test_other_dependency_changes_are_caught(
    repo: Path, capsys: pytest.CaptureFixture[str], old: str, new: str, name: str
) -> None:
    _edit(repo, old, new, name=name)
    assert _gate(repo, "--enforce") == 2, capsys.readouterr().out


def test_whitespace_alone_is_not_a_change(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _edit(repo, '"pydantic>=2.7"', '"pydantic >= 2.7"')
    assert _gate(repo, "--enforce") == 0, capsys.readouterr().out


def test_a_missing_lock_at_the_base_is_an_error(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(repo, "rm", "-q", "uv.lock")
    _git(repo, "commit", "-qm", "no lock")
    _git(repo, "switch", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "work")
    _git(repo, "switch", "-q", "work")
    (repo / "uv.lock").write_text(LOCK)
    assert _gate(repo, "--enforce") == 1
    assert "uv.lock" in capsys.readouterr().out
