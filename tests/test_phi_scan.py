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
    ],
    ids=["ssn", "phone", "phone-dotted", "email", "mrn", "mrn-words", "dob", "dob-words", "born"],
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
    ],
)
def test_reserved_and_look_alike_values_are_not_caught(text: str) -> None:
    assert scan.findings(text) == []


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
        "src/amber/other.py": f"OUT_OF_SCOPE = '{SSN}'\n",
        "docs/image.bin": "",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    (tmp_path / "docs" / "image.bin").write_bytes(b"\xff\xfe\x00" + SSN.encode())
    _git(tmp_path, "add", "-A")
    return tmp_path


def test_the_scan_covers_tracked_files_in_scope_and_never_prints_matches(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    assert scan.main([]) == 1
    out = capsys.readouterr().out
    assert "tests/test_a.py:1: ssn" in out
    assert "docs/note with spaces.md:1: email" in out
    assert "src/amber/other.py" not in out and "image.bin" not in out
    for secret in (SSN, EMAIL, "hospital", "3456"):
        assert secret not in out


def test_explicit_paths_are_scanned_when_in_scope(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    assert scan.main(["examples/clean.json", "src/amber/other.py"]) == 0
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
