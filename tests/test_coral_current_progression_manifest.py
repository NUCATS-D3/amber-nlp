"""Restricted CORAL manifest generation from invented BRAT fixtures only."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import stat
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from experiments.coral.scripts import coral_current_progression_manifest as manifest_module
from experiments.coral.scripts.coral_current_progression_manifest import (
    ManifestInput,
    allocate_candidate_quotas,
    assign_document_splits,
    build_manifest,
    main,
    sha256_file,
)


def _rows(
    breast_positive: int = 7,
    pancreatic_positive: int = 13,
) -> list[ManifestInput]:
    rows: list[ManifestInput] = []
    for cancer, positive_count in (
        ("breast", breast_positive),
        ("pancreatic", pancreatic_positive),
    ):
        for index in range(20):
            stem = f"{cancer}-{index:02d}"
            positive = index < positive_count
            rows.append(
                ManifestInput(
                    doc_id=stem,
                    group_id=stem,
                    coral_idx=stem,
                    cancer_type=cancer,
                    progression_candidate=positive,
                    candidate_disposition="answered" if positive else "not_mentioned",
                    txt_path=Path(f"{stem}.txt"),
                    ann_path=Path(f"{stem}.ann"),
                )
            )
    return rows


def _quota_objective(
    pair: tuple[tuple[int, int, int], tuple[int, int, int]],
    breast_count: int,
    pancreatic_count: int,
) -> tuple[int, int, tuple[int, ...]]:
    capacities = (10, 5, 5)

    def loss(quota: tuple[int, ...], count: int) -> int:
        return sum(
            (4 * value - weight * count) ** 2
            for value, weight in zip(quota, (2, 1, 1), strict=True)
        )

    total = tuple(pair[0][index] + pair[1][index] for index in range(3))
    cell_loss = 0
    for positive, count in zip(pair, (breast_count, pancreatic_count), strict=True):
        other = tuple(capacities[index] - positive[index] for index in range(3))
        cell_loss += loss(positive, count) + loss(other, 20 - count)
    return (
        loss(total, breast_count + pancreatic_count),
        cell_loss,
        pair[0] + pair[1],
    )


def _quota_options(count: int) -> list[tuple[int, int, int]]:
    return [
        (train, dev, count - train - dev)
        for train in range(11)
        for dev in range(6)
        if 0 <= count - train - dev <= 5
    ]


def test_sha256_file_hashes_exact_crlf_and_unicode_bytes(tmp_path: Path) -> None:
    raw = "invented\r\nβeta\r\n".encode()
    path = tmp_path / "source.txt"
    path.write_bytes(raw)

    assert sha256_file(path) == hashlib.sha256(raw).hexdigest()


def test_split_is_order_independent_complete_balanced_and_seeded() -> None:
    rows = _rows()

    first = assign_document_splits(rows, seed=20260915)
    second = assign_document_splits(tuple(reversed(rows)), seed=20260915)

    assert first == second
    assert Counter(first.values()) == {"train": 20, "dev": 10, "test": 10}
    assert set(first) == {row.group_id for row in rows}
    assert assign_document_splits(rows, seed=20260916) != first
    for cancer in ("breast", "pancreatic"):
        assert Counter(first[row.group_id] for row in rows if row.cancer_type == cancer) == {
            "train": 10,
            "dev": 5,
            "test": 5,
        }


def test_review_regression_retains_margins_and_global_candidate_counts() -> None:
    rows = _rows(breast_positive=2, pancreatic_positive=2)

    assignment = assign_document_splits(rows)

    assert Counter(assignment[row.group_id] for row in rows if row.progression_candidate) == {
        "train": 2,
        "dev": 1,
        "test": 1,
    }
    for cancer in ("breast", "pancreatic"):
        assert Counter(assignment[row.group_id] for row in rows if row.cancer_type == cancer) == {
            "train": 10,
            "dev": 5,
            "test": 5,
        }


def test_candidate_quotas_are_feasible_and_minimize_the_integer_objective() -> None:
    for breast_count, pancreatic_count in itertools.product(range(21), repeat=2):
        counts = {"breast": breast_count, "pancreatic": pancreatic_count}
        quotas = allocate_candidate_quotas(counts)
        reverse_order = allocate_candidate_quotas(dict(reversed(tuple(counts.items()))))
        pair = (quotas["breast"], quotas["pancreatic"])
        alternatives = itertools.product(
            _quota_options(breast_count), _quota_options(pancreatic_count)
        )

        assert quotas == reverse_order
        assert sum(pair[0]) == breast_count
        assert sum(pair[1]) == pancreatic_count
        assert all(
            0 <= value <= capacity
            for quota in pair
            for value, capacity in zip(quota, (10, 5, 5), strict=True)
        )
        assert _quota_objective(pair, breast_count, pancreatic_count) == min(
            _quota_objective(alternative, breast_count, pancreatic_count)
            for alternative in alternatives
        )


@pytest.mark.parametrize(
    "counts",
    [
        {},
        {"breast": 1},
        {"breast": 1, "pancreatic": 2, "other": 3},
        {"breast": -1, "pancreatic": 2},
        {"breast": 21, "pancreatic": 2},
        {"breast": True, "pancreatic": 2},
        {"breast": 1.0, "pancreatic": 2},
    ],
)
def test_candidate_quota_allocation_rejects_invalid_counts(counts: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        allocate_candidate_quotas(counts)  # type: ignore[arg-type]


@pytest.mark.parametrize("duplicate_field", ["doc_id", "coral_idx", "group_id"])
def test_split_rejects_duplicate_identities(duplicate_field: str) -> None:
    rows = _rows()
    duplicate_value = getattr(rows[0], duplicate_field)
    replacement = {
        field: getattr(rows[1], field)
        for field in (
            "doc_id",
            "group_id",
            "coral_idx",
            "cancer_type",
            "progression_candidate",
            "candidate_disposition",
            "txt_path",
            "ann_path",
        )
    }
    replacement[duplicate_field] = duplicate_value
    rows[1] = ManifestInput(**replacement)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        assign_document_splits(rows)


def test_split_rejects_wrong_total_or_per_cancer_counts() -> None:
    with pytest.raises(ValueError):
        assign_document_splits(_rows()[:-1])

    rows = _rows()
    rows[-1] = ManifestInput(**{**rows[-1].__dict__, "cancer_type": "breast"})
    with pytest.raises(ValueError):
        assign_document_splits(rows)


def test_split_rejects_unknown_cancer_and_is_stable_for_stratum_permutations() -> None:
    rows = _rows()
    interleaved = rows[::2] + rows[1::2]

    assert assign_document_splits(rows) == assign_document_splits(interleaved)

    rows[0] = ManifestInput(**{**rows[0].__dict__, "cancer_type": "other"})
    with pytest.raises(ValueError):
        assign_document_splits(rows)


def _write_invented_tree(project_root: Path) -> Path:
    annotated = project_root / "experiments" / "coral" / "data" / "raw" / "annotated"
    subject_rows = ["coral_idx,invented_site"]
    for directory, prefix in (("breastca", "breast"), ("pdac", "pancreatic")):
        cancer_dir = annotated / directory
        cancer_dir.mkdir(parents=True)
        for index in range(20):
            stem = f"{prefix}-{index:02d}"
            text = f"restricted invented note {stem} progression\r\n"
            (cancer_dir / f"{stem}.txt").write_bytes(text.encode())
            if index < 2:
                start = text.index("progression")
                records = (
                    f"T1\tDiseaseState {start} {start + len('progression')}\tprogression\n"
                    "A1\tDiseaseStateVal T1 progression-recurrence\n"
                    "A2\tNegationModalityVal T1 affirmed\n"
                )
            else:
                records = ""
            (cancer_dir / f"{stem}.ann").write_text(records, encoding="utf-8", newline="")
            subject_rows.append(f"{stem},site-{index % 3}")
    (annotated / "subject-info.csv").write_text(
        "\n".join(subject_rows) + "\n", encoding="utf-8", newline=""
    )
    (annotated / "annotation.conf").write_text(
        "[entities]\nDiseaseState\n", encoding="utf-8", newline=""
    )
    return annotated


@pytest.fixture
def invented_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    project_root = tmp_path / "checkout"
    annotated = _write_invented_tree(project_root)
    output = (
        project_root
        / "experiments"
        / "coral"
        / "data"
        / "manifests"
        / "oncology-current-progression-v1.json"
    )
    monkeypatch.setattr(manifest_module, "PROJECT_ROOT", project_root)
    return project_root, annotated, output


def _invoke(annotated: Path, output: Path):
    return CliRunner().invoke(main, [str(annotated), "--output", str(output)])


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value))
    return set()


def _raw_hashes(annotated: Path) -> dict[Path, str]:
    return {
        path: sha256_file(path)
        for path in sorted(item for item in annotated.rglob("*") if item.is_file())
    }


def test_cli_creates_aggregate_only_manifest_with_required_metadata(
    invented_tree: tuple[Path, Path, Path],
) -> None:
    _, annotated, output = invented_tree

    result = _invoke(annotated, output)

    assert result.exit_code == 0, result.output
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["dataset"] == {
        "name": "CORAL",
        "version": "1.0",
        "doi": "10.13026/v69y-xa45",
    }
    assert manifest["sensitivity"] == "deidentified"
    assert manifest["task"] == "oncology_current_progression"
    assert manifest["protocol_version"] == "1.0.0"
    assert manifest["adapter_version"] == "current-progression-candidate-1.0.0"
    assert manifest["split_policy_version"] == "current-progression-split-1.0.0"
    assert manifest["split_seed"] == 20260915
    assert manifest["split_counts"] == {"train": 20, "dev": 10, "test": 10}
    assert manifest["candidate_counts"] == {"positive": 4, "other": 36}
    assert manifest["candidate_flag_counts"]["clinical_review_required"] == 40
    assert len(manifest["documents"]) == 40
    assert set(manifest["documents"][0]) == {
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
    assert not _all_keys(manifest) & {
        "text",
        "quote",
        "spans",
        "start",
        "end",
        "attributes",
        "adjudication",
    }
    assert "restricted invented note" not in result.output
    assert set(result.output.splitlines()) >= {
        f"output: {output.resolve()}",
        "documents: 40",
        "splits: train=20 dev=10 test=10",
        "candidates: positive=4 other=36",
    }


def test_direct_file_command_retains_checkout_bootstrap() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "experiments"
        / "coral"
        / "scripts"
        / "coral_current_progression_manifest.py"
    )

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--output" in result.stdout


def test_build_manifest_hashes_all_inputs_and_records_candidate_quotas(
    invented_tree: tuple[Path, Path, Path],
) -> None:
    _, annotated, _ = invented_tree

    manifest = build_manifest(annotated)

    assert manifest["source_files"] == {
        "subject_info": {
            "path": "subject-info.csv",
            "sha256": sha256_file(annotated / "subject-info.csv"),
        },
        "annotation_config": {
            "path": "annotation.conf",
            "sha256": sha256_file(annotated / "annotation.conf"),
        },
    }
    assert manifest["candidate_quotas"] == {
        "breast": {"train": 1, "dev": 0, "test": 1},
        "pancreatic": {"train": 1, "dev": 1, "test": 0},
    }
    assert manifest["manifest_sha256"] == manifest_module.manifest_payload_hash(manifest)


def test_only_answered_true_is_in_the_positive_candidate_stratum(
    invented_tree: tuple[Path, Path, Path],
) -> None:
    _, annotated, _ = invented_tree
    cases = {
        "breast-02": (
            "T1\tDiseaseState {start} {end}\tprogression\n"
            "A1\tDiseaseStateVal T1 stability\n"
            "A2\tNegationModalityVal T1 affirmed\n"
        ),
        "breast-03": (
            "T1\tDiseaseState {start} {end}\tprogression\n"
            "A1\tDiseaseStateVal T1 progression-recurrence\n"
            "A2\tNegationModalityVal T1 uncertain_in_present\n"
        ),
        "breast-04": (
            "T1\tDiseaseState {start} {end}\tprogression\n"
            "A1\tDiseaseStateVal T1 progression-recurrence\n"
            "A2\tNegationModalityVal T1 affirmed\n"
            "T2\tDiseaseState {start} {end}\tprogression\n"
            "A3\tDiseaseStateVal T2 stability\n"
            "A4\tNegationModalityVal T2 affirmed\n"
        ),
    }
    for stem, template in cases.items():
        txt_path = annotated / "breastca" / f"{stem}.txt"
        text = manifest_module.read_text(txt_path)
        start = text.index("progression")
        (txt_path.with_suffix(".ann")).write_text(
            template.format(start=start, end=start + len("progression")),
            encoding="utf-8",
            newline="",
        )

    manifest = build_manifest(annotated)
    documents = {row["doc_id"]: row for row in manifest["documents"]}

    assert documents["breast-02"]["candidate_disposition"] == "answered"
    assert documents["breast-03"]["candidate_disposition"] == "insufficient_evidence"
    assert documents["breast-04"]["candidate_disposition"] == "conflicting_evidence"
    assert all(not documents[stem]["progression_candidate"] for stem in cases)
    assert manifest["candidate_counts"] == {"positive": 4, "other": 36}


@pytest.mark.parametrize("destination", ["outside", "input", "raw", "non_json"])
def test_cli_rejects_unsafe_output_destinations_without_creating_them(
    invented_tree: tuple[Path, Path, Path],
    destination: str,
) -> None:
    project_root, annotated, valid_output = invented_tree
    paths = {
        "outside": project_root / "elsewhere" / "manifest.json",
        "input": annotated / "manifest.json",
        "raw": annotated.parent / "manifest.json",
        "non_json": valid_output.with_suffix(".txt"),
    }
    output = paths[destination]

    result = _invoke(annotated, output)

    assert result.exit_code != 0
    assert "manifest destination rejected" in result.output
    assert not output.exists()


def test_cli_rejects_symlink_escape_and_input_file_alias(
    invented_tree: tuple[Path, Path, Path],
) -> None:
    project_root, annotated, _ = invented_tree
    manifest_dir = project_root / "experiments" / "coral" / "data" / "manifests"
    manifest_dir.parent.mkdir(parents=True, exist_ok=True)
    manifest_dir.symlink_to(annotated, target_is_directory=True)

    escaped = manifest_dir / "escaped.json"
    escape_result = _invoke(annotated, escaped)

    assert escape_result.exit_code != 0
    assert "manifest destination rejected" in escape_result.output
    assert not (annotated / "escaped.json").exists()

    manifest_dir.unlink()
    manifest_dir.mkdir()
    alias = manifest_dir / "alias.json"
    os.link(annotated / "annotation.conf", alias)
    before = alias.read_bytes()

    alias_result = _invoke(annotated, alias)

    assert alias_result.exit_code != 0
    assert "manifest destination rejected" in alias_result.output
    assert alias.read_bytes() == before


@pytest.mark.parametrize("redirect", ["allowed_root", "ancestor"])
def test_cli_rejects_output_authorization_roots_redirected_outside_checkout(
    invented_tree: tuple[Path, Path, Path],
    redirect: str,
) -> None:
    project_root, annotated, _ = invented_tree
    outside = project_root.parent / f"outside-{redirect}"
    if redirect == "allowed_root":
        outside.mkdir()
        outputs = project_root / "experiments" / "coral" / "outputs"
        outputs.symlink_to(outside, target_is_directory=True)
        output = outputs / "manifest.json"
    else:
        experiments = project_root / "experiments"
        experiments.rename(outside)
        experiments.symlink_to(outside, target_is_directory=True)
        annotated = project_root / "experiments" / "coral" / "data" / "raw" / "annotated"
        output = project_root / "experiments" / "coral" / "outputs" / "manifest.json"

    result = _invoke(annotated, output)

    assert result.exit_code != 0
    assert "manifest destination rejected" in result.output
    assert not (outside / "manifest.json").exists()
    assert not (outside / "coral" / "outputs" / "manifest.json").exists()


def test_cli_allows_a_canonicalized_alias_of_the_checkout_root(
    invented_tree: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _, _ = invented_tree
    alias = project_root.parent / "checkout-alias"
    alias.symlink_to(project_root, target_is_directory=True)
    monkeypatch.setattr(manifest_module, "PROJECT_ROOT", alias)
    annotated = alias / "experiments" / "coral" / "data" / "raw" / "annotated"
    output = alias / "experiments" / "coral" / "outputs" / "manifest.json"

    result = _invoke(annotated, output)

    assert result.exit_code == 0, result.output
    assert output.is_file()


@pytest.mark.parametrize("parent_existed", [False, True])
def test_cli_rejects_output_parent_redirected_during_manifest_build(
    invented_tree: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    parent_existed: bool,
) -> None:
    project_root, annotated, _ = invented_tree
    output_parent = project_root / "experiments" / "coral" / "outputs"
    displaced_parent = project_root / "displaced-outputs"
    if parent_existed:
        output_parent.mkdir()
    original = manifest_module.build_manifest

    def redirect_after_build(root: Path) -> dict[str, object]:
        manifest = original(root)
        if parent_existed:
            output_parent.rename(displaced_parent)
        output_parent.symlink_to(annotated, target_is_directory=True)
        return manifest

    monkeypatch.setattr(manifest_module, "build_manifest", redirect_after_build)
    output = output_parent / "manifest.json"

    result = _invoke(annotated, output)

    assert result.exit_code != 0
    assert "manifest write failed" in result.output
    assert not (annotated / "manifest.json").exists()
    assert not output.exists()
    assert not (displaced_parent / "manifest.json").exists()


def test_cli_fails_closed_when_directory_safe_operations_are_unavailable(
    invented_tree: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, annotated, output = invented_tree
    monkeypatch.setattr(manifest_module, "_SAFE_DIRECTORY_OPERATIONS", False)

    result = _invoke(annotated, output)

    assert result.exit_code != 0
    assert "manifest destination rejected" in result.output
    assert not output.parent.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "orphan",
        "duplicate_doc_id",
        "unknown_cancer",
        "missing_subject",
        "duplicate_coral_idx",
        "unannotated",
        "unreadable",
    ],
)
def test_cli_fails_closed_for_malformed_or_unsafe_inputs(
    invented_tree: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    project_root, annotated, output = invented_tree
    if mutation == "orphan":
        (annotated / "breastca" / "breast-00.ann").unlink()
    elif mutation == "duplicate_doc_id":
        source = annotated / "pdac" / "pancreatic-00"
        (source.with_suffix(".txt")).rename(source.with_name("breast-00.txt"))
        (source.with_suffix(".ann")).rename(source.with_name("breast-00.ann"))
    elif mutation == "unknown_cancer":
        (annotated / "breastca").rename(annotated / "unknown")
    elif mutation == "missing_subject":
        subject = annotated / "subject-info.csv"
        rows = subject.read_text(encoding="utf-8").splitlines()
        subject.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
    elif mutation == "duplicate_coral_idx":
        subject = annotated / "subject-info.csv"
        rows = subject.read_text(encoding="utf-8").splitlines()
        rows[-1] = rows[-2]
        subject.write_text("\n".join(rows) + "\n", encoding="utf-8")
    elif mutation == "unannotated":
        annotated = annotated.rename(annotated.with_name("unannotated"))
    else:
        original = manifest_module.read_text

        def reject_one(path: Path) -> str:
            if path.name == "breast-00.txt":
                raise PermissionError("invented secret error")
            return original(path)

        monkeypatch.setattr(manifest_module, "read_text", reject_one)

    result = _invoke(annotated, output)

    assert result.exit_code != 0
    assert "manifest input rejected" in result.output
    assert "invented secret error" not in result.output
    assert not output.exists()
    assert not (project_root / "elsewhere").exists()


def test_incomplete_parser_inventory_is_counted_as_an_insufficient_other_candidate(
    invented_tree: tuple[Path, Path, Path],
) -> None:
    _, annotated, output = invented_tree
    (annotated / "breastca" / "breast-00.ann").write_text(
        "malformed invented annotation\n", encoding="utf-8"
    )

    result = _invoke(annotated, output)

    assert result.exit_code == 0, result.output
    manifest = json.loads(output.read_text(encoding="utf-8"))
    document = next(row for row in manifest["documents"] if row["doc_id"] == "breast-00")
    assert document["progression_candidate"] is False
    assert document["candidate_disposition"] == "insufficient_evidence"
    assert manifest["diagnostic_counts"]["incomplete_annotation_inventory"] == 1
    assert "incomplete_inventories: 1" in result.output


def test_identical_rerun_preserves_manifest_bytes_and_mtime(
    invented_tree: tuple[Path, Path, Path],
) -> None:
    _, annotated, output = invented_tree
    first = _invoke(annotated, output)
    assert first.exit_code == 0, first.output
    before_bytes = output.read_bytes()
    before_mtime = output.stat().st_mtime_ns

    second = _invoke(annotated, output)

    assert second.exit_code == 0, second.output
    assert output.read_bytes() == before_bytes
    assert output.stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize(
    "mutation",
    [
        "txt",
        "ann",
        "subject",
        "config",
        "candidate",
        "protocol_version",
        "adapter_version",
        "seed",
        "split_membership",
    ],
)
def test_frozen_manifest_rejects_every_change_without_replacement(
    invented_tree: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    _, annotated, output = invented_tree
    created = _invoke(annotated, output)
    assert created.exit_code == 0, created.output
    if mutation == "txt":
        (annotated / "breastca" / "breast-00.txt").write_bytes(b"changed invented source")
    elif mutation == "ann":
        with (annotated / "breastca" / "breast-00.ann").open("ab") as handle:
            handle.write(b"\n")
    elif mutation == "subject":
        with (annotated / "subject-info.csv").open("ab") as handle:
            handle.write(b"\n")
    elif mutation == "config":
        with (annotated / "annotation.conf").open("ab") as handle:
            handle.write(b"\n")
    elif mutation == "candidate":
        original = manifest_module.derive_current_progression_candidate

        def change_candidate(document):
            candidate = original(document)
            if document.doc_id == "breast-02":
                return replace(candidate, disposition="answered", value=True)
            return candidate

        monkeypatch.setattr(
            manifest_module, "derive_current_progression_candidate", change_candidate
        )
    elif mutation == "protocol_version":
        monkeypatch.setattr(manifest_module, "PROTOCOL_VERSION", "1.0.1")
    elif mutation == "adapter_version":
        monkeypatch.setattr(manifest_module, "ADAPTER_VERSION", "changed")
    elif mutation == "seed":
        monkeypatch.setattr(manifest_module, "SPLIT_SEED", 1)
    else:
        payload = json.loads(output.read_text(encoding="utf-8"))
        current = payload["documents"][0]["split"]
        payload["documents"][0]["split"] = next(
            split for split in ("train", "dev", "test") if split != current
        )
        payload["manifest_sha256"] = manifest_module.manifest_payload_hash(payload)
        output.write_bytes(manifest_module.presentation_bytes(payload))
    before_raw = _raw_hashes(annotated)
    before_manifest = output.read_bytes()

    result = _invoke(annotated, output)

    assert result.exit_code != 0
    assert "immutable manifest conflict" in result.output
    assert output.read_bytes() == before_manifest
    assert _raw_hashes(annotated) == before_raw


@pytest.mark.parametrize("corruption", ["json", "self_hash"])
def test_corrupt_existing_manifest_is_rejected_without_replacement(
    invented_tree: tuple[Path, Path, Path],
    corruption: str,
) -> None:
    _, annotated, output = invented_tree
    assert _invoke(annotated, output).exit_code == 0
    if corruption == "json":
        output.write_bytes(b"{not-json")
    else:
        payload = json.loads(output.read_text(encoding="utf-8"))
        payload["manifest_sha256"] = "0" * 64
        output.write_bytes(manifest_module.presentation_bytes(payload))
    before = output.read_bytes()
    raw_before = _raw_hashes(annotated)

    result = _invoke(annotated, output)

    assert result.exit_code != 0
    assert "immutable manifest conflict" in result.output
    assert output.read_bytes() == before
    assert _raw_hashes(annotated) == raw_before


def test_racing_writer_cannot_be_overwritten(
    invented_tree: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, annotated, output = invented_tree
    proposed = build_manifest(annotated)
    winner = dict(proposed)
    winner["protocol_version"] = "winner-version"
    winner["manifest_sha256"] = manifest_module.manifest_payload_hash(winner)
    winner_bytes = manifest_module.presentation_bytes(winner)
    real_link = os.link

    def lose_race(source: str, destination: str, **kwargs: object) -> None:
        output.write_bytes(winner_bytes)
        raise FileExistsError

    monkeypatch.setattr(manifest_module.os, "link", lose_race)

    result = _invoke(annotated, output)

    monkeypatch.setattr(manifest_module.os, "link", real_link)
    assert result.exit_code != 0
    assert "immutable manifest conflict" in result.output
    assert output.read_bytes() == winner_bytes
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_interrupted_publication_leaves_no_partial_destination(
    invented_tree: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, annotated, output = invented_tree

    def interrupt(source: str, destination: str, **kwargs: object) -> None:
        raise OSError("invented restricted write detail")

    monkeypatch.setattr(manifest_module.os, "link", interrupt)

    result = _invoke(annotated, output)

    assert result.exit_code != 0
    assert "manifest write failed" in result.output
    assert "restricted write detail" not in result.output
    assert not output.exists()
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_descriptor_exhaustion_is_a_safe_write_failure_without_mutation(
    invented_tree: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, annotated, output = invented_tree
    raw_before = _raw_hashes(annotated)

    def exhaust_descriptors(descriptor: int) -> int:
        raise OSError("invented sensitive descriptor detail")

    monkeypatch.setattr(manifest_module.os, "dup", exhaust_descriptors)

    result = _invoke(annotated, output)

    assert result.exit_code != 0
    assert "manifest write failed" in result.output
    assert "sensitive descriptor detail" not in result.output
    assert not output.exists()
    assert _raw_hashes(annotated) == raw_before


def test_existing_fifo_destination_is_rejected_without_blocking(
    invented_tree: tuple[Path, Path, Path],
) -> None:
    project_root, annotated, _ = invented_tree
    output = project_root / "experiments" / "coral" / "outputs" / "manifest.json"
    output.parent.mkdir(parents=True)
    os.mkfifo(output)
    raw_before = _raw_hashes(annotated)
    code = (
        "import pathlib; "
        "from experiments.coral.scripts import coral_current_progression_manifest as module; "
        "module.PROJECT_ROOT = pathlib.Path(__import__('sys').argv[1]); "
        "module.main(args=[__import__('sys').argv[2], '--output', __import__('sys').argv[3]])"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(project_root), str(annotated), str(output)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        pytest.fail("manifest command blocked while opening an existing FIFO")

    assert process.returncode != 0
    assert "immutable manifest conflict" in stdout + stderr
    assert stat.S_ISFIFO(output.stat().st_mode)
    assert _raw_hashes(annotated) == raw_before
