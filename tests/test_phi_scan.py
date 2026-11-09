"""The identifier-pattern scan (amber-a6d.3): each pattern is caught, reserved and look-alike
values are not, and output names the path, line, and pattern but never the matched text. Every
value here is invented."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "phi_scan.py"
_spec = importlib.util.spec_from_file_location("phi_scan", SCRIPT)
assert _spec is not None and _spec.loader is not None
scan: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)

# Assembled at run time so this file never holds a contiguous identifier-shaped string.
SSN = "-".join(["000", "12", "3456"])
PHONE = "(" + "555" + ") " + "010" + "-" + "0199"
EMAIL = "jane.roe" + "@" + "hospital" + ".org"
MRN = "MRN" + ": " + "12345678"
SLASH_DATE = "/".join(["01", "02", "1960"])
DOB = "DOB" + " " + SLASH_DATE


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        (f"note {SSN} here", "ssn"),
        (f"call {PHONE} today", "phone"),
        ("call +1 " + "555" + "." + "010" + "." + "0199", "phone"),
        (f"write to {EMAIL}", "email"),
        (f"record {MRN}", "mrn"),
        ("medical record number " + "00123456", "mrn"),
        (f"patient {DOB}", "dob"),
        ("date of birth: " + "-".join(["1960", "01", "02"]), "dob"),
        ("Born " + ".".join(["2", "1", "1960"]), "dob"),
        ("SSN: " + "".join(["000", "12", "3456"]), "ssn"),
        ("SSN " + " ".join(["000", "12", "3456"]), "ssn"),
        ("call (" + "555)" + "010-0199", "phone"),
        ("MRN #: " + "1234567", "mrn"),
        ("MRN No. " + "1234567", "mrn"),
        ("MR# " + "A1234567", "mrn"),
        ("Med Rec No. " + "12-345-678", "mrn"),
        ("born on " + SLASH_DATE, "dob"),
        ("DOB: Jan " + "2, 1960", "dob"),
        ("DOB " + " ".join(["2", "January", "1960"]), "dob"),
        ("DOB " + "".join(["01", "02", "1960"]), "dob"),
        ("Date of birth (MM/DD/YYYY): " + SLASH_DATE, "dob"),
        ("birthday " + SLASH_DATE, "dob"),
        ("The DOB was " + SLASH_DATE + ".", "dob"),
        ("DOB: Jan " + "2, 1960.", "dob"),
        ("DOB " + "".join(["01", "02", "1960"]) + ".", "dob"),
    ],
    ids=[
        "ssn",
        "phone",
        "phone-dotted",
        "email",
        "mrn",
        "mrn-words",
        "dob",
        "dob-words",
        "born",
        "ssn-plain",
        "ssn-spaced",
        "phone-tight",
        "mrn-hash",
        "mrn-no",
        "mr-hash",
        "med-rec",
        "born-on",
        "dob-month-first",
        "dob-day-first",
        "dob-digits",
        "dob-format-hint",
        "birthday",
        "sentence-final",
        "sentence-final-month",
        "sentence-final-digits",
    ],
)
def test_each_pattern_is_caught(text: str, pattern: str) -> None:
    assert scan.findings(text) == [(1, pattern)]


@pytest.mark.parametrize(
    "text",
    [
        "reviewer t" + "@" + "example.invalid",
        "contact a" + "@" + "example.com or b" + "@" + "lab.example",
        "producer model:teacher" + "@" + "8.0 and sections" + "@" + "medspacy-1.3",
        "version 2026-01-02 and run 2026-11-08T09:00:00",
        "ULID 01KDVDNA080000000000000000 and sha 1234567890abcdef",
        "offsets 123-45 and 12-3456",
        "a date 01/02/1960 with no birth keyword",
        "MRN-like 1234 is too short",
        "born at home; seen in clinic on 01/02/1960",
        "born in release 1.2.3 of the tool",
        "the DOB field since 1.4.0",
        "DOB parser at commit 12345678abcdef90",
        "DOB column added 2026-11-08T09:00:00Z",
        "born of a decision 3 2026 review",
        "DOB lookup key ab12345678",
        'source = "import pytest\\n\\n\\n@pytest.mark.slow\\n"',
        "born in 1.2.30.4",
    ],
    ids=[
        "reserved-email",
        "reserved-domains",
        "producer-refs",
        "dates",
        "ids",
        "short-numbers",
        "bare-date",
        "short-mrn",
        "keyword-far-from-date",
        "version-after-born",
        "version-after-dob",
        "hash-after-dob",
        "timestamp-after-dob",
        "month-like-word",
        "digits-inside-an-id",
        "escaped-newline-before-decorator",
        "long-version",
    ],
)
def test_reserved_and_look_alike_values_are_not_caught(text: str) -> None:
    assert scan.findings(text) == []


@pytest.mark.parametrize(
    ("value", "pattern"),
    [(EMAIL, "email"), (SSN, "ssn"), (PHONE, "phone"), (MRN, "mrn"), (DOB, "dob")],
)
@pytest.mark.parametrize("escape", ["\\n", "\\t", "\\r", "\\u00e9"])
def test_values_after_a_literal_escape_are_caught(value: str, pattern: str, escape: str) -> None:
    # JSON and Python string text spell a line break as a backslash and a letter.
    assert scan.findings(f'"text": "Contact{escape}{value} today"') == [(1, pattern)]


def test_a_date_with_escaped_slashes_is_caught() -> None:
    escaped = SLASH_DATE.replace("/", "\\/")
    assert scan.findings(f'"text": "DOB {escaped}"') == [(1, "dob")]


def test_findings_report_lines() -> None:
    text = f"first line\nsecond {SSN}\nthird {EMAIL} and {PHONE}\n"
    assert scan.findings(text) == [(2, "ssn"), (3, "phone"), (3, "email")]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    files = {
        "tests/test_a.py": f"VALUE = '{SSN}'\n",
        "docs/note with spaces.md": f"Reach me at {EMAIL}.\n",
        "examples/clean.json": '{"text": "nothing to see"}\n',
        "src/amber/other.py": f"IN_SCOPE = '{SSN}'\n",
        "tools/opencode-check/package-lock.json": f'"deprecated": "contact {EMAIL}, {SSN}"\n',
        "docs/image.bin": "",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    (tmp_path / "docs" / "image.bin").write_bytes(b"\xff\xfe\x00" + SSN.encode())
    _git(tmp_path, "add", "-A")
    return tmp_path


def test_the_scan_covers_every_file_but_excluded_lockfiles_and_never_prints_matches(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    assert scan.main([]) == 1
    out = capsys.readouterr().out
    assert "tests/test_a.py:1: ssn" in out
    assert "docs/note with spaces.md:1: email" in out
    assert "src/amber/other.py:1: ssn" in out
    # The npm lockfile skips only third-party emails; other patterns still apply.
    assert "tools/opencode-check/package-lock.json:1: ssn" in out
    assert "package-lock.json:1: email" not in out
    # A binary file in scope cannot be scanned, so it is a finding of its own.
    assert "docs/image.bin: unscannable" in out
    for secret in (SSN, EMAIL, "hospital", "3456"):
        assert secret not in out


def test_explicit_paths_are_scanned(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    assert scan.main(["examples/clean.json"]) == 0
    assert scan.main(["tests/test_a.py"]) == 1
    assert "tests/test_a.py:1: ssn" in capsys.readouterr().out


def test_the_allowlist_names_path_line_and_pattern(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setattr(scan, "ALLOWED", frozenset({("tests/test_a.py", 1, "ssn")}))
    assert scan.main(["tests/test_a.py"]) == 0
    monkeypatch.setattr(scan, "ALLOWED", frozenset({("tests/test_a.py", 2, "ssn")}))
    assert scan.main(["tests/test_a.py"]) == 1


def test_the_repository_is_clean() -> None:
    root = Path(__file__).parents[1]
    result = subprocess.run(
        ["python3", str(SCRIPT)], cwd=root, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout


def test_text_that_is_not_utf8_is_still_scanned(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    (repo / "tests" / "latin1.py").write_bytes(("x = 'caf\xe9 " + SSN + "'\n").encode("latin-1"))
    assert scan.main(["tests/latin1.py"]) == 1
    assert "tests/latin1.py:1: ssn" in capsys.readouterr().out


def test_reviewed_binaries_are_pinned_by_content(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import hashlib

    monkeypatch.chdir(repo)
    digest = hashlib.sha256((repo / "docs" / "image.bin").read_bytes()).hexdigest()
    monkeypatch.setattr(scan, "UNSCANNABLE", {"docs/image.bin": digest})
    assert scan.main(["docs/image.bin"]) == 0
    # Replacing the reviewed bytes, even with another binary, ends the exemption.
    (repo / "docs" / "image.bin").write_bytes(b"\x00" + SSN.encode())
    assert scan.main(["docs/image.bin"]) == 1
    assert "docs/image.bin: unscannable" in capsys.readouterr().out


def test_the_listed_binaries_match_their_pins() -> None:
    import hashlib

    root = Path(__file__).parents[1]
    for path, digest in scan.UNSCANNABLE.items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest, path


def test_untracked_files_in_scope_are_scanned(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    (repo / "tests" / "test_new.py").write_text(f"VALUE = '{EMAIL}'\n")
    assert scan.main([]) == 1
    assert "tests/test_new.py:1: email" in capsys.readouterr().out


def test_beads_owner_fields_are_not_scanned_but_issue_text_is(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    monkeypatch.chdir(repo)
    issues = repo / ".beads" / "issues.jsonl"
    issues.parent.mkdir()
    clean = {"id": "x-1", "title": "Invented task", "owner": EMAIL}
    leaky = {"id": "x-2", "title": "Invented task", "owner": EMAIL, "description": f"call {PHONE}"}
    issues.write_text(json.dumps(clean) + "\n" + json.dumps(leaky) + "\n")
    assert scan.main([".beads/issues.jsonl"]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == ".beads/issues.jsonl:2: phone"
    assert "email" not in out


def test_root_files_are_scanned(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    (repo / "README.md").write_text(f"Questions to {EMAIL}\n")
    assert scan.main(["README.md"]) == 1
    assert "README.md:1: email" in capsys.readouterr().out


def _issues(repo: Path, *lines: str) -> None:
    issues = repo / ".beads" / "issues.jsonl"
    issues.parent.mkdir(exist_ok=True)
    issues.write_text("".join(line + "\n" for line in lines))


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        # Text in owner that is not the creator's email is scanned.
        ([f'{{"id": "x", "owner": "{SSN}"}}'], [".beads/issues.jsonl:1: ssn"]),
        # A nested owner is issue text, not the record's creator.
        (
            [f'{{"id": "x", "owner": "a@example.invalid", "notes": {{"owner": "{EMAIL}"}}}}'],
            [".beads/issues.jsonl:1: email"],
        ),
        # A malformed line is scanned as written, owner email included: it fails closed.
        ([f'{{"id": "x", "owner": "{EMAIL}"'], [".beads/issues.jsonl:1: email"]),
        # Duplicate keys cannot hide a value: the line is scanned as written, and the owner
        # exemption is withdrawn, so it fails closed.
        (
            [f'{{"description": "call {PHONE}", "owner": "{EMAIL}", "description": "x"}}'],
            [".beads/issues.jsonl:1: phone", ".beads/issues.jsonl:1: email"],
        ),
        # Line numbers survive blank lines, malformed lines, and escaped separators.
        (
            ["", "not json", f'{{"title": "a\\u2028b", "owner": "{EMAIL}"}}', f'{{"t": "{SSN}"}}'],
            [".beads/issues.jsonl:4: ssn"],
        ),
    ],
    ids=["owner-text", "nested-owner", "malformed", "duplicate-keys", "line-numbers"],
)
def test_the_beads_owner_exemption_is_narrow(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    lines: list[str],
    expected: list[str],
) -> None:
    monkeypatch.chdir(repo)
    _issues(repo, *lines)
    scan.main([".beads/issues.jsonl"])
    reported = [line for line in capsys.readouterr().out.splitlines() if ": " in line]
    assert reported == expected


def test_raw_unicode_line_separators_do_not_shift_line_numbers() -> None:
    # U+2028 and NEL are content inside a line; only newlines end one.
    text = "first \u2028 still first \x85 too\n" + f"second {SSN}\n"
    assert scan.findings(text) == [(2, "ssn")]
