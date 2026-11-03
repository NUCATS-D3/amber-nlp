"""``LocalExporter``: the reference ``Exporter`` for ``amber-bundle/0.1`` and
``amber-labels/0.1`` directories (contract v0.1 section 6.2); needs ``amber[bundles]``.

Every output file, the manifest included, is gated by its payload kind before anything is
written. The bundle is written to a temporary sibling and renamed into place, so a refused or
failed export leaves nothing behind.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from amber.bundles import (
    Bundle,
    ManifestDataset,
    ManifestProducer,
    build_bundle,
    build_labels_bundle,
)
from amber.bundles.parquet import MANIFEST, write_bundle
from amber.interfaces import ExportedFile, ExportItem, ExportReceipt, PolicyGate
from amber.policy import DataPolicy, Decision, Destination, PayloadKind, PolicyViolation
from amber.reference._guard import require_synthetic
from amber.schemas import Case, CaseResult, Example, LabelRecord, Source, VariableSet

ExportFormat = Literal["amber-bundle/0.1", "amber-labels/0.1"]


class ExportError(ValueError):
    """Items that the chosen format cannot hold; names no record contents."""


class LocalExporter:
    """Writes one bundle directory per export under ``directory``."""

    def __init__(
        self,
        directory: Path,
        *,
        format: ExportFormat,
        variables: VariableSet,
        dataset: ManifestDataset,
        producer: ManifestProducer,
        policy_version: str,
        cases: Iterable[Case],
        sources: Iterable[Source] = (),
        decisions: Iterable[Decision] = (),
    ) -> None:
        if format not in ("amber-bundle/0.1", "amber-labels/0.1"):
            raise ExportError("unsupported_format")
        self.format: str = format
        self.directory = directory
        self._variables = variables
        self._dataset = dataset
        self._producer = producer
        self._policy_version = policy_version
        self._cases = tuple(cases)
        self._sources = tuple(sources)
        self._decisions = tuple(decisions)

    def _bundle(self, items: Sequence[ExportItem], now: datetime) -> Bundle:
        results = [item for item in items if isinstance(item, CaseResult)]
        examples = [item for item in items if isinstance(item, Example)]
        labels = [item for item in items if isinstance(item, LabelRecord)]
        if len(results) + len(examples) + len(labels) != len(items) or not items:
            raise ExportError("unsupported_items")
        if self.format == "amber-labels/0.1":
            return build_labels_bundle(
                variables=self._variables,
                dataset=self._dataset,
                producer=self._producer,
                policy_version=self._policy_version,
                created_at=now,
                cases=self._cases,
                results=results,
                examples=examples,
                label_records=labels,
                decisions=self._decisions,
            )
        if labels or (results and examples):
            raise ExportError("unsupported_items")
        return build_bundle(
            kind="run" if results else "gold",
            variables=self._variables,
            dataset=self._dataset,
            producer=self._producer,
            policy_version=self._policy_version,
            created_at=now,
            sources=self._sources,
            cases=self._cases,
            results=results,
            examples=examples,
            decisions=self._decisions,
        )

    def export(
        self,
        items: Sequence[ExportItem],
        target: Destination,
        *,
        gate: PolicyGate,
        data: DataPolicy,
        now: datetime,
    ) -> ExportReceipt:
        """Gate every file, then write all of them or none."""
        require_synthetic(data.sensitivity, self._dataset.sensitivity)
        bundle = self._bundle(items, now)
        kinds: dict[str, PayloadKind] = {**bundle.manifest.payload_kinds, MANIFEST: "labels_only"}
        decisions = {
            file: gate.check(data, target, kind, now=now) for file, kind in sorted(kinds.items())
        }
        refused = [decision for decision in decisions.values() if not decision.permitted]
        if refused:
            raise PolicyViolation(refused[0], tuple(decisions.values()))

        self.directory.mkdir(parents=True, exist_ok=True)
        final = self.directory / f"{target.name}-{bundle.manifest.created_at:%Y%m%dT%H%M%S%fZ}"
        if final.exists():
            raise ExportError("target_exists")
        staging = Path(tempfile.mkdtemp(prefix=".amber-export-", dir=self.directory))
        try:
            written = write_bundle(bundle, staging / "bundle")
            os.replace(staging / "bundle", final)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        hashes = {
            f"{name}.parquet": table.content_sha256
            for name, table in written.manifest.tables.items()
        }
        hashes[MANIFEST] = hashlib.sha256((final / MANIFEST).read_bytes()).hexdigest()
        return ExportReceipt(
            format=self.format,
            destination=target.name,
            location=str(final),
            files=tuple(
                ExportedFile(
                    file=file,
                    payload_kind=kinds[file],
                    decision=decisions[file],
                    content_sha256=hashes[file],
                )
                for file in sorted(kinds)
            ),
        )
