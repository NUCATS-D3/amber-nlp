"""Validation errors carry only their documented codes (amber-u4c, mutation testing)."""

from __future__ import annotations

from typing import Any, get_args

import pytest

from amber.annotations import AnnotationValidationCode, AnnotationValidationError
from amber.cases import CaseValidationCode, CaseValidationError
from amber.examples import ExampleValidationCode, ExampleValidationError
from amber.graph_errors import GraphValidationCode, GraphValidationError

ERRORS = [
    (GraphValidationError, GraphValidationCode),
    (CaseValidationError, CaseValidationCode),
    (ExampleValidationError, ExampleValidationCode),
    (AnnotationValidationError, AnnotationValidationCode),
]


@pytest.mark.parametrize(("error", "codes"), ERRORS)
def test_every_documented_code_is_accepted(error: Any, codes: Any) -> None:
    for code in get_args(codes):
        assert error(code).code == code and str(error(code)) == code


@pytest.mark.parametrize(("error", "codes"), ERRORS)
@pytest.mark.parametrize("code", [None, "", "INVALID_SCHEMA", "not_a_code"])
def test_an_undocumented_code_is_a_programming_error(error: Any, codes: Any, code: Any) -> None:
    with pytest.raises(TypeError, match=f"^unknown {error.__name__} code$"):
        error(code)
