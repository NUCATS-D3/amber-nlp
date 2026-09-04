"""The grounding kernel: exact and fuzzy alignment of text to a Source, returning an
Inclusion (a verified span) or a GroundingFailure. This module is the only minter of
Inclusions (besides the annotation app acting for a human).
"""
