"""PreToolUse hook for the Claude Code reviewer agent (amber-a6d.7): Bash may run only single,
read-only git, bd, and gh commands, with no redirection, pipes, chaining, or substitution.

Exit 2 blocks the call and shows stderr to the agent; unreadable input is blocked too.
Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
from typing import TextIO

_ALLOWED = re.compile(
    r"^(?:git (?:diff|log|show|status|blame|merge-base|rev-parse|ls-files)"
    r"|bd show|gh pr (?:view|diff))(?: |$)"
)
_FORBIDDEN = re.compile(r"[<>|;&`\n\r]|\$\(|--output|--web\b|(?:^| )-w\b")


def allowed(command: str) -> bool:
    command = command.strip()
    return bool(_ALLOWED.match(command)) and not _FORBIDDEN.search(command)


def main(stdin: TextIO = sys.stdin) -> int:
    try:
        event = json.load(stdin)
        command = event["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        command = None
    if isinstance(command, str) and allowed(command):
        return 0
    print(
        "The reviewer is read-only: Bash may run only a single git diff/log/show/status/blame/"
        "merge-base/rev-parse/ls-files, bd show, or gh pr view/diff command, without "
        "redirection, pipes, chaining, substitution, or --output.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
