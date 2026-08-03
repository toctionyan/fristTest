#!/usr/bin/env python3
"""Validate the B30 WP-02A TurnRequestLedger design contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_WORK_PACKAGE = "WP-02A"
REQUIRED_SCOPE = ["tenant_id", "user_id", "thread_id", "client_request_id"]
REQUIRED_STATES = {
    "CLAIMED", "RUNNING", "SUCCEEDED", "FAILED_RETRYABLE",
    "FAILED_FINAL", "SUBMISSION_UNKNOWN", "RECONCILING",
}
REQUIRED_DECISIONS = {
    "NEW_CLAIM", "REPLAY_SUCCEEDED", "REQUEST_IN_PROGRESS",
    "PAYLOAD_CONFLICT", "RECOVERY_REQUIRED", "REJECTED_FINAL",
}
REQUIRED_ORDER = [
    "authenticate_and_normalize_scope",
    "require_client_request_id",
    "acquire_conversation_lease",
    "claim_turn_request_ledger",
    "branch_on_claim_decision",
    "persist_user_message_with_ledger_message_id",
    "invoke_lifecycle_graph_with_turn_identity",
    "persist_public_response",
    "complete_ledger_with_response_digest",
    "return_or_emit_terminal_response",
]
REQUIRED_FORBIDDEN = {
    "server_generated_request_id_for_public_chat",
    "message_append_before_ledger_claim",
    "graph_invocation_before_ledger_claim",
    "automatic_reexecution_of_expired_running_record",
    "same_client_request_id_with_different_payload",
    "completion_without_owner_and_fencing_compare_and_set",
    "successful_return_before_ledger_completion",
}


class LedgerContractError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerContractError(f"invalid_json:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise LedgerContractError("ledger_contract_root_must_be_object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LedgerContractError(f"missing_or_empty:{label}")
    return value.strip()


def validate(contract_path: Path, doc_path: Path) -> None:
    contract = _read(contract_path)
    documentation = doc_path.read_text(encoding="utf-8")
    if (
        contract.get("schema_version") != 1
        or contract.get("stage") != "B30"
        or contract.get("work_package") != REQUIRED_WORK_PACKAGE
    ):
        raise LedgerContractError("schema_stage_or_work_package_invalid")

    authority = contract.get("authority")
    if not isinstance(authority, dict) or authority.get("owner") != "TurnRequestLedger":
        raise LedgerContractError("turn_request_ledger_authority_required")
    if authority.get("scope_key") != REQUIRED_SCOPE:
        raise LedgerContractError("scope_key_invalid")
    if authority.get("transport_excluded_from_digest") is not True:
        raise LedgerContractError("transport_must_be_excluded_from_digest")

    if set(contract.get("states") or []) != REQUIRED_STATES:
        raise LedgerContractError("state_set_invalid")
    if [str(item) for item in contract.get("operation_order") or []] != REQUIRED_ORDER:
        raise LedgerContractError("operation_order_invalid")

    decisions = {
        str(row.get("decision"))
        for row in contract.get("claim_decisions") or []
        if isinstance(row, dict)
    }
    if decisions != REQUIRED_DECISIONS:
        raise LedgerContractError("claim_decision_set_invalid")
    for row in contract.get("claim_decisions") or []:
        if not isinstance(row, dict):
            raise LedgerContractError("claim_decision_must_be_object")
        for field in ("decision", "condition", "action"):
            _text(row.get(field), f"claim_decision.{field}")

    transitions = contract.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        raise LedgerContractError("transitions_missing")
    pairs = {(row.get("from"), row.get("to")) for row in transitions if isinstance(row, dict)}
    if ("RUNNING", "RUNNING") in pairs:
        raise LedgerContractError("automatic_running_reexecution_forbidden")
    for required_pair in {
        ("NEW", "CLAIMED"), ("CLAIMED", "RUNNING"),
        ("RUNNING", "SUCCEEDED"), ("RUNNING", "SUBMISSION_UNKNOWN"),
        ("SUBMISSION_UNKNOWN", "RECONCILING"), ("RECONCILING", "SUCCEEDED"),
    }:
        if required_pair not in pairs:
            raise LedgerContractError(f"required_transition_missing:{required_pair}")

    api = contract.get("api_contract")
    if not isinstance(api, dict) or api.get("field") != "ChatRequest.client_request_id":
        raise LedgerContractError("chat_request_client_request_id_required")
    if api.get("required") is not True or api.get("server_generated_fallback_allowed") is not False:
        raise LedgerContractError("client_request_id_must_be_required_without_fallback")

    forbidden = set(contract.get("forbidden_behaviors") or [])
    if not REQUIRED_FORBIDDEN.issubset(forbidden):
        raise LedgerContractError("forbidden_behavior_set_incomplete")
    tests = contract.get("acceptance_tests")
    if not isinstance(tests, list) or len(tests) < 12 or len(tests) != len(set(tests)):
        raise LedgerContractError("acceptance_tests_incomplete_or_duplicate")

    for section in ("message_invariant", "completion_invariant", "replay_contract", "implementation_scope"):
        if not isinstance(contract.get(section), dict):
            raise LedgerContractError(f"required_section_missing:{section}")

    for reference in (
        "WP-02A", "WP-02B", "TurnRequestLedger", "TurnSemanticContract",
        "client_request_id", "PAYLOAD_CONFLICT", "RECOVERY_REQUIRED",
        "SUBMISSION_UNKNOWN", "fencing token", "HTTP", "SSE", "one user message",
    ):
        if reference not in documentation:
            raise LedgerContractError(f"documentation_reference_missing:{reference}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path("governance/architecture/b30-turn-request-ledger.json"))
    parser.add_argument("--doc", type=Path, default=Path("docs/architecture/B30_TURN_REQUEST_LEDGER.md"))
    args = parser.parse_args()
    try:
        validate(args.contract, args.doc)
    except (LedgerContractError, OSError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "PASS", "stage": "B30", "work_package": REQUIRED_WORK_PACKAGE}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
