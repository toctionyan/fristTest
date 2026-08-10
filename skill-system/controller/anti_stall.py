from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class RemoteCallBudgetExceeded(RuntimeError):
    """Raised when one atomic step tries to exceed its remote-call budget."""


class ToolCircuitOpen(RuntimeError):
    """Raised when a tool has already failed for the current atomic step."""


@dataclass
class RemoteCallBudget:
    """Hard cap for remote calls inside one atomic task step.

    Local/cache operations never consume this budget.  The default of two keeps
    one step small enough to report a checkpoint instead of building a long,
    fragile chain of connector calls.
    """

    max_remote_calls: int = 2
    consumed: int = 0
    history: list[str] = field(default_factory=list)

    def acquire(self, tool_name: str, *, remote: bool = True) -> None:
        if not remote:
            return
        if self.consumed >= self.max_remote_calls:
            raise RemoteCallBudgetExceeded(
                f"atomic remote-call budget exhausted ({self.consumed}/{self.max_remote_calls}); "
                "save progress and continue in the next atomic step"
            )
        self.consumed += 1
        self.history.append(str(tool_name))


@dataclass
class ToolCircuit:
    """Step-scoped circuit breaker for one remote tool path."""

    failure_threshold: int = 1
    failures: int = 0
    state: str = "closed"
    last_failure_kind: str | None = None

    def before_call(self, tool_name: str) -> None:
        if self.state == "open":
            detail = f" after {self.last_failure_kind}" if self.last_failure_kind else ""
            raise ToolCircuitOpen(f"tool circuit is open for {tool_name}{detail}; use the declared fallback")

    def record_success(self) -> None:
        self.failures = 0
        self.state = "closed"
        self.last_failure_kind = None

    def record_failure(self, failure_kind: str) -> None:
        self.failures += 1
        self.last_failure_kind = str(failure_kind or "failure")
        if self.failures >= self.failure_threshold:
            self.state = "open"


@dataclass
class AtomicToolPolicy:
    """Shared anti-stall policy for one atomic execution step.

    The policy intentionally does not execute tools.  It constrains whichever
    task harness owns execution, so it can be reused without creating a second
    Quality Loop or a parallel business runtime.
    """

    max_remote_calls: int = 2
    failure_threshold: int = 1
    budget: RemoteCallBudget = field(init=False)
    circuits: dict[str, ToolCircuit] = field(default_factory=dict)
    fallbacks_used: set[tuple[str, str]] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.budget = RemoteCallBudget(max_remote_calls=self.max_remote_calls)

    def _circuit(self, tool_name: str) -> ToolCircuit:
        name = str(tool_name)
        if name not in self.circuits:
            self.circuits[name] = ToolCircuit(failure_threshold=self.failure_threshold)
        return self.circuits[name]

    def authorize(self, tool_name: str, *, remote: bool = True) -> None:
        if remote:
            self._circuit(tool_name).before_call(str(tool_name))
        self.budget.acquire(str(tool_name), remote=remote)

    def record_success(self, tool_name: str) -> None:
        self._circuit(tool_name).record_success()

    def record_failure(self, tool_name: str, failure_kind: str) -> None:
        self._circuit(tool_name).record_failure(failure_kind)

    def claim_fallback(self, source_tool: str, fallback_tool: str) -> None:
        """Allow one explicit fallback edge after the source circuit opens."""
        source = str(source_tool)
        fallback = str(fallback_tool)
        if source == fallback:
            raise ValueError("fallback must use a different tool path")
        circuit = self._circuit(source)
        if circuit.state != "open":
            raise ValueError("fallback is allowed only after the source tool circuit opens")
        edge = (source, fallback)
        if edge in self.fallbacks_used:
            raise ValueError(f"fallback already consumed for {source} -> {fallback}")
        self.fallbacks_used.add(edge)

    def snapshot(self) -> dict[str, Any]:
        return {
            "remote_calls": self.budget.consumed,
            "remote_call_budget": self.budget.max_remote_calls,
            "history": list(self.budget.history),
            "circuits": {
                name: {
                    "state": circuit.state,
                    "failures": circuit.failures,
                    "last_failure_kind": circuit.last_failure_kind,
                }
                for name, circuit in sorted(self.circuits.items())
            },
            "fallbacks_used": [list(edge) for edge in sorted(self.fallbacks_used)],
        }
