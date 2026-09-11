"""Immutable source records and their section spans."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime as DateTime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from amber.ids import source_id as make_source_id


class SourceKind(StrEnum):
    note = "note"
    lab = "lab"
    medication = "medication"
    problem = "problem"
    imaging = "imaging"
    other = "other"


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    label: str
    category: str | None = None
    is_template: bool = False

    @model_validator(mode="after")
    def validate_bounds(self) -> Section:
        if self.end <= self.start:
            raise ValueError("section end must be greater than start")
        return self


class Source(BaseModel):
    """A content-addressed source whose text cannot change without changing its id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    patient_id: str = Field(min_length=1)
    kind: SourceKind
    external_id: str | None
    datetime: DateTime | None
    text: str | None
    record: dict[str, Any] | None
    sections: list[Section] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        patient_id: str,
        kind: SourceKind,
        external_id: str | None,
        datetime: DateTime | None,
        text: str | None,
        record: dict[str, Any] | None,
        sections: Sequence[Section] = (),
        meta: dict[str, Any] | None = None,
    ) -> Source:
        return cls(
            source_id=make_source_id(
                patient_id=patient_id,
                kind=kind.value,
                external_id=external_id,
                text=text,
            ),
            patient_id=patient_id,
            kind=kind,
            external_id=external_id,
            datetime=datetime,
            text=text,
            record=record,
            sections=list(sections),
            meta={} if meta is None else meta,
        )

    @model_validator(mode="after")
    def validate_content_and_id(self) -> Source:
        if self.text is None and self.record is None:
            raise ValueError("source must contain text or a structured record")
        expected_id = make_source_id(
            patient_id=self.patient_id,
            kind=self.kind.value,
            external_id=self.external_id,
            text=self.text,
        )
        if self.source_id != expected_id:
            raise ValueError("source_id does not match the source identity fields")
        if self.text is None and self.sections:
            raise ValueError("a source without text cannot contain sections")
        if self.text is not None:
            for section in self.sections:
                if section.end > len(self.text):
                    raise ValueError("section bounds exceed source text")
        return self
