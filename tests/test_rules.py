"""Rules: reduction, on_missing, conflict propagation, and replay (contract v0.1 sections 3.4
and 3.6; catalog VAR-08 to VAR-11 and VAR-16)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from amber.cases import CaseValidationError, create_case_result, load_case_result
from amber.conformance import fixtures as fx
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.reference import AjccStub
from amber.rules import (
    EVIDENCE_INCLUSION,
    DerivedOutcome,
    Registry,
    RoleSpec,
    RuleRegistrationError,
    RuleResult,
    RuleSpec,
    derive,
    derive_all,
    replay_derivations,
)
from amber.schemas import (
    CaseOutcome,
    CaseResult,
    Claim,
    Inclusion,
    InferenceEvidence,
    Sensitivity,
    Variable,
    VariableOutcome,
    VariableSet,
)

CLOCK = datetime(2026, 1, 5, tzinfo=UTC)
STAMP = fx.provenance("rule:ajcc_stub@0.1", fx.RUN_IDS[0])
RULE = AjccStub()
REGISTRY = Registry([RULE])


def _derive(claims: Sequence[tuple[str, str]], rule: Any = RULE, **kwargs: Any) -> Any:
    graph = fx.stage_graph(claims)
    return graph, derive(graph, rule, provenance=STAMP, **kwargs)


def _inference(graph: EvidenceGraph, claim: Claim) -> InferenceEvidence:
    (edge,) = [edge for edge in graph.edges if edge.claim_id == claim.claim_id]
    record = graph.evidence[edge.evidence_id]
    assert isinstance(record, InferenceEvidence) and record.derivation is not None
    return record


def _claim_ids(graph: EvidenceGraph, variable: str) -> list[str]:
    return sorted(claim_id for claim_id, c in graph.claims.items() if c.variable == variable)


def _result(graph: EvidenceGraph, derived: Any) -> CaseResult:
    if isinstance(derived, Claim):
        outcome = CaseOutcome(status="answered", provenance=STAMP)
        finals = [derived.claim_id]
    else:
        outcome = CaseOutcome(
            status=derived.status,
            reason=derived.reason,
            evidence_ids=list(derived.evidence_ids),
            provenance=STAMP,
        )
        finals = []
    return create_case_result(
        fx.stage_case(),
        graph=graph,
        run_id=fx.RUN_IDS[0],
        outcome=outcome,
        final_claim_ids=finals,
    )


# Specs and registration --------------------------------------------------------------------


def test_rule_specs_require_defaults_exactly_for_default_roles() -> None:
    with pytest.raises(ValueError, match="need a default"):
        RuleSpec(
            name="x",
            version="1",
            roles={"A": RoleSpec(source="size_class", on_missing="default")},
            outputs="stage_group",
        )
    with pytest.raises(ValueError, match="need a default"):
        RuleSpec(
            name="x",
            version="1",
            roles={"A": RoleSpec(source="size_class")},
            outputs="stage_group",
            defaults={"A": "S1"},
        )
    with pytest.raises(ValueError, match="reserved"):
        RoleSpec(source="evidence:structured")


def test_rule_specs_must_fit_the_variable_set() -> None:
    RULE.spec.check(fx.STAGE_VARIABLES)
    stray = RULE.spec.model_copy(update={"outputs": "missing"})
    with pytest.raises(GraphValidationError) as failure:
        stray.check(fx.STAGE_VARIABLES)
    assert failure.value.code == "invalid_context"
    undeclared = VariableSet.create(
        name="conformance_stage",
        version="9",
        variables=tuple(
            variable.model_copy(update={"inputs": frozenset({"size_class", "node_class"})})
            if variable.final
            else variable
            for variable in fx.STAGE_VARIABLES.variables.values()
        ),
    )
    with pytest.raises(GraphValidationError) as failure:
        RULE.spec.check(undeclared)
    assert failure.value.code == "undeclared_dependency"


def test_the_registry_keys_rules_by_name_and_version() -> None:
    assert "ajcc_stub@0.1" in REGISTRY
    assert REGISTRY.get("ajcc_stub", "0.1") is RULE
    assert REGISTRY.get("ajcc_stub", "0.2") is None
    REGISTRY.register(AjccStub())
    changed = AjccStub()
    changed.spec = RULE.spec.model_copy(update={"defaults": {"M": "M1"}})
    with pytest.raises(RuleRegistrationError, match="rule_version_conflict"):
        Registry([RULE, changed])
    with pytest.raises(RuleRegistrationError, match="not_a_rule"):
        Registry([object()])  # type: ignore[list-item]


@pytest.mark.parametrize(
    "fields",
    [{}, {"value": {"stage": "x"}, "status": "not_mentioned", "reason": "r"}, {"status": "x"}],
)
def test_rule_results_are_a_value_or_a_status(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RuleResult(**fields)


# Derivation --------------------------------------------------------------------------------


def test_a_rule_derives_a_claim_recording_its_inputs_and_defaults() -> None:
    graph, claim = _derive([("size_class", "S2"), ("node_class", "N1")])
    assert isinstance(claim, Claim)
    assert claim.variable == "stage_group" and claim.value == {"stage": "stub-III"}
    record = _inference(graph, claim)
    derivation = record.derivation
    assert derivation is not None
    assert (derivation.kind, derivation.name, derivation.version) == ("rule", "ajcc_stub", "0.1")
    assert derivation.inputs == {
        "T": tuple(_claim_ids(graph, "size_class")),
        "N": tuple(_claim_ids(graph, "node_class")),
        "M": (),
    }
    assert derivation.defaults_used == {"M": "M0"}
    assert set(record.inputs) == {*derivation.inputs["T"], *derivation.inputs["N"]}
    assert graph.source_leaves(claim.claim_id)


def test_refuse_names_the_missing_role() -> None:
    """VAR-10: refuse."""
    graph, outcome = _derive([("size_class", "S1")])
    assert outcome == DerivedOutcome(
        "stage_group", "insufficient_evidence", "Rule ajcc_stub@0.1: role N has no input."
    )
    assert _claim_ids(graph, "stage_group") == []


class _Recording:
    def __init__(self, cardinality: str) -> None:
        self.spec = RuleSpec(
            name="recording",
            version="1",
            roles={
                "T": RoleSpec(source="size_class", cardinality=cardinality),  # type: ignore[arg-type]
                "N": RoleSpec(source="node_class", on_missing="pass_none"),
            },
            outputs="stage_group",
        )
        self.seen: list[Mapping[str, Any]] = []

    def apply(self, inputs: Mapping[str, Any], parameters: Mapping[str, Any]) -> RuleResult:
        self.seen.append(dict(inputs))
        return RuleResult(value={"stage": "recorded"})


def test_pass_none_hands_the_rule_none_or_an_empty_tuple() -> None:
    """VAR-10: pass_none (None for one, the empty tuple for many) and default."""
    rule = _Recording("one")
    _derive([("size_class", "S1")], rule)
    assert rule.seen[-1]["N"] is None
    many = _Recording("many")
    many.spec = many.spec.model_copy(
        update={
            "roles": {
                "T": RoleSpec(source="size_class"),
                "N": RoleSpec(source="node_class", cardinality="many", on_missing="pass_none"),
            }
        }
    )
    _derive([("size_class", "S1")], many)
    assert many.seen[-1]["N"] == ()


def test_equal_candidates_reduce_to_the_lowest_id_and_are_all_listed() -> None:
    """VAR-11: equal values."""
    rule = _Recording("one")
    graph, claim = _derive([("size_class", "S1"), ("size_class", "S1"), ("node_class", "N0")], rule)
    ids = _claim_ids(graph, "size_class")
    assert len(ids) == 2
    assert rule.seen[-1]["T"].claim_id == ids[0]
    derivation = _inference(graph, claim).derivation
    assert derivation is not None and derivation.inputs["T"] == tuple(ids)


def test_many_roles_receive_every_candidate_in_id_order() -> None:
    """VAR-11: many."""
    rule = _Recording("many")
    graph, _ = _derive([("size_class", "S2"), ("size_class", "S1"), ("node_class", "N0")], rule)
    seen = rule.seen[-1]["T"]
    assert [claim.claim_id for claim in seen] == _claim_ids(graph, "size_class")


def test_different_candidates_conflict_and_cite_their_evidence() -> None:
    """VAR-11: a conflict is never resolved by the rule and no claim is derived."""
    rule = _Recording("one")
    graph, outcome = _derive(
        [("size_class", "S1"), ("size_class", "S2"), ("node_class", "N0")], rule
    )
    assert isinstance(outcome, DerivedOutcome)
    assert outcome.status == "conflicting_evidence"
    leaves = {
        leaf.evidence_id
        for claim_id in _claim_ids(graph, "size_class")
        for leaf in graph.source_leaves(claim_id)
    }
    assert set(outcome.evidence_ids) == leaves and len(leaves) == 2
    assert rule.seen == []
    assert _claim_ids(graph, "stage_group") == []
    result = _result(graph, outcome)
    assert result.variable_outcomes["stage_group"].outcome.status == "conflicting_evidence"


def test_rejected_claims_are_never_candidates() -> None:
    """VAR-11: rejected claims (graphs hold only proposed claims, so a view stands in for a
    reviewed state)."""
    graph = fx.stage_graph([("size_class", "S1"), ("size_class", "S2"), ("node_class", "N0")])
    rejected = _claim_ids(graph, "size_class")[1]

    class Reviewed:
        variables = graph.variables
        evidence = graph.evidence
        source_leaves = graph.source_leaves
        commit_claim = graph.commit_claim
        claims = {
            claim_id: claim.model_copy(update={"status": "rejected"})
            if claim_id == rejected
            else claim
            for claim_id, claim in graph.claims.items()
        }

    claim = derive(Reviewed(), RULE, provenance=STAMP)  # type: ignore[arg-type]
    assert isinstance(claim, Claim) and claim.value == {"stage": "stub-I"}
    derivation = _inference(graph, claim).derivation
    assert derivation is not None and rejected not in derivation.inputs["T"]


def _intermediate_set() -> tuple[VariableSet, Any, Any]:
    variables = VariableSet.create(
        name="conformance_stage",
        version="2.0.0",
        variables=(
            *(
                variable.model_copy(update={"final": False})
                for variable in fx.STAGE_VARIABLES.variables.values()
            ),
            Variable(
                name="stage_summary",
                level="note",
                answer_model=fx.StageGroupAnswer,
                instructions="Invented: the stub stage restated.",
                inputs=frozenset({"stage_group"}),
                final=True,
            ),
        ),
    )

    class Restate:
        spec = RuleSpec(
            name="restate",
            version="1",
            roles={"stage": RoleSpec(source="stage_group")},
            outputs="stage_summary",
        )

        def apply(self, inputs: Mapping[str, Any], parameters: Mapping[str, Any]) -> RuleResult:
            return RuleResult(value=dict(inputs["stage"].value))

    return variables, AjccStub(), Restate()


def _graph_for(variables: VariableSet, claims: Sequence[tuple[str, str]]) -> EvidenceGraph:
    graph = EvidenceGraph(source=fx.SOURCE, variables=variables, sensitivity=Sensitivity.synthetic)
    base = fx.stage_graph(claims)
    for record in base.evidence.values():
        if isinstance(record, Inclusion):
            graph.register_evidence(record)
    for claim in sorted(base.claims.values(), key=lambda item: item.claim_id):
        (edge,) = [edge for edge in base.edges if edge.claim_id == claim.claim_id]
        inference = base.evidence[edge.evidence_id]
        assert isinstance(inference, InferenceEvidence)
        graph.commit_claim(
            claim.variable, dict(claim.value), list(inference.inputs), "Invented.", provenance=STAMP
        )
    return graph


def test_conflicts_propagate_through_intermediate_variables() -> None:
    """VAR-11: propagation, citing the original evidence."""
    variables, stub, restate = _intermediate_set()
    graph = _graph_for(
        variables, [("size_class", "S1"), ("size_class", "S2"), ("node_class", "N0")]
    )
    derived = derive_all(graph, [restate, stub], provenance=STAMP)
    assert list(derived) == ["stage_group", "stage_summary"]
    first, final = derived["stage_group"], derived["stage_summary"]
    assert isinstance(first, DerivedOutcome) and isinstance(final, DerivedOutcome)
    assert final.status == "conflicting_evidence"
    assert final.evidence_ids == first.evidence_ids and len(final.evidence_ids) == 2

    clean = _graph_for(variables, [("size_class", "S1"), ("node_class", "N0")])
    chained = derive_all(clean, [restate, stub], provenance=STAMP)
    assert isinstance(chained["stage_summary"], Claim)
    assert chained["stage_summary"].value == {"stage": "stub-I"}


def test_rule_statuses_and_evidence_roles() -> None:
    _, outcome = _derive([("size_class", "S9"), ("node_class", "N0")])
    assert isinstance(outcome, DerivedOutcome) and outcome.status == "insufficient_evidence"

    class FromSpan:
        spec = RuleSpec(
            name="from_span",
            version="1",
            roles={"span": RoleSpec(source=EVIDENCE_INCLUSION)},
            outputs="stage_group",
        )

        def apply(self, inputs: Mapping[str, Any], parameters: Mapping[str, Any]) -> RuleResult:
            return RuleResult(value={"stage": inputs["span"].quote})

    graph = fx.stage_graph([])
    span = next(item for item in graph.evidence.values() if isinstance(item, Inclusion))
    claim = derive(graph, FromSpan(), provenance=STAMP, evidence={"span": [span.evidence_id]})
    assert isinstance(claim, Claim) and claim.value == {"stage": span.quote}
    with pytest.raises(GraphValidationError):
        derive(graph, FromSpan(), provenance=STAMP, evidence={"span": ["unknown"]})


# Replay ------------------------------------------------------------------------------------


def _replayable() -> tuple[CaseResult, dict[str, Any]]:
    graph, claim = _derive([("size_class", "S2"), ("node_class", "N1")], parameters={"suffix": "x"})
    result = _result(graph, claim)
    return result, json.loads(result.model_dump_json())


def _load(payload: dict[str, Any]) -> CaseResult:
    return load_case_result(
        payload, case=fx.stage_case(), source=fx.SOURCE, variables=fx.STAGE_VARIABLES
    )


def _tamper(payload: dict[str, Any], edit: Any) -> CaseResult:
    for record in payload["evidence"]:
        if record.get("derivation"):
            edit(record)
    return _load(payload)


def test_registered_rules_replay_and_unregistered_rules_do_not() -> None:
    """VAR-09."""
    result, payload = _replayable()
    loaded = load_case_result(
        payload, case=fx.stage_case(), source=fx.SOURCE, variables=fx.STAGE_VARIABLES
    )
    assert loaded.claims[-1].value == {"stage": "stub-IIIx"}
    (replayed,) = replay_derivations(loaded, registry=REGISTRY, variables=fx.STAGE_VARIABLES)
    assert isinstance(
        next(e for e in loaded.evidence if e.evidence_id == replayed), InferenceEvidence
    )
    assert replay_derivations(loaded, registry=Registry(), variables=fx.STAGE_VARIABLES) == ()


def _mismatch(result: CaseResult) -> None:
    with pytest.raises(GraphValidationError) as failure:
        replay_derivations(result, registry=REGISTRY, variables=fx.STAGE_VARIABLES)
    assert failure.value.code == "derivation_mismatch"


def test_a_tampered_value_fails_replay() -> None:
    """VAR-09."""
    _, payload = _replayable()
    for claim in payload["claims"]:
        if claim["variable"] == "stage_group":
            claim["value"] = {"stage": "stub-I"}
    _mismatch(_load(payload))


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r["derivation"]["parameters"].update(suffix="y"),
        lambda r: r["derivation"]["defaults_used"].update(M="M1"),
        lambda r: r["derivation"].update(defaults_used={}),
        lambda r: r["derivation"]["inputs"].update(X=[]),
        lambda r: r["derivation"]["inputs"].update(T=r["derivation"]["inputs"]["N"]),
    ],
    ids=["parameters", "default_value", "dropped_default", "extra_role", "wrong_variable"],
)
def test_tampered_derivations_fail_replay(edit: Any) -> None:
    """VAR-09 and VAR-16: roles, defaults, and inputs must match the registered spec."""
    _, payload = _replayable()
    _mismatch(_tamper(payload, edit))


def test_a_default_is_only_valid_where_the_spec_defaults() -> None:
    """VAR-16: a registered role whose on_missing is not default may not be defaulted."""
    graph, claim = _derive([("size_class", "S2"), ("node_class", "N1"), ("spread_class", "M0")])
    payload = json.loads(_result(graph, claim).model_dump_json())

    def default_n(record: dict[str, Any]) -> None:
        derivation = record["derivation"]
        derivation["inputs"]["N"] = []
        derivation["defaults_used"] = {"N": "N1"}

    _mismatch(_tamper(payload, default_n))


def test_replay_supplies_the_recorded_default_and_hides_defaults_used() -> None:
    """VAR-16."""
    seen: list[Mapping[str, Any]] = []

    class Spy(AjccStub):
        def apply(self, inputs: Mapping[str, Any], parameters: Mapping[str, Any]) -> RuleResult:
            seen.append(dict(inputs))
            assert "defaults_used" not in parameters
            return super().apply(inputs, parameters)

    result, _ = _replayable()
    replay_derivations(result, registry=Registry([Spy()]), variables=fx.STAGE_VARIABLES)
    assert seen[-1]["M"] == "M0"


def test_an_unregistered_rule_keeps_its_defaults_unchanged() -> None:
    """VAR-16."""
    result, payload = _replayable()
    again = json.loads(
        load_case_result(
            payload, case=fx.stage_case(), source=fx.SOURCE, variables=fx.STAGE_VARIABLES
        ).model_dump_json()
    )
    assert again == payload
    (record,) = [item for item in again["evidence"] if item.get("derivation")]
    assert record["derivation"]["defaults_used"] == {"M": "M0"}


def test_derivation_inputs_outside_the_inference_fail_to_load() -> None:
    """VAR-08 at the result boundary."""
    _, payload = _replayable()
    for record in payload["evidence"]:
        if record.get("derivation"):
            record["derivation"]["inputs"]["N"] = [fx.fixed_ulid(77, 7)]
    with pytest.raises((CaseValidationError, GraphValidationError)) as failure:
        load_case_result(
            payload, case=fx.stage_case(), source=fx.SOURCE, variables=fx.STAGE_VARIABLES
        )
    assert failure.value.code == "invalid_schema"


def test_variable_outcome_for_a_derived_answer() -> None:
    result, _ = _replayable()
    assert result.variable_outcomes["stage_group"] == VariableOutcome(
        variable="stage_group",
        outcome=result.variable_outcomes["stage_group"].outcome,
        final_claim_ids=(result.claims[-1].claim_id,),
    )


def test_derivations_survive_events_examples_and_bundles() -> None:
    from amber.bundles import (
        BundleContents,
        ManifestDataset,
        ManifestProducer,
        build_bundle,
        load_bundle,
    )
    from amber.labeling import import_events
    from amber.reference import InMemoryResolver

    result, _ = _replayable()
    _, _, example = import_events(
        result,
        case=fx.stage_case(),
        source=fx.SOURCE,
        variables=fx.STAGE_VARIABLES,
        actor=fx.IMPORTER,
        provenance=fx.provenance(fx.IMPORTER, fx.RUN_IDS[0]),
        now=CLOCK,
    )
    assert replay_derivations(example, registry=REGISTRY, variables=fx.STAGE_VARIABLES)

    bundle = build_bundle(
        kind="run",
        variables=fx.STAGE_VARIABLES,
        dataset=ManifestDataset(
            name=fx.DATASET, manifest_hash="rules-test", sensitivity=Sensitivity.synthetic
        ),
        producer=ManifestProducer(name="rules-test", version="0", run_id=fx.RUN_IDS[0]),
        policy_version="rules-test-policy@1",
        created_at=CLOCK,
        sources=[fx.SOURCE],
        cases=[fx.stage_case()],
        results=[result],
    )
    loaded = load_bundle(
        bundle, variables=fx.STAGE_VARIABLES, resolver=InMemoryResolver([fx.SOURCE])
    )
    assert isinstance(loaded, BundleContents)
    assert loaded.results == (result,)
    assert replay_derivations(loaded.results[0], registry=REGISTRY, variables=fx.STAGE_VARIABLES)
