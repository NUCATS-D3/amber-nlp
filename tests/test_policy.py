"""Policy gate: the sensitivity x zone matrix, restrictions, decisions, and provenance stamping.

Every destination, dataset, and restriction here is invented. Case IDs refer to the contract
v0.1 conformance catalog (§7.2).
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from amber.ids import new_ulid
from amber.policy import (
    DEFAULT_ALLOWED,
    DEFAULT_MATRIX,
    DataPolicy,
    Decision,
    Destination,
    PolicyMatrix,
    PolicyViolation,
    ProviderDestination,
    Restriction,
    RunContext,
    check,
    combine,
    require,
    stamp_provenance,
)
from amber.schemas import Sensitivity, Zone

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
ALL_PAYLOADS = frozenset({"source_text", "derived_text", "labels_only", "metrics", "model_weights"})
DESTINATION_KINDS = (
    "model",
    "structured_source",
    "tracking",
    "artifact_store",
    "judge",
    "review",
    "export",
    "training",
)


def destination(
    zone: Zone = Zone.institution,
    *,
    kind: str = "model",
    accepts: frozenset[str] = ALL_PAYLOADS,
    **fields: Any,
) -> Destination:
    return Destination(
        name=f"synthetic-{kind}-{zone.value}", kind=kind, zone=zone, accepts=accepts, **fields
    )


def restriction(identifier: str = "synthetic-licence", **fields: Any) -> Restriction:
    return Restriction(
        id=identifier, kind="license", note="Invented restriction for tests.", **fields
    )


def policy(
    sensitivity: Sensitivity = Sensitivity.deidentified, *restrictions: Restriction
) -> DataPolicy:
    return DataPolicy(sensitivity=sensitivity, restrictions=restrictions)


@pytest.mark.parametrize("sensitivity", list(Sensitivity))
@pytest.mark.parametrize("zone", list(Zone))
def test_pol_01_every_cell_of_the_default_matrix(sensitivity: Sensitivity, zone: Zone) -> None:
    decision = check(DEFAULT_MATRIX, policy(sensitivity), destination(zone), "source_text", now=NOW)
    expected = zone in DEFAULT_ALLOWED[sensitivity]
    assert decision.permitted is expected
    assert decision.reason == ("ok" if expected else "zone_not_allowed")


def test_default_matrix_matches_the_contract_table() -> None:
    assert DEFAULT_ALLOWED[Sensitivity.synthetic] == frozenset(Zone)
    assert DEFAULT_ALLOWED[Sensitivity.deidentified] == {
        Zone.local,
        Zone.institution,
        Zone.external_baa,
    }
    assert DEFAULT_ALLOWED[Sensitivity.limited] == {Zone.local, Zone.institution}
    assert DEFAULT_ALLOWED[Sensitivity.phi] == {Zone.local, Zone.institution}


def test_pol_02_widening_requires_the_flag_and_a_note() -> None:
    wider = {**DEFAULT_ALLOWED, Sensitivity.phi: frozenset({Zone.local, Zone.external_baa})}
    with pytest.raises(ValidationError, match="widened_from_default"):
        PolicyMatrix(version="site@1", allowed=wider)
    with pytest.raises(ValidationError, match="note"):
        PolicyMatrix(version="site@1", allowed=wider, widened_from_default=True)
    widened = PolicyMatrix(
        version="site@1",
        allowed=wider,
        widened_from_default=True,
        note="Invented approval for a test deployment.",
    )
    decision = check(
        widened, policy(Sensitivity.phi), destination(Zone.external_baa), "source_text", now=NOW
    )
    assert decision.permitted


def test_narrowing_needs_no_flag_and_an_omitted_sensitivity_is_refused() -> None:
    narrow = PolicyMatrix(
        version="site@2", allowed={Sensitivity.deidentified: frozenset({Zone.local})}
    )
    assert check(
        narrow, policy(), destination(Zone.institution), "source_text", now=NOW
    ).reason == ("zone_not_allowed")
    assert check(
        narrow, policy(Sensitivity.phi), destination(Zone.local), "source_text", now=NOW
    ).reason == ("unknown_sensitivity")


def test_matrix_allowed_mapping_is_immutable() -> None:
    with pytest.raises(TypeError, match="cannot be mutated"):
        DEFAULT_MATRIX.allowed[Sensitivity.phi] = frozenset(Zone)  # type: ignore[index]
    with pytest.raises(ValidationError, match="frozen"):
        DEFAULT_MATRIX.version = "changed"  # type: ignore[misc]


def test_pol_03_payload_not_accepted_is_refused_in_an_allowed_zone() -> None:
    tracking = destination(
        Zone.local, kind="tracking", accepts=frozenset({"labels_only", "metrics"})
    )
    assert check(DEFAULT_MATRIX, policy(Sensitivity.phi), tracking, "metrics", now=NOW).permitted
    refused = check(DEFAULT_MATRIX, policy(Sensitivity.phi), tracking, "source_text", now=NOW)
    assert (refused.permitted, refused.reason) == (False, "payload_not_accepted")


_RULES: list[tuple[str, dict[str, Any], dict[str, Any], str]] = [
    # (case, restriction fields, destination fields, expected reason)
    ("expired", {"valid_until": NOW - timedelta(seconds=1)}, {}, "restriction_expired"),
    ("zone", {"forbids_zones": frozenset({Zone.institution})}, {}, "restriction_zone"),
    ("kind", {"forbids_kinds": frozenset({"judge"})}, {"kind": "judge"}, "restriction_kind"),
    (
        "training",
        {"forbids_training_on_inputs": True},
        {"trains_on_inputs": True},
        "restriction_training",
    ),
    ("tag", {"requires_tags": frozenset({"approved"})}, {}, "restriction_tag"),
    ("payload", {"forbids_payloads": frozenset({"source_text"})}, {}, "restriction_payload"),
]


@pytest.mark.parametrize(("case", "rule", "target", "reason"), _RULES, ids=[r[0] for r in _RULES])
def test_pol_04_each_restriction_rule_refuses_independently(
    case: str, rule: dict[str, Any], target: dict[str, Any], reason: str
) -> None:
    data = policy(Sensitivity.deidentified, restriction(f"r-{case}", **rule))
    refused = check(DEFAULT_MATRIX, data, destination(**target), "source_text", now=NOW)
    assert (refused.permitted, refused.reason, refused.restriction_id) == (
        False,
        reason,
        f"r-{case}",
    )
    satisfied = destination(
        **{**target, "trains_on_inputs": False, "tags": frozenset({"approved"})}
    )
    lenient = policy(Sensitivity.deidentified, restriction(f"r-{case}"))
    assert check(DEFAULT_MATRIX, lenient, satisfied, "source_text", now=NOW).permitted


def test_pol_05_first_failing_restriction_is_reported_in_declared_order() -> None:
    first = restriction("first", forbids_payloads=frozenset({"source_text"}))
    second = restriction("second", forbids_zones=frozenset({Zone.institution}))
    data = policy(Sensitivity.deidentified, first, second)
    assert (
        check(DEFAULT_MATRIX, data, destination(), "source_text", now=NOW).restriction_id == "first"
    )
    reordered = policy(Sensitivity.deidentified, second, first)
    assert check(
        DEFAULT_MATRIX, reordered, destination(), "source_text", now=NOW
    ).restriction_id == ("second")
    # Within one restriction, expiry is checked before any other rule.
    both = restriction(
        "both", valid_until=NOW - timedelta(days=1), forbids_zones=frozenset({Zone.institution})
    )
    assert check(
        DEFAULT_MATRIX,
        policy(Sensitivity.deidentified, both),
        destination(),
        "source_text",
        now=NOW,
    ).reason == ("restriction_expired")


def test_matrix_rules_precede_restrictions() -> None:
    data = policy(Sensitivity.phi, restriction(forbids_payloads=frozenset({"source_text"})))
    assert check(
        DEFAULT_MATRIX, data, destination(Zone.external), "source_text", now=NOW
    ).reason == ("zone_not_allowed")


_SECRET = "Invented note text that must never reach a decision"


@given(
    sensitivity=st.sampled_from(list(Sensitivity)),
    zone=st.sampled_from(list(Zone)),
    kind=st.sampled_from(DESTINATION_KINDS),
    accepts=st.frozensets(st.sampled_from(sorted(ALL_PAYLOADS))),
    payload=st.sampled_from(sorted(ALL_PAYLOADS)),
    trains=st.booleans(),
    forbids_zones=st.frozensets(st.sampled_from(list(Zone))),
    forbids_kinds=st.frozensets(st.sampled_from(DESTINATION_KINDS)),
    forbids_training=st.booleans(),
    requires_tags=st.frozensets(st.sampled_from(["a", "b"])),
    tags=st.frozensets(st.sampled_from(["a", "b"])),
    expiry_offset=st.one_of(st.none(), st.integers(min_value=-5, max_value=5)),
)
def test_pol_09_check_is_total_and_never_carries_source_text(
    sensitivity: Sensitivity,
    zone: Zone,
    kind: str,
    accepts: frozenset[str],
    payload: str,
    trains: bool,
    forbids_zones: frozenset[Zone],
    forbids_kinds: frozenset[str],
    forbids_training: bool,
    requires_tags: frozenset[str],
    tags: frozenset[str],
    expiry_offset: int | None,
) -> None:
    rule = Restriction(
        id="r",
        kind="dua",
        forbids_zones=forbids_zones,
        forbids_kinds=forbids_kinds,
        forbids_training_on_inputs=forbids_training,
        requires_tags=requires_tags,
        valid_until=None if expiry_offset is None else NOW + timedelta(days=expiry_offset),
        note=_SECRET,
    )
    target = Destination(
        name="synthetic-target",
        kind=kind,
        zone=zone,
        accepts=accepts,
        trains_on_inputs=trains,
        tags=tags,
    )
    decision = check(DEFAULT_MATRIX, policy(sensitivity, rule), target, payload, now=NOW)
    assert isinstance(decision, Decision)
    assert decision.permitted == (decision.reason == "ok")
    assert _SECRET not in decision.model_dump_json()


def test_pol_11_expiry_uses_the_supplied_clock() -> None:
    data = policy(Sensitivity.deidentified, restriction("irb-synthetic", valid_until=NOW))
    assert check(DEFAULT_MATRIX, data, destination(), "source_text", now=NOW).permitted
    later = check(
        DEFAULT_MATRIX, data, destination(), "source_text", now=NOW + timedelta(microseconds=1)
    )
    assert (later.reason, later.restriction_id) == ("restriction_expired", "irb-synthetic")
    earlier = check(DEFAULT_MATRIX, data, destination(), "source_text", now=NOW - timedelta(days=1))
    assert earlier.permitted


def test_pol_12_model_weights_follow_the_matrix_and_combined_policies() -> None:
    store = destination(Zone.external, kind="artifact_store")
    phi = policy(Sensitivity.phi)
    assert check(DEFAULT_MATRIX, phi, store, "model_weights", now=NOW).reason == "zone_not_allowed"
    synthetic = policy(Sensitivity.synthetic)
    assert check(DEFAULT_MATRIX, synthetic, store, "model_weights", now=NOW).permitted
    trained_on_both = combine([synthetic, phi])
    assert trained_on_both.sensitivity is Sensitivity.phi
    assert not check(DEFAULT_MATRIX, trained_on_both, store, "model_weights", now=NOW).permitted


def test_pol_13_a_serialized_decision_round_trips_and_detects_tampering() -> None:
    decision = check(DEFAULT_MATRIX, policy(), destination(), "derived_text", now=NOW)
    payload = json.loads(decision.model_dump_json())
    assert set(payload) == set(Decision.model_fields)
    assert Decision.model_validate(payload) == decision
    for field, forged in (("permitted", False), ("zone", "external"), ("payload", "metrics")):
        with pytest.raises(ValidationError):
            Decision.model_validate({**payload, field: forged})


def test_pol_16_training_is_a_destination_kind_distinct_from_vendor_training() -> None:
    trainer = destination(Zone.local, kind="training")
    vendor_only = policy(
        Sensitivity.deidentified, restriction("vendor-no-train", forbids_training_on_inputs=True)
    )
    assert check(DEFAULT_MATRIX, vendor_only, trainer, "source_text", now=NOW).permitted
    vendor = destination(Zone.external_baa, trains_on_inputs=True)
    assert check(DEFAULT_MATRIX, vendor_only, vendor, "source_text", now=NOW).reason == (
        "restriction_training"
    )
    no_derived_models = policy(
        Sensitivity.deidentified,
        restriction("no-derived-models", forbids_kinds=frozenset({"training"})),
    )
    refused = check(DEFAULT_MATRIX, no_derived_models, trainer, "source_text", now=NOW)
    assert (refused.reason, refused.restriction_id) == ("restriction_kind", "no-derived-models")


def test_pol_17_combine_keeps_every_obligation() -> None:
    licence = restriction("licence-a", forbids_zones=frozenset({Zone.external}))
    irb = restriction("irb-b", requires_tags=frozenset({"approved"}))
    combined = combine(
        [policy(Sensitivity.deidentified, licence), policy(Sensitivity.phi, irb, licence)]
    )
    assert combined.sensitivity is Sensitivity.phi
    assert combined.restriction_ids == ("licence-a", "irb-b")
    single = policy(Sensitivity.limited, licence)
    assert combine([single, single]) == single
    conflicting = restriction("licence-a", forbids_zones=frozenset({Zone.external_baa}))
    with pytest.raises(ValueError, match="conflicting"):
        combine(
            [
                policy(Sensitivity.deidentified, licence),
                policy(Sensitivity.deidentified, conflicting),
            ]
        )
    with pytest.raises(ValueError, match="at least one"):
        combine([])


def _context(version: str = DEFAULT_MATRIX.version) -> RunContext:
    return RunContext(
        run_id=new_ulid(),
        policy_version=version,
        data=policy(),
        dataset="synthetic-dataset",
        dataset_manifest_hash="0" * 64,
    )


def test_pol_18_decisions_carry_audit_context_and_a_stable_id() -> None:
    context = _context()
    decision = check(
        DEFAULT_MATRIX,
        policy(),
        destination(),
        "source_text",
        now=NOW,
        context=context,
        case_id="case-1",
    )
    assert (decision.dataset, decision.run_id, decision.case_id) == (
        "synthetic-dataset",
        context.run_id,
        "case-1",
    )
    assert decision.destination_kind == "model"
    again = check(
        DEFAULT_MATRIX,
        policy(),
        destination(),
        "source_text",
        now=NOW,
        context=context,
        case_id="case-1",
    )
    assert again.decision_id == decision.decision_id
    later = check(
        DEFAULT_MATRIX,
        policy(),
        destination(),
        "source_text",
        now=NOW + timedelta(seconds=1),
        context=context,
        case_id="case-1",
    )
    assert later.decision_id != decision.decision_id
    mismatch = check(
        DEFAULT_MATRIX, policy(), destination(), "source_text", now=NOW, context=_context("other@1")
    )
    assert (mismatch.permitted, mismatch.reason) == (False, "policy_version_mismatch")


def test_require_raises_policy_violation_with_the_decision() -> None:
    with pytest.raises(PolicyViolation) as caught:
        require(
            DEFAULT_MATRIX,
            policy(Sensitivity.phi),
            destination(Zone.external),
            "source_text",
            now=NOW,
        )
    assert caught.value.decision.reason == "zone_not_allowed"
    assert "zone_not_allowed" in str(caught.value)
    assert require(DEFAULT_MATRIX, policy(), destination(), "source_text", now=NOW).permitted


def test_stamp_provenance_records_the_transfer() -> None:
    data = policy(Sensitivity.deidentified, restriction("licence-a"))
    context = _context()
    decision = require(DEFAULT_MATRIX, data, destination(), "source_text", now=NOW, context=context)
    provenance = stamp_provenance(
        decision, data, producer="model:synthetic-llm@1", created_at=NOW, version="0.1.0"
    )
    assert provenance.destination == decision.destination
    assert provenance.zone is Zone.institution
    assert provenance.policy_version == DEFAULT_MATRIX.version
    assert provenance.restriction_ids == ("licence-a",)
    assert provenance.run_id == context.run_id
    refused = check(
        DEFAULT_MATRIX, policy(Sensitivity.phi), destination(Zone.external), "source_text", now=NOW
    )
    with pytest.raises(PolicyViolation):
        stamp_provenance(
            refused, policy(Sensitivity.phi), producer="tool:x", created_at=NOW, version="1"
        )
    with pytest.raises(ValueError, match="sensitivity"):
        stamp_provenance(
            decision, policy(Sensitivity.phi), producer="tool:x", created_at=NOW, version="1"
        )


def test_destination_and_policy_invariants() -> None:
    with pytest.raises(ValidationError, match="zone local"):
        Destination(name="local:quote", kind="export", zone=Zone.institution, accepts=frozenset())
    with pytest.raises(ValidationError, match="model or a structured source"):
        ProviderDestination(name="mlflow", kind="tracking", zone=Zone.local, accepts=frozenset())
    with pytest.raises(ValidationError, match="names its model"):
        ProviderDestination(name="vllm", kind="model", zone=Zone.institution, accepts=frozenset())
    provider = ProviderDestination(
        name="vllm-lab-gpu",
        kind="model",
        zone=Zone.institution,
        accepts=frozenset({"source_text"}),
        capabilities=frozenset({"schema_constrained"}),
        model="synthetic-llm",
    )
    assert check(
        DEFAULT_MATRIX, policy(Sensitivity.phi), provider, "source_text", now=NOW
    ).permitted
    assert provider.retains is True
    with pytest.raises(ValidationError, match="timezone-aware"):
        restriction(valid_until=datetime(2026, 1, 1))
    with pytest.raises(ValidationError, match="distinct"):
        policy(Sensitivity.phi, restriction("same"), restriction("same"))
    with pytest.raises(ValidationError):
        RunContext(
            run_id="not-a-ulid",
            policy_version="v",
            data=policy(),
            dataset="d",
            dataset_manifest_hash="h",
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        check(DEFAULT_MATRIX, policy(), destination(), "source_text", now=datetime(2026, 1, 1))
