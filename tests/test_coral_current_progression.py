"""Non-authoritative current-progression candidates from invented BRAT records."""

from __future__ import annotations

import importlib
import sys
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import ModuleType

import pytest

from experiments.coral.brat import Document, parse_ann

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "experiments" / "coral" / "scripts"


@pytest.fixture
def candidate_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPT_DIR))
    sys.modules.pop("coral_current_progression", None)
    return importlib.import_module("coral_current_progression")


def _parse(tmp_path: Path, text: str, records: list[str]) -> Document:
    ann_path = tmp_path / "invented.ann"
    ann_path.write_text("\n".join(records) + "\n", encoding="utf-8", newline="")
    return parse_ann(ann_path, text)


def _entity_record(
    ann_id: str,
    entity_type: str,
    text: str,
    quote: str,
    *,
    occurrence: int = 0,
) -> str:
    starts = [index for index in range(len(text)) if text.startswith(quote, index)]
    start = starts[occurrence]
    return f"{ann_id}\t{entity_type} {start} {start + len(quote)}\t{quote}"


def _disease_document(
    tmp_path: Path,
    *,
    value: str | None = "progression-recurrence",
    modality: str | None = "affirmed",
    experiencer: str | None = None,
    extra_records: tuple[str, ...] = (),
) -> Document:
    text = "synthetic progression statement"
    records = [_entity_record("T1", "DiseaseState", text, "progression")]
    if value is not None:
        records.append(f"A1\tDiseaseStateVal T1 {value}")
    if modality is not None:
        records.append(f"A2\tNegationModalityVal T1 {modality}")
    if experiencer is not None:
        records.append(f"A3\tExperiencerVal T1 {experiencer}")
    records.extend(extra_records)
    return _parse(tmp_path, text, records)


def _derive(module: ModuleType, document: Document):
    return module.derive_current_progression_candidate(document)


def test_candidate_contract_is_immutable_and_contains_no_source_text(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    candidate = _derive(candidate_module, _disease_document(tmp_path))

    assert candidate.disposition in {
        "answered",
        "not_mentioned",
        "conflicting_evidence",
        "insufficient_evidence",
    }
    assert candidate.value is True or candidate.value is False or candidate.value is None
    assert all(signal.ann_id.startswith("T") for signal in candidate.signals)
    assert "non_authoritative" in candidate.flags
    assert "clinical_review_required" in candidate.flags
    assert not candidate.clinical_scope_reviewed
    assert (candidate.disposition == "answered") == (type(candidate.value) is bool)
    assert {item.name for item in fields(candidate_module.CandidateSignal)} == {
        "ann_id",
        "direction",
        "reason",
    }
    assert {item.name for item in fields(candidate_module.CurrentProgressionCandidate)} == {
        "doc_id",
        "disposition",
        "value",
        "signals",
        "flags",
        "annotation_inventory_complete",
        "clinical_scope_reviewed",
    }
    assert "text" not in repr(candidate)
    with pytest.raises(FrozenInstanceError):
        candidate.value = False
    with pytest.raises(FrozenInstanceError):
        candidate.signals[0].reason = "changed"


def test_candidate_contract_rejects_values_on_non_answer_dispositions(
    candidate_module: ModuleType,
) -> None:
    with pytest.raises(AssertionError):
        candidate_module.CurrentProgressionCandidate(
            doc_id="synthetic",
            disposition="insufficient_evidence",
            value=1,
            signals=(),
            flags=("clinical_review_required", "non_authoritative"),
            annotation_inventory_complete=True,
        )


@pytest.mark.parametrize(
    ("value", "modality", "disposition", "expected"),
    [
        ("progression-recurrence", "affirmed", "answered", True),
        ("stability", "affirmed", "answered", False),
        ("remission", "affirmed", "answered", False),
        ("progression-recurrence", "negated", "answered", False),
        ("stability", "negated", "insufficient_evidence", None),
        ("remission", "negated", "insufficient_evidence", None),
        ("progression-recurrence", "uncertain_in_present", "insufficient_evidence", None),
        ("progression-recurrence", "uncertain_in_past", "insufficient_evidence", None),
        ("progression-recurrence", "planned_in_future", "insufficient_evidence", None),
        ("progression-recurrence", "hypothetical_in_future", "insufficient_evidence", None),
        ("hospice", "affirmed", "insufficient_evidence", None),
        ("progression-others", "affirmed", "insufficient_evidence", None),
        ("others", "affirmed", "insufficient_evidence", None),
    ],
)
def test_disease_state_value_and_modality_rules(
    tmp_path: Path,
    candidate_module: ModuleType,
    value: str,
    modality: str,
    disposition: str,
    expected: bool | None,
) -> None:
    candidate = _derive(
        candidate_module,
        _disease_document(tmp_path, value=value, modality=modality),
    )

    assert candidate.disposition == disposition
    assert candidate.value is expected
    if modality in {"affirmed", "negated"} and expected is not None:
        assert "temporality_unverified" in candidate.flags


@pytest.mark.parametrize("entity_type", ["DiseaseProgression", "Remission", "Hospice"])
@pytest.mark.parametrize("modality", ["affirmed", "negated", "uncertain_in_past"])
def test_legacy_entity_types_are_review_only(
    tmp_path: Path,
    candidate_module: ModuleType,
    entity_type: str,
    modality: str,
) -> None:
    text = "invented condition"
    document = _parse(
        tmp_path,
        text,
        [
            _entity_record("T1", entity_type, text, "condition"),
            f"A1\tNegationModalityVal T1 {modality}",
        ],
    )

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "insufficient_evidence"
    assert candidate.value is None
    assert candidate.signals[0].direction == "review"
    assert "unsupported_entity_type" in candidate.flags


def test_missing_modality_is_an_explicit_affirmative_assumption(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    candidate = _derive(
        candidate_module,
        _disease_document(tmp_path, modality=None),
    )

    assert candidate.disposition == "answered"
    assert candidate.value is True
    assert {"missing_modality", "temporality_unverified"} <= set(candidate.flags)


@pytest.mark.parametrize("history", ["history", "current"])
def test_history_attribute_on_disease_state_is_a_blocker(
    tmp_path: Path, candidate_module: ModuleType, history: str
) -> None:
    candidate = _derive(
        candidate_module,
        _disease_document(tmp_path, extra_records=(f"A3\tHistoryVal T1 {history}",)),
    )

    assert candidate.disposition == "insufficient_evidence"
    assert candidate.value is None
    assert "invalid_attribute_target" in candidate.flags


@pytest.mark.parametrize("experiencer", ["family", "others"])
def test_non_patient_experiencer_is_review_only(
    tmp_path: Path, candidate_module: ModuleType, experiencer: str
) -> None:
    candidate = _derive(
        candidate_module,
        _disease_document(tmp_path, experiencer=experiencer),
    )

    assert candidate.disposition == "insufficient_evidence"
    assert candidate.value is None
    assert "non_patient_experiencer" in candidate.flags


def test_complete_inventory_without_relevant_annotations_is_absence_only(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    document = _parse(tmp_path, "invented finding", ["T1\tSymptom 9 16\tfinding"])

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "not_mentioned"
    assert candidate.value is None
    assert candidate.signals == ()
    assert "annotation_absence_only" in candidate.flags
    assert candidate.annotation_inventory_complete
    assert not candidate.clinical_scope_reviewed


@pytest.mark.parametrize("reverse", [False, True])
def test_opposing_usable_seeds_conflict_independent_of_record_order(
    tmp_path: Path, candidate_module: ModuleType, reverse: bool
) -> None:
    text = "invented progression then remission"
    records = [
        _entity_record("T2", "DiseaseState", text, "progression"),
        "A2\tDiseaseStateVal T2 progression-recurrence",
        "A3\tNegationModalityVal T2 affirmed",
        _entity_record("T1", "DiseaseState", text, "remission"),
        "A4\tDiseaseStateVal T1 remission",
        "A5\tNegationModalityVal T1 affirmed",
    ]
    if reverse:
        records.reverse()

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "conflicting_evidence"
    assert candidate.value is None
    assert [(signal.ann_id, signal.direction) for signal in candidate.signals] == [
        ("T1", "negative"),
        ("T2", "positive"),
    ]


def test_warning_only_historical_signal_does_not_veto_negative_seed(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented remission and prior progression"
    records = [
        _entity_record("T1", "DiseaseState", text, "remission"),
        "A1\tDiseaseStateVal T1 remission",
        "A2\tNegationModalityVal T1 affirmed",
        _entity_record("T2", "DiseaseState", text, "progression"),
        "A3\tDiseaseStateVal T2 progression-recurrence",
        "A4\tNegationModalityVal T2 uncertain_in_past",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "answered"
    assert candidate.value is False
    assert "historical_signal" in candidate.flags
    assert [signal.direction for signal in candidate.signals] == ["negative", "review"]


def test_warning_only_future_signal_does_not_veto_positive_seed(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented progression and planned remission"
    records = [
        _entity_record("T1", "DiseaseState", text, "progression"),
        "A1\tDiseaseStateVal T1 progression-recurrence",
        "A2\tNegationModalityVal T1 affirmed",
        _entity_record("T2", "DiseaseState", text, "remission"),
        "A3\tDiseaseStateVal T2 remission",
        "A4\tNegationModalityVal T2 planned_in_future",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "answered"
    assert candidate.value is True
    assert "future_signal" in candidate.flags


def test_warning_only_non_patient_signal_does_not_veto_positive_seed(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented progression and remission"
    records = [
        _entity_record("T1", "DiseaseState", text, "progression"),
        "A1\tDiseaseStateVal T1 progression-recurrence",
        "A2\tNegationModalityVal T1 affirmed",
        _entity_record("T2", "DiseaseState", text, "remission"),
        "A3\tDiseaseStateVal T2 remission",
        "A4\tNegationModalityVal T2 affirmed",
        "A5\tExperiencerVal T2 others",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "answered"
    assert candidate.value is True
    assert "non_patient_experiencer" in candidate.flags


def test_affirmed_historical_wording_still_has_unverified_temporality(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "years ago the invented cancer had progression"
    records = [
        _entity_record("T1", "DiseaseState", text, "progression"),
        "A1\tDiseaseStateVal T1 progression-recurrence",
        "A2\tNegationModalityVal T1 affirmed",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.value is True
    assert "temporality_unverified" in candidate.flags


def test_temporal_relation_to_datetime_is_retained_as_unresolved(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented progression on 2020-01-01"
    records = [
        _entity_record("T1", "DiseaseState", text, "progression"),
        "A1\tDiseaseStateVal T1 progression-recurrence",
        "A2\tNegationModalityVal T1 affirmed",
        _entity_record("T2", "Datetime", text, "2020-01-01"),
        "R1\tHappensAtOnDuring Arg1:T1 Arg2:T2",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.value is True
    assert "temporal_relation_unresolved" in candidate.flags


@pytest.mark.parametrize(
    "entity_record",
    [
        "T1\tDiseaseState 9 99\tprogression",
        "T1\tDiseaseState 20 9\tprogression",
        "T1\tDiseaseState 9 9\tprogression",
    ],
)
def test_invalid_relevant_bounds_block_an_otherwise_usable_seed(
    tmp_path: Path, candidate_module: ModuleType, entity_record: str
) -> None:
    text = "invented progression and stability"
    records = [
        entity_record,
        "A1\tDiseaseStateVal T1 progression-recurrence",
        "A2\tNegationModalityVal T1 affirmed",
        _entity_record("T2", "DiseaseState", text, "stability"),
        "A3\tDiseaseStateVal T2 stability",
        "A4\tNegationModalityVal T2 affirmed",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "insufficient_evidence"
    assert candidate.value is None
    assert "malformed_span" in candidate.flags


def test_discontinuous_relevant_span_is_a_blocker(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented alpha gap omega"
    records = [
        "T1\tDiseaseState 9 14;19 24\talpha omega",
        "A1\tDiseaseStateVal T1 progression-recurrence",
        "A2\tNegationModalityVal T1 affirmed",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "insufficient_evidence"
    assert "discontinuous_span" in candidate.flags


def test_relevant_span_overlapping_skip_is_a_blocker(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented progression statement"
    records = [
        _entity_record("T1", "DiseaseState", text, "progression"),
        "A1\tDiseaseStateVal T1 progression-recurrence",
        "A2\tNegationModalityVal T1 affirmed",
        "T2\tSectionSkip 12 23\tgression sta",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "insufficient_evidence"
    assert "skipped_overlap" in candidate.flags


def test_malformed_skip_blocks_an_otherwise_usable_seed(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented progression statement"
    records = [
        _entity_record("T1", "DiseaseState", text, "progression"),
        "A1\tDiseaseStateVal T1 progression-recurrence",
        "A2\tNegationModalityVal T1 affirmed",
        "T2\tSectionSkip 22 200\tstatement",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "insufficient_evidence"
    assert "incomplete_scope" in candidate.flags


def test_discontinuous_skip_surface_is_space_joined_and_gap_remains_in_scope(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "skip-left progression skip-right"
    records = [
        "T1\tSectionSkip 0 9;22 32\tskip-left skip-right",
        _entity_record("T2", "DiseaseState", text, "progression"),
        "A1\tDiseaseStateVal T2 progression-recurrence",
        "A2\tNegationModalityVal T2 affirmed",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "answered"
    assert candidate.value is True
    assert "incomplete_scope" not in candidate.flags
    assert "skipped_overlap" not in candidate.flags


def test_discontinuous_skip_surface_mismatch_blocks_scope(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "skip-left progression skip-right"
    records = [
        "T1\tSectionSkip 0 9;22 32\tskip-left wrong-copy",
        _entity_record("T2", "DiseaseState", text, "progression"),
        "A1\tDiseaseStateVal T2 progression-recurrence",
        "A2\tNegationModalityVal T2 affirmed",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "insufficient_evidence"
    assert {"source_surface_mismatch", "incomplete_scope"} <= set(candidate.flags)


def test_missing_disease_state_value_is_a_blocker(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    candidate = _derive(
        candidate_module,
        _disease_document(tmp_path, value=None),
    )

    assert candidate.disposition == "insufficient_evidence"
    assert "missing_disease_state" in candidate.flags


@pytest.mark.parametrize(
    ("extra_records", "diagnostic"),
    [
        (("broken record",), "unknown_record"),
        (("A9\tBroken",), "malformed_record"),
        (("T9\tSymptom 0 8\tinvented", "T9\tSymptom 0 8\tinvented"), "duplicate_id"),
        (("A3\tNegationModalityVal T1 affirmed",), "duplicate_attribute"),
        (("A3\tNegationModalityVal T1 negated",), "conflicting_attribute"),
        (("R9\tTemporal Arg1:T1 Arg2:T99",), "dangling_reference"),
    ],
)
def test_incomplete_inventory_blocks_an_otherwise_usable_seed(
    tmp_path: Path,
    candidate_module: ModuleType,
    extra_records: tuple[str, ...],
    diagnostic: str,
) -> None:
    document = _disease_document(tmp_path, extra_records=extra_records)
    assert not document.annotation_inventory_complete

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "insufficient_evidence"
    assert candidate.value is None
    assert not candidate.annotation_inventory_complete
    assert "incomplete_annotation_inventory" in candidate.flags
    assert diagnostic in candidate.flags


@pytest.mark.parametrize(
    ("quote", "expected_parser_status"),
    [("XXXXXXXXXXX", "redacted"), ("wrong", "mismatch")],
)
def test_relevant_source_surface_mismatch_blocks_even_with_valid_bounds(
    tmp_path: Path,
    candidate_module: ModuleType,
    quote: str,
    expected_parser_status: str,
) -> None:
    text = "invented progression statement"
    document = _parse(
        tmp_path,
        text,
        [
            f"T1\tDiseaseState 9 20\t{quote}",
            "A1\tDiseaseStateVal T1 progression-recurrence",
            "A2\tNegationModalityVal T1 affirmed",
        ],
    )
    assert document.entities["T1"].status == expected_parser_status

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "insufficient_evidence"
    assert "source_surface_mismatch" in candidate.flags


def test_cached_entity_status_cannot_override_exact_source_comparison(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    document = _disease_document(tmp_path)
    document.entities["T1"].status = "mismatch"

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "answered"
    assert candidate.value is True


def test_unrelated_surface_mismatch_does_not_block_absence_candidate(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    document = _parse(tmp_path, "invented symptom", ["T1\tSymptom 9 16\tXXXXXXX"])

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "not_mentioned"
    assert "source_surface_mismatch" not in candidate.flags


def test_task_attribute_on_unrelated_entity_is_not_silently_ignored(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented symptom"
    document = _parse(
        tmp_path,
        text,
        [
            _entity_record("T1", "Symptom", text, "symptom"),
            "A1\tDiseaseStateVal T1 progression-recurrence",
        ],
    )

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "insufficient_evidence"
    assert "invalid_attribute_target" in candidate.flags
    assert candidate.signals[0].ann_id == "T1"


@pytest.mark.parametrize(
    "target_record",
    [
        "E1\tAction:T1",
        "R1\tPair Arg1:T1 Arg2:T2",
        "N1\tReference T1 Toy:1\talpha",
    ],
)
@pytest.mark.parametrize("attribute_type", ["DiseaseStateVal", "SectionSkipType"])
@pytest.mark.parametrize("reverse", [False, True])
def test_task_attribute_on_known_nonentity_target_blocks_without_invalid_signal(
    tmp_path: Path,
    candidate_module: ModuleType,
    target_record: str,
    attribute_type: str,
    reverse: bool,
) -> None:
    text = "alpha beta"
    target_id = target_record.split("\t", 1)[0]
    records = [
        "T1\tToken 0 5\talpha",
        "T2\tToken 6 10\tbeta",
        target_record,
        f"A1\t{attribute_type} {target_id} progression-recurrence",
    ]
    if attribute_type == "SectionSkipType":
        records[-1] = records[-1].replace("progression-recurrence", "laboratory")
    if reverse:
        records.reverse()
    document = _parse(tmp_path, text, records)
    assert document.annotation_inventory_complete

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "insufficient_evidence"
    assert candidate.value is None
    assert "invalid_attribute_target" in candidate.flags
    assert candidate.signals == ()


@pytest.mark.parametrize(
    ("entity_type", "attribute"),
    [
        ("Symptom", "NegationModalityVal T1 negated"),
        ("ClinicalCondition", "ExperiencerVal T1 family"),
    ],
)
def test_valid_shared_attribute_on_unrelated_entity_does_not_block(
    tmp_path: Path,
    candidate_module: ModuleType,
    entity_type: str,
    attribute: str,
) -> None:
    text = "invented unrelated finding"
    document = _parse(
        tmp_path,
        text,
        [
            _entity_record("T1", entity_type, text, "finding"),
            f"A1\t{attribute}",
        ],
    )

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "not_mentioned"
    assert "invalid_attribute_target" not in candidate.flags


def test_unexpected_attribute_name_on_disease_state_blocks_seed(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    candidate = _derive(
        candidate_module,
        _disease_document(tmp_path, extra_records=("A3\tContinuityVal T1 stable",)),
    )

    assert candidate.disposition == "insufficient_evidence"
    assert "invalid_attribute_target" in candidate.flags


def test_section_attribute_on_unrelated_entity_does_not_claim_broken_scope(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented symptom"
    document = _parse(
        tmp_path,
        text,
        [
            _entity_record("T1", "Symptom", text, "symptom"),
            "A1\tSectionSkipType T1 laboratory",
        ],
    )

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "insufficient_evidence"
    assert "invalid_attribute_target" in candidate.flags
    assert "incomplete_scope" not in candidate.flags


def test_unexpected_attribute_name_on_section_skip_blocks_candidate(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "skip progression"
    document = _parse(
        tmp_path,
        text,
        [
            "T1\tSectionSkip 0 5\tskip ",
            "A1\tContinuityVal T1 stable",
            "T2\tDiseaseState 5 16\tprogression",
            "A2\tDiseaseStateVal T2 progression-recurrence",
            "A3\tNegationModalityVal T2 affirmed",
        ],
    )

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "insufficient_evidence"
    assert "invalid_attribute_target" in candidate.flags


def test_unknown_modality_on_legacy_entity_blocks_an_otherwise_usable_seed(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented progression and legacy state"
    records = [
        _entity_record("T1", "DiseaseState", text, "progression"),
        "A1\tDiseaseStateVal T1 progression-recurrence",
        "A2\tNegationModalityVal T1 affirmed",
        _entity_record("T2", "Remission", text, "state"),
        "A3\tNegationModalityVal T2 invented-modality",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "insufficient_evidence"
    assert {"invalid_attribute_value", "unsupported_entity_type"} <= set(candidate.flags)


def test_disease_state_value_on_legacy_entity_keeps_both_review_diagnostics(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented legacy remission"
    records = [
        _entity_record("T1", "Remission", text, "remission"),
        "A1\tDiseaseStateVal T1 remission",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "insufficient_evidence"
    assert {"invalid_attribute_target", "unsupported_entity_type"} <= set(candidate.flags)


@pytest.mark.parametrize("attribute", ["NegationModalityVal T1", "ExperiencerVal T1"])
def test_present_but_valueless_task_attribute_is_invalid(
    tmp_path: Path, candidate_module: ModuleType, attribute: str
) -> None:
    records = [
        "T1\tDiseaseState 9 20\tprogression",
        "A1\tDiseaseStateVal T1 progression-recurrence",
        f"A2\t{attribute}",
    ]

    candidate = _derive(
        candidate_module,
        _parse(tmp_path, "invented progression statement", records),
    )

    assert candidate.disposition == "insufficient_evidence"
    assert "invalid_attribute_value" in candidate.flags


def test_one_review_signal_retains_each_independent_semantic_warning(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    candidate = _derive(
        candidate_module,
        _disease_document(
            tmp_path,
            value="hospice",
            modality="uncertain_in_past",
            experiencer="others",
        ),
    )

    assert candidate.disposition == "insufficient_evidence"
    assert {
        "historical_signal",
        "non_patient_experiencer",
        "uncertain_signal",
        "unsupported_disease_state",
    } <= set(candidate.flags)


@pytest.mark.parametrize(
    "attribute",
    [
        "DiseaseStateVal T1 invented-value",
        "NegationModalityVal T1 invented-modality",
        "ExperiencerVal T1 invented-experiencer",
    ],
)
def test_invalid_task_attribute_values_block_without_echoing_raw_values(
    tmp_path: Path, candidate_module: ModuleType, attribute: str
) -> None:
    attribute_name = attribute.split()[0]
    raw_value = attribute.split()[-1]
    records = (
        "A1\tDiseaseStateVal T1 progression-recurrence",
        "A2\tNegationModalityVal T1 affirmed",
        f"A3\t{attribute}",
    )
    if attribute_name == "DiseaseStateVal":
        records = records[1:]
    elif attribute_name == "NegationModalityVal":
        records = (records[0], records[2])
    candidate = _derive(
        candidate_module,
        _parse(
            tmp_path,
            "invented progression statement",
            ["T1\tDiseaseState 9 20\tprogression", *records],
        ),
    )

    assert candidate.disposition == "insufficient_evidence"
    assert "invalid_attribute_value" in candidate.flags
    assert raw_value not in repr(candidate)


def test_unicode_crlf_offsets_are_used_without_normalization(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "αβ\r\ninvented progression\r\n"
    start = text.index("progression")
    document = _parse(
        tmp_path,
        text,
        [
            f"T1\tDiseaseState {start} {start + len('progression')}\tprogression",
            "A1\tDiseaseStateVal T1 progression-recurrence",
            "A2\tNegationModalityVal T1 affirmed",
        ],
    )

    candidate = _derive(candidate_module, document)

    assert candidate.disposition == "answered"
    assert candidate.value is True


def test_touching_skip_boundary_does_not_count_as_overlap(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "skip progression"
    records = [
        "T1\tSectionSkip 0 5\tskip ",
        "T2\tDiseaseState 5 16\tprogression",
        "A1\tDiseaseStateVal T2 progression-recurrence",
        "A2\tNegationModalityVal T2 affirmed",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.disposition == "answered"
    assert candidate.value is True
    assert "skipped_overlap" not in candidate.flags


def test_flags_and_signals_are_sorted_deterministically(
    tmp_path: Path, candidate_module: ModuleType
) -> None:
    text = "invented progression then remission"
    records = [
        _entity_record("T9", "DiseaseState", text, "progression"),
        "A1\tDiseaseStateVal T9 progression-recurrence",
        _entity_record("T2", "DiseaseState", text, "remission"),
        "A2\tDiseaseStateVal T2 remission",
    ]

    candidate = _derive(candidate_module, _parse(tmp_path, text, records))

    assert candidate.flags == tuple(sorted(candidate.flags))
    assert [signal.ann_id for signal in candidate.signals] == ["T2", "T9"]
