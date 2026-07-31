"""Runtime lifecycle and clarification tool helpers."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from agent_core.ledger import active_entries, find_handle, scope_for_state
from agent_core.storage.repositories.base import TransactionLifecycleRepository
from agent_core.transaction import is_reusable_draft, transition_draft
from agent_core.transaction.lifecycle_query import TransactionLifecycleQuery
from agent_core.runtime.outcomes import outcome
from agent_core.transaction.coordinator import persist_draft_from_offer
from agent_modules.ecommerce.contracts import public_capability_labels

from .context import _error, _ok, _require_span, _turn
from .prepare_actions import _required_inputs, _runtime_result

def _query_transaction_lifecycle(
    state: dict[str, Any],
    args: dict[str, Any],
    *,
    transactions: TransactionLifecycleRepository | None,
) -> dict[str, Any]:
    evidence = _require_span(state, str(args.get("query_span") or ""), field="状态查询原文")
    if evidence:
        return _runtime_result(state, "query_transaction_lifecycle", evidence)
    if transactions is None:
        return _runtime_result(state, "query_transaction_lifecycle", _error("TRANSACTION_REPOSITORY_UNAVAILABLE", "当前无法查询办理状态。"))
    runtime = TransactionLifecycleQuery(transactions, outcome_factory=outcome)
    outcome = runtime.query(
        state=state,
        explicit_handle=str(args.get("transaction_handle") or "") or None,
        correlation_id=str(state.get("correlation_id") or "") or None,
    )
    return {
        "ok": outcome.outcome_type not in {"failure", "system_unavailable"},
        "data": dict(outcome.payload),
        "ledger_entries": [],
        "sources": [],
        "runtime_outcome": outcome.as_dict(),
    }


def _list_active_eligibilities(state: dict[str, Any]) -> dict[str, Any]:
    """Return current, scope-bound eligibility assessments.

    The public capability is registered by the active module and is
    intentionally read-only. It therefore has a concrete overlay implementation.
    """
    rows = active_entries(
        state.get("artifact_ledger") or [],
        scope=scope_for_state(state),
        kind="eligibility",
        statuses={"eligible"},
    )
    return _ok(
        {
            "eligibilities": [
                {
                    "handle": row["handle"],
                    "label": row.get("label"),
                    "action_id": row.get("action_id"),
                    "operation": row.get("operation"),
                    "target_handle": row.get("target_handle"),
                    "status": row.get("status"),
                    "expires_at": row.get("expires_at"),
                }
                for row in rows
            ]
        }
    )


def _list_active_offers(state: dict[str, Any]) -> dict[str, Any]:
    offers = [
        row
        for row in active_entries(
            state.get("artifact_ledger") or [],
            scope=scope_for_state(state),
            kind="offer",
        )
        if is_reusable_draft(row)
    ]
    return _ok(
        {
            "offers": [
                {
                    "handle": row["handle"],
                    "label": row.get("label"),
                    "action_id": row.get("action_id"),
                    "draft_state": row.get("draft_state"),
                    "required_inputs": _required_inputs(row),
                }
                for row in offers
            ]
        }
    )


def _dismiss_offer(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    evidence = _require_span(state, str(args.get("reference_span") or ""), field="放弃操作的说明")
    if evidence:
        return evidence
    offer = find_handle(state.get("artifact_ledger") or [], str(args.get("offer_handle") or ""), scope=scope_for_state(state), allowed_kinds={"offer"})
    from agent_core.transaction.model import is_cancellable_draft
    if not offer or not is_cancellable_draft(offer):
        return _error("INVALID_OFFER", "该待处理事项不存在、已关闭或不允许取消。")
    next_offer = transition_draft(offer, "REVOKED", reason="dismissed_by_user")
    next_offer["updated_turn"] = _turn(state)
    persist_draft_from_offer(state=state, offer=next_offer, draft_state="REVOKED")
    return _ok({"dismissed_offer": next_offer["handle"], "label": next_offer.get("label")}, entries=[next_offer])


def _dismiss_eligibility(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    evidence = _require_span(state, str(args.get("reference_span") or ""), field="放弃资格核验的说明")
    if evidence:
        return evidence
    eligibility = find_handle(
        state.get("artifact_ledger") or [],
        str(args.get("eligibility_handle") or ""),
        scope=scope_for_state(state),
        allowed_kinds={"eligibility"},
    )
    if not eligibility or str(eligibility.get("status") or "") != "eligible":
        return _error("INVALID_ELIGIBILITY", "该资格核验不存在、已过期、已否定或不属于当前会话。")
    next_eligibility = deepcopy(eligibility)
    next_eligibility["status"] = "superseded"
    next_eligibility["updated_turn"] = _turn(state)
    return _ok({"dismissed_eligibility": next_eligibility["handle"], "label": next_eligibility.get("label")}, entries=[next_eligibility])


def _ask_context_clarification(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    evidence = _require_span(state, str(args.get("reference_span") or ""), field="待澄清引用")
    if evidence:
        return evidence
    choices: list[dict[str, Any]] = []
    for handle in args.get("candidate_handles") or []:
        item = find_handle(state.get("artifact_ledger") or [], str(handle), scope=scope_for_state(state), allowed_kinds={"artifact", "view", "result"})
        if item:
            choices.append({"handle": item["handle"], "label": item.get("label"), "kind": item.get("kind")})
    if len(choices) < 2:
        return _error("CLARIFICATION_CANDIDATES_INVALID", "澄清需要至少两个当前会话内真实候选范围。")
    return _ok({"reference_span": str(args.get("reference_span") or ""), "choices": choices})


def _report_unsupported_request(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    evidence = _require_span(state, str(args.get("request_span") or ""), field="未支持请求")
    if evidence:
        return evidence
    return _ok({"supported": False, "message": "当前系统没有与该请求匹配的能力，因此不会改用语义相近的工具，也未查询、未创建 Offer、未执行任何业务写操作。", "available": public_capability_labels()})



# Module-private support only. Public fixed capability definitions live in capabilities/.
