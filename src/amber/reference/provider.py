"""``ScriptedProvider``: the reference ``Provider``, returning canned outcomes offline."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeAlias

from amber.policy import ProviderDestination
from amber.providers import ProviderFailure, ProviderRequest, ProviderResponse

Scripted: TypeAlias = (
    ProviderResponse
    | ProviderFailure
    | Callable[[ProviderRequest], ProviderResponse | ProviderFailure]
)


class ScriptedProvider:
    """Answers each request with the next scripted outcome; records every request.

    A request needing a capability the destination does not declare is refused with
    ``capability_missing`` and consumes no scripted outcome. An exhausted script answers
    ``unavailable``.
    """

    def __init__(self, destination: ProviderDestination, script: Iterable[Scripted]) -> None:
        if not isinstance(destination, ProviderDestination):
            raise TypeError("ScriptedProvider needs a ProviderDestination")
        self.destination = destination
        self._script = list(script)
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse | ProviderFailure:
        self.requests.append(request)
        if request.required_capabilities() - self.destination.capabilities:
            return ProviderFailure(kind="provider", reason="capability_missing")
        if not self._script:
            return ProviderFailure(kind="provider", reason="unavailable")
        step = self._script.pop(0)
        return step(request) if callable(step) else step
