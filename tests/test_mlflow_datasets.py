"""Examples as MLflow evaluation datasets: lossless, gated, and revalidated on the way back
(tracking extra)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

mlflow = pytest.importorskip("mlflow")

from amber.annotations import AnnotationValidationError  # noqa: E402
from amber.conformance import fixtures as fx  # noqa: E402
from amber.examples import ExampleValidationError, mint_example  # noqa: E402
from amber.graph_errors import GraphValidationError  # noqa: E402
from amber.mlflow_ext.datasets import (  # noqa: E402
    EXAMPLE_KEY,
    DatasetError,
    create_examples_dataset,
    examples_to_records,
    load_examples_dataset,
    records_to_examples,
)
from amber.policy import (  # noqa: E402
    DEFAULT_MATRIX,
    DataPolicy,
    Destination,
    PolicyViolation,
    StaticGate,
)
from amber.reference import InMemoryResolver  # noqa: E402
from amber.schemas import Sensitivity, Zone  # noqa: E402

from .test_bundles import CASE, _gold  # noqa: E402

NOW = datetime(2026, 1, 6, tzinfo=UTC)
DATA = DataPolicy(sensitivity=Sensitivity.synthetic)
GATE = StaticGate(DEFAULT_MATRIX)
STORE = Destination(
    name="institution-mlflow",
    kind="artifact_store",
    zone=Zone.institution,
    accepts=frozenset({"derived_text", "source_text"}),
)
RESOLVER = InMemoryResolver([fx.SOURCE])
NOTE = fx.note_case()


def _examples() -> list[Any]:
    _, adjudicated = _gold()
    silver = fx.note_example("silver", present=True, producer=fx.TEACHER)
    return [adjudicated, silver]


def _load(records: Any, **kwargs: Any) -> Any:
    return records_to_examples(
        records,
        cases=kwargs.pop("cases", {CASE.case_id: CASE}),
        resolver=kwargs.pop("resolver", RESOLVER),
        variables=kwargs.pop("variables", fx.VARIABLES),
        **kwargs,
    )


def test_records_round_trip_without_loss() -> None:
    _, example = _gold()
    (record,) = examples_to_records([example])
    assert record["inputs"] == {"case_id": example.case_id, "example_id": example.example_id}
    assert record["expectations"]["finding"] == {
        "tier": "adjudicated",
        "status": "answered",
        "values": [{"present": True}],
    }
    reloaded = _load(json.loads(json.dumps([record])))
    assert reloaded == (example,)
    (back,) = reloaded
    assert back.split == example.split
    assert back.evidence == example.evidence and back.edges == example.edges
    assert {name: ref.final_claim_ids for name, ref in back.references.items()} == {
        name: ref.final_claim_ids for name, ref in example.references.items()
    }


def test_text_is_included_only_when_asked() -> None:
    _, example = _gold()
    (record,) = examples_to_records([example], texts=RESOLVER)
    assert record["inputs"]["text"] == fx.SOURCE.text
    with pytest.raises(DatasetError, match="source_unavailable"):
        examples_to_records([example], texts=InMemoryResolver())


@pytest.mark.parametrize(
    "edit",
    [
        lambda e: e["references"]["finding"].update(tier="silver"),
        lambda e: e.update(split="train"),
        lambda e: e["evidence"].pop(),
    ],
    ids=["tier", "split", "support"],
)
def test_edited_records_never_reload(edit: Any) -> None:
    _, example = _gold()
    records = json.loads(json.dumps(examples_to_records([example])))
    edit(records[0]["expectations"][EXAMPLE_KEY])
    with pytest.raises((ExampleValidationError, GraphValidationError)):
        _load(records)


def test_an_edited_value_is_caught_against_the_events() -> None:
    """The content ID pins reviewed values; the stored events pin how they were reached."""
    log, _ = fx.adjudicated(CASE.case_id)
    _, example = _gold()
    records = json.loads(json.dumps(examples_to_records([example])))
    assert _load(records, events={CASE.case_id: log.events}) == (example,)
    for claim in records[0]["expectations"][EXAMPLE_KEY]["claims"]:
        claim["value"] = {"present": False}
    with pytest.raises(ExampleValidationError, match="example_id_mismatch"):
        _load(records)
    # A forger who also recomputes the ID is still caught by the events.
    edited = records[0]["expectations"][EXAMPLE_KEY]
    edited["example_id"] = mint_example(
        edited, case=CASE, source=fx.SOURCE, variables=fx.VARIABLES
    ).example_id
    assert _load(records)[0] != example
    with pytest.raises(AnnotationValidationError, match="events_mismatch"):
        _load(records, events={CASE.case_id: log.events})


@pytest.mark.parametrize(
    ("records", "kwargs", "code"),
    [
        ([{"expectations": {}}], {}, "invalid_record"),
        ([{}], {}, "invalid_record"),
        (None, {"cases": {}}, "unknown_case"),
        (None, {"resolver": InMemoryResolver()}, "source_unavailable"),
    ],
)
def test_unusable_records_are_refused(records: Any, kwargs: dict[str, Any], code: str) -> None:
    if records is None:
        records = examples_to_records([_gold()[1]])
    with pytest.raises(DatasetError, match=code):
        _load(records, **kwargs)


@pytest.fixture
def tracking(tmp_path: Path) -> Any:
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    experiment = mlflow.set_experiment("amber-datasets-test")
    yield experiment
    mlflow.set_tracking_uri(previous)


def test_a_dataset_round_trips_through_mlflow(tracking: Any) -> None:
    _, example = _gold()
    dataset, decisions = create_examples_dataset(
        [example],
        name="amber-gold",
        variables=fx.VARIABLES,
        gate=GATE,
        data=DATA,
        destination=STORE,
        now=NOW,
        experiment_id=tracking.experiment_id,
    )
    assert [decision.payload for decision in decisions] == ["derived_text"]
    assert dataset.tags["amber.variable_set_ref"] == fx.VARIABLES.schema_ref
    assert dataset.tags["amber.tier.adjudicated"] == "2"
    loaded = load_examples_dataset(
        dataset.dataset_id, cases={CASE.case_id: CASE}, resolver=RESOLVER, variables=fx.VARIABLES
    )
    assert loaded == (example,)
    with pytest.raises(DatasetError, match="variable_set_mismatch"):
        load_examples_dataset(
            dataset.dataset_id,
            cases={NOTE.case_id: NOTE},
            resolver=RESOLVER,
            variables=fx.NOTE_VARIABLES,
        )


def test_a_refused_payload_creates_nothing(tracking: Any) -> None:
    from mlflow.genai.datasets import search_datasets

    _, example = _gold()
    labels_only = STORE.model_copy(update={"accepts": frozenset({"labels_only"})})
    with pytest.raises(PolicyViolation):
        create_examples_dataset(
            [example],
            name="refused",
            variables=fx.VARIABLES,
            gate=GATE,
            data=DATA,
            destination=labels_only,
            now=NOW,
            experiment_id=tracking.experiment_id,
        )
    no_text = STORE.model_copy(update={"accepts": frozenset({"derived_text"})})
    with pytest.raises(PolicyViolation) as failure:
        create_examples_dataset(
            [example],
            name="refused-text",
            variables=fx.VARIABLES,
            gate=GATE,
            data=DATA,
            destination=no_text,
            now=NOW,
            experiment_id=tracking.experiment_id,
            texts=RESOLVER,
        )
    assert failure.value.decision.payload == "source_text"
    assert list(search_datasets(experiment_ids=[tracking.experiment_id])) == []


def test_datasets_hold_one_variable_set(tracking: Any) -> None:
    with pytest.raises(DatasetError, match="variable_set_mismatch"):
        create_examples_dataset(
            _examples(),
            name="mixed",
            variables=fx.VARIABLES,
            gate=GATE,
            data=DATA,
            destination=STORE,
            now=NOW,
        )
    with pytest.raises(DatasetError, match="no_examples"):
        create_examples_dataset(
            [],
            name="empty",
            variables=fx.VARIABLES,
            gate=GATE,
            data=DATA,
            destination=STORE,
            now=NOW,
        )


# A local destination while MLflow points at a remote store (amber-fc8.10).
REMOTE = ["databricks", "uc", "http://tracking.invalid:5000"]
LOCAL_STORE = STORE.model_copy(update={"name": "local-mlflow", "zone": Zone.local})


@pytest.mark.parametrize("uri", REMOTE)
@pytest.mark.parametrize("texts", [None, RESOLVER], ids=["derived", "with-text"])
def test_a_local_destination_refuses_a_remote_store(
    tracking: Any, monkeypatch: pytest.MonkeyPatch, uri: str, texts: Any
) -> None:
    from mlflow.genai.datasets import search_datasets

    from amber.mlflow_ext.tracking import TrackingError

    _, example = _gold()
    # Reaching the store must fail fast, not retry (MLflow's own settings).
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_TIMEOUT", "1")
    store = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(uri)
    try:
        with pytest.raises(TrackingError, match="tracking_uri_not_local"):
            create_examples_dataset(
                [example], name="remote", variables=fx.VARIABLES, gate=GATE, data=DATA,
                destination=LOCAL_STORE, now=NOW, experiment_id=tracking.experiment_id,
                texts=texts,
            )  # fmt: skip
    finally:
        mlflow.set_tracking_uri(store)
    assert list(search_datasets(experiment_ids=[tracking.experiment_id])) == []


def test_a_local_destination_accepts_a_local_store(tracking: Any) -> None:
    _, example = _gold()
    dataset, _ = create_examples_dataset(
        [example], name="local", variables=fx.VARIABLES, gate=GATE, data=DATA,
        destination=LOCAL_STORE, now=NOW, experiment_id=tracking.experiment_id,
    )  # fmt: skip
    assert dataset.tags["amber.examples"] == "1"
