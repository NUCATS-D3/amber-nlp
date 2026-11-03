"""Bundles in memory: rows, manifest, content hashes, payload kinds, building, and loading.

Contract v0.1 sections 5.2 and 5.4 to 5.6. Rows are dicts whose JSON columns hold parsed JSON
values; ``amber.bundles.parquet`` moves them to and from files. Nothing here needs pyarrow.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal, NoReturn, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from amber.annotations import replay, verify_example
from amber.bundles.schema import REQUIRED, TABLES, BundleKind, payload_kind
from amber.cases import load_case_result
from amber.examples import load_example
from amber.grounding import exact_mention, exact_quote
from amber.ids import canonical_json, canonical_sha256
from amber.interfaces import SourceResolver
from amber.policy import Decision, PayloadKind
from amber.schemas import (
    AnnotationEvent,
    Case,
    CaseResult,
    Claim,
    EvidenceEdge,
    Example,
    Inclusion,
    InferenceEvidence,
    Mention,
    MentionReading,
    Provenance,
    Sensitivity,
    Source,
    VariableOutcome,
    VariableSet,
)
from amber.schemas._wire import SCHEMA_VERSION, SchemaVersion, Timestamp

BundleErrorCode: TypeAlias = Literal[
    "missing_table",
    "unexpected_table",
    "missing_column",
    "unknown_column",
    "wrong_type",
    "null_value",
    "duplicate_key",
    "non_canonical_json",
    "content_mismatch",
    "file_mismatch",
    "payload_kind_mismatch",
    "unsupported_version",
    "invalid_manifest",
    "variable_set_mismatch",
    "case_mismatch",
    "run_mismatch",
    "invalid_bundle",
    "source_unavailable",
    "source_hash_mismatch",
    "orphan_evidence",
]


class BundleError(ValueError):
    """A stable bundle diagnostic naming at most a table and a column, never a value."""

    def __init__(
        self, code: BundleErrorCode, table: str | None = None, column: str | None = None
    ) -> None:
        self.code = code
        self.table = table
        self.column = column
        where = ".".join(part for part in (table, column) if part)
        super().__init__(f"{code}: {where}" if where else code)


def _raise(code: BundleErrorCode, table: str | None = None, column: str | None = None) -> NoReturn:
    raise BundleError(code, table, column) from None


# Manifest ----------------------------------------------------------------------------------

_FROZEN = ConfigDict(extra="forbid", frozen=True)


class ManifestProducer(BaseModel):
    model_config = _FROZEN

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    run_id: str | None = None


class ManifestVariableSet(BaseModel):
    model_config = _FROZEN

    name: str
    version: str
    schema_ref: str


class ManifestDataset(BaseModel):
    model_config = _FROZEN

    name: str = Field(min_length=1)
    manifest_hash: str = Field(min_length=1)
    sensitivity: Sensitivity
    restriction_ids: tuple[str, ...] = ()


class ManifestSources(BaseModel):
    model_config = _FROZEN

    count: int = Field(ge=0)
    text_hash_algorithm: Literal["sha256"] = "sha256"


class ManifestTable(BaseModel):
    model_config = _FROZEN

    rows: int = Field(ge=0)
    content_sha256: str
    file_sha256: str | None = None


class Manifest(BaseModel):
    """The bundle manifest (contract v0.1 section 5.5); ``labels_only``."""

    model_config = _FROZEN

    schema_version: SchemaVersion = SCHEMA_VERSION
    bundle_kind: BundleKind
    producer: ManifestProducer
    variable_set: ManifestVariableSet
    dataset: ManifestDataset
    policy_version: str = Field(min_length=1)
    sources: ManifestSources
    tables: dict[str, ManifestTable]
    payload_kinds: dict[str, PayloadKind]
    created_at: Timestamp


Row: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class Bundle:
    """A manifest and its tables' rows, JSON columns as parsed values."""

    manifest: Manifest
    tables: Mapping[str, tuple[Row, ...]]


# Rows and hashes ---------------------------------------------------------------------------


def text_hash(source: Source) -> str:
    """SHA-256 of the source text (UTF-8), or of the canonical record for structured rows."""
    if source.text is not None:
        return hashlib.sha256(source.text.encode("utf-8")).hexdigest()
    return canonical_sha256({"record": source.record})


def _sort_key(schema_key: Sequence[str], row: Row) -> tuple[tuple[bool, str], ...]:
    return tuple((row[column] is not None, str(row[column] or "")) for column in schema_key)


def sorted_rows(table: str, rows: Iterable[Row]) -> tuple[Row, ...]:
    key = TABLES[table].key
    return tuple(sorted(rows, key=lambda row: _sort_key(key, row)))


def content_sha256(table: str, rows: Iterable[Row]) -> str:
    """SHA-256 of the key-sorted rows, each the canonical JSON of the row, joined by newlines."""
    text = "\n".join(canonical_json(row) for row in sorted_rows(table, rows))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_value(table: str, name: str, kind: str, nullable: bool, value: Any) -> None:
    if value is None:
        if not nullable:
            _raise("null_value", table, name)
        return
    if kind == "string":
        valid = isinstance(value, str)
    elif kind == "int64":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "float64":
        valid = (
            isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
        )
    elif kind == "bool":
        valid = isinstance(value, bool)
    elif kind == "timestamp":
        valid = isinstance(value, datetime) and value.utcoffset() is not None
    else:
        try:
            canonical_json(value)
            valid = not isinstance(value, datetime)
        except (TypeError, ValueError):
            valid = False
    if not valid:
        _raise("wrong_type", table, name)


def check_rows(table: str, rows: Iterable[Row]) -> None:
    """Every column present with its type, only ``x_`` extras, no duplicate keys."""
    schema = TABLES[table]
    names = {column.name for column in schema.columns}
    keys: set[tuple[Any, ...]] = set()
    for row in rows:
        for column in schema.columns:
            if column.name not in row:
                _raise("missing_column", table, column.name)
            _check_value(table, column.name, column.type, column.nullable, row[column.name])
        for extra in set(row) - names:
            if not extra.startswith("x_"):
                _raise("unknown_column", table, extra)
        key = tuple(row[column] for column in schema.key)
        if key in keys:
            _raise("duplicate_key", table, ",".join(schema.key))
        keys.add(key)


_PROVENANCE_COLUMNS = tuple(
    column.name for column in TABLES["evidence"].columns if column.name.startswith("provenance_")
)


def _provenance_columns(provenance: Provenance | None) -> Row:
    if provenance is None:
        return {name: None for name in _PROVENANCE_COLUMNS}
    return {
        "provenance_producer": provenance.producer,
        "provenance_model": provenance.model,
        "provenance_prompt_versions": dict(provenance.prompt_versions),
        "provenance_run_id": provenance.run_id,
        "provenance_trace_id": provenance.trace_id,
        "provenance_zone": provenance.zone.value,
        "provenance_sensitivity": provenance.sensitivity.value,
        "provenance_destination": provenance.destination,
        "provenance_policy_version": provenance.policy_version,
        "provenance_restriction_ids": list(provenance.restriction_ids),
        "provenance_created_at": provenance.created_at,
        "provenance_version": provenance.version,
    }


def _provenance_payload(row: Row) -> dict[str, Any]:
    return {
        "producer": row["provenance_producer"],
        "model": row["provenance_model"],
        "prompt_versions": row["provenance_prompt_versions"],
        "run_id": row["provenance_run_id"],
        "trace_id": row["provenance_trace_id"],
        "zone": row["provenance_zone"],
        "sensitivity": row["provenance_sensitivity"],
        "destination": row["provenance_destination"],
        "policy_version": row["provenance_policy_version"],
        "restriction_ids": row["provenance_restriction_ids"],
        "created_at": row["provenance_created_at"],
        "version": row["provenance_version"],
    }


def _json(value: Any) -> Any:
    """A model value as plain JSON: lists and dicts of JSON scalars."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json(item) for item in value]
    return value


def _aware(table: str, column: str, value: datetime | None) -> datetime | None:
    if value is not None and value.utcoffset() is None:
        _raise("wrong_type", table, column)
    return value


def _source_row(source: Source) -> Row:
    return {
        "source_id": source.source_id,
        "patient_id": source.patient_id,
        "kind": source.kind.value,
        "external_id": source.external_id,
        "datetime": _aware("sources", "datetime", source.datetime),
        "text_hash": text_hash(source),
        "record": _json(source.record),
        "sections": [section.model_dump(mode="json") for section in source.sections],
        "meta": _json(source.meta),
    }


def _claim_row(claim: Claim, case_id: str, variables: VariableSet) -> Row:
    return {
        "claim_id": claim.claim_id,
        "case_id": case_id,
        "source_id": claim.source_id,
        "patient_id": claim.patient_id,
        "variable": claim.variable,
        "variable_set": claim.task,
        "variable_set_version": variables.version,
        "schema_ref": claim.schema_ref,
        "reading_id": claim.reading_id,
        "value": _json(claim.value),
        "effective_datetime": _aware("claims", "effective_datetime", claim.effective_datetime),
        "confidence": claim.confidence,
        "status": claim.status,
        **_provenance_columns(claim.provenance),
    }


def _claim_payload(row: Row) -> Row:
    return {
        "claim_id": row["claim_id"],
        "source_id": row["source_id"],
        "patient_id": row["patient_id"],
        "task": row["variable_set"],
        "variable": row["variable"],
        "reading_id": row["reading_id"],
        "schema_ref": row["schema_ref"],
        "value": row["value"],
        "effective_datetime": row["effective_datetime"],
        "confidence": row["confidence"],
        "status": row["status"],
        "provenance": _provenance_payload(row),
    }


def _evidence_row(record: Inclusion | InferenceEvidence) -> Row:
    row: Row = {column.name: None for column in TABLES["evidence"].columns}
    row["evidence_id"] = record.evidence_id
    row["kind"] = record.kind
    if isinstance(record, Inclusion):
        row.update(
            source_id=record.source_id,
            start=record.start,
            end=record.end,
            quote=record.quote,
            alignment=record.alignment,
            alignment_score=record.alignment_score,
        )
    else:
        row.update(
            rationale=record.rationale,
            inputs=list(record.inputs),
            trace_id=record.trace_id,
            span_id=record.span_id,
            **_provenance_columns(record.provenance),
        )
    return row


_RESERVED_EVIDENCE = ("field", "value", "datetime", "derivation")


def _evidence_payload(row: Row) -> Row:
    if row["kind"] == "inclusion":
        payload: Row = {
            key: row[key]
            for key in (
                "kind",
                "evidence_id",
                "source_id",
                "start",
                "end",
                "quote",
                "alignment",
                "alignment_score",
            )
        }
    elif row["kind"] == "inference":
        payload = {
            "kind": "inference",
            "evidence_id": row["evidence_id"],
            "rationale": row["rationale"],
            "inputs": row["inputs"],
            "trace_id": row["trace_id"],
            "span_id": row["span_id"],
            "provenance": _provenance_payload(row),
        }
    else:
        payload = {"kind": row["kind"], "evidence_id": row["evidence_id"]}
    for reserved in _RESERVED_EVIDENCE:
        if row[reserved] is not None:
            payload[reserved] = row[reserved]
    return payload


def _mention_row(mention: Mention) -> Row:
    return {
        "mention_id": mention.mention_id,
        "source_id": mention.source_id,
        "start": mention.start,
        "end": mention.end,
        "quote": mention.quote,
        "mention_type": mention.mention_type,
        "fragments": [list(fragment) for fragment in mention.fragments] or None,
        "section_category": mention.section_category,
    }


def _mention_payload(row: Row) -> Row:
    payload = {key: row[key] for key in _mention_row_keys()}
    payload["fragments"] = row["fragments"] or []
    return payload


def _mention_row_keys() -> tuple[str, ...]:
    return tuple(column.name for column in TABLES["mentions"].columns)


def _reading_row(reading: MentionReading, case_id: str) -> Row:
    return {
        "reading_id": reading.reading_id,
        "mention_id": reading.mention_id,
        "case_id": case_id,
        "label": reading.label,
        "value": reading.value,
        "unit": reading.unit,
        "attributes": {
            key: item.model_dump(mode="json") for key, item in reading.attributes.items()
        },
        "concept": None if reading.concept is None else reading.concept.model_dump(mode="json"),
        "inputs": list(reading.inputs) or None,
        "status": reading.status,
        **_provenance_columns(reading.provenance),
    }


def _reading_payload(row: Row) -> Row:
    return {
        "reading_id": row["reading_id"],
        "mention_id": row["mention_id"],
        "label": row["label"],
        "value": row["value"],
        "unit": row["unit"],
        "concept": row["concept"],
        "attributes": row["attributes"],
        "inputs": row["inputs"] or [],
        "status": row["status"],
        "provenance": _provenance_payload(row),
    }


def _outcome_row(case_id: str, outcome: VariableOutcome) -> Row:
    item = outcome.outcome
    return {
        "case_id": case_id,
        "variable": outcome.variable,
        "status": item.status,
        "reason": item.reason,
        "failure_kind": item.failure_kind,
        "reviewed_source_ids": list(item.reviewed_source_ids),
        "evidence_ids": list(item.evidence_ids),
        "final_claim_ids": list(outcome.final_claim_ids),
        **_provenance_columns(item.provenance),
    }


def _outcome_payload(row: Row) -> Row:
    return {
        "variable": row["variable"],
        "outcome": {
            "status": row["status"],
            "reason": row["reason"],
            "failure_kind": row["failure_kind"],
            "reviewed_source_ids": row["reviewed_source_ids"],
            "evidence_ids": row["evidence_ids"],
            "provenance": _provenance_payload(row),
        },
        "final_claim_ids": row["final_claim_ids"],
    }


def _event_row(event: AnnotationEvent) -> Row:
    return {
        "event_id": event.event_id,
        "case_id": event.case_id,
        "variable": event.variable,
        "actor": event.actor,
        "role": event.role,
        "round": event.round,
        "stream": event.stream,
        "action": event.action,
        "target_id": event.target_id,
        "before": _json(event.before),
        "after": _json(event.after),
        "reason": event.reason,
        "at": event.at,
        "external_ref": event.external_ref,
        **_provenance_columns(event.provenance),
    }


def _event_payload(row: Row) -> Row:
    payload = {
        key: row[key]
        for key in (
            "event_id",
            "case_id",
            "variable",
            "actor",
            "role",
            "round",
            "stream",
            "action",
            "target_id",
            "before",
            "after",
            "reason",
            "at",
            "external_ref",
        )
    }
    payload["provenance"] = _provenance_payload(row)
    return payload


def _decision_row(decision: Decision) -> Row:
    dumped = decision.model_dump(mode="json")
    row = {column.name: dumped[column.name] for column in TABLES["decisions"].columns}
    row["checked_at"] = decision.checked_at
    return row


def _plain(row: Row, table: str) -> Row:
    """Drop ``x_`` columns, which amber readers ignore."""
    names = {column.name for column in TABLES[table].columns}
    return {key: value for key, value in row.items() if key in names}


# Building ----------------------------------------------------------------------------------


class _Tables:
    def __init__(self) -> None:
        self.rows: dict[str, dict[tuple[Any, ...], Row]] = {}

    def add(self, table: str, row: Row) -> None:
        key = tuple(row[column] for column in TABLES[table].key)
        rows = self.rows.setdefault(table, {})
        if key in rows and rows[key] != row:
            _raise("duplicate_key", table, ",".join(TABLES[table].key))
        rows[key] = row

    def graph(
        self,
        case_id: str,
        variables: VariableSet,
        claims: Iterable[Claim],
        mentions: Iterable[Mention],
        readings: Iterable[MentionReading],
        evidence: Iterable[Inclusion | InferenceEvidence],
        edges: Iterable[EvidenceEdge],
    ) -> None:
        for claim in claims:
            self.add("claims", _claim_row(claim, case_id, variables))
        for mention in mentions:
            self.add("mentions", _mention_row(mention))
        for reading in readings:
            self.add("mention_readings", _reading_row(reading, case_id))
        for record in evidence:
            self.add("evidence", _evidence_row(record))
        for edge in edges:
            self.add("evidence_edges", edge.model_dump(mode="json"))


def build_bundle(
    *,
    kind: BundleKind,
    variables: VariableSet,
    dataset: ManifestDataset,
    producer: ManifestProducer,
    policy_version: str,
    created_at: datetime,
    sources: Iterable[Source],
    cases: Iterable[Case],
    results: Iterable[CaseResult] = (),
    examples: Iterable[Example] = (),
    events: Iterable[AnnotationEvent] = (),
    spans: Iterable[Inclusion | Mention] = (),
    decisions: Iterable[Decision] = (),
) -> Bundle:
    """Assemble a bundle of one dataset and one variable set from validated records.

    A ``run`` bundle holds results of one run (``producer.run_id``); a ``gold`` bundle one
    Example per case, with optional events; a ``review`` bundle events and the spans they name.
    """
    tables = _Tables()
    source_list = list(sources)
    by_source = {source.source_id: source for source in source_list}
    for source in source_list:
        tables.add("sources", _source_row(source))
    case_list = list(cases)
    case_ids: set[str] = set()
    for case in case_list:
        if (
            case.task != variables.name
            or case.dataset != dataset.name
            or case.sensitivity is not dataset.sensitivity
            or case.source_ids[0] not in by_source
            or case.patient_id != by_source[case.source_ids[0]].patient_id
        ):
            _raise("case_mismatch", "cases")
        tables.add(
            "cases",
            {
                "case_id": case.case_id,
                "dataset": dataset.name,
                "dataset_manifest_hash": dataset.manifest_hash,
                "source_id": case.source_ids[0],
                "patient_id": case.patient_id,
                "variable_set": variables.name,
                "variable_set_version": variables.version,
                "variable_set_ref": variables.schema_ref,
                "sensitivity": case.sensitivity.value,
                "split": case.split,
                "created_at": created_at,
            },
        )
        case_ids.add(case.case_id)
    if len({case.source_ids[0] for case in case_list}) != len(case_list):
        _raise("case_mismatch", "cases", "source_id")

    result_list, example_list, event_list = list(results), list(examples), list(events)
    if (kind == "run") != bool(result_list) or (kind == "gold") != bool(example_list):
        _raise("invalid_bundle")
    if kind == "review" and not event_list:
        _raise("invalid_bundle")
    for result in result_list:
        if result.case_id not in case_ids or result.variable_set_ref != variables.schema_ref:
            _raise("case_mismatch", "results")
        if producer.run_id is None or result.run_id != producer.run_id:
            _raise("run_mismatch", "results", "run_id")
        tables.graph(
            result.case_id,
            variables,
            result.claims,
            result.mentions,
            result.mention_readings,
            result.evidence,
            result.edges,
        )
        for outcome in result.variable_outcomes.values():
            tables.add("variable_outcomes", _outcome_row(result.case_id, outcome))
        tables.add(
            "results",
            {
                "case_id": result.case_id,
                "run_id": result.run_id,
                "grounding_failures": result.grounding_failures,
                "tool_calls": result.tool_calls,
                "escalated": result.escalated,
                "trace_id": result.trace_id,
                "budget": None if result.budget is None else result.budget.model_dump(mode="json"),
                "usage": None if result.usage is None else result.usage.model_dump(mode="json"),
            },
        )
    if len({example.case_id for example in example_list}) != len(example_list):
        _raise("invalid_bundle", "examples", "case_id")
    for example in example_list:
        if example.case_id not in case_ids or example.variable_set_ref != variables.schema_ref:
            _raise("case_mismatch", "examples")
        tables.graph(
            example.case_id,
            variables,
            example.claims,
            example.mentions,
            example.mention_readings,
            example.evidence,
            example.edges,
        )
        for name, reference in example.references.items():
            tables.add(
                "variable_outcomes",
                _outcome_row(
                    example.case_id,
                    VariableOutcome(
                        variable=name,
                        outcome=reference.outcome,
                        final_claim_ids=list(reference.final_claim_ids),
                    ),
                ),
            )
            tables.add(
                "examples",
                {
                    "example_id": example.example_id,
                    "variable": name,
                    "case_id": example.case_id,
                    "variable_set": example.variable_set,
                    "variable_set_version": example.variable_set_version,
                    "variable_set_ref": example.variable_set_ref,
                    "as_of_event": example.as_of_event,
                    "split": example.split,
                    "tier": reference.tier.value,
                    "outcome_status": reference.outcome.status,
                    "final_claim_ids": list(reference.final_claim_ids),
                    "annotators": list(reference.annotators),
                    "adjudicator": reference.adjudicator,
                    "derived_from_events": list(reference.derived_from_events),
                    "created_at": example.created_at,
                },
            )
    for event in event_list:
        if event.case_id not in case_ids:
            _raise("case_mismatch", "annotation_events")
        tables.add("annotation_events", _event_row(event))
    for span in spans:
        if isinstance(span, Mention):
            tables.add("mentions", _mention_row(span))
        else:
            tables.add("evidence", _evidence_row(span))
    for decision in decisions:
        tables.add("decisions", _decision_row(decision))

    present = set(REQUIRED[kind]) | set(tables.rows)
    rows = {
        name: sorted_rows(name, tables.rows.get(name, {}).values())
        for name in TABLES
        if name in present
    }
    for name, table_rows in rows.items():
        check_rows(name, table_rows)
    manifest = Manifest(
        bundle_kind=kind,
        producer=producer,
        variable_set=ManifestVariableSet(
            name=variables.name, version=variables.version, schema_ref=variables.schema_ref
        ),
        dataset=dataset,
        policy_version=policy_version,
        sources=ManifestSources(count=len(rows["sources"])),
        tables={
            name: ManifestTable(
                rows=len(table_rows), content_sha256=content_sha256(name, table_rows)
            )
            for name, table_rows in rows.items()
        },
        payload_kinds={TABLES[name].file: payload_kind(name, variables) for name in rows},
        created_at=created_at,
    )
    return Bundle(manifest=manifest, tables=MappingProxyType(rows))


# Checking and loading ----------------------------------------------------------------------


def check_bundle(bundle: Bundle, *, variables: VariableSet) -> None:
    """Structure, content hashes, payload kinds, and scope; needs no source text.

    Run before any validation, so a modified table is rejected before its rows are used.
    """
    manifest = bundle.manifest
    present = set(bundle.tables)
    for name in REQUIRED[manifest.bundle_kind]:
        if name not in present:
            _raise("missing_table", name)
    for name in present:
        if name not in TABLES:
            _raise("unexpected_table", name)
    if set(manifest.tables) != present:
        _raise("invalid_manifest")
    for name, rows in bundle.tables.items():
        check_rows(name, rows)
        declared = manifest.tables[name]
        if declared.rows != len(rows) or declared.content_sha256 != content_sha256(name, rows):
            _raise("content_mismatch", name)
    expected_kinds = {TABLES[name].file: payload_kind(name, variables) for name in present}
    if manifest.payload_kinds != expected_kinds:
        _raise("payload_kind_mismatch")
    if (
        manifest.variable_set.name != variables.name
        or manifest.variable_set.version != variables.version
        or manifest.variable_set.schema_ref != variables.schema_ref
    ):
        _raise("variable_set_mismatch")
    if manifest.sources.count != len(bundle.tables["sources"]):
        _raise("invalid_manifest")
    sources = {row["source_id"] for row in bundle.tables["sources"]}
    case_sources: set[str] = set()
    for row in bundle.tables["cases"]:
        if (
            row["dataset"] != manifest.dataset.name
            or row["dataset_manifest_hash"] != manifest.dataset.manifest_hash
            or row["sensitivity"] != manifest.dataset.sensitivity.value
            or row["variable_set"] != variables.name
            or row["variable_set_version"] != variables.version
            or row["variable_set_ref"] != variables.schema_ref
            or row["source_id"] not in sources
            or row["source_id"] in case_sources
        ):
            _raise("case_mismatch", "cases")
        case_sources.add(row["source_id"])
    if manifest.bundle_kind == "run" and any(
        row["run_id"] != manifest.producer.run_id for row in bundle.tables["results"]
    ):
        _raise("run_mismatch", "results", "run_id")


class UnvalidatedRow:
    """A parsed row that amber's load boundaries refuse: it is not a ``Mapping``."""

    __slots__ = ("_row",)

    def __init__(self, row: Row) -> None:
        self._row = dict(row)

    def get(self, column: str, default: Any = None) -> Any:
        return self._row.get(column, default)

    def columns(self) -> tuple[str, ...]:
        return tuple(self._row)


@dataclass(frozen=True)
class UnvalidatedBundle:
    """A structurally checked bundle loaded without a resolver: for ``labels_only`` work only."""

    manifest: Manifest
    tables: Mapping[str, tuple[UnvalidatedRow, ...]]


@dataclass(frozen=True)
class BundleContents:
    """A bundle fully revalidated against authoritative sources and its variable set."""

    manifest: Manifest
    sources: tuple[Source, ...]
    cases: tuple[Case, ...]
    results: tuple[CaseResult, ...]
    examples: tuple[Example, ...]
    events: tuple[AnnotationEvent, ...]
    spans: tuple[Inclusion | Mention, ...]
    decisions: tuple[Decision, ...]


def _resolve(row: Row, resolver: SourceResolver) -> Source:
    source = resolver.resolve(row["source_id"])
    if source is None:
        _raise("source_unavailable", "sources", "source_id")
    if (
        not isinstance(source, Source)
        or source.source_id != row["source_id"]
        or text_hash(source) != row["text_hash"]
    ):
        _raise("source_hash_mismatch", "sources", "source_id")
    expected = _source_row(source)
    if any(expected[key] != row[key] for key in expected):
        _raise("source_hash_mismatch", "sources", "source_id")
    return source


def _case(row: Row) -> Case:
    try:
        return Case(
            case_id=row["case_id"],
            task=row["variable_set"],
            patient_id=row["patient_id"],
            source_ids=[row["source_id"]],
            sensitivity=row["sensitivity"],
            dataset=row["dataset"],
            split=row["split"],
        )
    except ValueError:
        _raise("case_mismatch", "cases")


class _Index:
    """Rows grouped by case, with inferences placed by what they rest on."""

    def __init__(self, bundle: Bundle) -> None:
        tables = bundle.tables
        self.cases = {row["case_id"]: row for row in tables["cases"]}
        case_of_source = {row["source_id"]: row["case_id"] for row in tables["cases"]}
        self.claim_case = {row["claim_id"]: row["case_id"] for row in tables["claims"]}
        evidence = {row["evidence_id"]: row for row in tables["evidence"]}
        self.evidence_case: dict[str, str] = {}
        for evidence_id, row in evidence.items():
            if row["source_id"] is not None and row["source_id"] in case_of_source:
                self.evidence_case[evidence_id] = case_of_source[row["source_id"]]
        changed = True
        while changed:
            changed = False
            for evidence_id, row in evidence.items():
                if evidence_id in self.evidence_case or not isinstance(row["inputs"], list):
                    continue
                for item in row["inputs"]:
                    case_id = self.claim_case.get(item) or self.evidence_case.get(item)
                    if case_id is not None:
                        self.evidence_case[evidence_id] = case_id
                        changed = True
                        break
        if set(evidence) != set(self.evidence_case):
            _raise("orphan_evidence", "evidence", "evidence_id")
        self.case_of_source = case_of_source
        self.tables = tables

    def graph(self, case_id: str) -> Row:
        source_id = self.cases[case_id]["source_id"]
        tables = self.tables
        return {
            "claims": [
                _claim_payload(_plain(row, "claims"))
                for row in tables["claims"]
                if row["case_id"] == case_id
            ],
            "mentions": [
                _mention_payload(_plain(row, "mentions"))
                for row in tables["mentions"]
                if row["source_id"] == source_id
            ],
            "mention_readings": [
                _reading_payload(_plain(row, "mention_readings"))
                for row in tables["mention_readings"]
                if row["case_id"] == case_id
            ],
            "evidence": [
                _evidence_payload(_plain(row, "evidence"))
                for row in tables["evidence"]
                if self.evidence_case[row["evidence_id"]] == case_id
            ],
            "edges": [
                _plain(row, "evidence_edges")
                for row in tables["evidence_edges"]
                if self.claim_case.get(row["claim_id"]) == case_id
            ],
        }

    def outcomes(self, case_id: str) -> dict[str, Row]:
        return {
            row["variable"]: _outcome_payload(_plain(row, "variable_outcomes"))
            for row in self.tables["variable_outcomes"]
            if row["case_id"] == case_id
        }


def _check_claim_rows(bundle: Bundle, variables: VariableSet) -> None:
    for row in bundle.tables["claims"]:
        if (
            row["variable_set"] != variables.name
            or row["variable_set_version"] != variables.version
            or row["case_id"] not in {case["case_id"] for case in bundle.tables["cases"]}
        ):
            _raise("case_mismatch", "claims")


def load_bundle(
    bundle: Bundle, *, variables: VariableSet, resolver: SourceResolver | None
) -> BundleContents | UnvalidatedBundle:
    """Check a bundle and, with a resolver, revalidate every record (contract v0.1 5.6).

    Without a resolver the result is an ``UnvalidatedBundle`` whose rows amber's graph,
    case, and example boundaries refuse.
    """
    check_bundle(bundle, variables=variables)
    if resolver is None:
        return UnvalidatedBundle(
            manifest=bundle.manifest,
            tables=MappingProxyType(
                {
                    name: tuple(UnvalidatedRow(row) for row in rows)
                    for name, rows in bundle.tables.items()
                }
            ),
        )
    _check_claim_rows(bundle, variables)
    sources = {row["source_id"]: _resolve(row, resolver) for row in bundle.tables["sources"]}
    cases = {row["case_id"]: _case(row) for row in bundle.tables["cases"]}
    index = _Index(bundle)
    finals = sorted(name for name, variable in variables.variables.items() if variable.final)
    manifest = bundle.manifest

    results: list[CaseResult] = []
    for row in bundle.tables.get("results", ()):
        case = cases.get(row["case_id"])
        if case is None:
            _raise("case_mismatch", "results")
        outcomes = index.outcomes(case.case_id)
        single = outcomes.get(finals[0]) if len(finals) == 1 else None
        payload = {
            "schema_version": manifest.schema_version,
            "case_id": case.case_id,
            "run_id": row["run_id"],
            "variable_set": variables.name,
            "variable_set_version": variables.version,
            "variable_set_ref": variables.schema_ref,
            **index.graph(case.case_id),
            "variable_outcomes": outcomes,
            "outcome": None if single is None else single["outcome"],
            "final_claim_ids": None if single is None else single["final_claim_ids"],
            "grounding_failures": row["grounding_failures"],
            "tool_calls": row["tool_calls"],
            "escalated": row["escalated"],
            "trace_id": row["trace_id"],
            "budget": row["budget"],
            "usage": row["usage"],
        }
        results.append(
            load_case_result(
                payload, case=case, source=sources[case.source_ids[0]], variables=variables
            )
        )
    if manifest.bundle_kind == "run" and {result.case_id for result in results} != set(cases):
        _raise("invalid_bundle", "results", "case_id")

    try:
        events = tuple(
            AnnotationEvent.model_validate(_event_payload(_plain(row, "annotation_events")))
            for row in bundle.tables.get("annotation_events", ())
        )
    except ValidationError:
        _raise("invalid_bundle", "annotation_events")
    spans = _spans(bundle, sources)

    examples: list[Example] = []
    example_rows: dict[str, list[Row]] = {}
    for row in bundle.tables.get("examples", ()):
        example_rows.setdefault(row["example_id"], []).append(row)
    for example_id, rows in example_rows.items():
        first = rows[0]
        case = cases.get(first["case_id"])
        if case is None or any(row["case_id"] != case.case_id for row in rows):
            _raise("case_mismatch", "examples")
        outcomes = index.outcomes(case.case_id)
        references = {}
        for row in rows:
            outcome = outcomes.get(row["variable"])
            if outcome is None or outcome["outcome"]["status"] != row["outcome_status"]:
                _raise("case_mismatch", "examples", "outcome_status")
            references[row["variable"]] = {
                "variable": row["variable"],
                "tier": row["tier"],
                "outcome": outcome["outcome"],
                "final_claim_ids": row["final_claim_ids"],
                "annotators": row["annotators"],
                "adjudicator": row["adjudicator"],
                "derived_from_events": row["derived_from_events"],
            }
        payload = {
            "example_id": example_id,
            "schema_version": manifest.schema_version,
            "case_id": case.case_id,
            "variable_set": first["variable_set"],
            "variable_set_version": first["variable_set_version"],
            "variable_set_ref": first["variable_set_ref"],
            "as_of_event": first["as_of_event"],
            "source_ids": list(case.source_ids),
            "split": first["split"],
            "references": references,
            **index.graph(case.case_id),
            "created_at": first["created_at"],
        }
        example = load_example(
            payload, case=case, source=sources[case.source_ids[0]], variables=variables
        )
        case_events = [event for event in events if event.case_id == case.case_id]
        if case_events:
            verify_example(
                example,
                case_events,
                case=case,
                source=sources[case.source_ids[0]],
                variables=variables,
                spans=spans,
            )
        examples.append(example)
    if manifest.bundle_kind == "review":
        for case in cases.values():
            case_events = [event for event in events if event.case_id == case.case_id]
            if case_events:
                replay(
                    case_events,
                    case=case,
                    source=sources[case.source_ids[0]],
                    variables=variables,
                    spans=spans,
                )

    try:
        decisions = tuple(
            Decision.model_validate(
                {**_plain(row, "decisions"), "schema_version": manifest.schema_version}
            )
            for row in bundle.tables.get("decisions", ())
        )
    except ValidationError:
        _raise("invalid_bundle", "decisions")
    return BundleContents(
        manifest=manifest,
        sources=tuple(sources.values()),
        cases=tuple(cases.values()),
        results=tuple(results),
        examples=tuple(examples),
        events=events,
        spans=spans,
        decisions=decisions,
    )


def _spans(bundle: Bundle, sources: Mapping[str, Source]) -> tuple[Inclusion | Mention, ...]:
    """Re-mint every mention and inclusion row against its authoritative source."""
    spans: list[Inclusion | Mention] = []
    for row in bundle.tables["mentions"]:
        source = sources.get(row["source_id"])
        fragments = [tuple(item) for item in (row["fragments"] or [])]
        minted = (
            None
            if source is None
            else exact_mention(
                source, row["quote"], row["start"], row["mention_type"], fragments=fragments
            )
        )
        if not isinstance(minted, Mention) or _mention_row(minted) != _plain(row, "mentions"):
            _raise("source_hash_mismatch", "mentions", "mention_id")
        spans.append(minted)
    for row in bundle.tables["evidence"]:
        if row["kind"] != "inclusion":
            continue
        source = sources.get(row["source_id"])
        quoted = None if source is None else exact_quote(source, row["quote"], row["start"])
        if not isinstance(quoted, Inclusion) or quoted.evidence_id != row["evidence_id"]:
            _raise("source_hash_mismatch", "evidence", "evidence_id")
        spans.append(quoted)
    return tuple(spans)
