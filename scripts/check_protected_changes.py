"""Fail a change to amber's oracle (pinned contract artifacts, the verification harness, or
existing test assertions) without human approval (amber-22g, amber-a6d.2; see the
contract-change skill).

Protected:

- ``src/amber/conformance/golden/*.json``, ``catalog.json``, and ``fixtures.py``: any change;
- the harness: agent hook settings and scripts, agent definitions and the review prompt, the
  pre-commit config, the Makefile, CI workflows, and this script: any change;
- ``tests/**/*.py``: any removed line that asserts (``assert``, ``pytest.raises``, or an
  ``_assert*`` helper call), which covers deleted, edited, and moved assertions;
- moving tests out of ``make check`` (amber-a6d.16): any change to ``tests/**/conftest.py``; an
  added or removed line of pytest configuration in ``pyproject.toml``; an added line in
  ``tests/**/*.py`` that skips, deselects, or marks a test (``pytest.mark.skip``, ``skipif``,
  ``xfail``, ``slow``; ``pytest.skip``, ``xfail``, ``importorskip``) or sets Hypothesis
  ``settings``. Otherwise added tests are unrestricted. Dropped parametrized cases cannot be
  told from refactors in a diff and are left to review.

Pull requests: ``git diff --unified=0 BASE HEAD | python3 scripts/check_protected_changes.py
--labels "a,b"``; the ``contract-approved`` label approves. Commits (pre-commit): ``--staged``
checks the index; ``ALLOW_ORACLE_CHANGE=1`` approves, and only there. Prints paths and line
numbers only, never line contents. Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable

APPROVAL_LABEL = "contract-approved"
APPROVAL_ENV = "ALLOW_ORACLE_CHANGE"
_PROTECTED = (
    re.compile(r"^src/amber/conformance/golden/[^/]+\.json$"),
    re.compile(r"^src/amber/conformance/catalog\.json$"),
    re.compile(r"^src/amber/conformance/fixtures\.py$"),
    re.compile(r"^\.claude/settings\.json$"),
    re.compile(r"^\.claude/hooks/"),
    re.compile(r"^\.opencode/plugins?/"),
    re.compile(r"^\.(?:claude|opencode)/agents/"),
    re.compile(r"^docs/review_prompt\.md$"),
    re.compile(r"^opencode\.jsonc?$"),
    re.compile(r"^\.pre-commit-config\.yaml$"),
    re.compile(r"^\.github/workflows/"),
    re.compile(r"^Makefile$"),
    re.compile(r"^scripts/check_protected_changes\.py$"),
    re.compile(r"^tests/(?:.+/)?conftest\.py$"),
)
_TESTS = re.compile(r"^tests/.+\.py$")
_ASSERTION = re.compile(r"^\s*assert\b|\bpytest\.raises\(|\b_assert\w*\(")
# Code positions only (decorators, module marks, param marks, statement-level calls), so the
# same words inside strings or comments are not flagged.
_MARK = r"pytest\.mark\.(?:skip|skipif|xfail|slow)\b"
_DESELECTION = re.compile(
    rf"^\s*@{_MARK}|^\s*pytestmark\s*=.*{_MARK}|\bmarks\s*=.*{_MARK}"
    r"|^\s*(?:\w+\s*=\s*)?pytest\.(?:skip|xfail|importorskip)\("
    r"|^\s*@settings\(|^\s*settings\.register_profile\("
)
_PYTEST_CONFIG = re.compile(
    r"^\s*(?:\[tool\.pytest|addopts|testpaths|markers|python_files|python_classes"
    r"|python_functions|norecursedirs|filterwarnings|required_plugins|xfail_strict|minversion)\b"
)
_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _removed_is_protected(path: str, body: str) -> bool:
    """A removed assertion in a test, or removed pytest configuration."""
    if _TESTS.match(path):
        return bool(_ASSERTION.search(body))
    return path == "pyproject.toml" and bool(_PYTEST_CONFIG.search(body))


def _added_is_protected(path: str, body: str) -> bool:
    """An added line that deselects a test (not quoted data or a comment), or added pytest
    configuration."""
    if _TESTS.match(path):
        quoted = body.lstrip().startswith(('"', "'", "#"))
        return not quoted and bool(_DESELECTION.search(body))
    return path == "pyproject.toml" and bool(_PYTEST_CONFIG.search(body))


def violations(diff: Iterable[str]) -> list[str]:
    """``path`` for changed protected artifacts and ``path:line`` for flagged lines: removed
    lines by their base line number, added lines by their new line number."""
    found: list[str] = []
    old_path: str | None = None
    new_path: str | None = None
    line = new_line = 0
    for raw in diff:
        text = raw.rstrip("\n")
        if text.startswith("diff --git "):
            old_path = new_path = None
            continue
        if text.startswith("--- "):
            old_path = None if text == "--- /dev/null" else text[4:].removeprefix("a/")
            continue
        if text.startswith("+++ "):
            new_path = None if text == "+++ /dev/null" else text[4:].removeprefix("b/")
            for path in {old_path, new_path} - {None}:
                assert path is not None
                if any(pattern.match(path) for pattern in _PROTECTED) and path not in found:
                    found.append(path)
            continue
        hunk = _HUNK.match(text)
        if hunk:
            line, new_line = int(hunk.group(1)), int(hunk.group(2))
            continue
        if text.startswith("-") and old_path is not None:
            if _removed_is_protected(old_path, text[1:]):
                found.append(f"{old_path}:{line}")
            line += 1
        elif text.startswith("+") and new_path is not None:
            if _added_is_protected(new_path, text[1:]):
                found.append(f"{new_path}:{new_line}")
            new_line += 1
        elif text.startswith(" "):
            line += 1
            new_line += 1
    return list(dict.fromkeys(found))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("diff", nargs="?", help="unified diff file (default: stdin)")
    parser.add_argument("--labels", default="", help="comma-separated pull request labels")
    parser.add_argument(
        "--staged", action="store_true", help=f"check the git index; {APPROVAL_ENV}=1 approves"
    )
    args = parser.parse_args(argv)
    if args.staged:
        staged = subprocess.run(
            ["git", "diff", "--cached", "--unified=0", "--no-color", "--no-ext-diff"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        found = violations(staged.splitlines(keepends=True))
    elif args.diff:
        with open(args.diff, encoding="utf-8") as handle:
            found = violations(handle)
    else:
        found = violations(sys.stdin)
    labels = {label.strip() for label in args.labels.split(",") if label.strip()}
    if not found:
        print("No protected artifacts, harness files, or existing test assertions changed.")
        return 0
    print("Protected changes:")
    for item in found:
        print(f"  {item}")
    if args.staged and os.environ.get(APPROVAL_ENV) == "1":
        print(f"Approved by {APPROVAL_ENV}=1.")
        return 0
    if not args.staged and APPROVAL_LABEL in labels:
        print(f"Approved by the '{APPROVAL_LABEL}' label.")
        return 0
    approval = (
        f"commit with {APPROVAL_ENV}=1" if args.staged else f"add the '{APPROVAL_LABEL}' label"
    )
    print(
        f"These need a human decision: {approval} after reviewing them "
        "(see .agents/skills/contract-change/SKILL.md). Agents never approve their own changes."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
