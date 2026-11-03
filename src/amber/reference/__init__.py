"""Synthetic reference implementations of amber's protocols, for tests and the conformance kit.

Not loaded by ``import amber``. Real implementations live in the consuming projects.
"""

from amber.reference.extractor import DeterministicExtractor
from amber.reference.provider import ScriptedProvider
from amber.reference.scorers import LabelCorrectScorer, StructuralValidityScorer
from amber.reference.store import InMemoryResolver, InMemoryStore, StoreError

__all__ = [
    "DeterministicExtractor",
    "InMemoryResolver",
    "InMemoryStore",
    "LabelCorrectScorer",
    "ScriptedProvider",
    "StoreError",
    "StructuralValidityScorer",
]
