"""The contract v0.1 conformance catalog (design section 7), loaded from ``catalog.json``.

The JSON is generated from docs/design/contract-v0.1.md section 7. IDs are stable and never
reused; changing a case's text changes ``catalog_revision()``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Literal, TypeAlias, cast

from amber.ids import canonical_sha256

Level: TypeAlias = Literal["core", "implementation", "bundle"]
Target: TypeAlias = Literal[
    "PolicyGate",
    "SourceResolver",
    "Provider",
    "Extractor",
    "Rule",
    "Labeler",
    "Store",
    "Exporter",
    "Scorer",
    "ReviewBackend",
    "copal adapter",
]
TARGETS: tuple[Target, ...] = (
    "PolicyGate",
    "SourceResolver",
    "Provider",
    "Extractor",
    "Rule",
    "Labeler",
    "Store",
    "Exporter",
    "Scorer",
    "ReviewBackend",
    "copal adapter",
)


@dataclass(frozen=True)
class CatalogCase:
    id: str
    level: Level
    targets: tuple[Target, ...]
    text: str


def _load() -> tuple[str, tuple[CatalogCase, ...]]:
    data = json.loads(files("amber.conformance").joinpath("catalog.json").read_text("utf-8"))
    cases = tuple(
        CatalogCase(
            id=item["id"],
            level=cast(Level, item["level"]),
            targets=tuple(cast(Target, target) for target in item["targets"]),
            text=item["text"],
        )
        for item in data["cases"]
    )
    return str(data["catalog_version"]), cases


CATALOG_VERSION, CATALOG = _load()
CASES = {case.id: case for case in CATALOG}


def catalog_revision() -> str:
    """SHA-256 of the canonical JSON of every case's ID, level, targets, and text."""
    return canonical_sha256(
        {
            "cases": [
                {
                    "id": case.id,
                    "level": case.level,
                    "targets": list(case.targets),
                    "text": case.text,
                }
                for case in CATALOG
            ]
        }
    )
