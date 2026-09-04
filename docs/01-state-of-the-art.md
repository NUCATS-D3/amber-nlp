# Clinical Information Extraction: State of the Art (living doc)

Last updated: 2026-09-02. Ongoing — update as new results land. Later sessions: read this before re-researching, and append rather than rewrite.

## Summary

The field has split into two regimes. For classic span-level NER on established corpora, encoder-style models (GLiNER family, fine-tuned BERT/GatorTron) still lead, by 15–30 F1 over decoder LLMs. For document-level, schema-driven extraction (registry curation, trial eligibility, pathology/radiology report structuring, SDOH), generative models now lead, but the winning recipe is fine-tuned or distilled small open models and decomposed pipelines, not single-prompt frontier models.

## Regime 1: span-level NER — encoders still win

- Clinical NER Benchmark (arXiv 2410.05046; entities mapped to OMOP classes; token- and span-level scoring): GLiNER multitask-large ~65.7 avg F1 zero-shot, UniNER-7B 62.3, GLiNER large v2.5 59.8. GPT-4o and Llama-3-70B 15–30 points lower. Autoregressive generation is a poor fit for boundary-exact extraction.
- JAMIA 2025/26 "Are we ready to switch to LLMs?" (UTHealth; ~1,600 notes from UT Physicians, MTSamples, MIMIC-III, i2b2): instruction-tuned Llama-2/3 vs BERT. With abundant training data, LLMs gain only ~1% NER and 1.5–3.7% RE, at up to 28× slower. Out-of-distribution (unseen i2b2) LLMs gain +7% NER / +4% RE. Conclusion: task-specific choice, not wholesale switch.
- GLiNER-BioMed (Bioinformatics 2026 / arXiv 2504.00676): uni- and bi-encoder variants; LLM-distilled synthetic biomedical NER pretraining + general-domain fine-tuning; +6 F1 over strongest prior GLiNER baseline zero/few-shot; CPU-deployable.
- OpenMed (Hugging Face / GitHub, Apache-2.0): 2,000+ open clinical NER and PHI de-id encoder models, local-first, 21 languages.

## Regime 2: document-level schema extraction — generative, but fine-tuned/distilled

1. Small fine-tuned open models reach human level. Sci Rep 2025 (Berkeley/UCSF): LoRA-tuned Llama-3.1-8B on ≤100 reports per task (breast/kidney/bone-marrow pathology, prostate MRI) → 87–92% exact match, non-inferior to a second human annotator, ahead of GPT-4 (86%). Zero-shot open models 17–57%; "medical" pretrained models (PMC-LLaMA, UltraMedical) were worst. Domain pretraining bought nothing; task fine-tuning bought everything.
2. Distillation beats the teacher. npj Digit Med 2025: Llama-3.1-70B generates QA pairs with source spans, difficulty, explanations → QLoRA students 1B/3B/8B. 8B student beat 70B teacher on trial-criteria extraction (balanced acc 0.93 vs 0.89; i2b2 2018 micro-F1 0.89 vs 0.93) at ~¼ cost ($929 vs $4,066 for 10k patients × 23 criteria).
3. Reasoning models close the gap zero-shot with engineering. SDOH on n2c2/UW SHAC (arXiv 2604.13502): o4-mini micro-F1 0.866 (top-tier for the shared task, precision 0.902) using official guidelines in prompt + 50-shot + self-consistency voting across 3 runs (+0.063 alone) + post-hoc validation. Gemini 2.5 Flash 0.825; Llama-3.1-8B zero-shot 0.591.

## Pipelines over single prompts

- CLINES (medRxiv, Dec 2025): semantic chunking (~768 tok) → LLM mention extraction → SapBERT normalization to UMLS → assertion/value+unit/date extraction → aggregation to i2b2-style schema. Beat single-prompt GPT-4o/o3-mini by +0.21–0.38 entity F1 (entity F1 0.69–0.87, assertion 0.84–0.93, value+unit 0.77–0.90) on MIMIC-III, 4CE, CORAL. Flat across note length where cTAKES/GatorTron degraded. Hallucination audit: ~54% of "hallucinations" were annotation gaps.
- Google LangExtract: same shape productized — few-shot schema, mandatory character-offset grounding, chunked long context, visualization.
- MEDIQA-SYNUR 2026 (LREC ClinicalNLP workshop shared task, nurse-dictation observation extraction): dominated by RAG-grounded, schema-constrained, multi-stage LLM pipelines mapping to flowsheet/SNOMED targets. Also at the workshop: context-aware SNOMED CT entity linking, multilingual clinical resources (JA, NL, ES, FR, AR), human-in-the-loop validation, privacy-preserving methods.

## Entity normalization: SapBERT

- Canonical citation: Liu, Shareghi, Meng, Basaldella, Collier. "Self-Alignment Pretraining for Biomedical Entity Representations." NAACL 2021, pp. 4228–4238. doi:10.18653/v1/2021.naacl-main.334; arXiv:2010.11784. Multilingual companion: Liu et al., "Learning Domain-Specialised Representations for Cross-Lingual Biomedical Entity Linking" (ACL 2021, XL-BEL). Code: github.com/cambridgeltl/sapbert; default checkpoint `cambridgeltl/SapBERT-from-PubMedBERT-fulltext`.
- Status 2025–26: still the default bi-encoder candidate retriever. Newer work layers re-rankers on top (BioNNE-L 2025 hybrid re-ranking; accelerated cross-encoders, BioNLP 2025; neighborhood-aware dual linking, arXiv 2608.04144) rather than replacing the representation. Comparison paper: Kartchner et al., "A Comprehensive Evaluation of Biomedical Entity Linking Models" (EMNLP 2023).

## Note structure: sections, headers, layout

Status: section boundaries reasonably mature for clean notes; header semantics has a lineage (SecTag/LOINC DO) but no modern standard; name/value pairs, pseudo-tables, and whitespace layout have no off-the-shelf clinical tool.

- Rule-based baseline: medspaCy Sectionizer (pattern rules → section category, nesting), descended from SecTag (Denny et al. 2008/2009, Vanderbilt; ~1,100 header concepts with synonyms/hierarchy, partially mapped to the LOINC Document Ontology). LOINC DO remains the only standards-body vocabulary for section semantics.
- Reference corpus: MedSecId (Landes et al., COLING 2022; 2,002 MIMIC-III notes, 51 section types; github.com/uic-nlp-lab/medsecid).
- CNSight (arXiv 2512.22795, Dec 2025; 1,000 MIMIC-IV notes): medspaCy won on free-text notes (88 F1); API LLMs (GPT-5-mini, Gemini 2.5 Flash, Claude 4.5 Haiku) won on sentence-segmented input (~80 F1); 7B "medical" LLMs poor.
- LREC 2026 obstetrics paper (arXiv 2602.17513): fine-tuned BERT/GatorTron+CRF 0.68 macro-F1 in-domain on MedSecId, 0.40–0.50 on new subdomain; zero-shot Llama-3.3-70B 0.67 out-of-domain after mapping hallucinated header labels to the taxonomy (+9–33 F1 from that step).
- Bhattacharya et al. 2024 (arXiv 2404.16294): GPT-4 97% on MedSecId, 38% on real faxed/OCR'd prior-auth documents; header creativity + exact-match evaluation are the culprits.
- MedSlice (arXiv 2501.14105, Jan 2025): best production pattern. LoRA-tuned Llama-3.1-8B on ~500 annotated notes; model emits first/last five words of each section, fuzzy-matched (Levenshtein >80%) back to source for exact offsets. 0.89/0.94 F1 (HPI-like / A&P) vs GPT-4o 0.78/0.79 and SecTag/medspaCy 0.19–0.30.
- JAMIA Open 2024 (ooae075): sectioning as QA with section definitions in prompt; 27 types; GPT-4 F1 0.77; modest domain-specific fine-tuning beats large general instruction data.
- Layout / name-value / embedded tables: no recommended library. Document-AI layout models (LayoutLM, Donut) don't apply to plain text; PDF parsers (LlamaParse, Docling, Unstructured) don't help on EHR text exports. Practice is bespoke line-level classification (header / key:value / table row / list / prose via indentation, colon position, delimiter repetition, column alignment, numeric density) + regex, or grounded LLM-to-JSON (LangExtract style) for vitals/labs/med blocks.
- Template / copy-forward detection: TRACE (Stanford, arXiv 2604.16364, Apr 2026). Reference module aligns Epic Clarity attribution metadata against final notes (Ratcliff-Obershelp; 97% precision / 84% recall on template spans); frequency-based fallback without attribution data. Removed 47% of chart text with negligible downstream IE loss (<0.002 F1). Templated blocks are where flowsheet dumps and SmartPhrase tables live, so this pairs with sectioning.
- Suggested stack: medspaCy or MedSlice-style fine-tuned sectionizer for boundaries → LLM or SapBERT-style embedding normalization of headers onto a SecTag/LOINC-DO-derived category set → TRACE-style template detection → line-level heuristics + grounded LLM extraction inside structured blocks.

## Where it still fails

- medRxiv Jan 2026 benchmark of extraction tools on 1,000 synthetic molecular test reports (7 layouts, clean vs fax-distorted): GPT-4.1-mini best at 55.6 F1 clean / 37.3 distorted; Gemma-3-27B best open at 41.3 (image input); NuExtract 2.0 4B promising for constrained settings. Nothing cleared 65 F1. Prompting strategy (zero- vs one-shot) had minimal effect.
- Open problems: layout-heavy and OCR-degraded documents; token-level boundary fidelity from decoders; long tail of rare entities; gold-standard quality (annotation gaps masquerading as hallucinations).

## Practical recipe (for I.AIM-style work)

- Annotate ~100 docs per schema and LoRA-tune an 8B open model, or distill from a larger one with span-grounded synthetic QA.
- Use GLiNER-BioMed / OpenMed encoders for span NER and PHI de-id.
- Reserve reasoning-model zero-shot (with guidelines-in-prompt, few-shot, self-consistency) for low-volume or rapidly changing schemas.
- Treat span grounding + normalization (SapBERT → UMLS/SNOMED/OMOP) as non-negotiable; that's the common thread across leading systems.

## Gaps / to watch

- No head-to-head yet of GPT-5-class or Claude 4.x-class models on n2c2-style corpora (reasoning results above are o4-mini / Gemini 2.5).
- n2c2 site shows no new 2025/2026 IE shared task; freshest community benchmark is MEDIQA-SYNUR 2026.
- Temporal relation extraction with long-context transformers (Frontiers Digit Health 2026, MIMIC-III/IV robustness) — not yet read closely.
- Structured-output robustness of small LMs for open attribute-value extraction (arXiv 2507.01810) — not yet read closely.
- Layout/name-value parsing of plain-text notes: no benchmark or standard tool found; candidate for original work.

## Sources

- JAMIA: https://academic.oup.com/jamia/advance-article/doi/10.1093/jamia/ocaf213/8425815 (arXiv 2411.10020)
- Clinical NER Benchmark: https://arxiv.org/pdf/2410.05046
- GLiNER-BioMed: https://academic.oup.com/bioinformatics/article/42/6/btag322/8690923 (arXiv 2504.00676)
- Fine-tuned LMs, human-level IE: https://www.nature.com/articles/s41598-025-28767-z
- Synthetic data distillation: https://www.nature.com/articles/s41746-025-01681-4
- Reasoning LLMs for SDOH: https://arxiv.org/html/2604.13502v2
- CLINES: https://www.medrxiv.org/content/10.64898/2025.12.01.25341355v2.full
- Benchmarking IE tools on medical documents: https://www.medrxiv.org/content/10.64898/2026.01.19.26344287v1.full
- LangExtract: https://github.com/google/langextract
- OpenMed: https://github.com/maziyarpanahi/openmed
- ClinicalNLP 2026 workshop: https://aclanthology.org/events/clinicalnlp-2026/
- Systematic review (ACM TCH): https://dl.acm.org/doi/10.1145/3744660
- LLMs struggle in token-level clinical NER: https://pmc.ncbi.nlm.nih.gov/articles/PMC12099373/
- n2c2: https://n2c2.dbmi.hms.harvard.edu/
- SapBERT: https://aclanthology.org/2021.naacl-main.334/ ; https://github.com/cambridgeltl/sapbert
- Biomedical entity linking evaluation: https://pmc.ncbi.nlm.nih.gov/articles/PMC11097978/
- medspaCy: https://github.com/medspacy/medspacy
- SecTag: https://www.sciencedirect.com/science/article/abs/pii/S1067502709001583
- MedSecId: https://aclanthology.org/2022.coling-1.326/
- CNSight: https://arxiv.org/html/2512.22795
- Supervised vs zero-shot section segmentation (LREC 2026): https://arxiv.org/html/2602.17513v1
- LLM section identifiers, public vs real-world: https://arxiv.org/html/2404.16294v1
- MedSlice: https://arxiv.org/html/2501.14105v1
- Generalizable section identification (JAMIA Open 2024): https://academic.oup.com/jamiaopen/article/7/3/ooae075/7727366
- TRACE note bloat reduction: https://arxiv.org/html/2604.16364
