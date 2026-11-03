"""Parquet files for bundles (contract v0.1 sections 5.2, 5.4, 5.5); needs ``amber[bundles]``.

JSON columns are UTF-8 strings holding canonical JSON; timestamps are ``timestamp[us, UTC]``.
Readers check each file's ``file_sha256`` when the manifest has one, the Arrow type of every
column, and that every JSON value is canonical; content hashes are checked by
``check_bundle``, before any validation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError as error:  # pragma: no cover - exercised by the core-only install check
    raise ImportError("amber Parquet bundles need pyarrow; install amber[bundles]") from error

from pydantic import ValidationError

from amber.bundles.core import (
    Bundle,
    BundleError,
    Manifest,
    ManifestTable,
    Row,
    sorted_rows,
)
from amber.bundles.schema import TABLES, ColumnType
from amber.ids import canonical_json
from amber.schemas._wire import check_schema_version

_ARROW: dict[ColumnType, pa.DataType] = {
    "string": pa.string(),
    "int64": pa.int64(),
    "float64": pa.float64(),
    "bool": pa.bool_(),
    "timestamp": pa.timestamp("us", tz="UTC"),
    "json": pa.string(),
}
MANIFEST = "manifest.json"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arrow_table(name: str, rows: tuple[Row, ...]) -> pa.Table:
    schema = TABLES[name]
    ordered = sorted_rows(name, rows)
    arrays: dict[str, pa.Array] = {}
    fields: list[pa.Field] = []
    for column in schema.columns:
        values = [row[column.name] for row in ordered]
        if column.type == "json":
            values = [None if value is None else canonical_json(value) for value in values]
        if column.type == "float64":
            values = [None if value is None else float(value) for value in values]
        arrays[column.name] = pa.array(values, type=_ARROW[column.type])
        fields.append(pa.field(column.name, _ARROW[column.type], nullable=column.nullable))
    extras = sorted({key for row in ordered for key in row if key.startswith("x_")})
    for extra in extras:
        array = pa.array([row.get(extra) for row in ordered])
        arrays[extra] = array
        fields.append(pa.field(extra, array.type))
    return pa.Table.from_arrays(list(arrays.values()), schema=pa.schema(fields))


def write_bundle(bundle: Bundle, directory: Path, *, file_hashes: bool = True) -> Bundle:
    """Write every table and the manifest; return the bundle with ``file_sha256`` recorded."""
    directory.mkdir(parents=True, exist_ok=False)
    tables: dict[str, ManifestTable] = {}
    for name, rows in bundle.tables.items():
        path = directory / TABLES[name].file
        pq.write_table(_arrow_table(name, rows), path)
        declared = bundle.manifest.tables[name]
        tables[name] = declared.model_copy(
            update={"file_sha256": _file_sha256(path) if file_hashes else None}
        )
    manifest = bundle.manifest.model_copy(update={"tables": tables})
    (directory / MANIFEST).write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return Bundle(manifest=manifest, tables=bundle.tables)


def _parse_json(name: str, column: str, text: Any) -> Any:
    if text is None:
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise BundleError("non_canonical_json", name, column) from None
    if canonical_json(value) != text:
        raise BundleError("non_canonical_json", name, column)
    return value


def _rows(name: str, table: pa.Table) -> tuple[Row, ...]:
    schema = TABLES[name]
    declared = {column.name: column for column in schema.columns}
    for field in table.schema:
        column = declared.get(field.name)
        if column is None:
            if not field.name.startswith("x_"):
                raise BundleError("unknown_column", name, field.name)
        elif field.type != _ARROW[column.type]:
            raise BundleError("wrong_type", name, field.name)
    for column in schema.columns:
        if column.name not in table.column_names:
            raise BundleError("missing_column", name, column.name)
    rows = table.to_pylist()
    for row in rows:
        for column in schema.columns:
            if column.type == "json":
                row[column.name] = _parse_json(name, column.name, row[column.name])
    return tuple(rows)


def read_bundle(directory: Path) -> Bundle:
    """Read a bundle's files; the result still needs ``check_bundle`` or ``load_bundle``."""
    try:
        raw = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BundleError("invalid_manifest") from None
    try:
        check_schema_version(raw.get("schema_version") if isinstance(raw, dict) else None)
    except ValueError:
        raise BundleError("unsupported_version") from None
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError:
        raise BundleError("invalid_manifest") from None
    tables: dict[str, tuple[Row, ...]] = {}
    for name, declared in manifest.tables.items():
        if name not in TABLES:
            raise BundleError("unexpected_table", name)
        path = directory / TABLES[name].file
        if not path.exists():
            raise BundleError("missing_table", name)
        if declared.file_sha256 is not None and declared.file_sha256 != _file_sha256(path):
            raise BundleError("file_mismatch", name)
        tables[name] = _rows(name, pq.read_table(path))
    for name, schema in TABLES.items():
        if name not in tables and (directory / schema.file).exists():
            raise BundleError("unexpected_table", name)
    return Bundle(manifest=manifest, tables=MappingProxyType(tables))
