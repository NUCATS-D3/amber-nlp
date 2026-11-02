"""Adversarial tests for mention spans and the mention() tool (contract v0.1 section 2.2)."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from amber import ids
from amber.schemas import GroundingFailure, Inclusion, Mention, Section, Source, SourceKind
from amber.tools.mention import cite, mention
from amber.tools.quote import quote

LINE_1 = "History: pT2 \U0001f642 tumor.\r\n"
LINE_2 = "Final diagnosis: no evidence of e\u0301 distant metastasis.\r\n"
LINE_3 = "pT2 again."
TEXT = LINE_1 + LINE_2 + LINE_3
LINE_2_START = len(LINE_1)
IMPRESSION_START = TEXT.index("no evidence")


def make_source(text: str = TEXT, sections: tuple[Section, ...] | None = None) -> Source:
    if sections is None:
        sections = (
            Section(start=0, end=len(TEXT), label="whole note"),
            Section(start=0, end=LINE_2_START, label="History", category="history"),
            Section(
                start=LINE_2_START,
                end=LINE_2_START + len(LINE_2),
                label="Final diagnosis",
                category="final_diagnosis",
            ),
            Section(
                start=IMPRESSION_START,
                end=LINE_2_START + len(LINE_2) - 2,
                label="Impression",
                category="impression",
            ),
        )
    return Source.create(
        patient_id="synthetic-patient-1",
        kind=SourceKind.note,
        external_id="synthetic-note-1",
        datetime=datetime(2026, 1, 2, tzinfo=UTC),
        text=text,
        record=None,
        sections=sections,
    )


SOURCE = make_source()
SOURCES = {SOURCE.source_id: SOURCE}


def ground(
    text: str,
    hint_start: int | None = None,
    mention_type: str = "tnm_t",
    fragments: Any = (),
) -> Mention | GroundingFailure:
    return mention(
        SOURCE.source_id, text, hint_start, mention_type, fragments=fragments, sources=SOURCES
    )


def grounded(*args: Any, **kwargs: Any) -> Mention:
    result = ground(*args, **kwargs)
    assert isinstance(result, Mention), result
    return result


def test_mention_preserves_unicode_and_crlf_code_point_offsets() -> None:
    emoji = grounded("\U0001f642 tumor.\r\nFinal", mention_type="finding")
    assert emoji.start == TEXT.index("\U0001f642")
    assert emoji.end - emoji.start == len("\U0001f642 tumor.\r\nFinal")
    assert emoji.quote == TEXT[emoji.start : emoji.end]

    combining = grounded("e\u0301 distant metastasis", mention_type="finding")
    assert combining.quote == TEXT[combining.start : combining.end]
    assert combining.end - combining.start == 21
    assert combining.mention_id == ids.mention_id(
        source_id=SOURCE.source_id,
        start=combining.start,
        end=combining.end,
        mention_type="finding",
    )


def test_mention_is_rejected_when_the_quote_does_not_match_the_source() -> None:
    mismatch = ground("pT3", hint_start=TEXT.index("pT2"))
    assert isinstance(mismatch, GroundingFailure)
    assert mismatch.reason == "hint_mismatch"

    normalized = ground("e\u00e9 distant")  # precomposed, not the source's combining sequence
    assert isinstance(normalized, GroundingFailure)
    assert normalized.reason == "not_found"


def test_repeated_mention_text_requires_an_exact_hint() -> None:
    ambiguous = ground("pT2")
    assert isinstance(ambiguous, GroundingFailure)
    assert ambiguous.reason == "ambiguous"
    assert ambiguous.match_starts == (TEXT.index("pT2"), TEXT.rindex("pT2"))

    second = grounded("pT2", hint_start=TEXT.rindex("pT2"))
    assert second.start == TEXT.rindex("pT2")


@pytest.mark.parametrize(
    ("hint_start", "reason"),
    [(-1, "hint_out_of_bounds"), (len(TEXT) - 2, "hint_out_of_bounds")],
)
def test_mention_hull_outside_the_source_is_rejected(hint_start: int, reason: str) -> None:
    result = ground("pT2", hint_start=hint_start)
    assert isinstance(result, GroundingFailure)
    assert result.reason == reason


def test_mention_cannot_be_constructed_outside_grounding() -> None:
    start = TEXT.index("pT2")
    payload = {
        "mention_id": ids.mention_id(
            source_id=SOURCE.source_id, start=start, end=start + 3, mention_type="tnm_t"
        ),
        "source_id": SOURCE.source_id,
        "start": start,
        "end": start + 3,
        "quote": "pT2",
        "mention_type": "tnm_t",
        "section_category": "history",
    }
    with pytest.raises(ValidationError, match="verified mention"):
        Mention(**payload)
    with pytest.raises(ValidationError, match="verified mention"):
        Mention.model_validate(payload)
    with pytest.raises(TypeError, match="verified mention"):
        Mention.model_construct(**payload)


def test_mention_is_frozen() -> None:
    result = grounded("pT2", hint_start=TEXT.index("pT2"))
    with pytest.raises(ValidationError, match="frozen"):
        result.start = 0  # type: ignore[misc]


@pytest.mark.parametrize("mention_type", ["", "   ", None, 3])
def test_mention_type_must_be_a_nonblank_string(mention_type: Any) -> None:
    with pytest.raises(ValueError, match="mention_type"):
        ground("pT2", hint_start=TEXT.index("pT2"), mention_type=mention_type)


def test_mention_tool_rejects_unknown_sources() -> None:
    with pytest.raises(KeyError, match="unknown source_id"):
        mention("missing", "pT2", None, "tnm_t", sources={})


def test_section_category_is_derived_from_the_innermost_categorized_section() -> None:
    history = grounded("pT2", hint_start=TEXT.index("pT2"))
    assert history.section_category == "history"

    nested = grounded("distant metastasis", mention_type="finding")
    assert nested.section_category == "impression"

    outer = grounded("Final diagnosis", mention_type="heading")
    assert outer.section_category == "final_diagnosis"

    crossing = grounded("tumor.\r\nFinal", mention_type="finding")
    assert crossing.section_category is None

    uncategorized = grounded("pT2", hint_start=TEXT.rindex("pT2"))
    assert uncategorized.section_category is None

    no_sections = make_source(sections=())
    plain = mention(
        no_sections.source_id,
        "pT2",
        TEXT.index("pT2"),
        "tnm_t",
        sources={no_sections.source_id: no_sections},
    )
    assert isinstance(plain, Mention)
    assert plain.section_category is None


def test_discontinuous_mention_has_fragments_inside_its_hull() -> None:
    start = TEXT.index("no evidence")
    end = TEXT.index(" metastasis") + len(" metastasis")
    fragments = ((start, start + 2), (TEXT.index("metastasis"), end))
    result = grounded(TEXT[start:end], mention_type="finding", fragments=fragments)

    assert result.fragments == fragments
    assert (result.start, result.end) == (start, end)
    assert [TEXT[lo:hi] for lo, hi in result.fragments] == ["no", "metastasis"]


@pytest.mark.parametrize(
    "fragments",
    [
        "single",
        7,
        None,
        [(0, 2)],
        [(0, 2), (1, 5)],
        [(0, 2), (2, 5)],
        [(3, 5), (0, 2)],
        [(0, 2), (3, 4)],
        [(1, 2), (3, 5)],
        [(0, 2), (3, 6)],
        [(0, 0), (3, 5)],
        [(0, 2, 3), (3, 5)],
        [(0, True), (3, 5)],
        [(0.0, 2), (3, 5)],
        [("0", 2), (3, 5)],
        [3, (3, 5)],
    ],
)
def test_invalid_fragments_are_explicit_failures(fragments: Any) -> None:
    start = TEXT.index("pT2")
    if isinstance(fragments, list):
        fragments = [
            tuple(offset + start if type(offset) is int else offset for offset in fragment)
            if isinstance(fragment, tuple)
            else fragment
            for fragment in fragments
        ]
    result = ground(TEXT[start : start + 5], hint_start=start, fragments=fragments)
    assert isinstance(result, GroundingFailure)
    assert result.reason == "invalid_fragments"


def test_contiguous_mention_ids_are_unchanged_and_fragments_change_identity() -> None:
    assert ids.mention_id(source_id="s" * 64, start=3, end=17, mention_type="tnm_t") == (
        "4ec5f72e870dbeb2cc748c11888a6f724cae721e20af2b9cee82c08b9d780299"
    )
    assert ids.mention_id(
        source_id="s" * 64, start=3, end=17, mention_type="tnm_t", fragments=()
    ) == ids.mention_id(source_id="s" * 64, start=3, end=17, mention_type="tnm_t")

    start = TEXT.index("no evidence")
    end = TEXT.index("metastasis") + len("metastasis")
    hull = grounded(TEXT[start:end], mention_type="finding")
    split = grounded(
        TEXT[start:end],
        mention_type="finding",
        fragments=((start, start + 2), (TEXT.index("metastasis"), end)),
    )
    other_split = grounded(
        TEXT[start:end],
        mention_type="finding",
        fragments=((start, start + 11), (TEXT.index("metastasis"), end)),
    )
    assert len({hull.mention_id, split.mention_id, other_split.mention_id}) == 3


def test_mention_identity_depends_on_type_and_position() -> None:
    first = grounded("pT2", hint_start=TEXT.index("pT2"))
    again = grounded("pT2", hint_start=TEXT.index("pT2"))
    other_type = grounded("pT2", hint_start=TEXT.index("pT2"), mention_type="stage")
    other_position = grounded("pT2", hint_start=TEXT.rindex("pT2"))

    assert first == again
    assert first.mention_id != other_type.mention_id
    assert first.mention_id != other_position.mention_id


def test_mentions_cues_and_quotes_at_one_position_share_one_inclusion() -> None:
    start = TEXT.index("pT2")
    as_t = grounded("pT2", hint_start=start)
    as_stage = grounded("pT2", hint_start=start, mention_type="stage")
    cue = quote(SOURCE.source_id, "pT2", start, sources=SOURCES)

    assert isinstance(cue, Inclusion)
    assert cite(as_t, sources=SOURCES) == (cue,)
    assert cite(as_stage, sources=SOURCES) == (cue,)


def test_cite_returns_one_inclusion_per_fragment() -> None:
    start = TEXT.index("no evidence")
    end = TEXT.index("metastasis") + len("metastasis")
    fragments = ((start, start + 2), (TEXT.index("metastasis"), end))
    discontinuous = grounded(TEXT[start:end], mention_type="finding", fragments=fragments)

    inclusions = cite(discontinuous, sources=SOURCES)

    assert [(item.start, item.end, item.quote) for item in inclusions] == [
        (lo, hi, TEXT[lo:hi]) for lo, hi in fragments
    ]


def test_cite_rejects_foreign_or_tampered_mentions() -> None:
    result = grounded("pT2", hint_start=TEXT.index("pT2"))
    other = make_source(text=TEXT + " ", sections=())

    with pytest.raises(KeyError, match="unknown source_id"):
        cite(result, sources={other.source_id: other})
    with pytest.raises(ValueError, match="supplied source"):
        cite(result, sources={result.source_id: other})
    with pytest.raises(ValueError, match="identity"):
        cite(result.model_copy(update={"start": result.start + 1}), sources=SOURCES)
    with pytest.raises(ValueError, match="identity"):
        cite(result.model_copy(update={"mention_type": "stage"}), sources=SOURCES)
    with pytest.raises(TypeError, match="requires a Mention"):
        cite(cite(result, sources=SOURCES)[0], sources=SOURCES)  # type: ignore[arg-type]
