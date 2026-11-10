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


def _tests_job() -> str:
    start = CI.index("\n  tests:\n")
    following = re.search(r"\n  [\w-]+:\n", CI[start + 1 :])
    return CI[start : start + 1 + following.start()] if following else CI[start:]


def _make_targets(job: str) -> set[str]:
    """Targets run unconditionally as plain ``make`` commands in one job."""
    targets: set[str] = set()
    for line in job.splitlines():
        command = line.strip().removeprefix("run:").strip()
        if command.startswith("make "):
            assert not re.search(r"[|;&]", command), f"make command is not plain: {command}"
            targets.update(command.split()[1:])
    return targets


def test_ci_runs_every_check_target_except_the_editable_sync() -> None:
    prerequisites = _check_prerequisites()
    assert "sync" in prerequisites and "test-fast" in prerequisites
    job = _tests_job()
    assert "continue-on-error" not in job
    assert not re.search(r"^\s+if:", job, re.MULTILINE), "the tests job or a step is conditional"
    missing = set(prerequisites) - {"sync"} - _make_targets(job)
    assert not missing, f"the tests job skips make check targets: {sorted(missing)}"


def test_ci_installs_without_editable_mode_and_requires_mlflow() -> None:
    job = _tests_job()
    assert "uv sync --locked --no-editable --extra dev --extra tracking --extra bundles" in job
    assert 'python -c "import mlflow"' in job
    # make must not re-sync over the non-editable install.
    assert "RUN := uv run --no-sync" in (ROOT / "Makefile").read_text()


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


def test_the_mutation_summary_reads_the_report_the_script_writes(tmp_path: Path) -> None:
    import json
    import subprocess

    workflow = MUTATION.read_text()
    start = workflow.index("\n", workflow.index("<<'PY'")) + 1
    body = workflow[start : workflow.index("          PY", start)]
    summary = "\n".join(line.removeprefix("          ") for line in body.strip("\n").splitlines())
    report = {
        "stats": {"killed": 90, "survived": 10},
        "kill_rate": 0.9,
        "survivors_by_module": {"grounding": 4, "cases": 6},
        "survivors": ["amber.grounding.x__mutmut_1"],
    }
    (tmp_path / "mutation-report.json").write_text(json.dumps(report))
    run = subprocess.run(
        ["python3", "-c", summary], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stderr
    assert "90.0%" in run.stdout and "grounding: 4 survivors" in run.stdout


def test_the_tests_job_fetches_history_for_the_ratchet() -> None:
    """The ratchet in make quality finds new code against the merge base with origin/main."""
    job = _tests_job()
    assert "make quality" in job
    assert re.search(r"fetch-depth: 0\n", job), "the tests job needs origin/main for the ratchet"


def test_the_fast_suite_runs_on_parallel_workers_and_deselects_only_slow_tests() -> None:
    """amber-7q0.18: the stop hook and CI run make test-fast; workers keep it under ~90 s."""
    recipe = re.search(r"^test-fast:.*\n\t(.+)$", (ROOT / "Makefile").read_text(), re.MULTILINE)
    assert recipe is not None
    # Four workers run as fast as eighteen here (about 40 s) with a quarter of the memory.
    assert recipe.group(1).split() == ["$(PYTEST)", "-n", "4", "-m", '"not', 'slow"']


def test_ci_refuses_dependency_changes_without_the_label() -> None:
    """amber-7q0.7: the required pull-request job runs the dependency gate with its labels."""
    start = CI.index("\n  protected-artifacts:\n")
    job = CI[start : CI.index("\n  core-install:\n")]
    assert "fetch-depth: 0" in job
    assert 'python3 quality/dep_gate.py --base "$BASE_SHA" --labels "$LABELS" --enforce' in job
