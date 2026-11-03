"""Seeded ULID sources for reproducible graphs (used by the loupe adapter)."""

from __future__ import annotations

import pytest

from amber.conformance import fixtures as fx
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.ids import seeded_ulids, validate_ulid
from amber.schemas import Sensitivity


def _graph(ids: object) -> EvidenceGraph:
    graph = EvidenceGraph(
        source=fx.SOURCE,
        variables=fx.NOTE_VARIABLES,
        sensitivity=Sensitivity.synthetic,
        ids=ids,  # type: ignore[arg-type]
    )
    graph.register_evidence(fx.SENTENCE)
    return graph


def _commit(graph: EvidenceGraph) -> object:
    return graph.commit_claim(
        fx.NOTE_VARIABLES.name,
        {"present": True},
        [fx.SENTENCE.evidence_id],
        "Invented rationale.",
        {"present": [fx.SENTENCE.evidence_id]},
        provenance=fx.provenance("human:synthetic-reviewer", fx.RUN_IDS[0]),
    )


def test_seeded_ulids_are_canonical_distinct_and_reproducible() -> None:
    first, second = seeded_ulids("seed-a"), seeded_ulids("seed-a")
    values = [first() for _ in range(50)]
    assert values == [second() for _ in range(50)]
    assert len(set(values)) == 50
    assert all(validate_ulid(value) == value for value in values)
    assert seeded_ulids("seed-b")() != values[0]
    with pytest.raises(ValueError):
        seeded_ulids("")


def test_a_graph_mints_its_ids_from_the_seeded_source() -> None:
    one, two = _graph(seeded_ulids("case-1")), _graph(seeded_ulids("case-1"))
    assert _commit(one) == _commit(two)
    assert one.to_payload() == two.to_payload()
    unseeded = _commit(_graph(None))
    assert unseeded != _commit(_graph(seeded_ulids("case-1")))


@pytest.mark.parametrize("ids", [lambda: "not-a-ulid", lambda: 7, lambda: fx.fixed_ulid(5).lower()])
def test_invalid_minted_ids_are_rejected(ids: object) -> None:
    with pytest.raises(GraphValidationError) as failure:
        _commit(_graph(ids))
    assert failure.value.code == "invalid_schema"


def test_a_repeating_id_source_cannot_overwrite_a_node() -> None:
    graph = _graph(lambda: fx.fixed_ulid(9))
    with pytest.raises(GraphValidationError) as failure:
        _commit(graph)
    assert failure.value.code in ("duplicate_reference", "invalid_schema")
    assert graph.claims == {}
