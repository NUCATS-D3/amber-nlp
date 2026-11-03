"""Score reports, self-reference refusal, move-on inclusion, and the reference scorers
(contract v0.1 sections 4.2, 4.6, 6.2; catalog ANN-11, ANN-12, ANN-17, ANN-18, IFC-09)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from amber.annotations import example_from_projection, replay
from amber.cases import create_case_result
from amber.conformance import fixtures as fx
from amber.graph import EvidenceGraph
from amber.ids import new_ulid
from amber.interfaces import Scorer
from amber.reference import LabelCorrectScorer, StructuralValidityScorer
from amber.schemas import (
    Attribute,
    CaseOutcome,
    LabelRecord,
    MentionReading,
    ReferenceTier,
    Sensitivity,
    VariableOutcome,
)
from amber.scoring import (
    Metric,
    ScoreReport,
    ScoringRefused,
    configuration,
    refuse_self_reference,
    require_move_on,
    tier_mix,
)

from ._annotation_helpers import (
    CASE,
    SOURCE,
    SPANS,
    SYSTEM,
    VARIABLES,
    _raise_to_single_review,
    _silver,
)

CANDIDATE = "model:candidate@1"
RUN = fx.RUN_IDS[0]


def _label(producer: str = fx.TEACHER, **changes: Any) -> LabelRecord:
    fields: dict[str, Any] = {
        "schema_version": "amber/0.1",
        "case_id": "conformance-note-case-1",
        "variable": fx.NOTE_VARIABLES.name,
        "variable_set": fx.NOTE_VARIABLES.name,
        "variable_set_version": fx.NOTE_VARIABLES.version,
        "variable_set_ref": fx.NOTE_VARIABLES.schema_ref,
        "tier": "silver",
        "outcome_status": "answered",
        "value": {"present": True},
        "producer": producer,
        "split": "dev",
        **changes,
    }
    return LabelRecord(**fields)


def _predicted(
    present: bool = True, producer: str = CANDIDATE, case_id: str = "conformance-note-case-1"
) -> Any:
    return fx.note_result(RUN, present=present, producer=producer, case_id=case_id)


def _failed(case_id: str) -> Any:
    stamped = fx.provenance("tool:extractor@1", RUN)
    graph = EvidenceGraph(
        source=fx.SOURCE, variables=fx.NOTE_VARIABLES, sensitivity=Sensitivity.synthetic
    )
    return create_case_result(
        fx.note_case(case_id),
        graph=graph,
        run_id=RUN,
        outcome=CaseOutcome(
            status="failed", reason="x", failure_kind="provider", provenance=stamped
        ),
        final_claim_ids=[],
    )


def _metrics(report: ScoreReport, variable: str | None = None) -> dict[str, float | None]:
    return {metric.name: metric.value for metric in report.metrics if metric.variable == variable}


# Reports -----------------------------------------------------------------------------------


def test_reports_are_provisional_exactly_below_adjudicated() -> None:
    adjudicated = {ReferenceTier.adjudicated: 2}
    ScoreReport(scorer="x", metrics=(), tier_mix=adjudicated, provisional=False)
    for mix in (
        {ReferenceTier.silver: 1},
        {ReferenceTier.single_review: 1, ReferenceTier.adjudicated: 1},
    ):
        with pytest.raises(ValidationError):
            ScoreReport(scorer="x", metrics=(), tier_mix=mix, provisional=False)
    with pytest.raises(ValidationError):
        ScoreReport(scorer="x", metrics=(), tier_mix=adjudicated, provisional=True)
    metric = Metric(name="evidence_overlap", kind="evidence", numerator=1, denominator=2)
    assert metric.value == 0.5
    assert Metric(name="m", kind="label", numerator=0, denominator=0).value is None
    with pytest.raises(ValidationError):
        ScoreReport(
            scorer="x", metrics=(metric,), provisional=False, inconclusive=("evidence_overlap",)
        )


def test_tier_mix_counts_each_reference() -> None:
    examples = [
        fx.note_example("adjudicated", present=True, producer=fx.TEACHER),
        fx.note_example("silver", present=True, producer=fx.TEACHER),
    ]
    assert tier_mix([*examples, _label()]) == {
        ReferenceTier.adjudicated: 1,
        ReferenceTier.silver: 2,
    }


# Self reference (ANN-12, IFC-09) -----------------------------------------------------------


def _with_assigned_by(assigned_by: str) -> Any:
    stamped = fx.provenance("tool:tagger@1", RUN)
    graph = EvidenceGraph(
        source=fx.SOURCE, variables=fx.VARIABLES, sensitivity=Sensitivity.synthetic
    )
    reading = MentionReading(
        reading_id=new_ulid(),
        mention_id=fx.MENTION.mention_id,
        attributes={"negation": Attribute(value=True, assigned_by=assigned_by)},
        provenance=stamped,
    )
    graph.register_mention(fx.MENTION, [reading])
    outcome = CaseOutcome(status="insufficient_evidence", reason="x", provenance=stamped)
    return create_case_result(
        fx.case(),
        graph=graph,
        run_id=RUN,
        variable_outcomes={
            name: VariableOutcome(variable=name, outcome=outcome) for name in ("finding", "summary")
        },
    )


def test_configurations_collect_model_and_rule_producers_only() -> None:
    assert configuration(_predicted(producer="model:m@2")) == {"model:m@2"}
    assert configuration(_predicted(producer="rule:r@1+abc")) == {"rule:r@1"}
    assert configuration(_predicted(producer="tool:t@1")) == frozenset()
    assert configuration(_label("model:teacher@1")) == {"model:teacher@1"}
    assert configuration(_with_assigned_by("rule:negex@2+h1")) == {"rule:negex@2"}


@pytest.mark.parametrize(
    ("predicted_by", "reference"),
    [
        (CANDIDATE, lambda: fx.note_example("silver", present=True, producer=CANDIDATE)),
        (CANDIDATE, lambda: _label(CANDIDATE)),
        ("rule:rules@1+aaa", lambda: _label("rule:rules@1+bbb")),
    ],
)
def test_silver_references_from_the_same_configuration_are_refused(
    predicted_by: str, reference: Any
) -> None:
    with pytest.raises(ScoringRefused, match="self_reference"):
        refuse_self_reference([_predicted(producer=predicted_by)], [reference()])


def test_an_assigned_by_overlap_alone_is_refused() -> None:
    with pytest.raises(ScoringRefused, match="self_reference"):
        refuse_self_reference([_with_assigned_by("rule:negex@2+h1")], [_label("rule:negex@2+h9")])


def test_human_tiers_tools_and_other_models_are_not_refused() -> None:
    adjudicated = fx.note_example("adjudicated", present=True, producer=CANDIDATE)
    refuse_self_reference([_predicted()], [adjudicated])
    refuse_self_reference([_predicted(producer="tool:t@1")], [_label("tool:t@1")])
    refuse_self_reference([_predicted()], [_label("model:other@1")])
    refuse_self_reference([_predicted(producer="model:candidate@2")], [_label(CANDIDATE)])


# Move-on (ANN-18 scoring half) -------------------------------------------------------------


def _single_review(with_move_on: bool) -> tuple[Any, str | None]:
    log, ids = _silver()
    _raise_to_single_review(log, ids)
    decision = None
    if with_move_on:
        decision = log.add(
            "tier.set", SYSTEM, "system", after={"decision_ref": "protocol/move-on"}
        ).event_id
    projection = replay(log.events, case=CASE, source=SOURCE, variables=VARIABLES, spans=SPANS)
    return example_from_projection(
        projection, case=CASE, source=SOURCE, variables=VARIABLES
    ), decision


def test_single_review_snapshots_scored_under_move_on_include_the_decision() -> None:
    example, decision = _single_review(with_move_on=True)
    assert decision is not None
    require_move_on([example], decision)

    before, _ = _single_review(with_move_on=False)
    with pytest.raises(ScoringRefused, match="move_on_not_included"):
        require_move_on([before], decision)
    adjudicated = fx.note_example("adjudicated", present=True, producer=fx.TEACHER)
    require_move_on([adjudicated, _label()], decision)


# Reference scorers -------------------------------------------------------------------------


def test_the_reference_scorers_satisfy_the_protocol() -> None:
    assert isinstance(LabelCorrectScorer(), Scorer)
    assert isinstance(StructuralValidityScorer(), Scorer)
    assert StructuralValidityScorer.needs == frozenset()


def test_label_correctness_against_adjudicated_examples() -> None:
    examples = [
        fx.note_example("adjudicated", present=True, producer=fx.TEACHER),
        fx.note_example("adjudicated", present=True, producer=fx.TEACHER, case_id="note-2"),
        fx.note_example("adjudicated", present=False, producer=fx.TEACHER, case_id="note-3"),
        fx.note_example("adjudicated", present=True, producer=fx.TEACHER, case_id="note-4"),
    ]
    predicted = [
        _predicted(True),
        _predicted(False, case_id="note-2"),
        _failed("note-3"),
    ]
    report = LabelCorrectScorer().score(predicted, examples, variables=fx.NOTE_VARIABLES)

    overall = _metrics(report)
    assert overall["label_accuracy"] == 0.25
    assert overall["coverage"] == 0.5
    assert overall["omissions"] == 0.5
    assert overall["evidence_overlap"] == 1.0
    assert overall["tokens_per_case"] == 12.0
    assert _metrics(report, fx.NOTE_VARIABLES.name)["label_accuracy"] == 0.25
    assert not report.provisional and report.inconclusive == ()
    assert set(report.example_ids) == {example.example_id for example in examples}
    assert report.tier_mix == {ReferenceTier.adjudicated: 4}


def test_label_records_score_labels_but_not_evidence() -> None:
    report = LabelCorrectScorer().score(
        [_predicted(True)], [_label(), _label(case_id="missing-case")], variables=fx.NOTE_VARIABLES
    )
    assert report.provisional
    assert report.inconclusive == ("evidence_overlap",)
    assert all(metric.kind != "evidence" for metric in report.metrics)
    assert _metrics(report)["label_accuracy"] == 0.5
    assert report.example_ids == ()


def test_non_answers_must_match_status() -> None:
    reference = _label(outcome_status="insufficient_evidence", value=None)
    report = LabelCorrectScorer().score(
        [_predicted(True)], [reference], variables=fx.NOTE_VARIABLES
    )
    assert _metrics(report)["label_accuracy"] == 0.0
    assert "omissions" not in _metrics(report)


def test_scorers_refuse_self_reference_and_check_move_on() -> None:
    silver = fx.note_example("silver", present=True, producer=CANDIDATE)
    for scorer in (LabelCorrectScorer(), StructuralValidityScorer()):
        with pytest.raises(ScoringRefused):
            scorer.score([_predicted()], [silver], variables=fx.NOTE_VARIABLES)
    example, _ = _single_review(with_move_on=False)
    with pytest.raises(ScoringRefused, match="move_on_not_included"):
        LabelCorrectScorer(move_on_event=new_ulid()).score(
            [], [example], variables=fx.NOTE_VARIABLES
        )


def test_predictions_must_use_the_scored_variable_set() -> None:
    with pytest.raises(ValueError, match="variable_set_mismatch"):
        LabelCorrectScorer().score([_predicted()], [], variables=fx.VARIABLES)


def test_structural_validity_counts_outcomes_and_grounding_failures() -> None:
    predicted = [_predicted(True), _failed("note-2"), _predicted(False, case_id="note-3")]
    report = StructuralValidityScorer().score(predicted, [], variables=fx.NOTE_VARIABLES)
    metrics = _metrics(report)
    assert metrics["answered"] == pytest.approx(2 / 3)
    assert metrics["failed"] == pytest.approx(1 / 3)
    assert metrics["failed:provider"] == pytest.approx(1 / 3)
    assert metrics["grounding_failures_per_case"] == 0.0
    assert not report.provisional and report.tier_mix == {}
