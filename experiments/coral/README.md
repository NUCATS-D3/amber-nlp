# CORAL experiment

Initial workspace for running Amber on CORAL v1.0 (DOI `10.13026/v69y-xa45`). The annotation
audit is runnable. The adapter is a draft that imports M1 schemas which do not exist yet;
the extraction run and evaluation remain to be implemented under the
[M1–M3 roadmap](../../docs/04-roadmap.md).

```text
coral/
  brat.py                    importable BRAT records and parser (stdlib only)
  audit.py                   annotation audit CLI and local JSONL export
  scripts/coral_ingest.py    compatibility launcher for the audit CLI
  scripts/coral_adapter.py   draft CORAL-to-Amber mapping policies
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

Before extraction or scoring, define the clinical task, answer schema, annotation coverage,
gold derivation, patient/document split, numeric acceptance criteria, and permitted provider
and artifact destinations. Record the dataset manifest/hash and versioned adapter policy
with results. Evaluation code belongs in `eval/`; local manifests and derived examples belong
in `data/`, and generated reports belong in `outputs/`.
