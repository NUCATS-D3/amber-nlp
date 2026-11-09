"""Agent hooks (amber-a6d.4): the post-edit check and the stop gate, driven by synthetic hook
input and a fake command runner, so no linter, type checker, or test suite runs here."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

HOOKS = Path(__file__).parents[1] / ".claude" / "hooks"
ROOT = Path(__file__).parents[1]


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


post_edit = _load("post_edit")
stop_gate = _load("stop_gate")

Outcome = tuple[int, str]


class FakeRunner:
    """Records commands; answers each with the first outcome whose key is a prefix of it."""

    def __init__(self, outcomes: dict[tuple[str, ...], Outcome] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        command = tuple(command)
        self.commands.append(command)
        for key, (code, output) in self.outcomes.items():
            if command[: len(key)] == key:
                return subprocess.CompletedProcess(command, code, output, "")
        return subprocess.CompletedProcess(command, 0, "", "")


def _run(
    main: Callable[..., int],
    event: dict[str, Any],
    runner: FakeRunner,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, str]:
    code = main(io.StringIO(json.dumps(event)), runner)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _edit(path: str) -> dict[str, Any]:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "cwd": str(ROOT),
        "tool_input": {"file_path": str(ROOT / path)},
    }


# post_edit ---------------------------------------------------------------------------------

SRC_FILE = "src/amber/ids.py"
TEST_FILE = "tests/test_ids.py"


def test_a_source_file_is_fixed_formatted_and_type_checked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = FakeRunner()
    assert _run(post_edit.main, _edit(SRC_FILE), runner, capsys) == (0, "", "")
    tools = [command[3:5] for command in runner.commands]
    # Format first, so lines the formatter wraps are never reported as lint errors.
    assert tools == [("ruff", "format"), ("ruff", "check"), ("mypy", SRC_FILE)]
    assert "--fix" in runner.commands[1] and runner.commands[1][-1] == SRC_FILE


def test_a_test_file_is_linted_but_not_type_checked(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner()
    assert _run(post_edit.main, _edit(TEST_FILE), runner, capsys)[0] == 0
    assert [command[3] for command in runner.commands] == ["ruff", "ruff"]


@pytest.mark.parametrize(
    "event",
    [
        _edit("docs/contracts.md"),
        _edit("scripts/check_core_install.py"),
        _edit("src/amber/conformance/catalog.json"),
        _edit("src/amber/does_not_exist.py"),
        {**_edit(SRC_FILE), "tool_input": {"file_path": "/elsewhere/src/amber/ids.py"}},
        {**_edit(SRC_FILE), "tool_input": {}},
        {"hook_event_name": "PostToolUse"},
    ],
    ids=["markdown", "outside-src-and-tests", "json", "deleted", "outside-repo", "no-path", "bare"],
)
def test_other_edits_run_nothing(event: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner()
    assert _run(post_edit.main, event, runner, capsys) == (0, "", "")
    assert runner.commands == []


def test_a_relative_path_resolves_against_the_session_directory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = FakeRunner()
    event = {**_edit(SRC_FILE), "tool_input": {"file_path": SRC_FILE}}
    assert _run(post_edit.main, event, runner, capsys)[0] == 0
    assert len(runner.commands) == 3


def test_unreadable_input_does_not_block(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner()
    assert post_edit.main(io.StringIO("not json"), runner) == 0
    assert runner.commands == []


@pytest.mark.parametrize("tool", ["ruff check", "ruff format", "mypy"])
def test_a_failing_check_blocks_with_its_output(
    tool: str, capsys: pytest.CaptureFixture[str]
) -> None:
    key = ("uv", "run", "--no-sync", *tool.split())
    runner = FakeRunner({key: (1, f"{SRC_FILE}:2: error: something [code]\n")})
    code, out, err = _run(post_edit.main, _edit(SRC_FILE), runner, capsys)
    assert code == 2 and out == ""
    assert SRC_FILE in err and f"{SRC_FILE}:2: error: something [code]" in err
    assert tool in err


def test_long_output_is_truncated(capsys: pytest.CaptureFixture[str]) -> None:
    lines = "".join(f"{SRC_FILE}:{n}: error: e{n}\n" for n in range(500))
    runner = FakeRunner({("uv", "run", "--no-sync", "mypy"): (1, lines)})
    code, _, err = _run(post_edit.main, _edit(SRC_FILE), runner, capsys)
    assert code == 2 and "e0" in err and "e499" not in err and "more lines" in err


# stop_gate ---------------------------------------------------------------------------------

GIT_DIFF = ("git", "diff", "--name-only", "HEAD")
GIT_UNTRACKED = ("git", "ls-files", "--others", "--exclude-standard")
TESTS_OK = (0, "...\n===== 1543 passed, 8 warnings in 42.08s =====\n")


def _stop(active: bool = False) -> dict[str, Any]:
    return {"hook_event_name": "Stop", "cwd": str(ROOT), "stop_hook_active": active}


def _makes(runner: FakeRunner) -> list[str]:
    return [command[1] for command in runner.commands if command[0] == "make"]


def test_an_active_stop_hook_never_loops(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner({GIT_DIFF: (0, "src/amber/ids.py\n")})
    assert _run(stop_gate.main, _stop(active=True), runner, capsys) == (0, "", "")
    assert runner.commands == []


@pytest.mark.parametrize("changed", ["", "docs/plan.md\nREADME.md\n", "scripts/x.py\n"])
def test_no_source_or_test_changes_skip_the_gate(
    changed: str, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeRunner({GIT_DIFF: (0, changed)})
    assert _run(stop_gate.main, _stop(), runner, capsys)[0] == 0
    assert _makes(runner) == []


@pytest.mark.parametrize(
    "outcomes",
    [{GIT_DIFF: (0, "src/amber/ids.py\n")}, {GIT_UNTRACKED: (0, "tests/test_new.py\n")}],
    ids=["tracked", "untracked"],
)
def test_source_or_test_changes_run_lint_types_and_fast_tests(
    outcomes: dict[tuple[str, ...], Outcome], capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeRunner({**outcomes, ("make", "test-fast"): TESTS_OK})
    code, out, err = _run(stop_gate.main, _stop(), runner, capsys)
    assert code == 0 and err == ""
    assert _makes(runner) == ["lint", "typecheck", "test-fast"]
    assert out.startswith("check ok: 1543 passed")


def test_a_failing_test_blocks_with_the_failure_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = (
        "tests/test_ids.py::test_a PASSED\n"
        "FAILED tests/test_ids.py::test_b - AssertionError\n"
        "ERROR tests/test_ids.py::test_c - RuntimeError\n"
        "===== 2 failed, 1541 passed in 40.00s =====\n"
    )
    runner = FakeRunner({GIT_DIFF: (0, "src/amber/ids.py\n"), ("make", "test-fast"): (2, output)})
    code, out, err = _run(stop_gate.main, _stop(), runner, capsys)
    assert code == 2 and out == ""
    assert "FAILED tests/test_ids.py::test_b" in err and "ERROR tests/test_ids.py::test_c" in err
    assert "2 failed, 1541 passed" in err and "test_a PASSED" not in err


@pytest.mark.parametrize("target", ["lint", "typecheck"])
def test_lint_or_type_errors_block_and_tests_still_run(
    target: str, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeRunner(
        {
            GIT_DIFF: (0, "src/amber/ids.py\n"),
            ("make", target): (2, "src/amber/ids.py:3: error: bad\nmake: *** Error 1\n"),
            ("make", "test-fast"): TESTS_OK,
        }
    )
    code, _, err = _run(stop_gate.main, _stop(), runner, capsys)
    assert code == 2 and f"make {target}" in err and "src/amber/ids.py:3: error: bad" in err
    assert _makes(runner) == ["lint", "typecheck", "test-fast"]


def test_git_failure_runs_the_gate_anyway(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner({GIT_DIFF: (128, ""), ("make", "test-fast"): TESTS_OK})
    assert _run(stop_gate.main, _stop(), runner, capsys)[0] == 0
    assert _makes(runner) == ["lint", "typecheck", "test-fast"]


def test_the_opencode_plugin_runs_the_same_scripts() -> None:
    plugin = (ROOT / ".opencode" / "plugins" / "amber-harness.ts").read_text()
    assert '".claude", "hooks"' in plugin
    for script in ("post_edit.py", "stop_gate.py"):
        assert f'"{script}"' in plugin and (HOOKS / script).is_file()
    assert "stop_hook_active: active" in plugin
    assert '"session.execution.succeeded"' in plugin and '"execute.after"' in plugin


def test_settings_wire_both_hooks() -> None:
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    hooks = settings["hooks"]
    (post,) = hooks["PostToolUse"]
    assert set(post["matcher"].split("|")) >= {"Edit", "Write"}
    assert post["hooks"][0]["args"] == ["${CLAUDE_PROJECT_DIR}/.claude/hooks/post_edit.py"]
    (stop,) = hooks["Stop"]
    assert stop["hooks"][0]["args"] == ["${CLAUDE_PROJECT_DIR}/.claude/hooks/stop_gate.py"]


def test_the_opencode_check_pins_exact_versions_and_runs_in_ci() -> None:
    package = json.loads((ROOT / "tools" / "opencode-check" / "package.json").read_text())
    versions = package["devDependencies"]
    assert {"@opencode/plugin", "@opencode/schema", "typescript"} <= set(versions)
    assert all(version[0].isdigit() for version in versions.values()), "pins must be exact"
    assert (ROOT / "tools" / "opencode-check" / "package-lock.json").is_file()
    workflow = (ROOT / ".github" / "workflows" / "opencode-harness.yml").read_text()
    assert "npm ci" in workflow and "npm run check" in workflow
    plugin = (ROOT / ".opencode" / "plugins" / "amber-harness.ts").read_text()
    assert 'import type { Plugin } from "@opencode/plugin"' in plugin
    assert "satisfies Plugin.Plugin" in plugin and ": any" not in plugin
