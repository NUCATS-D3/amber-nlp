"""Variable sets: named questions with answer schemas, levels, and declared dependencies.

A ``Task`` is the one-variable case (contract v0.1 section 3.2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from amber.ids import canonical_sha256
from amber.schemas._immutable import FrozenMapping
from amber.schemas.answers import AnswerModel
from amber.schemas.tasks import Task

TASK_SET_VERSION = "task"
_Name = Annotated[str, Field(strict=True, min_length=1)]
AttributeValue = StrictBool | StrictInt | StrictStr


def _name(value: str) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError("names must be nonblank and unpadded")
    return value


def _names(value: frozenset[str]) -> frozenset[str]:
    for item in value:
        _name(item)
    return value


def _value_key(value: bool | int | str) -> tuple[str, str]:
    return (type(value).__name__, repr(value))


class AttributeRequirement(BaseModel):
    """An attribute category a mention-level variable requires on the reading it relies on.

    ``values`` are compared with strict types, so they are kept as a tuple: a set would merge
    ``True`` and ``1``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: _Name
    values: tuple[AttributeValue, ...] | None = None
    evidence: Literal["required", "lenient"] = "required"

    @field_validator("category")
    @classmethod
    def require_name(cls, value: str) -> str:
        return _name(value)

    @field_validator("values")
    @classmethod
    def canonicalize_values(
        cls, value: tuple[bool | int | str, ...] | None
    ) -> tuple[bool | int | str, ...] | None:
        if value is None:
            return None
        keys = [_value_key(item) for item in value]
        if not value or len(keys) != len(set(keys)):
            raise ValueError("permitted values must be nonempty and distinct by type and value")
        return tuple(sorted(value, key=_value_key))


class Variable(BaseModel):
    """A named question with its own answer schema and level."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: _Name
    level: Literal["mention", "note", "patient"]
    answer_model: type[AnswerModel]
    instructions: str = Field(strict=True)
    evidence_policy: Literal["claim", "field"] = "claim"
    inputs: frozenset[str] = frozenset()
    mention_types: frozenset[str] = frozenset()
    requires_attributes: tuple[AttributeRequirement, ...] = ()
    requires_section: frozenset[str] = frozenset()
    final: bool = Field(default=False, strict=True)

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        return _name(value)

    @field_validator("inputs", "mention_types", "requires_section")
    @classmethod
    def require_names(cls, value: frozenset[str]) -> frozenset[str]:
        return _names(value)

    @model_validator(mode="after")
    def require_consistent_level(self) -> Variable:
        if self.name in self.inputs:
            raise ValueError("a variable cannot be its own input")
        if self.level == "mention" and not self.mention_types:
            raise ValueError("a mention-level variable must declare mention_types")
        if self.level != "mention" and (self.mention_types or self.requires_attributes):
            raise ValueError("mention_types and requires_attributes are mention-level only")
        categories = [requirement.category for requirement in self.requires_attributes]
        if len(categories) != len(set(categories)):
            raise ValueError("attribute requirements must name distinct categories")
        return self


def _identity(variable: Variable) -> dict[str, Any]:
    return {
        "answer_model": variable.answer_model.model_json_schema(),
        "evidence_policy": variable.evidence_policy,
        "final": variable.final,
        "inputs": sorted(variable.inputs),
        "instructions": variable.instructions,
        "level": variable.level,
        "mention_types": sorted(variable.mention_types),
        "name": variable.name,
        "requires_attributes": [
            {
                "category": requirement.category,
                "evidence": requirement.evidence,
                "values": None if requirement.values is None else list(requirement.values),
            }
            for requirement in sorted(
                variable.requires_attributes, key=lambda requirement: requirement.category
            )
        ],
        "requires_section": sorted(variable.requires_section),
    }


def variable_set_ref(name: str, variables: Iterable[Variable]) -> str:
    """Content identity of a set: its name and every variable field, never its version.

    Answer models enter as their JSON Schema, so moving a model class does not change it.
    """
    return canonical_sha256(
        {"name": name, "variables": {item.name: _identity(item) for item in variables}}
    )


class VariableSet(BaseModel):
    """Named, versioned variables whose content hash is their identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: _Name
    version: _Name
    variables: Mapping[str, Variable]
    schema_ref: Annotated[str, Field(strict=True)]

    @classmethod
    def create(cls, *, name: str, version: str, variables: Iterable[Variable]) -> VariableSet:
        items = tuple(variables)
        return cls(
            name=name,
            version=version,
            variables={item.name: item for item in items},
            schema_ref=variable_set_ref(name, items),
        )

    @classmethod
    def from_task(cls, task: Task) -> VariableSet:
        """The one-variable set a ``Task`` stands for: one final variable named as the task."""
        if not isinstance(task, Task):
            raise TypeError("from_task requires a Task")
        variable = Variable(
            name=task.name,
            level=task.scope,
            answer_model=task.answer_model,
            instructions=task.instructions,
            evidence_policy=task.evidence_policy,
            final=True,
        )
        return cls.create(name=task.name, version=TASK_SET_VERSION, variables=(variable,))

    @field_validator("name", "version")
    @classmethod
    def require_name(cls, value: str) -> str:
        return _name(value)

    @field_validator("variables")
    @classmethod
    def freeze_variables(cls, value: Mapping[str, Variable]) -> Mapping[str, Variable]:
        if not value:
            raise ValueError("a variable set needs at least one variable")
        if any(key != variable.name for key, variable in value.items()):
            raise ValueError("variables must be keyed by their names")
        return FrozenMapping(value)

    @model_validator(mode="after")
    def require_valid_set(self) -> VariableSet:
        variables = self.variables
        if not any(variable.final for variable in variables.values()):
            raise ValueError("a variable set needs at least one final variable")
        for variable in variables.values():
            for input_name in variable.inputs:
                if input_name not in variables:
                    raise ValueError("variable inputs must name variables in the set")
                if variable.level == "mention" and variables[input_name].level != "mention":
                    raise ValueError("mention-level variables may derive only from mentions")
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(name: str) -> None:
            if name in done:
                return
            if name in visiting:
                raise ValueError("variable inputs must be acyclic")
            visiting.add(name)
            for input_name in sorted(variables[name].inputs):
                visit(input_name)
            visiting.discard(name)
            done.add(name)

        for name in sorted(variables):
            visit(name)
        if self.schema_ref != variable_set_ref(self.name, variables.values()):
            raise ValueError("schema_ref does not match the variable set content")
        return self

    def task(self) -> Task | None:
        """Return the ``Task`` this set stands for, if it is exactly a task's one-variable set."""
        if len(self.variables) != 1 or self.name not in self.variables:
            return None
        variable = self.variables[self.name]
        if variable.level == "mention" or not variable.final or variable.inputs:
            return None
        if variable.requires_section:
            return None
        task = Task(
            name=variable.name,
            answer_model=variable.answer_model,
            instructions=variable.instructions,
            evidence_policy=variable.evidence_policy,
            scope=variable.level,
        )
        return task if VariableSet.from_task(task).schema_ref == self.schema_ref else None
