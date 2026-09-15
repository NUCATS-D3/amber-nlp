# Experiments

Each folder contains one experiment: a run of Amber on a particular dataset, with its
scripts, evaluation code, local data, and outputs together. Use a descriptive name such as
`coral` for the initial pilot or `coral-fixed-baseline` for a later, distinct experiment.

```text
experiments/<experiment>/
  README.md       objective, dataset, protocol, configuration, and reproduction commands
  scripts/        experiment-specific preparation and execution scripts
  eval/           experiment-specific scorers and analysis code
  data/           local inputs, derived examples, and split manifests (Git-ignored)
  outputs/        predictions, metrics, reports, and run artifacts (Git-ignored)
```

Commit code, non-sensitive configuration, and documentation. Only `.gitkeep` placeholders
are tracked inside `data/` and `outputs/`; these ignore rules apply to every experiment.
Keep restricted data and reconstructable derivatives in those local directories under the
dataset's access restrictions. Synthetic repository fixtures remain in `examples/` or tests.

Document the task and acceptance criteria before running an experiment. Record the dataset
version/hash, patient/document split, adapter policy, Amber revision, prompt/model/backend
versions, budgets, and evaluation commands with each run. Keep repeated executions in separate
`outputs/<run-id>/` directories so results can be compared without overwriting earlier runs.

Experiment code calls Amber's public facade/services. Promote reusable behavior into
`src/amber/` with synthetic tests; core code must not import from `experiments/`.
Experiment-local logic can live in importable modules beside `scripts/`, as in CORAL's
`brat.py`; command launchers remain thin. Experiment modules are checkout-local, not part of
the distributed Amber package. Shared operational utilities, such as starting MLflow, remain
in the repository-level `scripts/`.
Run commands from the repository root using its `uv` environment. Corpus-dependent evaluation
is a separate local step and must not become a requirement for repository tests or CI.

The first experiment is [CORAL](coral/README.md). It currently contains an annotation audit
and a draft adapter; extraction and clinical evaluation are not implemented yet.
