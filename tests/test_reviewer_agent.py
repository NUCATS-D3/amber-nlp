"""The read-only reviewer agents (amber-a6d.7) share one prompt and cannot write."""

from __future__ import annotations

import json
import re
from pathlib import Path

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


def test_claude_reviewer_has_no_edit_tools() -> None:
    match = re.search(r"^tools: (.+)$", _frontmatter(CLAUDE), re.MULTILINE)
    assert match is not None
    tools = {tool.strip() for tool in match.group(1).split(",")}
    assert tools == {"Read", "Grep", "Glob", "Bash"}
