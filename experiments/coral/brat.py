"""Experiment-local BRAT records and quote-agreement parsing.

Source files are read without newline translation. Copied annotation quotes are retained
alongside source slices; discontinuous text_quote values concatenate fragments, not gaps.
The existing exact/whitespace/redacted/mismatch/discontinuous categories are audit
heuristics, not evidence validation. In particular, the ``redacted`` heuristic does not
prove that offsets are in bounds; candidate tooling must validate bounds independently.

This module uses only the standard library and does not import Amber or the audit CLI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

RECORD_START = re.compile(r"(?:[A-Z]\d+|#\d+|\*)\t")
T_HEADER = re.compile(r"^(T\d+)\t(\S+) (\d+ \d+(?:;\d+ \d+)*)\t?")
RECORD_ID = re.compile(r"^(?:[A-Z]\d+|#\d+)$")

DiagnosticCode = Literal[
    "malformed_record",
    "unknown_record",
    "duplicate_id",
    "duplicate_attribute",
    "conflicting_attribute",
    "dangling_reference",
]


@dataclass(frozen=True)
class ParseDiagnostic:
    code: DiagnosticCode
    record_id: str | None
    line_number: int


@dataclass
class Entity:
    ann_id: str
    type: str
    spans: list[tuple[int, int]]
    quote: str  # as recorded in the .ann, possibly stale
    text_quote: str  # text[start:end], authoritative
    status: str  # exact | whitespace | redacted | mismatch | discontinuous

    @property
    def start(self) -> int:
        return self.spans[0][0]

    @property
    def end(self) -> int:
        return self.spans[-1][1]

    @property
    def span_len(self) -> int:
        return sum(hi - lo for lo, hi in self.spans)


@dataclass
class Attribute:
    ann_id: str
    type: str
    target: str
    value: str | None


@dataclass
class Relation:
    ann_id: str
    type: str
    args: dict[str, str]
    symmetric: bool = False  # came from a "*" line: argument order is NOT meaningful


@dataclass
class Event:
    ann_id: str
    type: str
    trigger: str
    args: dict[str, str]


@dataclass
class Document:
    doc_id: str
    path: Path
    text: str
    entities: dict[str, Entity] = field(default_factory=dict)
    attributes: list[Attribute] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    equivs: list[list[str]] = field(default_factory=list)
    unparsed: list[tuple[int, str]] = field(default_factory=list)
    diagnostics: list[ParseDiagnostic] = field(default_factory=list)

    @property
    def annotation_inventory_complete(self) -> bool:
        """Whether every raw annotation record has one unambiguous parse."""
        return not self.diagnostics and not self.unparsed


def read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def norm_ws(s: str) -> str:
    return " ".join(s.split())


def split_records(raw: str) -> list[tuple[int, str]]:
    """Split an .ann into records.

    A T record's reference text is verbatim, so an entity spanning a line break puts a
    literal newline inside its record and naive line iteration truncates the quote.
    But joining every non-id line is equally wrong -- it swallows unrelated content.
    The offsets bound the answer: keep absorbing continuation lines only while the
    accumulated quote is still shorter than the span the header declares.
    """
    records: list[tuple[int, str]] = []
    want = 0  # remaining characters the open record may still absorb
    separator_before = ""

    for lineno, physical_line in enumerate(raw.splitlines(keepends=True), start=1):
        if physical_line.endswith("\r\n"):
            line, separator_after = physical_line[:-2], "\r\n"
        elif physical_line.endswith(("\r", "\n")):
            line, separator_after = physical_line[:-1], physical_line[-1]
        else:
            line, separator_after = physical_line, ""
        starts_record = bool(RECORD_START.match(line))
        if starts_record or not records:
            records.append((lineno, line))
            want = 0
            m = T_HEADER.match(line)
            if m:
                try:
                    spans = [tuple(int(x) for x in c.split(" ")) for c in m.group(3).split(";")]
                except ValueError:
                    # Preserve the record for parse_ann's fixed malformed-record diagnostic.
                    pass
                else:
                    span_len = sum(hi - lo for lo, hi in spans)
                    quote_so_far = line[m.end() :]
                    want = span_len - len(quote_so_far)
        elif want > 0:
            ln, body = records[-1]
            records[-1] = (ln, body + separator_before + line)
            want -= len(line) + len(separator_before)
        else:
            records.append((lineno, line))  # orphan; reported as unparsed
            want = 0
        separator_before = separator_after
    return records


def parse_ann(path: Path, text: str) -> Document:
    doc = Document(doc_id=path.stem, path=path, text=text)
    records = [(line, record) for line, record in split_records(read_text(path)) if record.strip()]

    id_records: dict[str, list[tuple[int, str]]] = {}
    for lineno, record in records:
        ann_id = record.split("\t", 1)[0]
        if RECORD_ID.fullmatch(ann_id):
            id_records.setdefault(ann_id, []).append((lineno, record))

    duplicate_ids = {ann_id for ann_id, rows in id_records.items() if len(rows) > 1}
    for ann_id in duplicate_ids:
        rows = id_records[ann_id]
        doc.unparsed.extend(rows)
        for lineno, _ in rows[1:]:
            doc.diagnostics.append(ParseDiagnostic("duplicate_id", ann_id, lineno))

    known_ids: set[str] = set()
    references: list[tuple[str | None, int, set[str]]] = []
    attribute_rows: list[tuple[Attribute, int]] = []

    for lineno, record in records:
        parts = record.split("\t", 2)
        ann_id = parts[0]
        if ann_id in duplicate_ids:
            continue
        kind = ann_id[:1]

        try:
            if kind == "T" and RECORD_ID.fullmatch(ann_id) and len(parts) == 3:
                type_name, separator, offsets = parts[1].partition(" ")
                if (
                    not type_name
                    or not separator
                    or not re.fullmatch(r"\d+ \d+(?:;\d+ \d+)*", offsets)
                ):
                    raise ValueError
                spans = [tuple(map(int, fragment.split(" "))) for fragment in offsets.split(";")]
                quote = parts[2]
                text_quote = "".join(text[lo:hi] for lo, hi in spans)

                if len(spans) > 1:
                    status = "discontinuous"
                elif text_quote == quote:
                    status = "exact"
                elif norm_ws(text_quote) == norm_ws(quote):
                    status = "whitespace"
                elif len(text_quote) == len(quote):
                    status = "redacted"
                else:
                    status = "mismatch"

                doc.entities[ann_id] = Entity(ann_id, type_name, spans, quote, text_quote, status)

            elif kind in {"A", "M"} and RECORD_ID.fullmatch(ann_id):
                bits = _metadata_body_with_empty_tail(parts).split()
                if len(bits) not in {2, 3}:
                    raise ValueError
                attribute = Attribute(ann_id, bits[0], bits[1], bits[2] if len(bits) == 3 else None)
                doc.attributes.append(attribute)
                attribute_rows.append((attribute, lineno))
                references.append((ann_id, lineno, {attribute.target}))

            elif kind == "R" and RECORD_ID.fullmatch(ann_id):
                bits = _metadata_body_with_empty_tail(parts).split()
                args = _parse_role_targets(bits[1:], minimum=2)
                doc.relations.append(Relation(ann_id, bits[0], args))
                references.append((ann_id, lineno, set(args.values())))

            elif kind == "E" and RECORD_ID.fullmatch(ann_id):
                bits = _metadata_body_with_empty_tail(parts).split()
                if not bits:
                    raise ValueError
                head = _parse_role_targets(bits[:1], minimum=1)
                args = _parse_role_targets(bits[1:], minimum=0)
                head_role, trigger = next(iter(head.items()))
                doc.events.append(Event(ann_id, head_role, trigger, args))
                references.append((ann_id, lineno, {trigger, *args.values()}))

            elif kind == "*" and ann_id == "*":
                bits = _metadata_body_with_empty_tail(parts).split()
                if len(bits) < 3:
                    raise ValueError
                targets = bits[1:]
                if bits[0] == "Equiv":
                    doc.equivs.append(targets)
                else:
                    # BRAT's symmetric-relation form. Argument order is not meaningful.
                    args = {f"Arg{i}": target for i, target in enumerate(targets, start=1)}
                    doc.relations.append(Relation(ann_id, bits[0], args, symmetric=True))
                references.append((None, lineno, set(targets)))

            elif kind == "N" and RECORD_ID.fullmatch(ann_id) and len(parts) in {2, 3}:
                bits = parts[1].split()
                if len(bits) != 3:
                    raise ValueError
                references.append((ann_id, lineno, {bits[1]}))

            elif kind == "#" and RECORD_ID.fullmatch(ann_id) and len(parts) == 3:
                bits = parts[1].split()
                if len(bits) != 2:
                    raise ValueError
                references.append((ann_id, lineno, {bits[1]}))

            elif kind in {"T", "A", "M", "R", "E", "N", "#", "*"}:
                raise ValueError
            else:
                doc.unparsed.append((lineno, record))
                record_id = ann_id if RECORD_ID.fullmatch(ann_id) else None
                doc.diagnostics.append(ParseDiagnostic("unknown_record", record_id, lineno))
                continue
        except (IndexError, TypeError, ValueError):
            doc.unparsed.append((lineno, record))
            record_id = ann_id if RECORD_ID.fullmatch(ann_id) else None
            doc.diagnostics.append(ParseDiagnostic("malformed_record", record_id, lineno))
            continue

        if ann_id != "*":
            known_ids.add(ann_id)

    seen_attributes: dict[tuple[str, str], set[str | None]] = {}
    for attribute, lineno in attribute_rows:
        key = (attribute.target, attribute.type)
        if key in seen_attributes:
            code: DiagnosticCode = (
                "duplicate_attribute"
                if attribute.value in seen_attributes[key]
                else "conflicting_attribute"
            )
            doc.diagnostics.append(ParseDiagnostic(code, attribute.ann_id, lineno))
            seen_attributes[key].add(attribute.value)
        else:
            seen_attributes[key] = {attribute.value}

    for record_id, lineno, targets in references:
        if targets - known_ids:
            doc.diagnostics.append(ParseDiagnostic("dangling_reference", record_id, lineno))

    doc.unparsed.sort(key=lambda item: item[0])
    doc.diagnostics.sort(key=lambda item: (item.line_number, item.code, item.record_id or ""))

    return doc


def _parse_role_targets(bits: list[str], *, minimum: int) -> dict[str, str]:
    if len(bits) < minimum:
        raise ValueError
    args: dict[str, str] = {}
    for bit in bits:
        role, separator, target = bit.partition(":")
        if not role or not separator or not target or role in args:
            raise ValueError
        args[role] = target
    return args


def _metadata_body_with_empty_tail(parts: list[str]) -> str:
    """Apply CORAL's compatibility policy for metadata-only BRAT records."""
    if len(parts) == 2 or (len(parts) == 3 and parts[2] == ""):
        return parts[1]
    raise ValueError
