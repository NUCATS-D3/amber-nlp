"""Bundles: tables, manifest, content hashes, payload kinds, and resolver loading (contract v0.1
sections 5.2 and 5.4 to 5.6; catalog WIR-01 to WIR-11 and WIR-13)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from pydantic import Field

from amber.annotations import AnnotationValidationError, example_from_projection, replay
from amber.bundles import (
    TABLES,
    Bundle,
    BundleContents,
    BundleError,
    ManifestDataset,
    ManifestProducer,
    UnvalidatedBundle,
    build_bundle,
    content_sha256,
    load_bundle,
    payload_kind,
)
from amber.cases import CaseValidationError, create_case_result, load_case_result
from amber.conformance import fixtures as fx
from amber.examples import ExampleValidationError
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.ids import new_ulid
from amber.policy import DEFAULT_MATRIX, DataPolicy, Destination, check
from amber.reference import InMemoryResolver
from amber.schemas import (
    AnswerModel,
    Budget,
    CaseOutcome,
    CaseResult,
    InferenceEvidence,
    MentionReading,
    Sensitivity,
    Source,
    SourceKind,
    Usage,
    Variable,
    VariableOutcome,
    VariableSet,
    Zone,
)

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
from amber.bundles.parquet import read_bundle, write_bundle  # noqa: E402

RUN = fx.RUN_IDS[0]
CASE = fx.case(dataset=fx.DATASET)
DATASET = ManifestDataset(
    name=fx.DATASET, manifest_hash="synthetic-manifest-hash", sensitivity=Sensitivity.synthetic
)
CREATED = datetime(2026, 1, 1, tzinfo=UTC)
RESOLVER = InMemoryResolver([fx.SOURCE])


def _rich_result(
    *, budget: Budget | None = None, usage: Usage | None = None, run_id: str = RUN
) -> CaseResult:
    graph = EvidenceGraph(
        source=fx.SOURCE, variables=fx.VARIABLES, sensitivity=Sensitivity.synthetic
    )
    stamped = fx.provenance("model:bundle-test@1", run_id)
    reading = MentionReading(
        reading_id=new_ulid(), mention_id=fx.MENTION.mention_id, value="mass", provenance=stamped
    )
    graph.register_mention(fx.MENTION, [reading])
    graph.register_evidence(fx.POSITION)
    graph.register_evidence(fx.SENTENCE)
    finding = graph.commit_claim(
        "finding",
        {"present": True},
        [fx.POSITION.evidence_id],
        "Invented rationale.",
        provenance=stamped,
        reading_id=reading.reading_id,
        confidence=0.5,
    )
    bridge = InferenceEvidence(
        evidence_id=new_ulid(),
        rationale="Invented derivation.",
        inputs=[finding.claim_id, fx.SENTENCE.evidence_id],
        trace_id="synthetic-trace",
        span_id=None,
        provenance=stamped,
    )
    graph.register_evidence(bridge)
    summary = graph.commit_claim(
        "summary", {"present": True}, [bridge.evidence_id], "Invented.", provenance=stamped
    )
    answered = CaseOutcome(status="answered", provenance=stamped)
    return create_case_result(
        CASE,
        graph=graph,
        run_id=run_id,
        budget=budget,
        usage=usage,
        tool_calls=3,
        grounding_failures=1,
        escalated=True,
        trace_id="synthetic-trace",
        variable_outcomes={
            "finding": VariableOutcome(
                variable="finding", outcome=answered, final_claim_ids=[finding.claim_id]
            ),
            "summary": VariableOutcome(
                variable="summary", outcome=answered, final_claim_ids=[summary.claim_id]
            ),
        },
    )


def _decision() -> Any:
    destination = Destination(
        name="bundle-tracking",
        kind="tracking",
        zone=Zone.institution,
        accepts=frozenset({"labels_only"}),
    )
    return check(
        DEFAULT_MATRIX,
        DataPolicy(sensitivity=Sensitivity.synthetic),
        destination,
        "labels_only",
        now=CREATED,
    )


def _run(**kwargs: Any) -> Bundle:
    return build_bundle(
        kind="run",
        variables=kwargs.pop("variables", fx.VARIABLES),
        dataset=DATASET,
        producer=ManifestProducer(name="bundle-test", version="1", run_id=RUN),
        policy_version="synthetic-policy@1",
        created_at=CREATED,
        sources=[fx.SOURCE],
        cases=[CASE],
        results=kwargs.pop("results", None) or [_rich_result()],
        decisions=[_decision()],
        **kwargs,
    )


def _gold() -> tuple[Bundle, Any]:
    log, _ = fx.adjudicated()
    projection = replay(
        log.events, case=CASE, source=fx.SOURCE, variables=fx.VARIABLES, spans=fx.SPANS
    )
    example = example_from_projection(
        projection, case=CASE, source=fx.SOURCE, variables=fx.VARIABLES
    )
    bundle = build_bundle(
        kind="gold",
        variables=fx.VARIABLES,
        dataset=DATASET,
        producer=ManifestProducer(name="bundle-test", version="1"),
        policy_version="synthetic-policy@1",
        created_at=CREATED,
        sources=[fx.SOURCE],
        cases=[CASE],
        examples=[example],
        events=log.events,
        spans=fx.SPANS,
    )
    return bundle, example


def _review() -> Bundle:
    log, _ = fx.blind_streams()
    return build_bundle(
        kind="review",
        variables=fx.VARIABLES,
        dataset=DATASET,
        producer=ManifestProducer(name="bundle-test", version="1"),
        policy_version="synthetic-policy@1",
        created_at=CREATED,
        sources=[fx.SOURCE],
        cases=[CASE],
        events=log.events,
        spans=fx.SPANS,
    )


def _load(bundle: Bundle, **kwargs: Any) -> Any:
    kwargs.setdefault("resolver", RESOLVER)
    return load_bundle(bundle, variables=kwargs.pop("variables", fx.VARIABLES), **kwargs)


def _assert_bundle_error(
    code: str,
    function: Any,
    /,
    *args: Any,
    table: str | None = None,
    column: str | None = None,
    **kwargs: Any,
) -> BundleError:
    with pytest.raises(BundleError) as failure:
        function(*args, **kwargs)
    assert failure.value.code == code
    if table is not None:
        assert failure.value.table == table
    if column is not None:
        assert failure.value.column == column
    return failure.value


def _tamper(bundle: Bundle, table: str, change: Any, *, rehash: bool = True) -> Bundle:
    rows = [dict(row) for row in bundle.tables[table]]
    change(rows)
    tables = dict(bundle.tables)
    tables[table] = tuple(rows)
    manifest = bundle.manifest
    if rehash:
        entry = manifest.tables[table].model_copy(
            update={"rows": len(rows), "content_sha256": content_sha256(table, rows)}
        )
        manifest = manifest.model_copy(update={"tables": {**manifest.tables, table: entry}})
    return replace(bundle, manifest=manifest, tables=MappingProxyType(tables))


# WIR-01 and WIR-13: round trips ------------------------------------------------------------


def test_a_run_bundle_round_trips_through_parquet(tmp_path: Path) -> None:
    result = _rich_result(budget=Budget(max_calls=5), usage=Usage(calls=3, tokens=12))
    bundle = _run(results=[result])
    written = write_bundle(bundle, tmp_path / "first")
    read = read_bundle(tmp_path / "first")
    again = write_bundle(read, tmp_path / "second")

    for name in bundle.tables:
        assert (
            written.manifest.tables[name].content_sha256
            == bundle.manifest.tables[name].content_sha256
        )
        assert (
            again.manifest.tables[name].content_sha256
            == bundle.manifest.tables[name].content_sha256
        )
        assert written.manifest.tables[name].file_sha256 is not None
    assert read.tables == bundle.tables

    contents = _load(read)
    assert isinstance(contents, BundleContents)
    (loaded,) = contents.results
    assert loaded == result
    assert loaded.model_dump_json() == result.model_dump_json()
    assert (loaded.run_id, loaded.tool_calls, loaded.grounding_failures) == (RUN, 3, 1)
    assert (loaded.escalated, loaded.trace_id) == (True, "synthetic-trace")
    assert (loaded.budget, loaded.usage) == (Budget(max_calls=5), Usage(calls=3, tokens=12))
    assert contents.cases == (CASE,)
    assert contents.decisions == (_decision(),)


def test_rows_are_written_sorted_by_key(tmp_path: Path) -> None:
    write_bundle(_run(), tmp_path / "bundle")
    for name, schema in TABLES.items():
        path = tmp_path / "bundle" / schema.file
        if not path.exists():
            continue
        rows = pq.read_table(path).to_pylist()
        keys = [
            tuple("" if row[key] is None else str(row[key]) for key in schema.key) for row in rows
        ]
        assert keys == sorted(keys), name


def test_gold_and_review_bundles_round_trip(tmp_path: Path) -> None:
    gold, example = _gold()
    write_bundle(gold, tmp_path / "gold")
    contents = _load(read_bundle(tmp_path / "gold"))
    assert contents.examples == (example,)
    assert contents.examples[0].model_dump_json() == example.model_dump_json()
    assert len(contents.events) == 17

    review = _review()
    write_bundle(review, tmp_path / "review")
    loaded = _load(read_bundle(tmp_path / "review"))
    assert len(loaded.events) == len(review.tables["annotation_events"])
    assert {type(span).__name__ for span in loaded.spans} == {"Mention", "Inclusion"}


def test_run_bundles_need_results_and_one_run() -> None:
    bundle = _run()
    tables = {name: rows for name, rows in bundle.tables.items() if name != "results"}
    manifest = bundle.manifest.model_copy(
        update={"tables": {k: v for k, v in bundle.manifest.tables.items() if k != "results"}}
    )
    _assert_bundle_error(
        "missing_table",
        _load,
        Bundle(manifest=manifest, tables=MappingProxyType(tables)),
        table="results",
    )
    _assert_bundle_error(
        "run_mismatch", _run, results=[_rich_result(run_id=fx.RUN_IDS[1])], table="results"
    )
    with pytest.raises(BundleError):
        build_bundle(
            kind="run",
            variables=fx.VARIABLES,
            dataset=DATASET,
            producer=ManifestProducer(name="x", version="1", run_id=RUN),
            policy_version="p",
            created_at=CREATED,
            sources=[fx.SOURCE],
            cases=[CASE],
        )


def test_an_over_budget_row_that_does_not_say_so_is_rejected() -> None:
    bundle = _run(results=[_rich_result(budget=Budget(max_calls=5), usage=Usage(calls=3))])
    tampered = _tamper(
        bundle,
        "results",
        lambda rows: rows[0].update(usage={"calls": 6, "tokens": 0, "retries": 0, "seconds": 0}),
    )
    with pytest.raises(CaseValidationError, match="budget_exceeded"):
        _load(tampered)


# WIR-02: versions --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "loads"), [("amber/0.0", True), ("amber/1.0", False), ("amber/0.9", False)]
)
def test_manifest_versions(tmp_path: Path, version: str, loads: bool) -> None:
    write_bundle(_run(), tmp_path / "bundle")
    path = tmp_path / "bundle" / "manifest.json"
    raw = json.loads(path.read_text())
    raw["schema_version"] = version
    path.write_text(json.dumps(raw))
    if loads:
        assert isinstance(_load(read_bundle(tmp_path / "bundle")), BundleContents)
    else:
        _assert_bundle_error("unsupported_version", read_bundle, tmp_path / "bundle")


# WIR-03: structure -------------------------------------------------------------------------


def test_a_missing_table_file_is_rejected(tmp_path: Path) -> None:
    write_bundle(_run(), tmp_path / "bundle")
    (tmp_path / "bundle" / "claims.parquet").unlink()
    _assert_bundle_error("missing_table", read_bundle, tmp_path / "bundle", table="claims")


def _rewrite(path: Path, transform: Any) -> None:
    table = pq.read_table(path)
    pq.write_table(transform(table), path)


@pytest.mark.parametrize(
    ("transform", "code", "column"),
    [
        (lambda t: t.drop_columns(["claim_id"]), "missing_column", "claim_id"),
        (
            lambda t: t.set_column(
                t.schema.get_field_index("confidence"),
                "confidence",
                pa.array([str(v) for v in t["confidence"].to_pylist()]),
            ),
            "wrong_type",
            "confidence",
        ),
        (
            lambda t: t.append_column("surprise", pa.array([1] * t.num_rows)),
            "unknown_column",
            "surprise",
        ),
    ],
)
def test_column_problems_name_the_table_and_column_only(
    tmp_path: Path, transform: Any, code: str, column: str
) -> None:
    written = write_bundle(_run(), tmp_path / "bundle", file_hashes=False)
    _rewrite(tmp_path / "bundle" / "claims.parquet", transform)
    error = _assert_bundle_error(
        code, read_bundle, tmp_path / "bundle", table="claims", column=column
    )
    assert str(error) == f"{code}: claims.{column}"
    assert written.manifest.tables["claims"].file_sha256 is None


def test_duplicate_keys_and_nulls_are_rejected() -> None:
    bundle = _run()
    duplicated = _tamper(bundle, "claims", lambda rows: rows.append(dict(rows[0])))
    _assert_bundle_error("duplicate_key", _load, duplicated, table="claims")
    nulled = _tamper(bundle, "claims", lambda rows: rows[0].update(value=None))
    _assert_bundle_error("null_value", _load, nulled, table="claims", column="value")


# WIR-04: resolver --------------------------------------------------------------------------


def test_without_a_resolver_rows_are_parsed_but_refused_by_boundaries() -> None:
    unvalidated = _load(_run(), resolver=None)
    assert isinstance(unvalidated, UnvalidatedBundle)
    row = unvalidated.tables["claims"][0]
    assert row.get("variable") in {"finding", "summary"}
    with pytest.raises(CaseValidationError, match="invalid_schema"):
        load_case_result(row, case=CASE, source=fx.SOURCE, variables=fx.VARIABLES)  # type: ignore[arg-type]


def test_resolver_failures_name_only_the_source() -> None:
    _assert_bundle_error(
        "source_unavailable", _load, _run(), resolver=InMemoryResolver(), table="sources"
    )

    class _Forging:
        def resolve(self, source_id: str) -> Source | None:
            return Source.create(
                patient_id=fx.SOURCE.patient_id,
                kind=SourceKind.note,
                external_id=fx.SOURCE.external_id,
                datetime=None,
                text=fx.SOURCE.text + " ",
                record=None,
            )

    error = _assert_bundle_error("source_hash_mismatch", _load, _run(), resolver=_Forging())
    assert "mass" not in str(error)


# WIR-05 and WIR-11: payload kinds ----------------------------------------------------------


class _FreeText(AnswerModel):
    note: str = Field(strict=True)


def test_payload_kinds_follow_schemas_and_answer_models() -> None:
    kinds = _run().manifest.payload_kinds
    assert kinds["claims.parquet"] == "labels_only"
    assert kinds["sources.parquet"] == kinds["variable_outcomes.parquet"] == "derived_text"
    assert kinds["evidence.parquet"] == kinds["mention_readings.parquet"] == "derived_text"
    assert kinds["results.parquet"] == kinds["cases.parquet"] == "labels_only"
    assert kinds["decisions.parquet"] == kinds["evidence_edges.parquet"] == "labels_only"
    open_variable = Variable(
        name="open", level="note", answer_model=_FreeText, instructions="x", final=True
    )
    open_set = VariableSet.create(name="open", version="1", variables=[open_variable])
    assert payload_kind("claims", open_set) == "derived_text"


@pytest.mark.parametrize(
    "file", ["evidence.parquet", "claims.parquet", "variable_outcomes.parquet"]
)
def test_a_manifest_that_misdeclares_payload_kinds_is_rejected(file: str) -> None:
    bundle = _run()
    kinds = dict(bundle.manifest.payload_kinds)
    kinds[file] = "derived_text" if kinds[file] == "labels_only" else "labels_only"
    tampered = replace(bundle, manifest=bundle.manifest.model_copy(update={"payload_kinds": kinds}))
    _assert_bundle_error("payload_kind_mismatch", _load, tampered)


# WIR-06: hashes and scope ------------------------------------------------------------------


def test_a_changed_table_is_rejected_before_validation() -> None:
    bundle = _run()
    tampered = _tamper(bundle, "claims", lambda rows: rows[0].update(confidence=0.9), rehash=False)
    _assert_bundle_error("content_mismatch", _load, tampered, resolver=None, table="claims")


def test_a_changed_file_is_rejected_by_its_file_hash(tmp_path: Path) -> None:
    write_bundle(_run(), tmp_path / "bundle")
    _rewrite(
        tmp_path / "bundle" / "claims.parquet",
        lambda table: table.replace_schema_metadata({b"x": b"y"}),
    )
    _assert_bundle_error("file_mismatch", read_bundle, tmp_path / "bundle", table="claims")


@pytest.mark.parametrize(
    "change",
    [
        {"dataset": "other"},
        {"dataset_manifest_hash": "other"},
        {"variable_set_version": "9"},
        {"sensitivity": "phi"},
    ],
)
def test_a_case_row_outside_the_manifest_scope_is_rejected(change: dict[str, Any]) -> None:
    tampered = _tamper(_run(), "cases", lambda rows: rows[0].update(change))
    _assert_bundle_error("case_mismatch", _load, tampered, resolver=None, table="cases")


def test_bundles_hold_one_dataset_and_one_variable_set() -> None:
    with pytest.raises(BundleError, match="case_mismatch"):
        build_bundle(
            kind="run",
            variables=fx.VARIABLES,
            dataset=DATASET,
            producer=ManifestProducer(name="x", version="1", run_id=RUN),
            policy_version="p",
            created_at=CREATED,
            sources=[fx.SOURCE],
            cases=[CASE.model_copy(update={"dataset": "other-set"})],
            results=[_rich_result()],
        )
    other = VariableSet.model_validate({**fx.VARIABLES.model_dump(), "version": "9"})
    _assert_bundle_error("variable_set_mismatch", _load, _run(), variables=other)


# WIR-07: canonical JSON columns ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ['{"present": true}', '{"present":true }', '{"b":1,"a":2}', '{"x":1e-07}', '{"x":1.0}'],
)
def test_non_canonical_json_columns_are_rejected(tmp_path: Path, text: str) -> None:
    write_bundle(_run(), tmp_path / "bundle", file_hashes=False)

    def transform(table: Any) -> Any:
        index = table.schema.get_field_index("value")
        values = [text] + table["value"].to_pylist()[1:]
        return table.set_column(index, "value", pa.array(values, type=pa.string()))

    _rewrite(tmp_path / "bundle" / "claims.parquet", transform)
    _assert_bundle_error(
        "non_canonical_json", read_bundle, tmp_path / "bundle", table="claims", column="value"
    )


# WIR-08 and WIR-10 -------------------------------------------------------------------------


def test_unknown_evidence_kinds_and_event_actions_are_rejected() -> None:
    def structured(rows: list[dict[str, Any]]) -> None:
        inclusion = next(row for row in rows if row["kind"] == "inclusion")
        inclusion["kind"] = "structured"

    with pytest.raises(GraphValidationError, match="unsupported_evidence"):
        _load(_tamper(_run(), "evidence", structured))
    tampered = _tamper(
        _review(), "annotation_events", lambda rows: rows[0].update(action="claim.delete")
    )
    _assert_bundle_error("invalid_bundle", _load, tampered, table="annotation_events")


# WIR-09: extension columns -----------------------------------------------------------------


def test_x_columns_survive_and_other_unknown_columns_do_not(tmp_path: Path) -> None:
    bundle = _tamper(_run(), "claims", lambda rows: [row.update(x_reviewer_note=7) for row in rows])
    write_bundle(bundle, tmp_path / "first")
    read = read_bundle(tmp_path / "first")
    assert {row["x_reviewer_note"] for row in read.tables["claims"]} == {7}
    write_bundle(read, tmp_path / "second")
    assert read_bundle(tmp_path / "second").tables == read.tables
    assert isinstance(_load(read), BundleContents)
    unknown = _tamper(_run(), "claims", lambda rows: [row.update(reviewer_note=7) for row in rows])
    _assert_bundle_error("unknown_column", _load, unknown, table="claims", column="reviewer_note")


# Every row is consumed by a validated record (amber-fc8.4) -------------------------------


def _extra(bundle: Bundle, table: str, row: Any, **changes: Any) -> Bundle:
    """``bundle`` with one more ``table`` row, rehashed so only revalidation can refuse it."""
    return _tamper(bundle, table, lambda rows: rows.append({**row, **changes}))


def test_a_reading_row_no_result_reaches_is_refused() -> None:
    bundle = _run()
    (reading,) = bundle.tables["mention_readings"]
    ghost = _extra(bundle, "mention_readings", reading, reading_id=new_ulid(),
                   case_id="ghost-case", status="invented-status")  # fmt: skip
    _assert_bundle_error("unconsumed_rows", _load, ghost, table="mention_readings")


def test_an_edge_between_unknown_records_is_refused() -> None:
    bundle = _run()
    edge = {"claim_id": new_ulid(), "evidence_id": new_ulid(), "role": None, "weight": None}
    _assert_bundle_error("unconsumed_rows", _load, _extra(bundle, "evidence_edges", edge),
                         table="evidence_edges")  # fmt: skip


def test_a_claim_row_in_a_review_bundle_is_refused() -> None:
    claim = _run().tables["claims"][0]
    forged = _extra(_review(), "claims", claim, value={"invented": True}, status="invented")
    _assert_bundle_error("unconsumed_rows", _load, forged, table="claims")


def test_a_gold_bundle_without_examples_is_refused() -> None:
    bundle, _ = _gold()
    emptied = _tamper(_tamper(bundle, "examples", list.clear), "annotation_events", list.clear)
    _assert_bundle_error("invalid_bundle", _load, emptied)


def test_a_case_whose_patient_is_not_its_source_s_is_refused() -> None:
    bundle = _review()
    moved = _tamper(bundle, "cases", lambda rows: rows[0].update(patient_id="other-patient"))
    _assert_bundle_error("case_mismatch", _load, moved, table="cases")


def test_an_event_for_a_case_outside_the_bundle_is_refused() -> None:
    bundle, _ = _gold()
    event = bundle.tables["annotation_events"][0]
    stray = _extra(bundle, "annotation_events", event, event_id=new_ulid(), case_id="ghost-case")
    _assert_bundle_error("case_mismatch", _load, stray, table="annotation_events")


_LOAD_ERRORS = (BundleError, CaseValidationError, GraphValidationError, ExampleValidationError,
                AnnotationValidationError)  # fmt: skip

# Tables whose rows only exist as parts of a result or an Example. Decisions, events, and
# spans are records in their own right: decisions and spans revalidate alone, and events are
# parsed (replayed only in some bundle kinds, amber-fc8.12).
_PART_TABLES = ("claims", "mention_readings", "evidence", "evidence_edges", "variable_outcomes",
                "results", "examples")  # fmt: skip


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    kind=st.sampled_from(["run", "gold"]),
    table=st.sampled_from(_PART_TABLES),
    index=st.integers(min_value=0, max_value=4),
    key=st.text(alphabet="abcdefghjkmnpqrstvwxyz0123456789", min_size=1, max_size=12),
)
def test_an_extra_part_row_never_loads(kind: str, table: str, index: int, key: str) -> None:
    bundle = _run() if kind == "run" else _gold()[0]
    assume(table in bundle.tables)
    rows = bundle.tables[table] or _run().tables[table]
    row = rows[index % len(rows)]
    fresh = {column: f"x{key}{column}" for column in TABLES[table].key
             if isinstance(row[column], str)}  # fmt: skip
    assume(fresh)
    with pytest.raises(_LOAD_ERRORS):
        _load(_extra(bundle, table, row, **fresh))
