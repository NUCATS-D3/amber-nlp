"""Answer contract for the first M1 oncology task."""

from typing import Final, Literal

from pydantic import Field

from amber.schemas.answers import AnswerModel

ONCOLOGY_CURRENT_PROGRESSION_TASK: Final = "oncology_current_progression"
ONCOLOGY_CURRENT_PROGRESSION_PROTOCOL_VERSION: Final = "1.0.0"
ONCOLOGY_CURRENT_PROGRESSION_SCOPE: Final[Literal["note"]] = "note"
ONCOLOGY_CURRENT_PROGRESSION_EVIDENCE_POLICY: Final[Literal["field"]] = "field"


class OncologyCurrentProgressionAnswer(AnswerModel):
    progression_or_recurrence: bool = Field(strict=True)
