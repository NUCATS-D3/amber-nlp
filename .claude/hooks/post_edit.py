"""PostToolUse hook (amber-a6d.4): lint, format, and type-check a Python file an agent just edited.

Reads the hook event from stdin. For a ``.py`` file under ``src/`` or ``tests/`` it runs Ruff
format and Ruff with fixes, and mypy for files under ``src/`` (the tests are not type-checked in
CI). Any failure goes to stderr with exit code 2, which shows it to the agent; otherwise it exits
0 silently. Anything else, including unreadable input, is ignored. Standard library only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parents[2]
MAX_LINES = 40
Runner = Callable[[Sequence[str], Path], "subprocess.CompletedProcess[str]"]


def _run(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


def _edited_file(event: Any) -> str | None:
    """The edited file relative to the repository, if it is Python under src/ or tests/."""
    if not isinstance(event, dict):
        return None
    tool_input = event.get("tool_input")
    raw = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(event.get("cwd") or ROOT) / path
    try:
        relative = path.resolve().relative_to(ROOT)
    except ValueError:
        return None
    if relative.suffix != ".py" or relative.parts[0] not in {"src", "tests"}:
        return None
    if not (ROOT / relative).is_file():
        return None
    return relative.as_posix()


def _clip(output: str) -> str:
    lines = output.rstrip().splitlines()
    if len(lines) <= MAX_LINES:
        return "\n".join(lines)
    return "\n".join([*lines[:MAX_LINES], f"... {len(lines) - MAX_LINES} more lines"])


def main(stdin: TextIO = sys.stdin, run: Runner = _run) -> int:
    try:
        event = json.load(stdin)
    except ValueError:
        return 0
    target = _edited_file(event)
    if target is None:
        return 0
    checks: list[tuple[str, list[str]]] = [
        ("ruff format", ["ruff", "format", "--quiet", target]),
        ("ruff check", ["ruff", "check", "--fix", "--quiet", target]),
    ]
    if target.startswith("src/"):
        checks.append(("mypy", ["mypy", target]))
    failures = []
    for name, command in checks:
        result = run(["uv", "run", "--no-sync", *command], ROOT)
        if result.returncode != 0:
            failures.append(f"{name}:\n{_clip(result.stdout or '')}")
    if not failures:
        return 0
    print(f"Post-edit checks failed for {target}. Fix these now:", file=sys.stderr)
    print("\n\n".join(failures), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
