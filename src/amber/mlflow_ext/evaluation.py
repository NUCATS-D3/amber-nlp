"""MLflow evaluation plugins (OSS API only): a structural/source-validity scorer and gated
logging of evaluation and score reports. Requires the ``tracking`` extra.

Logging to a tracking server is a transfer: ``log_evaluation`` checks ``metrics`` (values) and
``labels_only`` (gate statuses, tier mix) against the destination before writing anything.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

try:
    import mlflow
    from mlflow.entities import Feedback
    from mlflow.genai.scorers import scorer
except ModuleNotFoundError as exc:
    if exc.name is None or not exc.name.startswith("mlflow"):
        raise
    raise ImportError(
        "MLflow is optional. Install tracking dependencies with: uv sync --extra tracking"
    ) from exc

from amber.cases import CaseValidationError, load_case_result
from amber.evaluation import EvaluationReport
from amber.graph_errors import GraphValidationError
from amber.interfaces import PolicyGate, SourceResolver
from amber.policy import DataPolicy, Decision, Destination
from amber.schemas import Case, VariableSet
from amber.scoring import ScoreReport

_UNSAFE = re.compile(r"[^A-Za-z0-9_\-./ ]")


def _key(*parts: str) -> str:
    return _UNSAFE.sub("_", ".".join(parts))


def validity_scorer(
    *,
    cases: Mapping[str, Case],
    resolver: SourceResolver,
    variables: VariableSet,
    name: str = "amber_structural_validity",
) -> Any:
    """An MLflow scorer: does ``outputs`` (a ``CaseResult`` as JSON) load through amber's
    boundary against its authoritative case, source, and variable set?

    The feedback value is a boolean and the rationale a source-free code (``valid``,
    ``unknown_case``, ``source_unavailable``, or the validation code).
    """

    @scorer(name=name)  # type: ignore[misc]
    def amber_validity(outputs: Any) -> Feedback:
        try:
            payload = json.loads(outputs) if isinstance(outputs, str) else dict(outputs)
        except (TypeError, ValueError):
            return Feedback(value=False, rationale="invalid_schema")
        case_id = payload.get("case_id") if isinstance(payload, dict) else None
        case = cases.get(case_id) if isinstance(case_id, str) else None
        if case is None:
            return Feedback(value=False, rationale="unknown_case")
        source = resolver.resolve(case.source_ids[0])
        if source is None:
            return Feedback(value=False, rationale="source_unavailable")
        try:
            load_case_result(payload, case=case, source=source, variables=variables)
        except (CaseValidationError, GraphValidationError) as error:
            return Feedback(value=False, rationale=error.code)
        return Feedback(value=True, rationale="valid")

    return amber_validity


def _evaluation_payload(report: EvaluationReport) -> tuple[dict[str, float], dict[str, str]]:
    metrics: dict[str, float] = {}
    for name, measure in report.measures.items():
        if measure.value is not None and not (measure.evidence and report.label_only):
            metrics[_key("amber", name)] = measure.value
        metrics[_key("amber", name, "numerator")] = float(measure.numerator)
        if measure.denominator is not None:
            metrics[_key("amber", name, "denominator")] = float(measure.denominator)
    for name, (low, high) in report.wilson.items():
        metrics[_key("amber", name, "wilson_low")] = low
        metrics[_key("amber", name, "wilson_high")] = high
    for name, bootstrap in report.bootstrap.items():
        if bootstrap.low is not None and bootstrap.high is not None:
            metrics[_key("amber", name, "bootstrap_low")] = bootstrap.low
            metrics[_key("amber", name, "bootstrap_high")] = bootstrap.high
    tags = {
        _key("amber", "overall"): report.overall,
        _key("amber", "provisional"): str(report.provisional).lower(),
        _key("amber", "incomplete"): ",".join(report.incomplete),
    }
    for gate in report.gates:
        tags[_key("amber", "gate", gate.name)] = gate.status
    for tier, count in report.tier_mix.items():
        tags[_key("amber", "tier", tier.value)] = str(count)
    return metrics, tags


def _score_payload(report: ScoreReport) -> tuple[dict[str, float], dict[str, str]]:
    metrics: dict[str, float] = {}
    for metric in report.metrics:
        base = ("amber", report.scorer, metric.variable or "all", metric.name)
        if metric.value is not None:
            metrics[_key(*base)] = metric.value
        metrics[_key(*base, "numerator")] = float(metric.numerator)
        metrics[_key(*base, "denominator")] = float(metric.denominator)
    tags = {
        _key("amber", report.scorer, "provisional"): str(report.provisional).lower(),
        _key("amber", report.scorer, "inconclusive"): ",".join(report.inconclusive),
        _key("amber", report.scorer, "examples"): str(len(report.example_ids)),
    }
    for tier, count in report.tier_mix.items():
        tags[_key("amber", report.scorer, "tier", tier.value)] = str(count)
    return metrics, tags


def log_evaluation(
    report: EvaluationReport | ScoreReport,
    *,
    gate: PolicyGate,
    data: DataPolicy,
    destination: Destination,
    now: datetime,
) -> tuple[Decision, Decision]:
    """Log a report's metrics and labels to the active MLflow run after both are permitted.

    Raises ``PolicyViolation`` (writing nothing) when either transfer is refused, and
    ``ValueError`` when no run is active.
    """
    if mlflow.active_run() is None:
        raise ValueError("log_evaluation needs an active MLflow run")
    metrics_decision = gate.require(data, destination, "metrics", now=now)
    labels_decision = gate.require(data, destination, "labels_only", now=now)
    if isinstance(report, EvaluationReport):
        metrics, tags = _evaluation_payload(report)
    else:
        metrics, tags = _score_payload(report)
    mlflow.log_metrics(metrics)
    mlflow.set_tags(tags)
    return metrics_decision, labels_decision
