"""commit_claim(...) -> Claim. Validate the task's AnswerModel, field evidence, case scope,
and the full source-backed support DAG. Shared by fixed pipelines and optional agents.
Unanswered outcomes are recorded by ``amber.tools.outcomes.no_claim``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from amber.graph import EvidenceGraph
from amber.schemas import Claim, Provenance, Task


def commit_claim(
    task: Task,
    value: dict[str, Any],
    evidence_ids: Sequence[str],
    rationale: str,
    field_roles: Mapping[str, Sequence[str]] | None = None,
    *,
    graph: EvidenceGraph,
    provenance: Provenance,
    effective_datetime: datetime | None = None,
    confidence: float | None = None,
) -> Claim:
    """Forward a proposed claim commit to its graph-owned validation boundary."""
    return graph.commit_claim(
        task,
        value,
        evidence_ids,
        rationale,
        field_roles,
        provenance=provenance,
        effective_datetime=effective_datetime,
        confidence=confidence,
    )
