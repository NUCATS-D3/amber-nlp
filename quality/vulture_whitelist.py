"""Names vulture reports as unused that are not dead (amber-7q0.6). Protected: only a human adds
an entry, with the reason. Vulture counts each name below as used; nothing imports this file."""

from amber.bundles.core import ManifestSources
from amber.conformance.golden import write_golden
from amber.conformance.report import ConformanceReport
from amber.evaluation import ReviewEffort
from amber.mlflow_ext.context import AmberRunContextProvider
from amber.mlflow_ext.deployment import ExtractorModel
from amber.providers import ProviderRequest, ProviderResponse
from amber.regression import MetricComparison
from amber.schemas.mentions import Concept
from amber.schemas.sources import Section, SourceKind

_ = (
    # Contract fields and enum members: written to the wire and read by consumers, not by amber.
    ManifestSources.text_hash_algorithm,
    ConformanceReport.amber_version,
    ConformanceReport.catalog_version,
    ReviewEffort.idle_seconds,
    ProviderRequest.timeout_seconds,
    ProviderResponse.cost_estimate,
    MetricComparison.baseline_support,
    MetricComparison.current_support,
    Concept.system,
    Section.is_template,
    SourceKind.medication,
    SourceKind.problem,
    SourceKind.imaging,
    # Plugin hooks MLflow calls: the run-context entry point and PythonModel.load_context.
    AmberRunContextProvider,
    ExtractorModel.load_context,
    # Maintainer tool: the contract-change skill regenerates golden records with it.
    write_golden,
)
