"""The reference implementations are synthetic-only (amber-0z4.4): every entry point that sees a
case, data policy, run, or result refuses anything whose sensitivity is not ``synthetic``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from amber.conformance import fixtures as fx
from amber.policy import DEFAULT_MATRIX, DataPolicy, Destination, StaticGate
from amber.reference import (
    DeterministicExtractor,
    FileReviewBackend,
    ImportLabeler,
    InMemoryStore,
    LabelCorrectScorer,
    ReferenceUseError,
    ScriptedProvider,
    StructuralValidityScorer,
)
from amber.schemas import Sensitivity, Zone

NOW = datetime(2026, 1, 9, tzinfo=UTC)
GATE = StaticGate(DEFAULT_MATRIX)
REAL = [Sensitivity.deidentified, Sensitivity.limited, Sensitivity.phi]


def _real_case(sensitivity: Sensitivity) -> Any:
    return fx.note_case().model_copy(update={"sensitivity": sensitivity})


def _real_context(sensitivity: Sensitivity) -> Any:
    context = fx.run_context()
    return context.model_copy(update={"data": DataPolicy(sensitivity=sensitivity)})


def _refused(call: Any) -> None:
    with pytest.raises(ReferenceUseError) as failure:
        call()
    assert failure.value.code == "synthetic_only"


@pytest.mark.parametrize("sensitivity", REAL)
def test_the_reference_store_holds_only_synthetic_cases(sensitivity: Sensitivity) -> None:
    store = InMemoryStore()
    _refused(lambda: store.put_case(_real_case(sensitivity), variables=fx.NOTE_VARIABLES))
    store.put_case(fx.note_case(), variables=fx.NOTE_VARIABLES)


@pytest.mark.parametrize("sensitivity", REAL)
def test_the_reference_extractor_runs_only_on_synthetic_data(sensitivity: Sensitivity) -> None:
    model = fx_model()
    extractor = DeterministicExtractor(ScriptedProvider(model, []))
    _refused(
        lambda: extractor.run(
            _real_case(sensitivity), fx.SOURCE, fx.NOTE_VARIABLES, gate=GATE, budget=None,
            context=_real_context(sensitivity),
        )
    )  # fmt: skip


def fx_model() -> Any:
    from amber.policy import ProviderDestination

    return ProviderDestination(
        name="local:guard-model",
        kind="model",
        zone=Zone.local,
        accepts=frozenset({"source_text"}),
        model="guard-model",
        capabilities=frozenset({"schema_constrained"}),
    )


@pytest.mark.parametrize("sensitivity", REAL)
def test_the_reference_labeler_labels_only_synthetic_cases(
    sensitivity: Sensitivity, tmp_path: Path
) -> None:
    labeler = ImportLabeler(tmp_path / "absent.json")
    _refused(
        lambda: labeler.label(
            _real_case(sensitivity), fx.SOURCE, fx.NOTE_VARIABLES, gate=GATE,
            context=fx.run_context(),
        )
    )  # fmt: skip
    _refused(
        lambda: labeler.label(
            fx.note_case(), fx.SOURCE, fx.NOTE_VARIABLES, gate=GATE,
            context=_real_context(sensitivity),
        )
    )  # fmt: skip


@pytest.mark.parametrize("sensitivity", REAL)
@pytest.mark.parametrize("scorer", [LabelCorrectScorer, StructuralValidityScorer])
def test_the_reference_scorers_score_only_synthetic_results(
    sensitivity: Sensitivity, scorer: Any
) -> None:
    result = fx.note_result(fx.RUN_IDS[0], present=True, producer="model:x@1")
    outcomes = {
        name: item.model_copy(
            update={
                "outcome": item.outcome.model_copy(
                    update={
                        "provenance": item.outcome.provenance.model_copy(
                            update={"sensitivity": sensitivity}
                        )
                    }
                )
            }
        )
        for name, item in result.variable_outcomes.items()
    }
    real = result.model_copy(update={"variable_outcomes": outcomes})
    examples = [fx.note_example("adjudicated", present=True, producer=fx.TEACHER)]
    _refused(lambda: scorer().score([real], examples, variables=fx.NOTE_VARIABLES))
    scorer().score([result], examples, variables=fx.NOTE_VARIABLES)


@pytest.mark.parametrize("sensitivity", REAL)
def test_the_file_review_backend_exports_only_synthetic_data(
    sensitivity: Sensitivity, tmp_path: Path
) -> None:
    review = Destination(
        name="guard-review", kind="review", zone=Zone.local, accepts=frozenset({"source_text"})
    )
    backend = FileReviewBackend(tmp_path, review, answer_models=[fx.FindingAnswer])
    _refused(
        lambda: backend.export_cases(
            [(_real_case(sensitivity), fx.SOURCE, None)], variables=fx.NOTE_VARIABLES,
            streams=["a"], gate=GATE, data=DataPolicy(sensitivity=sensitivity),
        )
    )  # fmt: skip
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("sensitivity", REAL)
def test_the_local_exporter_exports_only_synthetic_data(
    sensitivity: Sensitivity, tmp_path: Path
) -> None:
    pytest.importorskip("pyarrow")
    from amber.bundles import ManifestDataset, ManifestProducer
    from amber.reference.exporter import LocalExporter

    exporter = LocalExporter(
        tmp_path,
        format="amber-labels/0.1",
        variables=fx.NOTE_VARIABLES,
        dataset=ManifestDataset(name=fx.DATASET, manifest_hash="g", sensitivity=sensitivity),
        producer=ManifestProducer(name="guard", version="1"),
        policy_version="guard-policy@1",
        cases=[],
    )
    target = Destination(
        name="local:guard", kind="export", zone=Zone.local, accepts=frozenset({"labels_only"})
    )
    _refused(
        lambda: exporter.export(
            [], target, gate=GATE, data=DataPolicy(sensitivity=sensitivity), now=NOW
        )
    )
