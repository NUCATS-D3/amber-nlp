"""Property tests for amber's core relations (amber-2sw; the invariant-tests skill): canonical
hashing, quote faithfulness, wire and bundle round trips, replay prefix determinism, and
random support graphs that must refuse cycles and source-free branches, and span scoring
(amber-a6d.10). Strategies are
bounded so the suite stays fast; example counts and deadlines come from the Hypothesis profile
in tests/conftest.py."""

from __future__ import annotations

import itertools
import json
import re
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from hypothesis import assume, example, given
from hypothesis import strategies as st
from pydantic import ValidationError

from amber import _graph_validation
from amber.annotations import replay
from amber.bundles import (
    BundleContents,
    ManifestDataset,
    ManifestProducer,
    build_bundle,
    load_bundle,
)
from amber.cases import create_case_result, load_case_result
from amber.conformance import fixtures as fx
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.grounding import exact_mention, exact_quote
from amber.ids import (
    canonical_json,
    canonical_sha256,
    new_ulid,
    parse_canonical_json,
    seeded_ulids,
)
from amber.reference import InMemoryResolver
from amber.schemas import (
    Case,
    CaseOutcome,
    Inclusion,
    InferenceEvidence,
    Mention,
    MentionReading,
    Sensitivity,
    Source,
    SourceKind,
    VariableSet,
)
from amber.span_scoring import Span, span_metrics

from ._claim_helpers import SYNTHETIC_RUN_ID, make_provenance, make_task

TASK = make_task(evidence_policy="claim")
VARIABLES = VariableSet.from_task(TASK)

# Text with CRLF, astral characters, combining marks, and plain words.
_ATOMS = st.sampled_from(
    ["a", "b", "mass", " ", "\r\n", "\n", "\U0001f642", "e\u0301", "\u00e9", "\U00010348", "."]
)
TEXTS = st.lists(_ATOMS, min_size=1, max_size=40).map("".join)
JSON_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**53 - 1), max_value=2**53 - 1),
    st.floats(allow_nan=False, allow_infinity=False, width=64),
    st.text(max_size=8),
)
JSON_VALUES = st.recursive(
    JSON_SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=6), children, max_size=4),
    ),
    max_leaves=12,
)


def _source(text: str) -> Source:
    return Source.create(
        patient_id="synthetic-patient-1",
        kind=SourceKind.note,
        external_id="synthetic-note-1",
        datetime=datetime(2026, 1, 2, tzinfo=UTC),
        text=text,
        record=None,
    )


def _case(source: Source) -> Case:
    return Case(
        case_id="property-case",
        task=TASK.name,
        patient_id=source.patient_id,
        source_ids=[source.source_id],
        sensitivity=Sensitivity.synthetic,
        dataset=fx.DATASET,
        split="dev",
    )


@st.composite
def spans(draw: st.DrawFn) -> tuple[str, int, int]:
    text = draw(TEXTS)
    start = draw(st.integers(min_value=0, max_value=len(text) - 1))
    end = draw(st.integers(min_value=start + 1, max_value=len(text)))
    return text, start, end


# Canonical hashing -------------------------------------------------------------------------


@given(st.dictionaries(st.text(max_size=6), JSON_VALUES, max_size=6), st.randoms())
def test_canonical_hashes_ignore_key_order(value: dict[str, Any], rng: Any) -> None:
    def shuffled(item: Any) -> Any:
        if isinstance(item, dict):
            keys = list(item)
            rng.shuffle(keys)
            return {key: shuffled(item[key]) for key in keys}
        if isinstance(item, list):
            return [shuffled(child) for child in item]
        return item

    reordered = shuffled(value)
    assert canonical_json(reordered) == canonical_json(value)
    assert canonical_sha256(reordered) == canonical_sha256(value)
    text = canonical_json(value)
    assert canonical_json(parse_canonical_json(text)) == text


@given(st.dictionaries(st.text(max_size=6), JSON_VALUES, min_size=1, max_size=6), JSON_VALUES)
def test_changing_a_value_changes_the_hash(value: dict[str, Any], other: Any) -> None:
    key = sorted(value)[0]
    assume(canonical_json(other) != canonical_json(value[key]))
    assert canonical_sha256({**value, key: other}) != canonical_sha256(value)


# Quote faithfulness ------------------------------------------------------------------------


@given(spans())
def test_a_quote_is_exactly_the_text_at_its_code_point_offsets(span: tuple[str, int, int]) -> None:
    text, start, end = span
    source = _source(text)
    inclusion = exact_quote(source, text[start:end], start)
    assert isinstance(inclusion, Inclusion)
    assert (inclusion.start, inclusion.end) == (start, end)
    assert text[inclusion.start : inclusion.end] == inclusion.quote == text[start:end]


@given(spans())
def test_a_quote_never_grounds_at_an_offset_where_it_does_not_occur(
    span: tuple[str, int, int],
) -> None:
    text, start, end = span
    quote = text[start:end]
    for hint in range(len(text)):
        if text[hint : hint + len(quote)] != quote:
            assert not isinstance(exact_quote(_source(text), quote, hint), Inclusion)


# Wire and bundle round trips ---------------------------------------------------------------


def _result(text: str, start: int, end: int, present: bool) -> Any:
    source = _source(text)
    graph = EvidenceGraph(source=source, task=TASK, sensitivity=Sensitivity.synthetic)
    inclusion = exact_quote(source, text[start:end], start)
    assert isinstance(inclusion, Inclusion)
    graph.register_evidence(inclusion)
    claim = graph.commit_claim(
        TASK, {"finding_present": present}, [inclusion.evidence_id], "Invented.",
        provenance=make_provenance(),
    )  # fmt: skip
    result = create_case_result(
        _case(source),
        graph=graph,
        run_id=SYNTHETIC_RUN_ID,
        outcome=CaseOutcome(status="answered", provenance=make_provenance()),
        final_claim_ids=[claim.claim_id],
    )
    return source, graph, result


@given(spans(), st.booleans())
def test_graphs_and_results_round_trip_byte_for_byte(
    span: tuple[str, int, int], present: bool
) -> None:
    source, graph, result = _result(*span, present)
    payload = json.loads(json.dumps(graph.to_payload(), default=str))
    restored = EvidenceGraph.from_payload(
        payload, source=source, task=TASK, sensitivity=Sensitivity.synthetic
    )
    assert restored.to_payload() == graph.to_payload()
    text = result.model_dump_json()
    loaded = load_case_result(
        json.loads(text), case=_case(source), source=source, variables=VARIABLES
    )
    assert loaded.model_dump_json() == text


@given(spans(), st.booleans())
def test_run_bundles_round_trip(span: tuple[str, int, int], present: bool) -> None:
    source, _, result = _result(*span, present)
    bundle = build_bundle(
        kind="run",
        variables=VARIABLES,
        dataset=ManifestDataset(
            name=fx.DATASET, manifest_hash="property", sensitivity=Sensitivity.synthetic
        ),
        producer=ManifestProducer(name="property", version="1", run_id=SYNTHETIC_RUN_ID),
        policy_version="property-policy@1",
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
        sources=[source],
        cases=[_case(source)],
        results=[result],
    )
    loaded = load_bundle(bundle, variables=VARIABLES, resolver=InMemoryResolver([source]))
    assert isinstance(loaded, BundleContents)
    assert loaded.results == (result,)


# Replay ------------------------------------------------------------------------------------


_LOG, _ = fx.adjudicated()
_EVENTS = list(_LOG.events)
_ACTORS = st.sampled_from([None, fx.REVIEWER_A, fx.REVIEWER_B, fx.ADJUDICATOR, fx.IMPORTER])


@given(st.integers(min_value=0, max_value=len(_EVENTS) - 1), _ACTORS)
def test_replaying_up_to_an_event_equals_replaying_that_prefix(index: int, actor: Any) -> None:
    common = {"case": fx.case(), "source": fx.SOURCE, "variables": fx.VARIABLES, "spans": fx.SPANS}
    as_of = _EVENTS[index].event_id
    full = replay(_EVENTS, actor=actor, as_of_event=as_of, **common)
    prefix = replay(_EVENTS[: index + 1], actor=actor, **common)
    assert full == prefix
    assert replay(_EVENTS[: index + 1], actor=actor, **common) == prefix


# Random support graphs ---------------------------------------------------------------------


@st.composite
def support_graphs(draw: st.DrawFn) -> tuple[list[list[int]], set[int]]:
    """Per claim, the inputs of its inference (-1 = the inclusion, j = claim j), and the claims
    that have no evidence at all."""
    size = draw(st.integers(min_value=1, max_value=5))
    inputs = [
        draw(
            st.lists(
                st.integers(min_value=-1, max_value=size - 1), min_size=1, max_size=3, unique=True
            )
        )
        for _ in range(size)
    ]
    orphans = draw(st.sets(st.integers(min_value=0, max_value=size - 1), max_size=1))
    return inputs, orphans


def _has_cycle(inputs: list[list[int]], orphans: set[int]) -> bool:
    state: dict[int, int] = {}

    def visit(node: int) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2 or node in orphans:
            return False
        state[node] = 1
        found = any(visit(child) for child in inputs[node] if child >= 0)
        state[node] = 2
        return found

    return any(visit(node) for node in range(len(inputs)))


@given(support_graphs())
def test_random_graphs_validate_exactly_when_acyclic_and_source_backed(
    graph: tuple[list[list[int]], set[int]],
) -> None:
    inputs, orphans = graph
    source = _source("Alpha beta gamma")
    seed = EvidenceGraph(source=source, task=TASK, sensitivity=Sensitivity.synthetic)
    inclusion = exact_quote(source, "Alpha", 0)
    assert isinstance(inclusion, Inclusion)
    template = seed.register_evidence(inclusion)
    claim = seed.commit_claim(
        TASK,
        {"finding_present": True},
        [template.evidence_id],
        "Invented.",
        provenance=make_provenance(),
    )
    payload = json.loads(json.dumps(seed.to_payload(), default=str))
    claim_template, inference_template, inclusion_payload = (
        payload["claims"][0],
        next(e for e in payload["evidence"] if e["kind"] == "inference"),
        next(e for e in payload["evidence"] if e["kind"] == "inclusion"),
    )
    claim_ids = [new_ulid() for _ in inputs]
    claims, evidence, edges = [], [inclusion_payload], []
    for index, node_inputs in enumerate(inputs):
        claims.append({**claim_template, "claim_id": claim_ids[index]})
        if index in orphans:
            continue
        inference_id = new_ulid()
        ids = [inclusion.evidence_id if item < 0 else claim_ids[item] for item in node_inputs]
        evidence.append({**inference_template, "evidence_id": inference_id, "inputs": ids})
        edges.append(
            {
                "claim_id": claim_ids[index],
                "evidence_id": inference_id,
                "role": None,
                "weight": None,
            }
        )
    payload.update(claims=claims, evidence=evidence, edges=edges)
    valid = not orphans and not _has_cycle(inputs, orphans)
    try:
        EvidenceGraph.from_payload(
            payload, source=source, task=TASK, sensitivity=Sensitivity.synthetic
        )
    except GraphValidationError as error:
        assert not valid, error.code
        assert error.code in {"cyclic_support", "source_free_support", "missing_evidence"}
    else:
        assert valid
    assert claim.claim_id not in claim_ids


# Incremental validation equals full revalidation (amber-2ad) --------------------------------

_MUTATION_TEXT = "alpha beta alpha\r\ngamma \U0001f642 e\u0301 beta"
_MUTATION_SPANS = [(match.start(), match.end()) for match in re.finditer(r"\S+", _MUTATION_TEXT)]
_SPAN_INDEX = st.integers(min_value=0, max_value=len(_MUTATION_SPANS) - 1)
_KNOWN_INDEX = st.integers(min_value=-1, max_value=12)
_MUTATIONS = st.lists(
    st.one_of(
        st.tuples(st.just("inclusion"), _SPAN_INDEX),
        st.tuples(
            st.just("mention"),
            _SPAN_INDEX,
            st.sampled_from(["finding", "anatomy"]),
            st.integers(min_value=-1, max_value=len(_MUTATION_SPANS) - 1),
        ),
        st.tuples(st.just("reading"), _SPAN_INDEX),
        st.tuples(st.just("inference"), st.lists(_KNOWN_INDEX, min_size=1, max_size=2)),
        st.tuples(st.just("claim"), st.lists(_KNOWN_INDEX, min_size=1, max_size=2)),
        st.tuples(st.just("reuse_id"), _KNOWN_INDEX),
    ),
    max_size=10,
)


def _mutate(
    graph: EvidenceGraph, mutation: tuple[Any, ...], mint: Any, known: list[str]
) -> list[str]:
    """Apply one mutation; return the IDs it registered."""
    source = graph.source

    def known_id(index: int) -> str:
        return known[index % len(known)] if known and index >= 0 else "invented-unknown-id"

    def inclusion(index: int) -> Inclusion:
        start, end = _MUTATION_SPANS[index]
        result = exact_quote(source, _MUTATION_TEXT[start:end], start)
        assert isinstance(result, Inclusion)
        return result

    def reading(span: Mention, cue: int = -1) -> MentionReading:
        attributes = (
            {}
            if cue < 0
            else {"negation": {"value": True, "evidence_id": inclusion(cue).evidence_id}}
        )
        return MentionReading.model_validate(
            {
                "reading_id": mint(),
                "mention_id": span.mention_id,
                "label": "invented",
                "attributes": attributes,
                "provenance": make_provenance(),
            }
        )

    def mention(index: int, mention_type: str = "finding") -> Mention:
        start, end = _MUTATION_SPANS[index]
        result = exact_mention(source, _MUTATION_TEXT[start:end], start, mention_type)
        assert isinstance(result, Mention)
        return result

    def inference(evidence_id: str, inputs: list[str]) -> InferenceEvidence:
        return InferenceEvidence.model_validate(
            {
                "kind": "inference",
                "evidence_id": evidence_id,
                "rationale": "Invented.",
                "inputs": inputs,
                "trace_id": None,
                "span_id": None,
                "provenance": make_provenance(),
            }
        )

    kind = mutation[0]
    if kind == "inclusion":
        return [graph.register_evidence(inclusion(mutation[1])).evidence_id]
    if kind == "mention":
        span = mention(mutation[1], mutation[2])
        _, readings = graph.register_mention(span, [reading(span, mutation[3])])
        return [readings[0].reading_id]
    if kind == "reading":
        return [graph.register_reading(reading(mention(mutation[1]))).reading_id]
    if kind == "inference":
        inputs = list(dict.fromkeys(known_id(index) for index in mutation[1]))
        return [graph.register_evidence(inference(mint(), inputs)).evidence_id]
    if kind == "claim":
        cited = list(dict.fromkeys(known_id(index) for index in mutation[1]))
        claim = graph.commit_claim(
            TASK, {"finding_present": True}, cited, "Invented.", provenance=make_provenance()
        )
        return [claim.claim_id]
    graph.register_evidence(inference(known_id(mutation[1]), [known_id(0)]))
    return []


def _apply_mutations(mutations: list[tuple[Any, ...]]) -> tuple[list[str], dict[str, Any]]:
    mint = seeded_ulids("amber-2ad")
    graph = EvidenceGraph(
        source=_source(_MUTATION_TEXT), task=TASK, sensitivity=Sensitivity.synthetic, ids=mint
    )
    known: list[str] = []
    outcomes: list[str] = []
    for mutation in mutations:
        try:
            known.extend(_mutate(graph, mutation, mint, known))
        except GraphValidationError as error:
            outcomes.append(error.code)
        except ValidationError:
            outcomes.append("record_refused_before_the_graph")
        else:
            outcomes.append("ok")
    return outcomes, graph.to_payload()


def _revalidate_everything(context: Any, *, base: Any = None, **records: Any) -> Any:
    """Validation as it was before amber-2ad: the base state's records are checked again."""
    if base is not None:
        existing = {
            "claims": base.claims.values(),
            "evidence": base.evidence.values(),
            "edges": base.edges,
            "mentions": base.mentions.values(),
            "readings": base.readings.values(),
        }
        for name, values in existing.items():
            records[name] = [
                *(value.model_dump() for value in values),
                *records.get(name, ()),
            ]
    return _graph_validation.validate_state(context, **records)


@given(_MUTATIONS)
def test_incremental_validation_matches_full_revalidation(
    mutations: list[tuple[Any, ...]],
) -> None:
    outcomes, payload = _apply_mutations(mutations)
    with patch("amber.graph.validate_state", _revalidate_everything):
        expected_outcomes, expected_payload = _apply_mutations(mutations)

    assert outcomes == expected_outcomes
    assert payload == expected_payload
    restored = EvidenceGraph.from_payload(
        payload, source=_source(_MUTATION_TEXT), task=TASK, sensitivity=Sensitivity.synthetic
    )
    assert restored.to_payload() == payload


def test_large_floats_survive_canonical_json_and_parquet_columns(tmp_path: Any) -> None:
    """Regression found by the property above: RFC 8785 writes 5.575263392078219e16 as
    55752633920782190, which a plain JSON parser reads as an integer amber cannot
    re-serialize."""
    import pytest

    value = {"x": 5.575263392078219e16, "y": 2.0**53, "z": 1e21, "n": 7}
    text = canonical_json(value)
    assert text == '{"n":7,"x":55752633920782190,"y":9007199254740992,"z":1e+21}'
    assert parse_canonical_json(text) == value
    assert canonical_json(parse_canonical_json(text)) == text

    pytest.importorskip("pyarrow")
    from amber.bundles.parquet import _parse_json

    assert _parse_json("claims", "value", text) == value


# Span scoring ---------------------------------------------------------------------------------


@st.composite
def scored_spans(draw: Any) -> Span:
    start = draw(st.integers(0, 6))
    end = start + draw(st.integers(1, 4))
    fragments: tuple[tuple[int, int], ...] = ()
    if draw(st.booleans()):
        first = start + draw(st.integers(1, 2))
        end = first + draw(st.integers(1, 3)) + draw(st.integers(1, 2))
        fragments = ((start, first), (end - draw(st.integers(1, end - first - 1)), end))
    negation = draw(st.lists(st.booleans(), max_size=2))
    return Span(
        case_id="c",
        source_id="s",
        mention_type=draw(st.sampled_from(("finding", "site"))),
        start=start,
        end=end,
        fragments=fragments,
        attributes={"negation": frozenset((bool, value) for value in negation)} if negation else {},
    )


_SPAN_LISTS = st.lists(scored_spans(), max_size=5)


def _counts(predicted: list[Span], gold: list[Span]) -> dict[str, tuple[float, int]]:
    return {
        metric.name: (metric.numerator, metric.denominator)
        for metric in span_metrics(predicted, gold)
    }


def _brute_force_lenient(predicted: list[Span], gold: list[Span]) -> int:
    """Largest one-to-one overlap matching, by trying every assignment."""
    mine = sorted({(span.mention_type, span.pieces) for span in predicted})
    theirs = sorted({(span.mention_type, span.pieces) for span in gold})

    def overlap(a: tuple[str, Any], b: tuple[str, Any]) -> bool:
        return a[0] == b[0] and any(s < f and t < e for s, e in a[1] for t, f in b[1])

    for size in range(min(len(mine), len(theirs)), 0, -1):
        for chosen in itertools.combinations(mine, size):
            for partners in itertools.permutations(theirs, size):
                if all(overlap(a, b) for a, b in zip(chosen, partners, strict=True)):
                    return size
    return 0


def _finding(start: int, end: int) -> Span:
    return Span(case_id="c", source_id="s", mention_type="finding", start=start, end=end)


@given(_SPAN_LISTS, _SPAN_LISTS)
@example([_finding(0, 2), _finding(2, 3)], [_finding(0, 3), _finding(1, 2)])
def test_lenient_span_matching_is_maximum_and_never_below_strict(
    predicted: list[Span], gold: list[Span]
) -> None:
    counts = _counts(predicted, gold)
    strict, lenient = counts["strict_precision"][0], counts["lenient_precision"][0]
    assert strict <= lenient == _brute_force_lenient(predicted, gold)


@given(_SPAN_LISTS, _SPAN_LISTS, st.randoms())
def test_span_scores_ignore_order_and_swap_precision_with_recall(
    predicted: list[Span], gold: list[Span], random: Any
) -> None:
    counts = _counts(predicted, gold)
    shuffled_predicted, shuffled_gold = list(predicted), list(gold)
    random.shuffle(shuffled_predicted)
    random.shuffle(shuffled_gold)
    assert span_metrics(shuffled_predicted, shuffled_gold) == span_metrics(predicted, gold)
    swapped = _counts(gold, predicted)
    for match in ("strict", "lenient"):
        assert swapped[f"{match}_precision"] == counts[f"{match}_recall"]
        assert swapped[f"{match}_f1"] == counts[f"{match}_f1"]


@given(st.lists(scored_spans(), min_size=1, max_size=5))
def test_predicting_the_reference_scores_perfectly(gold: list[Span]) -> None:
    for metric in span_metrics(gold, gold):
        if metric.kind == "evidence":
            assert metric.value == 1
