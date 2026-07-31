"""Small module-internal adapter used by one-tool executors."""
from __future__ import annotations
from typing import Any, Callable


def _engine():
    from agent_modules.ecommerce import _shared_execution
    return _shared_execution


def execute_one(state: dict[str, Any], tool_name: str, runner: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    # The facade selects only a fixed public capability.  The generic runtime
    # outcome projector belongs to the dedicated write/transaction slice.
    from agent_modules.ecommerce.shared.prepare_actions import _runtime_result
    engine = _engine()
    return _runtime_result(state, tool_name, runner(engine))
