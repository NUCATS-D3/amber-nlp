"""Tests for immutable task, claim, and inference records."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from amber.ids import new_ulid, schema_ref, validate_ulid
from amber.schemas import AnswerModel, Claim, EvidenceEdge, InferenceEvidence, Provenance, Task
from amber.schemas._immutable import freeze_json
from amber.schemas.claims import _ClaimRecord
from amber.schemas.oncology_current_progression import OncologyCurrentProgressionAnswer

from ._claim_helpers import make_provenance, make_task


def claim_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "claim_id": new_ulid(),
        "source_id": "synthetic-source-1",
        "patient_id": "synthetic-patient-1",
        "task": "oncology_current_progression",
        "schema_ref": schema_ref(OncologyCurrentProgressionAnswer),
        "value": {"progression_or_recurrence": True},
        "effective_datetime": datetime(2026, 1, 2, tzinfo=UTC),
        "confidence": 0.75,
        "status": "proposed",
        "provenance": make_provenance(),
    }
    data.update(overrides)
    return data


def inference_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "evidence_id": new_ulid(),
        "rationale": "The verified span directly supports the answer.",
        "inputs": ["source-evidence-1"],
        "trace_id": None,
        "span_id": None,
        "provenance": make_provenance(),
    }
    data.update(overrides)
    return data


def test_freeze_json_breaks_aliases_and_preserves_wire_shape() -> None:
    original = {"rows": [{"active": True}]}

    frozen = freeze_json(original)
    original["rows"][0]["active"] = False

    with pytest.raises(TypeError):
        frozen["rows"][0]["active"] = False
    with pytest.raises(TypeError):
        frozen["rows"].append({})
    assert json.loads(json.dumps(frozen)) == {"rows": [{"active": True}]}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("new", 1),
        lambda value: value.__delitem__("rows"),
        lambda value: value.clear(),
        lambda value: value.pop("rows"),
        lambda value: value.popitem(),
        lambda value: value.setdefault("new", 1),
        lambda value: value.update({"new": 1}),
        lambda value: value.__ior__({"new": 1}),
    ],
)
def test_frozen_json_dict_rejects_every_standard_mutator(
    mutate: Callable[[dict[str, object]], object],
) -> None:
    frozen = freeze_json({"rows": []})

    with pytest.raises(TypeError, match="frozen JSON values cannot be mutated"):
        mutate(frozen)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__(0, 2),
        lambda value: value.__setitem__(slice(None), [2]),
        lambda value: value.__delitem__(0),
        lambda value: value.__delitem__(slice(None)),
        lambda value: value.append(2),
        lambda value: value.clear(),
        lambda value: value.extend([2]),
        lambda value: value.insert(0, 2),
        lambda value: value.pop(),
        lambda value: value.remove(1),
        lambda value: value.reverse(),
        lambda value: value.sort(),
        lambda value: value.__iadd__([2]),
        lambda value: value.__imul__(2),
    ],
)
def test_frozen_json_list_rejects_every_standard_mutator(
    mutate: Callable[[list[object]], object],
) -> None:
    frozen = freeze_json([1])

    with pytest.raises(TypeError, match="frozen JSON values cannot be mutated"):
        mutate(frozen)


def test_frozen_json_rejects_reinitialization_and_deepcopy_returns_itself() -> None:
    frozen_dict = freeze_json({"value": [1]})
    frozen_list = frozen_dict["value"]

    with pytest.raises(TypeError, match="frozen JSON values cannot be mutated"):
        frozen_dict.__init__({"changed": True})
    with pytest.raises(TypeError, match="frozen JSON values cannot be mutated"):
        frozen_list.__init__([2])
    assert copy.copy(frozen_dict) is frozen_dict
    assert copy.deepcopy(frozen_dict) is frozen_dict
    assert copy.copy(frozen_list) is frozen_list
    assert copy.deepcopy(frozen_list) is frozen_list


@pytest.mark.parametrize("value", [None, True, False, 0, -4, 1.5, "text"])
def test_freeze_json_accepts_json_scalars_without_changing_their_type(value: object) -> None:
    frozen = freeze_json(value)

    assert frozen == value
    assert type(frozen) is type(value)


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        (float("-inf"), ValueError),
        ({1: "not a JSON object"}, TypeError),
        (b"bytes", TypeError),
        (object(), TypeError),
    ],
)
def test_freeze_json_rejects_values_that_are_not_safe_json(
    value: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        freeze_json(value)


def test_schema_ref_uses_the_exact_canonical_answer_schema_hash() -> None:
    schema = OncologyCurrentProgressionAnswer.model_json_schema()
    payload = json.dumps(
        schema,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    expected_digest = hashlib.sha256(payload).hexdigest()

    assert schema_ref(OncologyCurrentProgressionAnswer) == (
        "amber.schemas.oncology_current_progression:"
        f"OncologyCurrentProgressionAnswer@{expected_digest}"
    )


def test_validate_ulid_accepts_only_the_unchanged_canonical_string() -> None:
    identifier = new_ulid()

    assert validate_ulid(identifier) == identifier
    with pytest.raises(ValueError):
        validate_ulid(identifier.lower())
    with pytest.raises(ValueError):
        validate_ulid("not-a-ulid")


def test_task_accepts_only_answer_model_subclasses_and_has_v1_defaults() -> None:
    class OtherModel(BaseModel):
        value: str

    task = make_task()

    assert not issubclass(Task, AnswerModel)
    assert task.name == "oncology_current_progression"
    assert task.answer_model is OncologyCurrentProgressionAnswer
    assert task.evidence_policy == "field"
    assert task.scope == "note"
    assert set(Task.model_fields) == {
        "name",
        "answer_model",
        "instructions",
        "evidence_policy",
        "scope",
    }
    with pytest.raises(ValidationError):
        make_task(answer_model=OtherModel)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["", " ", "\t\n"])
def test_task_rejects_blank_names(name: str) -> None:
    with pytest.raises(ValidationError, match="task name must not be blank"):
        Task(
            name=name,
            answer_model=OncologyCurrentProgressionAnswer,
            instructions="Synthetic task instructions.",
        )


def test_task_is_frozen_and_forbids_extra_fields() -> None:
    task = make_task()

    with pytest.raises(ValidationError, match="frozen"):
        task.name = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Task(
            name="synthetic",
            answer_model=OncologyCurrentProgressionAnswer,
            instructions="Synthetic task instructions.",
            unexpected=True,  # type: ignore[call-arg]
        )


def test_provenance_prompt_versions_are_deeply_immutable_and_round_trip() -> None:
    prompt_versions = {"extractor": "prompts:/synthetic/1"}
    provenance = Provenance(
        **{
            **make_provenance().model_dump(exclude={"prompt_versions"}),
            "prompt_versions": prompt_versions,
        }
    )
    prompt_versions["extractor"] = "changed"

    with pytest.raises(TypeError, match="frozen JSON values cannot be mutated"):
        provenance.prompt_versions["extractor"] = "changed"
    assert provenance.prompt_versions == {"extractor": "prompts:/synthetic/1"}
    assert Provenance.model_validate(provenance.model_dump()) == provenance
    assert json.loads(provenance.model_dump_json())["prompt_versions"] == {
        "extractor": "prompts:/synthetic/1"
    }


def test_claim_record_has_exact_v1_fields_and_preserves_wire_shape() -> None:
    value = {"progression_or_recurrence": True, "details": [{"site": "liver"}]}
    record = _ClaimRecord.model_validate(claim_data(value=value))
    value["details"][0]["site"] = "changed"

    assert set(_ClaimRecord.model_fields) == {
        "claim_id",
        "source_id",
        "patient_id",
        "task",
        "schema_ref",
        "value",
        "effective_datetime",
        "confidence",
        "status",
        "provenance",
    }
    with pytest.raises(TypeError, match="frozen JSON values cannot be mutated"):
        record.value["details"][0]["site"] = "changed"
    assert record.value["details"][0]["site"] == "liver"
    assert json.loads(record.model_dump_json())["value"] == {
        "progression_or_recurrence": True,
        "details": [{"site": "liver"}],
    }
    assert record.model_dump()["value"] == {
        "progression_or_recurrence": True,
        "details": [{"site": "liver"}],
    }


@pytest.mark.parametrize("status", ["proposed", "verified", "rejected", "gold"])
def test_claim_record_preserves_every_v1_status(status: str) -> None:
    assert _ClaimRecord.model_validate(claim_data(status=status)).status == status


@pytest.mark.parametrize("confidence", [None, 0.0, 0.5, 1.0])
def test_claim_record_accepts_nullable_bounded_confidence(confidence: float | None) -> None:
    assert _ClaimRecord.model_validate(claim_data(confidence=confidence)).confidence == confidence


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), float("inf"), float("-inf")])
def test_claim_record_rejects_out_of_range_or_nonfinite_confidence(confidence: float) -> None:
    with pytest.raises(ValidationError):
        _ClaimRecord.model_validate(claim_data(confidence=confidence))


@pytest.mark.parametrize("field", ["source_id", "patient_id", "task", "schema_ref"])
@pytest.mark.parametrize("value", ["", " \t", 3])
def test_claim_record_rejects_nonstrict_or_blank_identifiers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _ClaimRecord.model_validate(claim_data(**{field: value}))


def test_claim_record_requires_a_canonical_ulid_and_forbids_extras() -> None:
    identifier = new_ulid()

    with pytest.raises(ValidationError):
        _ClaimRecord.model_validate(claim_data(claim_id=identifier.lower()))
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _ClaimRecord.model_validate(claim_data(extra="not allowed"))


def test_claim_cannot_be_constructed_or_loaded_without_graph_validation() -> None:
    data = claim_data()

    with pytest.raises(ValidationError, match="validated claim graph"):
        Claim(**data)
    with pytest.raises(ValidationError, match="validated claim graph"):
        Claim.model_validate(data)
    with pytest.raises(ValidationError, match="validated claim graph"):
        Claim.model_validate_json(
            json.dumps(_ClaimRecord.model_validate(data).model_dump(mode="json"))
        )
    with pytest.raises(TypeError, match="validated claim graph"):
        Claim.model_construct(**data)


def test_rejected_claim_cannot_bypass_the_graph_boundary() -> None:
    with pytest.raises(ValidationError, match="validated claim graph"):
        Claim.model_validate(claim_data(status="rejected"))


def test_claim_record_revalidates_an_unchecked_provenance_instance() -> None:
    valid = make_provenance()
    forged = Provenance.model_construct(
        **{**valid.model_dump(), "producer": ""},
    )

    with pytest.raises(ValidationError):
        _ClaimRecord.model_validate(claim_data(provenance=forged))


def test_inference_evidence_has_exact_v1_fields_and_immutable_inputs() -> None:
    inputs = ["source-evidence-1"]
    inference = InferenceEvidence.model_validate(inference_data(inputs=inputs))
    inputs.append("changed")

    assert set(InferenceEvidence.model_fields) == {
        "kind",
        "evidence_id",
        "rationale",
        "inputs",
        "trace_id",
        "span_id",
        "provenance",
    }
    assert inference.kind == "inference"
    assert inference.trace_id is None
    assert inference.span_id is None
    with pytest.raises(TypeError, match="frozen JSON values cannot be mutated"):
        inference.inputs.append("changed")
    assert inference.model_dump()["inputs"] == ["source-evidence-1"]
    assert json.loads(inference.model_dump_json())["inputs"] == ["source-evidence-1"]


@pytest.mark.parametrize("rationale", ["", " ", "\t\n"])
def test_inference_evidence_rejects_blank_rationales(rationale: str) -> None:
    with pytest.raises(ValidationError):
        InferenceEvidence.model_validate(inference_data(rationale=rationale))


@pytest.mark.parametrize("inputs", [[], ["same", "same"], [""], ["  "], [3]])
def test_inference_evidence_requires_distinct_nonblank_string_inputs(inputs: list[object]) -> None:
    with pytest.raises(ValidationError):
        InferenceEvidence.model_validate(inference_data(inputs=inputs))


@pytest.mark.parametrize("field", ["trace_id", "span_id"])
@pytest.mark.parametrize("value", ["", " ", 3])
def test_inference_evidence_rejects_nonstrict_or_blank_optional_ids(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        InferenceEvidence.model_validate(inference_data(**{field: value}))


def test_inference_evidence_requires_a_canonical_ulid_and_revalidates_provenance() -> None:
    identifier = new_ulid()
    valid = make_provenance()
    forged = Provenance.model_construct(
        **{**valid.model_dump(), "producer": ""},
    )

    with pytest.raises(ValidationError):
        InferenceEvidence.model_validate(inference_data(evidence_id=identifier.lower()))
    with pytest.raises(ValidationError):
        InferenceEvidence.model_validate(inference_data(provenance=forged))


def test_evidence_edge_has_exact_v1_fields_and_defaults() -> None:
    edge = EvidenceEdge(claim_id="claim-1", evidence_id="evidence-1")

    assert set(EvidenceEdge.model_fields) == {"claim_id", "evidence_id", "role", "weight"}
    assert edge.model_dump() == {
        "claim_id": "claim-1",
        "evidence_id": "evidence-1",
        "role": None,
        "weight": None,
    }


@pytest.mark.parametrize("field", ["claim_id", "evidence_id", "role"])
@pytest.mark.parametrize("value", ["", " ", 3])
def test_evidence_edge_rejects_nonstrict_or_blank_identifiers(field: str, value: object) -> None:
    data: dict[str, object] = {"claim_id": "claim-1", "evidence_id": "evidence-1", field: value}
    with pytest.raises(ValidationError):
        EvidenceEdge.model_validate(data)


@pytest.mark.parametrize("weight", [float("nan"), float("inf"), float("-inf")])
def test_evidence_edge_rejects_nonfinite_weights(weight: float) -> None:
    with pytest.raises(ValidationError):
        EvidenceEdge(claim_id="claim-1", evidence_id="evidence-1", weight=weight)


def test_evidence_edge_accepts_finite_weights_and_is_closed_and_frozen() -> None:
    edge = EvidenceEdge(claim_id="claim-1", evidence_id="evidence-1", role="answer", weight=-0.5)

    assert edge.weight == -0.5
    with pytest.raises(ValidationError, match="frozen"):
        edge.weight = 1.0  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceEdge(claim_id="claim-1", evidence_id="evidence-1", extra=True)  # type: ignore[call-arg]
