# ADR 0002: Offsets are end-exclusive code points into immutable source text

- Status: proposed
- Date: 2026-11-09
- Related: [CLAUDE.md invariant 2](../../CLAUDE.md), [contract v0.1](../design/contract-v0.1.md),
  amber-7q0.2

## Context

Every claim in amber is grounded in a span of a source note, and spans travel between Python,
JSON, Parquet, loupe's browser editor, and other languages. Byte offsets, UTF-16 offsets, and
code-point offsets disagree as soon as text contains astral characters or combining marks, and
line-ending normalization (CRLF to LF) shifts every later offset. A span that quietly points at
different text is worse than no span.

## Decision

- A span is `(start, end)`: end-exclusive offsets counted in Unicode code points (Python string
  indices) into `Source.text`.
- `Source.text` is immutable once `source_id` is minted. Nothing normalizes, strips, or
  re-encodes it afterward; `source_id` hashes the text, so a changed text is a different source.
- A span's quote always equals `text[start:end]`, checked wherever a span is created or loaded.
  Discontinuous mentions carry ordered, separated `fragments` whose hull is `(start, end)`.

## Consequences

- `Source` is a frozen model and `amber.ids.source_id` hashes its content
  (`src/amber/schemas/sources.py`). Grounding checks the quote against the slice
  (`src/amber/grounding.py`), and graph validation, bundle loading, review import, and the loupe
  adapter re-ground every span against the authoritative source.
- Tests pin the behaviour with Unicode, astral, and CRLF text:
  `tests/test_invariants.py::test_exact_quote_preserves_unicode_and_crlf_offsets`,
  `tests/test_mentions.py::test_mention_preserves_unicode_and_crlf_code_point_offsets`,
  `tests/test_loupe_adapter.py::test_unicode_and_crlf_offsets_are_code_points`, and the
  Hypothesis property `test_a_quote_is_exactly_the_text_at_its_code_point_offsets`.
- Consumers in other languages must convert at their boundary (for example, JavaScript's UTF-16
  indices); the contract does not.

## Alternatives considered

- **UTF-8 byte offsets.** Natural for Rust and Parquet, but not for Python slicing, and every
  consumer would have to agree on the encoding.
- **UTF-16 offsets.** Match browsers, but split astral characters and differ from Python.
- **Normalizing text on ingest.** Simplifies matching, but breaks the link to the stored note and
  makes offsets depend on the normalization version.
