"""The reference extractor end to end with a scripted provider, for every outcome kind
(contract v0.1 sections 6.2 and 6.6; bead acceptance for amber-bc9.4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import Field

from amber.cases import load_case_result
from amber.conformance import fixtures as fx
from amber.extraction import failed_result, local_provenance
from amber.interfaces import Extractor
from amber.policy import (
    DEFAULT_MATRIX,
    DataPolicy,
    PolicyMatrix,
    ProviderDestination,
    RunContext,
    StaticGate,
)
from amber.providers import ProviderFailure, ProviderResponse
from amber.reference import (
    DeterministicExtractor,
    InMemoryResolver,
    InMemoryStore,
    ScriptedProvider,
)
from amber.schemas import (
    AnswerModel,
    Budget,
    CaseResult,
    Sensitivity,
    Usage,
    Variable,
    VariableSet,
    Zone,
)

CLOCK = datetime(2026, 1, 1, tzinfo=UTC)
GATE = StaticGate(DEFAULT_MATRIX)
QUOTE = "mass \U0001f642 seen in the liver."
ANSWER = {"status": "answered", "value": {"present": True}, "quote": QUOTE, "reason": "Stated."}


def _destination(*capabilities: str) -> ProviderDestination:
    return ProviderDestination(
        name="local:synthetic-model",
        kind="model",
        zone=Zone.local,
        accepts=frozenset({"source_text"}),
        model="synthetic-model",
        capabilities=frozenset(capabilities or ("schema_constrained",)),  # type: ignore[arg-type]
    )


def _run(
    *script: Any,
    variables: VariableSet = fx.NOTE_VARIABLES,
    budget: Budget | None = None,
    gate: Any = GATE,
    destination: ProviderDestination | None = None,
    context: RunContext | None = None,
) -> tuple[CaseResult, ScriptedProvider]:
    provider = ScriptedProvider(destination or _destination(), list(script))
    extractor = DeterministicExtractor(provider, clock=lambda: CLOCK)
    case = fx.case("extraction-case-1", task=variables.name)
    result = extractor.run(
        case, fx.SOURCE, variables, gate=gate, budget=budget, context=context or fx.run_context()
    )
    reloaded = load_case_result(
        json.loads(result.model_dump_json()), case=case, source=fx.SOURCE, variables=variables
    )
    assert reloaded == result
    return result, provider


def _statuses(result: CaseResult) -> dict[str, tuple[str, str | None]]:
    return {
        name: (item.outcome.status, item.outcome.failure_kind)
        for name, item in result.variable_outcomes.items()
    }


def test_the_reference_extractor_satisfies_the_protocol() -> None:
    extractor = DeterministicExtractor(ScriptedProvider(_destination(), []))
    assert isinstance(extractor, Extractor)
    assert extractor.destinations == frozenset({_destination()})
    assert extractor.capabilities == {"note_claims", "batch"}


def test_an_answer_is_grounded_committed_and_stamped_from_the_run() -> None:
    result, provider = _run(
        ProviderResponse(parsed=ANSWER, input_tokens=20, output_tokens=6, trace_ref="trace-1"),
        budget=Budget(max_calls=2),
    )
    assert _statuses(result) == {"conformance_note": ("answered", None)}
    (claim,) = result.claims
    assert claim.value == {"present": True}
    inclusion = next(item for item in result.evidence if item.kind == "inclusion")
    assert inclusion.quote == QUOTE and fx.SOURCE.text[inclusion.start : inclusion.end] == QUOTE
    assert (claim.provenance.run_id, claim.provenance.destination) == (
        fx.RUN_IDS[0],
        "local:synthetic-model",
    )
    assert claim.provenance.trace_id == "trace-1"
    assert (result.tool_calls, result.grounding_failures) == (2, 0)
    assert (result.budget, result.usage) == (Budget(max_calls=2), Usage(calls=1, tokens=26))
    request = provider.requests[0]
    assert request.messages[1].content == fx.SOURCE.text
    assert request.json_schema is not None


@pytest.mark.parametrize("status", ["not_mentioned", "insufficient_evidence"])
def test_non_answers_are_recorded_without_claims(status: str) -> None:
    result, _ = _run(ProviderResponse(parsed={"status": status, "reason": "Invented."}))
    assert _statuses(result) == {"conformance_note": (status, None)}
    assert result.claims == []
    if status == "not_mentioned":
        assert result.outcome is not None
        assert result.outcome.reviewed_source_ids == [fx.SOURCE.source_id]


def test_an_ungrounded_quote_is_insufficient_evidence_and_counted() -> None:
    result, _ = _run(ProviderResponse(parsed={**ANSWER, "quote": "Invented, absent text."}))
    assert _statuses(result) == {"conformance_note": ("insufficient_evidence", None)}
    assert result.grounding_failures == 1 and result.claims == []


@pytest.mark.parametrize(
    "parsed",
    [
        {**ANSWER, "value": {"present": "yes"}},
        {**ANSWER, "value": None},
        {**ANSWER, "quote": None},
        {"status": "maybe"},
        {},
    ],
)
def test_invalid_answers_are_parse_failures(parsed: dict[str, Any]) -> None:
    result, _ = _run(ProviderResponse(parsed=parsed))
    assert _statuses(result) == {"conformance_note": ("failed", "parse")}


@pytest.mark.parametrize(
    ("outcome", "kind"),
    [
        (ProviderFailure(kind="provider", reason="unavailable"), "provider"),
        (ProviderResponse(text="not json"), "parse"),
        (ProviderFailure(kind="parse", reason="invalid_json"), "parse"),
    ],
)
def test_provider_and_parse_failures_fail_the_variable(outcome: Any, kind: str) -> None:
    result, _ = _run(outcome)
    assert _statuses(result) == {"conformance_note": ("failed", kind)}
    assert result.outcome is not None
    assert result.outcome.provenance.run_id == fx.RUN_IDS[0]
    assert result.outcome.provenance.destination == "local:amber-deterministic"


def test_a_missing_capability_fails_without_sending() -> None:
    result, provider = _run(ProviderResponse(parsed=ANSWER), destination=_destination("tools"))
    assert _statuses(result) == {"conformance_note": ("failed", "provider")}
    assert provider.requests == []


def test_a_policy_refusal_fails_the_whole_result_and_sends_nothing() -> None:
    closed = StaticGate(
        PolicyMatrix(
            version=DEFAULT_MATRIX.version, allowed={level: frozenset() for level in Sensitivity}
        )
    )
    result, provider = _run(ProviderResponse(parsed=ANSWER), gate=closed)
    assert _statuses(result) == {"conformance_note": ("failed", "policy")}
    assert provider.requests == []


def test_budgets_are_enforced_and_recorded() -> None:
    result, provider = _run(ProviderResponse(parsed=ANSWER), budget=Budget(max_calls=0))
    assert _statuses(result) == {"conformance_note": ("failed", "budget")}
    assert provider.requests == [] and result.usage == Usage()

    result, _ = _run(
        ProviderResponse(parsed=ANSWER, input_tokens=90, output_tokens=20),
        budget=Budget(max_tokens=50),
    )
    assert _statuses(result) == {"conformance_note": ("failed", "budget")}
    assert result.usage is not None and result.usage.tokens == 110
    assert result.claims == []


def test_retries_follow_the_budget() -> None:
    flaky = ProviderFailure(kind="provider", reason="timeout", retryable=True)
    result, provider = _run(flaky, ProviderResponse(parsed=ANSWER), budget=Budget(max_retries=1))
    assert _statuses(result) == {"conformance_note": ("answered", None)}
    assert len(provider.requests) == 2
    assert result.usage == Usage(calls=2, retries=1)


def test_a_context_that_does_not_match_the_case_is_a_runner_error() -> None:
    context = RunContext(
        run_id=fx.RUN_IDS[0],
        policy_version=DEFAULT_MATRIX.version,
        data=DataPolicy(sensitivity=Sensitivity.phi),
        dataset=fx.DATASET,
        dataset_manifest_hash="x",
    )
    provider = ScriptedProvider(_destination(), [ProviderResponse(parsed=ANSWER)])
    extractor = DeterministicExtractor(provider, clock=lambda: CLOCK)
    case = fx.case("extraction-case-1", task=fx.NOTE_VARIABLES.name)
    with pytest.raises(ValueError, match="sensitivity"):
        extractor.run(case, fx.SOURCE, fx.NOTE_VARIABLES, gate=GATE, budget=None, context=context)


def test_unexpected_errors_are_internal_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    import amber.reference.extractor as module

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Invented failure mentioning note text")

    monkeypatch.setattr(module, "quote", broken)
    result, _ = _run(ProviderResponse(parsed=ANSWER))
    assert _statuses(result) == {"conformance_note": ("failed", "internal")}
    assert "note text" not in result.model_dump_json()


class _Grade(AnswerModel):
    grade: int = Field(strict=True)


def test_each_note_variable_is_answered_independently() -> None:
    variables = VariableSet.create(
        name="extraction-set",
        version="1",
        variables=(
            Variable(
                name="present",
                level="note",
                answer_model=fx.FindingAnswer,
                instructions="A.",
                final=True,
            ),
            Variable(
                name="grade", level="note", answer_model=_Grade, instructions="B.", final=True
            ),
            Variable(
                name="derived",
                level="note",
                answer_model=fx.FindingAnswer,
                instructions="C.",
                inputs=frozenset({"present"}),
                final=True,
            ),
        ),
    )
    result, provider = _run(
        ProviderResponse(parsed={**ANSWER, "value": {"grade": 2}}),
        ProviderResponse(parsed=ANSWER),
        variables=variables,
    )
    assert _statuses(result) == {
        "derived": ("insufficient_evidence", None),
        "grade": ("answered", None),
        "present": ("answered", None),
    }
    assert result.outcome is None
    assert [request.messages[0].content for request in provider.requests] == ["B.", "A."]


def test_batches_keep_input_order_and_results_persist() -> None:
    provider = ScriptedProvider(_destination(), [ProviderResponse(parsed=ANSWER)] * 3)
    extractor = DeterministicExtractor(provider, clock=lambda: CLOCK)
    cases = [
        fx.case(f"extraction-case-{index}", task=fx.NOTE_VARIABLES.name) for index in (3, 1, 2)
    ]
    results = list(
        extractor.run_many(
            [(case, fx.SOURCE) for case in cases],
            fx.NOTE_VARIABLES,
            gate=GATE,
            budget=None,
            context=fx.run_context(),
        )
    )
    assert [result.case_id for result in results] == [case.case_id for case in cases]

    store = InMemoryStore()
    for case, result in zip(cases, results, strict=True):
        store.put_case(
            case.model_copy(update={"dataset": f"set-{case.case_id}"}), variables=fx.NOTE_VARIABLES
        )
        store.put_result(result)
        loaded = store.get_result(
            case.case_id, result.run_id, resolver=InMemoryResolver([fx.SOURCE])
        )
        assert loaded == result


def test_failed_results_carry_every_final_variable() -> None:
    provenance = local_provenance(
        fx.run_context(), producer="tool:x@1", tool="x", version="1", created_at=CLOCK
    )
    result = failed_result(
        fx.case(),
        fx.SOURCE,
        fx.VARIABLES,
        context=fx.run_context(),
        failure_kind="provider",
        reason="Synthetic.",
        provenance=provenance,
    )
    assert _statuses(result) == {
        "finding": ("failed", "provider"),
        "summary": ("failed", "provider"),
    }
    assert result.run_id == fx.RUN_IDS[0]
