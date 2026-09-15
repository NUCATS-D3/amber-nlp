"""Command-line CORAL annotation audit.

Run from the repository root with python -m experiments.coral.audit ROOT --show 0.
The legacy scripts/coral_ingest.py command delegates here. Default output includes
mismatch examples; use --show 0 for aggregate-only output. --category, --dump-unparsed,
and --jsonl can expose restricted source data. JSONL is an audit export, not Amber gold.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import click

from experiments.coral.brat import parse_ann, read_text


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
