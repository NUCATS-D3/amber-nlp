"""pytest plugin for the amber conformance kit (``pytest11`` entry point ``amber_conformance``).

Inert unless ``--amber-conformance`` is given. The consumer implements
``pytest_amber_conformance_subject(config)`` in its root ``conftest.py``; the plugin then
collects ``amber.conformance.suite`` and, with ``--amber-conformance-report=PATH``, writes the
``ConformanceReport`` as JSON.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

try:
    import pytest
except ImportError as error:  # pragma: no cover - pytest loads this module
    raise ImportError(
        "amber's conformance plugin needs pytest; install amber[conformance]"
    ) from error

from amber.conformance import hookspecs
from amber.conformance.checks import Check, ConformanceFailure, Subject
from amber.conformance.report import build_report

_SUITE = Path(__file__).with_name("suite.py")
_OUTCOMES = pytest.StashKey[dict[tuple[str, str], str | None]]()
_SUBJECT = pytest.StashKey[Subject]()


def pytest_addhooks(pluginmanager: pytest.PytestPluginManager) -> None:
    pluginmanager.add_hookspecs(hookspecs)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("amber-conformance", "amber conformance kit")
    group.addoption(
        "--amber-conformance",
        action="store_true",
        default=False,
        help="run the amber conformance kit against the declared subject",
    )
    group.addoption(
        "--amber-conformance-report",
        default=None,
        metavar="PATH",
        help="write the ConformanceReport JSON to PATH",
    )


def _enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("--amber-conformance"))


def pytest_configure(config: pytest.Config) -> None:
    if not _enabled(config):
        return
    config.stash[_OUTCOMES] = {}
    config.args.append(str(_SUITE))


def _subject(config: pytest.Config) -> Subject:
    if _SUBJECT not in config.stash:
        subject = config.hook.pytest_amber_conformance_subject(config=config)
        if not isinstance(subject, Subject):
            raise pytest.UsageError(
                "--amber-conformance needs a pytest_amber_conformance_subject hook returning "
                "amber.conformance.Subject"
            )
        config.stash[_SUBJECT] = subject
    return config.stash[_SUBJECT]


@pytest.fixture(scope="session")
def amber_conformance_subject(pytestconfig: pytest.Config) -> Subject:
    return _subject(pytestconfig)


def _check(item: pytest.Item) -> Check | None:
    callspec = getattr(item, "callspec", None)
    check = None if callspec is None else callspec.params.get("amber_check")
    return check if isinstance(check, Check) else None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, Any, Any]:
    report = yield
    check = _check(item)
    if check is None or not _enabled(item.config) or report.when != "call":
        return report
    outcomes = item.config.stash[_OUTCOMES]
    key = (check.case_id, check.target)
    if report.skipped:
        return report
    if call.excinfo is None:
        outcomes[key] = None
    elif isinstance(call.excinfo.value, ConformanceFailure):
        outcomes[key] = call.excinfo.value.code
    else:
        outcomes[key] = "unexpected_error"
    return report


def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    if not _enabled(config):
        return
    path = config.getoption("--amber-conformance-report")
    if path is None or _SUBJECT not in config.stash:
        return
    subject = config.stash[_SUBJECT]
    outcomes: dict[tuple[str, str], str | None] = config.stash[_OUTCOMES]
    report = build_report(
        name=subject.name,
        version=subject.version,
        targets=list(subject.targets),
        outcomes=outcomes,
    )
    Path(path).write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
