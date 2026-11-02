"""Ground candidate text against an immutable source."""

from collections.abc import Sequence

from amber.ids import inclusion_id, mention_id
from amber.schemas.evidence import GroundingFailure, Inclusion, _mint_inclusion
from amber.schemas.mentions import Mention, _mint_mention, fragments_error
from amber.schemas.sources import Source


def _all_match_starts(text: str, candidate: str) -> tuple[int, ...]:
    starts: list[int] = []
    offset = 0
    while (start := text.find(candidate, offset)) != -1:
        starts.append(start)
        offset = start + 1
    return tuple(starts)


def exact_quote(
    source: Source,
    candidate: str,
    hint_start: int | None = None,
) -> Inclusion | GroundingFailure:
    """Return one verified exact source span or an explicit grounding failure.

    Repeated candidates are intentionally ambiguous unless ``hint_start`` identifies the exact
    occurrence. Returned offsets always index the original ``Source.text``.
    """
    if source.text is None:
        return GroundingFailure(
            source_id=source.source_id,
            candidate=candidate,
            reason="source_not_text",
            message="structured sources cannot provide text inclusions",
        )
    if not candidate:
        return GroundingFailure(
            source_id=source.source_id,
            candidate=candidate,
            reason="empty_candidate",
            message="candidate quote must not be empty",
        )

    if hint_start is not None:
        if hint_start < 0 or hint_start + len(candidate) > len(source.text):
            return GroundingFailure(
                source_id=source.source_id,
                candidate=candidate,
                reason="hint_out_of_bounds",
                message="hint_start places the candidate outside the source text",
            )
        if source.text[hint_start : hint_start + len(candidate)] != candidate:
            return GroundingFailure(
                source_id=source.source_id,
                candidate=candidate,
                reason="hint_mismatch",
                message="source text at hint_start does not equal the candidate",
            )
        start = hint_start
    else:
        starts = _all_match_starts(source.text, candidate)
        if not starts:
            return GroundingFailure(
                source_id=source.source_id,
                candidate=candidate,
                reason="not_found",
                message="candidate does not occur in the source text",
            )
        if len(starts) > 1:
            return GroundingFailure(
                source_id=source.source_id,
                candidate=candidate,
                reason="ambiguous",
                message="candidate occurs more than once; provide hint_start",
                match_starts=starts,
            )
        start = starts[0]

    end = start + len(candidate)
    return _mint_inclusion(
        {
            "evidence_id": inclusion_id(source_id=source.source_id, start=start, end=end),
            "source_id": source.source_id,
            "start": start,
            "end": end,
            "quote": source.text[start:end],
            "alignment": "exact",
            "alignment_score": 1.0,
        }
    )


def _section_category(source: Source, start: int, end: int) -> str | None:
    """Return the category of the innermost categorized section containing the span."""
    containing = [
        section
        for section in source.sections
        if section.category is not None and section.start <= start and end <= section.end
    ]
    if not containing:
        return None
    innermost = min(
        containing,
        key=lambda section: (section.end - section.start, -section.start, section.category),
    )
    return innermost.category


def _fragment_pairs(fragments: Sequence[tuple[int, int]]) -> tuple[tuple[int, int], ...] | None:
    pairs: list[tuple[int, int]] = []
    for fragment in fragments:
        if not isinstance(fragment, Sequence) or isinstance(fragment, (str, bytes)):
            return None
        if len(fragment) != 2 or any(type(offset) is not int for offset in fragment):
            return None
        pairs.append((fragment[0], fragment[1]))
    return tuple(pairs)


def exact_mention(
    source: Source,
    candidate: str,
    hint_start: int | None,
    mention_type: str,
    *,
    fragments: Sequence[tuple[int, int]] = (),
) -> Mention | GroundingFailure:
    """Return one verified typed span or an explicit grounding failure.

    ``candidate`` is the text of the span's hull and is grounded exactly as ``exact_quote``
    grounds a quote. ``fragments``, when given, are source offsets of the discontinuous parts;
    their hull must be the grounded span.
    """
    if type(mention_type) is not str or not mention_type.strip():
        raise ValueError("mention_type must be a nonblank string")
    hull = exact_quote(source, candidate, hint_start)
    if isinstance(hull, GroundingFailure):
        return hull
    pairs = (
        _fragment_pairs(fragments)
        if isinstance(fragments, Sequence) and not isinstance(fragments, (str, bytes))
        else None
    )
    if pairs is None or fragments_error(hull.start, hull.end, pairs) is not None:
        return GroundingFailure(
            source_id=source.source_id,
            candidate=candidate,
            reason="invalid_fragments",
            message="fragments must be two or more ordered, separated spans whose hull is the "
            "grounded text",
        )
    return _mint_mention(
        {
            "mention_id": mention_id(
                source_id=source.source_id,
                start=hull.start,
                end=hull.end,
                mention_type=mention_type,
                fragments=pairs,
            ),
            "source_id": source.source_id,
            "start": hull.start,
            "end": hull.end,
            "quote": hull.quote,
            "mention_type": mention_type,
            "fragments": pairs,
            "section_category": _section_category(source, hull.start, hull.end),
        }
    )


def cite_mention(source: Source, mention: Mention) -> tuple[Inclusion, ...]:
    """Return the inclusion over a mention's hull, or one per fragment, re-grounded."""
    if not isinstance(mention, Mention) or mention.source_id != source.source_id:
        raise ValueError("mention does not belong to the supplied source")
    expected_id = mention_id(
        source_id=mention.source_id,
        start=mention.start,
        end=mention.end,
        mention_type=mention.mention_type,
        fragments=mention.fragments,
    )
    if mention.mention_id != expected_id or (
        fragments_error(mention.start, mention.end, mention.fragments) is not None
    ):
        raise ValueError("mention identity does not match its span")
    text = source.text
    if text is None:
        raise ValueError("mention does not belong to the supplied source")
    spans = mention.fragments or ((mention.start, mention.end),)
    inclusions: list[Inclusion] = []
    for start, end in spans:
        grounded = exact_quote(source, text[start:end], hint_start=start)
        if not isinstance(grounded, Inclusion) or grounded.end != end:
            raise ValueError("mention does not ground in the supplied source")
        inclusions.append(grounded)
    if text[mention.start : mention.end] != mention.quote:
        raise ValueError("mention does not ground in the supplied source")
    return tuple(inclusions)
