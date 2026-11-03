"""JSON forms of records that are not plain model dumps (contract v0.1 section 5.3).

A ``VariableSet``'s answer models are Python classes, so its JSON carries each answer model as
its JSON Schema plus ``schema_ref`` and never as an import path alone. Loading needs the
authoritative answer-model classes and checks that the JSON describes exactly them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, NoReturn, TypeAlias

from amber.ids import schema_ref
from amber.schemas._wire import SCHEMA_VERSION, check_schema_version
from amber.schemas.answers import AnswerModel
from amber.schemas.variables import AttributeRequirement, Variable, VariableSet

WireFormatCode: TypeAlias = Literal[
    "invalid_schema", "unknown_answer_model", "answer_model_mismatch", "variable_set_mismatch"
]
_SET_KEYS = frozenset({"schema_version", "name", "version", "variables", "schema_ref"})
_VARIABLE_KEYS = frozenset(Variable.model_fields)


class WireFormatError(ValueError):
    """A stable wire-format diagnostic that renders no record contents."""

    def __init__(self, code: WireFormatCode) -> None:
        self.code = code
        super().__init__(code)


def _raise(code: WireFormatCode) -> NoReturn:
    raise WireFormatError(code) from None


def variable_set_json(variables: VariableSet) -> dict[str, Any]:
    """The JSON form of a variable set; sets serialize in sorted, deterministic order."""
    return {
        "schema_version": SCHEMA_VERSION,
        "name": variables.name,
        "version": variables.version,
        "variables": {
            name: {
                "name": variable.name,
                "level": variable.level,
                "answer_model": {
                    "schema_ref": schema_ref(variable.answer_model),
                    "json_schema": variable.answer_model.model_json_schema(),
                },
                "instructions": variable.instructions,
                "evidence_policy": variable.evidence_policy,
                "inputs": sorted(variable.inputs),
                "mention_types": sorted(variable.mention_types),
                "requires_attributes": [
                    requirement.model_dump(mode="json")
                    for requirement in variable.requires_attributes
                ],
                "requires_section": sorted(variable.requires_section),
                "final": variable.final,
            }
            for name, variable in sorted(variables.variables.items())
        },
        "schema_ref": variables.schema_ref,
    }


def load_variable_set(
    payload: Mapping[str, Any], *, answer_models: Iterable[type[AnswerModel]]
) -> VariableSet:
    """Rebuild a variable set from JSON against the authoritative answer-model classes.

    Each answer model is matched by ``schema_ref`` and must have exactly the recorded JSON
    Schema; the rebuilt set's ``schema_ref`` must equal the recorded one.
    """
    if not isinstance(payload, Mapping) or set(payload) != _SET_KEYS:
        _raise("invalid_schema")
    try:
        check_schema_version(payload["schema_version"])
    except ValueError:
        _raise("invalid_schema")
    models = {schema_ref(model): model for model in answer_models}
    entries = payload["variables"]
    if not isinstance(entries, Mapping):
        _raise("invalid_schema")
    variables: list[Variable] = []
    for name, entry in entries.items():
        if not isinstance(entry, Mapping) or set(entry) != _VARIABLE_KEYS or entry["name"] != name:
            _raise("invalid_schema")
        answer = entry["answer_model"]
        if not isinstance(answer, Mapping) or set(answer) != {"schema_ref", "json_schema"}:
            _raise("invalid_schema")
        model = models.get(answer["schema_ref"])
        if model is None:
            _raise("unknown_answer_model")
        if answer["json_schema"] != model.model_json_schema():
            _raise("answer_model_mismatch")
        try:
            variables.append(
                Variable(
                    name=entry["name"],
                    level=entry["level"],
                    answer_model=model,
                    instructions=entry["instructions"],
                    evidence_policy=entry["evidence_policy"],
                    inputs=frozenset(entry["inputs"]),
                    mention_types=frozenset(entry["mention_types"]),
                    requires_attributes=tuple(
                        AttributeRequirement.model_validate(item)
                        for item in entry["requires_attributes"]
                    ),
                    requires_section=frozenset(entry["requires_section"]),
                    final=entry["final"],
                )
            )
        except (TypeError, ValueError):
            _raise("invalid_schema")
    try:
        rebuilt = VariableSet.create(
            name=payload["name"], version=payload["version"], variables=variables
        )
    except (TypeError, ValueError):
        _raise("invalid_schema")
    if rebuilt.schema_ref != payload["schema_ref"]:
        _raise("variable_set_mismatch")
    return rebuilt
