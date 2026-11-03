"""``AjccStub``: the reference ``Rule``, over a tiny invented staging table.

NOT CLINICAL. The classes and stages are made up for conformance tests and resemble no real
staging system's values or logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from amber.rules import RoleInput, RoleSpec, RuleResult, RuleSpec
from amber.schemas import Claim

# (size, node, spread) -> invented stage label.
STUB_TABLE: Mapping[tuple[str, str, str], str] = {
    ("S1", "N0", "M0"): "stub-I",
    ("S2", "N0", "M0"): "stub-II",
    ("S1", "N1", "M0"): "stub-II",
    ("S2", "N1", "M0"): "stub-III",
}


class AjccStub:
    """Derives ``stage_group`` from ``size_class`` (T), ``node_class`` (N), and
    ``spread_class`` (M, defaulting to ``M0`` when absent). A ``suffix`` parameter appends to the
    suffix, so parameters reach the rule; any combination outside the table is
    ``insufficient_evidence``.
    """

    spec = RuleSpec(
        name="ajcc_stub",
        version="0.1",
        roles={
            "T": RoleSpec(source="size_class"),
            "N": RoleSpec(source="node_class"),
            "M": RoleSpec(source="spread_class", on_missing="default"),
        },
        outputs="stage_group",
        defaults={"M": "M0"},
    )

    def apply(self, inputs: Mapping[str, RoleInput], parameters: Mapping[str, Any]) -> RuleResult:
        values = []
        for role in ("T", "N", "M"):
            item = inputs[role]
            values.append(item.value["value"] if isinstance(item, Claim) else item)
        stage = STUB_TABLE.get((values[0], values[1], values[2]))
        if stage is None:
            return RuleResult(
                status="insufficient_evidence", reason="Combination outside the stub table."
            )
        suffix = parameters.get("suffix")
        return RuleResult(value={"stage": stage if suffix is None else f"{stage}{suffix}"})
