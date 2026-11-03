"""Synthetic constructors shared by claim-kernel tests."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from amber.ids import new_ulid, schema_ref
from amber.schemas import (
    AnswerModel,
    Provenance,
    Sensitivity,
    Source,
    SourceKind,
    Task,
    Zone,
)


class SyntheticFindingAnswer(AnswerModel):
    """Invented single-boolean answer used to exercise the task-agnostic kernel."""

    finding_present: bool = Field(strict=True)


def make_source(text: str = "Alpha beta") -> Source:
    return Source.create(
        patient_id="synthetic-patient-1",
        kind=SourceKind.note,
        external_id="synthetic-note-1",
        datetime=datetime(2026, 1, 2, tzinfo=UTC),
        text=text,
        record=None,
    )


SYNTHETIC_RUN_ID = "01K00000000000000000000000"


def make_provenance() -> Provenance:
    return Provenance(
        producer="tool:synthetic@1",
        run_id=SYNTHETIC_RUN_ID,
        prompt_versions={"extractor": "prompts:/synthetic/1"},
        destination="local:synthetic",
        policy_version="synthetic-policy@1",
        zone=Zone.local,
        sensitivity=Sensitivity.synthetic,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        version="0.0.0",
    )


def make_task(
    answer_model: type[AnswerModel] = SyntheticFindingAnswer,
    evidence_policy: Literal["claim", "field"] = "field",
) -> Task:
    return Task(
        name="synthetic_finding",
        answer_model=answer_model,
        instructions="Determine whether the invented finding is documented.",
        evidence_policy=evidence_policy,
    )


def make_claim_record(
    *,
    source: Source,
    value: dict[str, Any],
    task: Task | None = None,
) -> dict[str, Any]:
    bound_task = make_task() if task is None else task
    return {
        "claim_id": new_ulid(),
        "source_id": source.source_id,
        "patient_id": source.patient_id,
        "task": bound_task.name,
        "variable": bound_task.name,
        "schema_ref": schema_ref(bound_task.answer_model),
        "value": value,
        "effective_datetime": source.datetime,
        "confidence": 0.75,
        "status": "proposed",
        "provenance": make_provenance().model_dump(),
    }


def make_inference_record(*, inputs: list[str]) -> dict[str, Any]:
    return {
        "kind": "inference",
        "evidence_id": new_ulid(),
        "rationale": "The supplied evidence supports this synthetic inference.",
        "inputs": inputs,
        "trace_id": None,
        "span_id": None,
        "provenance": make_provenance().model_dump(),
    }
