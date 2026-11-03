"""The amber side of the loupe review bridge: the claim-mode ``AmberClaimsAdapter`` loupe loads
by import path, and export to and import from loupe (``amber.loupe.reference``). amber never
imports loupe. Not loaded by ``import amber``."""

from amber.loupe.adapter import ADAPTER_VERSION, SCHEMA_VERSION, AdapterError, AmberClaimsAdapter
from amber.loupe.reference import (
    ImportedReference,
    LoupeReviewBackend,
    RecordDiagnostic,
    ReferenceError,
    import_reference,
    source_manifest,
)

__all__ = [
    "ADAPTER_VERSION",
    "SCHEMA_VERSION",
    "AdapterError",
    "AmberClaimsAdapter",
    "ImportedReference",
    "LoupeReviewBackend",
    "RecordDiagnostic",
    "ReferenceError",
    "import_reference",
    "source_manifest",
]
