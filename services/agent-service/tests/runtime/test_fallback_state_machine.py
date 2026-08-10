from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))


def _load_module():
    path = CONTROLLER / "fallback_state.py"
    spec = importlib.util.spec_from_file_location("fallback_state_machine_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cache_hit_uses_no_remote_budget() -> None:
    module = _load_module()
    machine = module.AtomicFallbackStateMachine()

    machine.begin_cache_lookup()
    machine.cache_hit()
    machine.analysis_completed()

    assert machine.snapshot()["state"] == "DONE"
    assert machine.snapshot()["policy"]["remote_calls"] == 0


def test_primary_success_uses_one_remote_call_then_local_analysis() -> None:
    module = _load_module()
    machine = module.AtomicFallbackStateMachine()

    machine.begin_cache_lookup()
    machine.cache_miss("github.fetch_file")
    assert machine.authorize_remote() == "github.fetch_file"
    machine.remote_succeeded()
    machine.cache_stored()
    machine.analysis_completed()

    snapshot = machine.snapshot()
    assert snapshot["state"] == "DONE"
    assert snapshot["policy"]["remote_calls"] == 1


def test_primary_failure_may_use_one_different_fallback() -> None:
    module = _load_module()
    machine = module.AtomicFallbackStateMachine()

    machine.begin_cache_lookup()
    machine.cache_miss("github.search")
    machine.authorize_remote()
    machine.remote_failed("timeout", fallback_tool="github.fetch_file")

    assert machine.snapshot()["state"] == "FALLBACK"
    assert machine.authorize_remote() == "github.fetch_file"
    machine.remote_succeeded()
    machine.cache_stored()
    machine.analysis_completed()

    snapshot = machine.snapshot()
    assert snapshot["state"] == "DONE"
    assert snapshot["policy"]["remote_calls"] == 2
    assert snapshot["fallback_tool"] == "github.fetch_file"


def test_same_tool_retry_is_not_a_fallback() -> None:
    module = _load_module()
    machine = module.AtomicFallbackStateMachine()

    machine.begin_cache_lookup()
    machine.cache_miss("github.search")
    machine.authorize_remote()

    with pytest.raises(ValueError):
        machine.remote_failed("empty_result", fallback_tool="github.search")


def test_fallback_failure_stops_instead_of_finding_third_path() -> None:
    module = _load_module()
    machine = module.AtomicFallbackStateMachine()

    machine.begin_cache_lookup()
    machine.cache_miss("github.search")
    machine.authorize_remote()
    machine.remote_failed("503", fallback_tool="github.fetch_file")
    machine.authorize_remote()
    machine.remote_failed("timeout")

    snapshot = machine.snapshot()
    assert snapshot["state"] == "STOPPED"
    assert snapshot["policy"]["remote_calls"] == 2
    assert any(item["event"] == "fallback_exhausted" for item in snapshot["history"])


def test_invalid_transition_fails_closed() -> None:
    module = _load_module()
    machine = module.AtomicFallbackStateMachine()

    with pytest.raises(module.InvalidFallbackTransition):
        machine.authorize_remote()
    with pytest.raises(module.InvalidFallbackTransition):
        machine.analysis_completed()
