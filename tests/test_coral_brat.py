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


def test_crlf_annotation_record_separators_are_not_part_of_quotes(tmp_path: Path) -> None:
    ann_path = tmp_path / "invented.ann"
    ann_path.write_bytes(b"T1\tToken 0 5\talpha\r\nA1\tFlag T1\r\n")

    doc = parse_ann(ann_path, "alpha")

    assert doc.entities["T1"].quote == "alpha"
    assert doc.entities["T1"].status == "exact"
    assert doc.attributes[0].target == "T1"


def test_tabs_inside_an_entity_quote_are_preserved(tmp_path: Path) -> None:
    ann_path = tmp_path / "invented.ann"
    ann_path.write_bytes(b"T1\tToken 0 10\talpha\tbeta\n")

    doc = parse_ann(ann_path, "alpha\tbeta")

    assert doc.entities["T1"].quote == "alpha\tbeta"
    assert doc.entities["T1"].status == "exact"
    assert doc.annotation_inventory_complete


@pytest.mark.parametrize("reverse", [False, True])
def test_empty_metadata_tail_is_tolerated_for_supported_record_kinds(
    tmp_path: Path, reverse: bool
) -> None:
    records = [
        "T1\tToken 0 3\tred",
        "T2\tToken 4 8\tblue",
        "A1\tFlag T1\t",
        "M1\tKind T2 toy\t",
        "R1\tPair Arg1:T1 Arg2:T2\t",
        "E1\tAction:T1 Theme:T2\t",
        "*\tPair T1 T2\t",
        "*\tEquiv T1 T2\t",
    ]
    if reverse:
        records.reverse()
    raw = ("\n".join(records) + "\n").encode()
    ann_path = tmp_path / "invented.ann"
    ann_path.write_bytes(raw)

    doc = parse_ann(ann_path, "red blue")

    assert [(item.type, item.target, item.value) for item in doc.attributes] == [
        ("Flag", "T1", None),
        ("Kind", "T2", "toy"),
    ][:: -1 if reverse else 1]
    assert [(item.type, item.symmetric) for item in doc.relations] == [
        ("Pair", False),
        ("Pair", True),
    ][:: -1 if reverse else 1]
    assert [(item.type, item.trigger) for item in doc.events] == [("Action", "T1")]
    assert doc.equivs == [["T1", "T2"]]
    assert doc.annotation_inventory_complete
    assert doc.diagnostics == []
    assert ann_path.read_bytes() == raw


@pytest.mark.parametrize(
    ("record", "record_id"),
    [
        ("A1\tFlag T1\tnot-empty", "A1"),
        ("M1\tKind T2 toy\tnot-empty", "M1"),
        ("R1\tPair Arg1:T1 Arg2:T2\tnot-empty", "R1"),
        ("E1\tAction:T1 Theme:T2\tnot-empty", "E1"),
        ("*\tPair T1 T2\tnot-empty", None),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_nonempty_metadata_tail_remains_malformed(
    tmp_path: Path, record: str, record_id: str | None, reverse: bool
) -> None:
    records = ["T1\tToken 0 3\tred", "T2\tToken 4 8\tblue", record]
    if reverse:
        records.reverse()
    raw = ("\n".join(records) + "\n").encode()
    ann_path = tmp_path / "invented.ann"
    ann_path.write_bytes(raw)

    doc = parse_ann(ann_path, "red blue")

    malformed_line = records.index(record) + 1
    assert set(doc.entities) == {"T1", "T2"}
    assert doc.unparsed == [(malformed_line, record)]
    assert [(item.code, item.record_id, item.line_number) for item in doc.diagnostics] == [
        ("malformed_record", record_id, malformed_line)
    ]
    assert not doc.annotation_inventory_complete
    assert ann_path.read_bytes() == raw


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


@pytest.mark.parametrize(
    "record",
    [
        "T1\tDiseaseState 0 5",
        "T1\tDiseaseState zero 5\talpha",
        "A1\tCertainty",
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_malformed_records_are_retained_without_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], record: str, reverse: bool
) -> None:
    records = [record, "T2\tToken 0 5\talpha"]
    if reverse:
        records.reverse()
    ann_path = tmp_path / "invented.ann"
    ann_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    doc = parse_ann(ann_path, "alpha")

    malformed_line = records.index(record) + 1
    assert set(doc.entities) == {"T2"}
    assert doc.attributes == []
    assert doc.unparsed == [(malformed_line, record)]
    assert [(item.code, item.record_id, item.line_number) for item in doc.diagnostics] == [
        ("malformed_record", record.split("\t")[0], malformed_line)
    ]
    assert not doc.annotation_inventory_complete
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("reverse", [False, True])
def test_oversized_numeric_offset_is_safely_retained_as_malformed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], reverse: bool
) -> None:
    malformed = f"T1\tToken 0 {'9' * 5000}\talpha"
    records = [malformed, "T2\tToken 0 5\talpha"]
    if reverse:
        records.reverse()
    ann_path = tmp_path / "invented.ann"
    ann_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    doc = parse_ann(ann_path, "alpha")

    malformed_line = records.index(malformed) + 1
    assert set(doc.entities) == {"T2"}
    assert doc.unparsed == [(malformed_line, malformed)]
    assert [(item.code, item.record_id, item.line_number) for item in doc.diagnostics] == [
        ("malformed_record", "T1", malformed_line)
    ]
    assert not doc.annotation_inventory_complete
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_ids_remove_both_records_from_authoritative_inventory(
    tmp_path: Path, reverse: bool
) -> None:
    records = [
        "T1\tDiseaseState 0 5\talpha",
        "A1\tCertainty T1 possible",
        "T1\tDiseaseState 6 10\tbeta",
    ]
    if reverse:
        records.reverse()
    ann_path = tmp_path / "invented.ann"
    ann_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    doc = parse_ann(ann_path, "alpha beta")

    assert "T1" not in doc.entities
    assert len(doc.attributes) == 1
    assert len(doc.unparsed) == 2
    assert {record for _, record in doc.unparsed} == {
        "T1\tDiseaseState 0 5\talpha",
        "T1\tDiseaseState 6 10\tbeta",
    }
    assert [item.code for item in doc.diagnostics].count("duplicate_id") == 1
    assert not doc.annotation_inventory_complete


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_id_detection_spans_record_types(tmp_path: Path, reverse: bool) -> None:
    records = [
        "T1\tToken 0 5\talpha",
        "R1\tPair Arg1:T1 Arg2:T1",
        "R1\tOther Arg1:T1 Arg2:T1",
    ]
    if reverse:
        records.reverse()
    ann_path = tmp_path / "invented.ann"
    ann_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    doc = parse_ann(ann_path, "alpha")

    assert set(doc.entities) == {"T1"}
    assert doc.relations == []
    assert doc.unparsed == [
        (index + 1, record) for index, record in enumerate(records) if record.startswith("R1\t")
    ]
    assert [(item.code, item.record_id, item.line_number) for item in doc.diagnostics] == [
        (
            "duplicate_id",
            "R1",
            max(index + 1 for index, record in enumerate(records) if record.startswith("R1\t")),
        )
    ]
    assert not doc.annotation_inventory_complete


@pytest.mark.parametrize("reverse", [False, True])
def test_unknown_record_is_distinct_from_malformed_supported_record(
    tmp_path: Path, reverse: bool
) -> None:
    records = ["Z1\topaque", "A1\tCertainty", "T1\tToken 0 5\talpha"]
    if reverse:
        records.reverse()
    ann_path = tmp_path / "invented.ann"
    ann_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    doc = parse_ann(ann_path, "alpha")

    assert set(doc.entities) == {"T1"}
    expected = {
        "Z1\topaque": ("unknown_record", "Z1"),
        "A1\tCertainty": ("malformed_record", "A1"),
    }
    assert doc.unparsed == [
        (index + 1, record) for index, record in enumerate(records) if record in expected
    ]
    assert [(item.code, item.record_id, item.line_number) for item in doc.diagnostics] == [
        (*expected[record], index + 1) for index, record in enumerate(records) if record in expected
    ]
    assert not doc.annotation_inventory_complete


@pytest.mark.parametrize(
    ("values", "code"),
    [
        (("possible", "possible"), "duplicate_attribute"),
        (("possible", "certain"), "conflicting_attribute"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_repeated_target_type_attributes_are_diagnosed_and_retained(
    tmp_path: Path, values: tuple[str, str], code: str, reverse: bool
) -> None:
    attrs = [f"A1\tCertainty T1 {values[0]}", f"A2\tCertainty T1 {values[1]}"]
    if reverse:
        attrs.reverse()
    ann_path = tmp_path / "invented.ann"
    ann_path.write_text("\n".join(["T1\tToken 0 5\talpha", *attrs]) + "\n", encoding="utf-8")

    doc = parse_ann(ann_path, "alpha")

    assert len(doc.attributes) == 2
    assert {attribute.value for attribute in doc.attributes} == set(values)
    assert code in {item.code for item in doc.diagnostics}
    assert not doc.annotation_inventory_complete


@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_repeated_attributes_classify_against_all_prior_values(
    tmp_path: Path, reverse: bool
) -> None:
    records = [
        "T1\tToken 0 5\talpha",
        "A1\tCertainty T1 possible",
        "A2\tCertainty T1 certain",
        "A3\tCertainty T1 certain",
    ]
    if reverse:
        records.reverse()
    ann_path = tmp_path / "invented.ann"
    ann_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    doc = parse_ann(ann_path, "alpha")

    assert len(doc.attributes) == 3
    repeated = [
        (item.code, item.record_id, item.line_number)
        for item in doc.diagnostics
        if item.code in {"duplicate_attribute", "conflicting_attribute"}
    ]
    expected_by_order = (
        [
            ("duplicate_attribute", "A2", 2),
            ("conflicting_attribute", "A1", 3),
        ]
        if reverse
        else [
            ("conflicting_attribute", "A2", 3),
            ("duplicate_attribute", "A3", 4),
        ]
    )
    assert repeated == expected_by_order
    assert not doc.annotation_inventory_complete


@pytest.mark.parametrize("reverse", [False, True])
def test_normalization_with_extra_metadata_token_is_retained_as_malformed(
    tmp_path: Path, reverse: bool
) -> None:
    malformed = "N1\tReference T1 Toy:1 surplus\talpha"
    records = [
        malformed,
        "N2\tReference T1 Toy:1\talpha",
        "T1\tToken 0 5\talpha",
    ]
    if reverse:
        records.reverse()
    raw = ("\n".join(records) + "\n").encode()
    ann_path = tmp_path / "invented.ann"
    ann_path.write_bytes(raw)

    doc = parse_ann(ann_path, "alpha")

    malformed_line = records.index(malformed) + 1
    assert set(doc.entities) == {"T1"}
    assert doc.unparsed == [(malformed_line, malformed)]
    assert [(item.code, item.record_id, item.line_number) for item in doc.diagnostics] == [
        ("malformed_record", "N1", malformed_line)
    ]
    assert not doc.annotation_inventory_complete
    assert ann_path.read_bytes() == raw


@pytest.mark.parametrize("reverse", [False, True])
def test_forward_references_are_valid_but_dangling_targets_are_diagnosed(
    tmp_path: Path, reverse: bool
) -> None:
    records = [
        "A1\tCertainty T1 possible",
        "R1\tPair Arg1:T1 Arg2:T9",
        "#1\tAnnotatorNotes T1\tnote",
        "N1\tReference T1 Toy:1\talpha",
        "T1\tToken 0 5\talpha",
    ]
    if reverse:
        records.reverse()
    ann_path = tmp_path / "invented.ann"
    ann_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    doc = parse_ann(ann_path, "alpha")

    assert "T1" in doc.entities
    dangling = [item for item in doc.diagnostics if item.code == "dangling_reference"]
    assert [(item.record_id, item.line_number) for item in dangling] == [
        ("R1", records.index("R1\tPair Arg1:T1 Arg2:T9") + 1)
    ]


def test_symmetric_markers_are_not_duplicate_ids(tmp_path: Path) -> None:
    ann_path = tmp_path / "invented.ann"
    ann_path.write_text(
        "T1\tToken 0 3\tred\nT2\tToken 4 8\tblue\n*\tPair T1 T2\n*\tEquiv T1 T2\n",
        encoding="utf-8",
    )

    doc = parse_ann(ann_path, "red blue")

    assert doc.annotation_inventory_complete
    assert doc.diagnostics == []


def test_out_of_bounds_span_remains_a_quote_mismatch_not_a_parse_diagnostic(
    tmp_path: Path,
) -> None:
    ann_path = tmp_path / "invented.ann"
    ann_path.write_text("T1\tToken 0 9\talpha beta\n", encoding="utf-8")

    doc = parse_ann(ann_path, "alpha")

    assert doc.entities["T1"].status == "mismatch"
    assert doc.diagnostics == []
    assert doc.annotation_inventory_complete
