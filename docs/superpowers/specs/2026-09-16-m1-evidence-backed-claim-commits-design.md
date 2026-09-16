# M1 evidence-backed claim commits

Status: approved, implemented, and verified on 2026-09-16 under the
[checkpoint plan](../plans/2026-09-16-m1-evidence-backed-claim-commits.md). Independent reviews and
517 tests on each supported Python version cover this slice; it does not establish clinical
validation or complete M1.

## Purpose and authority

Connect the existing immutable note, exact grounding, and answer schema into a working path that
returns a proposed Claim only after validating its complete evidence support. This is the next M1
increment, not the remainder of M1 in one change.

The [v1 contract](../../02-v1-schemas-and-tools.md), especially sections 4–6 and 8, and
[working agreement](../../../CLAUDE.md) remain authoritative. This design follows the
[goals](../../00-goals-and-architecture.md), [roadmap](../../04-roadmap.md), and
[current-progression protocol](../../protocols/oncology_current_progression-v1.md).

At design approval, the foundations were `Source`, `Inclusion`, exact grounding, `AnswerModel`,
canonical IDs, and provenance; `graph.py` and `tools/commit.py` were placeholders. Ordinary
`Inclusion` construction was already guarded, but the graph still needed to independently recheck
evidence against the source.
Pydantic's `frozen=True` does not make nested dictionaries or lists immutable.

The chosen approach is an in-memory graph with atomic claim commits. A models-only increment
would defer the usable connection between quotes and answers; adding storage now would introduce
an additional transaction and serialization boundary. The in-memory slice tests the domain rules
before a database adapter needs to depend on them.

## Scope

Implement:

- The contract's `Task`, `Claim`, `InferenceEvidence`, and `EvidenceEdge` models, alongside the
  existing `Inclusion`. Use the existing oncology answer model without changing its protocol.
- One explicitly supplied text note and one explicitly supplied task per `EvidenceGraph`.
- Registration and validation of exact text evidence and source-backed inference chains.
- Full graph validation and deterministic traversal to the source-backed leaves.
- `commit_claim(...) -> Claim`, returning only after an atomic in-memory update succeeds.
- In-memory snapshot/restore through the same validation boundary, with no file or database I/O.
- Synthetic tests, public exports for the implemented APIs, and accurate implementation-status docs.

Do not add `Mention`, `StructuredEvidence`, `CaseOutcome`, `CaseResult`, `Example`, `no_claim`,
policy enforcement, persistence, exports, providers, prompts, API/CLI routes, human annotation,
clinical scoring, or gold generation. The graph rejects unsupported evidence kinds rather than
partially accepting them. Structured-source identity and mapping need their own later increment.
Multiple tasks or notes within one graph are also outside this slice.

No CORAL records, candidates, manifests, or split membership are read or changed. Only invented
notes are used. The completed preparation plan and frozen local CORAL artifact remain untouched.

## Ownership and dependency direction

| Location | Responsibility |
|---|---|
| `src/amber/schemas/tasks.py` | Task definition; no registry, discovery, or prompt loading |
| `src/amber/schemas/claims.py` | Guarded Claim value object and local field validation |
| `src/amber/schemas/evidence.py` | Existing inclusions plus inference and edge value objects |
| `src/amber/schemas/provenance.py` | Existing provenance, with protection of nested prompt versions |
| `src/amber/ids.py` | Existing hashing/ULIDs plus deterministic schema-reference construction |
| `src/amber/grounding.py` | Exact source verification and inclusion restoration through grounding |
| `src/amber/graph.py` | Complete support validation, traversal, owned state, snapshot/restore |
| `src/amber/tools/commit.py` | Thin typed commit entry point calling the graph boundary |
| `tests/test_claim_schemas.py`, `test_graph.py`, `test_commit.py` | Synthetic schema, graph, and end-to-end tests |

Use a small private schema helper for nested immutable values if needed; do not create a general
serialization framework or add dependencies. Schemas never import graph/tools/services. The
graph depends on schemas, IDs, and grounding; tools depend on the graph. No optional stack is
imported by core Amber. Configuration and environment lookup are absent from this path.

## Domain contracts

### Task and schema identity

Implement the Task fields from the v1 contract: `name`, `answer_model`, `instructions`,
`evidence_policy`, and `scope`. An answer model must subclass `AnswerModel`. The graph accepts only
`scope="note"`; a patient-scoped Task is not silently treated as note-scoped. Instructions are
caller-supplied metadata, not a new prompt definition or an executed prompt in this slice.

Bind the graph to one Task at construction. No task registry, dynamic module loading, entry-point
discovery, or network lookup is introduced. Tests construct explicit tasks, including one using
the exported current-progression task/scope/evidence-policy constants.

Construct `schema_ref` in `ids.py` as
`<answer_model.__module__>:<answer_model.__qualname__>@<schema_hash>`, using `canonical_sha256` on
`answer_model.model_json_schema()` for the hash. Loading compares against the explicitly supplied
Task and validates values with that model; it never imports code named in a payload. A JSON-schema
hash is not a hash of every Python validator or clinical rule. Protocol and package versioning
remain necessary when their semantics change.

### Claims

Keep every Claim field from the v1 contract, including patient/source identity, schema reference,
effective datetime, status, confidence, and provenance. `value` remains an answer dictionary,
never `None`. The complete value must validate under the graph's Task, including strict booleans.
When supplied, confidence must be finite and in `[0, 1]`; it is not an acceptance threshold.

Only a successful graph boundary may issue a Claim instance through the supported API. Ordinary
constructor and standalone deserialization paths reject construction without the internal
validation context. Internal candidate payloads are not public Claims. That construction guard
is an accidental-misuse barrier, not a security boundary against arbitrary Python execution.

`commit_claim` always creates `status="proposed"`. Passing graph validation proves structural
validity and source traceability, not clinical correctness. The tool has no option to mint verified
or gold status. The model reserves the contract's complete status vocabulary, but graph admission
and restore accept only `proposed` and `rejected` in this increment. Verified/gold records require
a later trusted review/import path and are rejected here. There is no status-editing or
human-review workflow in this slice.

Rejected claims still require valid local fields, task/schema identity, and case scope. They need
not have supporting edges, but any retained edges must be valid. No inference may consume a
rejected claim. Unsupported non-rejected claims are never admitted, including on restore.

### Inferences and edges

Use the contract's inference fields: ULID, nonblank rationale, nonempty input ID list, trace/span
IDs, and provenance. Inputs reference known evidence or non-rejected claims; a raw source or
mention ID is not an evidence input. Duplicate inputs are rejected. A standalone inference record
has only local shape validation; it is not accepted support until admitted by graph validation.

Edges retain `claim_id`, `evidence_id`, `role`, and optional `weight`. Edges point from a Claim to
an evidence node, never directly to another Claim. Claim-to-claim support runs through inference
inputs. Role names are either `None` or actual answer-field names. Weights, when present, must be
finite; they cannot waive support requirements. Duplicate edge triples are rejected, including
duplicates with differing weights.

### Immutability

All new value objects are frozen and extra-forbidden. Defensively copy caller-owned containers,
then prevent ordinary nested mutation of Claim values, inference inputs, and provenance prompt
versions. Preserve the contract's dictionary/list serialization shapes; immutable internal
containers must not introduce additional wire fields or change the public field meanings.

Graph state must never alias a mutable input mapping, answer payload, source metadata container,
or returned snapshot. Public graph views are read-only or detached copies. Mutating a serialized
payload cannot mutate the graph that produced it. Explicit validation bypasses such as
`model_construct` or unchecked `model_copy(update=...)` never confer trust: graph admission and
restore must validate their data again, including nested objects.

## Graph context and text scope

An `EvidenceGraph` owns one revalidated `Source`, one Task, and an explicitly declared
`Sensitivity`. The source must be a note with text. Source and patient identities come from that
source, not from separate caller-supplied claim fields. Capture source/task context defensively.
Every retained claim and every inclusion must refer to that same source and patient as applicable.
Every retained Claim and InferenceEvidence provenance must match the graph's declared sensitivity.
Do not infer sensitivity from source content. This consistency check is not the provider policy
gate and does not authorize a data transfer.

Accept explicit excluded text intervals as an optional graph-context input. Validate nonempty,
strict-integer, end-exclusive bounds against the exact source. Reject overlapping intervals;
adjacent intervals are permitted, and disjoint fragments remain separate. An evidence span that
overlaps any excluded interval is rejected; touching a boundary is not overlap. No source text
is removed or normalized, and all offsets remain absolute.

The caller, not the domain kernel, is responsible for supplying exclusions under the task's
coverage policy. CORAL `SectionSkip` parsing stays in the experiment and is not wired into the
kernel in this increment. An empty exclusion list does not certify complete annotation inventory
or clinical review. Invalid or unresolved experiment coverage cannot be converted into a trusted
scope by this API; future adapter integration must handle that explicitly.

## Validation boundary

Registration, commit, and restore all use the same complete validation, not separate weaker paths.
Validate all retained nodes and edges, including disconnected components, before publishing state:

1. Validate the source identity and context, Task binding, model fields, and declared sensitivity.
   Reject unknown fields, blank IDs, non-finite values, and malformed bounds. Index records without
   silently replacing duplicate IDs; claim/evidence ID collisions are rejected.
2. Recheck every inclusion's kind, source ID, canonical evidence ID, strict bounds, and
   `quote == source.text[start:end]`, including exclusions. Accept exact alignment with score `1.0`
   only. Fuzzy records and non-null `mention_id` are unsupported until those paths can be validated.
3. Validate every claim's complete answer with the supplied Task; verify task/schema/source/patient
   identity. Validate all edge targets and inference references, independent of record order.
4. Require at least one evidence edge for every non-rejected claim. Under `evidence_policy="field"`,
   require a nonempty field-specific support set for every declared answer field, including fields
   populated by defaults. A `role=None` edge cannot satisfy a field requirement. Under claim policy,
   overall support is required and any supplied field roles must still be valid.
5. Check the directed graph formed by claim-to-evidence edges and inference-to-input references.
   Reject cycles, including self-cycles and mixed claim/inference cycles. Use iterative traversal
   and memoization so an ordinary deep valid chain does not fail at Python's recursion limit.
6. Require every branch of every non-rejected claim and every inference to terminate in an exact,
   source-verified inclusion. One valid leaf does not rescue an empty, unknown, rejected, cyclic,
   or source-free sibling branch. Revalidate supporting claims with their own field policies.

Valid unused inclusions may remain registered. Valid disconnected support chains may remain too;
they must meet the same rules. A rejected claim may lack evidence; it is never a support leaf.
Graph traversal returns the complete reachable support closure, not only a convenient valid path.
Do not interpret clinical meaning, resolve time, or decide whether a quote entails the answer.

## Registration and atomic claim commit

Provide an evidence-registration method for existing inclusions and inference records. It validates
against a staged copy of the graph and publishes only on success. Registering an identical existing
evidence record is idempotent; a different record with the same ID is an error. Snapshot payloads
must contain unique records even when duplicates are identical. An inference can only be registered
after its inputs exist; restore supports arbitrary record order by validating the whole batch.

The typed tool preserves the contract's primary inputs and `Claim` return type:

- `task`, `value`, `evidence_ids`, `rationale`, and optional `field_roles`;
- explicit keyword-only graph context and provenance; and
- optional effective datetime and confidence, with omitted effective datetime using the source's
  datetime (including `None` when the source date is unavailable).

The supplied Task must match the graph binding. Both `true` and `false` undergo identical evidence
validation. `evidence_ids` must be a nonempty, duplicate-free set of already registered evidence
IDs. `field_roles` maps known answer-field names to nonempty, duplicate-free subsets of those same
evidence IDs. It cannot smuggle in extra inputs, raw claim IDs, or unsupported fields.

Stage a new Claim payload and one new InferenceEvidence containing the nonblank commit rationale,
the supplied evidence IDs as inputs, and the explicit provenance. Copy `provenance.trace_id` into
the inference's `trace_id` and set `span_id=None`; tracing integration is not added. Create a
whole-claim edge to this inference and direct field-role edges to the supplied field evidence.
This retains the rationale without pretending it supplies a missing field citation. The resulting
support closure must satisfy the full validation boundary before any new node is visible.

Generate Claim and inference IDs through `new_ulid()`. Repeating a commit produces distinct Claim
and inference IDs; evidence registration remains idempotent. This is not a deduplicating job API.
Failures leave the previous graph unchanged and return no Claim, partial inference, or edges.
Success replaces the owned in-memory state in one operation and returns its proposed Claim.
There is no concurrent-writer or persistent-transaction guarantee in this increment.

## In-memory restore and errors

Expose `to_payload()` and a validated `from_payload(...)` boundary with strict integer
`format_version=1`. The payload contains task/schema/policy identity, source/patient identity,
declared sensitivity, exclusions, and the complete retained claim/evidence/edge records. Produce stable
ordering by node IDs and edge triples. It is source-bearing data because it includes quotes;
neither the tool nor graph prints or logs it.

Restore requires the caller to supply the authoritative Source, Task, and declared sensitivity
and exclusions. Compare the payload context to those inputs and reject any mismatch or unknown
format version. This is not a standalone dataset export: the referenced Source is required to
recheck the evidence, and no storage adapter is added.

Raw inclusion payloads return through the exact grounding path using their recorded start as a
hint; require all supplied identity, quote, bounds, and supported alignment fields to match the
verified result. Do not repair offsets or discard unrecognized metadata. Raw claim payloads remain
internal until the full graph has validated. No trusted-deserialization flag skips these checks.

Use a typed graph-validation exception with stable diagnostic codes: `invalid_schema`,
`invalid_context`, `invalid_span`, `quote_mismatch`, `unsupported_evidence`,
`unsupported_claim_status`, `duplicate_reference`, `unknown_reference`, `missing_evidence`,
`missing_field_evidence`, `cyclic_support`, and `source_free_support`. Invalid local inference/edge
fields use `invalid_schema`; a changed evidence identity uses `invalid_context`. Public error
strings must not interpolate source text, quotes, answer values, or source-bearing underlying
validation exceptions. Errors are not clinical outcomes: runtime translation into a future
`CaseOutcome` remains outside this slice.

## Acceptance tests

All tests run offline on CPU with invented text. Cover:

- A positive and an explicit-negative oncology answer committed from exact text, with correct task,
  schema reference, patient, effective datetime, provenance, and field edges. Both are proposed.
- Required strict booleans, no null answers, extra fields, blank rationales/IDs, empty inference
  inputs, invalid confidence/weights, unsupported evidence kinds, and guarded Claim construction.
  Reject verified/gold status on admission or restore, even with otherwise valid evidence.
- Nested mutation attempts and caller aliasing for values, inputs, provenance, source context,
  graph views, and payloads. Tampered unchecked Pydantic copies are rejected on graph admission.
- Invalid/reversed/empty/boolean/float/out-of-range offsets, stale IDs, fabricated or normalized
  quotes, Unicode and CRLF, and repeated quotes restored to the exact original occurrence.
- Valid and invalid exclusions, adjacency, disjoint-fragment gaps, and scope mismatch on restore.
- Wrong source/patient/task/schema/sensitivity, unknown IDs, duplicate IDs and edges, wrong-kind
  references, rejected supporting claims, and shuffled snapshot record order.
- A synthetic two-field answer showing that whole-claim support and support for one field cannot
  replace the other field's evidence. Validate supporting claims' field roles independently.
- Self-cycles, evidence-only cycles, mixed claim/inference cycles, disconnected bad components,
  and a support graph with both a valid leaf and an invalid branch. Include deep valid chains.
- Successful snapshot round-trips and corrupted snapshots; exact source verification runs on
  both registration and restore. Restore cannot relax evidence policy or exclusions.
- Atomic failures at each validation stage; no partially added Claim, inference, or edge survives.
  Duplicate commits have distinct ULIDs; identical inclusion registration is idempotent.
- A deliberately semantically wrong but exactly grounded citation passes structural validation:
  this documents the boundary without calling the claim clinically supported or gold.
- Fresh-process lightweight core imports and the existing source/grounding/interface regressions.

Start with focused schema/graph/commit tests, then the full suite on Python 3.11 and 3.12, Ruff lint
and format checks, `mypy src`, offline lock consistency, and `git diff --check`. See the
[test guide](../../../tests/README.md) for dependencies. No clinical audit or model run is required.

## Completion and checkpoint boundaries

The implementation plan should keep commits independently safe: guarded models and graph
validation first, then the commit tool and round-trip integration, with adversarial tests beside
the behavior they verify. No intermediate commit exposes an unenforced non-rejected-claim path.
Work stays in the existing checkout with explicit file staging; do not push.

This slice is complete when a caller can ground invented text, register evidence, commit a typed
answer with field support, and restore its graph with all invariants rechecked. Failure paths must
leave the graph unchanged. Update the canonical status documentation only as capabilities land.
M1 remains incomplete; the next designs can address outcomes/policy and local persistence without
claiming that structural evidence validation establishes clinical performance.
