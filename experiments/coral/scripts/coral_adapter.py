"""Draft CORAL -> amber adapter for the CORAL experiment; requires unimplemented M1 schemas.

The first consumer of `amber.schemas`, and therefore the thing that pins down its
contract. Everywhere CORAL's annotation scheme disagrees with amber's data model,
the reconciliation lives here as a named, testable function rather than inside a
loader -- these are corpus decisions, and they should be arguable.

Four such decisions, each with its evidence from the 40-note corpus:

1.  DISCONTINUOUS SPANS (264 of 19,655; 1.3%).  amber's Inclusion is a single
    interval.  Gap sizes are bimodal: conjunction-and-modifier splits gap by under
    ~20 chars (Symptom 11, ProcedureName 13, Pathology 18), while genuinely scattered
    annotations gap by far more (RadPathResult 192, ClinicalTrial 167, Site 58).
    Below GAP_THRESHOLD we widen to the enclosing interval; above it we emit one
    Mention per fragment sharing a `fragment_group`.  See `resolve_spans`.

2.  MODALITY (NegationModalityVal, 1,790).  CORAL fuses polarity, certainty and
    tense into one six-valued attribute; amber keeps them orthogonal.  The mapping is
    total, but HistoryVal (556) encodes temporality *as well*, so the two can
    disagree.  Precedence: NegationModalityVal wins when its value names a tense
    (`*_in_past`, `*_in_future`), HistoryVal wins otherwise.  See `map_modality`.

3.  SECTIONS.  SectionSkip (321) with SectionSkipType marks regions excluded from
    annotation -- these become Section(is_template=True), NOT Mentions, and a
    multi-fragment skip becomes several Sections rather than one widened one.
    hpi_start/hpi_end (47/47) and ap_start/ap_end (40/40) are paired boundary
    markers that become HPI and A&P Sections.  Two SectionSkip annotations have
    `.ann` quotes running ~1,000 chars past their recorded offsets; both are widened
    by hand via MANUAL_SPAN_FIXES.  See `build_sections`.

4.  RELATION DIRECTION.  44 relations are written as BRAT '*' (symmetric) lines
    where argument order carries no meaning, and BiomarkerRel appears in both forms
    (183 directed, 38 not).  TreatmentTypeRel exists ONLY undirected.  Direction is
    therefore recovered from the entity types of the arguments, and the directed
    instances are used to check that table rather than trusted blindly.
    See `orient_relation` and `check_direction_table`.

Everything else is a straight rename.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from coral_ingest import Document, Entity

from amber.schemas import (  # M1 deliverable; see 02-v1-schemas-and-tools.md §1-§3
    Experiencer,
    Mention,
    Polarity,
    Provenance,
    Section,
    Sensitivity,
    Source,
    SourceKind,
    Temporality,
    Zone,
)

# --------------------------------------------------------------------------- #
# 1. Discontinuous spans
# --------------------------------------------------------------------------- #

GAP_THRESHOLD = 25
"""Chars. Below this, widening swallows only filler; above, unrelated text.

Set from the per-type maxima, which is a weak basis -- rerun the ingest script with a
gap histogram before treating this as settled. It is a knob, not a constant of nature.
"""


@dataclass
class ResolvedSpans:
    intervals: list[tuple[int, int]]
    strategy: str  # "single" | "widened" | "fragmented"
    widest_gap: int


def resolve_spans(spans: list[tuple[int, int]], threshold: int = GAP_THRESHOLD) -> ResolvedSpans:
    """Reduce BRAT's fragment list to amber intervals."""
    if len(spans) == 1:
        return ResolvedSpans(spans, "single", 0)

    ordered = sorted(spans)
    gaps = [ordered[i + 1][0] - ordered[i][1] for i in range(len(ordered) - 1)]
    widest = max(max(gaps), 0)

    if widest <= threshold:
        return ResolvedSpans([(ordered[0][0], ordered[-1][1])], "widened", widest)
    return ResolvedSpans(ordered, "fragmented", widest)


# --------------------------------------------------------------------------- #
# 2. Modality
# --------------------------------------------------------------------------- #

MODALITY: dict[str, tuple[Polarity, Temporality | None]] = {
    "affirmed": (Polarity.positive, None),
    "negated": (Polarity.negated, None),
    "uncertain_in_present": (Polarity.uncertain, Temporality.current),
    "uncertain_in_past": (Polarity.uncertain, Temporality.historical),
    "hypothetical_in_future": (Polarity.positive, Temporality.hypothetical),
    "planned_in_future": (Polarity.positive, Temporality.future),
}

HISTORY: dict[str, Temporality] = {
    "new": Temporality.current,
    "history": Temporality.historical,
}

EXPERIENCER: dict[str, Experiencer] = {
    "patient": Experiencer.patient,
    "family": Experiencer.family,
    "others": Experiencer.other,
}


def map_modality(
    negation_modality: str | None,
    history: str | None,
) -> tuple[Polarity, Temporality, str | None]:
    """Split CORAL's fused modality into amber's orthogonal fields.

    Returns (polarity, temporality, conflict) where `conflict` is non-None when the
    two source attributes disagreed, so the rate can be counted rather than buried.
    """
    polarity, tense_from_modality = MODALITY.get(negation_modality or "", (Polarity.positive, None))
    tense_from_history = HISTORY.get(history or "")

    conflict = None
    if tense_from_modality and tense_from_history and tense_from_modality != tense_from_history:
        conflict = f"{negation_modality} vs {history}"

    # Precedence: an explicit tense in the modality value wins; otherwise HistoryVal.
    temporality = tense_from_modality or tense_from_history or Temporality.current
    return polarity, temporality, conflict


# --------------------------------------------------------------------------- #
# 3. Sections
# --------------------------------------------------------------------------- #

STRUCTURAL_TYPES = {
    "SectionSkip",
    "SectionAnnotate",
    "hpi_start",
    "hpi_end",
    "ap_start",
    "ap_end",
}

BOUNDARY_PAIRS = {"hpi": ("hpi_start", "hpi_end"), "ap": ("ap_start", "ap_end")}

MANUAL_SPAN_FIXES: dict[tuple[str, str], tuple[int, int]] = {
    # (doc_id, ann_id) -> corrected (start, end).
    # Two SectionSkip annotations whose .ann quote runs far past their offsets; the
    # recorded span covers a header line while the intended skip covers the whole
    # block. Left unfixed, ~1,000 chars of physical-exam and genomics text count as
    # in-scope-but-unannotated and inflate find_mentions false positives.
    # TODO: fill in from the .ann quote lengths; see ingest --dump-unparsed.
    # ("31", "T8"): (5741, 6950),
    # ("16", "T?"): (....),
}


def build_sections(doc: Document) -> list[Section]:
    """SectionSkip regions and HPI/A&P boundary markers -> Section objects."""
    sections: list[Section] = []
    skip_types = {
        a.target: a.value for d in [doc] for a in d.attributes if a.type == "SectionSkipType"
    }

    for ent in doc.entities.values():
        if ent.type != "SectionSkip":
            continue
        fragments = (
            [MANUAL_SPAN_FIXES[(doc.doc_id, ent.ann_id)]]
            if (doc.doc_id, ent.ann_id) in MANUAL_SPAN_FIXES
            else ent.spans
        )
        # A multi-fragment skip is several excluded regions, never one widened one.
        for start, end in fragments:
            sections.append(
                Section(
                    start=start,
                    end=end,
                    label=doc.text[start:end][:60],
                    category=skip_types.get(ent.ann_id),
                    is_template=True,
                )
            )

    by_type: dict[str, list[Entity]] = defaultdict(list)
    for ent in doc.entities.values():
        if ent.type in {t for pair in BOUNDARY_PAIRS.values() for t in pair}:
            by_type[ent.type].append(ent)

    for category, (start_type, end_type) in BOUNDARY_PAIRS.items():
        starts = sorted(by_type.get(start_type, []), key=lambda e: e.start)
        ends = sorted(by_type.get(end_type, []), key=lambda e: e.start)
        if len(starts) != len(ends):
            # hpi_start 47 / hpi_end 47 balance corpus-wide but may not per document.
            continue
        for s, e in zip(starts, ends, strict=True):
            sections.append(
                Section(
                    start=s.start, end=e.end, label=category, category=category, is_template=False
                )
            )

    return sorted(sections, key=lambda s: s.start)


# --------------------------------------------------------------------------- #
# 4. Relation direction
# --------------------------------------------------------------------------- #

DIRECTION: dict[str, tuple[set[str], set[str]]] = {
    # relation type -> (head entity types, tail entity types)
    "BiomarkerRel": ({"BiomarkerName"}, {"BiomarkerResult"}),
    "TreatmentTypeRel": ({"TreatmentType"}, {"TREATMENT", "TreatmentDesc"}),
    "TreatmentDesc": (
        {"TREATMENT"},
        {"TreatmentDosage", "Frequency", "Duration", "Cycles", "MedicationRegimen"},
    ),
    "ProcedureDesc": ({"ProcedureName"}, {"Site", "Laterality", "MarginStatus"}),
    "ExclusionCriteriaFor": ({"PROBLEM", "ClinicalCondition"}, {"ClinicalTrial"}),
}


def orient_relation(
    rel_type: str,
    arg_ids: list[str],
    entity_type: dict[str, str],
) -> tuple[str, str] | None:
    """Return (head_id, tail_id), or None when the type pair does not decide it."""
    rule = DIRECTION.get(rel_type)
    if rule is None or len(arg_ids) != 2:
        return None
    heads, tails = rule
    a, b = arg_ids
    ta, tb = entity_type.get(a, ""), entity_type.get(b, "")
    if ta in heads and tb in tails:
        return a, b
    if tb in heads and ta in tails:
        return b, a
    return None


def check_direction_table(docs: list[Document]) -> Counter:
    """Score DIRECTION against the directed R lines it claims to describe.

    A high disagreement count means the table is wrong; a low one means the directed
    subset has annotation noise. Either way it should be a number, not an assumption.
    """
    verdicts: Counter = Counter()
    for doc in docs:
        etype = {aid: e.type for aid, e in doc.entities.items()}
        for rel in doc.relations:
            if rel.symmetric or rel.type not in DIRECTION:
                continue
            args = [rel.args.get("Arg1", ""), rel.args.get("Arg2", "")]
            oriented = orient_relation(rel.type, args, etype)
            if oriented is None:
                verdicts[f"{rel.type}:undecidable"] += 1
            elif oriented == tuple(args):
                verdicts[f"{rel.type}:agrees"] += 1
            else:
                verdicts[f"{rel.type}:disagrees"] += 1
    return verdicts


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

SKIP_STATUSES = {"redacted"}
"""Offsets are sound but the surface form was re-redacted after annotation, so these
cannot support value-level scoring. Only 4 in the corpus; excluded from gold, kept
for span-localization scoring."""


def coral_provenance(version: str) -> Provenance:
    return Provenance(
        producer="tool:coral_adapter",
        zone=Zone.local,
        sensitivity=Sensitivity.deidentified,
        created_at=datetime.now(UTC),
        version=version,
    )


def to_amber(doc: Document, version: str = "0.1.0") -> tuple[Source, list[Mention], list[dict]]:
    """Return (Source, mentions, oriented relation records) for one CORAL note."""
    prov = coral_provenance(version)
    sections = build_sections(doc)

    source = Source(
        source_id="",  # minted by amber: sha256(patient_id, kind, external_id, text)
        patient_id=doc.doc_id,
        kind=SourceKind.note,
        external_id=doc.doc_id,
        datetime=None,
        text=doc.text,
        sections=sections,
        meta={"corpus": "coral"},
    )

    attrs: dict[str, dict[str, str | None]] = defaultdict(dict)
    for a in doc.attributes:
        attrs[a.target][a.type] = a.value

    mentions: list[Mention] = []
    for ent in doc.entities.values():
        if ent.type in STRUCTURAL_TYPES or ent.status in SKIP_STATUSES:
            continue
        a = attrs.get(ent.ann_id, {})
        polarity, temporality, conflict = map_modality(
            a.get("NegationModalityVal"), a.get("HistoryVal")
        )
        resolved = resolve_spans(ent.spans)
        for start, end in resolved.intervals:
            mentions.append(
                Mention(
                    mention_id="",  # minted by amber
                    source_id=source.source_id,
                    start=start,
                    end=end,
                    quote=doc.text[start:end],
                    mention_type=ent.type,
                    polarity=polarity,
                    temporality=temporality,
                    experiencer=EXPERIENCER.get(a.get("ExperiencerVal") or "", Experiencer.patient),
                    section_category=None,  # filled by amber from `sections`
                    provenance=prov,
                )
            )
            if conflict or resolved.strategy != "single":
                # Surface, don't swallow: these drive the corpus-decision metrics.
                mentions[-1].provenance.prompt_versions.setdefault(
                    "coral_note",
                    f"spans={resolved.strategy};gap={resolved.widest_gap};conflict={conflict}",
                )

    etype = {aid: e.type for aid, e in doc.entities.items()}
    relations = []
    for rel in doc.relations:
        args = [v for k, v in sorted(rel.args.items())]
        oriented = orient_relation(rel.type, args, etype)
        relations.append(
            {
                "type": rel.type,
                "head": oriented[0] if oriented else args[0] if args else None,
                "tail": oriented[1] if oriented else args[1] if len(args) > 1 else None,
                "directed_in_source": not rel.symmetric,
                "oriented_by_type": oriented is not None,
            }
        )

    return source, mentions, relations
