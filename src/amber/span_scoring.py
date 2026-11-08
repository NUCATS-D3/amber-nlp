"""Span-level scoring of mentions (experimental; amber-a6d.10).

A span is a ``Mention`` with at least one admitted reading. Spans match within one case,
source, and ``mention_type``:

- **strict**: identical offsets and fragments (equivalently, the same ``mention_id``);
- **lenient**: any positive overlap of their fragments, paired one-to-one by a maximum matching,
  so no span is credited twice and no pairing choice undercounts. Lenient is never below strict.

Precision, recall, and F1 are kept as counts (``Metric`` numerator and denominator): micro over
all spans, per mention type (``strict_f1:<type>``), and macro means over the types whose ratio is
defined. A zero denominator is undefined, never 0 or 1; per-type ratios that are undefined are
omitted. Attribute accuracy is scored on strict matches, per category the reference assessed: a
prediction that omits the category or whose readings disagree is wrong; a reference whose
readings disagree on a category does not score it. Values compare by type, so ``True`` is not
``1``. Results do not depend on input order.

``MentionSpanScorer`` adapts ``CaseResult`` predictions and ``Example`` references and applies
the rules every scorer follows (``amber.scoring``). Diagnostics never carry source text.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from amber.interfaces import ScorerNeed
from amber.schemas import (
    CaseResult,
    Example,
    LabelRecord,
    Mention,
    MentionReading,
    ReferenceTier,
    VariableSet,
)
from amber.schemas.mentions import fragments_error
from amber.scoring import (
    Metric,
    ScoreReport,
    provisional,
    refuse_self_reference,
    require_move_on,
    tier_mix,
)

AttributeValue: TypeAlias = tuple[type, bool | int | str]
Match: TypeAlias = Literal["strict", "lenient"]
_MATCHES: tuple[Match, ...] = ("strict", "lenient")
_RATIOS = ("precision", "recall", "f1")
_HUMAN_STATUSES = frozenset({"gold", "verified"})


@dataclass(frozen=True)
class Span:
    """A typed span to score; ``attributes`` maps a category to the values its readings gave."""

    case_id: str
    source_id: str
    mention_type: str
    start: int
    end: int
    fragments: tuple[tuple[int, int], ...] = ()
    attributes: Mapping[str, frozenset[AttributeValue]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("span end must be greater than start, and start nonnegative")
        if (error := fragments_error(self.start, self.end, self.fragments)) is not None:
            raise ValueError(error)

    @property
    def pieces(self) -> tuple[tuple[int, int], ...]:
        return self.fragments or ((self.start, self.end),)

    @property
    def group(self) -> tuple[str, str, str]:
        return (self.case_id, self.source_id, self.mention_type)


def _overlaps(left: Span, right: Span) -> bool:
    return any(a < d and c < b for a, b in left.pieces for c, d in right.pieces)


def _distinct(spans: Iterable[Span]) -> dict[tuple[str, str, str], list[Span]]:
    """Spans grouped for matching, one per position, in a canonical order."""
    groups: dict[tuple[str, str, str], dict[tuple[tuple[int, int], ...], Span]] = defaultdict(dict)
    for span in spans:
        groups[span.group].setdefault(span.pieces, span)
    return {key: [found[piece] for piece in sorted(found)] for key, found in groups.items()}


def _maximum_matching(predicted: Sequence[Span], gold: Sequence[Span]) -> int:
    """Size of a maximum one-to-one overlap matching (augmenting paths; inputs are ordered)."""
    neighbours = [[j for j, item in enumerate(gold) if _overlaps(span, item)] for span in predicted]
    owner: dict[int, int] = {}

    def augment(i: int, seen: set[int]) -> bool:
        for j in neighbours[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in owner or augment(owner[j], seen):
                owner[j] = i
                return True
        return False

    return sum(augment(i, set()) for i in range(len(predicted)))


@dataclass
class _Counts:
    tp: int = 0
    predicted: int = 0
    gold: int = 0


def _single(values: frozenset[AttributeValue] | None) -> AttributeValue | None:
    return next(iter(values)) if values is not None and len(values) == 1 else None


def _ratio_metrics(prefix: str, counts: _Counts, suffix: str = "") -> list[Metric]:
    ratios = {
        "precision": (counts.tp, counts.predicted),
        "recall": (counts.tp, counts.gold),
        "f1": (2 * counts.tp, counts.predicted + counts.gold),
    }
    return [
        Metric(name=f"{prefix}_{ratio}{suffix}", kind="evidence", numerator=num, denominator=den)
        for ratio, (num, den) in ratios.items()
    ]


def span_metrics(predicted: Iterable[Span], gold: Iterable[Span]) -> tuple[Metric, ...]:
    """Strict and lenient P/R/F1 (micro, per type, macro) and attribute accuracy."""
    predicted_groups, gold_groups = _distinct(predicted), _distinct(gold)
    counts: dict[Match, dict[str, _Counts]] = {match: defaultdict(_Counts) for match in _MATCHES}
    attributes: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for key in sorted(predicted_groups.keys() | gold_groups.keys()):
        mention_type = key[2]
        mine, theirs = predicted_groups.get(key, []), gold_groups.get(key, [])
        by_position = {span.pieces: span for span in theirs}
        strict_pairs = [
            (span, by_position[span.pieces]) for span in mine if span.pieces in by_position
        ]
        tallies = {"strict": len(strict_pairs), "lenient": _maximum_matching(mine, theirs)}
        for match in _MATCHES:
            entry = counts[match][mention_type]
            entry.tp += tallies[match]
            entry.predicted += len(mine)
            entry.gold += len(theirs)
        for mine_span, gold_span in strict_pairs:
            for category, values in gold_span.attributes.items():
                expected = _single(values)
                if expected is None:
                    continue
                score = attributes[category]
                score[0] += int(_single(mine_span.attributes.get(category)) == expected)
                score[1] += 1
    metrics: list[Metric] = []
    for match in _MATCHES:
        by_type = counts[match]
        total = _Counts(
            tp=sum(item.tp for item in by_type.values()),
            predicted=sum(item.predicted for item in by_type.values()),
            gold=sum(item.gold for item in by_type.values()),
        )
        metrics.extend(_ratio_metrics(match, total))
        per_type = [
            metric
            for mention_type in sorted(by_type)
            for metric in _ratio_metrics(match, by_type[mention_type], f":{mention_type}")
        ]
        for ratio in _RATIOS:
            defined = [
                value
                for metric in per_type
                if metric.name.startswith(f"{match}_{ratio}:")
                and (value := metric.value) is not None
            ]
            metrics.append(
                Metric(
                    name=f"{match}_macro_{ratio}",
                    kind="evidence",
                    numerator=sum(defined),
                    denominator=len(defined),
                )
            )
        metrics.extend(metric for metric in per_type if metric.denominator)
    metrics.append(
        Metric(
            name="attribute_accuracy",
            kind="label",
            numerator=sum(score[0] for score in attributes.values()),
            denominator=sum(score[1] for score in attributes.values()),
        )
    )
    metrics.extend(
        Metric(
            name=f"attribute_accuracy:{category}",
            kind="label",
            numerator=score[0],
            denominator=score[1],
        )
        for category, score in sorted(attributes.items())
    )
    return tuple(metrics)


# Adapters -----------------------------------------------------------------------------------


def _human_types(example: Example, variables: VariableSet) -> frozenset[str]:
    """Mention types some human-tier reference covers; their spans need reviewed readings."""
    types: set[str] = set()
    for name, variable in variables.variables.items():
        reference = example.references.get(name)
        if reference is not None and reference.tier is not ReferenceTier.silver:
            types.update(variable.mention_types)
    return frozenset(types)


def spans_of(record: CaseResult | Example, variables: VariableSet) -> tuple[Span, ...]:
    """The record's spans: mentions with an admitted reading, attributes merged per category.

    Rejected readings are never admitted. In an Example, a mention type that a human-tier
    reference covers admits only ``gold`` and ``verified`` readings.
    """
    human = _human_types(record, variables) if isinstance(record, Example) else frozenset()
    readings: dict[str, list[MentionReading]] = defaultdict(list)
    for reading in record.mention_readings:
        readings[reading.mention_id].append(reading)

    def admitted(mention: Mention, reading: MentionReading) -> bool:
        if reading.status == "rejected":
            return False
        return mention.mention_type not in human or reading.status in _HUMAN_STATUSES

    spans: list[Span] = []
    for mention in record.mentions:
        live = [reading for reading in readings[mention.mention_id] if admitted(mention, reading)]
        if not live:
            continue
        merged: dict[str, set[AttributeValue]] = defaultdict(set)
        for reading in live:
            for category, attribute in reading.attributes.items():
                merged[category].add((type(attribute.value), attribute.value))
        spans.append(
            Span(
                case_id=record.case_id,
                source_id=mention.source_id,
                mention_type=mention.mention_type,
                start=mention.start,
                end=mention.end,
                fragments=mention.fragments,
                attributes={category: frozenset(values) for category, values in merged.items()},
            )
        )
    return tuple(sorted(spans, key=lambda span: (span.group, span.pieces)))


class MentionSpanScorer:
    """Scores predicted mentions against ``Example`` spans (experimental).

    Only cases with an Example are scored; a case without a prediction misses every reference
    span. ``LabelRecord``s carry no spans: when no Example is given, every metric is
    ``inconclusive``. Refuses ``self_reference``; with ``move_on_event``, every
    ``single_review`` snapshot must include that decision.
    """

    name = "amber-mention-spans"
    needs: frozenset[ScorerNeed] = frozenset({"examples", "mentions"})

    def __init__(self, *, move_on_event: str | None = None) -> None:
        self.move_on_event = move_on_event

    def score(
        self,
        predicted: Sequence[CaseResult],
        reference: Sequence[Example | LabelRecord],
        *,
        variables: VariableSet,
    ) -> ScoreReport:
        records = [*predicted, *reference]
        if any(record.variable_set_ref != variables.schema_ref for record in records):
            raise ValueError("variable_set_mismatch")
        refuse_self_reference(predicted, reference)
        if self.move_on_event is not None:
            require_move_on(reference, self.move_on_event)
        examples = [record for record in reference if isinstance(record, Example)]
        cases = [example.case_id for example in examples]
        if len(cases) != len(set(cases)):
            raise ValueError("duplicate_reference")
        predicted_cases = [result.case_id for result in predicted]
        if len(predicted_cases) != len(set(predicted_cases)):
            raise ValueError("duplicate_prediction")
        scored = set(cases)
        metrics = span_metrics(
            (
                span
                for result in predicted
                if result.case_id in scored
                for span in spans_of(result, variables)
            ),
            (span for example in examples for span in spans_of(example, variables)),
        )
        inconclusive: tuple[str, ...] = ()
        if not examples:
            inconclusive = tuple(metric.name for metric in metrics)
            metrics = ()
        mix = tier_mix(reference)
        return ScoreReport(
            scorer=self.name,
            metrics=metrics,
            example_ids=tuple(sorted(example.example_id for example in examples)),
            tier_mix=mix,
            provisional=provisional(mix),
            inconclusive=inconclusive,
        )
