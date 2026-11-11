"""Grounding failures are exact and complete (amber-u4c.11, from the first mutation run):
every failure names its source, candidate, reason, and message; overlapping repeats are
ambiguous; sections and tampered mentions are handled at their edges."""

from __future__ import annotations

import pytest

from amber.conformance.forgery import forge
from amber.grounding import cite_mention, exact_mention, exact_quote
from amber.ids import mention_id
from amber.schemas import GroundingFailure, Inclusion, Mention, Section, Source, SourceKind


def _note(text: str, sections: tuple[Section, ...] = ()) -> Source:
    return Source.create(
        patient_id="grounding-patient",
        kind=SourceKind.note,
        external_id="grounding-note",
        datetime=None,
        text=text,
        record=None,
        sections=sections,
    )


NOTE = _note("Invented: aaa then mass, and mass again.")


def _failure(
    source: Source, candidate: str, reason: str, message: str, starts: tuple[int, ...] = ()
) -> GroundingFailure:
    return GroundingFailure(
        source_id=source.source_id,
        candidate=candidate,
        reason=reason,  # type: ignore[arg-type]
        message=message,
        match_starts=starts,
    )


def test_a_structured_source_cannot_be_quoted() -> None:
    structured = Source.create(
        patient_id="grounding-patient",
        kind=SourceKind.lab,
        external_id="grounding-row",
        datetime=None,
        text=None,
        record={"invented": True},
    )
    assert exact_quote(structured, "anything") == _failure(
        structured,
        "anything",
        "source_not_text",
        "structured sources cannot provide text inclusions",
    )


@pytest.mark.parametrize(
    ("candidate", "hint", "reason", "message"),
    [
        ("", None, "empty_candidate", "candidate quote must not be empty"),
        ("mass", 999, "hint_out_of_bounds",
         "hint_start places the candidate outside the source text"),
        ("mass", 0, "hint_mismatch", "source text at hint_start does not equal the candidate"),
        ("liver", None, "not_found", "candidate does not occur in the source text"),
    ],
)  # fmt: skip
def test_each_failure_is_exact(candidate: str, hint: int | None, reason: str, message: str) -> None:
    assert exact_quote(NOTE, candidate, hint) == _failure(NOTE, candidate, reason, message)


def test_an_ambiguous_quote_lists_every_start() -> None:
    text = NOTE.text or ""
    starts = (text.index("mass"), text.index("mass", text.index("mass") + 1))
    assert exact_quote(NOTE, "mass") == _failure(
        NOTE, "mass", "ambiguous", "candidate occurs more than once; provide hint_start", starts
    )


def test_overlapping_repeats_are_ambiguous() -> None:
    # "aa" occurs at two overlapping positions in "aaa"; without a hint it is ambiguous.
    start = NOTE.text.index("aaa")  # type: ignore[union-attr]
    failure = exact_quote(NOTE, "aa")
    assert isinstance(failure, GroundingFailure) and failure.reason == "ambiguous"
    assert failure.match_starts == (start, start + 1)
    second = exact_quote(NOTE, "aa", start + 1)
    assert isinstance(second, Inclusion) and second.start == start + 1


def test_invalid_mentions_fail_with_their_messages() -> None:
    with pytest.raises(ValueError, match="^mention_type must be a nonblank string$"):
        exact_mention(NOTE, "mass", 19, " ")
    start = NOTE.text.index("aaa")  # type: ignore[union-attr]
    failure = exact_mention(NOTE, "aaa", start, "finding", fragments=((start, start + 3),))
    assert failure == _failure(
        NOTE,
        "aaa",
        "invalid_fragments",
        "fragments must be two or more ordered, separated spans whose hull is the grounded text",
    )


# Sections ----------------------------------------------------------------------------------


def test_a_mention_ending_at_a_section_end_takes_its_category() -> None:
    text = "History: mass. Plan: none."
    note = _note(text, (Section(start=0, end=text.index("."), label="History", category="h"),))
    mention = exact_mention(note, "mass", None, "finding")
    assert isinstance(mention, Mention) and mention.end == text.index(".")
    assert mention.section_category == "h"


def test_equal_length_sections_resolve_to_the_later_start() -> None:
    text = "0123456789AB"
    sections = (
        Section(start=0, end=10, label="First", category="first"),
        Section(start=2, end=12, label="Second", category="second"),
    )
    mention = exact_mention(_note(text, sections), "3456", None, "finding")
    assert isinstance(mention, Mention) and mention.section_category == "second"


# Tampered mentions -------------------------------------------------------------------------


def _mention() -> Mention:
    made = exact_mention(NOTE, "mass", NOTE.text.index("mass"), "finding")  # type: ignore[union-attr]
    assert isinstance(made, Mention)
    return made


def _rekeyed(mention: Mention, **changes: object) -> Mention:
    """A tampered copy whose ID still matches its (tampered) span."""
    changed = forge(mention, **changes)
    return forge(
        changed,
        mention_id=mention_id(
            source_id=changed.source_id,
            start=changed.start,
            end=changed.end,
            mention_type=changed.mention_type,
            fragments=changed.fragments,
        ),
    )


def test_a_mention_of_another_source_is_refused() -> None:
    other = _note("Invented: another note with mass.")
    with pytest.raises(ValueError, match="^mention does not belong to the supplied source$"):
        cite_mention(other, _mention())
    textless = NOTE.model_copy(update={"text": None})
    with pytest.raises(ValueError, match="^mention does not belong to the supplied source$"):
        cite_mention(textless, _mention())


def test_a_mention_whose_identity_or_fragments_do_not_match_is_refused() -> None:
    mention = _mention()
    with pytest.raises(ValueError, match="^mention identity does not match its span$"):
        cite_mention(NOTE, forge(mention, start=mention.start + 1))
    bad_fragments = _rekeyed(mention, fragments=((mention.start, mention.end),))
    with pytest.raises(ValueError, match="^mention identity does not match its span$"):
        cite_mention(NOTE, bad_fragments)


@pytest.mark.parametrize(
    "changes",
    [{"quote": "Mass"}, {"end": 10_000}, {"start": 10_000, "end": 10_004}],
    ids=["quote", "past_the_end", "outside_the_text"],
)
def test_a_mention_that_does_not_ground_is_refused(changes: dict[str, object]) -> None:
    tampered = _rekeyed(_mention(), **changes)
    with pytest.raises(ValueError, match="^mention does not ground in the supplied source$"):
        cite_mention(NOTE, tampered)
