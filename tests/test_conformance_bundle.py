"""Bundle-level conformance: the WIR cases run against a written bundle (contract v0.1 sections
5.4 to 5.6 and 7.8)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from amber.bundles.parquet import write_bundle  # noqa: E402
from amber.conformance import fixtures as fx  # noqa: E402
from amber.conformance.bundle import (  # noqa: E402
    BUNDLE_CASES,
    manifest_content_sha256,
    run_bundle_checks,
)
from amber.conformance.report import ConformanceReport  # noqa: E402
from amber.reference import InMemoryResolver  # noqa: E402
from amber.schemas import Budget, Usage  # noqa: E402

from .test_bundles import RESOLVER, _gold, _review, _rich_result, _run, _tamper  # noqa: E402


def _write(tmp_path: Path, bundle: Any = None, **kwargs: Any) -> Path:
    path = tmp_path / "bundle"
    write_bundle(bundle if bundle is not None else _run(), path, **kwargs)
    return path


def _statuses(report: ConformanceReport) -> dict[str, tuple[str, str | None]]:
    return {case.id: (case.status, case.code) for case in report.cases if case.id in BUNDLE_CASES}


def _failures(report: ConformanceReport) -> dict[str, str | None]:
    return {case: code for case, (status, code) in _statuses(report).items() if status == "failed"}


def test_a_valid_run_bundle_passes_every_bundle_case(tmp_path: Path) -> None:
    result = _rich_result(budget=Budget(max_calls=5), usage=Usage(calls=3, tokens=12))
    path = tmp_path / "bundle"
    written = write_bundle(_run(results=[result]), path)
    report = run_bundle_checks(path, variables=fx.VARIABLES, resolver=RESOLVER)
    statuses = _statuses(report)
    assert statuses.pop("WIR-12") == ("not_implemented", None)
    assert set(statuses.values()) == {("passed", None)}
    assert report.subject.kind == "bundle"
    assert report.subject.manifest_content_sha256 == manifest_content_sha256(written)
    assert report.targets == ("SourceResolver",)
    assert report.conforms()
    assert all(
        case.status == "not_applicable" for case in report.cases if case.id not in BUNDLE_CASES
    )
    assert ConformanceReport.model_validate_json(report.model_dump_json()) == report


@pytest.mark.parametrize("make", [lambda: _gold()[0], _review])
def test_gold_and_review_bundles_pass_without_a_run_case(tmp_path: Path, make: Any) -> None:
    report = run_bundle_checks(_write(tmp_path, make()), variables=fx.VARIABLES, resolver=RESOLVER)
    assert _failures(report) == {}
    assert _statuses(report)["WIR-13"] == ("not_applicable", None)


def test_without_a_resolver_source_cases_are_not_applicable(tmp_path: Path) -> None:
    report = run_bundle_checks(_write(tmp_path), variables=fx.VARIABLES)
    statuses = _statuses(report)
    assert statuses["WIR-04"] == statuses["WIR-13"] == ("not_applicable", None)
    assert statuses["WIR-01"] == ("passed", None)
    assert report.targets == ()


def _manifest(path: Path, edit: Any) -> None:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    edit(manifest)
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _rewrite(path: Path, transform: Any) -> None:
    pq.write_table(transform(pq.read_table(path)), path)


def test_a_newer_major_version_fails_wir_02(tmp_path: Path) -> None:
    path = _write(tmp_path)
    _manifest(path, lambda manifest: manifest.update(schema_version="amber/1.0"))
    report = run_bundle_checks(path, variables=fx.VARIABLES, resolver=RESOLVER)
    failures = _failures(report)
    assert failures.pop("WIR-02") == "unsupported_version"
    assert set(failures.values()) == {"blocked"}
    assert report.subject.manifest_content_sha256 is None
    assert not report.conforms()


@pytest.mark.parametrize(
    ("transform", "case_id", "code"),
    [
        (lambda t: t.drop_columns(["claim_id"]), "WIR-03", "missing_column"),
        (
            lambda t: t.append_column("surprise", pa.array([1] * t.num_rows)),
            "WIR-09",
            "unknown_column",
        ),
    ],
)
def test_reader_diagnostics_map_to_their_case(
    tmp_path: Path, transform: Any, case_id: str, code: str
) -> None:
    path = _write(tmp_path, file_hashes=False)
    _rewrite(path / "claims.parquet", transform)
    failures = _failures(run_bundle_checks(path, variables=fx.VARIABLES, resolver=RESOLVER))
    assert failures.pop(case_id) == code


def test_a_changed_file_fails_wir_06(tmp_path: Path) -> None:
    path = _write(tmp_path)
    _rewrite(path / "claims.parquet", lambda table: table.slice(0, table.num_rows - 1))
    failures = _failures(run_bundle_checks(path, variables=fx.VARIABLES, resolver=RESOLVER))
    assert failures["WIR-06"] == "file_mismatch"


def test_a_misdeclared_payload_kind_fails_wir_11(tmp_path: Path) -> None:
    path = _write(tmp_path)
    _manifest(
        path, lambda manifest: manifest["payload_kinds"].update({"evidence.parquet": "labels_only"})
    )
    failures = _failures(run_bundle_checks(path, variables=fx.VARIABLES, resolver=RESOLVER))
    assert failures["WIR-11"] == "payload_kind_mismatch"


def test_unknown_evidence_kinds_and_null_claim_values_fail_their_cases(tmp_path: Path) -> None:
    def structured(rows: list[dict[str, Any]]) -> None:
        next(row for row in rows if row["kind"] == "inclusion")["kind"] = "structured"

    report = run_bundle_checks(
        _write(tmp_path, _tamper(_run(), "evidence", structured)),
        variables=fx.VARIABLES,
        resolver=RESOLVER,
    )
    assert _failures(report)["WIR-08"] == "unknown_evidence_kind"

    path = tmp_path / "nulled"
    write_bundle(_run(), path, file_hashes=False)

    def null_value(table: Any) -> Any:
        index = table.schema.get_field_index("value")
        values = [None, *table["value"].to_pylist()[1:]]
        return table.set_column(index, pa.field("value", pa.string()), pa.array(values))

    _rewrite(path / "claims.parquet", null_value)
    failures = _failures(run_bundle_checks(path, variables=fx.VARIABLES, resolver=RESOLVER))
    assert failures["WIR-10"] == "null_claim_value"
    assert failures["WIR-03"] == "null_value"


def test_unsorted_rows_fail_wir_01(tmp_path: Path) -> None:
    path = _write(tmp_path, file_hashes=False)
    _rewrite(path / "claims.parquet", lambda table: table.take(list(range(table.num_rows))[::-1]))
    failures = _failures(run_bundle_checks(path, variables=fx.VARIABLES, resolver=RESOLVER))
    assert failures == {"WIR-01": "rows_not_sorted"}


def test_x_columns_pass_wir_09_and_survive_the_round_trip(tmp_path: Path) -> None:
    extended = _tamper(_run(), "claims", lambda rows: [row.update(x_note=7) for row in rows])
    report = run_bundle_checks(
        _write(tmp_path, extended), variables=fx.VARIABLES, resolver=RESOLVER
    )
    assert _failures(report) == {}


def test_resolver_failures_fail_wir_04(tmp_path: Path) -> None:
    path = _write(tmp_path)
    report = run_bundle_checks(path, variables=fx.VARIABLES, resolver=InMemoryResolver())
    failures = _failures(report)
    assert failures.pop("WIR-04") == "source_unavailable"
    assert failures == {"WIR-13": "blocked"}


def test_an_unreadable_bundle_blocks_everything(tmp_path: Path) -> None:
    report = run_bundle_checks(tmp_path / "absent", variables=fx.VARIABLES)
    failures = _failures(report)
    assert failures.pop("WIR-03") == "invalid_manifest"
    assert set(failures.values()) == {"blocked"}


_BUNDLE_CONFTEST = """
from amber.conformance import BundleUnderTest
from amber.conformance import fixtures as fx
from amber.reference import InMemoryResolver


def pytest_amber_conformance_bundle(config):
    return BundleUnderTest(variables=fx.VARIABLES, resolver=InMemoryResolver([fx.SOURCE]))
"""


def test_the_plugin_checks_a_bundle_and_writes_a_bundle_report(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    path = _write(tmp_path)
    pytester.makeconftest(_BUNDLE_CONFTEST)
    result = pytester.runpytest(
        "--amber-conformance",
        f"--amber-conformance-bundle={path}",
        "--amber-conformance-report=report.json",
    )
    from amber.conformance.checks import CHECKS

    result.assert_outcomes(passed=len(BUNDLE_CASES) - 1, skipped=len(CHECKS) + 1)
    report = json.loads((pytester.path / "report.json").read_text())
    assert report["subject"]["kind"] == "bundle"
    assert {case["id"]: case["status"] for case in report["cases"]}["WIR-04"] == "passed"


def test_the_plugin_fails_bundle_cases_by_code(pytester: pytest.Pytester, tmp_path: Path) -> None:
    path = _write(tmp_path, file_hashes=False)
    _rewrite(path / "claims.parquet", lambda table: table.take(list(range(table.num_rows))[::-1]))
    pytester.makeconftest(_BUNDLE_CONFTEST)
    result = pytester.runpytest("--amber-conformance", f"--amber-conformance-bundle={path}")
    from amber.conformance.checks import CHECKS

    result.assert_outcomes(failed=1, passed=len(BUNDLE_CASES) - 2, skipped=len(CHECKS) + 1)
    result.stdout.fnmatch_lines(["*rows_not_sorted*"])


def test_bundle_mode_needs_its_hook(pytester: pytest.Pytester, tmp_path: Path) -> None:
    path = _write(tmp_path)
    result = pytester.runpytest("--amber-conformance", f"--amber-conformance-bundle={path}")
    result.stdout.fnmatch_lines(["*pytest_amber_conformance_bundle*"])
