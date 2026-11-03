"""Conformance cases collected by the amber pytest plugin (``--amber-conformance``)."""

from __future__ import annotations

import pytest

from amber.conformance.checks import CHECKS, Check, Subject


@pytest.mark.parametrize(
    "amber_check", CHECKS, ids=[f"{check.case_id}[{check.target}]" for check in CHECKS]
)
def test_amber_conformance(amber_check: Check, amber_conformance_subject: Subject) -> None:
    if amber_check.target not in amber_conformance_subject.targets:
        pytest.skip("not_applicable")
    amber_check.run(amber_conformance_subject)
