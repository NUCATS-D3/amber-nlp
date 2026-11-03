"""Synthetic reference implementations of amber's protocols, for tests and the conformance kit.

Not loaded by ``import amber``. Real implementations live in the consuming projects.
"""

from amber.reference.extractor import DeterministicExtractor
from amber.reference.labeler import ImportLabeler
from amber.reference.provider import ScriptedProvider
from amber.reference.review import FileReviewBackend
from amber.reference.rules import AjccStub
from amber.reference.scorers import LabelCorrectScorer, StructuralValidityScorer
from amber.reference.store import InMemoryResolver, InMemoryStore, StoreError

__all__ = [
    "AjccStub",
    "DeterministicExtractor",
    "FileReviewBackend",
    "ImportLabeler",
    "InMemoryResolver",
    "InMemoryStore",
    "LabelCorrectScorer",
    "ScriptedProvider",
    "StoreError",
    "StructuralValidityScorer",
]
