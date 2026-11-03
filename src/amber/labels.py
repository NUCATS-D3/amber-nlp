"""The only sanctioned downgrades from ``derived_text`` to ``labels_only`` (contract v0.1
sections 1.4 and 4.4).

``labels_only_view(result, case=...)`` keeps one ``LabelRecord`` per final variable: outcome
status, answer value, producer, and split, without quotes, offsets, rationales, or reasons.
``labels_only_view(events)`` keeps who did what and when. A caller may not strip fields on its
own judgement instead.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Annotated, Any, overload

from pydantic import BaseModel, ConfigDict, Field

from amber.ids import canonical_json
from amber.schemas._wire import Timestamp
from amber.schemas.annotations import AnnotationEvent, EventAction, EventRole
from amber.schemas.cases import Case, CaseResult
from amber.schemas.examples import LabelRecord
from amber.schemas.variables import VariableSet

_CLOSED_STRING_FORMATS = frozenset({"date", "date-time"})
_Text = Annotated[str, Field(strict=True, min_length=1)]


class LabelsOnlyError(ValueError):
    """A record that cannot be reduced to labels without keeping source-bearing text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _closed(schema: Any, definitions: Mapping[str, Any], seen: frozenset[str]) -> bool:
    if not isinstance(schema, Mapping):
        return False
    if "const" in schema or "enum" in schema:
        return True
    reference = schema.get("$ref")
    if isinstance(reference, str):
        name = reference.rsplit("/", 1)[-1]
        if name in seen or name not in definitions:
            return name in seen
        return _closed(definitions[name], definitions, seen | {name})
    for combinator in ("anyOf", "oneOf", "allOf"):
        if combinator in schema:
            branches = schema[combinator]
            return isinstance(branches, list) and all(
                _closed(branch, definitions, seen) for branch in branches
            )
    kind = schema.get("type")
    if isinstance(kind, list):
        return all(_closed({**schema, "type": item}, definitions, seen) for item in kind)
    if kind in ("boolean", "integer", "number", "null"):
        return True
    if kind == "string":
        return schema.get("format") in _CLOSED_STRING_FORMATS
    if kind == "array":
        return _closed(schema.get("items"), definitions, seen)
    if kind == "object":
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        return (
            isinstance(properties, Mapping)
            and all(_closed(item, definitions, seen) for item in properties.values())
            and (extra is False or _closed(extra, definitions, seen))
        )
    return False


def is_closed_schema(schema: Mapping[str, Any]) -> bool:
    """Whether every value a JSON Schema admits comes from a fixed set (contract v0.1
    section 5.2): booleans, numbers, nulls, and strings limited by ``enum``, ``const``, or a
    ``date``/``date-time`` format, in closed arrays and objects. Any open string is not."""
    definitions = schema.get("$defs", {})
    return _closed(schema, definitions if isinstance(definitions, Mapping) else {}, frozenset())


class EventLabel(BaseModel):
    """Who did what to which record, and when: an annotation event without content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: _Text
    case_id: _Text
    variable: _Text | None
    actor: _Text
    role: EventRole
    round: int
    stream: _Text | None
    action: EventAction
    target_id: _Text | None
    at: Timestamp
    external_ref: _Text | None


def _result_labels(
    result: CaseResult, case: Case, variables: VariableSet
) -> tuple[LabelRecord, ...]:
    if not isinstance(case, Case) or case.case_id != result.case_id:
        raise LabelsOnlyError("invalid_context")
    if (
        not isinstance(variables, VariableSet)
        or variables.schema_ref != result.variable_set_ref
        or variables.name != result.variable_set
        or variables.version != result.variable_set_version
    ):
        raise LabelsOnlyError("variable_set_mismatch")
    claims = {claim.claim_id: claim for claim in result.claims}
    records: list[LabelRecord] = []
    for name, outcome in sorted(result.variable_outcomes.items()):
        if outcome.outcome.status == "failed":
            continue
        variable = variables.variables[name]
        value: Mapping[str, Any] | None = None
        producer = outcome.outcome.provenance.producer
        if outcome.outcome.status == "answered":
            if not is_closed_schema(variable.answer_model.model_json_schema()):
                raise LabelsOnlyError("open_answer_fields")
            finals = [claims[claim_id] for claim_id in outcome.final_claim_ids]
            if len({canonical_json(claim.value) for claim in finals}) != 1:
                raise LabelsOnlyError("several_answer_values")
            if len({claim.provenance.producer for claim in finals}) != 1:
                raise LabelsOnlyError("several_producers")
            value = finals[0].value
            producer = finals[0].provenance.producer
        try:
            records.append(
                LabelRecord(
                    schema_version="amber/0.1",
                    case_id=result.case_id,
                    variable=name,
                    variable_set=result.variable_set,
                    variable_set_version=result.variable_set_version,
                    variable_set_ref=result.variable_set_ref,
                    tier="silver",
                    outcome_status=outcome.outcome.status,
                    value=value,
                    producer=producer,
                    split=case.split,
                )
            )
        except ValueError:
            raise LabelsOnlyError("not_a_silver_label") from None
    return tuple(records)


def _event_labels(events: Iterable[AnnotationEvent]) -> tuple[EventLabel, ...]:
    labels: list[EventLabel] = []
    for event in events:
        if not isinstance(event, AnnotationEvent):
            raise LabelsOnlyError("invalid_schema")
        labels.append(
            EventLabel(
                event_id=event.event_id,
                case_id=event.case_id,
                variable=event.variable,
                actor=event.actor,
                role=event.role,
                round=event.round,
                stream=event.stream,
                action=event.action,
                target_id=event.target_id,
                at=event.at,
                external_ref=event.external_ref,
            )
        )
    return tuple(labels)


@overload
def labels_only_view(
    item: CaseResult, *, case: Case, variables: VariableSet
) -> tuple[LabelRecord, ...]: ...
@overload
def labels_only_view(item: Iterable[AnnotationEvent]) -> tuple[EventLabel, ...]: ...
def labels_only_view(
    item: CaseResult | Iterable[AnnotationEvent],
    *,
    case: Case | None = None,
    variables: VariableSet | None = None,
) -> tuple[LabelRecord, ...] | tuple[EventLabel, ...]:
    """Reduce a result or an event log to its ``labels_only`` form.

    For a ``CaseResult``: one silver ``LabelRecord`` per final variable that did not fail
    (failed runs are diagnostics, not labels), with the case's split. The answer value is kept
    only when the variable's answer model is closed (``open_answer_fields`` otherwise) and all
    final claims agree on it and on their producer. ``case`` and ``variables`` are the
    authoritative context. For events: one ``EventLabel`` per event.
    """
    if isinstance(item, CaseResult):
        if case is None or variables is None:
            raise LabelsOnlyError("invalid_context")
        return _result_labels(item, case, variables)
    if case is not None or variables is not None:
        raise LabelsOnlyError("invalid_context")
    return _event_labels(item)
