"""``FileReviewBackend``: the reference ``ReviewBackend``, round-tripping through JSON files, and
``simulate_reviews``, a synthetic review service writing reviewers' events back.

A batch directory holds ``batch.json`` (receipt fields, cases, the variable set's JSON form)
and ``cases/<n>.json`` (the source and any proposal) on the way out, and
``reviews/<n>.json`` (``{"events": [...], "spans": [...]}``) on the way back.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from amber.cases import load_case_result
from amber.interfaces import PolicyGate, SourceResolver
from amber.labeling import EventIds, import_events
from amber.policy import DataPolicy, Decision, Destination
from amber.review import (
    ReviewBatchReceipt,
    ReviewImportError,
    check_review_provenance,
    load_spans,
    reviewed_example,
)
from amber.schemas import (
    AnnotationEvent,
    AnswerModel,
    Case,
    CaseResult,
    Example,
    Inclusion,
    Mention,
    Provenance,
    Source,
    VariableSet,
)
from amber.wire import WireFormatError, load_variable_set, variable_set_json

_BATCH = "batch.json"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReviewImportError("invalid_batch") from None


class FileReviewBackend:
    """Writes one batch directory per export under ``directory``.

    ``answer_models`` are the authoritative answer-model classes the import rebuilds the
    batch's variable set against, so a fresh backend can import a batch it did not export.
    """

    def __init__(
        self,
        directory: Path,
        destination: Destination,
        *,
        answer_models: Iterable[type[AnswerModel]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if destination.kind != "review":
            raise ValueError("a review backend needs a review destination")
        self.directory = directory
        self.destination = destination
        self._answer_models = tuple(answer_models)
        self._clock = clock

    # export ----------------------------------------------------------------------------------

    def export_cases(
        self,
        cases: Sequence[tuple[Case, Source, CaseResult | None]],
        *,
        variables: VariableSet,
        streams: Sequence[str],
        gate: PolicyGate,
        data: DataPolicy,
    ) -> ReviewBatchReceipt:
        if not cases:
            raise ValueError("nothing to review")
        payloads: list[dict[str, Any]] = []
        for case, source, result in cases:
            if case.task != variables.name or source.source_id not in case.source_ids:
                raise ValueError("case, source, and variable set do not belong together")
            if case.sensitivity is not data.sensitivity:
                raise ValueError("the data policy is for another sensitivity")
            proposal = None
            if result is not None:
                proposal = load_case_result(
                    result.model_dump(mode="json"), case=case, source=source, variables=variables
                )
            payloads.append(
                {
                    "source": source.model_dump(mode="json"),
                    "result": None if proposal is None else proposal.model_dump(mode="json"),
                }
            )
        now = self._clock()
        decisions: list[Decision] = []
        for (case, _, result), _payload in zip(cases, payloads, strict=True):
            decisions.append(
                gate.require(data, self.destination, "source_text", now=now, case_id=case.case_id)
            )
            if result is not None:
                decisions.append(
                    gate.require(
                        data, self.destination, "derived_text", now=now, case_id=case.case_id
                    )
                )
        batch_id = EventIds(now).next()
        self.directory.mkdir(parents=True, exist_ok=True)
        location = self.directory / batch_id
        receipt = ReviewBatchReceipt(
            batch_id=batch_id,
            destination=self.destination.name,
            location=str(location),
            variable_set_ref=variables.schema_ref,
            case_ids=tuple(case.case_id for case, _, _ in cases),
            streams=tuple(streams),
            decisions=tuple(decisions),
        )
        staging = Path(tempfile.mkdtemp(prefix=f".{batch_id}-", dir=self.directory))
        try:
            _write_json(
                staging / _BATCH,
                {
                    "receipt": receipt.model_dump(mode="json"),
                    "cases": [case.model_dump(mode="json") for case, _, _ in cases],
                    "variables": variable_set_json(variables),
                },
            )
            for index, payload in enumerate(payloads):
                _write_json(staging / "cases" / f"{index}.json", payload)
            os.replace(staging, location)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return receipt

    # import ----------------------------------------------------------------------------------

    def import_references(
        self, receipt: ReviewBatchReceipt, *, resolver: SourceResolver
    ) -> tuple[Sequence[Example], Sequence[AnnotationEvent]]:
        location = Path(receipt.location)
        batch = _read_json(location / _BATCH)
        if not isinstance(batch, dict) or batch.get("receipt") != receipt.model_dump(mode="json"):
            raise ReviewImportError("receipt_mismatch")
        try:
            variables = load_variable_set(batch["variables"], answer_models=self._answer_models)
            cases = [Case.model_validate(item) for item in batch["cases"]]
        except (KeyError, TypeError, ValueError, WireFormatError):
            raise ReviewImportError("invalid_batch") from None
        if tuple(case.case_id for case in cases) != receipt.case_ids:
            raise ReviewImportError("receipt_mismatch")
        if variables.schema_ref != receipt.variable_set_ref:
            raise ReviewImportError("receipt_mismatch")
        policy_versions = {decision.policy_version for decision in receipt.decisions}
        if len(policy_versions) != 1:
            raise ReviewImportError("receipt_mismatch")
        (policy_version,) = policy_versions

        examples: list[Example] = []
        log: list[AnnotationEvent] = []
        for index, case in enumerate(cases):
            source = resolver.resolve(case.source_ids[0])
            if source is None or source.source_id != case.source_ids[0]:
                raise ReviewImportError("source_unavailable")
            path = location / "reviews" / f"{index}.json"
            if not path.exists():
                continue
            events, spans = _load_review(_read_json(path), source)
            check_review_provenance(
                events, destination=receipt.destination, policy_version=policy_version
            )
            example = reviewed_example(
                events, case=case, source=source, variables=variables, spans=spans
            )
            if example is not None:
                examples.append(example)
            log.extend(events)
        return tuple(examples), tuple(log)


def _load_review(
    payload: Any, source: Source
) -> tuple[tuple[AnnotationEvent, ...], tuple[Inclusion | Mention, ...]]:
    if not isinstance(payload, dict) or set(payload) != {"events", "spans"}:
        raise ReviewImportError("invalid_batch")
    try:
        events = tuple(AnnotationEvent.model_validate(item) for item in payload["events"])
    except (TypeError, ValueError):
        raise ReviewImportError("invalid_events") from None
    if not isinstance(payload["spans"], list):
        raise ReviewImportError("invalid_batch")
    return events, load_spans(payload["spans"], source)


# The simulated review service ----------------------------------------------------------------

ReviewDecision = Literal["accepted", "unresolved", "excluded"]
REVIEWER = "human:synthetic-reviewer-{stream}"
ADJUDICATOR = "human:synthetic-adjudicator"
IMPORTER = "tool:synthetic-review-service@1"


def simulate_reviews(
    receipt: ReviewBatchReceipt,
    *,
    variables: VariableSet,
    decision: ReviewDecision = "accepted",
    now: datetime,
    skip: Iterable[str] = (),
) -> None:
    """Review every case of a batch, writing ``reviews/<n>.json``.

    A proposal is imported first and accepted unchanged; a case without one is confirmed
    ``not_mentioned`` over its whole note. One stream gives ``single_review``; several give
    blind submissions and an adjudication with ``decision``. Cases in ``skip`` stay unreviewed.
    """
    if len(receipt.streams) == 1 and decision != "accepted":
        raise ValueError("only an adjudication can leave a case unresolved or excluded")
    location = Path(receipt.location)
    batch = json.loads((location / _BATCH).read_text(encoding="utf-8"))
    cases = [Case.model_validate(item) for item in batch["cases"]]
    policy_version = receipt.decisions[0].policy_version
    skipped = set(skip)
    for index, case in enumerate(cases):
        if case.case_id in skipped:
            continue
        payload = json.loads((location / "cases" / f"{index}.json").read_text(encoding="utf-8"))
        source = Source.model_validate(payload["source"])
        stamp = {
            "destination": receipt.destination,
            "policy_version": policy_version,
            "zone": receipt.decisions[0].zone,
            "sensitivity": case.sensitivity,
            "version": "1",
        }
        writer = _Writer(case, stamp, now + timedelta(seconds=index))
        if payload["result"] is not None:
            result = load_case_result(
                payload["result"], case=case, source=source, variables=variables
            )
            writer.import_proposal(result, source, variables)
        writer.review(variables, source, receipt.streams, decision)
        _write_json(
            location / "reviews" / f"{index}.json",
            {
                "events": [event.model_dump(mode="json") for event in writer.events],
                "spans": [span.model_dump(mode="json") for span in writer.spans],
            },
        )


class _Writer:
    def __init__(self, case: Case, stamp: dict[str, Any], start: datetime) -> None:
        self.case = case
        self.stamp = stamp
        self.start = start
        self.events: list[AnnotationEvent] = []
        self.spans: tuple[Inclusion | Mention, ...] = ()
        self._ids = EventIds(start + timedelta(milliseconds=500))
        self._tick = 0

    def provenance(self, producer: str) -> Provenance:
        return Provenance(producer=producer, created_at=self.start, **self.stamp)

    def import_proposal(self, result: CaseResult, source: Source, variables: VariableSet) -> None:
        events, spans, _ = import_events(
            result,
            case=self.case,
            source=source,
            variables=variables,
            actor=IMPORTER,
            provenance=self.provenance(IMPORTER),
            now=self.start,
        )
        self.events.extend(events)
        self.spans = spans

    def add(self, action: str, actor: str, role: str, **fields: Any) -> None:
        self._tick += 1
        self.events.append(
            AnnotationEvent.model_validate(
                {
                    "event_id": self._ids.next(),
                    "case_id": self.case.case_id,
                    "actor": actor,
                    "role": role,
                    "round": 0,
                    "action": action,
                    "at": self.start + timedelta(milliseconds=500 + self._tick),
                    "provenance": self.provenance(actor),
                    **fields,
                }
            )
        )

    def _live(self, kind: str) -> list[str]:
        latest: dict[str, str] = {}
        for event in self.events:
            if event.action == f"{kind}.create" and event.after is not None:
                record = event.after[kind]
                latest[record[f"{kind}_id"]] = record["status"]
        return sorted(key for key, status in latest.items() if status == "proposed")

    def _confirm_absent(
        self, actor: str, role: str, variables: VariableSet, source: Source
    ) -> None:
        for name in sorted(name for name, item in variables.variables.items() if item.final):
            self.add(
                "outcome.set",
                actor,
                role,
                variable=name,
                after={
                    "outcome": {
                        "status": "not_mentioned",
                        "reason": "Synthetic: no target mentions.",
                        "reviewed_source_ids": [source.source_id],
                        "provenance": self.provenance(actor).model_dump(mode="json"),
                    },
                    "final_claim_ids": [],
                },
            )

    def review(
        self,
        variables: VariableSet,
        source: Source,
        streams: Sequence[str],
        decision: ReviewDecision,
    ) -> None:
        finals = sorted(name for name, item in variables.variables.items() if item.final)
        proposed = bool(self.events)
        readings, claims = self._live("reading"), self._live("claim")
        if len(streams) == 1:
            reviewer = REVIEWER.format(stream=streams[0])
            self.add("review.open", reviewer, "reviewer")
            for reading_id in readings:
                self.add(
                    "reading.set_status",
                    reviewer,
                    "reviewer",
                    target_id=reading_id,
                    after={"status": "verified"},
                )
            for claim_id in claims:
                self.add(
                    "claim.set_status",
                    reviewer,
                    "reviewer",
                    target_id=claim_id,
                    after={"status": "verified"},
                )
            if not proposed:
                self._confirm_absent(reviewer, "reviewer", variables, source)
            self.add("review.submit", reviewer, "reviewer")
            for name in finals:
                self.add(
                    "tier.set", reviewer, "reviewer", variable=name, after={"tier": "single_review"}
                )
            return
        for stream in streams:
            reviewer = REVIEWER.format(stream=stream)
            self.add("review.open", reviewer, "reviewer", stream=stream)
            self.add("review.submit", reviewer, "reviewer", stream=stream)
        if decision != "accepted":
            self.add(
                "adjudication.resolve",
                ADJUDICATOR,
                "adjudicator",
                after={"decision": decision},
                reason=f"Synthetic: {decision}.",
            )
            return
        for reading_id in readings:
            self.add(
                "adjudication.resolve",
                ADJUDICATOR,
                "adjudicator",
                target_id=reading_id,
                after={"status": "gold"},
            )
        for claim_id in claims:
            self.add(
                "adjudication.resolve",
                ADJUDICATOR,
                "adjudicator",
                target_id=claim_id,
                after={"status": "gold"},
            )
        if not proposed:
            self._confirm_absent(ADJUDICATOR, "adjudicator", variables, source)
        self.add("adjudication.resolve", ADJUDICATOR, "adjudicator", after={"decision": "accepted"})
        for name in finals:
            self.add(
                "tier.set", ADJUDICATOR, "adjudicator", variable=name, after={"tier": "adjudicated"}
            )


__all__ = ["FileReviewBackend", "ReviewDecision", "simulate_reviews"]
