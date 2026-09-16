"""Private immutable containers for JSON-shaped schema values."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, NoReturn

_MUTATION_ERROR = "frozen JSON values cannot be mutated"


def _reject_mutation(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise TypeError(_MUTATION_ERROR)


class _FrozenDict(dict[str, Any]):
    """A JSON-serializable dictionary whose contents cannot be changed."""

    def __init__(self, value: Mapping[str, Any]) -> None:
        if getattr(self, "_initialized", False):
            raise TypeError(_MUTATION_ERROR)
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        dict.__init__(self, ((key, freeze_json(item)) for key, item in value.items()))
        self._initialized = True

    __setitem__ = _reject_mutation
    __delitem__ = _reject_mutation
    clear = _reject_mutation
    pop = _reject_mutation
    popitem = _reject_mutation
    setdefault = _reject_mutation
    update = _reject_mutation
    __ior__ = _reject_mutation

    def __copy__(self) -> _FrozenDict:
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> _FrozenDict:
        return self


class _FrozenList(list[Any]):
    """A JSON-serializable list whose contents cannot be changed."""

    def __init__(self, value: list[Any]) -> None:
        if getattr(self, "_initialized", False):
            raise TypeError(_MUTATION_ERROR)
        list.__init__(self, (freeze_json(item) for item in value))
        self._initialized = True

    __setitem__ = _reject_mutation
    __delitem__ = _reject_mutation
    append = _reject_mutation
    clear = _reject_mutation
    extend = _reject_mutation
    insert = _reject_mutation
    pop = _reject_mutation
    remove = _reject_mutation
    reverse = _reject_mutation
    sort = _reject_mutation
    __iadd__ = _reject_mutation
    __imul__ = _reject_mutation

    def __copy__(self) -> _FrozenList:
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> _FrozenList:
        return self


def freeze_json(value: Any) -> Any:
    """Copy a JSON-shaped value into recursively immutable containers."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats cannot be frozen")
        return value
    if isinstance(value, Mapping):
        return _FrozenDict(value)
    if isinstance(value, list):
        return _FrozenList(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")
