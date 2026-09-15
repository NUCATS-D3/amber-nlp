#!/usr/bin/env python3
"""Create a restricted local CORAL current-progression manifest and frozen split.

The selected manifest is the freeze authority: choosing another filename is not permission to
repartition exposed patients. A successor requires a future explicit migration that preserves
known assignments and links the original manifest; this command intentionally has no migration,
rebalancing, force, or overwrite mode.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import os
import secrets
import stat
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import click

# Direct file execution puts scripts/, not the repository root, on the import path.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from experiments.coral.brat import parse_ann, read_text  # noqa: E402
from experiments.coral.scripts.coral_current_progression import (  # noqa: E402
    derive_current_progression_candidate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

_CANCERS = ("breast", "pancreatic")
_SPLITS = ("train", "dev", "test")
_CAPACITIES = (10, 5, 5)
PROTOCOL_VERSION = "1.0.0"
ADAPTER_VERSION = "current-progression-candidate-1.0.0"
SPLIT_POLICY_VERSION = "current-progression-split-1.0.0"
SPLIT_SEED = 20260915
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_SAFE_DIRECTORY_OPERATIONS = bool(
    _O_NOFOLLOW
    and _O_DIRECTORY
    and all(
        function in os.supports_dir_fd
        for function in (os.open, os.mkdir, os.stat, os.unlink, os.link)
    )
    and os.link in os.supports_follow_symlinks
)


class ManifestInputError(ValueError):
    """The restricted dataset tree cannot produce a trustworthy manifest."""


class ManifestDestinationError(ValueError):
    """The requested output destination is outside the permitted local roots."""


class ImmutableManifestConflict(ValueError):
    """An existing artifact is invalid or differs from the proposed freeze."""


class ManifestWriteError(OSError):
    """The manifest could not be durably published without replacement."""


@dataclass(frozen=True)
class _DestinationPlan:
    path: Path
    project_root: Path
    parent_parts: tuple[str, ...]
    filename: str
    input_identities: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class ManifestInput:
    doc_id: str
    group_id: str
    coral_idx: str
    cancer_type: Literal["breast", "pancreatic"]
    progression_candidate: bool
    candidate_disposition: str
    txt_path: Path
    ann_path: Path


def sha256_file(path: Path) -> str:
    """Hash the exact bytes stored at *path*."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def allocate_candidate_quotas(
    positive_counts: Mapping[str, int],
) -> dict[str, tuple[int, int, int]]:
    """Choose deterministic positive-candidate quotas under fixed cancer margins."""
    if set(positive_counts) != set(_CANCERS):
        raise ValueError("exactly breast and pancreatic counts are required")
    if any(type(count) is not int or not 0 <= count <= 20 for count in positive_counts.values()):
        raise ValueError("candidate counts must be integers from 0 through 20")

    def options(count: int) -> list[tuple[int, int, int]]:
        return [
            (train, dev, count - train - dev)
            for train in range(11)
            for dev in range(6)
            if 0 <= count - train - dev <= 5
        ]

    def loss(quota: tuple[int, ...], count: int) -> int:
        return sum(
            (4 * value - weight * count) ** 2
            for value, weight in zip(quota, (2, 1, 1), strict=True)
        )

    def objective(
        pair: tuple[tuple[int, int, int], ...],
    ) -> tuple[int, int, tuple[int, ...]]:
        total = tuple(sum(quota[index] for quota in pair) for index in range(3))
        global_loss = loss(total, sum(positive_counts.values()))
        cell_loss = 0
        for cancer, positive in zip(_CANCERS, pair, strict=True):
            count = positive_counts[cancer]
            other = tuple(_CAPACITIES[index] - positive[index] for index in range(3))
            cell_loss += loss(positive, count) + loss(other, 20 - count)
        return global_loss, cell_loss, tuple(value for quota in pair for value in quota)

    pairs = itertools.product(*(options(positive_counts[cancer]) for cancer in _CANCERS))
    selected = min(pairs, key=objective)
    return dict(zip(_CANCERS, selected, strict=True))


def _validate_split_inputs(rows: Sequence[ManifestInput]) -> None:
    if len(rows) != 40:
        raise ValueError("exactly 40 documents are required")
    for field in ("doc_id", "coral_idx", "group_id"):
        values = [getattr(row, field) for row in rows]
        if len(set(values)) != len(values):
            raise ValueError(f"duplicate {field}")
    counts = Counter(row.cancer_type for row in rows)
    if counts != {"breast": 20, "pancreatic": 20}:
        raise ValueError("exactly 20 documents per cancer type are required")


def assign_document_splits(rows: Sequence[ManifestInput], seed: int = 20260915) -> dict[str, str]:
    """Assign the fixed forty-document pool by cancer and candidate stratum."""
    _validate_split_inputs(rows)
    positive_counts = {
        cancer: sum(row.progression_candidate for row in rows if row.cancer_type == cancer)
        for cancer in _CANCERS
    }
    positive_quotas = allocate_candidate_quotas(positive_counts)
    assignment: dict[str, str] = {}

    for cancer in _CANCERS:
        for positive in (True, False):
            stratum = sorted(
                (
                    row
                    for row in rows
                    if row.cancer_type == cancer and row.progression_candidate is positive
                ),
                key=lambda row: (
                    hashlib.sha256(f"{seed}\0{row.group_id}".encode()).hexdigest(),
                    row.group_id,
                ),
            )
            quotas = positive_quotas[cancer]
            if not positive:
                quotas = (
                    _CAPACITIES[0] - quotas[0],
                    _CAPACITIES[1] - quotas[1],
                    _CAPACITIES[2] - quotas[2],
                )
            offset = 0
            for split, quota in zip(_SPLITS, quotas, strict=True):
                for row in stratum[offset : offset + quota]:
                    assignment[row.group_id] = split
                offset += quota
            if offset != len(stratum):
                raise ValueError("candidate quota did not consume its stratum")

    if set(assignment) != {row.group_id for row in rows}:
        raise ValueError("split assignment is incomplete")
    if Counter(assignment.values()) != {"train": 20, "dev": 10, "test": 10}:
        raise ValueError("global split margins are invalid")
    for cancer in _CANCERS:
        cancer_counts = Counter(
            assignment[row.group_id] for row in rows if row.cancer_type == cancer
        )
        if cancer_counts != {"train": 10, "dev": 5, "test": 5}:
            raise ValueError("cancer split margins are invalid")
        for positive in (True, False):
            actual = tuple(
                sum(
                    assignment[row.group_id] == split
                    for row in rows
                    if row.cancer_type == cancer and row.progression_candidate is positive
                )
                for split in _SPLITS
            )
            expected = positive_quotas[cancer]
            if not positive:
                expected = (
                    _CAPACITIES[0] - expected[0],
                    _CAPACITIES[1] - expected[1],
                    _CAPACITIES[2] - expected[2],
                )
            if actual != expected:
                raise ValueError("candidate split quotas are invalid")
    return assignment


def _discover_inputs(
    root: Path,
) -> tuple[list[ManifestInput], Path, Path, Counter[str], Counter[str]]:
    if "unannotated" in root.parts:
        raise ManifestInputError("unannotated input is not permitted")
    if not root.is_dir():
        raise ManifestInputError("annotated input directory is required")
    subject_path = root / "subject-info.csv"
    config_path = root / "annotation.conf"
    if not subject_path.is_file() or not config_path.is_file():
        raise ManifestInputError("required corpus metadata is missing")

    try:
        with subject_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or "coral_idx" not in reader.fieldnames:
                raise ManifestInputError("subject metadata lacks coral_idx")
            subject_ids = [row.get("coral_idx", "") for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ManifestInputError("subject metadata is unreadable") from exc
    if any(not value for value in subject_ids) or len(subject_ids) != len(set(subject_ids)):
        raise ManifestInputError("subject identities must be nonempty and unique")

    try:
        txt_paths = sorted(root.rglob("*.txt"))
        ann_paths = sorted(root.rglob("*.ann"))
    except OSError as exc:
        raise ManifestInputError("input discovery failed") from exc
    if any("unannotated" in path.relative_to(root).parts for path in txt_paths + ann_paths):
        raise ManifestInputError("unannotated input is not permitted")

    def index(paths: list[Path], suffix: str) -> dict[str, Path]:
        indexed: dict[str, Path] = {}
        for path in paths:
            relative = path.relative_to(root)
            if len(relative.parts) != 2 or path.suffix != suffix or path.is_symlink():
                raise ManifestInputError("documents must be immediate corpus children")
            if path.stem in indexed:
                raise ManifestInputError("document ids must be globally unique")
            indexed[path.stem] = path
        return indexed

    txt_by_id = index(txt_paths, ".txt")
    ann_by_id = index(ann_paths, ".ann")
    if set(txt_by_id) != set(ann_by_id):
        raise ManifestInputError("every document must have one txt and ann file")
    if set(subject_ids) != set(txt_by_id):
        raise ManifestInputError("subject metadata must match document identities")

    directory_to_cancer: dict[str, Literal["breast", "pancreatic"]] = {
        "breastca": "breast",
        "pdac": "pancreatic",
    }
    diagnostics: Counter[str] = Counter()
    candidate_flags: Counter[str] = Counter()
    rows: list[ManifestInput] = []
    for doc_id in sorted(txt_by_id):
        txt_path = txt_by_id[doc_id]
        ann_path = ann_by_id[doc_id]
        if txt_path.parent != ann_path.parent:
            raise ManifestInputError("paired files must share a corpus directory")
        try:
            cancer_type = directory_to_cancer[txt_path.parent.name]
        except KeyError as exc:
            raise ManifestInputError("unknown cancer directory") from exc
        try:
            text = read_text(txt_path)
            document = parse_ann(ann_path, text)
            candidate = derive_current_progression_candidate(document)
        except (OSError, UnicodeError) as exc:
            raise ManifestInputError("document input is unreadable") from exc
        progression_candidate = candidate.disposition == "answered" and candidate.value is True
        candidate_flags.update(candidate.flags)
        if not candidate.annotation_inventory_complete:
            diagnostics["incomplete_annotation_inventory"] += 1
        diagnostics.update(diagnostic.code for diagnostic in document.diagnostics)
        rows.append(
            ManifestInput(
                doc_id=doc_id,
                group_id=doc_id,
                coral_idx=doc_id,
                cancer_type=cancer_type,
                progression_candidate=progression_candidate,
                candidate_disposition=candidate.disposition,
                txt_path=txt_path,
                ann_path=ann_path,
            )
        )
    try:
        _validate_split_inputs(rows)
    except ValueError as exc:
        raise ManifestInputError("invalid fixed CORAL document pool") from exc
    return rows, subject_path, config_path, diagnostics, candidate_flags


def _counts_by_split(values: Sequence[str]) -> dict[str, int]:
    counts = Counter(values)
    return {split: counts[split] for split in _SPLITS}


def _canonical_payload_bytes(manifest: Mapping[str, object]) -> bytes:
    payload = dict(manifest)
    payload.pop("manifest_sha256", None)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def manifest_payload_hash(manifest: Mapping[str, object]) -> str:
    """Hash the canonical payload, excluding its self-hash field."""
    return hashlib.sha256(_canonical_payload_bytes(manifest)).hexdigest()


def presentation_bytes(manifest: Mapping[str, object]) -> bytes:
    """Render a deterministic human-readable artifact with one trailing newline."""
    return (
        json.dumps(
            manifest,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def build_manifest(root: Path) -> dict[str, object]:
    """Build a source-free manifest from one fixed annotated CORAL tree."""
    try:
        resolved_root = root.resolve(strict=True)
        rows, subject_path, config_path, diagnostics, candidate_flags = _discover_inputs(
            resolved_root
        )
        assignments = assign_document_splits(rows, seed=SPLIT_SEED)
        positive_counts = {
            cancer: sum(row.progression_candidate for row in rows if row.cancer_type == cancer)
            for cancer in _CANCERS
        }
        quotas = allocate_candidate_quotas(positive_counts)
        documents = [
            {
                "doc_id": row.doc_id,
                "coral_idx": row.coral_idx,
                "cancer_type": row.cancer_type,
                "progression_candidate": row.progression_candidate,
                "candidate_disposition": row.candidate_disposition,
                "split": assignments[row.group_id],
                "txt_path": row.txt_path.relative_to(resolved_root).as_posix(),
                "txt_sha256": sha256_file(row.txt_path),
                "ann_path": row.ann_path.relative_to(resolved_root).as_posix(),
                "ann_sha256": sha256_file(row.ann_path),
            }
            for row in sorted(rows, key=lambda item: item.doc_id)
        ]
        dispositions = Counter(row.candidate_disposition for row in rows)
        split_counts = _counts_by_split([assignments[row.group_id] for row in rows])
        manifest: dict[str, object] = {
            "dataset": {"name": "CORAL", "version": "1.0", "doi": "10.13026/v69y-xa45"},
            "sensitivity": "deidentified",
            "task": "oncology_current_progression",
            "protocol_version": PROTOCOL_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "split_policy_version": SPLIT_POLICY_VERSION,
            "split_seed": SPLIT_SEED,
            "split_counts": split_counts,
            "candidate_counts": {
                "positive": sum(positive_counts.values()),
                "other": len(rows) - sum(positive_counts.values()),
            },
            "candidate_disposition_counts": dict(sorted(dispositions.items())),
            "candidate_flag_counts": dict(sorted(candidate_flags.items())),
            "candidate_quotas": {
                cancer: dict(zip(_SPLITS, quotas[cancer], strict=True)) for cancer in _CANCERS
            },
            "diagnostic_counts": dict(sorted(diagnostics.items())),
            "source_files": {
                "subject_info": {
                    "path": subject_path.relative_to(resolved_root).as_posix(),
                    "sha256": sha256_file(subject_path),
                },
                "annotation_config": {
                    "path": config_path.relative_to(resolved_root).as_posix(),
                    "sha256": sha256_file(config_path),
                },
            },
            "documents": documents,
        }
        manifest["manifest_sha256"] = manifest_payload_hash(manifest)
        return manifest
    except ManifestInputError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ManifestInputError("manifest input could not be processed") from exc


def _is_beneath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return path != parent


def _validate_output_destination(root: Path, output: Path) -> _DestinationPlan:
    if output.suffix.lower() != ".json":
        raise ManifestDestinationError("manifest destination must be JSON")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_output = output.resolve(strict=False)
        project_root = PROJECT_ROOT.resolve(strict=True)
        experiment_root = project_root / "experiments" / "coral"
        permitted_roots = (
            experiment_root / "data" / "manifests",
            experiment_root / "outputs",
        )
        guarded_roots = (experiment_root, *permitted_roots)
        if any(anchor.resolve(strict=False) != anchor for anchor in guarded_roots):
            raise ManifestDestinationError("manifest authorization root is redirected")
        raw_root = experiment_root / "data" / "raw"
    except OSError as exc:
        raise ManifestDestinationError("manifest destination cannot be resolved") from exc
    if resolved_output.suffix.lower() != ".json" or not any(
        _is_beneath(resolved_output, permitted) for permitted in permitted_roots
    ):
        raise ManifestDestinationError("manifest destination is outside permitted roots")
    if _is_beneath(resolved_output, resolved_root) or _is_beneath(resolved_output, raw_root):
        raise ManifestDestinationError("manifest destination overlaps restricted inputs")
    try:
        input_files = [path for path in resolved_root.rglob("*") if path.is_file()]
        identity_set: set[tuple[int, int]] = set()
        for path in input_files:
            metadata = path.stat()
            identity_set.add((metadata.st_dev, metadata.st_ino))
        input_identities = frozenset(identity_set)
        if output.exists() and any(os.path.samefile(output, path) for path in input_files):
            raise ManifestDestinationError("manifest destination aliases an input")
    except OSError as exc:
        raise ManifestDestinationError("manifest destination cannot be inspected") from exc
    try:
        parent_parts = resolved_output.parent.relative_to(project_root).parts
    except ValueError as exc:
        raise ManifestDestinationError("manifest destination escaped the checkout") from exc
    return _DestinationPlan(
        path=resolved_output,
        project_root=project_root,
        parent_parts=parent_parts,
        filename=resolved_output.name,
        input_identities=input_identities,
    )


def _validate_manifest(manifest: object) -> dict[str, object]:
    if not isinstance(manifest, dict):
        raise ImmutableManifestConflict("existing manifest is not an object")
    expected_hash = manifest.get("manifest_sha256")
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or manifest_payload_hash(manifest) != expected_hash
    ):
        raise ImmutableManifestConflict("existing manifest self-hash is invalid")
    required = {
        "dataset",
        "sensitivity",
        "task",
        "protocol_version",
        "adapter_version",
        "split_policy_version",
        "split_seed",
        "split_counts",
        "candidate_counts",
        "candidate_disposition_counts",
        "candidate_flag_counts",
        "candidate_quotas",
        "diagnostic_counts",
        "source_files",
        "documents",
        "manifest_sha256",
    }
    if set(manifest) != required:
        raise ImmutableManifestConflict("existing manifest fields are invalid")
    documents = manifest.get("documents")
    if not isinstance(documents, list) or len(documents) != 40:
        raise ImmutableManifestConflict("existing manifest document inventory is invalid")
    document_keys = {
        "doc_id",
        "coral_idx",
        "cancer_type",
        "progression_candidate",
        "candidate_disposition",
        "split",
        "txt_path",
        "txt_sha256",
        "ann_path",
        "ann_sha256",
    }
    if any(not isinstance(row, dict) or set(row) != document_keys for row in documents):
        raise ImmutableManifestConflict("existing manifest document fields are invalid")
    try:
        rows = [
            ManifestInput(
                doc_id=row["doc_id"],
                group_id=row["coral_idx"],
                coral_idx=row["coral_idx"],
                cancer_type=row["cancer_type"],
                progression_candidate=row["progression_candidate"],
                candidate_disposition=row["candidate_disposition"],
                txt_path=Path(row["txt_path"]),
                ann_path=Path(row["ann_path"]),
            )
            for row in documents
        ]
        assignment = assign_document_splits(rows, seed=manifest["split_seed"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ImmutableManifestConflict("existing manifest partition is invalid") from exc
    if any(row["split"] != assignment[row["coral_idx"]] for row in documents):
        raise ImmutableManifestConflict("existing manifest partition membership is invalid")
    quotas = allocate_candidate_quotas(
        {
            cancer: sum(row.progression_candidate for row in rows if row.cancer_type == cancer)
            for cancer in _CANCERS
        }
    )
    expected_quotas = {
        cancer: dict(zip(_SPLITS, quotas[cancer], strict=True)) for cancer in _CANCERS
    }
    if manifest["candidate_quotas"] != expected_quotas:
        raise ImmutableManifestConflict("existing manifest candidate quotas are invalid")
    return manifest


def _read_existing(
    directory_fd: int,
    filename: str,
    input_identities: frozenset[tuple[int, int]],
) -> dict[str, object]:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY | _O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ImmutableManifestConflict("existing manifest is not a regular file")
        if (metadata.st_dev, metadata.st_ino) in input_identities:
            raise ImmutableManifestConflict("existing manifest aliases an input")
        with os.fdopen(descriptor, "r", encoding="utf-8", newline="") as handle:
            descriptor = None
            return _validate_manifest(json.load(handle))
    except ImmutableManifestConflict:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ImmutableManifestConflict("existing manifest is unreadable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _verify_existing(
    directory_fd: int,
    filename: str,
    input_identities: frozenset[tuple[int, int]],
    proposed: Mapping[str, object],
) -> None:
    existing = _read_existing(directory_fd, filename, input_identities)
    if _canonical_payload_bytes(existing) != _canonical_payload_bytes(proposed):
        raise ImmutableManifestConflict("existing manifest differs from proposed payload")


def _open_project_root(path: Path) -> int:
    if not _SAFE_DIRECTORY_OPERATIONS:
        raise ManifestDestinationError("directory-safe operations are unavailable")
    try:
        return os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_DIRECTORY)
    except OSError as exc:
        raise ManifestDestinationError("checkout root cannot be anchored") from exc


def _open_or_create_parent(root_fd: int, parts: tuple[str, ...]) -> int:
    if not _SAFE_DIRECTORY_OPERATIONS:
        raise ManifestWriteError("directory-safe operations are unavailable")
    current_fd = os.dup(root_fd)
    try:
        for part in parts:
            with suppress(FileExistsError):
                os.mkdir(part, mode=0o700, dir_fd=current_fd)
            next_fd = os.open(
                part,
                os.O_RDONLY | _O_NOFOLLOW | _O_DIRECTORY,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except OSError as exc:
        os.close(current_fd)
        raise ManifestWriteError("manifest directory could not be anchored") from exc


def _destination_exists(directory_fd: int, filename: str) -> bool:
    try:
        os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ImmutableManifestConflict("existing manifest cannot be inspected") from exc
    return True


def _create_temporary_file(directory_fd: int, filename: str) -> tuple[int, str]:
    if not _SAFE_DIRECTORY_OPERATIONS:
        raise ManifestWriteError("file-safe operations are unavailable")
    for _ in range(100):
        temporary_name = f".{filename}.{secrets.token_hex(12)}.tmp"
        try:
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        return descriptor, temporary_name
    raise ManifestWriteError("temporary manifest name allocation failed")


def _create_or_verify(
    destination: _DestinationPlan,
    root_fd: int,
    manifest: Mapping[str, object],
) -> None:
    directory_fd = _open_or_create_parent(root_fd, destination.parent_parts)
    try:
        if _destination_exists(directory_fd, destination.filename):
            _verify_existing(
                directory_fd,
                destination.filename,
                destination.input_identities,
                manifest,
            )
            return

        descriptor, temporary_name = _create_temporary_file(directory_fd, destination.filename)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(presentation_bytes(manifest))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(
                    temporary_name,
                    destination.filename,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                _verify_existing(
                    directory_fd,
                    destination.filename,
                    destination.input_identities,
                    manifest,
                )
                return
            os.fsync(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            with suppress(OSError):
                os.unlink(temporary_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except ImmutableManifestConflict:
        raise
    except OSError as exc:
        raise ManifestWriteError("manifest could not be published") from exc
    finally:
        os.close(directory_fd)


@click.command()
@click.argument("root", type=click.Path(path_type=Path))
@click.option("--output", required=True, type=click.Path(path_type=Path))
def main(root: Path, output: Path) -> None:
    """Create or verify one immutable restricted local manifest."""
    try:
        destination = _validate_output_destination(root, output)
        root_fd = _open_project_root(destination.project_root)
    except (ManifestDestinationError, OSError):
        raise click.ClickException("manifest destination rejected") from None
    try:
        try:
            manifest = build_manifest(root)
        except (ManifestInputError, OSError):
            raise click.ClickException("manifest input rejected") from None
        try:
            _create_or_verify(destination, root_fd, manifest)
        except ImmutableManifestConflict:
            raise click.ClickException("immutable manifest conflict") from None
        except ManifestWriteError:
            raise click.ClickException("manifest write failed") from None
    finally:
        os.close(root_fd)

    split_counts = manifest["split_counts"]
    candidate_counts = manifest["candidate_counts"]
    diagnostic_counts = manifest["diagnostic_counts"]
    candidate_flag_counts = manifest["candidate_flag_counts"]
    assert isinstance(split_counts, dict)
    assert isinstance(candidate_counts, dict)
    assert isinstance(diagnostic_counts, dict)
    assert isinstance(candidate_flag_counts, dict)
    click.echo(f"output: {destination.path}")
    click.echo(f"manifest_sha256: {manifest['manifest_sha256']}")
    click.echo(f"documents: {len(manifest['documents'])}")  # type: ignore[arg-type]
    click.echo(
        f"splits: train={split_counts['train']} dev={split_counts['dev']} "
        f"test={split_counts['test']}"
    )
    click.echo(
        f"candidates: positive={candidate_counts['positive']} other={candidate_counts['other']}"
    )
    click.echo(
        f"incomplete_inventories: {diagnostic_counts.get('incomplete_annotation_inventory', 0)}"
    )
    click.echo(f"candidate_flags: {sum(candidate_flag_counts.values())}")


if __name__ == "__main__":
    main()
