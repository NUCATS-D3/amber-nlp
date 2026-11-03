"""Provider requests, responses, failures, and the gated, budgeted ``call_provider``
(contract v0.1 sections 1.5, 6.2, 6.3; bead acceptance: policy refusal, capability mismatch,
parse failure, bounded retries)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from amber.conformance import fixtures as fx
from amber.interfaces import Provider
from amber.policy import (
    DEFAULT_MATRIX,
    DataPolicy,
    PolicyViolation,
    ProviderDestination,
    RunContext,
    StaticGate,
)
from amber.providers import (
    Message,
    ProviderFailure,
    ProviderRequest,
    ProviderResponse,
    UsageMeter,
    call_provider,
)
from amber.reference import ScriptedProvider
from amber.schemas import Budget, Sensitivity, Usage, Zone

NOW = datetime(2026, 1, 2, tzinfo=UTC)
GATE = StaticGate(DEFAULT_MATRIX)
PRODUCER = "model:synthetic-model@1"


def _destination(zone: Zone = Zone.local, *capabilities: str) -> ProviderDestination:
    return ProviderDestination(
        name="local:synthetic-model" if zone is Zone.local else "external-model",
        kind="model",
        zone=zone,
        accepts=frozenset({"source_text"}),
        model="synthetic-model",
        capabilities=frozenset(capabilities),  # type: ignore[arg-type]
    )


def _context(sensitivity: Sensitivity = Sensitivity.synthetic) -> RunContext:
    return RunContext(
        run_id=fx.RUN_IDS[0],
        policy_version=DEFAULT_MATRIX.version,
        data=DataPolicy(sensitivity=sensitivity),
        dataset=fx.DATASET,
        dataset_manifest_hash="synthetic-manifest",
    )


def _call(
    provider: ScriptedProvider,
    request: ProviderRequest,
    *,
    budget: Budget | None = None,
    sensitivity: Sensitivity = Sensitivity.synthetic,
) -> tuple[Any, UsageMeter]:
    meter = UsageMeter(budget)
    context = _context(sensitivity)
    call = call_provider(
        provider,
        request,
        gate=GATE,
        data=context.data,
        context=context,
        now=NOW,
        meter=meter,
        producer=PRODUCER,
        version="1",
    )
    return call, meter


PROMPT = ProviderRequest(prompt="Invented note text.", prompt_versions={"extract": "prompts:/x/1"})
SCHEMA = ProviderRequest(prompt="Invented note text.", json_schema={"type": "object"})


# Records -----------------------------------------------------------------------------------


def test_requests_take_messages_or_a_prompt_and_name_their_capabilities() -> None:
    with pytest.raises(ValidationError):
        ProviderRequest()
    with pytest.raises(ValidationError):
        ProviderRequest(prompt="x", messages=(Message(role="user", content="x"),))
    with pytest.raises(ValidationError):
        ProviderRequest(prompt="x", max_tokens=0)
    request = ProviderRequest(
        messages=(Message(role="user", content="x"),),
        json_schema={"type": "object"},
        tools=({"name": "lookup"},),
        logprobs=True,
        adapter="lora-1",
    )
    assert request.required_capabilities() == {
        "schema_constrained",
        "tools",
        "logprobs",
        "adapters",
    }
    assert PROMPT.required_capabilities() == frozenset()
    with pytest.raises(TypeError):
        request.json_schema["type"] = "array"  # type: ignore[index]


def test_responses_carry_content_and_failures_map_to_failure_kinds() -> None:
    with pytest.raises(ValidationError):
        ProviderResponse()
    with pytest.raises(ValidationError):
        ProviderResponse(text="x", input_tokens=-1)
    for kind in ("parse", "provider", "budget"):
        assert ProviderFailure(kind=kind, reason="unavailable").failure_kind == kind  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ProviderFailure(kind="policy", reason="unavailable")  # type: ignore[arg-type]


def test_the_scripted_provider_satisfies_the_protocol_and_records_requests() -> None:
    provider = ScriptedProvider(_destination(), [ProviderResponse(text="a")])
    assert isinstance(provider, Provider)
    assert provider.complete(PROMPT) == ProviderResponse(text="a")
    assert provider.complete(PROMPT) == ProviderFailure(kind="provider", reason="unavailable")
    assert provider.complete(SCHEMA).reason == "capability_missing"  # type: ignore[union-attr]
    assert provider.requests == [PROMPT, PROMPT, SCHEMA]
    with pytest.raises(TypeError):
        ScriptedProvider("model", [])  # type: ignore[arg-type]


# call_provider -----------------------------------------------------------------------------


def test_a_permitted_call_stamps_provenance_from_the_decision() -> None:
    provider = ScriptedProvider(
        _destination(),
        [ProviderResponse(text="answer", input_tokens=7, output_tokens=2, trace_ref="t-1")],
    )
    call, meter = _call(provider, PROMPT)

    assert call.outcome.text == "answer"
    assert call.decision.permitted and call.decision.payload == "source_text"
    provenance = call.provenance
    assert provenance is not None
    assert (provenance.producer, provenance.model, provenance.trace_id) == (
        PRODUCER,
        "synthetic-model",
        "t-1",
    )
    assert (provenance.run_id, provenance.destination, provenance.zone) == (
        fx.RUN_IDS[0],
        "local:synthetic-model",
        Zone.local,
    )
    assert provenance.prompt_versions == {"extract": "prompts:/x/1"}
    assert meter.usage == Usage(calls=1, tokens=9)


def test_a_refused_transfer_raises_and_sends_nothing() -> None:
    provider = ScriptedProvider(_destination(Zone.external), [ProviderResponse(text="x")])
    with pytest.raises(PolicyViolation) as failure:
        _call(provider, PROMPT, sensitivity=Sensitivity.phi)
    assert failure.value.decision.reason == "zone_not_allowed"
    assert provider.requests == []


def test_a_missing_capability_is_refused_without_sending() -> None:
    provider = ScriptedProvider(_destination(), [ProviderResponse(text="x")])
    call, meter = _call(provider, SCHEMA)
    assert call.outcome == ProviderFailure(kind="provider", reason="capability_missing")
    assert call.provenance is None and call.attempts == 0
    assert provider.requests == [] and meter.usage == Usage()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (ProviderResponse(text='{"present": true}'), {"present": True}),
        (ProviderResponse(parsed={"present": False}), {"present": False}),
    ],
)
def test_schema_responses_are_parsed(response: ProviderResponse, expected: dict[str, Any]) -> None:
    provider = ScriptedProvider(_destination(Zone.local, "schema_constrained"), [response])
    call, _ = _call(provider, SCHEMA)
    assert call.outcome.parsed == expected


@pytest.mark.parametrize("text", ["not json", "[1, 2]", ""])
def test_unparseable_schema_responses_are_parse_failures(text: str) -> None:
    provider = ScriptedProvider(
        _destination(Zone.local, "schema_constrained"),
        [ProviderResponse(text=text or " ", input_tokens=4, output_tokens=1)],
    )
    call, meter = _call(provider, SCHEMA)
    assert (call.outcome.kind, call.outcome.reason) == ("parse", "invalid_json")
    assert call.provenance is None
    assert meter.usage.tokens == 5


def test_retries_are_bounded_by_the_budget() -> None:
    flaky = ProviderFailure(kind="provider", reason="rate_limited", retryable=True)
    provider = ScriptedProvider(_destination(), [flaky] * 5 + [ProviderResponse(text="late")])
    call, meter = _call(provider, PROMPT, budget=Budget(max_retries=2))
    assert call.outcome == flaky and call.attempts == 3
    assert meter.usage == Usage(calls=3, retries=2)

    provider = ScriptedProvider(_destination(), [flaky, ProviderResponse(text="ok")])
    call, meter = _call(provider, PROMPT, budget=Budget(max_retries=2))
    assert call.outcome.text == "ok" and call.attempts == 2

    provider = ScriptedProvider(_destination(), [flaky, ProviderResponse(text="ok")])
    call, _ = _call(provider, PROMPT)
    assert call.outcome == flaky and call.attempts == 1

    final = ProviderFailure(kind="provider", reason="unavailable")
    provider = ScriptedProvider(_destination(), [final, ProviderResponse(text="ok")])
    call, _ = _call(provider, PROMPT, budget=Budget(max_retries=3))
    assert call.outcome == final and call.attempts == 1


def test_an_exhausted_budget_is_a_budget_failure() -> None:
    provider = ScriptedProvider(_destination(), [ProviderResponse(text="x")])
    meter = UsageMeter(Budget(max_calls=0))
    context = _context()
    call = call_provider(
        provider, PROMPT, gate=GATE, data=context.data, context=context, now=NOW,
        meter=meter, producer=PRODUCER, version="1",
    )  # fmt: skip
    assert (call.outcome.kind, call.outcome.reason) == ("budget", "budget_exhausted")
    assert provider.requests == []

    provider = ScriptedProvider(_destination(), [ProviderResponse(text="x", input_tokens=50)])
    call, meter = _call(provider, PROMPT, budget=Budget(max_tokens=10))
    assert (call.outcome.kind, call.outcome.reason) == ("budget", "budget_exhausted")
    assert meter.exceeded() and call.provenance is None


def test_provider_errors_become_failures() -> None:
    provider = ScriptedProvider(_destination(), [])

    def explode(request: ProviderRequest) -> ProviderResponse:
        raise RuntimeError("Invented transport error with note text")

    provider = ScriptedProvider(_destination(), [explode])
    call, _ = _call(provider, PROMPT)
    assert (call.outcome.kind, call.outcome.reason) == ("provider", "unexpected_error")
    assert "note text" not in call.outcome.model_dump_json()

    provider = ScriptedProvider(_destination(), [lambda request: "raw text"])  # type: ignore[list-item]
    call, _ = _call(provider, PROMPT)
    assert call.outcome.reason == "invalid_response"


def test_only_model_destinations_are_called() -> None:
    source_destination = ProviderDestination(
        name="local:warehouse",
        kind="structured_source",
        zone=Zone.local,
        accepts=frozenset({"labels_only"}),
    )
    with pytest.raises(TypeError):
        _call(ScriptedProvider(source_destination, []), PROMPT)
