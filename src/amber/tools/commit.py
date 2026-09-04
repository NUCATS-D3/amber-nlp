"""commit_claim(...) -> Claim; no_claim(...) -> CaseOutcome. Validates the task's AnswerModel,
field evidence, case scope, and the full source-backed support DAG. Shared by fixed pipelines
and agents; explicit unanswered outcomes never mint null-valued clinical claims.
"""
