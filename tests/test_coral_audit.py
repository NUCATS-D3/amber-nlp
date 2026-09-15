"""Audit CLI regression tests; every input is invented and temporary."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from experiments.coral.audit import main

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "experiments/coral/scripts/coral_ingest.py"


@pytest.fixture
def invented_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "invented.txt").write_bytes("Alpha βeta\r\nred blue.".encode())
    (root / "invented.ann").write_bytes(
        b"T1\tToken 0 5\tAlpha\n"
        b"T2\tToken 6 10\tBETA\n"
        b"T3\tToken 12 15\tverylong\n"
        b"T4\tToken 12 15;16 20\tred blue\n"
        b"A1\tKind T1 toy\n"
        b"M1\tFlag T2\n"
        b"R1\tPair Arg1:T1 Arg2:T2\n"
        b"*\tPair T1 T2\n"
        b"E1\tAction:T1 Theme:T2\n"
        b"*\tEquiv T1 T2\n"
        b"X1\topaque\n"
    )
    return root


def test_aggregate_audit_omits_example_text(invented_corpus: Path) -> None:
    result = CliRunner().invoke(main, [str(invented_corpus), "--show", "0"])
    assert result.exit_code == 0, result.output
    for label, count in (
        ("documents", 1),
        ("entities", 4),
        ("attributes", 2),
        ("relations", 2),
        ("events", 1),
        ("equiv sets", 1),
        ("unparsed lines", 1),
    ):
        assert any(
            line.split() == [*label.split(), str(count)] for line in result.output.splitlines()
        )
    for value in ("Alpha", "βeta", "BETA", "verylong", "red blue", "opaque", "invented"):
        assert value not in result.output
    assert "25.0%  discontinuous" in result.output
    assert "1 undirected" in result.output


def test_default_audit_still_prints_mismatch_examples(invented_corpus: Path) -> None:
    result = CliRunner().invoke(main, [str(invented_corpus)])
    assert result.exit_code == 0, result.output
    assert "verylong" in result.output
    assert "Alpha" not in result.output
    assert "opaque" not in result.output


def test_category_and_unparsed_options_are_preserved(invented_corpus: Path) -> None:
    result = CliRunner().invoke(
        main, [str(invented_corpus), "--category", "exact", "--show", "1", "--dump-unparsed"]
    )
    assert result.exit_code == 0, result.output
    assert "Alpha" in result.output
    assert "opaque" in result.output
    assert "verylong" not in result.output


def test_jsonl_uses_source_quotes_and_preserves_fragment_offsets(
    invented_corpus: Path, tmp_path: Path
) -> None:
    originals = {p: p.read_bytes() for p in invented_corpus.iterdir()}
    output = tmp_path / "outputs" / "audit.jsonl"
    result = CliRunner().invoke(main, [str(invented_corpus), "--show", "0", "--jsonl", str(output)])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 4
    assert rows[0]["attributes"] == {"Kind": "toy"}
    assert rows[1]["quote"] == "βeta"
    assert rows[1]["ann_quote"] == "BETA"
    assert rows[1]["attributes"] == {"Flag": None}
    assert rows[3] == {
        "doc_id": "invented",
        "ann_id": "T4",
        "mention_type": "Token",
        "start": 12,
        "end": 20,
        "spans": [[12, 15], [16, 20]],
        "quote": "redblue",
        "ann_quote": "red blue",
        "status": "discontinuous",
        "attributes": {},
    }
    assert {p: p.read_bytes() for p in invented_corpus.iterdir()} == originals


def test_no_paired_files_fails(tmp_path: Path) -> None:
    (tmp_path / "unpaired.ann").write_bytes(b"T1\tToken 0 3\ttoy\n")
    result = CliRunner().invoke(main, [str(tmp_path), "--show", "0"])
    assert result.exit_code != 0
    assert "no .ann/.txt pairs" in result.output


@pytest.mark.parametrize("entrypoint", ["script", "module"])
def test_entrypoints_run_the_same_aggregate_audit(
    invented_corpus: Path, tmp_path: Path, entrypoint: str
) -> None:
    args = [str(SCRIPT)] if entrypoint == "script" else ["-m", "experiments.coral.audit"]
    result = subprocess.run(
        [sys.executable, *args, str(invented_corpus), "--show", "0"],
        # The legacy script must also work when called outside the repository.
        cwd=tmp_path if entrypoint == "script" else REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    expected = CliRunner().invoke(main, [str(invented_corpus), "--show", "0"])
    assert expected.exit_code == 0
    assert result.stdout == expected.output
