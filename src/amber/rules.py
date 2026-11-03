"""Rules, reduction, and replay of rule derivations (contract v0.1 sections 3.4 and 3.6).

A ``Rule`` is registered with a ``RuleSpec`` declaring its roles. Before a rule is applied,
amber gathers each role's candidates: the non-rejected claims of the role's variable in the
case, or the inclusions named for an ``evidence:inclusion`` role.

- A ``many`` role receives every candidate as a tuple in ID order; an empty tuple is missing.
- A ``one`` role with no candidates is missing; with candidates whose values are equal
  (canonical JSON of the claim value, or the quote of an inclusion) it receives the lowest-ID
  candidate and the derivation lists them all; with different values it is a **conflict**.
- A missing role follows ``on_missing``: ``refuse`` gives ``insufficient_evidence`` naming the
  role; ``default`` passes ``RuleSpec.defaults[role]`` and records it in ``defaults_used``;
  ``pass_none`` passes ``None`` (``()`` for ``many``).

Rules never see a conflict: the derived variable is ``conflicting_evidence``, citing the source
leaves of every conflicting claim, and ``derive_all`` propagates the conflict to rules that
read that variable. Rules are pure; ``replay_derivations`` recomputes every registered rule
derivation in a result or Example and fails with ``derivation_mismatch`` on any difference.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.ids import canonical_json
from amber.schemas import (
    CaseResult,
    Claim,
    Derivation,
    Example,
    Inclusion,
    InferenceEvidence,
    Provenance,
    VariableSet,
)
from amber.schemas._immutable import FrozenMapping, freeze_json

EVIDENCE_INCLUSION = "evidence:inclusion"
_RESERVED_SOURCES = frozenset({"evidence:structured"})

RoleInput: TypeAlias = Any
"""A ``Claim`` or ``Inclusion`` (or ``None``) for a ``one`` role, a tuple of them for a ``many``
role, or the declared default value of a defaulted role."""


class RoleSpec(BaseModel):
    """How a role gathers its inputs and what happens when it has none."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    cardinality: Literal["one", "many"] = "one"
    on_missing: Literal["refuse", "default", "pass_none"] = "refuse"

    @field_validator("source")
    @classmethod
    def reject_reserved_sources(cls, value: str) -> str:
        if value in _RESERVED_SOURCES:
            raise ValueError("structured evidence roles are reserved")
        if not value.strip():
            raise ValueError("a role source must not be blank")
        return value


class RuleSpec(BaseModel):
    """A rule's registration: its roles, the variable it derives, and its defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    roles: dict[str, RoleSpec]
    outputs: str = Field(min_length=1)
    defaults: dict[str, Any] = Field(default_factory=dict)

    @field_validator("roles")
    @classmethod
    def freeze_roles(cls, value: dict[str, RoleSpec]) -> dict[str, RoleSpec]:
        if not value or any(not role.strip() for role in value):
            raise ValueError("a rule needs named roles")
        return FrozenMapping(value)

    @field_validator("defaults")
    @classmethod
    def freeze_defaults(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            return freeze_json(value)  # type: ignore[no-any-return]
        except TypeError:
            raise ValueError("defaults must be JSON") from None

    @model_validator(mode="after")
    def require_defaults_for_default_roles(self) -> RuleSpec:
        defaulted = {name for name, role in self.roles.items() if role.on_missing == "default"}
        if set(self.defaults) != defaulted:
            raise ValueError("exactly the roles with on_missing='default' need a default")
        return self

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def check(self, variables: VariableSet) -> None:
        """Require the output variable and every variable role to fit the set: the output
        exists and declares each role's variable among its ``inputs``."""
        output = variables.variables.get(self.outputs)
        if output is None:
            raise GraphValidationError("invalid_context")
        for role in self.roles.values():
            if role.source == EVIDENCE_INCLUSION:
                continue
            if role.source not in variables.variables:
                raise GraphValidationError("invalid_context")
            if role.source not in output.inputs:
                raise GraphValidationError("undeclared_dependency")


class RuleResult(BaseModel):
    """A value for the deriving variable, or an unanswered status with a reason."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: dict[str, Any] | None = None
    status: Literal["insufficient_evidence", "not_mentioned"] | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def require_one_result(self) -> RuleResult:
        if (self.value is None) == (self.status is None):
            raise ValueError("a rule result is a value or a status")
        if self.status is not None and not (self.reason and self.reason.strip()):
            raise ValueError("a status needs a reason")
        if self.value is not None and self.reason is not None:
            raise ValueError("a value carries no reason")
        return self


@runtime_checkable
class Rule(Protocol):
    """A pure rule (contract v0.1 sections 3.4 and 6.2): no I/O, clock, or randomness.

    ``apply`` receives one ``RoleInput`` per declared role and the rule's parameters; it never
    sees conflicts or ``defaults_used``. Reference: ``amber.reference.AjccStub``.
    """

    spec: RuleSpec

    def apply(
        self, inputs: Mapping[str, RoleInput], parameters: Mapping[str, Any]
    ) -> RuleResult: ...


class RuleRegistrationError(ValueError):
    """A rule that cannot be registered; renders no record contents."""


class Registry:
    """Rules keyed by ``name@version``, populated by the consumer at startup."""

    def __init__(self, rules: Sequence[Rule] = ()) -> None:
        self._rules: dict[str, Rule] = {}
        for rule in rules:
            self.register(rule)

    def register(self, rule: Rule) -> None:
        spec = getattr(rule, "spec", None)
        if not isinstance(spec, RuleSpec) or not callable(getattr(rule, "apply", None)):
            raise RuleRegistrationError("not_a_rule")
        current = self._rules.get(spec.ref)
        if current is not None and current.spec != spec:
            raise RuleRegistrationError("rule_version_conflict")
        self._rules[spec.ref] = rule

    def get(self, name: str, version: str) -> Rule | None:
        return self._rules.get(f"{name}@{version}")

    def __contains__(self, ref: object) -> bool:
        return ref in self._rules


# Reduction and application -------------------------------------------------------------------


@dataclass(frozen=True)
class DerivedOutcome:
    """A derived variable without a claim: its status, a reason, and cited evidence IDs."""

    variable: str
    status: Literal["conflicting_evidence", "insufficient_evidence", "not_mentioned"]
    reason: str
    evidence_ids: tuple[str, ...] = ()


Derived: TypeAlias = Claim | DerivedOutcome


def _value_key(record: Claim | Inclusion) -> str:
    return canonical_json(record.value if isinstance(record, Claim) else record.quote)


@dataclass(frozen=True)
class _Gathered:
    inputs: dict[str, RoleInput]
    listed: dict[str, tuple[str, ...]]
    defaults: dict[str, Any]
    conflict: tuple[str, ...]
    refused: str | None


def _gather(
    spec: RuleSpec,
    graph: EvidenceGraph,
    upstream: Mapping[str, Derived],
    evidence: Mapping[str, Sequence[str]],
) -> _Gathered:
    inputs: dict[str, RoleInput] = {}
    listed: dict[str, tuple[str, ...]] = {}
    defaults: dict[str, Any] = {}
    conflict: list[str] = []
    refused: str | None = None
    for name, role in sorted(spec.roles.items()):
        prior = upstream.get(role.source)
        if isinstance(prior, DerivedOutcome) and prior.status == "conflicting_evidence":
            conflict.extend(prior.evidence_ids)
            continue
        candidates: list[Claim | Inclusion]
        if role.source == EVIDENCE_INCLUSION:
            ids = sorted(set(evidence.get(name, ())))
            records = [graph.evidence.get(item) for item in ids]
            if any(not isinstance(record, Inclusion) for record in records):
                raise GraphValidationError("unknown_reference")
            candidates = [record for record in records if isinstance(record, Inclusion)]
        else:
            candidates = [
                claim
                for _, claim in sorted(graph.claims.items())
                if claim.variable == role.source and claim.status != "rejected"
            ]
        ids_of = tuple(
            record.claim_id if isinstance(record, Claim) else record.evidence_id
            for record in candidates
        )
        listed[name] = ids_of
        if candidates and role.cardinality == "many":
            inputs[name] = tuple(candidates)
        elif candidates and len({_value_key(record) for record in candidates}) == 1:
            inputs[name] = candidates[0]
        elif candidates:
            for record in candidates:
                if isinstance(record, Claim):
                    conflict.extend(
                        leaf.evidence_id for leaf in graph.source_leaves(record.claim_id)
                    )
                else:
                    conflict.append(record.evidence_id)
        elif role.on_missing == "refuse":
            refused = refused or name
        elif role.on_missing == "default":
            inputs[name] = spec.defaults[name]
            defaults[name] = spec.defaults[name]
        else:
            inputs[name] = () if role.cardinality == "many" else None
    return _Gathered(inputs, listed, defaults, tuple(sorted(set(conflict))), refused)


def derive(
    graph: EvidenceGraph,
    rule: Rule,
    *,
    provenance: Provenance,
    parameters: Mapping[str, Any] | None = None,
    upstream: Mapping[str, Derived] | None = None,
    evidence: Mapping[str, Sequence[str]] | None = None,
) -> Derived:
    """Reduce the rule's roles over the graph, then commit the derived claim or return the
    outcome the deriving variable has instead.

    ``upstream`` carries what earlier rules derived, so a conflict propagates; ``evidence``
    names the inclusions for each ``evidence:inclusion`` role.
    """
    spec = rule.spec
    spec.check(graph.variables)
    params = dict(parameters or {})
    gathered = _gather(spec, graph, upstream or {}, evidence or {})
    if gathered.conflict:
        return DerivedOutcome(
            spec.outputs,
            "conflicting_evidence",
            f"Rule {spec.ref}: inputs disagree.",
            gathered.conflict,
        )
    if gathered.refused is not None:
        return DerivedOutcome(
            spec.outputs,
            "insufficient_evidence",
            f"Rule {spec.ref}: role {gathered.refused} has no input.",
        )
    result = rule.apply(gathered.inputs, params)
    if not isinstance(result, RuleResult):
        raise GraphValidationError("invalid_schema")
    if result.status is not None:
        assert result.reason is not None
        return DerivedOutcome(spec.outputs, result.status, result.reason)
    cited = sorted({item for ids in gathered.listed.values() for item in ids})
    if not cited:
        return DerivedOutcome(
            spec.outputs,
            "insufficient_evidence",
            f"Rule {spec.ref}: no source-backed input.",
        )
    derivation = Derivation(
        kind="rule",
        name=spec.name,
        version=spec.version,
        inputs=gathered.listed,
        parameters=params,
        defaults_used=gathered.defaults,
    )
    assert result.value is not None
    return graph.commit_claim(
        spec.outputs,
        dict(result.value),
        cited,
        f"Rule {spec.ref}.",
        provenance=provenance,
        derivation=derivation,
    )


def derive_all(
    graph: EvidenceGraph,
    rules: Sequence[Rule],
    *,
    provenance: Provenance,
    parameters: Mapping[str, Mapping[str, Any]] | None = None,
    evidence: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> dict[str, Derived]:
    """Apply rules in dependency order (a rule runs after every rule deriving a variable it
    reads), propagating conflicts. ``parameters`` and ``evidence`` are keyed by rule ref."""
    pending = {rule.spec.ref: rule for rule in rules}
    outputs = {rule.spec.outputs for rule in rules}
    if len(outputs) != len(rules):
        raise GraphValidationError("invalid_context")
    derived: dict[str, Derived] = {}
    while pending:
        ready = sorted(
            ref
            for ref, rule in pending.items()
            if all(
                role.source not in outputs or role.source in derived
                for role in rule.spec.roles.values()
            )
        )
        if not ready:
            raise GraphValidationError("cyclic_support")
        for ref in ready:
            rule = pending.pop(ref)
            derived[rule.spec.outputs] = derive(
                graph,
                rule,
                provenance=provenance,
                parameters=(parameters or {}).get(ref),
                upstream=derived,
                evidence=(evidence or {}).get(ref),
            )
    return derived


# Replay --------------------------------------------------------------------------------------


def _mismatch() -> GraphValidationError:
    return GraphValidationError("derivation_mismatch")


def _replay_inputs(
    spec: RuleSpec,
    derivation: Derivation,
    claims: Mapping[str, Claim],
    inclusions: Mapping[str, Inclusion],
) -> dict[str, RoleInput]:
    if set(derivation.inputs) != set(spec.roles):
        raise _mismatch()
    inputs: dict[str, RoleInput] = {}
    for name, role in spec.roles.items():
        ids = derivation.inputs[name]
        if role.source != EVIDENCE_INCLUSION:
            # Every non-rejected claim of the role's variable was a candidate; listing fewer
            # could hide a conflict, and a claim added since makes the derivation stale.
            candidates = {
                claim_id
                for claim_id, claim in claims.items()
                if claim.variable == role.source and claim.status != "rejected"
            }
            if set(ids) != candidates:
                raise _mismatch()
        if name in derivation.defaults_used:
            if role.on_missing != "default" or canonical_json(
                derivation.defaults_used[name]
            ) != canonical_json(spec.defaults[name]):
                raise _mismatch()
            inputs[name] = spec.defaults[name]
            continue
        if not ids:
            if role.on_missing != "pass_none":
                raise _mismatch()
            inputs[name] = () if role.cardinality == "many" else None
            continue
        records: list[Claim | Inclusion] = []
        for item in sorted(ids):
            if role.source == EVIDENCE_INCLUSION:
                record: Claim | Inclusion | None = inclusions.get(item)
            else:
                claim = claims.get(item)
                record = claim if claim is not None and claim.variable == role.source else None
            if record is None:
                raise _mismatch()
            records.append(record)
        if role.cardinality == "many":
            inputs[name] = tuple(records)
        elif len({_value_key(record) for record in records}) == 1:
            inputs[name] = records[0]
        else:
            raise _mismatch()
    return inputs


def replay_derivations(
    record: CaseResult | Example, *, registry: Registry, variables: VariableSet
) -> tuple[str, ...]:
    """Recompute every registered rule derivation and compare it with the derived claims.

    A variable role must list exactly the record's non-rejected claims of that variable: a
    missing candidate could hide a conflict, and a claim added or rejected after the derivation
    (say, in review) makes it stale, so the rule must be applied again. Returns the IDs of the
    inferences replayed. Unregistered rules, and model and human derivations, are not
    replayed. Raises ``GraphValidationError("derivation_mismatch")``.
    """
    claims = {claim.claim_id: claim for claim in record.claims}
    inclusions = {item.evidence_id: item for item in record.evidence if isinstance(item, Inclusion)}
    replayed: list[str] = []
    for item in sorted(record.evidence, key=lambda evidence: evidence.evidence_id):
        if not isinstance(item, InferenceEvidence) or item.derivation is None:
            continue
        derivation = item.derivation
        rule = (
            registry.get(derivation.name, derivation.version) if derivation.kind == "rule" else None
        )
        if rule is None:
            continue
        spec = rule.spec
        derived = [
            claims[edge.claim_id]
            for edge in record.edges
            if edge.evidence_id == item.evidence_id and edge.claim_id in claims
        ]
        if not derived or any(claim.variable != spec.outputs for claim in derived):
            raise _mismatch()
        inputs = _replay_inputs(spec, derivation, claims, inclusions)
        try:
            result = rule.apply(inputs, dict(derivation.parameters))
            if not isinstance(result, RuleResult) or result.value is None:
                raise _mismatch()
            answer = variables.variables[spec.outputs].answer_model.model_validate(
                dict(result.value)
            )
        except (GraphValidationError, ValidationError, ValueError, TypeError, KeyError):
            raise _mismatch() from None
        expected = canonical_json(answer.model_dump(mode="json"))
        if any(canonical_json(claim.value) != expected for claim in derived):
            raise _mismatch()
        replayed.append(item.evidence_id)
    return tuple(replayed)


__all__ = [
    "EVIDENCE_INCLUSION",
    "Derived",
    "DerivedOutcome",
    "Registry",
    "RoleInput",
    "RoleSpec",
    "Rule",
    "RuleRegistrationError",
    "RuleResult",
    "RuleSpec",
    "derive",
    "derive_all",
    "replay_derivations",
]
