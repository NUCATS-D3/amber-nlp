"""Any ``Extractor`` as an MLflow pyfunc model (OSS API only; ``tracking`` extra).

The model holds no code by value: its ``model_config`` names, by import path, a zero-argument
factory returning the extractor, the ``VariableSet``, and optionally a zero-argument factory
returning the ``PolicyGate`` (default: ``StaticGate(DEFAULT_MATRIX)``), plus the sensitivity
it serves, the policy version, and the dataset manifest hash for run provenance.

At load, the extractor must declare every capability the variable set needs (``mentions``
for mention-level variables, ``note_claims`` for note-level ones; patient-level sets are
refused), and the gate must permit ``source_text`` to each of the extractor's destinations
at the configured sensitivity. At predict, every case must have that sensitivity and name
its source and the configured variable set, and every result is reloaded at amber's boundary
before it is returned. Each row of the input has ``case`` and ``source`` columns of JSON
(the model signature); each prediction is a ``CaseResult`` as JSON. A prediction call is one run:
its ``run_id`` comes from ``params["run_id"]`` or a new ULID. Errors are
``DeploymentError`` with a source-free code.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import mlflow.pyfunc
    from mlflow.models import ModelSignature
    from mlflow.pyfunc import PythonModel
    from mlflow.types import ColSpec, ParamSchema, ParamSpec, Schema
except ModuleNotFoundError as exc:
    if exc.name is None or not exc.name.startswith("mlflow"):
        raise
    raise ImportError(
        "MLflow is optional. Install tracking dependencies with: uv sync --extra tracking"
    ) from exc

from amber.cases import CaseValidationError, load_case_result
from amber.extraction import ExtractorCapability
from amber.graph_errors import GraphValidationError
from amber.ids import new_ulid
from amber.interfaces import Extractor, PolicyGate
from amber.policy import DEFAULT_MATRIX, DataPolicy, RunContext, StaticGate
from amber.schemas import Case, CaseResult, Sensitivity, Source, VariableSet

_CONFIG_KEYS = {
    "extractor",
    "variables",
    "gate",
    "sensitivity",
    "policy_version",
    "dataset_manifest_hash",
}


class DeploymentError(ValueError):
    """A model that cannot load or an input it cannot serve; the message is the code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _imported(path: Any) -> Any:
    if not isinstance(path, str) or path.count(":") != 1:
        raise DeploymentError("invalid_configuration")
    module, attribute = path.split(":")
    try:
        return getattr(importlib.import_module(module), attribute)
    except (ImportError, AttributeError):
        raise DeploymentError("unavailable_import") from None


def needed_capabilities(variables: VariableSet) -> frozenset[ExtractorCapability]:
    """What an extractor must declare to answer ``variables``."""
    levels = {variable.level for variable in variables.variables.values()}
    if "patient" in levels:
        raise DeploymentError("unsupported_variable_level")
    needed: set[ExtractorCapability] = set()
    if "mention" in levels:
        needed.add("mentions")
    if "note" in levels:
        needed.add("note_claims")
    return frozenset(needed)


class ExtractorModel(PythonModel):  # type: ignore[misc]
    """The pyfunc wrapper; build it with ``save_extractor_model`` or ``log_extractor_model``."""

    def load_context(self, context: Any) -> None:
        config = dict(context.model_config or {})
        self.configure(config)

    def configure(self, config: Mapping[str, Any]) -> None:
        if (
            not {"extractor", "variables", "sensitivity", "policy_version"} <= set(config)
            or set(config) - _CONFIG_KEYS
        ):
            raise DeploymentError("invalid_configuration")
        variables = _imported(config["variables"])
        if not isinstance(variables, VariableSet):
            raise DeploymentError("invalid_configuration")
        factory = _imported(config["extractor"])
        extractor = factory() if callable(factory) else None
        if not isinstance(extractor, Extractor):
            raise DeploymentError("not_an_extractor")
        gate_factory = _imported(config["gate"]) if config.get("gate") else None
        gate = StaticGate(DEFAULT_MATRIX) if gate_factory is None else gate_factory()
        if not isinstance(gate, PolicyGate):
            raise DeploymentError("not_a_policy_gate")
        try:
            sensitivity = Sensitivity(config["sensitivity"])
        except ValueError:
            raise DeploymentError("invalid_configuration") from None
        if not needed_capabilities(variables) <= extractor.capabilities:
            raise DeploymentError("missing_capability")
        data = DataPolicy(sensitivity=sensitivity)
        now = datetime.now(UTC)
        for destination in sorted(extractor.destinations, key=lambda item: item.name):
            if not gate.check(data, destination, "source_text", now=now).permitted:
                raise DeploymentError("destination_not_permitted")
        self.extractor: Extractor = extractor
        self.variables = variables
        self.gate = gate
        self.data = data
        self.policy_version = str(config["policy_version"])
        self.manifest_hash = config.get("dataset_manifest_hash")

    def _rows(self, model_input: Any) -> list[tuple[Case, Source]]:
        if hasattr(model_input, "to_dict"):
            model_input = model_input.to_dict(orient="records")
        if isinstance(model_input, Mapping):
            model_input = [model_input]
        if not isinstance(model_input, Sequence) or isinstance(model_input, str):
            raise DeploymentError("invalid_input")
        rows: list[tuple[Case, Source]] = []
        for row in model_input:
            if not isinstance(row, Mapping) or set(row) != {"case", "source"}:
                raise DeploymentError("invalid_input")
            try:
                values = [
                    json.loads(row[key]) if isinstance(row[key], str) else row[key]
                    for key in ("case", "source")
                ]
                case, source = Case.model_validate(values[0]), Source.model_validate(values[1])
            except (TypeError, ValueError):
                raise DeploymentError("invalid_input") from None
            if case.sensitivity is not self.data.sensitivity:
                raise DeploymentError("sensitivity_not_served")
            if case.task != self.variables.name or source.source_id not in case.source_ids:
                raise DeploymentError("case_mismatch")
            rows.append((case, source))
        return rows

    # model_input is left unannotated: the explicit signature, not type hints, defines it.
    def predict(self, context, model_input, params=None):  # type: ignore[no-untyped-def]
        rows = self._rows(model_input)
        run_id = (params or {}).get("run_id") or new_ulid()
        results: list[CaseResult] = []
        for case, source in rows:
            run = RunContext(
                run_id=run_id,
                policy_version=self.policy_version,
                data=self.data,
                dataset=case.dataset,
                dataset_manifest_hash=self.manifest_hash,
            )
            result = self.extractor.run(
                case, source, self.variables, gate=self.gate, budget=None, context=run
            )
            try:
                load_case_result(
                    result.model_dump(mode="json"),
                    case=case,
                    source=source,
                    variables=self.variables,
                )
            except (CaseValidationError, GraphValidationError) as error:
                raise DeploymentError(f"invalid_result:{error.code}") from None
            results.append(result)
        return [result.model_dump_json() for result in results]


def model_config(
    *,
    extractor: str,
    variables: str,
    sensitivity: Sensitivity,
    policy_version: str,
    gate: str | None = None,
    dataset_manifest_hash: str | None = None,
) -> dict[str, Any]:
    """The ``model_config`` for an extractor model, checked by loading it once."""
    config = {
        "extractor": extractor,
        "variables": variables,
        "sensitivity": sensitivity.value,
        "policy_version": policy_version,
        **({"gate": gate} if gate else {}),
        **({"dataset_manifest_hash": dataset_manifest_hash} if dataset_manifest_hash else {}),
    }
    ExtractorModel().configure(config)
    return config


SIGNATURE = ModelSignature(
    inputs=Schema([ColSpec("string", "case"), ColSpec("string", "source")]),
    outputs=Schema([ColSpec("string")]),
    params=ParamSchema([ParamSpec("run_id", "string", "")]),
)


def _requirements() -> list[str]:
    import amber

    return [f"amber[tracking]=={amber.__version__}"]


def save_extractor_model(
    path: Path | str, config: Mapping[str, Any], *, pip_requirements: list[str] | None = None
) -> None:
    """Save an extractor model directory (``model_config`` from ``model_config``)."""
    mlflow.pyfunc.save_model(
        str(path),
        python_model=ExtractorModel(),
        model_config=dict(config),
        signature=SIGNATURE,
        pip_requirements=pip_requirements if pip_requirements is not None else _requirements(),
    )


def log_extractor_model(
    name: str, config: Mapping[str, Any], *, pip_requirements: list[str] | None = None
) -> Any:
    """Log an extractor model to the active run; returns MLflow's ``ModelInfo``."""
    return mlflow.pyfunc.log_model(
        name=name,
        python_model=ExtractorModel(),
        model_config=dict(config),
        signature=SIGNATURE,
        pip_requirements=pip_requirements if pip_requirements is not None else _requirements(),
    )


__all__ = [
    "DeploymentError",
    "ExtractorModel",
    "log_extractor_model",
    "model_config",
    "needed_capabilities",
    "save_extractor_model",
]
