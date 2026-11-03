"""The reference implementations are for synthetic data only (amber-0z4.4).

They exist for tests and the conformance kit, are not validated for clinical use, and refuse
any case, data policy, run, or result whose sensitivity is not ``synthetic``.
"""

from __future__ import annotations

from collections.abc import Iterable

from amber.schemas import CaseResult, Sensitivity


class ReferenceUseError(ValueError):
    """A reference implementation was given non-synthetic data; the message is the code."""

    def __init__(self) -> None:
        self.code = "synthetic_only"
        super().__init__(
            "synthetic_only: amber.reference implementations are for synthetic data only; "
            "use a validated implementation for real data"
        )


def require_synthetic(*sensitivities: Sensitivity) -> None:
    if any(sensitivity is not Sensitivity.synthetic for sensitivity in sensitivities):
        raise ReferenceUseError()


def require_synthetic_results(results: Iterable[CaseResult]) -> None:
    require_synthetic(
        *(
            item.outcome.provenance.sensitivity
            for result in results
            for item in result.variable_outcomes.values()
        )
    )
