# Tests and CI

Keep the suite flat and grouped by the boundary under test:

- `test_interfaces.py`: core imports, the Python facade, and CLI.
- `test_config.py`: environment/dotenv precedence and cached settings.
- `test_answer_schemas.py`: strict answer validation, serialization, and frozen/closed value objects.
- `test_claim_schemas.py`: guarded claim prerequisites, task/inference/edge fields, canonical
  identifiers, nested immutability, and JSON wire shapes.
- `test_api.py`: optional HTTP health/info routes; requires `app` and HTTPX.
- `test_tracking.py`: optional MLflow entry point, missing dependencies, and the local launcher.
- `test_invariants.py`: the implemented source, ID, and exact-grounding invariants.
- `test_coral_brat.py` and `test_coral_audit.py`: parser integrity, preserved source text, safe
  aggregate diagnostics, and explicit local audit exports; invented BRAT and temporary files only.
- `test_coral_current_progression.py`: non-authoritative candidate rules, warning/blocker
  precedence, annotation completeness, and relevant span/skip safety using invented BRAT.
- `test_coral_current_progression_manifest.py`: deterministic quotas and patient-level splits,
  byte hashes, restricted output paths, immutable reruns, and atomic publication using invented
  dataset trees only.

## Local checks

From the repository root:

```sh
uv sync --locked --extra dev --extra app --extra tracking
uv run --no-sync pytest --strict-markers -ra -m "not slow and not gpu and not mlx"
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src
uv lock --check
```

Without the optional extras, HTTP and installed-MLflow tests may skip. Install both extras for
the full suite; a skipped interface test is not evidence that its integration works. No test
starts a server, calls a model, or reads credentialed CORAL data.

Modules that use settings opt into `isolated_settings` from [conftest.py](conftest.py) with
`pytest.mark.usefixtures`. The fixture removes inherited `AMBER_*` variables, changes into a
temporary directory without a caller-owned `.env`, and clears `get_settings()` before and after
each test. Environment and working directory changes are restored on teardown. Use
`Settings(_env_file=None, ...)` for explicit settings; dotenv tests write invented values to
temporary files. Other tests keep their normal working directory.

## CI

[GitHub Actions](../.github/workflows/ci.yml) runs on pushes and pull requests, using Python 3.11
and 3.12 on Linux. Jobs install from `uv.lock`, with no dataset credentials or external services:

- Core installation: install non-editably without extras or default dependency groups, then run
  [check_core_install.py](../scripts/check_core_install.py) with `python -I`. This checks the
  installed package location, absence of optional stacks, facade, Unicode/CRLF grounding, and CLI.
- Full suite: install `dev`, `app`, and `tracking` plus the default dev group. Explicitly import
  the optional interfaces before testing so a missing dependency fails the job rather than
  skipping its tests. Run the synthetic CPU suite on both Python versions; run Ruff, formatting,
  and mypy on the 3.11 lint/type-check target.

Package installation requires network access; test execution requires no network. Slow/model and
hardware-marked tests are excluded. Clinical corpus audits remain a separate local operation.

To reproduce the core check without replacing your development environment:

```sh
core_check_env="$(mktemp -d)/venv"
UV_PROJECT_ENVIRONMENT="$core_check_env" uv sync --locked --no-default-groups --no-editable
UV_PROJECT_ENVIRONMENT="$core_check_env" uv run --no-sync python -I scripts/check_core_install.py
```
