"""Wire-format primitives shared by every record (contract v0.1 section 5.2)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer

SCHEMA_VERSION = "amber/0.1"
_MAJOR, _MINOR = 0, 1
_VERSION = re.compile(r"amber/(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def check_schema_version(value: object) -> str:
    """Accept this major at a lower or equal minor and return the current version.

    Readers reject a different major and a newer minor, and never guess.
    """
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("schema_version must be amber/<major>.<minor>")
    major, minor = int(match.group(1)), int(match.group(2))
    if major != _MAJOR or minor > _MINOR:
        raise ValueError("unsupported schema_version")
    return SCHEMA_VERSION


def json_timestamp(value: datetime) -> str:
    """RFC 3339 in UTC with ``Z`` and exactly six fractional digits; naive times keep their
    ISO form, since no zone can be assumed."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.isoformat(timespec="microseconds")
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


SchemaVersion = Annotated[str, BeforeValidator(check_schema_version)]
Timestamp = Annotated[datetime, PlainSerializer(json_timestamp, return_type=str, when_used="json")]
