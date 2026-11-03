"""Model and structured-source calls (contract v0.1 sections 1.5, 6.2, 6.3).

A ``Provider`` never sees a ``Source``: the extractor decides what text to send. ``call_provider``
is the enforcement point every call goes through: it requires a permitting policy decision for
``source_text`` to the provider's destination, refuses requests needing capabilities the
destination does not declare without sending anything, keeps the run within its ``Budget``,
retries only retryable failures and only as often as the budget allows, and stamps the
``Provenance`` that nodes produced from the response must carry.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from amber.policy import (
    DataPolicy,
    Decision,
    ProviderCapability,
    ProviderDestination,
    RunContext,
    stamp_provenance,
)
from amber.schemas._immutable import freeze_json
from amber.schemas.cases import Budget, FailureKind, Usage
from amber.schemas.provenance import Provenance

if TYPE_CHECKING:
    from amber.interfaces import PolicyGate, Provider

_FROZEN = ConfigDict(extra="forbid", frozen=True)
_Text = Annotated[str, Field(strict=True, min_length=1)]
_Count = Annotated[int, Field(strict=True, ge=0)]
_Seconds = Annotated[float, Field(allow_inf_nan=False, ge=0)]

FailureReason: TypeAlias = Literal[
    "capability_missing",
    "unavailable",
    "timeout",
    "rate_limited",
    "invalid_response",
    "invalid_json",
    "token_limit",
    "budget_exhausted",
    "unexpected_error",
]


class Message(BaseModel):
    model_config = _FROZEN

    role: Literal["system", "user", "assistant", "tool"]
    content: Annotated[str, Field(strict=True)]


class ProviderRequest(BaseModel):
    """What the extractor sends; ``source_text`` and never persisted by amber."""

    model_config = _FROZEN

    messages: tuple[Message, ...] = ()
    prompt: Annotated[str, Field(strict=True)] | None = None
    json_schema: Mapping[str, Any] | None = None
    tools: tuple[Mapping[str, Any], ...] = ()
    logprobs: bool = Field(default=False, strict=True)
    adapter: _Text | None = None
    max_tokens: Annotated[int, Field(strict=True, gt=0)] | None = None
    timeout_seconds: Annotated[float, Field(allow_inf_nan=False, gt=0)] | None = None
    prompt_versions: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("json_schema", "prompt_versions")
    @classmethod
    def freeze_mapping(cls, value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        return None if value is None else freeze_json(dict(value))  # type: ignore[no-any-return]

    @field_validator("tools")
    @classmethod
    def freeze_tools(cls, value: tuple[Mapping[str, Any], ...]) -> tuple[Mapping[str, Any], ...]:
        return tuple(freeze_json(dict(item)) for item in value)

    @model_validator(mode="after")
    def require_one_input(self) -> ProviderRequest:
        if bool(self.messages) == (self.prompt is not None):
            raise ValueError("a request has either messages or a prompt")
        return self

    def required_capabilities(self) -> frozenset[ProviderCapability]:
        needed: set[ProviderCapability] = set()
        if self.json_schema is not None:
            needed.add("schema_constrained")
        if self.tools:
            needed.add("tools")
        if self.logprobs:
            needed.add("logprobs")
        if self.adapter is not None:
            needed.add("adapters")
        return frozenset(needed)


class ProviderResponse(BaseModel):
    """A completed call: text or parsed JSON, with what it cost."""

    model_config = _FROZEN

    text: Annotated[str, Field(strict=True)] | None = None
    parsed: Mapping[str, Any] | None = None
    input_tokens: _Count = 0
    output_tokens: _Count = 0
    latency_seconds: _Seconds = 0.0
    cost_estimate: _Seconds | None = None
    trace_ref: _Text | None = None

    @field_validator("parsed")
    @classmethod
    def freeze_parsed(cls, value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        return None if value is None else freeze_json(dict(value))  # type: ignore[no-any-return]

    @model_validator(mode="after")
    def require_content(self) -> ProviderResponse:
        if self.text is None and self.parsed is None:
            raise ValueError("a response carries text or parsed JSON")
        return self


class ProviderFailure(BaseModel):
    """A call that produced no usable response; ``kind`` is the result's ``failure_kind``."""

    model_config = _FROZEN

    kind: Literal["parse", "provider", "budget"]
    reason: FailureReason
    retryable: bool = Field(default=False, strict=True)
    input_tokens: _Count = 0
    output_tokens: _Count = 0
    latency_seconds: _Seconds = 0.0

    @property
    def failure_kind(self) -> FailureKind:
        return self.kind


class UsageMeter:
    """Accumulates a run's usage across calls and checks it against the run's budget."""

    def __init__(self, budget: Budget | None = None) -> None:
        self.budget = budget
        self._usage = Usage()

    @property
    def usage(self) -> Usage:
        return self._usage

    def exceeded(self) -> bool:
        return self.budget is not None and self._usage.exceeds(self.budget)

    def can_call(self) -> bool:
        limit = None if self.budget is None else self.budget.max_calls
        return limit is None or self._usage.calls < limit

    def can_retry(self) -> bool:
        limit = None if self.budget is None else self.budget.max_retries
        return limit is not None and self._usage.retries < limit

    def record(self, outcome: ProviderResponse | ProviderFailure, *, retry: bool) -> None:
        self._usage = Usage(
            calls=self._usage.calls + 1,
            tokens=self._usage.tokens + outcome.input_tokens + outcome.output_tokens,
            retries=self._usage.retries + (1 if retry else 0),
            seconds=self._usage.seconds + outcome.latency_seconds,
        )


@dataclass(frozen=True)
class ProviderCall:
    """The outcome of ``call_provider``: the response or failure, the permitting decision,
    the provenance for nodes built from a response, and how many attempts were made."""

    outcome: ProviderResponse | ProviderFailure
    decision: Decision
    provenance: Provenance | None
    attempts: int


def _parse(
    request: ProviderRequest, response: ProviderResponse
) -> ProviderResponse | ProviderFailure:
    if request.json_schema is None or response.parsed is not None:
        return response
    try:
        parsed = json.loads(response.text or "")
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        return ProviderFailure(
            kind="parse",
            reason="invalid_json",
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_seconds=response.latency_seconds,
        )
    return response.model_copy(update={"parsed": freeze_json(parsed)})


def _complete(provider: Provider, request: ProviderRequest) -> ProviderResponse | ProviderFailure:
    started = time.monotonic()
    try:
        outcome = provider.complete(request)
    except Exception:
        return ProviderFailure(
            kind="provider",
            reason="unexpected_error",
            latency_seconds=time.monotonic() - started,
        )
    if not isinstance(outcome, ProviderResponse | ProviderFailure):
        return ProviderFailure(kind="provider", reason="invalid_response")
    return outcome


def call_provider(
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
) -> ProviderCall:
    """Make one gated, budgeted, capability-checked call (with bounded retries).

    Raises ``PolicyViolation`` when the transfer is refused; the caller records
    ``failed(policy)``. Every other failure is returned as a ``ProviderFailure``.
    """
    destination = provider.destination
    if not isinstance(destination, ProviderDestination) or destination.kind != "model":
        raise TypeError("call_provider needs a provider with a model ProviderDestination")
    decision = gate.require(data, destination, "source_text", now=now, context=context)
    attempts = 0

    def finish(outcome: ProviderResponse | ProviderFailure) -> ProviderCall:
        provenance = None
        if isinstance(outcome, ProviderResponse):
            provenance = stamp_provenance(
                decision,
                data,
                producer=producer,
                created_at=now,
                version=version,
                model=destination.model,
                prompt_versions=request.prompt_versions,
                trace_id=outcome.trace_ref,
            )
        return ProviderCall(outcome, decision, provenance, attempts)

    if request.required_capabilities() - destination.capabilities:
        return finish(ProviderFailure(kind="provider", reason="capability_missing"))
    while True:
        if not meter.can_call():
            return finish(ProviderFailure(kind="budget", reason="budget_exhausted"))
        outcome = _complete(provider, request)
        meter.record(outcome, retry=attempts > 0)
        attempts += 1
        if isinstance(outcome, ProviderResponse):
            outcome = _parse(request, outcome)
        if meter.exceeded():
            return finish(ProviderFailure(kind="budget", reason="budget_exhausted"))
        if isinstance(outcome, ProviderFailure) and outcome.retryable and meter.can_retry():
            continue
        return finish(outcome)
