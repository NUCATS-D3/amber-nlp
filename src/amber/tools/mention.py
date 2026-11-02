"""The deterministic mention tools: ground a typed span, and cite one as evidence."""

from collections.abc import Mapping

from amber.grounding import cite_mention, exact_mention
from amber.schemas import GroundingFailure, Inclusion, Mention, Source


def _source(source_id: str, sources: Mapping[str, Source]) -> Source:
    try:
        return sources[source_id]
    except KeyError as error:
        raise KeyError(f"unknown source_id: {source_id}") from error


def mention(
    source_id: str,
    text: str,
    hint_start: int | None,
    mention_type: str,
    *,
    fragments: tuple[tuple[int, int], ...] = (),
    sources: Mapping[str, Source],
) -> Mention | GroundingFailure:
    """Ground a typed span in a known source; the only way to mint a ``Mention``.

    ``text`` is the span's hull and is matched exactly, with ``hint_start`` required when it
    repeats. Cue and scope spans are not mentions; ground them with ``quote()``.
    """
    return exact_mention(
        _source(source_id, sources), text, hint_start, mention_type, fragments=fragments
    )


def cite(mention: Mention, *, sources: Mapping[str, Source]) -> tuple[Inclusion, ...]:
    """Return the inclusion at a mention's hull, or one per fragment, through grounding.

    Inclusions are positions only, so the result equals ``quote()`` of the same offsets.
    """
    if not isinstance(mention, Mention):
        raise TypeError("cite() requires a Mention")
    return cite_mention(_source(mention.source_id, sources), mention)
