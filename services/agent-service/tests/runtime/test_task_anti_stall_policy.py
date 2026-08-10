from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load_module():
    path = ROOT / "skill-system" / "controller" / "anti_stall.py"
    spec = importlib.util.spec_from_file_location("task_anti_stall_policy_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_remote_call_budget_stops_third_connector_call() -> None:
    module = _load_module()
    policy = module.AtomicToolPolicy(max_remote_calls=2)

    policy.authorize("github.fetch_file")
    policy.record_success("github.fetch_file")
    policy.authorize("github.fetch_file")

    with pytest.raises(module.RemoteCallBudgetExceeded):
        policy.authorize("github.search")

    assert policy.snapshot()["remote_calls"] == 2


def test_local_cache_reads_do_not_consume_remote_budget() -> None:
    module = _load_module()
    policy = module.AtomicToolPolicy(max_remote_calls=1)

    for _ in range(20):
        policy.authorize("local.snapshot.read", remote=False)
    policy.authorize("github.fetch_file")

    assert policy.snapshot()["remote_calls"] == 1


def test_one_failed_remote_path_opens_circuit_for_current_step() -> None:
    module = _load_module()
    policy = module.AtomicToolPolicy(max_remote_calls=4, failure_threshold=1)

    policy.authorize("github.search")
    policy.record_failure("github.search", "empty_result")

    with pytest.raises(module.ToolCircuitOpen):
        policy.authorize("github.search")

    snapshot = policy.snapshot()
    assert snapshot["circuits"]["github.search"]["state"] == "open"
    assert snapshot["circuits"]["github.search"]["last_failure_kind"] == "empty_result"


def test_open_source_allows_one_explicit_different_fallback() -> None:
    module = _load_module()
    policy = module.AtomicToolPolicy(max_remote_calls=4)

    policy.authorize("github.search")
    policy.record_failure("github.search", "timeout")
    policy.claim_fallback("github.search", "github.fetch_file")
    policy.authorize("github.fetch_file")

    with pytest.raises(ValueError):
        policy.claim_fallback("github.search", "github.fetch_file")
    with pytest.raises(ValueError):
        policy.claim_fallback("github.search", "github.search")


def test_failure_does_not_poison_independent_tool_path() -> None:
    module = _load_module()
    policy = module.AtomicToolPolicy(max_remote_calls=3)

    policy.authorize("github.search")
    policy.record_failure("github.search", "503")
    policy.claim_fallback("github.search", "github.fetch_file")
    policy.authorize("github.fetch_file")
    policy.record_success("github.fetch_file")

    assert policy.snapshot()["circuits"]["github.fetch_file"]["state"] == "closed"
