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
  ``pytest.ini``, ``.pytest.ini``, ``tox.ini``, ``setup.cfg``, ``.gitattributes``, or the
  conformance pytest plugin, including an empty new file or a mode change; an added or removed
  line of pytest configuration in ``pyproject.toml``, including any line shaped like an entry
  point (a ``module:attr`` or ``amber.*`` target), since pytest11 entries load plugins; an added
  ``pytest_plugins`` or ``pytest_collection*`` line in any Python file; in ``tests/**/*.py``,
  test names (``def test_``, ``class Test``) removed from a file that still exists when the file
  loses more test names than it gains, or an added line whose code (string literals and comments
  removed) skips, xfails, slow-marks, stops, or re-plugs the suite in common spellings and
  aliases (marks, the ``pytest.mark`` namespace, ``skip``, ``importorskip``, ``SkipTest``,
  ``__test__``, ``exit(``, ``Exit``, Hypothesis ``settings(``, ``max_examples``, profiles, or
  ``phases``); a rename that takes a test module out of collection; and a binary diff of a
  protected or test file, which hides its lines. So a new test file that skips without an
  optional extra (``importorskip``) needs approval too: a skipped plugin test proves nothing.
  Otherwise added tests are unrestricted. Dropped parametrized cases and unusual aliases cannot
  be told from refactors in a diff and are left to review.

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
    re.compile(r"^(?:.+/)?\.gitattributes$"),
)
_TESTS = re.compile(r"^tests/.+\.py$")
_ASSERTION = re.compile(r"^\s*assert\b|\bpytest\.raises\(|\b_assert\w*\(")
_TEST_MODULE = re.compile(r"^tests/(?:.+/)?(?:test_[^/]*|[^/]*_test)\.py$")
_TEST_NAME = re.compile(r"^\s*(?:async\s+)?def (test_\w*)|^\s*class (Test\w*)")
# Common spellings and aliases in any line position (review still catches the rest), matched
# against code only: string literals and comments are removed first (see _code).
_DESELECTION = re.compile(
    r"mark\.(?:skip|skipif|xfail|slow)\b|\bpytest\.mark\b(?!\.)|\bskip\b(?!\s*=)|\bxfail\("
    r"|importorskip|SkipTest|__test__|\bsettings\(|max_examples|load_profile|register_profile"
    r"|\bphases\b|\bexit\(|_exit\(|\bExit\b|exit\.Exception|pytest_plugins|pytest_collection"
)
# Hooks that change collection, wherever the suite might import them from.
_COLLECTION_HOOK = re.compile(r"pytest_plugins|pytest_collection")
_PYTEST_CONFIG = re.compile(
    r"^\s*(?:\[tool\.pytest|\[project\.entry-points\.pytest11|addopts|testpaths|markers"
    r"|python_files|python_classes|python_functions|norecursedirs|filterwarnings"
    r"|required_plugins|xfail_strict|minversion)\b"
    # A hunk does not say which TOML section it is in, so any line shaped like an entry point
    # (a module:attr target, or an amber module) counts: pytest11 entries load plugins.
    r"""|^\s*[\w.-]+\s*=\s*(["'])(?:[\w.]+:[\w.]+|amber(?:\.\w+)+)\1\s*$"""
)
_STRING = re.compile(r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'""")


def _code(line: str) -> str:
    """A Python line without string literals and comments; an unterminated string (a
    multi-line literal) blanks the rest of the line."""
    code = _STRING.sub('""', line)
    for quote in ('"', "'"):
        if code.count(quote) % 2:
            code = code[: code.rindex(quote)]
    return code.split("#", 1)[0]


_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_GIT_HEADER = re.compile(r'^diff --git "?a/(.+?)"? "?b/(.+?)"?$')


def _protected(path: str) -> bool:
    return any(pattern.match(path) for pattern in _PROTECTED)


def _removed_is_protected(path: str, body: str) -> bool:
    """A removed assertion in a test, or removed pytest configuration."""
    if _TESTS.match(path):
        return bool(_ASSERTION.search(body))
    return path == "pyproject.toml" and bool(_PYTEST_CONFIG.search(body))


def _added_is_protected(path: str, body: str) -> bool:
    """An added line whose code deselects or stops tests, an added collection hook, or added
    pytest configuration."""
    if _TESTS.match(path):
        return bool(_DESELECTION.search(_code(body)))
    if path.endswith(".py"):
        return bool(_COLLECTION_HOOK.search(_code(body)))
    return path == "pyproject.toml" and bool(_PYTEST_CONFIG.search(body))


def _renamed_is_protected(old: str, new: str) -> bool:
    """A protected file moved, or a test module renamed out of collection."""
    if _protected(old) or _protected(new):
        return True
    return bool(_TEST_MODULE.match(old)) and not _TEST_MODULE.match(new)


def _test_name(body: str) -> str | None:
    match = _TEST_NAME.match(body)
    return None if match is None else match.group(1) or match.group(2)


class _File:
    """What one file's section of a diff changed."""

    def __init__(self, old: str | None, new: str | None) -> None:
        self.old, self.new = old, new
        self.in_hunk = False
        self.renamed_from: str | None = None
        self.removed_names: dict[str, int] = {}
        self.added_names: set[str] = set()

    def paths(self) -> list[str]:
        return [path for path in dict.fromkeys((self.old, self.new)) if path is not None]

    def renamed_tests(self) -> list[str]:
        """When a file that still exists loses more test names than it gains, the removed names
        not added back: tests renamed out of collection. A rename to another test name keeps
        the count. A deleted file is flagged by its assertions instead."""
        if self.old is None or self.new is None or not _TESTS.match(self.old):
            return []
        missing = [name for name in self.removed_names if name not in self.added_names]
        if len(missing) <= len(self.added_names - self.removed_names.keys()):
            return []
        return [f"{self.old}:{self.removed_names[name]}" for name in missing]


def violations(diff: Iterable[str]) -> list[str]:
    """``path`` for changed protected artifacts and ``path:line`` for flagged lines: removed
    lines by their base line number, added lines by their new line number. Paths come from
    ``diff --git`` lines, so a binary diff (no ``---``/``+++`` lines; a ``-diff`` attribute or a
    NUL byte) of a protected or test file is flagged. File headers are read only before a
    file's first hunk, so content that looks like a header is content."""
    found: list[str] = []
    current: _File | None = None
    line = new_line = 0

    def finish() -> None:
        if current is not None:
            found.extend(current.renamed_tests())

    for raw in diff:
        text = raw.rstrip("\n")
        header = _GIT_HEADER.match(text)
        if header:
            finish()
            current = _File(header.group(1), header.group(2))
            # Covers files whose diff has no ---/+++ lines: empty new files and mode changes.
            found.extend(path for path in current.paths() if _protected(path))
            continue
        if current is None:
            continue
        if not current.in_hunk:
            if text.startswith("rename from "):
                current.renamed_from = text.removeprefix("rename from ")
            elif text.startswith("rename to ") and current.renamed_from is not None:
                if _renamed_is_protected(current.renamed_from, text.removeprefix("rename to ")):
                    found.append(current.renamed_from)
            elif text.startswith("--- "):
                current.old = None if text == "--- /dev/null" else text[4:].removeprefix("a/")
            elif text.startswith("+++ "):
                current.new = None if text == "+++ /dev/null" else text[4:].removeprefix("b/")
                found.extend(path for path in current.paths() if _protected(path))
            elif text.startswith("Binary files "):
                found.extend(
                    path for path in current.paths() if _protected(path) or _TESTS.match(path)
                )
        hunk = _HUNK.match(text)
        if hunk:
            current.in_hunk = True
            line, new_line = int(hunk.group(1)), int(hunk.group(2))
            continue
        if not current.in_hunk:
            continue
        if text.startswith("-") and current.old is not None:
            if _removed_is_protected(current.old, text[1:]):
                found.append(f"{current.old}:{line}")
            if (name := _test_name(text[1:])) is not None:
                current.removed_names.setdefault(name, line)
            line += 1
        elif text.startswith("+") and current.new is not None:
            if _added_is_protected(current.new, text[1:]):
                found.append(f"{current.new}:{new_line}")
            if (name := _test_name(text[1:])) is not None:
                current.added_names.add(name)
            new_line += 1
        elif text.startswith(" "):
            line += 1
            new_line += 1
    finish()
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
