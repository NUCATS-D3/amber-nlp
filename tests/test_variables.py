"""Adversarial tests for variable sets, the Task sugar, and multi-variable note graphs
(contract v0.1 sections 3.2 and 3.3)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import Field, ValidationError

from amber.cases import create_case_result, load_case_result
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.grounding import exact_quote
from amber.ids import new_ulid, schema_ref
from amber.schemas import (
    AnswerModel,
    AttributeRequirement,
    Case,
    CaseOutcome,
    Inclusion,
    InferenceEvidence,
    Sensitivity,
    Task,
    Variable,
    VariableSet,
)
from amber.schemas.variables import TASK_SET_VERSION, variable_set_ref

from ._claim_helpers import (
    SyntheticFindingAnswer,
    make_inference_record,
    make_provenance,
    make_source,
    make_task,
)


class SyntheticGradeAnswer(AnswerModel):
    grade: int = Field(strict=True, ge=1, le=3)


class SyntheticStageAnswer(AnswerModel):
    stage: str = Field(strict=True)


def _assert_error(code: str, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(GraphValidationError) as failure:
        function(*args, **kwargs)
    assert failure.value.code == code


def _variable(name: str, **changes: Any) -> Variable:
    fields: dict[str, Any] = {
        "name": name,
        "level": "note",
        "answer_model": SyntheticGradeAnswer,
        "instructions": f"Invented instructions for {name}.",
        **changes,
    }
    return Variable(**fields)


def _set(*variables: Variable, name: str = "synthetic_set", version: str = "1.0.0") -> VariableSet:
    return VariableSet.create(name=name, version=version, variables=variables)


def _staging_set(stage_inputs: frozenset[str] = frozenset({"grade"})) -> VariableSet:
    return _set(
        _variable("grade", evidence_policy="field"),
        _variable("stage", answer_model=SyntheticStageAnswer, inputs=stage_inputs, final=True),
        _variable("other", answer_model=SyntheticStageAnswer),
    )


# Variable and set validation (VAR-2) -----------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": " grade"},
        {"level": "case"},
        {"instructions": None},
        {"inputs": frozenset({"grade"})},
        {"inputs": frozenset({""})},
        {"level": "mention"},
        {"mention_types": frozenset({"tnm_t"})},
        {"requires_attributes": (AttributeRequirement(category="negation"),)},
        {
            "level": "mention",
            "mention_types": frozenset({"tnm_t"}),
            "requires_attributes": (
                AttributeRequirement(category="negation"),
                AttributeRequirement(category="negation", evidence="lenient"),
            ),
        },
        {"final": 1},
        {"answer_model": dict},
        {"extra": "field"},
    ],
)
def test_invalid_variables_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises((ValidationError, TypeError)):
        _variable("grade", **changes)


@pytest.mark.parametrize(
    "variables",
    [
        (),
        (_variable("grade"),),
        (_variable("a", inputs=frozenset({"missing"}), final=True),),
        (
            _variable("a", inputs=frozenset({"b"}), final=True),
            _variable("b", inputs=frozenset({"c"})),
            _variable("c", inputs=frozenset({"a"})),
        ),
        (
            _variable("note_grade", final=True),
            _variable(
                "mention_grade",
                level="mention",
                mention_types=frozenset({"grade"}),
                inputs=frozenset({"note_grade"}),
            ),
        ),
    ],
)
def test_invalid_variable_sets_are_rejected(variables: tuple[Variable, ...]) -> None:
    with pytest.raises(ValidationError):
        _set(*variables)


def test_variable_sets_are_keyed_by_name_and_frozen() -> None:
    grade = _variable("grade", final=True)
    with pytest.raises(ValidationError, match="keyed by their names"):
        VariableSet(
            name="s",
            version="1",
            variables={"other": grade},
            schema_ref=variable_set_ref("s", (grade,)),
        )
    variables = _set(grade)
    with pytest.raises(TypeError):
        variables.variables["new"] = grade  # type: ignore[index]
    with pytest.raises(ValidationError):
        variables.version = "2"  # type: ignore[misc]


def test_attribute_requirement_values_keep_strict_types() -> None:
    requirement = AttributeRequirement(category="negation", values=(1, True, "1"))
    assert len(requirement.values or ()) == 3
    assert AttributeRequirement(category="x", values=("b", "a")).values == ("a", "b")
    for values in [(), (1, 1), (None,), (1.5,), ([1],)]:
        with pytest.raises(ValidationError):
            AttributeRequirement(category="negation", values=values)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AttributeRequirement(category=" negation")


def test_mention_level_variables_with_requirements_are_constructible() -> None:
    variable = _variable(
        "finding",
        level="mention",
        mention_types=frozenset({"finding"}),
        requires_attributes=(
            AttributeRequirement(category="negation", values=(True, False)),
            AttributeRequirement(category="temporality", evidence="lenient"),
        ),
        requires_section=frozenset({"findings"}),
        final=True,
    )
    assert _set(variable).variables["finding"].requires_attributes[0].category == "negation"


# Identity (VAR-15) -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"answer_model": SyntheticStageAnswer},
        {"instructions": "Changed instructions."},
        {"evidence_policy": "field"},
        {"requires_section": frozenset({"findings"})},
        {"final": False},
    ],
)
def test_schema_ref_changes_with_any_variable_field(changes: dict[str, Any]) -> None:
    base = _set(_variable("grade", final=True), _variable("stage", inputs=frozenset({"grade"})))
    changed_grade = _variable("grade", **{"final": True, **changes})
    changed = _set(changed_grade, _variable("stage", inputs=frozenset({"grade"}), final=True))
    assert changed.schema_ref != base.schema_ref


def test_schema_ref_changes_with_inputs_and_name_but_not_version() -> None:
    grade = _variable("grade", final=True)
    stage = _variable("stage")
    base = _set(grade, stage)
    assert _set(grade, stage, version="2.0.0").schema_ref == base.schema_ref
    assert _set(grade, stage, name="renamed").schema_ref != base.schema_ref
    assert (
        _set(grade, _variable("stage", inputs=frozenset({"grade"}))).schema_ref != base.schema_ref
    )
    assert _set(stage, grade).schema_ref == base.schema_ref


def test_schema_ref_must_match_content() -> None:
    variables = _set(_variable("grade", final=True))
    with pytest.raises(ValidationError, match="schema_ref"):
        VariableSet.model_validate({**variables.model_dump(), "schema_ref": "0" * 64})
    with pytest.raises(ValidationError, match="schema_ref"):
        VariableSet.model_validate({**variables.model_dump(), "name": "renamed"})
    assert VariableSet.model_validate({**variables.model_dump(), "version": "9"}).version == "9"


def test_task_is_a_one_variable_set() -> None:
    task = make_task()
    variables = VariableSet.from_task(task)

    assert variables.name == task.name
    assert variables.version == TASK_SET_VERSION
    variable = variables.variables[task.name]
    assert (variable.level, variable.final, variable.evidence_policy) == ("note", True, "field")
    assert variables.task() == task
    assert _staging_set().task() is None
    assert _set(_variable("synthetic_set", final=True)).task() is not None


def test_patient_level_is_constructible_but_graphs_reject_it() -> None:
    patient_task = Task(
        name="synthetic_patient",
        answer_model=SyntheticGradeAnswer,
        instructions="Invented.",
        scope="patient",
    )
    assert VariableSet.from_task(patient_task).variables["synthetic_patient"].level == "patient"
    source = make_source()
    _assert_error(
        "invalid_context",
        EvidenceGraph,
        source=source,
        task=patient_task,
        sensitivity=Sensitivity.synthetic,
    )
    patient_set = _set(_variable("p", level="patient", final=True))
    _assert_error(
        "invalid_context",
        EvidenceGraph,
        source=source,
        variables=patient_set,
        sensitivity=Sensitivity.synthetic,
    )


def test_graph_needs_exactly_one_of_task_or_variables() -> None:
    source = make_source()
    task = make_task()
    _assert_error(
        "invalid_context", EvidenceGraph, source=source, sensitivity=Sensitivity.synthetic
    )
    _assert_error(
        "invalid_context",
        EvidenceGraph,
        source=source,
        task=task,
        variables=VariableSet.from_task(task),
        sensitivity=Sensitivity.synthetic,
    )
    _assert_error(
        "invalid_context",
        EvidenceGraph,
        source=source,
        variables=task,
        sensitivity=Sensitivity.synthetic,
    )


def test_task_and_its_variable_set_build_the_same_graph() -> None:
    source = make_source("Invented finding stated.")
    task = make_task()
    by_task = EvidenceGraph(source=source, task=task, sensitivity=Sensitivity.synthetic)
    by_set = EvidenceGraph(
        source=source, variables=VariableSet.from_task(task), sensitivity=Sensitivity.synthetic
    )
    for graph in (by_task, by_set):
        leaf = exact_quote(source, "Invented finding stated.")
        assert isinstance(leaf, Inclusion)
        graph.register_evidence(leaf)
        claim = graph.commit_claim(
            task,
            {"finding_present": True},
            [leaf.evidence_id],
            "Invented rationale.",
            {"finding_present": [leaf.evidence_id]},
            provenance=make_provenance(),
        )
        assert (claim.task, claim.variable) == (task.name, task.name)
        assert claim.schema_ref == schema_ref(SyntheticFindingAnswer)
    assert by_task.task == by_set.task == task
    assert by_task.to_payload()["context"] == by_set.to_payload()["context"]


# Multi-variable note graphs (VAR-3, VAR-7) -----------------------------------------------------

TEXT = "Grade 2 noted.\r\nStage II \U0001f642 assigned."


def _multi_graph(
    variables: VariableSet | None = None,
) -> tuple[EvidenceGraph, Inclusion, Inclusion]:
    source = make_source(TEXT)
    graph = EvidenceGraph(
        source=source,
        variables=_staging_set() if variables is None else variables,
        sensitivity=Sensitivity.synthetic,
    )
    grade_leaf = exact_quote(source, "Grade 2 noted.")
    stage_leaf = exact_quote(source, "Stage II \U0001f642 assigned.")
    assert isinstance(grade_leaf, Inclusion) and isinstance(stage_leaf, Inclusion)
    graph.register_evidence(grade_leaf)
    graph.register_evidence(stage_leaf)
    return graph, grade_leaf, stage_leaf


def _bridge(graph: EvidenceGraph, *inputs: str) -> str:
    bridge = InferenceEvidence.model_validate(make_inference_record(inputs=list(inputs)))
    graph.register_evidence(bridge)
    return bridge.evidence_id


def _commit_grade(graph: EvidenceGraph, leaf: Inclusion) -> str:
    return graph.commit_claim(
        "grade",
        {"grade": 2},
        [leaf.evidence_id],
        "Invented rationale.",
        {"grade": [leaf.evidence_id]},
        provenance=make_provenance(),
    ).claim_id


def test_claims_validate_against_their_own_variable() -> None:
    graph, grade_leaf, stage_leaf = _multi_graph()
    grade_id = _commit_grade(graph, grade_leaf)
    stage = graph.commit_claim(
        "stage",
        {"stage": "II"},
        [stage_leaf.evidence_id, _bridge(graph, grade_id, stage_leaf.evidence_id)],
        "Invented derivation.",
        provenance=make_provenance(),
    )

    claims = graph.claims
    assert claims[grade_id].variable == "grade"
    assert claims[grade_id].schema_ref == schema_ref(SyntheticGradeAnswer)
    assert stage.variable == "stage"
    assert stage.schema_ref == schema_ref(SyntheticStageAnswer)
    assert {claim.task for claim in claims.values()} == {"synthetic_set"}
    _assert_error("invalid_context", getattr, graph, "task")
    assert graph.variables == _staging_set()


@pytest.mark.parametrize(
    ("variable", "value", "field_roles", "code"),
    [
        ("missing", {"grade": 2}, None, "invalid_context"),
        ("stage", {"grade": 2}, None, "invalid_schema"),
        ("grade", {"grade": "2"}, None, "invalid_schema"),
        ("grade", {"grade": 2}, None, "missing_field_evidence"),
        ("stage", {"stage": "II"}, {"grade": None}, "invalid_context"),
    ],
)
def test_commit_checks_the_named_variable(
    variable: str, value: dict[str, Any], field_roles: Any, code: str
) -> None:
    graph, grade_leaf, _ = _multi_graph()
    roles = None if field_roles is None else {"grade": [grade_leaf.evidence_id]}
    _assert_error(
        code,
        graph.commit_claim,
        variable,
        value,
        [grade_leaf.evidence_id],
        "Invented rationale.",
        roles,
        provenance=make_provenance(),
    )
    assert graph.claims == {}


def test_commit_rejects_a_task_that_is_not_the_graph_set() -> None:
    graph, grade_leaf, _ = _multi_graph()
    _assert_error(
        "invalid_context",
        graph.commit_claim,
        make_task(),
        {"finding_present": True},
        [grade_leaf.evidence_id],
        "Invented rationale.",
        provenance=make_provenance(),
    )


def _claim_payload(graph: EvidenceGraph, **changes: Any) -> dict[str, Any]:
    payload = graph.to_payload()
    claim = dict(payload["claims"][0])
    claim.update(changes)
    payload["claims"] = [claim]
    return payload


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"variable": "missing"}, "invalid_schema"),
        ({"variable": "other"}, "invalid_context"),
        ({"variable": "other", "schema_ref": schema_ref(SyntheticStageAnswer)}, "invalid_schema"),
        ({"task": "other_set"}, "invalid_context"),
        ({"schema_ref": schema_ref(SyntheticStageAnswer)}, "invalid_context"),
        ({"value": {"grade": 9}}, "invalid_schema"),
    ],
)
def test_restored_claims_must_match_a_variable_in_the_set(
    changes: dict[str, Any], code: str
) -> None:
    graph, grade_leaf, _ = _multi_graph()
    _commit_grade(graph, grade_leaf)
    _assert_error(
        code,
        EvidenceGraph.from_payload,
        _claim_payload(graph, **changes),
        source=graph.source,
        variables=graph.variables,
        sensitivity=Sensitivity.synthetic,
    )


def test_a_claim_without_a_variable_is_rejected() -> None:
    graph, grade_leaf, _ = _multi_graph()
    _commit_grade(graph, grade_leaf)
    payload = _claim_payload(graph)
    payload["claims"][0].pop("variable")
    _assert_error(
        "invalid_schema",
        EvidenceGraph.from_payload,
        payload,
        source=graph.source,
        variables=graph.variables,
        sensitivity=Sensitivity.synthetic,
    )


def test_inference_citing_an_undeclared_variable_fails() -> None:
    graph, grade_leaf, stage_leaf = _multi_graph()
    grade_id = _commit_grade(graph, grade_leaf)
    bridge = _bridge(graph, grade_id, stage_leaf.evidence_id)
    _assert_error(
        "undeclared_dependency",
        graph.commit_claim,
        "other",
        {"stage": "II"},
        [stage_leaf.evidence_id, bridge],
        "Invented derivation.",
        provenance=make_provenance(),
    )
    assert set(graph.claims) == {grade_id}
    assert bridge in graph.evidence


def test_undeclared_dependencies_are_found_through_nested_inferences() -> None:
    graph, grade_leaf, stage_leaf = _multi_graph()
    grade_id = _commit_grade(graph, grade_leaf)
    payload = graph.to_payload()
    inner = make_inference_record(inputs=[grade_id, grade_leaf.evidence_id])
    outer = make_inference_record(inputs=[inner["evidence_id"], stage_leaf.evidence_id])
    claim = {
        **payload["claims"][0],
        "claim_id": new_ulid(),
        "variable": "other",
        "schema_ref": schema_ref(SyntheticStageAnswer),
        "value": {"stage": "II"},
    }
    payload["claims"].append(claim)
    payload["evidence"].extend([inner, outer])
    payload["edges"].append(
        {"claim_id": claim["claim_id"], "evidence_id": outer["evidence_id"], "role": None}
    )
    restore = dict(source=graph.source, sensitivity=Sensitivity.synthetic)
    _assert_error(
        "undeclared_dependency",
        EvidenceGraph.from_payload,
        payload,
        variables=graph.variables,
        **restore,
    )
    declared = _set(
        _variable("grade", evidence_policy="field"),
        _variable("stage", answer_model=SyntheticStageAnswer, inputs=frozenset({"grade"})),
        _variable(
            "other", answer_model=SyntheticStageAnswer, inputs=frozenset({"grade"}), final=True
        ),
    )
    payload["context"]["variable_set_ref"] = declared.schema_ref
    restored = EvidenceGraph.from_payload(payload, variables=declared, **restore)
    assert restored.claims[claim["claim_id"]].variable == "other"


def test_same_variable_supporting_claims_need_no_declaration() -> None:
    graph, grade_leaf, stage_leaf = _multi_graph()
    first = _commit_grade(graph, grade_leaf)
    bridge = _bridge(graph, first, stage_leaf.evidence_id)
    second = graph.commit_claim(
        "grade",
        {"grade": 2},
        [bridge],
        "Invented support chain.",
        {"grade": [bridge]},
        provenance=make_provenance(),
    )
    assert second.variable == "grade"


def test_rejected_claims_are_not_dependency_checked() -> None:
    graph, grade_leaf, stage_leaf = _multi_graph()
    grade_id = _commit_grade(graph, grade_leaf)
    payload = graph.to_payload()
    inference = make_inference_record(inputs=[grade_id, stage_leaf.evidence_id])
    claim = {
        **payload["claims"][0],
        "claim_id": new_ulid(),
        "variable": "other",
        "schema_ref": schema_ref(SyntheticStageAnswer),
        "value": {"stage": "II"},
        "status": "rejected",
    }
    payload["claims"].append(claim)
    payload["evidence"].append(inference)
    payload["edges"].append(
        {"claim_id": claim["claim_id"], "evidence_id": inference["evidence_id"], "role": None}
    )
    restored = EvidenceGraph.from_payload(
        payload, source=graph.source, variables=graph.variables, sensitivity=Sensitivity.synthetic
    )
    assert restored.claims[claim["claim_id"]].status == "rejected"


def test_multi_variable_snapshots_round_trip_and_bind_the_set() -> None:
    graph, grade_leaf, _ = _multi_graph()
    _commit_grade(graph, grade_leaf)
    payload = json.loads(json.dumps(graph.to_payload()))
    assert payload["format_version"] == 3
    assert payload["context"]["variable_set_ref"] == _staging_set().schema_ref

    restored = EvidenceGraph.from_payload(
        payload, source=graph.source, variables=_staging_set(), sensitivity=Sensitivity.synthetic
    )
    assert restored.to_payload() == graph.to_payload()

    other = _staging_set(stage_inputs=frozenset())
    _assert_error(
        "invalid_context",
        EvidenceGraph.from_payload,
        payload,
        source=graph.source,
        variables=other,
        sensitivity=Sensitivity.synthetic,
    )


# Case results ----------------------------------------------------------------------------------


def test_case_results_accept_a_task_or_its_variable_set() -> None:
    source = make_source("Invented finding stated.")
    task = make_task()
    graph = EvidenceGraph(source=source, task=task, sensitivity=Sensitivity.synthetic)
    case = Case(
        case_id="synthetic-case-1",
        task=task.name,
        patient_id=source.patient_id,
        source_ids=[source.source_id],
        sensitivity=Sensitivity.synthetic,
    )
    outcome = CaseOutcome(
        status="not_mentioned",
        reason="Synthetic complete review.",
        reviewed_source_ids=[source.source_id],
        provenance=make_provenance(),
    )
    result = create_case_result(case, graph=graph, outcome=outcome, final_claim_ids=[])
    payload = json.loads(result.model_dump_json())

    by_task = load_case_result(payload, case=case, source=source, task=task)
    by_set = load_case_result(
        payload, case=case, source=source, variables=VariableSet.from_task(task)
    )
    assert by_task == by_set == result
