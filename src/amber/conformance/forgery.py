"""Forge tampered records for adversarial checks (amber-8gm).

Minted records refuse ``model_copy(update=...)`` (ADR 0003), but the conformance kit and amber's
tests must still prove that load and commit boundaries reject a record someone tampered with in
memory. ``forge`` makes such a record the way an attacker could: a deep copy whose fields are
set without validation. It is for adversarial checks only; nothing that produces results may
call it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def forge[Record: BaseModel](record: Record, **changes: Any) -> Record:
    """A deep copy of ``record`` with ``changes`` written past validation."""
    fields = type(record).model_fields
    unknown = sorted(set(changes) - set(fields))
    if unknown:
        raise AttributeError(f"{type(record).__name__} has no field {', '.join(unknown)}")
    forged = record.model_copy(deep=True)
    for name, value in changes.items():
        object.__setattr__(forged, name, value)
        forged.__pydantic_fields_set__.add(name)
    return forged
