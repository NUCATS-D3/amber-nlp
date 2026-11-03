"""Declared data-handling policy: destinations, restrictions, and the transfer check.

Policy is pure data and pure functions (contract v0.1 §1). It performs no I/O, holds no
credentials, and never reads the clock: enforcement points supply ``now`` and are the only
places that move data. Sensitivity and zones are declared, never inferred from content.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from amber.ids import canonical_sha256, validate_ulid
from amber.schemas._immutable import FrozenMapping
from amber.schemas._wire import SCHEMA_VERSION, SchemaVersion, Timestamp
from amber.schemas.provenance import Provenance, Sensitivity, Zone

PayloadKind: TypeAlias = Literal[
    "source_text", "derived_text", "labels_only", "metrics", "model_weights"
]
DestinationKind: TypeAlias = Literal[
    "model",
    "structured_source",
    "tracking",
    "artifact_store",
    "judge",
    "review",
    "export",
    "training",
]
ProviderCapability: TypeAlias = Literal[
    "schema_constrained", "tools", "logprobs", "batch", "adapters"
]
RestrictionKind: TypeAlias = Literal["license", "dua", "irb", "contract", "internal"]
DecisionReason: TypeAlias = Literal[
    "ok",
    "zone_not_allowed",
    "payload_not_accepted",
    "restriction_zone",
    "restriction_kind",
    "restriction_training",
    "restriction_tag",
    "restriction_payload",
    "restriction_expired",
    "unknown_sensitivity",
    "policy_version_mismatch",
]

SENSITIVITY_ORDER: tuple[Sensitivity, ...] = (
    Sensitivity.synthetic,
    Sensitivity.deidentified,
    Sensitivity.limited,
    Sensitivity.phi,
)

DEFAULT_ALLOWED: Mapping[Sensitivity, frozenset[Zone]] = FrozenMapping(
    {
        Sensitivity.synthetic: frozenset(Zone),
        Sensitivity.deidentified: frozenset({Zone.local, Zone.institution, Zone.external_baa}),
        Sensitivity.limited: frozenset({Zone.local, Zone.institution}),
        Sensitivity.phi: frozenset({Zone.local, Zone.institution}),
    }
)

_Name = Annotated[str, Field(strict=True, min_length=1)]
_FROZEN = ConfigDict(extra="forbid", frozen=True)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value


class Destination(BaseModel):
    """Anything data can be sent to, with its declared zone and obligations (§1.3)."""

    model_config = _FROZEN

    name: _Name
    kind: DestinationKind
    zone: Zone
    accepts: frozenset[PayloadKind]
    retains: bool = True
    trains_on_inputs: bool = False
    tags: frozenset[str] = frozenset()

    @field_validator("name")
    @classmethod
    def require_nonblank_name(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("tags")
    @classmethod
    def require_nonblank_tags(cls, value: frozenset[str]) -> frozenset[str]:
        for tag in value:
            _nonblank(tag)
        return value

    @model_validator(mode="after")
    def require_local_zone_for_local_names(self) -> Destination:
        if self.name.startswith("local:") and self.zone is not Zone.local:
            raise ValueError("a local: destination must be zone local")
        return self


class ProviderDestination(Destination):
    """A configured model or structured-source endpoint and its capabilities."""

    capabilities: frozenset[ProviderCapability] = frozenset()
    model: str | None = None

    @model_validator(mode="after")
    def require_provider_kind(self) -> ProviderDestination:
        if self.kind not in ("model", "structured_source"):
            raise ValueError("a provider destination is a model or a structured source")
        if self.kind == "model" and not (self.model and self.model.strip()):
            raise ValueError("a model destination names its model")
        return self


class Restriction(BaseModel):
    """A declared obligation that comes with a dataset, beyond the zone matrix."""

    model_config = _FROZEN

    id: _Name
    kind: RestrictionKind
    forbids_zones: frozenset[Zone] = frozenset()
    forbids_kinds: frozenset[DestinationKind] = frozenset()
    forbids_training_on_inputs: bool = False
    requires_tags: frozenset[str] = frozenset()
    forbids_payloads: frozenset[PayloadKind] = frozenset()
    valid_until: Timestamp | None = None
    note: _Name

    @field_validator("id", "note")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("valid_until")
    @classmethod
    def require_aware_expiry(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)


class DataPolicy(BaseModel):
    """A dataset's declared sensitivity and restrictions."""

    model_config = _FROZEN

    sensitivity: Sensitivity
    restrictions: tuple[Restriction, ...] = ()

    @field_validator("restrictions")
    @classmethod
    def require_distinct_restrictions(
        cls, value: tuple[Restriction, ...]
    ) -> tuple[Restriction, ...]:
        ids = [restriction.id for restriction in value]
        if len(ids) != len(set(ids)):
            raise ValueError("restriction IDs must be distinct")
        return value

    @property
    def restriction_ids(self) -> tuple[str, ...]:
        return tuple(restriction.id for restriction in self.restrictions)


class PolicyMatrix(BaseModel):
    """A deployment's permitted zones per sensitivity; may narrow the default freely."""

    model_config = _FROZEN

    version: _Name
    allowed: Mapping[Sensitivity, frozenset[Zone]]
    widened_from_default: bool = False
    note: str | None = None

    @field_validator("version")
    @classmethod
    def require_nonblank_version(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("allowed")
    @classmethod
    def freeze_allowed(
        cls, value: Mapping[Sensitivity, frozenset[Zone]]
    ) -> Mapping[Sensitivity, frozenset[Zone]]:
        return FrozenMapping(value)

    @model_validator(mode="after")
    def require_recorded_widening(self) -> PolicyMatrix:
        widened = any(
            zones - DEFAULT_ALLOWED[sensitivity] for sensitivity, zones in self.allowed.items()
        )
        if widened and not self.widened_from_default:
            raise ValueError("a matrix wider than the default needs widened_from_default")
        if self.widened_from_default and not (self.note and self.note.strip()):
            raise ValueError("widened_from_default requires a note")
        return self


DEFAULT_MATRIX = PolicyMatrix(version="amber-default@0.1", allowed=DEFAULT_ALLOWED)


class RunContext(BaseModel):
    """What a runner supplies to everything it calls (§6.2)."""

    model_config = _FROZEN

    run_id: str
    policy_version: _Name
    data: DataPolicy
    dataset: _Name
    dataset_manifest_hash: _Name

    @field_validator("run_id")
    @classmethod
    def require_ulid(cls, value: str) -> str:
        return validate_ulid(value)

    @field_validator("policy_version", "dataset", "dataset_manifest_hash")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        return _nonblank(value)


_DECISION_FIELDS = (
    "permitted",
    "reason",
    "restriction_id",
    "policy_version",
    "destination",
    "destination_kind",
    "zone",
    "sensitivity",
    "payload",
    "dataset",
    "run_id",
    "case_id",
    "checked_at",
)


def _decision_id(fields: Mapping[str, Any]) -> str:
    return canonical_sha256({name: fields[name] for name in _DECISION_FIELDS})


class Decision(BaseModel):
    """The outcome of one check; also the audit record. Contains no source text."""

    model_config = _FROZEN

    decision_id: str
    permitted: bool
    reason: DecisionReason
    restriction_id: str | None
    policy_version: str
    destination: str
    destination_kind: DestinationKind
    zone: Zone
    sensitivity: Sensitivity
    payload: PayloadKind
    dataset: str | None
    run_id: str | None
    case_id: str | None
    checked_at: Timestamp
    schema_version: SchemaVersion = SCHEMA_VERSION

    @field_validator("checked_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def require_consistent_record(self) -> Decision:
        if self.permitted != (self.reason == "ok"):
            raise ValueError("a decision is permitted exactly when its reason is ok")
        if (self.restriction_id is not None) != self.reason.startswith("restriction_"):
            raise ValueError("restriction_id is set exactly for restriction refusals")
        if self.decision_id != _decision_id(self.__dict__):
            raise ValueError("decision_id does not match the decision")
        return self


class PolicyViolation(Exception):
    """Raised by enforcement points, never by ``check``; carries the refusal.

    ``decision`` is the first refusal; ``decisions`` holds every decision made for the
    transfer, permitted or refused, when one transfer needed several (an export's files).
    """

    def __init__(self, decision: Decision, decisions: Sequence[Decision] = ()) -> None:
        self.decision = decision
        self.decisions = tuple(decisions) or (decision,)
        super().__init__(
            f"policy refused {decision.payload} to {decision.destination}: {decision.reason}"
        )


def _restriction_refusal(
    restriction: Restriction, destination: Destination, payload: PayloadKind, now: datetime
) -> DecisionReason | None:
    if restriction.valid_until is not None and now > restriction.valid_until:
        return "restriction_expired"
    if destination.zone in restriction.forbids_zones:
        return "restriction_zone"
    if destination.kind in restriction.forbids_kinds:
        return "restriction_kind"
    if restriction.forbids_training_on_inputs and destination.trains_on_inputs:
        return "restriction_training"
    if not restriction.requires_tags <= destination.tags:
        return "restriction_tag"
    if payload in restriction.forbids_payloads:
        return "restriction_payload"
    return None


def check(
    matrix: PolicyMatrix,
    data: DataPolicy,
    destination: Destination,
    payload: PayloadKind,
    *,
    now: datetime,
    context: RunContext | None = None,
    case_id: str | None = None,
) -> Decision:
    """Decide whether ``payload`` from ``data`` may go to ``destination`` (§1.4).

    Total and deterministic: a refusal is returned, never raised. Rules apply in order and the
    first failure is the reason.
    """
    _aware(now)
    reason: DecisionReason = "ok"
    restriction_id: str | None = None
    if context is not None and context.policy_version != matrix.version:
        reason = "policy_version_mismatch"
    elif data.sensitivity not in matrix.allowed:
        reason = "unknown_sensitivity"
    elif destination.zone not in matrix.allowed[data.sensitivity]:
        reason = "zone_not_allowed"
    elif payload not in destination.accepts:
        reason = "payload_not_accepted"
    else:
        for restriction in data.restrictions:
            refusal = _restriction_refusal(restriction, destination, payload, now)
            if refusal is not None:
                reason, restriction_id = refusal, restriction.id
                break
    fields: dict[str, Any] = {
        "permitted": reason == "ok",
        "reason": reason,
        "restriction_id": restriction_id,
        "policy_version": matrix.version,
        "destination": destination.name,
        "destination_kind": destination.kind,
        "zone": destination.zone,
        "sensitivity": data.sensitivity,
        "payload": payload,
        "dataset": None if context is None else context.dataset,
        "run_id": None if context is None else context.run_id,
        "case_id": case_id,
        "checked_at": now,
    }
    return Decision(decision_id=_decision_id(fields), **fields)


def require(
    matrix: PolicyMatrix,
    data: DataPolicy,
    destination: Destination,
    payload: PayloadKind,
    *,
    now: datetime,
    context: RunContext | None = None,
    case_id: str | None = None,
) -> Decision:
    """Return a permitting decision, or raise ``PolicyViolation`` carrying the refusal."""
    decision = check(matrix, data, destination, payload, now=now, context=context, case_id=case_id)
    if not decision.permitted:
        raise PolicyViolation(decision)
    return decision


class StaticGate:
    """The reference ``PolicyGate``: ``check`` and ``require`` against one fixed matrix."""

    def __init__(self, matrix: PolicyMatrix) -> None:
        if not isinstance(matrix, PolicyMatrix):
            raise TypeError("StaticGate needs a PolicyMatrix")
        self.matrix = matrix

    def check(
        self,
        data: DataPolicy,
        destination: Destination,
        payload: PayloadKind,
        *,
        now: datetime,
        context: RunContext | None = None,
        case_id: str | None = None,
    ) -> Decision:
        return check(
            self.matrix, data, destination, payload, now=now, context=context, case_id=case_id
        )

    def require(
        self,
        data: DataPolicy,
        destination: Destination,
        payload: PayloadKind,
        *,
        now: datetime,
        context: RunContext | None = None,
        case_id: str | None = None,
    ) -> Decision:
        return require(
            self.matrix, data, destination, payload, now=now, context=context, case_id=case_id
        )


def combine(policies: Sequence[DataPolicy]) -> DataPolicy:
    """The policy of data drawn from several datasets: highest sensitivity, every restriction.

    Restrictions keep input order with duplicate IDs removed; two restrictions sharing an ID
    with different content are an error, never a silent choice.
    """
    if not policies:
        raise ValueError("combine needs at least one policy")
    sensitivity = max((policy.sensitivity for policy in policies), key=SENSITIVITY_ORDER.index)
    seen: dict[str, Restriction] = {}
    for policy in policies:
        for restriction in policy.restrictions:
            previous = seen.setdefault(restriction.id, restriction)
            if previous != restriction:
                raise ValueError(f"conflicting definitions of restriction {restriction.id}")
    return DataPolicy(sensitivity=sensitivity, restrictions=tuple(seen.values()))


def stamp_provenance(
    decision: Decision,
    data: DataPolicy,
    *,
    producer: str,
    created_at: datetime,
    version: str,
    model: str | None = None,
    prompt_versions: Mapping[str, str] | None = None,
    trace_id: str | None = None,
) -> Provenance:
    """Provenance for a node produced through a permitted transfer (§1.5 step 3, §1.6)."""
    if not decision.permitted:
        raise PolicyViolation(decision)
    if data.sensitivity is not decision.sensitivity:
        raise ValueError("the data policy does not match the decision's sensitivity")
    return Provenance(
        producer=producer,
        model=model,
        prompt_versions=dict(prompt_versions or {}),
        run_id=decision.run_id,
        trace_id=trace_id,
        destination=decision.destination,
        policy_version=decision.policy_version,
        restriction_ids=data.restriction_ids,
        zone=decision.zone,
        sensitivity=decision.sensitivity,
        created_at=created_at,
        version=version,
    )
