"""``AmberClaimsAdapter``: loupe's ``AnnotationAdapter`` for amber claim and outcome review.

loupe loads it by import path from a frozen source configuration
(``"adapter": "amber.loupe:AmberClaimsAdapter"``); amber never imports loupe. The adapter turns
a reviewer's claim-mode request into a ``CaseResult`` built through amber's own tools
(``exact_quote``, ``commit_claim``, ``no_claim``, ``create_case_result``) and checks a stored
result by rebuilding it, so loupe never holds an ungrounded span or an invalid outcome.

Configuration (``project_configuration`` in the loupe source):

- ``variable_set``: ``"module:attribute"`` naming the ``VariableSet`` (trusted operator code);
  ``variable_set_ref`` pins its ``schema_ref``;
- ``dataset``, ``dataset_manifest_hash``, ``sensitivity``, ``policy_version``, and
  ``destination`` (the review service's destination name) and ``zone`` for provenance.

Each loupe record is one note: ``record_id`` must be the amber ``source_id`` of
``Source(patient_id=metadata.patient_id, kind=note, external_id=metadata.external_id, text)``;
``metadata.case_id`` (default: the record ID) and ``metadata.split`` complete the ``Case``.

Request (one entry per final variable)::

    {"reviewer": "...", "whole_note_complete": true,
     "variables": {name: {"status": "answered" | "not_mentioned" | "insufficient_evidence"
                                     | "conflicting_evidence",
                          "reason": "...",               # required unless answered
                          "claims": [{"value": {...}, "rationale": "...",
                                      "evidence": [{"start": 0, "end": 4, "quote": "..."}],
                                      "fields": {"<answer field>": [<evidence index>, ...]}}],
                          "evidence": [{"start", "end", "quote"}]}}}   # conflicting only

Offsets are Unicode code points into the exact note text and every quote must equal the text
at its offsets. Only note-level variables are supported; ``failed`` is never a review outcome.

IDs are minted from a seed over the record, configuration, and normalized request, and
provenance carries a fixed time, so the same request always builds the same result (loupe
rebuilds and compares at publication). Errors are ``AdapterError`` with a source-free code.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
from datetime import UTC, datetime
from typing import Any, NoReturn

import amber
from amber.cases import CaseValidationError, create_case_result, load_case_result
from amber.examples import ExampleValidationError
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.grounding import exact_quote
from amber.ids import canonical_json, canonical_sha256, parse_canonical_json, seeded_ulids
from amber.schemas import (
    Case,
    CaseOutcome,
    Inclusion,
    Provenance,
    Sensitivity,
    Source,
    SourceKind,
    VariableOutcome,
    VariableSet,
    Zone,
)
from amber.tools.outcomes import no_claim

SCHEMA_VERSION = "amber-loupe-claims-v1"
ADAPTER_VERSION = "1"
# Review time is recorded by loupe; provenance time is fixed so rebuilds are byte-identical.
PROVENANCE_TIME = datetime(1970, 1, 1, tzinfo=UTC)
_STATUSES = ("answered", "not_mentioned", "insufficient_evidence", "conflicting_evidence")
_MAX_CLAIMS = 50
_MAX_EVIDENCE = 50


class AdapterError(ValueError):
    """A request, record, or configuration loupe must refuse; the message is the code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise AdapterError(code)


def _text(value: Any, code: str, *, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail(code)
    return str(value)


def _load_variables(path: Any) -> VariableSet:
    if not isinstance(path, str) or path.count(":") != 1:
        _fail("invalid_configuration")
    module, attribute = path.split(":")
    try:
        variables = getattr(importlib.import_module(module), attribute)
    except (ImportError, AttributeError):
        _fail("variable_set_unavailable")
    if not isinstance(variables, VariableSet):
        _fail("invalid_configuration")
    return variables  # type: ignore[no-any-return]


class AmberClaimsAdapter:
    """loupe ``AnnotationAdapter`` for note-level amber variables (claim and outcome mode)."""

    def __init__(self, configuration: dict[str, Any]) -> None:
        project = configuration.get("project_configuration")
        if not isinstance(project, dict):
            _fail("invalid_configuration")
        self.variables = _load_variables(project.get("variable_set"))
        if project.get("variable_set_ref") != self.variables.schema_ref:
            _fail("variable_set_mismatch")
        if any(variable.level != "note" for variable in self.variables.variables.values()):
            _fail("unsupported_variable_level")
        try:
            self.sensitivity = Sensitivity(str(project.get("sensitivity")))
            self.zone = Zone(str(project.get("zone")))
        except ValueError:
            _fail("invalid_configuration")
        self.dataset = _text(project.get("dataset"), "invalid_configuration", limit=200)
        self.manifest_hash = _text(
            project.get("dataset_manifest_hash"), "invalid_configuration", limit=200
        )
        self.policy_version = _text(project.get("policy_version"), "invalid_configuration")
        self.destination = _text(project.get("destination"), "invalid_configuration", limit=200)
        self.finals = sorted(name for name, v in self.variables.variables.items() if v.final)
        # loupe's mention-mode fields, kept so its current screens still load.
        self.axes = copy.deepcopy(configuration.get("axes") or {})
        self.inventory: dict[str, Any] = {
            "concepts": copy.deepcopy(configuration.get("concepts") or []),
            "mode": "claims",
            "variables": [
                {
                    "name": name,
                    "final": variable.final,
                    "instructions": variable.instructions,
                    "evidence_policy": variable.evidence_policy,
                    "fields": sorted(variable.answer_model.model_fields),
                    "json_schema": variable.answer_model.model_json_schema(),
                }
                for name, variable in sorted(self.variables.variables.items())
            ],
        }

    @property
    def attribute_values(self) -> dict[str, Any]:
        return {axis: spec.get("values", []) for axis, spec in self.axes.items()}

    @property
    def default_values(self) -> dict[str, str]:
        return {axis: spec.get("default", "") for axis, spec in self.axes.items()}

    def binding(self) -> dict[str, str]:
        """Content hashes of what validation depends on beyond this class's source."""
        return {
            "amber_version": amber.__version__,
            "adapter_version": ADAPTER_VERSION,
            "variable_set_ref": self.variables.schema_ref,
            "dataset_manifest_hash": self.manifest_hash,
        }

    # records ---------------------------------------------------------------------------------

    def source_and_case(self, example: dict[str, Any]) -> tuple[Source, Case]:
        """The amber note source and case a loupe example stands for."""
        record = example.get("source_record") or {}
        metadata = record.get("metadata") if isinstance(record, dict) else None
        text = example.get("source_text")
        if not isinstance(metadata, dict) or not isinstance(text, str):
            _fail("invalid_record")
        try:
            source = Source.create(
                patient_id=metadata.get("patient_id"),  # type: ignore[arg-type]
                kind=SourceKind.note,
                external_id=metadata.get("external_id"),
                datetime=None,
                text=text,
                record=None,
            )
            case = Case(
                case_id=metadata.get("case_id", example.get("example_id")),
                task=self.variables.name,
                patient_id=source.patient_id,
                source_ids=[source.source_id],
                sensitivity=self.sensitivity,
                dataset=self.dataset,
                split=metadata.get("split"),
            )
        except (TypeError, ValueError):
            _fail("invalid_record")
        if example.get("example_id") != source.source_id:
            _fail("source_identity_mismatch")
        return source, case

    # requests --------------------------------------------------------------------------------

    def _normalize(self, request: Any) -> tuple[str, dict[str, Any]]:
        if not isinstance(request, dict) or request.get("whole_note_complete") is not True:
            _fail("whole_note_review_required")
        reviewer = _text(request.get("reviewer"), "reviewer_required", limit=200)
        variables = request.get("variables")
        if not isinstance(variables, dict) or sorted(variables) != self.finals:
            _fail("every_final_variable_required")
        normalized: dict[str, Any] = {}
        for name in self.finals:
            entry = variables[name]
            if not isinstance(entry, dict) or entry.get("status") not in _STATUSES:
                _fail("invalid_outcome")
            status = entry["status"]
            claims = entry.get("claims", [])
            evidence = entry.get("evidence", [])
            if not isinstance(claims, list) or len(claims) > _MAX_CLAIMS:
                _fail("invalid_claims")
            if (status == "answered") != bool(claims):
                _fail("invalid_outcome")
            if not isinstance(evidence, list) or len(evidence) > _MAX_EVIDENCE:
                _fail("invalid_evidence")
            if (status == "conflicting_evidence") != bool(evidence):
                _fail("invalid_outcome")
            item: dict[str, Any] = {"status": status}
            if status != "answered":
                item["reason"] = _text(entry.get("reason"), "reason_required")
            item["claims"] = [self._normalize_claim(claim) for claim in claims]
            item["evidence"] = [self._span(span) for span in evidence]
            normalized[name] = item
        return reviewer, normalized

    @staticmethod
    def _span(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"start", "end", "quote"}:
            _fail("invalid_span")
        start, end, quote = value["start"], value["end"], value["quote"]
        if type(start) is not int or type(end) is not int or not isinstance(quote, str):
            _fail("invalid_span")
        return {"start": start, "end": end, "quote": quote}

    def _normalize_claim(self, claim: Any) -> dict[str, Any]:
        if not isinstance(claim, dict) or set(claim) - {"value", "rationale", "evidence", "fields"}:
            _fail("invalid_claim")
        value = claim.get("value")
        evidence = claim.get("evidence")
        fields = claim.get("fields", {})
        if not isinstance(value, dict) or not isinstance(evidence, list) or not evidence:
            _fail("claim_evidence_required")
        if len(evidence) > _MAX_EVIDENCE or not isinstance(fields, dict):
            _fail("invalid_claim")
        spans = [self._span(span) for span in evidence]
        roles: dict[str, list[int]] = {}
        for field, indexes in sorted(fields.items()):
            if (
                not isinstance(field, str)
                or not isinstance(indexes, list)
                or not indexes
                or any(type(i) is not int or not 0 <= i < len(spans) for i in indexes)
                or len(set(indexes)) != len(indexes)
            ):
                _fail("invalid_field_evidence")
            roles[field] = sorted(indexes)
        return {
            "value": parse_canonical_json(canonical_json(value)),
            "rationale": _text(claim.get("rationale"), "rationale_required"),
            "evidence": spans,
            "fields": roles,
        }

    # building --------------------------------------------------------------------------------

    def build_result(
        self,
        request: dict[str, Any],
        example: dict[str, Any],
        inventory: dict[str, Any],
        configuration_sha256: str,
    ) -> dict[str, Any]:
        source, case = self.source_and_case(example)
        reviewer, normalized = self._normalize(request)
        producer = "human:" + re.sub(r"\s+", "-", reviewer.strip())
        seed = canonical_sha256(
            {
                "record_id": source.source_id,
                "configuration_sha256": configuration_sha256,
                "reviewer": reviewer,
                "variables": normalized,
            }
        )
        ids = seeded_ulids(seed)
        run_id = ids()
        try:
            provenance = Provenance(
                producer=producer,
                run_id=run_id,
                destination=self.destination,
                policy_version=self.policy_version,
                zone=self.zone,
                sensitivity=self.sensitivity,
                created_at=PROVENANCE_TIME,
                version=ADAPTER_VERSION,
            )
        except ValueError:
            _fail("reviewer_required")
        graph = EvidenceGraph(
            source=source, variables=self.variables, sensitivity=self.sensitivity, ids=ids
        )
        outcomes: dict[str, VariableOutcome] = {}
        issues: list[dict[str, str]] = []
        try:
            for name in self.finals:
                outcomes[name] = self._variable(
                    graph, case, source, name, normalized[name], provenance
                )
                if normalized[name]["status"] == "insufficient_evidence":
                    issues.append({"code": "insufficient_evidence", "variable": name})
            result = create_case_result(
                case, graph=graph, run_id=run_id, variable_outcomes=outcomes
            )
        except (GraphValidationError, CaseValidationError, ExampleValidationError) as error:
            _fail(error.code)
        return {
            "schema_version": SCHEMA_VERSION,
            "record_id": source.source_id,
            "source_sha256": hashlib.sha256(source.text.encode("utf-8")).hexdigest(),  # type: ignore[union-attr]
            "source_length": len(source.text or ""),
            "processing_state": "partial" if issues else "ok",
            "issues": issues,
            "mentions": [{"variable": name, **normalized[name]} for name in self.finals],
            "case_result": json.loads(result.model_dump_json()),
            "producer": {
                "method": "human",
                "reviewer": reviewer,
                "configuration_sha256": configuration_sha256,
            },
        }

    def _inclusion(self, graph: EvidenceGraph, source: Source, span: dict[str, Any]) -> str:
        text = source.text or ""
        if not 0 <= span["start"] < span["end"] <= len(text):
            _fail("invalid_span")
        if text[span["start"] : span["end"]] != span["quote"]:
            _fail("quote_mismatch")
        quoted = exact_quote(source, span["quote"], span["start"])
        if not isinstance(quoted, Inclusion) or quoted.end != span["end"]:
            _fail("quote_mismatch")
        return graph.register_evidence(quoted).evidence_id

    def _variable(
        self,
        graph: EvidenceGraph,
        case: Case,
        source: Source,
        name: str,
        entry: dict[str, Any],
        provenance: Provenance,
    ) -> VariableOutcome:
        status = entry["status"]
        if status != "answered":
            cited = [self._inclusion(graph, source, span) for span in entry["evidence"]]
            outcome = no_claim(
                status,
                entry["reason"],
                [source.source_id],
                cited,
                case=case,
                graph=graph,
                provenance=provenance,
            )
            return VariableOutcome(variable=name, outcome=outcome)
        final_ids = []
        for claim in entry["claims"]:
            spans = [self._inclusion(graph, source, span) for span in claim["evidence"]]
            committed = graph.commit_claim(
                name,
                claim["value"],
                list(dict.fromkeys(spans)),
                claim["rationale"],
                {field: [spans[i] for i in indexes] for field, indexes in claim["fields"].items()}
                or None,
                provenance=provenance,
            )
            final_ids.append(committed.claim_id)
        return VariableOutcome(
            variable=name,
            outcome=CaseOutcome(status="answered", provenance=provenance),
            final_claim_ids=final_ids,
        )

    # loupe hooks -----------------------------------------------------------------------------

    def editable(self, mentions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Stored items are already in editor form (one entry per final variable)."""
        return copy.deepcopy(mentions)

    def request_from(self, result: dict[str, Any]) -> dict[str, Any]:
        """The request a stored result was built from."""
        items = result.get("mentions")
        producer = result.get("producer")
        if not isinstance(items, list) or not isinstance(producer, dict):
            _fail("invalid_result")
        variables = {}
        for item in items:
            if not isinstance(item, dict):
                _fail("invalid_result")
            entry = {key: value for key, value in item.items() if key != "variable"}
            variables[item.get("variable")] = entry
        return {
            "whole_note_complete": True,
            "reviewer": producer.get("reviewer"),
            "variables": variables,
        }

    def validate_result(self, result: dict[str, Any], example: dict[str, Any]) -> dict[str, Any]:
        """Accept a stored result only if it is exactly what its request builds and its
        ``CaseResult`` loads at amber's boundary."""
        if not isinstance(result, dict) or result.get("schema_version") != SCHEMA_VERSION:
            _fail("invalid_result")
        if result.get("record_id") != example.get("example_id"):
            _fail("source_identity_mismatch")
        producer = result.get("producer")
        if not isinstance(producer, dict):
            _fail("invalid_result")
        expected = self.build_result(
            self.request_from(result), example, self.inventory, producer["configuration_sha256"]
        )
        if expected != result:
            _fail("result_mismatch")
        source, case = self.source_and_case(example)
        try:
            load_case_result(
                result["case_result"], case=case, source=source, variables=self.variables
            )
        except (GraphValidationError, CaseValidationError) as error:
            _fail(error.code)
        return result


__all__ = ["ADAPTER_VERSION", "SCHEMA_VERSION", "AdapterError", "AmberClaimsAdapter"]
