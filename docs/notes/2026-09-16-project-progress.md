# Project progress — 2026-09-16

Assessment at commit `88ce99e` on `main`. This is a dated handoff note, not a second live status
inventory; see [Current implementation](../README.md#current-implementation) for canonical status.

## Where we are

Amber has moved beyond scaffolding into a working, well-tested evidence kernel. M0's interfaces
exist and a substantial M1 slice is implemented, but M1 remains incomplete. There is no complete
extraction, evaluation, or correction workflow, and clinical usefulness has not been demonstrated.

The completed [claim-commit plan](../superpowers/plans/2026-09-16-m1-evidence-backed-claim-commits.md)
connects exact source grounding to typed proposed claims. It includes immutable task/claim/evidence
records, complete support-graph validation, field citations, atomic commits, read-only or detached
views, and validated in-memory snapshot/restore. Graphs are limited to one explicit note and Task.
Structural validity and source traceability do not establish semantic support or gold status.

The current-progression protocol, answer schema, non-authoritative CORAL candidate rules, and split
tooling are also implemented. The latest work did not access or change clinical data. The recorded
local CORAL split remains restricted data; it was not re-audited during this assessment.

## Completed checkpoints

| Commit | Checkpoint |
|---|---|
| `0cdf157` | Record the approved claim-commit implementation plan |
| `05b0ce1` | Add immutable claim primitives |
| `25403ae` | Validate complete evidence support |
| `bffc4a7` | Add validated in-memory evidence graphs |
| `88ce99e` | Commit evidence-backed proposed claims |

Independent task and whole-slice reviews were completed. Review fixes closed source-bearing
Pydantic serializer warnings and mutable Source metadata aliasing. Regression tests cover both.
Raw Claim records require nullable `confidence`; tool callers may omit it and receive `None`.
Inference records likewise require nullable `trace_id` and `span_id`. Omission tests are present.

## Verification recorded during the assessment

- Python 3.11: 517 tests passed.
- Python 3.12: 517 tests passed.
- Statement coverage for `src/amber`: 94%; this is not branch coverage or roadmap completion.
- Ruff lint and format checks passed; 82 Python files checked for formatting.
- Mypy passed for 45 source files under the current non-strict configuration.
- Offline lock consistency and `git diff --check` passed.
- Installed core-only facade, grounding, claim commit, restore, and CLI smoke checks passed.
- Two existing Starlette/AnyIO deprecation warnings remain; no test failures or skips were reported.

Checks used isolated environments without replacing the project environment. See the
[test guide](../../tests/README.md) for reproduction commands. The worktree was clean before this
note was added, and no commits were pushed. These results validate software behavior, not clinical
accuracy, safety, or reductions in expert effort.

## Assessment and remaining gaps

The dependency boundaries and repository structure are suitable for the next increment; a broad
reorganization is not needed. The main risk is continuing to generalize infrastructure before
completing a measurable workflow.

- **Case outcomes:** `cases.py` is a placeholder. Explicit negative answers, not mentioned,
  conflict, insufficient evidence, and execution failure still need executable consistency rules.
- **Policy enforcement:** `policy.py` is a placeholder. Declaring sensitivity or checking it
  against provenance does not authorize provider calls or source-bearing transfers.
- **Persistence:** `store/` and `export/` have no working persistence/export path. Graph snapshots
  are source-bearing in-memory payloads, not durable storage or complete dataset exports.
- **Extraction:** the public facade currently exposes system information; extraction and annotation
  routers have no workflow. The provider adapter and prompt-registry helpers remain placeholders.
- **Clinical evidence:** task-specific independent adjudication, semantic-support assessment,
  baseline scoring, and expert-effort measurement remain outstanding. Candidate labels are not gold.

## Recommended next work

These are recommendations, not a newly approved implementation plan:

1. Implement `CaseOutcome` and a minimal case-result boundary, including explicit final-claim IDs
   and answer/abstention/failure consistency. Reuse the existing graph validation.
2. Implement provider and source-bearing destination policy checks before adding integrations.
3. Add one DuckDB persistence path with validated source/evidence/outcome round-trips and the
   roadmap's native Parquet export; define the required annotated-data contract without inventing
   an automatic gold-generation path.
4. Arrange qualified human adjudication alongside engineering, then establish M2's single-provider
   fixed baseline and evaluate clinical correctness and total expert effort.

Keep additional agents, backends, broad NLP, and UI expansion deferred. Follow the
[M1–M3 roadmap](../04-roadmap.md); the immediate priority is the smallest complete workflow that
can test Amber's value, not a broader platform.
