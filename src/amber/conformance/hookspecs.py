"""Hook a consumer implements to declare its conformance subject."""

from __future__ import annotations

from typing import Any

import pytest

from amber.conformance.checks import Subject


@pytest.hookspec(firstresult=True)
def pytest_amber_conformance_subject(config: Any) -> Subject | None:
    """Return the implementation under test and a factory per implemented target.

    ``Store``: a zero-argument factory returning an empty store. ``SourceResolver``: a factory
    taking the fixture sources.
    """
