"""Synthetic constructors shared by claim-kernel tests."""

from datetime import UTC, datetime
from typing import Literal

from amber.schemas import (
    AnswerModel,
    OncologyCurrentProgressionAnswer,
    Provenance,
    Sensitivity,
    Source,
    SourceKind,
    Task,
    Zone,
)


def make_source(text: str = "Alpha beta") -> Source:
    return Source.create(
        patient_id="synthetic-patient-1",
        kind=SourceKind.note,
        external_id="synthetic-note-1",
        datetime=datetime(2026, 1, 2, tzinfo=UTC),
        text=text,
        record=None,
    )


def make_provenance() -> Provenance:
    return Provenance(
        producer="tool:synthetic@1",
        prompt_versions={"extractor": "prompts:/synthetic/1"},
        zone=Zone.local,
        sensitivity=Sensitivity.synthetic,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        version="0.0.0",
    )


def make_task(
    answer_model: type[AnswerModel] = OncologyCurrentProgressionAnswer,
    evidence_policy: Literal["claim", "field"] = "field",
) -> Task:
    return Task(
        name="oncology_current_progression",
        answer_model=answer_model,
        instructions="Determine whether current progression or recurrence is documented.",
        evidence_policy=evidence_policy,
    )
