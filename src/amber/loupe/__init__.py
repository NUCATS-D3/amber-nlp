"""The amber side of the loupe review bridge. loupe loads ``AmberClaimsAdapter`` by import
path; amber never imports loupe. Not loaded by ``import amber``."""

from amber.loupe.adapter import ADAPTER_VERSION, SCHEMA_VERSION, AdapterError, AmberClaimsAdapter

__all__ = ["ADAPTER_VERSION", "SCHEMA_VERSION", "AdapterError", "AmberClaimsAdapter"]
