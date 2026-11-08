"""Span-level mention scoring (amber-a6d.10): strict and lenient P/R/F1, per mention type and
macro averages, one-to-one maximal lenient matching, attribute accuracy on strict matches, and
the scorer's reference rules. Toy values are computed by hand in each test."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from amber.annotations import example_from_projection, replay
from amber.cases import create_case_result
from amber.conformance import fixtures as fx
from amber.graph import EvidenceGraph
from amber.grounding import exact_mention
from amber.ids import new_ulid
from amber.interfaces import Scorer
from amber.labeling import import_events
from amber.schemas import (
    Attribute,
    CaseOutcome,
    LabelRecord,
    Mention,
    MentionReading,
    Sensitivity,
    VariableOutcome,
)
from amber.scoring import Metric, ScoreReport, ScoringRefused
from amber.span_scoring import MentionSpanScorer, Span, span_metrics, spans_of


def _span(
    start: int,
    end: int,
    mention_type: str = "finding",
    *,
    case: str = "c1",
    source: str = "s1",
    fragments: tuple[tuple[int, int], ...] = (),
    attributes: Mapping[str, Sequence[bool | int | str]] | None = None,
) -> Span:
    return Span(
        case_id=case,
        source_id=source,
        mention_type=mention_type,
        start=start,
        end=end,
        fragments=fragments,
        attributes={
            key: frozenset((type(v), v) for v in values)
            for key, values in (attributes or {}).items()
        },
    )


def _values(metrics: Sequence[Metric]) -> dict[str, tuple[float, int]]:
    return {metric.name: (metric.numerator, metric.denominator) for metric in metrics}


# The pure core: hand-computed toy cases -----------------------------------------------------


def test_exact_matches_and_misses_count_as_hand_computed() -> None:
    # gold: [0,4) [10,14) [20,24); predicted: [0,4) exact, [11,14) overlaps, [30,34) spurious.
    gold = [_span(0, 4), _span(10, 14), _span(20, 24)]
    predicted = [_span(0, 4), _span(11, 14), _span(30, 34)]
    values = _values(span_metrics(predicted, gold))
    # strict: TP 1, FP 2, FN 2.
    assert values["strict_precision"] == (1, 3)
    assert values["strict_recall"] == (1, 3)
    assert values["strict_f1"] == (2, 6)
    # lenient: TP 2, FP 1, FN 1.
    assert values["lenient_precision"] == (2, 3)
    assert values["lenient_recall"] == (2, 3)
    assert values["lenient_f1"] == (4, 6)
    assert values["strict_precision:finding"] == (1, 3)


def test_the_mention_type_must_agree() -> None:
    values = _values(span_metrics([_span(0, 4, "site")], [_span(0, 4, "finding")]))
    assert values["strict_precision"] == (0, 1) and values["strict_recall"] == (0, 1)
    assert values["lenient_precision"] == (0, 1)
    assert values["strict_recall:finding"] == (0, 1)
    assert values["strict_precision:site"] == (0, 1)
    # Undefined per-type ratios are reported as undefined, never 0 or 1.
    assert values["strict_precision:finding"] == (0, 0)
    assert values["strict_recall:site"] == (0, 0)


def test_spans_never_match_across_cases_or_sources() -> None:
    gold = [_span(0, 4, case="c1"), _span(0, 4, source="s1")]
    predicted = [_span(0, 4, case="c2"), _span(0, 4, source="s2")]
    values = _values(span_metrics(predicted, gold))
    assert values["strict_precision"] == (0, 2) and values["lenient_precision"] == (0, 2)


def test_touching_spans_do_not_overlap() -> None:
    values = _values(span_metrics([_span(4, 8)], [_span(0, 4)]))
    assert values["lenient_precision"] == (0, 1)


def test_lenient_matching_is_one_to_one() -> None:
    # One gold span overlapped by two predictions earns one true positive, not two.
    values = _values(span_metrics([_span(0, 3), _span(2, 6)], [_span(0, 6)]))
    assert values["lenient_precision"] == (1, 2) and values["lenient_recall"] == (1, 1)
    # And one prediction overlapping two gold spans is credited once.
    values = _values(span_metrics([_span(0, 10)], [_span(0, 3), _span(5, 8)]))
    assert values["lenient_precision"] == (1, 1) and values["lenient_recall"] == (1, 2)


def test_lenient_matching_is_maximal() -> None:
    # Greedy pairing of A=[0,10) with P=[0,10) would strand B=[0,3) and Q=[5,6); the maximum
    # matching pairs A-Q and B-P.
    gold = [_span(0, 10), _span(0, 3)]
    predicted = [_span(0, 10), _span(5, 6)]
    values = _values(span_metrics(predicted, gold))
    assert values["strict_precision"] == (1, 2)
    assert values["lenient_precision"] == (2, 2) and values["lenient_recall"] == (2, 2)


def test_lenient_matching_does_not_depend_on_pairing_order() -> None:
    # P=[0,10) overlaps X=[0,3) and Y=[5,9); Q=[1,2) overlaps only X. Pairing P with X first
    # (the first candidate in order) would strand Q; the maximum matching is P-Y and Q-X.
    gold = [_span(0, 3), _span(5, 9)]
    predicted = [_span(0, 10), _span(1, 2)]
    values = _values(span_metrics(predicted, gold))
    assert values["lenient_precision"] == (2, 2) and values["lenient_recall"] == (2, 2)


def test_lenient_is_never_below_strict() -> None:
    gold = [_span(0, 4), _span(0, 4, "site"), _span(6, 9)]
    predicted = [_span(0, 4), _span(1, 3), _span(0, 4, "site")]
    values = _values(span_metrics(predicted, gold))
    assert values["lenient_precision"][0] >= values["strict_precision"][0]


def test_duplicate_spans_count_once() -> None:
    values = _values(span_metrics([_span(0, 4), _span(0, 4)], [_span(0, 4), _span(0, 4)]))
    assert values["strict_precision"] == (1, 1) and values["strict_recall"] == (1, 1)


def test_duplicate_spans_merge_their_attributes_in_any_order() -> None:
    # Two predictions at one position that disagree are one span whose readings disagree.
    gold = [_span(0, 4, attributes={"negation": [True]})]
    agreeing = _span(0, 4, attributes={"negation": [True]})
    disagreeing = _span(0, 4, attributes={"negation": [False]})
    for predicted in ([agreeing, disagreeing], [disagreeing, agreeing]):
        values = _values(span_metrics(predicted, gold))
        assert values["attribute_accuracy:negation"] == (0, 1)


def test_discontinuous_spans_match_by_fragments() -> None:
    gold = _span(0, 10, fragments=((0, 2), (8, 10)))
    # The same hull with other fragments is not a strict match, and a span inside the gap
    # does not overlap.
    assert _values(span_metrics([_span(0, 10)], [gold]))["strict_precision"] == (0, 1)
    assert _values(span_metrics([_span(0, 10)], [gold]))["lenient_precision"] == (1, 1)
    assert _values(span_metrics([_span(3, 7)], [gold]))["lenient_precision"] == (0, 1)
    same = _span(0, 10, fragments=((0, 2), (8, 10)))
    assert _values(span_metrics([same], [gold]))["strict_precision"] == (1, 1)


def test_macro_averages_defined_per_type_values() -> None:
    # finding: TP 1 FP 0 FN 1 -> P 1, R 1/2, F1 2/3. site: TP 0 FP 1 FN 0 -> P 0, R undefined,
    # F1 0. Macro P = (1 + 0) / 2; macro R = (1/2) / 1; macro F1 = (2/3 + 0) / 2.
    gold = [_span(0, 4), _span(10, 14)]
    predicted = [_span(0, 4), _span(20, 24, "site")]
    values = _values(span_metrics(predicted, gold))
    assert values["strict_macro_precision"] == (1.0, 2)
    assert values["strict_macro_recall"] == (0.5, 1)
    assert values["strict_macro_f1"][1] == 2
    assert values["strict_macro_f1"][0] == pytest.approx(2 / 3)


def test_nothing_to_score_leaves_ratios_undefined() -> None:
    values = _values(span_metrics([], []))
    assert values["strict_precision"] == (0, 0) and values["strict_f1"] == (0, 0)
    assert values["strict_macro_f1"] == (0, 0)
    for metric in span_metrics([], []):
        assert metric.value is None


def test_attribute_accuracy_counts_strict_matches_only() -> None:
    gold = [
        _span(0, 4, attributes={"negation": [True], "temporality": ["current"]}),
        _span(10, 14, attributes={"negation": [False]}),
        _span(20, 24, attributes={"negation": [True]}),
    ]
    predicted = [
        _span(0, 4, attributes={"negation": [True], "temporality": ["historical"]}),
        _span(10, 14),  # negation missing: wrong
        _span(21, 24, attributes={"negation": [True]}),  # lenient only: not scored
    ]
    values = _values(span_metrics(predicted, gold))
    assert values["attribute_accuracy:negation"] == (1, 2)
    assert values["attribute_accuracy:temporality"] == (0, 1)
    assert values["attribute_accuracy"] == (1, 3)


def test_attribute_values_compare_by_type() -> None:
    # True == 1 in Python, but a bool and an int are different attribute values.
    gold = [_span(0, 4, attributes={"negation": [True]}), _span(5, 9, attributes={"rank": [1]})]
    predicted = [
        _span(0, 4, attributes={"negation": [1]}),
        _span(5, 9, attributes={"rank": [True]}),
    ]
    values = _values(span_metrics(predicted, gold))
    assert values["attribute_accuracy"] == (0, 2)


def test_conflicting_attribute_values() -> None:
    # A predicted category with two readings that disagree is wrong; a gold category that
    # disagrees with itself is not scored.
    gold = [
        _span(0, 4, attributes={"negation": [True]}),
        _span(5, 9, attributes={"negation": [True, False]}),
    ]
    predicted = [
        _span(0, 4, attributes={"negation": [True, False]}),
        _span(5, 9, attributes={"negation": [True]}),
    ]
    values = _values(span_metrics(predicted, gold))
    assert values["attribute_accuracy:negation"] == (0, 1)


def test_attributes_absent_from_gold_are_not_assessed() -> None:
    values = _values(span_metrics([_span(0, 4, attributes={"negation": [True]})], [_span(0, 4)]))
    assert "attribute_accuracy:negation" not in values
    assert values["attribute_accuracy"] == (0, 0)


def test_results_are_independent_of_input_order() -> None:
    gold = [_span(0, 10), _span(0, 3), _span(20, 24, "site"), _span(30, 34)]
    predicted = [_span(0, 10), _span(5, 6), _span(21, 23, "site"), _span(40, 44)]
    first = span_metrics(predicted, gold)
    assert span_metrics(list(reversed(predicted)), list(reversed(gold))) == first
    assert span_metrics(predicted, gold) == first


@pytest.mark.parametrize(
    ("start", "end", "fragments"),
    [(4, 4, ()), (5, 4, ()), (-1, 2, ()), (0, 10, ((0, 10),)), (0, 10, ((0, 4), (4, 10)))],
    ids=["empty", "reversed", "negative", "one-fragment", "touching-fragments"],
)
def test_invalid_spans_are_refused(
    start: int, end: int, fragments: tuple[tuple[int, int], ...]
) -> None:
    with pytest.raises(ValueError):
        _span(start, end, fragments=fragments)


# Adapters and the Scorer --------------------------------------------------------------------

TEXT = fx.TEXT  # "Exam: mass 🙂 seen in the liver.\r\nNo évidence of other mass."
CANDIDATE = "model:candidate@1"
RUN = fx.RUN_IDS[0]
_NOW = datetime(2026, 1, 2, tzinfo=UTC)


def _mention(quote: str, occurrence: int = 0, mention_type: str = "finding") -> Mention:
    start = -1
    for _ in range(occurrence + 1):
        start = TEXT.index(quote, start + 1)
    found = exact_mention(fx.SOURCE, quote, start, mention_type)
    assert isinstance(found, Mention)
    return found


def _reading(mention: Mention, producer: str, **fields: Any) -> MentionReading:
    return MentionReading(
        reading_id=new_ulid(),
        mention_id=mention.mention_id,
        provenance=fx.provenance(producer, RUN),
        **fields,
    )


def _result(
    readings: Sequence[tuple[Mention, Sequence[MentionReading]]],
    *,
    producer: str = CANDIDATE,
    case_id: str = "conformance-case-1",
) -> Any:
    graph = EvidenceGraph(
        source=fx.SOURCE, variables=fx.VARIABLES, sensitivity=Sensitivity.synthetic
    )
    for mention, items in readings:
        graph.register_mention(mention, list(items))
    stamped = fx.provenance(producer, RUN)
    outcome = CaseOutcome(status="insufficient_evidence", reason="Synthetic.", provenance=stamped)
    return create_case_result(
        fx.case(case_id),
        graph=graph,
        run_id=RUN,
        variable_outcomes={
            name: VariableOutcome(variable=name, outcome=outcome) for name in ("finding", "summary")
        },
    )


def _silver_example(result: Any, case_id: str = "conformance-case-1") -> Any:
    _, _, example = import_events(
        result,
        case=fx.case(case_id),
        source=fx.SOURCE,
        variables=fx.VARIABLES,
        actor=fx.IMPORTER,
        provenance=fx.provenance(fx.IMPORTER),
        now=_NOW,
    )
    return example


def _negated(mention: Mention, producer: str, value: bool) -> MentionReading:
    return _reading(mention, producer, attributes={"negation": Attribute(value=value)})


def test_spans_are_read_from_results_with_unicode_and_crlf_offsets() -> None:
    first = _mention("mass")
    second = _mention("mass", occurrence=1)  # after the astral emoji and the CRLF
    result = _result(
        [(first, [_negated(first, CANDIDATE, False)]), (second, [_reading(second, CANDIDATE)])]
    )
    spans = spans_of(result, fx.VARIABLES)
    assert [(span.start, span.end) for span in spans] == [
        (first.start, first.end),
        (second.start, second.end),
    ]
    assert TEXT[spans[1].start : spans[1].end] == "mass"
    assert spans[0].attributes == {"negation": frozenset({(bool, False)})}


def test_rejected_readings_do_not_make_a_span() -> None:
    first = _mention("mass")
    result = _result([(first, [_reading(first, CANDIDATE, status="rejected")])])
    assert spans_of(result, fx.VARIABLES) == ()


def test_the_scorer_satisfies_the_protocol_and_scores_silver_examples() -> None:
    scorer = MentionSpanScorer()
    assert isinstance(scorer, Scorer) and "mentions" in scorer.needs
    first, second, liver = _mention("mass"), _mention("mass", 1), _mention("liver")
    gold = _silver_example(
        _result(
            [
                (first, [_negated(first, fx.TEACHER, False)]),
                (second, [_negated(second, fx.TEACHER, True)]),
            ],
            producer=fx.TEACHER,
        )
    )
    predicted = _result(
        [(first, [_negated(first, CANDIDATE, False)]), (liver, [_reading(liver, CANDIDATE)])]
    )
    report = scorer.score([predicted], [gold], variables=fx.VARIABLES)
    assert isinstance(report, ScoreReport)
    values = _values(report.metrics)
    assert values["strict_precision"] == (1, 2) and values["strict_recall"] == (1, 2)
    assert values["attribute_accuracy:negation"] == (1, 1)
    assert report.example_ids == (gold.example_id,) and report.provisional
    assert all(metric.kind in ("evidence", "label") for metric in report.metrics)


def test_a_missing_prediction_counts_every_reference_span_as_missed() -> None:
    first = _mention("mass")
    gold = _silver_example(_result([(first, [_reading(first, fx.TEACHER)])], producer=fx.TEACHER))
    report = MentionSpanScorer().score([], [gold], variables=fx.VARIABLES)
    assert _values(report.metrics)["strict_recall"] == (0, 1)


def test_predictions_for_unreferenced_cases_are_ignored() -> None:
    first = _mention("mass")
    gold = _silver_example(_result([(first, [_reading(first, fx.TEACHER)])], producer=fx.TEACHER))
    other = _result([(first, [_reading(first, CANDIDATE)])], case_id="conformance-case-2")
    report = MentionSpanScorer().score([other], [gold], variables=fx.VARIABLES)
    assert _values(report.metrics)["strict_precision"] == (0, 0)


def test_adjudicated_references_admit_only_reviewed_readings() -> None:
    # The adjudicator verifies the reading of "mass"; an imported reading of "liver" is never
    # reviewed, so the adjudicated Example keeps it as proposed and it is not a reference span.
    liver = _mention("liver")
    log, ids = fx.blind_streams()
    log.add("mention.create", fx.IMPORTER, "importer", target_id=liver.mention_id)
    reading_id = log._id()
    reading = {
        "reading_id": reading_id,
        "mention_id": liver.mention_id,
        "provenance": fx.provenance(fx.TEACHER).model_dump(mode="json"),
        "status": "proposed",
    }
    log.add(
        "reading.create", fx.IMPORTER, "importer", target_id=reading_id, after={"reading": reading}
    )
    fx.adjudicate(log, ids)
    case = fx.case()
    spans = (*fx.SPANS, liver)
    projection = replay(
        log.events, case=case, source=fx.SOURCE, variables=fx.VARIABLES, spans=spans
    )
    example = example_from_projection(
        projection, case=case, source=fx.SOURCE, variables=fx.VARIABLES
    )
    assert {item.status for item in example.mention_readings} == {"verified", "proposed"}
    found = spans_of(example, fx.VARIABLES)
    assert [(span.start, span.end) for span in found] == [(fx.MENTION.start, fx.MENTION.end)]
    report = MentionSpanScorer().score([], [example], variables=fx.VARIABLES)
    assert not report.provisional
    assert _values(report.metrics)["strict_recall"] == (0, 1)


def test_label_records_leave_span_metrics_inconclusive() -> None:
    record = LabelRecord(
        schema_version="amber/0.1",
        case_id="conformance-case-1",
        variable="summary",
        variable_set=fx.VARIABLES.name,
        variable_set_version=fx.VARIABLES.version,
        variable_set_ref=fx.VARIABLES.schema_ref,
        tier="silver",
        outcome_status="answered",
        value={"present": True},
        producer=fx.TEACHER,
        split="dev",
    )
    report = MentionSpanScorer().score([], [record], variables=fx.VARIABLES)
    assert report.metrics == ()
    assert "strict_f1" in report.inconclusive and "attribute_accuracy" in report.inconclusive


def test_self_reference_is_refused() -> None:
    first = _mention("mass")
    gold = _silver_example(_result([(first, [_reading(first, CANDIDATE)])], producer=CANDIDATE))
    predicted = _result([(first, [_reading(first, CANDIDATE)])])
    with pytest.raises(ScoringRefused) as caught:
        MentionSpanScorer().score([predicted], [gold], variables=fx.VARIABLES)
    assert caught.value.code == "self_reference"


def test_single_review_references_must_include_the_move_on_decision() -> None:
    log, ids = fx.silver_import()
    fx.raise_to_single_review(log, ids)
    case = fx.case()
    projection = replay(
        log.events, case=case, source=fx.SOURCE, variables=fx.VARIABLES, spans=fx.SPANS
    )
    example = example_from_projection(
        projection, case=case, source=fx.SOURCE, variables=fx.VARIABLES
    )
    later = fx.fixed_ulid(10_000_000)
    with pytest.raises(ScoringRefused) as caught:
        MentionSpanScorer(move_on_event=later).score([], [example], variables=fx.VARIABLES)
    assert caught.value.code == "move_on_not_included"
    shared = set.intersection(
        *(set(ref.derived_from_events) for ref in example.references.values())
    )
    decision = max(shared)  # the reviewer's submission, which every reference includes
    report = MentionSpanScorer(move_on_event=decision).score([], [example], variables=fx.VARIABLES)
    assert report.provisional


def test_duplicate_or_mismatched_records_are_refused() -> None:
    first = _mention("mass")
    predicted = _result([(first, [_reading(first, CANDIDATE)])])
    gold = _silver_example(_result([(first, [_reading(first, fx.TEACHER)])], producer=fx.TEACHER))
    scorer = MentionSpanScorer()
    with pytest.raises(ValueError, match="duplicate_prediction"):
        scorer.score([predicted, predicted], [gold], variables=fx.VARIABLES)
    with pytest.raises(ValueError, match="duplicate_reference"):
        scorer.score([predicted], [gold, gold], variables=fx.VARIABLES)
    with pytest.raises(ValueError, match="variable_set_mismatch"):
        scorer.score([predicted], [gold], variables=fx.NOTE_VARIABLES)


def test_the_scorer_passes_the_conformance_kit() -> None:
    from amber.conformance.checks import Subject
    from amber.conformance.report import run_checks

    report = run_checks(
        Subject(name="span-scorer", version="1", targets={"Scorer": MentionSpanScorer})
    )
    assert report.conforms("Scorer")
    assert report.counts()["failed"] == 0 and report.counts()["passed"] > 0


def test_long_augmenting_paths_do_not_recurse() -> None:
    # Prediction k overlaps gold k and k+1, and the last prediction holds gold 0. In canonical
    # order each new prediction first searches the whole chain below it before taking gold k+1,
    # a path deeper than Python's recursion limit.
    size = 1200
    gold = [_span(2 * k, 2 * k + 2) for k in range(size)]
    predicted = [_span(2 * k + 1, 2 * k + 3) for k in range(size - 1)] + [_span(0, 1)]
    values = _values(span_metrics(predicted, gold))
    assert values["lenient_precision"] == (size, size)
