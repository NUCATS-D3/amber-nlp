"""Stable Python façade over Amber use cases."""

from dataclasses import dataclass

from amber.config import Settings, get_settings
from amber.services import SystemInfo, get_system_info


@dataclass(frozen=True, slots=True)
class Amber:
    """Library entry point used directly and injected into CLI/API adapters."""

    settings: Settings

    @classmethod
    def from_env(cls) -> "Amber":
        """Create a client from ``AMBER_*`` environment variables."""

        return cls(settings=get_settings())

    def info(self) -> SystemInfo:
        """Return metadata about this Amber instance."""

        return get_system_info(self.settings)


def create_client(settings: Settings | None = None) -> Amber:
    """Construct an Amber façade with explicit or environment-based settings."""

    return Amber(settings=settings or get_settings())
