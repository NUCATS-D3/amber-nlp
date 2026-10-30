# amber plan

Status: draft for review, 2026-10-30. Scope and non-goals are in the [charter](charter.md); what
exists today is in [contracts](contracts.md). This plan sets the order of work and the exit
criteria for each phase. Individual tasks live in Beads.

## Where we are

**Implemented:** the note-scoped kernel. This covers the source, task, claim, and evidence models,
canonical IDs, exact grounding, full evidence-graph validation, claim commits, explicit outcomes,
and `CaseResult`, with 430 synthetic tests. One MLflow run-context tag also exists.

**Missing:** everything a consumer needs to plug in. That means the policy gate, mentions,
annotated data (`Example`), wire formats, interfaces, the conformance kit, and most MLflow
plugins.

**First consumers:**

- lapidary L1: tables, policy, validation, scorers, Extractor, Labeler;
- the loupe bridge: review export and import;
- later, the CORAL rehearsal and the institutional corpus.

## Approach

Design first, as one coherent contract; then implement in dependency order. The open design
questions interact: layered variables shape `Example`, the store tables, and review payloads, and
mentions shape evidence and copal mapping. Deciding them one issue at a time would produce
contracts that need breaking changes later.

## Phase D: contract v0.1 design

The deliverable is one reviewed document, `docs/design/contract-v0.1.md`, that answers the
questions below. Each answer comes with invented examples and a list of the conformance cases
that will test it.

1. **Layered variables** (`amber-bpr.11`):
   - a declared variable dependency graph;
   - multi-task evidence graphs, where each claim is validated against its own task;
   - typed derivation provenance that can be replayed for deterministic rules;
   - per-variable outcomes and final claims.

   Keep note scope, and do not preclude patient-level variables.
2. **Mentions and structured evidence** (`amber-bpr.8`):
   - `Mention` fields and attributes;
   - how a mention becomes cited evidence;
   - `StructuredEvidence` against a declared structured-source mapping;
   - how copal annotations map into mentions.
3. **Annotated data** (`amber-bpr.2`):
   - `Example`, with reference tiers (`adjudicated`, `single_review`, `silver`) and split;
   - annotation events and human status transitions;
   - label-only evaluation records;
   - how review revisions map to events.
4. **Policy** (`amber-bpr.1`, design part):
   - the `Provider` and destination models;
   - the default matrix;
   - license and IRB restrictions as declared data;
   - what is stamped into `Provenance`;
   - the failure mode when a check fails.
5. **Wire formats** (`amber-bpr.5`, design part):
   - versioned JSON and Parquet table schemas;
   - the manifest;
   - the source resolver used for load-time validation when tables hold no text;
   - schema-version migration rules.
6. **Interfaces**:
   - signatures and capability declarations for `Extractor`, `Provider`, `PolicyGate`, `Store`,
     `Exporter`, `Scorer`, `Labeler`, and `ReviewBackend`;
   - what each one must validate and what it must not do.
7. **Conformance catalog**: the adversarial cases every implementation must pass, grouped by
   contract area.

Inputs: the pre-reset contract ([history/02](history/02-v1-schemas-and-tools.md)), the
terminology design ([history/05](history/05-terminology-and-mention-detection.md)), the loupe
review requirements ([history/06](history/06-annotation-app.md)), copal's consumer contract,
lapidary's L1 plan, and the CORAL protocol's reference-tier rules.

**Exit:** you accept the design document; implementation issues are filed against its sections;
and lapidary and loupe confirm that their planned use fits it.

## Phase 1: contract v0.1 implementation

Implement in this order, with adversarial tests for each step:

1. policy gate and provenance stamping;
2. `Mention` and `StructuredEvidence`;
3. layered-variable graphs, keeping single-task graphs as the simple case;
4. annotation events, status transitions, and `Example` with tiers;
5. wire formats, the source resolver, and load-time revalidation.

**Exit:** every contract area in the design has implementation plus tests, and the core-only
install check still passes.

## Phase 2: interfaces, reference implementation, conformance kit

- Protocols as designed, plus one synthetic reference implementation of each, in an optional
  extra.
- The conformance kit as a pytest plugin that runs the catalog against any implementation's
  outputs.
- `Store` and `Exporter` reference implementations: in memory and Parquet.

**Exit:** the reference implementation passes the kit, and deliberately broken implementations
fail it with source-free diagnostics.

## Phase 3: MLflow plugins

- Evaluation: structural-validity scorers and a tier-aware metric harness.
- `Example` ↔ MLflow Dataset conversion.
- Run and table logging, with destination checks.
- The loupe bridge: the `AnnotationAdapter` and `ReviewBackend` implementation.
- A pyfunc deployment wrapper (lower priority).

**Exit:** each plugin is exercised by a real consumer (lapidary or loupe) against a local MLflow
server using synthetic data.

## Release 0.1.0

Release when lapidary L1 and the loupe bridge run on amber's contracts and pass the conformance
kit. After that, contract changes follow semver with migration notes.

## Deferred

These wait for a consumer and a design:

- patient-level aggregation;
- fuzzy-alignment policy (`amber-ndt.1`);
- OMOP `NOTE_NLP` export;
- terminology and vocabulary references.
