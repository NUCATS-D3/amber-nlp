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
        'EXAMPLES = [\n    "@pytest.mark.slow",\n    "pytest.skip(\'x\')",\n]\n'
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


# Bypass routes found in review (amber-a6d.16) ---------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "conftest.py",
        "pytest.ini",
        ".pytest.ini",
        "tox.ini",
        "setup.cfg",
        "src/amber/conformance/plugin.py",
    ],
)
def test_other_pytest_configuration_files_are_protected(repo: Path, path: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text("[pytest]\naddopts = --ignore=tests\n")
    assert _found(repo) == [path]


@pytest.mark.parametrize(
    "added",
    [
        "@mark.skip\n",
        "@hypothesis.settings(max_examples=1)\n",
        "__test__ = False\n",
        "    pytest.mark.slow,\n",
        "SKIP = unittest.SkipTest\n",
    ],
    ids=["aliased-mark", "qualified-settings", "dunder-test", "multiline-mark", "skiptest"],
)
def test_other_spellings_of_deselection_are_found(repo: Path, added: str) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(added + test.read_text())
    assert _found(repo) == ["tests/test_x.py:1"]


@pytest.mark.parametrize(
    ("old", "new"),
    [("def test_one():", "def _test_one():"), ("def test_two():", "def check_two():")],
    ids=["underscore", "renamed"],
)
def test_renaming_a_test_function_is_found(repo: Path, old: str, new: str) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(test.read_text().replace(old, new))
    assert [item.split(":")[0] for item in _found(repo)] == ["tests/test_x.py"]


@pytest.mark.parametrize(
    ("source", "target", "flagged"),
    [
        ("tests/test_x.py", "tests/x_cases.py", ["tests/test_x.py"]),
        ("src/amber/conformance/catalog.json", "src/amber/conformance/old.json", None),
        ("tests/test_x.py", "tests/test_renamed.py", []),
    ],
    ids=["out-of-collection", "protected", "still-collected"],
)
def test_pure_renames_are_seen(
    repo: Path, source: str, target: str, flagged: list[str] | None
) -> None:
    _git(repo, "mv", source, target)
    diff = _git(repo, "diff", "--cached", "--unified=0", "HEAD")
    assert "rename from" in diff
    expected = [source] if flagged is None else flagged
    assert guard.violations(diff.splitlines(keepends=True)) == expected


def test_lines_that_look_like_headers_inside_hunks_are_content(repo: Path) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text('TEXT = """\n++ not a header\n"""\n@pytest.mark.skip\n' + test.read_text())
    assert _found(repo) == ["tests/test_x.py:4"]


def test_marks_in_a_new_test_file_are_found_at_their_line(repo: Path) -> None:
    (repo / "tests" / "test_new.py").write_text(
        "import pytest\n\n\n@pytest.mark.slow\ndef test_new():\n    assert True\n"
    )
    assert _found(repo) == ["tests/test_new.py:4"]


@pytest.mark.parametrize("path", ["Makefile", "tests/conftest.py", "tests/test_x.py"])
def test_binary_diffs_of_protected_or_test_files_are_found(repo: Path, path: str) -> None:
    (repo / ".gitattributes").write_text(f"{path} -diff\n")
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text("changed\n")
    _git(repo, "add", "-A")
    diff = _git(repo, "diff", "--cached", "--unified=0", "HEAD")
    assert "Binary files" in diff
    found = guard.violations(diff.splitlines(keepends=True))
    assert ".gitattributes" in found and path in found


def test_a_nul_byte_does_not_hide_a_new_pytest_ini(repo: Path) -> None:
    (repo / "pytest.ini").write_bytes(b"[pytest]\naddopts = --ignore=tests\n#\x00\n")
    assert _found(repo) == ["pytest.ini"]


@pytest.mark.parametrize(
    "added",
    [
        "pytest.exit('done', returncode=0)\n",
        "sys.exit(0)\n",
        "raise pytest.skip.Exception('x')\n",
        "settings.load_profile('quick')\n",
        "settings.register_profile('quick', phases=[Phase.explicit])\n",
        "pytest_plugins = ['amber.x']\n",
        "def pytest_collection_modifyitems(items):\n",
    ],
    ids=["pytest-exit", "sys-exit", "skip-exception", "load-profile", "phases", "plugins", "hook"],
)
def test_ways_to_end_or_empty_the_suite_are_found(repo: Path, added: str) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(added + test.read_text())
    assert _found(repo) == ["tests/test_x.py:1"]


def test_a_new_pytest_entry_point_is_found(repo: Path) -> None:
    _with_pyproject(repo)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text() + '\n[project.entry-points.pytest11]\nx = "amber.x"\n'
    )
    assert _found(repo) == ["pyproject.toml:9", "pyproject.toml:10"]


def test_editing_a_test_signature_is_not_a_rename(repo: Path) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(test.read_text().replace("def test_one():", "def test_one() -> None:"))
    assert _found(repo) == []


PLUGIN_PYPROJECT = """[project]
name = "x"
version = "0.1.0"

[project.entry-points.pytest11]
amber_conformance = "amber.conformance.plugin"
"""


def _with_plugin(repo: Path) -> Path:
    (repo / "pyproject.toml").write_text(PLUGIN_PYPROJECT)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "plugin")
    return repo / "pyproject.toml"


@pytest.mark.parametrize(
    ("old", "new", "flagged"),
    [
        (
            'amber_conformance = "amber.conformance.plugin"\n',
            'amber_conformance = "amber.conformance.plugin"\nextra = "amber.extra"\n',
            ["pyproject.toml:7"],
        ),
        (
            '"amber.conformance.plugin"',
            '"amber.conformance.other"',
            ["pyproject.toml:6"],
        ),
        (
            '"amber.conformance.plugin"',
            '"tests.helpers:plugin"',
            ["pyproject.toml:6"],
        ),
    ],
    ids=["added-entry", "retargeted", "retargeted-with-attr"],
)
def test_entry_points_under_an_existing_header_are_found(
    repo: Path, old: str, new: str, flagged: list[str]
) -> None:
    pyproject = _with_plugin(repo)
    pyproject.write_text(pyproject.read_text().replace(old, new))
    assert _found(repo) == flagged


def test_a_version_bump_is_not_an_entry_point(repo: Path) -> None:
    pyproject = _with_plugin(repo)
    pyproject.write_text(pyproject.read_text().replace('"0.1.0"', '"0.2.0"'))
    assert _found(repo) == []


@pytest.mark.parametrize(
    "added",
    ["def pytest_collection_modifyitems(items):\n", "pytest_plugins = ['tests.x']\n"],
    ids=["hook", "plugins"],
)
def test_collection_hooks_in_source_are_found(repo: Path, added: str) -> None:
    (repo / "src" / "amber" / "other.py").write_text(added + "Y = 1\n")
    assert _found(repo) == ["src/amber/other.py:1"]


@pytest.mark.parametrize(
    "added",
    [
        "m = pytest.mark\n",
        "later = skip\n",
        "raise Exit('x')\n",
        "raise pytest.exit.Exception()\n",
    ],
    ids=["mark-namespace", "bare-skip", "exit-class", "exit-exception"],
)
def test_aliases_of_deselection_are_found(repo: Path, added: str) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(added + test.read_text())
    assert _found(repo) == ["tests/test_x.py:1"]


def test_an_empty_new_protected_file_is_found(repo: Path) -> None:
    (repo / "pytest.ini").write_text("")
    assert _found(repo) == ["pytest.ini"]


@pytest.mark.parametrize(
    "added",
    [
        "    ROWS = (1, 2)  # fmt: skip\n",
        "    review(cases, skip=['case-1'])\n",
        "    TEXT = 'a # pytest.mark.skip in a string'\n",
    ],
    ids=["fmt-skip", "skip-keyword", "hash-in-string"],
)
def test_ordinary_test_lines_are_not_flagged(repo: Path, added: str) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(test.read_text().replace("    value = 1\n", "    value = 1\n" + added))
    assert _found(repo) == []


def test_a_mark_before_a_trailing_comment_is_still_found(repo: Path) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text("@pytest.mark.skip  # temporary\n" + test.read_text())
    assert _found(repo) == ["tests/test_x.py:1"]


def test_single_quoted_entry_points_are_found(repo: Path) -> None:
    pyproject = _with_plugin(repo)
    pyproject.write_text(pyproject.read_text() + "extra = 'amber.extra'\n")
    assert _found(repo) == ["pyproject.toml:7"]


def test_renaming_a_test_to_another_test_name_is_not_flagged(repo: Path) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text(test.read_text().replace("def test_one():", "def test_first():"))
    assert _found(repo) == []


@pytest.mark.parametrize("hidden", ["_test_one", "one", "disabled_one"])
def test_a_test_renamed_out_beside_a_filler_test_is_found(repo: Path, hidden: str) -> None:
    test = repo / "tests" / "test_x.py"
    renamed = test.read_text().replace("def test_one():", f"def {hidden}():")
    test.write_text(renamed + "\n\ndef test_filler():\n    assert True\n")
    assert _found(repo) == ["tests/test_x.py:1"]


@pytest.mark.parametrize("hidden", ["_TestOne", "One"])
def test_a_test_class_renamed_out_beside_a_filler_is_found(repo: Path, hidden: str) -> None:
    test = repo / "tests" / "test_x.py"
    test.write_text("class TestOne:\n    def test_a(self):\n        assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "class")
    renamed = test.read_text().replace("class TestOne:", f"class {hidden}:")
    test.write_text(renamed + "\n\ndef test_filler():\n    assert True\n")
    assert _found(repo) == ["tests/test_x.py:1"]
def test_staged_diffs_are_read_as_text(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A -diff attribute would hide the removed assertion behind "Binary files ... differ".
    (repo / ".gitattributes").write_text("tests/test_x.py -diff\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "attributes")
    test = repo / "tests" / "test_x.py"
    test.write_text(test.read_text().replace("    assert value == 1\n", ""))
    _git(repo, "add", "-A")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALLOW_ORACLE_CHANGE", raising=False)
    staged = subprocess.run(
        ["python3", str(SCRIPT), "--staged"], capture_output=True, text=True, check=False
    )
    assert staged.returncode == 1 and "tests/test_x.py:3" in staged.stdout


def test_binary_bytes_in_a_text_diff_do_not_crash_the_guard(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    (repo / "docs").mkdir()
    (repo / "docs" / "deck.pdf").write_bytes(b"%PDF\xff\xfe\x00\x80 binary\n")
    _git(repo, "add", "-A")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALLOW_ORACLE_CHANGE", raising=False)
    staged = subprocess.run(
        ["python3", str(SCRIPT), "--staged"], capture_output=True, text=True, check=False
    )
    assert staged.returncode == 0 and "Traceback" not in staged.stderr
    diff = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--text", "HEAD"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    path = tmp_path_factory.mktemp("diff") / "changes.diff"
    path.write_bytes(diff)
    from_file = subprocess.run(
        ["python3", str(SCRIPT), str(path)], capture_output=True, text=True, check=False
    )
    assert from_file.returncode == 0 and "Traceback" not in from_file.stderr
    from_stdin = subprocess.run(
        ["python3", str(SCRIPT)], input=diff, capture_output=True, check=False
    )
    assert from_stdin.returncode == 0 and b"Traceback" not in from_stdin.stderr


@pytest.mark.parametrize("prefix", ["\x0c", "\u2028", "\x85", "\r"], ids=["ff", "ls", "nel", "cr"])
def test_odd_line_breaks_do_not_hide_a_removed_assertion(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    prefix: str,
) -> None:
    test = repo / "tests" / "test_x.py"

    # newline="" keeps a lone carriage return as written instead of translating it.
    def read() -> str:
        with open(test, encoding="utf-8", newline="") as handle:
            return handle.read()

    def write(text: str) -> None:
        with open(test, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    write(read() + f"\n\ndef test_three():\n{prefix}    assert value\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "odd")
    edited = read().replace(f"{prefix}    assert value\n", "    pass\n")
    assert edited != read()
    write(edited)
    _git(repo, "add", "-A")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALLOW_ORACLE_CHANGE", raising=False)
    staged = subprocess.run(
        ["python3", str(SCRIPT), "--staged"], capture_output=True, text=True, check=False
    )
    assert staged.returncode == 1 and "tests/test_x.py:" in staged.stdout
    diff = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--text", "HEAD"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    path = tmp_path_factory.mktemp("diff") / "changes.diff"
    path.write_bytes(diff)
    from_file = subprocess.run(
        ["python3", str(SCRIPT), str(path)], capture_output=True, text=True, check=False
    )
    assert from_file.returncode == 1 and "tests/test_x.py:" in from_file.stdout
