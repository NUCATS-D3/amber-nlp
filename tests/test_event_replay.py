"""Event replay branches the mutation run found untested (amber-u4c.8): who may record review
and import markers, adding and removing edges, and citing evidence (inclusions and
inferences) on its own event."""

from __future__ import annotations

from typing import Any

import pytest

from amber.annotations import AnnotationValidationError, replay
from amber.ids import new_ulid

from ._annotation_helpers import (
    ADJ,
    CASE,
    IMPORTER,
    POSITION,
    SENTENCE,
    SOURCE,
    SPANS,
    VARIABLES,
    A,
    Log,
    _claim,
    _import,
    _provenance,
)


def _replay(log: Log) -> Any:
    return replay(log.events, case=CASE, source=SOURCE, variables=VARIABLES, spans=SPANS)


def _code(log: Log) -> str:
    with pytest.raises(AnnotationValidationError) as failure:
        _replay(log)
    return failure.value.code


def _finding() -> tuple[Log, str]:
    log = Log()
    ids = _import(log)
    return log, log.finding(A, A, "reviewer", ids["reading"])


# Markers -----------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["review.open", "review.submit", "review.reopen"])
@pytest.mark.parametrize(("actor", "role"), [(IMPORTER, "importer"), (ADJ, "adjudicator")])
def test_only_reviewers_record_review_markers(action: str, actor: str, role: str) -> None:
    log, _ = _finding()
    log.add(action, A, "reviewer")
    _replay(log)
    log.add(action, actor, role)
    assert _code(log) == "invalid_role"


def test_only_importers_record_imports() -> None:
    log, _ = _finding()
    log.add("import.result", A, "reviewer")
    assert _code(log) == "invalid_role"


# Edges -------------------------------------------------------------------------------------


def _edge(claim_id: str, evidence_id: str) -> dict[str, Any]:
    return {"claim_id": claim_id, "evidence_id": evidence_id, "role": None}


def test_edges_are_added_and_removed_by_their_own_events() -> None:
    log, finding = _finding()
    log.add("evidence.cite", A, "reviewer", target_id=SENTENCE.evidence_id)
    log.add("edge.add", A, "reviewer", after={"edge": _edge(finding, SENTENCE.evidence_id)})
    added = _replay(log)
    assert SENTENCE.evidence_id in added.evidence
    assert {(e.claim_id, e.evidence_id) for e in added.edges} == {
        (finding, POSITION.evidence_id),
        (finding, SENTENCE.evidence_id),
    }
    log.add("edge.remove", A, "reviewer", before={"edge": _edge(finding, SENTENCE.evidence_id)})
    removed = _replay(log)
    assert {(e.claim_id, e.evidence_id) for e in removed.edges} == {(finding, POSITION.evidence_id)}


def test_edge_events_are_checked() -> None:
    log, finding = _finding()
    log.add("evidence.cite", A, "reviewer", target_id=SENTENCE.evidence_id)
    edge = _edge(finding, SENTENCE.evidence_id)
    log.add("edge.add", A, "reviewer", after={"edge": edge})

    again = Log()
    again.events = [*log.events]
    again.tick = log.tick
    again.add("edge.add", A, "reviewer", after={"edge": edge})
    assert _code(again) == "invalid_transition"

    missing = Log()
    missing.events = [*log.events]
    missing.tick = log.tick
    missing.add("edge.remove", A, "reviewer", before={"edge": _edge(finding, "e" * 64)})
    assert _code(missing) == "unknown_reference"

    malformed = Log()
    malformed.events = [*log.events]
    malformed.tick = log.tick
    malformed.add("edge.remove", A, "reviewer", before={"edge": edge, "extra": 1})
    assert _code(malformed) == "invalid_schema"


# Citations ---------------------------------------------------------------------------------


def _inference(finding: str) -> dict[str, Any]:
    return {
        "kind": "inference",
        "evidence_id": new_ulid(),
        "rationale": "Invented derivation.",
        "inputs": [finding, SENTENCE.evidence_id],
        "trace_id": None,
        "span_id": None,
        "provenance": _provenance(A),
    }


def test_an_inference_may_be_cited_before_the_claim_that_uses_it() -> None:
    log, finding = _finding()
    log.add("evidence.cite", A, "reviewer", target_id=SENTENCE.evidence_id)
    inference = _inference(finding)
    log.add("evidence.cite", A, "reviewer", after={"inference": inference})
    summary = _claim("summary", A)
    log.add(
        "claim.create",
        A,
        "reviewer",
        target_id=summary["claim_id"],
        after={
            "claim": summary,
            "edges": [_edge(summary["claim_id"], inference["evidence_id"])],
        },
    )
    projection = _replay(log)
    assert inference["evidence_id"] in projection.evidence
    assert summary["claim_id"] in projection.claims

    log.add("evidence.cite", A, "reviewer", after={"inference": inference})
    assert _code(log) == "invalid_transition"


# Statuses ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "action", "actor", "role", "status"),
    [
        (None, "claim.set_status", A, "reviewer", "rejected"),
        ("verified", "adjudication.resolve", ADJ, "adjudicator", "rejected"),
    ],
    ids=["reviewer_rejects_proposed", "adjudicator_rejects_verified"],
)
def test_permitted_transitions_apply(
    first: str | None, action: str, actor: str, role: str, status: str
) -> None:
    log, finding = _finding()
    if first:
        log.add("claim.set_status", A, "reviewer", target_id=finding, after={"status": first})
    log.add(action, actor, role, target_id=finding, after={"status": status}, reason="Invented.")
    assert _replay(log).claims[finding].status == status


def test_a_verified_claim_becomes_gold_by_adjudication() -> None:
    log = Log()
    ids = _import(log)
    finding = log.finding(A, A, "reviewer", ids["reading"])
    log.add("reading.set_status", A, "reviewer", target_id=ids["reading"],
            after={"status": "verified"})  # fmt: skip
    log.add("claim.set_status", A, "reviewer", target_id=finding, after={"status": "verified"})
    log.add("adjudication.resolve", ADJ, "adjudicator", target_id=finding,
            after={"status": "gold"})  # fmt: skip
    assert _replay(log).claims[finding].status == "gold"


def test_correcting_a_verified_claim_supersedes_it() -> None:
    log, finding = _finding()
    log.add("claim.set_status", A, "reviewer", target_id=finding, after={"status": "verified"})
    corrected = _claim("finding", A, _replay(log).claims[finding].reading_id)
    corrected["value"] = {"present": False}
    log.add(
        "claim.update", A, "reviewer", target_id=finding,
        after={"claim": corrected, "inclusions": [POSITION.evidence_id],
               "edges": [_edge(corrected["claim_id"], POSITION.evidence_id)]},
    )  # fmt: skip
    assert _replay(log).claims[finding].status == "rejected"


def test_a_human_tier_cannot_rest_on_a_rejected_final_claim() -> None:
    log, finding = _finding()
    reading = _replay(log).claims[finding].reading_id
    log.add("reading.set_status", A, "reviewer", target_id=reading, after={"status": "verified"})
    log.outcome("finding", [finding], A, "reviewer")
    log.add("claim.set_status", A, "reviewer", target_id=finding, after={"status": "rejected"})
    log.add("tier.set", A, "reviewer", variable="finding", after={"tier": "single_review"})
    assert _code(log) == "invalid_transition"


# Malformed events --------------------------------------------------------------------------


def test_malformed_events_name_their_code() -> None:
    log, finding = _finding()
    log.add("outcome.set", A, "reviewer", after={"outcome": {}, "final_claim_ids": []})
    assert _code(log) == "invalid_context"  # no variable

    log, finding = _finding()
    inclusion = {**POSITION.model_dump(mode="json"), "evidence_id": new_ulid()}
    log.add("evidence.cite", A, "reviewer", after={"inference": inclusion})
    assert _code(log) == "invalid_schema"  # not an inference

    log, finding = _finding()
    claim = _claim("finding", A, _replay(log).claims[finding].reading_id)
    log.add("claim.create", A, "reviewer", target_id=claim["claim_id"],
            after={"claim": claim, "unexpected": 1})  # fmt: skip
    assert _code(log) == "invalid_schema"  # unknown key in after

    log, finding = _finding()
    claim = _claim("finding", A, _replay(log).claims[finding].reading_id)
    log.add("claim.create", A, "reviewer", target_id=claim["claim_id"],
            after={"claim": claim, "edges": [_edge(finding, POSITION.evidence_id)]})  # fmt: skip
    assert _code(log) == "invalid_context"  # an edge for another claim


def test_a_claim_may_receive_its_edges_later() -> None:
    log, finding = _finding()
    claim = _claim("finding", A, _replay(log).claims[finding].reading_id)
    log.add("claim.create", A, "reviewer", target_id=claim["claim_id"], after={"claim": claim})
    log.add("edge.add", A, "reviewer", after={"edge": _edge(claim["claim_id"],
                                                             POSITION.evidence_id)})  # fmt: skip
    assert claim["claim_id"] in _replay(log).claims


def test_replay_checks_its_arguments_and_accepts_an_empty_log() -> None:
    with pytest.raises(AnnotationValidationError, match="^invalid_context$"):
        replay([], case="not a case", source=SOURCE, variables=VARIABLES)  # type: ignore[arg-type]
    assert replay([], case=CASE, source=SOURCE, variables=VARIABLES).as_of_event is None


def test_system_actors_see_every_stream() -> None:
    from amber.annotations import Viewer, visible_events

    from ._annotation_helpers import SYSTEM, _stream

    log = Log()
    ids = _import(log)
    _stream(log, A, "a", ids["reading"])
    log.add("tier.set", SYSTEM, "system", after={"decision_ref": "invented-move-on"})
    assert len(visible_events(log.events, Viewer(SYSTEM, "system"))) == len(log.events)
    outsider = Viewer("human:outsider", "reviewer")
    assert len(visible_events(log.events, outsider)) < len(log.events)


def _bad_outcome(log: Log, finding: str) -> None:
    log.add("outcome.set", A, "reviewer", variable="finding",
            after={"outcome": {"status": "nonsense"}, "final_claim_ids": []})  # fmt: skip


def _unknown_final(log: Log, finding: str) -> None:
    log.outcome("finding", [new_ulid()], A, "reviewer")


def _status_of_nothing(log: Log, finding: str) -> None:
    log.add("claim.set_status", A, "reviewer", target_id=new_ulid(), after={"status": "verified"})


def _bad_tier(log: Log, finding: str) -> None:
    log.add("tier.set", A, "reviewer", variable="finding", after={"tier": "platinum"})


def _tier_without_outcome(log: Log, finding: str) -> None:
    log.add("tier.set", A, "reviewer", variable="finding", after={"tier": "single_review"})


def _mention_of_an_inclusion(log: Log, finding: str) -> None:
    log.add("mention.create", A, "reviewer", target_id=POSITION.evidence_id)


def _update_of_nothing(log: Log, finding: str) -> None:
    claim = _claim("finding", A)
    log.add("claim.update", A, "reviewer", target_id=new_ulid(), after={"claim": claim})


def _reading_of_an_unknown_mention(log: Log, finding: str) -> None:
    reading = {"reading_id": new_ulid(), "mention_id": "m" * 64, "status": "proposed",
               "provenance": _provenance(A)}  # fmt: skip
    log.add("reading.create", A, "reviewer", target_id=reading["reading_id"],
            after={"reading": reading})  # fmt: skip


def _unknown_variable(log: Log, finding: str) -> None:
    log.add("review.submit", A, "reviewer", variable="no_such_variable")


def _bad_edge(log: Log, finding: str) -> None:
    log.add("edge.add", A, "reviewer", after={"edge": {"claim_id": finding}})


@pytest.mark.parametrize(
    ("edit", "code"),
    [
        (_bad_outcome, "invalid_schema"),
        (_unknown_final, "unknown_reference"),
        (_status_of_nothing, "unknown_reference"),
        (_bad_tier, "invalid_schema"),
        (_tier_without_outcome, "invalid_transition"),
        (_mention_of_an_inclusion, "invalid_context"),
        (_update_of_nothing, "unknown_reference"),
        (_reading_of_an_unknown_mention, "unknown_reference"),
        (_unknown_variable, "invalid_context"),
        (_bad_edge, "invalid_schema"),
    ],
)
def test_each_malformed_event_names_its_code(edit: Any, code: str) -> None:
    """amber-u4c.8: every replay refusal names its own code."""
    log, finding = _finding()
    edit(log, finding)
    assert _code(log) == code


def test_replay_inputs_name_their_code() -> None:
    from amber.annotations import example_from_projection, verify_example
    from amber.examples import ExampleValidationError

    log, _ = _finding()
    with pytest.raises(AnnotationValidationError, match="^invalid_schema$"):
        replay([*log.events, "not an event"], case=CASE, source=SOURCE, variables=VARIABLES)  # type: ignore[list-item]
    with pytest.raises(AnnotationValidationError, match="^invalid_context$"):
        replay(log.events, case=CASE.model_copy(update={"task": "other"}), source=SOURCE,
               variables=VARIABLES)  # fmt: skip
    projection = _replay(log)
    with pytest.raises((AnnotationValidationError, ExampleValidationError), match="^incomplete"):
        example_from_projection(projection, case=CASE, source=SOURCE, variables=VARIABLES)
    with pytest.raises(AnnotationValidationError, match="^invalid_context$"):
        example_from_projection(projection, case=CASE.model_copy(update={"case_id": "other"}),
                                source=SOURCE, variables=VARIABLES)  # fmt: skip
    from ._annotation_helpers import _adjudicated

    full, _ = _adjudicated()
    example = example_from_projection(
        replay(full.events, case=CASE, source=SOURCE, variables=VARIABLES, spans=SPANS),
        case=CASE, source=SOURCE, variables=VARIABLES,
    )  # fmt: skip
    with pytest.raises(AnnotationValidationError, match="^events_mismatch$"):
        verify_example(example, full.events[:-1], case=CASE, source=SOURCE, variables=VARIABLES,
                       spans=SPANS)  # fmt: skip
    with pytest.raises(AnnotationValidationError, match="^invalid_schema$"):
        verify_example("not an example", full.events, case=CASE, source=SOURCE,  # type: ignore[arg-type]
                       variables=VARIABLES, spans=SPANS)  # fmt: skip
