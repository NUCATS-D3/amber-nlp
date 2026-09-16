"""Task definitions that bind instructions to an answer schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from amber.schemas.answers import AnswerModel


class Task(BaseModel):
    """An immutable extraction task definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(strict=True)
    answer_model: type[AnswerModel]
    instructions: str = Field(strict=True)
    evidence_policy: Literal["claim", "field"] = "claim"
    scope: Literal["note", "patient"] = "note"

    @field_validator("name")
    @classmethod
    def require_nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task name must not be blank")
        return value
