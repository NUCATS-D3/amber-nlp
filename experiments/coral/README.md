# CORAL experiment

Initial workspace for running Amber on CORAL v1.0 (DOI `10.13026/v69y-xa45`). The annotation
audit, pure current-progression candidate rules, and split/manifest tooling are implemented.
The domain adapter remains a draft with missing M1 schema dependencies; extraction and evaluation
remain to be implemented under the
[M1–M3 roadmap](../../docs/04-roadmap.md).

```text
coral/
  brat.py                    importable BRAT records and parser (stdlib only)
  audit.py                   annotation audit CLI and local JSONL export
  scripts/coral_ingest.py    compatibility launcher for the audit CLI
  scripts/coral_adapter.py   draft CORAL-to-Amber mapping policies
  scripts/coral_current_progression.py  pure candidate rules (no CLI or file writes)
  scripts/coral_current_progression_manifest.py  deterministic splits and immutable local manifest
  eval/                     evaluation code and analysis
  data/raw/annotated/        40 expert-labeled notes (local only)
  data/raw/unannotated/      200 other notes and GPT-4 pseudo-labels (local only)
  data/                     dataset documentation and derived inputs (local only)
  outputs/                  audit exports, predictions, and evaluation reports (local only)
```

Place the credentialed dataset's `annotated/` and `unannotated/` directories under `data/raw/`.
The existing local copy has moved here from the repository-level `data/coral/`. Keep the
dataset documentation in `data/` as
`CORAL_ expert-Curated medical Oncology Reports to Advance Language model inference v1.0.pdf`.
Neither the dataset nor its documentation is distributed with this repository.

CORAL is restricted, deidentified clinical data, declared as `Sensitivity.deidentified`.
Preserve raw `.txt` and `.ann` files exactly, including newlines. Consult the dataset
documentation and `data/raw/annotated/annotation.conf` before interpreting labels. Follow
the repository's [CORAL rules](../../AGENTS.md#coral-dataset) for grounding, mapping policies,
patient/document splits, and permitted destinations. Keep the expert gold set separate from
the GPT-4 pseudo-labels.

From the repository root, run an aggregate audit without printing example spans:

```sh
uv run python experiments/coral/scripts/coral_ingest.py \
  experiments/coral/data/raw/annotated --show 0
```

The equivalent module command is `uv run python -m experiments.coral.audit` with the same
arguments. Other experiment modules and tests import the parser directly:

```python
from experiments.coral.brat import Document, Entity, parse_ann, read_text
```

These modules are importable from a repository checkout; they are not shipped in the Amber
wheel. The parser has no Click or Amber dependency and does not load data or run the audit on
import. `Document.diagnostics` records malformed or unknown records, duplicate IDs, duplicate or
conflicting attributes, and dangling references. Raw malformed, unknown, and duplicate-ID records
remain available in the restricted in-memory audit; parsed attributes remain a complete list.
`Document.annotation_inventory_complete` is true only when both
diagnostics and unparsed records are empty; this describes parse integrity, not human review of
the note or evidence validity. References are checked after parsing so forward references work.
The audit reports diagnostic counts with `--show 0` without printing the raw problem records.
Independent span bounds validation remains required before using annotations as evidence; quote
agreement categories do not supply it. The draft domain adapter still requires missing M1 schemas.

The **empty metadata tail** adapter policy accepts one empty trailing tab field on attribute,
directed relation, event, and symmetric-relation records. CORAL uses this representation; it
adds no annotation content. Nonempty surplus fields remain malformed. This parsing policy does
not rewrite raw files or trim copied entity quotes.

The default audit prints mismatch text; `--category`, `--dump-unparsed`, and `--jsonl` can
expose source-bearing data. Save exports under `experiments/coral/outputs/<run-id>/`, keep
them out of shared logs and commits, and retain the dataset's access restrictions. Audit
JSONL is not a validated Amber `Example`, and the `redacted` heuristic does not validate
span bounds. Its legacy attribute map is not lossless for conflicting attributes; consult the
parser diagnostics and full in-memory attribute list. The draft adapter's mapping policies require
validation before gold derivation.

## Current-progression candidates

The [candidate module](scripts/coral_current_progression.py) implements the conservative mapping
in [protocol v1.0.0](../../docs/protocols/oncology_current_progression-v1.md). Pass an existing parsed
`Document` to `derive_current_progression_candidate`; it returns a frozen candidate with sorted
signals and flags. The function performs no file writes or model calls. The separate manifest
command below consumes these candidates for pre-adjudication stratification.

Every result is `non_authoritative`, requires clinical review, and leaves `clinical_scope_reviewed`
false. An answered candidate proposes a strict boolean, not a gold label or evidence-backed Amber
Claim. Usable seeds retain `temporality_unverified`; missing modality is an explicit flagged
assumption. Incomplete annotation inventories, invalid required attributes, unsafe relevant spans,
source-surface mismatches, and unresolved skip boundaries block answers. Other-experiencer,
uncertain, historical-modality, future, hospice, and unsupported-entity signals are warnings that
do not override an independently usable seed. No annotation-only rule establishes currentness.

Skip fragments remain separate: a candidate in the gap between them is not excluded by widening.
The **discontinuous skip surface** policy compares copied text with the exact source fragments
joined by one space, as in BRAT's discontinuous representation. A disagreement requires review;
the parser's `discontinuous` category alone is not verification. Single spans use the exact source
slice, and all intervals require independent bounds checks.

Candidate objects omit source text, copied quotes, and offsets, but their document/annotation IDs
remain linked to restricted data. Keep derived results local under the dataset's access rules;
do not print or commit individual candidates. `not_mentioned` with `annotation_absence_only` means
only that no relevant annotation was found in a complete inventory, not that a human reviewed the
whole note. Gold still requires independent review and adjudication.

## Split and manifest tooling

The [manifest command](scripts/coral_current_progression_manifest.py) implements deterministic
20/10/10 document splitting with seed `20260915`, preserving 10/5/5 per cancer type. Positive
candidate quotas are allocated jointly before hash-ordering patients within each stratum. Only an
`answered true` candidate enters the positive stratum; the other stratum is not a clinical-negative
label. Repeated patient identities are rejected rather than split across partitions.

Manifests contain restricted linked metadata, hashes, and aggregate diagnostics, not source text
or evidence. Output is limited to ignored `data/manifests/` or `outputs/` paths outside input trees.
Existing artifacts are immutable: identical reruns verify without writing; changed inputs,
versions, or membership fail. First publication is atomic and never replaces a competing artifact.

The selected manifest is the freeze authority. Choosing a new filename does not authorize
repartitioning exposed patients. A successor needs an explicit future migration retaining known
assignments and linking the original artifact; no migration or overwrite mode is implemented.
Synthetic tests cover the tooling; the real CORAL manifest freeze and complete operator workflow
remain the next implementation checkpoint.

Before extraction or scoring, define the clinical task, answer schema, annotation coverage,
gold derivation, patient/document split, numeric acceptance criteria, and permitted provider
and artifact destinations. Record the dataset manifest/hash and versioned adapter policy
with results. Evaluation code belongs in `eval/`; local manifests and derived examples belong
in `data/`, and generated reports belong in `outputs/`.
