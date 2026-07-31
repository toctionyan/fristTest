"""Customer response and debug projection, isolated from turn orchestration."""
from __future__ import annotations

import json
from typing import Any

from app.schemas.chat_schema import ChatResponse
from agent_core.transaction.interaction import (
    INTERACTION_SCHEMA_VERSION,
    explicit_interaction_response_contract,
    interaction_response_contract,
    transaction_display_snapshot_from_state,
    transaction_update_from_state,
)
from agent_core.presentation.registry import build_response_blocks, default_presentation_registry
from agent_core.presentation.outcome import Presentation, presentation_from_outcome
from agent_core.runtime.outcomes import coerce_runtime_outcome
from agent_core.runtime.answer_release_alignment import evaluate_answer_release
from agent_core.presentation.contracts.runtime import project_interaction_timeline
from agent_core.transaction.active_draft import get_active_draft_id


class ResponseProjector:
    """Projects canonical graph state into public and debug response envelopes."""

    def __init__(self, *, message_store: Any) -> None:
        self._message_store = message_store

    @staticmethod
    def _message_to_debug(message: Any) -> dict[str, Any]:
        data = {
            "type": message.__class__.__name__,
            "content": getattr(message, "content", None),
        }
        for attr, key in (
            ("id", "id"),
            ("tool_calls", "tool_calls"),
            ("invalid_tool_calls", "invalid_tool_calls"),
            ("name", "name"),
            ("tool_call_id", "tool_call_id"),
            ("additional_kwargs", "additional_kwargs"),
            ("response_metadata", "response_metadata"),
            ("usage_metadata", "usage_metadata"),
        ):
            value = getattr(message, attr, None)
            if value:
                data[key] = value
        return data

    @staticmethod
    def dedupe_debug_llm_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for call in calls:
            key = json.dumps(call, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                unique.append(call)
        return unique

    def safe_state(self, result: dict[str, Any]) -> dict[str, Any]:
        keys = [
            "phase", "status", "turn_index", "current_user_input",
            "ledger_schema_version", "artifact_ledger", "ledger_snapshot",
            "current_turn_plan", "loop_plans", "agent_loop_step", "agent_loop_max_steps", "agent_loop_seen_calls", "answer_protocol_retry", "goal_declaration_retry", "clarification_scope_retry", "deferred_terminal_calls",
            "task_board", "current_turn_task_ids", "action_queue", "action_gateway_result", "conversation_event_log", "audit_snapshot",
            "tool_trace", "tool_error", "answer_evidence_handles", "active_draft_id", "pending_confirmation_id", "pending_confirmation_version", "response_contract",
            "approval_result", "commit_authority", "offer_execution_result", "current_ask_message",
            "current_final_answer", "sources", "summary", "debug_llm_calls", "runtime_outcome", "presentation",
            "debug_current_run_id", "decision_chain", "state_contract_violations", "presentation_contract_violations", "answer_release_alignment", "history_recall_evidence_binding", "transaction_reconciliation", "context_health", "transaction_context_blocked",
        ]
        state = {key: result.get(key) for key in keys if key in result}
        state["active_draft_id"] = get_active_draft_id(result)
        if "debug_llm_calls" in state:
            state["debug_llm_calls"] = self.dedupe_debug_llm_calls(state.get("debug_llm_calls") or [])
        return state

    def public_state(self, result: dict[str, Any]) -> dict[str, Any] | None:
        keys = ["phase", "status", "current_ask_message", "pending_confirmation_id", "pending_confirmation_version"]
        public = {key: result.get(key) for key in keys if key in result and result.get(key) is not None}
        active_draft_id = get_active_draft_id(result)
        if active_draft_id:
            public["active_draft_id"] = active_draft_id
        snapshot = result.get("ledger_snapshot")
        if isinstance(snapshot, dict):
            public["active_offer_count"] = len(snapshot.get("offers") or [])
        if isinstance(result.get("offer_execution_result"), dict):
            public["execution_result"] = {
                "success": bool(result["offer_execution_result"].get("success")),
                "error": result["offer_execution_result"].get("error"),
            }
        context_health = result.get("context_health")
        if isinstance(context_health, dict) and context_health.get("transactions") == "unavailable":
            public["context_health"] = {"transactions": "unavailable"}
        return public or None

    def response_state(self, result: dict[str, Any], *, include_debug: bool) -> dict[str, Any] | None:
        return self.safe_state(result) if include_debug else self.public_state(result)

    @staticmethod
    def fallback_interaction_snapshot(response: ChatResponse) -> dict[str, Any] | None:
        update = response.interaction_update if isinstance(response.interaction_update, dict) else None
        if not update:
            return None
        return {
            "schema_version": INTERACTION_SCHEMA_VERSION,
            "interaction_id": str(update.get("interaction_id") or ""),
            "kind": "transaction",
            "lifecycle": str(update.get("lifecycle") or "expired"),
            "title": "业务办理",
            "target": "",
            "summary": str(update.get("message") or response.answer or response.message or ""),
            "details": [],
            "fields": [],
            "actions": [],
            "control": {},
            "read_only": True,
        }

    def persist_public_response(self, thread_id: str, response: ChatResponse, result: dict[str, Any] | None = None) -> None:
        if self._message_store is None:
            return
        content = str(response.answer or response.message or response.error or "").strip()
        blocks = [dict(block) for block in response.blocks or [] if isinstance(block, dict)]
        interaction = None
        if isinstance(result, dict):
            try:
                interaction = transaction_display_snapshot_from_state(result)
            except Exception:
                interaction = None
        interaction = interaction or self.fallback_interaction_snapshot(response)
        if not content and not blocks and not interaction:
            return
        try:
            self._message_store.add_message(
                thread_id,
                "assistant",
                content,
                message_type=str(response.type),
                presentation=blocks,
                interaction=interaction,
            )
        except TypeError:
            self._message_store.add_message(thread_id, "assistant", content)

    @staticmethod
    def _extract_interrupt(result: dict[str, Any]) -> dict[str, Any] | None:
        interrupts = result.get("__interrupt__")
        if not interrupts:
            return None
        first = interrupts[0]
        payload = getattr(first, "value", first)
        return payload if isinstance(payload, dict) else {"message": str(payload)}

    def debug_snapshot(self, result: dict[str, Any]) -> dict[str, Any]:
        messages = result.get("messages") or []
        return {
            "state": self.safe_state(result),
            "messages": [self._message_to_debug(message) for message in messages],
            "tool_messages": [self._message_to_debug(message) for message in messages if message.__class__.__name__ == "ToolMessage"],
            "ai_tool_calls": [
                self._message_to_debug(message)
                for message in messages
                if message.__class__.__name__ == "AIMessage" and getattr(message, "tool_calls", None)
            ],
        }

    @staticmethod
    def _latest_runtime_outcome(result: dict[str, Any]) -> dict[str, Any] | None:
        correlation_id = str(result.get("correlation_id") or "") or None
        direct = result.get("runtime_outcome")
        if direct is not None:
            normalized = coerce_runtime_outcome(direct, correlation_id=correlation_id)
            return normalized.as_dict() if normalized is not None else None
        for row in reversed(list(result.get("tool_trace") or [])):
            if not isinstance(row, dict):
                continue
            payload = row.get("result") if isinstance(row.get("result"), dict) else {}
            candidate = payload.get("runtime_outcome")
            if candidate is not None:
                normalized = coerce_runtime_outcome(candidate, correlation_id=correlation_id)
                return normalized.as_dict() if normalized is not None else None
        return None

    @staticmethod
    def _record_presentation_violation(result: dict[str, Any], blocks: list[dict[str, Any]]) -> None:
        violations = [
            dict(block.get("contract_violation") or {})
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "projection_contract_violation"
        ]
        if violations:
            prior = result.get("presentation_contract_violations")
            result["presentation_contract_violations"] = [*(prior if isinstance(prior, list) else []), *violations]

    @staticmethod
    def _customer_safe_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep projection diagnostics in state, never in the public envelope."""
        if any(
            isinstance(block, dict) and block.get("type") == "projection_contract_violation"
            for block in blocks
        ):
            return [{
                "type": "notice",
                "tone": "warning",
                "content": "结果暂时无法完整展示，未创建或提交任何业务申请。请重新说明需要查询的事项。",
            }]
        return blocks

    @staticmethod
    def _presentation_blocks(presentation: Presentation, result: dict[str, Any]) -> list[dict[str, Any]]:
        trace_id = str(result.get("correlation_id") or "") or None
        if presentation.primary:
            blocks = default_presentation_registry().release_blocks(
                [dict(presentation.primary)],
                trace_id=trace_id,
                require_primary=True,
            )
            ResponseProjector._record_presentation_violation(result, blocks)
            return ResponseProjector._customer_safe_blocks(blocks)
        if presentation.mode == "structured":
            blocks = build_response_blocks(result, answer=None)
            ResponseProjector._record_presentation_violation(result, blocks)
            return ResponseProjector._customer_safe_blocks(blocks)
        return []

    @staticmethod
    def _interaction_timeline_blocks(interaction: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
        trace_id = str(result.get("correlation_id") or "") or None
        block = project_interaction_timeline(interaction=dict(interaction or {}), trace_id=trace_id)
        blocks = default_presentation_registry().release_blocks([block], trace_id=trace_id, require_primary=True)
        ResponseProjector._record_presentation_violation(result, blocks)
        return ResponseProjector._customer_safe_blocks(blocks)

    @staticmethod
    def _answer_release_or_notice(
        *,
        result: dict[str, Any],
        answer: str | None,
        blocks: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]], str]:
        verdict = evaluate_answer_release(state=result, result=result, answer=answer, blocks=blocks)
        result["answer_release_alignment"] = verdict.as_dict()
        if verdict.decision == "pass":
            return answer, blocks, "pass"
        # A verifier may request an evidence-grounded rewrite, but this
        # projector is not itself a rewriter. Publishing the pre-rewrite
        # response would silently ignore the verdict, so fail closed until a
        # separately registered rewriter produces a new evidence-bound result.
        if verdict.decision == "rewrite_from_evidence":
            notice = {
                "type": "notice",
                "tone": "warning",
                "content": "系统需要基于已核验结果重新整理回答；为避免展示未经重写核验的内容，请稍后重试或换一种方式询问。",
            }
            return None, [notice], "rewrite_from_evidence"
        # Do not publish a result whose decisive query conditions/range failed
        # to survive execution.  This notice is a controlled non-success; it
        # never invents a narrower answer from a broader business response.
        notice = {
            "type": "notice",
            "tone": "warning",
            "content": "系统未能证明当前结果完整满足你的查询条件，因此未展示可能范围不正确的结果。",
        }
        return None, [notice], verdict.decision

    def normalize(self, thread_id: str, result: dict[str, Any], *, include_debug: bool = False) -> ChatResponse:
        # Only an interaction explicitly emitted by the current transaction
        # runtime preempts a normal read-only response.  A durable pending
        # Draft by itself must not hide lifecycle/status queries.
        interaction_contract = explicit_interaction_response_contract(result)
        if interaction_contract is not None:
            interaction = dict(interaction_contract.get("interaction") or {})
            current_outcome = self._latest_runtime_outcome(result) or {}
            if str(current_outcome.get("outcome_type") or "") == "interaction_redirect":
                interaction["summary"] = str(
                    current_outcome.get("customer_safe_summary")
                    or "当前已有待处理办理事项；本次没有创建或提交新的业务申请。"
                )
            blocks = self._interaction_timeline_blocks(interaction, result)
            return ChatResponse(
                type="interaction_required",
                thread_id=thread_id,
                message=None,
                presentation_mode="structured",
                interaction=interaction,
                blocks=blocks,
                state=self.response_state(result, include_debug=include_debug),
                sources=result.get("sources") or [],
            )
        interrupt_payload = self._extract_interrupt(result)
        if interrupt_payload:
            interaction = interrupt_payload.get("interaction") if isinstance(interrupt_payload.get("interaction"), dict) else None
            if interaction is not None:
                blocks = self._interaction_timeline_blocks(interaction, result)
                return ChatResponse(
                    type="interaction_required",
                    thread_id=thread_id,
                    message=None,
                    presentation_mode="structured",
                    interaction=interaction,
                    blocks=blocks,
                    state=self.response_state(result, include_debug=include_debug),
                    sources=result.get("sources") or [],
                )
            message = "该会话中的办理卡缺少当前交互合同，无法安全恢复，请重新发起办理。"
            return ChatResponse(
                type="error",
                thread_id=thread_id,
                answer=message,
                error="INTERACTION_RECOVERY_UNAVAILABLE",
                presentation_mode="notice",
                blocks=[],
                state=self.response_state(result, include_debug=include_debug),
                sources=result.get("sources") or [],
            )

        outcome = self._latest_runtime_outcome(result)
        presentation = presentation_from_outcome(outcome)
        if presentation is not None:
            # Canonical presentation is a runtime result, while answer/blocks/
            # interaction are API projections only.
            result["presentation"] = presentation.as_dict()
            # Keep canonical presentation in state for debugging/audit.  API
            # fields are projections; never emit the same sentence as answer
            # and a text block.
            answer: str | None = None
            blocks: list[dict[str, Any]] = self._presentation_blocks(presentation, result)
            if presentation.mode in {"narrative", "notice"}:
                answer = presentation.summary
                blocks = []
            elif presentation.mode == "structured":
                answer = None
            elif presentation.mode == "transaction_status":
                answer = None
            elif presentation.mode == "interaction":
                # An interaction redirect is derived from the existing durable
                # Draft only for this response; no chat text can mutate it.
                contract = interaction_response_contract(result)
                if contract is not None and isinstance(contract.get("interaction"), dict):
                    interaction = dict(contract["interaction"])
                    blocks = self._interaction_timeline_blocks(interaction, result)
                    return ChatResponse(
                        type="interaction_required",
                        thread_id=thread_id,
                        message=None,
                        presentation_mode="structured",
                        interaction=interaction,
                        blocks=blocks,
                        state=self.response_state(result, include_debug=include_debug),
                        sources=result.get("sources") or [],
                    )
                answer = presentation.summary
                blocks = []
            if presentation.mode in {"notice", "transaction_status"}:
                # A canonical non-success notice and a canonical transaction
                # status are already conclusions owned by Runtime. The latter
                # is projected from a receipt/draft outcome and can be emitted
                # by polling without a fresh natural-language request. Sending
                # either through a request-alignment model can incorrectly
                # reject it as ``no_user_request``. Natural/structured query
                # successes still cross the independent verifier below.
                result["answer_release_alignment"] = {
                    "decision": "pass",
                    "reason_code": (
                        "canonical_transaction_status"
                        if presentation.mode == "transaction_status"
                        else "canonical_runtime_notice"
                    ),
                    "source": "runtime_outcome",
                    "independent": True,
                    "details": {},
                }
                alignment = "pass"
            else:
                answer, blocks, alignment = self._answer_release_or_notice(
                    result=result,
                    answer=answer,
                    blocks=blocks,
                )
            public_mode = "notice" if alignment != "pass" or any(
                isinstance(block, dict) and block.get("type") == "notice" for block in blocks
            ) else presentation.mode
            return ChatResponse(
                type="answer",
                thread_id=thread_id,
                answer=answer,
                blocks=blocks,
                interaction_update=transaction_update_from_state(result),
                presentation_mode=public_mode,
                state=self.response_state(result, include_debug=include_debug),
                sources=result.get("sources") or [],
            )

        # Defensive boundary for non-graph callers.  Graph finalization always
        # emits RuntimeOutcome; if a trace reaches here without one, fail closed
        # instead of treating arbitrary text as a business conclusion.
        if result.get("tool_trace"):
            safe = "系统未获得可继续办理的明确结果；未确认创建或提交任何业务申请。请刷新后查看事务中心，或重新说明需要查询的事项。"
            return ChatResponse(
                type="answer",
                thread_id=thread_id,
                answer=safe,
                blocks=[],
                presentation_mode="notice",
                state=self.response_state(result, include_debug=include_debug),
                sources=[],
            )
        answer = str(result.get("current_final_answer") or "")
        blocks = build_response_blocks(result, answer=answer)
        self._record_presentation_violation(result, blocks)
        blocks = self._customer_safe_blocks(blocks)
        mode = "structured" if blocks else "narrative"
        # A structured block owns the detailed expression.  Keep only an
        # optional non-repetitive model summary; current renderer has no such
        # guarantee, so suppress answer in the fallback structured path.
        if blocks:
            answer = None
        answer, blocks, alignment = self._answer_release_or_notice(result=result, answer=answer, blocks=blocks)
        public_mode = "notice" if alignment != "pass" or any(
            isinstance(block, dict) and block.get("type") == "notice" for block in blocks
        ) else mode
        return ChatResponse(
            type="answer",
            thread_id=thread_id,
            answer=answer,
            blocks=blocks,
            interaction_update=transaction_update_from_state(result),
            presentation_mode=public_mode,
            state=self.response_state(result, include_debug=include_debug),
            sources=result.get("sources") or [],
        )
