"""The shared base of records that only their minting path creates (ADR 0003, amber-8gm).

Inclusion, Mention, Claim, CaseResult, and Example check a private minting context in their
validators. ``model_copy(update=...)`` and the deprecated ``copy(update=...)`` write fields
without running validators, so they could change a minted record's offsets, quote, or values
unchecked; both are refused. Plain and deep copies keep every field and stay allowed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel


def _refuse(record: BaseModel) -> None:
    raise TypeError(
        f"{type(record).__name__} changes only through its minting path; "
        "model_copy(update=...) skips validation"
    )


class MintedRecord(BaseModel):
    """A record whose fields change only by minting a new one."""

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Copy the record; refuses ``update``, which would skip validation."""
        if update:
            _refuse(self)
        return super().model_copy(deep=deep)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        """Pydantic's deprecated copy; refuses ``update`` like ``model_copy``."""
        if update:
            _refuse(self)
        return super().copy(include=include, exclude=exclude, deep=deep)
