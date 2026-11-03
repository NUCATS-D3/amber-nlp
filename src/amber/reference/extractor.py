"""``DeterministicExtractor``: the reference ``Extractor`` (contract v0.1 section 6.2).

For each final note-level variable without inputs it asks its provider for a schema-constrained
answer of the form ``{"status", "value", "quote", "hint_start", "reason"}``, grounds the quote
with ``quote()``, and records the answer with ``commit_claim`` or the non-answer with
``no_claim``. Given a ``ScriptedProvider`` and a fixed clock it is fully deterministic. Every
expected failure becomes a ``failed`` result; ``run`` never raises.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from amber.cases import CaseValidationError, create_case_result
from amber.extraction import ExtractorCapability, failed_result, local_provenance
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.interfaces import PolicyGate, Provider
from amber.policy import Destination, PolicyViolation, RunContext
from amber.providers import Message, ProviderFailure, ProviderRequest, UsageMeter, call_provider
from amber.schemas import (
    Budget,
    Case,
    CaseOutcome,
    CaseResult,
    GroundingFailure,
    Provenance,
    Source,
    VariableOutcome,
    VariableSet,
)
from amber.schemas.cases import FailureKind
from amber.tools.commit import commit_claim
from amber.tools.outcomes import no_claim
from amber.tools.quote import quote

OUTPUT_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"enum": ["answered", "not_mentioned", "insufficient_evidence"]},
        "value": {"type": ["object", "null"]},
        "quote": {"type": ["string", "null"]},
        "hint_start": {"type": ["integer", "null"]},
        "reason": {"type": ["string", "null"]},
    },
    "required": ["status"],
}


class _Abort(Exception):
    """Ends a run as a whole-result failure."""

    def __init__(self, kind: FailureKind, reason: str) -> None:
        self.kind = kind
        self.reason = reason
        super().__init__(reason)


class DeterministicExtractor:
    """Answers note-level variables through one provider; synthetic and offline."""

    capabilities: frozenset[ExtractorCapability] = frozenset({"note_claims", "batch"})

    def __init__(
        self,
        provider: Provider,
        *,
        name: str = "amber-deterministic",
        version: str = "0.1",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.provider = provider
        self.name = name
        self.version = version
        self.destinations: frozenset[Destination] = frozenset({provider.destination})
        self._clock = clock

    @property
    def producer(self) -> str:
        return f"tool:{self.name}@{self.version}"

    def _local(self, context: RunContext) -> Provenance:
        return local_provenance(
            context,
            producer=self.producer,
            tool=self.name,
            version=self.version,
            created_at=self._clock(),
        )

    def run(
        self,
        case: Case,
        source: Source,
        variables: VariableSet,
        *,
        gate: PolicyGate,
        budget: Budget | None,
        context: RunContext,
    ) -> CaseResult:
        if context.data.sensitivity is not case.sensitivity:
            # A runner error, not an expected failure: no truthful provenance can be stamped.
            raise ValueError("the run context's sensitivity does not match the case")
        meter = UsageMeter(budget)
        counters = {"grounding_failures": 0, "tool_calls": 0}
        try:
            return self._run(case, source, variables, gate, context, meter, counters)
        except PolicyViolation:
            kind: FailureKind = "policy"
            reason = "The policy gate refused the transfer."
        except _Abort as abort:
            kind, reason = abort.kind, abort.reason
        except (GraphValidationError, CaseValidationError):
            kind, reason = "validation", "The result failed amber's validation."
        except Exception:
            kind, reason = "internal", "The extractor failed unexpectedly."
        return failed_result(
            case,
            source,
            variables,
            context=context,
            failure_kind=kind,
            reason=reason,
            provenance=self._local(context),
            budget=budget,
            usage=meter.usage,
            grounding_failures=counters["grounding_failures"],
            tool_calls=counters["tool_calls"],
        )

    def run_many(
        self,
        items: Iterable[tuple[Case, Source]],
        variables: VariableSet,
        *,
        gate: PolicyGate,
        budget: Budget | None,
        context: RunContext,
    ) -> Iterator[CaseResult]:
        """One result per case, in input order; each equal to ``run`` for that case."""
        for case, source in items:
            yield self.run(case, source, variables, gate=gate, budget=budget, context=context)

    def _failed(
        self, name: str, kind: FailureKind, reason: str, context: RunContext
    ) -> VariableOutcome:
        outcome = CaseOutcome(
            status="failed", reason=reason, failure_kind=kind, provenance=self._local(context)
        )
        return VariableOutcome(variable=name, outcome=outcome)

    def _run(
        self,
        case: Case,
        source: Source,
        variables: VariableSet,
        gate: PolicyGate,
        context: RunContext,
        meter: UsageMeter,
        counters: dict[str, int],
    ) -> CaseResult:
        graph = EvidenceGraph(source=source, variables=variables, sensitivity=case.sensitivity)
        sources = {source.source_id: source}
        outcomes: dict[str, VariableOutcome] = {}
        for name, variable in sorted(variables.variables.items()):
            if not variable.final:
                continue
            if variable.level != "note" or variable.inputs:
                outcomes[name] = VariableOutcome(
                    variable=name,
                    outcome=CaseOutcome(
                        status="insufficient_evidence",
                        reason="This extractor answers only note-level variables without inputs.",
                        provenance=self._local(context),
                    ),
                )
                continue
            request = ProviderRequest(
                messages=(
                    Message(role="system", content=variable.instructions),
                    Message(role="user", content=source.text or ""),
                ),
                json_schema=OUTPUT_SCHEMA,
                prompt_versions={name: f"{self.name}@{self.version}"},
            )
            call = call_provider(
                self.provider,
                request,
                gate=gate,
                data=context.data,
                context=context,
                now=self._clock(),
                meter=meter,
                producer=self.producer,
                version=self.version,
            )
            if isinstance(call.outcome, ProviderFailure):
                if call.outcome.kind == "budget":
                    raise _Abort("budget", "The run's budget was exhausted.")
                outcomes[name] = self._failed(
                    name, call.outcome.kind, "The provider call failed.", context
                )
                continue
            assert call.provenance is not None and call.outcome.parsed is not None
            outcomes[name] = self._record(
                name, variable.evidence_policy, dict(call.outcome.parsed), call.provenance,
                case=case, graph=graph, sources=sources, context=context, counters=counters,
            )  # fmt: skip
        if meter.exceeded():
            raise _Abort("budget", "The run's budget was exhausted.")
        return create_case_result(
            case,
            graph=graph,
            run_id=context.run_id,
            variable_outcomes=outcomes,
            budget=meter.budget,
            usage=meter.usage,
            grounding_failures=counters["grounding_failures"],
            tool_calls=counters["tool_calls"],
        )

    def _record(
        self,
        name: str,
        evidence_policy: str,
        answer: dict[str, Any],
        provenance: Provenance,
        *,
        case: Case,
        graph: EvidenceGraph,
        sources: Mapping[str, Source],
        context: RunContext,
        counters: dict[str, int],
    ) -> VariableOutcome:
        status = answer.get("status")
        reason = answer.get("reason") if isinstance(answer.get("reason"), str) else None
        source_id = case.source_ids[0]
        if status in ("not_mentioned", "insufficient_evidence"):
            counters["tool_calls"] += 1
            try:
                outcome = no_claim(
                    status,
                    reason or "The model gave no answer.",
                    [source_id] if status == "not_mentioned" else [],
                    [],
                    case=case,
                    graph=graph,
                    provenance=provenance,
                )
            except CaseValidationError:
                return self._failed(name, "parse", "The model's non-answer was invalid.", context)
            return VariableOutcome(variable=name, outcome=outcome)
        if status != "answered" or not isinstance(answer.get("quote"), str):
            return self._failed(name, "parse", "The model's answer was not recognized.", context)
        hint = answer.get("hint_start")
        counters["tool_calls"] += 1
        included = quote(
            source_id, answer["quote"], hint if isinstance(hint, int) else None, sources=sources
        )
        if isinstance(included, GroundingFailure):
            counters["grounding_failures"] += 1
            return VariableOutcome(
                variable=name,
                outcome=CaseOutcome(
                    status="insufficient_evidence",
                    reason="The model's quote did not ground in the note.",
                    provenance=provenance,
                ),
            )
        graph.register_evidence(included)
        value = answer.get("value")
        fields = list(graph.variables.variables[name].answer_model.model_fields)
        roles = (
            {field: [included.evidence_id] for field in fields}
            if evidence_policy == "field"
            else None
        )
        counters["tool_calls"] += 1
        try:
            claim = commit_claim(
                name,
                value if isinstance(value, dict) else {},
                [included.evidence_id],
                reason or "The quoted text states the answer.",
                roles,
                graph=graph,
                provenance=provenance,
            )
        except GraphValidationError:
            return self._failed(
                name, "parse", "The model's answer did not fit the schema.", context
            )
        return VariableOutcome(
            variable=name,
            outcome=CaseOutcome(status="answered", provenance=provenance),
            final_claim_ids=[claim.claim_id],
        )
