"""Parser regression tests using invented BRAT records only."""

import subprocess
import sys
from pathlib import Path

import pytest

from experiments.coral.brat import parse_ann, read_text, split_records

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_parser_import_needs_only_stdlib_and_does_not_load_cli() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; from experiments.coral.brat import parse_ann; "
            "assert callable(parse_ann); "
            "assert not {'click', 'amber', 'experiments.coral.audit', "
            "'experiments.coral.scripts.coral_ingest'} & sys.modules.keys()",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_read_and_parse_preserve_unicode_and_crlf_offsets(tmp_path: Path) -> None:
    text_path = tmp_path / "invented.txt"
    text_path.write_bytes("αβ\r\nToy one.\r\n".encode())
    ann_path = tmp_path / "invented.ann"
    ann_path.write_bytes(b"T1\tToken 4 7\tToy\n")

    text = read_text(text_path)
    doc = parse_ann(ann_path, text)

    assert text == "αβ\r\nToy one.\r\n"
    assert doc.doc_id == "invented"
    assert doc.path == ann_path
    assert doc.text == text
    entity = doc.entities["T1"]
    assert (entity.start, entity.end, entity.span_len) == (4, 7, 3)
    assert entity.text_quote == "Toy"
    assert entity.quote == "Toy"
    assert entity.status == "exact"


@pytest.mark.parametrize(
    ("text", "record", "status", "text_quote", "spans"),
    [
        ("Alpha", "T1\tToken 0 5\tAlpha\n", "exact", "Alpha", [(0, 5)]),
        ("A  B", "T1\tToken 0 4\tA B\n", "whitespace", "A  B", [(0, 4)]),
        ("Alpha", "T1\tToken 0 5\tOmega\n", "redacted", "Alpha", [(0, 5)]),
        ("Alpha", "T1\tToken 0 5\tLonger\n", "mismatch", "Alpha", [(0, 5)]),
        (
            "red blue",
            "T1\tToken 0 3;4 8\tred blue\n",
            "discontinuous",
            "redblue",
            [(0, 3), (4, 8)],
        ),
    ],
)
def test_existing_quote_agreement_classifications(
    tmp_path: Path,
    text: str,
    record: str,
    status: str,
    text_quote: str,
    spans: list[tuple[int, int]],
) -> None:
    ann_path = tmp_path / "invented.ann"
    ann_path.write_bytes(record.encode())
    entity = parse_ann(ann_path, text).entities["T1"]

    assert entity.status == status
    assert entity.text_quote == text_quote
    assert entity.spans == spans


def test_multiline_records_preserve_crlf_and_report_orphans(tmp_path: Path) -> None:
    raw = "T1\tToken 0 4\tA\r\nB\nA1\tFlag T1\norphan text\n"
    assert split_records(raw) == [
        (1, "T1\tToken 0 4\tA\r\nB"),
        (3, "A1\tFlag T1"),
        (4, "orphan text"),
    ]
    ann_path = tmp_path / "invented.ann"
    ann_path.write_bytes(raw.encode())
    doc = parse_ann(ann_path, "A\r\nB")
    assert doc.entities["T1"].quote == "A\r\nB"
    assert doc.entities["T1"].status == "exact"
    assert doc.unparsed == [(4, "orphan text")]


def test_records_keep_attributes_relations_events_and_unparsed_lines(tmp_path: Path) -> None:
    ann_path = tmp_path / "invented.ann"
    ann_path.write_bytes(
        b"T1\tToken 0 3\tred\n"
        b"T2\tToken 4 8\tblue\n"
        b"A1\tKind T1 toy\n"
        b"M1\tFlag T2\n"
        b"R1\tPair Arg1:T1 Arg2:T2\n"
        b"*\tPair T2 T1\n"
        b"E1\tAction:T1 Theme:T2\n"
        b"*\tEquiv T1 T2\n"
        b"N1\tReference T1 Toy:1\tred\n"
        b"#1\tAnnotatorNotes T1\ttoy note\n"
        b"X1\topaque\n"
        b"*\tPair T1\n"
    )
    doc = parse_ann(ann_path, "red blue")

    assert [(a.ann_id, a.type, a.target, a.value) for a in doc.attributes] == [
        ("A1", "Kind", "T1", "toy"),
        ("M1", "Flag", "T2", None),
    ]
    assert [(r.type, r.args, r.symmetric) for r in doc.relations] == [
        ("Pair", {"Arg1": "T1", "Arg2": "T2"}, False),
        ("Pair", {"Arg1": "T2", "Arg2": "T1"}, True),
    ]
    assert [(e.ann_id, e.type, e.trigger, e.args) for e in doc.events] == [
        ("E1", "Action", "T1", {"Theme": "T2"}),
    ]
    assert doc.equivs == [["T1", "T2"]]
    assert doc.unparsed == [(11, "X1\topaque"), (12, "*\tPair T1")]
