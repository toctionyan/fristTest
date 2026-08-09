#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"anchor count for {path}: expected 1, got {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


# 1) Candidate-blind dependency authority: any asserted dependency edge gets
# one candidate-blind second audit even when it happens to match the candidate.
granularity = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_granularity.py"
replace_once(
    granularity,
    """    The model sees only current USER_TEXT. A first structural disagreement gets
    one second candidate-blind self-audit; the audit is never told candidate
    Goals, candidate count, matching result, tools or capabilities. The final
    blind inventory authority can then be frozen across declaration repair.
""",
    """    The model sees only current USER_TEXT. A first structural disagreement, or
    any first-pass assertion of a current-turn result dependency, gets one
    second candidate-blind self-audit; the audit is never told candidate Goals,
    candidate count, matching result, tools or capabilities. This prevents an
    accidental execution-support edge from becoming frozen merely because the
    candidate made the same mistake. The final blind inventory authority can
    then be frozen across declaration repair.
""",
)
replace_once(
    granularity,
    '            "When a later outcome omits its target but an earlier phrase in the same USER_TEXT already names the reusable business object or scope, inherit that stated scope as ellipsis; that is not a dependency on the earlier Goal result by itself.",\n',
    '            "When a later outcome omits its target but an earlier phrase in the same USER_TEXT already names the reusable business object or scope, inherit that stated scope as ellipsis; that is not a dependency on the earlier Goal result by itself.",\n'
    '            "Keep semantic result dependency separate from execution-support dataflow: if the later outcome can identify its intended business target from a literal object/descriptor/scope already stated in the same USER_TEXT, a lookup that an implementation may later need to turn that descriptor into an ID or artifact handle is only a support step and must not create dependency_edges. Add an edge only when the customer-visible meaning of the later outcome itself needs the earlier outcome result.",\n',
)
replace_once(
    granularity,
    """            if verdict.exact or attempt > 0:
                return verdict
            verifier_repair = (
""",
    """            if attempt == 0 and dependency_edges:
                verifier_repair = (
                    "Run a second candidate-blind audit of USER_TEXT because the first inventory asserted one or more result dependencies. "
                    "Distinguish semantic result dependency from execution-support dataflow: if a later outcome can identify its intended business target "
                    "from a literal object/descriptor/scope already stated in this same USER_TEXT, any later lookup needed to obtain an ID, artifact handle, "
                    "or transaction input is an implementation support step and dependency_edges must remain empty for that relationship. Keep an edge only "
                    "when the customer-visible meaning of the later outcome itself requires the earlier current-turn outcome result. Sentence order, then/然后, "
                    "shared topic/scope and likely implementation order are not evidence of a semantic dependency. Return the full strict candidate-blind JSON "
                    "again and do not inspect candidate Goals, tools or capabilities."
                )
                continue
            if verdict.exact or attempt > 0:
                return verdict
            verifier_repair = (
""",
)
replace_once(
    granularity,
    """                "A later omitted target may inherit an explicitly stated same-turn business object/scope without depending on an earlier Goal result. "
                "Sentence order, shared topic/object/scope and unsupported/open status do not create dependency; an edge exists only when one outcome needs "
""",
    """                "A later omitted target may inherit an explicitly stated same-turn business object/scope without depending on an earlier Goal result. "
                "A lookup or support step needed only to convert that already-stated target into an ID/artifact/transaction input is execution dataflow, not semantic dependency. "
                "Sentence order, shared topic/object/scope and unsupported/open status do not create dependency; an edge exists only when one outcome needs "
""",
)

# 2) Alignment verifier: make the same semantic-vs-execution distinction visible
# to the independent declaration reviewer.
goal_planning = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
replace_once(
    goal_planning,
    '            "when a later outcome omits its target but an earlier phrase in the same current user turn already names the reusable business object or scope, inherit that stated scope as ellipsis; that shared scope is not a dependency on the earlier Goal result by itself",\n',
    '            "when a later outcome omits its target but an earlier phrase in the same current user turn already names the reusable business object or scope, inherit that stated scope as ellipsis; that shared scope is not a dependency on the earlier Goal result by itself",\n'
    '            "semantic depends_on is not execution-support dataflow: if the later Goal can identify its target directly from an object/descriptor/scope already literal in the same USER_TEXT, a lookup that execution may need to obtain a stable ID/artifact handle is a support step, not a dependency on the earlier query Goal; require depends_on only when the later user-visible outcome itself needs the earlier Goal result",\n',
)
replace_once(
    goal_planning,
    '            "expected_result_cardinality describes the final verified business population, not the number of sentences in the answer: a singular choice, superlative, one entity detail, or one eligibility/policy conclusion is single; a list/set/plural comparison is collection; an existence question over records/orders/items (for example whether any record exists) is collection because the verified population may contain zero, one, or many members even when the answer is one yes/no sentence; narrative or clarification without a business result is none; intermediate sort/filter operations do not change the user\'s final cardinality",\n',
    '            "expected_result_cardinality describes the final verified business population, not the number of sentences in the answer: a singular choice, superlative, one entity detail, one object status/detail follow-up, or one eligibility/policy conclusion is single; a list/set/plural comparison is collection; an existence question over records/orders/items (for example whether any record exists) is collection because the verified population may contain zero, one, or many members even when the answer is one yes/no sentence; narrative or clarification without a business result is none; intermediate sort/filter operations do not change the user\'s final cardinality",\n'
    '            "reference_expression.expected_cardinality describes the historical referent being pointed at, not the Goal output: use single when the user refers to one prior visible object/member, and collection when the user refers to a prior visible set that will be filtered/sorted/compared; it may therefore differ from expected_result_cardinality for a single-result selection over a collection",\n',
)

# 3) Protocol/schema wording: force the model to separate final-result cardinality,
# referent cardinality, semantic dependencies and execution support.
protocol = ROOT / "services/agent-service/src/agent_core/lifecycle/protocol.py"
replace_once(
    protocol,
    '        "只用于引用已经在更早轮次向客户可见的 ResultRef、历史轮次或其展示成员。"\n        "同一当前轮中一个 Goal 依赖另一个尚未执行 Goal 的未来结果时禁止填写 reference_expression；"\n',
    '        "只用于引用已经在更早轮次向客户可见的 ResultRef、历史轮次或其展示成员。reference_expression.expected_cardinality 描述被引用对象本身：指向一个可见对象/成员时用 single，指向将继续筛选/排序/比较的可见集合时用 collection；它不是 Goal 最终输出数量。"\n        "同一当前轮中一个 Goal 依赖另一个尚未执行 Goal 的未来结果时禁止填写 reference_expression；"\n',
)
replace_once(
    protocol,
    '            "同一当前轮内只有真实结果依赖才填写 depends_on：后一个 Goal 的目标、输入、条件或可完成含义必须使用前一个 Goal 的结果时才依赖；并列、再/然后/另外、共享对象或共享主题本身不构成依赖。同一原话前文已明确对象或范围、后文只省略重复对象时继承该已明示范围，不依赖前一个 Goal 的执行结果。不得为尚未执行的当前轮 Goal 的未来结果创建 reference_expression。"\n',
    '            "同一当前轮内只有真实结果依赖才填写 depends_on：后一个 Goal 的目标、输入、条件或可完成含义必须使用前一个 Goal 的结果时才依赖；并列、再/然后/另外、共享对象或共享主题本身不构成依赖。同一原话前文已明确对象或范围、后文只省略重复对象时继承该已明示范围，不依赖前一个 Goal 的执行结果。即使执行时需要先查一次把这个已明示对象解析成订单号/ID/artifact handle，这也只是执行支持数据流，不是 Goal 语义依赖。不得为尚未执行的当前轮 Goal 的未来结果创建 reference_expression。"\n',
)
replace_once(
    protocol,
    '                            "expected_result_cardinality": {\n                                "type": "string",\n                                "enum": ["single", "collection", "none", "unknown"],\n                            },\n',
    '                            "expected_result_cardinality": {\n                                "type": "string",\n                                "enum": ["single", "collection", "none", "unknown"],\n                                "description": "本 Goal 最终经验证业务结果的人口基数；单个对象的状态/详情/单项结论用 single。它与 reference_expression.expected_cardinality 分离：后者描述历史被引用对象是单成员还是集合。",\n                            },\n',
)
replace_once(
    protocol,
    '                                    "同一原话前文已明确业务对象或范围而后文只省略重复对象时，应继承该明示范围；这不是对前一个 Goal 执行结果的依赖。"\n',
    '                                    "同一原话前文已明确业务对象或范围而后文只省略重复对象时，应继承该明示范围；这不是对前一个 Goal 执行结果的依赖。执行时若仍需一次读取把该描述解析成稳定 ID/artifact handle，那只是支持步骤，不能反向制造 depends_on。"\n',
)

# 4) Runtime prompt: remove the contradiction that told a singular follow-up to
# keep consuming the parent singleton collection. The model must consume the
# member only when the frozen ReferenceExpression itself is singular.
dialogue = ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
replace_once(
    dialogue,
    "同一用户原话中前文已明确业务对象或范围、后文只是省略重复对象时，应继承这个已明示范围作为省略语义，不得因此依赖前一个 Goal 的执行结果。",
    "同一用户原话中前文已明确业务对象或范围、后文只是省略重复对象时，应继承这个已明示范围作为省略语义，不得因此依赖前一个 Goal 的执行结果；即使后续执行需要先做一次读取把这个已明示对象解析成稳定 ID/artifact handle，那也是执行支持数据流，不是 Goal 语义依赖。",
)
replace_once(
    dialogue,
    "- 在统一语义声明阶段，只要本轮明确引用历史可见结果、历史轮次或展示顺序成员，就必须在对应 Goal 填写 reference_expression。模型只提出关系；Runtime 生成 ReferentResolutionProof。只有 UNIQUE 证明可冻结为 resolved_reference；AMBIGUOUS/NOT_FOUND/TYPE_CONFLICT/CARDINALITY_CONFLICT 必须澄清或失败关闭，禁止改选更近、相似或更宽的结果。业务 Tool 的 target 必须消费该 resolved_reference 的精确 ResultRef 或成员 handle。\n",
    "- 在统一语义声明阶段，只要本轮明确引用历史可见结果、历史轮次或展示顺序成员，就必须在对应 Goal 填写 reference_expression。模型只提出关系；Runtime 生成 ReferentResolutionProof。只有 UNIQUE 证明可冻结为 resolved_reference；AMBIGUOUS/NOT_FOUND/TYPE_CONFLICT/CARDINALITY_CONFLICT 必须澄清或失败关闭，禁止改选更近、相似或更宽的结果。reference_expression.expected_cardinality 描述被引用的历史对象本身，而 expected_result_cardinality 描述本 Goal 最终业务结果；指向一个对象/成员时前者用 single，指向将继续筛选/排序/比较的历史集合时前者用 collection。业务 Tool 的 target 必须消费该 resolved_reference 的精确 ResultRef 或成员 handle。\n",
)
replace_once(
    dialogue,
    "- 上述链式选择的下游工具必须用 target.mode=collection、left_handle=新的 ResultRef 消费整个已验证结果（即使它只有一项），不得从工具输出中抽取成员 handle 后伪装成 entity_match；target.mode 与字段必须严格遵守判别联合合同。\n",
    "- 链式选择产生的一项 ResultRef 仍可作为集合证据继续做集合级 filter/sort/compare，此时下游工具用 target.mode=collection、left_handle=该 ResultRef；但若后续用户以单对象方式继续指代它，并且冻结的 reference_expression.expected_cardinality=single、ReferentResolutionProof=UNIQUE 且 resolved_reference.member_handles 恰有一个成员，则单对象业务 Tool 必须消费这个已冻结证明中的成员 handle（通常 target.mode=artifact），不得把父 collection 当成单对象，也不得从未冻结的工具原始输出自行抽取/猜测成员。多成员集合绝不能因为代词而由 Runtime 自动挑一个。\n",
)

# 5) Reference resolver prompt contract makes the referent/output cardinality
# split explicit; resolution code itself stays deterministic and unchanged.
reference_resolution = ROOT / "services/agent-service/src/agent_core/context/reference_resolution.py"
replace_once(
    reference_resolution,
    """            (
                "An unqualified continuation that semantically denotes the result just discussed "
                "should be proposed as temporal_visible_result/latest. Older visible results remain "
                "available for explicit return, but their mere presence does not make the latest "
                "continuation ambiguous; Runtime never auto-selects the relation."
            ),
""",
    """            (
                "An unqualified continuation that semantically denotes the result just discussed "
                "should be proposed as temporal_visible_result/latest. Older visible results remain "
                "available for explicit return, but their mere presence does not make the latest "
                "continuation ambiguous; Runtime never auto-selects the relation."
            ),
            (
                "expected_cardinality belongs to the historical referent, not the Goal output. "
                "Use single only when the user is pointing at one prior visible object/member; use "
                "collection when the user is pointing at a visible set that will be filtered, sorted "
                "or compared. A UNIQUE single proof exposes the verified member handle; Runtime does "
                "not choose a member from a multi-member collection."
            ),
""",
)

# 6) MatchProof must bind against the ReferenceExpression's referent cardinality,
# not the Goal's final output cardinality. A singular frozen referent consumes a
# proven member handle; collection references consume the exact resolved ResultRef.
capability_gate = ROOT / "services/agent-service/src/agent_core/runtime/capability_gate.py"
replace_once(
    capability_gate,
    """        resolution_status = str((proof or {}).get("resolution_status") or "")
        expected_cardinality = str(goal.get("expected_result_cardinality") or "unknown")
        if len(member_handles) == 1:
""",
    """        resolution_status = str((proof or {}).get("resolution_status") or "")
        goal_result_cardinality = str(goal.get("expected_result_cardinality") or "unknown")
        reference_expression = goal.get("reference_expression") if isinstance(goal.get("reference_expression"), dict) else {}
        reference_cardinality = str(reference_expression.get("expected_cardinality") or "unknown")
        if len(member_handles) == 1:
""",
)
replace_once(
    capability_gate,
    """        elif expected_cardinality == "single" and member_handles:
            if mode == "artifact":
                matched = str(target.get("left_handle") or "") in member_handles
            else:
                matched = bool(actual_handles & ({result_ref} | member_handles))
            reason = "resolved_single_reference_bound" if matched else "resolved_single_reference_target_mismatch"
        else:
            matched = bool(result_ref and result_ref in actual_handles)
            reason = "resolved_collection_reference_bound" if matched else "resolved_collection_reference_target_mismatch"
""",
    """        elif reference_cardinality == "single" and member_handles:
            matched = bool(actual_handles & member_handles)
            reason = "resolved_single_reference_member_bound" if matched else "resolved_single_reference_requires_member_handle"
        else:
            matched = bool(result_ref and result_ref in actual_handles)
            reason = "resolved_collection_reference_bound" if matched else "resolved_collection_reference_target_mismatch"
""",
)
replace_once(
    capability_gate,
    '            "expected_cardinality": expected_cardinality,\n',
    '            "expected_cardinality": reference_cardinality,\n            "goal_result_cardinality": goal_result_cardinality,\n',
)

# 7) Focused regression module.
test_path = ROOT / "skill-system/tests/test_wp08_new_release_attempt2_root_fixes.py"
test_path.write_text(r'''from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "services" / "agent-service"
SRC = AGENT / "src"
for path in (AGENT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {"goal_id": goal_id, "evidence_span": span, "depends_on": depends_on}


def _response(*, spans: list[str], edges: list[dict[str, str]], reason: str) -> SimpleNamespace:
    return SimpleNamespace(content=json.dumps({
        "verdict": "exact",
        "outcome_spans": spans,
        "dependency_edges": edges,
        "reason_code": reason,
    }, ensure_ascii=False))


def test_first_pass_dependency_cannot_freeze_without_candidate_blind_second_audit() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    user_text = "查一下鼠标订单，然后帮我申请退款"
    spans = ["查一下鼠标订单", "帮我申请退款"]
    asserted = [{
        "dependent_span": spans[1],
        "requires_result_of_span": spans[0],
    }]
    calls = [
        (_response(spans=spans, edges=asserted, reason="first_pass_execution_order_confusion"), {}),
        (_response(spans=spans, edges=[], reason="second_pass_shared_literal_target_is_independent"), {}),
    ]
    goals = [_goal("g1", spans[0], []), _goal("g2", spans[1], ["g1"])]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ) as invoke:
        verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
    assert invoke.call_count == 2
    assert verdict.verdict == "mixed"
    assert verdict.reason_code == "blind_inventory_dependency_graph_mismatch"
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["blind_self_audit_attempted"] is True


def test_true_result_dependency_survives_required_second_blind_audit() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    user_text = "查一下键盘订单，再看看它能不能退款"
    spans = ["查一下键盘订单", "它能不能退款"]
    edge = [{
        "dependent_span": spans[1],
        "requires_result_of_span": spans[0],
    }]
    calls = [
        (_response(spans=spans, edges=edge, reason="pronoun_result_dependency"), {}),
        (_response(spans=spans, edges=edge, reason="pronoun_result_dependency_reaudited"), {}),
    ]
    goals = [_goal("g1", spans[0], []), _goal("g2", spans[1], ["g1"])]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ) as invoke:
        verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["blind_self_audit_attempted"] is True
    assert verdict.details["dependency_edges"] == [{
        "dependent_span": spans[1],
        "requires_result_of_span": spans[0],
    }]


def _contract(*, reference_cardinality: str, goal_result_cardinality: str = "single") -> dict:
    from agent_core.kernel.semantic_contract import (
        FROZEN_SEMANTIC_CONTRACT_VERSION,
        compute_semantic_digest,
    )
    goal = {
        "goal_id": "g1",
        "description": "查询它现在的状态",
        "evidence_span": "它现在是什么状态",
        "requested_effect": {
            "domain": "order",
            "operation": "get_details",
            "object_type": "order",
            "raw_description": "查询它现在的状态",
        },
        "expected_result_cardinality": goal_result_cardinality,
        "required": True,
        "depends_on": [],
        "reference_expression": {
            "version": "reference-expression@1",
            "reference_type": "temporal_visible_result",
            "temporal_relation": "latest",
            "evidence_span": "它",
            "object_type": "order",
            "expected_cardinality": reference_cardinality,
        },
        "resolved_reference": {
            "result_ref": "h_result:latest-singleton",
            "member_handles": ["artifact:order:10001"],
            "proof_digest": "proof-placeholder",
        },
    }
    contract = {
        "version": FROZEN_SEMANTIC_CONTRACT_VERSION,
        "authority": "sole_formal_turn_semantics",
        "immutable": True,
        "turn": 5,
        "user_text": "它现在是什么状态？",
        "summary": "查询单个订单状态",
        "goals": [goal],
        "goal_changes": [],
        "blocker_resolutions": [],
        "focus_change": None,
        "alignment_proof": {"verdict": "exact"},
        "granularity_proof": {"verdict": "exact"},
        "semantic_rewrite_allowed_after_freeze": False,
    }
    contract["semantic_digest"] = compute_semantic_digest(contract)
    contract["semantic_contract_id"] = f"semantic:5:{contract['semantic_digest'][:20]}"
    return contract


def test_semantic_binding_uses_referent_cardinality_not_goal_output_cardinality() -> None:
    from agent_core.runtime.capability_gate import _semantic_reference_binding_proof

    single_state = {"frozen_semantic_contract": _contract(reference_cardinality="single")}
    member = _semantic_reference_binding_proof(
        single_state,
        {"target": {"mode": "artifact", "left_handle": "artifact:order:10001"}},
        goal_ids={"g1"},
    )
    assert member["complete"] is True
    assert member["checks"][0]["reason_code"] == "resolved_single_reference_member_bound"
    parent = _semantic_reference_binding_proof(
        single_state,
        {"target": {"mode": "collection", "left_handle": "h_result:latest-singleton"}},
        goal_ids={"g1"},
    )
    assert parent["complete"] is False
    assert parent["checks"][0]["reason_code"] == "resolved_single_reference_requires_member_handle"

    # A Goal may return one selected answer while the historical referent is a
    # collection source. In that case the exact collection ResultRef remains the
    # correct binding; this proves the two cardinalities are intentionally separate.
    collection_state = {
        "frozen_semantic_contract": _contract(
            reference_cardinality="collection",
            goal_result_cardinality="single",
        )
    }
    collection = _semantic_reference_binding_proof(
        collection_state,
        {"target": {"mode": "collection", "left_handle": "h_result:latest-singleton"}},
        goal_ids={"g1"},
    )
    assert collection["complete"] is True
    assert collection["checks"][0]["expected_cardinality"] == "collection"
    assert collection["checks"][0]["goal_result_cardinality"] == "single"


def test_single_latest_reference_resolves_verified_member_without_runtime_guessing() -> None:
    from agent_core.context.reference_resolution import (
        normalize_reference_expression,
        resolve_reference_expression,
    )
    expression = normalize_reference_expression(
        {
            "reference_type": "temporal_visible_result",
            "temporal_relation": "latest",
            "expected_cardinality": "single",
            "evidence_span": "它",
        },
        user_text="它现在是什么状态？",
        expected_object_type="order",
        expected_cardinality="single",
    )
    proof = resolve_reference_expression(expression, visible_result_refs=[{
        "result_ref": "h_result:latest-singleton",
        "source_turn": 4,
        "shape": "collection",
        "member_handles": ["artifact:order:10001"],
        "canonical_order": ["artifact:order:10001"],
        "resource_types": ["order"],
        "member_resource_types": ["order"],
        "discourse_recency_rank": 1,
    }])
    assert proof["resolution_status"] == "UNIQUE"
    assert proof["resolved_member_handles"] == ["artifact:order:10001"]
    assert proof["auto_substitution_used"] is False


def test_prompt_surfaces_state_execution_support_and_single_referent_rules_consistently() -> None:
    granularity = (SRC / "agent_core/lifecycle/goal_granularity.py").read_text(encoding="utf-8")
    alignment = (SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    protocol = (SRC / "agent_core/lifecycle/protocol.py").read_text(encoding="utf-8")
    dialogue = (SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
    reference = (SRC / "agent_core/context/reference_resolution.py").read_text(encoding="utf-8")
    gate = (SRC / "agent_core/runtime/capability_gate.py").read_text(encoding="utf-8")

    assert "execution-support dataflow" in granularity
    assert "semantic depends_on is not execution-support dataflow" in alignment
    assert "执行支持数据流" in protocol
    assert "执行支持数据流" in dialogue
    assert "reference_expression.expected_cardinality" in dialogue
    assert "expected_cardinality belongs to the historical referent" in reference
    assert 'reference_cardinality = str(reference_expression.get("expected_cardinality") or "unknown")' in gate
    assert "resolved_single_reference_requires_member_handle" in gate


def test_release_envelopes_remain_unchanged() -> None:
    smoke = (AGENT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
    config = (SRC / "agent_core/config.py").read_text(encoding="utf-8")
    browser = (AGENT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
    assert 'model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes")' in smoke
    assert '_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0' in config
    assert '_bounded_int_env("MODEL_MAX_RETRIES", 1' in config
    assert '{ timeout: 120_000 }' in browser
''', encoding="utf-8")

print("Attempt-2 root fix staged")
