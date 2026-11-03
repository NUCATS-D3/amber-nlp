"""The conformance kit: catalog, fixtures, store checks, report, and pytest plugin."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from amber.conformance import Subject
from amber.conformance import fixtures as fx
from amber.conformance.catalog import CASES, CATALOG, CATALOG_VERSION, catalog_revision
from amber.conformance.checks import CHECKS
from amber.conformance.report import build_report, run_checks
from amber.policy import ProviderDestination
from amber.providers import ProviderFailure, ProviderRequest, ProviderResponse
from amber.reference import (
    DeterministicExtractor,
    InMemoryResolver,
    InMemoryStore,
    LabelCorrectScorer,
    ScriptedProvider,
)
from amber.schemas import Example, Source, Zone
from amber.scoring import Metric

DESIGN = Path(__file__).parents[1] / "docs" / "design" / "contract-v0.1.md"


def _resolver(sources: Iterable[Source]) -> InMemoryResolver:
    return InMemoryResolver(sources)


def _answer(request: ProviderRequest) -> ProviderResponse:
    if request.json_schema is not None:
        return ProviderResponse(parsed={"present": True}, input_tokens=5, output_tokens=3)
    return ProviderResponse(text="Invented answer.", input_tokens=5, output_tokens=3)


def _destination(*capabilities: str) -> ProviderDestination:
    return ProviderDestination(
        name="conformance-model",
        kind="model",
        zone=Zone.local,
        accepts=frozenset({"source_text"}),
        model="synthetic-model",
        capabilities=frozenset(capabilities),  # type: ignore[arg-type]
    )


def _provider(*capabilities: str) -> Any:
    return lambda: ScriptedProvider(_destination(*capabilities), [_answer] * 10)


def _extractor(provider: Any) -> DeterministicExtractor:
    return DeterministicExtractor(provider, clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))


def _subject(
    store: type[InMemoryStore] = InMemoryStore, provider: Any = None, extractor: Any = None
) -> Subject:
    return Subject(
        name="amber-reference",
        version="0.0.0",
        targets={
            "Store": store,
            "SourceResolver": _resolver,
            "Provider": provider or _provider("schema_constrained", "logprobs"),
            "Extractor": extractor or _extractor,
            "Scorer": LabelCorrectScorer,
        },
    )


def _statuses(report: Any) -> dict[str, tuple[str, str | None]]:
    return {case.id: (case.status, case.code) for case in report.cases}


# Catalog and fixtures ----------------------------------------------------------------------


def test_catalog_matches_the_design_document() -> None:
    text = DESIGN.read_text(encoding="utf-8")
    section = text[text.index("## 7. Conformance catalog") : text.index("### 7.8 Reporting")]
    rows = re.findall(r"^\| `([A-Z]{3}-\d\d)` \| (\w+) \| ([^|]*) \| (.*) \|$", section, re.M)
    expected = [
        (
            case_id,
            level,
            () if applies.strip() == "—" else tuple(t.strip() for t in applies.split(",")),
            body,
        )
        for case_id, level, applies, body in rows
    ]
    assert [(c.id, c.level, c.targets, c.text) for c in CATALOG] == expected
    assert len(CATALOG) == 101
    assert len(CASES) == 101
    assert CATALOG_VERSION == "0.1"


def test_catalog_revision_and_fixtures_ref_are_stable_content_hashes() -> None:
    assert catalog_revision() == catalog_revision()
    assert re.fullmatch(r"[0-9a-f]{64}", catalog_revision())
    assert fx.fixtures_ref.__wrapped__() == fx.fixtures_ref.__wrapped__()  # type: ignore[attr-defined]
    log, _ = fx.adjudicated()
    again, _ = fx.adjudicated()
    assert log.events == again.events


def test_every_check_names_a_catalog_case_for_its_target() -> None:
    assert {(check.case_id, check.target) for check in CHECKS} == {
        ("ANN-05", "Store"),
        ("ANN-16", "Store"),
        ("ANN-17", "Store"),
        ("IFC-07", "Store"),
        ("IFC-11", "Store"),
        ("IFC-12", "Store"),
        ("IFC-04", "Provider"),
        ("IFC-12", "Provider"),
        ("IFC-02", "Extractor"),
        ("IFC-13", "Extractor"),
        ("IFC-09", "Scorer"),
        ("ANN-11", "Scorer"),
        ("ANN-12", "Scorer"),
        ("ANN-17", "Scorer"),
    }


# The reference passes ----------------------------------------------------------------------


def test_the_references_pass_every_implemented_case() -> None:
    report = run_checks(_subject())
    statuses = _statuses(report)

    for check in CHECKS:
        if check.case_id != "IFC-12":
            assert statuses[check.case_id] == ("passed", None)
    for target in ("Store", "Provider", "Extractor", "Scorer", "SourceResolver"):
        assert report.conforms(target)
    assert statuses["ANN-06"] == ("not_applicable", None)
    assert statuses["WIR-01"] == ("not_implemented", None)
    assert statuses["WIR-04"] == ("not_implemented", None)
    assert report.counts() == {
        "passed": 11,
        "failed": 0,
        "not_applicable": 101 - 11 - 18,
        "not_implemented": 18,
    }
    assert report.targets == ("Extractor", "Provider", "Scorer", "SourceResolver", "Store")
    # IFC-12's Extractor part has no check yet, so the shared case is never reported passed.
    assert statuses["IFC-12"] == ("not_implemented", None)
    assert statuses["IFC-03"] == ("not_implemented", None)
    assert report.catalog_revision == catalog_revision()
    assert report.fixtures_ref == fx.fixtures_ref()


def test_undeclared_targets_are_not_applicable_never_passed() -> None:
    report = run_checks(Subject(name="nothing", version="1", targets={}))
    assert report.counts()["passed"] == 0
    assert _statuses(report)["IFC-11"] == ("not_applicable", None)


def test_declared_targets_without_checks_are_not_implemented() -> None:
    report = build_report(name="x", version="1", targets=["Scorer"], outcomes={})
    assert _statuses(report)["ANN-12"] == ("not_implemented", None)
    with pytest.raises(ValueError, match="unknown"):
        build_report(name="x", version="1", targets=["Database"], outcomes={})


# Broken stores fail with source-free codes -------------------------------------------------


class _NoSplitChecks(InMemoryStore):
    def put_case(self, case: Any, *, variables: Any) -> None:
        self._put_variable_set(variables)
        self._cases[case.case_id] = (case, variables.schema_ref)


class _LeaksStreams(InMemoryStore):
    def project(self, case_id: str, *, actor: Any, **kwargs: Any) -> Any:
        return super().project(case_id, actor=None, **kwargs)


class _OverwritesRuns(InMemoryStore):
    def put_result(self, result: Any) -> None:
        self._results.setdefault(result.case_id, {})[result.run_id] = result.model_dump(mode="json")


class _TrustsTiers(InMemoryStore):
    def put_example(self, example: Example, *, resolver: Any) -> None:
        log = self._events.get(example.case_id, [])
        if example.as_of_event not in {event.event_id for event in log}:
            raise ValueError("events_required")
        self._examples[example.example_id] = example.model_dump(mode="json")


class _FirstExampleForever(InMemoryStore):
    def current_example(self, case_id: str, *, resolver: Any) -> Any:
        stored = list(self._examples)
        return self.get_example(stored[0], resolver=resolver) if stored else None


class _RewritesEvents(InMemoryStore):
    def append_events(self, events: Any, *, spans: Any = ()) -> None:
        log = self._events.setdefault(events[0].case_id, []) if events else []
        known = {event.event_id: index for index, event in enumerate(log)}
        fresh = [event for event in events if event.event_id not in known]
        for event in events:
            if event.event_id in known:
                log[known[event.event_id]] = event
        if fresh:
            super().append_events(fresh, spans=spans)
        elif spans:
            super().append_events([], spans=spans)


class _HidesCapabilities(InMemoryStore):
    capabilities = frozenset({"events"})  # type: ignore[assignment]


@pytest.mark.parametrize(
    ("store", "case_id", "code"),
    [
        (_NoSplitChecks, "ANN-05", "second_split_accepted"),
        (_LeaksStreams, "IFC-07", "stream_leak"),
        (_LeaksStreams, "ANN-16", "projection_mismatch"),
        (_OverwritesRuns, "IFC-11", "duplicate_result_accepted"),
        (_TrustsTiers, "IFC-07", "unsupported_tier_accepted"),
        (_FirstExampleForever, "ANN-17", "current_example_stale"),
        (_RewritesEvents, "IFC-07", "event_update_accepted"),
        (_HidesCapabilities, "IFC-12", "undeclared_capability_served"),
    ],
)
def test_broken_stores_fail_with_source_free_codes(
    store: type[InMemoryStore], case_id: str, code: str
) -> None:
    report = run_checks(_subject(store))
    assert _statuses(report)[case_id] == ("failed", code)
    assert not report.conforms("Store")
    serialized = report.model_dump_json()
    for fragment in ("mass", "liver", "vidence", fx.SOURCE.text[:8]):
        assert fragment not in serialized


# pytest plugin -----------------------------------------------------------------------------

_CONFTEST = """
from amber.conformance import Subject
from amber.reference import InMemoryResolver, InMemoryStore
{extra}

def pytest_amber_conformance_subject(config):
    return Subject(
        name="synthetic-consumer",
        version="1.2.3",
        targets={{"Store": {store}, "SourceResolver": InMemoryResolver}},
    )
"""

_BROKEN = """
class BrokenStore(InMemoryStore):
    def project(self, case_id, *, actor, **kwargs):
        return super().project(case_id, actor=None, **kwargs)
"""


def test_the_plugin_runs_the_kit_and_writes_a_report(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(_CONFTEST.format(extra="", store="InMemoryStore"))
    result = pytester.runpytest("--amber-conformance", "--amber-conformance-report=report.json")
    stores = sum(check.target == "Store" for check in CHECKS)
    result.assert_outcomes(passed=stores, skipped=len(CHECKS) - stores)
    report = json.loads((pytester.path / "report.json").read_text())
    assert report["subject"] == {
        "kind": "implementation",
        "name": "synthetic-consumer",
        "version": "1.2.3",
    }
    assert {case["id"]: case["status"] for case in report["cases"]}["IFC-11"] == "passed"
    assert report["catalog_revision"] == catalog_revision()


def test_the_plugin_reports_failures_by_code(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(_CONFTEST.format(extra=_BROKEN, store="BrokenStore"))
    result = pytester.runpytest("--amber-conformance", "--amber-conformance-report=report.json")
    stores = sum(check.target == "Store" for check in CHECKS)
    result.assert_outcomes(passed=stores - 2, failed=2, skipped=len(CHECKS) - stores)
    cases = {
        case["id"]: case
        for case in json.loads((pytester.path / "report.json").read_text())["cases"]
    }
    assert (cases["ANN-16"]["status"], cases["ANN-16"]["code"]) == ("failed", "projection_mismatch")
    assert (cases["IFC-07"]["status"], cases["IFC-07"]["code"]) == ("failed", "stream_leak")


def test_the_plugin_is_inert_without_its_option(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("def test_ordinary():\n    assert True\n")
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_the_plugin_needs_a_declared_subject(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest("--amber-conformance")
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*pytest_amber_conformance_subject*"])


class _IgnoresCapabilities(ScriptedProvider):
    def complete(self, request: ProviderRequest) -> ProviderResponse | ProviderFailure:
        return ProviderResponse(text="Unconstrained text.")


class _RaisingProvider(ScriptedProvider):
    def complete(self, request: ProviderRequest) -> ProviderResponse | ProviderFailure:
        raise RuntimeError("transport down")


@pytest.mark.parametrize(
    ("provider", "case_id", "code"),
    [
        (lambda: _IgnoresCapabilities(_destination(), []), "IFC-04", "schema_request_served"),
        (
            lambda: _IgnoresCapabilities(_destination(), []),
            "IFC-12",
            "undeclared_capability_served",
        ),
        (
            lambda: _IgnoresCapabilities(_destination("schema_constrained"), []),
            "IFC-12",
            "declared_capability_failed",
        ),
        (lambda: _RaisingProvider(_destination(), []), "IFC-04", "provider_raised"),
        (lambda: object(), "IFC-12", "not_a_provider"),
    ],
)
def test_broken_providers_fail_with_source_free_codes(
    provider: Any, case_id: str, code: str
) -> None:
    report = run_checks(_subject(provider=provider))
    assert _statuses(report)[case_id] == ("failed", code)
    assert not report.conforms("Provider")
    assert report.conforms("Store")


class _RaisesOnFailure(DeterministicExtractor):
    def run(self, case: Any, source: Any, variables: Any, **kwargs: Any) -> Any:
        result = super().run(case, source, variables, **kwargs)
        if any(item.outcome.status == "failed" for item in result.variable_outcomes.values()):
            raise RuntimeError("failure")
        return result


class _HidesFailures(DeterministicExtractor):
    def _failed(self, name: str, kind: Any, reason: str, context: Any) -> Any:
        return super()._failed(name, "internal", reason, context)


class _UndeclaredDestinations(DeterministicExtractor):
    def __init__(self, provider: Any, **kwargs: Any) -> None:
        super().__init__(provider, **kwargs)
        self.destinations = frozenset()


class _ReversedBatch(DeterministicExtractor):
    def run_many(self, items: Any, variables: Any, **kwargs: Any) -> Any:
        return reversed(list(super().run_many(items, variables, **kwargs)))


@pytest.mark.parametrize(
    ("extractor", "case_id", "code"),
    [
        (_RaisesOnFailure, "IFC-02", "extractor_raised"),
        (_HidesFailures, "IFC-02", "provider_failure_not_reported"),
        (_UndeclaredDestinations, "IFC-02", "undeclared_destination"),
        (_ReversedBatch, "IFC-13", "batch_order"),
        (lambda provider: object(), "IFC-02", "not_an_extractor"),
    ],
)
def test_broken_extractors_fail_with_source_free_codes(
    extractor: Any, case_id: str, code: str
) -> None:
    report = run_checks(_subject(extractor=extractor))
    assert _statuses(report)[case_id] == ("failed", code)
    assert not report.conforms("Extractor")
    assert report.conforms("Store")


class _TrustsItself(LabelCorrectScorer):
    def score(self, predicted: Any, reference: Any, *, variables: Any) -> Any:
        import amber.reference.scorers as module

        original = module.refuse_self_reference
        module.refuse_self_reference = lambda *args: None  # type: ignore[assignment]
        try:
            return super().score(predicted, reference, variables=variables)
        finally:
            module.refuse_self_reference = original


class _ScoresEvidenceOnLabels(LabelCorrectScorer):
    def score(self, predicted: Any, reference: Any, *, variables: Any) -> Any:
        report = super().score(predicted, reference, variables=variables)
        extra = Metric(name="evidence_overlap_guess", kind="evidence", numerator=1, denominator=1)
        return report.model_copy(update={"metrics": (*report.metrics, extra)})


class _ForgetsExamples(LabelCorrectScorer):
    def score(self, predicted: Any, reference: Any, *, variables: Any) -> Any:
        return (
            super()
            .score(predicted, reference, variables=variables)
            .model_copy(update={"example_ids": ()})
        )


class _IgnoresTiers(LabelCorrectScorer):
    def score(self, predicted: Any, reference: Any, *, variables: Any) -> Any:
        return (
            super()
            .score(predicted, reference, variables=variables)
            .model_copy(update={"tier_mix": {}, "provisional": False})
        )


@pytest.mark.parametrize(
    ("scorer", "case_id", "code"),
    [
        (_TrustsItself, "ANN-12", "self_reference_not_refused"),
        (_ScoresEvidenceOnLabels, "ANN-11", "evidence_metric_on_labels"),
        (_ForgetsExamples, "ANN-17", "example_ids_missing"),
        (_IgnoresTiers, "IFC-09", "provisional_wrong"),
        (lambda: object(), "IFC-09", "not_a_scorer"),
    ],
)
def test_broken_scorers_fail_with_source_free_codes(scorer: Any, case_id: str, code: str) -> None:
    report = run_checks(
        Subject(name="broken", version="1", targets={"Scorer": scorer, "Store": InMemoryStore})
    )
    assert _statuses(report)[case_id] == ("failed", code)
    assert not report.conforms("Scorer")
    assert report.conforms("Store")
