"""Canonical content-addressed identifiers, producer references, and ULIDs.

Keeping serialization here prevents subtly different identifiers for the same domain node.
Canonical JSON follows RFC 8785 (JSON Canonicalization Scheme) so that implementations in
other languages reproduce every content-addressed ID exactly.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel
from ulid import ULID

# RFC 8785 numbers are IEEE 754 doubles; larger integers must travel as strings (Appendix D).
MAX_SAFE_INTEGER = 2**53 - 1


def _check_string(value: str) -> str:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("canonical strings must not contain lone surrogates")
    return value


def _utf16_key(key: str) -> bytes:
    return key.encode("utf-16-be")


def _format_number(value: int | float) -> str:
    """Serialize a number exactly as ECMAScript's Number::toString (RFC 8785 §3.2.2.3)."""
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("canonical integers must be within ±(2**53 - 1); use a string")
        return str(value)
    if not math.isfinite(value):
        raise ValueError("non-finite floats cannot be canonically hashed")
    if value == 0:
        return "0"
    # repr gives the shortest digits that round-trip, which is what ECMAScript requires.
    sign, digit_tuple, exponent = Decimal(repr(abs(value))).as_tuple()
    digits = "".join(map(str, digit_tuple)).rstrip("0")
    assert isinstance(exponent, int)
    exponent += len(digit_tuple) - len(digits)
    count = len(digits)
    point = count + exponent  # the value is 0.<digits> * 10**point
    prefix = "-" if value < 0 else ""
    if count <= point <= 21:
        return prefix + digits + "0" * (point - count)
    if 0 < point <= 21:
        return prefix + digits[:point] + "." + digits[point:]
    if -6 < point <= 0:
        return prefix + "0." + "0" * -point + digits
    power = point - 1
    mantissa = digits if count == 1 else digits[0] + "." + digits[1:]
    return f"{prefix}{mantissa}e{'+' if power > 0 else '-'}{abs(power)}"


def _canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("canonical timestamps must be timezone-aware")
    utc = value.astimezone(UTC).replace(tzinfo=None)
    return utc.isoformat(timespec="microseconds") + "Z"


def _serialize(value: Any, out: list[str]) -> None:
    if isinstance(value, BaseModel):
        _serialize(value.model_dump(mode="python"), out)
    elif isinstance(value, Enum):
        _serialize(value.value, out)
    elif value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, (int, float)):
        out.append(_format_number(value))
    elif isinstance(value, str):
        # json.dumps escapes exactly the RFC 8785 set: '"', '\\', and U+0000-U+001F (short
        # forms for \b \t \n \f \r, lowercase \u00hh otherwise); everything else is literal.
        out.append(json.dumps(_check_string(value), ensure_ascii=False))
    elif isinstance(value, datetime):
        out.append(json.dumps(_canonical_timestamp(value)))
    elif isinstance(value, date):
        out.append(json.dumps(value.isoformat()))
    elif isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical mappings must have string keys")
        out.append("{")
        for index, key in enumerate(sorted(value, key=_utf16_key)):
            if index:
                out.append(",")
            out.append(json.dumps(_check_string(key), ensure_ascii=False))
            out.append(":")
            _serialize(value[key], out)
        out.append("}")
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _serialize(item, out)
        out.append("]")
    else:
        raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return the RFC 8785 canonical JSON text of a value.

    Timestamps must be timezone-aware and are written in UTC with six fractional digits and
    ``Z``. Strings are never Unicode-normalized, because they may be source text.
    """
    out: list[str] = []
    _serialize(value, out)
    return "".join(out)


def canonical_sha256(fields: Mapping[str, Any]) -> str:
    """Hash named fields using canonical UTF-8 JSON (RFC 8785)."""
    return hashlib.sha256(canonical_json(fields).encode("utf-8")).hexdigest()


ProducerKind = Literal["rule", "model", "human", "tool"]

_SEGMENT = r"[^\s\x00-\x1f\x7f@+]+"
_PRODUCER_PATTERNS: dict[str, re.Pattern[str]] = {
    "rule": re.compile(
        rf"rule:(?P<name>{_SEGMENT})@(?P<version>{_SEGMENT})(?:\+(?P<hash>{_SEGMENT}))?"
    ),
    "model": re.compile(rf"model:(?P<name>{_SEGMENT})(?:@(?P<version>{_SEGMENT}))?"),
    "tool": re.compile(rf"tool:(?P<name>{_SEGMENT})(?:@(?P<version>{_SEGMENT}))?"),
    "human": re.compile(r"human:(?P<name>[^\s\x00-\x1f\x7f]+)"),
}


@dataclass(frozen=True)
class ProducerRef:
    """A parsed producer reference (contract v0.1 §1.6)."""

    kind: ProducerKind
    name: str
    version: str | None = None
    hash: str | None = None

    def __str__(self) -> str:
        text = f"{self.kind}:{self.name}"
        if self.version is not None:
            text += f"@{self.version}"
        if self.hash is not None:
            text += f"+{self.hash}"
        return text

    @property
    def configuration_key(self) -> str | None:
        """The identity used by ``self_reference``: rule hash removed; humans and tools excluded."""
        if self.kind == "rule":
            return f"rule:{self.name}@{self.version}"
        if self.kind == "model":
            return str(self)
        return None


def parse_producer(value: str) -> ProducerRef:
    """Parse ``rule:<name>@<version>[+<hash>]``, ``model:<name>[@<version>]``,
    ``human:<user>``, or ``tool:<name>[@<version>]``; raise ``ValueError`` otherwise."""
    if not isinstance(value, str):
        raise ValueError("producer reference must be a string")
    kind, separator, _ = value.partition(":")
    pattern = _PRODUCER_PATTERNS.get(kind)
    if not separator or pattern is None:
        raise ValueError("producer reference must start with rule:, model:, human:, or tool:")
    match = pattern.fullmatch(value)
    if match is None:
        raise ValueError(f"malformed {kind}: producer reference")
    groups = match.groupdict()
    return ProducerRef(
        kind=kind,  # type: ignore[arg-type]
        name=groups["name"],
        version=groups.get("version"),
        hash=groups.get("hash"),
    )


def producer_configuration(producers: Iterable[str]) -> frozenset[str]:
    """Return the ``model:``/``rule:`` configuration keys of producer references.

    Two configurations overlap, for the scorer's ``self_reference`` refusal, when these sets
    intersect; rule ``+<hash>`` suffixes are ignored and ``human:``/``tool:`` references
    never count.
    """
    keys = (parse_producer(producer).configuration_key for producer in producers)
    return frozenset(key for key in keys if key is not None)


def source_id(*, patient_id: str, kind: str, external_id: str | None, text: str | None) -> str:
    return canonical_sha256(
        {
            "external_id": external_id,
            "kind": kind,
            "patient_id": patient_id,
            "text": text,
        }
    )


def mention_id(*, source_id: str, start: int, end: int, mention_type: str) -> str:
    return canonical_sha256(
        {
            "end": end,
            "mention_type": mention_type,
            "source_id": source_id,
            "start": start,
        }
    )


def inclusion_id(*, source_id: str, start: int, end: int) -> str:
    return canonical_sha256({"end": end, "source_id": source_id, "start": start})


def structured_evidence_id(*, source_id: str, field: str, value: Any) -> str:
    return canonical_sha256({"field": field, "source_id": source_id, "value": value})


def new_ulid() -> str:
    """Return a lexicographically sortable identifier for non-deterministic nodes."""
    return str(ULID())


def schema_ref(answer_model: type[BaseModel]) -> str:
    """Return the qualified answer-model name and canonical JSON-schema digest."""
    digest = canonical_sha256(answer_model.model_json_schema())
    return f"{answer_model.__module__}:{answer_model.__qualname__}@{digest}"


def validate_ulid(value: str) -> str:
    """Accept a ULID only when its spelling is already canonical."""
    parsed = ULID.from_str(value)
    if str(parsed) != value:
        raise ValueError("ULID must use its canonical string representation")
    return value
