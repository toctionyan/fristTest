from __future__ import annotations

from typing import Any, Mapping


RECOVERY_POLICY_SCHEMA = "engineering-failure-recovery-policy@1"

AUTO_REPAIR = "AUTO_REPAIR"
AUTO_RETRY = "AUTO_RETRY"
AUTO_DIAGNOSE = "AUTO_DIAGNOSE"
WAIT_EXTERNAL = "WAIT_EXTERNAL"
HUMAN_REQUIRED = "HUMAN_REQUIRED"

_REPAIRABLE_CLASSES = {
    "PRODUCT_CODE_REPAIRABLE",
    "CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE",
}
_TRANSIENT_CLASSES = {"TRANSIENT_INFRA_RETRYABLE"}
_ENVIRONMENT_CLASSES = {"ENVIRONMENT_BLOCKED"}
_HUMAN_CLASSES = {
    "AUTHORITY_ORACLE_CHANGE_REQUIRED",
    "HUMAN_GATE",
    "TEST_HARNESS_REPAIRABLE",
}


def _text(value: object) -> str:
    return str(value or "").strip()


def decide_recovery(
    *,
    repair_route: Mapping[str, Any] | None,
    classification: str,
    diagnosis_attempt: int = 0,
    max_diagnosis_attempts: int = 2,
    retry_count: int = 0,
    max_retry_count: int = 3,
) -> dict[str, Any]:
    """Return the next recovery disposition without acquiring any authority.

    A failed attempt is not automatically a user interruption. Repairable failures
    continue only when a pre-existing route already carries exact write authority;
    transient failures retry the same candidate; unknown evidence receives a bounded
    read-only diagnostic pass before the task is handed to a human. Oracle, baseline,
    acceptance, test-harness, and explicit Human Gates always remain human-owned.
    """

    route = dict(repair_route or {})
    repair_class = _text(route.get("repair_class")).upper()
    normalized = _text(classification).casefold()
    diagnostic_attempt = int(diagnosis_attempt)
    diagnostic_budget = int(max_diagnosis_attempts)
    retries = int(retry_count)
    retry_budget = int(max_retry_count)
    if diagnostic_attempt < 0 or diagnostic_budget < 0:
        raise ValueError("diagnosis attempts must be non-negative")
    if retries < 0 or retry_budget < 0:
        raise ValueError("retry counts must be non-negative")

    disposition = HUMAN_REQUIRED
    reason = "failure requires a user-owned decision"
    source_write_allowed = False
    retry_allowed = False
    diagnostic_allowed = False
    human_required = True

    if repair_class in _REPAIRABLE_CLASSES:
        if route.get("automatic_write_allowed") is True and route.get("human_required") is False:
            disposition = AUTO_REPAIR
            reason = "trusted repair route already carries an exact bounded write scope"
            source_write_allowed = True
            human_required = False
        else:
            reason = "repair class exists but exact automatic write authority is absent"
    elif repair_class in _TRANSIENT_CLASSES or normalized in {
        "timeout",
        "cancelled",
        "canceled",
        "runner_or_platform",
        "stale",
    }:
        if retries < retry_budget:
            disposition = AUTO_RETRY
            reason = "transient failure may retry the exact same candidate within retry budget"
            retry_allowed = True
            human_required = False
        else:
            reason = "transient retry budget is exhausted"
    elif repair_class in _ENVIRONMENT_CLASSES or normalized == "environment":
        disposition = WAIT_EXTERNAL
        reason = "external environment must recover; source changes are not authorized"
        human_required = False
    elif repair_class in _HUMAN_CLASSES or normalized in {
        "protected_baseline_drift",
        "policy_or_approval",
        "production_diagnostic",
        "test_defect",
    }:
        disposition = HUMAN_REQUIRED
        reason = "failure crosses a protected oracle, acceptance, baseline, test, or Human Gate boundary"
    elif repair_class == "UNKNOWN" or normalized in {
        "unknown",
        "unknown_failure_without_gate_evidence",
        "",
    }:
        if diagnostic_attempt < diagnostic_budget:
            disposition = AUTO_DIAGNOSE
            reason = "evidence is insufficient for writes; continue with bounded read-only diagnosis"
            diagnostic_allowed = True
            human_required = False
        else:
            reason = "read-only diagnosis budget is exhausted without a safe repair route"
    elif normalized == "code_or_contract" and route.get("automatic_write_allowed") is True:
        disposition = AUTO_REPAIR
        reason = "code/contract failure has an existing bounded automatic write route"
        source_write_allowed = True
        human_required = False

    return {
        "schema": RECOVERY_POLICY_SCHEMA,
        "disposition": disposition,
        "human_required": human_required,
        "source_write_allowed": source_write_allowed,
        "retry_allowed": retry_allowed,
        "diagnostic_allowed": diagnostic_allowed,
        "diagnosis_attempt": diagnostic_attempt,
        "max_diagnosis_attempts": diagnostic_budget,
        "retry_count": retries,
        "max_retry_count": retry_budget,
        "reason": reason,
        "authority_effect": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
