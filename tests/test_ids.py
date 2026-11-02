"""Canonical JSON (RFC 8785), identifier stability, and producer references."""

import struct
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from amber import ids
from amber.ids import (
    MAX_SAFE_INTEGER,
    ProducerRef,
    canonical_json,
    canonical_sha256,
    parse_producer,
    producer_configuration,
)
from amber.schemas import Provenance, Zone

from ._claim_helpers import SyntheticFindingAnswer, make_provenance

# IDs computed by the pre-RFC-8785 serializer. Strings and integers serialize identically under
# both, so every stored ID must be unchanged.
_REGRESSION_TEXT = 'Ação\r\n"quoted" \\ tab\t ctrl\x01 emoji 🙂 e\u0301 line\u2028sep'


def test_existing_identifiers_are_unchanged() -> None:
    assert ids.source_id(
        patient_id="synthetic-patient-1",
        kind="note",
        external_id="synthetic-note-1",
        text=_REGRESSION_TEXT,
    ) == ("db9797d228aa162eaee2c0ba89f91f1785c0ac61567b5f34193159e8c643ee7e")
    assert ids.source_id(patient_id="p", kind="lab", external_id=None, text=None) == (
        "68b5d08f6cbcbb31549b9d116ca1c127ad0b01ca444f220f70a00b16595e254e"
    )
    assert ids.mention_id(source_id="s" * 64, start=3, end=17, mention_type="tnm_t") == (
        "4ec5f72e870dbeb2cc748c11888a6f724cae721e20af2b9cee82c08b9d780299"
    )
    assert ids.inclusion_id(source_id="s" * 64, start=0, end=9) == (
        "7abe7b02dd15da73ad465e6fb49cacbec468cf8b2fe01666d5d9422b0e8b9ddf"
    )
    assert ids.structured_evidence_id(source_id="s" * 64, field="value", value="positive") == (
        "0671aa8b7e8c24200eec6be5b0172efcfd3445691a6fb07de80b3c0011b4abcd"
    )
    assert ids.structured_evidence_id(source_id="s" * 64, field="value", value=42) == (
        "b402eb1a2ae06dcd9a1b759f499ca4a5923fbcb4133265640956a6b81eb26cb1"
    )
    assert ids.schema_ref(SyntheticFindingAnswer) == (
        "tests._claim_helpers:SyntheticFindingAnswer@"
        "0abac5877bf6081ed8ca3b3839cba1efe3dcf100f9ebc26530caa916a4e87c1d"
    )
    assert canonical_sha256({"b": [1, True, None, "x"], "a": {"z": "ü", "y": -7}}) == (
        "d3382252954d82fb61596d7aab644eec675d52b4de013088ad93c0755b354042"
    )


def test_rfc8785_section_3_2_4_sample_bytes() -> None:
    sample = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
        "string": "\u20ac$\u000f\u000aA'\u0042\u0022\u005c\\\"/",
        "literals": [None, True, False],
    }
    expected = bytes.fromhex(
        "7b226c69746572616c73223a5b6e756c6c2c747275652c66616c73655d2c226e756d62657273223a"
        "5b3333333333333333332e333333333333332c31652b33302c342e352c302e3030322c31652d3237"
        "5d2c22737472696e67223a22e282ac245c75303030665c6e4127425c225c5c5c5c5c222f227d"
    )
    assert canonical_json(sample).encode("utf-8") == expected


# RFC 8785 Appendix B, plus values checked against Node.js JSON.stringify (v26).
_NUMBER_VECTORS_HEX = [
    ("0000000000000000", "0"),
    ("8000000000000000", "0"),
    ("0000000000000001", "5e-324"),
    ("8000000000000001", "-5e-324"),
    ("7fefffffffffffff", "1.7976931348623157e+308"),
    ("ffefffffffffffff", "-1.7976931348623157e+308"),
    ("4340000000000000", "9007199254740992"),
    ("c340000000000000", "-9007199254740992"),
    ("4430000000000000", "295147905179352830000"),
    ("44b52d02c7e14af5", "9.999999999999997e+22"),
    ("44b52d02c7e14af6", "1e+23"),
    ("44b52d02c7e14af7", "1.0000000000000001e+23"),
    ("444b1ae4d6e2ef4e", "999999999999999700000"),
    ("444b1ae4d6e2ef4f", "999999999999999900000"),
    ("444b1ae4d6e2ef50", "1e+21"),
    ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
    ("3eb0c6f7a0b5ed8d", "0.000001"),
    ("41b3de4355555553", "333333333.3333332"),
    ("41b3de4355555554", "333333333.33333325"),
    ("41b3de4355555555", "333333333.3333333"),
    ("41b3de4355555556", "333333333.3333334"),
    ("41b3de4355555557", "333333333.33333343"),
    ("becbf647612f3696", "-0.0000033333333333333333"),
    ("43143ff3c1cb0959", "1424953923781206.2"),
]


@pytest.mark.parametrize(("bits", "expected"), _NUMBER_VECTORS_HEX)
def test_rfc8785_number_vectors(bits: str, expected: str) -> None:
    (value,) = struct.unpack(">d", bytes.fromhex(bits))
    assert canonical_json(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "1"),
        (1.5, "1.5"),
        (100.0, "100"),
        (0.1, "0.1"),
        (1e-7, "1e-7"),
        (2.5e-7, "2.5e-7"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
        (-1e30, "-1e+30"),
        (0, "0"),
        (-7, "-7"),
        (MAX_SAFE_INTEGER, "9007199254740991"),
    ],
)
def test_numbers_use_ecmascript_form(value: float, expected: str) -> None:
    assert canonical_json(value) == expected


def test_properties_sort_by_utf16_code_units() -> None:
    # RFC 8785 §3.2.3: U+1F600 encodes as surrogates (0xD83D...) and so precedes U+FB33,
    # unlike code-point order.
    data = {
        "\u20ac": "Euro Sign",
        "\r": "Carriage Return",
        "\ufb33": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\U0001f600": "Emoji: Grinning Face",
        "\u0080": "Control",
        "\u00f6": "Latin Small Letter O With Diaeresis",
    }
    text = canonical_json(data)
    order = [
        "Carriage Return",
        "One",
        "Control",
        "Latin Small Letter O With Diaeresis",
        "Euro Sign",
        "Emoji: Grinning Face",
        "Hebrew Letter Dalet With Dagesh",
    ]
    positions = [text.index(f'"{name}"') for name in order]
    assert positions == sorted(positions)


def test_string_escaping_matches_ecmascript() -> None:
    controls = "".join(chr(code) for code in range(0x20))
    value = controls + '"\\/\x7f\u2028\u2029é🙂'
    short = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}
    expected_controls = "".join(short.get(char, f"\\u{ord(char):04x}") for char in controls)
    # Node.js JSON.stringify output for the same string.
    assert canonical_json(value) == f'"{expected_controls}\\"\\\\/\x7f\u2028\u2029é🙂"'


def test_strings_are_never_unicode_normalized() -> None:
    composed = "\u00e9"
    decomposed = "e\u0301"
    assert canonical_json(composed) != canonical_json(decomposed)
    assert canonical_sha256({"text": composed}) != canonical_sha256({"text": decomposed})


def test_booleans_are_not_numbers() -> None:
    assert canonical_json([True, 1, False, 0]) == "[true,1,false,0]"
    assert canonical_sha256({"value": True}) != canonical_sha256({"value": 1})


def test_timestamps_are_utc_with_six_fractional_digits() -> None:
    chicago = timezone(timedelta(hours=-6))
    local = datetime(2026, 10, 30, 14, 0, 0, tzinfo=chicago)
    assert canonical_json(local) == '"2026-10-30T20:00:00.000000Z"'
    assert canonical_json(datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC)) == (
        '"2026-01-02T03:04:05.000006Z"'
    )
    assert canonical_sha256({"at": local}) == canonical_sha256({"at": local.astimezone(UTC)})
    assert canonical_json(date(2026, 1, 2)) == '"2026-01-02"'


@pytest.mark.parametrize(
    ("value", "error", "message"),
    [
        (float("nan"), ValueError, "non-finite"),
        (float("inf"), ValueError, "non-finite"),
        (MAX_SAFE_INTEGER + 1, ValueError, "2\\*\\*53"),
        (-(MAX_SAFE_INTEGER + 1), ValueError, "2\\*\\*53"),
        ("lone \ud800 surrogate", ValueError, "surrogate"),
        ({"\udfff": 1}, ValueError, "surrogate"),
        (datetime(2026, 1, 2), ValueError, "timezone-aware"),
        ({1: "not canonical"}, TypeError, "string keys"),
        ({"set"}, TypeError, "unsupported"),
        (b"bytes", TypeError, "unsupported"),
    ],
)
def test_values_outside_i_json_are_rejected(
    value: Any, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        canonical_json({"value": value})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("rule:ajcc_breast_stage_group@8.0", ProducerRef("rule", "ajcc_breast_stage_group", "8.0")),
        ("rule:context@1.2+ab12", ProducerRef("rule", "context", "1.2", "ab12")),
        ("model:gpt-6-sol", ProducerRef("model", "gpt-6-sol")),
        ("model:Qwen/Qwen3-32B@2026-10", ProducerRef("model", "Qwen/Qwen3-32B", "2026-10")),
        ("model:llama3:8b", ProducerRef("model", "llama3:8b")),
        ("human:reviewer-7", ProducerRef("human", "reviewer-7")),
        ("human:r7@example.org", ProducerRef("human", "r7@example.org")),
        ("tool:quote", ProducerRef("tool", "quote")),
        ("tool:importer@0.3.0", ProducerRef("tool", "importer", "0.3.0")),
    ],
)
def test_producer_references_parse_and_round_trip(text: str, expected: ProducerRef) -> None:
    parsed = parse_producer(text)
    assert parsed == expected
    assert str(parsed) == text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ajcc_breast_stage_group@8.0",  # unprefixed
        "agent:planner@1",  # unknown prefix
        "rule:context",  # rule without @version
        "rule:context@",  # empty version
        "rule:@1.2",  # empty name
        "rule:context@1.2+",  # empty hash
        "rule:context@1.2+a+b",
        "model:",
        "model:gpt@",
        "tool:",
        "human:",
        "human:two words",
        "model:gpt 6",
        "tool:quote\t",
        "rule:ctx@1\n",
        "model:a@b@c",
        "tool:x\x00y",
    ],
)
def test_malformed_producer_references_are_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse_producer(text)


def test_rule_hash_is_ignored_only_for_configuration() -> None:
    first, second = "rule:context@1.2+h1", "rule:context@1.2+h2"
    assert parse_producer(first) != parse_producer(second)
    assert producer_configuration([first]) == producer_configuration([second])
    assert producer_configuration([first]) != producer_configuration(["rule:context@1.3+h1"])


def test_configuration_counts_only_models_and_rules() -> None:
    assert producer_configuration(
        ["human:reviewer-7", "tool:quote@1", "model:gpt-6-sol", "rule:context@1.2+h"]
    ) == frozenset({"model:gpt-6-sol", "rule:context@1.2"})
    assert not producer_configuration(["human:a"]) & producer_configuration(["human:a"])


def _provenance_payload(**updates: Any) -> dict[str, Any]:
    return {**make_provenance().model_dump(), **updates}


@pytest.mark.parametrize("missing", ["destination", "policy_version"])
def test_provenance_requires_destination_and_policy_version(missing: str) -> None:
    payload = _provenance_payload()
    del payload[missing]
    with pytest.raises(ValidationError):
        Provenance.model_validate(payload)
    with pytest.raises(ValidationError):
        Provenance.model_validate({**payload, missing: "  "})


@pytest.mark.parametrize("producer", ["synthetic", "agent:x", "rule:context", "model: gpt"])
def test_provenance_rejects_malformed_producers(producer: str) -> None:
    with pytest.raises(ValidationError):
        Provenance.model_validate(_provenance_payload(producer=producer))


def test_local_destination_requires_local_zone() -> None:
    with pytest.raises(ValidationError, match="zone local"):
        Provenance.model_validate(_provenance_payload(zone=Zone.institution))
    remote = Provenance.model_validate(
        _provenance_payload(destination="vllm-lab-gpu", zone=Zone.institution)
    )
    assert remote.zone is Zone.institution


def test_restriction_ids_are_distinct_non_blank_and_immutable() -> None:
    provenance = Provenance.model_validate(
        _provenance_payload(restriction_ids=["physionet-chdl-1.5.0", "irb-synthetic"])
    )
    assert provenance.restriction_ids == ("physionet-chdl-1.5.0", "irb-synthetic")
    assert Provenance.model_validate(provenance.model_dump()) == provenance
    for invalid in (["dup", "dup"], ["ok", " "], [""]):
        with pytest.raises(ValidationError):
            Provenance.model_validate(_provenance_payload(restriction_ids=invalid))
