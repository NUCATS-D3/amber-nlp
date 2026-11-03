"""Smoke-test a non-editable, core-only install: run with ``python -I``.

No pytest, dataset, model, or service is needed. An environment containing optional
stacks intentionally fails this check; use a separate minimal environment.
"""

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

import amber
from amber.cases import create_case_result, load_case_result
from amber.graph import EvidenceGraph
from amber.policy import DEFAULT_MATRIX, DataPolicy, Destination, check
from amber.schemas import (
    AnswerModel,
    Case,
    CaseOutcome,
    Inclusion,
    Provenance,
    Sensitivity,
    Source,
    SourceKind,
    Task,
    Zone,
)
from amber.tools.commit import commit_claim
from amber.tools.outcomes import no_claim
from amber.tools.quote import quote


class SyntheticFindingAnswer(AnswerModel):
    finding_present: bool = Field(strict=True)


def main() -> None:
    assert sys.flags.isolated, "Run this check with python -I"
    assert Path(amber.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), (
        "Amber must be installed non-editably in this environment"
    )
    optional = (
        "fastapi",
        "click",
        "mlflow",
        "pandas",
        "pyarrow",
        "duckdb",
        "numpy",
        "scipy",
        "sklearn",
        "pydantic_ai",
        "transformers",
        "mlx",
        "pytest",
        "marimo",
        "anthropic",
        "azure",
        "openai",
    )
    available = [name for name in optional if importlib.util.find_spec(name) is not None]
    assert not available, f"Expected a core-only environment; found {available}"

    assert amber.__version__
    source = Source.create(
        patient_id="synthetic-patient",
        kind=SourceKind.note,
        external_id="synthetic-note",
        datetime=None,
        text="Alpha βeta\r\n",
        record=None,
    )
    evidence = quote(source.source_id, "βeta\r\n", sources={source.source_id: source})
    assert isinstance(evidence, Inclusion)
    assert (evidence.start, evidence.end, evidence.quote) == (6, 12, "βeta\r\n")
    task = Task(
        name="synthetic_core_smoke",
        answer_model=SyntheticFindingAnswer,
        instructions="Exercise an invented evidence-backed answer.",
        evidence_policy="field",
    )
    run_id = "01K00000000000000000000000"
    provenance = Provenance(
        producer="tool:core-smoke@1",
        run_id=run_id,
        destination="local:core-smoke",
        policy_version="core-smoke-policy@1",
        zone=Zone.local,
        sensitivity=Sensitivity.synthetic,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        version="0.0.0",
    )
    graph = EvidenceGraph(source=source, task=task, sensitivity=Sensitivity.synthetic)
    graph.register_evidence(evidence)
    claim = commit_claim(
        task,
        {"finding_present": False},
        [evidence.evidence_id],
        "The invented exact quote is retained for structural smoke testing.",
        {"finding_present": [evidence.evidence_id]},
        graph=graph,
        provenance=provenance,
    )
    assert claim.status == "proposed"
    assert graph.source_leaves(claim.claim_id) == (evidence,)
    restored = EvidenceGraph.from_payload(
        graph.to_payload(),
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
    )
    restored.validate()
    assert restored.to_payload() == graph.to_payload()

    case = Case(
        case_id="synthetic-core-case",
        task=task.name,
        patient_id=source.patient_id,
        source_ids=[source.source_id],
        sensitivity=Sensitivity.synthetic,
    )
    result = create_case_result(
        case,
        graph=graph,
        run_id=run_id,
        final_claim_ids=[claim.claim_id],
        outcome=CaseOutcome(status="answered", provenance=provenance),
    )
    loaded_result = load_case_result(
        json.loads(result.model_dump_json()),
        case=case,
        source=source,
        task=task,
    )
    assert loaded_result == result
    assert loaded_result.final_claim_ids == [claim.claim_id]
    unanswered = no_claim(
        "insufficient_evidence",
        "Invented partial review only.",
        [],
        [],
        case=case,
        graph=graph,
        provenance=provenance,
    )
    diagnostic = create_case_result(
        case, graph=graph, run_id=run_id, outcome=unanswered, final_claim_ids=[]
    )
    assert diagnostic.final_claim_ids == []
    assert diagnostic.claims[0].value["finding_present"] is False

    external = Destination(
        name="synthetic-external",
        kind="model",
        zone=Zone.external,
        accepts=frozenset({"source_text"}),
    )
    refused = check(
        DEFAULT_MATRIX,
        DataPolicy(sensitivity=Sensitivity.phi),
        external,
        "source_text",
        now=datetime(2026, 1, 2, tzinfo=UTC),
    )
    assert (refused.permitted, refused.reason) == (False, "zone_not_allowed")

    # The conformance kit runs without pytest; only its pytest plugin needs the extra.
    from amber.conformance import Subject
    from amber.conformance.report import run_checks
    from amber.reference import InMemoryResolver, InMemoryStore

    report = run_checks(
        Subject(
            name="core-smoke",
            version="0",
            targets={"Store": InMemoryStore, "SourceResolver": InMemoryResolver},
        )
    )
    assert len(report.cases) == 101 and report.counts()["failed"] == 0
    assert report.counts()["passed"] > 0
    try:
        importlib.import_module("amber.conformance.plugin")
    except ImportError as error:
        assert "amber[conformance]" in str(error)
    else:
        raise AssertionError("the conformance plugin must require the conformance extra")
    assert "pytest" not in sys.modules

    # Bundles validate in memory without pyarrow; only Parquet files need the extra.
    from amber.bundles import (
        BundleContents,
        ManifestDataset,
        ManifestProducer,
        build_bundle,
        load_bundle,
    )
    from amber.conformance import fixtures

    bundle_case = fixtures.case(dataset=fixtures.DATASET)
    bundle = build_bundle(
        kind="run",
        variables=fixtures.VARIABLES,
        dataset=ManifestDataset(
            name=fixtures.DATASET, manifest_hash="core-smoke", sensitivity=Sensitivity.synthetic
        ),
        producer=ManifestProducer(name="core-smoke", version="0", run_id=fixtures.RUN_IDS[0]),
        policy_version="core-smoke-policy@1",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        sources=[fixtures.SOURCE],
        cases=[bundle_case],
        results=[fixtures.result(fixtures.RUN_IDS[0])],
    )
    loaded = load_bundle(
        bundle, variables=fixtures.VARIABLES, resolver=InMemoryResolver([fixtures.SOURCE])
    )
    assert isinstance(loaded, BundleContents) and len(loaded.results) == 1
    try:
        importlib.import_module("amber.bundles.parquet")
    except ImportError as error:
        assert "amber[bundles]" in str(error)
    else:
        raise AssertionError("Parquet bundles must require the bundles extra")
    assert "pyarrow" not in sys.modules
    print(
        "Core-only installed grounding, claim commit, case results, restore, policy, "
        "conformance-kit, and in-memory bundle checks passed."
    )


if __name__ == "__main__":
    main()
