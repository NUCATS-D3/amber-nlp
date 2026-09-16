"""Source-safe errors raised by evidence graph validation."""

from typing import Literal, TypeAlias

GraphValidationCode: TypeAlias = Literal[
    "invalid_schema",
    "invalid_context",
    "invalid_span",
    "quote_mismatch",
    "unsupported_evidence",
    "unsupported_claim_status",
    "duplicate_reference",
    "unknown_reference",
    "missing_evidence",
    "missing_field_evidence",
    "cyclic_support",
    "source_free_support",
]


class GraphValidationError(ValueError):
    """A stable diagnostic that never renders source-bearing values."""

    def __init__(self, code: GraphValidationCode) -> None:
        self.code = code
        super().__init__(code)
