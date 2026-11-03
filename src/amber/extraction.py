"""Shared pieces for extractors (contract v0.1 sections 6.2 and 6.6).

An extractor returns exactly one ``CaseResult`` per case, always: an expected failure is a
result whose final variables are ``failed`` with a ``failure_kind``, never an exception.
``failed_result`` builds that result, and ``local_provenance`` stamps nodes and outcomes that
no external destination produced.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeAlias

from amber.cases import create_case_result
from amber.graph import EvidenceGraph
from amber.policy import RunContext
from amber.schemas import (
    Budget,
    Case,
    CaseOutcome,
    CaseResult,
    Provenance,
    Source,
    Usage,
    VariableOutcome,
    VariableSet,
    Zone,
)
from amber.schemas.cases import FailureKind

ExtractorCapability: TypeAlias = Literal["mentions", "note_claims", "derivations", "batch"]


def local_provenance(
    context: RunContext, *, producer: str, tool: str, version: str, created_at: datetime
) -> Provenance:
    """Provenance for a node or outcome produced locally under ``context``."""
    return Provenance(
        producer=producer,
        run_id=context.run_id,
        destination=f"local:{tool}",
        policy_version=context.policy_version,
        restriction_ids=context.data.restriction_ids,
        zone=Zone.local,
        sensitivity=context.data.sensitivity,
        created_at=created_at,
        version=version,
    )


def failed_result(
    case: Case,
    source: Source,
    variables: VariableSet,
    *,
    context: RunContext,
    failure_kind: FailureKind,
    reason: str,
    provenance: Provenance,
    budget: Budget | None = None,
    usage: Usage | None = None,
    grounding_failures: int = 0,
    tool_calls: int = 0,
) -> CaseResult:
    """A result with no claims whose every final variable failed with ``failure_kind``."""
    graph = EvidenceGraph(source=source, variables=variables, sensitivity=case.sensitivity)
    outcome = CaseOutcome(
        status="failed", reason=reason, failure_kind=failure_kind, provenance=provenance
    )
    return create_case_result(
        case,
        graph=graph,
        run_id=context.run_id,
        variable_outcomes={
            name: VariableOutcome(variable=name, outcome=outcome)
            for name, variable in variables.variables.items()
            if variable.final
        },
        budget=budget,
        usage=usage,
        grounding_failures=grounding_failures,
        tool_calls=tool_calls,
    )
