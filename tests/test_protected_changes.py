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


@pytest.mark.parametrize(
    "path",
    [
        ".claude/settings.json",
        ".claude/hooks/stop_gate.py",
        ".opencode/plugin/harness.ts",
        ".opencode/agents/reviewer.md",
        ".claude/agents/reviewer.md",
        "docs/review_prompt.md",
        "opencode.json",
        ".pre-commit-config.yaml",
        ".github/workflows/ci.yml",
        "Makefile",
        "scripts/check_protected_changes.py",
    ],
)
def test_harness_files_are_protected(repo: Path, path: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text("changed\n")
    assert _found(repo) == [path]


@pytest.mark.parametrize(
    "path",
    [
        ".claude/settings.local.json",
        ".claude/skills/x/SKILL.md",
        ".opencode/skills/x/SKILL.md",
        "docs/review_notes.md",
        "scripts/other.py",
    ],
)
def test_neighbouring_files_are_not_protected(repo: Path, path: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text("changed\n")
    assert _found(repo) == []


def _staged(repo: Path, monkeypatch: pytest.MonkeyPatch, allow: str | None) -> int:
    _git(repo, "add", "-A")
    monkeypatch.chdir(repo)
    if allow is None:
        monkeypatch.delenv("ALLOW_ORACLE_CHANGE", raising=False)
    else:
        monkeypatch.setenv("ALLOW_ORACLE_CHANGE", allow)
    return guard.main(["--staged"])


@pytest.mark.parametrize("allow", [None, "", "0", "true", "yes"])
def test_staged_protected_changes_need_the_environment_variable(
    repo: Path, monkeypatch: pytest.MonkeyPatch, allow: str | None
) -> None:
    (repo / "src/amber/conformance/catalog.json").write_text('{"x": 1}\n')
    assert _staged(repo, monkeypatch, allow) == 1


def test_allow_oracle_change_approves_staged_changes(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(test.read_text().replace("    assert value == 1\n", ""))
    assert _staged(repo, monkeypatch, "1") == 0
    output = capsys.readouterr().out
    assert "tests/test_x.py:3" in output and "value == 1" not in output


def test_unprotected_staged_changes_pass(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "src" / "amber" / "other.py").write_text("Y = 2\n")
    assert _staged(repo, monkeypatch, None) == 0


def test_the_environment_variable_never_approves_a_pull_request_diff(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    (repo / "src/amber/conformance/catalog.json").write_text('{"x": 1}\n')
    _git(repo, "add", "-A")
    diff = tmp_path_factory.mktemp("diff") / "changes.diff"
    diff.write_text(_git(repo, "diff", "--cached", "--unified=0", "HEAD"))
    monkeypatch.setenv("ALLOW_ORACLE_CHANGE", "1")
    assert guard.main([str(diff), "--labels", ""]) == 1


# Moving tests out of make check (amber-a6d.16) -------------------------------------------------

PYPROJECT = """[project]
name = "x"
dependencies = ["pydantic"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["slow: long-running"]
"""


def _with_pyproject(repo: Path) -> Path:
    (repo / "pyproject.toml").write_text(PYPROJECT)
    (repo / "tests" / "conftest.py").write_text("PROFILE = 'dev'\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "config")
    return repo


@pytest.mark.parametrize("path", ["tests/conftest.py", "tests/sub/conftest.py"])
def test_conftest_files_are_protected(repo: Path, path: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text("PROFILE = 'quick'\n")
    assert _found(repo) == [path]


@pytest.mark.parametrize(
    ("old", "new", "line"),
    [
        ('testpaths = ["tests"]\n', 'testpaths = ["tests/unit"]\n', 6),
        (
            'markers = ["slow: long-running"]\n',
            'markers = ["slow: long-running"]\naddopts = "-m fast"\n',
            8,
        ),
        (
            "[tool.pytest.ini_options]\n",
            "[tool.pytest.ini_options]\nfilterwarnings = ['ignore']\n",
            6,
        ),
    ],
    ids=["edited-key", "added-addopts", "added-filterwarnings"],
)
def test_pytest_configuration_changes_are_found(repo: Path, old: str, new: str, line: int) -> None:
    _with_pyproject(repo)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace(old, new))
    assert _found(repo) == [f"pyproject.toml:{line}"]


def test_other_pyproject_changes_are_unrestricted(repo: Path) -> None:
    _with_pyproject(repo)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('["pydantic"]', '["pydantic", "ulid"]'))
    assert _found(repo) == []


@pytest.mark.parametrize(
    "added",
    [
        "@pytest.mark.slow\n",
        "@pytest.mark.skip(reason='later')\n",
        "@pytest.mark.skipif(True, reason='x')\n",
        "@pytest.mark.xfail\n",
        "@settings(max_examples=5)\n",
    ],
    ids=["slow", "skip", "skipif", "xfail", "max-examples"],
)
def test_added_marks_that_deselect_tests_are_found(repo: Path, added: str) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(test.read_text().replace("def test_one():\n", added + "def test_one():\n"))
    assert _found(repo) == ["tests/test_x.py:1"]


@pytest.mark.parametrize(
    "body",
    [
        "    pytest.skip('flaky')\n",
        "    pytest.xfail('flaky')\n",
        "    pytest.importorskip('numpy')\n",
    ],
    ids=["skip-call", "xfail-call", "importorskip"],
)
def test_added_skip_calls_are_found(repo: Path, body: str) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(test.read_text().replace("    value = 1\n", body + "    value = 1\n"))
    assert _found(repo) == ["tests/test_x.py:2"]


def test_marks_outside_tests_and_unchanged_marks_are_unrestricted(repo: Path) -> None:
    (repo / "src" / "amber" / "other.py").write_text("# pytest.mark.skip is just text here\n")
    assert _found(repo) == []
    test = repo / "tests" / "test_x.py"
    test.write_text("import pytest\n\n\n@pytest.mark.slow\n" + test.read_text())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "mark")
    test.write_text(test.read_text() + "\n\ndef test_three():\n    assert True\n")
    assert _found(repo) == []


@pytest.mark.parametrize(
    "added",
    [
        "pytestmark = pytest.mark.slow\n",
        "CASES = [pytest.param(1, marks=pytest.mark.xfail)]\n",
    ],
    ids=["module-mark", "param-mark"],
)
def test_module_and_param_marks_are_found(repo: Path, added: str) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(added + test.read_text())
    assert _found(repo) == ["tests/test_x.py:1"]


def test_mark_names_inside_strings_and_comments_are_not_flagged(repo: Path) -> None:
    test = repo / "tests" / "test_x.py"
    added = (
        'EXAMPLES = ["@pytest.mark.slow\\n", "pytest.skip(\'x\')"]\n'
        "# pytest.mark.xfail is described here\n"
    )
    test.write_text(added + test.read_text())
    assert _found(repo) == []


def test_a_removed_pytest_configuration_line_is_found(repo: Path) -> None:
    _with_pyproject(repo)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('markers = ["slow: long-running"]\n', ""))
    assert _found(repo) == ["pyproject.toml:7"]


def test_quoted_test_data_is_not_flagged(repo: Path) -> None:
    test = repo / "tests" / "test_x.py"
    added = 'CASES = [\n    "x = [pytest.param(1, marks=pytest.mark.xfail)]",\n]\n'
    test.write_text(added + test.read_text())
    assert _found(repo) == []
