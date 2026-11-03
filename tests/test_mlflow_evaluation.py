"""MLflow evaluation plugins: the validity scorer and gated report logging (tracking extra)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

mlflow = pytest.importorskip("mlflow")

from amber.conformance import fixtures as fx  # noqa: E402
from amber.evaluation import GateSpec, Gold, Prediction, evaluate_binary  # noqa: E402
from amber.mlflow_ext.evaluation import log_evaluation, validity_scorer  # noqa: E402
from amber.policy import (  # noqa: E402
    DEFAULT_MATRIX,
    DataPolicy,
    Destination,
    PolicyViolation,
    StaticGate,
)
from amber.reference import InMemoryResolver, LabelCorrectScorer  # noqa: E402
from amber.schemas import ReferenceTier, Sensitivity, Zone  # noqa: E402

NOW = datetime(2026, 1, 2, tzinfo=UTC)
DATA = DataPolicy(sensitivity=Sensitivity.synthetic)
GATE = StaticGate(DEFAULT_MATRIX)
TRACKING = Destination(
    name="institution-mlflow",
    kind="tracking",
    zone=Zone.institution,
    accepts=frozenset({"metrics", "labels_only"}),
)


def _scorer() -> Any:
    case = fx.case()
    return validity_scorer(
        cases={case.case_id: case}, resolver=InMemoryResolver([fx.SOURCE]), variables=fx.VARIABLES
    )


def test_the_validity_scorer_revalidates_results() -> None:
    scorer = _scorer()
    payload = json.loads(fx.result(fx.RUN_IDS[0]).model_dump_json())
    valid = scorer(outputs=payload)
    assert (valid.value, valid.rationale) == (True, "valid")
    assert scorer(outputs=json.dumps(payload)).value is True

    tampered = {**payload, "run_id": fx.RUN_IDS[1]}
    assert (scorer(outputs=tampered).value, scorer(outputs=tampered).rationale) == (
        False,
        "run_mismatch",
    )
    assert scorer(outputs={**payload, "case_id": "unknown"}).rationale == "unknown_case"
    assert scorer(outputs="not json").rationale == "invalid_schema"

    case = fx.case()
    unresolved = validity_scorer(
        cases={case.case_id: case}, resolver=InMemoryResolver(), variables=fx.VARIABLES
    )
    assert unresolved(outputs=payload).rationale == "source_unavailable"


@pytest.fixture
def tracking(tmp_path: Path) -> Any:
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    mlflow.set_experiment("amber-evaluation-test")
    yield mlflow.tracking.MlflowClient()
    mlflow.set_tracking_uri(previous)


def _report() -> Any:
    gold = [
        Gold("n1", "answered", True, ReferenceTier.adjudicated, True),
        Gold("n2", "answered", False, ReferenceTier.single_review, True),
    ]
    predicted = [
        Prediction("n1", "answered", True),
        Prediction("n2", "insufficient_evidence", None),
    ]
    gates = (
        GateSpec(name="precision", measure="precision", op="ge", threshold=0.95),
        GateSpec(name="recall", measure="recall", op="ge", threshold=0.85),
        GateSpec(name="omissions", measure="omission_rate", op="le", threshold=0.15),
    )
    return evaluate_binary(["n1", "n2"], gold, predicted, gates, resamples=200)


def test_evaluation_reports_are_logged_after_both_transfers_are_permitted(tracking: Any) -> None:
    with mlflow.start_run() as run:
        decisions = log_evaluation(_report(), gate=GATE, data=DATA, destination=TRACKING, now=NOW)
    assert [decision.payload for decision in decisions] == ["metrics", "labels_only"]
    logged = tracking.get_run(run.info.run_id).data
    assert logged.metrics["amber.precision"] == 1.0
    assert logged.metrics["amber.omission_rate.numerator"] == 1.0
    assert logged.metrics["amber.omission_rate.denominator"] == 2.0
    assert "amber.precision.wilson_low" in logged.metrics
    assert logged.tags["amber.gate.precision"] == "inconclusive"
    assert logged.tags["amber.gate.omissions"] == "unmet"
    assert logged.tags["amber.overall"] == "unmet"
    assert logged.tags["amber.provisional"] == "true"
    assert logged.tags["amber.tier.single_review"] == "1"


def test_score_reports_are_logged_per_variable(tracking: Any) -> None:
    examples = [fx.note_example("adjudicated", present=True, producer=fx.TEACHER)]
    predicted = [fx.note_result(fx.RUN_IDS[0], present=True, producer="model:candidate@1")]
    report = LabelCorrectScorer().score(predicted, examples, variables=fx.NOTE_VARIABLES)
    with mlflow.start_run() as run:
        log_evaluation(report, gate=GATE, data=DATA, destination=TRACKING, now=NOW)
    logged = tracking.get_run(run.info.run_id).data
    assert logged.metrics["amber.amber-label-correct.all.label_accuracy"] == 1.0
    assert (
        logged.metrics[f"amber.amber-label-correct.{fx.NOTE_VARIABLES.name}.label_accuracy"] == 1.0
    )
    assert logged.tags["amber.amber-label-correct.provisional"] == "false"
    assert logged.tags["amber.amber-label-correct.examples"] == "1"


def test_a_refused_transfer_logs_nothing(tracking: Any) -> None:
    metrics_only = TRACKING.model_copy(update={"accepts": frozenset({"metrics"})})
    with mlflow.start_run() as run, pytest.raises(PolicyViolation) as failure:
        log_evaluation(_report(), gate=GATE, data=DATA, destination=metrics_only, now=NOW)
    assert failure.value.decision.payload == "labels_only"
    logged = tracking.get_run(run.info.run_id).data
    assert not any(key.startswith("amber.") for key in logged.metrics)
    assert not any(key.startswith("amber.") and key != "amber.version" for key in logged.tags)


def test_logging_needs_an_active_run(tracking: Any) -> None:
    with pytest.raises(ValueError, match="active MLflow run"):
        log_evaluation(_report(), gate=GATE, data=DATA, destination=TRACKING, now=NOW)
