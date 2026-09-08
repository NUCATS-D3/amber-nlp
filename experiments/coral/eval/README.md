# CORAL evaluation

Place experiment-specific scorers and analysis code here. Evaluation is not implemented yet;
first define the task and acceptance protocol described in the [experiment README](../README.md).

Score expert gold separately from pseudo-labels, split by patient/document before deriving
examples, and record dataset and adapter versions. Write generated metrics and reports to
`../outputs/<run-id>/`. Reusable evaluation behavior belongs in `src/amber/evaluate.py` with
synthetic tests; repository tests must run without CORAL access.
