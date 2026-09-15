---
marp: true
theme: default
paginate: true
header: 'Clinical IE infrastructure — Feinberg'
style: |
  section {
    font-size: 26px;
  }
  section.lead h1 {
    font-size: 52px;
  }
  h1 {
    color: #1a3a4a;
  }
  h2 {
    color: #1a3a4a;
    font-size: 34px;
  }
  table {
    font-size: 22px;
  }
  code {
    font-size: 0.9em;
  }
  .small {
    font-size: 20px;
    color: #555;
  }
---

<!-- _class: lead -->

# Chart abstraction: the interface and the substrate

### Why a successful Brim pilot makes the case for building underneath it, not instead of it

Will Thompson · Northwestern Feinberg

---

## Where we are

- Feinberg has agreed to a **Brim Analytics pilot**
- Brim is a strong product: Vanderbilt spin-out, $3.9M ARPA-H (2024–2027), 120+ teams at VUMC, deployed at UCSF, CHOP, and others
- Bring-your-own-LLM, on-prem install, evidence shown for every value, human-in-the-loop review
- **This deck assumes the pilot succeeds.** The question is what we build around it.

---

## Brim does several things well — that we should not rebuild

- No-code variable authoring for non-technical researchers
- 250+ pre-built validated variables (NSQIP, CathPCI, trauma, oncology)
- REDCap and Epic import; CSV and REST export
- SOC 2 / HIPAA; runs inside our network on our LLM endpoint
- A review UI researchers reportedly *enjoy* using

**The annotation app is the heaviest milestone in our own roadmap. Brim has shipped and iterated on it for two years.**

---

## The independent evidence is real

Johns Hopkins, medRxiv preprint, March 2026 — Brim vs. DeepPhe on 330 pancreatic + 34 breast pathology reports

| | Brim | DeepPhe |
|---|---|---|
| Pancreatic, 7 vars (mean) | **96.7%** | — |
| T stage | 96.4% | 83.6% |
| Breast, zero-shot transfer | 93.7% | 83.3% (staging) |
| Free-text vs synoptic error | 4.6% / 1.1% | 21.4% / 3.3% |

<span class="small">Caveats: Brim received two rounds of expert variable tuning; DeepPhe ran out-of-the-box. Single institution. n=34 breast is underpowered. Ran v2024.10.30 on GPT-4.1-mini.</span>

---

## What the benchmark did *not* measure

Brim produces evidence for every value. The study evaluated **accuracy only**.

No measurement of:

- Whether cited spans actually support the claim
- Whether spans align to the source text at all
- Span localization against gold spans
- Grounding-failure rate

**The thing we care about most has never been benchmarked.**

---

## So why build anything?

Three arguments, in order of how hard they are to dismiss:

1. **Cost at EDW scale**
2. **We cannot modify what we cannot see**
3. **Institutional assets vs. per-project outputs**

---

## Cost: the arithmetic is driven by one design choice

BAMM's *map* phase runs **one model call per variable per note**.

Bill ≈ `variables × notes × (note tokens + instruction tokens)`

The note is re-read once per variable.

<span class="small">Illustrative: 2,500-token pathology report · 800-token instruction block · 25 registry variables → ~82,000 input tokens per note</span>

---

## Cost: model tier swings it 10× before anything else

Per **one million notes**, 25 variables, illustrative rates:

| Model tier | Approx. input cost |
|---|---|
| Mini tier (~$0.40/M) | **~$30K** |
| Frontier tier (~$3/M) | **~$250K** |
| Reasoning + self-consistency voting | multiply again |

Tractable — but not something to leave unengineered.

---

## Three levers, none of which we can pull in a closed platform

**1. Prompt caching and prompt order**
Note as cache prefix, question as suffix → 24 of 25 calls hit cache. 5–10×.
If the instruction comes first, caching buys nothing. We cannot inspect this.

**2. Multi-variable batching**
One call asking 25 questions instead of 25 calls. Near an order of magnitude, at a *measurable* accuracy cost.

**3. Input reduction before the model sees anything**
TRACE-style template/copy-forward removal: **47% of chart text removed, <0.002 F1 loss**.
Brim does no note-structure preprocessing — it concatenates sections and sends the whole note.

---

## Stacked, the spread is 20–50× on identical output

At one million notes: the difference between a **grant line item** and a **non-starter**.

Then the cascade underneath:

- Deterministic tier (sections → mentions → ConText → rules) answers the bulk
- Escalate to the agent only on low confidence, conflict, or unfilled schema
- Escalation rate becomes an **instrumented dial**, not a guess

Brim's conditional generation skips variables by criteria — but there is no non-LLM tier. Every value costs a model call.

---

## The endgame: convert variable cost to capital cost

- Distill to an 8B model, LoRA-tuned per schema, served on lab GPUs
- Marginal cost of the millionth note ≈ electricity
- 8B students have beaten 70B teachers on trial-criteria extraction

Brim has **explicitly ruled this out** — they published the case for foundation models over fine-tuning.

*Defensible for a vendor that must work at 50 institutions on day one. Wrong for one institution running six schemas across millions of notes.*

---

## What closed source actually costs us

Things we would want to do, and cannot:

- Insert sectioning or template removal upstream of generation
- Change chunking, caching, batching, or decode strategy
- Add SapBERT normalization → SNOMED / RxNorm / UMLS
- Add our own scorers — evidence faithfulness, span localization, grounding failure
- Audit the prompt that was actually sent
- Fix a bug or add a field on **our** schedule

---

## Reproducibility is a research-integrity argument

Retrospective studies get challenged, reviewed, and re-analyzed years later.

**Reproducible:** frozen adapter + versioned prompt + dataset hash + run id

**Not reproducible:** "GPT-4.1-mini, as it behaved in March 2026, inside a vendor's orchestration layer"

Hosted model versions get deprecated. Our cohorts outlive them.

---

## Continuity risk, stated plainly

Brim is a small company on federal grant runway through 2027, no disclosed venture round, founded by someone whose previous company was acquired twice.

Survival, acquisition, or pivot — **all three branches** leave an institutional abstraction layer as someone else's asset.

An owned data model and an open pipeline is insurance against all three.

---

## Value-add 1: the mention layer as durable infrastructure

**Brim's output:** a per-project table. Project ends, extraction dies with it.

**Ours:** mentions land in the EDW as an OMOP `NOTE_NLP` view with stable, content-addressed ids.

The 100th project inherits the first 99.

*This is the structural difference between a research platform and a project tool.*

---

## Value-add 2: grounding as a measured quantity

| | Brim | amber |
|---|---|---|
| Evidence | highlighted snippet for a reviewer | verified char interval, minted only via `quote()` |
| Unalignable span | (unknown) | dropped, counted as grounding failure |
| Reported as | a UI affordance | a **number on a run** |

One is a review aid. The other is a claim you can put in a paper and a control you can put in front of an IRB.

---

## Value-add 3: longitudinal and patient-level reasoning

BAMM aggregation is a **prose instruction to an LLM**:
*"Return the most recent, or the higher stage if they conflict."*

Fine for a T stage. Not sufficient for cardiovascular phenotyping:

- EF trajectories, medication changes over time, event adjudication
- Typed effective datetimes, conflict detection across notes
- Evidence that **survives aggregation** — PatientFact edges point back to note-level claims and their spans

---

## Value-add 4: one evidence model for structured + unstructured

A phenotype depending on a lab value, a med order, and a sentence in an HPI should produce **one claim with three evidence edges of different kinds**.

- `Inclusion` — verified span in a note
- `StructuredEvidence` — a row in the EDW
- `InferenceEvidence` — the reasoning step that combined them

Brim added structured data in Jan 2026 — as an *input*, not a first-class evidence type.

---

## Value-add 5: research and funding surface

- **Note-structure parsing** of plain-text EHR exports: no benchmark, no off-the-shelf tool exists. That's a paper, not just an optimization — and it happens to be worth half the token bill.
- **Annotation economics**: time-per-case, IAA, accuracy vs. number-of-examples per rung of the ladder. A vendor will never publish this.
- **Grant surface**: DAGCAP drew $3.9M of federal money for essentially this problem statement. Open + grounded + OMOP-native + reproducible is a fundable program.
- **Multi-site work** with collaborators who hold no Brim license.

---

## The framing: layered, not competing

**Brim owns the front door** — variable authoring, the library, REDCap/Epic, the review experience.

**amber owns the substrate** —

- grounded data model and evidence DAG
- cheap deterministic tier + escalation
- local / distilled execution path
- evaluation and provenance layer
- OMOP landing zone that makes output reusable

---

## BAMM as the seam

Brim has proposed BAMM as an open, tool-agnostic abstraction schema — and is **publicly inviting collaboration**.

- Import a BAMM variable set as an amber `Task`
- Run it through our pipeline when scale or reproducibility demands it
- Export claims and evidence back

A pilot site offering an open reference implementation of their own proposed standard is a conversation they have every reason to want.

---

<!-- _class: lead -->

## The pilot isn't a purchase. It's a bootstrap.

### Use the expensive system to compile a cheap one

---

## Inverting the annotation ladder

The ladder climbs **up** from zero-shot, using examples to buy accuracy.

This runs **down** from expensive to cheap, using adjudicated output to buy scale.

Same asset. Opposite direction.

---

## Why adjudicated Brim output is unusually good supervision

Every Brim value ships with **evidence**. A human adjudicates it.

Each reviewed case yields: `(note, variable, gold value, where the answer lives)`

- Label-only supervision tells you **what** the answer is
- The span tells you **where** — which is most of the work of rule induction

We get localized supervision as a byproduct of a workflow the institution is already paying for.

---

## The compilation loop

1. Cluster adjudicated spans per variable — section, preceding key text, surface forms → enum values
2. LLM proposes candidate patterns from the clusters: section locator, key pattern, value normalizer
3. Validate each candidate empirically on a holdout
4. Keep only rules clearing a high precision threshold with adequate support

**The LLM becomes the compiler — called once per variable — not the executor called once per note.**

That is the cost inversion.

---

## The design constraint that decides everything: rules must abstain

Error profiles from the Hopkins study:

| System | False positives | False negatives |
|---|---|---|
| DeepPhe (rule/ontology) | 48 | 6 |
| Brim (LLM) | 1 | 11 |

DeepPhe fires confidently and is wrong. The authors argue underclassification is the correct failure mode with a human in the loop.

**Build the rule tier to that spec:** high precision, generous abstention, every abstention escalates.
A rule that says *"I don't know"* costs one LLM call. A rule that is silently wrong costs a retracted cohort.

---

## Where the yield is concentrated

Hopkins error rates by report format (Brim): **1.1% synoptic vs. 4.6% narrative**

- Synoptic CAP-templated content is near-deterministic: key:value pairs, controlled vocabulary
- That is the compilable fraction — substantial and growing post-2019
- Narrative inference, negation/temporality subtleties, and the rare-phrasing tail will not compile

**Nice convergence:** TRACE-style template detection finds templated spans — and templated spans are the highest-yield rule targets. The stage that cuts the token bill also tells the rules where to look.

Expect a Zipf curve: **60–85%** of variable-instances at high precision; the remainder resists indefinitely.

---

## Three tiers, each with a measured hand-off rate

| Tier | Marginal cost | Coverage |
|---|---|---|
| Compiled rules | ~0 (CPU, thousands/sec) | 60–85% |
| Distilled 8B, on-prem | electricity | the middle band |
| Frontier model | $$ | the tail |

Rules at 70% coverage alone cut the LLM bill **3.3×**.
Stacked with caching and template removal: **50–100×** off the naive per-variable number.

---

## The reproducibility payoff is the underrated part

A rule set in git with a hash is the strongest methods artifact available:

- Perfectly deterministic
- Inspectable by a reviewer
- Re-runnable in 2031
- Independent of any hosted model version

Better on this axis than a distilled adapter. Far better than a vendor's orchestration layer.

**If reproducibility is the argument to a study section, compiled rules are the cleanest thing to point at.**

---

## Four things that will bite us

**Rules rot silently.** Epic upgrades, template revisions, new CAP versions — patterns drift and nothing errors out. Permanent canary: route a random few percent to the LLM tier, monitor agreement, alarm on degradation.

**Brim-adjudicated gold carries Brim's blind spots.** If Brim misses a phrasing, the adjudicator never sees it. Needs an independent from-scratch gold sample, or we're measuring *agreement with Brim*, not accuracy.

**Amortization is per schema.** Breast pathology across millions of notes: yes. A one-off 400-chart study: never. State the crossover point explicitly.

**Check the pilot agreement.** Using vendor output to train a system that reduces dependence on that vendor is exactly what a ToS clause may address. Probably fine — BYO-LLM, our notes, our data — but *probably* isn't good enough to build a program on.

---

## What the pilot should give us regardless

Questions no amount of reading answers. Get them on the record:

- Does the evidence API return **char offsets**, or just a snippet? Is the span **verified** against the note?
- Are BAMM definitions genuinely round-trippable?
- Which endpoint, inside whose BAA boundary?
- How are note-level conflicts resolved — visibly, or silently?
- Is there a confidence signal that **correlates with actual error**?
- Can we pull evidence rows programmatically?

---

## Asks

1. **Establish now, in writing:** gold datasets, variable definitions, adjudication logs, and **derived works** are institutional assets, exportable in open formats. This gets much harder to negotiate after go-live.
2. **Instrument the pilot** for token cost per note per variable — and for **hours to a working variable set**, the number the case studies never report.
3. **Retain adjudicated spans**, not just final values. The span is what makes compilation possible.
4. **Fund the substrate work** as infrastructure, not as a competing pilot.

---

<!-- _class: lead -->

## The three-sentence version

Brim is the interface. It is good, and we should use it — its UI and library get variables authored and adjudicated by people who aren't engineers.

That adjudication compiles into a rule tier that runs our standing high-volume phenotypes at near-zero marginal cost, with a citable rule set and a reproducible methods section.

Brim keeps the new schemas, the low-volume projects, and the tail. **Nobody has to be replaced for this to pay off.**
