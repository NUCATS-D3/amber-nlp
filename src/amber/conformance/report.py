"""``ConformanceReport`` (contract v0.1 section 7.8) and a pytest-free runner.

A report is ``labels_only``: case IDs, statuses, and source-free codes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

import amber
from amber.conformance.catalog import CATALOG, CATALOG_VERSION, TARGETS, catalog_revision
from amber.conformance.checks import CHECKS, ConformanceFailure, Subject
from amber.conformance.fixtures import fixtures_ref
from amber.schemas._wire import SCHEMA_VERSION, SchemaVersion

CaseStatus = Literal["passed", "failed", "not_applicable", "not_implemented"]


class ReportSubject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["implementation"] = "implementation"
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class CaseReport(BaseModel):
    """One catalog case. Core cases test amber itself and are ``not_applicable`` to a
    consumer; ``not_implemented`` marks applicable cases the kit cannot check yet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    level: Literal["core", "implementation", "bundle"]
    status: CaseStatus
    code: str | None = None
    failed_targets: tuple[str, ...] = ()


class ConformanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: SchemaVersion = SCHEMA_VERSION
    subject: ReportSubject
    amber_version: str
    targets: tuple[str, ...]
    catalog_version: str
    catalog_revision: str
    fixtures_ref: str
    cases: tuple[CaseReport, ...]

    def counts(self) -> dict[CaseStatus, int]:
        counts: dict[CaseStatus, int] = {
            "passed": 0,
            "failed": 0,
            "not_applicable": 0,
            "not_implemented": 0,
        }
        for case in self.cases:
            counts[case.status] += 1
        return counts

    def conforms(self, target: str) -> bool:
        """Zero failures among the cases that apply to ``target``."""
        return not any(target in case.failed_targets for case in self.cases)


def build_report(
    *,
    name: str,
    version: str,
    targets: Sequence[str],
    outcomes: Mapping[tuple[str, str], str | None],
) -> ConformanceReport:
    """Assemble a report from per-``(case_id, target)`` outcomes: ``None`` passed, else a code.

    A pair missing from ``outcomes`` was not run.
    """
    declared = tuple(sorted(set(targets)))
    unknown = set(declared) - set(TARGETS)
    if unknown:
        raise ValueError("unknown conformance targets declared")
    implemented: dict[str, list[str]] = {}
    for check in CHECKS:
        implemented.setdefault(check.case_id, []).append(check.target)
    cases: list[CaseReport] = []
    for case in CATALOG:
        status: CaseStatus
        code: str | None = None
        failed_targets: tuple[str, ...] = ()
        applicable = [target for target in case.targets if target in declared]
        if case.level == "core":
            status = "not_applicable"
        elif case.level == "bundle":
            status = "not_implemented"
        elif not applicable:
            status = "not_applicable"
        else:
            runs = [
                (target, outcomes[(case.id, target)])
                for target in applicable
                if target in implemented.get(case.id, []) and (case.id, target) in outcomes
            ]
            failures = [(target, result) for target, result in runs if result is not None]
            if failures:
                status, code = "failed", failures[0][1]
                failed_targets = tuple(target for target, _ in failures)
            elif runs:
                status = "passed"
            else:
                status = "not_implemented"
        cases.append(
            CaseReport(
                id=case.id,
                level=case.level,
                status=status,
                code=code,
                failed_targets=failed_targets,
            )
        )
    return ConformanceReport(
        subject=ReportSubject(name=name, version=version),
        amber_version=amber.__version__,
        targets=declared,
        catalog_version=CATALOG_VERSION,
        catalog_revision=catalog_revision(),
        fixtures_ref=fixtures_ref(),
        cases=tuple(cases),
    )


def run_checks(subject: Subject) -> ConformanceReport:
    """Run every implemented check for the subject's declared targets, without pytest."""
    outcomes: dict[tuple[str, str], str | None] = {}
    for check in CHECKS:
        if check.target not in subject.targets:
            continue
        try:
            check.run(subject)
        except ConformanceFailure as failure:
            outcomes[(check.case_id, check.target)] = failure.code
        except Exception:
            outcomes[(check.case_id, check.target)] = "unexpected_error"
        else:
            outcomes[(check.case_id, check.target)] = None
    return build_report(
        name=subject.name,
        version=subject.version,
        targets=list(subject.targets),
        outcomes=outcomes,
    )
