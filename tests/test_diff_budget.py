"""The diff budget (amber-7q0.9): large or sprawling changes warn, and very large ones need a
human's large-change label in CI."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "quality" / "diff_budget.py"
_spec = importlib.util.spec_from_file_location("diff_budget", SCRIPT)
assert _spec is not None and _spec.loader is not None
diff_budget = importlib.util.module_from_spec(_spec)
sys.modules["diff_budget"] = diff_budget
_spec.loader.exec_module(diff_budget)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> Path:
    root = tmp_path_factory.mktemp("budget") / "repo"
    (root / "src" / "amber").mkdir(parents=True)
    (root / "src" / "amber" / "ids.py").write_text("X = 1\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Budget Test")
    _git(root, "config", "user.email", "budget@example.invalid")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "switch", "-q", "-c", "work")
    capsys.readouterr()
    return root


def _write(repo: Path, path: str, lines: int) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(f"value_{n} = {n}\n" for n in range(lines)))


def _budget(repo: Path, *args: str) -> int:
    return int(diff_budget.main(["--root", str(repo), "--base", "main", *args]))


def test_a_small_change_is_within_budget(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo, "src/amber/graph.py", 20)
    assert _budget(repo, "--enforce") == 0
    out = capsys.readouterr().out
    assert "20 lines in 1 file" in out
    assert "warning" not in out


@pytest.mark.parametrize(
    ("files", "lines", "warned"),
    [(1, 401, True), (1, 400, False), (13, 1, True), (12, 1, False)],
    ids=["401-lines", "400-lines", "13-files", "12-files"],
)
def test_a_large_change_warns(
    repo: Path, capsys: pytest.CaptureFixture[str], files: int, lines: int, warned: bool
) -> None:
    for n in range(files):
        _write(repo, f"tests/test_{n}.py", lines)
    assert _budget(repo, "--enforce") == 0
    assert ("warning: large change" in capsys.readouterr().out) == warned


@pytest.mark.parametrize(
    ("files", "lines", "failed"),
    [(1, 801, True), (1, 800, False), (26, 1, True), (25, 1, False)],
    ids=["801-lines", "800-lines", "26-files", "25-files"],
)
def test_a_very_large_change_fails_in_ci_without_the_label(
    repo: Path, capsys: pytest.CaptureFixture[str], files: int, lines: int, failed: bool
) -> None:
    for n in range(files):
        _write(repo, f"tests/test_{n}.py", lines)
    assert _budget(repo, "--enforce") == (diff_budget.REGRESSION if failed else 0)
    assert ("large-change" in capsys.readouterr().out) == failed


def test_the_label_approves_a_very_large_change(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo, "tests/test_big.py", 900)
    assert _budget(repo, "--enforce", "--labels", "contract-approved,large-change") == 0
    assert "approved by the large-change label" in capsys.readouterr().out


def test_locally_a_very_large_change_is_a_warning(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo, "tests/test_big.py", 900)
    assert _budget(repo) == 0
    assert "warning" in capsys.readouterr().out


@pytest.mark.parametrize("committed", [True, False])
def test_generated_and_locked_files_do_not_count(
    repo: Path, capsys: pytest.CaptureFixture[str], committed: bool
) -> None:
    for path in (
        "uv.lock",
        "tools/opencode-check/package-lock.json",
        ".beads/issues.jsonl",
        "src/amber/conformance/golden/case.json",
        "docs/architecture/deps.md",
        "quality/baseline.json",
    ):
        _write(repo, path, 900)
    if committed:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "generated")
    assert _budget(repo, "--enforce") == 0
    assert "0 lines in 0 files" in capsys.readouterr().out


def test_spreading_over_many_subpackages_warns(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in (
        "graph.py",
        "tools/cite.py",
        "bundles/core.py",
        "loupe/adapter.py",
        "schemas/x.py",
    ):
        _write(repo, f"src/amber/{name}", 2)
    assert _budget(repo, "--enforce") == 0
    out = capsys.readouterr().out
    assert "warning: touches 4 amber subpackages" in out
    for unit in ("tools", "bundles", "loupe", "schemas"):
        assert f"  {unit}/: 2 lines in 1 file" in out
    assert "  (top-level modules): 2 lines in 1 file" in out


def test_top_level_modules_count_as_one_unit(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in (
        "graph.py",
        "cases.py",
        "_graph_validation.py",
        "graph_errors.py",
        "tools/cite.py",
    ):
        _write(repo, f"src/amber/{name}", 2)
    assert _budget(repo) == 0
    out = capsys.readouterr().out
    assert "subpackages" not in out
    assert "  (top-level modules): 8 lines in 4 files" in out


def test_committed_deleted_and_untracked_changes_count(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo, "tests/test_committed.py", 300)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "committed")
    (repo / "src" / "amber" / "ids.py").unlink()
    _write(repo, "tests/test_untracked.py", 300)
    assert _budget(repo) == 0
    assert "601 lines in 3 files" in capsys.readouterr().out


def test_an_unknown_base_is_an_error(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert diff_budget.main(["--root", str(repo), "--base", "nowhere"]) == 1
    assert "nowhere" in capsys.readouterr().out


@pytest.mark.parametrize(
    "path",
    [
        "docs/architecture/notes.md",
        "src/amber/data.lock",
        "src/amber/golden/x.json",
        "src/amber/conformance/golden/nested/x.json",
    ],
)
def test_hand_written_files_near_generated_ones_count(
    repo: Path, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    _write(repo, path, 5)
    assert _budget(repo) == 0
    assert "5 lines in 1 file" in capsys.readouterr().out


def test_exactly_three_subpackages_do_not_warn(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("graph.py", "tools/cite.py", "bundles/core.py", "loupe/adapter.py"):
        _write(repo, f"src/amber/{name}", 2)
    assert _budget(repo) == 0
    assert "subpackages" not in capsys.readouterr().out
