# Clinical NLP for registries and computational phenotyping

A 15-slide follow-on to the NUCATS D3 update, intended for an informatics and
translational-research audience. Approximately 15–20 minutes with discussion.

- `deck.md`: editable Marp source, including speaker notes and source references.
- `theme.css`: local NUCATS-inspired theme; no external fonts or network assets.
- `deck.html`: generated browser presentation with speaker view.
- `deck.pdf`: generated presentation PDF.
- `assets/nucats-logo.png`: logo crop from the user-provided reference deck, page 1.

The reference is [`NUCATS-D3 update (Sept 26).pdf`](<../NUCATS-D3 update (Sept 26).pdf>),
dated September 2, 2026 within the slides. Pages 17–18 provide the brief bridge:
registry/computational phenotyping tools and MLflow model governance. Purple
`#4e2a84`, white content slides, rounded workflow boxes, institutional footer,
and the title-slide treatment follow that deck. The small gold accent identifies
Amber's proposed direction.

## Build

From this directory, with Node.js and Chrome installed:

```sh
npx --yes @marp-team/marp-cli@4.3.1 deck.md --theme theme.css --html -o deck.html
npx --yes @marp-team/marp-cli@4.3.1 deck.md --theme theme.css --html \
  --pdf --allow-local-files -o deck.pdf
```

The local-files flag allows Chrome to include the bundled logo. No patient data
is present. The checked-in theme embeds the logo as a data URI so generated HTML
is self-contained; `assets/nucats-logo.png` retains the editable source asset.
Speaker notes remain in the Markdown and HTML; the PDF contains only slides.

## Content and status

The deployment statement comes from the user's presentation brief: the existing
abstractor is being deployed into the Databricks EDW environment, with MLflow
being used to evaluate results. It is not described as a completed rollout, and
no clinical performance numbers are asserted.

Amber is presented as a proposed next generation. The checkout was inspected,
including initial source/grounding code and the current facade; no completed
clinical extraction/correction workflow or comparative benefit is claimed.
Amber's design goals are explicitly distinguished from existing capabilities.

Slides 12–14 distill three high-level themes from
[`amber-vs-brim.md`](../amber-vs-brim.md): complementary product/infrastructure
roles, reuse of expert-reviewed cases, and the questions to measure during the
Northwestern Brim Analytics trial. They precede the closing slide and replace
the prior comparison placeholder. Trial status is user-provided; integration,
reuse, and cost benefits are proposed directions. The summary omits the source
deck's unverified benchmark, cost-reduction, and vendor-limit claims. Speaker
notes retain the conditions on export permissions and independent validation.

The grade example on slide 5 is invented. The deck contains no clinical records,
screenshots of clinical systems, CORAL source text, or patient identifiers.
CORAL is mentioned only as Amber's planned pilot dataset, with its sample-size
and task-mapping limitations.

## Source map

The sibling repository paths below are relative to the Amber repository root.

| Slides | Basis |
| --- | --- |
| 1–2 | User brief; reference PDF, pages 17–18 |
| 3–4 | `../note-abstractor-main/README.md`, `docs/architecture.md`, `docs/rails-compatibility.md`; pipeline, NLP, evidence, and decision code |
| 5 | `../note-abstractor-main/docs/llm-resolution.md`, `docs/llm-workflow.md`; resolver and adjudication code |
| 6–7 | User brief; `../note-abstractor-main/docs/databricks-run.md`, `docs/llm-workflow.md`; Databricks notebooks, MLflow dataset and tracing code |
| 8–9 | `docs/00-goals-and-architecture.md`, `docs/02-v1-schemas-and-tools.md`, `docs/04-roadmap.md`; current Amber code |
| 10 | Both projects' architecture and workflow documents; package dependencies; Amber's `CLAUDE.md` |
| 11, 15 | Amber goals, contract, and roadmap; user brief |
| 12–14 | `docs/slides/amber-vs-brim.md`; user-provided Northwestern trial status; Amber roadmap; https://www.brimanalytics.com/ |

CORAL v1.0: DOI [10.13026/v69y-xa45](https://doi.org/10.13026/v69y-xa45).
Its 40 expert-labeled notes support a pilot; the separate GPT-4 pseudo-labels
are not expert gold. A shared comparison requires a validated overlapping task.

## Validation

Regenerate HTML and PDF after edits, inspect all slides for clipping and layout,
check the slide count, and run `git diff --check`. Application tests are not
needed for changes confined to these presentation files.
