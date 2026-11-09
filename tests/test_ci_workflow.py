"""CI runs the definition of done (amber-a6d.8): the same make targets as ``make check``, the
guard on text diffs (amber-a6d.17), and a scheduled mutation run. Read as text, so no YAML
dependency is needed."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
MUTATION = ROOT / ".github" / "workflows" / "mutation.yml"


def _check_prerequisites() -> list[str]:
    match = re.search(r"^check:([^#\n]*)", (ROOT / "Makefile").read_text(), re.MULTILINE)
    assert match is not None
    return match.group(1).split()


def _make_targets(workflow: str) -> set[str]:
    targets: set[str] = set()
    for line in workflow.splitlines():
        command = line.strip().removeprefix("run:").strip()
        if command.startswith("make "):
            targets.update(command.split()[1:])
    return targets


def test_ci_runs_every_check_target_except_the_editable_sync() -> None:
    prerequisites = _check_prerequisites()
    assert "sync" in prerequisites and "test-fast" in prerequisites
    missing = set(prerequisites) - {"sync"} - _make_targets(CI)
    assert not missing, f"CI skips make check targets: {sorted(missing)}"


def test_ci_no_longer_duplicates_the_commands_make_runs() -> None:
    for command in ("uv run --no-sync pytest", "uv run --no-sync ruff", "uv run --no-sync mypy"):
        assert command not in CI


def test_the_guard_reads_text_diffs() -> None:
    (diff,) = [line for line in CI.splitlines() if "git diff" in line and "BASE_SHA" in line]
    for flag in ("--text", "--no-ext-diff", "--no-textconv"):
        assert flag in diff


def test_mutation_testing_runs_on_a_schedule_and_on_demand() -> None:
    workflow = MUTATION.read_text()
    assert re.search(r"^\s*schedule:\s*$", workflow, re.MULTILINE)
    assert re.search(r"^\s*-\s*cron:", workflow, re.MULTILINE)
    assert "workflow_dispatch:" in workflow
    assert "scripts/mutation_test.py" in workflow
    assert "mutation-report.json" in workflow and "upload-artifact" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
