"""Replay annotation events into projections, and projections into Examples.

Contract v0.1 sections 4.4, 4.5, and 6.2. Events are the record; a ``Projection`` is the
deterministic replay of a case's events up to ``as_of_event`` as one actor may see them, and
``example_from_projection`` is the one path from events to an ``Example``.

Spans never travel in events: ``mention.create`` and ``evidence.cite`` name a mention or
inclusion ID, which must resolve in the ``spans`` the caller supplies (a store's records).
Every span is re-minted against the authoritative ``Source`` when the projection validates.

Event shapes (``after`` unless stated):

- ``mention.create``: ``target_id`` = mention ID from ``spans``.
- ``reading.create``: ``{"reading": <MentionReading>}``; ``reading.update``: the same with a new
  reading ID, ``target_id`` = the corrected reading.
- ``claim.create``: ``{"claim": <Claim>, "inferences": [...], "inclusions": [IDs],
  "edges": [...]}`` so a claim arrives with its support; ``claim.update``: the same with a new
  claim ID, ``target_id`` = the corrected claim.
- ``claim.set_status`` / ``reading.set_status`` / targeted ``adjudication.resolve``:
  ``{"status": ...}``.
- ``evidence.cite``: ``target_id`` = inclusion ID from ``spans``, or ``{"inference": ...}``.
- ``edge.add``: ``{"edge": ...}``; ``edge.remove``: ``before`` = ``{"edge": ...}``.
- ``outcome.set``: ``{"outcome": <CaseOutcome>, "final_claim_ids": [...]}`` for a variable.
- ``tier.set``: ``{"tier": ...}`` for a variable; with no variable it is the protocol's
  move-on decision: ``role="system"`` and ``{"decision_ref": ...}``.
- untargeted ``adjudication.resolve``: ``{"decision": "accepted" | "unresolved" |
  "excluded"}`` (default accepted); it ends blind review for its round.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, NoReturn, TypeAlias, get_args

from amber._graph_validation import build_context, validate_state
from amber.examples import mint_example
from amber.ids import parse_producer
from amber.schemas.annotations import HUMAN_ROLES, AnnotationEvent, EventRole
from amber.schemas.cases import Case, CaseOutcome, VariableOutcome
from amber.schemas.claims import Claim
from amber.schemas.evidence import EvidenceEdge, Inclusion, InferenceEvidence
from amber.schemas.examples import HUMAN_TIERS, Example, ReferenceTier
from amber.schemas.mentions import Mention, MentionReading
from amber.schemas.sources import Source
from amber.schemas.variables import VariableSet

AnnotationValidationCode: TypeAlias = Literal[
    "invalid_schema",
    "invalid_context",
    "event_order",
    "unknown_reference",
    "invalid_transition",
    "invalid_role",
    "incomplete_projection",
    "excluded_case",
    "unresolved_case",
    "events_mismatch",
]
ALL_STATUSES = frozenset({"proposed", "verified", "gold", "rejected"})
_TIER_RANK = {ReferenceTier.silver: 0, ReferenceTier.single_review: 1, ReferenceTier.adjudicated: 2}
_TIER_ROLES = {
    ReferenceTier.silver: frozenset({"importer", "system"}),
    ReferenceTier.single_review: frozenset({"reviewer"}),
    ReferenceTier.adjudicated: frozenset({"adjudicator"}),
}
# Who may set the outcome of a variable that already has a human tier (amber-fc8.2): the
# roles of that tier and every higher one, so a model or importer never rewrites a human
# reference.
_OUTCOME_ROLES = {
    tier: frozenset().union(
        *(roles for other, roles in _TIER_ROLES.items() if _TIER_RANK[other] >= _TIER_RANK[tier])
    )
    for tier in HUMAN_TIERS
}
_ATTRIBUTION_ACTIONS = frozenset(
    {"review.submit", "adjudication.resolve", "tier.set", "outcome.set", "import.result"}
)


class AnnotationValidationError(ValueError):
    """Stable event or projection diagnostic that does not render event contents."""

    def __init__(self, code: AnnotationValidationCode) -> None:
        # Codes are a closed, documented set; an unknown one is a programming error.
        if code not in get_args(AnnotationValidationCode):
            raise TypeError("unknown AnnotationValidationError code")
        self.code = code
        super().__init__(code)


def _raise(code: AnnotationValidationCode) -> NoReturn:
    raise AnnotationValidationError(code) from None


@dataclass(frozen=True)
class Viewer:
    """Who is looking: an actor, the role they hold, and, for a reviewer, the streams assigned
    to them. The caller that authenticates the actor (a store, a review system) supplies it;
    it is never inferred from events, which any writer can tag with any role or stream
    (amber-fc8.1)."""

    actor: str
    role: EventRole
    streams: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        try:
            parse_producer(self.actor)
        except (TypeError, ValueError):
            _raise("invalid_context")
        if self.role not in get_args(EventRole):
            _raise("invalid_role")
        if not isinstance(self.streams, frozenset) or any(
            type(stream) is not str or not stream.strip() for stream in self.streams
        ):
            _raise("invalid_schema")


@dataclass(frozen=True)
class Projection:
    """Replayed state of a case as of one event, as one viewer may see it.

    It validates under the graph rules but not the Example invariants: work in progress need
    not satisfy them. ``viewer=None`` is the complete view.
    """

    case_id: str
    variable_set_ref: str
    as_of_event: str | None
    viewer: Viewer | None
    claims: Mapping[str, Claim]
    mentions: Mapping[str, Mention]
    readings: Mapping[str, MentionReading]
    evidence: Mapping[str, Inclusion | InferenceEvidence]
    edges: tuple[EvidenceEdge, ...]
    outcomes: Mapping[str, VariableOutcome]
    tiers: Mapping[str, ReferenceTier]
    excluded: bool
    unresolved: bool
    move_on_events: tuple[str, ...]
    events: tuple[AnnotationEvent, ...]


# Visibility --------------------------------------------------------------------------------


def _event(value: Any) -> AnnotationEvent:
    try:
        if isinstance(value, AnnotationEvent):
            value = value.model_dump(warnings=False)
        return AnnotationEvent.model_validate(value)
    except (TypeError, ValueError):
        _raise("invalid_schema")


def _ordered(events: Iterable[Any], case: Case) -> list[AnnotationEvent]:
    ordered = [_event(item) for item in events]
    if any(event.case_id != case.case_id for event in ordered):
        _raise("invalid_context")
    if any(event.provenance.sensitivity is not case.sensitivity for event in ordered):
        _raise("invalid_context")
    pairs = zip(ordered, ordered[1:], strict=False)
    if any(left.event_id >= right.event_id for left, right in pairs):
        _raise("event_order")
    return ordered


def _resolved_rounds(events: Sequence[AnnotationEvent]) -> frozenset[int]:
    return frozenset(
        event.round
        for event in events
        if event.action == "adjudication.resolve" and event.target_id is None
    )


def visible_events(
    events: Sequence[AnnotationEvent], viewer: Viewer | None
) -> tuple[AnnotationEvent, ...]:
    """Apply blind-review visibility (contract v0.1 section 4.4) to an ordered event prefix.

    Before ``adjudication.resolve`` for a round, a reviewer sees their assigned streams and
    stream-less events and never another stream, nor the adjudicator's work in progress,
    which may concern other streams; an adjudicator sees every stream; system and importer
    viewers see everything; after resolve every viewer sees every round's events.
    ``viewer=None`` is the complete view. The role and streams come from ``viewer`` only.
    """
    if viewer is None or viewer.role != "reviewer":
        return tuple(events)
    resolved = _resolved_rounds(events)
    return tuple(
        event
        for event in events
        if event.round in resolved
        or event.stream in viewer.streams
        or (event.stream is None and event.role != "adjudicator")
    )


# Replay ------------------------------------------------------------------------------------


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _raise("invalid_schema")
    return {key: _thaw(item) for key, item in value.items()}


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_thaw(item) for item in value]
    return value


def _after(event: AnnotationEvent, *keys: str, optional: Sequence[str] = ()) -> dict[str, Any]:
    after = _mapping(event.after)
    if not set(keys) <= set(after) or not set(after) <= {*keys, *optional}:
        _raise("invalid_schema")
    return after


class _State:
    def __init__(self, variables: VariableSet, spans: Mapping[str, Inclusion | Mention]) -> None:
        self.variables = variables
        self.spans = spans
        self.claims: dict[str, dict[str, Any]] = {}
        self.evidence: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str | None], dict[str, Any]] = {}
        self.mentions: dict[str, dict[str, Any]] = {}
        self.readings: dict[str, dict[str, Any]] = {}
        self.outcomes: dict[str, VariableOutcome] = {}
        self.tiers: dict[str, ReferenceTier] = {}
        self.excluded = False
        self.unresolved = False
        self.move_on: list[str] = []

    # records

    def _span(self, span_id: str | None, kind: type[Inclusion] | type[Mention]) -> dict[str, Any]:
        record = self.spans.get(span_id) if span_id is not None else None
        if record is None:
            _raise("unknown_reference")
        if not isinstance(record, kind):
            _raise("invalid_context")
        return record.model_dump(mode="json")

    def _new_record(
        self, event: AnnotationEvent, record: Any, key: str, store: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        payload = _mapping(record)
        identifier = payload.get(key)
        if type(identifier) is not str or identifier in store:
            _raise("invalid_transition")
        if payload.get("status") != "proposed":
            _raise("invalid_transition")
        provenance = _mapping(payload.get("provenance"))
        if event.role in HUMAN_ROLES and provenance.get("producer") != event.actor:
            _raise("invalid_role")
        store[identifier] = payload
        return payload

    def _edge(self, value: Any) -> tuple[tuple[str, str, str | None], dict[str, Any]]:
        try:
            edge = EvidenceEdge.model_validate(_mapping(value))
        except (TypeError, ValueError):
            _raise("invalid_schema")
        return (edge.claim_id, edge.evidence_id, edge.role), edge.model_dump(mode="json")

    def _claim_bundle(self, event: AnnotationEvent) -> str:
        after = _after(event, "claim", optional=("inferences", "inclusions", "edges"))
        for inclusion_id in after.get("inclusions", []):
            self.evidence.setdefault(inclusion_id, self._span(inclusion_id, Inclusion))
        for inference in after.get("inferences", []):
            self._cite_inference(inference)
        claim = self._new_record(event, after["claim"], "claim_id", self.claims)
        for value in after.get("edges", []):
            key, edge = self._edge(value)
            if key[0] != claim["claim_id"] or key in self.edges:
                _raise("invalid_context")
            self.edges[key] = edge
        return str(claim["claim_id"])

    def _cite_inference(self, value: Any) -> None:
        record = _mapping(value)
        identifier = record.get("evidence_id")
        if record.get("kind") != "inference" or type(identifier) is not str:
            _raise("invalid_schema")
        if identifier in self.evidence:
            _raise("invalid_transition")
        self.evidence[identifier] = record

    def _supersede(self, store: dict[str, dict[str, Any]], target: str | None) -> None:
        record = store.get(target) if target is not None else None
        if record is None:
            _raise("unknown_reference")
        # A correction never edits gold in place; only adjudication can reject gold.
        if record["status"] in ("proposed", "verified"):
            record["status"] = "rejected"

    def _readings_reviewed(self, claim: Mapping[str, Any]) -> bool:
        reading_id = claim.get("reading_id")
        if reading_id is None:
            return True
        reading = self.readings.get(reading_id)
        return reading is not None and reading["status"] in ("gold", "verified")

    def _set_status(
        self, event: AnnotationEvent, store: dict[str, dict[str, Any]], adjudicating: bool
    ) -> None:
        record = store.get(event.target_id) if event.target_id is not None else None
        if record is None:
            _raise("unknown_reference")
        status = _after(event, "status")["status"]
        current = record["status"]
        if adjudicating:
            permitted = {
                ("proposed", "gold"),
                ("verified", "gold"),
                ("proposed", "rejected"),
                ("verified", "rejected"),
                ("gold", "rejected"),
            }
            if (current, status) == ("gold", "rejected") and event.reason is None:
                _raise("invalid_transition")
        else:
            permitted = {("proposed", "verified"), ("proposed", "rejected")}
        if (current, status) not in permitted:
            _raise("invalid_transition")
        if status == "gold" and store is self.claims and not self._readings_reviewed(record):
            _raise("invalid_transition")
        record["status"] = status

    # actions

    def apply(self, event: AnnotationEvent) -> None:
        action = event.action
        if event.variable is not None and event.variable not in self.variables.variables:
            _raise("invalid_context")
        if action == "mention.create":
            if event.target_id not in self.mentions:
                self.mentions[str(event.target_id)] = self._span(event.target_id, Mention)
        elif action in ("reading.create", "reading.update"):
            reading = _mapping(_after(event, "reading")["reading"])
            if reading.get("mention_id") not in self.mentions:
                _raise("unknown_reference")
            if action == "reading.update":
                old = self.readings.get(event.target_id) if event.target_id else None
                if old is None:
                    _raise("unknown_reference")
                if old["mention_id"] != reading["mention_id"]:
                    _raise("invalid_transition")
                self._supersede_reading(str(event.target_id))
            self._new_record(event, reading, "reading_id", self.readings)
        elif action == "reading.set_status":
            self._require_role(event, HUMAN_ROLES)
            self._set_status(event, self.readings, adjudicating=False)
        elif action == "claim.create":
            self._claim_bundle(event)
        elif action == "claim.update":
            self._supersede(self.claims, event.target_id)
            self._claim_bundle(event)
        elif action == "claim.set_status":
            self._require_role(event, HUMAN_ROLES)
            self._set_status(event, self.claims, adjudicating=False)
        elif action == "evidence.cite":
            if event.after is None:
                self.evidence.setdefault(
                    str(event.target_id), self._span(event.target_id, Inclusion)
                )
            else:
                self._cite_inference(_after(event, "inference")["inference"])
        elif action == "edge.add":
            self._add_edge(event)
        elif action == "edge.remove":
            self._remove_edge(event)
        elif action == "outcome.set":
            self._set_outcome(event)
        elif action == "tier.set":
            self._set_tier(event)
        elif action == "adjudication.resolve":
            self._require_role(event, {"adjudicator"})
            if event.target_id is None:
                self._resolve(event)
            elif event.target_id in self.claims:
                self._set_status(event, self.claims, adjudicating=True)
            else:
                self._set_status(event, self.readings, adjudicating=True)
        elif action in ("review.open", "review.submit", "review.reopen"):
            self._require_role(event, {"reviewer"})
        elif action == "import.result":
            self._require_role(event, {"importer"})

    @staticmethod
    def _require_role(event: AnnotationEvent, roles: Iterable[str]) -> None:
        if event.role not in roles:
            _raise("invalid_role")

    def _final_variable(self, event: AnnotationEvent) -> str:
        name = event.variable
        if name is None or not self.variables.variables[name].final:
            _raise("invalid_context")
        return name

    def _require_editable_support(self, claim_id: str) -> None:
        """Gold is never edited in place (contract v0.1 section 4.5): its support is fixed;
        adjudication rejects it and a corrected claim replaces it."""
        claim = self.claims.get(claim_id)
        if claim is not None and claim["status"] == "gold":
            _raise("invalid_transition")

    def _supersede_reading(self, reading_id: str) -> None:
        """A reading a gold claim rests on is part of that gold, so it is never superseded."""
        if any(
            claim["status"] == "gold" and claim.get("reading_id") == reading_id
            for claim in self.claims.values()
        ):
            _raise("invalid_transition")
        self._supersede(self.readings, reading_id)

    def _add_edge(self, event: AnnotationEvent) -> None:
        key, edge = self._edge(_after(event, "edge")["edge"])
        self._require_editable_support(key[0])
        if key in self.edges:
            _raise("invalid_transition")
        self.edges[key] = edge

    def _remove_edge(self, event: AnnotationEvent) -> None:
        before = _mapping(event.before)
        if set(before) != {"edge"}:
            _raise("invalid_schema")
        key, _ = self._edge(before["edge"])
        self._require_editable_support(key[0])
        if self.edges.pop(key, None) is None:
            _raise("unknown_reference")

    def _set_outcome(self, event: AnnotationEvent) -> None:
        name = self._final_variable(event)
        tier = self.tiers.get(name)
        if tier in _OUTCOME_ROLES and event.role not in _OUTCOME_ROLES[tier]:
            _raise("invalid_role")
        after = _after(event, "outcome", "final_claim_ids")
        try:
            outcome = VariableOutcome(
                variable=name,
                outcome=CaseOutcome.model_validate(after["outcome"]),
                final_claim_ids=after["final_claim_ids"],
            )
        except (TypeError, ValueError):
            _raise("invalid_schema")
        if any(claim_id not in self.claims for claim_id in outcome.final_claim_ids):
            _raise("unknown_reference")
        self.outcomes[name] = outcome

    def _set_tier(self, event: AnnotationEvent) -> None:
        if event.variable is None:
            after = _mapping(event.after)
            reference = after.get("decision_ref")
            if event.role != "system" or type(reference) is not str or not reference.strip():
                _raise("invalid_role")
            self.move_on.append(event.event_id)
            return
        name = self._final_variable(event)
        try:
            tier = ReferenceTier(_after(event, "tier")["tier"])
        except ValueError:
            _raise("invalid_schema")
        if event.role not in _TIER_ROLES[tier]:
            _raise("invalid_role")
        # An unresolved adjudication is no adjudicated reference until a later accepted resolve
        # (contract v0.1 section 4.7: unresolved cases give no Example).
        if tier is ReferenceTier.adjudicated and self.unresolved:
            _raise("invalid_transition")
        current = self.tiers.get(name)
        if current is not None and _TIER_RANK[tier] <= _TIER_RANK[current]:
            _raise("invalid_transition")
        if tier in HUMAN_TIERS:
            self._promote_finals(name)
        self.tiers[name] = tier

    def _promote_finals(self, name: str) -> None:
        """A human tier makes the variable's final claims gold; each must be reviewable."""
        outcome = self.outcomes.get(name)
        if outcome is None:
            _raise("invalid_transition")
        for claim_id in outcome.final_claim_ids:
            claim = self.claims[claim_id]
            if claim["status"] == "rejected" or not self._readings_reviewed(claim):
                _raise("invalid_transition")
            claim["status"] = "gold"

    def _resolve(self, event: AnnotationEvent) -> None:
        decision = _mapping(event.after or {}).get("decision", "accepted")
        if decision not in ("accepted", "unresolved", "excluded"):
            _raise("invalid_schema")
        if decision != "accepted" and event.reason is None:
            _raise("invalid_schema")
        if decision == "excluded":
            self.excluded = True
        if decision == "unresolved":
            self.unresolved = True
        elif decision == "accepted":
            self.unresolved = False


def replay(
    events: Iterable[AnnotationEvent | Mapping[str, Any]],
    *,
    case: Case,
    source: Source,
    variables: VariableSet,
    spans: Iterable[Inclusion | Mention] = (),
    viewer: Viewer | None = None,
    as_of_event: str | None = None,
    excluded_spans: Sequence[tuple[int, int]] = (),
) -> Projection:
    """Deterministically replay a case's events up to ``as_of_event`` (default: the latest).

    Events must be in ``event_id`` order. ``spans`` resolves the mention and inclusion IDs that
    events name. The projection validates under the graph rules; any status may be retained.
    """
    if not isinstance(case, Case) or not isinstance(variables, VariableSet):
        _raise("invalid_context")
    if case.task != variables.name:
        _raise("invalid_context")
    ordered = _ordered(events, case)
    if as_of_event is not None:
        if as_of_event not in {event.event_id for event in ordered}:
            _raise("unknown_reference")
        ordered = [event for event in ordered if event.event_id <= as_of_event]
    if viewer is not None and not isinstance(viewer, Viewer):
        _raise("invalid_context")
    shown = visible_events(ordered, viewer)
    pool: dict[str, Inclusion | Mention] = {}
    for span in spans:
        if isinstance(span, Inclusion):
            pool[span.evidence_id] = span
        elif isinstance(span, Mention):
            pool[span.mention_id] = span
        else:
            _raise("invalid_schema")

    state = _State(variables, pool)
    for event in shown:
        state.apply(event)

    read = {reading["mention_id"] for reading in state.readings.values()}
    context = build_context(
        source=source,
        variables=variables,
        sensitivity=case.sensitivity,
        excluded_spans=excluded_spans,
    )
    validated = validate_state(
        context,
        claims=list(state.claims.values()),
        evidence=list(state.evidence.values()),
        edges=list(state.edges.values()),
        mentions=[record for key, record in state.mentions.items() if key in read],
        readings=list(state.readings.values()),
        statuses=ALL_STATUSES,
    )
    return Projection(
        case_id=case.case_id,
        variable_set_ref=variables.schema_ref,
        as_of_event=ordered[-1].event_id if ordered else None,
        viewer=viewer,
        claims=validated.claims,
        mentions=validated.mentions,
        readings=validated.readings,
        evidence=validated.evidence,
        edges=tuple(
            sorted(
                validated.edges, key=lambda edge: (edge.claim_id, edge.evidence_id, edge.role or "")
            )
        ),
        outcomes=MappingProxyType(dict(state.outcomes)),
        tiers=MappingProxyType(dict(state.tiers)),
        excluded=state.excluded,
        unresolved=state.unresolved,
        move_on_events=tuple(state.move_on),
        events=tuple(shown),
    )


# Examples ----------------------------------------------------------------------------------


def _relevant(event: AnnotationEvent, variable: str) -> bool:
    return event.variable in (None, variable) and (
        event.action != "adjudication.resolve" or event.target_id is None
    )


def _reference(projection: Projection, variable: str) -> dict[str, Any]:
    outcome = projection.outcomes.get(variable)
    tier = projection.tiers.get(variable)
    if outcome is None or tier is None:
        _raise("incomplete_projection")
    events = [event for event in projection.events if _relevant(event, variable)]
    if tier in HUMAN_TIERS:
        annotators = list(
            dict.fromkeys(event.actor for event in events if event.action == "review.submit")
        )
        resolves = [event for event in events if event.action == "adjudication.resolve"]
        adjudicator = resolves[-1].actor if tier is ReferenceTier.adjudicated and resolves else None
    else:
        producers = [
            projection.claims[claim_id].provenance.producer for claim_id in outcome.final_claim_ids
        ]
        annotators = list(dict.fromkeys(producers or [outcome.outcome.provenance.producer]))
        adjudicator = None
    return {
        "variable": variable,
        "tier": tier.value,
        "outcome": outcome.outcome.model_dump(mode="json"),
        "final_claim_ids": list(outcome.final_claim_ids),
        "annotators": annotators,
        "adjudicator": adjudicator,
        "derived_from_events": sorted(
            event.event_id for event in events if event.action in _ATTRIBUTION_ACTIONS
        ),
    }


def example_from_projection(
    projection: Projection,
    *,
    case: Case,
    source: Source,
    variables: VariableSet,
    excluded_spans: Sequence[tuple[int, int]] = (),
) -> Example:
    """Return the Example a complete projection is, or raise naming the failing invariant.

    Rejected claims and readings stay in the event history but are not retained, nor are
    inferences built only on them; a span is retained while it has a retained reading.
    ``created_at`` is the time of ``as_of_event``, so the same events always give the same
    Example.
    """
    if not isinstance(projection, Projection) or projection.viewer is not None:
        _raise("invalid_context")
    if projection.case_id != case.case_id or projection.variable_set_ref != variables.schema_ref:
        _raise("invalid_context")
    if projection.excluded:
        _raise("excluded_case")
    if projection.unresolved:
        _raise("unresolved_case")
    if projection.as_of_event is None:
        _raise("incomplete_projection")
    finals = sorted(name for name, variable in variables.variables.items() if variable.final)
    references = {name: _reference(projection, name) for name in finals}
    claims = {key: claim for key, claim in projection.claims.items() if claim.status != "rejected"}
    readings = [reading for reading in projection.readings.values() if reading.status != "rejected"]
    evidence = dict(projection.evidence)
    changed = True
    while changed:
        dropped = [
            key
            for key, record in evidence.items()
            if isinstance(record, InferenceEvidence)
            and any(item not in claims and item not in evidence for item in record.inputs)
        ]
        changed = bool(dropped)
        for key in dropped:
            del evidence[key]
    read = {reading.mention_id for reading in readings}
    payload = {
        "example_id": None,
        "schema_version": "amber/0.1",
        "case_id": case.case_id,
        "variable_set": variables.name,
        "variable_set_version": variables.version,
        "variable_set_ref": variables.schema_ref,
        "as_of_event": projection.as_of_event,
        "source_ids": list(case.source_ids),
        "split": case.split,
        "references": references,
        "claims": [claim.model_dump(mode="json") for claim in claims.values()],
        "mentions": [
            mention.model_dump(mode="json")
            for key, mention in projection.mentions.items()
            if key in read
        ],
        "mention_readings": [reading.model_dump(mode="json") for reading in readings],
        "evidence": [record.model_dump(mode="json") for record in evidence.values()],
        "edges": [
            edge.model_dump(mode="json") for edge in projection.edges if edge.claim_id in claims
        ],
        "created_at": projection.events[-1].at.isoformat(),
    }
    return mint_example(
        payload, case=case, source=source, variables=variables, excluded_spans=excluded_spans
    )


def verify_example(
    example: Example,
    events: Iterable[AnnotationEvent | Mapping[str, Any]],
    *,
    case: Case,
    source: Source,
    variables: VariableSet,
    spans: Iterable[Inclusion | Mention] = (),
    excluded_spans: Sequence[tuple[int, int]] = (),
) -> Example:
    """Require an Example to be exactly the replay of its events up to its ``as_of_event``.

    A snapshot asserting a tier, status, or answer the events do not support is
    ``events_mismatch``.
    """
    if not isinstance(example, Example):
        _raise("invalid_schema")
    try:
        projection = replay(
            events,
            case=case,
            source=source,
            variables=variables,
            spans=spans,
            as_of_event=example.as_of_event,
            excluded_spans=excluded_spans,
        )
    except AnnotationValidationError as error:
        if error.code == "unknown_reference":
            _raise("events_mismatch")
        raise
    replayed = example_from_projection(
        projection, case=case, source=source, variables=variables, excluded_spans=excluded_spans
    )
    if replayed != example:
        _raise("events_mismatch")
    return replayed


__all__ = [
    "AnnotationValidationCode",
    "AnnotationValidationError",
    "Projection",
    "Viewer",
    "example_from_projection",
    "replay",
    "verify_example",
    "visible_events",
]
