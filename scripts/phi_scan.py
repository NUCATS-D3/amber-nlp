"""Scan synthetic-only paths for identifier-shaped text (amber-a6d.3).

Looks in tracked files under ``tests/``, ``examples/``, ``src/amber/conformance/``, and ``docs/``
(or the given paths, when they are in that scope) for SSNs, phone numbers, emails, MRN-like
numbers next to a record keyword, and dates next to a birth keyword. Emails at reserved test
domains (RFC 2606/6761: ``example.com``/``.org``/``.net``, ``.invalid``, ``.test``,
``.example``, ``localhost``) pass. Prints ``path:line: pattern`` and never the matched text, and
exits 1 on any finding. ``ALLOWED`` lists reviewed exceptions by path, line, and pattern; this
script is a protected path, so only a human extends it. Standard library only.

    python3 scripts/phi_scan.py              # the whole scope
    python3 scripts/phi_scan.py FILE...      # files in scope only (pre-commit)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SCOPE = ("tests/", "examples/", "src/amber/conformance/", "docs/")
ALLOWED: frozenset[tuple[str, int, str]] = frozenset()

_PATTERNS = {
    "ssn": re.compile(r"(?<![\w-])\d{3}-\d{2}-\d{4}(?![\w-])"),
    "phone": re.compile(r"(?<![\w.-])(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?![\w-])"),
    "email": re.compile(r"\b[\w.+-]+@((?:[\w-]+\.)+[A-Za-z]{2,})\b"),
    "mrn": re.compile(
        r"\b(?:MRN|medical\s+record(?:\s+(?:number|no\.?|#))?)\W{0,3}\d{5,}", re.IGNORECASE
    ),
    "dob": re.compile(
        r"\b(?:DOB|D\.O\.B\.?|date\s+of\s+birth|birth\s*date|born)\b\W{0,20}"
        r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}",
        re.IGNORECASE,
    ),
}
_RESERVED_DOMAIN = re.compile(
    r"(?:^|\.)(?:example\.(?:com|org|net)|invalid|test|example|localhost)$", re.IGNORECASE
)


def findings(text: str) -> list[tuple[int, str]]:
    """``(line, pattern)`` for each identifier-shaped match, in line and pattern order."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        for name, pattern in _PATTERNS.items():
            for match in pattern.finditer(line):
                if name == "email" and _RESERVED_DOMAIN.search(match.group(1)):
                    continue
                found.append((number, name))
    return list(dict.fromkeys(found))


def _tracked() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *SCOPE], check=True, capture_output=True
    ).stdout
    return [path for path in listed.decode("utf-8").split("\0") if path]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    paths = [path for path in args if path.startswith(SCOPE)] if args else _tracked()
    reported = 0
    for path in paths:
        file = Path(path)
        if not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line, name in findings(text):
            if (path, line, name) not in ALLOWED:
                print(f"{path}:{line}: {name}")
                reported += 1
    if reported:
        print(f"{reported} identifier-shaped value(s); use invented, reserved, or no values.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
