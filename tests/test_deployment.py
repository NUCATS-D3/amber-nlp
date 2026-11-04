"""Any Extractor as an MLflow pyfunc model (amber-sm8.2): capability and policy checks at load
and predict time, CaseResults with evidence as predictions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mlflow")

import mlflow.pyfunc  # noqa: E402

from amber.cases import load_case_result  # noqa: E402
from amber.conformance import fixtures as fx  # noqa: E402
from amber.mlflow_ext.deployment import (  # noqa: E402
    DeploymentError,
    model_config,
    needed_capabilities,
    save_extractor_model,
)
from amber.policy import DEFAULT_MATRIX, PolicyMatrix, ProviderDestination, StaticGate  # noqa: E402
from amber.providers import ProviderRequest, ProviderResponse  # noqa: E402
from amber.reference import DeterministicExtractor, ScriptedProvider  # noqa: E402
from amber.schemas import Sensitivity, Zone  # noqa: E402

MODEL = ProviderDestination(
    name="local:deployment-model",
    kind="model",
    zone=Zone.local,
    accepts=frozenset({"source_text"}),
    model="synthetic-model",
    capabilities=frozenset({"schema_constrained"}),
)


def _answer(request: ProviderRequest) -> ProviderResponse:
    answer = {"status": "answered", "value": {"present": True}, "quote": "mass", "hint_start": 6}
    return ProviderResponse(parsed=answer, input_tokens=5, output_tokens=3)


def make_extractor() -> DeterministicExtractor:
    return DeterministicExtractor(
        ScriptedProvider(MODEL, [_answer] * 20), clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)
    )


def refusing_gate() -> StaticGate:
    return StaticGate(
        PolicyMatrix(
            version=DEFAULT_MATRIX.version, allowed={level: frozenset() for level in Sensitivity}
        )
    )


def _config(**changes: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "extractor": "tests.test_deployment:make_extractor",
        "variables": "amber.conformance.fixtures:NOTE_VARIABLES",
        "sensitivity": Sensitivity.synthetic,
        "policy_version": DEFAULT_MATRIX.version,
        "dataset_manifest_hash": "synthetic-manifest",
        **changes,
    }
    return model_config(**fields)


def _row(case: Any = None, source: Any = None) -> dict[str, Any]:
    return {
        "case": (case or fx.note_case()).model_dump_json(),
        "source": (source or fx.SOURCE).model_dump_json(),
    }


@pytest.fixture
def model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    save_extractor_model(tmp_path / "model", _config(), pip_requirements=[])
    return mlflow.pyfunc.load_model(str(tmp_path / "model"))


def test_predictions_are_case_results_with_evidence(model: Any) -> None:
    run_id = fx.RUN_IDS[1]
    (prediction,) = model.predict([_row()], params={"run_id": run_id})
    result = load_case_result(
        json.loads(prediction), case=fx.note_case(), source=fx.SOURCE, variables=fx.NOTE_VARIABLES
    )
    assert result.run_id == run_id
    assert result.variable_outcomes[fx.NOTE_VARIABLES.name].outcome.status == "answered"
    assert result.evidence and result.claims


def test_a_dataframe_of_several_cases_gives_one_result_each(model: Any) -> None:
    import pandas as pd

    cases = [fx.note_case(f"deployment-case-{index}") for index in range(3)]
    predictions = model.predict(pd.DataFrame([_row(case) for case in cases]))
    assert [json.loads(item)["case_id"] for item in predictions] == [c.case_id for c in cases]


@pytest.mark.parametrize(
    ("row", "code"),
    [
        (lambda: _row(fx.note_case().model_copy(update={"sensitivity": "phi"})),
         "sensitivity_not_served"),
        (lambda: _row(fx.case()), "case_mismatch"),
        (lambda: {"case": "{}", "source": "{}"}, "invalid_input"),
    ],
    ids=["sensitivity", "other_task", "invalid"],
)  # fmt: skip
def test_inputs_it_cannot_serve_are_refused(model: Any, row: Any, code: str) -> None:
    with pytest.raises(DeploymentError, match=code):
        model.predict([row()])


def test_the_signature_refuses_rows_without_a_source(model: Any) -> None:
    from mlflow.exceptions import MlflowException

    with pytest.raises(MlflowException, match="source"):
        model.predict([{"case": _row()["case"]}])


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"variables": "amber.conformance.fixtures:VARIABLES"}, "missing_capability"),
        ({"gate": "tests.test_deployment:refusing_gate"}, "destination_not_permitted"),
        ({"extractor": "tests.test_deployment:nowhere"}, "unavailable_import"),
        ({"extractor": "amber.conformance.fixtures:NOTE_VARIABLES"}, "not_an_extractor"),
        ({"variables": "amber.conformance.fixtures"}, "invalid_configuration"),
    ],
)
def test_models_that_cannot_serve_their_variables_never_load(
    changes: dict[str, Any], code: str
) -> None:
    with pytest.raises(DeploymentError, match=code):
        _config(**changes)


def test_capabilities_follow_variable_levels() -> None:
    assert needed_capabilities(fx.NOTE_VARIABLES) == {"note_claims"}
    assert needed_capabilities(fx.VARIABLES) == {"mentions", "note_claims"}
