"""commit_claim(...) -> Claim and no_claim(reason). Validates against the task's AnswerModel,
requires evidence (per claim or per field), records InferenceEvidence from the current
trace, checks acyclicity. The only way an agent produces output.
"""
