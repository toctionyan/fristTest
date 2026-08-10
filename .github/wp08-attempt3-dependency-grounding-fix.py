from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "services/agent-service/src/agent_core/lifecycle/protocol.py"
PLANNING = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
GRANULARITY = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_granularity.py"
SEMANTIC = ROOT / "services/agent-service/src/agent_core/lifecycle/semantic_contract.py"
DIALOGUE = ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
SMOKE = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Protocol: structured target-source and dependency-basis declarations.
# ---------------------------------------------------------------------------
text = PROTOCOL.read_text(encoding="utf-8")
anchor = """DECLARE_TURN_GOALS_SCHEMA: dict[str, Any] = {\n"""
schemas = r'''
TARGET_BINDING_SCHEMA: dict[str, Any] = {
    "description": (
        "结构化说明本 Goal 的业务对象在当前轮从哪里获得；它是语义证据，不是 Resource handle。"
        "local_literal 表示对象字面就在本 Goal evidence_span 内；same_turn_literal_scope 表示本 Goal 省略了对象，"
        "但另一个同轮 Goal 的局部 evidence_span 已经逐字写出可复用对象/范围；current_turn_goal_output 表示本 Goal 真正指向另一个尚未完成 Goal 的未来结果。"
    ),
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "source": {"const": "local_literal"},
                "evidence_span": {"type": "string"},
            },
            "required": ["source", "evidence_span"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source": {"const": "same_turn_literal_scope"},
                "source_goal_id": {"type": "string"},
                "evidence_span": {"type": "string"},
            },
            "required": ["source", "source_goal_id", "evidence_span"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source": {"const": "current_turn_goal_output"},
                "source_goal_id": {"type": "string"},
                "evidence_span": {"type": "string"},
            },
            "required": ["source", "source_goal_id", "evidence_span"],
            "additionalProperties": False,
        },
    ],
}

DEPENDENCY_BINDINGS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "description": (
        "为 depends_on 提供结构化依据；没有 depends_on 时必须是空数组。target 关系必须与 target_binding=current_turn_goal_output 一致；"
        "condition 关系必须与 condition 中的 goal_output 操作数一致。input/completion 只用于用户可见结果确实把输入或可完成含义绑定到前一 Goal 结果的情况。"
    ),
    "items": {
        "type": "object",
        "properties": {
            "source_goal_id": {"type": "string"},
            "relation": {"type": "string", "enum": ["target", "input", "condition", "completion"]},
            "evidence_span": {"type": "string"},
        },
        "required": ["source_goal_id", "relation", "evidence_span"],
        "additionalProperties": False,
    },
}


'''
text = replace_once(text, anchor, schemas + anchor, label="insert-dependency-schemas")

old_dep = r'''                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "只表达当前轮 Goal 的真实结果依赖：只有后一个 Goal 的目标、输入、条件或可完成含义必须使用前一个 Goal 的结果时才填写。"
                                    "并列、再/然后/另外等话语顺序、共享同一业务对象或同一主题本身都不是依赖；这些情况必须保持独立。"
                                    "同一原话前文已明确业务对象或范围而后文只省略重复对象时，应继承该明示范围；这不是对前一个 Goal 执行结果的依赖。执行时若仍需一次读取把该描述解析成稳定 ID/artifact handle，那只是支持步骤，不能反向制造 depends_on。"
                                    "若后一个 Goal 用它/这个/其中某项等指向本轮前一个 Goal 尚未产生的结果，或其条件显式依赖前一个结果，则应填写依赖。"
                                    "系统不支持某个效果也不能因此制造依赖：能力缺失由后续 MatchProof 独立证明。"
                                ),
                            },
'''
new_dep = old_dep + r'''                            "target_binding": deepcopy(TARGET_BINDING_SCHEMA),
                            "dependency_bindings": deepcopy(DEPENDENCY_BINDINGS_SCHEMA),
'''
text = replace_once(text, old_dep, new_dep, label="goal-dependency-properties")
text = replace_once(
    text,
    '                            "expected_result_cardinality", "required", "depends_on"\n',
    '                            "expected_result_cardinality", "required", "depends_on", "dependency_bindings"\n',
    label="require-dependency-bindings",
)
text = replace_once(
    text,
    """            \"多 Goal 时，每个 Goal 的 evidence_span 必须是只覆盖该 Goal 的局部连续原文，不能把整句或兄弟 Goal 的文字重复复制给多个 Goal；每个证据片段必须能唯一归属到对应 Goal。\"\n""",
    """            \"多 Goal 时，每个 Goal 的 evidence_span 必须是只覆盖该 Goal 的局部连续原文，不能把整句或兄弟 Goal 的文字重复复制给多个 Goal；每个证据片段必须能唯一归属到对应 Goal。\"\n            \"每个 Goal 都必须填写 dependency_bindings；没有 depends_on 时填空数组。目标对象若在本 Goal 局部原文内，用 target_binding.local_literal；若本 Goal 省略对象但同轮另一个 Goal 的局部原文已经写出复用对象/范围，用 same_turn_literal_scope；只有本 Goal 的目标确实指向前一 Goal 尚未产生的结果时才用 current_turn_goal_output 并声明 depends_on。\"\n""",
    label="declare-description-grounding",
)
PROTOCOL.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Goal planning: deterministic structural validation of model-owned grounding.
# ---------------------------------------------------------------------------
text = PLANNING.read_text(encoding="utf-8")
insert_at = """def _known_tool_names(capability_registry: CapabilityRegistry) -> set[str]:\n"""
helper = r'''
_TARGET_BINDING_SOURCES = {"local_literal", "same_turn_literal_scope", "current_turn_goal_output"}
_DEPENDENCY_RELATIONS = {"target", "input", "condition", "completion"}


def _normalize_current_turn_dependency_grounding(
    goals: list[dict[str, Any]],
    *,
    user_text: str,
) -> list[str]:
    """Validate model-owned dependency structure without interpreting language.

    Runtime checks only declared IDs and literal-span placement.  In particular,
    ``same_turn_literal_scope`` proves that a later Goal is reusing a literal
    business scope already present in a sibling Goal span rather than consuming
    that sibling Goal's future result.  No pronoun/keyword list is used here.
    """
    errors: list[str] = []
    by_id = {
        str(goal.get("goal_id") or ""): goal
        for goal in goals
        if str(goal.get("goal_id") or "")
    }
    ids = set(by_id)
    for goal in goals:
        goal_id = str(goal.get("goal_id") or "")
        goal_span = str(goal.get("evidence_span") or "")
        dependencies = {
            str(value) for value in list(goal.get("depends_on") or []) if str(value)
        }
        condition_dependencies = (
            condition_goal_dependencies(goal["condition"])
            if isinstance(goal.get("condition"), dict)
            else set()
        )

        raw_target = goal.pop("_raw_target_binding", None)
        target_binding: dict[str, Any] | None = None
        if raw_target not in (None, "", {}):
            if not isinstance(raw_target, dict):
                errors.append(f"target_binding_invalid:{goal_id}")
            else:
                source = _clean_text(raw_target.get("source"), limit=80)
                evidence_span = _clean_text(raw_target.get("evidence_span"), limit=240)
                source_goal_id = _clean_text(raw_target.get("source_goal_id"), limit=80)
                if source not in _TARGET_BINDING_SOURCES:
                    errors.append(f"target_binding_source_invalid:{goal_id}")
                if not evidence_span or evidence_span not in user_text:
                    errors.append(f"target_binding_evidence_not_in_current_turn:{goal_id}")
                if source == "local_literal":
                    if not evidence_span or evidence_span not in goal_span:
                        errors.append(f"target_binding_local_literal_not_in_goal_span:{goal_id}")
                    if source_goal_id:
                        errors.append(f"target_binding_local_literal_source_goal_forbidden:{goal_id}")
                elif source == "same_turn_literal_scope":
                    source_goal = by_id.get(source_goal_id)
                    source_span = str((source_goal or {}).get("evidence_span") or "")
                    if not source_goal_id or source_goal_id not in ids or source_goal_id == goal_id:
                        errors.append(f"target_binding_same_turn_source_goal_invalid:{goal_id}")
                    elif not evidence_span or evidence_span not in source_span:
                        errors.append(f"target_binding_same_turn_literal_not_in_source_goal:{goal_id}:{source_goal_id}")
                    if evidence_span and evidence_span in goal_span:
                        errors.append(f"target_binding_same_turn_literal_must_be_omitted_locally:{goal_id}")
                elif source == "current_turn_goal_output":
                    if not source_goal_id or source_goal_id not in ids or source_goal_id == goal_id:
                        errors.append(f"target_binding_goal_output_source_invalid:{goal_id}")
                    if not evidence_span or evidence_span not in goal_span:
                        errors.append(f"target_binding_goal_output_evidence_not_local:{goal_id}")
                    if source_goal_id and source_goal_id not in dependencies:
                        errors.append(f"target_binding_goal_output_dependency_required:{goal_id}:{source_goal_id}")
                target_binding = {
                    "source": source,
                    "evidence_span": evidence_span,
                    **({"source_goal_id": source_goal_id} if source_goal_id else {}),
                }
                goal["target_binding"] = target_binding

        raw_bindings = goal.pop("_raw_dependency_bindings", [])
        if raw_bindings is None:
            raw_bindings = []
        if not isinstance(raw_bindings, list):
            errors.append(f"dependency_bindings_invalid:{goal_id}")
            raw_bindings = []
        normalized_bindings: list[dict[str, str]] = []
        for index, raw in enumerate(raw_bindings):
            if not isinstance(raw, dict):
                errors.append(f"dependency_binding_invalid:{goal_id}:{index}")
                continue
            source_goal_id = _clean_text(raw.get("source_goal_id"), limit=80)
            relation = _clean_text(raw.get("relation"), limit=40).lower()
            evidence_span = _clean_text(raw.get("evidence_span"), limit=240)
            if not source_goal_id or source_goal_id not in ids or source_goal_id == goal_id:
                errors.append(f"dependency_binding_source_invalid:{goal_id}:{index}")
            if relation not in _DEPENDENCY_RELATIONS:
                errors.append(f"dependency_binding_relation_invalid:{goal_id}:{index}")
            if not evidence_span or evidence_span not in user_text or evidence_span not in goal_span:
                errors.append(f"dependency_binding_evidence_not_local:{goal_id}:{index}")
            if source_goal_id and source_goal_id not in dependencies:
                errors.append(f"dependency_binding_not_declared:{goal_id}:{source_goal_id}")
            if relation == "condition" and source_goal_id not in condition_dependencies:
                errors.append(f"dependency_binding_condition_not_structural:{goal_id}:{source_goal_id}")
            if relation == "target" and not (
                isinstance(target_binding, dict)
                and target_binding.get("source") == "current_turn_goal_output"
                and target_binding.get("source_goal_id") == source_goal_id
            ):
                errors.append(f"dependency_binding_target_not_goal_output:{goal_id}:{source_goal_id}")
            row = {
                "source_goal_id": source_goal_id,
                "relation": relation,
                "evidence_span": evidence_span,
            }
            if row not in normalized_bindings:
                normalized_bindings.append(row)
        goal["dependency_bindings"] = normalized_bindings

        target_dependency = {
            str(target_binding.get("source_goal_id") or "")
            for target_binding in [target_binding]
            if isinstance(target_binding, dict)
            and target_binding.get("source") == "current_turn_goal_output"
            and str(target_binding.get("source_goal_id") or "")
        }
        binding_dependencies = {
            str(row.get("source_goal_id") or "")
            for row in normalized_bindings
            if str(row.get("source_goal_id") or "")
        }
        grounded = set(condition_dependencies) | target_dependency | binding_dependencies
        for dependency in sorted(dependencies - grounded):
            errors.append(f"goal_dependency_basis_required:{goal_id}:{dependency}")

        if isinstance(target_binding, dict) and target_binding.get("source") == "same_turn_literal_scope":
            source_goal_id = str(target_binding.get("source_goal_id") or "")
            target_relation = any(
                row.get("source_goal_id") == source_goal_id and row.get("relation") == "target"
                for row in normalized_bindings
            )
            if source_goal_id in dependencies and not (
                source_goal_id in condition_dependencies
                or any(
                    row.get("source_goal_id") == source_goal_id
                    and row.get("relation") in {"input", "completion"}
                    for row in normalized_bindings
                )
            ):
                errors.append(f"same_turn_literal_scope_cannot_be_result_dependency:{goal_id}:{source_goal_id}")
            if target_relation:
                errors.append(f"same_turn_literal_scope_target_dependency_forbidden:{goal_id}:{source_goal_id}")
    return errors


'''
text = replace_once(text, insert_at, helper + insert_at, label="insert-dependency-grounding-helper")

text = replace_once(
    text,
    """        if raw.get(\"reference_expression\") not in (None, \"\", [], {}):\n            row[\"_raw_reference_expression\"] = deepcopy(raw.get(\"reference_expression\"))\n        goals.append(row)\n""",
    """        if raw.get(\"reference_expression\") not in (None, \"\", [], {}):\n            row[\"_raw_reference_expression\"] = deepcopy(raw.get(\"reference_expression\"))\n        if raw.get(\"target_binding\") not in (None, \"\", [], {}):\n            row[\"_raw_target_binding\"] = deepcopy(raw.get(\"target_binding\"))\n        row[\"_raw_dependency_bindings\"] = deepcopy(raw.get(\"dependency_bindings\") or [])\n        goals.append(row)\n""",
    label="capture-grounding-fields",
)

anchor_after_refs = """            except ValueError as exc:\n                errors.append(f\"invalid_reference_expression:{row['goal_id']}:{exc}\")\n    for row in goals:\n        invalid = [dep for dep in row[\"depends_on\"] if dep not in ids or dep == row[\"goal_id\"]]\n"""
replacement_after_refs = """            except ValueError as exc:\n                errors.append(f\"invalid_reference_expression:{row['goal_id']}:{exc}\")\n    errors.extend(_normalize_current_turn_dependency_grounding(goals, user_text=user_text))\n    for row in goals:\n        invalid = [dep for dep in row[\"depends_on\"] if dep not in ids or dep == row[\"goal_id\"]]\n"""
text = replace_once(text, anchor_after_refs, replacement_after_refs, label="validate-dependency-grounding")

text = replace_once(
    text,
    """            \"target_candidate\", \"reference_expression\", \"referent_resolution_proof\",\n            \"resolved_reference\", \"input_candidates\", \"condition\", \"execution_commitment\"\n""",
    """            \"target_candidate\", \"target_binding\", \"dependency_bindings\",\n            \"reference_expression\", \"referent_resolution_proof\",\n            \"resolved_reference\", \"input_candidates\", \"condition\", \"execution_commitment\"\n""",
    label="project-grounding-fields",
)
PLANNING.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Blind granularity: raw blind edge cannot override a structurally proven
# same-turn literal-scope reuse when candidate declares no result dependency.
# ---------------------------------------------------------------------------
text = GRANULARITY.read_text(encoding="utf-8")
anchor = """def _blind_dependency_graph_matches(\n"""
helper = r'''
def _effective_blind_dependency_edges(
    *,
    dependency_edges: tuple[tuple[int, int], ...],
    goals: list[dict[str, Any]],
    goal_to_outcome: dict[int, int],
) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
    """Bound blind model edges by deterministic same-turn literal-scope evidence.

    The blind verifier remains independent and its raw authority is retained for
    audit.  It is not, however, allowed to turn a structurally declared
    ``same_turn_literal_scope`` into a result dependency when the candidate Goal
    itself declares no such dependency.  This is a structural cross-check only:
    Runtime does not inspect pronouns, domain vocabulary or capability state.
    """
    outcome_to_goal = {outcome: goal for goal, outcome in goal_to_outcome.items()}
    effective: list[tuple[int, int]] = []
    suppressed: list[tuple[int, int]] = []
    for edge in dependency_edges:
        dependent_outcome, prerequisite_outcome = edge
        dependent_goal_index = outcome_to_goal.get(dependent_outcome)
        prerequisite_goal_index = outcome_to_goal.get(prerequisite_outcome)
        if dependent_goal_index is None or prerequisite_goal_index is None:
            effective.append(edge)
            continue
        dependent_goal = goals[dependent_goal_index]
        prerequisite_goal_id = str(goals[prerequisite_goal_index].get("goal_id") or "")
        declared_dependencies = {
            str(value) for value in list(dependent_goal.get("depends_on") or []) if str(value)
        }
        target_binding = (
            dependent_goal.get("target_binding")
            if isinstance(dependent_goal.get("target_binding"), dict)
            else {}
        )
        structural_literal_reuse = bool(
            target_binding.get("source") == "same_turn_literal_scope"
            and str(target_binding.get("source_goal_id") or "") == prerequisite_goal_id
            and prerequisite_goal_id
            and prerequisite_goal_id not in declared_dependencies
        )
        if structural_literal_reuse:
            suppressed.append(edge)
        else:
            effective.append(edge)
    return tuple(effective), tuple(suppressed)


'''
text = replace_once(text, anchor, helper + anchor, label="insert-effective-blind-edges")

text = replace_once(
    text,
    """    dependency_graph_match = _blind_dependency_graph_matches(\n        outcome_count=outcome_count,\n        dependency_edges=dependency_edges,\n        goals=goals,\n        goal_to_outcome=goal_to_outcome,\n    )\n    dependency_edge_details = [\n""",
    """    effective_dependency_edges, suppressed_dependency_edges = _effective_blind_dependency_edges(\n        dependency_edges=dependency_edges,\n        goals=goals,\n        goal_to_outcome=goal_to_outcome,\n    )\n    dependency_graph_match = _blind_dependency_graph_matches(\n        outcome_count=outcome_count,\n        dependency_edges=effective_dependency_edges,\n        goals=goals,\n        goal_to_outcome=goal_to_outcome,\n    )\n    raw_dependency_edge_details = [\n        {\n            \"dependent_span\": outcome_spans[dependent],\n            \"requires_result_of_span\": outcome_spans[prerequisite],\n        }\n        for dependent, prerequisite in dependency_edges\n    ]\n    dependency_edge_details = [\n""",
    label="use-effective-blind-edges",
)
text = replace_once(
    text,
    """        for dependent, prerequisite in dependency_edges\n    ]\n    details = {\n""",
    """        for dependent, prerequisite in effective_dependency_edges\n    ]\n    suppressed_dependency_edge_details = [\n        {\n            \"dependent_span\": outcome_spans[dependent],\n            \"requires_result_of_span\": outcome_spans[prerequisite],\n        }\n        for dependent, prerequisite in suppressed_dependency_edges\n    ]\n    details = {\n""",
    label="effective-edge-details",
)
text = replace_once(
    text,
    """        \"dependency_edges\": dependency_edge_details,\n        \"dependency_graph_match\": dependency_graph_match,\n""",
    """        \"dependency_edges\": dependency_edge_details,\n        \"raw_dependency_edges\": raw_dependency_edge_details,\n        \"dependency_edges_suppressed_by_structured_scope_binding\": suppressed_dependency_edge_details,\n        \"dependency_graph_match\": dependency_graph_match,\n""",
    label="record-suppressed-edges",
)
GRANULARITY.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Frozen semantic contract retains the structural dependency evidence.
# ---------------------------------------------------------------------------
text = SEMANTIC.read_text(encoding="utf-8")
text = replace_once(
    text,
    """        \"target_candidate\",\n        \"input_candidates\",\n""",
    """        \"target_candidate\",\n        \"target_binding\",\n        \"dependency_bindings\",\n        \"input_candidates\",\n""",
    label="freeze-grounding-fields",
)
text = replace_once(
    text,
    """            \"condition\",\n            \"reference_expression\",\n""",
    """            \"condition\",\n            \"target_binding\",\n            \"dependency_bindings\",\n            \"reference_expression\",\n""",
    label="projection-grounding-fields",
)
SEMANTIC.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Planner and certification prompts explain the structural source distinction.
# ---------------------------------------------------------------------------
text = DIALOGUE.read_text(encoding="utf-8")
anchor = """- Goal 的 depends_on 只表示“本轮一个 Goal 的用户可见结果必须使用另一个本轮 Goal 的结果”；句子先后、共享对象/主题、然后/再/also/next 以及能力缺失都不是依赖。同一当前原话前文已经写出业务对象或范围、后文只是省略重复对象时，继承这个同轮明示范围即可，不要依赖前一个 Goal 的查询结果；即使执行时需要先查一次把该描述解析成稳定 ID/artifact handle，也只是执行支持数据流。只有后一个 Goal 用它/这个/其中某项等真正指向前一个 Goal 尚未产生的结果，或条件必须读取前一个结果时，才填写 depends_on。\n"""
replacement = anchor + """- 为上述边界提供结构证据：dependency_bindings 每个 Goal 都要填写，没有 depends_on 时为空数组。对象就在本 Goal 局部 evidence_span 内时用 target_binding.source=local_literal；对象只在同轮兄弟 Goal 的局部 evidence_span 中逐字出现而本 Goal 省略它时，用 same_turn_literal_scope 并引用那个 source_goal_id，这明确表示复用原文字面范围而不是依赖其执行结果；只有本 Goal 局部原文真正指向前一个尚未完成 Goal 的未来结果时才用 current_turn_goal_output，并与 depends_on 对齐。不要为了通过校验伪造 input/completion dependency binding。\n"""
text = replace_once(text, anchor, replacement, label="dialogue-structured-dependency-rule")
DIALOGUE.write_text(text, encoding="utf-8")

text = SMOKE.read_text(encoding="utf-8")
anchor = """            \"同一当前轮中后续目标依赖前一目标时只用 depends_on；reference_expression 只用于已经在更早轮次向客户展示的历史结果，\"\n            \"不能引用本轮尚未执行目标的未来结果。\"\n"""
replacement = """            \"同一当前轮中后续目标依赖前一目标时只用 depends_on；reference_expression 只用于已经在更早轮次向客户展示的历史结果，\"\n            \"不能引用本轮尚未执行目标的未来结果。每个 Goal 必须填写 dependency_bindings；没有 depends_on 时为空数组。\"\n            \"对象就在本 Goal 局部原文时 target_binding 用 local_literal；本 Goal 省略对象、但同轮兄弟 Goal 的局部原文已逐字写出复用对象/范围时用 same_turn_literal_scope，并引用该 source_goal_id，不能因此填写 depends_on；\"\n            \"只有本 Goal 的局部原文真正指向前一 Goal 尚未产生的未来结果时才用 current_turn_goal_output，并与 depends_on/target dependency binding 对齐。\"\n"""
text = replace_once(text, anchor, replacement, label="smoke-structured-dependency-rule")
SMOKE.write_text(text, encoding="utf-8")

print("Attempt-3 structured dependency-grounding repair applied")
