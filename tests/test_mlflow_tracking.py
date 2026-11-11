"""Gated run, decision, and trace logging to MLflow (tracking extra)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

mlflow = pytest.importorskip("mlflow")

from amber.conformance import fixtures as fx  # noqa: E402
from amber.mlflow_ext.tracking import (  # noqa: E402
    DECISIONS_ARTIFACT,
    LOCAL_TRACKING,
    TrackingError,
    local_tracking,
    log_decisions,
    log_run,
    traced_provider_call,
)
from amber.policy import (  # noqa: E402
    DEFAULT_MATRIX,
    DataPolicy,
    Destination,
    PolicyViolation,
    ProviderDestination,
    StaticGate,
    check,
)
from amber.providers import (  # noqa: E402
    ProviderFailure,
    ProviderRequest,
    ProviderResponse,
    UsageMeter,
)
from amber.reference import ScriptedProvider  # noqa: E402
from amber.schemas import Sensitivity, Zone  # noqa: E402

NOW = datetime(2026, 1, 7, tzinfo=UTC)
GATE = StaticGate(DEFAULT_MATRIX)
CONTEXT = fx.run_context()
DATA = CONTEXT.data
PROMPT = "Invented note: is the invented finding present?"
MODEL = ProviderDestination(
    name="local:synthetic-model",
    kind="model",
    zone=Zone.local,
    accepts=frozenset({"source_text"}),
    model="synthetic-model",
    capabilities=frozenset({"schema_constrained"}),
)


@pytest.fixture
def local(tmp_path: Path) -> Any:
    previous = mlflow.get_tracking_uri()
    destination = local_tracking(tmp_path / ".mlflow")
    mlflow.set_experiment("amber-tracking-test")
    yield destination
    mlflow.set_tracking_uri(previous)


def _logged(run: Any) -> Any:
    return mlflow.tracking.MlflowClient().get_run(run.info.run_id).data


def test_local_tracking_is_a_local_sqlite_store(local: Destination, tmp_path: Path) -> None:
    assert (local.name, local.kind, local.zone) == (LOCAL_TRACKING, "tracking", Zone.local)
    assert local.accepts == frozenset({"metrics", "labels_only"})
    assert mlflow.get_tracking_uri().startswith("sqlite:///")
    assert (tmp_path / ".mlflow").is_dir()


def test_a_run_configuration_is_logged_as_labels(local: Destination) -> None:
    with mlflow.start_run() as run:
        decision = log_run(
            CONTEXT,
            providers=[MODEL],
            gate=GATE,
            destination=local,
            now=NOW,
            prompt_versions={"system": "prompts:/synthetic/3"},
            adapter_policy="copal-adapter@1",
            producer="model:synthetic-model@1",
        )
    assert decision.payload == "labels_only" and decision.permitted
    logged = _logged(run)
    assert logged.params["amber.run_id"] == CONTEXT.run_id
    assert logged.params["amber.dataset_manifest_hash"] == CONTEXT.dataset_manifest_hash
    assert logged.params["amber.sensitivity"] == "synthetic"
    assert logged.params["amber.prompt.system"] == "prompts:/synthetic/3"
    assert logged.params["amber.adapter_policy"] == "copal-adapter@1"
    assert logged.params["amber.provider.local:synthetic-model.zone"] == "local"
    assert logged.params["amber.provider.local:synthetic-model.model"] == "synthetic-model"
    assert logged.tags["amber.tracking_zone"] == "local"


def test_disallowed_destinations_log_nothing(local: Destination) -> None:
    metrics_only = local.model_copy(update={"accepts": frozenset({"metrics"})})
    with mlflow.start_run() as run, pytest.raises(PolicyViolation) as failure:
        log_run(CONTEXT, providers=[MODEL], gate=GATE, destination=metrics_only, now=NOW)
    assert failure.value.decision.payload == "labels_only"
    assert not _logged(run).params

    restricted = DataPolicy.model_validate(
        {
            "sensitivity": "synthetic",
            "restrictions": [
                {
                    "id": "dua-1",
                    "kind": "dua",
                    "forbids_kinds": ["tracking"],
                    "note": "Invented: no tracking servers.",
                }
            ],
        }
    )
    with mlflow.start_run() as run, pytest.raises(PolicyViolation):
        log_run(
            CONTEXT.model_copy(update={"data": restricted}),
            providers=[MODEL],
            gate=GATE,
            destination=local,
            now=NOW,
        )
    assert not _logged(run).params


def test_a_local_destination_refuses_a_remote_tracking_uri(local: Destination) -> None:
    mlflow.set_tracking_uri("http://tracking.invalid:5000")
    with pytest.raises(TrackingError, match="tracking_uri_not_local"):
        traced_provider_call(
            ScriptedProvider(MODEL, []),
            ProviderRequest(prompt=PROMPT),
            gate=GATE,
            data=DATA,
            context=CONTEXT,
            now=NOW,
            meter=UsageMeter(),
            producer="model:synthetic-model@1",
            version="1",
            tracking=local,
        )


def test_wrong_targets_and_missing_runs_are_refused(local: Destination) -> None:
    artifact = Destination(
        name="local:artifacts", kind="artifact_store", zone=Zone.local, accepts=frozenset()
    )
    with mlflow.start_run(), pytest.raises(TrackingError, match="not_a_tracking_destination"):
        log_run(CONTEXT, providers=[], gate=GATE, destination=artifact, now=NOW)
    with pytest.raises(TrackingError, match="no_active_run"):
        log_run(CONTEXT, providers=[], gate=GATE, destination=local, now=NOW)


def test_decisions_are_stored_as_an_artifact(local: Destination) -> None:
    refused = check(
        DEFAULT_MATRIX,
        DATA,
        Destination(name="public-api", kind="model", zone=Zone.external, accepts=frozenset()),
        "source_text",
        now=NOW,
    )
    with mlflow.start_run() as run:
        log_decisions([refused], gate=GATE, data=DATA, destination=local, now=NOW)
    stored = mlflow.artifacts.load_dict(f"{run.info.artifact_uri}/{DECISIONS_ARTIFACT}")
    assert stored["decisions"][0]["decision_id"] == refused.decision_id
    assert stored["decisions"][0]["permitted"] is False


def _call(local: Destination, outcomes: list[Any], **kwargs: Any) -> Any:
    return traced_provider_call(
        kwargs.pop("provider", None) or ScriptedProvider(MODEL, outcomes),
        ProviderRequest(prompt=PROMPT, prompt_versions={"user": "prompts:/synthetic/1"}),
        gate=GATE,
        data=DATA,
        context=CONTEXT,
        now=NOW,
        meter=UsageMeter(),
        producer="model:synthetic-model@1",
        version="1",
        tracking=kwargs.pop("tracking", local),
    )


def _last_span() -> Any:
    trace = mlflow.get_trace(mlflow.get_last_active_trace_id(), flush=True)
    assert trace is not None
    (span,) = trace.data.spans
    return span


def test_traces_redact_prompts_unless_source_text_is_permitted(local: Destination) -> None:
    answer = ProviderResponse(text="Invented answer.", input_tokens=7, output_tokens=2)
    call = _call(local, [answer])
    assert call.outcome == answer
    span = _last_span()
    assert span.attributes["amber.content"] == "redacted"
    assert span.attributes["amber.input_tokens"] == 7
    assert span.attributes["amber.zone"] == "local"
    assert PROMPT not in json.dumps(span.to_dict())
    assert "Invented answer." not in json.dumps(span.to_dict())

    with_prompts = local.model_copy(
        update={"accepts": frozenset({"metrics", "labels_only", "source_text"})}
    )
    _call(local, [answer], tracking=with_prompts)
    span = _last_span()
    assert span.attributes["amber.content"] == "included"
    assert span.inputs["prompt"] == PROMPT
    assert span.outputs["text"] == "Invented answer."


def test_failures_and_refusals_are_recorded_on_the_span(local: Destination) -> None:
    _call(local, [ProviderFailure(kind="provider", reason="timeout")])
    span = _last_span()
    assert span.attributes["amber.failure_kind"] == "provider"
    assert span.attributes["amber.failure_reason"] == "timeout"

    external = MODEL.model_copy(update={"name": "public-model", "zone": Zone.external})
    restricted = DataPolicy(sensitivity=Sensitivity.phi)
    with pytest.raises(PolicyViolation):
        traced_provider_call(
            ScriptedProvider(external, []),
            ProviderRequest(prompt=PROMPT),
            gate=GATE,
            data=restricted,
            context=CONTEXT.model_copy(update={"data": restricted}),
            now=NOW,
            meter=UsageMeter(),
            producer="model:synthetic-model@1",
            version="1",
            tracking=local,
        )
    span = _last_span()
    assert span.attributes["amber.refused"]
    assert PROMPT not in json.dumps(span.to_dict())


def test_a_tracking_destination_that_refuses_labels_makes_no_call(local: Destination) -> None:
    provider = ScriptedProvider(MODEL, [ProviderResponse(text="unused")])
    nothing = local.model_copy(update={"accepts": frozenset()})
    with pytest.raises(PolicyViolation):
        _call(local, [], provider=provider, tracking=nothing)
    assert provider.requests == []


# MLflow's reserved names are remote services, not directories (amber-fc8.5).
REMOTE_NAMES = ["databricks", "databricks://synthetic-profile", "databricks-uc", "uc", "Databricks"]


def _trace(local: Destination) -> None:
    traced_provider_call(
        ScriptedProvider(MODEL, []), ProviderRequest(prompt=PROMPT), gate=GATE, data=DATA,
        context=CONTEXT, now=NOW, meter=UsageMeter(), producer="model:synthetic-model@1",
        version="1", tracking=local,
    )  # fmt: skip


@pytest.mark.parametrize("uri", REMOTE_NAMES)
def test_a_local_destination_refuses_mlflow_s_remote_names(local: Destination, uri: str) -> None:
    store = mlflow.get_tracking_uri()
    with mlflow.start_run():
        mlflow.set_tracking_uri(uri)
        try:
            with pytest.raises(TrackingError, match="tracking_uri_not_local"):
                log_run(CONTEXT, providers=[], gate=GATE, destination=local, now=NOW)
            with pytest.raises(TrackingError, match="tracking_uri_not_local"):
                log_decisions([], gate=GATE, data=DATA, destination=local, now=NOW)
            with pytest.raises(TrackingError, match="tracking_uri_not_local"):
                _trace(local)
        finally:
            mlflow.set_tracking_uri(store)  # end the run where it started


@pytest.mark.parametrize("form", ["relative", "absolute", "file", "sqlite"])
def test_local_paths_and_stores_are_accepted(
    local: Destination, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")  # MLflow 3 keeps it opt-in
    uri = {
        "relative": "store", "absolute": str(tmp_path / "store"),
        "file": (tmp_path / "store").as_uri(), "sqlite": f"sqlite:///{tmp_path / 'other.db'}",
    }[form]  # fmt: skip
    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment("amber-local-forms")
    with mlflow.start_run() as run:
        log_run(CONTEXT, providers=[], gate=GATE, destination=local, now=NOW)
    assert _logged(run).params["amber.run_id"] == CONTEXT.run_id
