"""search(patient_id, query, kinds) -> list[Hit]. v1: sections/chunks of the current note
(BM25); v2: a patient's sources (BM25 + embeddings). Hits are candidates, not evidence.
"""
