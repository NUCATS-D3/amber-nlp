"""Opt-in test isolation for code that resolves process-wide Amber settings."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from amber.config import get_settings


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Ignore the caller's dotenv/environment and contain cached settings to one test."""
    for name in os.environ:
        if name.upper().startswith("AMBER_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
