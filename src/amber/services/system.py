"""System-level use cases that do not require an extraction backend."""

from importlib.metadata import PackageNotFoundError, version

from pydantic import BaseModel, ConfigDict

from amber.config import Settings


class SystemInfo(BaseModel):
    """Public metadata exposed consistently by each interface."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "amber"
    version: str
    environment: str


def package_version() -> str:
    """Resolve the installed package version, including editable installs."""

    try:
        return version("amber")
    except PackageNotFoundError:
        return "0.0.0"


def get_system_info(settings: Settings) -> SystemInfo:
    """Describe the running Amber application."""

    return SystemInfo(version=package_version(), environment=settings.environment)
