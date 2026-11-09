"""Regression gate (amber-a6d.11): a ScoreReport compared to a baseline with tolerances."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from amber.regression import Baseline, LowSupport, baseline_from, compare
from amber.schemas import LabelRecord
from amber.scoring import Metric, ScoreReport


def _report(
    metrics: dict[str, tuple[float, int]],
    *,
    scorer: str = "amber-mention-spans",
    example_ids: tuple[str, ...] = (),
    inconclusive: tuple[str, ...] = (),
) -> ScoreReport:
    return ScoreReport(
        scorer=scorer,
        metrics=tuple(
            Metric(name=name, kind="evidence", numerator=num, denominator=den)
            for name, (num, den) in metrics.items()
        ),
        example_ids=example_ids,
        provisional=False,
        inconclusive=inconclusive,
    )


def _label(case_id: str, present: bool = True) -> LabelRecord:
    return LabelRecord(
        schema_version="amber/0.1",
        case_id=case_id,
        variable="finding",
        variable_set="synthetic",
        variable_set_version="1",
        variable_set_ref="synthetic-ref",
        tier="silver",
        outcome_status="answered",
        value={"present": present},
        producer="model:synthetic-teacher@1",
        split="dev",
    )


REFS = (_label("case-1"), _label("case-2"))
RELABELLED = (_label("case-1"), _label("case-2", present=False))

BASE = _report({"strict_f1": (160, 200), "strict_f1:site": (8, 10), "tokens_per_case": (500, 10)})


def _baseline(**kwargs: Any) -> Baseline:
    return baseline_from(BASE, **kwargs, references=REFS)


def _status(result: Any, name: str) -> str:
    return {row.name: row.status for row in result.metrics}[name]


def test_an_unchanged_report_passes() -> None:
    result = compare(BASE, _baseline(), references=REFS)
    assert result.status == "passed" and result.exit_code == 0
    assert result.regressions == ()


def test_a_drop_just_beyond_the_tolerance_regresses() -> None:
    # 0.800 - 0.005 = 0.795; 158/200 = 0.790 is beyond it.
    result = compare(
        _report({"strict_f1": (158, 200), "strict_f1:site": (8, 10)}),
        _baseline(metrics=("strict_f1", "strict_f1:site")),
        references=REFS,
    )
    assert result.status == "regressed" and result.exit_code == 2
    assert result.regressions == ("strict_f1",)


def test_a_drop_to_exactly_the_tolerance_passes() -> None:
    # 159/200 = 0.795, exactly baseline - tolerance.
    report = _report({"strict_f1": (159, 200), "strict_f1:site": (8, 10)})
    result = compare(report, _baseline(metrics=("strict_f1", "strict_f1:site")), references=REFS)
    assert _status(result, "strict_f1") == "passed" and result.status == "passed"


def test_improvements_pass() -> None:
    report = _report({"strict_f1": (190, 200), "strict_f1:site": (10, 10)})
    result = compare(report, _baseline(metrics=("strict_f1", "strict_f1:site")), references=REFS)
    assert result.status == "passed"


def test_a_missing_metric_regresses() -> None:
    report = _report({"strict_f1": (160, 200)})
    result = compare(report, _baseline(metrics=("strict_f1", "strict_f1:site")), references=REFS)
    assert _status(result, "strict_f1:site") == "missing"
    assert result.status == "regressed" and result.exit_code == 2
    assert result.regressions == ("strict_f1:site",)


@pytest.mark.parametrize("how", ["zero_denominator", "reported_inconclusive"])
def test_undefined_metrics_are_inconclusive_never_passed(how: str) -> None:
    if how == "zero_denominator":
        report = _report({"strict_f1": (160, 200), "strict_f1:site": (0, 0)})
    else:
        report = _report({"strict_f1": (160, 200)}, inconclusive=("strict_f1:site",))
    result = compare(report, _baseline(metrics=("strict_f1", "strict_f1:site")), references=REFS)
    assert _status(result, "strict_f1:site") == "inconclusive"
    assert result.status == "inconclusive" and result.exit_code == 3


def test_a_regression_outranks_an_inconclusive_metric() -> None:
    report = _report({"strict_f1": (100, 200), "strict_f1:site": (0, 0)})
    result = compare(report, _baseline(metrics=("strict_f1", "strict_f1:site")), references=REFS)
    assert result.status == "regressed"


def test_per_metric_overrides_apply() -> None:
    report = _report({"strict_f1": (150, 200), "strict_f1:site": (8, 10)})
    loose = _baseline(metrics=("strict_f1", "strict_f1:site"), tolerances={"strict_f1": 0.06})
    assert compare(report, loose, references=REFS).status == "passed"  # 0.75 >= 0.80 - 0.06
    tight = _baseline(metrics=("strict_f1", "strict_f1:site"), tolerances={"strict_f1": 0.04})
    assert compare(report, tight, references=REFS).regressions == ("strict_f1",)


def test_low_support_metrics_get_the_larger_tolerance() -> None:
    # strict_f1:site has denominator 10; one fewer true positive drops it by 0.1.
    report = _report({"strict_f1": (160, 200), "strict_f1:site": (7, 10)})
    plain = _baseline(metrics=("strict_f1", "strict_f1:site"))
    assert compare(report, plain, references=REFS).regressions == ("strict_f1:site",)
    lenient = _baseline(
        metrics=("strict_f1", "strict_f1:site"),
        low_support=LowSupport(below=50, tolerance=0.15),
    )
    assert compare(report, lenient, references=REFS).status == "passed"
    # Support equal to the threshold is not low.
    at_threshold = _baseline(
        metrics=("strict_f1", "strict_f1:site"),
        low_support=LowSupport(below=10, tolerance=0.15),
    )
    assert compare(report, at_threshold, references=REFS).regressions == ("strict_f1:site",)
    # The rule follows the baseline's support, and a per-metric override still wins.
    strict = _baseline(
        metrics=("strict_f1", "strict_f1:site"),
        low_support=LowSupport(below=50, tolerance=0.15),
        tolerances={"strict_f1:site": 0.0},
    )
    assert compare(report, strict, references=REFS).regressions == ("strict_f1:site",)


def test_lower_is_better_metrics_regress_upwards() -> None:
    baseline = _baseline(
        metrics=("tokens_per_case",),
        lower_is_better=("tokens_per_case",),
        tolerances={"tokens_per_case": 1.0},
    )
    assert (
        compare(_report({"tokens_per_case": (510, 10)}), baseline, references=REFS).status
        == "passed"
    )  # 51 <= 51
    assert compare(
        _report({"tokens_per_case": (520, 10)}), baseline, references=REFS
    ).regressions == ("tokens_per_case",)
    assert (
        compare(_report({"tokens_per_case": (100, 10)}), baseline, references=REFS).status
        == "passed"
    )


def test_different_gold_is_inconclusive() -> None:
    report = _report({"strict_f1": (199, 200), "strict_f1:site": (10, 10)})
    baseline = _baseline(metrics=("strict_f1", "strict_f1:site"))
    result = compare(report, baseline, references=RELABELLED)
    assert result.status == "inconclusive" and result.reasons == ("reference_changed",)
    fewer = compare(report, baseline, references=REFS[:1])
    assert fewer.reasons == ("reference_changed",)
    reordered = compare(report, baseline, references=tuple(reversed(REFS)))
    assert reordered.status == "passed"


def test_references_must_match_the_report() -> None:
    # A report lists the Examples it scored; the references passed alongside must be those.
    with pytest.raises(ValueError, match="references_mismatch"):
        baseline_from(_report({"strict_f1": (1, 2)}, example_ids=("e1",)), references=REFS)
    with pytest.raises(ValueError, match="references_mismatch"):
        compare(_report({"strict_f1": (1, 2)}, example_ids=("e1",)), _baseline(), references=REFS)


def test_a_different_scorer_is_refused() -> None:
    with pytest.raises(ValueError, match="scorer_mismatch"):
        compare(_report({"strict_f1": (160, 200)}, scorer="other"), _baseline(), references=REFS)


def test_untracked_metrics_are_listed_not_gated() -> None:
    report = _report({"strict_f1": (160, 200), "strict_f1:site": (8, 10), "new_metric": (0, 5)})
    result = compare(report, _baseline(metrics=("strict_f1", "strict_f1:site")), references=REFS)
    assert result.status == "passed" and result.untracked == ("new_metric",)


def test_the_baseline_tracks_every_defined_metric_by_default() -> None:
    report = _report({"strict_f1": (160, 200), "strict_f1:site": (0, 0)})
    baseline = baseline_from(report, references=REFS)
    assert [metric.key for metric in baseline.metrics] == ["strict_f1"]
    assert baseline.references and baseline.scorer == "amber-mention-spans"
    with pytest.raises(ValueError, match="undefined_metric"):
        baseline_from(report, metrics=("strict_f1:site",), references=REFS)
    with pytest.raises(ValueError, match="unknown_metric"):
        baseline_from(report, metrics=("nope",), references=REFS)
    with pytest.raises(ValueError, match="unknown_metric"):
        baseline_from(report, tolerances={"nope": 0.1}, references=REFS)


def test_a_baseline_round_trips_as_json() -> None:
    baseline = _baseline(
        low_support=LowSupport(below=50, tolerance=0.1), tolerances={"strict_f1": 0.01}
    )
    assert Baseline.model_validate_json(baseline.model_dump_json()) == baseline


@pytest.mark.parametrize(
    "change",
    [
        {"default_tolerance": -0.1},
        {"default_tolerance": float("nan")},
        {"metrics": ()},
        {"extra": 1},
    ],
    ids=["negative-tolerance", "nan-tolerance", "no-metrics", "unknown-field"],
)
def test_invalid_baselines_are_refused(change: dict[str, Any]) -> None:
    data = {**_baseline().model_dump(mode="json"), **change}
    with pytest.raises(ValidationError):
        Baseline.model_validate(data)


def test_duplicate_baseline_metrics_are_refused() -> None:
    data = _baseline().model_dump(mode="json")
    data["metrics"] = [data["metrics"][0], data["metrics"][0]]
    with pytest.raises(ValidationError):
        Baseline.model_validate(data)


def test_the_summary_is_a_compact_table_naming_regressions() -> None:
    report = _report({"strict_f1": (158, 200), "strict_f1:site": (8, 10)})
    result = compare(report, _baseline(metrics=("strict_f1", "strict_f1:site")), references=REFS)
    table = result.summary()
    lines = table.splitlines()
    assert lines[0].split() == ["metric", "baseline", "current", "delta", "tolerance", "status"]
    assert any(line.split()[0] == "strict_f1" and line.split()[-1] == "regressed" for line in lines)
    assert "0.8000" in table and "0.7900" in table and "-0.0100" in table
    assert lines[-1].startswith("regressed:")


def test_a_broken_extractor_fails_a_span_baseline() -> None:
    from amber.span_scoring import Span, span_metrics

    def spans(offsets: list[tuple[int, int]]) -> list[Span]:
        return [
            Span(case_id="c", source_id="s", mention_type="finding", start=a, end=b)
            for a, b in offsets
        ]

    def report(predicted: list[tuple[int, int]]) -> ScoreReport:
        gold = spans([(0, 4), (10, 14), (20, 24), (30, 34)])
        return ScoreReport(
            scorer="amber-mention-spans",
            metrics=span_metrics(spans(predicted), gold),
            provisional=False,
        )

    good = report([(0, 4), (10, 14), (20, 24)])
    baseline = baseline_from(good, references=REFS)
    assert compare(good, baseline, references=REFS).exit_code == 0
    # An off-by-one end offset loses strict matches but keeps lenient ones.
    broken = compare(report([(0, 5), (10, 15), (20, 24)]), baseline, references=REFS)
    assert broken.exit_code == 2
    assert "strict_f1" in broken.regressions and "lenient_f1" not in broken.regressions


def _variable_report(
    per_variable: dict[str | None, tuple[float, int]], **kwargs: Any
) -> ScoreReport:
    return ScoreReport(
        scorer="amber-label-correct",
        metrics=tuple(
            Metric(
                name="label_accuracy", kind="label", numerator=num, denominator=den, variable=var
            )
            for var, (num, den) in per_variable.items()
        ),
        provisional=False,
        **kwargs,
    )


def test_per_variable_metrics_are_tracked_separately() -> None:
    base = _variable_report({None: (90, 100), "finding": (45, 50), "summary": (45, 50)})
    baseline = baseline_from(base, references=REFS)
    assert [metric.key for metric in baseline.metrics] == [
        "label_accuracy",
        "label_accuracy[finding]",
        "label_accuracy[summary]",
    ]
    # The overall value collapses while the last variable holds steady: still a regression.
    worse = _variable_report({None: (50, 100), "finding": (5, 50), "summary": (45, 50)})
    assert compare(worse, baseline, references=REFS).regressions == (
        "label_accuracy",
        "label_accuracy[finding]",
    )
    pick = baseline_from(base, metrics=("label_accuracy[finding]",), references=REFS)
    assert [metric.key for metric in pick.metrics] == ["label_accuracy[finding]"]


def test_duplicate_metric_keys_are_refused() -> None:
    twice = _variable_report({None: (1, 2)})
    report = twice.model_copy(update={"metrics": twice.metrics * 2})
    with pytest.raises(ValueError, match="duplicate_metric"):
        baseline_from(report, references=REFS)
    with pytest.raises(ValueError, match="duplicate_metric"):
        compare(report, baseline_from(twice, references=REFS), references=REFS)


def test_examples_are_pinned_by_their_content_ids() -> None:
    from amber.conformance import fixtures as fx

    example = fx.note_example("adjudicated", present=True, producer=fx.TEACHER)
    other = fx.note_example("adjudicated", present=False, producer=fx.TEACHER)
    report = _report({"strict_f1": (160, 200)}, example_ids=(example.example_id,))
    baseline = baseline_from(report, references=(example,))
    assert compare(report, baseline, references=(example,)).status == "passed"
    moved = _report({"strict_f1": (160, 200)}, example_ids=(other.example_id,))
    assert compare(moved, baseline, references=(other,)).reasons == ("reference_changed",)
    both = _report({"strict_f1": (160, 200)}, example_ids=(other.example_id, example.example_id))
    pinned = baseline_from(both, references=(example, other))
    assert compare(both, pinned, references=(other, example)).status == "passed"


def test_options_for_untracked_metrics_are_refused() -> None:
    with pytest.raises(ValueError, match="untracked_metric"):
        baseline_from(
            BASE, metrics=("strict_f1",), lower_is_better=("tokens_per_case",), references=REFS
        )
    with pytest.raises(ValueError, match="untracked_metric"):
        baseline_from(
            BASE, metrics=("strict_f1",), tolerances={"strict_f1:site": 0.1}, references=REFS
        )


def test_the_baseline_value_comes_from_its_counts() -> None:
    data = _baseline().model_dump(mode="json")
    assert all("value" not in metric for metric in data["metrics"])
    assert _baseline().metrics[0].value == 0.8


def test_the_summary_keeps_reasons_alongside_regressions() -> None:
    report = _report({"strict_f1": (100, 200), "strict_f1:site": (0, 0)})
    baseline = _baseline(metrics=("strict_f1", "strict_f1:site"))
    result = compare(report, baseline, references=RELABELLED)
    last = result.summary().splitlines()[-1]
    assert "strict_f1" in last and "reference_changed" in last and "strict_f1:site" in last


def test_the_boundary_holds_for_large_values() -> None:
    # 1e9 tokens with tolerance 0.1: exactly baseline + tolerance passes despite float rounding.
    base = _report({"tokens_per_case": (1e10 + 0.0, 10)})
    baseline = baseline_from(
        base,
        lower_is_better=("tokens_per_case",),
        tolerances={"tokens_per_case": 0.1},
        references=REFS,
    )
    assert (
        compare(_report({"tokens_per_case": (1e10 + 1.0, 10)}), baseline, references=REFS).status
        == "passed"
    )
    assert (
        compare(_report({"tokens_per_case": (1e10 + 2.0, 10)}), baseline, references=REFS).status
        == "regressed"
    )


def test_a_drop_of_exactly_the_tolerance_survives_float_rounding() -> None:
    # 4/5 - 0.2 is 0.6000000000000001 in floating point, but 3/5 is exactly that far below.
    baseline = baseline_from(
        _report({"recall": (4, 5)}), tolerances={"recall": 0.2}, references=REFS
    )
    assert compare(_report({"recall": (3, 5)}), baseline, references=REFS).status == "passed"


def test_a_per_variable_metric_is_inconclusive_when_its_name_is() -> None:
    base = _variable_report({None: (9, 10), "finding": (4, 5)})
    baseline = baseline_from(base, references=REFS)
    undefined = ScoreReport(
        scorer="amber-label-correct",
        metrics=(),
        provisional=False,
        inconclusive=("label_accuracy",),
    )
    result = compare(undefined, baseline, references=REFS)
    assert result.status == "inconclusive" and result.exit_code == 3
