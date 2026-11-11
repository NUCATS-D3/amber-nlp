"""The amber conformance kit (contract v0.1 section 7).

Pure-Python parts (catalog, fixtures, checks, report) need nothing beyond the core. The pytest
plugin (``amber.conformance.plugin``) is registered as a ``pytest11`` entry point and stays
inert unless ``--amber-conformance`` is given; install the ``conformance`` extra for pytest.

A consumer declares what it implements by implementing the hook in its root ``conftest.py``::

    def pytest_amber_conformance_subject(config):
        return Subject(
            name="lapidary",
            version="0.3.0",
            targets={"Store": MyStore, "Extractor": lambda provider: MyExtractor(provider)},
        )

``Store``, ``Provider``, ``Scorer``, and ``Labeler`` factories take no arguments;
``SourceResolver`` takes the fixture sources; ``Extractor`` takes the scripted ``Provider`` the
kit supplies; ``ReviewBackend`` takes no arguments and returns ``(backend, review)``, where
``review(receipt)`` completes the reviews of an exported batch (accepting every proposal).
``Rule`` takes no arguments and returns ``(rule, result, variables)``: a result holding at
least one derivation by the rule, and its variable set.
An ``Extractor`` built for one task (whose results on the kit's own variable sets can only be
failures) passes ``Subject(..., extractor_fixture=ExtractorFixture(case, source, variables,
answers))``: an invented case of its task on which its mentions are checked (IFC-03, IFC-12).

then runs ``pytest --amber-conformance --amber-conformance-report=report.json``.

To check a written bundle instead, the consumer returns a ``BundleUnderTest`` (variable set and
optional resolver) from ``pytest_amber_conformance_bundle(config)`` and runs
``pytest --amber-conformance --amber-conformance-bundle=DIR``; the report's subject is then the
bundle (``amber.conformance.bundle``, which needs the ``bundles`` extra).
"""

from amber.conformance.checks import (
    CHECKS,
    BundleUnderTest,
    ConformanceFailure,
    ExtractorFixture,
    Subject,
)

__all__ = ["CHECKS", "BundleUnderTest", "ConformanceFailure", "ExtractorFixture", "Subject"]
