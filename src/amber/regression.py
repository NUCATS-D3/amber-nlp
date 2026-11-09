"""A regression gate: a ``ScoreReport`` compared to a committed baseline (amber-a6d.11).

``baseline_from`` turns a report into a ``Baseline``; the consumer stores it, and amber never
writes one. ``compare`` checks a new report against it, metric by metric:

- ``regressed``: the value moved past the tolerance in the wrong direction (below
  ``baseline - tolerance``, or above ``baseline + tolerance`` for lower-is-better metrics);
  landing exactly on the boundary passes;
- ``missing``: a tracked metric is absent from the report, which counts as a regression;
- ``inconclusive``: the metric is undefined (zero denominator) or reported inconclusive, and
  never passes;
- ``passed``: anything else, including improvements.

A metric's tolerance is its own override if set, else the low-support tolerance when its
baseline denominator is below the threshold, else the default (0.005). The overall status is
``regressed`` if any metric regressed or is missing, else ``inconclusive`` if any metric is, or
if the report was scored against other references than the baseline (``reference_changed``),
else ``passed``. Reports from another scorer are refused. Results carry names and numbers only.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from amber.scoring import ScoreReport

BASELINE_VERSION: Literal["amber-baseline/0.1"] = "amber-baseline/0.1"
DEFAULT_TOLERANCE = 0.005
_EPSILON = 1e-12
_FROZEN = ConfigDict(extra="forbid", frozen=True)
_Tolerance = Annotated[float, Field(ge=0, allow_inf_nan=False)]
MetricStatus: TypeAlias = Literal["passed", "regressed", "missing", "inconclusive"]
RegressionOutcome: TypeAlias = Literal["passed", "regressed", "inconclusive"]


class LowSupport(BaseModel):
    """Metrics whose baseline denominator is below ``below`` use ``tolerance`` instead."""

    model_config = _FROZEN

    below: int = Field(strict=True, gt=0)
    tolerance: _Tolerance


class BaselineMetric(BaseModel):
    model_config = _FROZEN

    name: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    numerator: float = Field(ge=0, allow_inf_nan=False)
    denominator: int = Field(strict=True, gt=0)
    lower_is_better: bool = False
    tolerance: _Tolerance | None = None


class Baseline(BaseModel):
    """The metrics a gate tracks, the references they were scored against, and tolerances."""

    model_config = _FROZEN

    schema_version: Literal["amber-baseline/0.1"] = BASELINE_VERSION
    scorer: str = Field(min_length=1)
    example_ids: tuple[str, ...]
    metrics: tuple[BaselineMetric, ...] = Field(min_length=1)
    default_tolerance: _Tolerance = DEFAULT_TOLERANCE
    low_support: LowSupport | None = None

    @model_validator(mode="after")
    def require_distinct_metrics(self) -> Baseline:
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("baseline metric names must be distinct")
        return self

    def tolerance(self, metric: BaselineMetric) -> float:
        if metric.tolerance is not None:
            return metric.tolerance
        if self.low_support is not None and metric.denominator < self.low_support.below:
            return self.low_support.tolerance
        return self.default_tolerance


def baseline_from(
    report: ScoreReport,
    *,
    metrics: Collection[str] | None = None,
    lower_is_better: Collection[str] = (),
    tolerances: Mapping[str, float] | None = None,
    default_tolerance: float = DEFAULT_TOLERANCE,
    low_support: LowSupport | None = None,
) -> Baseline:
    """A baseline tracking ``metrics`` (default: every defined metric in the report)."""
    found = {metric.name: metric for metric in report.metrics}
    tolerances = dict(tolerances or {})
    named = set(metrics or ()) | set(lower_is_better) | set(tolerances)
    if unknown := sorted(named - found.keys()):
        raise ValueError(f"unknown_metric: {', '.join(unknown)}")
    if metrics is None:
        tracked = [name for name, metric in found.items() if metric.value is not None]
    else:
        tracked = list(dict.fromkeys(metrics))
        if undefined := [name for name in tracked if found[name].value is None]:
            raise ValueError(f"undefined_metric: {', '.join(undefined)}")
    entries = []
    for name in tracked:
        metric = found[name]
        assert metric.value is not None
        entries.append(
            BaselineMetric(
                name=name,
                value=metric.value,
                numerator=metric.numerator,
                denominator=metric.denominator,
                lower_is_better=name in lower_is_better,
                tolerance=tolerances.get(name),
            )
        )
    return Baseline(
        scorer=report.scorer,
        example_ids=report.example_ids,
        metrics=tuple(entries),
        default_tolerance=default_tolerance,
        low_support=low_support,
    )


class MetricComparison(BaseModel):
    model_config = _FROZEN

    name: str
    status: MetricStatus
    baseline: float
    current: float | None
    tolerance: float
    lower_is_better: bool

    @property
    def delta(self) -> float | None:
        return None if self.current is None else self.current - self.baseline


class RegressionResult(BaseModel):
    """Per-metric comparisons and the overall outcome; ``exit_code`` is 0, 2, or 3."""

    model_config = _FROZEN

    status: RegressionOutcome
    metrics: tuple[MetricComparison, ...]
    reasons: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    provisional: bool

    @property
    def regressions(self) -> tuple[str, ...]:
        return tuple(m.name for m in self.metrics if m.status in ("regressed", "missing"))

    @property
    def exit_code(self) -> int:
        return {"passed": 0, "regressed": 2, "inconclusive": 3}[self.status]

    def summary(self) -> str:
        """A fixed-width table, then the regressed metrics or the inconclusive reasons."""

        def number(value: float | None, signed: bool = False) -> str:
            return "-" if value is None else f"{value:+.4f}" if signed else f"{value:.4f}"

        width = max([len("metric"), *(len(m.name) for m in self.metrics)])
        header = ("metric", "baseline", "current", "delta", "tolerance", "status")
        rows = [header] + [
            (
                m.name,
                number(m.baseline),
                number(m.current),
                number(m.delta, signed=True),
                number(m.tolerance),
                m.status,
            )
            for m in self.metrics
        ]
        lines = [
            f"{row[0]:<{width}}  " + "  ".join(f"{cell:>9}" for cell in row[1:]) for row in rows
        ]
        if self.regressions:
            lines.append(f"regressed: {', '.join(self.regressions)}")
        elif self.status == "inconclusive":
            names = [m.name for m in self.metrics if m.status == "inconclusive"]
            lines.append(f"inconclusive: {', '.join([*self.reasons, *names])}")
        else:
            lines.append(f"passed: {len(self.metrics)} metrics")
        return "\n".join(lines)


def _regressed(current: float, metric: BaselineMetric, tolerance: float) -> bool:
    if metric.lower_is_better:
        return current > metric.value + tolerance + _EPSILON
    return current < metric.value - tolerance - _EPSILON


def compare(report: ScoreReport, baseline: Baseline) -> RegressionResult:
    """Compare ``report`` to ``baseline``; never writes or changes the baseline."""
    if report.scorer != baseline.scorer:
        raise ValueError("scorer_mismatch")
    found = {metric.name: metric for metric in report.metrics}
    rows = []
    for metric in baseline.metrics:
        tolerance = baseline.tolerance(metric)
        reported = found.get(metric.name)
        current = None if reported is None else reported.value
        status: MetricStatus
        if metric.name in report.inconclusive or (reported is not None and current is None):
            status = "inconclusive"
        elif current is None:
            status = "missing"
        elif _regressed(current, metric, tolerance):
            status = "regressed"
        else:
            status = "passed"
        rows.append(
            MetricComparison(
                name=metric.name,
                status=status,
                baseline=metric.value,
                current=current,
                tolerance=tolerance,
                lower_is_better=metric.lower_is_better,
            )
        )
    reasons = () if set(report.example_ids) == set(baseline.example_ids) else ("reference_changed",)
    statuses = {row.status for row in rows}
    outcome: RegressionOutcome
    if statuses & {"regressed", "missing"}:
        outcome = "regressed"
    elif "inconclusive" in statuses or reasons:
        outcome = "inconclusive"
    else:
        outcome = "passed"
    tracked = {metric.name for metric in baseline.metrics}
    return RegressionResult(
        status=outcome,
        metrics=tuple(rows),
        reasons=reasons,
        untracked=tuple(sorted(found.keys() - tracked)),
        provisional=report.provisional,
    )
