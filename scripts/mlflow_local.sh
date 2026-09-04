#!/usr/bin/env bash
set -euo pipefail
mkdir -p .mlflow
exec uv run mlflow server --host 127.0.0.1 --port 5000 --backend-store-uri sqlite:///.mlflow/mlflow.db --artifacts-destination .mlflow/artifacts
