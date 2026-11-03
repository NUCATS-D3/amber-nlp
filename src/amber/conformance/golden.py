"""Golden JSON records for WIR-14 (contract v0.1 section 5.3).

Each record built from the deterministic fixtures must have exactly its required top-level
keys and serialize to the bytes stored in ``golden/<name>.json``; parsing and re-serializing
must give the same bytes. Regenerate with ``write_golden`` only for a deliberate format change,
which is a contract change.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from amber.annotations import example_from_projection, replay
from amber.cases import load_case_result
from amber.conformance import fixtures as fx
from amber.examples import load_example, load_label_record
from amber.policy import DEFAULT_MATRIX, DataPolicy, Decision, Destination, check
from amber.schemas import AnnotationEvent, Sensitivity, Zone
from amber.wire import load_variable_set, variable_set_json

REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "case_result": (
        "schema_version", "case_id", "run_id", "variable_set", "variable_set_version",
        "variable_set_ref", "claims", "mentions", "mention_readings", "evidence", "edges",
        "variable_outcomes", "outcome", "final_claim_ids", "grounding_failures", "tool_calls",
        "escalated", "trace_id", "budget", "usage",
    ),
    "example": (
        "example_id", "schema_version", "case_id", "variable_set", "variable_set_version",
        "variable_set_ref", "as_of_event", "source_ids", "split", "references", "claims",
        "mentions", "mention_readings", "evidence", "edges", "created_at",
    ),
    "annotation_event": (
        "event_id", "schema_version", "case_id", "variable", "actor", "role", "round",
        "stream", "action", "target_id", "before", "after", "reason", "at", "provenance",
        "external_ref",
    ),
    "label_record": (
        "schema_version", "case_id", "variable", "variable_set", "variable_set_version",
        "variable_set_ref", "tier", "outcome_status", "value", "producer", "split",
    ),
    "decision": (
        "decision_id", "permitted", "reason", "restriction_id", "policy_version",
        "destination", "destination_kind", "zone", "sensitivity", "payload", "dataset",
        "run_id", "case_id", "checked_at", "schema_version",
    ),
    "variable_set": ("schema_version", "name", "version", "variables", "schema_ref"),
}  # fmt: skip


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _case_result() -> str:
    return fx.result(fx.RUN_IDS[0]).model_dump_json()


def _example() -> str:
    log, _ = fx.silver_import()
    projection = replay(
        log.events, case=fx.case(), source=fx.SOURCE, variables=fx.VARIABLES, spans=fx.SPANS
    )
    example = example_from_projection(
        projection, case=fx.case(), source=fx.SOURCE, variables=fx.VARIABLES
    )
    return example.model_dump_json()


def _annotation_event() -> str:
    log, _ = fx.silver_import()
    return log.events[3].model_dump_json()


def _label_record() -> str:
    record = load_label_record(
        {
            "schema_version": "amber/0.1",
            "case_id": "conformance-case-1",
            "variable": "summary",
            "variable_set": fx.VARIABLES.name,
            "variable_set_version": fx.VARIABLES.version,
            "variable_set_ref": fx.VARIABLES.schema_ref,
            "tier": "silver",
            "outcome_status": "answered",
            "value": {"present": True},
            "producer": fx.TEACHER,
            "split": "dev",
        },
        case=fx.case(),
        variables=fx.VARIABLES,
    )
    return record.model_dump_json()


def _decision() -> str:
    destination = Destination(
        name="conformance-tracking",
        kind="tracking",
        zone=Zone.institution,
        accepts=frozenset({"labels_only", "metrics"}),
    )
    decision = check(
        DEFAULT_MATRIX,
        DataPolicy(sensitivity=Sensitivity.synthetic),
        destination,
        "labels_only",
        now=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )
    return decision.model_dump_json()


def _variable_set() -> str:
    return _compact(variable_set_json(fx.VARIABLES))


RECORDS: dict[str, Callable[[], str]] = {
    "case_result": _case_result,
    "example": _example,
    "annotation_event": _annotation_event,
    "label_record": _label_record,
    "decision": _decision,
    "variable_set": _variable_set,
}


def reserialize(name: str, text: str) -> str:
    """Parse a record through its load boundary and serialize it again."""
    payload = json.loads(text)
    if name == "case_result":
        return load_case_result(
            payload, case=fx.case(), source=fx.SOURCE, variables=fx.VARIABLES
        ).model_dump_json()
    if name == "example":
        return load_example(
            payload, case=fx.case(), source=fx.SOURCE, variables=fx.VARIABLES
        ).model_dump_json()
    if name == "annotation_event":
        return AnnotationEvent.model_validate(payload).model_dump_json()
    if name == "label_record":
        return load_label_record(payload, case=fx.case(), variables=fx.VARIABLES).model_dump_json()
    if name == "decision":
        return Decision.model_validate(payload).model_dump_json()
    if name == "variable_set":
        return _compact(
            variable_set_json(load_variable_set(payload, answer_models=[fx.FindingAnswer]))
        )
    raise KeyError(name)


def golden(name: str) -> str:
    """The stored golden bytes for one record, without the trailing newline."""
    path = files("amber.conformance").joinpath("golden", f"{name}.json")
    return path.read_text("utf-8").removesuffix("\n")


def write_golden(directory: Path) -> None:
    """Rewrite the golden files; only for a deliberate, documented format change."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, build in RECORDS.items():
        (directory / f"{name}.json").write_text(build() + "\n", encoding="utf-8")
