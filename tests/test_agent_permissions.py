"""Agents must ask before editing a protected path (amber-a6d.9): every pattern the guard protects
has an "ask" rule in opencode.json and in .claude/settings.json. The guard and CI stay the
backstop, since shell commands are not covered by these rules."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_protected_changes", ROOT / "scripts" / "check_protected_changes.py"
)
assert _spec is not None and _spec.loader is not None
guard: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

# One path per guard pattern; a new pattern without a sample fails below.
SAMPLES = {
    r"^src/amber/conformance/golden/[^/]+\.json$": "src/amber/conformance/golden/claim.json",
    r"^src/amber/conformance/catalog\.json$": "src/amber/conformance/catalog.json",
    r"^src/amber/conformance/fixtures\.py$": "src/amber/conformance/fixtures.py",
    r"^\.claude/settings\.json$": ".claude/settings.json",
    r"^\.claude/hooks/": ".claude/hooks/stop_gate.py",
    r"^\.claude/agents/": ".claude/agents/reviewer.md",
    r"^(?:.+/)?\.opencode/(?:plugins?|agents?|modes?)/": "src/.opencode/mode/build.md",
    r"^docs/review_prompt\.md$": "docs/review_prompt.md",
    r"^(?:.+/)?opencode\.jsonc?$": "src/opencode.json",
    r"^\.pre-commit-config\.yaml$": ".pre-commit-config.yaml",
    r"^\.github/workflows/": ".github/workflows/ci.yml",
    r"^Makefile$": "Makefile",
    r"^scripts/check_protected_changes\.py$": "scripts/check_protected_changes.py",
    r"^(?:.+/)?conftest\.py$": "tests/sub/conftest.py",
    r"^(?:\.?pytest\.ini|tox\.ini|setup\.cfg)$": "pytest.ini",
    r"^src/amber/conformance/plugin\.py$": "src/amber/conformance/plugin.py",
    r"^(?:.+/)?\.gitattributes$": "docs/.gitattributes",
    r"^scripts/phi_scan\.py$": "scripts/phi_scan.py",
    r"^tools/opencode-check/": "tools/opencode-check/check-agents.mjs",
    r"^(?:.+/)?\.?ruff\.toml$": "src/.ruff.toml",
    r"^\.importlinter$": ".importlinter",
    r"^quality/": "quality/baseline.json",
    r"^\.?mypy\.ini$": "mypy.ini",
}
# Places outside the guard's view (a diff of this repository) that could switch the rules off:
# local and global config, parent directories, agent folders OpenCode also reads, and the system
# directories. OpenCode's edit resource for a file outside the project is its absolute path (seen
# live: an edit under ~/.config/opencode raised an "edit" request naming the absolute path).
EXTRA = [
    ".claude/settings.local.json",
    ".opencode/opencode.json",
    ".opencode/plugins/amber-harness.ts",
    ".opencode/agents/sub/reviewer.md",
    ".opencode/agent/build.md",
    ".opencode/modes/build.md",
    "opencode.jsonc",
    "~/.config/opencode/opencode.json",
    "~/.config/opencode/agents/build.md",
    f"{Path.home()}/projects/.opencode/opencode.json",
    f"{Path.home()}/projects/opencode.jsonc",
    "/etc/opencode/opencode.json",
    "/Library/Application Support/opencode/agents/build.md",
    # The other harness's home config, which OpenCode agents could otherwise rewrite.
    "~/.claude/settings.json",
    "~/.claude/agents/build.md",
]
CONFIG = ROOT / ".opencode" / "opencode.json"


def _opencode_rules() -> list[dict[str, str]]:
    rules: list[dict[str, str]] = json.loads(CONFIG.read_text())["permissions"]
    return rules


def _opencode_matches(pattern: str, path: str) -> bool:
    """OpenCode's whole-value wildcard: * is any run of characters, including /."""
    regex = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(regex, path) is not None


def _claude_rules() -> list[str]:
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    rules: list[str] = settings["permissions"]["ask"]
    return rules


def _claude_matches(rule: str, path: str) -> bool:
    """Claude Code's Edit(...) paths: /x is relative to the project root, ~/x to the home
    directory; ** crosses directories, * does not."""
    match = re.fullmatch(r"Edit\((.+)\)", rule)
    if match is None:
        return False
    pattern = match.group(1)
    if pattern.startswith("/"):
        pattern = pattern[1:]
    parts = re.split(r"(\*\*/|\*\*|\*)", pattern)
    regex = "".join(
        "(?:.*/)?" if part == "**/" else ".*" if part == "**" else "[^/]*" if part == "*" else
        re.escape(part)
        for part in parts
    )  # fmt: skip
    return re.fullmatch(regex, path) is not None


def test_every_guard_pattern_has_a_sample() -> None:
    patterns = {pattern.pattern for pattern in guard._PROTECTED}
    assert patterns == set(SAMPLES), f"update SAMPLES for: {sorted(patterns ^ set(SAMPLES))}"
    for pattern, path in SAMPLES.items():
        assert re.match(pattern, path), (pattern, path)


@pytest.mark.parametrize("path", [*SAMPLES.values(), *EXTRA])
def test_opencode_asks_before_an_agent_edits_a_protected_path(path: str) -> None:
    resource = path.replace("~", str(Path.home()), 1) if path.startswith("~") else path
    rules = [rule for rule in _opencode_rules() if rule["action"] == "edit"]
    matched = [rule for rule in rules if _opencode_matches(
        rule["resource"].replace("~", str(Path.home()), 1), resource
    )]  # fmt: skip
    assert matched, f"no .opencode/opencode.json edit rule covers {path}"
    assert matched[-1]["effect"] == "ask"


def test_opencode_rules_only_ask() -> None:
    rules = _opencode_rules()
    assert rules and all(rule["action"] == "edit" and rule["effect"] == "ask" for rule in rules)


CLAUDE_EXTRA = [
    ".claude/settings.local.json",
    "docs/.claude/settings.json",
    "~/.claude/settings.json",
    "~/.claude/agents/build.md",
    # OpenCode's global and parent-directory config, which Claude agents could otherwise rewrite.
    "~/.config/opencode/agents/build.md",
    "~/.config/opencode/opencode.json",
    "../.opencode/mode/build.md",
]


@pytest.mark.parametrize("path", [*SAMPLES.values(), *CLAUDE_EXTRA])
def test_claude_code_asks_before_an_agent_edits_a_protected_path(path: str) -> None:
    assert any(_claude_matches(rule, path) for rule in _claude_rules()), path


def test_the_rules_live_where_opencode_applies_them_last() -> None:
    # Every discovered .opencode config overrides every direct opencode.json, and the closest
    # .opencode config is merged last, so the project rules belong there and nowhere else.
    assert CONFIG.is_file() and not (ROOT / "opencode.json").exists()


def _agent_rules(file: Path) -> list[dict[str, str]]:
    """An agent's permission rules, which this repository writes one JSON object per line."""
    front = file.read_text().split("\n---\n", 1)[0]
    assert not re.search(r"^permission:", front, re.MULTILINE), f"{file}: legacy permission key"
    return [
        json.loads(line.strip()[2:]) for line in front.splitlines() if line.strip()[:3] == "- {"
    ]


def test_no_agent_definition_re_allows_edits_to_a_protected_path() -> None:
    folders = ("agent", "agents", "mode", "modes")
    files = [path for folder in folders for path in (ROOT / ".opencode" / folder).rglob("*.md")]
    assert files, "expected at least the reviewer agent"
    paths = [*SAMPLES.values(), *(p for p in EXTRA if not p.startswith(("~", "/")))]
    for file in files:
        for rule in _agent_rules(file):
            if rule["effect"] != "allow" or not _opencode_matches(rule["action"], "edit"):
                continue
            hits = [path for path in paths if _opencode_matches(rule["resource"], path)]
            assert not hits, f"{file.name} allows edits to {hits}"


@pytest.mark.parametrize(
    "rule",
    [
        {"action": "edit", "resource": ".opencode/*", "effect": "allow"},
        {"action": "e*", "resource": "*", "effect": "allow"},
    ],
)
def test_the_re_allow_check_catches_a_reopening_rule(rule: dict[str, str]) -> None:
    paths = [*SAMPLES.values(), *(p for p in EXTRA if not p.startswith(("~", "/")))]
    assert _opencode_matches(rule["action"], "edit")
    assert [path for path in paths if _opencode_matches(rule["resource"], path)]


def test_agent_rules_parse_and_refuse_the_legacy_key(tmp_path: Path) -> None:
    agent = tmp_path / "x.md"
    agent.write_text(
        '---\npermissions:\n  - {"action": "read", "resource": "*", "effect": "allow"}\n---\n'
    )
    assert _agent_rules(agent) == [{"action": "read", "resource": "*", "effect": "allow"}]
    agent.write_text("---\npermission:\n  edit: allow\n---\n")
    with pytest.raises(AssertionError, match="legacy permission key"):
        _agent_rules(agent)


def test_neighbouring_paths_do_not_ask() -> None:
    for path in ("src/amber/ids.py", "tests/test_ids.py", "docs/contracts.md", "scripts/x.py"):
        assert not any(_opencode_matches(rule["resource"], path) for rule in _opencode_rules())
        assert not any(_claude_matches(rule, path) for rule in _claude_rules())
