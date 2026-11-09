"""Fail a change to amber's oracle (pinned contract artifacts, the verification harness, or
existing test assertions) without human approval (amber-22g, amber-a6d.2; see the
contract-change skill).

Protected:

- ``src/amber/conformance/golden/*.json``, ``catalog.json``, and ``fixtures.py``: any change;
- the harness: agent hook settings and scripts, agent definitions and the review prompt, the
  pre-commit config, the Makefile, CI workflows, and this script: any change;
- ``tests/**/*.py``: any removed line that asserts (``assert``, ``pytest.raises``, or an
  ``_assert*`` helper call), which covers deleted, edited, and moved assertions;
- moving tests out of ``make check`` (amber-a6d.16): any change to a ``conftest.py``,
  ``pytest.ini``, ``.pytest.ini``, ``tox.ini``, ``setup.cfg``, or the conformance pytest plugin;
  an added or removed line of pytest configuration in ``pyproject.toml``; in ``tests/**/*.py``,
  a removed ``def test_``/``class Test`` line in a file that still exists, or an added line (not
  a comment or a line that starts with a string) naming a skip, xfail, or ``slow`` mark,
  ``skip(``, ``importorskip``, ``SkipTest``, ``__test__``, or Hypothesis
  ``settings(``/``max_examples``; and a rename that takes a test module out of collection. So a
  new test file that skips without an optional extra (``importorskip``) needs approval too: a
  skipped plugin test proves nothing. Otherwise added tests are unrestricted. Dropped
  parametrized cases cannot be told from refactors in a diff and are left to review.

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
    re.compile(r"^(?:.+/)?conftest\.py$"),
    re.compile(r"^(?:\.?pytest\.ini|tox\.ini|setup\.cfg)$"),
    re.compile(r"^src/amber/conformance/plugin\.py$"),
)
_TESTS = re.compile(r"^tests/.+\.py$")
_ASSERTION = re.compile(r"^\s*assert\b|\bpytest\.raises\(|\b_assert\w*\(")
_TEST_MODULE = re.compile(r"^tests/(?:.+/)?(?:test_[^/]*|[^/]*_test)\.py$")
_TEST_DEFINITION = re.compile(r"^\s*(?:async\s+)?def test_|^\s*class Test")
# Any spelling, alias, or line position; lines that are comments or start with a string are
# test data, not code.
_DESELECTION = re.compile(
    r"mark\.(?:skip|skipif|xfail|slow)\b|\b(?:skip|xfail)\(|importorskip|SkipTest|__test__"
    r"|\bsettings\(|max_examples"
)
_PYTEST_CONFIG = re.compile(
    r"^\s*(?:\[tool\.pytest|addopts|testpaths|markers|python_files|python_classes"
    r"|python_functions|norecursedirs|filterwarnings|required_plugins|xfail_strict|minversion)\b"
)
_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _removed_is_protected(path: str, body: str, *, deleted: bool) -> bool:
    """A removed assertion in a test, a removed test definition in a test file that still exists
    (a deleted file is already flagged by its assertions), or removed pytest configuration."""
    if _TESTS.match(path):
        renamed_test = not deleted and _TEST_DEFINITION.search(body)
        return bool(_ASSERTION.search(body) or renamed_test)
    return path == "pyproject.toml" and bool(_PYTEST_CONFIG.search(body))


def _added_is_protected(path: str, body: str) -> bool:
    """An added line that deselects a test (not quoted data or a comment), or added pytest
    configuration."""
    if _TESTS.match(path):
        quoted = body.lstrip().startswith(('"', "'", "#"))
        return not quoted and bool(_DESELECTION.search(body))
    return path == "pyproject.toml" and bool(_PYTEST_CONFIG.search(body))


def _renamed_is_protected(old: str, new: str) -> bool:
    """A protected file moved, or a test module renamed out of collection."""
    if any(pattern.match(path) for pattern in _PROTECTED for path in (old, new)):
        return True
    return bool(_TEST_MODULE.match(old)) and not _TEST_MODULE.match(new)


def violations(diff: Iterable[str]) -> list[str]:
    """``path`` for changed protected artifacts and ``path:line`` for flagged lines: removed
    lines by their base line number, added lines by their new line number. File headers are
    read only before a file's first hunk, so content that looks like a header is content."""
    found: list[str] = []
    old_path: str | None = None
    new_path: str | None = None
    renamed_from: str | None = None
    in_hunk = False
    line = new_line = 0
    for raw in diff:
        text = raw.rstrip("\n")
        if text.startswith("diff --git "):
            old_path = new_path = renamed_from = None
            in_hunk = False
            continue
        if not in_hunk and text.startswith("rename from "):
            renamed_from = text.removeprefix("rename from ")
            continue
        if not in_hunk and text.startswith("rename to ") and renamed_from is not None:
            if _renamed_is_protected(renamed_from, text.removeprefix("rename to ")):
                found.append(renamed_from)
            continue
        if not in_hunk and text.startswith("--- "):
            old_path = None if text == "--- /dev/null" else text[4:].removeprefix("a/")
            continue
        if not in_hunk and text.startswith("+++ "):
            new_path = None if text == "+++ /dev/null" else text[4:].removeprefix("b/")
            for path in {old_path, new_path} - {None}:
                assert path is not None
                if any(pattern.match(path) for pattern in _PROTECTED) and path not in found:
                    found.append(path)
            continue
        hunk = _HUNK.match(text)
        if hunk:
            in_hunk = True
            line, new_line = int(hunk.group(1)), int(hunk.group(2))
            continue
        if text.startswith("-") and old_path is not None:
            if _removed_is_protected(old_path, text[1:], deleted=new_path is None):
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
