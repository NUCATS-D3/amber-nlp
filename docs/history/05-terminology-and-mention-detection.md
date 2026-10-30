# Terminology and mention detection

Consolidated 2026-10-07 from the [dated design bundle](amber-docs-2026-10-07/README.md)
and the [contextual linking reading guide](context-aware-biomedical-entity-linking.md).
This is a conditional design, not an implemented capability or a replacement for the
[v1 contract](02-v1-schemas-and-tools.md). No terminology or detector schema migration is adopted
by this document. M1–M3 retain their [task-first delivery order](04-roadmap.md).

## Purpose and adoption gate

Use a shared, model-independent terminology asset when a named task needs normalized concepts or
when baseline errors justify targeted mention detection. The current progression answer is a
strict boolean; its first baseline does not require a concept store, exhaustive NER, or a linker.
Before implementing this extension, identify the error or downstream need, freeze its annotation
and evaluation policies, and compare it with the simpler baseline. Include terminology setup and
expert labeling in the effort and cost comparison.

OMOP Standardized Vocabularies are the preferred integration target for a future OMOP-backed
workflow. They carry vocabulary-specific codes such as SNOMED, RxNorm, and LOINC within an OMOP
`concept_id` space. Sharing identifiers can simplify text/structured-data joins. It does not
resolve clinical disagreement, temporality, negation, or whether a patient has a condition.

## Boundaries

- Domain records contain identifiers and immutable terminology references. They do not import
  DuckDB, terminology clients, embedding models, or detector packages.
- Services depend on vocabulary and detector protocols. A local vocabulary-store adapter loads
  the site's authorized files; an EDW adapter remains a separately gated provider.
- Reusable fragment construction and mapping policies belong in Amber when implemented with
  synthetic tests. Dataset-specific annotation reconciliation stays in experiments.
- Encoder inference, contextual reranking, and LLM extraction all require provider checks before
  source-bearing calls. Telemetry and artifact destinations have their own checks. A local model
  label alone does not authorize remote logging or dataset transfer.

Terminology is reference knowledge, not patient evidence. A verified `is_a` relation can validate
a concept constraint; a drug-indication relation cannot establish that the patient has the
indication. Introducing reference-knowledge evidence needs a separate design for source identity,
scope, relation provenance, and permitted inference. Do not add patient-independent sources to
the current single-note graph through a corpus adapter exception.

## Vocabulary identity and mapping

An authorized local vocabulary snapshot may use `CONCEPT`, `CONCEPT_ANCESTOR`,
`CONCEPT_RELATIONSHIP`, `CONCEPT_SYNONYM`, and `VOCABULARY`. Record the snapshot manifest/hash,
per-vocabulary versions, selection policy, and builder version. A release label alone cannot
identify a locally modified download or a different set of included vocabularies.

Use valid standard targets for normalization where the task permits them. Retain the native
`vocabulary_id` and `concept_code`, matched lexical term, and source concept when a declared
mapping was used. An unresolved or ambiguous mapping is an explicit linking result, not a
fabricated standard ID or a score of certainty. Verify validity dates and relationship direction
against the loaded snapshot.

`CONCEPT_ANCESTOR` is useful for supported hierarchy queries and polyhierarchy. Define whether
self-membership is included and test the chosen policy. Verify which vocabularies and attribute
relationships are present before using them; an OMOP download is not automatically a complete
SNOMED distribution or a replacement for ECL, refsets, or post-coordination.

Concept replacement must preserve old assertions. `Maps to` is not a universal, one-to-one
release-upgrade function: mappings can be absent, multiple, or semantically unsuitable. A migration
produces a versioned proposal/event retaining the original concept, release, candidates, and
decision; ambiguous changes require review. Frozen claims and adjudicated gold are never silently
re-pointed. New releases require reproducibility and migration checks.

Check each vocabulary's applicable license and redistribution terms. Ship builder code and
invented fixtures; keep restricted vocabulary contents and derived lexicons/indexes local unless
their terms explicitly permit distribution. A fragment specification containing names or
descriptions also needs a license review before publication. Concept IDs do not carry clinical
source text, but that does not settle the rights to every derived artifact.

## Fragment design requirements

A fragment is a versioned terminology subset and lexicon shared by detectors and linkers. The
October bundle's `FragmentSpec`, `LabelSpec`, and `LexiconEntry` are useful starting ideas, not
ready-to-implement binding models. Resolve these requirements before choosing their public fields:

| Concern | Required policy |
|---|---|
| Scope | Explicit roots, domains, exclusions, concept validity, standard-target and language rules |
| Identity | Canonical spec plus vocabulary snapshot identity and builder/mapping policy version, hashed through `ids.py` |
| Labels | Curated clinical descriptions; multiple labels per concept or a named overlap-resolution policy |
| Lexicon | Preserve aliases, their origins, ambiguous targets, and one-to-many mappings |
| Short terms | Preserve clinically useful short terms under a tested ambiguity policy; a universal three-character cutoff is unsuitable |
| Derived assets | Index/pattern manifests include fragment ID, encoder/tokenizer revision, build parameters, and checksums |
| Changes | New immutable fragment/index versions; migration proposals retain previous references |

`domain_id` describes an OMOP domain; it is not a sufficient detector-label taxonomy or an
automatic rule for creating patient-level CDM rows. Polyhierarchy and overlapping clinical labels
make the bundle's `label_of: dict[int, str]` incomplete without an explicit policy.

Keep model-specific label translation at each detector boundary. Curated hierarchy groups may
re-rank candidates, but a mistaken detector label or subtype must not make the correct concept
unreachable. Evaluate unrestricted retrieval or an explicit fallback alongside type restrictions.

## Mention detection and normalization

Candidate detectors include fragment dictionaries, GLiNER-BioMed, OpenMed, Fastino GLiNER2/2.5,
and grounded LLM extraction. No detector or attribute extractor is an adopted default. medspaCy
ConText is a useful comparator for polarity, temporality, and experiencer; compare it with model
attributes and task rules on the same annotated spans.

The original GLiNER checkpoint `gliner_large-v2.5` and Fastino GLiNER2.5 are different models.
The dated bundle reports Fastino boundary architectures, span attributes, global offset remapping,
and LoRA support. These are upstream capability claims to verify against a pinned package and
checkpoint before adoption; public upstream files could not be retrieved during this review.
Measure tokenization behavior on clinical shorthand rather than treating whitespace splitting as
proof of poor quality. Confirm training supports occurrence-specific offsets; repeated strings
can have different attributes and cannot be trained safely as an undifferentiated entity string.

Every returned Mention must quote the exact immutable Source slice. Validate bounds, Unicode,
CRLF, repeated strings, nested/overlapping entities, chunk boundaries, and discontinuous spans
through named policies. A detector output is not an Inclusion: supporting text still enters through
the existing quote/grounding or explicit human annotation path.

Do not adopt automatic longest-span-wins merging. Overlapping mentions may represent distinct
concepts, and widening a span can include negation or temporal qualifiers. Preserve occurrence
and contributor records, then apply a versioned reconciliation policy. Dictionary-only matches
do not automatically deserve lower confidence; score semantics depend on the method and its
measured errors.

Separate lexical/dense candidate retrieval from contextual disambiguation. SapBERT is a useful
retrieval baseline, not proof that a selected concept is supported. Retain candidate sets, raw
scores, context offsets, model/index versions, and the final resolution or abstention. Distinguish
similarity, vendor confidence, and calibrated correctness. A reranker cannot recover a target
excluded by retrieval.

Ancestor back-off is a task-dependent proposal. A common ancestor of top candidates may be too
broad, have multiple alternatives under polyhierarchy, or be clinically unsupported. Compare
explicit unresolved linking with any back-off rule; report exact and hierarchical accuracy
separately. Hierarchy consistency is a structural constraint, not clinical entailment.

## Annotation and schema evolution

Before adding terminology fields to `Concept`, `Mention`, `Provenance`, `Task`, or `Example`,
specify serialization, identity, validation, and migration behavior in the v1 contract and tests.
The binding generic Concept fields remain unchanged for now. A future `Example.mentions` field
must retain exact spans, attributes, adjudication, and original patient/document membership;
it must preserve `outcome` and `final_claim_ids` as well as the support closure.

A mention experiment needs its own declared annotation coverage and linking gold. CORAL's BRAT
labels are not automatically OMOP-linked gold or evidence of complete note review. Additional
expert annotation is a measured cost. The current progression protocol excludes CORAL's separate
200-note pseudo-label pool; a future silver-data experiment needs a separate approved protocol
and must not change the frozen gold comparison silently.

## Bounded experiment and adoption evidence

Start with the task's observed failure, not an unconditional five-detector benchmark. For a
mention/linking task, compare a lexical baseline with one plausible model and a union only if
recall errors justify it. Use expert spans to isolate linking errors, then evaluate end-to-end
behavior. Keep the provider, task, split, budgets, and relevant policies fixed.

Report:

- Exact and overlap mention precision/recall/F1, with nested and repeated-mention results.
- Attribute correctness on gold spans and on detected spans separately.
- Candidate recall at k, top-1 linking accuracy, unresolved rate, and any ancestor back-off rate.
- Ambiguous aliases, out-of-fragment concepts, detector-type errors, and concepts unseen in training.
- Final clinical correctness, semantic support, contradictions, omissions, outcomes, and automation
  coverage; better NER alone is not an adoption criterion.
- Latency, memory, all model calls, terminology/index setup, annotation, correction, and total
  expert effort at matched quality.

Split patients/documents before deriving spans, chunks, or silver exports. Vocabulary aliases and
synonyms for a withheld concept must stay out of silver generation if the claim concerns unseen
concept generalization; document any mapping through ancestors or related labels. Vocabulary
availability at inference is a separate condition to disclose. Resample patients/documents, not
mentions. Ten CORAL development notes cannot justify precise per-field calibration curves or a
broad detector superiority claim.

## Relationship to select–decide

[Select–decide](select-decide.md) is a separate execution hypothesis: select evidence, then render
a decision request from resolved evidence IDs. Terminology assets may help retrieve candidates,
but neither proposal is a prerequisite for the other. Preserve opposing evidence, temporal
qualifiers, and review coverage. A verifier limited to selected text cannot certify that omitted
text contains no contradiction. See the [evaluation](select-decide-evaluation.md) for the proposed
bounded full-note versus evidence-restricted comparison and its unresolved contract issues.

## Primary references and verification limits

- [OMOP CDM v5.4](https://ohdsi.github.io/CommonDataModel/cdm54.html) and
  [Athena](https://athena.ohdsi.org) — vocabulary/export references; validate the site's snapshot.
- [Fastino GLiNER2](https://github.com/fastino-ai/GLiNER2) — verify APIs and training format against
  pinned versions before implementing a backend.
- [SapBERT](https://aclanthology.org/2021.naacl-main.334/) and the
  [contextual linking guide](context-aware-biomedical-entity-linking.md) — retrieval and
  disambiguation references.
- [Clinical IE survey](01-state-of-the-art.md) — research leads, with supervision and comparator
  qualifications. This consolidation did not independently reverify primary-study results.
