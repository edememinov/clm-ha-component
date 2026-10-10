"""Authority derived from authenticated platform context only."""

from dataclasses import dataclass, replace
from time import monotonic


class Denied(Exception):
    """A neutral denial without object or credential details."""


@dataclass(frozen=True)
class RunScope:
    actor: str
    bindings: frozenset[str]
    expires: float
    remaining: int = 20


class Authority:
    """Bounded, transient authority for integration-created ingress contexts."""

    def __init__(self, clock=monotonic):
        self._clock = clock
        self._runs: dict[str, RunScope] = {}

    def register(self, context_id: str, actor: str, bindings: list[str]) -> None:
        self._prune()
        if not actor or not context_id or len(bindings) > 20 or len(self._runs) >= 100:
            raise Denied()
        self._runs[context_id] = RunScope(actor, frozenset(bindings), self._clock() + 60)

    def resolve(self, user_id: str | None, parent_id: str | None, binding: str) -> str:
        if user_id:
            return user_id
        self._prune()
        scope = self._runs.get(parent_id or "")
        if scope is None or binding not in scope.bindings or scope.remaining <= 0:
            raise Denied()
        self._runs[parent_id] = replace(scope, remaining=scope.remaining - 1)
        return scope.actor

    def remove(self, context_id: str) -> None:
        self._runs.pop(context_id, None)

    def _prune(self) -> None:
        self._runs = {key: value for key, value in self._runs.items() if value.expires > self._clock()}
