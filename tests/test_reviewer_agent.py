"""The read-only reviewer agents (amber-a6d.7) share one prompt and cannot write."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
OPENCODE = ROOT / ".opencode" / "agents" / "reviewer.md"
CLAUDE = ROOT / ".claude" / "agents" / "reviewer.md"
PROMPT = ROOT / "docs" / "review_prompt.md"
READ_ONLY_COMMANDS = (
    "git diff",
    "git log",
    "git show",
    "git status",
    "git blame",
    "git merge-base",
    "git rev-parse",
    "git ls-files",
    "bd show",
    "gh pr view",
    "gh pr diff",
)


def _frontmatter(path: Path) -> str:
    text = path.read_text()
    assert text.startswith("---\n")
    return text.split("\n---\n", 1)[0]


def _rules() -> list[dict[str, str]]:
    lines = [line.strip() for line in _frontmatter(OPENCODE).splitlines()]
    return [json.loads(line[2:]) for line in lines if line.startswith("- {")]


def test_both_agents_defer_to_the_shared_prompt() -> None:
    for path in (OPENCODE, CLAUDE):
        assert "docs/review_prompt.md" in path.read_text()
    prompt = PROMPT.read_text()
    assert "VERDICT: PASS | FAIL" in prompt and "path:line" in prompt


def test_opencode_reviewer_denies_everything_then_allows_only_reads() -> None:
    rules = _rules()
    assert rules[0] == {"action": "*", "resource": "*", "effect": "deny"}
    allowed = [rule for rule in rules if rule["effect"] == "allow"]
    assert {rule["action"] for rule in allowed} == {"read", "glob", "grep", "shell"}
    for rule in allowed:
        if rule["action"] == "shell":
            assert rule["resource"].removesuffix(" *") in READ_ONLY_COMMANDS
    denied_last = [rule["resource"] for rule in rules if rule["effect"] == "deny"][1:]
    assert {"*--output*", "*>*"} <= set(denied_last)
    shell = [i for i, rule in enumerate(rules) if rule["action"] == "shell"]
    assert rules[shell[-1]]["effect"] == "deny"


def test_claude_reviewer_has_no_edit_tools_and_only_guarded_bash() -> None:
    match = re.search(r"^tools: (.+)$", _frontmatter(CLAUDE), re.MULTILINE)
    assert match is not None
    tools = {tool.strip() for tool in match.group(1).split(",")}
    assert tools == {"Read", "Grep", "Glob", "Bash"}


def _bash_guard() -> object:
    import importlib.util

    path = ROOT / ".claude" / "hooks" / "reviewer_bash.py"
    spec = importlib.util.spec_from_file_location("reviewer_bash", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "command",
    [
        "git diff main...HEAD",
        "git log main..HEAD --oneline",
        "git show bc84dfd:AGENTS.md",
        "git diff --stat main...harness-reviewer",
        "bd show amber-a6d.7",
        "gh pr diff 27",
        "gh pr view 27 --json body",
    ],
)
def test_the_claude_reviewer_may_run_read_only_commands(command: str) -> None:
    assert _bash_guard().allowed(command)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "command",
    [
        "git diff main > out.txt",
        "git diff --output=out.txt main",
        "git diff main | tee out.txt",
        "git status; touch x",
        "git status && touch x",
        "git log $(touch x)",
        "git log `touch x`",
        "git diff main >> out.txt",
        "touch x",
        "sed -i s/a/b/ AGENTS.md",
        "git checkout main",
        "git commit -m x",
        "git push",
        "bd close amber-a6d.7",
        "gh pr merge 27",
        "gh pr view 27 --web",
        "git diff\nrm -rf src",
        "",
    ],
)
def test_the_claude_reviewer_may_not_run_anything_else(command: str) -> None:
    assert not _bash_guard().allowed(command)  # type: ignore[attr-defined]


def test_the_claude_reviewer_bash_guard_blocks_with_exit_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    guard: Any = _bash_guard()
    event = {"tool_name": "Bash", "tool_input": {"command": "touch x"}}
    assert guard.main(io.StringIO(json.dumps(event))) == 2
    assert "read-only" in capsys.readouterr().err
    event["tool_input"] = {"command": "git diff main...HEAD"}
    assert guard.main(io.StringIO(json.dumps(event))) == 0
    assert guard.main(io.StringIO("not json")) == 2


def test_the_claude_reviewer_runs_the_bash_guard() -> None:
    frontmatter = _frontmatter(CLAUDE)
    assert "PreToolUse:" in frontmatter and 'matcher: "Bash"' in frontmatter
    assert ".claude/hooks/reviewer_bash.py" in frontmatter
