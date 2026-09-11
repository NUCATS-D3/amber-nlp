"""Canonical content-addressed identifiers and ULIDs.

Keeping serialization here prevents subtly different identifiers for the same domain node.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel
from ulid import ULID


def _canonicalize(value: Any) -> Any:
    """Convert supported values to a deterministic JSON-compatible representation."""
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical mappings must have string keys")
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite floats cannot be canonically hashed")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_sha256(fields: Mapping[str, Any]) -> str:
    """Hash named fields using canonical UTF-8 JSON."""
    payload = json.dumps(
        _canonicalize(fields),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
