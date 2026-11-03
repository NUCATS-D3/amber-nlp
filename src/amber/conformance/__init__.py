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
kit supplies.

then runs ``pytest --amber-conformance --amber-conformance-report=report.json``.
"""

from amber.conformance.checks import CHECKS, ConformanceFailure, Subject

__all__ = ["CHECKS", "ConformanceFailure", "Subject"]
