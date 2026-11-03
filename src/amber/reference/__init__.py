"""Synthetic reference implementations of amber's protocols, for tests and the conformance kit.

Not loaded by ``import amber``. Real implementations live in the consuming projects.
"""

from amber.reference.store import InMemoryResolver, InMemoryStore, StoreError

__all__ = ["InMemoryResolver", "InMemoryStore", "StoreError"]
