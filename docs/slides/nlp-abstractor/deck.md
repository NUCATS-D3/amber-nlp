---
marp: true
theme: nucats
size: 16:9
paginate: true
header: 'NEW DIRECTIONS UNDER D3'
footer: 'Northwestern University Clinical and Translational Sciences Institute'
title: 'Clinical NLP for registries and computational phenotyping'
description: 'Deploying the existing abstractor in the Databricks EDW, evaluating with MLflow, and preparing a comparison with Amber.'
---

<!-- _class: cover -->
<!-- _paginate: false -->

<div class="cover-logo" role="img" aria-label="Northwestern University NUCATS"></div>

# Clinical NLP for registries<br>and computational phenotyping

<p>Deploy the existing abstractor. Evaluate with MLflow.<br>Build the evidence for Amber.</p>

<div class="cover-date">NUCATS · Informatics & Data Science · September 2026</div>

<!--
This follows the September 2, 2026 NUCATS D3 update. The focus is one practical
starting point for the registry and computational phenotyping direction:
clinical note abstraction. We are deploying the existing tool into the
Databricks EDW environment and using MLflow to evaluate results. Amber is a
proposed next generation; this presentation does not claim comparative gains.
-->

---

# From the D3 vision to a concrete first use case

<div class="columns two">
<div class="panel">
<div class="eyebrow">D3 UPDATE · SLIDE 17</div>
<h2>Registry & computational<br>phenotyping tools</h2>
<p>Use NLP and AI to reduce the time required to build phenotypes for registries and pragmatic trials.</p>
</div>
<div class="panel">
<div class="eyebrow">D3 UPDATE · SLIDE 18</div>
<h2>MLflow for<br>model governance</h2>
<p>Use the Databricks environment for reproducible model versioning, evaluation, and monitoring.</p>
</div>
</div>

<div class="takeaway">Our starting point: extract usable oncology variables from clinical notes,<br>then evaluate them against expert review.</div>

<p class="small">Note abstraction supplies inputs to phenotyping; patient-level phenotype logic remains a separate step.</p>

<!--
Keep this bridge brief. Slides 17 and 18 of the source deck connect the two
themes directly. The broader D3 vision includes structured data, temporal
logic, and phenotype definitions. This project starts with extracting variables
from notes and does not yet implement that entire vision.
Source: docs/slides/NUCATS-D3 update (Sept 26).pdf, PDF pages 17–18.
-->

---

# An existing oncology abstractor is the foundation

<p class="lead">A Python implementation of the Rails <strong>omop-abstractor</strong> workflow.</p>

<div class="columns three">
<div class="panel">
<div class="eyebrow">CLINICAL CONTENT</div>
<h2>Structured fields</h2>
<p>Histology, site, laterality, procedure, grade, stage, and receptor status.</p>
</div>
<div class="panel">
<div class="eyebrow">ONCOLOGY CONTEXT</div>
<h2>Configured tasks</h2>
<p>Note selection, local and outside reports, specimen groups, and cohort vocabularies.</p>
</div>
<div class="panel">
<div class="eyebrow">RESEARCH OUTPUTS</div>
<h2>Reviewable tables</h2>
<p>Report rows with candidate values, source matches, and decision reasons available for audit.</p>
</div>
</div>

<p class="small">Suites include breast, lung, colorectal, AML, lymphoma, and other oncology cohorts.</p>

<div class="takeaway">Preserve useful clinical rules and reporting conventions while moving execution into Python.</div>

<!--
The existing package has real extraction, reporting, LLM, and review code.
It runs in process without a Rails application, database, or separate NLP
service. Compatibility tests compare fields and report rows with saved Rails
outputs. These tests establish software behavior, not clinical accuracy.
Sources: note-abstractor-main/README.md; docs/architecture.md;
docs/rails-compatibility.md; schemas/; note_abstractor/datamart/reports/.
-->

---

# Collect the evidence. Then choose the value.

<div class="flow four">
<div class="step"><span>01</span><h2>Select notes</h2><p>Apply cohort rules.<br>Join configured<br>sibling notes.</p></div>
<div class="step"><span>02</span><h2>Find matches</h2><p>Detect sections,<br>field names, values,<br>and negation.</p></div>
<div class="step"><span>03</span><h2>Keep evidence</h2><p>Group candidates.<br>Retain each source<br>occurrence.</p></div>
<div class="step"><span>04</span><h2>Decide & report</h2><p>Apply explicit rules.<br>Record decisions.<br>Build table rows.</p></div>
</div>

<div class="columns two compact">
<div><h3>Evidence stays inspectable</h3><p>A match records its text, section, location, and negation status—even when it loses.</p></div>
<div><h3>Uncertainty stays visible</h3><p>Fields can be valued, ambiguous, unknown, not applicable, or abstained after an LLM call.</p></div>
</div>

<div class="takeaway">A final value is accompanied by the candidates considered and the reason for the decision.</div>

<!--
The core separates evidence collection from the later decision phase. Frozen
SourceFact objects describe occurrences; Suggestion objects group canonical
values. The spaCy-based pipeline uses configured matching and NegEx-style
negation, with the standard NER component disabled. Some defaults can win
without text evidence. A compatibility mode for breast receptors deliberately
preserves Rails' order-sensitive outcomes, so do not claim all fields are
order-independent. ABSTAINED is an optional LLM outcome, not a default-rule state.
Sources: note_abstractor/pipeline.py; nlp/abstract.py; models.py; postprocess.py;
results.py; docs/rails-compatibility.md.
-->

---

# Use LLMs where interpretation adds value

<div class="columns two example">
<div>
<div class="eyebrow">SYNTHETIC ILLUSTRATION</div>
<blockquote>DCIS: nuclear grade 3.<br><br>Invasive carcinoma:<br>Nottingham grade 2.</blockquote>
<p class="small">Finding both grades is only the first step.<br>Each grade must belong to the correct field.</p>
</div>
<div class="panel">
<h2>Optional resolution after NLP</h2>
<ul>
<li>Resolve competing candidate values.</li>
<li>Check values shared across related fields.</li>
<li>Extract related fields together, such as ER, PR, and HER2.</li>
<li>Validate answers and retain the original evidence.</li>
</ul>
</div>
</div>

<div class="takeaway">Compare rules alone with rules + LLM resolution on the same cases.</div>

<!--
The text on this slide is invented and is not an actual run or performance
result. The example illustrates field attribution: DCIS nuclear grade and
invasive Nottingham grade can be confused even when both values are found.
The implemented workflows include field-level ambiguity resolution, checks
across related fields, multi-field extraction, and note-level resolution.
Model responses are constrained by the field vocabulary or numeric rules.
Sources: docs/llm-resolution.md; docs/llm-workflow.md;
note_abstractor/llm/resolver.py; multi_field.py; adjudication.py.
-->

---

# Deploying the abstractor into the Databricks EDW

<p class="lead">We are deploying the existing NLP abstractor tool into the<br><strong>Databricks Enterprise Data Warehouse environment.</strong></p>

<div class="boundary">
<div class="eyebrow">DATABRICKS EDW · DEPLOYMENT WORKFLOW</div>
<div class="flow three">
<div class="step"><h2>EDW notes</h2><p>Selected cohort<br>and source metadata</p></div>
<div class="step"><h2>Python abstractor</h2><p>Existing schemas<br>and optional LLM resolution</p></div>
<div class="step"><h2>Research tables</h2><p>Structured variables<br>with review and audit views</p></div>
</div>
</div>

<div class="columns two compact">
<div><h3>Build on the existing environment</h3><p>Databricks notebooks connect source queries, abstraction, and report generation.</p></div>
<div><h3>Evaluate the deployed workflow</h3><p>MLflow links runs and LLM traces to expert-reviewed evaluation records.</p></div>
</div>

<p class="small">Clinical text, model requests, and review artifacts stay in institutionally approved destinations.</p>

<!--
Deployment is in progress, as specified for this presentation; do not describe
it as completed enterprise rollout. The repository has Databricks notebooks
and an existing source-row → abstractor → datamart path. The diagram is a
workflow, not a claim of distributed Spark execution or native model hosting.
Optional model calls use an approved configured endpoint. Review artifacts and
traces can contain note-derived data and follow the same destination rules.
Sources: user-provided deployment status; note-abstractor-main/docs/databricks-run.md;
notebooks/breast_databricks_run.py; notebooks/breast_llm_resolver.py.
-->

---

<!-- _class: mlflow -->

# Using MLflow to evaluate results

<p class="lead">We are using <strong>MLflow in Databricks</strong> to connect extraction results,<br>expert expectations, and reproducible comparisons.</p>

<div class="flow three">
<div class="step"><span>RUN</span><h2>Record what ran</h2><p>Configuration, outputs,<br>LLM requests, responses,<br>and token use</p></div>
<div class="step"><span>REVIEW</span><h2>Establish expectations</h2><p>Experts review delivery<br>records and record the<br>expected field values</p></div>
<div class="step"><span>COMPARE</span><h2>Score the outputs</h2><p>Join results to reviewed<br>records and compare<br>workflow variants</p></div>
</div>

<div class="columns two compact">
<div><h3>Current comparison</h3><p>Deterministic abstraction versus abstraction with selected LLM resolution.</p></div>
<div><h3>Foundation for Amber</h3><p>Reuse the evaluation approach and compatible task definitions for the next generation.</p></div>
</div>

<div class="takeaway">Successful execution is only the start; expert-reviewed outputs determine quality.</div>

<!--
The existing workflow creates stable evaluation records, collects reviewer
expectations through labeling sessions or offline review, and calls
mlflow.genai.evaluate. MLflow traces capture the LLM decisions. This deck
reports no numeric evaluation outcome. Do not equate agreement with Rails
with agreement with clinical ground truth. Cache replay is useful for
development but is not an independent sample of model behavior.
Sources: docs/llm-workflow.md; docs/databricks-run.md;
note_abstractor/llm/dataset.py; tracing.py; workflows.py.
-->

---

# Let the baseline identify the next investment

<div class="columns three">
<div class="panel">
<div class="eyebrow">ADAPTATION</div>
<h2>A new task</h2>
<p>How much expert work goes into field definitions, vocabularies, rules, and prompts?</p>
</div>
<div class="panel">
<div class="eyebrow">TRUST</div>
<h2>A defensible answer</h2>
<p>Can a reviewer trace a value to text that actually supports the intended clinical meaning?</p>
</div>
<div class="panel">
<div class="eyebrow">EFFORT</div>
<h2>A useful workflow</h2>
<p>Does assisted review reduce total expert time at an acceptable quality level?</p>
</div>
</div>

<div class="takeaway">These questions shape Amber’s design and evaluation.</div>

<p class="small">Design goals are hypotheses. Improvements require a measured comparison.</p>

<!--
The existing abstractor provides a useful baseline and clinical domain
knowledge. Amber's justification is a measurable gain in quality, total expert
effort, adaptability, or cost. We have not established that a newer model or
an agent architecture provides such a gain. Count setup, annotation, review,
and adjudication as well as the time spent correcting individual answers.
Source: amber-nlp/docs/00-goals-and-architecture.md; docs/04-roadmap.md.
-->

---

<!-- _class: amber -->

# Amber: a proposed next generation

<p class="lead">Structured clinical answers with <strong>verified source evidence</strong><br>and a reusable correction workflow.</p>

<div class="flow four">
<div class="step"><span>DEFINE</span><h2>One task</h2><p>A note-level question<br>and a typed<br>answer schema</p></div>
<div class="step"><span>EXTRACT</span><h2>Propose an answer</h2><p>A fixed pipeline<br>returns values and<br>candidate quotes</p></div>
<div class="step"><span>VALIDATE</span><h2>Verify evidence</h2><p>Check exact source<br>spans and required<br>support links</p></div>
<div class="step"><span>REVIEW</span><h2>Correct & reuse</h2><p>Accepted examples<br>support evaluation<br>and later learning</p></div>
</div>

<div class="takeaway">The goal: less total expert effort at a defined clinical quality target.</div>

<p class="small"><strong>Under development.</strong> The full extraction and correction workflow is planned.<br>Agents are a later experiment; source validation alone does not establish clinical correctness.</p>

<!--
Amber is in early development. The checkout contains an interface scaffold
and initial source/grounding code; the full clinical workflow and comparative
benefit are not established. The diagram describes the planned workflow.
Every retained claim is intended to have validated evidence; inference chains
must terminate in verified source evidence. Semantic support still requires
expert evaluation. Start with one task, one permitted provider, a fixed pipeline,
and minimal correction. Patient aggregation and FHIR remain later work.
Sources: docs/00-goals-and-architecture.md; docs/02-v1-schemas-and-tools.md;
docs/04-roadmap.md; src/amber/grounding.py; src/amber/client.py.
-->

---

<!-- _class: comparison -->

# What changes—and what carries forward

| Dimension | Existing abstractor | Amber: proposed direction |
| :--- | :--- | :--- |
| Starting point | Implemented oncology workflows;<br>Databricks EDW deployment underway | Early development;<br>start with one measured clinical task |
| Extraction | Configured NLP rules + optional<br>LLM resolution | Typed task + fixed extraction pipeline;<br>test agents only when justified |
| Evidence | Source matches, candidates,<br>and explicit decision reasons | Validate source spans and the full<br>chain of support before saving claims |
| Review & reuse | Delivery records, expert expectations,<br>and MLflow evaluation | Canonical corrected examples for<br>evaluation and later learning |
| Platform | Databricks integration and<br>legacy-compatible report outputs | Portable core and open-source MLflow<br>APIs; Databricks by configuration |

<div class="takeaway">Carry forward clinical knowledge; measure quality, effort, and cost.</div>

<!--
The right column is a target design, not a list of delivered features. The
existing abstractor already preserves evidence and offers review/evaluation;
Amber aims to strengthen validation and unify reusable annotated examples.
The existing package has Databricks SDK/MLflow dependencies, whereas Amber's
contract requires the OSS MLflow surface and optional outward integrations.
Reusing material needs explicit adapters: the legacy NLP uses inclusive end
offsets in places; Amber's contract uses end-exclusive offsets into immutable
sources. There is no automatic migration implied by this comparison.
Sources: note-abstractor-main/pyproject.toml; docs/architecture.md;
docs/llm-workflow.md; amber-nlp/docs/02-v1-schemas-and-tools.md; CLAUDE.md.
-->

---

# Compare on the same task, cases, and quality target

<div class="columns two evaluation">
<div>
<h2>One shared protocol</h2>
<ol>
<li>Choose an overlapping note-level task and define acceptable answers.</li>
<li>Set quality targets before tuning; split by patient or document.</li>
<li>Compare rules, rules + LLM, and Amber’s fixed pipeline when ready.</li>
<li>Include manual abstraction as the reference for expert effort.</li>
</ol>
</div>
<div class="panel scorecard">
<h2>Report more than accuracy</h2>
<p><strong>Correctness</strong> · right values and missed answers</p>
<p><strong>Evidence</strong> · valid locations and clinical support</p>
<p><strong>Coverage</strong> · automatic completion, abstentions, failures</p>
<p><strong>Effort & cost</strong> · setup, review, correction, latency, model use</p>
</div>
</div>

<div class="takeaway">Track comparisons in MLflow; use expert judgment to assess clinical support.</div>

<p class="small">Amber’s first planned dataset: CORAL v1.0, with 40 expert-labeled notes.<br>A pilot, not a broad validation claim; a shared task requires validated label mapping.</p>

<!--
This is the proposed common protocol; it is not a completed head-to-head
evaluation. Hold task definitions, source cases, and scoring rules constant.
Where possible hold the LLM endpoint/configuration constant and record all
prompt, orchestration, and budget differences. Freeze choices before held-out
testing. Count omissions across all eligible cases, including abstentions and
failures, and quality among automatically completed cases. Compare expert
time at matched quality. CORAL expert gold is distinct from its 200 other
notes and GPT-4 pseudo-labels. Validate whether the selected gold annotations
support the shared task; any extra adjudication counts as expert effort.
CORAL v1.0 DOI: 10.13026/v69y-xa45. No CORAL text appears in this deck.
Sources: docs/00-goals-and-architecture.md; docs/04-roadmap.md.
-->

---

# Brim and Amber can serve complementary roles

<p class="lead"><strong>Brim Analytics is undergoing a trial at Northwestern.</strong><br>The pilot can inform how researcher tools and institutional infrastructure fit together.</p>

<div class="columns two">
<div class="panel">
<div class="eyebrow">BRIM PILOT · RESEARCHER WORKFLOW</div>
<h2>Author and review variables</h2>
<ul>
<li>Define study variables.</li>
<li>Review extracted values and evidence.</li>
<li>Produce project-level data.</li>
</ul>
</div>
<div class="panel">
<div class="eyebrow">AMBER · PROPOSED INFRASTRUCTURE</div>
<h2>Make the evidence reusable</h2>
<ul>
<li>Validate source-linked claims.</li>
<li>Version extraction and evaluation.</li>
<li>Retain evidence for EDW reuse.</li>
</ul>
</div>
</div>

<div class="takeaway">A successful pilot could inform both product adoption and institutional infrastructure.</div>

<p class="small">Proposed relationship; interoperability remains to be assessed. <a href="https://www.brimanalytics.com/">Brim Analytics</a></p>

<!--
Distilled from amber-vs-brim.md: "Where we are", "Brim does several things
well", and "The framing: layered, not competing". The source deck frames Brim
as a researcher-facing authoring/review product and Amber as an institutional
evidence and execution layer. This is a possible division of responsibilities,
not an implemented integration or an assertion that the pilot has succeeded.
The existing abstractor remains our deployed-workflow baseline. Confirm product
capabilities and export formats in the pilot. Source-deck claims about vendor
funding, benchmark accuracy, and closed-source limitations are not reproduced.
Sources: docs/slides/amber-vs-brim.md; user-provided Northwestern trial status;
https://www.brimanalytics.com/.
-->

---

# Turn expert-reviewed cases into reusable assets

<p class="lead">The pilot could produce more than a study table:<br><strong>reviewed values, variable definitions, and supporting source spans.</strong></p>

<div class="flow three">
<div class="step"><span>REVIEW</span><h2>Adjudicate the outputs</h2><p>Experts correct values<br>and verify the evidence<br>for the defined task.</p></div>
<div class="step"><span>RETAIN</span><h2>Keep reusable examples</h2><p>Preserve definitions,<br>source references,<br>and review decisions.</p></div>
<div class="step"><span>TEST</span><h2>Evaluate cheaper execution</h2><p>Test targeted rules<br>or smaller models for<br>repeated extraction.</p></div>
</div>

<div class="takeaway">Hypothesis: reviewed examples can lower recurring cost at matched clinical quality.</div>

<p class="small">Reuse requires permitted exports and verified evidence. Evaluate on an independent expert-labeled test set.</p>

<!--
Distilled from amber-vs-brim.md: "Why adjudicated Brim output is unusually
good supervision", "The compilation loop", "The design constraint that
decides everything: rules must abstain", and "Four things that will bite us".
This is a conditional research direction, not an implemented Amber feature or
a promise of cheaper execution. Confirm contractual reuse/export rights and
source references before treating reviewed outputs as reusable examples.
Develop candidate rules or models on training/development cases, freeze them,
and evaluate on held-out patients/documents. Include independent from-scratch
expert annotation to detect omissions inherited from a proposal-based review.
Audit non-escalated cases and report coverage, errors, and total expert effort.
Keep Amber's task/evidence/fixed-baseline/correction milestones first; rule
induction, distillation, and additional models require demonstrated need.
No numerical coverage or cost-reduction estimates from the source deck are used.
Sources: docs/slides/amber-vs-brim.md; docs/04-roadmap.md.
-->

---

# What we should learn from the Brim pilot

<p class="lead">Use the pilot to assess <strong>clinical value, operational fit, and reuse.</strong></p>

<div class="columns three">
<div class="panel">
<div class="eyebrow">QUALITY & EFFORT</div>
<h2>Does it help experts?</h2>
<p>Measure correct values, omissions, evidence support, and time to define and review variables.</p>
</div>
<div class="panel">
<div class="eyebrow">PORTABILITY & REUSE</div>
<h2>What can we retain?</h2>
<p>Confirm export of definitions, values, source locations, review history, and run configuration.</p>
</div>
<div class="panel">
<div class="eyebrow">DEPLOYMENT & COST</div>
<h2>Where does it fit?</h2>
<p>Assess EDW integration, permitted endpoints, and total cost at study and recurring-cohort scale.</p>
</div>
</div>

<div class="takeaway">Use MLflow for the common evaluation; let measured results guide adoption and development.</div>

<p class="small">Comparison with the existing abstractor—and Amber when ready—requires aligned tasks and independent expert review.</p>

<!--
Distilled from amber-vs-brim.md: "What the pilot should give us regardless",
"Asks", and the cost/reproducibility discussion. These are proposed evaluation
questions, not findings from the Northwestern trial. Check whether evidence
exports provide verifiable source offsets or only display snippets, whether
variable definitions can be reused, and which versions/configurations can be
recorded. Assess BAMM or other interchange only after confirming formats and
round-trip behavior. Agree permitted reuse and institutional access to exports.
Where exports permit, use MLflow to record the common evaluation; no native
Brim–MLflow integration is claimed. Compare task setup, annotation, review,
adjudication, model usage, and operational costs at the intended workload.
Sources: docs/slides/amber-vs-brim.md; docs/04-roadmap.md.
-->
---

<!-- _class: closing -->

# Deliver the baseline. Build the comparison.

<div class="columns three">
<div class="panel">
<div class="eyebrow">NOW · DEPLOY & EVALUATE</div>
<h2>Existing abstractor</h2>
<p>Deploy in the Databricks EDW.<br>Evaluate with MLflow.<br>Review errors with experts.</p>
</div>
<div class="panel">
<div class="eyebrow">NEXT · BUILD & TEST</div>
<h2>Amber’s first task</h2>
<p>Define acceptance criteria.<br>Validate source evidence.<br>Add minimal correction.</p>
</div>
<div class="panel">
<div class="eyebrow">THEN · COMPARE & DECIDE</div>
<h2>Evidence for adoption</h2>
<p>Compare matched cases.<br>Measure total expert effort.<br>Expand when results justify it.</p>
</div>
</div>

<div class="closing-statement">A practical path toward D3’s registry<br>and computational phenotyping tools.</div>

<!--
The immediate deliverable is deployment and evaluation of the existing
abstractor. Amber provides a parallel development direction, beginning with
the M1–M3 task/evidence/baseline/correction sequence. The decision to expand
depends on measured results. Neither an agent loop nor a broad patient-level
platform is required for the first useful comparison. Discussion can focus on
the first task, its clinical owner, acceptance criteria, and reviewer capacity.
Sources: user-provided deployment status; docs/04-roadmap.md.
-->
