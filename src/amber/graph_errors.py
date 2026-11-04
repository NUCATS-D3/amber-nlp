"""Source-safe errors raised by evidence graph validation."""

from typing import Literal, TypeAlias, get_args

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
    "unsupported_reading_status",
    "missing_reading",
    "attribute_outside_scope",
    "undeclared_dependency",
    "uncited_mention",
    "missing_attribute_evidence",
    "attribute_value_not_permitted",
    "citation_outside_section",
    "derivation_mismatch",
]


class GraphValidationError(ValueError):
    """A stable diagnostic that never renders source-bearing values."""

    def __init__(self, code: GraphValidationCode) -> None:
        # Codes are a closed, documented set; an unknown one is a programming error.
        if code not in get_args(GraphValidationCode):
            raise TypeError("unknown GraphValidationError code")
        self.code = code
        super().__init__(code)
