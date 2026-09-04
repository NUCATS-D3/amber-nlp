#!/usr/bin/env python3
"""Load CORAL's BRAT standoff annotations and classify how they diverge from the text.

Usage:
    coral_ingest.py <root>
    coral_ingest.py <root> --jsonl out/coral_mentions.jsonl --category redacted

The .txt is the source of truth: text[start:end] IS the span, and is what any model
sees. The quote field in the .ann is a redundant copy that CORAL does not maintain --
its notes were re-redacted after annotation with length-preserving surrogates, so a
quote can disagree with the text while the offsets remain correct. Rather than calling
that an error, each entity is classified:

    exact        text[start:end] == quote
    whitespace   equal once whitespace is normalized (line wraps, trailing tabs)
    redacted     same length, different characters -- offsets good, gold surface stale
    mismatch     lengths differ -- quote unusable, inspect before trusting the span
    discontinuous  BRAT "start end;start end"; needs an Inclusion policy

Only `mismatch` warrants investigation. `redacted` entities have sound offsets but a
corrupted surface form, so they should be excluded from surface-form scoring.

Stdlib plus click. Text is read with newline="" so Python does not rewrite \r\n and
shift every offset left.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import click

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


@click.command()
@click.argument("root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--jsonl",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="Write normalized mentions here.",
)
@click.option(
    "--category",
    type=click.Choice(["exact", "whitespace", "redacted", "mismatch", "discontinuous"]),
    default=None,
    help="Print examples from one status only.",
)
@click.option("--show", type=int, default=10, show_default=True, help="How many examples to print.")
@click.option(
    "--dump-unparsed",
    is_flag=True,
    help="Print every record the parser did not recognise, with its line number.",
)
def main(
    root: Path, jsonl: Path | None, category: str | None, show: int, dump_unparsed: bool
) -> None:
    """Load CORAL BRAT annotations under ROOT and classify span/quote agreement."""
    ann_paths = sorted(p for p in root.rglob("*.ann") if p.with_suffix(".txt").exists())
    if not ann_paths:
        raise click.ClickException(f"no .ann/.txt pairs found under {root}")

    docs = [parse_ann(p, read_text(p.with_suffix(".txt"))) for p in ann_paths]
    entities = [e for d in docs for e in d.entities.values()]

    click.echo(f"documents      {len(docs)}")
    click.echo(f"entities       {len(entities)}")
    click.echo(f"attributes     {sum(len(d.attributes) for d in docs)}")
    click.echo(f"relations      {sum(len(d.relations) for d in docs)}")
    n_events = sum(len(d.events) for d in docs)
    n_equivs = sum(len(d.equivs) for d in docs)
    if n_events:
        click.echo(f"events         {n_events}")
    if n_equivs:
        click.echo(f"equiv sets     {n_equivs}")
    orphans = sum(len(d.unparsed) for d in docs)
    if orphans:
        click.echo(f"unparsed lines {orphans}")

    status_counts = Counter(e.status for e in entities)
    click.echo("\nspan/quote agreement")
    for name in ("exact", "whitespace", "redacted", "mismatch", "discontinuous"):
        n = status_counts.get(name, 0)
        pct = 100 * n / len(entities) if entities else 0
        click.echo(f"  {n:6d}  {pct:5.1f}%  {name}")

    click.echo("\nentity types")
    for name, count in Counter(e.type for e in entities).most_common():
        bad = sum(1 for e in entities if e.type == name and e.status == "redacted")
        note = f"   ({bad} redacted)" if bad else ""
        click.echo(f"  {count:6d}  {name}{note}")

    attr_values: dict[str, Counter] = defaultdict(Counter)
    for d in docs:
        for a in d.attributes:
            attr_values[a.type][a.value if a.value is not None else "<binary>"] += 1
    click.echo("\nattribute types and value sets")
    for name in sorted(attr_values):
        total = sum(attr_values[name].values())
        vals = ", ".join(f"{v} ({c})" for v, c in attr_values[name].most_common())
        click.echo(f"  {total:6d}  {name}: {vals}")

    click.echo("\nrelation types")
    rels = [r for d in docs for r in d.relations]
    sym = Counter(r.type for r in rels if r.symmetric)
    for name, count in Counter(r.type for r in rels).most_common():
        note = f"   ({sym[name]} undirected)" if sym.get(name) else ""
        click.echo(f"  {count:6d}  {name}{note}")
    if sym:
        click.echo("  undirected = written as a '*' line; argument order is not meaningful")

    if n_events:
        click.echo("\nevent types")
        for name, count in Counter(e.type for d in docs for e in d.events).most_common():
            click.echo(f"  {count:6d}  {name}")

    disc = [e for e in entities if e.status == "discontinuous"]
    if disc:
        click.echo(f"\ndiscontinuous spans by type  ({len(disc)} total)")
        for name, count in Counter(e.type for e in disc).most_common():
            gaps = [
                max(0, e.spans[i + 1][0] - e.spans[i][1])
                for e in disc
                if e.type == name
                for i in range(len(e.spans) - 1)
            ]
            widest = max(gaps) if gaps else 0
            frags = Counter(len(e.spans) for e in disc if e.type == name)
            frag_desc = ", ".join(f"{k} frags x{v}" for k, v in sorted(frags.items()))
            click.echo(f"  {count:6d}  {name}   (widest gap {widest} chars; {frag_desc})")
        click.echo("  widest gap is what widening to the enclosing interval would swallow")

    if dump_unparsed:
        rows = [(d.doc_id, ln, rec) for d in docs for ln, rec in d.unparsed]
        click.echo(f"\nunparsed records  ({len(rows)})")
        for name, count in Counter(r[2][:1] for r in rows).most_common():
            click.echo(f"  prefix {name!r}: {count}")
        for doc_id, ln, rec in rows:
            click.echo(f"  {doc_id}:{ln}  {rec[:200]!r}")

    wanted = category or "mismatch"
    examples = [(d, e) for d in docs for e in d.entities.values() if e.status == wanted]
    if examples:
        click.echo(f"\nfirst {min(show, len(examples))} of {len(examples)} '{wanted}'")
        for d, e in examples[:show]:
            click.echo(f"  {d.doc_id} {e.ann_id} ({e.type}) {e.spans} span_len={e.span_len}")
            click.echo(f"    text  {e.text_quote!r}")
            click.echo(f"    ann   {e.quote!r}")

    if jsonl:
        jsonl.parent.mkdir(parents=True, exist_ok=True)
        with jsonl.open("w", encoding="utf-8") as fh:
            for d in docs:
                attrs: dict[str, dict[str, str | None]] = defaultdict(dict)
                for a in d.attributes:
                    attrs[a.target][a.type] = a.value
                for e in d.entities.values():
                    fh.write(
                        json.dumps(
                            {
                                "doc_id": d.doc_id,
                                "ann_id": e.ann_id,
                                "mention_type": e.type,
                                "start": e.start,
                                "end": e.end,
                                "spans": e.spans,
                                "quote": e.text_quote,
                                "ann_quote": e.quote,
                                "status": e.status,
                                "attributes": attrs.get(e.ann_id, {}),
                            }
                        )
                        + "\n"
                    )
        click.echo(f"\nwrote {jsonl}")


if __name__ == "__main__":
    main()
