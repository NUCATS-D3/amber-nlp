"""Pure contract models.

Models in this package perform no I/O and import nothing outside the amber core. Task-specific
answer models belong to the projects that define those tasks, not to amber-nlp.
"""

from amber.schemas.answers import AnswerModel
from amber.schemas.cases import Case, CaseOutcome, CaseResult, VariableOutcome
from amber.schemas.claims import Claim
from amber.schemas.evidence import EvidenceEdge, GroundingFailure, Inclusion, InferenceEvidence
from amber.schemas.examples import Example, LabelRecord, ReferenceTier, VariableReference
from amber.schemas.mentions import Attribute, Concept, Mention, MentionReading
from amber.schemas.provenance import Provenance, Sensitivity, Zone
from amber.schemas.sources import Section, Source, SourceKind
from amber.schemas.tasks import Task
from amber.schemas.variables import AttributeRequirement, Variable, VariableSet

__all__ = [
    "Case",
    "CaseOutcome",
    "CaseResult",
    "GroundingFailure",
    "Inclusion",
    "InferenceEvidence",
    "AnswerModel",
    "AttributeRequirement",
    "Attribute",
    "Claim",
    "Concept",
    "EvidenceEdge",
    "Example",
    "LabelRecord",
    "Mention",
    "MentionReading",
    "Provenance",
    "ReferenceTier",
    "Section",
    "Sensitivity",
    "Source",
    "SourceKind",
    "Task",
    "Variable",
    "VariableOutcome",
    "VariableReference",
    "VariableSet",
    "Zone",
]
