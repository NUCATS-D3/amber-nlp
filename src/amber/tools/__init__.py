"""Deterministic, Pydantic-typed operations that implementations call to produce valid results.

These functions are part of the contract: extractors, agents, and review backends use them
rather than re-implementing grounding, commit, or outcome validation.
"""
