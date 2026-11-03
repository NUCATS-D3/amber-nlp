"""Bundles: one dataset and one variable set as tables plus a manifest (contract v0.1
section 5.4).

The bundle model, hashing, payload kinds, and validation need only the core. Reading and
writing Parquet files is ``amber.bundles.parquet``, which needs the ``bundles`` extra.
"""

from amber.bundles.core import (
    Bundle,
    BundleContents,
    BundleError,
    Manifest,
    ManifestDataset,
    ManifestProducer,
    UnvalidatedBundle,
    UnvalidatedRow,
    build_bundle,
    check_bundle,
    content_sha256,
    load_bundle,
    text_hash,
)
from amber.bundles.schema import TABLES, payload_kind

__all__ = [
    "TABLES",
    "Bundle",
    "BundleContents",
    "BundleError",
    "Manifest",
    "ManifestDataset",
    "ManifestProducer",
    "UnvalidatedBundle",
    "UnvalidatedRow",
    "build_bundle",
    "check_bundle",
    "content_sha256",
    "load_bundle",
    "payload_kind",
    "text_hash",
]
