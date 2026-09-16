# Evidence-Backed Claim Commits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect exact grounded text to proposed typed claims through complete, atomic in-memory
evidence validation.

**Architecture:** Immutable schema records feed one deterministic validation boundary. A small
in-memory graph owns context and publishes validated state atomically; the commit tool delegates
to it. Keep detailed validation in private `_graph_validation.py` so graph lifecycle and validation
do not become one large module.

**Tech Stack:** Python 3.11/3.12, Pydantic v2, existing ULID/hash helpers, stdlib, pytest, Ruff, mypy.

**Spec:** [Approved design](../specs/2026-09-16-m1-evidence-backed-claim-commits-design.md),
approved by the user on 2026-09-16. The [v1 contract](../../02-v1-schemas-and-tools.md) and
[working agreement](../../../CLAUDE.md) remain binding.

**Progress:** Task 1 complete (347 tests on both Python versions; independent review approved).
Tasks 2–4 remain. Each checkpoint includes tests, review, and a commit.
Continue the established subagent-driven workflow in the current checkout; no push.

## Global Constraints

- All new value objects are frozen and extra-forbidden.
- Preserve the contract's dictionary/list serialization shapes; immutable internal containers
  must not introduce additional wire fields or change the public field meanings.
- One explicitly supplied text note and one explicitly supplied task per `EvidenceGraph`.
- `commit_claim` always creates `status="proposed"`.
- Graph admission and restore accept only `proposed` and `rejected` in this increment.
- The graph rejects unsupported evidence kinds rather than partially accepting them.
- No CORAL records, candidates, manifests, or split membership are read or changed.
- Only invented notes are used. No dependencies, models, I/O, database, provider, API, or prompt
  integration is added. Keep core imports lightweight and schemas independent of graph/tools.
- Reject unknown references, missing field evidence, cycles, and every source-free support branch.
  Structural validation does not establish semantic support or authorize data transfers.
- Explicit validation bypasses such as `model_construct` or unchecked `model_copy(update=...)`
  never confer trust: graph admission and restore must validate their data again.
- Public error strings must not interpolate source text, quotes, answer values, or source-bearing
  underlying validation exceptions.
- `format_version=1` is a strict integer. Restore requires authoritative context from the caller.
- Work stays in the existing checkout with explicit file staging; do not push.

## File map and verification conventions

- Schema prerequisites: `_immutable.py`, `tasks.py`, `claims.py`, existing `evidence.py`,
  `provenance.py`, `schemas/__init__.py`, and `ids.py` under `src/amber/`.
- Complete validation: `src/amber/_graph_validation.py` and `src/amber/graph_errors.py`.
- Owned graph: `src/amber/graph.py`; reuse `grounding.exact_quote` for restored inclusions.
- Tool: `src/amber/tools/commit.py`.
- Tests: `tests/_claim_helpers.py`, `test_claim_schemas.py`, `test_graph_validation.py`,
  `test_graph.py`, `test_commit.py`; extend the existing core-import and installed smoke checks.
- Status documentation: `docs/README.md`, `tests/README.md`, and this plan. The approved design's
  status can be updated as implementation lands; the old CORAL preparation plan is untouched.

Run commands using the existing temporary development environments, without syncing project `.venv`:

```sh
UV_PROJECT_ENVIRONMENT=/private/tmp/amber-answer-schema.RUhgD3/checks \
  uv run --no-sync --cache-dir /private/tmp/amber-structure-review-uv-cache pytest -q
UV_PROJECT_ENVIRONMENT=/private/tmp/amber-ci-check.QySipf/checks312 UV_PYTHON=3.12 \
  uv run --no-sync --cache-dir /private/tmp/amber-structure-review-uv-cache pytest -q
```

Use the first command's environment for focused pytest, Ruff, and mypy commands below. If these
temporary environments no longer exist, create a new temporary environment from the tracked lock
with dev/app/tracking, without modifying dependencies. Full checks include pytest on both versions,
`ruff check .`, `ruff format --check .`, `mypy src`, `uv lock --check --offline`, and
`git diff --check`. Do not run a clinical corpus audit for this synthetic-only slice.

### Task 1: Immutable task, claim, and inference records

**Files:**

- Create: `src/amber/schemas/_immutable.py`, `tasks.py`, `claims.py`.
- Modify: `src/amber/schemas/evidence.py`, `provenance.py`, `__init__.py`, `src/amber/ids.py`.
- Create: `tests/_claim_helpers.py`, `tests/test_claim_schemas.py`.
- Update status: `docs/README.md`, `tests/README.md`, this plan (controller-owned).

**Interfaces:**

- Consume existing `AnswerModel`, `Provenance`, `Inclusion`, `canonical_sha256`, `new_ulid`.
- Produce `freeze_json(value: Any) -> Any` in the private immutable helper; JSON scalar values,
  read-only dictionary/list subclasses, rejection of unsupported/non-finite values.
- Produce `schema_ref(answer_model: type[BaseModel]) -> str` and
  `validate_ulid(value: str) -> str` in `ids.py`.
- Produce public `Task`, `Claim`, `InferenceEvidence`, `EvidenceEdge` through `amber.schemas`.
- Produce private `_ClaimRecord` for local candidate validation and
  `_mint_claim(data: Mapping[str, Any]) -> Claim` for the later validated graph boundary only.
- Test helpers: `make_source(text: str = "Alpha beta") -> Source`,
  `make_provenance() -> Provenance`, and
  `make_task(answer_model: type[AnswerModel] = OncologyCurrentProgressionAnswer,
  evidence_policy: Literal["claim", "field"] = "field") -> Task`.

- [x] **Step 1: Write failing immutable-value and model-contract tests**

Use locally constructed synthetic provenance and inference records. Assert mutation cannot change
validated data, including nested dictionaries/lists and repeated `__init__` calls. Cover all normal
container mutation methods, in-place operators, input aliasing, deepcopy, and JSON serialization:

```python
def test_freeze_json_breaks_aliases_and_preserves_wire_shape():
    original = {"rows": [{"active": True}]}
    frozen = freeze_json(original)
    original["rows"][0]["active"] = False
    with pytest.raises(TypeError):
        frozen["rows"][0]["active"] = False
    with pytest.raises(TypeError):
        frozen["rows"].append({})
    assert json.loads(json.dumps(frozen)) == {"rows": [{"active": True}]}
```

Add cases for non-finite floats, non-string keys, bytes/unsupported objects, Task subclass checking,
all v1 fields, no extra fields, blank IDs/rationales, duplicate/empty inference inputs, canonical
ULIDs, finite weights, and confidence in `[0, 1]`. A Claim cannot be directly constructed or loaded
as a standalone model, including rejected status; tests use `_ClaimRecord` for local fields and
wait until Task 2 to obtain a real Claim through complete validation. Do not call the private minter
to manufacture unsupported test Claims. Revalidate provenance from its dumped fields to reject
unchecked forged nested instances.

- [x] **Step 2: Demonstrate RED**

Run `uv run pytest tests/test_claim_schemas.py -q` in the documented environment. Initially the new
imports do not exist. Record this absence, then run incremental behavior tests before implementing
their corresponding changes; do not rely solely on import failure as evidence for every rule.

- [x] **Step 3: Implement nested immutability and exact schema references**

Use private dictionary/list subclasses compatible with JSON/Pydantic output. Constructors
recursively copy/freeze inputs once; reinitialization and mutators raise a fixed `TypeError`.
Immutable copies/deepcopies can return themselves. Ordinary `.copy()`/serialization may return
detached mutable containers. Python base-class bypasses are not a security boundary; later graph
validation still rechecks incoming data. Keep the helper local to schema value protection.

```python
def schema_ref(answer_model: type[BaseModel]) -> str:
    digest = canonical_sha256(answer_model.model_json_schema())
    return f"{answer_model.__module__}:{answer_model.__qualname__}@{digest}"
```

`validate_ulid` rejects noncanonical strings and returns the unchanged input only after parsing
with the installed `python-ulid` API and checking that its canonical string is identical. It never
generates or repairs an ID. Test hashing with an independently constructed stdlib canonical JSON
digest, not by computing the expected answer with `schema_ref` itself.

- [x] **Step 4: Implement guarded and closed domain records**

Task fields: `name: str`, `answer_model: type[AnswerModel]`, `instructions: str`,
`evidence_policy: Literal["claim", "field"] = "claim"`, and
`scope: Literal["note", "patient"] = "note"`. Reject blank names; do not load prompts.

`_ClaimRecord` contains exactly the v1 Claim fields and their local validation. Claim subclasses
that private record and adds the private construction-context guard. Apply nested freezing after
local validation; the private record itself is not exported as an accepted clinical Claim.

```python
_CLAIM_MINT_CONTEXT = object()

def _mint_claim(data: Mapping[str, Any]) -> Claim:
    return Claim.model_validate(dict(data), context={"claim_minter": _CLAIM_MINT_CONTEXT})
```

The Claim validator rejects any other context. Graph validation is the only future production
caller of this helper. No public ungated factory is introduced. Claim/inference IDs are canonical
ULIDs. Other identifiers are strict, nonblank strings. Claim status preserves all four contract
values; graph acceptance narrows them in Task 2. Confidence is optional, finite, and in `[0, 1]`.

Inference fields match the v1 contract, including nullable trace/span IDs and revalidated
provenance. Inputs are nonempty, distinct strict nonblank strings. Edge fields match the contract;
role is nullable/nonblank and weight nullable/finite. Freeze inference inputs and Claim values
recursively, and freeze `Provenance.prompt_versions` without changing wire types. Test that standard
`model_dump` and JSON dumps return the original field shapes.

- [x] **Step 5: Verify, review, and commit prerequisites**

Run focused schema tests and existing answer/invariant tests, then the full checks. Update status
to describe only implemented guarded records, not a working commit path. Review this task before
committing only its source/tests/status changes with subject `M1: add immutable claim primitives`.

### Task 2: Complete deterministic graph validation

**Files:**

- Create: `src/amber/_graph_validation.py`, `src/amber/graph_errors.py`.
- Create: `tests/test_graph_validation.py`; extend `tests/_claim_helpers.py` as needed.
- Update status: `docs/README.md`, `tests/README.md`, this plan (controller-owned).

**Interfaces:**

- Consume Task 1's models, `_ClaimRecord`, `_mint_claim`, `schema_ref`, and existing `exact_quote`.
- Produce `GraphValidationError(ValueError)` with a public stable `code` and fixed safe string.
- Produce private frozen dataclasses `_GraphContext` and `_ValidatedState`.
- `build_context(*, source: Source, task: Task, sensitivity: Sensitivity,
  excluded_spans: Sequence[tuple[int, int]] = ()) -> _GraphContext`.
- `validate_state(context: _GraphContext, *, claims: Sequence[Mapping[str, Any]],
  evidence: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]) -> _ValidatedState`.
- `_ValidatedState.claims: Mapping[str, Claim]`, `.evidence: Mapping[str, Inclusion |
  InferenceEvidence]`, `.edges: tuple[EvidenceEdge, ...]`, and
  `.adjacency: Mapping[str, tuple[str, ...]]`. All maps are read-only and owned by this result.
- `_GraphContext.source`, `.task`, `.sensitivity`, `.excluded_spans`, and `.schema_ref` are captured
  from defensively copied/revalidated inputs. Public graph code must not expose their mutable aliases.

- [ ] **Step 1: Write failing graph validation tests with literal synthetic graphs**

Create payload helpers in `tests/_claim_helpers.py` for complete candidate claim fields and
inference fields; use valid generated IDs as inputs, not as the expected result of an ID test.
Ground leaves through real `exact_quote`. Construct a valid one-field claim payload with a direct
field edge; `validate_state` must issue a real guarded Claim only after checking the complete set.

```python
def test_whole_claim_edge_does_not_satisfy_field_policy():
    source = make_source("Stable disease.")
    leaf = exact_quote(source, "Stable disease.")
    assert isinstance(leaf, Inclusion)
    context = build_context(source=source, task=make_task(), sensitivity=Sensitivity.synthetic)
    record = make_claim_record(source=source, value={"progression_or_recurrence": False})
    with pytest.raises(GraphValidationError) as failure:
        validate_state(
            context,
            claims=[record], evidence=[leaf.model_dump()],
            edges=[{"claim_id": record["claim_id"], "evidence_id": leaf.evidence_id}],
        )
    assert failure.value.code == "missing_field_evidence"
```

Define `make_claim_record(*, source: Source, value: dict[str, Any], task: Task | None = None)
-> dict[str, Any]` in the helper module, including every Claim field, proposed status, and synthetic
provenance. A supplied task controls schema identity; absent task uses `make_task()`.

Cover the spec's graph cases: blank/malformed local fields, wrong source/patient/task/schema/
sensitivity, duplicate IDs and edges, unknown/wrong-kind references, unsupported evidence/status,
rejected support, missing field roles, and all disconnected components. Include a two-field Task
whose defaulted second field still needs its own edge. Check repeated/Unicode/CRLF text with exact
recorded offsets, strict bounds, mismatched IDs, malformed exclusions, touching boundaries, and
valid disjoint-fragment gaps. Use self/mixed/disconnected cycles and a valid branch alongside an
invalid branch. A chain deeper than Python's recursion limit must validate without recursion.

- [ ] **Step 2: Demonstrate RED and implement context and leaf checks**

Run `uv run pytest tests/test_graph_validation.py -q` before implementation; develop in small
RED/GREEN groups. Source/Task/provenance inputs must be dumped and revalidated, not trusted because
they are Pydantic instances. Require one note with text, note scope, explicit sensitivity, valid
sorted nonoverlapping exclusions, and correct schema reference. Never normalize source text.

For inclusion payloads, reject unknown fields, missing required fields, wrong source/ID, invalid
strict bounds, and unsupported alignment or mention links before issuing a new Inclusion. Use the
existing grounding function only after safe prechecks:

```python
if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
    raise GraphValidationError("invalid_span")
if quote != text[start:end]:
    raise GraphValidationError("quote_mismatch")
if any(start < hi and lo < end for lo, hi in context.excluded_spans):
    raise GraphValidationError("invalid_context")
verified = exact_quote(context.source, quote, hint_start=start)
```

`start`, `end`, `text`, and `quote` above are locally validated fields, not coerced candidate
values. Require exact alignment score `1.0` (not boolean), `mention_id=None`, and canonical ID.
Compare the resulting Inclusion with every supplied supported field; unknown metadata is not
discarded. This wrapper may remain in `_graph_validation.py`; actual minting stays in grounding.

- [ ] **Step 3: Implement full reference and support validation before Claim minting**

First validate all local records and build duplicate-checked indexes. Inference/claim provenance
must match context sensitivity; claims must match Task, schema, source, and patient. Validate the
complete answer with `task.answer_model.model_validate`, retain its JSON-compatible dictionary,
and preserve strict booleans. Do not turn validation failures into clinical outcomes.

Build adjacency from Claim edges and inference inputs; inclusions have no children. Edge targets
are evidence only, inference inputs may also be non-rejected claims, and every reference must be
known. Reject verified/gold claims even if otherwise valid. Rejected claims may lack evidence,
but cannot be consumed and cannot retain invalid edges. Check field coverage independently for
every non-rejected claim. A `None` role never counts as a field role.

Use iterative reverse-topological evaluation, not recursive DFS. Count unprocessed child nodes,
start with nodes with no children, and propagate to parents. A node is source-backed exactly when
it is an Inclusion, or has nonempty children all of which are source-backed. Fewer processed nodes
than indexed nodes means a cycle. Check every component, not just the newest claim:

```python
backed[node_id] = node_id in inclusion_ids or (
    bool(adjacency[node_id]) and all(backed[child] for child in adjacency[node_id])
)
```

After cycle/reference/field checks and source reachability succeed, call `_mint_claim` for each
candidate and return read-only `_ValidatedState`. Earlier failures return no accepted Claims.
The source-free check is a defense in depth: more specific unknown-reference, missing-evidence,
empty-input, or cycle errors may reject a bad branch first.

- [ ] **Step 4: Freeze safe diagnostics and verify the task**

Allowed codes are exactly `invalid_schema`, `invalid_context`, `invalid_span`, `quote_mismatch`,
`unsupported_evidence`, `unsupported_claim_status`, `duplicate_reference`, `unknown_reference`,
`missing_evidence`, `missing_field_evidence`, `cyclic_support`, and `source_free_support`.
The exception string is the code only. Wrap underlying validation/serialization errors without
including their source-bearing messages; suppress their rendered cause with `from None`.
Unsupported node kinds use `unsupported_evidence`; local model errors use `invalid_schema`;
identity/sensitivity/scope mismatches and rejected support use `invalid_context`.

```python
with pytest.raises(GraphValidationError) as error:
    validate_state(context, claims=[bad_record], evidence=[], edges=[])
assert "invented sensitive value" not in str(error.value)
```

Run focused validation/schema tests, full checks, and review. Commit source/tests/status changes
with `M1: validate complete evidence support`. No public commit tool exists at this checkpoint.

### Task 3: Owned graph state, registration, and validated restore

**Files:**

- Implement: `src/amber/graph.py`.
- Create: `tests/test_graph.py`; extend `tests/_claim_helpers.py` only for shared invented data.
- Update status: `docs/README.md`, `tests/README.md`, this plan (controller-owned).

**Interfaces:**

- Consume Task 2's `build_context`, `validate_state`, `_GraphContext`, `_ValidatedState`, and error.
- Produce `EvidenceGraph(*, source: Source, task: Task, sensitivity: Sensitivity,
  excluded_spans: Sequence[tuple[int, int]] = ())` with an initially validated empty state.
- `register_evidence(record: Inclusion | InferenceEvidence) -> Inclusion | InferenceEvidence`.
- Read-only `.claims`, `.evidence`, `.edges`; detached `.source` and `.task` properties;
  `.sensitivity` and immutable `.excluded_spans`.
- `validate() -> None`, `support_closure(node_id: str) -> tuple[str, ...]` (sorted IDs including
  the requested node), and `source_leaves(node_id: str) -> tuple[Inclusion, ...]` (sorted by ID).
- `to_payload() -> dict[str, Any]`; `EvidenceGraph.from_payload(payload: Mapping[str, Any], *,
  source: Source, task: Task, sensitivity: Sensitivity,
  excluded_spans: Sequence[tuple[int, int]] = ()) -> EvidenceGraph`.
- Keep state publication private. Task 4 adds a graph-owned commit method; do not add a public raw
  state-replacement method or a test-only reset API.

- [ ] **Step 1: Write failing registration, ownership, and restore tests**

```python
def test_registration_and_payload_are_isolated():
    source = make_source("Stable.")
    graph = EvidenceGraph(source=source, task=make_task(), sensitivity=Sensitivity.synthetic)
    leaf = exact_quote(source, "Stable.")
    assert isinstance(leaf, Inclusion)
    graph.register_evidence(leaf)
    graph.register_evidence(leaf)
    assert len(graph.evidence) == 1
    payload = graph.to_payload()
    payload["evidence"][0]["quote"] = "invented alteration"
    assert graph.evidence[leaf.evidence_id].quote == "Stable."
```

Add inference registration only after its inputs exist, mismatched-ID idempotency conflicts,
forged unchecked model copies, source/Task aliasing, immutable graph views, full support traversal,
and unknown-node errors. Snapshot tests construct valid raw candidate graphs using Task 2 helpers
so valid Claims can be restored before the commit tool exists. Round-trip through stdlib JSON.
Shuffle each record list and verify deterministic output and identical support closure. Corrupt
one record/header field at a time and assert safe rejection and unchanged existing graph state.

- [ ] **Step 2: Demonstrate RED and implement graph lifecycle**

Run `uv run pytest tests/test_graph.py -q` before implementing the facade. All mutation candidates
use fresh record dumps and Task 2 validation; compare an existing evidence ID against the fully
validated incoming record before declaring idempotency. No early return may trust a forged copy.

```python
candidate_state = validate_state(
    self._context, claims=claim_records, evidence=evidence_records, edges=edge_records
)
self._state = candidate_state
```

The three record sequences are detached dumps of existing state plus a proposed change. The single
assignment is the publication point; all checks precede it. Empty initialization, `validate`, and
restore share the same boundary. Views cannot leak internal mutable state; source/Task reads
produce independently revalidated copies. Iterative traversal uses validated adjacency, returns
all branches, and raises `unknown_reference` for unknown starting IDs.

- [ ] **Step 3: Implement the strict snapshot envelope and revalidation**

Use this exact envelope, with all context keys required and unknown keys rejected:

```json
{
  "format_version": 1,
  "context": {
    "task": "oncology_current_progression",
    "schema_ref": "supplied task schema reference",
    "evidence_policy": "field",
    "scope": "note",
    "source_id": "supplied source identity",
    "patient_id": "supplied patient identity",
    "sensitivity": "synthetic",
    "excluded_spans": []
  },
  "claims": [],
  "evidence": [],
  "edges": []
}
```

The identity strings above describe payload values derived from explicit context, not constants
to hard-code. Freeze the format version; reject booleans/floats/strings in its place. Context
comparison includes all listed fields, strict interval validation, and the caller's authoritative
exclusions. Do not trust JSON's `true == 1` coincidence in Python equality. Do not load classes
from schema-reference strings. Node records pass through the same complete validation, which
restores inclusions through exact grounding and mints Claims only after support succeeds.

Sort claim/evidence records by ID. Sort edges by `(claim_id, evidence_id, role or "")`; empty role
strings are invalid, so this ordering distinguishes real field roles from whole-claim edges.
Use model dumps in JSON mode for dates and enums. Return detached containers and never print
payloads. The actual Source is deliberately caller-supplied, not embedded in this envelope.

- [ ] **Step 4: Verify and commit graph lifecycle**

Run focused graph/validation/schema tests, full checks, and review. Commit only intended files
with `M1: add validated in-memory evidence graphs`. Record that graph support/restore works while
the commit tool and clinical outcomes remain unimplemented.

### Task 4: Atomic proposed-claim tool and vertical-slice verification

**Files:**

- Modify: `src/amber/graph.py`, `src/amber/tools/commit.py`.
- Create: `tests/test_commit.py`.
- Extend: `tests/test_interfaces.py`, `scripts/check_core_install.py`.
- Update: `docs/README.md`, `tests/README.md`, the design status, and this plan.

**Interfaces:**

- Consume the graph/schema/validation APIs from Tasks 1–3 and existing `new_ulid`.
- Add the graph-owned method `commit_claim` and thin tool with the same typed signature:

```python
def commit_claim(
    task: Task,
    value: dict[str, Any],
    evidence_ids: Sequence[str],
    rationale: str,
    field_roles: Mapping[str, Sequence[str]] | None = None,
    *,
    graph: EvidenceGraph,
    provenance: Provenance,
    effective_datetime: datetime | None = None,
    confidence: float | None = None,
) -> Claim:
    return graph.commit_claim(
        task, value, evidence_ids, rationale, field_roles,
        provenance=provenance,
        effective_datetime=effective_datetime,
        confidence=confidence,
    )
```

The graph method omits the `graph` argument. For this API `effective_datetime=None` means use the
source datetime; a supplied datetime overrides it. The resulting field may be `None` when the
source date is absent. Do not introduce a status parameter.

- [ ] **Step 1: Write failing end-to-end commit tests**

```python
@pytest.mark.parametrize("value", [True, False])
def test_commit_retains_field_support_and_only_proposed_status(value):
    source = make_source("An invented current cancer statement.")
    task = make_task()
    graph = EvidenceGraph(source=source, task=task, sensitivity=Sensitivity.synthetic)
    leaf = exact_quote(source, "An invented current cancer statement.")
    assert isinstance(leaf, Inclusion)
    graph.register_evidence(leaf)
    claim = commit_claim(
        task, {"progression_or_recurrence": value}, [leaf.evidence_id],
        "Synthetic interpretation; no clinical assertion.",
        {"progression_or_recurrence": [leaf.evidence_id]},
        graph=graph, provenance=make_provenance(),
    )
    assert claim.status == "proposed"
    assert claim.value == {"progression_or_recurrence": value}
    assert claim.source_id == source.source_id
    assert claim.patient_id == source.patient_id
    assert [item.evidence_id for item in graph.source_leaves(claim.claim_id)] == [leaf.evidence_id]
    assert len(graph.claims) == 1
    assert len(graph.evidence) == 2
```

Cover all invalid value/input/role/rationale/provenance cases; repeated commits get distinct IDs;
effective datetime defaults/overrides; no partial state on rejection; correct trace propagation;
and read-only returned Claim fields/provenance. Capture payload before each bad operation and
assert exact unchanged payload afterward. Add a real multi-level supporting-claim chain, a
synthetic two-field defaulted answer, and snapshot round-trips after successful commits.

Use real graph validation rather than mocks for routine tests. A focused collision/failure
injection may replace ULID generation to prove that a late-stage error cannot publish state.
Add one exactly grounded but semantically wrong citation that passes structural checks, explicitly
showing why proposed status is not semantic verification. Do not add an automatic clinical judge.

- [ ] **Step 2: Demonstrate RED and implement graph-owned commit**

Run `uv run pytest tests/test_commit.py -q` first. Validate Task binding, a complete strict answer,
nonblank rationale, unique/nonempty evidence IDs, and field-role keys and ID subsets. Raw claim IDs
cannot be passed as evidence IDs; supporting claims must be referenced by registered inference.
All supplied field-role IDs must appear in the primary evidence ID list. Missing required field
roles fail with `missing_field_evidence`; no implicit role is inferred for the one-field task.

Stage complete Claim and inference records with distinct `new_ulid()` results. Claim source/patient
come from graph context, schema reference from its Task, value from validated answer output,
status proposed, and explicit provenance. Inference inputs retain the requested evidence IDs;
its trace ID equals `provenance.trace_id` and `span_id=None`. Create a whole-claim edge to this
inference and direct field edges to each declared field's evidence:

```python
new_edges = [{"claim_id": claim_id, "evidence_id": inference_id, "role": None, "weight": None}]
for role, ids in field_roles.items():
    new_edges.extend(
        {"claim_id": claim_id, "evidence_id": evidence_id, "role": role, "weight": None}
        for evidence_id in ids
    )
```

Here `field_roles` has already been validated and normalized to an empty mapping for claim policy
when omitted. Pass the complete candidate graph through `validate_state`, publish `_state` only
after success, and return its minted Claim. The thin tool does not duplicate validation or state.
No model, trace server, provider, or database call occurs.

- [ ] **Step 3: Verify core imports and installed behavior**

Extend the fresh-process import check to import the new schemas/graph/tool and assert no optional
web/agent/model/storage/tracking packages load. Extend the existing installed smoke script with a
synthetic source, grounded inclusion, registered evidence, proposed commit, and validated restore.
Use the script's existing assertion/error style; source text is invented, and no cleanup-only
production API is added. This tests packaging rather than duplicating every unit assertion.

- [ ] **Step 4: Verify the completed slice, review, and commit**

Run all focused tests, both full Python suites, Ruff, mypy, offline lock and whitespace checks.
Run the installed core smoke in a temporary core-only environment without changing dependencies
or the project environment. Update documentation with the implemented subset, safe synthetic usage,
and remaining restrictions. No automatic outcomes, semantic verification, policy gate, persistence,
or M1-complete claim is made.

Review this task, then the entire slice against the approved design. Resolve findings under the
established review workflow. Commit with `M1: commit evidence-backed proposed claims`, record final
verification counts, and leave a clean intended worktree without pushing.

## Plan self-review and spec coverage

| Design requirement | Implementation task |
|---|---|
| Frozen closed models, guarded Claims, canonical IDs/schema references | 1, exercised through 2/4 |
| Complete references, field policy, source leaves, all components, iterative cycles | 2 |
| Exact restore, note/patient/sensitivity/exclusion context, unsupported paths | 2 and 3 |
| State ownership, idempotent registration, detached stable snapshots | 3 |
| Atomic proposed commits, rationale inference, field edges, trace/date metadata | 4 |
| Clinical/non-goal separation, safe errors, offline tests, lightweight packaging | 1–4 |

The approved design permits private helpers; `_graph_validation.py` is an internal decomposition,
not a second graph API. Snapshot restoration preserves complete support but requires an external
authoritative Source. Public Claim construction remains guarded at every checkpoint. All task
interfaces above use the same names and value shapes; tests ship beside their enforcing behavior.
