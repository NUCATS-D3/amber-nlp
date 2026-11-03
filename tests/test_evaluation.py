"""The binary metric harness (CORAL protocol 1.1.0 gates; bead amber-bc9.6 acceptance: the
protocol's arithmetic examples and the P=0, Q=0, and no-automatic-answer edge cases)."""

from __future__ import annotations

from typing import Any

import pytest

from amber.conformance import fixtures as fx
from amber.evaluation import (
    BOOTSTRAP_SEED,
    GateSpec,
    Gold,
    Measure,
    Prediction,
    bootstrap,
    evaluate_binary,
    gold_from,
    prediction_from,
    wilson_interval,
)
from amber.schemas import LabelRecord, ReferenceTier

ADJ = ReferenceTier.adjudicated
GATES = (
    GateSpec(name="precision", measure="precision", op="ge", threshold=0.95),
    GateSpec(name="zero_false_positives", measure="false_positives", op="eq", threshold=0),
    GateSpec(name="recall", measure="recall", op="ge", threshold=0.85),
    GateSpec(name="omissions", measure="omission_rate", op="le", threshold=0.15),
    GateSpec(name="coverage", measure="coverage", op="ge", threshold=0.60),
    GateSpec(name="no_failures", measure="execution_failures", op="eq", threshold=0),
)


def _gold(
    case_id: str, value: bool | None, tier: ReferenceTier = ADJ, evidence: bool = True
) -> Gold:
    return Gold(
        case_id, "answered" if value is not None else "not_mentioned", value, tier, evidence
    )


def _answer(case_id: str, value: bool) -> Prediction:
    return Prediction(case_id, "answered", value)


def _abstain(case_id: str) -> Prediction:
    return Prediction(case_id, "insufficient_evidence", None)


def _evaluate(
    eligible: list[str], gold: list[Gold], predicted: list[Prediction], **kwargs: Any
) -> Any:
    kwargs.setdefault("resamples", 500)
    return evaluate_binary(eligible, gold, predicted, GATES, **kwargs)


def _statuses(report: Any) -> dict[str, str]:
    return {gate.name: gate.status for gate in report.gates}


def _value(report: Any, name: str) -> float | None:
    return report.measures[name].value


# The protocol's arithmetic example ---------------------------------------------------------


def _protocol_example() -> tuple[list[str], list[Gold], list[Prediction]]:
    eligible = [f"note-{index}" for index in range(1, 11)]
    gold = [
        *(_gold(eligible[index], True) for index in range(0, 4)),
        *(_gold(eligible[index], False) for index in range(4, 8)),
        _gold(eligible[8], None),
        _gold(eligible[9], None),
    ]
    predicted = [
        *(_answer(eligible[index], True) for index in range(0, 3)),
        *(_answer(eligible[index], False) for index in range(4, 7)),
        *(_abstain(eligible[index]) for index in (3, 7, 8, 9)),
    ]
    return eligible, gold, predicted


def test_the_protocol_arithmetic_example() -> None:
    report = _evaluate(*_protocol_example())
    counts = report.counts
    assert (counts.N, counts.B, counts.P, counts.Q) == (10, 8, 4, 4)
    assert (counts.A, counts.C, counts.A_pos, counts.TP, counts.FP, counts.O) == (6, 6, 3, 3, 0, 2)
    assert _value(report, "precision") == 1.0
    assert _value(report, "ppv") == 1.0
    assert _value(report, "recall") == 0.75
    assert _value(report, "omission_rate") == 0.25
    assert _value(report, "coverage") == 0.6
    assert _value(report, "correct_yield") == 0.6
    assert _statuses(report) == {
        "precision": "met",
        "zero_false_positives": "met",
        "recall": "unmet",
        "omissions": "unmet",
        "coverage": "met",
        "no_failures": "met",
    }
    assert report.overall == "unmet"
    assert not report.provisional and report.incomplete == ()
    recall = next(gate for gate in report.gates if gate.name == "recall")
    assert (recall.numerator, recall.denominator) == (3, 4)


def test_a_partition_without_gold_positives_has_undefined_recall() -> None:
    eligible = ["n1", "n2", "n3"]
    gold = [_gold("n1", False), _gold("n2", False), _gold("n3", False)]
    predicted = [_answer("n1", False), _answer("n2", False), _answer("n3", False)]
    report = _evaluate(eligible, gold, predicted)
    assert _value(report, "recall") is None
    assert _statuses(report)["recall"] == "inconclusive"
    assert report.overall == "inconclusive"
    assert "recall" not in report.wilson
    assert report.bootstrap["recall"].low is None


def test_without_gold_negatives_zero_false_positives_is_inconclusive() -> None:
    eligible = ["n1", "n2"]
    report = _evaluate(
        eligible, [_gold("n1", True), _gold("n2", True)], [_answer("n1", True), _answer("n2", True)]
    )
    assert report.counts.FP == 0 and report.counts.Q == 0
    assert _value(report, "false_positives") is None
    assert _statuses(report)["zero_false_positives"] == "inconclusive"


def test_no_automatic_answers_leave_precision_undefined_and_coverage_unmet() -> None:
    eligible, gold, _ = _protocol_example()
    report = _evaluate(eligible, gold, [_abstain(case_id) for case_id in eligible])
    assert _value(report, "precision") is None
    assert _value(report, "coverage") == 0.0
    assert _statuses(report)["precision"] == "inconclusive"
    assert _statuses(report)["coverage"] == "unmet"
    assert report.counts.O == 8
    assert report.overall == "unmet"


def test_answers_on_gold_non_answers_are_incorrect_and_positives_count_as_false_positives() -> None:
    eligible = ["n1", "n2", "n3"]
    gold = [_gold("n1", None), _gold("n2", None), _gold("n3", False)]
    predicted = [_answer("n1", True), _answer("n2", False), _answer("n3", False)]
    report = _evaluate(eligible, gold, predicted)
    assert (report.counts.A, report.counts.C, report.counts.FP) == (3, 1, 1)
    assert _value(report, "false_positives") == 1
    assert _statuses(report)["zero_false_positives"] == "unmet"


def test_failures_are_omissions_counted_by_kind() -> None:
    eligible = ["n1", "n2", "n3"]
    gold = [_gold("n1", True), _gold("n2", False), _gold("n3", False)]
    predicted = [
        _answer("n1", True),
        Prediction("n2", "failed", None, "provider"),
        Prediction("n3", "failed", None, "budget"),
    ]
    report = _evaluate(eligible, gold, predicted)
    assert report.counts.failures == {"provider": 1, "budget": 1}
    assert report.counts.O == 2
    assert _value(report, "execution_failures") == 2
    assert _statuses(report)["no_failures"] == "unmet"


# Tiers, label-only references, and completeness --------------------------------------------


def test_provisional_passes_are_never_met() -> None:
    eligible = ["n1", "n2"]
    gold = [_gold("n1", True, ReferenceTier.single_review), _gold("n2", False)]
    report = _evaluate(eligible, gold, [_answer("n1", True), _answer("n2", False)])
    assert report.provisional
    assert report.tier_mix == {ReferenceTier.single_review: 1, ADJ: 1}
    precision = next(gate for gate in report.gates if gate.name == "precision")
    assert (precision.status, precision.observed_pass, precision.provisional) == (
        "inconclusive",
        True,
        True,
    )
    assert "met" not in _statuses(report).values()
    assert report.overall == "inconclusive"

    wrong = _evaluate(eligible, gold, [_answer("n1", False), _answer("n2", False)])
    assert _statuses(wrong)["precision"] == "unmet" and wrong.overall == "unmet"


def test_evidence_measures_are_inconclusive_against_label_only_references() -> None:
    eligible = ["n1"]
    unsupported = Measure(name="unsupported_claims", kind="count", numerator=0, evidence=True)
    gates = (GateSpec(name="supported", measure="unsupported_claims", op="eq", threshold=0),)
    with_examples = evaluate_binary(
        eligible, [_gold("n1", True)], [_answer("n1", True)], gates,
        extra_measures={"unsupported_claims": unsupported}, resamples=10,
    )  # fmt: skip
    assert _statuses(with_examples) == {"supported": "met"}
    silver = [_gold("n1", True, ReferenceTier.silver, evidence=False)]
    labels_only = evaluate_binary(
        eligible, silver, [_answer("n1", True)], gates,
        extra_measures={"unsupported_claims": unsupported}, resamples=10,
    )  # fmt: skip
    assert _statuses(labels_only) == {"supported": "inconclusive"}
    assert labels_only.label_only


def test_missing_gold_or_unattempted_cases_prohibit_an_overall_met() -> None:
    eligible = ["n1", "n2", "n3"]
    gold = [_gold("n1", True), _gold("n2", False), _gold("n3", True)]
    complete = _evaluate(
        eligible, gold, [_answer("n1", True), _answer("n2", False), _answer("n3", True)]
    )
    assert complete.overall == "met"

    abstains = [_answer("n1", True), _answer("n2", False), _abstain("n3")]
    missing_gold = _evaluate(eligible, gold[:2], abstains)
    assert missing_gold.incomplete == ("missing_gold",)
    assert missing_gold.overall == "inconclusive"

    unattempted = _evaluate(eligible, gold, [_answer("n1", True), _answer("n2", False)])
    assert unattempted.incomplete == ("unattempted",)
    assert unattempted.counts.O == 1
    assert unattempted.overall == "unmet"


def test_mean_cost_divides_by_all_eligible_cases() -> None:
    eligible, gold, predicted = _protocol_example()
    costs = {case_id: 0.1 for case_id in eligible[:5]}
    report = _evaluate(eligible, gold, predicted, costs=costs)
    assert _value(report, "mean_cost") == pytest.approx(0.05)
    assert "mean_cost" not in report.wilson


# Intervals ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("successes", "trials", "low", "high"),
    [(6, 6, 0.6097, 1.0), (3, 4, 0.3006, 0.9544), (0, 10, 0.0, 0.2775), (5, 10, 0.2366, 0.7634)],
)
def test_wilson_intervals(successes: int, trials: int, low: float, high: float) -> None:
    interval = wilson_interval(successes, trials)
    assert interval is not None
    assert interval == pytest.approx((low, high), abs=1e-4)


def test_wilson_needs_trials() -> None:
    assert wilson_interval(0, 0) is None


def test_every_defined_proportion_gets_a_wilson_interval() -> None:
    report = _evaluate(*_protocol_example())
    assert set(report.wilson) == {
        "precision",
        "ppv",
        "recall",
        "omission_rate",
        "coverage",
        "correct_yield",
    }
    assert report.wilson["precision"] == pytest.approx((0.6097, 1.0), abs=1e-4)


def test_the_bootstrap_is_seeded_case_level_and_reports_undefined_replicates() -> None:
    eligible, gold, predicted = _protocol_example()
    first = _evaluate(eligible, gold, predicted, resamples=1000)
    second = _evaluate(eligible, gold, predicted, resamples=1000)
    assert first.bootstrap == second.bootstrap
    recall = first.bootstrap["recall"]
    assert recall.low is not None and recall.high is not None
    assert recall.low <= 0.75 <= recall.high
    assert recall.valid + recall.undefined == 1000
    assert recall.undefined > 0 and recall.conditional
    assert first.bootstrap["coverage"].undefined == 0


def test_bootstrap_resamples_whole_cases_with_its_own_generator() -> None:
    seen: list[tuple[str, ...]] = []

    def statistic(sample: Any) -> dict[str, float | None]:
        seen.append(tuple(sample))
        return {"size": float(len(sample))}

    intervals = bootstrap(["a", "b", "c"], statistic, resamples=50, seed=BOOTSTRAP_SEED)
    assert intervals["size"].low == intervals["size"].high == 3.0
    assert all(set(sample) <= {"a", "b", "c"} for sample in seen)
    assert len({sample for sample in seen[1:]}) > 1


# Adapters ----------------------------------------------------------------------------------


def test_gold_and_predictions_come_from_examples_records_and_results() -> None:
    variable = fx.NOTE_VARIABLES.name
    example = fx.note_example("adjudicated", present=False, producer=fx.TEACHER)
    gold = gold_from(example, variable=variable, field="present")
    assert gold == Gold(example.case_id, "answered", False, ADJ, True)

    record = LabelRecord(
        schema_version="amber/0.1",
        case_id="note-x",
        variable=variable,
        variable_set=fx.NOTE_VARIABLES.name,
        variable_set_version=fx.NOTE_VARIABLES.version,
        variable_set_ref=fx.NOTE_VARIABLES.schema_ref,
        tier="silver",
        outcome_status="answered",
        value={"present": True},
        producer=fx.TEACHER,
        split=None,
    )
    assert gold_from(record, variable=variable, field="present") == Gold(
        "note-x", "answered", True, ReferenceTier.silver, False
    )
    with pytest.raises(ValueError, match="not binary"):
        gold_from(record, variable=variable, field="missing")
    with pytest.raises(ValueError, match="another variable"):
        gold_from(record, variable="other", field="present")

    result = fx.note_result(fx.RUN_IDS[0], present=True, producer="model:x@1")
    assert prediction_from(result, variable=variable, field="present") == Prediction(
        result.case_id, "answered", True, None
    )
    with pytest.raises(ValueError):
        prediction_from(result, variable="other", field="present")
