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
from agent_core.lifecycle.condition_expression import condition_schema
from agent_core.kernel.loop_contract import (
    MAX_AGENT_LOOP_STEPS_DEFAULT,
    MAX_SAME_CALLS_PER_TURN_DEFAULT,
)

MAX_WORK_ITEMS = 12


REFERENCE_EXPRESSION_SCHEMA: dict[str, Any] = {
    "description": (
        "只用于引用已经在更早轮次向客户可见的 ResultRef、历史轮次或其展示成员。reference_expression.expected_cardinality 描述被引用对象本身：指向一个可见对象/成员时用 single，指向将继续筛选/排序/比较的可见集合时用 collection；它不是 Goal 最终输出数量。"
        "同一当前轮中一个 Goal 依赖另一个尚未执行 Goal 的未来结果时禁止填写 reference_expression；"
        "这种当前轮先后/结果依赖只能用 depends_on 表达。"
    ),
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "reference_type": {"const": "explicit_result_ref"},
                "result_ref": {"type": "string"},
                "object_type": {"type": "string"},
                "expected_cardinality": {"enum": ["single", "collection", "unknown"]},
                "evidence_span": {"type": "string"},
            },
            "required": ["reference_type", "result_ref", "evidence_span"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "reference_type": {"const": "temporal_visible_result"},
                "temporal_relation": {
                    "enum": ["latest", "previous", "previous_previous", "visible_turn_offset", "first_visible", "last_same_type", "explicit_turn"]
                },
                "visible_turn_offset": {"type": "integer", "minimum": 0, "maximum": 50},
                "source_turn": {"type": "integer", "minimum": 0},
                "object_type": {"type": "string"},
                "expected_cardinality": {"enum": ["single", "collection", "unknown"]},
                "evidence_span": {"type": "string"},
            },
            "required": ["reference_type", "temporal_relation", "evidence_span"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "reference_type": {"const": "ordinal_visible_member"},
                "temporal_relation": {
                    "enum": ["latest", "previous", "previous_previous", "visible_turn_offset", "first_visible", "last_same_type", "explicit_turn"]
                },
                "visible_turn_offset": {"type": "integer", "minimum": 0, "maximum": 50},
                "source_turn": {"type": "integer", "minimum": 0},
                "position": {"type": "integer", "minimum": 1, "maximum": 100},
                "object_type": {"type": "string"},
                "expected_cardinality": {"const": "single"},
                "evidence_span": {"type": "string"},
            },
            "required": ["reference_type", "temporal_relation", "position", "evidence_span"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "reference_type": {"const": "explicit_visible_member"},
                "member_handle": {"type": "string", "description": "精确的历史可见成员身份；同一成员出现在多个父 ResultRef 时仍是同一语义目标。"},
                "source_result_ref": {"type": "string", "description": "可选的精确父结果限定；只有用户语义确实限定该父结果时填写，不得仅为消除同成员的父别名而编造。"},
                "object_type": {"type": "string"},
                "expected_cardinality": {"const": "single"},
                "evidence_span": {"type": "string"},
            },
            "required": ["reference_type", "member_handle", "evidence_span"],
            "additionalProperties": False,
        },
    ]
}


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
                        "condition": condition_schema(depth=3),
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
            "多 Goal 时，每个 Goal 的 evidence_span 必须是只覆盖该 Goal 的局部连续原文，不能把整句或兄弟 Goal 的文字重复复制给多个 Goal；每个证据片段必须能唯一归属到对应 Goal。"
            "显式引用已经向客户可见的历史结果、历史轮次或展示顺序成员时必须填写 reference_expression；Runtime 只接受 UNIQUE 解析证明，禁止用较新的同类结果替代。"
            "同一当前轮内只有真实结果依赖才填写 depends_on：后一个 Goal 的目标、输入、条件或可完成含义必须使用前一个 Goal 的结果时才依赖；并列、再/然后/另外、共享对象或共享主题本身不构成依赖。同一原话前文已明确对象或范围、后文真正省略重复对象（零指代）时继承该已明示范围，不依赖前一个 Goal 的执行结果；但后文若出现显式指代表达并指向前一个 Goal 尚未产生的本轮结果，这不是“仅省略重复对象”，真实结果依赖优先，必须 depends_on 前一个 Goal。即使执行时需要先查一次把已明示对象解析成订单号/ID/artifact handle，这也只是执行支持数据流，不是 Goal 语义依赖。不得为尚未执行的当前轮 Goal 的未来结果创建 reference_expression。"
            "能力缺失不能改变依赖图；unsupported/open Goal 若语义上可独立判断是否得到满足，就必须保持独立，后续由 Capability MatchProof 证明缺失。"
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
                                "description": ("开放业务效果身份；domain、operation、object_type 三字段必须完整。"
                                                "若当前部署登记的业务效果身份与用户请求精确对应，必须逐字段使用该身份；"
                                                "没有精确对应时保留开放身份，禁止改写成相近能力或泛化类别。"),
                                "properties": {
                                    "domain": {"type": "string"},
                                    "operation": {"type": "string"},
                                    "object_type": {"type": "string"},
                                    "raw_description": {"type": "string"},
                                },
                                "required": ["domain", "operation", "object_type"],
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
                                "description": "本 Goal 最终经验证业务结果的人口基数；单个对象的状态/详情/单项结论用 single。它与 reference_expression.expected_cardinality 分离：后者描述历史被引用对象是单成员还是集合。",
                            },
                            "required": {"type": "boolean"},
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "只表达当前轮 Goal 的真实结果依赖：只有后一个 Goal 的目标、输入、条件或可完成含义必须使用前一个 Goal 的结果时才填写。"
                                    "并列、再/然后/另外等话语顺序、共享同一业务对象或同一主题本身都不是依赖；这些情况必须保持独立。"
                                    "同一原话前文已明确业务对象或范围而后文真正省略重复对象（零指代）时，应继承该明示范围；这不是对前一个 Goal 执行结果的依赖。执行时若仍需一次读取把该描述解析成稳定 ID/artifact handle，那只是支持步骤，不能反向制造 depends_on。"
                                    "若后一个 Goal 用显式指代表达（例如它/这个/其中某项）指向本轮前一个 Goal 尚未产生的结果，或其条件显式依赖前一个结果，则应填写依赖；这种显式结果指代不是普通零指代省略，并且优先于上一条省略规则。"
                                    "系统不支持某个效果也不能因此制造依赖：能力缺失由后续 MatchProof 独立证明。"
                                ),
                            },
                            "continuation_of": {
                                "type": "string",
                                "description": "需要继续既有 Goal 时引用其 goal_id；不要求改变本轮其他独立 Goal。",
                            },
                            "target_candidate": {"type": "object", "additionalProperties": True},
                            "reference_expression": deepcopy(REFERENCE_EXPRESSION_SCHEMA),
                            "input_candidates": {"type": "object", "additionalProperties": True},
                            "condition": condition_schema(depth=3),
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
            "goal_ids": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
                "description": (
                    "仅在本轮正式语义已冻结时使用：列出真正因本次缺失输入而暂停的 Goal。"
                    "语义冻结前可以省略；多个待处理 Goal 时不得用它扩大或改写用户目标。"
                ),
            },
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


def _unique_provider_values(values: list[Any]) -> list[Any]:
    """Return JSON-like values in stable first-seen order."""
    output: list[Any] = []
    for value in values:
        if value not in output:
            output.append(value)
    return output


def _provider_union_discriminators(variants: list[dict[str, Any]]) -> list[str]:
    """Find properties that carry a const in every object variant."""
    common: set[str] | None = None
    ordered: list[str] = []
    for variant in variants:
        properties = variant.get("properties")
        if not isinstance(properties, dict):
            return []
        names = {
            str(name)
            for name, schema in properties.items()
            if isinstance(schema, dict) and "const" in schema
        }
        common = names if common is None else common & names
        for name in properties:
            if name in names and name not in ordered:
                ordered.append(str(name))
    return [name for name in ordered if common and name in common]


def _merge_provider_property_schemas(
    raw_schemas: list[dict[str, Any]],
    *,
    property_name: str,
) -> dict[str, Any]:
    """Build a permissive provider guide while canonical Runtime remains strict.

    A provider projection is only an input hint.  When union variants disagree
    on a field type or constraints, this function widens the projection instead
    of selecting one variant and accidentally making another valid Runtime form
    impossible for the model to emit.
    """
    schemas = [
        _compact_provider_discriminated_unions(schema)
        for schema in raw_schemas
        if isinstance(schema, dict)
    ]
    if not schemas:
        return {}
    if all(schema == schemas[0] for schema in schemas):
        return schemas[0]

    output: dict[str, Any] = {}
    types = _unique_provider_values(
        [schema.get("type") for schema in schemas if schema.get("type") is not None]
    )
    if len(types) == 1:
        output["type"] = types[0]

    # An enum is safe only when every alternative is itself enum/const-bound.
    # Otherwise retaining the narrow enum would reject valid number/date/text
    # variants in the provider-facing guide.
    if all("const" in schema or isinstance(schema.get("enum"), list) for schema in schemas):
        enum_values: list[Any] = []
        for schema in schemas:
            if "const" in schema:
                enum_values.append(schema["const"])
            else:
                enum_values.extend(schema.get("enum") or [])
        enum_values = _unique_provider_values(enum_values)
        if enum_values:
            output["enum"] = enum_values

    if len(types) == 1 and types[0] == "array":
        item_schemas = [
            schema["items"]
            for schema in schemas
            if isinstance(schema.get("items"), dict)
        ]
        if item_schemas:
            output["items"] = _merge_provider_property_schemas(
                item_schemas,
                property_name=f"{property_name}.items",
            )
        minimums = [
            schema["minItems"]
            for schema in schemas
            if isinstance(schema.get("minItems"), int)
        ]
        maximums = [
            schema["maxItems"]
            for schema in schemas
            if isinstance(schema.get("maxItems"), int)
        ]
        if minimums:
            output["minItems"] = min(minimums)
        if maximums:
            output["maxItems"] = max(maximums)
        if any(schema.get("uniqueItems") is True for schema in schemas):
            output["uniqueItems"] = True
    elif len(types) == 1 and types[0] == "object":
        property_order: list[str] = []
        for schema in schemas:
            for name in (schema.get("properties") or {}):
                if name not in property_order:
                    property_order.append(str(name))
        properties: dict[str, Any] = {}
        for name in property_order:
            alternatives = [
                schema["properties"][name]
                for schema in schemas
                if isinstance(schema.get("properties"), dict)
                and isinstance(schema["properties"].get(name), dict)
            ]
            properties[name] = _merge_provider_property_schemas(
                alternatives,
                property_name=name,
            )
        output["properties"] = properties
        required_sets = [set(schema.get("required") or []) for schema in schemas]
        common_required = set.intersection(*required_sets) if required_sets else set()
        output["required"] = [
            name for name in property_order if name in common_required
        ]
        output["additionalProperties"] = False
    elif len(types) > 1:
        output["description"] = (
            f"{property_name} 的类型由判别字段决定；Runtime 按严格合同校验。"
        )

    if len(types) == 1 and types[0] in {"integer", "number"}:
        minimums = [
            schema["minimum"]
            for schema in schemas
            if isinstance(schema.get("minimum"), (int, float))
        ]
        maximums = [
            schema["maximum"]
            for schema in schemas
            if isinstance(schema.get("maximum"), (int, float))
        ]
        if minimums:
            output["minimum"] = min(minimums)
        if maximums:
            output["maximum"] = max(maximums)
    if len(types) == 1 and types[0] == "string":
        minimums = [
            schema["minLength"]
            for schema in schemas
            if isinstance(schema.get("minLength"), int)
        ]
        maximums = [
            schema["maxLength"]
            for schema in schemas
            if isinstance(schema.get("maxLength"), int)
        ]
        if minimums:
            output["minLength"] = min(minimums)
        if maximums:
            output["maxLength"] = max(maximums)

    descriptions = _unique_provider_values(
        [schema.get("description") for schema in schemas if schema.get("description")]
    )
    if len(descriptions) == 1 and "description" not in output:
        output["description"] = descriptions[0]
    return output


def _compact_provider_discriminated_unions(value: Any) -> Any:
    """Flatten repeated discriminated unions only on the provider projection.

    OpenAI-compatible function calling has no shared-schema facility across
    tools. Repeating strict Target and pipeline ``oneOf`` variants in every
    capability consumes substantial input context. The CapabilityRegistry keeps
    the canonical strict schema and CapabilityGate validates candidates against
    it before issuing a permit. This widened projection only guides the model;
    it cannot weaken Runtime enforcement.
    """
    if isinstance(value, list):
        return [_compact_provider_discriminated_unions(item) for item in value]
    if not isinstance(value, dict):
        return value

    variants = value.get("oneOf")
    object_variants = (
        variants
        if isinstance(variants, list)
        and bool(variants)
        and all(
            isinstance(variant, dict)
            and isinstance(variant.get("properties"), dict)
            for variant in variants
        )
        else []
    )
    if object_variants:
        merged = _merge_provider_property_schemas(
            object_variants,
            property_name="union",
        )
        merged["description"] = str(value.get("description") or "").strip() or (
            "按判别字段填写；字段组合由 Runtime 严格校验。"
        )
        return merged

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
            "description": "此调用明确服务的已冻结语义合同 goal_id。一个能力只有在精确完成每个绑定 Goal、目标兼容且分别有完成证明时才可绑定多个 Goal；终止调用可绑定多个已处理目标。",
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
