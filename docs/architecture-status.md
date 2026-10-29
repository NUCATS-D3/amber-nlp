# Architecture and implementation status

Snapshot: 2026-10-07, after the case-outcome/result increment in commit `6334b89`.
This is a visual companion to the canonical [implementation inventory](README.md#current-implementation),
[architecture](00-goals-and-architecture.md), and [v1 contract](02-v1-schemas-and-tools.md).
Refresh the diagrams from that inventory when capabilities change.

Green means implemented within the stated scope; amber means partially implemented; blue means
planned; purple means conditional on measured benefit. Status text also appears in each node.
Implemented here means working code covered by synthetic tests, not clinical validation.

## Component architecture

Arrows show calls or dependencies, pointing from the caller toward what it uses. Solid arrows
exist today; dashed arrows describe planned integration. Optional adapters stay outside the
domain kernel, and the kernel never imports delivery, storage, or model runtimes.

```mermaid
flowchart TB
    subgraph delivery["Delivery adapters"]
        cli["CLI - IMPLEMENTED<br/>info, info --json, api serve"]
        api["HTTP API - PARTIAL<br/>health and info work<br/>extraction and annotation routers empty"]
        ui["Correction interface - PLANNED M3<br/>source, evidence, accept/correct/reject"]
    end

    facade["Python facade - PARTIAL<br/>Amber / create_client<br/>system information only"]
    settings["Settings - IMPLEMENTED<br/>explicit injection, AMBER environment"]

    subgraph services["Use cases and coordination"]
        system["SystemInfo service - IMPLEMENTED"]
        workflow["Clinical services - PLANNED M2-M3<br/>fixed extraction, correction, execution audit"]
        ports["Provider and store protocols - PLANNED<br/>services depend on interfaces"]
    end

    subgraph kernel["Domain kernel and shared deterministic boundaries"]
        tools["Tools - IMPLEMENTED<br/>quote / commit_claim / no_claim"]
        results["Case-result boundary - IMPLEMENTED<br/>create / load, final IDs, explicit outcomes"]
        evidenceGraph["EvidenceGraph - IMPLEMENTED<br/>atomic commits, full support validation<br/>in-memory snapshot and restore"]
        domain["Schemas, IDs, exact grounding - IMPLEMENTED SUBSET<br/>Source, Task, Claim, Inclusion, InferenceEvidence<br/>Case, CaseOutcome, CaseResult, Provenance"]
        policy["Provider and destination policy - PLANNED M1<br/>pure zone / sensitivity rules"]
    end

    subgraph adapters["Outward adapters"]
        provider["Provider adapter - PLANNED M2<br/>one permitted endpoint, capability checks"]
        store["Local store - PLANNED M1<br/>DuckDB via store protocols"]
        export["Native export - PLANNED M1<br/>Parquet source/evidence/outcome bundle"]
        prompts["Prompt registry - PLANNED M2<br/>versioned YAML seeds and loading"]
        tracking["MLflow integration - PARTIAL<br/>amber.version run-context tag works<br/>clinical tracing and evaluation planned"]
    end

    subgraph extensions["Conditional extensions"]
        agents["Bounded agents and note reviewers - CONDITIONAL M4-M5<br/>compare with the fixed baseline"]
        terminology["Terminology, detectors, contextual linking - CONDITIONAL<br/>task need or measured baseline error"]
    end

    cli --> facade
    api --> facade
    ui -.-> facade
    facade --> settings
    facade --> system
    facade -.-> workflow
    workflow -.-> tools
    workflow -.-> results
    tools --> evidenceGraph
    tools --> domain
    results --> evidenceGraph
    results --> domain
    evidenceGraph --> domain
    workflow -.-> policy
    workflow -.-> ports
    provider -.-> ports
    store -.-> ports
    workflow -.-> export
    workflow -.-> prompts
    workflow -.-> tracking
    provider -.-> domain
    store -.-> domain
    export -.-> domain
    agents -.-> tools
    agents -.-> results
    agents -.-> policy
    agents -.-> ports
    agents -.-> prompts
    agents -.-> tracking
    workflow -.-> terminology
    terminology -.-> policy

    classDef implemented fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef partial fill:#fef3c7,stroke:#b45309,color:#78350f
    classDef planned fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef conditional fill:#f3e8ff,stroke:#9333ea,color:#581c87
    class cli,settings,system,tools,results,evidenceGraph,domain implemented
    class api,facade,tracking partial
    class ui,workflow,ports,policy,provider,store,export,prompts planned
    class agents,terminology conditional
```

The facade and HTTP/CLI adapters do not currently expose clinical runs. Python callers use the
implemented tools and case-result functions directly. Policy, provider, store, export, and
prompt-registry modules currently contain placeholders; installing their dependency extras does
not implement them. The implemented MLflow tag has no source-bearing clinical tracing path.

Policy is a prerequisite for every model/EDW call and every source-bearing destination transfer,
including telemetry, judges, artifact stores, and annotation services. The diagram groups those
boundaries for readability; no dashed edge represents an authorized or working transfer today.

## Current in-memory evidence workflow

These arrows show data flow. The entire flow exists today for caller-supplied inputs. A model
runtime does not yet produce the candidate values, quotes, review declarations, or failures.

```mermaid
flowchart LR
    context["IMPLEMENTED<br/>Source + Task + Case<br/>one note, explicit sensitivity"]
    quote["IMPLEMENTED<br/>exact quote grounding<br/>immutable text offsets"]
    evidence["IMPLEMENTED<br/>verified Inclusion<br/>register in EvidenceGraph"]
    commit["IMPLEMENTED<br/>commit_claim<br/>schema, fields, support DAG"]
    claim["IMPLEMENTED<br/>proposed Claim<br/>positive or explicit negative"]
    outcome["IMPLEMENTED<br/>CaseOutcome / no_claim<br/>answer, non-answer, or failure"]
    result["IMPLEMENTED<br/>create_case_result<br/>full graph revalidation<br/>final IDs separate from support"]
    restore["IMPLEMENTED<br/>serialize / load_case_result<br/>authoritative context required<br/>in memory only"]

    context --> quote
    quote --> evidence
    evidence --> commit
    context --> commit
    commit --> claim
    context --> outcome
    evidence --> outcome
    claim --> result
    evidence --> result
    outcome --> result
    context --> result
    result --> restore
    context --> restore

    classDef implemented fill:#dcfce7,stroke:#15803d,color:#14532d
    class context,quote,evidence,commit,claim,outcome,result,restore implemented
```

`answered` requires nonempty final IDs naming known, non-rejected claims. The four other outcomes
(`not_mentioned`, `conflicting_evidence`, `insufficient_evidence`, `failed`) require no final IDs;
valid supporting claims can still be retained. `no_claim` creates only the three clinical
non-answer outcomes; callers construct execution failures with a failure kind. A not-mentioned
outcome requires declared complete review of the case note. Scope checks do not prove that the
review was complete, that cited evidence conflicts, or that a claim is clinically supported.

## Preparation, annotated data, and evaluation

| Area | Current status | Next boundary |
|---|---|---|
| Progression task | Protocol and strict boolean answer schema implemented | Independent clinical adjudication and prospective acceptance gates remain unevaluated |
| CORAL preparation | BRAT parser/audit, candidate rules, and split/manifest tooling implemented; local split recorded in the inventory | Draft mapping adapter is not functional; candidates are not task gold or Amber Examples |
| Annotated data | `Example`, append-only human review events, and verified/gold transitions planned | `Example` becomes the single annotated-data source of truth; failed runs cannot become gold |
| Clinical evaluation | Metrics, semantic-support review, and expert-effort comparison planned for M2-M3 | Evaluate the fixed baseline, then manual authoring versus assisted correction at matched quality |
| Additional domain scope | Mention and StructuredEvidence models planned; CaseResult mentions must currently be empty | Add with implementations and invariant tests when the selected use case requires them |
| Patient-wide scope and interoperability | PatientFact, temporal aggregation, FHIR, and additional backends deferred; OMOP NOTE_NLP conditional | Preserve patient IDs and effective datetimes without expanding the current single-note graph |

CORAL preparation stays under [experiments/coral](../experiments/coral/README.md); core code never
imports experiments. Its credentialed, deidentified data and local manifests are restricted,
ignored artifacts. No corpus records or manifests were inspected to create this diagram.

M0's interfaces exist and M1 is partly implemented. The remaining delivery order is policy and
local persistence/annotated-data boundaries, one permitted fixed baseline in M2, then minimal
correction and measured expert effort in M3. Agents, broad NLP, and additional runtimes follow
measured benefit. See the [roadmap](04-roadmap.md) for milestone requirements.

The implemented flow is covered by the [synthetic test suites](../tests/README.md) and
[installed core smoke example](../scripts/check_core_install.py). Code entry points are
[schemas](../src/amber/schemas/__init__.py), [grounding](../src/amber/grounding.py),
[graph](../src/amber/graph.py), [commit](../src/amber/tools/commit.py),
[outcomes](../src/amber/tools/outcomes.py), and [case results](../src/amber/cases.py).
