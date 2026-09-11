"""The deterministic quote tool exposed to fixed pipelines and optional agents."""

from collections.abc import Mapping

from amber.grounding import exact_quote
from amber.schemas import GroundingFailure, Inclusion, Source


def quote(
    source_id: str,
    text: str,
    hint_start: int | None = None,
    *,
    sources: Mapping[str, Source],
) -> Inclusion | GroundingFailure:
    """Ground text in a known source without allowing a caller to mint evidence directly."""
    try:
        source = sources[source_id]
    except KeyError as error:
        raise KeyError(f"unknown source_id: {source_id}") from error
    return exact_quote(source, text, hint_start)
