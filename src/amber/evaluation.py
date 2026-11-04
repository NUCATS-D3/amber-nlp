"""A tier-aware metric harness for binary answers (contract v0.1 sections 4.2 and 6.2;
CORAL protocol 1.1.0, "Pilot acceptance gates").

Counts follow the protocol's definitions over a fixed set of eligible cases:

- ``N`` eligible cases; ``B`` with a gold binary answer, ``P`` gold true, ``Q`` gold false;
- ``A`` complete automatic binary answers; ``C`` those matching gold (either boolean on a gold
  non-answer is incorrect); ``A_pos`` automatic ``true``, ``TP`` of those gold true,
  ``FP = A_pos - TP``;
- ``O`` gold-binary cases without an automatic answer (abstentions, failures, missing runs).

A zero denominator is undefined, never 0 or 1. Gates are consumer configuration; each is
``met``, ``unmet``, or ``inconclusive`` on the observed point estimate. Any reference below
``adjudicated`` makes a gate provisional, and a provisional pass is reported ``inconclusive``
with ``observed_pass`` set, never ``met``. Evidence measures are inconclusive against label-only
references. Intervals (Wilson for proportions, a seeded case-level bootstrap) are reported next
to, never instead of, gate decisions.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from amber.schemas import AnnotationEvent, CaseResult, Example, LabelRecord, ReferenceTier

GateStatus: TypeAlias = Literal["met", "unmet", "inconclusive"]
BOOTSTRAP_SEED = 20260915
_FROZEN = ConfigDict(extra="forbid", frozen=True)
_Z95 = 1.959963984540054


# Inputs ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Gold:
    """One eligible case's reference: its outcome status, binary value, tier, and whether it
    carries evidence (an Example) or labels only."""

    case_id: str
    status: str
    value: bool | None
    tier: ReferenceTier
    has_evidence: bool


@dataclass(frozen=True)
class Prediction:
    """One case's automatic outcome: its status, binary value, and failure kind."""

    case_id: str
    status: str
    value: bool | None
    failure_kind: str | None = None


def _binary(values: Iterable[object], field: str) -> bool | None:
    found = {value.get(field) for value in values if isinstance(value, Mapping)}
    if len(found) != 1:
        return None
    (value,) = found
    return value if isinstance(value, bool) else None


def gold_from(record: Example | LabelRecord, *, variable: str, field: str) -> Gold:
    """A ``Gold`` from an Example reference or a label record for one boolean answer field."""
    if isinstance(record, LabelRecord):
        if record.variable != variable:
            raise ValueError("label record is for another variable")
        value = None
        if record.outcome_status == "answered":
            value = _binary([record.value], field)
            if value is None:
                raise ValueError("gold answer is not binary")
        return Gold(record.case_id, record.outcome_status, value, ReferenceTier.silver, False)
    reference = record.references.get(variable)
    if reference is None:
        raise ValueError("example has no reference for the variable")
    value = None
    if reference.outcome.status == "answered":
        claims = {claim.claim_id: claim.value for claim in record.claims}
        value = _binary([claims[claim_id] for claim_id in reference.final_claim_ids], field)
        if value is None:
            raise ValueError("gold answer is not binary")
    return Gold(record.case_id, reference.outcome.status, value, reference.tier, True)


def prediction_from(result: CaseResult, *, variable: str, field: str) -> Prediction:
    """A ``Prediction`` from a result: an answer counts only when its final claims agree on
    one boolean value of ``field``."""
    item = result.variable_outcomes.get(variable)
    if item is None:
        raise ValueError("result has no outcome for the variable")
    status = item.outcome.status
    value = None
    if status == "answered":
        claims = {claim.claim_id: claim.value for claim in result.claims}
        value = _binary([claims[claim_id] for claim_id in item.final_claim_ids], field)
    return Prediction(result.case_id, status, value, item.outcome.failure_kind)


# Counts and measures -----------------------------------------------------------------------


class BinaryCounts(BaseModel):
    model_config = _FROZEN

    N: int
    B: int
    P: int
    Q: int
    A: int
    C: int
    A_pos: int
    TP: int
    FP: int
    O: int  # noqa: E741 - the protocol's name for omissions
    failures: dict[str, int]
    unattempted: int
    missing_gold: int


def binary_counts(
    eligible: Sequence[str], gold: Mapping[str, Gold], predicted: Mapping[str, Prediction]
) -> BinaryCounts:
    counts = {
        "N": len(eligible),
        "B": 0,
        "P": 0,
        "Q": 0,
        "A": 0,
        "C": 0,
        "A_pos": 0,
        "TP": 0,
        "O": 0,
    }
    failures: dict[str, int] = {}
    unattempted = missing_gold = 0
    for case_id in eligible:
        reference = gold.get(case_id)
        prediction = predicted.get(case_id)
        if reference is None:
            missing_gold += 1
        if prediction is None:
            unattempted += 1
        elif prediction.failure_kind is not None:
            failures[prediction.failure_kind] = failures.get(prediction.failure_kind, 0) + 1
        gold_binary = reference is not None and reference.value is not None
        if gold_binary:
            assert reference is not None
            counts["B"] += 1
            counts["P" if reference.value else "Q"] += 1
        answered = (
            prediction is not None
            and prediction.status == "answered"
            and prediction.value is not None
        )
        if answered:
            assert prediction is not None
            counts["A"] += 1
            if gold_binary and reference is not None and prediction.value == reference.value:
                counts["C"] += 1
            if prediction.value:
                counts["A_pos"] += 1
                if gold_binary and reference is not None and reference.value:
                    counts["TP"] += 1
        elif gold_binary:
            counts["O"] += 1
    return BinaryCounts(
        **counts,
        FP=counts["A_pos"] - counts["TP"],
        failures=failures,
        unattempted=unattempted,
        missing_gold=missing_gold,
    )


class Measure(BaseModel):
    """A proportion (``numerator / denominator``) or a count; ``value`` is None when undefined."""

    model_config = _FROZEN

    name: str
    kind: Literal["proportion", "count"]
    numerator: float
    denominator: int | None = None
    defined: bool = True
    evidence: bool = False
    # A case-level proportion, which gets a Wilson interval.
    binomial: bool = False

    @property
    def value(self) -> float | None:
        if not self.defined:
            return None
        if self.kind == "count":
            return self.numerator
        if not self.denominator:
            return None
        return self.numerator / self.denominator


def binary_measures(counts: BinaryCounts, *, cost: float | None = None) -> dict[str, Measure]:
    """The protocol's measures from counts; ``false_positives`` needs ``Q > 0``."""

    def ratio(name: str, numerator: int, denominator: int) -> Measure:
        return Measure(
            name=name,
            kind="proportion",
            numerator=numerator,
            denominator=denominator,
            binomial=True,
        )

    measures = {
        "precision": ratio("precision", counts.C, counts.A),
        "ppv": ratio("ppv", counts.TP, counts.A_pos),
        "recall": ratio("recall", counts.TP, counts.P),
        "omission_rate": ratio("omission_rate", counts.O, counts.B),
        "coverage": ratio("coverage", counts.A, counts.N),
        "correct_yield": ratio("correct_yield", counts.C, counts.N),
        "false_positives": Measure(
            name="false_positives", kind="count", numerator=counts.FP, defined=counts.Q > 0
        ),
        "execution_failures": Measure(
            name="execution_failures", kind="count", numerator=sum(counts.failures.values())
        ),
    }
    if cost is not None:
        measures["mean_cost"] = Measure(
            name="mean_cost", kind="proportion", numerator=cost, denominator=counts.N
        )
    return measures


# Expert effort -----------------------------------------------------------------------------


class ReviewEffort(BaseModel):
    """Review time recorded on ``review.submit`` events (loupe's timing, imported by
    ``amber.loupe.import_reference``). ``timed_reviews < reviews`` means some reviews carry no
    timing, so the recorded minutes are incomplete."""

    model_config = _FROZEN

    reviews: int
    timed_reviews: int
    active_seconds: float
    idle_seconds: float

    @property
    def complete(self) -> bool:
        return self.timed_reviews == self.reviews


def review_effort(events: Iterable[AnnotationEvent]) -> ReviewEffort:
    """Sum active and idle review time over the ``review.submit`` events in ``events``."""
    submits = [event for event in events if event.action == "review.submit"]
    timed = [
        event.after
        for event in submits
        if event.after is not None and "active_seconds" in event.after
    ]
    return ReviewEffort(
        reviews=len(submits),
        timed_reviews=len(timed),
        active_seconds=sum(float(after["active_seconds"]) for after in timed),
        idle_seconds=sum(float(after["idle_seconds"]) for after in timed),
    )


def expert_minutes_per_accepted_case(
    effort: ReviewEffort, *, accepted_cases: int, other_minutes: float = 0.0
) -> Measure:
    """The protocol's ``T / K``: recorded active review minutes plus ``other_minutes`` (declared
    setup, prompt development, and other attributed effort) over accepted cases. Undefined when
    any review is untimed (a missing measurement is never zero) or there are no accepted cases.
    """
    if other_minutes < 0 or not math.isfinite(other_minutes):
        raise ValueError("other_minutes must be a finite non-negative number")
    return Measure(
        name="expert_minutes_per_accepted_case",
        kind="proportion",
        numerator=effort.active_seconds / 60 + other_minutes,
        denominator=accepted_cases,
        defined=effort.complete,
    )


# Intervals ---------------------------------------------------------------------------------


def wilson_interval(
    successes: float, trials: int, *, z: float = _Z95
) -> tuple[float, float] | None:
    """The Wilson score interval for a binomial proportion; None when ``trials`` is 0."""
    if trials <= 0:
        return None
    p = successes / trials
    denominator = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


class BootstrapInterval(BaseModel):
    model_config = _FROZEN

    low: float | None
    high: float | None
    valid: int
    undefined: int

    @property
    def conditional(self) -> bool:
        """True when some replicates had an undefined denominator and were excluded."""
        return self.undefined > 0


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def bootstrap(
    eligible: Sequence[str],
    statistic: Callable[[Sequence[str]], Mapping[str, float | None]],
    *,
    resamples: int = 10_000,
    seed: int = BOOTSTRAP_SEED,
    level: float = 0.95,
) -> dict[str, BootstrapInterval]:
    """Percentile intervals from resampling whole cases with replacement, with a separate
    random generator. A metric undefined on the full data gets no interval."""
    observed = statistic(eligible)
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {name: [] for name in observed}
    undefined = dict.fromkeys(observed, 0)
    for _ in range(resamples):
        sample = [eligible[rng.randrange(len(eligible))] for _ in eligible] if eligible else []
        values = statistic(sample)
        for name in observed:
            value = values.get(name)
            if value is None:
                undefined[name] += 1
            else:
                samples[name].append(value)
    alpha = (1 - level) / 2
    intervals: dict[str, BootstrapInterval] = {}
    for name, value in observed.items():
        ordered = sorted(samples[name])
        if value is None or not ordered:
            intervals[name] = BootstrapInterval(
                low=None, high=None, valid=len(ordered), undefined=undefined[name]
            )
            continue
        intervals[name] = BootstrapInterval(
            low=_percentile(ordered, alpha),
            high=_percentile(ordered, 1 - alpha),
            valid=len(ordered),
            undefined=undefined[name],
        )
    return intervals


# Gates -------------------------------------------------------------------------------------


class GateSpec(BaseModel):
    """A consumer's gate: a measure, a comparison, and a threshold."""

    model_config = _FROZEN

    name: str = Field(min_length=1)
    measure: str = Field(min_length=1)
    op: Literal["ge", "le", "eq"]
    threshold: float


class GateResult(BaseModel):
    model_config = _FROZEN

    name: str
    measure: str
    status: GateStatus
    observed_pass: bool | None
    provisional: bool
    value: float | None
    numerator: float | None
    denominator: int | None


class EvaluationReport(BaseModel):
    """Counts, measures, intervals, gate results, and the overall status."""

    model_config = _FROZEN

    counts: BinaryCounts
    measures: dict[str, Measure]
    wilson: dict[str, tuple[float, float]]
    bootstrap: dict[str, BootstrapInterval]
    gates: tuple[GateResult, ...]
    overall: GateStatus
    provisional: bool
    label_only: bool
    tier_mix: dict[ReferenceTier, int]
    incomplete: tuple[str, ...]


def _passes(value: float, gate: GateSpec) -> bool:
    if gate.op == "ge":
        return value >= gate.threshold
    if gate.op == "le":
        return value <= gate.threshold
    return value == gate.threshold


def evaluate_gates(
    gates: Sequence[GateSpec],
    measures: Mapping[str, Measure],
    *,
    provisional: bool,
    label_only: bool,
    incomplete: Sequence[str] = (),
) -> tuple[tuple[GateResult, ...], GateStatus]:
    """Gate results and the overall status by the protocol's rules."""
    results: list[GateResult] = []
    for gate in gates:
        measure = measures.get(gate.measure)
        value = None if measure is None or (measure.evidence and label_only) else measure.value
        observed = None if value is None else _passes(value, gate)
        if observed is None:
            status: GateStatus = "inconclusive"
        elif not observed:
            status = "unmet"
        else:
            status = "inconclusive" if provisional else "met"
        results.append(
            GateResult(
                name=gate.name,
                measure=gate.measure,
                status=status,
                observed_pass=observed,
                provisional=provisional,
                value=value,
                numerator=None if measure is None else measure.numerator,
                denominator=None if measure is None else measure.denominator,
            )
        )
    if any(result.status == "unmet" for result in results):
        overall: GateStatus = "unmet"
    elif results and all(result.status == "met" for result in results) and not incomplete:
        overall = "met"
    else:
        overall = "inconclusive"
    return tuple(results), overall


def evaluate_binary(
    eligible: Sequence[str],
    gold: Iterable[Gold],
    predicted: Iterable[Prediction],
    gates: Sequence[GateSpec],
    *,
    costs: Mapping[str, float] | None = None,
    extra_measures: Mapping[str, Measure] | None = None,
    resamples: int = 10_000,
    seed: int = BOOTSTRAP_SEED,
) -> EvaluationReport:
    """Score binary answers on a frozen eligible set and apply the consumer's gates."""
    gold_by_case = {item.case_id: item for item in gold if item.case_id in set(eligible)}
    predicted_by_case = {item.case_id: item for item in predicted if item.case_id in set(eligible)}

    def measures_for(sample: Sequence[str]) -> dict[str, Measure]:
        counts = binary_counts(sample, gold_by_case, predicted_by_case)
        cost = None if costs is None else sum(costs.get(case_id, 0.0) for case_id in sample)
        return binary_measures(counts, cost=cost)

    counts = binary_counts(eligible, gold_by_case, predicted_by_case)
    measures = measures_for(eligible)
    measures.update(extra_measures or {})
    tiers = [item.tier for item in gold_by_case.values()]
    mix: dict[ReferenceTier, int] = {}
    for tier in tiers:
        mix[tier] = mix.get(tier, 0) + 1
    provisional = any(tier is not ReferenceTier.adjudicated for tier in tiers)
    label_only = any(not item.has_evidence for item in gold_by_case.values())
    incomplete = tuple(
        reason
        for reason, flag in (
            ("missing_gold", counts.missing_gold > 0),
            ("unattempted", counts.unattempted > 0),
            ("provisional_reference", provisional),
        )
        if flag
    )
    results, overall = evaluate_gates(
        gates, measures, provisional=provisional, label_only=label_only, incomplete=incomplete
    )
    wilson = {
        name: interval
        for name, measure in measures.items()
        if measure.binomial
        and measure.value is not None
        and (interval := wilson_interval(measure.numerator, measure.denominator or 0)) is not None
    }
    intervals = bootstrap(
        list(eligible),
        lambda sample: {name: measure.value for name, measure in measures_for(sample).items()},
        resamples=resamples,
        seed=seed,
    )
    return EvaluationReport(
        counts=counts,
        measures=measures,
        wilson=wilson,
        bootstrap=intervals,
        gates=results,
        overall=overall,
        provisional=provisional,
        label_only=label_only,
        tier_mix=mix,
        incomplete=incomplete,
    )
