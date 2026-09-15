# Context-Aware Biomedical Entity Linking: Reading Guide

This note summarizes useful literature and evaluation criteria for building a context-aware
biomedical entity-linking pipeline beyond a surface-form embedding baseline such as SapBERT.
It reflects literature checked through September 2026.

## Recommended reading order

1. **Kartchner et al. (2023), [A Comprehensive Evaluation of Biomedical Entity Linking
   Models](https://aclanthology.org/2023.emnlp-main.893/).** This is the best starting point. It
   compares nine systems under a unified framework along accuracy, speed, usability,
   generalization, and adaptability. The evaluation finds that current systems still struggle
   with genes and proteins and often fail to use context effectively.

2. **Zhang et al. (2022), [Knowledge-Rich Self-Supervision for Biomedical Entity Linking
   (KRISSBERT)](https://aclanthology.org/2022.findings-emnlp.61/).** KRISSBERT is an important
   contextual-retrieval baseline. It generates self-supervised contextual mentions from an
   ontology and unlabeled text, trains a contextual encoder with contrastive learning, and uses
   sampled mentions as entity prototypes at inference time. It is especially relevant for
   zero-shot linking and concepts without reliable canonical descriptions.

3. **Sänger et al. (2024), [BELHD: Improving Biomedical Entity Linking with Homonym
   Disambiguation](https://doi.org/10.1093/bioinformatics/btae474).** BELHD focuses on ambiguous
   aliases and homonyms, where synonym-oriented embeddings such as SapBERT are weakest. It is a
   useful reference for separating candidate retrieval from contextual disambiguation.

4. **Ye and Mitchell (2025), [LLM as Entity Disambiguator for Biomedical
   Entity-Linking](https://aclanthology.org/2025.acl-short.25/).** This work places an LLM after
   candidate generation and reports gains of up to 16 accuracy points across several biomedical
   datasets without fine-tuning. Its results make it an important reranking reference, but cost,
   latency, reproducibility, privacy, and dependence on candidate quality must be evaluated in
   the intended deployment setting.

5. **Sänger et al. (2023), [BELB: A Biomedical Entity Linking
   Benchmark](https://doi.org/10.1093/bioinformatics/btad698).** BELB provides a common benchmark
   spanning multiple entity types and datasets. It is preferable to comparing headline results
   reported under incompatible preprocessing, ontology releases, or train/test assumptions.

6. **Yuan et al. (2022), [Generative Biomedical Entity Linking via Knowledge Base-Guided
   Pre-training and Synonyms-Aware Fine-tuning
   (GenBioEL)](https://aclanthology.org/2022.naacl-main.296/).** GenBioEL is a useful alternative
   to retrieve-then-rerank systems. It uses knowledge-base-guided pretraining, synonym-aware
   fine-tuning, and constrained decoding to generate canonical concept names without a separate
   candidate-selection stage.

7. **[BeLink: Biomedical Entity Linking Meets Generative
   Re-Ranking](https://doi.org/10.1145/3805712.3809918) (SIGIR 2026).** BeLink is a recent
   reference connecting candidate retrieval with generative reranking. Because it is new, its
   results should be reproduced on the target ontology and corpus before treating it as a settled
   baseline.

## Recommended system comparison

A practical evaluation should separate retrieval from disambiguation:

```text
mention + local context
        |
        +-- lexical/UMLS alias retrieval
        +-- SapBERT or BioLORD dense retrieval
        +-- KRISSBERT-style contextual retrieval
                         |
                         v
                merged top-k candidates
                         |
                         v
       cross-encoder or constrained LLM reranker
                         |
                         v
          concept ID + calibrated abstention
```

The minimum useful baseline suite is:

- lexical alias retrieval;
- SapBERT dense retrieval;
- KRISSBERT contextual retrieval;
- BELHD-style homonym disambiguation; and
- SapBERT top-20 retrieval followed by either a biomedical cross-encoder or the Ye and Mitchell
  LLM disambiguator.

This comparison identifies whether improvement comes from candidate recall, contextual ranking,
or both. A reranker cannot recover a correct concept that was absent from the retrieved candidate
set.

## Evaluation criteria

Measure retrieval and final linking separately:

- candidate recall at several values of *k*;
- top-1 accuracy and mean reciprocal rank;
- accuracy on ambiguous aliases and homonyms;
- performance on seen versus unseen concepts and synonyms;
- calibration, abstention accuracy, and selective risk;
- robustness to ontology-version drift;
- results by entity type, especially diseases, drugs, genes, and proteins;
- latency, memory use, and monetary cost; and
- expert correction time, not only automated accuracy.

For clinical notes, compare mention-only, sentence, section, and wider note context explicitly.
More context is not automatically better: a full note can introduce historical conditions,
family history, negated findings, or diagnoses belonging to another section. Section-level context
is therefore a strong default hypothesis, but it should be validated for the target task.

## Implications for Amber

Amber should treat concept normalization as a candidate-producing inference step rather than as
ground truth. The selected concept and any abstention should retain the source mention and exact
verified quote, the context window or section identifier, ontology name and release, candidate
generator and version, candidate set with scores, reranker and version, and final confidence.

Any use of an external reranker or LLM remains subject to Amber's provider-zone and data-sensitivity
policy before source-bearing context is transmitted. For clinical evaluation, entity-linking
accuracy should be reported alongside semantic support, omissions, automation coverage, and total
expert effort.
