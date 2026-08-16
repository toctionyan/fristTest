from __future__ import annotations

import pytest


_NESTED_CONTROLLER_ENV_ISOLATION_TESTS = {
    "test_quality_controller_lock_rejection_is_machine_readable",
}


@pytest.fixture(autouse=True)
def isolate_nested_quality_controller_from_parent_judge_binding(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep fake-workspace controller probes independent of the outer CI Judge.

    The governed adoption Quick loop intentionally exports ``SKILL_JUDGE_ROOT`` and
    ``SKILL_JUDGE_TRUST_MODE=external-readonly`` for the real top-level controller.
    The lock-rejection test calls ``quality_loop.main()`` again in-process against a
    synthetic ``tmp_path`` workspace after monkeypatching ``run_loop``.  Inheriting
    the outer binding makes that nested probe fail the trusted-Judge preflight before
    it can exercise the lock-rejection branch, so the test observes empty stdout.

    Clear the binding only for that synthetic nested probe.  Pytest's monkeypatch
    restores the real process environment immediately after the test, so the outer
    Quick controller and every other gate retain fail-closed external-Judge binding.
    """
    if request.node.name not in _NESTED_CONTROLLER_ENV_ISOLATION_TESTS:
        return
    monkeypatch.delenv("SKILL_JUDGE_ROOT", raising=False)
    monkeypatch.delenv("SKILL_JUDGE_TRUST_MODE", raising=False)
