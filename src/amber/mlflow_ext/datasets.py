"""Examples as MLflow GenAI evaluation datasets (OSS API only; ``tracking`` extra).

Each Example becomes one record:

- ``inputs``: ``case_id`` and ``example_id`` (and the note ``text`` when asked for);
- ``expectations``: ``amber_example``, the Example's full JSON (tiers, split, outcomes,
  final claims, and the whole support closure), plus per final variable a summary
  ``{"tier", "status", "values"}`` that scorers can read directly.

Records are reloaded only through ``amber.examples.load_example`` against the authoritative
case, source, and variable set; with the stored events, each must also equal the replay of
its case's events, so an edited value never comes back as reviewed. A dataset
is ``derived_text`` (``source_text`` too with ``text``), and ``create_examples_dataset``
checks each payload kind against the destination before writing anything.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

try:
    from mlflow.genai.datasets import EvaluationDataset, create_dataset, get_dataset
except ModuleNotFoundError as exc:
    if exc.name is None or not exc.name.startswith("mlflow"):
        raise
    raise ImportError(
        "MLflow is optional. Install tracking dependencies with: uv sync --extra tracking"
    ) from exc

from amber.annotations import verify_example
from amber.examples import load_example
from amber.interfaces import PolicyGate, SourceResolver
from amber.labeling import spans_of
from amber.policy import DataPolicy, Decision, Destination, PayloadKind
from amber.schemas import AnnotationEvent, Case, Example, VariableSet

EXAMPLE_KEY = "amber_example"


class DatasetError(ValueError):
    """A dataset that cannot hold or return Examples; renders no record contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _summary(example: Example) -> dict[str, Any]:
    claims = {claim.claim_id: claim for claim in example.claims}
    return {
        name: {
            "tier": reference.tier.value,
            "status": reference.outcome.status,
            "values": [json.loads(json.dumps(claims[i].value)) for i in reference.final_claim_ids],
        }
        for name, reference in sorted(example.references.items())
    }


def examples_to_records(
    examples: Iterable[Example], *, texts: SourceResolver | None = None
) -> list[dict[str, Any]]:
    """One evaluation record per Example; ``texts`` adds each case's note text to ``inputs``."""
    records: list[dict[str, Any]] = []
    for example in examples:
        if not isinstance(example, Example):
            raise DatasetError("not_an_example")
        inputs: dict[str, Any] = {"case_id": example.case_id, "example_id": example.example_id}
        if texts is not None:
            source = texts.resolve(example.source_ids[0])
            if source is None or source.text is None:
                raise DatasetError("source_unavailable")
            inputs["text"] = source.text
        if EXAMPLE_KEY in example.references:
            raise DatasetError("reserved_variable_name")
        expectations = {**_summary(example), EXAMPLE_KEY: json.loads(example.model_dump_json())}
        records.append({"inputs": inputs, "expectations": expectations})
    return records


def records_to_examples(
    records: Iterable[Mapping[str, Any]],
    *,
    cases: Mapping[str, Case],
    resolver: SourceResolver,
    variables: VariableSet,
    events: Mapping[str, Sequence[AnnotationEvent]] | None = None,
) -> tuple[Example, ...]:
    """Revalidate every record's Example against its authoritative case, source, and set.

    Loading checks structure, grounding, and tiers but not that the values are the ones
    reviewed; pass the stored ``events`` per case ID to also require each Example to be
    exactly the replay of its case's events (``verify_example``).

    Raises ``DatasetError`` (``invalid_record``, ``unknown_case``, ``source_unavailable``) or
    the load boundary's own error; returns nothing unless every record loads.
    """
    examples: list[Example] = []
    for record in records:
        expectations = record.get("expectations") if isinstance(record, Mapping) else None
        payload = expectations.get(EXAMPLE_KEY) if isinstance(expectations, Mapping) else None
        if not isinstance(payload, Mapping):
            raise DatasetError("invalid_record")
        case = cases.get(payload.get("case_id"))  # type: ignore[arg-type]
        if case is None:
            raise DatasetError("unknown_case")
        source = resolver.resolve(case.source_ids[0])
        if source is None:
            raise DatasetError("source_unavailable")
        example = load_example(payload, case=case, source=source, variables=variables)
        if events is not None:
            example = verify_example(
                example,
                events.get(case.case_id, ()),
                case=case,
                source=source,
                variables=variables,
                spans=spans_of(example),
            )
        examples.append(example)
    return tuple(sorted(examples, key=lambda example: (example.case_id, example.example_id)))


def create_examples_dataset(
    examples: Sequence[Example],
    *,
    name: str,
    variables: VariableSet,
    gate: PolicyGate,
    data: DataPolicy,
    destination: Destination,
    now: datetime,
    experiment_id: str | None = None,
    texts: SourceResolver | None = None,
) -> tuple[EvaluationDataset, tuple[Decision, ...]]:
    """Create an MLflow evaluation dataset of ``examples`` after the gate permits it.

    Raises ``PolicyViolation`` before writing anything when ``derived_text`` (or, with
    ``texts``, ``source_text``) may not go to ``destination``.
    """
    if not examples:
        raise DatasetError("no_examples")
    if any(example.variable_set_ref != variables.schema_ref for example in examples):
        raise DatasetError("variable_set_mismatch")
    payloads: tuple[PayloadKind, ...] = (
        ("derived_text", "source_text") if texts is not None else ("derived_text",)
    )
    decisions = tuple(gate.require(data, destination, kind, now=now) for kind in payloads)
    records = examples_to_records(examples, texts=texts)
    tiers: dict[str, int] = {}
    for example in examples:
        for reference in example.references.values():
            tiers[reference.tier.value] = tiers.get(reference.tier.value, 0) + 1
    tags = {
        "amber.variable_set": variables.name,
        "amber.variable_set_version": variables.version,
        "amber.variable_set_ref": variables.schema_ref,
        "amber.examples": str(len(examples)),
        **{f"amber.tier.{tier}": str(count) for tier, count in sorted(tiers.items())},
    }
    dataset = create_dataset(name=name, experiment_id=experiment_id, tags=tags)
    dataset.merge_records(records)
    return dataset, decisions


def load_examples_dataset(
    dataset_id: str,
    *,
    cases: Mapping[str, Case],
    resolver: SourceResolver,
    variables: VariableSet,
    events: Mapping[str, Sequence[AnnotationEvent]] | None = None,
) -> tuple[Example, ...]:
    """Read a dataset's records back as Examples, revalidated (see ``records_to_examples``)."""
    dataset = get_dataset(dataset_id=dataset_id)
    if dataset.tags.get("amber.variable_set_ref") != variables.schema_ref:
        raise DatasetError("variable_set_mismatch")
    frame = dataset.to_df()
    records = [{"expectations": value} for value in frame["expectations"].tolist()]
    return records_to_examples(
        records, cases=cases, resolver=resolver, variables=variables, events=events
    )


__all__ = [
    "EXAMPLE_KEY",
    "DatasetError",
    "create_examples_dataset",
    "examples_to_records",
    "load_examples_dataset",
    "records_to_examples",
]
