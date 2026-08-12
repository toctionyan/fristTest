from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"replace_once expected 1 match in {path}, got {count}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


def regex_replace_once(path: str, pattern: str, replacement: str) -> None:
    text = read(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"regex_replace_once expected 1 match in {path}, got {count}: {pattern[:120]!r}")
    write(path, updated)


GOAL_PLANNING = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
SEMANTIC = "services/agent-service/src/agent_core/lifecycle/semantic_contract.py"
PROTOCOL = "services/agent-service/src/agent_core/lifecycle/protocol.py"
DIALOGUE = "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
ARCH_TEST = "services/agent-service/tests/architecture/test_semantic_single_writer_invariants.py"


# ---------------------------------------------------------------------------
# 1) Keep legacy diagnostic helper contracts for existing audit/Skill tests,
#    but do NOT expose those prescriptive details to the Semantic Writer.
# ---------------------------------------------------------------------------
legacy_feedback_block = r'''def _alignment_repair_feedback\(alignment: GoalAlignmentVerdict\) -> dict\[str, Any\]:.*?\n\ndef validate_goal_declaration\('''
legacy_feedback_replacement = '''def _alignment_repair_feedback(alignment: GoalAlignmentVerdict) -> dict[str, Any]:
    """Expose a complete grounded diagnostic proof for audit compatibility.

    This helper is NOT the provider-facing writer projection.  The real model
    message boundary in ``dialogue_runtime`` strips replacement semantic values
    and exposes only violation evidence before any declaration retry.
    """
    if (
        alignment.verdict != "incomplete"
        or alignment.reason_code != "goal_alignment_dependency_graph_mismatch"
        or not alignment.independent
    ):
        return {}
    details = alignment.details if isinstance(alignment.details, dict) else {}
    if not (
        details.get("dependency_authority") == "independent_goal_alignment"
        and details.get("dependency_proof_complete") is True
        and details.get("dependency_graph_match") is False
    ):
        return {}

    verified_edges: list[dict[str, str]] = []
    for raw in list(details.get("dependency_edges") or []):
        if not isinstance(raw, dict):
            return {}
        dependent = _clean_text(raw.get("dependent_goal_id"), limit=80)
        prerequisite = _clean_text(raw.get("requires_result_of_goal_id"), limit=80)
        basis_kind = _clean_text(raw.get("basis_kind"), limit=80).lower()
        basis_span = _clean_text(raw.get("basis_span"), limit=240)
        if (
            not dependent
            or not prerequisite
            or basis_kind not in _ALLOWED_ALIGNMENT_DEPENDENCY_BASIS_KINDS
            or not basis_span
        ):
            return {}
        verified_edges.append({
            "dependent_goal_id": dependent,
            "requires_result_of_goal_id": prerequisite,
            "basis_kind": basis_kind,
            "basis_span": basis_span,
        })

    declared_edges: list[dict[str, str]] = []
    for raw in list(details.get("declared_dependency_edges") or []):
        if not isinstance(raw, dict):
            return {}
        dependent = _clean_text(raw.get("dependent_goal_id"), limit=80)
        prerequisite = _clean_text(raw.get("requires_result_of_goal_id"), limit=80)
        if not dependent or not prerequisite:
            return {}
        declared_edges.append({
            "dependent_goal_id": dependent,
            "requires_result_of_goal_id": prerequisite,
        })

    return {
        "independent_verifier_feedback": {
            "authority": "independent_goal_alignment",
            "required_action": "redeclaration_preserving_grounded_dependency_graph",
            "dependency_edges": verified_edges,
            "candidate_declared_dependency_edges": declared_edges,
            "constraints": [
                "change_only_the_dependency_relation_proved_by_this_feedback",
                "preserve_goal_inventory_requested_effects_and_literal_evidence_spans",
                "do_not_infer_tool_order_or_capability_prerequisites_as_goal_dependencies",
                "an_empty_verified_dependency_graph_requires_removing_unproved_candidate_edges",
                "runtime_does_not_auto_rewrite_the_candidate",
            ],
        }
    }


def _granularity_repair_feedback(granularity: Any) -> dict[str, Any]:
    """Keep the historical audit payload; provider projection is violation-only."""
    verdict = str(getattr(granularity, "verdict", "") or "")
    reason_code = str(getattr(granularity, "reason_code", "") or "")
    details = getattr(granularity, "details", {})
    details = details if isinstance(details, dict) else {}
    if verdict == "under_split":
        spans: list[str] = []
        for finding in tuple(getattr(granularity, "findings", ()) or ()):
            if not isinstance(finding, dict):
                continue
            if str(finding.get("reason") or "") != "blind_inventory_outcome_not_covered":
                continue
            span = _clean_text(finding.get("evidence_span"), limit=240)
            if span and span not in spans:
                spans.append(span)
        if not spans:
            return {}
        return {
            "independent_verifier_feedback": {
                "authority": "candidate_blind_goal_inventory",
                "required_action": "redeclaration_preserving_existing_goals_and_adding_uncovered_outcomes",
                "uncovered_outcome_spans": spans,
                "constraints": [
                    "literal_user_text_spans_only",
                    "do_not_infer_or_copy_tool_or_capability_identity",
                    "preserve_unsupported_or_open_business_effects",
                    "do_not_delete_already_preserved_independent_outcomes",
                ],
            }
        }
    if verdict == "mixed" and reason_code == "blind_inventory_dependency_graph_mismatch":
        edges: list[dict[str, str]] = []
        for raw in list(details.get("dependency_edges") or []):
            if not isinstance(raw, dict):
                continue
            dependent_span = _clean_text(raw.get("dependent_span"), limit=240)
            prerequisite_span = _clean_text(raw.get("requires_result_of_span"), limit=240)
            if dependent_span and prerequisite_span:
                edges.append({
                    "dependent_span": dependent_span,
                    "requires_result_of_span": prerequisite_span,
                })
        return {
            "independent_verifier_feedback": {
                "authority": "candidate_blind_goal_inventory",
                "required_action": "redeclaration_preserving_candidate_blind_dependency_graph",
                "dependency_edges": edges,
                "constraints": [
                    "dependency_edges_are_literal_user_text_relations_not_oracle_answers",
                    "sentence_order_shared_topic_and_capability_absence_do_not_create_dependency",
                    "true_current_turn_result_dependency_must_be_preserved",
                    "do_not_change_requested_effect_to_fit_available_capabilities",
                ],
            }
        }
    return {}


def validate_goal_declaration('''
regex_replace_once(GOAL_PLANNING, legacy_feedback_block, legacy_feedback_replacement)

replace_once(
    GOAL_PLANNING,
    '"requested_effect_rule": "rederive effect_kind, subject_type and requested_outputs from current_user_input; never copy verifier semantic answers or capability identities",',
    '"requested_effect_rule": "rederive capability-independent domain, operation, object_type and requested_outputs from current_user_input; never copy verifier semantic answers or capability identities",',
)

semantic_output_validator = r'''def _validate_semantic_output_effect\(.*?\n\ndef _known_tool_names'''
semantic_output_validator_replacement = '''def _validate_semantic_output_effect(
    effect: dict[str, Any],
    *,
    user_text: str,
    goal_evidence_span: str,
    goal_id: str,
) -> list[str]:
    """Validate canonical output IDs and literal evidence, not capability names.

    ``domain/operation/object_type`` remain an open compatibility shape for old
    callers.  Exact execution authority comes only from ``requested_outputs``
    when present; this validator therefore never maps the compatibility fields
    to a Tool or Capability.
    """
    outputs = effect.get("requested_outputs")
    if not isinstance(outputs, list):
        return []  # historical/direct compatibility representation
    errors: list[str] = []
    try:
        from agent_core.modules.registry import current_module_registry
        vocabulary = current_module_registry().semantic_output_index()
    except RuntimeError:
        vocabulary = {}
    for index, output in enumerate(outputs):
        if not isinstance(output, dict):
            errors.append(f"semantic_output_invalid:{goal_id}:{index}")
            continue
        output_id = _clean_text(output.get("output_id"), limit=240).casefold()
        span = _clean_text(output.get("evidence_span"), limit=240)
        if not span or span not in user_text or not goal_evidence_span or span not in goal_evidence_span:
            errors.append(f"semantic_output_evidence_not_in_goal:{goal_id}:{index}")
        if output_id == "open":
            if not _clean_text(output.get("open_description"), limit=500):
                errors.append(f"semantic_open_description_required:{goal_id}:{index}")
            continue
        if output_id not in vocabulary:
            errors.append(f"semantic_output_unknown:{goal_id}:{output_id or index}")
    return errors


def _known_tool_names'''
regex_replace_once(GOAL_PLANNING, semantic_output_validator, semantic_output_validator_replacement)


# ---------------------------------------------------------------------------
# 2) Hybrid semantic normalization: legacy triple remains readable and can be
#    present in provider payloads, while requested_outputs is the exact formal
#    post-freeze coverage identity whenever supplied.
# ---------------------------------------------------------------------------
normalize_pattern = r'''def normalize_requested_effect\(raw: Any, \*, description: str = ""\) -> dict\[str, Any\]:.*?\n\ndef _normalized_goal_base'''
normalize_replacement = '''def normalize_requested_effect(raw: Any, *, description: str = "") -> dict[str, Any]:
    """Normalize one capability-blind effect without consulting installed Tools.

    Historical/direct callers may still provide only the open
    ``domain/operation/object_type`` triple. New provider declarations also
    carry ``requested_outputs``; those canonical semantic output IDs become the
    exact post-freeze capability-coverage identity. The compatibility triple is
    preserved for old readers but never grants execution authority.
    """
    source = raw if isinstance(raw, dict) else {}
    raw_description = _text(source.get("raw_description") or description)
    values = source.get("requested_outputs")
    if isinstance(values, list):
        if not values or len(values) > 8:
            raise ValueError("requested_effect.requested_outputs_required")
        outputs: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, raw_output in enumerate(values):
            if not isinstance(raw_output, dict):
                raise ValueError(f"requested_effect.output_invalid:{index}")
            output_id = _text(raw_output.get("output_id"), limit=240).casefold()
            evidence_span = _text(raw_output.get("evidence_span"), limit=240)
            open_description = _text(raw_output.get("open_description"), limit=500)
            if not output_id or not evidence_span:
                raise ValueError(f"requested_effect.output_incomplete:{index}")
            if output_id in seen:
                raise ValueError(f"requested_effect.output_duplicate:{output_id}")
            if output_id == "open" and not open_description:
                raise ValueError("requested_effect.open_description_required")
            if output_id != "open" and open_description:
                raise ValueError("requested_effect.open_description_only_for_open")
            seen.add(output_id)
            row = {"output_id": output_id, "evidence_span": evidence_span}
            if open_description:
                row["open_description"] = open_description
            outputs.append(row)

        first_semantic = next((row["output_id"] for row in outputs if row["output_id"] != "open"), "")
        semantic_domain, semantic_operation = (
            first_semantic.split(".", 1) if "." in first_semantic else ("", "")
        )
        domain = _text(source.get("domain"), limit=120) or semantic_domain or "open"
        operation = _text(source.get("operation"), limit=160) or (
            semantic_operation if len(outputs) == 1 and semantic_operation else "semantic_output_set"
        )
        object_type = _text(
            source.get("object_type") or source.get("subject_type"), limit=160
        ) or "unspecified"
        result: dict[str, Any] = {
            "domain": domain,
            "operation": operation,
            "object_type": object_type,
            "requested_outputs": outputs,
            "raw_description": raw_description,
        }
        effect_kind = _text(source.get("effect_kind"), limit=80).casefold()
        subject_type = _text(source.get("subject_type"), limit=160).casefold()
        if effect_kind:
            result["effect_kind"] = effect_kind
        if subject_type:
            result["subject_type"] = subject_type
        return result

    effect = {
        "domain": _text(source.get("domain"), limit=120),
        "operation": _text(source.get("operation"), limit=160),
        "object_type": _text(source.get("object_type"), limit=160),
        "raw_description": raw_description,
    }
    if not effect["operation"]:
        raise ValueError("requested_effect.operation_required")
    if not effect["domain"]:
        effect["domain"] = "open"
    if not effect["object_type"]:
        effect["object_type"] = "unspecified"
    return effect


def _normalized_goal_base'''
regex_replace_once(SEMANTIC, normalize_pattern, normalize_replacement)


# ---------------------------------------------------------------------------
# 3) Provider schema preserves the historical required shape for compatibility
#    but makes canonical requested_outputs mandatory through allOf. The triple
#    is explicitly capability-blind compatibility metadata.
# ---------------------------------------------------------------------------
protocol_effect_pattern = r'''                            "requested_effect": \{.*?\n                            "goal_type": \{'''
protocol_effect_replacement = '''                            "requested_effect": {
                                "type": "object",
                                "description": (
                                    "能力无关的用户业务效果。domain、operation、object_type 是开放语义兼容字段，不是 Tool/Capability 身份；"
                                    "requested_outputs 才是冻结后精确覆盖所使用的 canonical semantic output。"
                                    "语义输出词汇不包含能力可用性；没有对应概念时 output_id 使用 open，禁止为了匹配附近能力改写。"
                                ),
                                "properties": {
                                    "domain": {
                                        "type": "string",
                                        "description": "当前用户业务语义的开放命名空间；不是已安装能力域。",
                                    },
                                    "operation": {
                                        "type": "string",
                                        "description": "当前用户业务语义的开放操作名；不得使用能力可用性决定该值。",
                                    },
                                    "object_type": {
                                        "type": "string",
                                        "description": "当前用户所指业务对象的开放语义类型。",
                                    },
                                    "requested_outputs": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 8,
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "output_id": {
                                                    "type": "string",
                                                    "description": "canonical semantic output ID；词汇不存在时只能使用 open。",
                                                },
                                                "evidence_span": {
                                                    "type": "string",
                                                    "description": "必须是当前 Goal evidence_span 内、直接证明该输出需求的连续原文。",
                                                },
                                                "open_description": {
                                                    "type": "string",
                                                    "description": "仅 output_id=open 时填写，原样描述词汇中不存在的用户可见结果。",
                                                },
                                            },
                                            "required": ["output_id", "evidence_span"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    "raw_description": {"type": "string"},
                                },
                                "required": ["domain", "operation", "object_type"],
                                "allOf": [{"required": ["requested_outputs"]}],
                                "additionalProperties": False,
                            },
                            "goal_type": {'''
regex_replace_once(PROTOCOL, protocol_effect_pattern, protocol_effect_replacement)


# ---------------------------------------------------------------------------
# 4) Real provider boundary: keep legacy diagnostic payloads in trace/audit but
#    redact every verifier-authored replacement semantic value before the next
#    Semantic Writer model call.
# ---------------------------------------------------------------------------
replace_once(DIALOGUE, "from copy import deepcopy\nfrom typing import Any\n", "from copy import deepcopy\nimport json\nfrom typing import Any\n")

static_old = '''- requested_effect 必须填写 effect_kind、subject_type、requested_outputs、raw_description。requested_outputs 只能选择能力无关语义输出词汇中的 canonical output_id；词汇没有该用户概念时使用 open 并保留原始描述。语义词汇不包含能力可用性，禁止因为某个能力存在、缺失或名称相近而改变用户业务效果。'''
static_new = '''- requested_effect 的 domain、operation、object_type 只是旧协议兼容形状，必须按当前用户原话填写开放语义，不能作为 Capability 身份；正式完成语义由 requested_outputs 的能力无关 canonical output_id 冻结。对语义词汇有精确对应时必须使用对应 output_id，不存在精确对应时才保留开放身份；禁止用 query/action 等泛化类别替代真实业务含义。语义词汇不包含能力可用性，禁止因为某个能力存在、缺失或名称相近而改变用户业务效果。'''
replace_once(DIALOGUE, static_old, static_new)

planning_old = '''每个 Goal 必须给出能力无关 requested_effect(effect_kind/subject_type/requested_outputs/raw_description)、字面 evidence_span、对象/输入候选、封闭 condition 和依赖。requested_outputs 从当前语义输出词汇选择；没有对应概念时使用 open，绝不能按已安装能力改写。'''
planning_new = '''每个 Goal 必须给出能力无关 requested_effect(domain/operation/object_type + requested_outputs)、字面 evidence_span、对象/输入候选、封闭 condition 和依赖。domain/operation/object_type 仅保持开放语义兼容形状；requested_outputs 从当前能力无关语义输出词汇选择并作为冻结后的精确输出合同，没有对应概念时使用 open，绝不能按已安装能力改写。'''
replace_once(DIALOGUE, planning_old, planning_new)

projection_helpers = '''def _semantic_writer_declaration_result_projection(result: dict[str, Any]) -> dict[str, Any]:
    """Project rejected declaration diagnostics to violation-only writer input.

    Trace/audit may retain the independent verifier's complete proof, including
    candidate-blind dependency graphs.  That proof is never semantic write
    authority.  Before the next model declaration, this boundary retains only
    current-user text, structural errors, reason codes, constraints and literal
    violation spans; replacement edges, requested effects, roles and targets are
    deliberately dropped.
    """
    payload = deepcopy(result) if isinstance(result, dict) else {}
    if payload.get("ok") is True:
        return payload
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    projected_data: dict[str, Any] = {}
    for key in ("errors", "current_user_input", "repair_contract"):
        if key in data:
            projected_data[key] = deepcopy(data[key])

    spans: list[str] = []
    reason_code = ""
    field = "semantic_declaration"

    alignment = data.get("alignment_proof") if isinstance(data.get("alignment_proof"), dict) else {}
    if alignment and str(alignment.get("verdict") or "") != "exact":
        reason_code = str(alignment.get("reason_code") or "")
        if reason_code == "goal_alignment_dependency_graph_mismatch":
            field = "depends_on"
        for value in list(alignment.get("missing_spans") or []):
            span = str(value or "").strip()
            if span and span not in spans:
                spans.append(span)

    granularity = data.get("granularity_proof") if isinstance(data.get("granularity_proof"), dict) else {}
    if granularity and str(granularity.get("verdict") or "") != "exact":
        reason_code = str(granularity.get("reason_code") or reason_code)
        field = "goal_inventory"
        for finding in list(granularity.get("findings") or []):
            if not isinstance(finding, dict):
                continue
            span = str(finding.get("evidence_span") or "").strip()
            if span and span not in spans:
                spans.append(span)

    feedback = data.get("independent_verifier_feedback") if isinstance(data.get("independent_verifier_feedback"), dict) else {}
    authority = str(feedback.get("authority") or "")
    if authority == "candidate_blind_goal_inventory":
        field = "goal_inventory"
    elif authority == "independent_goal_alignment":
        field = "depends_on"
    for key in ("uncovered_outcome_spans",):
        for value in list(feedback.get(key) or []):
            span = str(value or "").strip()
            if span and span not in spans:
                spans.append(span)
    for edge in list(feedback.get("dependency_edges") or []):
        if not isinstance(edge, dict):
            continue
        for key in ("basis_span", "dependent_span", "requires_result_of_span"):
            span = str(edge.get(key) or "").strip()
            if span and span not in spans:
                spans.append(span)

    if feedback or reason_code or spans:
        projected_data["independent_verifier_feedback"] = {
            "authority": "read_only_violation_evidence",
            "required_action": "redeclaration_from_current_user_input",
            "violation": {
                "field": field,
                "reason_code": reason_code or str(payload.get("code") or "semantic_declaration_rejected"),
                "evidence_spans": spans,
            },
            "constraints": [
                "rederive_semantics_from_current_user_input",
                "do_not_copy_verifier_dependency_edges_or_replacement_semantic_values",
                "do_not_copy_verifier_recommended_roles_targets_or_requested_effects",
                "runtime_does_not_auto_rewrite_the_candidate",
            ],
        }

    return {
        "ok": bool(payload.get("ok")),
        "code": str(payload.get("code") or ""),
        "message": str(payload.get("message") or ""),
        "data": projected_data,
    }


def _semantic_writer_tool_message_projection(message: Any) -> Any:
    if str(getattr(message, "name", "") or "") != "declare_turn_goals":
        return message
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        return message
    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return message
    if not isinstance(payload, dict) or payload.get("ok") is True:
        return message
    projected_content = json.dumps(
        _semantic_writer_declaration_result_projection(payload),
        ensure_ascii=False,
        default=str,
    )
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"content": projected_content})
    clone = deepcopy(message)
    try:
        clone.content = projected_content
    except Exception:
        return message
    return clone


'''
marker = "def _loop_messages(\n"
text = read(DIALOGUE)
if projection_helpers.strip() not in text:
    if text.count(marker) != 1:
        raise SystemExit("_loop_messages insertion marker missing")
    text = text.replace(marker, projection_helpers + marker, 1)
    write(DIALOGUE, text)

replace_once(
    DIALOGUE,
    '    messages = list(state.get("messages") or [])\n',
    '    messages = [\n        _semantic_writer_tool_message_projection(message)\n        for message in list(state.get("messages") or [])\n    ]\n',
)


# ---------------------------------------------------------------------------
# 5) Architecture tests assert the real model-bound projection, not the legacy
#    diagnostic helper API that remains intentionally compatible for audit.
# ---------------------------------------------------------------------------
first_test_pattern = r'''def test_planning_schema_is_requested_output_based_and_has_no_legacy_deployed_identity_fields\(\) -> None:.*?\n\ndef test_pre_freeze_prompt_source_never_renders_capability_effect_index'''
first_test_replacement = '''def test_planning_schema_keeps_compat_shape_but_exact_output_is_capability_blind() -> None:
    from agent_core.lifecycle.protocol import planning_schemas

    schema = planning_schemas(semantic_output_ids=["shipment.current_status", "courier.contact.phone"])[0]
    effect = (
        schema["function"]["parameters"]["properties"]["goals"]["items"]
        ["properties"]["requested_effect"]
    )
    assert set(effect["required"]) == {"domain", "operation", "object_type"}
    assert effect["allOf"] == [{"required": ["requested_outputs"]}]
    assert "requested_outputs" in effect["properties"]
    assert "enum" not in effect["properties"]["operation"]
    output_id = effect["properties"]["requested_outputs"]["items"]["properties"]["output_id"]
    assert output_id["enum"] == ["shipment.current_status", "courier.contact.phone", "open"]
    encoded = json.dumps(schema, ensure_ascii=False)
    assert "当前部署登记的业务效果身份" not in encoded
    assert "能力无关" in encoded


def test_pre_freeze_prompt_source_never_renders_capability_effect_index'''
regex_replace_once(ARCH_TEST, first_test_pattern, first_test_replacement)

feedback_test_pattern = r'''def test_alignment_and_granularity_feedback_are_violation_only\(\) -> None:.*\Z'''
feedback_test_replacement = '''def test_alignment_and_granularity_feedback_are_violation_only() -> None:
    from agent_core.lifecycle.dialogue_runtime import _semantic_writer_declaration_result_projection

    alignment_result = {
        "ok": False,
        "code": "GOAL_DECLARATION_INCOMPLETE",
        "message": "rejected",
        "data": {
            "current_user_input": "查订单，然后退款",
            "alignment_proof": {
                "verdict": "incomplete",
                "reason_code": "goal_alignment_dependency_graph_mismatch",
                "missing_spans": [],
            },
            "independent_verifier_feedback": {
                "authority": "independent_goal_alignment",
                "required_action": "redeclaration_preserving_grounded_dependency_graph",
                "dependency_edges": [{
                    "dependent_goal_id": "g2",
                    "requires_result_of_goal_id": "g1",
                    "basis_kind": "result_reference",
                    "basis_span": "然后退款",
                }],
                "candidate_declared_dependency_edges": [],
            },
        },
    }
    projected = _semantic_writer_declaration_result_projection(alignment_result)
    keys = _mapping_keys(projected)
    assert "dependency_edges" not in keys
    assert "requires_result_of_goal_id" not in keys
    feedback = projected["data"]["independent_verifier_feedback"]
    assert feedback["authority"] == "read_only_violation_evidence"
    assert feedback["violation"]["evidence_spans"] == ["然后退款"]

    granularity_result = {
        "ok": False,
        "code": "GOAL_DECLARATION_UNDER_SPLIT",
        "message": "rejected",
        "data": {
            "current_user_input": "查物流，再告诉我快递员手机号",
            "granularity_proof": {
                "verdict": "under_split",
                "reason_code": "blind_inventory_has_more_outcomes_than_declared_goals",
                "findings": [{
                    "reason": "blind_inventory_outcome_not_covered",
                    "recommended_role": "goal",
                    "evidence_span": "快递员手机号",
                }],
            },
            "independent_verifier_feedback": {
                "authority": "candidate_blind_goal_inventory",
                "uncovered_outcome_spans": ["快递员手机号"],
            },
        },
    }
    projected = _semantic_writer_declaration_result_projection(granularity_result)
    keys = _mapping_keys(projected)
    encoded = json.dumps(projected, ensure_ascii=False)
    assert "recommended_role" not in keys
    assert "dependency_edges" not in keys
    assert "快递员手机号" in encoded
'''
regex_replace_once(ARCH_TEST, feedback_test_pattern, feedback_test_replacement)

print("A2/B compatibility correction applied")
