"""Flag large or sprawling changes, where agent drift hides (amber-7q0.9).

Counts changed lines (added plus removed) and files between the merge base of ``--base`` and
HEAD and the working tree, untracked files included, leaving out lockfiles, the Beads export,
golden records, and generated files (EXCLUDED, exact paths). A moved file counts as removed and
added, so a large move needs the label too. Prints the totals and the lines and files per amber
subpackage; top-level modules such as graph.py count together as one unit.

Warns above WARN_LINES lines or WARN_FILES files, and when the change touches more than
MAX_SUBPACKAGES amber subpackages (the top-level modules not counted). Above FAIL_LINES lines
or FAIL_FILES files, ``--enforce`` (CI) exits 2 unless ``--labels`` includes ``large-change``,
which only a human adds; without it (make quality) that too is a warning. Exit 1 when git
fails. Standard library only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "large-change"
REGRESSION = 2
WARN_LINES, WARN_FILES = 400, 12
FAIL_LINES, FAIL_FILES = 800, 25
MAX_SUBPACKAGES = 3
EXCLUDED = (
    "uv.lock",
    "tools/opencode-check/package-lock.json",
    ".beads/issues.jsonl",
    "src/amber/conformance/golden/*.json",
    "docs/architecture/deps.md",
    "quality/baseline.json",
)
_PACKAGE = "src/amber/"
_MODULES = "(top-level modules)"


class BudgetError(RuntimeError):
    """git could not describe the change."""


@dataclass
class Size:
    """Changed lines and files."""

    lines: int = 0
    files: int = 0

    def add(self, lines: int) -> None:
        """Count one more file with this many changed lines."""
        self.lines += lines
        self.files += 1

    def __str__(self) -> str:
        return f"{self.lines} lines in {self.files} file{'' if self.files == 1 else 's'}"


@dataclass
class Change:
    """A change's size in total and per amber subpackage."""

    total: Size = field(default_factory=Size)
    subpackages: dict[str, Size] = field(default_factory=dict)

    def add(self, path: str, lines: int) -> None:
        """Count a changed file, and its subpackage when it is under src/amber."""
        self.total.add(lines)
        if path.startswith(_PACKAGE):
            parts = Path(path.removeprefix(_PACKAGE)).parts
            unit = f"{parts[0]}/" if len(parts) > 1 else _MODULES
            self.subpackages.setdefault(unit, Size()).add(lines)


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise BudgetError(f"git {' '.join(args[:2])}: {done.stderr.strip()[:300]}")
    return done.stdout


def _counted(path: str) -> bool:
    # fnmatch's * also matches "/", so a pattern applies only at its own depth.
    return not any(
        fnmatch(path, pattern) and path.count("/") == pattern.count("/") for pattern in EXCLUDED
    )


def measure(root: Path, base: str) -> Change:
    """Lines and files changed since the merge base of ``base`` and HEAD."""
    merge_base = _git(root, "merge-base", base, "HEAD").strip()
    change = Change()
    numstat = _git(
        root, "-c", "core.quotePath=false", "diff", "--numstat", "-z", "--no-renames", merge_base
    )
    for record in numstat.split("\0"):
        if not record:
            continue
        added, removed, path = record.split("\t", 2)
        if _counted(path):
            binary = added == "-"
            change.add(path, 0 if binary else int(added) + int(removed))
    untracked = _git(root, "ls-files", "-z", "--others", "--exclude-standard")
    for path in filter(None, untracked.split("\0")):
        if _counted(path) and (root / path).is_file():
            text = (root / path).read_text(encoding="utf-8", errors="replace")
            change.add(path, text.count("\n") + (not text.endswith("\n") and bool(text)))
    return change


def _report(change: Change) -> bool:
    """Print the change's size and warnings; True when it is over the failure limit."""
    print(f"diff_budget: {change.total}")
    for unit, size in sorted(change.subpackages.items()):
        print(f"  {unit}: {size}")
    if change.total.lines > WARN_LINES or change.total.files > WARN_FILES:
        print(f"diff_budget: warning: large change (over {WARN_LINES} lines or {WARN_FILES} files)")
    subpackages = len(set(change.subpackages) - {_MODULES})
    if subpackages > MAX_SUBPACKAGES:
        print(f"diff_budget: warning: touches {subpackages} amber subpackages")
    return change.total.lines > FAIL_LINES or change.total.files > FAIL_FILES


def main(argv: Sequence[str] | None = None) -> int:
    """Report the change's size; with ``--enforce``, fail a very large one unless approved."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base", default="origin/main", help="compare against this")
    parser.add_argument("--labels", default="", help="pull request labels, comma-separated")
    parser.add_argument("--enforce", action="store_true", help="fail without the label (CI)")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        change = measure(args.root, args.base)
    except (BudgetError, OSError) as error:
        print(f"diff_budget: {error}")
        return 1
    if not _report(change):
        return 0
    limit = f"over {FAIL_LINES} lines or {FAIL_FILES} files"
    if LABEL in {label.strip() for label in args.labels.split(",")}:
        print(f"diff_budget: {limit}, approved by the {LABEL} label")
        return 0
    if args.enforce:
        print(f"diff_budget: {limit}: split it, or a human adds the {LABEL} label")
        return REGRESSION
    print(f"diff_budget: warning: {limit}; CI fails it until a human adds the {LABEL} label")
    return 0


if __name__ == "__main__":
    sys.exit(main())
