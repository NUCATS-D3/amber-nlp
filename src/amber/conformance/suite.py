"""Conformance cases collected by the amber pytest plugin (``--amber-conformance``)."""

from __future__ import annotations

import pytest

from amber.conformance.catalog import CATALOG
from amber.conformance.checks import CHECKS, Check, Subject

BUNDLE_CASES = tuple(case.id for case in CATALOG if case.level == "bundle")


@pytest.mark.parametrize(
    "amber_check", CHECKS, ids=[f"{check.case_id}[{check.target}]" for check in CHECKS]
)
def test_amber_conformance(amber_check: Check, amber_conformance_subject: Subject | None) -> None:
    if amber_conformance_subject is None:
        pytest.skip("bundle_mode")
    if amber_check.target not in amber_conformance_subject.targets:
        pytest.skip("not_applicable")
    amber_check.run(amber_conformance_subject)


@pytest.mark.parametrize("amber_bundle_case", BUNDLE_CASES)
def test_amber_bundle_conformance(
    amber_bundle_case: str, amber_conformance_bundle_statuses: dict[str, tuple[str, str | None]]
) -> None:
    if not amber_conformance_bundle_statuses:
        pytest.skip("implementation_mode")
    status, code = amber_conformance_bundle_statuses[amber_bundle_case]
    if status == "failed":
        pytest.fail(code or "failed", pytrace=False)
    if status != "passed":
        pytest.skip(status)
