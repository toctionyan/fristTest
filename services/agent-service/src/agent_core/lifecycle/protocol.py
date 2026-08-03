from __future__ import annotations

"""Domain-neutral Agent-loop protocol.

The model protocol contains only terminal/internal controls. Domain schemas,
capability contracts and tool classification are supplied through the injected
CapabilityRegistry assembled by the Composition Root.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.kernel.loop_contract import (
    MAX_AGENT_LOOP_STEPS_DEFAULT,
    MAX_SAME_CALLS_PER_TURN_DEFAULT,
)

MAX_WORK_ITEMS = 12


_GOAL_LIFECYCLE_ENUM = ["OPEN", "ACTIVE", "BLOCKED", "PAUSED", "COMPLETED", "CANCELLED", "SUPERSEDED"]
_MODEL_SETTABLE_GOAL_LIFECYCLE_ENUM = ["ACTIVE", "PAUSED", "CANCELLED"]

GOAL_CHANGE_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "operation": {"const": "SET_GOAL_LIFECYCLE"},
                "goal_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 1},
                "from": {"type": "string", "enum": _GOAL_LIFECYCLE_ENUM},
                "to": {"type": "string", "enum": _MODEL_SETTABLE_GOAL_LIFECYCLE_ENUM},
                "evidence_span": {
                    "type": "string",
                    "description": "必须是当前用户原话中的连续字面片段。",
                },
            },
            "required": ["operation", "goal_id", "expected_revision", "from", "to", "evidence_span"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "operation": {"const": "PATCH_GOAL"},
                "goal_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 1},
                "patch": {
                    "type": "object",
                    "properties": {
                        "target_candidate": {"type": "object", "additionalProperties": True},
                        "input_candidates": {"type": "object", "additionalProperties": True},
                        "condition": {},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                    },
                    "minProperties": 1,
                    "additionalProperties": False,
                },
                "evidence_span": {
                    "type": "string",
                    "description": "必须是当前用户原话中的连续字面片段。业务效果变化不能 PATCH，必须新建或 supersede Goal。",
                },
            },
            "required": ["operation", "goal_id", "expected_revision", "patch", "evidence_span"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "operation": {"const": "SUPERSEDE_GOAL"},
                "goal_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 1},
                "superseded_by": {
                    "type": "string",
                    "description": "必须引用本轮 goals 中的新 goal_id。",
                },
                "evidence_span": {
                    "type": "string",
                    "description": "必须是当前用户原话中的连续字面片段。",
                },
            },
            "required": ["operation", "goal_id", "expected_revision", "superseded_by", "evidence_span"],
            "additionalProperties": False,
        },
    ]
}

FOCUS_CHANGE_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "operation": {"const": "SET_GOAL_FOCUS"},
                "goal_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "evidence_span": {"type": "string"},
            },
            "required": ["operation", "goal_id", "expected_revision", "evidence_span"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "operation": {"const": "SET_INTERACTION_FOCUS"},
                "interaction_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "evidence_span": {"type": "string"},
            },
            "required": ["operation", "interaction_id", "expected_revision", "evidence_span"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "operation": {"const": "CLEAR_FOCUS"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "evidence_span": {"type": "string"},
            },
            "required": ["operation", "expected_revision", "evidence_span"],
            "additionalProperties": False,
        },
    ]
}


DECLARE_TURN_GOALS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "declare_turn_goals",
        "description": (
            "在选择任何业务能力之前，按当前原话和权威上下文声明本轮全部业务 Goal、对象候选、条件、顺序和状态变化。"
            "requested_effect 使用开放字符串描述用户要实现的业务效果，不得为了匹配现有工具改写为相近能力。"
            "goal_type 仅是旧执行链兼容提示，可省略且不是正式语义。"
            "修改已有 Goal 或 Focus 时必须复制 ContextBundle 中当前 revision，并提供当前用户原话的连续 evidence_span；"
            "requested_effect 变化不能 PATCH，必须新建 Goal 并显式 supersede 旧 Goal。此阶段看不到业务工具名。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "goals": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_WORK_ITEMS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "goal_id": {"type": "string"},
                            "description": {"type": "string"},
                            "evidence_span": {
                                "type": "string",
                                "description": "必须是当前用户原话中的连续字面片段。",
                            },
                            "requested_effect": {
                                "type": "object",
                                "description": "开放业务效果身份；不要求落入已有枚举。",
                                "properties": {
                                    "domain": {"type": "string"},
                                    "operation": {"type": "string"},
                                    "object_type": {"type": "string"},
                                    "raw_description": {"type": "string"},
                                },
                                "required": ["operation"],
                                "additionalProperties": False,
                            },
                            "goal_type": {
                                "type": "string",
                                "enum": ["query", "consult", "action", "clarification", "unsupported", "narrative"],
                                "description": "旧 Capability 合同迁移期的非权威执行提示；不得作为正式语义身份。",
                            },
                            "expected_result_cardinality": {
                                "type": "string",
                                "enum": ["single", "collection", "none", "unknown"],
                            },
                            "required": {"type": "boolean"},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "continuation_of": {
                                "type": "string",
                                "description": "需要继续既有 Goal 时引用其 goal_id；不要求改变本轮其他独立 Goal。",
                            },
                            "target_candidate": {"type": "object", "additionalProperties": True},
                            "input_candidates": {"type": "object", "additionalProperties": True},
                            "condition": {},
                            "execution_commitment": {"type": "string"},
                        },
                        "required": [
                            "goal_id", "description", "evidence_span", "requested_effect",
                            "expected_result_cardinality", "required", "depends_on"
                        ],
                        "additionalProperties": False,
                    },
                },
                "goal_changes": {
                    "type": "array",
                    "items": deepcopy(GOAL_CHANGE_SCHEMA),
                    "description": "对已有 Goal 的强类型状态操作；必须绑定当前原文证据和 expected revision。",
                },
                "blocker_resolutions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "blocker_id": {"type": "string"},
                            "operation": {
                                "type": "string",
                                "enum": ["RESOLVE_BLOCKER", "CANCEL_BLOCKER", "SUPERSEDE_BLOCKER"],
                            },
                            "evidence_span": {"type": "string"},
                            "value": {},
                        },
                        "required": ["blocker_id", "operation", "evidence_span"],
                        "additionalProperties": False,
                    },
                    "description": "只处理本轮明确涉及的 blocker；其他 blocker 保持不变。",
                },
                "focus_change": deepcopy(FOCUS_CHANGE_SCHEMA),
            },
            "required": ["summary", "goals"],
            "additionalProperties": False,
        },
    },
}


RESPOND_TO_USER_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "respond_to_user",
        "description": (
            "在已获得足够的业务工具或知识库观察后，向用户给出最终回答。涉及业务事实时必须列出真实 ledger handle；"
            "不能把猜测写成事实。若普通承接指向唯一 latest visible ResultRef 且它只有一个成员，用户问该集合的"
            "最高级/唯一项时应直接用该 ResultRef 回答这个成员，不要因无法比较或存在旧集合而澄清。"
        ),
        "parameters": {"type": "object", "properties": {
            "answer": {"type": "string", "description": "面向用户的简洁自然语言回答。"},
            "evidence_handles": {"type": "array", "items": {"type": "string"}},
            "task_ids": {"type": "array", "items": {"type": "string"}},
        }, "required": ["answer", "evidence_handles"], "additionalProperties": False},
    },
}
ASK_USER_CLARIFICATION_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ask_user_clarification",
        "description": (
            "只有在对象、范围或用户真正目标无法安全确定时向用户提问。不要把系统能力缺失伪装成澄清。"
            "若普通承接已有唯一 is_latest_visible_turn=true 的 ResultRef，不得把更旧结果提升为歧义；"
            "该最新结果只有一个成员时，最高级/唯一项就是该成员，禁止以‘只有一项无法比较’为由澄清。"
            "missing_kind 必须诚实区分缺少目标/范围/条件/真实意图；中文连续对话省略主语但最新目标唯一时，"
            "不能声明 target 或 scope 不明确。"
        ),
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"}, "reason": {"type": "string"},
            "missing_kind": {"type": "string", "enum": ["target", "scope", "condition", "intent"]},
            "evidence_handles": {"type": "array", "items": {"type": "string"}},
        }, "required": ["question", "reason", "missing_kind", "evidence_handles"], "additionalProperties": False},
    },
}
INSPECT_AUDIT_EVENT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "inspect_audit_event",
        "description": "仅当用户明确引用此前回答、此前计划或此前结果时，按 ContextBundle 的 trace_handle 读取脱敏历史摘要。reason_span 必须是本轮原话中的完整连续片段；比较多个历史事件时可对每个调用复用同一个完整比较片段，不要自行删除中间词拼接新短语。",
        "parameters": {"type": "object", "properties": {
            "trace_handle": {"type": "string"}, "reason_span": {"type": "string"},
        }, "required": ["trace_handle", "reason_span"], "additionalProperties": False},
    },
}
UPDATE_TASK_BOARD_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "update_task_board",
        "description": "维护会话软工作项；不是业务事实，不能直接触发任何工具或业务写入。",
        "parameters": {"type": "object", "properties": {
            "operation": {"type": "string", "enum": ["create", "update", "complete", "pause", "resume", "supersede"]},
            "task_id": {"type": "string"}, "title": {"type": "string"}, "status_note": {"type": "string"},
            "target_handles": {"type": "array", "items": {"type": "string"}}, "evidence_span": {"type": "string"},
        }, "required": ["operation", "evidence_span"], "additionalProperties": False},
    },
}

TERMINAL_TOOL_NAMES = {"respond_to_user", "ask_user_clarification"}
INTERNAL_TOOL_NAMES = {"declare_turn_goals", "update_task_board", "inspect_audit_event"}
DISALLOWED_PLANNER_TOOL_NAMES: set[str] = set()


def planning_schemas() -> list[dict[str, Any]]:
    """Expose the sole semantic declaration protocol before capability discovery."""
    return [deepcopy(DECLARE_TURN_GOALS_SCHEMA)]


def _compact_provider_discriminated_unions(value: Any) -> Any:
    """Flatten repeated discriminated unions only on the provider projection.

    OpenAI-compatible function calling has no shared-schema facility across
    tools.  Repeating the full Target ``oneOf`` in every capability consumed
    tens of thousands of input characters on every loop iteration.  The
    CapabilityRegistry retains the canonical strict schema and CapabilityGate
    still validates candidates against it before issuing a permit; this compact
    projection only guides the model and cannot weaken runtime enforcement.
    """
    if isinstance(value, list):
        return [_compact_provider_discriminated_unions(item) for item in value]
    if not isinstance(value, dict):
        return value

    variants = value.get("oneOf")
    is_mode_union = (
        value.get("type") == "object"
        and isinstance(variants, list)
        and bool(variants)
        and all(
            isinstance(variant, dict)
            and isinstance(variant.get("properties"), dict)
            and isinstance(variant["properties"].get("mode"), dict)
            and "const" in variant["properties"]["mode"]
            for variant in variants
        )
    )
    if is_mode_union:
        properties: dict[str, Any] = {}
        required_sets: list[set[str]] = []
        for variant in variants:
            variant_properties = variant["properties"]
            required_sets.append(set(variant.get("required") or []))
            for name, raw_schema in variant_properties.items():
                if name in properties or not isinstance(raw_schema, dict):
                    continue
                properties[name] = {
                    key: item for key, item in raw_schema.items() if key != "const"
                }
        common_required = set.intersection(*required_sets) if required_sets else set()
        return {
            "type": "object",
            "description": "按固定 Target 判别联合填写；字段组合由 Runtime 严格校验。",
            "properties": {
                name: _compact_provider_discriminated_unions(schema)
                for name, schema in properties.items()
            },
            "required": [name for name in properties if name in common_required],
            "additionalProperties": False,
        }

    return {
        key: _compact_provider_discriminated_unions(item)
        for key, item in value.items()
    }


def agent_loop_schemas(
    capabilities: CapabilityRegistry,
    *,
    allowed_capability_tools: set[str] | None = None,
) -> list[dict[str, Any]]:
    schemas = [
        *[
            schema
            for schema in capabilities.schemas(allowed_capability_tools)
            if str((schema.get("function") or {}).get("name") or "") not in DISALLOWED_PLANNER_TOOL_NAMES
        ],
        UPDATE_TASK_BOARD_SCHEMA,
        INSPECT_AUDIT_EVENT_SCHEMA,
        ASK_USER_CLARIFICATION_SCHEMA,
        RESPOND_TO_USER_SCHEMA,
    ]
    output: list[dict[str, Any]] = []
    for raw in schemas:
        schema = deepcopy(raw)
        function = schema.get("function") if isinstance(schema.get("function"), dict) else {}
        name = str(function.get("name") or "")
        if name in {"update_task_board", "inspect_audit_event"}:
            output.append(schema)
            continue
        parameters = function.get("parameters") if isinstance(function.get("parameters"), dict) else {}
        properties = parameters.setdefault("properties", {})
        properties["goal_ids"] = {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
            "description": "此调用明确服务的已冻结语义合同 goal_id。业务调用必须且只能绑定一个目标；终止调用可绑定多个已处理目标。",
        }
        required = list(parameters.get("required") or [])
        if "goal_ids" not in required:
            required.append("goal_ids")
        parameters["required"] = required
        output.append(_compact_provider_discriminated_unions(schema))
    return output


@dataclass(frozen=True)
class ToolClassification:
    name: str
    category: str


def classify_tool(name: str, capabilities: CapabilityRegistry) -> ToolClassification:
    value = str(name or "")
    if value in TERMINAL_TOOL_NAMES:
        return ToolClassification(value, "terminal")
    if value in INTERNAL_TOOL_NAMES:
        return ToolClassification(value, "internal")
    if value in DISALLOWED_PLANNER_TOOL_NAMES:
        return ToolClassification(value, "disallowed")
    contract = capabilities.contract_for_tool(value)
    if contract is None:
        return ToolClassification(value, "unknown")
    return ToolClassification(value, "action_draft" if contract.execution_kind == "action_draft" else "observation")


def canonical_call_key(name: str, args: dict[str, Any] | None) -> str:
    import json
    return f"{name}:{json.dumps(dict(args or {}), ensure_ascii=False, sort_keys=True, default=str)}"
