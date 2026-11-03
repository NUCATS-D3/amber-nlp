"""Source identity for notes and structured rows (contract v0.1 section 2.6, decision 2.9.13;
catalog MEN-12)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from amber import ids
from amber.schemas import Source, SourceKind


def _row(record: dict[str, Any] | None, *, text: str | None = None) -> Source:
    return Source.create(
        patient_id="synthetic-patient-1",
        kind=SourceKind.lab,
        external_id="synthetic-lab-1",
        datetime=datetime(2026, 1, 2, tzinfo=UTC),
        text=text,
        record=record,
    )


def test_rows_differing_only_in_record_get_different_ids() -> None:
    first = _row({"test": "synthetic-analyte", "value": 1.5, "unit": "mg/dL"})
    second = _row({"test": "synthetic-analyte", "value": 2.5, "unit": "mg/dL"})
    assert first.source_id != second.source_id


def test_record_identity_is_canonical() -> None:
    first = _row({"value": 1.0, "unit": "mg/dL"})
    reordered = _row({"unit": "mg/dL", "value": 1})
    assert first.source_id == reordered.source_id
    assert first.source_id == ids.source_id(
        patient_id="synthetic-patient-1",
        kind="lab",
        external_id="synthetic-lab-1",
        text=None,
        record={"unit": "mg/dL", "value": 1},
    )


def test_note_ids_are_unchanged() -> None:
    note = Source.create(
        patient_id="synthetic-patient-1",
        kind=SourceKind.note,
        external_id="synthetic-note-1",
        datetime=None,
        text="Invented note.",
        record=None,
    )
    assert note.source_id == ids.source_id(
        patient_id="synthetic-patient-1",
        kind="note",
        external_id="synthetic-note-1",
        text="Invented note.",
    )
    assert ids.source_id(patient_id="p", kind="lab", external_id=None, text=None) == (
        "68b5d08f6cbcbb31549b9d116ca1c127ad0b01ca444f220f70a00b16595e254e"
    )


def test_create_and_load_validation_agree() -> None:
    row = _row({"value": 1.5})
    assert Source.model_validate(row.model_dump()) == row
    with pytest.raises(ValidationError, match="source_id does not match"):
        Source.model_validate({**row.model_dump(), "record": {"value": 9.5}})
    stale = ids.source_id(
        patient_id="synthetic-patient-1", kind="lab", external_id="synthetic-lab-1", text=None
    )
    with pytest.raises(ValidationError, match="source_id does not match"):
        Source.model_validate({**row.model_dump(), "source_id": stale})


def test_records_are_frozen_and_json_only() -> None:
    row = _row({"value": 1.5, "flags": ["high"]})
    with pytest.raises(TypeError):
        row.record["value"] = 2.0  # type: ignore[index]
    with pytest.raises(TypeError):
        row.record["flags"].append("low")  # type: ignore[index]
    with pytest.raises(ValidationError, match="JSON"):
        _row({"when": datetime(2026, 1, 1, tzinfo=UTC)})
    with pytest.raises(ValueError, match="non-finite"):
        _row({"value": float("nan")})


def test_a_row_with_text_and_record_hashes_both() -> None:
    with_text = _row({"value": 1.5}, text="Invented report text.")
    without_record = Source.create(
        patient_id="synthetic-patient-1",
        kind=SourceKind.lab,
        external_id="synthetic-lab-1",
        datetime=None,
        text="Invented report text.",
        record=None,
    )
    assert with_text.source_id != without_record.source_id
