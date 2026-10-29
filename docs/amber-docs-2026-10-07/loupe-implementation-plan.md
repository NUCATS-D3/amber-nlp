> **Historical, superseded.** This 2026-10-07 plan follows the October bundle's roadmap and is not
> a current plan. Human review now uses `mlflow-span-review`; see [the human-review design](../06-annotation-app.md).
> The separate `loupe` workbench repository is a different project. Work is tracked in Beads.

# loupe: implementation plan for amber's annotation app

Status: plan, 2026-10-07. Working name **loupe** (the jeweler's lens used to inspect inclusions). Companion to `amber-goals-and-architecture.md` ("Annotation app", Decision 5) and `amber-v1-schemas-and-tools.md` (§3–§10). Replaces the plan to use INCEpTION for the M2 gold set.

Estimates assume focused work by one developer with a coding agent. Total: roughly 8–11 weeks to full v1, with a usable span annotator after about 2 weeks.

---

## 1. Goals

| # | Goal | How we'll know |
|---|---|---|
| G1 | **Cheap gold.** Annotators produce mention gold (spans, ConText attributes, linked OMOP concepts) faster than with general-purpose tools. | Median time per note, measured from the event log; baseline set in the P1 pilot, then tracked per release. |
| G2 | **Correct, don't author.** Model output (detectors, extractor `CaseResult`s, LLM pre-annotation) loads as suggestions to accept, edit, or reject. | Suggestion acceptance rate; share of gold spans that began as suggestions. |
| G3 | **Evidence-first.** Claims carry evidence edges; text evidence is minted only with `quote` semantics; the evidence DAG is visible and editable. | Every exported gold claim passes amber's `commit_claim` validation. |
| G4 | **One data model.** The app imports amber's pydantic models; nothing is translated between app and pipeline. | Round-trip test: pipeline output → app → `Example` → pipeline evaluation, byte-identical where expected. |
| G5 | **Reproducible gold.** Every change is an append-only event; gold is derived by replay; inter-annotator agreement is computed from the same log. | Replay determinism test; IAA report per round. |
| G6 | **Portable and safe.** SQLite on a laptop, Postgres when shared; works offline; zone/sensitivity policy gate on every model call; no PHI in logs or URLs. | Policy test matrix; app runs with no network access. |
| G7 | **Beautiful and keyboard-first.** Clean, modern UI with full keyboard operation and dark mode. | Pilot annotators complete a note without the mouse; SUS questionnaire after the pilot. |
| G8 | **MLflow via the OSS API only.** Optional integration: trace import, human assessments written back, evaluation dataset export. Works the same on OSS MLflow and Databricks. | Integration tests against a local `mlflow server`; app fully functional with MLflow disabled. |

### Non-goals for v1

- PDF, image, or OCR layout annotation; plain text only.
- Crowdsourcing features (payments, qualification tests).
- A home-grown auth system; local mode has none, shared mode uses OIDC.
- Real-time co-editing of the same case by two people.
- A general-purpose graph editor; the DAG view is a viewer with "add evidence" and "remove edge".

---

## 2. Invariants and principles

1. **Offsets are Python code points.** The API carries code-point offsets everywhere. The frontend converts to and from UTF-16 in one module (`lib/offsets.ts`) and nowhere else. `quote == source.text[start:end]` is checked on every write.
2. **Source text is immutable** once a `source_id` is minted. No whitespace normalization after import.
3. **Events are the record; tables are projections.** Every write appends to `annotation_events`, and current-state tables are rebuilt from it. Projections can be dropped and replayed.
4. **Schema-driven UI.** Span labels and descriptions come from the project's `FragmentSpec`/`LabelSpec`; mention attributes from amber's enums; claim forms from each task's `AnswerModel` JSON schema. Adding a task doesn't require frontend code.
5. **Annotations are per annotator until adjudicated.** Independent rounds never show one annotator's work to another.
6. **No PHI outside its zone.** Note text never appears in server logs, URLs, analytics, or error reports. LLM suggestions go through amber's `Provider` + `Policy`.

---

## 3. Architecture

```
┌──────────────────────── browser ────────────────────────┐
│ React + TS (Vite) · shadcn/ui · Tailwind · TanStack Query │
│ source view · claim panel · evidence DAG · queue · admin  │
└──────────────┬──────────────────────────────────────────┘
               │ JSON over HTTP (types generated from OpenAPI)
┌──────────────▼──────────────────────────────────────────┐
│ FastAPI (python package `loupe`)                         │
│ routers → services (events, replay, quote, concepts,     │
│           suggestions, iaa, export, mlflow_sync)         │
│ imports amber: schemas, quote(), Fragment, Provider/Policy│
└───────┬──────────────────┬───────────────────┬──────────┘
        │ SQLAlchemy 2      │ fragment lexicon   │ optional
   SQLite | Postgres    FTS5 | pg_trgm      MLflow client
                         (+ SapBERT index)    model providers
```

### Stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI, pydantic v2 | Shares amber's models directly; OpenAPI for free. |
| DB | SQLAlchemy 2.0 + Alembic | One `DATABASE_URL` switches SQLite ↔ Postgres; migrations tested on both. |
| Concept search | SQLite FTS5 / Postgres `pg_trgm`; optional SapBERT re-rank | Fast typeahead over the fragment lexicon without extra services. |
| Frontend | React 19 + TypeScript + Vite | Largest ecosystem for complex editors. |
| UI kit | Tailwind + shadcn/ui (Radix primitives), lucide icons | Clean modern look, accessible primitives, components owned in-repo. |
| State | TanStack Query (server), Zustand (editor) | Clear split between cached server data and local editing state. |
| Types | `openapi-typescript` (or `orval`) from FastAPI's schema | A pydantic change becomes a TypeScript compile error. |
| Span layer | Decide in P0 spike: `@recogito/text-annotator` (BSD-3) vs. a custom renderer | See §11. |
| DAG view | React Flow, layered layout via elkjs | Mature, themeable, handles a few hundred nodes. |
| Auth | None (local mode); OIDC via Authlib (shared mode) | Northwestern SSO without building auth. |
| Tooling | uv, ruff, pytest, hypothesis; pnpm, vitest, Playwright | Matches the amber workspace. |

### Repository layout

```
loupe/
  backend/loupe/
    api/            projects.py sources.py cases.py mentions.py claims.py evidence.py
                    suggestions.py concepts.py exports.py events.py admin.py
    db/             tables.py  projections.py  migrations/ (alembic)
    services/       events.py replay.py quote.py concepts.py suggest.py iaa.py
                    importers/ (jsonl, csv, parquet, brat, spanmark, caseresult)
                    exporters/ (example, trl, mlx, note_nlp, brat)
                    mlflow_sync.py
    settings.py     DATABASE_URL, MODE=local|shared, MLFLOW_TRACKING_URI, providers, policy
  frontend/src/
    routes/         queue, annotate/$caseId, adjudicate/$caseId, projects, dashboard
    features/       source-view, mention-panel, claim-panel, evidence-dag,
                    concept-picker, suggestions, project-config
    lib/            api/ (generated), offsets.ts, keymap.ts, colors.ts
  e2e/              Playwright specs
  docker/           compose for Postgres deployment
```

---

## 4. Data model

### Reused from amber, unchanged

`Source`, `Section`, `Mention`, `Concept`, `Claim`, `Inclusion`, `StructuredEvidence`, `InferenceEvidence`, `EvidenceEdge`, `Example`, `Fragment`, `Provenance` (human producer = `human:<user_id>`).

### App tables

| table | key | columns |
|---|---|---|
| `projects` | project_id | name, tasks (JSON), fragment_id, label_config (JSON), attribute_config (JSON), guidelines_md, sensitivity, created_at |
| `users` | user_id | display_name, email, role (annotator / adjudicator / admin) |
| `sources` | source_id | as amber §10, plus full `text` (the app needs it) |
| `cases` | case_id | as amber §10, plus project_id, priority, queue_reason |
| `assignments` | (case_id, user_id, round) | mode (independent / collaborative / adjudication), status, assigned_at, submitted_at |
| `annotation_events` | event_id (ULID) | project_id, case_id, user_id, round, seq, type, target_id, before (JSON), after (JSON), at |
| `suggestions` | suggestion_id | case_id, kind (mention / claim / concept), payload (JSON), producer, run_id, trace_id, score, status (pending / accepted / edited / rejected) |
| projections | — | `mentions_current`, `claims_current`, `evidence_current`, `edges_current`, keyed additionally by (user_id, round) |

### Event types

`mention.create | update | delete`, `suggestion.accept | edit | reject`, `concept.set | clear`, `claim.create | update | set_status`, `evidence.mint_inclusion | attach_structured | add_edge | remove_edge`, `case.submit | flag | skip | reopen`, `adjudication.resolve`, `vocab.upgrade` (actor `tool:vocab_upgrade`).

### Gold derivation (replay)

- Per case, take the latest `adjudication.resolve` result if one exists.
- Otherwise, if exactly one annotator submitted in the final round, use their state.
- Otherwise, the case is unresolved and not exported as gold.
- Gold exports record the event range they were derived from (`Example.derived_from_events`).

---

## 5. API sketch

```
GET  /projects                         POST /projects            PATCH /projects/{id}
POST /projects/{id}/import             (JSONL | CSV | Parquet | brat | spanmark | CaseResult)
GET  /projects/{id}/queue?policy=…     next case for the current user
GET  /cases/{id}                       source text, sections, my current mentions/claims/evidence, suggestions
POST /cases/{id}/events                batch of events (client sends intents; server validates, assigns seq)
POST /cases/{id}/quote                 {text | start,end} → Inclusion | GroundingFailure
POST /cases/{id}/suggest               run detectors / LLM (policy-gated) → suggestions
GET  /concepts/search?q=&label=&k=     fragment lexicon typeahead (+ SapBERT top-k for a span)
GET  /concepts/{concept_id}            name, vocabulary, code, synonyms, ancestors (breadcrumb)
GET  /cases/{id}/diff?round=           annotator states side by side (adjudicators only)
GET  /projects/{id}/iaa?round=         span exact/overlap F1, attribute kappa, concept agreement
GET  /projects/{id}/export?format=     example | trl | mlx | note_nlp | brat
POST /projects/{id}/mlflow/sync        assessments + evaluation dataset (if configured)
```

The server is the only writer of offsets and ids; the client sends intents (for example, "create a mention over code points 120–134 with label `problem`"), and the server validates them against the source text and the project's label config.

---

## 6. Frontend

### Screens

1. **Queue.** Your next case plus filters: flagged, disagreement, low-confidence suggestions. Progress per project.
2. **Annotate** (the core screen).
   - *Source view:* note text with section shading, template/copy-forward spans dimmed, mentions colored by label, overlapping spans as stacked underlines, suggestions as dashed outlines. Virtualized for long notes.
   - *Side panel tabs:* **Mentions** (list, attributes, concept), **Claim** (form generated from the `AnswerModel` schema, evidence list per field), **Evidence** (DAG).
   - *Concept picker:* typeahead over the fragment lexicon showing concept name, vocabulary + code, matched term, and an ancestor breadcrumb. SapBERT top-k for the selected span appear first.
3. **Adjudicate.** Annotators' versions side by side with differences highlighted; choose a version, merge, or edit; resolve.
4. **Projects (admin).** Import, label and attribute config from a `FragmentSpec`, guidelines editor (Markdown), assignments, export.
5. **Dashboard.** Throughput, time per note, suggestion acceptance, IAA per round.

### Keyboard map (initial)

| Key | Action |
|---|---|
| `1`–`9` | Label the selection, or relabel the selected span |
| `Tab` / `Shift+Tab` | Next / previous span |
| `[` / `]` | Shrink / extend the selected span by one token at the end |
| `n` `h` `u` | Toggle negated / historical / uncertain |
| `c` | Open the concept picker |
| `a` / `x` | Accept / reject the selected suggestion; `Shift+A` accepts all visible |
| `q` | Quote the selection as evidence for the active claim field |
| `Del` | Delete span |
| `Ctrl/Cmd+Z`, `Shift+Ctrl/Cmd+Z` | Undo / redo (inverse events) |
| `Ctrl/Cmd+Enter` | Submit case and go to next |
| `f` | Flag for review |
| `?` | Show shortcuts |

### Visual design

- shadcn/ui components with Tailwind design tokens; light and dark themes from the first commit.
- Label palette chosen for color-blind safety, with a non-color cue (underline style) for polarity.
- Readable note typography with an optional monospace mode for notes whose layout depends on alignment.
- Selection snaps to token boundaries by default (toggle per project), with character-level adjustment by keyboard.
- Interaction budget: under 50 ms to create a span on a 20,000-character note with 500 existing spans.

---

## 7. Integrations

- **amber pipeline.** Import `find_mentions` output and `CaseResult`s as suggestions, keeping detector name, run id, and trace id. Export accepted cases as `Example`s; amber's exporters produce TRL/mlx JSONL and detector training data from them.
- **Sections and templates.** Call amber's `sections()` and `dedupe()` at import and store the results on `Source.sections` for shading.
- **LLM pre-annotation.** Through amber's `Provider` + `Policy`. The model returns exact substrings; the server grounds them with `quote()`, drops ungroundable spans, and records the grounding-failure count on the suggestion batch.
- **MLflow (optional).** On case submit, write a HUMAN assessment (feedback for verdicts, expectation for the corrected value) to the source trace. On export, create or update an `mlflow.genai` evaluation dataset. Run-context tags for project, fragment_id, and vocabulary release. Disabled when no tracking URI is set.
- **Interchange.** Import and export brat standoff (for existing clinical corpora) and spanmark JSONL. BioC and INCEpTION JSON CAS are later, on demand.

---

## 8. Phases

### P0. Foundations (3–4 days)

- Repo, uv + pnpm workspaces, CI (pytest on SQLite and Postgres via testcontainers, vitest, Playwright).
- FastAPI skeleton, settings, SQLAlchemy tables, Alembic baseline.
- OpenAPI → TypeScript generation wired into CI.
- `offsets` modules (Python and TS) with hypothesis/property tests over non-BMP characters, CRLF, and combining marks.
- **Spike (1 day):** Recogito text-annotator vs. a custom renderer on a long synthetic note with overlapping spans. Record the decision.

Exit: a source containing emoji and CRLF round-trips with correct offsets on both databases; CI green.

### P1. Span annotation MVP (1–1.5 weeks)

- Import notes (JSONL / CSV / Parquet) with a `sensitivity` manifest.
- Project config: labels from a `FragmentSpec` or a plain list; ConText attributes.
- Source view: create, resize, relabel, and delete spans; overlapping spans; keyboard map; undo/redo.
- Event log, projections, replay command; `Example` JSONL export with mentions.
- Local mode, no auth.

Exit: annotate 10 synthetic notes end to end; replay reproduces the projections exactly; time per note is logged. **From here, loupe replaces INCEpTION for M2.**

### P2. Suggestions and concepts (1.5–2 weeks)

- Suggestion import (detector output, `CaseResult`), accept / edit / reject, accept-all.
- Concept picker over the fragment lexicon; SapBERT top-k for a span; ancestor breadcrumb.
- LLM pre-annotation through `Provider` + `Policy`, grounded by `quote()`.
- Section shading and template dimming.

Exit: the M2 gold set (50–100 notes) is annotated with suggestions; acceptance rate and time per note reported.

### P3. Multiple annotators and adjudication (1–1.5 weeks)

- Users, roles, OIDC in shared mode; Postgres deployment via docker compose.
- Assignments and independent rounds.
- IAA: span exact and overlap F1, attribute Cohen's kappa, concept agreement (exact and ancestor-tolerant via CONCEPT_ANCESTOR).
- Adjudication diff view; gold by replay.

Exit: pilot with 2 annotators × 20 notes produces an IAA report and adjudicated gold.

### P4. Claims and evidence (2–3 weeks)

- Claim form generated from the `AnswerModel` JSON schema (enums, units, `ConceptField` via the concept picker).
- Evidence: quote action (mint an Inclusion), attach a mention, attach a structured row; per-field evidence when `evidence_policy == "field"`.
- Evidence DAG viewer (React Flow) with "add evidence" and "remove edge".
- Server-side validation reusing amber's `commit_claim` rules (evidence required, is-a checks, acyclicity).
- Queue policies: uncertainty, model disagreement, verifier disagreement.

Exit: claims for a synthetic task annotated with evidence; the exported `Example`s run through amber's §11 scorers unchanged.

### P5. MLflow and hardening (about 1 week)

- Trace import by id; assessment write-back; evaluation dataset export.
- Audit view of events per case; accessibility pass (keyboard, contrast, screen-reader labels).
- Performance budget enforced in Playwright; deployment documentation.

Exit: an end-to-end loop — amber run → loupe review → MLflow assessments and dataset → amber evaluation — demonstrated on synthetic data.

### Mapping to the amber roadmap

| loupe | amber |
|---|---|
| P1–P2 | M2 gold set and detector bake-off |
| P3 | M2 pilot IAA; shared annotation |
| P4 | M6 (annotation app) |
| P5 | Hooks for M7 (annotation ladder) and M8 (verifier queue) |

---

## 9. Testing

- **Property tests:** offset conversion, `quote` alignment, replay determinism (random event sequences → same projections).
- **Invariant tests:** `quote == text[start:end]`; acyclic evidence edges; concept fields in-fragment and standard.
- **API tests** on SQLite and Postgres.
- **E2E (Playwright):** annotate, suggest/accept, adjudicate, and claim flows, keyboard-only.
- **Performance:** the 20,000-character / 500-span fixture within budget.
- **Fixtures:** synthetic notes and the tiny synthetic vocabulary from amber M1; no licensed SNOMED content in CI.

---

## 10. Success metrics

- Median time per note (pilot baseline, then trend).
- Suggestion acceptance rate, and share of gold that began as suggestions.
- IAA per round (span overlap F1, attribute kappa, concept agreement).
- Zero offset mismatches in exported gold (checked at export).
- Grounding-failure rate of LLM pre-annotation.
- Annotator SUS score after the pilot.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Span rendering (overlaps, cross-line selections, long notes) eats time | P0 spike; virtualize from P1; keep the renderer behind one component interface so it can be swapped. |
| UTF-16 vs. code-point offset bugs | Single conversion module on each side; property tests; export-time check. |
| Schema-generated forms get awkward for complex `AnswerModel`s | Allow per-field UI hints in `json_schema_extra`; fall back to a JSON editor for unusual fields. |
| Scope creep toward a general annotation platform | Non-goals above; every feature must serve the amber gold or review loop. |
| PHI leakage via logs or errors | Structured logging that never includes text; error reports scrubbed; review before shared deployment. |
| Deployment inside Northwestern stalls on infrastructure decisions | Local mode (SQLite, no auth) is fully useful on its own; shared mode is additive. |

---

## 12. Open decisions

1. Name: loupe or something else.
2. Span renderer: Recogito text-annotator vs. custom (decided in P0 spike).
3. Shared-mode hosting: Azure vs. on-prem vs. Databricks Apps; SSO mechanism.
4. Whether the app owns its Postgres or shares an amber database.
5. License: Apache 2.0 to match amber (recommended).
6. Whether claim annotation (P4) starts before the M2 gold set is complete or after.
