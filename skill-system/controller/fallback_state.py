from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from anti_stall import AtomicToolPolicy


class InvalidFallbackTransition(RuntimeError):
    """Raised when an atomic remote attempt leaves the declared fallback flow."""


TERMINAL_STATES = {"SUCCEEDED", "STOPPED"}


@dataclass
class AtomicFallbackStateMachine:
    """Fail-closed orchestration for one primary remote path plus one fallback.

    Cache lookup/store and local analysis are owned by ``AntiStallTaskHarness``.
    This machine only governs remote-attempt transitions.
    """

    policy: AtomicToolPolicy = field(default_factory=AtomicToolPolicy)
    state: str = "READY"
    current_tool: str | None = None
    primary_tool: str | None = None
    fallback_tool: str | None = None
    failure_kind: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def _record(self, event: str, **details: Any) -> None:
        self.history.append({"event": event, "state": self.state, **details})

    def _require(self, *allowed: str) -> None:
        if self.state not in allowed:
            raise InvalidFallbackTransition(
                f"event is invalid from state {self.state}; expected one of {sorted(allowed)}"
            )

    def begin(self, primary_tool: str) -> None:
        self._require("READY")
        tool = str(primary_tool).strip()
        if not tool:
            raise ValueError("primary_tool must be non-empty")
        self.primary_tool = tool
        self.current_tool = tool
        self.state = "PRIMARY"
        self._record("primary_selected", tool=tool)

    def authorize_remote(self) -> str:
        self._require("PRIMARY", "FALLBACK")
        if not self.current_tool:
            raise InvalidFallbackTransition("remote state has no current tool")
        self.policy.authorize(self.current_tool)
        self._record("remote_authorized", tool=self.current_tool)
        return self.current_tool

    def remote_succeeded(self) -> None:
        self._require("PRIMARY", "FALLBACK")
        if not self.current_tool:
            raise InvalidFallbackTransition("remote success has no current tool")
        tool = self.current_tool
        self.policy.record_success(tool)
        self.current_tool = None
        self.state = "SUCCEEDED"
        self._record("remote_succeeded", tool=tool)

    def remote_failed(self, failure_kind: str, *, fallback_tool: str | None = None) -> None:
        self._require("PRIMARY", "FALLBACK")
        if not self.current_tool:
            raise InvalidFallbackTransition("remote failure has no current tool")
        failed_tool = self.current_tool
        kind = str(failure_kind or "failure")
        self.policy.record_failure(failed_tool, kind)
        self.failure_kind = kind
        self._record("remote_failed", tool=failed_tool, failure_kind=kind)

        if self.state == "FALLBACK":
            self.state = "STOPPED"
            self.current_tool = None
            self._record("fallback_exhausted", tool=failed_tool)
            return

        if fallback_tool is None:
            self.state = "STOPPED"
            self.current_tool = None
            self._record("no_fallback_declared", tool=failed_tool)
            return

        fallback = str(fallback_tool).strip()
        if not fallback:
            raise ValueError("fallback_tool must be non-empty when supplied")
        self.policy.claim_fallback(failed_tool, fallback)
        self.fallback_tool = fallback
        self.current_tool = fallback
        self.state = "FALLBACK"
        self._record("fallback_selected", source=failed_tool, fallback=fallback)

    def stop(self, reason: str) -> None:
        if self.state in TERMINAL_STATES:
            raise InvalidFallbackTransition(f"cannot stop terminal state {self.state}")
        self.failure_kind = str(reason or "stopped")
        self.current_tool = None
        self.state = "STOPPED"
        self._record("stopped", reason=self.failure_kind)

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "primary_tool": self.primary_tool,
            "fallback_tool": self.fallback_tool,
            "current_tool": self.current_tool,
            "failure_kind": self.failure_kind,
            "policy": self.policy.snapshot(),
            "history": [dict(item) for item in self.history],
        }
