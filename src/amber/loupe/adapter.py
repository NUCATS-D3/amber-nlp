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
     "mentions": [{"key": "m1", "start": 6, "end": 10, "quote": "mass",
                   "mention_type": "finding", "label": "...", "value": ...,   # optional
                   "attributes": {"negation": {"value": true,
                                               "cue": {"start", "end", "quote"},      # optional
                                               "scope": {"start", "end", "quote"}}}}], # optional
     "variables": {name: {"status": "answered" | "not_mentioned" | "insufficient_evidence"
                                     | "conflicting_evidence",
                          "reason": "...",               # required unless answered
                          "claims": [{"value": {...}, "rationale": "...",
                                      "mention": "m1",   # mention-level variables only
                                      "evidence": [{"start": 0, "end": 4, "quote": "..."}],
                                      "fields": {"<answer field>": [<evidence index>, ...]}}],
                          "evidence": [{"start", "end", "quote"}]}}}   # conflicting only

Offsets are Unicode code points into the exact note text and every quote must equal the text
at its offsets. Note- and mention-level variables are supported (not patient-level); ``failed``
is never a review outcome, and mention-level variables are never ``conflicting_evidence``.

Each request mention becomes a ``Mention`` (through amber's mention grounding) with one reading
by the reviewer carrying its label, value, and attributes; cue and scope spans are grounded as
inclusions. A mention-level claim relies on its mention's reading and cites the mention's
position and every attribute cue (as ``attribute:<category>``) besides its own ``evidence``,
which may then be empty.

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
from amber.grounding import cite_mention, exact_mention, exact_quote
from amber.ids import canonical_json, canonical_sha256, parse_canonical_json, seeded_ulids
from amber.schemas import (
    Attribute,
    Case,
    CaseOutcome,
    Inclusion,
    Mention,
    MentionReading,
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
_MAX_MENTIONS = 500
_MENTION_KEYS = {"key", "start", "end", "quote", "mention_type", "label", "value", "attributes"}


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
    """loupe ``AnnotationAdapter`` for note- and mention-level amber variables (claim and
    outcome mode)."""

    def __init__(self, configuration: dict[str, Any]) -> None:
        project = configuration.get("project_configuration")
        if not isinstance(project, dict):
            _fail("invalid_configuration")
        self.variables = _load_variables(project.get("variable_set"))
        if project.get("variable_set_ref") != self.variables.schema_ref:
            _fail("variable_set_mismatch")
        if any(variable.level == "patient" for variable in self.variables.variables.values()):
            _fail("unsupported_variable_level")
        self.mention_types = frozenset(
            kind
            for variable in self.variables.variables.values()
            for kind in variable.mention_types
        )
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
                    "level": variable.level,
                    "mention_types": sorted(variable.mention_types),
                    "requires_attributes": [
                        {
                            "category": item.category,
                            "values": None if item.values is None else list(item.values),
                            "evidence": item.evidence,
                        }
                        for item in variable.requires_attributes
                    ],
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

    def _normalize(self, request: Any) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        if not isinstance(request, dict) or request.get("whole_note_complete") is not True:
            _fail("whole_note_review_required")
        reviewer = _text(request.get("reviewer"), "reviewer_required", limit=200)
        raw_mentions = request.get("mentions", [])
        if not isinstance(raw_mentions, list) or len(raw_mentions) > _MAX_MENTIONS:
            _fail("invalid_mention")
        mentions = [self._normalize_mention(item) for item in raw_mentions]
        keys = [item["key"] for item in mentions]
        if len(set(keys)) != len(keys):
            _fail("duplicate_mention_key")
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
            mention_level = self.variables.variables[name].level == "mention"
            item["claims"] = [
                self._normalize_claim(claim, set(keys) if mention_level else None)
                for claim in claims
            ]
            item["evidence"] = [self._span(span) for span in evidence]
            normalized[name] = item
        return reviewer, mentions, normalized

    def _normalize_mention(self, value: Any) -> dict[str, Any]:
        if (
            not isinstance(value, dict)
            or not {"key", "mention_type"} <= set(value) <= _MENTION_KEYS
        ):
            _fail("invalid_mention")
        span = self._span({key: value.get(key) for key in ("start", "end", "quote")})
        if value["mention_type"] not in self.mention_types:
            _fail("invalid_mention_type")
        item: dict[str, Any] = {
            "key": _text(value["key"], "invalid_mention", limit=100),
            **span,
            "mention_type": value["mention_type"],
        }
        if "label" in value:
            item["label"] = _text(value["label"], "invalid_mention", limit=200)
        if "value" in value:
            reading_value = value["value"]
            if isinstance(reading_value, bool) or not isinstance(reading_value, str | int | float):
                _fail("invalid_mention")
            item["value"] = parse_canonical_json(canonical_json(reading_value))
        attributes = value.get("attributes", {})
        if not isinstance(attributes, dict):
            _fail("invalid_attribute")
        item["attributes"] = {}
        for category, attribute in sorted(attributes.items()):
            if (
                not isinstance(category, str)
                or not isinstance(attribute, dict)
                or "value" not in attribute
                or set(attribute) - {"value", "cue", "scope"}
                or not isinstance(attribute["value"], bool | int | str)
            ):
                _fail("invalid_attribute")
            normalized: dict[str, Any] = {"value": attribute["value"]}
            for role in ("cue", "scope"):
                if role in attribute:
                    normalized[role] = self._span(attribute[role])
            item["attributes"][category] = normalized
        return item

    @staticmethod
    def _span(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"start", "end", "quote"}:
            _fail("invalid_span")
        start, end, quote = value["start"], value["end"], value["quote"]
        if type(start) is not int or type(end) is not int or not isinstance(quote, str):
            _fail("invalid_span")
        return {"start": start, "end": end, "quote": quote}

    def _normalize_claim(self, claim: Any, mention_keys: set[str] | None) -> dict[str, Any]:
        allowed = {"value", "rationale", "evidence", "fields"}
        if mention_keys is not None:
            allowed.add("mention")
        if not isinstance(claim, dict) or set(claim) - allowed:
            _fail("invalid_claim")
        if mention_keys is not None and claim.get("mention") not in mention_keys:
            _fail("claim_mention_required")
        value = claim.get("value")
        evidence = claim.get("evidence")
        fields = claim.get("fields", {})
        # A mention-level claim cites its mention's position automatically.
        if (
            not isinstance(value, dict)
            or not isinstance(evidence, list)
            or (not evidence and mention_keys is None)
        ):
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
            **({"mention": claim["mention"]} if mention_keys is not None else {}),
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
        reviewer, mentions, normalized = self._normalize(request)
        producer = "human:" + re.sub(r"\s+", "-", reviewer.strip())
        # Mentions join the seed only when present, so note-only results rebuild unchanged.
        seed = canonical_sha256(
            {
                "record_id": source.source_id,
                "configuration_sha256": configuration_sha256,
                "reviewer": reviewer,
                "variables": normalized,
                **({"mentions": mentions} if mentions else {}),
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
            readings = {
                item["key"]: self._mention(graph, source, item, ids, provenance)
                for item in mentions
            }
            for name in self.finals:
                outcomes[name] = self._variable(
                    graph, case, source, name, normalized[name], provenance, readings
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
            **({"amber_mentions": mentions} if mentions else {}),
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

    def _mention(
        self,
        graph: EvidenceGraph,
        source: Source,
        item: dict[str, Any],
        ids: Any,
        provenance: Provenance,
    ) -> tuple[MentionReading, list[str], dict[str, list[str]]]:
        """Ground a request mention and register it with the reviewer's reading. Returns the
        reading, the mention's position inclusions, and each attribute cue as a field role."""
        text = source.text or ""
        if not 0 <= item["start"] < item["end"] <= len(text):
            _fail("invalid_span")
        if text[item["start"] : item["end"]] != item["quote"]:
            _fail("quote_mismatch")
        span = exact_mention(source, item["quote"], item["start"], item["mention_type"])
        if not isinstance(span, Mention) or span.end != item["end"]:
            _fail("quote_mismatch")
        attributes: dict[str, Attribute] = {}
        cues: dict[str, list[str]] = {}
        for category, attribute in item["attributes"].items():
            cue = attribute.get("cue")
            scope = attribute.get("scope")
            cue_id = None if cue is None else self._inclusion(graph, source, cue)
            scope_id = None if scope is None else self._inclusion(graph, source, scope)
            try:
                attributes[category] = Attribute(
                    value=attribute["value"], evidence_id=cue_id, scope_evidence_id=scope_id
                )
            except ValueError:
                _fail("invalid_attribute")
            if cue_id is not None:
                cues[f"attribute:{category}"] = [cue_id]
        try:
            reading = MentionReading(
                reading_id=ids(),
                mention_id=span.mention_id,
                label=item.get("label"),
                value=item.get("value"),
                attributes=attributes,
                provenance=provenance,
            )
        except ValueError:
            _fail("invalid_mention")
        graph.register_mention(span, [reading])
        positions = [
            graph.register_evidence(position).evidence_id for position in cite_mention(source, span)
        ]
        return reading, positions, cues

    def _variable(
        self,
        graph: EvidenceGraph,
        case: Case,
        source: Source,
        name: str,
        entry: dict[str, Any],
        provenance: Provenance,
        readings: dict[str, tuple[MentionReading, list[str], dict[str, list[str]]]],
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
            roles = {
                field: [spans[i] for i in indexes] for field, indexes in claim["fields"].items()
            }
            reading_id = None
            cited = spans
            if "mention" in claim:
                reading, positions, cues = readings[claim["mention"]]
                reading_id = reading.reading_id
                cited = [*positions, *spans, *(cue for ids in cues.values() for cue in ids)]
                roles.update(cues)
            committed = graph.commit_claim(
                name,
                claim["value"],
                list(dict.fromkeys(cited)),
                claim["rationale"],
                roles or None,
                provenance=provenance,
                reading_id=reading_id,
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
        mentions = result.get("amber_mentions")
        return {
            "whole_note_complete": True,
            "reviewer": producer.get("reviewer"),
            **({"mentions": mentions} if mentions is not None else {}),
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
