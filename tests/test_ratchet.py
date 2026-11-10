"""The quality ratchet (amber-7q0.4): violation counts may fall but never rise, and new code is
held to the full standard. Each test runs the real Ruff, mypy, and git on a small repository."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "quality" / "ratchet.py"
_spec = importlib.util.spec_from_file_location("ratchet", SCRIPT)
assert _spec is not None and _spec.loader is not None
ratchet = importlib.util.module_from_spec(_spec)
sys.modules["ratchet"] = ratchet
_spec.loader.exec_module(ratchet)

HUMAN = {"ALLOW_ORACLE_CHANGE": "1"}


def _complex(name: str, branches: int) -> str:
    """A typed function whose cyclomatic complexity is ``branches + 1``; its lines mention its
    name, so two such functions are not duplicated code."""
    body = "".join(
        f"    if x == {i}:\n        return {i} + len({name!r})\n" for i in range(branches)
    )
    return f"def {name}(x: int) -> int:\n{body}    return -1\n"


LEGACY = (
    "import os\n\n\n"
    + _complex("tangled", 11)
    + "\n\ndef untyped(x):\n    return x\n\n\nWIDTH = 3  # noqa: E501\n"
)


USES = """def test_names(pkg: object) -> None:
    for name in (pkg.tangled, pkg.small, pkg.untyped, pkg.more, pkg.fresh, pkg.public, pkg.f):
        assert name
    assert (pkg.WIDTH, pkg.LIMIT, pkg.SPAN, pkg.VALUE)
"""


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return done.stdout


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _run(repo: Path, *args: str, env: dict[str, str] | None = None) -> int:
    return int(ratchet.main(["--root", str(repo), "--base", "main", *args], env=env or {}))


def _baseline(repo: Path) -> dict[str, int]:
    return dict(json.loads((repo / "quality" / "baseline.json").read_text())["metrics"])


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A repository on a work branch off main, whose baseline records one F401, one mypy-strict
    error, one module over the complexity limit (12), and one unjustified noqa. Built once; each
    test gets a copy, with the tools' caches warm."""
    root = tmp_path_factory.mktemp("ratchet") / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "quality").mkdir()
    (root / "ruff.toml").write_text('[lint]\nselect = ["E", "F"]\n')
    (root / "quality" / "ruff-ratchet.toml").write_text('extend = "../ruff.toml"\n')
    (root / "quality" / "pinned-config.toml").write_text("# Deliberately empty.\n")
    (root / ".gitignore").write_text(".mypy_cache/\n.ruff_cache/\n")
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "legacy.py").write_text(LEGACY)
    (root / "src" / "pkg" / "small.py").write_text(_complex("small", 2))
    # The names these tests give to code count as used, as a library's tests use its API.
    (root / "tests").mkdir()
    (root / "tests" / "test_pkg.py").write_text(USES)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Ratchet Test")
    _git(root, "config", "user.email", "ratchet@example.invalid")
    _commit(root, "legacy")
    assert ratchet.main(["--root", str(root), "--base", "main", "--write-baseline"], env=HUMAN) == 0
    _commit(root, "baseline")
    _git(root, "switch", "-q", "-c", "work")
    return root


@pytest.fixture
def repo(
    template: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> Path:
    # Not under pytest's per-test directory: vulture treats any path matching */test*.py as a
    # test file, and that directory is named after the test.
    copy = tmp_path_factory.mktemp("work") / "repo"
    shutil.copytree(template, copy, symlinks=True)
    capsys.readouterr()
    return copy


def test_the_baseline_records_legacy_violations(repo: Path) -> None:
    metrics = _baseline(repo)
    assert metrics["ruff:F401"] == 1
    assert metrics["complexity:src/pkg/legacy.py"] == 12
    assert "complexity:src/pkg/small.py" not in metrics
    assert metrics["suppressions:noqa"] == 1
    assert metrics["suppressions:type-ignore"] == 0
    assert metrics["suppressions:unjustified"] == 1
    assert metrics["mypy-strict"] == 1


def test_an_unchanged_tree_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(repo) == 0
    assert "ratchet ok" in capsys.readouterr().out


def test_a_new_violation_in_an_existing_file_fails_with_old_new_and_location(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text("import sys\n" + legacy.read_text())
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "ruff:F401 rose 1 -> 2" in out
    assert "src/pkg/legacy.py:1:F401" in out
    assert "src/pkg/legacy.py:2:F401" not in out


def test_a_regression_without_an_added_line_lists_every_finding(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text("import sys\n" + legacy.read_text())
    _commit(repo, "edited")
    _git(repo, "switch", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "work")
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "ruff:F401 rose 1 -> 2" in out
    assert "src/pkg/legacy.py:1:F401" in out
    assert "src/pkg/legacy.py:2:F401" in out


def test_an_unjustified_noqa_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    small = repo / "src" / "pkg" / "small.py"
    small.write_text(small.read_text() + "LIMIT = 3  # noqa: E501\n")
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "suppressions:unjustified rose 1 -> 2" in out
    assert "suppressions:noqa rose 1 -> 2" in out
    assert "src/pkg/small.py:" in out
    assert ":unjustified # noqa: E501" in out


def test_an_unjustified_suppression_fails_even_when_the_count_holds(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(legacy.read_text().replace("  # noqa: E501", ""))
    small = repo / "src" / "pkg" / "small.py"
    small.write_text(small.read_text() + "LIMIT = 3  # noqa: E501\n")
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "rose" not in out
    assert "new code must be clean" in out
    assert "src/pkg/small.py:" in out


@pytest.mark.parametrize(
    "comment",
    [
        "# noqa: E501 - the generated table",
        "# noqa: E501 -- the generated table",
        "# noqa: E501 \u2014 the generated table",
    ],
)
def test_a_justified_suppression_may_replace_a_removed_one(
    repo: Path, capsys: pytest.CaptureFixture[str], comment: str
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(legacy.read_text().replace("  # noqa: E501", ""))
    small = repo / "src" / "pkg" / "small.py"
    small.write_text(small.read_text() + f"LIMIT = 3  {comment}\n")
    assert _run(repo) == 0, capsys.readouterr().out


def test_every_added_suppression_counts_even_when_justified(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    small = repo / "src" / "pkg" / "small.py"
    small.write_text(small.read_text() + "LIMIT = 3  # noqa: E501 - the generated table\n")
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "suppressions:noqa rose 1 -> 2" in out
    assert "suppressions:unjustified" not in out
    assert "new code must be clean" not in out


def test_a_justified_type_ignore_is_valid_for_mypy(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(
        legacy.read_text().replace("  # noqa: E501", "")
        + "\n\nVALUE: int = 'text'  # type: ignore[assignment]  # - a fixture\n"
    )
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "suppressions:type-ignore rose 0 -> 1" in out
    assert "mypy-strict" not in out
    assert "unjustified rose" not in out
    assert "new code must be clean" not in out


@pytest.mark.parametrize(
    ("line", "found"),
    [
        ("x = 1  # noqa", [("noqa", "unjustified")]),
        ("x = 1  # noqa:E501,F401", [("noqa", "unjustified")]),
        ("x = 1  # noqa: E501 -", [("noqa", "unjustified")]),
        ("x = 1  # noqa: E501 - # another comment", [("noqa", "unjustified")]),
        ("x = 1  # type: ignore", [("type-ignore", "unjustified")]),
        ("x = 1  # type:ignore[a, b]  -  why", [("type-ignore", "justified")]),
        ("x = 1  # type: ignore[misc]  # - why", [("type-ignore", "justified")]),
        ("x = 1  # type: ignore[misc]  # why", [("type-ignore", "unjustified")]),
        ("x = 1  # pragma: no cover \u2014 why", [("no-cover", "justified")]),
        ("x = 1  # pragma: no cover", [("no-cover", "unjustified")]),
        (
            "x = 1  # type: ignore[misc]  # noqa: E501 - long",
            [("type-ignore", "unjustified"), ("noqa", "justified")],
        ),
        ("x = 1  # NOQA: E501", [("noqa", "unjustified")]),
        ("x = 1  #noqa", [("noqa", "unjustified")]),
        ("x = 1  # Type: Ignore", [("type-ignore", "unjustified")]),
        ("x = 1  # pragma: nocover", [("no-cover", "unjustified")]),
        ("x = 1  # PRAGMA: NO COVER - why", [("no-cover", "justified")]),
        ("# ruff: noqa", [("noqa", "unjustified")]),
        ("# ruff: noqa: E501 - generated", [("noqa", "unjustified")]),
        ("# ruff: noqa - generated", [("noqa", "unjustified")]),
        ("# flake8: noqa", [("noqa", "unjustified")]),
        ("# mypy: ignore-errors", [("type-ignore", "unjustified")]),
        ("# mypy: disable-error-code=misc", [("type-ignore", "unjustified")]),
        ("# pylint: disable=duplicate-code", [("noqa", "unjustified")]),
        ("x = 1  # pylint: disable=R0801 - reason", [("noqa", "unjustified")]),
        ("x = 1  # PyLint:disable=all", [("noqa", "unjustified")]),
        ("x = '# noqa'", []),
        ("x = 1  # we never noqa here", []),
    ],
)
def test_suppressions_are_read_from_comments(line: str, found: list[tuple[str, str]]) -> None:
    seen = [(kind, f.code) for kind, f in ratchet.suppressions("m.py", line + "\n")]
    assert seen == found


def test_complexity_over_the_limit_in_a_new_file_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "src" / "pkg" / "fresh.py").write_text(_complex("fresh", 10))
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "complexity:src/pkg/fresh.py rose 10 -> 11" in out
    assert "src/pkg/fresh.py:1:C901" in out


def test_complexity_at_the_limit_in_a_new_file_passes(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "src" / "pkg" / "fresh.py").write_text(_complex("fresh", 9))
    assert _run(repo) == 0, capsys.readouterr().out


def test_a_module_under_the_limit_may_grow_to_it(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "src" / "pkg" / "small.py").write_text(_complex("small", 9))
    assert _run(repo) == 0, capsys.readouterr().out


def test_a_module_over_the_limit_may_not_grow(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(LEGACY.replace(_complex("tangled", 11), _complex("tangled", 12)))
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "complexity:src/pkg/legacy.py rose 12 -> 13" in out


def test_a_renamed_module_keeps_its_complexity_ceiling(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(repo, "mv", "src/pkg/legacy.py", "src/pkg/moved.py")
    assert _run(repo) == 0, capsys.readouterr().out


def test_a_mypy_strict_error_in_a_new_file_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "src" / "pkg" / "untyped.py").write_text("def f(x):\n    return x\n")
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "mypy-strict rose 1 -> 2" in out
    assert "src/pkg/untyped.py:1:no-untyped-def" in out


@pytest.mark.parametrize("committed", [True, False])
def test_a_ruff_violation_in_a_new_file_fails_even_when_the_count_holds(
    repo: Path, capsys: pytest.CaptureFixture[str], committed: bool
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(legacy.read_text().replace("import os\n", ""))
    (repo / "src" / "pkg" / "fresh.py").write_text("import os\n")
    if committed:
        _commit(repo, "swap the unused import into a new file")
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "ruff:F401 rose" not in out
    assert "new code must be clean" in out
    assert "src/pkg/fresh.py:1:F401" in out


def test_a_fixed_violation_prints_the_tighten_message(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(legacy.read_text().replace("import os\n", ""))
    assert _run(repo) == 0
    assert "ratchet can be tightened: ruff:F401 1 -> 0" in capsys.readouterr().out


def test_rebaseline_refuses_to_loosen(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = (repo / "quality" / "baseline.json").read_text()
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text("import sys\n" + legacy.read_text())
    assert _run(repo, "--write-baseline", env=HUMAN) == ratchet.REGRESSION
    assert "refusing to rebaseline" in capsys.readouterr().out
    assert (repo / "quality" / "baseline.json").read_text() == before


def test_rebaseline_needs_a_human(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = (repo / "quality" / "baseline.json").read_text()
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(legacy.read_text().replace("import os\n", ""))
    assert _run(repo, "--write-baseline") == 1
    assert "ALLOW_ORACLE_CHANGE=1" in capsys.readouterr().out
    assert (repo / "quality" / "baseline.json").read_text() == before


def test_rebaseline_tightens(repo: Path) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(legacy.read_text().replace("import os\n", ""))
    assert _run(repo, "--write-baseline", env=HUMAN) == 0
    assert "ruff:F401" not in _baseline(repo)


def test_a_missing_baseline_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "quality" / "baseline.json").unlink()
    assert _run(repo) == 1
    assert "make quality-rebaseline" in capsys.readouterr().out


def test_different_tool_versions_warn(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = repo / "quality" / "baseline.json"
    stored = json.loads(path.read_text())
    stored["tools"]["ruff"] = "0.0.1"
    path.write_text(json.dumps(stored))
    assert _run(repo) == 0
    assert "warning: tools" in capsys.readouterr().out


def test_import_linter_ignores_are_counted(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / ".importlinter").write_text(
        "[importlinter]\nroot_package = pkg\n\n[importlinter:contract:one]\nname = One\n"
        "type = forbidden\nsource_modules = pkg\nforbidden_modules = os\n"
        "ignore_imports =\n    pkg.legacy -> os\n"
    )
    assert _run(repo) == ratchet.REGRESSION
    assert "importlinter:ignore_imports rose 0 -> 1" in capsys.readouterr().out


def test_an_unknown_base_is_an_error(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ratchet.main(["--root", str(repo), "--base", "nowhere"], env={}) == 1
    assert "no merge base with nowhere" in capsys.readouterr().out


@pytest.mark.parametrize(
    "directive", ["# ruff: noqa\n", "# flake8: noqa\n", "# mypy: ignore-errors\n"]
)
def test_a_file_level_directive_in_a_new_file_fails(
    repo: Path, capsys: pytest.CaptureFixture[str], directive: str
) -> None:
    (repo / "src" / "pkg" / "fresh.py").write_text(directive + "import os\n")
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "new code must be clean" in out
    assert f"src/pkg/fresh.py:1:unjustified {directive.strip()}" in out


@pytest.mark.parametrize("noqa", ["# noqa: C901", "# NOQA: C901 - it is fine"])
def test_a_noqa_does_not_hide_complexity(
    repo: Path, capsys: pytest.CaptureFixture[str], noqa: str
) -> None:
    source = _complex("fresh", 12).replace("-> int:", f"-> int:  {noqa}", 1)
    (repo / "src" / "pkg" / "fresh.py").write_text(source)
    assert _run(repo) == ratchet.REGRESSION
    assert "complexity:src/pkg/fresh.py rose 10 -> 13" in capsys.readouterr().out


def test_a_violation_on_an_added_line_fails_even_when_the_count_holds(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(legacy.read_text().replace("import os\n", ""))
    small = repo / "src" / "pkg" / "small.py"
    small.write_text("import os\n" + small.read_text())
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "ruff:F401 rose" not in out
    assert "src/pkg/small.py:1:F401" in out


def test_a_mypy_strict_error_in_a_new_file_fails_even_when_the_count_holds(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(legacy.read_text().replace("def untyped(x):", "def untyped(x: int) -> int:"))
    (repo / "src" / "pkg" / "untyped.py").write_text("def f(x):\n    return x\n")
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "mypy-strict rose" not in out
    assert "src/pkg/untyped.py:1:no-untyped-def" in out


def test_a_gitignored_tracked_file_is_still_linted(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / ".gitignore").write_text((repo / ".gitignore").read_text() + "hidden.py\n")
    (repo / "src" / "pkg" / "hidden.py").write_text("import os\n")
    _git(repo, "add", "-f", "src/pkg/hidden.py")
    assert _run(repo) == ratchet.REGRESSION
    assert "src/pkg/hidden.py:1:F401" in capsys.readouterr().out


@pytest.mark.parametrize(
    "config",
    [
        ["diff.mnemonicPrefix", "true"],
        ["diff.noprefix", "true"],
        ["diff.srcPrefix", "x/"],
        ["diff.dstPrefix", "y/"],
    ],
)
def test_added_lines_do_not_depend_on_git_diff_config(
    repo: Path, capsys: pytest.CaptureFixture[str], config: list[str]
) -> None:
    _git(repo, "config", *config)
    small = repo / "src" / "pkg" / "small.py"
    small.write_text(small.read_text() + "LIMIT = 3  # noqa: E501\n")
    _commit(repo, "added an unjustified noqa")
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(legacy.read_text().replace("  # noqa: E501", ""))
    assert _run(repo) == ratchet.REGRESSION
    assert "src/pkg/small.py:7:unjustified" in capsys.readouterr().out


def test_hunk_bodies_are_not_read_as_file_headers() -> None:
    diff = (
        "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
        "@@ -1,1 +1,2 @@\n--- old\n+++ b/elsewhere.py\n+x = 1\n@@ -9,0 +10 @@\n+y = 2\n"
        "diff --git a/n p.py b/n p.py\n--- a/n p.py\t\n+++ b/n p.py\t\n@@ -0,0 +3 @@\n+y\n"
    )
    assert ratchet.added_lines(diff) == {"m.py": {1, 2, 10}, "n p.py": {3}}


def test_renamed_and_spaced_paths_are_read(repo: Path) -> None:
    _git(repo, "mv", "src/pkg/small.py", "src/pkg/small moved.py")
    (repo / "src" / "pkg" / "new one.py").write_text("X = 1\n")
    changes = ratchet.changed_since(repo, "main")
    assert changes.renamed_from == {"src/pkg/small moved.py": "src/pkg/small.py"}
    assert "src/pkg/new one.py" in changes.added_files
    assert changes.added_lines["src/pkg/new one.py"] == frozenset({1, 2})


def test_an_untracked_nested_repository_is_skipped(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(repo, "init", "-q", "nested")
    (repo / "nested" / "x.txt").write_text("x\n")
    assert _run(repo) == 0, capsys.readouterr().out


def test_a_tool_failure_is_an_error_not_a_pass(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "ruff.toml").write_text("[lint]\nselect = 7\n")
    assert _run(repo) == 1
    assert "ratchet: " in capsys.readouterr().out


def test_bootstrapping_a_baseline_needs_a_human(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "quality" / "baseline.json").unlink()
    assert _run(repo, "--write-baseline") == 1
    assert "ALLOW_ORACLE_CHANGE=1" in capsys.readouterr().out
    assert not (repo / "quality" / "baseline.json").exists()


def test_the_ratchet_holds_quality_code_to_mypy_strict(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "quality" / "tool.py").write_text("def f(x):\n    return x\n")
    assert _run(repo) == ratchet.REGRESSION
    assert "quality/tool.py:1:no-untyped-def" in capsys.readouterr().out


def test_a_percent_sign_in_the_import_contracts_is_read_literally(repo: Path) -> None:
    (repo / ".importlinter").write_text(
        "[importlinter:contract:one]\nname = One\n"
        "ignore_imports =\n    pkg.a -> os  # 100% legacy\n"
    )
    assert [f.message for f in ratchet.import_ignores(repo)] == ["pkg.a -> os  # 100% legacy"]


WIDER = 'extend = "../ruff.toml"\n\n[lint]\nextend-select = ["T20"]\n'


def test_the_ratchet_reads_its_own_wider_ruff_config(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "quality" / "ruff-ratchet.toml").write_text(WIDER)
    small = repo / "src" / "pkg" / "small.py"
    small.write_text(small.read_text() + "print(1)\n")
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "ruff:T201 rose 0 -> 1" in out
    assert "src/pkg/small.py:7:T201" in out
    assert "ruff:F401" not in out


def test_lint_does_not_read_the_ratchet_config(repo: Path) -> None:
    (repo / "quality" / "ruff-ratchet.toml").write_text(WIDER)
    small = repo / "src" / "pkg" / "small.py"
    small.write_text(small.read_text() + "print(1)\n")
    lint = [sys.executable, "-m", "ruff", "check", "--select", "T20", "--quiet", "src/pkg/small.py"]
    assert subprocess.run(lint, cwd=repo, check=False).returncode == 1
    default = [sys.executable, "-m", "ruff", "check", "--quiet", "src/pkg/small.py"]
    assert subprocess.run(default, cwd=repo, check=False).returncode == 0


def test_loosening_the_baseline_takes_a_human_and_a_flag(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "quality" / "ruff-ratchet.toml").write_text(WIDER)
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(legacy.read_text() + "print(1)\n")
    before = (repo / "quality" / "baseline.json").read_text()
    assert _run(repo, "--write-baseline", env=HUMAN) == ratchet.REGRESSION
    assert _run(repo, "--write-baseline", "--loosen") == 1
    assert (repo / "quality" / "baseline.json").read_text() == before
    capsys.readouterr()
    assert _run(repo, "--write-baseline", "--loosen", env=HUMAN) == 0
    out = capsys.readouterr().out
    assert "loosened: ruff:T201 0 -> 1" in out
    assert "src/pkg/legacy.py:" in out
    assert ":T201 `print` found" in out
    assert _baseline(repo)["ruff:T201"] == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda legacy: "import sys\n" + legacy,
        lambda legacy: legacy.replace("WIDTH = 3", "WIDTH = 4  # noqa: E501\nSPAN = 5"),
        lambda legacy: legacy.replace(
            "def untyped(x):", "def untyped(x):\n    y = x\n    return y\n\n\ndef more(z):"
        ),
    ],
    ids=["ruff-code-already-recorded", "suppression", "mypy-strict"],
)
def test_loosen_refuses_rises_in_metrics_the_baseline_already_has(
    repo: Path, capsys: pytest.CaptureFixture[str], change: Callable[[str], str]
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(change(legacy.read_text()))
    before = (repo / "quality" / "baseline.json").read_text()
    assert _run(repo, "--write-baseline", "--loosen", env=HUMAN) == ratchet.REGRESSION
    assert "only new Ruff rule codes" in capsys.readouterr().out
    assert (repo / "quality" / "baseline.json").read_text() == before


def test_a_missing_ratchet_ruff_config_is_an_error(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "quality" / "ruff-ratchet.toml").unlink()
    assert _run(repo) == 1
    assert "ruff-ratchet.toml" in capsys.readouterr().out


def test_the_real_ratchet_config_resolves_its_paths_from_the_root(repo: Path) -> None:
    root = Path(__file__).parents[1]
    shutil.copy(root / "ruff.toml", repo / "ruff.toml")
    shutil.copy(root / "quality" / "ruff-ratchet.toml", repo / "quality" / "ruff-ratchet.toml")
    files = {
        "tests/test_fresh.py": "def test_fresh(x):\n    assert x == 3\n",
        "src/pkg/fresh.py": "def public(x: int) -> int:\n    return x\n",
        "examples/demo.py": "import os\n",
    }
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    found = ratchet.ruff_findings(repo, list(files), "--config", "quality/ruff-ratchet.toml")
    assert [(f.path, f.code) for f in found] == [("src/pkg/fresh.py", "D103")]


def test_loosen_only_applies_to_writing_the_baseline(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        _run(repo, "--loosen")
    assert stopped.value.code == 2
    assert "--loosen only applies with --write-baseline" in capsys.readouterr().err


def test_loosen_refuses_a_new_module_over_the_complexity_limit(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "src" / "pkg" / "fresh.py").write_text(_complex("fresh", 12))
    assert _run(repo, "--write-baseline", "--loosen", env=HUMAN) == ratchet.REGRESSION
    assert "complexity:src/pkg/fresh.py 10 -> 13" in capsys.readouterr().out


def test_ci_gets_the_report_in_its_job_summary(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step\n")
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text("import sys\n" + legacy.read_text())
    assert _run(repo, env={"GITHUB_STEP_SUMMARY": str(summary)}) == ratchet.REGRESSION
    out = capsys.readouterr().out
    report = summary.read_text()
    assert report.startswith("earlier step\n### Quality ratchet\n")
    assert "ruff:F401 rose 1 -> 2" in report and "ruff:F401 rose 1 -> 2" in out


def test_strict_mypy_keeps_its_own_cache(repo: Path) -> None:
    """Sharing make typecheck's cache made each run re-check everything (options differ)."""
    assert _run(repo) == 0
    assert (repo / ".mypy_cache" / "strict").is_dir()


def test_the_report_and_summary_survive_an_unexpected_error(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing_run(*_: object) -> int:
        sys.stdout.write("ratchet: measuring\n")
        raise RuntimeError("tool output changed shape")

    monkeypatch.setattr(ratchet, "run", failing_run)
    summary = tmp_path / "summary.md"
    with pytest.raises(RuntimeError):
        _run(repo, env={"GITHUB_STEP_SUMMARY": str(summary)})
    assert "ratchet: measuring" in capsys.readouterr().out
    assert "ratchet: measuring" in summary.read_text()


def test_make_passes_a_base_through_to_the_ratchet() -> None:
    root = Path(__file__).parents[1]
    dry = ["make", "-n", "ratchet", "RATCHET_BASE=main"]
    done = subprocess.run(dry, cwd=root, capture_output=True, text=True, check=True)
    assert "quality/ratchet.py --base main" in done.stdout


def test_an_unused_function_in_a_new_file_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "src" / "pkg" / "fresh.py").write_text("def forgotten() -> int:\n    return 1\n")
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    # The legacy module's unused import is the baseline's one vulture finding.
    assert "vulture rose 1 -> 2" in out
    assert "src/pkg/fresh.py:1:vulture unused function 'forgotten'" in out


def test_an_unused_function_on_added_lines_fails_even_when_the_count_holds(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = repo / "src" / "pkg" / "legacy.py"
    legacy.write_text(legacy.read_text().replace("import os\n", ""))
    small = repo / "src" / "pkg" / "small.py"
    small.write_text(small.read_text() + "\n\ndef stray() -> int:\n    return 2\n")
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "vulture rose" not in out
    assert "new code must be clean" in out
    assert "unused function 'stray'" in out


def test_code_used_only_by_tests_or_the_whitelist_is_not_dead(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "src" / "pkg" / "api.py").write_text(
        "def tested() -> int:\n    return 1\n\n\ndef listed() -> int:\n    return 2\n"
    )
    (repo / "tests" / "test_api.py").write_text(
        "def test_it(pkg: object) -> None:\n    pkg.tested\n"
    )
    (repo / "quality" / "vulture_whitelist.py").write_text(
        '"""Known false positives."""\n\nfrom pkg.api import listed\n\n'
        "_ = listed  # consumers call it\n"
    )
    assert _run(repo) == 0, capsys.readouterr().out


def test_validators_and_pytest_hooks_are_not_dead(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "src" / "pkg" / "hooks.py").write_text(
        "def pytest_configure(config: object) -> object:\n    return config\n"
    )
    assert _run(repo) == 0, capsys.readouterr().out


COPIED = "".join(f"    total += {n} * value - {n + 1}\n" for n in range(10))


def test_a_copied_ten_line_block_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    block = "def {name}(value: int) -> int:\n    total = 0\n" + COPIED + "    return total\n"
    (repo / "src" / "pkg" / "first.py").write_text(block.format(name="first"))
    (repo / "src" / "pkg" / "second.py").write_text(block.format(name="second"))
    (repo / "tests" / "test_copies.py").write_text(
        "def test_it(pkg: object) -> None:\n    pkg.first, pkg.second\n"
    )
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "duplicate-code rose 0 -> 2" in out
    # pylint's range starts at the first similar line (the signature is ignored) and is
    # end-exclusive, so the block covers lines 2 to 13.
    assert "src/pkg/first.py:2:duplicate-code pkg.first:2, pkg.second:2" in out
    assert "src/pkg/second.py:2:duplicate-code" in out


def test_loosen_records_a_newly_adopted_tools_legacy_count(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = repo / "quality" / "baseline.json"
    stored = json.loads(path.read_text())
    del stored["metrics"]["vulture"]  # a baseline written before vulture was adopted
    path.write_text(json.dumps(stored))
    assert _run(repo, "--write-baseline", "--loosen", env=HUMAN) == 0
    out = capsys.readouterr().out
    assert "loosened: vulture 0 -> 1" in out
    assert "unused import 'os'" in out
    assert _baseline(repo)["vulture"] == 1


def test_pydantic_validators_are_not_dead(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "src" / "pkg" / "model.py").write_text(
        "from pydantic import BaseModel, field_validator\n\n\n"
        "class Width(BaseModel):\n"
        "    size: int\n\n"
        '    @field_validator("size")\n'
        "    @classmethod\n"
        "    def positive(cls, value: int) -> int:\n"
        "        return max(value, len(cls.__name__))\n"
    )
    (repo / "tests" / "test_model.py").write_text(
        "def test_it(pkg: object) -> None:\n    pkg.Width, pkg.size\n"
    )
    assert _run(repo) == 0, capsys.readouterr().out


@pytest.mark.parametrize(
    ("added", "unclean"), [({7}, True), ({3}, True), ({8}, True), ({9}, False), ({2}, False)]
)
def test_a_duplicate_range_is_new_when_any_of_its_lines_is(added: set[int], unclean: bool) -> None:
    block = ratchet.Finding("src/pkg/a.py", 3, "duplicate-code", "pkg.a:3, pkg.b:1", end=8)
    measurement = ratchet.Measurement(findings={"duplicate-code": [block]})
    changes = ratchet.Changes(frozenset(), {}, {"src/pkg/a.py": frozenset(added)})
    assert ratchet.unclean_new_code(measurement, changes) == ([block] if unclean else [])


def test_other_tool_config_cannot_hide_dead_code_or_duplicates(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "pyproject.toml").write_text(
        '[tool.vulture]\nexclude = ["src/pkg/fresh.py"]\nmin_confidence = 100\n\n'
        "[tool.pylint.similarities]\nmin-similarity-lines = 1000\n"
    )
    (repo / "pylintrc").write_text("[MAIN]\nignore=first.py,second.py\n")
    (repo / ".pylintrc").write_text("[MAIN]\nignore=first.py,second.py\n")
    (repo / "src" / "pkg" / "fresh.py").write_text("def forgotten() -> int:\n    return 1\n")
    block = "def {name}(value: int) -> int:\n    total = 0\n" + COPIED + "    return total\n"
    (repo / "src" / "pkg" / "first.py").write_text(block.format(name="first"))
    (repo / "src" / "pkg" / "second.py").write_text(block.format(name="second"))
    (repo / "tests" / "test_copies.py").write_text(
        "def test_it(pkg: object) -> None:\n    pkg.first, pkg.second\n"
    )
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "unused function 'forgotten'" in out
    assert "duplicate-code rose 0 -> 2" in out


def test_dead_code_named_like_a_test_in_src_is_still_dead(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "src" / "pkg" / "helpers.py").write_text("def test_helper() -> int:\n    return 1\n")
    assert _run(repo) == ratchet.REGRESSION
    assert "unused function 'test_helper'" in capsys.readouterr().out


def test_the_baseline_records_every_tool_version(repo: Path) -> None:
    tools = json.loads((repo / "quality" / "baseline.json").read_text())["tools"]
    assert set(tools) == {"ruff", "mypy", "vulture", "pylint"}


def test_an_unreadable_duplicate_report_is_an_error() -> None:
    message = {"symbol": "duplicate-code", "message": "Similar lines in 2 files\n==pkg.a:[1:9]\n"}
    with pytest.raises(ratchet.ToolError, match="duplicate-code"):
        ratchet.duplicate_ranges(message, {"pkg.a": "src/pkg/a.py"})


def test_two_bare_modules_with_one_name_are_an_error() -> None:
    names = ratchet.module_paths(["quality/x/tool.py", "quality/y/tool.py"])
    message = {"symbol": "duplicate-code", "message": "==tool:[1:9]\n==tool:[20:28]\n"}
    with pytest.raises(ratchet.ToolError, match="tool"):
        ratchet.duplicate_ranges(message, names)


def test_a_pylint_pragma_cannot_hide_a_copied_block(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = "def {name}(value: int) -> int:\n    total = 0\n" + COPIED + "    return total\n"
    (repo / "src" / "pkg" / "first.py").write_text(block.format(name="first"))
    hidden = "# pylint: disable=duplicate-code\n" + block.format(name="second")
    (repo / "src" / "pkg" / "second.py").write_text(hidden)
    (repo / "tests" / "test_copies.py").write_text(
        "def test_it(pkg: object) -> None:\n    pkg.first, pkg.second\n"
    )
    assert _run(repo) == ratchet.REGRESSION
    out = capsys.readouterr().out
    assert "suppressions:unjustified rose 1 -> 2" in out
    assert "src/pkg/second.py:1:unjustified # pylint: disable=duplicate-code" in out


def test_a_dead_function_named_like_a_pytest_hook_is_still_dead(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "src" / "pkg" / "hooks.py").write_text("def pytest_whatever() -> int:\n    return 1\n")
    assert _run(repo) == ratchet.REGRESSION
    assert "unused function 'pytest_whatever'" in capsys.readouterr().out
