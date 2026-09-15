"""Experiment-local BRAT records and quote-agreement parsing.

Source files are read without newline translation. Copied annotation quotes are retained
alongside source slices; discontinuous text_quote values concatenate fragments, not gaps.
The existing exact/whitespace/redacted/mismatch/discontinuous categories are audit
heuristics, not evidence validation: bounds and malformed-record handling still need
hardening before this parser can support candidate or gold derivation.

This module uses only the standard library and does not import Amber or the audit CLI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

RECORD_START = re.compile(r"(?:[TRNAMEX]\d+|#\d+|\*)\t")
T_HEADER = re.compile(r"^(T\d+)\t(\S+) (\d+ \d+(?:;\d+ \d+)*)\t?")


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
    lines = re.sub(r"\r?\n\Z", "", raw).split("\n")
    records: list[tuple[int, str]] = []
    want = 0  # remaining characters the open record may still absorb

    for lineno, line in enumerate(lines, start=1):
        starts_record = bool(RECORD_START.match(line))
        if starts_record or not records:
            records.append((lineno, line))
            want = 0
            m = T_HEADER.match(line)
            if m:
                spans = [tuple(int(x) for x in c.split(" ")) for c in m.group(3).split(";")]
                span_len = sum(hi - lo for lo, hi in spans)
                quote_so_far = line[m.end() :]
                want = span_len - len(quote_so_far)
        elif want > 0:
            ln, body = records[-1]
            records[-1] = (ln, body + "\n" + line)
            want -= len(line) + 1
        else:
            records.append((lineno, line))  # orphan; reported as unparsed
            want = 0
    return records


def parse_ann(path: Path, text: str) -> Document:
    doc = Document(doc_id=path.stem, path=path, text=text)

    for lineno, record in split_records(read_text(path)):
        if not record.strip():
            continue
        parts = record.split("\t", 2)
        ann_id = parts[0]
        kind = ann_id[:1]

        if kind == "T" and len(parts) >= 3:
            type_name, _, offsets = parts[1].partition(" ")
            spans = [tuple(int(x) for x in c.strip().split(" ")) for c in offsets.split(";")]
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

        elif kind in {"A", "M"} and len(parts) >= 2:
            bits = parts[1].split(" ")
            doc.attributes.append(
                Attribute(ann_id, bits[0], bits[1], bits[2] if len(bits) > 2 else None)
            )

        elif kind == "R" and len(parts) >= 2:
            bits = parts[1].split(" ")
            args = {}
            for bit in bits[1:]:
                role, _, target = bit.partition(":")
                args[role] = target
            doc.relations.append(Relation(ann_id, bits[0], args))

        elif kind == "E" and len(parts) >= 2:
            bits = parts[1].split(" ")
            head_role, _, trigger = bits[0].partition(":")
            args = {}
            for bit in bits[1:]:
                role, _, target = bit.partition(":")
                args[role] = target
            doc.events.append(Event(ann_id, head_role, trigger, args))

        elif kind == "*" and len(parts) >= 2:
            # BRAT's symmetric-relation form. CORAL uses it for real relation types
            # (BiomarkerRel, TreatmentDesc, ...) alongside directed R lines of the
            # same type. Argument order is not meaningful here -- direction has to be
            # recovered from the entity types of the arguments.
            bits = parts[1].split(" ")
            if bits and bits[0] == "Equiv":
                doc.equivs.append(bits[1:])
            elif len(bits) >= 3:
                args = {f"Arg{i}": t for i, t in enumerate(bits[1:], start=1)}
                doc.relations.append(Relation(ann_id, bits[0], args, symmetric=True))
            else:
                doc.unparsed.append((lineno, record))

        elif kind in {"N", "#"}:
            continue

        else:
            doc.unparsed.append((lineno, record))

    return doc
