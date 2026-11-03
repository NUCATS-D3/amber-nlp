"""Bundle-level conformance (contract v0.1 sections 5.4 to 5.6 and 7.8): run the ``WIR`` bundle
cases against a written ``amber-bundle/0.1`` directory, its variable set, and optionally a
``SourceResolver``. Needs the ``bundles`` extra.

Each case checks a property of the bundle under test:

- WIR-01: a write and read gives every table the same content hash and identical rows, and
  rows are stored sorted by key.
- WIR-02 (version), WIR-03 (tables, columns, types, keys), WIR-05 and WIR-11 (payload kinds),
  WIR-06 (content and file hashes, case scope), WIR-07 (canonical JSON), and WIR-09 (only
  ``x_`` extra columns, which survive the round trip): the reader's and ``check_bundle``'s
  diagnostics, mapped to the case they belong to.
- WIR-08: every evidence ``kind`` and event ``action`` is known. WIR-10: no claim row has a
  null ``value``.
- WIR-04 (needs a resolver): every record revalidates against the resolved sources; any load
  failure is reported here with the boundary's code, except ``budget_exceeded``.
- WIR-13 (run bundles with a resolver): the revalidated results rebuild into identical tables,
  and over-budget rows say so.

A failed bundle case has no ``failed_targets``: the bundle, not a target, failed it. A case
left undecided because an earlier stage failed is ``failed`` with code ``blocked``.
WIR-12 (migration) has no check and reports ``not_implemented``. The report's subject is
``{kind: "bundle", manifest_content_sha256}``, the SHA-256 of the manifest's canonical JSON.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, get_args

from amber.annotations import AnnotationValidationError
from amber.bundles import (
    Bundle,
    BundleContents,
    BundleError,
    build_bundle,
    check_bundle,
    content_sha256,
    load_bundle,
)
from amber.bundles.core import sorted_rows
from amber.cases import CaseValidationError
from amber.conformance.catalog import CATALOG
from amber.conformance.report import (
    BundleSubject,
    CaseReport,
    CaseStatus,
    ConformanceReport,
    report_header,
)
from amber.examples import ExampleValidationError
from amber.graph_errors import GraphValidationError
from amber.ids import canonical_sha256
from amber.interfaces import SourceResolver
from amber.schemas import VariableSet
from amber.schemas.annotations import EventAction

try:
    from amber.bundles.parquet import read_bundle, write_bundle
except ImportError as error:  # pragma: no cover - exercised by the core-install check
    raise ImportError("bundle conformance reads Parquet bundles; install amber[bundles]") from error

BUNDLE_CASES = tuple(case.id for case in CATALOG if case.level == "bundle")
_READ_AND_CHECK: Mapping[str, str] = {
    "unsupported_version": "WIR-02",
    "invalid_manifest": "WIR-03",
    "missing_table": "WIR-03",
    "unexpected_table": "WIR-03",
    "missing_column": "WIR-03",
    "wrong_type": "WIR-03",
    "null_value": "WIR-03",
    "duplicate_key": "WIR-03",
    "invalid_bundle": "WIR-03",
    "derived_text_in_labels": "WIR-05",
    "payload_kind_mismatch": "WIR-11",
    "content_mismatch": "WIR-06",
    "file_mismatch": "WIR-06",
    "case_mismatch": "WIR-06",
    "variable_set_mismatch": "WIR-06",
    "run_mismatch": "WIR-13",
    "non_canonical_json": "WIR-07",
    "unknown_column": "WIR-09",
}
_STRUCTURAL = ("WIR-02", "WIR-03", "WIR-05", "WIR-06", "WIR-07", "WIR-09", "WIR-11")
_EVIDENCE_KINDS = frozenset({"inclusion", "inference"})
_ACTIONS = frozenset(get_args(EventAction))
_LOAD_ERRORS = (
    BundleError,
    CaseValidationError,
    GraphValidationError,
    ExampleValidationError,
    AnnotationValidationError,
)


def manifest_content_sha256(bundle: Bundle) -> str:
    return canonical_sha256(bundle.manifest.model_dump(mode="json"))


def _without_extras(rows: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [{key: value for key, value in row.items() if not key.startswith("x_")} for row in rows]


def _round_trip(bundle: Bundle) -> str | None:
    if any(rows != sorted_rows(name, rows) for name, rows in bundle.tables.items()):
        return "rows_not_sorted"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "copy"
        write_bundle(bundle, path)
        copy = read_bundle(path)
    if set(copy.tables) != set(bundle.tables):
        return "round_trip_changed"
    for name, rows in bundle.tables.items():
        if copy.tables[name] != rows or content_sha256(name, copy.tables[name]) != (
            bundle.manifest.tables[name].content_sha256
        ):
            return "round_trip_changed"
    return None


def _rows_check(bundle: Bundle) -> dict[str, str | None]:
    kinds = {row.get("kind") for row in bundle.tables.get("evidence", ())}
    actions = {row.get("action") for row in bundle.tables.get("annotation_events", ())}
    unknown = (
        "unknown_evidence_kind"
        if kinds - _EVIDENCE_KINDS
        else "unknown_event_action"
        if actions - _ACTIONS
        else None
    )
    null_value = any(row.get("value") is None for row in bundle.tables.get("claims", ()))
    return {"WIR-08": unknown, "WIR-10": "null_claim_value" if null_value else None}


def _rebuild(bundle: Bundle, contents: BundleContents, variables: VariableSet) -> str | None:
    manifest = bundle.manifest
    rebuilt = build_bundle(
        kind="run",
        variables=variables,
        dataset=manifest.dataset,
        producer=manifest.producer,
        policy_version=manifest.policy_version,
        created_at=manifest.created_at,
        sources=contents.sources,
        cases=contents.cases,
        results=contents.results,
        spans=contents.spans,
        decisions=contents.decisions,
    )
    for name, rows in bundle.tables.items():
        rebuilt_rows = rebuilt.tables.get(name, ())
        if content_sha256(name, tuple(_without_extras(rows))) != content_sha256(name, rebuilt_rows):
            return "results_changed_on_reload"
    return None


def evaluate_bundle(
    directory: Path, *, variables: VariableSet, resolver: SourceResolver | None = None
) -> tuple[str | None, dict[str, CaseStatus], dict[str, str]]:
    """Decide every bundle case: ``(manifest_content_sha256, statuses, codes)``."""
    statuses: dict[str, CaseStatus] = {"WIR-12": "not_implemented"}
    codes: dict[str, str] = {}

    def decide(case_id: str, code: str | None) -> None:
        if case_id in statuses:
            return
        statuses[case_id] = "passed" if code is None else "failed"
        if code is not None:
            codes[case_id] = code

    def block_rest() -> None:
        for case_id in BUNDLE_CASES:
            decide(case_id, "blocked")

    try:
        bundle = read_bundle(directory)
    except BundleError as error:
        decide(_READ_AND_CHECK.get(error.code, "WIR-03"), error.code)
        block_rest()
        return None, statuses, codes
    for case_id, code in _rows_check(bundle).items():
        decide(case_id, code)
    try:
        check_bundle(bundle, variables=variables)
    except BundleError as error:
        decide(_READ_AND_CHECK.get(error.code, "WIR-03"), error.code)
        block_rest()
        return manifest_content_sha256(bundle), statuses, codes
    for case_id in _STRUCTURAL:
        decide(case_id, None)
    decide("WIR-01", _round_trip(bundle))

    if resolver is None:
        statuses.setdefault("WIR-04", "not_applicable")
        statuses.setdefault("WIR-13", "not_applicable")
        return manifest_content_sha256(bundle), statuses, codes
    try:
        contents = load_bundle(bundle, variables=variables, resolver=resolver)
    except _LOAD_ERRORS as error:
        decide("WIR-13" if error.code == "budget_exceeded" else "WIR-04", str(error.code))
        block_rest()
        return manifest_content_sha256(bundle), statuses, codes
    decide("WIR-04", None)
    if bundle.manifest.bundle_kind != "run" or not isinstance(contents, BundleContents):
        statuses.setdefault("WIR-13", "not_applicable")
    else:
        decide("WIR-13", _rebuild(bundle, contents, variables))
    return manifest_content_sha256(bundle), statuses, codes


def run_bundle_checks(
    directory: Path, *, variables: VariableSet, resolver: SourceResolver | None = None
) -> ConformanceReport:
    """Run the bundle cases and report them; every other case is ``not_applicable``."""
    digest, statuses, codes = evaluate_bundle(directory, variables=variables, resolver=resolver)
    targets = ("SourceResolver",) if resolver is not None else ()
    cases = []
    for case in CATALOG:
        status: CaseStatus = statuses.get(case.id, "not_applicable")
        cases.append(
            CaseReport(
                id=case.id,
                level=case.level,
                status=status,
                code=codes.get(case.id),
            )
        )
    return ConformanceReport(
        subject=BundleSubject(manifest_content_sha256=digest),
        targets=targets,
        cases=tuple(cases),
        **report_header(),
    )


__all__ = ["BUNDLE_CASES", "evaluate_bundle", "manifest_content_sha256", "run_bundle_checks"]
