"""Stop hook (amber-a6d.4): keep the agent working until lint, mypy, and the fast tests pass.

Reads the hook event from stdin. Exits 0 at once when ``stop_hook_active`` is set (the agent is
already continuing because of this hook, so blocking again could loop) or when nothing under
``src/`` or ``tests/`` differs from HEAD, untracked files included. Otherwise runs ``make lint``,
``make typecheck``, and ``make test-fast``; prints a one-line summary and exits 0 when all pass,
or prints the failures to stderr and exits 2, which keeps the agent going. ``make check`` is
still the definition of done. Standard library only.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ("lint", "typecheck", "test-fast")
MAX_LINES = 30
Runner = Callable[[Sequence[str], Path], "subprocess.CompletedProcess[str]"]

_SUMMARY = re.compile(r"^=+ (.*\b(?:passed|failed|error|errors)\b.*) =+$")
_PASSED = re.compile(r"\b(\d+) passed\b")


def _run(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


def _touches_code(run: Runner) -> bool:
    """Whether src/ or tests/ changed since HEAD; True when git cannot tell."""
    paths: list[str] = []
    for command in (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        result = run(command, ROOT)
        if result.returncode != 0:
            return True
        paths.extend((result.stdout or "").splitlines())
    return any(path.startswith(("src/", "tests/")) for path in paths)


def _test_summary(output: str) -> str | None:
    for line in reversed(output.splitlines()):
        match = _SUMMARY.match(line.strip())
        if match:
            return match.group(1)
    return None


def _failure_report(target: str, output: str) -> str:
    lines = output.rstrip().splitlines()
    if target == "test-fast":
        picked = [line for line in lines if line.startswith(("FAILED ", "ERROR "))]
        summary = _test_summary(output)
        lines = [*picked[:MAX_LINES], *([summary] if summary else [])] or lines[-MAX_LINES:]
    else:
        lines = [line for line in lines if not line.startswith("make: ***")][-MAX_LINES:]
    return f"make {target} failed:\n" + "\n".join(lines)


def main(stdin: TextIO = sys.stdin, run: Runner = _run) -> int:
    try:
        event = json.load(stdin)
    except ValueError:
        event = {}
    if isinstance(event, dict) and event.get("stop_hook_active"):
        return 0
    if not _touches_code(run):
        return 0
    failures = []
    passed = "?"
    for target in TARGETS:
        result = run(["make", target], ROOT)
        output = result.stdout or ""
        if result.returncode != 0:
            failures.append(_failure_report(target, output))
        elif target == "test-fast":
            match = _PASSED.search(_test_summary(output) or "")
            passed = match.group(1) if match else passed
    if failures:
        print("Not done: fix these before stopping.\n", file=sys.stderr)
        print("\n\n".join(failures), file=sys.stderr)
        return 2
    print(f"check ok: {passed} passed (lint, mypy, fast tests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
