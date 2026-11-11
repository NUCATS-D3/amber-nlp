"""Gated run and trace logging to MLflow (OSS API only; ``tracking`` extra).

Everything sent to a tracking server is a transfer to a ``tracking`` destination:

- ``log_run`` records how a run was configured (providers, zones, sensitivity, dataset hash,
  prompt versions, adapter policy) as ``labels_only`` params and tags;
- ``log_decisions`` stores policy decisions, which are ``labels_only`` by construction, as a run
  artifact (contract v0.1 section 1.5, "Audit");
- ``traced_provider_call`` wraps ``call_provider`` in an MLflow span. Its attributes (model,
  zone, attempts, tokens, failure kind) are ``labels_only``; the prompt and response are
  ``source_text`` and are attached only when that payload is permitted to the tracking
  destination, otherwise the span records that they were redacted.

``local_tracking`` points MLflow at a SQLite store on this machine and returns the matching
``local`` destination, the default for development. A ``local`` destination is refused while
MLflow points anywhere else, so a remote server can never be logged to as if it were local.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import mlflow
except ModuleNotFoundError as exc:
    if exc.name is None or not exc.name.startswith("mlflow"):
        raise
    raise ImportError(
        "MLflow is optional. Install tracking dependencies with: uv sync --extra tracking"
    ) from exc

from amber.interfaces import PolicyGate, Provider
from amber.policy import (
    DataPolicy,
    Decision,
    Destination,
    PayloadKind,
    PolicyViolation,
    ProviderDestination,
    RunContext,
)
from amber.providers import (
    ProviderCall,
    ProviderFailure,
    ProviderRequest,
    UsageMeter,
    call_provider,
)
from amber.schemas import Zone

LOCAL_TRACKING = "local:mlflow"
DECISIONS_ARTIFACT = "amber/decisions.json"
_LOCAL_SCHEMES = frozenset({"file", "sqlite"})
# Scheme-less tracking URIs that MLflow resolves to a remote service rather than a directory
# (mlflow.utils.uri: Databricks and Unity Catalog), compared case-insensitively.
_REMOTE_NAMES = frozenset({"databricks", "databricks-uc", "uc"})


class TrackingError(ValueError):
    """A tracking target that cannot be used; renders no record contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def local_tracking(
    directory: Path = Path(".mlflow"),
    *,
    accepts: frozenset[PayloadKind] = frozenset({"metrics", "labels_only"}),
) -> Destination:
    """Use a SQLite tracking store under ``directory`` and return its ``local`` destination.

    ``accepts`` defaults to metrics and labels; add ``source_text`` only for a store that may
    hold prompts (the policy matrix still decides).
    """
    directory.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{(directory / 'mlflow.db').resolve()}")
    return Destination(name=LOCAL_TRACKING, kind="tracking", zone=Zone.local, accepts=accepts)


def _check_target(destination: Destination) -> None:
    if destination.kind != "tracking":
        raise TrackingError("not_a_tracking_destination")
    if destination.zone is Zone.local and not _is_local(mlflow.get_tracking_uri()):
        raise TrackingError("tracking_uri_not_local")


def _is_local(uri: str) -> bool:
    """A file or SQLite store, or a directory path; never a name MLflow reserves for a service."""
    scheme = urlparse(uri).scheme
    if scheme:
        return scheme in _LOCAL_SCHEMES
    return uri.strip().lower() not in _REMOTE_NAMES


def _require_run() -> None:
    if mlflow.active_run() is None:
        raise TrackingError("no_active_run")


def _permit(
    gate: PolicyGate,
    data: DataPolicy,
    destination: Destination,
    payload: PayloadKind,
    *,
    now: datetime,
    context: RunContext | None = None,
) -> Decision:
    _check_target(destination)
    return gate.require(data, destination, payload, now=now, context=context)


def log_run(
    context: RunContext,
    *,
    providers: Sequence[ProviderDestination],
    gate: PolicyGate,
    destination: Destination,
    now: datetime,
    prompt_versions: Mapping[str, str] | None = None,
    adapter_policy: str | None = None,
    producer: str | None = None,
) -> Decision:
    """Log a run's configuration to the active MLflow run as ``labels_only`` params and tags.

    Raises ``PolicyViolation`` (logging nothing) when ``labels_only`` may not go to
    ``destination``, and ``TrackingError`` for a wrong target or no active run.
    """
    _require_run()
    decision = _permit(gate, context.data, destination, "labels_only", now=now, context=context)
    params: dict[str, Any] = {
        "amber.run_id": context.run_id,
        "amber.dataset": context.dataset,
        "amber.dataset_manifest_hash": context.dataset_manifest_hash,
        "amber.policy_version": context.policy_version,
        "amber.sensitivity": context.data.sensitivity.value,
        "amber.restriction_ids": ",".join(r.id for r in context.data.restrictions),
        "amber.adapter_policy": adapter_policy or "",
        "amber.producer": producer or "",
    }
    for role, version in sorted((prompt_versions or {}).items()):
        params[f"amber.prompt.{role}"] = version
    tags = {
        "amber.tracking_destination": destination.name,
        "amber.tracking_zone": destination.zone.value,
    }
    for provider in sorted(providers, key=lambda item: item.name):
        base = f"amber.provider.{provider.name}"
        params[f"{base}.kind"] = provider.kind
        params[f"{base}.zone"] = provider.zone.value
        params[f"{base}.model"] = provider.model or ""
        params[f"{base}.capabilities"] = ",".join(sorted(provider.capabilities))
    mlflow.log_params(params)
    mlflow.set_tags(tags)
    return decision


def log_decisions(
    decisions: Sequence[Decision],
    *,
    gate: PolicyGate,
    data: DataPolicy,
    destination: Destination,
    now: datetime,
    artifact_file: str = DECISIONS_ARTIFACT,
) -> Decision:
    """Store policy decisions (permitted and refused) as a JSON run artifact."""
    _require_run()
    decision = _permit(gate, data, destination, "labels_only", now=now)
    mlflow.log_dict(
        {"decisions": [item.model_dump(mode="json") for item in decisions]}, artifact_file
    )
    return decision


def _call_attributes(call: ProviderCall) -> dict[str, Any]:
    outcome = call.outcome
    attributes: dict[str, Any] = {
        "amber.attempts": call.attempts,
        "amber.decision_id": call.decision.decision_id,
        "amber.input_tokens": outcome.input_tokens,
        "amber.output_tokens": outcome.output_tokens,
        "amber.latency_seconds": outcome.latency_seconds,
    }
    if isinstance(outcome, ProviderFailure):
        attributes["amber.failure_kind"] = outcome.kind
        attributes["amber.failure_reason"] = outcome.reason
    return attributes


def traced_provider_call(
    provider: Provider,
    request: ProviderRequest,
    *,
    gate: PolicyGate,
    data: DataPolicy,
    context: RunContext,
    now: datetime,
    meter: UsageMeter,
    producer: str,
    version: str,
    tracking: Destination,
) -> ProviderCall:
    """``call_provider`` inside an MLflow span on ``tracking``.

    ``labels_only`` must be permitted to ``tracking`` before the call is made (``PolicyViolation``
    otherwise). The prompt and response are attached only when ``source_text`` is permitted
    too; either way the span's ``amber.content`` attribute says ``included`` or ``redacted``,
    and ``amber.content_decision_id`` names the decision. A refused model call is recorded on
    the span and re-raised.
    """
    _permit(gate, data, tracking, "labels_only", now=now, context=context)
    content = gate.check(data, tracking, "source_text", now=now, context=context)
    destination = provider.destination
    with mlflow.start_span(name="amber.provider_call", span_type="LLM") as span:
        span.set_attributes(
            {
                "amber.run_id": context.run_id,
                "amber.destination": destination.name,
                "amber.zone": destination.zone.value,
                "amber.model": getattr(destination, "model", None) or "",
                "amber.prompt_versions": ",".join(
                    f"{role}={value}" for role, value in sorted(request.prompt_versions.items())
                ),
                "amber.content": "included" if content.permitted else "redacted",
                "amber.content_decision_id": content.decision_id,
            }
        )
        if content.permitted:
            span.set_inputs(request.model_dump(mode="json"))
        try:
            call = call_provider(
                provider,
                request,
                gate=gate,
                data=data,
                context=context,
                now=now,
                meter=meter,
                producer=producer,
                version=version,
            )
        except PolicyViolation as violation:
            span.set_attributes(
                {
                    "amber.refused": violation.decision.reason,
                    "amber.decision_id": violation.decision.decision_id,
                }
            )
            raise
        span.set_attributes(_call_attributes(call))
        if content.permitted and not isinstance(call.outcome, ProviderFailure):
            span.set_outputs(call.outcome.model_dump(mode="json"))
    return call


__all__ = [
    "DECISIONS_ARTIFACT",
    "LOCAL_TRACKING",
    "TrackingError",
    "local_tracking",
    "log_decisions",
    "log_run",
    "traced_provider_call",
]
