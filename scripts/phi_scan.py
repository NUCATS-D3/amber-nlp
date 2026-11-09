"""Scan the repository for identifier-shaped text (amber-a6d.3).

Looks in every tracked and untracked (not ignored) file, or the given paths, for SSNs, phone
numbers, emails, MRN-like numbers next to a record keyword, and dates next to a birth keyword.
Emails at reserved test domains (RFC 2606/6761: ``example.com``/``.org``/``.net``, ``.invalid``,
``.test``, ``.example``, ``localhost``) pass. Text that is not UTF-8 is still scanned; a binary
file (one with a NUL byte) cannot be, so it is a finding of its own (``unscannable``) unless
``UNSCANNABLE`` lists it with the SHA-256 of its reviewed content. Prints ``path:line: pattern``
(or ``path: unscannable``) and never the matched text, and exits 1 on any finding.
``SKIPPED_PATTERNS`` skips a pattern for a whole file (third-party emails in the npm lockfile),
and in ``.beads/issues.jsonl`` each line's top-level ``owner`` email, the issue's creator, is
skipped while the rest of the line is scanned. ``ALLOWED`` lists reviewed exceptions by path,
line, and pattern; this script is a protected path, so only a human extends either list.
Standard library only.

    python3 scripts/phi_scan.py              # the whole scope
    python3 scripts/phi_scan.py FILE...      # just these files (pre-commit)
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# Patterns skipped for a whole file: npm's lockfile repeats package authors' contact emails in
# third-party metadata. Every other pattern still applies there.
SKIPPED_PATTERNS: dict[str, frozenset[str]] = {
    "tools/opencode-check/package-lock.json": frozenset({"email"}),
}
# Beads records who created each issue as a top-level "owner" email. Exactly that email is
# skipped on its own line; the line is otherwise scanned as written.
OWNER_EMAILS = frozenset({".beads/issues.jsonl"})
ALLOWED: frozenset[tuple[str, int, str]] = frozenset()
# Reviewed binaries in scope (slides and images), pinned to the SHA-256 of the reviewed bytes;
# regexes cannot inspect them, and any other content ends the exemption.
UNSCANNABLE: dict[str, str] = {
    "docs/slides/NUCATS-D3 update (Sept 26).pdf": (
        "477e8ba68d1a5168cb9dcb5a3950924eb20ed0759c293fb66aef23a22693baf1"
    ),
    "docs/slides/amber-ecosystem/deck.pdf": (
        "0e27e7deb39082add727e3fdd5b22ce57f0f589f4175ec18ed529864ad70a599"
    ),
    "docs/slides/nlp-abstractor/assets/nucats-logo.png": (
        "efbaf817c3b7e97deefad449f175521cff916e8864cdabe65faf0bb2e9345730"
    ),
    "docs/slides/nlp-abstractor/deck.pdf": (
        "e96e22d513854b2ab3f1e274150b0be26edbd786a490cdeca954dcd9d48ca419"
    ),
}

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?"
)
# A whole date with a two- or four-digit year: not a version string, hash, or timestamp.
_DATE = (
    r"(?<![\w.])(?:\d{1,2}[-/.]\d{1,2}[-/.](?:\d{4}|\d{2})|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{8}"
    rf"|{_MONTH}\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}\s+{_MONTH}\s+\d{{4}})(?![\w-]|\.\d)"
)
_PATTERNS = {
    "ssn": re.compile(
        r"(?<![\w-])\d{3}-\d{2}-\d{4}(?![\w-])|\bSSN\b\W{0,3}\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b",
        re.IGNORECASE,
    ),
    "phone": re.compile(
        r"(?<![\w.-])(?:\+?1[-.\s]?)?(?:\(\d{3}\)[-.\s]?|\d{3}[-.\s])\d{3}[-.\s]\d{4}(?![\w-])"
    ),
    "email": re.compile(r"(?<![\w.+-])[\w.+-]+@((?:[\w-]+\.)+[A-Za-z]{2,})\b"),
    "mrn": re.compile(
        r"(?:\bMRN|\bMR\s*#|\bmed(?:ical)?\.?\s*rec(?:ord)?\.?)"
        r"(?:\s*(?:number|num\.?|no\.?|#))?\W{0,4}[A-Z]{0,2}\d[\d-]{4,}",
        re.IGNORECASE,
    ),
    # Up to three words may sit between the keyword and the date ("born on", "(MM/DD/YYYY)").
    "dob": re.compile(
        r"\b(?:DOB|D\.O\.B\.?|date\s+of\s+birth|birth\s*date|birthday|born)\b"
        rf"(?:[^\w\n]+(?!\d){_MONTH}(?=\s)|[^\w\n]+(?!\d)\w+){{0,3}}?[^\w\n]{{0,20}}{_DATE}",
        re.IGNORECASE,
    ),
}
# JSON and Python string text spell line breaks and characters as escapes ("Contact\\njane..."),
# which would glue a letter to the value; findings() reads each escape as a space, and an escaped
# slash ("01\\/02\\/1960") as a slash.
_ESCAPE = re.compile(r"\\(?:[nrt]|u[0-9a-fA-F]{4})")
_RESERVED_DOMAIN = re.compile(
    r"(?:^|\.)(?:example\.(?:com|org|net)|invalid|test|example|localhost)$", re.IGNORECASE
)


def findings(
    text: str,
    *,
    skipped: frozenset[str] = frozenset(),
    owner_emails: bool = False,
) -> list[tuple[int, str]]:
    """``(line, pattern)`` for each identifier-shaped match, in line and pattern order. Lines are
    split on ``\\n`` only, so numbers match the file. ``skipped`` patterns are not reported;
    with ``owner_emails``, a JSON line's top-level ``owner`` email is not reported."""
    found: list[tuple[int, str]] = []
    for number, raw in enumerate(text.split("\n"), 1):
        line = _ESCAPE.sub(" ", raw).replace("\\/", "/")
        owner = _owner_email(raw) if owner_emails else None
        for name, pattern in _PATTERNS.items():
            if name in skipped:
                continue
            for match in pattern.finditer(line):
                if name == "email" and _RESERVED_DOMAIN.search(match.group(1)):
                    continue
                if name == "email" and match.group(0) == owner:
                    continue
                found.append((number, name))
    return list(dict.fromkeys(found))


class _Record(list[tuple[str, object]]):
    """A JSON object's key-value pairs, kept apart from JSON arrays."""


def _owner_email(line: str) -> str | None:
    """The top-level ``owner`` of a JSON line when it is exactly one email, else ``None``. With
    duplicate keys or a malformed line there is no exemption."""
    try:
        pairs = json.loads(line, object_pairs_hook=_Record)
    except ValueError:
        return None
    if not isinstance(pairs, _Record):
        return None
    owners = [value for key, value in pairs if key == "owner"]
    keys = [key for key, _ in pairs]
    if len(owners) != 1 or len(keys) != len(set(keys)) or not isinstance(owners[0], str):
        return None
    match = _PATTERNS["email"].fullmatch(owners[0])
    return owners[0] if match else None


def _in_scope() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    ).stdout
    return [path for path in listed.decode("utf-8").split("\0") if path]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    paths = args or _in_scope()
    reported = 0
    for path in dict.fromkeys(paths):
        file = Path(path)
        if not file.is_file():
            continue
        data = file.read_bytes()
        if b"\0" in data:
            if UNSCANNABLE.get(path) != hashlib.sha256(data).hexdigest():
                print(f"{path}: unscannable")
                reported += 1
            continue
        text = data.decode("utf-8", errors="replace")
        skipped = SKIPPED_PATTERNS.get(path, frozenset())
        for line, name in findings(text, skipped=skipped, owner_emails=path in OWNER_EMAILS):
            if (path, line, name) not in ALLOWED:
                print(f"{path}:{line}: {name}")
                reported += 1
    if reported:
        print(f"{reported} identifier-shaped value(s); use invented, reserved, or no values.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
