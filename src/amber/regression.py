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

Metrics are keyed by name and variable (``label_accuracy[finding]``); a report with a key twice
is refused (``duplicate_metric``).

A metric's tolerance is its own override if set, else the low-support tolerance when its
baseline denominator is below the threshold, else the default (0.005). The overall status is
``regressed`` if any metric regressed or is missing, else ``inconclusive`` if any metric is or
if the references differ from the baseline's (``reference_changed``), else ``passed``. Reports
from another scorer are refused. Results carry names and numbers only.

Both functions take the references the report was scored against. A report names only its
Examples, so the baseline pins the references themselves: Examples by their content IDs and label
records by a canonical hash of their content, which no report carries. The report's
``example_id``s must be exactly the Examples given (``references_mismatch``).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from amber.ids import canonical_sha256
from amber.schemas import Example, LabelRecord
from amber.scoring import Metric, ScoreReport, tier_mix

BASELINE_VERSION: Literal["amber-baseline/0.1"] = "amber-baseline/0.1"
DEFAULT_TOLERANCE = 0.005
_ROUNDING_ULPS = 8
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
    variable: str | None = None
    numerator: float = Field(ge=0, allow_inf_nan=False)
    denominator: int = Field(strict=True, gt=0)
    lower_is_better: bool = False
    tolerance: _Tolerance | None = None

    @property
    def key(self) -> str:
        return _key(self)

    @property
    def value(self) -> float:
        return self.numerator / self.denominator


def _key(metric: Metric | BaselineMetric) -> str:
    return metric.name if metric.variable is None else f"{metric.name}[{metric.variable}]"


def _by_key(metrics: Sequence[Metric]) -> dict[str, Metric]:
    found: dict[str, Metric] = {}
    for metric in metrics:
        if _key(metric) in found:
            raise ValueError(f"duplicate_metric: {_key(metric)}")
        found[_key(metric)] = metric
    return found


class Baseline(BaseModel):
    """The metrics a gate tracks, the references they were scored against, and tolerances."""

    model_config = _FROZEN

    schema_version: Literal["amber-baseline/0.1"] = BASELINE_VERSION
    scorer: str = Field(min_length=1)
    references: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: tuple[BaselineMetric, ...] = Field(min_length=1)
    default_tolerance: _Tolerance = DEFAULT_TOLERANCE
    low_support: LowSupport | None = None

    @model_validator(mode="after")
    def require_distinct_metrics(self) -> Baseline:
        keys = [metric.key for metric in self.metrics]
        if len(keys) != len(set(keys)):
            raise ValueError("baseline metric keys must be distinct")
        return self

    def tolerance(self, metric: BaselineMetric) -> float:
        if metric.tolerance is not None:
            return metric.tolerance
        if self.low_support is not None and metric.denominator < self.low_support.below:
            return self.low_support.tolerance
        return self.default_tolerance


def _references(report: ScoreReport, references: Sequence[Example | LabelRecord]) -> str:
    """SHA-256 of the references, after checking they match the report's Examples and tier mix.

    Label records cannot be checked against the report beyond their tiers: callers pass the
    references the report was scored on.
    """
    examples = sorted({record.example_id for record in references if isinstance(record, Example)})
    if examples != sorted(set(report.example_ids)) or tier_mix(references) != dict(report.tier_mix):
        raise ValueError("references_mismatch")
    labels = sorted(
        canonical_sha256(record.model_dump(mode="json"))
        for record in references
        if isinstance(record, LabelRecord)
    )
    return canonical_sha256({"examples": examples, "label_records": labels})


def baseline_from(
    report: ScoreReport,
    *,
    references: Sequence[Example | LabelRecord],
    metrics: Collection[str] | None = None,
    lower_is_better: Collection[str] = (),
    tolerances: Mapping[str, float] | None = None,
    default_tolerance: float = DEFAULT_TOLERANCE,
    low_support: LowSupport | None = None,
) -> Baseline:
    """A baseline tracking ``metrics`` by key (default: every defined metric in the report).

    Options naming a metric the report lacks raise ``unknown_metric``; options for a metric the
    baseline does not track raise ``untracked_metric``.
    """
    pinned = _references(report, references)
    found = _by_key(report.metrics)
    tolerances = dict(tolerances or {})
    named = set(metrics or ()) | set(lower_is_better) | set(tolerances)
    if unknown := sorted(named - found.keys()):
        raise ValueError(f"unknown_metric: {', '.join(unknown)}")
    if metrics is None:
        tracked = [key for key, metric in found.items() if metric.value is not None]
    else:
        tracked = list(dict.fromkeys(metrics))
        if undefined := [key for key in tracked if found[key].value is None]:
            raise ValueError(f"undefined_metric: {', '.join(undefined)}")
    if untracked := sorted((set(lower_is_better) | set(tolerances)) - set(tracked)):
        raise ValueError(f"untracked_metric: {', '.join(untracked)}")
    entries = [
        BaselineMetric(
            name=found[key].name,
            variable=found[key].variable,
            numerator=found[key].numerator,
            denominator=found[key].denominator,
            lower_is_better=key in lower_is_better,
            tolerance=tolerances.get(key),
        )
        for key in tracked
    ]
    return Baseline(
        scorer=report.scorer,
        references=pinned,
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
    baseline_support: int
    current_support: int | None

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
        undefined = [m.name for m in self.metrics if m.status == "inconclusive"]
        parts = []
        if self.regressions:
            parts.append(f"regressed: {', '.join(self.regressions)}")
        if self.reasons or undefined:
            parts.append(f"inconclusive: {', '.join([*self.reasons, *undefined])}")
        lines.append("; ".join(parts) or f"passed: {len(self.metrics)} metrics")
        return "\n".join(lines)


def _regressed(current: float, metric: BaselineMetric, tolerance: float) -> bool:
    """Past the boundary, allowing rounding relative to the values' size."""
    slack = _ROUNDING_ULPS * math.ulp(max(1.0, abs(metric.value), abs(current)))
    if metric.lower_is_better:
        return current > metric.value + tolerance + slack
    return current < metric.value - tolerance - slack


def compare(
    report: ScoreReport,
    baseline: Baseline,
    *,
    references: Sequence[Example | LabelRecord],
) -> RegressionResult:
    """Compare ``report`` to ``baseline``; never writes or changes the baseline."""
    if report.scorer != baseline.scorer:
        raise ValueError("scorer_mismatch")
    pinned = _references(report, references)
    found = _by_key(report.metrics)
    rows = []
    for metric in baseline.metrics:
        tolerance = baseline.tolerance(metric)
        reported = found.get(metric.key)
        current = None if reported is None else reported.value
        status: MetricStatus
        undefined = {metric.key, metric.name} & set(report.inconclusive)
        if undefined or (reported is not None and current is None):
            status = "inconclusive"
        elif current is None:
            status = "missing"
        elif _regressed(current, metric, tolerance):
            status = "regressed"
        else:
            status = "passed"
        rows.append(
            MetricComparison(
                name=metric.key,
                status=status,
                baseline=metric.value,
                current=current,
                tolerance=tolerance,
                lower_is_better=metric.lower_is_better,
                baseline_support=metric.denominator,
                current_support=None if reported is None else reported.denominator,
            )
        )
    reasons = ("reference_changed",) if pinned != baseline.references else ()
    statuses = {row.status for row in rows}
    outcome: RegressionOutcome
    if statuses & {"regressed", "missing"}:
        outcome = "regressed"
    elif "inconclusive" in statuses or reasons:
        outcome = "inconclusive"
    else:
        outcome = "passed"
    tracked = {metric.key for metric in baseline.metrics}
    return RegressionResult(
        status=outcome,
        metrics=tuple(rows),
        reasons=reasons,
        untracked=tuple(sorted(found.keys() - tracked)),
        provisional=report.provisional,
    )
