"""Reference scorers (contract v0.1 section 6.2): ``StructuralValidityScorer`` and
``LabelCorrectScorer``. They read labels, outcomes, and offsets; never source text."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from amber.ids import canonical_json
from amber.schemas import (
    CaseResult,
    Example,
    Inclusion,
    InferenceEvidence,
    LabelRecord,
    VariableSet,
)
from amber.scoring import (
    Metric,
    ScoreReport,
    provisional,
    refuse_self_reference,
    require_move_on,
    tier_mix,
)

ScorerNeed = Literal["examples", "label_records", "mentions", "traces"]


def _check_predictions(predicted: Sequence[CaseResult], variables: VariableSet) -> None:
    if any(result.variable_set_ref != variables.schema_ref for result in predicted):
        raise ValueError("variable_set_mismatch")


def _report(
    scorer: str,
    metrics: list[Metric],
    references: Sequence[Example | LabelRecord],
    inconclusive: Sequence[str] = (),
) -> ScoreReport:
    mix = tier_mix(references)
    return ScoreReport(
        scorer=scorer,
        metrics=tuple(metrics),
        example_ids=tuple(
            sorted({record.example_id for record in references if isinstance(record, Example)})
        ),
        tier_mix=mix,
        provisional=provisional(mix),
        inconclusive=tuple(inconclusive),
    )


class StructuralValidityScorer:
    """Outcome mix and grounding failures of predictions. References only set the tier mix,
    but the ``self_reference`` refusal applies to every scorer."""

    name = "amber-structural-validity"
    needs: frozenset[ScorerNeed] = frozenset()

    def score(
        self,
        predicted: Sequence[CaseResult],
        reference: Sequence[Example | LabelRecord],
        *,
        variables: VariableSet,
    ) -> ScoreReport:
        _check_predictions(predicted, variables)
        refuse_self_reference(predicted, reference)
        outcomes = [item for result in predicted for item in result.variable_outcomes.values()]
        total = len(outcomes)
        metrics = [
            Metric(
                name="answered",
                kind="structure",
                numerator=sum(item.outcome.status == "answered" for item in outcomes),
                denominator=total,
            ),
            Metric(
                name="failed",
                kind="structure",
                numerator=sum(item.outcome.status == "failed" for item in outcomes),
                denominator=total,
            ),
            Metric(
                name="grounding_failures_per_case",
                kind="structure",
                numerator=sum(result.grounding_failures for result in predicted),
                denominator=len(predicted),
            ),
        ]
        kinds = sorted(
            {item.outcome.failure_kind for item in outcomes if item.outcome.failure_kind}
        )
        metrics.extend(
            Metric(
                name=f"failed:{kind}",
                kind="structure",
                numerator=sum(item.outcome.failure_kind == kind for item in outcomes),
                denominator=total,
            )
            for kind in kinds
        )
        return _report(self.name, metrics, reference)


def _values(record: CaseResult | Example, claim_ids: Sequence[str]) -> frozenset[str]:
    claims = {claim.claim_id: claim for claim in record.claims}
    return frozenset(canonical_json(claims[claim_id].value) for claim_id in claim_ids)


def _spans(record: CaseResult | Example, claim_ids: Sequence[str]) -> set[tuple[int, int]]:
    """Positions of the inclusions supporting the given claims."""
    evidence = {item.evidence_id: item for item in record.evidence}
    children: dict[str, list[str]] = {}
    for edge in record.edges:
        children.setdefault(edge.claim_id, []).append(edge.evidence_id)
    for item in record.evidence:
        if isinstance(item, InferenceEvidence):
            children.setdefault(item.evidence_id, []).extend(item.inputs)
    spans: set[tuple[int, int]] = set()
    seen: set[str] = set()
    pending = list(claim_ids)
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        record_item = evidence.get(node)
        if isinstance(record_item, Inclusion):
            spans.add((record_item.start, record_item.end))
        pending.extend(children.get(node, ()))
    return spans


def _overlaps(left: set[tuple[int, int]], right: set[tuple[int, int]]) -> bool:
    return any(a < d and c < b for a, b in left for c, d in right)


class LabelCorrectScorer:
    """Label accuracy, coverage, omissions, and (against Examples) evidence overlap.

    Refuses ``self_reference``. With ``move_on_event``, every ``single_review`` snapshot must
    include that move-on decision (contract v0.1 ANN-18).
    """

    name = "amber-label-correct"
    needs: frozenset[ScorerNeed] = frozenset({"examples", "label_records"})

    def __init__(self, *, move_on_event: str | None = None) -> None:
        self.move_on_event = move_on_event

    def score(
        self,
        predicted: Sequence[CaseResult],
        reference: Sequence[Example | LabelRecord],
        *,
        variables: VariableSet,
    ) -> ScoreReport:
        _check_predictions(predicted, variables)
        refuse_self_reference(predicted, reference)
        if self.move_on_event is not None:
            require_move_on(reference, self.move_on_event)
        predictions = {
            (result.case_id, name): (result, item)
            for result in predicted
            for name, item in result.variable_outcomes.items()
        }
        tallies: dict[str | None, dict[str, list[int]]] = {}

        def tally(variable: str, metric: str, hit: bool) -> None:
            for key in (None, variable):
                counts = tallies.setdefault(key, {}).setdefault(metric, [0, 0])
                counts[0] += int(hit)
                counts[1] += 1

        has_examples = False
        for record in reference:
            if isinstance(record, Example):
                has_examples = True
                entries = [
                    (
                        name,
                        ref.outcome.status,
                        _values(record, ref.final_claim_ids),
                        ref.final_claim_ids,
                    )
                    for name, ref in record.references.items()
                ]
            else:
                value = (
                    frozenset({canonical_json(dict(record.value))})
                    if record.value is not None
                    else frozenset()
                )
                entries = [(record.variable, record.outcome_status, value, ())]
            for name, status, values, final_ids in entries:
                found = predictions.get((record.case_id, name))
                if found is None:
                    tally(name, "label_accuracy", False)
                    tally(name, "coverage", False)
                    if status == "answered":
                        tally(name, "omissions", True)
                    continue
                result, item = found
                predicted_values = _values(result, item.final_claim_ids)
                correct = item.outcome.status == status and (
                    status != "answered" or predicted_values == values
                )
                tally(name, "label_accuracy", correct)
                tally(name, "coverage", item.outcome.status != "failed")
                if status == "answered":
                    tally(name, "omissions", item.outcome.status != "answered")
                if isinstance(record, Example) and status == "answered" and correct:
                    tally(
                        name,
                        "evidence_overlap",
                        _overlaps(_spans(result, item.final_claim_ids), _spans(record, final_ids)),
                    )
        kinds = {
            "label_accuracy": "label",
            "coverage": "coverage",
            "omissions": "label",
            "evidence_overlap": "evidence",
        }
        metrics = [
            Metric(
                name=metric,
                kind=kinds[metric],  # type: ignore[arg-type]
                numerator=counts[0],
                denominator=counts[1],
                variable=variable,
            )
            for variable, by_metric in sorted(tallies.items(), key=lambda item: item[0] or "")
            for metric, counts in sorted(by_metric.items())
        ]
        tokens = [result.usage.tokens for result in predicted if result.usage is not None]
        if tokens:
            metrics.append(
                Metric(
                    name="tokens_per_case",
                    kind="cost",
                    numerator=sum(tokens),
                    denominator=len(tokens),
                )
            )
        inconclusive = () if has_examples else ("evidence_overlap",)
        return _report(self.name, metrics, reference, inconclusive)
