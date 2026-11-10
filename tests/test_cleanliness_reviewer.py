"""The read-only cleanliness reviewer agents (amber-7q0.12) share one prompt, hold the same
read-only permissions as the correctness reviewer, and check the rubric the spec asks for."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
OPENCODE = ROOT / ".opencode" / "agents" / "cleanliness-reviewer.md"
CLAUDE = ROOT / ".claude" / "agents" / "cleanliness-reviewer.md"
PROMPT = ROOT / "docs" / "cleanliness_review_prompt.md"
RUBRIC = (
    "Speculative generality",
    "Parallel implementations",
    "Defensive clutter",
    "Leftovers",
    "Pass-through wrappers",
    "Metric gaming",
    "Layering and placement",
    "Naming and interfaces",
    "Comments",
    "Scope",
    "ADR conflicts",
)


def _frontmatter(path: Path) -> str:
    text = path.read_text()
    assert text.startswith("---\n")
    return text.split("\n---\n", 1)[0]


def _rules(path: Path) -> list[dict[str, str]]:
    lines = [line.strip() for line in _frontmatter(path).splitlines()]
    return [json.loads(line[2:]) for line in lines if line.startswith("- {")]


def test_both_agents_defer_to_the_shared_prompt() -> None:
    for path in (OPENCODE, CLAUDE):
        assert "docs/cleanliness_review_prompt.md" in path.read_text()


def test_the_prompt_checks_every_rubric_item_in_order() -> None:
    prompt = PROMPT.read_text()
    numbered = re.findall(r"^(\d+)\. \*\*(.+?)\.\*\*", prompt, re.MULTILINE)
    assert numbered == [(str(n), item) for n, item in enumerate(RUBRIC, start=1)]
    assert "VERDICT: PASS | PASS WITH NITS | FAIL" in prompt
    assert "[blocker|major|nit] path:line (item N)" in prompt


def test_the_prompt_reads_the_design_rules_and_accepted_adrs() -> None:
    prompt = PROMPT.read_text()
    for source in ("Design rules", "docs/decisions/", "bd show", "make quality"):
        assert source in prompt


def test_the_opencode_agent_has_exactly_the_reviewers_read_only_rules() -> None:
    reviewer = ROOT / ".opencode" / "agents" / "reviewer.md"
    assert _rules(OPENCODE) == _rules(reviewer)
    assert _rules(OPENCODE)[0] == {"action": "*", "resource": "*", "effect": "deny"}


def test_the_claude_agent_has_no_edit_tools_and_runs_the_bash_guard() -> None:
    frontmatter = _frontmatter(CLAUDE)
    match = re.search(r"^tools: (.+)$", frontmatter, re.MULTILINE)
    assert match is not None
    assert {tool.strip() for tool in match.group(1).split(",")} == {"Read", "Grep", "Glob", "Bash"}
    assert "PreToolUse:" in frontmatter and 'matcher: "Bash"' in frontmatter
    assert 'command: python3 "${CLAUDE_PROJECT_DIR}/.claude/hooks/reviewer_bash.py"' in frontmatter


def test_agents_md_asks_for_the_cleanliness_review() -> None:
    agents = (ROOT / "AGENTS.md").read_text()
    assert "cleanliness-reviewer" in agents
    assert "docs/cleanliness_review_prompt.md" in agents
