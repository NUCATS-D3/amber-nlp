"""Pure contract models.

Models in this package perform no I/O and import nothing outside the amber core. Task-specific
answer models belong to the projects that define those tasks, not to amber-nlp.
"""

from amber.schemas.answers import AnswerModel
from amber.schemas.cases import Case, CaseOutcome, CaseResult
from amber.schemas.claims import Claim
from amber.schemas.evidence import EvidenceEdge, GroundingFailure, Inclusion, InferenceEvidence
from amber.schemas.mentions import Attribute, Concept, Mention, MentionReading
from amber.schemas.provenance import Provenance, Sensitivity, Zone
from amber.schemas.sources import Section, Source, SourceKind
from amber.schemas.tasks import Task

__all__ = [
    "Case",
    "CaseOutcome",
    "CaseResult",
    "GroundingFailure",
    "Inclusion",
    "InferenceEvidence",
    "AnswerModel",
    "Attribute",
    "Claim",
    "Concept",
    "EvidenceEdge",
    "Mention",
    "MentionReading",
    "Provenance",
    "Section",
    "Sensitivity",
    "Source",
    "SourceKind",
    "Task",
    "Zone",
]
