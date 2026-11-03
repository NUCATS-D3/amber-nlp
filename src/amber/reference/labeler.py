"""``ImportLabeler``: the reference ``Labeler``, importing stored predictions as silver."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from amber.cases import CaseValidationError, load_case_result
from amber.examples import ExampleValidationError, load_label_record
from amber.extraction import local_provenance
from amber.graph_errors import GraphValidationError
from amber.interfaces import PolicyGate
from amber.labeling import LabelerFailure, LabelerOutput, import_events
from amber.policy import RunContext
from amber.schemas import Case, ReferenceTier, Source, VariableSet


class ImportLabeler:
    """Reads ``{case_id: {"result": <CaseResult JSON>}}`` or ``{case_id: {"label":
    <LabelRecord JSON>}}`` from a JSON file of stored predictions.

    A result becomes a silver Example with its import events, after revalidation against the
    authoritative case, source, and variable set; a label becomes a validated ``LabelRecord``.
    Reading a local file sends nothing, so no gate check is needed.
    """

    tier = ReferenceTier.silver

    def __init__(
        self,
        path: Path,
        *,
        name: str = "amber-import",
        version: str = "0.1",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path = path
        self.name = name
        self.version = version
        self._clock = clock

    @property
    def producer(self) -> str:
        return f"tool:{self.name}@{self.version}"

    def _entry(self, case_id: str) -> Any:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data.get(case_id) if isinstance(data, dict) else None

    def label(
        self,
        case: Case,
        source: Source,
        variables: VariableSet,
        *,
        gate: PolicyGate,
        context: RunContext,
    ) -> LabelerOutput:
        entry = self._entry(case.case_id)
        if not isinstance(entry, dict) or len(entry) != 1:
            return LabelerFailure(reason="missing")
        try:
            if "label" in entry:
                return load_label_record(entry["label"], case=case, variables=variables)
            if "result" not in entry:
                return LabelerFailure(reason="unsupported")
            result = load_case_result(
                entry["result"], case=case, source=source, variables=variables
            )
        except (CaseValidationError, ExampleValidationError, GraphValidationError):
            return LabelerFailure(reason="invalid")
        if any(item.outcome.status == "failed" for item in result.variable_outcomes.values()):
            return LabelerFailure(reason="failed_result")
        now = self._clock()
        events, _, example = import_events(
            result,
            case=case,
            source=source,
            variables=variables,
            actor=self.producer,
            provenance=local_provenance(
                context,
                producer=self.producer,
                tool=self.name,
                version=self.version,
                created_at=now,
            ),
            now=now,
        )
        return events, example
