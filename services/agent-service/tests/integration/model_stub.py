"""Deterministic OpenAI-compatible endpoint used only by CI integration.

The integration gate must exercise the same lifecycle graph as production,
including its independent capability-verification call.  This server therefore
implements the intentionally small behaviours required by the protected chain:

* a literal current-turn ``declare_turn_goals`` plan;
* a structured ``prepare_cancel_order`` *candidate* for the disposable order
  ``10003``; and
* exact/pass JSON verdicts for all three isolated verifiers.

It never calls a Business Service and never returns a commit/authority tool.
The lifecycle runtime still owns schema validation, MatchProofs, Draft creation
and every business side effect.
"""
from __future__ import annotations

import json
from time import time
from typing import Any

from fastapi import FastAPI

app = FastAPI()


def _content(message: object) -> str:
    """Normalize OpenAI message content without depending on one SDK shape."""
    if not isinstance(message, dict):
        return ""
    value = message.get("content")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or "")
            for item in value
            if isinstance(item, dict)
        )
    return str(value or "")


def _semantic_verifier_user_text(messages: list[object]) -> str | None:
    """Return the exact untrusted user text from an isolated verifier prompt."""
    payload = _verifier_prompt(messages, "capability_exactness_verifier")
    if payload is not None:
        user_text = payload.get("USER_TEXT_UNTRUSTED")
        if isinstance(user_text, str) and user_text:
            return user_text
    return None


def _verifier_prompt(messages: list[object], role: str) -> dict[str, Any] | None:
    for index, raw in enumerate(messages):
        text = _content(raw)
        if role not in text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("role") == role:
            merged = dict(payload)
            for following in messages[index + 1:]:
                try:
                    extra = json.loads(_content(following))
                except json.JSONDecodeError:
                    continue
                if isinstance(extra, dict):
                    merged.update(extra)
            return merged
    return None


def _latest_user_text(messages: list[object]) -> str:
    for raw in reversed(messages):
        if isinstance(raw, dict) and str(raw.get("role") or "") == "user":
            return _content(raw)
    return ""


def _tool_call(name: str, arguments: dict[str, Any], *, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


def _message_for(messages: list[object], tool_names: set[str] | None = None) -> dict[str, Any]:
    """Produce only deterministic candidates that the integration smoke uses."""
    verifier_user_text = _semantic_verifier_user_text(messages)
    if verifier_user_text is not None:
        # The verifier validates that evidence_span is a literal substring.
        # Returning the original user text makes this test double generic for
        # its one responsibility without interpreting business intent itself.
        return {
            "role": "assistant",
            "content": json.dumps(
                {
                    "verdict": "exact",
                    "evidence_span": verifier_user_text,
                    "reason_code": "deterministic_ci_exact",
                },
                ensure_ascii=False,
            ),
        }

    goal_prompt = _verifier_prompt(messages, "turn_goal_alignment_verifier")
    if goal_prompt is not None:
        user_text = str(goal_prompt.get("USER_TEXT_UNTRUSTED") or "")
        return {
            "role": "assistant",
            "content": json.dumps(
                {
                    "verdict": "exact",
                    "evidence_spans": [user_text] if user_text else [],
                    "missing_spans": [],
                    "reason_code": "deterministic_ci_goal_complete",
                },
                ensure_ascii=False,
            ),
        }

    answer_prompt = _verifier_prompt(messages, "answer_release_alignment_verifier")
    if answer_prompt is not None:
        return {
            "role": "assistant",
            "content": json.dumps(
                {"decision": "pass", "reason_code": "deterministic_ci_evidence_aligned"},
                ensure_ascii=False,
            ),
        }

    user_text = _latest_user_text(messages)
    if tool_names == {"declare_turn_goals"}:
        is_cancel = "10003" in user_text and "取消" in user_text
        goal_type = "action" if is_cancel else "narrative"
        requested_effect = (
            {
                "domain": "order",
                "operation": "cancel",
                "object_type": "order",
                "requested_outputs": [
                    {
                        "output_id": "order.cancellation",
                        "evidence_span": user_text,
                    }
                ],
                "raw_description": user_text,
            }
            if is_cancel
            else {
                "domain": "conversation",
                "operation": "respond",
                "object_type": "message",
                "requested_outputs": [
                    {
                        "output_id": "open",
                        "evidence_span": user_text,
                        "open_description": user_text,
                    }
                ],
                "raw_description": user_text,
            }
        )
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                _tool_call(
                    "declare_turn_goals",
                    {
                        "summary": user_text,
                        "goals": [
                            {
                                "goal_id": "goal:primary",
                                "description": user_text,
                                "evidence_span": user_text,
                                "requested_effect": requested_effect,
                                "goal_type": goal_type,
                                "expected_result_cardinality": "single",
                                "required": True,
                                "input_bindings": [],
                            }
                        ],
                    },
                    call_id="call_deterministic_goal_declaration",
                )
            ],
        }
    if "10003" in user_text and "取消" in user_text:
        # These are candidate arguments only.  The graph must still resolve
        # the order from the BusinessPort, validate the literal spans, obtain a
        # MatchProof, and stop at a structured Draft interaction.
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                _tool_call(
                    "prepare_cancel_order",
                    {
                        "target": {"mode": "entity_match", "attribute_span": "订单10003"},
                        "reference_span": "订单10003",
                        "action_span": "取消",
                        "goal_ids": ["goal:primary"],
                    },
                    call_id="call_deterministic_cancel_10003",
                )
            ],
        }

    if "Respond with exactly: model-smoke-ok" in user_text:
        return {"role": "assistant", "content": "model-smoke-ok"}

    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            _tool_call(
                "respond_to_user",
                {
                    "answer": "您好，我可以协助查询订单和办理已确认的事项。",
                    "evidence_handles": [],
                    "goal_ids": ["goal:primary"],
                },
                call_id="call_deterministic_greeting",
            )
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(payload: dict):
    model = str(payload.get("model") or "deterministic-ci-model")
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    tool_names = {
        str((tool.get("function") or {}).get("name") or "")
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
    }
    message = _message_for(messages, tool_names)
    return {
        "id": "chatcmpl-deterministic",
        "object": "chat.completion",
        "created": int(time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
