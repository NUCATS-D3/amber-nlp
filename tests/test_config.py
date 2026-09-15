"""Settings contracts exercised with invented environment and dotenv values."""

from pathlib import Path

import pytest

from amber.config import Settings, get_settings

pytestmark = pytest.mark.usefixtures("isolated_settings")


def test_default_settings_are_independent_of_the_invoking_environment() -> None:
    assert get_settings().model_dump() == {
        "environment": "development",
        "api_prefix": "/api/v1",
        "api_host": "127.0.0.1",
        "api_port": 8000,
        "log_level": "info",
    }


def test_environment_overrides_dotenv_and_ignores_unrelated_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotenv = tmp_path / "settings.env"
    dotenv.write_text("AMBER_API_PORT=8100\nUNRELATED_SETTING=ignored\n")
    monkeypatch.setenv("AMBER_API_PORT", "8200")

    assert Settings(_env_file=dotenv).api_port == 8200


def test_explicit_settings_can_disable_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("AMBER_ENVIRONMENT=production\nAMBER_API_PORT=8100\n")

    settings = Settings(_env_file=None, environment="test")

    assert settings.environment == "test"
    assert settings.api_port == 8000


def test_cached_settings_change_only_after_cache_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMBER_API_PORT", "8200")
    initial = get_settings()
    monkeypatch.setenv("AMBER_API_PORT", "8300")

    assert get_settings() is initial
    assert get_settings().api_port == 8200

    get_settings.cache_clear()
    assert get_settings().api_port == 8300
