"""Adversarial tests for the implemented M1 domain invariants."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from amber.grounding import exact_quote
from amber.ids import canonical_sha256, inclusion_id, new_ulid
from amber.schemas import GroundingFailure, Inclusion, Section, Source, SourceKind
from amber.tools.quote import quote


def make_source(text: str = "Alpha βeta\r\nRepeated. Repeated.") -> Source:
    return Source.create(
        patient_id="synthetic-patient-1",
        kind=SourceKind.note,
        external_id="synthetic-note-1",
        datetime=datetime(2026, 1, 2, tzinfo=UTC),
        text=text,
        record=None,
    )


def test_source_id_is_deterministic_and_text_sensitive() -> None:
    source = make_source()
    assert source.source_id == make_source().source_id
    assert source.source_id != make_source(source.text.replace("\r\n", "\n")).source_id


def test_source_rejects_stale_id_and_mutation() -> None:
    source = make_source()
    with pytest.raises(ValidationError, match="source_id does not match"):
        Source.model_validate({**source.model_dump(), "text": "changed"})
    with pytest.raises(ValidationError, match="frozen"):
        source.text = "changed"  # type: ignore[misc]


def test_source_validates_section_bounds() -> None:
    with pytest.raises(ValidationError, match="section bounds exceed"):
        Source.create(
            patient_id="synthetic-patient-1",
            kind=SourceKind.note,
            external_id="synthetic-note-1",
            datetime=None,
            text="short",
            record=None,
            sections=(Section(start=0, end=6, label="too long"),),
        )


def test_exact_quote_preserves_unicode_and_crlf_offsets() -> None:
    source = make_source()
    result = exact_quote(source, "βeta\r\n")
    assert isinstance(result, Inclusion)
    assert result.start == 6
    assert result.end == 12
    assert result.quote == source.text[result.start : result.end]
    assert result.evidence_id == inclusion_id(
        source_id=source.source_id,
        start=result.start,
        end=result.end,
    )


def test_repeated_quote_requires_an_exact_hint() -> None:
    source = make_source()
    ambiguous = exact_quote(source, "Repeated.")
    assert isinstance(ambiguous, GroundingFailure)
    assert ambiguous.reason == "ambiguous"
    assert ambiguous.match_starts == (12, 22)

    grounded = exact_quote(source, "Repeated.", hint_start=22)
    assert isinstance(grounded, Inclusion)
    assert grounded.start == 22


@pytest.mark.parametrize(
    ("candidate", "hint_start", "reason"),
    [
        ("", None, "empty_candidate"),
        ("missing", None, "not_found"),
        ("Alpha", -1, "hint_out_of_bounds"),
        ("Alpha", 1, "hint_mismatch"),
    ],
)
def test_quote_returns_explicit_failures(
    candidate: str,
    hint_start: int | None,
    reason: str,
) -> None:
    result = exact_quote(make_source(), candidate, hint_start)
    assert isinstance(result, GroundingFailure)
    assert result.reason == reason


def test_inclusion_cannot_be_constructed_outside_grounding() -> None:
    source = make_source("verified text")
    with pytest.raises(ValidationError, match="verified grounding path"):
        Inclusion(
            evidence_id=inclusion_id(source_id=source.source_id, start=0, end=8),
            source_id=source.source_id,
            start=0,
            end=8,
            quote="verified",
            alignment="exact",
            alignment_score=1.0,
        )


def test_grounded_inclusion_id_changes_with_offsets() -> None:
    source = make_source("same same")
    first = exact_quote(source, "same", hint_start=0)
    second = exact_quote(source, "same", hint_start=5)
    assert isinstance(first, Inclusion)
    assert isinstance(second, Inclusion)
    assert first.evidence_id != second.evidence_id


def test_quote_tool_rejects_unknown_sources() -> None:
    with pytest.raises(KeyError, match="unknown source_id"):
        quote("missing", "text", sources={})


def test_canonical_hash_rejects_unsafe_values() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        canonical_sha256({"value": float("nan")})
    with pytest.raises(TypeError, match="string keys"):
        canonical_sha256({"value": {1: "not canonical"}})


def test_ulids_are_valid_and_distinct() -> None:
    first = new_ulid()
    second = new_ulid()
    assert len(first) == 26
    assert first != second
