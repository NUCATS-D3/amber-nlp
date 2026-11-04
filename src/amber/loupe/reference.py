"""Exporting cases to loupe and importing its published references as Examples (contract
v0.1 section 4.7). Pure functions over loupe's JSON; amber never imports loupe.

``source_manifest`` turns cases and their sources into a loupe source manifest (one record per
note: ``record_id`` = ``source_id``, exact text, and labels-free metadata) after a
``source_text`` decision for the review destination.

``import_reference`` takes a ``loupe-reference-v1`` publication and returns the annotation
events it implies, the spans they name, the Examples those events replay to, and a source-free
diagnostic per record that yields no Example:

- the publication's ``snapshot_sha256`` and ``adapter_sha256`` must match its content and the
  installed ``AmberClaimsAdapter``; every record must be its authoritative source (resolver);
- every considered review is rebuilt and reloaded by the adapter, so every quote is regrounded
  against the source and a tampered result, offset, or text is refused;
- each review becomes reviewer events in its stream (``claim.create`` with its support,
  ``outcome.set``, then ``review.submit`` with the submission ID); a review loupe timed is
  preceded by ``review.open`` (``after.opened_at``), and its ``review.submit`` carries
  ``submitted_at``, ``active_seconds``, ``idle_seconds``, ``focus_changes``, and loupe's
  ``received_at`` (self-reported by the editor; see ``amber.evaluation.review_effort``);
  with one stream the reviewer sets ``single_review``; otherwise the adjudicator keeps the
  selected review's claims, rejects the others, sets the outcomes, resolves the case
  (publication hash as ``external_ref``), and sets ``adjudicated``;
- ``unresolved`` and ``excluded`` decisions are resolved with their reason and give no Example.

Event IDs are strictly increasing per case and event times are ordinal (real review times
travel in the review events' ``after``), so the same publication always imports to the same
events and Examples.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ulid import ULID

from amber.annotations import AnnotationValidationError, example_from_projection, replay
from amber.cases import CaseValidationError, load_case_result
from amber.graph_errors import GraphValidationError
from amber.interfaces import PolicyGate, SourceResolver
from amber.loupe.adapter import PROVENANCE_TIME, AdapterError, AmberClaimsAdapter
from amber.policy import DataPolicy, Decision, Destination
from amber.review import ReviewBatchReceipt, ReviewImportError
from amber.schemas import (
    AnnotationEvent,
    Case,
    CaseResult,
    Example,
    Inclusion,
    InferenceEvidence,
    Mention,
    Provenance,
    Source,
    VariableSet,
)

REFERENCE_FORMAT = "loupe-reference-v1"
ADAPTER_PATH = "amber.loupe:AmberClaimsAdapter"


class ReferenceError(ValueError):
    """A publication that cannot be imported; the message is a source-free code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def loupe_digest(value: Any) -> str:
    """loupe's content hash: SHA-256 of key-sorted, compact, non-ASCII-preserving JSON."""
    text = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(text.encode()).hexdigest()


def adapter_sha256(adapter: AmberClaimsAdapter) -> str:
    """The binding loupe records for an adapter: class path, class source, and ``binding()``."""
    kind = type(adapter)
    return loupe_digest(
        {
            "class": kind.__module__ + ":" + kind.__qualname__,
            "source": inspect.getsource(kind),
            "dependencies": adapter.binding(),
        }
    )


# Export --------------------------------------------------------------------------------------


def source_manifest(
    cases: Sequence[tuple[Case, Source]],
    *,
    name: str,
    variables: VariableSet,
    project_configuration: Mapping[str, Any],
    streams: Sequence[str],
    synthetic: bool,
    gate: PolicyGate,
    data: DataPolicy,
    destination: Destination,
    now: datetime,
) -> tuple[dict[str, Any], Decision]:
    """A loupe source manifest for claim-mode review of ``cases`` (one note each).

    Decides ``source_text`` for ``destination`` (a ``review`` destination) first and raises
    ``PolicyViolation`` before building anything when refused. Records carry no labels.
    """
    if destination.kind != "review":
        raise ReferenceError("not_a_review_destination")
    if project_configuration.get("variable_set_ref") != variables.schema_ref:
        raise ReferenceError("variable_set_mismatch")
    decision = gate.require(data, destination, "source_text", now=now)
    records: list[dict[str, Any]] = []
    for case, source in cases:
        if (
            case.task != variables.name
            or case.source_ids != [source.source_id]
            or source.text is None
            or case.sensitivity is not data.sensitivity
        ):
            raise ReferenceError("case_mismatch")
        metadata: dict[str, Any] = {
            "patient_id": source.patient_id,
            "external_id": source.external_id,
            "case_id": case.case_id,
            "sensitivity": case.sensitivity.value,
            "variable_set": variables.name,
            "variable_set_version": variables.version,
            "variable_set_ref": variables.schema_ref,
        }
        if case.split is not None:
            metadata["split"] = case.split
        if case.dataset is not None:
            metadata["dataset"] = case.dataset
        records.append({"record_id": source.source_id, "text": source.text, "metadata": metadata})
    manifest = {
        "name": name,
        "synthetic": synthetic,
        "adapter": ADAPTER_PATH,
        "project_configuration": dict(project_configuration),
        # loupe's mention-mode fields; claim mode does not use them.
        "concepts": [{"concept_id": variables.name, "label": variables.name}],
        "axes": {"review": {"values": ["complete"], "default": "complete"}},
        "streams": list(streams),
        "records": sorted(records, key=lambda record: str(record["record_id"])),
    }
    return manifest, decision


def freeze_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The manifest in loupe's frozen form (``loupe freeze``): format, defaults, per-record
    ``source_sha256``, records in ID order, and ``snapshot_sha256``. loupe's own tests check
    that this matches its ``freeze_source``."""
    body = json.loads(json.dumps(dict(manifest)))
    body.pop("snapshot_sha256", None)
    body["format"] = "loupe-source-v1"
    body.setdefault("data_classification", "synthetic" if body["synthetic"] else "restricted")
    body.setdefault("adapter", "generic")
    for record in body["records"]:
        record.setdefault("metadata", {})
        record["source_sha256"] = hashlib.sha256(record["text"].encode()).hexdigest()
    body["records"] = sorted(body["records"], key=lambda record: record["record_id"])
    body["snapshot_sha256"] = loupe_digest(body)
    return body


# Proposals -----------------------------------------------------------------------------------

PROPOSALS_FORMAT = "loupe-proposals-v1"


def proposal_request(result: CaseResult, *, reviewer: str) -> dict[str, Any] | None:
    """The claim-mode request that reproduces a result, or None when any final variable failed
    (failed runs are never proposals) or a mention has fragments (the request cannot express
    them). Each live reading becomes a request mention keyed by its reading ID, with its label,
    value, and attributes; units and concepts are not carried."""
    evidence = {item.evidence_id: item for item in result.evidence}
    inclusions = {key: item for key, item in evidence.items() if isinstance(item, Inclusion)}
    claims = {claim.claim_id: claim for claim in result.claims}
    spans_by_id = {mention.mention_id: mention for mention in result.mentions}

    def span(item: Inclusion | Mention) -> dict[str, Any]:
        return {"start": item.start, "end": item.end, "quote": item.quote}

    mentions: list[dict[str, Any]] = []
    for reading in result.mention_readings:
        if reading.status == "rejected":
            continue
        mention = spans_by_id[reading.mention_id]
        if mention.fragments:
            return None
        entry_mention: dict[str, Any] = {
            "key": reading.reading_id,
            **span(mention),
            "mention_type": mention.mention_type,
        }
        if reading.label is not None:
            entry_mention["label"] = reading.label
        if reading.value is not None:
            entry_mention["value"] = reading.value
        attributes: dict[str, Any] = {}
        for category, attribute in sorted(reading.attributes.items()):
            attributes[category] = {"value": attribute.value}
            for role, evidence_id in (
                ("cue", attribute.evidence_id),
                ("scope", attribute.scope_evidence_id),
            ):
                if evidence_id in inclusions:
                    attributes[category][role] = span(inclusions[evidence_id])
        entry_mention["attributes"] = attributes
        mentions.append(entry_mention)

    variables: dict[str, Any] = {}
    for name, item in sorted(result.variable_outcomes.items()):
        status = item.outcome.status
        if status == "failed":
            return None
        if status != "answered":
            entry: dict[str, Any] = {"status": status, "reason": item.outcome.reason}
            if status == "conflicting_evidence":
                entry["evidence"] = [
                    span(inclusions[i]) for i in item.outcome.evidence_ids if i in inclusions
                ]
            variables[name] = entry
            continue
        built = []
        for claim_id in item.final_claim_ids:
            edges = [edge for edge in result.edges if edge.claim_id == claim_id]
            rationale = next(
                (
                    record.rationale
                    for edge in edges
                    if isinstance((record := evidence.get(edge.evidence_id)), InferenceEvidence)
                ),
                "Model proposal.",
            )
            cited: list[str] = []
            for edge in edges:
                record = evidence.get(edge.evidence_id)
                inputs = (
                    record.inputs if isinstance(record, InferenceEvidence) else [edge.evidence_id]
                )
                cited.extend(i for i in inputs if i in inclusions)
            order = sorted(
                dict.fromkeys(cited), key=lambda i: (inclusions[i].start, inclusions[i].end)
            )
            fields: dict[str, list[int]] = {}
            for edge in edges:
                if (
                    edge.role
                    and not edge.role.startswith("attribute:")
                    and edge.evidence_id in order
                ):
                    fields.setdefault(edge.role, []).append(order.index(edge.evidence_id))
            reading_id = claims[claim_id].reading_id
            built.append(
                {
                    "value": json.loads(json.dumps(claims[claim_id].value)),
                    "rationale": rationale,
                    **({"mention": reading_id} if reading_id is not None else {}),
                    "evidence": [span(inclusions[i]) for i in order],
                    "fields": {k: sorted(set(v)) for k, v in sorted(fields.items())},
                }
            )
        variables[name] = {"status": "answered", "claims": built}
    return {
        "reviewer": reviewer,
        "whole_note_complete": True,
        **({"mentions": mentions} if mentions else {}),
        "variables": variables,
    }


def proposals_file(
    frozen: Mapping[str, Any],
    results: Sequence[CaseResult],
    *,
    producer: str,
    streams: Sequence[str],
    configuration_frozen: bool,
    gate: PolicyGate,
    data: DataPolicy,
    destination: Destination,
    now: datetime,
) -> tuple[dict[str, Any], Decision]:
    """A ``loupe-proposals-v1`` file for a frozen manifest: each result becomes the adapter
    result its proposal request builds. Proposals are ``derived_text``, decided for the review
    destination first. Results whose case is not in the manifest, or that failed, are skipped.
    """
    if destination.kind != "review":
        raise ReferenceError("not_a_review_destination")
    decision = gate.require(data, destination, "derived_text", now=now)
    adapter = AmberClaimsAdapter(dict(frozen))
    records = {record["metadata"].get("case_id"): record for record in frozen["records"]}
    proposed: dict[str, Any] = {}
    for result in results:
        record = records.get(result.case_id)
        request = proposal_request(result, reviewer=producer)
        if record is None or request is None:
            continue
        example = {
            "example_id": record["record_id"],
            "source_text": record["text"],
            "source_record": record,
        }
        proposed[record["record_id"]] = adapter.build_result(
            request, example, adapter.inventory, "proposal"
        )
    body = {
        "format": PROPOSALS_FORMAT,
        "source_sha256": frozen["snapshot_sha256"],
        "producer": producer,
        "streams": list(streams),
        "configuration_frozen": configuration_frozen,
        "records": proposed,
    }
    return body, decision


# Import --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordDiagnostic:
    """A record that gave no Example, and why (``unresolved``, ``excluded``, or a code)."""

    case_id: str
    status: str
    code: str


@dataclass(frozen=True)
class ImportedReference:
    examples: tuple[Example, ...]
    events: tuple[AnnotationEvent, ...]
    spans: tuple[Inclusion | Mention, ...]
    diagnostics: tuple[RecordDiagnostic, ...] = field(default=())


class _Log:
    """Events for one case with strictly increasing IDs and ordinal times."""

    def __init__(self, case: Case, seed: str, provenance: Provenance) -> None:
        self.case = case
        self.provenance = provenance
        self.events: list[AnnotationEvent] = []
        self._serial = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")

    def add(self, action: str, actor: str, role: str, **fields: Any) -> AnnotationEvent:
        self._serial += 1
        event = AnnotationEvent.model_validate(
            {
                "event_id": str(ULID.from_bytes(bytes(6) + self._serial.to_bytes(10, "big"))),
                "case_id": self.case.case_id,
                "actor": actor,
                "role": role,
                "round": 0,
                "action": action,
                "at": PROVENANCE_TIME + timedelta(milliseconds=len(self.events) + 1),
                "provenance": self.provenance.model_copy(update={"producer": actor}),
                **fields,
            }
        )
        self.events.append(event)
        return event


def _claim_events(log: _Log, result: CaseResult, actor: str, stream: str | None) -> None:
    evidence = {item.evidence_id: item for item in result.evidence}
    for claim in result.claims:
        edges = [edge for edge in result.edges if edge.claim_id == claim.claim_id]
        inferences = [
            evidence[edge.evidence_id]
            for edge in edges
            if isinstance(evidence.get(edge.evidence_id), InferenceEvidence)
        ]
        inclusions = sorted(
            {
                item
                for record in inferences
                if isinstance(record, InferenceEvidence)
                for item in record.inputs
                if isinstance(evidence.get(item), Inclusion)
            }
            | {
                edge.evidence_id
                for edge in edges
                if isinstance(evidence.get(edge.evidence_id), Inclusion)
            }
        )
        log.add(
            "claim.create",
            actor,
            "reviewer",
            stream=stream,
            target_id=claim.claim_id,
            after={
                "claim": claim.model_dump(mode="json"),
                "inclusions": inclusions,
                "inferences": [record.model_dump(mode="json") for record in inferences],
                "edges": [edge.model_dump(mode="json") for edge in edges],
            },
        )


def _mention_events(
    log: _Log, result: CaseResult, actor: str, stream: str | None, *, verify: bool
) -> None:
    """A review's mentions, its attribute cue and scope spans, and its readings; with one
    stream the reviewer verifies its own readings (no adjudicator will)."""
    for mention in result.mentions:
        log.add("mention.create", actor, "reviewer", stream=stream, target_id=mention.mention_id)
    cues = sorted(
        {
            evidence_id
            for reading in result.mention_readings
            for attribute in reading.attributes.values()
            for evidence_id in (attribute.evidence_id, attribute.scope_evidence_id)
            if evidence_id is not None
        }
    )
    for evidence_id in cues:
        log.add("evidence.cite", actor, "reviewer", stream=stream, target_id=evidence_id)
    for reading in result.mention_readings:
        log.add(
            "reading.create",
            actor,
            "reviewer",
            stream=stream,
            target_id=reading.reading_id,
            after={"reading": reading.model_dump(mode="json")},
        )
        if verify:
            log.add(
                "reading.set_status",
                actor,
                "reviewer",
                stream=stream,
                target_id=reading.reading_id,
                after={"status": "verified"},
            )


def _outcome_events(
    log: _Log, result: CaseResult, actor: str, role: str, stream: str | None
) -> None:
    for name, item in sorted(result.variable_outcomes.items()):
        log.add(
            "outcome.set",
            actor,
            role,
            stream=stream,
            variable=name,
            after={
                "outcome": item.outcome.model_dump(mode="json"),
                "final_claim_ids": list(item.final_claim_ids),
            },
        )


_TIMING_KEYS = {"opened_at", "submitted_at", "active_seconds", "idle_seconds", "focus_changes"}


def _instant(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReferenceError("invalid_timing")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ReferenceError("invalid_timing") from None


def _review_timing(value: Any) -> dict[str, Any] | None:
    """A review's loupe timing (``{"client": ..., "received_at": ...}``) as event state, or
    None when loupe recorded none (older publications and editors)."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"client", "received_at"}:
        raise ReferenceError("invalid_timing")
    client = value["client"]
    if client is None:
        return None
    if not isinstance(client, dict) or set(client) != _TIMING_KEYS:
        raise ReferenceError("invalid_timing")
    seconds = [client["active_seconds"], client["idle_seconds"]]
    if any(
        isinstance(item, bool) or not isinstance(item, int | float) or not 0 <= item < 1e9
        for item in seconds
    ):
        raise ReferenceError("invalid_timing")
    changes = client["focus_changes"]
    if isinstance(changes, bool) or not isinstance(changes, int) or changes < 0:
        raise ReferenceError("invalid_timing")
    if not _instant(client["opened_at"]) <= _instant(client["submitted_at"]):
        raise ReferenceError("invalid_timing")
    _instant(value["received_at"])
    return {
        "opened_at": client["opened_at"],
        "submitted_at": client["submitted_at"],
        "active_seconds": float(client["active_seconds"]),
        "idle_seconds": float(client["idle_seconds"]),
        "focus_changes": changes,
        "received_at": value["received_at"],
    }


def _check_reference(reference: Any, adapter: AmberClaimsAdapter) -> None:
    if not isinstance(reference, dict) or reference.get("format") != REFERENCE_FORMAT:
        raise ReferenceError("invalid_reference")
    body = {key: value for key, value in reference.items() if key != "snapshot_sha256"}
    if reference.get("snapshot_sha256") != loupe_digest(body):
        raise ReferenceError("reference_checksum_mismatch")
    if reference.get("adapter_sha256") != adapter_sha256(adapter):
        raise ReferenceError("adapter_mismatch")


def import_reference(
    reference: Mapping[str, Any],
    *,
    resolver: SourceResolver,
    variables: VariableSet,
) -> ImportedReference:
    """Import a loupe publication; raises ``ReferenceError`` (or an adapter or validation
    error) for anything tampered, and returns nothing partially validated."""
    configuration = reference.get("configuration") if isinstance(reference, Mapping) else None
    if not isinstance(configuration, dict) or configuration.get("adapter") != ADAPTER_PATH:
        raise ReferenceError("invalid_reference")
    adapter = AmberClaimsAdapter(configuration)
    if adapter.variables.schema_ref != variables.schema_ref:
        raise ReferenceError("variable_set_mismatch")
    _check_reference(dict(reference), adapter)
    streams = configuration.get("streams")
    if not isinstance(streams, list) or not streams:
        raise ReferenceError("invalid_reference")
    publication = str(reference["snapshot_sha256"])

    examples: list[Example] = []
    events: list[AnnotationEvent] = []
    spans: dict[str, Inclusion | Mention] = {}
    diagnostics: list[RecordDiagnostic] = []
    for record in reference.get("records", []):
        source_record = record.get("source") if isinstance(record, dict) else None
        if not isinstance(source_record, dict):
            raise ReferenceError("invalid_reference")
        source = resolver.resolve(str(source_record.get("record_id")))
        if source is None:
            raise ReferenceError("source_unavailable")
        if (
            source.text != source_record.get("text")
            or source_record.get("source_sha256")
            != hashlib.sha256((source.text or "").encode()).hexdigest()
        ):
            raise ReferenceError("source_text_mismatch")
        example_input = {
            "example_id": source.source_id,
            "source_text": source.text,
            "source_record": source_record,
        }
        source, case = adapter.source_and_case(example_input)
        imported = _import_record(
            record, adapter, example_input, case, source, variables, streams, publication
        )
        events.extend(imported[0])
        for span in imported[1]:
            spans[span.evidence_id if isinstance(span, Inclusion) else span.mention_id] = span
        if isinstance(imported[2], Example):
            examples.append(imported[2])
        else:
            diagnostics.append(imported[2])
    return ImportedReference(
        examples=tuple(examples),
        events=tuple(events),
        spans=tuple(spans.values()),
        diagnostics=tuple(diagnostics),
    )


def _import_record(
    record: dict[str, Any],
    adapter: AmberClaimsAdapter,
    example_input: dict[str, Any],
    case: Case,
    source: Source,
    variables: VariableSet,
    streams: list[str],
    publication: str,
) -> tuple[list[AnnotationEvent], list[Inclusion | Mention], Example | RecordDiagnostic]:
    status = record.get("status")
    adjudication = record.get("adjudication")
    if status not in ("accepted", "unresolved", "excluded") or not isinstance(adjudication, dict):
        raise ReferenceError("invalid_reference")
    adjudicator = "human:" + "-".join(str(adjudication.get("reviewer", "")).split())
    reason = adjudication.get("reason")
    if adjudicator == "human:" or not isinstance(reason, str) or not reason.strip():
        raise ReferenceError("invalid_reference")
    stamp = Provenance(
        producer=adjudicator,
        destination=adapter.destination,
        policy_version=adapter.policy_version,
        zone=adapter.zone,
        sensitivity=adapter.sensitivity,
        created_at=PROVENANCE_TIME,
        version="1",
    )
    log = _Log(case, f"{publication}\x00{case.case_id}", stamp)
    spans: list[Inclusion | Mention] = []
    reviews: dict[str, tuple[str, CaseResult]] = {}
    for review in adjudication.get("reviews", []):
        if not isinstance(review, dict) or review.get("stream") not in streams:
            raise ReferenceError("invalid_reference")
        submitted = review.get("result")
        if not isinstance(submitted, dict) or not isinstance(review.get("submission_id"), str):
            raise ReferenceError("invalid_reference")
        result = adapter.validate_result(submitted, example_input)
        try:
            loaded = load_case_result(
                result["case_result"], case=case, source=source, variables=variables
            )
        except (CaseValidationError, GraphValidationError) as error:
            raise ReferenceError(error.code) from None
        actor = "human:" + "-".join(str(result["producer"]["reviewer"]).split())
        stream = review["stream"] if len(streams) > 1 else None
        timing = _review_timing(review.get("timing"))
        spans.extend(item for item in loaded.evidence if isinstance(item, Inclusion))
        spans.extend(loaded.mentions)
        if timing is not None:
            log.add(
                "review.open",
                actor,
                "reviewer",
                stream=stream,
                after={"opened_at": timing["opened_at"]},
                external_ref=review["submission_id"],
            )
        _mention_events(log, loaded, actor, stream, verify=len(streams) == 1)
        _claim_events(log, loaded, actor, stream)
        _outcome_events(log, loaded, actor, "reviewer", stream)
        log.add(
            "review.submit",
            actor,
            "reviewer",
            stream=stream,
            after=None if timing is None else {k: v for k, v in timing.items() if k != "opened_at"},
            external_ref=review["submission_id"],
        )
        reviews[review["submission_id"]] = (actor, loaded)

    finals = sorted(name for name, item in variables.variables.items() if item.final)
    if status != "accepted":
        log.add(
            "adjudication.resolve",
            adjudicator,
            "adjudicator",
            after={"decision": status},
            reason=reason,
            external_ref=publication,
        )
        return log.events, spans, RecordDiagnostic(case.case_id, status, status)

    chosen = reviews.get(str(adjudication.get("selected_submission_id")))
    if chosen is None:
        raise ReferenceError("selected_review_missing")
    actor, selected = chosen
    if len(streams) == 1:
        for name in finals:
            log.add("tier.set", actor, "reviewer", variable=name, after={"tier": "single_review"})
    else:
        kept = {claim.claim_id for claim in selected.claims}
        for _, (_, other) in sorted(reviews.items()):
            for claim in other.claims:
                if claim.claim_id not in kept:
                    log.add(
                        "adjudication.resolve",
                        adjudicator,
                        "adjudicator",
                        target_id=claim.claim_id,
                        after={"status": "rejected"},
                        reason="Not the selected review.",
                    )
        # The selected review's readings become gold (gold claims rely on reviewed readings);
        # the others' are rejected with their claims.
        for _, (_, review_result) in sorted(reviews.items()):
            chosen_review = review_result is selected
            for reading in review_result.mention_readings:
                log.add(
                    "adjudication.resolve",
                    adjudicator,
                    "adjudicator",
                    target_id=reading.reading_id,
                    after={"status": "gold" if chosen_review else "rejected"},
                    reason=None if chosen_review else "Not the selected review.",
                )
        _outcome_events(log, selected, adjudicator, "adjudicator", None)
        log.add(
            "adjudication.resolve",
            adjudicator,
            "adjudicator",
            after={"decision": "accepted"},
            reason=reason,
            external_ref=publication,
        )
        for name in finals:
            log.add(
                "tier.set", adjudicator, "adjudicator", variable=name, after={"tier": "adjudicated"}
            )
    try:
        projection = replay(log.events, case=case, source=source, variables=variables, spans=spans)
        example = example_from_projection(projection, case=case, source=source, variables=variables)
    except (AnnotationValidationError, GraphValidationError) as error:
        raise ReferenceError(error.code) from None
    return log.events, spans, example


class LoupeReviewBackend:
    """``ReviewBackend`` over loupe's files: ``export_cases`` writes ``source.json`` (the source
    manifest in loupe's frozen form) into a batch directory for the operator to set up in loupe,
    plus ``proposals.json`` (``loupe setup --proposals``) when cases carry results;
    ``import_references`` reads the publication's ``reference.json`` placed in that directory.

    Proposals need ``proposal_streams`` (loupe keeps at least one stream source-only) and are
    suppressed by loupe for test-split notes unless ``configuration_frozen``.
    """

    def __init__(
        self,
        directory: Path,
        destination: Destination,
        *,
        name: str,
        project_configuration: Mapping[str, Any],
        synthetic: bool,
        proposal_streams: Sequence[str] = (),
        producer: str | None = None,
        configuration_frozen: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if destination.kind != "review":
            raise ReferenceError("not_a_review_destination")
        self.proposal_streams = tuple(proposal_streams)
        self.producer = producer
        self.configuration_frozen = configuration_frozen
        self.directory = directory
        self.destination = destination
        self.name = name
        self.project_configuration = dict(project_configuration)
        self.synthetic = synthetic
        self._clock = clock

    def export_cases(
        self,
        cases: Sequence[tuple[Case, Source, CaseResult | None]],
        *,
        variables: VariableSet,
        streams: Sequence[str],
        gate: PolicyGate,
        data: DataPolicy,
    ) -> ReviewBatchReceipt:
        results = [result for _, _, result in cases if result is not None]
        if results and (not self.proposal_streams or not self.producer):
            raise ReferenceError("proposal_streams_required")
        now = self._clock()
        manifest, decision = source_manifest(
            [(case, source) for case, source, _ in cases],
            name=self.name,
            variables=variables,
            project_configuration=self.project_configuration,
            streams=streams,
            synthetic=self.synthetic,
            gate=gate,
            data=data,
            destination=self.destination,
            now=now,
        )
        frozen = freeze_manifest(manifest)
        decisions = [decision]
        proposals = None
        if results:
            assert self.producer is not None
            proposals, proposal_decision = proposals_file(
                frozen,
                results,
                producer=self.producer,
                streams=self.proposal_streams,
                configuration_frozen=self.configuration_frozen,
                gate=gate,
                data=data,
                destination=self.destination,
                now=now,
            )
            decisions.append(proposal_decision)
        batch_id = str(ULID.from_datetime(now))
        location = self.directory / batch_id
        location.mkdir(parents=True)
        for file, body in (("source.json", frozen), ("proposals.json", proposals)):
            if body is not None:
                (location / file).write_text(
                    json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
        return ReviewBatchReceipt(
            batch_id=batch_id,
            destination=self.destination.name,
            location=str(location),
            variable_set_ref=variables.schema_ref,
            case_ids=tuple(case.case_id for case, _, _ in cases),
            streams=tuple(streams),
            decisions=tuple(decisions),
        )

    def import_references(
        self, receipt: ReviewBatchReceipt, *, resolver: SourceResolver
    ) -> tuple[Sequence[Example], Sequence[AnnotationEvent]]:
        location = Path(receipt.location)
        try:
            reference = json.loads((location / "reference.json").read_text(encoding="utf-8"))
            manifest = json.loads((location / "source.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ReviewImportError("invalid_batch") from None
        configuration = reference.get("configuration") if isinstance(reference, dict) else None
        if not isinstance(configuration, dict) or any(
            configuration.get(key) != manifest.get(key)
            for key in ("adapter", "project_configuration", "streams")
        ):
            raise ReviewImportError("receipt_mismatch")
        variables = AmberClaimsAdapter(configuration).variables
        if variables.schema_ref != receipt.variable_set_ref:
            raise ReviewImportError("receipt_mismatch")
        try:
            imported = import_reference(reference, resolver=resolver, variables=variables)
        except (ReferenceError, AdapterError) as error:
            raise ReviewImportError("invalid_events", error.code) from None
        if {example.case_id for example in imported.examples} - set(receipt.case_ids):
            raise ReviewImportError("receipt_mismatch")
        return imported.examples, imported.events


__all__ = [
    "LoupeReviewBackend",
    "ADAPTER_PATH",
    "REFERENCE_FORMAT",
    "ImportedReference",
    "PROPOSALS_FORMAT",
    "RecordDiagnostic",
    "ReferenceError",
    "adapter_sha256",
    "freeze_manifest",
    "import_reference",
    "loupe_digest",
    "proposal_request",
    "proposals_file",
    "source_manifest",
]
