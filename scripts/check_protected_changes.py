"""Fail a pull request that changes amber's pinned contract artifacts or weakens existing tests
without the ``contract-approved`` label (amber-22g; see the contract-change skill).

Protected:

- ``src/amber/conformance/golden/*.json``, ``catalog.json``, and ``fixtures.py``: any change;
- ``tests/**/*.py``: any removed line that asserts (``assert``, ``pytest.raises``, or an
  ``_assert*`` helper call), which covers deleted, edited, and moved assertions. Added tests
  are unrestricted.

Usage: ``git diff --unified=0 BASE HEAD | python3 scripts/check_protected_changes.py
--labels "a,b"``. Prints paths and line numbers only, never line contents. Standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable

APPROVAL_LABEL = "contract-approved"
_PROTECTED = (
    re.compile(r"^src/amber/conformance/golden/[^/]+\.json$"),
    re.compile(r"^src/amber/conformance/catalog\.json$"),
    re.compile(r"^src/amber/conformance/fixtures\.py$"),
)
_TESTS = re.compile(r"^tests/.+\.py$")
_ASSERTION = re.compile(r"^\s*assert\b|\bpytest\.raises\(|\b_assert\w*\(")
_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")


def violations(diff: Iterable[str]) -> list[str]:
    """``path`` for changed protected artifacts and ``path:line`` for removed assertions
    (line numbers in the base version)."""
    found: list[str] = []
    old_path: str | None = None
    new_path: str | None = None
    line = 0
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
            line = int(hunk.group(1))
            continue
        if text.startswith("-") and old_path is not None:
            if _TESTS.match(old_path) and _ASSERTION.search(text[1:]):
                found.append(f"{old_path}:{line}")
            line += 1
        elif text.startswith(" "):
            line += 1
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("diff", nargs="?", help="unified diff file (default: stdin)")
    parser.add_argument("--labels", default="", help="comma-separated pull request labels")
    args = parser.parse_args(argv)
    if args.diff:
        with open(args.diff, encoding="utf-8") as handle:
            found = violations(handle)
    else:
        found = violations(sys.stdin)
    labels = {label.strip() for label in args.labels.split(",") if label.strip()}
    if not found:
        print("No protected contract artifacts or existing test assertions changed.")
        return 0
    print("Protected changes:")
    for item in found:
        print(f"  {item}")
    if APPROVAL_LABEL in labels:
        print(f"Approved by the '{APPROVAL_LABEL}' label.")
        return 0
    print(
        f"These need a human decision: add the '{APPROVAL_LABEL}' label after reviewing them "
        "(see .agents/skills/contract-change/SKILL.md)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
