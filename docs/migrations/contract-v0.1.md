# Migrating to contract v0.1

Contract v0.1 ([design](../design/contract-v0.1.md)) changes some records and identifiers that
existing code and stored payloads depend on. Each change is listed here when it is implemented,
with who is affected, how to upgrade, and the commit that made it. Changes still to be
implemented are listed at the end.

The other v0.1 additions (labelers, review backends, derivations and rules, the evaluation
harness) are additive: old payloads load unchanged and keep their IDs, so they need no
migration.

## Implemented

### Provenance gains destination, policy version, and restrictions

Commit: `6d1de59`.

`Provenance` now requires `destination` and `policy_version`, and accepts `restriction_ids`
(contract §1.6).

- **Affected:** every stored claim, inference, and case outcome, and every caller that builds a
  `Provenance`. Payloads without the new fields fail validation.
- **Upgrade:** set `destination` to the name of the destination that produced the node, or
  `local:<tool>` for a tool that sends nothing anywhere (its `zone` must then be `local`). Set
  `policy_version` to the policy matrix version in force, and `restriction_ids` to the IDs of
  the dataset restrictions that applied. Payloads produced before the policy gate existed
  cannot be re-stamped truthfully; regenerate them, or record them with the destination and
  policy version that actually applied.

### Producer references are validated

Commit: `6d1de59`.

`Provenance.producer` must be a producer reference: `rule:<name>@<version>[+<hash>]`,
`model:<name>[@<version>]`, `human:<user>`, or `tool:<name>[@<version>]` (contract §1.6).

- **Affected:** callers using free-form producer names.
- **Upgrade:** rewrite each producer in the grammar. A rule without a version needs one.

### Canonical JSON follows RFC 8785

Commit: `6d1de59`.

`amber.ids.canonical_json` and `canonical_sha256` implement RFC 8785 (contract §5.2).

- **Unchanged:** every ID over strings, integers, booleans, and null, with ASCII keys. That
  covers every `source_id`, inclusion `evidence_id`, and `mention_id`, and string or integer
  structured values. A regression test pins them.
- **Changed:**
  - floats are written in ECMAScript form (`1` not `1.0`, `1e-7` not `1e-07`);
  - timestamps hash as UTC with six fractional digits and `Z`, and naive timestamps are
    rejected;
  - keys sort by UTF-16 code units, which differs from the old order only for keys above
    U+FFFF compared with keys in U+E000–U+FFFF;
  - integers beyond ±(2⁵³−1) and strings with lone surrogates are rejected.
- **Affected:** an answer model's `schema_ref` changes if its JSON Schema contains such floats
  (for example a `ge=1.0` constraint) or non-ASCII keys. Stored claims citing the old
  `schema_ref` no longer match the model.
- **Upgrade:** recompute `schema_ref` for each answer model. Where it changed, record the new
  value in a new variable-set version.

### `Inclusion.mention_id` is removed; `mention_id` hashes fragments

Commit: `2a7fd4e`.

Inclusions are positions only (§2.3), so `Inclusion` no longer has a `mention_id` field.
`amber.ids.mention_id` accepts `fragments` and hashes them only when nonempty (§2.2).

- **Affected:** stored evidence graphs and case results whose inclusion payloads carry
  `mention_id` (always `null` before this change). Such payloads now fail validation with
  `invalid_schema`. Callers constructing inclusions never set the field, because only grounding
  can mint one.
- **Unchanged:** every inclusion `evidence_id`, and every contiguous `mention_id`.
- **Upgrade:** drop the `mention_id` key from each stored inclusion payload. Link spans to
  mentions through the `mention()` tool and, once readings land, through `Claim.reading_id`.

### Graph snapshots and case results carry mentions and readings

Commit: `4b0fd80`.

`EvidenceGraph.to_payload` writes `mentions` and `mention_readings` lists (introduced in
`format_version` 2, now 3; see below), and `from_payload` requires them. `CaseResult.mentions` holds
`Mention` records instead of being forced empty, and `CaseResult` gains a required
`mention_readings` list (§2.9 decisions 3 and 4).

- **Affected:** stored graph snapshots (version 1) and stored case results, which lack the new
  keys and fail with `invalid_schema`.
- **Upgrade:** for a graph snapshot, add empty `mentions` and `mention_readings` lists and
  follow the next entry. For a case result, add an empty `mention_readings` list. Results
  produced before this change never had mentions, so nothing else changes.

### `Task` is a one-variable `VariableSet`; claims name their variable

Commit: `a517856`.

`EvidenceGraph`, `from_payload`, and `load_case_result` accept `task=` or `variables=`; a
`Task` is wrapped by `VariableSet.from_task` (version label `task`) (§3.2). `Claim` gains a
required `variable` (§3.3). Graph snapshots move to `format_version` 3, whose `context` holds
`variable_set`, `variable_set_version`, and `variable_set_ref` instead of `task`,
`schema_ref`, `evidence_policy`, and `scope`. `commit_claim` takes a `Task` or a variable name.

- **Affected:** stored claims and graph snapshots, and any code reading the old snapshot
  context keys. Call sites passing `task=` keep working.
- **Unchanged:** every claim's `task` and `schema_ref`, and every existing validation result
  for single-task graphs.
- **Upgrade:** add `"variable": <task name>` to every stored claim. For a snapshot, set
  `format_version` to 3 and replace the four task keys in `context` with
  `variable_set` (the task name), `variable_set_version` (`"task"`), and `variable_set_ref`
  (`VariableSet.from_task(task).schema_ref`).

### Case results record their variable set and per-variable outcomes

Commit: `c19f896`.

`CaseResult` gains `variable_set`, `variable_set_version`, `variable_set_ref`, and
`variable_outcomes` (one `VariableOutcome` per final variable). `outcome` and
`final_claim_ids` become nullable: they equal the single final variable's outcome when the set
has one, and are null otherwise (§3.5). `create_case_result` takes `outcome` and
`final_claim_ids` as before, or `variable_outcomes`.

- **Affected:** stored case results, which lack the new keys and fail with `invalid_schema`,
  and readers that assume `outcome` is never null.
- **Upgrade:** for a result of a `Task`, add `variable_set` (the task name),
  `variable_set_version` (`"task"`), `variable_set_ref`
  (`VariableSet.from_task(task).schema_ref`), and `variable_outcomes` as
  `{task: {"variable": task, "outcome": <outcome>, "final_claim_ids": <ids>}}`. Use
  `amber.cases.case_status` instead of `result.outcome.status` where several final variables
  are possible.

### Case results carry their run

Commit: `540ec2a`.

`CaseResult` gains a required `run_id` (a ULID), and `create_case_result` takes `run_id`.
Every claim, inference, mention reading, and variable outcome in the result must have
`provenance.run_id` equal to it, so stores can key results by `(case_id, run_id)` (§6.6
decision 4). `Case` gains an optional `dataset`.

- **Affected:** stored case results (missing `run_id`, `invalid_schema`), results whose nodes
  have no or different `provenance.run_id` (`run_mismatch`), and every `create_case_result`
  call site.
- **Upgrade:** mint one ULID per run, stamp it in the provenance of every node the run
  produces, and pass it to `create_case_result`. For stored results, add `run_id` and set
  each node's `provenance.run_id` to it only if they truly came from one run; otherwise
  regenerate them.

### JSON records carry `amber/0.1`, budgets, and UTC timestamps

Commit: `00c8932` (key order: `0aea5b8`.

Every §5.3 JSON record carries `schema_version: "amber/0.1"`: `CaseResult`, `Example`,
`AnnotationEvent`, `LabelRecord`, `Decision`, and the new VariableSet JSON
(`amber.wire.variable_set_json` / `load_variable_set`). Readers accept `amber/0.<n>` with
`n` ≤ 1, normalize it to `amber/0.1`, and reject other majors and newer minors. `CaseResult`
gains `budget` and `usage` (§6.3), and its keys follow the §5.3 order. JSON timestamps are
UTC with `Z` and six fractional digits (§5.2); hashes and Python values are unchanged.

- **Affected:** stored case results (missing `schema_version`, `budget`, `usage`:
  `invalid_schema`); stored Examples, label records, and events written with
  `schema_version: "0.1"`; readers comparing timestamp strings; results whose `usage`
  exceeds their `budget` without every variable `failed` with `failure_kind="budget"`
  (`budget_exceeded`).
- **Also:** results and Examples now list claims, mentions, readings, evidence, and edges in
  key order, so stored JSON written in another order reloads equal but re-serializes in key
  order.
- **Upgrade:** add `"schema_version": "amber/0.1"`, `"budget": null`, and `"usage": null` to
  stored case results; rewrite `"0.1"` as `"amber/0.1"` elsewhere. Parse timestamps instead
  of comparing strings. An over-budget run must record `failed(budget)` itself; loading
  never rewrites an outcome.

### A structured source's `source_id` includes its record

Commit: `62fb532`.

`ids.source_id` hashes the canonical `record` whenever it is set (§2.6, decision 2.9.13), and
`Source.record` is frozen. Note sources (no record) hash exactly as before.

- **Affected:** stored structured sources (`record` set): their `source_id` changes, and so do
  the IDs of inclusions and mentions on them. Before this change, rows with the same patient,
  kind, and external ID collided.
- **Upgrade:** recompute structured sources with `Source.create` and re-key anything that
  referenced their old IDs. Note sources need nothing.

### `example_id` is the hash of the Example's content

Commit: the one closing `amber-sei` (design decision 4.10.10, revising 4.10.5).

`example_id` was `sha256(case_id, variable_set_ref, as_of_event)`, so an Example whose values
were edited after review still loaded wherever it travelled without its events. It is now the
canonical SHA-256 of the Example's content (every field except `example_id` and
`schema_version`, record lists in canonical order), and `load_example` rejects any mismatch
with `example_id_mismatch`. `amber.ids.example_id` now takes the content mapping.

- **Affected:** every stored Example (files, gold bundles, MLflow datasets, stores), and
  anything that recorded its `example_id` (score reports, dataset tags). Code calling
  `ids.example_id(case_id=..., variable_set_ref=..., as_of_event=...)`.
- **Upgrade:** re-key stored Examples with `amber.examples.mint_example(payload, case=...,
  source=..., variables=...)`, or rebuild them from their events with
  `example_from_projection`; rebuild bundles and datasets from the re-keyed Examples. Score
  reports made before the change name old IDs and should be regenerated if they must
  reference current Examples. Call `ids.example_id(example.model_dump(mode="json"))`.

### `Inclusion.model_construct` is refused

Commit: the one closing `amber-73l` ([ADR 0003](../decisions/0003-grounding-mints-spans.md)).

`Inclusion.model_construct` skipped validators and so returned an unverified `Inclusion`. It now
raises `TypeError`, as `Mention`, `Claim`, `CaseResult`, and `Example` already did; so does the
deprecated `Inclusion.construct`, which delegates to it. Records, IDs, and wire bytes are
unchanged, so `schema_version` stays `amber/0.1`.

- **Affected:** Python code that calls `Inclusion.model_construct` or `Inclusion.construct`.
  A search of amber, lapidary, loupe, and copal found none.
- **Upgrade:** mint inclusions with `amber.grounding.exact_quote` (or `cite_mention` for a
  mention's positions) and handle `GroundingFailure`.

### Minted records refuse `model_copy(update=...)`

Commit: the one closing `amber-8gm` ([ADR 0003](../decisions/0003-grounding-mints-spans.md)).

`model_copy(update=...)` and Pydantic's deprecated `copy(update=...)` write fields without
running validators, so a minted `Inclusion` could be copied with other offsets or another quote,
and a `Claim`, `CaseResult`, or `Example` with other values, unchecked. On these five records
both now raise `TypeError`; `model_copy()` and `model_copy(deep=True)` still work. Records, IDs,
and wire bytes are unchanged, so `schema_version` stays `amber/0.1`.

- **Affected:** code that update-copies one of the five records. In amber, the `Example` mint
  path (now re-mints) and the forgeries in the conformance kit and tests (now use `forge`).
  lapidary's tests (at lapidary 77ab9e0, which pins an earlier amber) do it three times, in
  `tests/test_validation.py` (`tampered`) and `tests/test_scorers.py` (`result` with usage);
  loupe and copal do not.
- **Upgrade:** produce a changed record through its minting path (graph validation for claims,
  `amber.cases` for results, `amber.examples.mint_example` for Examples, grounding for spans).
  To build a deliberately tampered record for an adversarial test, use
  `amber.conformance.forgery.forge(record, field=value)`.

## Still to implement

Nothing: every breaking change listed by the contract v0.1 design is implemented above.
