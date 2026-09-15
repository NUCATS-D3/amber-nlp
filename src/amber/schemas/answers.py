"""Base class for task-specific answer value objects."""

from pydantic import BaseModel, ConfigDict


class AnswerModel(BaseModel):
    """Frozen, closed base for values stored in task claims."""

    model_config = ConfigDict(extra="forbid", frozen=True)
