"""Derive conservative, non-authoritative current-progression review candidates.

This module does not mint evidence, claims, outcomes, or gold examples. It only reduces
CORAL annotations into deterministic review aids after independently checking the parser
inventory, source intervals, copied surfaces, and explicit skip boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from experiments.coral.brat import Document, Entity

Direction = Literal["positive", "negative", "review"]
Classification = Literal["positive", "negative", "review", "ignore"]
Disposition = Literal["answered", "not_mentioned", "conflicting_evidence", "insufficient_evidence"]

_RELEVANT_ENTITY_TYPES = frozenset({"DiseaseState", "DiseaseProgression", "Remission", "Hospice"})
_LEGACY_ENTITY_TYPES = frozenset({"DiseaseProgression", "Remission", "Hospice"})
_DISEASE_STATE_VALUES = frozenset(
    {"remission", "progression-recurrence", "progression-others", "stability", "hospice", "others"}
)
_MODALITIES = frozenset(
    {
        "negated",
        "affirmed",
        "uncertain_in_present",
        "uncertain_in_past",
        "planned_in_future",
        "hypothetical_in_future",
    }
)
_EXPERIENCERS = frozenset({"patient", "family", "others"})
_CANDIDATE_ATTRIBUTE_TYPES = frozenset(
    {
        "DiseaseStateVal",
        "ExperiencerVal",
        "HistoryVal",
        "NegationModalityVal",
        "SectionSkipType",
    }
)
_SECTION_SKIP_VALUES = frozenset(
    {
        "physical_exam",
        "medications",
        "laboratory",
        "allergies",
        "radiology_report",
        "pathology_report",
        "copy_forward",
        "others",
    }
)
_TEMPORAL_RELATIONS = frozenset(
    {
        "HappensAtOnDuring",
        "BeginsOnOrAt",
        "EndsOnOrAt",
        "HappensBefore",
        "HappensAfter",
        "HappensOverlapping",
        "Temporal",
    }
)
_BLOCKING_FLAGS = frozenset(
    {
        "discontinuous_span",
        "incomplete_annotation_inventory",
        "incomplete_scope",
        "invalid_attribute_target",
        "invalid_attribute_value",
        "malformed_span",
        "missing_disease_state",
        "skipped_overlap",
        "source_surface_mismatch",
    }
)
_BASE_FLAGS = frozenset({"clinical_review_required", "non_authoritative"})


@dataclass(frozen=True)
class CandidateSignal:
    ann_id: str
    direction: Direction
    reason: str


@dataclass(frozen=True)
class CurrentProgressionCandidate:
    doc_id: str
    disposition: Disposition
    value: bool | None
    signals: tuple[CandidateSignal, ...]
    flags: tuple[str, ...]
    annotation_inventory_complete: bool
    clinical_scope_reviewed: Literal[False] = False

    def __post_init__(self) -> None:
        if self.disposition == "answered":
            assert type(self.value) is bool
        else:
            assert self.value is None
        assert self.clinical_scope_reviewed is False
        assert self.annotation_inventory_complete or self.disposition == "insufficient_evidence"


def _attributes_by_target(doc: Document) -> dict[str, dict[str, str | None]]:
    """Index attributes only after the caller has established an unambiguous inventory."""
    indexed: dict[str, dict[str, str | None]] = {}
    for attribute in doc.attributes:
        indexed.setdefault(attribute.target, {})[attribute.type] = attribute.value
    return indexed


def _valid_intervals(entity: Entity, text_length: int) -> bool:
    """Return whether all fragments are ordered, nonempty, and inside the source."""
    if not entity.spans:
        return False
    previous_end = -1
    for start, end in entity.spans:
        if not (0 <= start < end <= text_length) or start < previous_end:
            return False
        previous_end = end
    return True


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    """Use half-open interval overlap; touching boundaries do not overlap."""
    left_start, left_end = left
    right_start, right_end = right
    return left_start < right_end and right_start < left_end


def _surface_matches(doc: Document, entity: Entity) -> bool:
    if not _valid_intervals(entity, len(doc.text)):
        return False
    separator = " " if len(entity.spans) > 1 else ""
    source_surface = separator.join(doc.text[start:end] for start, end in entity.spans)
    return entity.quote == source_surface


def _skip_intervals(doc: Document) -> tuple[tuple[tuple[int, int], ...], bool]:
    """Return separate valid skip fragments and whether every skip boundary is trustworthy."""
    intervals: list[tuple[int, int]] = []
    scope_complete = True
    for entity in doc.entities.values():
        if entity.type != "SectionSkip":
            continue
        if not _valid_intervals(entity, len(doc.text)):
            scope_complete = False
            continue
        intervals.extend(entity.spans)
        if not _surface_matches(doc, entity):
            scope_complete = False
    return tuple(intervals), scope_complete


def _classify_entity(
    entity: Entity, attributes: dict[str, str | None]
) -> tuple[Classification, str, tuple[str, ...]]:
    """Classify one structurally safe task-relevant entity without reading source text."""
    if entity.type not in _RELEVANT_ENTITY_TYPES:
        return "ignore", "irrelevant_entity", ()

    invalid_flags: set[str] = set()
    allowed_attributes = (
        {"DiseaseStateVal", "ExperiencerVal", "NegationModalityVal"}
        if entity.type == "DiseaseState"
        else {"ExperiencerVal", "NegationModalityVal"}
    )
    if set(attributes) - allowed_attributes:
        invalid_flags.add("invalid_attribute_target")
    if "HistoryVal" in attributes:
        invalid_flags.add("invalid_attribute_target")
    modality = attributes.get("NegationModalityVal")
    if "NegationModalityVal" in attributes and modality not in _MODALITIES:
        invalid_flags.add("invalid_attribute_value")
    experiencer = attributes.get("ExperiencerVal")
    if "ExperiencerVal" in attributes and experiencer not in _EXPERIENCERS:
        invalid_flags.add("invalid_attribute_value")

    disease_state = attributes.get("DiseaseStateVal")
    if entity.type == "DiseaseState":
        if disease_state is None:
            invalid_flags.add("missing_disease_state")
        elif disease_state not in _DISEASE_STATE_VALUES:
            invalid_flags.add("invalid_attribute_value")

    semantic_flags: set[str] = set()
    if entity.type in _LEGACY_ENTITY_TYPES:
        semantic_flags.add("unsupported_entity_type")
    if experiencer in {"family", "others"}:
        semantic_flags.add("non_patient_experiencer")
    if modality == "uncertain_in_past":
        semantic_flags.update({"historical_signal", "uncertain_signal"})
    elif modality == "uncertain_in_present":
        semantic_flags.add("uncertain_signal")
    if modality in {"planned_in_future", "hypothetical_in_future"}:
        semantic_flags.add("future_signal")
    if disease_state in {"hospice", "progression-others", "others"}:
        semantic_flags.add("unsupported_disease_state")

    review_flags = invalid_flags | semantic_flags
    if review_flags:
        reason = next(
            code
            for code in (
                "invalid_attribute_target",
                "invalid_attribute_value",
                "missing_disease_state",
                "unsupported_entity_type",
                "non_patient_experiencer",
                "historical_signal",
                "uncertain_signal",
                "future_signal",
                "unsupported_disease_state",
            )
            if code in review_flags
        )
        return "review", reason, tuple(sorted(review_flags))

    flags: tuple[str, ...] = ("temporality_unverified",)
    if modality is None:
        flags = ("missing_modality", "temporality_unverified")
        modality = "affirmed"

    if modality == "affirmed" and disease_state == "progression-recurrence":
        return "positive", "affirmed_progression", flags
    if modality == "affirmed" and disease_state in {"stability", "remission"}:
        return "negative", "affirmed_nonprogression", flags
    if modality == "negated" and disease_state == "progression-recurrence":
        return "negative", "negated_progression", flags
    return "review", "non_inferential_negation", ("non_inferential_negation",)


def _invalid_task_attribute_targets(
    doc: Document,
) -> tuple[set[str], list[CandidateSignal]]:
    flags: set[str] = set()
    signals: dict[str, CandidateSignal] = {}
    for attribute in doc.attributes:
        entity = doc.entities.get(attribute.target)
        if entity is None:
            if attribute.type in _CANDIDATE_ATTRIBUTE_TYPES:
                flags.add("invalid_attribute_target")
            continue
        invalid = False
        if attribute.type == "DiseaseStateVal":
            invalid = entity.type != "DiseaseState"
        elif attribute.type == "HistoryVal":
            invalid = entity.type in _RELEVANT_ENTITY_TYPES
        elif attribute.type == "SectionSkipType":
            invalid = entity.type != "SectionSkip"
        elif entity.type == "SectionSkip":
            invalid = True
        if invalid:
            flags.add("invalid_attribute_target")
            signals[entity.ann_id] = CandidateSignal(
                entity.ann_id, "review", "invalid_attribute_target"
            )
    return flags, list(signals.values())


def _invalid_skip_attributes(doc: Document) -> bool:
    for attribute in doc.attributes:
        if attribute.type != "SectionSkipType":
            continue
        entity = doc.entities.get(attribute.target)
        if (
            entity is not None
            and entity.type == "SectionSkip"
            and attribute.value not in _SECTION_SKIP_VALUES
        ):
            return True
    return False


def _has_temporal_relation(doc: Document, relevant_ids: set[str]) -> bool:
    for relation in doc.relations:
        if relation.type not in _TEMPORAL_RELATIONS:
            continue
        targets = set(relation.args.values())
        if not targets & relevant_ids:
            continue
        other_targets = targets - relevant_ids
        if any(
            (entity := doc.entities.get(target)) is not None and entity.type == "Datetime"
            for target in other_targets
        ):
            return True
    return False


def _candidate(
    doc: Document,
    *,
    disposition: Disposition,
    value: bool | None,
    signals: list[CandidateSignal],
    flags: set[str],
) -> CurrentProgressionCandidate:
    return CurrentProgressionCandidate(
        doc_id=doc.doc_id,
        disposition=disposition,
        value=value,
        signals=tuple(sorted(signals, key=lambda signal: signal.ann_id)),
        flags=tuple(sorted(_BASE_FLAGS | flags)),
        annotation_inventory_complete=doc.annotation_inventory_complete,
    )


def derive_current_progression_candidate(doc: Document) -> CurrentProgressionCandidate:
    """Reduce one parsed document to a deterministic, non-authoritative review candidate."""
    if not doc.annotation_inventory_complete:
        flags = {"incomplete_annotation_inventory"}
        flags.update(diagnostic.code for diagnostic in doc.diagnostics)
        return _candidate(
            doc,
            disposition="insufficient_evidence",
            value=None,
            signals=[],
            flags=flags,
        )

    attributes = _attributes_by_target(doc)
    flags, signals = _invalid_task_attribute_targets(doc)
    skip_intervals, scope_complete = _skip_intervals(doc)
    if not scope_complete:
        flags.add("incomplete_scope")
    if _invalid_skip_attributes(doc):
        flags.add("invalid_attribute_value")

    for skip in (entity for entity in doc.entities.values() if entity.type == "SectionSkip"):
        if _valid_intervals(skip, len(doc.text)) and not _surface_matches(doc, skip):
            flags.update({"incomplete_scope", "source_surface_mismatch"})

    relevant_ids: set[str] = set()
    existing_signal_ids = {signal.ann_id for signal in signals}
    for entity in doc.entities.values():
        if entity.type not in _RELEVANT_ENTITY_TYPES:
            continue
        relevant_ids.add(entity.ann_id)
        structural_flags: set[str] = set()
        if not _valid_intervals(entity, len(doc.text)):
            structural_flags.add("malformed_span")
        else:
            if len(entity.spans) > 1:
                structural_flags.add("discontinuous_span")
            if not _surface_matches(doc, entity):
                structural_flags.add("source_surface_mismatch")
            if any(
                _overlaps(fragment, skipped)
                for fragment in entity.spans
                for skipped in skip_intervals
            ):
                structural_flags.add("skipped_overlap")
        if structural_flags:
            flags.update(structural_flags)
            if entity.ann_id not in existing_signal_ids:
                reason = sorted(structural_flags)[0]
                signals.append(CandidateSignal(entity.ann_id, "review", reason))
                existing_signal_ids.add(entity.ann_id)
            continue
        direction, reason, entity_flags = _classify_entity(
            entity, attributes.get(entity.ann_id, {})
        )
        flags.update(entity_flags)
        if direction != "ignore":
            if entity.ann_id in existing_signal_ids:
                signals = [signal for signal in signals if signal.ann_id != entity.ann_id]
            signals.append(CandidateSignal(entity.ann_id, direction, reason))
            existing_signal_ids.add(entity.ann_id)

    if _has_temporal_relation(doc, relevant_ids):
        flags.add("temporal_relation_unresolved")

    if flags & _BLOCKING_FLAGS:
        return _candidate(
            doc,
            disposition="insufficient_evidence",
            value=None,
            signals=signals,
            flags=flags,
        )

    directions = {signal.direction for signal in signals}
    if {"positive", "negative"} <= directions:
        disposition: Disposition = "conflicting_evidence"
        value = None
    elif "positive" in directions:
        disposition = "answered"
        value = True
    elif "negative" in directions:
        disposition = "answered"
        value = False
    elif signals:
        disposition = "insufficient_evidence"
        value = None
    else:
        disposition = "not_mentioned"
        value = None
        flags.add("annotation_absence_only")

    return _candidate(
        doc,
        disposition=disposition,
        value=value,
        signals=signals,
        flags=flags,
    )
