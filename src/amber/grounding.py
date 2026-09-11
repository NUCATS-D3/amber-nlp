"""Ground candidate text against an immutable source."""

from amber.ids import inclusion_id
from amber.schemas.evidence import GroundingFailure, Inclusion, _mint_inclusion
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
