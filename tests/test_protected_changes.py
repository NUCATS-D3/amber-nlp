"""The CI guard for pinned contract artifacts and existing test assertions (amber-22g)."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_protected_changes.py"
_spec = importlib.util.spec_from_file_location("check_protected_changes", SCRIPT)
assert _spec is not None and _spec.loader is not None
guard: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

TEST_FILE = """def test_one():
    value = 1
    assert value == 1


def test_two():
    with pytest.raises(ValueError):
        raise ValueError
    _assert_code("x", f)
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    files = {
        "tests/test_x.py": TEST_FILE,
        "src/amber/conformance/golden/example.json": '{"a": 1}\n',
        "src/amber/conformance/catalog.json": "{}\n",
        "src/amber/conformance/fixtures.py": "X = 1\n",
        "src/amber/other.py": "Y = 1\n",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def _found(repo: Path) -> list[str]:
    _git(repo, "add", "-A")
    diff = _git(repo, "diff", "--cached", "--unified=0", "HEAD")
    return guard.violations(diff.splitlines(keepends=True))


def test_added_tests_and_ordinary_code_are_unrestricted(repo: Path) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(TEST_FILE + "\n\ndef test_three():\n    assert True\n")
    (repo / "tests" / "test_new.py").write_text("def test_new():\n    assert 1\n")
    (repo / "src" / "amber" / "other.py").write_text("Y = 2\n")
    assert _found(repo) == []


@pytest.mark.parametrize(
    ("old", "new", "line"),
    [
        ("    assert value == 1\n", "    assert value >= 1\n", 3),
        ("    assert value == 1\n", "", 3),
        ("    with pytest.raises(ValueError):\n", "    if True:\n", 7),
        ('    _assert_code("x", f)\n', "", 9),
    ],
    ids=["weakened", "deleted", "raises", "helper"],
)
def test_edited_or_removed_assertions_are_found(repo: Path, old: str, new: str, line: int) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(test.read_text().replace(old, new))
    assert _found(repo) == [f"tests/test_x.py:{line}"]


def test_deleted_test_files_count_as_removed_assertions(repo: Path) -> None:
    (repo / "tests" / "test_x.py").unlink()
    assert _found(repo) == ["tests/test_x.py:3", "tests/test_x.py:7", "tests/test_x.py:9"]


@pytest.mark.parametrize(
    "path",
    [
        "src/amber/conformance/golden/example.json",
        "src/amber/conformance/catalog.json",
        "src/amber/conformance/fixtures.py",
    ],
)
def test_pinned_artifacts_are_protected(repo: Path, path: str) -> None:
    (repo / path).write_text((repo / path).read_text() + "\n")
    assert _found(repo) == [path]


def test_new_and_deleted_golden_files_are_protected(repo: Path) -> None:
    (repo / "src/amber/conformance/golden/new.json").write_text("{}\n")
    (repo / "src/amber/conformance/golden/example.json").unlink()
    assert sorted(_found(repo)) == [
        "src/amber/conformance/golden/example.json",
        "src/amber/conformance/golden/new.json",
    ]


def test_the_label_approves_and_output_names_no_contents(
    repo: Path, capsys: pytest.CaptureFixture[str], tmp_path_factory: pytest.TempPathFactory
) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(test.read_text().replace("    assert value == 1\n", ""))
    _git(repo, "add", "-A")
    diff = tmp_path_factory.mktemp("diff") / "changes.diff"
    diff.write_text(_git(repo, "diff", "--cached", "--unified=0", "HEAD"))
    assert guard.main([str(diff), "--labels", "documentation"]) == 1
    output = capsys.readouterr().out
    assert "tests/test_x.py:3" in output and "value == 1" not in output
    assert guard.main([str(diff), "--labels", "documentation, contract-approved"]) == 0
