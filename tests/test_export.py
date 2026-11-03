"""PolicyGate, Exporter, and amber-labels bundles (contract v0.1 sections 1 and 6.2; catalog
IFC-08)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import Field

from amber.bundles import (
    BundleError,
    ManifestProducer,
    build_labels_bundle,
    check_bundle,
    load_bundle,
    load_labels,
)
from amber.conformance import fixtures as fx
from amber.interfaces import Exporter, PolicyGate
from amber.labels import labels_only_view
from amber.policy import (
    DEFAULT_MATRIX,
    DataPolicy,
    Decision,
    Destination,
    PayloadKind,
    PolicyViolation,
    StaticGate,
)
from amber.reference import InMemoryResolver
from amber.schemas import AnswerModel, LabelRecord, Sensitivity, Variable, VariableSet, Zone

pytest.importorskip("pyarrow")
from amber.bundles.parquet import read_bundle  # noqa: E402
from amber.reference.exporter import ExportError, LocalExporter  # noqa: E402

from .test_bundles import CASE, DATASET, RUN, _rich_result  # noqa: E402

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
DATA = DataPolicy(sensitivity=Sensitivity.synthetic)
ARTIFACTS = Destination(
    name="institution-artifacts",
    kind="artifact_store",
    zone=Zone.institution,
    accepts=frozenset({"derived_text", "labels_only", "metrics"}),
)
TRACKING = Destination(
    name="tracking-server",
    kind="tracking",
    zone=Zone.institution,
    accepts=frozenset({"labels_only", "metrics"}),
)


class RecordingGate:
    """Wraps the reference gate and records every check."""

    def __init__(self) -> None:
        self._gate = StaticGate(DEFAULT_MATRIX)
        self.matrix = self._gate.matrix
        self.calls: list[tuple[str, PayloadKind]] = []

    def check(
        self, data: DataPolicy, destination: Destination, payload: PayloadKind, **kwargs: Any
    ) -> Decision:
        self.calls.append((destination.name, payload))
        return self._gate.check(data, destination, payload, **kwargs)

    def require(
        self, data: DataPolicy, destination: Destination, payload: PayloadKind, **kwargs: Any
    ) -> Decision:
        self.calls.append((destination.name, payload))
        return self._gate.require(data, destination, payload, **kwargs)


def _exporter(directory: Path, format: str = "amber-bundle/0.1", **kwargs: Any) -> LocalExporter:
    return LocalExporter(
        directory,
        format=format,  # type: ignore[arg-type]
        variables=kwargs.pop("variables", fx.VARIABLES),
        dataset=DATASET,
        producer=ManifestProducer(name="export-test", version="1", run_id=RUN),
        policy_version=DEFAULT_MATRIX.version,
        cases=[CASE],
        sources=[fx.SOURCE],
        **kwargs,
    )


def test_references_satisfy_their_protocols(tmp_path: Path) -> None:
    assert isinstance(StaticGate(DEFAULT_MATRIX), PolicyGate)
    assert isinstance(RecordingGate(), PolicyGate)
    assert isinstance(_exporter(tmp_path), Exporter)
    with pytest.raises(TypeError):
        StaticGate("matrix")  # type: ignore[arg-type]
    gate = StaticGate(DEFAULT_MATRIX)
    assert gate.check(DATA, TRACKING, "labels_only", now=NOW).permitted
    with pytest.raises(PolicyViolation):
        gate.require(DATA, TRACKING, "derived_text", now=NOW)


# IFC-08 ------------------------------------------------------------------------------------


def test_a_bundle_export_gates_every_file_and_receipts_what_it_wrote(tmp_path: Path) -> None:
    gate = RecordingGate()
    result = _rich_result()
    receipt = _exporter(tmp_path).export([result], ARTIFACTS, gate=gate, data=DATA, now=NOW)

    files = {item.file: item for item in receipt.files}
    assert sorted(gate.calls) == sorted(
        (ARTIFACTS.name, item.payload_kind) for item in receipt.files
    )
    assert len(gate.calls) == len(files)
    assert "manifest.json" in files and files["manifest.json"].payload_kind == "labels_only"
    assert files["evidence.parquet"].payload_kind == "derived_text"
    assert all(item.decision.permitted for item in receipt.files)
    assert all(item.decision.destination == ARTIFACTS.name for item in receipt.files)

    location = Path(receipt.location)
    written = read_bundle(location)
    for name, table in written.manifest.tables.items():
        assert files[f"{name}.parquet"].content_sha256 == table.content_sha256
    manifest_bytes = (location / "manifest.json").read_bytes()
    assert files["manifest.json"].content_sha256 == hashlib.sha256(manifest_bytes).hexdigest()
    loaded = load_bundle(written, variables=fx.VARIABLES, resolver=InMemoryResolver([fx.SOURCE]))
    assert loaded.results == (result,)  # type: ignore[union-attr]
    assert fx.TEXT[:12] not in receipt.model_dump_json()


def test_one_refused_file_means_nothing_is_written(tmp_path: Path) -> None:
    gate = RecordingGate()
    with pytest.raises(PolicyViolation) as failure:
        _exporter(tmp_path / "out").export(
            [_rich_result()], TRACKING, gate=gate, data=DATA, now=NOW
        )
    decisions = failure.value.decisions
    assert len(decisions) == len(gate.calls)
    assert {decision.payload for decision in decisions if not decision.permitted} == {
        "derived_text"
    }
    assert any(decision.permitted for decision in decisions)
    assert failure.value.decision.reason == "payload_not_accepted"
    assert not (tmp_path / "out").exists()


def test_a_failed_write_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import amber.reference.exporter as module

    def broken(bundle: Any, directory: Path, **kwargs: Any) -> Any:
        directory.mkdir()
        (directory / "partial.parquet").write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(module, "write_bundle", broken)
    with pytest.raises(OSError):
        _exporter(tmp_path).export(
            [_rich_result()], ARTIFACTS, gate=RecordingGate(), data=DATA, now=NOW
        )
    assert list(tmp_path.iterdir()) == []


# amber-labels/0.1 --------------------------------------------------------------------------


def test_a_labels_export_holds_only_labels_only_files(tmp_path: Path) -> None:
    result = _rich_result()
    receipt = _exporter(tmp_path, format="amber-labels/0.1").export(
        [result], TRACKING, gate=RecordingGate(), data=DATA, now=NOW
    )
    assert {item.payload_kind for item in receipt.files} == {"labels_only"}
    assert {item.file for item in receipt.files} == {
        "manifest.json",
        "cases.parquet",
        "claims.parquet",
        "evidence_edges.parquet",
        "label_records.parquet",
        "results.parquet",
    }
    bundle = read_bundle(Path(receipt.location))
    contents = load_labels(bundle, variables=fx.VARIABLES)
    assert contents.label_records == labels_only_view(result, case=CASE, variables=fx.VARIABLES)
    assert contents.cases == (CASE,)
    with pytest.raises(BundleError, match="invalid_bundle"):
        load_bundle(bundle, variables=fx.VARIABLES, resolver=InMemoryResolver([fx.SOURCE]))
    serialized = json.dumps(
        [row.get("value") for row in contents.tables["claims"]] + [receipt.model_dump(mode="json")]
    )
    assert fx.TEXT[:12] not in serialized


def test_label_records_from_a_labeler_export_too(tmp_path: Path) -> None:
    record = labels_only_view(_rich_result(), case=CASE, variables=fx.VARIABLES)[0]
    receipt = _exporter(tmp_path, format="amber-labels/0.1").export(
        [record], TRACKING, gate=RecordingGate(), data=DATA, now=NOW
    )
    contents = load_labels(read_bundle(Path(receipt.location)), variables=fx.VARIABLES)
    assert contents.label_records == (record,)


class _FreeText(AnswerModel):
    note: str = Field(strict=True)


def test_open_answer_models_cannot_produce_a_labels_bundle() -> None:
    open_set = VariableSet.create(
        name=fx.VARIABLES.name,
        version="open",
        variables=[
            Variable(
                name="note", level="note", answer_model=_FreeText, instructions="x", final=True
            )
        ],
    )
    record = LabelRecord(
        schema_version="amber/0.1",
        case_id=CASE.case_id,
        variable="note",
        variable_set=open_set.name,
        variable_set_version=open_set.version,
        variable_set_ref=open_set.schema_ref,
        tier="silver",
        outcome_status="answered",
        value={"note": fx.TEXT},
        producer="model:x@1",
        split=CASE.split,
    )
    with pytest.raises(BundleError) as failure:
        build_labels_bundle(
            variables=open_set,
            dataset=DATASET,
            producer=ManifestProducer(name="x", version="1"),
            policy_version="p",
            created_at=NOW,
            cases=[CASE],
            label_records=[record],
        )
    assert failure.value.code == "derived_text_in_labels"
    assert fx.TEXT[:12] not in str(failure.value)


def test_a_labels_bundle_with_a_derived_text_table_is_rejected(tmp_path: Path) -> None:
    receipt = _exporter(tmp_path, format="amber-labels/0.1").export(
        [_rich_result()], TRACKING, gate=RecordingGate(), data=DATA, now=NOW
    )
    labels = read_bundle(Path(receipt.location))
    full = _exporter(tmp_path / "full").export(
        [_rich_result()], ARTIFACTS, gate=RecordingGate(), data=DATA, now=NOW
    )
    evidence_rows = read_bundle(Path(full.location)).tables["evidence"]
    entry = read_bundle(Path(full.location)).manifest.tables["evidence"]
    smuggled = type(labels)(
        manifest=labels.manifest.model_copy(
            update={
                "tables": {**labels.manifest.tables, "evidence": entry},
                "payload_kinds": {
                    **labels.manifest.payload_kinds,
                    "evidence.parquet": "derived_text",
                },
            }
        ),
        tables={**labels.tables, "evidence": evidence_rows},
    )
    with pytest.raises(BundleError) as failure:
        check_bundle(smuggled, variables=fx.VARIABLES)
    assert (failure.value.code, failure.value.table) == ("unexpected_table", "evidence")


def test_formats_hold_only_their_items(tmp_path: Path) -> None:
    record = labels_only_view(_rich_result(), case=CASE, variables=fx.VARIABLES)[0]
    with pytest.raises(ExportError, match="unsupported_items"):
        _exporter(tmp_path).export([record], ARTIFACTS, gate=RecordingGate(), data=DATA, now=NOW)
    with pytest.raises(ExportError, match="unsupported_items"):
        _exporter(tmp_path).export([], ARTIFACTS, gate=RecordingGate(), data=DATA, now=NOW)
    with pytest.raises(ExportError, match="unsupported_format"):
        _exporter(tmp_path, format="mlflow-dataset")
