"""Bundle table schemas (contract v0.1 section 5.4) and their payload kinds (section 5.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from amber.labels import is_closed_schema
from amber.policy import PayloadKind
from amber.schemas.variables import VariableSet

ColumnType: TypeAlias = Literal["string", "int64", "float64", "bool", "timestamp", "json"]
BundleKind: TypeAlias = Literal["run", "review", "gold", "labels"]


@dataclass(frozen=True)
class Column:
    name: str
    type: ColumnType
    nullable: bool = False


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]
    # None: decided by the variable set's answer models (claims only).
    payload_kind: PayloadKind | None

    @property
    def file(self) -> str:
        return f"{self.name}.parquet"

    def column(self, name: str) -> Column:
        return next(column for column in self.columns if column.name == name)


def _c(name: str, kind: ColumnType = "string", nullable: bool = False) -> Column:
    return Column(name, kind, nullable)


def _provenance(nullable: bool = False) -> tuple[Column, ...]:
    return (
        _c("provenance_producer", nullable=nullable),
        _c("provenance_model", nullable=True),
        _c("provenance_prompt_versions", "json", nullable),
        _c("provenance_run_id", nullable=True),
        _c("provenance_trace_id", nullable=True),
        _c("provenance_zone", nullable=nullable),
        _c("provenance_sensitivity", nullable=nullable),
        _c("provenance_destination", nullable=nullable),
        _c("provenance_policy_version", nullable=nullable),
        _c("provenance_restriction_ids", "json", nullable),
        _c("provenance_created_at", "timestamp", nullable),
        _c("provenance_version", nullable=nullable),
    )


TABLES: dict[str, TableSchema] = {
    schema.name: schema
    for schema in (
        TableSchema(
            "sources",
            (
                _c("source_id"),
                _c("patient_id"),
                _c("kind"),
                _c("external_id", nullable=True),
                _c("datetime", "timestamp", True),
                _c("text_hash"),
                _c("record", "json", True),
                _c("sections", "json"),
                _c("meta", "json"),
            ),
            ("source_id",),
            "derived_text",
        ),
        TableSchema(
            "mentions",
            (
                _c("mention_id"),
                _c("source_id"),
                _c("start", "int64"),
                _c("end", "int64"),
                _c("quote"),
                _c("mention_type"),
                _c("fragments", "json", True),
                _c("section_category", nullable=True),
            ),
            ("mention_id",),
            "derived_text",
        ),
        TableSchema(
            "mention_readings",
            (
                _c("reading_id"),
                _c("mention_id"),
                _c("case_id"),
                _c("label", nullable=True),
                _c("value", "json", True),
                _c("unit", nullable=True),
                _c("attributes", "json"),
                _c("concept", "json", True),
                _c("inputs", "json", True),
                _c("status"),
                *_provenance(),
            ),
            ("reading_id",),
            "derived_text",
        ),
        TableSchema(
            "claims",
            (
                _c("claim_id"),
                _c("case_id"),
                _c("source_id"),
                _c("patient_id"),
                _c("variable"),
                _c("variable_set"),
                _c("variable_set_version"),
                _c("schema_ref"),
                _c("reading_id", nullable=True),
                _c("value", "json"),
                _c("effective_datetime", "timestamp", True),
                _c("confidence", "float64", True),
                _c("status"),
                *_provenance(),
            ),
            ("claim_id",),
            None,
        ),
        TableSchema(
            "evidence",
            (
                _c("evidence_id"),
                _c("kind"),
                _c("source_id", nullable=True),
                _c("start", "int64", True),
                _c("end", "int64", True),
                _c("quote", nullable=True),
                _c("alignment", nullable=True),
                _c("alignment_score", "float64", True),
                _c("field", nullable=True),
                _c("value", "json", True),
                _c("datetime", "timestamp", True),
                _c("rationale", nullable=True),
                _c("inputs", "json", True),
                _c("derivation", "json", True),
                _c("trace_id", nullable=True),
                _c("span_id", nullable=True),
                *_provenance(nullable=True),
            ),
            ("evidence_id",),
            "derived_text",
        ),
        TableSchema(
            "evidence_edges",
            (
                _c("claim_id"),
                _c("evidence_id"),
                _c("role", nullable=True),
                _c("weight", "float64", True),
            ),
            ("claim_id", "evidence_id", "role"),
            "labels_only",
        ),
        TableSchema(
            "cases",
            (
                _c("case_id"),
                _c("dataset"),
                _c("dataset_manifest_hash"),
                _c("source_id"),
                _c("patient_id"),
                _c("variable_set"),
                _c("variable_set_version"),
                _c("variable_set_ref"),
                _c("sensitivity"),
                _c("split", nullable=True),
                _c("created_at", "timestamp"),
            ),
            ("case_id",),
            "labels_only",
        ),
        TableSchema(
            "variable_outcomes",
            (
                _c("case_id"),
                _c("variable"),
                _c("status"),
                _c("reason", nullable=True),
                _c("failure_kind", nullable=True),
                _c("reviewed_source_ids", "json"),
                _c("evidence_ids", "json"),
                _c("final_claim_ids", "json"),
                *_provenance(),
            ),
            ("case_id", "variable"),
            "derived_text",
        ),
        TableSchema(
            "results",
            (
                _c("case_id"),
                _c("run_id"),
                _c("grounding_failures", "int64"),
                _c("tool_calls", "int64"),
                _c("escalated", "bool"),
                _c("trace_id", nullable=True),
                _c("budget", "json", True),
                _c("usage", "json", True),
            ),
            ("case_id", "run_id"),
            "labels_only",
        ),
        TableSchema(
            "annotation_events",
            (
                _c("event_id"),
                _c("case_id"),
                _c("variable", nullable=True),
                _c("actor"),
                _c("role"),
                _c("round", "int64"),
                _c("stream", nullable=True),
                _c("action"),
                _c("target_id", nullable=True),
                _c("before", "json", True),
                _c("after", "json", True),
                _c("reason", nullable=True),
                _c("at", "timestamp"),
                _c("external_ref", nullable=True),
                *_provenance(),
            ),
            ("event_id",),
            "derived_text",
        ),
        TableSchema(
            "examples",
            (
                _c("example_id"),
                _c("variable"),
                _c("case_id"),
                _c("variable_set"),
                _c("variable_set_version"),
                _c("variable_set_ref"),
                _c("as_of_event"),
                _c("split", nullable=True),
                _c("tier"),
                _c("outcome_status"),
                _c("final_claim_ids", "json"),
                _c("annotators", "json"),
                _c("adjudicator", nullable=True),
                _c("derived_from_events", "json"),
                _c("created_at", "timestamp"),
            ),
            ("example_id", "variable"),
            "labels_only",
        ),
        TableSchema(
            "label_records",
            (
                _c("case_id"),
                _c("variable"),
                _c("variable_set"),
                _c("variable_set_version"),
                _c("variable_set_ref"),
                _c("tier"),
                _c("outcome_status"),
                _c("value", "json", True),
                _c("producer"),
                _c("split", nullable=True),
            ),
            ("case_id", "variable"),
            None,
        ),
        TableSchema(
            "decisions",
            (
                _c("decision_id"),
                _c("checked_at", "timestamp"),
                _c("permitted", "bool"),
                _c("reason"),
                _c("restriction_id", nullable=True),
                _c("policy_version"),
                _c("destination"),
                _c("destination_kind"),
                _c("zone"),
                _c("sensitivity"),
                _c("payload"),
                _c("dataset", nullable=True),
                _c("run_id", nullable=True),
                _c("case_id", nullable=True),
            ),
            ("decision_id",),
            "labels_only",
        ),
    )
}

ALWAYS_REQUIRED = (
    "sources",
    "mentions",
    "mention_readings",
    "claims",
    "evidence",
    "evidence_edges",
    "cases",
    "variable_outcomes",
)
REQUIRED: dict[BundleKind, tuple[str, ...]] = {
    "run": (*ALWAYS_REQUIRED, "results"),
    "review": (*ALWAYS_REQUIRED, "annotation_events"),
    "gold": (*ALWAYS_REQUIRED, "examples"),
    # amber-labels/0.1: only files that are labels_only by construction (section 6.2).
    "labels": ("cases", "label_records"),
}
LABELS_TABLES = frozenset(
    {"cases", "results", "evidence_edges", "examples", "decisions", "claims", "label_records"}
)


def payload_kind(table: str, variables: VariableSet) -> PayloadKind:
    """A file's payload kind from its schema; ``claims`` and ``label_records`` follow the
    answer models, since their values are answers."""
    declared = TABLES[table].payload_kind
    if declared is not None:
        return declared
    closed = all(
        is_closed_schema(variable.answer_model.model_json_schema())
        for variable in variables.variables.values()
    )
    return "labels_only" if closed else "derived_text"
