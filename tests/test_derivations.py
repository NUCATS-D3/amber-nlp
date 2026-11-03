"""Derivation records on inference evidence (contract v0.1 section 3.4; catalog VAR-08 and the
structural half of VAR-16)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from amber.conformance import fixtures as fx
from amber.schemas import Derivation, InferenceEvidence

T_CLAIM = fx.fixed_ulid(10, 1)
N_CLAIM = fx.fixed_ulid(10, 2)
SPAN = "a" * 64


def _derivation(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "rule",
        "name": "synthetic_stage",
        "version": "1.0",
        "inputs": {"T": [T_CLAIM], "N": [N_CLAIM], "M": []},
        "parameters": {"edition": 1},
        "defaults_used": {"M": "M0"},
    }
    payload.update(changes)
    return payload


def _inference(derivation: Any = None, inputs: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "inference",
        "evidence_id": fx.fixed_ulid(11, 3),
        "rationale": "Invented rule application.",
        "inputs": inputs if inputs is not None else [T_CLAIM, N_CLAIM, SPAN],
        "trace_id": None,
        "span_id": None,
        "provenance": fx.provenance("rule:synthetic_stage@1.0").model_dump(mode="json"),
    }
    if derivation is not None:
        payload["derivation"] = derivation
    return payload


def test_a_rule_derivation_round_trips() -> None:
    record = InferenceEvidence.model_validate(_inference(_derivation()))
    assert record.derivation is not None
    assert record.derivation.inputs == {"T": (T_CLAIM,), "N": (N_CLAIM,), "M": ()}
    assert record.derivation.defaults_used == {"M": "M0"}
    dumped = json.loads(record.model_dump_json())
    assert InferenceEvidence.model_validate(dumped) == record
    assert dumped["derivation"]["inputs"]["M"] == []


def test_an_inference_without_a_derivation_serializes_as_before() -> None:
    record = InferenceEvidence.model_validate(_inference())
    assert record.derivation is None
    assert "derivation" not in record.model_dump(mode="json")
    assert "derivation" not in json.loads(record.model_dump_json())


def test_derivation_inputs_must_be_inference_inputs() -> None:
    """VAR-08."""
    with pytest.raises(ValidationError, match="inference's inputs"):
        InferenceEvidence.model_validate(_inference(_derivation(), inputs=[T_CLAIM, SPAN]))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"defaults_used": {"T": "T1"}}, "no inputs"),
        ({"defaults_used": {"X": "T1"}}, "no inputs"),
        ({"kind": "model", "parameters": {}}, "rule derivations"),
        ({"kind": "human", "parameters": {}}, "rule derivations"),
        ({"inputs": {"T": [T_CLAIM, T_CLAIM], "N": [N_CLAIM], "M": []}}, "distinct"),
        ({"inputs": {"": [T_CLAIM]}, "defaults_used": {}}, "role"),
        ({"name": " "}, "blank"),
        ({"inputs": {}, "defaults_used": {}}, "role"),
    ],
)
def test_derivations_are_structurally_checked(changes: dict[str, Any], message: str) -> None:
    """VAR-16, structural half: defaults only on rule derivations and only for empty roles."""
    with pytest.raises(ValidationError, match=message):
        Derivation.model_validate(_derivation(**changes))


def test_model_and_human_derivations_are_recorded_without_defaults() -> None:
    for kind in ("model", "human"):
        record = Derivation.model_validate(_derivation(kind=kind, defaults_used={}))
        assert record.kind == kind


def test_derivations_are_frozen_json() -> None:
    record = Derivation.model_validate(_derivation(parameters={"edition": 1, "tags": ["a"]}))
    with pytest.raises(TypeError):
        record.parameters["edition"] = 2  # type: ignore[index]
    with pytest.raises(ValidationError):
        Derivation.model_validate(_derivation(parameters={"bad": float("nan")}))
