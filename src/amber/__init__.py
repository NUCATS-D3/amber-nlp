"""Public Python interface for grounded clinical information extraction."""

from amber.client import Amber, create_client
from amber.services.system import package_version

__version__ = package_version()

__all__ = ["Amber", "__version__", "create_client"]
