#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

TEST_PATH = "skill-system/tests/test_wp08_attempt6_dependency_and_historical_reference_repair.py"
SOURCE_PATHS = (
    "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
)


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_goal_planning(root: Path) -> None:
    path = root / SOURCE_PATHS[0]

    helper_anchor = '''def _has_unique_historical_reference(goals: list[dict[str, Any]]) -> bool:\n'''
    helper = '''def _dependency_adjudication_goal_projection(\n    goals: list[dict[str, Any]],\n    *,\n    include_requested_effect: bool = False,\n) -> list[dict[str, Any]]:\n    """Project only evidence needed for adversarial dependency adjudication.\n\n    The positive-edge adjudicator must decide user-visible result dependency\n    from the complete literal USER_TEXT, not infer execution prerequisites from\n    target candidates, conditions, historical binding proposals or transaction\n    mechanics. requested_effect is included only when the same bounded third\n    call must also arbitrate an independently signaled sibling-effect collision.\n    Runtime never rewrites the dependency graph from this projection.\n    """\n    rows: list[dict[str, Any]] = []\n    for goal in goals:\n        row: dict[str, Any] = {\n            "goal_id": _clean_text(goal.get("goal_id"), limit=80),\n            "evidence_span": _clean_text(goal.get("evidence_span"), limit=240),\n        }\n        if include_requested_effect and isinstance(goal.get("requested_effect"), dict):\n            row["requested_effect"] = deepcopy(goal.get("requested_effect"))\n        rows.append(row)\n    return rows\n\n\ndef _has_unique_historical_reference(goals: list[dict[str, Any]]) -> bool:\n'''
    replace_once(path, helper_anchor, helper, "dependency adjudication minimal projection")

    old_rule = '''            "a historical reference span is the smallest literal referring phrase and may be shorter than the Goal evidence_span; never demand that surrounding status/detail/filter/action wording be copied into reference_expression.evidence_span",\n            "when Runtime has already resolved a supplied historical reference uniquely, judge the semantic fidelity of the declared referring phrase against RECENT_PUBLIC_CONTEXT; do not reopen target selection or require non-reference wording inside the reference span",\n'''
    new_rule = '''            "when USER_TEXT semantically returns to or continues an already customer-visible historical result/member represented in RECENT_PUBLIC_CONTEXT, the corresponding Goal must supply reference_expression; a literal label merely appearing in both current text and history is not sufficient by itself to force a historical relation, so a genuinely fresh literal target remains valid without one",\n            "a historical reference span is the smallest literal referring phrase and may be shorter than the Goal evidence_span; never demand that surrounding status/detail/filter/action wording be copied into reference_expression.evidence_span",\n            "when Runtime has already resolved a supplied historical reference uniquely, judge the semantic fidelity of the declared referring phrase against RECENT_PUBLIC_CONTEXT; do not reopen target selection or require non-reference wording inside the reference span",\n'''
    replace_once(path, old_rule, new_rule, "historical-reference omission decision rule")

    old_blind = '''            "(4) when reference_expression is supplied, "\n            "whether it preserves the smallest literal historical referring phrase and its stated historical relation/cardinality "\n            "against RECENT_PUBLIC_CONTEXT. Do not require surrounding status/detail/filter/action wording inside the historical "\n            "reference evidence_span. A scope constraint stores only the smallest "\n'''
    new_blind = '''            "(4) whether current Goal wording semantically returns to or continues an already customer-visible historical result/member "\n            "represented in RECENT_PUBLIC_CONTEXT. If it does, reference_expression is required and, when supplied, must preserve the "\n            "smallest literal historical referring phrase and its stated relation/cardinality. Do not require reference_expression merely "\n            "because the same literal label appears in history when USER_TEXT is instead introducing a genuinely fresh literal target. "\n            "Do not require surrounding status/detail/filter/action wording inside the historical reference evidence_span. A scope constraint stores only the smallest "\n'''
    replace_once(path, old_blind, new_blind, "historical-reference omission blind audit")

    old_positive = '''                            "Adversarially re-audit the complete current-turn dependency graph from USER_TEXT only. Start every unordered "\n                            "Goal pair from independent and retain a positive edge only when a literal basis_span inside the dependent Goal "\n                            "proves that the user-visible later outcome itself consumes the earlier current-turn Goal result as a result_reference, "\n                            "result_condition or result_value_input. Sequencing, shared topic/scope, repeated business object, and stable-ID/artifact "\n                            "lookup needed only by execution are not result dependencies. Do not see or reconstruct Planner depends_on from tool "\n                            "needs. Return one dependency_decisions row for every unordered Goal pair together with the normal requested-effect and "\n                            "scope audit fields. A true explicit result reference/condition/value dependency must still be retained. When "\n'''
    new_positive = '''                            "Adversarially re-audit the complete current-turn dependency graph from USER_TEXT only. Re-read the whole "\n                            "USER_TEXT, start every unordered Goal pair from independent, and retain a positive edge only when a literal basis_span "\n                            "inside the dependent Goal proves that the user-visible later outcome itself consumes the earlier current-turn Goal result "\n                            "as a result_reference, result_condition or result_value_input. If the later outcome merely omits a repeated target while "\n                            "an earlier literal phrase in the same USER_TEXT already names the reusable business object or scope, treat that as same-turn "\n                            "zero-anaphora ellipsis and keep the outcomes independent. A lookup, stable-ID/artifact resolution, Draft prerequisite or "\n                            "form/transaction input needed only to execute against that already literal target is support dataflow, not result_value_input. "\n                            "Sequencing, shared topic/scope and repeated business object are not result dependencies. A positive edge requires literal "\n                            "dependent wording that consumes the earlier outcome's result, not merely its business target. Do not see or reconstruct "\n                            "Planner depends_on from tool needs. Return one dependency_decisions row for every unordered Goal pair together with the "\n                            "normal requested-effect and scope audit fields. A true explicit result reference/condition/value dependency must still be retained. When "\n'''
    replace_once(path, old_positive, new_positive, "positive dependency adversarial semantics")

    old_prompt = '''                    prompt = {\n                        "USER_TEXT_UNTRUSTED": user_text,\n                        "DECLARED_GOALS": _dependency_blind_goal_projection(goals),\n                        "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),\n                        "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n                    }\n                    if effect_collision_risk["risk"]:\n                        prompt["REQUESTED_EFFECT_COLLISION_RISK"] = effect_collision_risk\n                    continue\n'''
    new_prompt = '''                    adjudication_goals = _dependency_adjudication_goal_projection(\n                        goals,\n                        include_requested_effect=bool(effect_collision_risk["risk"]),\n                    )\n                    prompt = {\n                        "USER_TEXT_UNTRUSTED": user_text,\n                        "DECLARED_GOALS": adjudication_goals,\n                        "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),\n                        "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n                    }\n                    if effect_collision_risk["risk"]:\n                        prompt["REQUESTED_EFFECT_COLLISION_RISK"] = effect_collision_risk\n                    continue\n'''
    replace_once(path, old_prompt, new_prompt, "minimal positive dependency adjudication payload")

    old_recent = '''        rows.append({\n            "turn": int(event.get("turn_index") or event.get("turn") or 0),\n            "user_summary": user_summary,\n            "answer_summary": answer_summary,\n            "result_handles": list(dict.fromkeys(\n                str(value)\n                for value in list(event.get("answer_evidence_handles") or [])\n                if str(value)\n            ))[:8],\n            "historical_only": True,\n        })\n    return rows\n'''
    new_recent = '''        rows.append({\n            "turn": int(event.get("turn_index") or event.get("turn") or 0),\n            "user_summary": user_summary,\n            "answer_summary": answer_summary,\n            "result_handles": list(dict.fromkeys(\n                str(value)\n                for value in list(event.get("answer_evidence_handles") or [])\n                if str(value)\n            ))[:8],\n            "historical_only": True,\n        })\n    if state.get("artifact_ledger"):\n        visible_refs = visible_result_refs_from_ledger(\n            state.get("artifact_ledger") or [],\n            state=state,\n            limit=12,\n        )\n        for ref in visible_refs:\n            rows.append({\n                "context_kind": "visible_result_ref",\n                "turn": int(ref.get("source_turn") or 0),\n                "result_ref": str(ref.get("result_ref") or ""),\n                "shape": str(ref.get("shape") or ""),\n                "member_handles": [\n                    str(value) for value in list(ref.get("member_handles") or []) if str(value)\n                ][:12],\n                "member_labels": [\n                    str(value) for value in list(ref.get("member_labels") or []) if str(value)\n                ][:12],\n                "resource_types": [\n                    str(value) for value in list(ref.get("resource_types") or []) if str(value)\n                ][:6],\n                "historical_only": True,\n                "semantic_target_authority": False,\n            })\n    return rows\n'''
    replace_once(path, old_recent, new_recent, "public visible-result context projection")


def patch_dialogue_runtime(root: Path) -> None:
    path = root / SOURCE_PATHS[1]
    old = '''- 本轮原话直接出现商品称呼或业务标识符（例如订单号）时，可直接使用 entity_match(attribute_span=该连续原文) 做一次权威查询；它不要求该对象先出现在 visible_result_refs。当前明确称呼优先于旧 ResultRef，禁止把旧 artifact/collection 句柄与新的 reference_span 拼接。只有代词/省略承接才依赖可见 ResultRef。\n'''
    new = '''- 本轮原话直接出现业务对象称呼或业务标识符时，只有在该字面表达语义上是一个新的直接目标、而不是在回到/引用已经向客户可见的历史结果或成员时，才可直接使用 entity_match(attribute_span=该连续原文)；fresh literal target 不要求对象先出现在 visible_result_refs。若当前字面标签在本轮话语语义中是在显式回到/引用历史可见成员，即使标签本身逐字出现在当前原话，也必须先在对应 Goal 声明 reference_expression 并由 Runtime 得到 UNIQUE 证明，后续消费冻结的 ResultRef/member handle；不得用 entity_match 绕过历史引用证明。历史中恰好存在同名字面标签本身不自动证明当前是历史引用，语义关系仍由模型结合公开上下文判断；Runtime 不做关键词或名称自动绑定。\n'''
    replace_once(path, old, new, "direct literal target vs historical label precedence")


def write_tests(root: Path) -> None:
    path = root / TEST_PATH
    if path.exists():
        raise SystemExit(f"test path already exists: {TEST_PATH}")
    template = Path(__file__).with_name("tmp_wp08_attempt6_dependency_reference_test_template.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")


def patch(root: Path) -> None:
    patch_goal_planning(root)
    patch_dialogue_runtime(root)
    write_tests(root)


def baseline(root: Path, product_sha: str) -> None:
    path = root / "skill-system/registry/product-source-baseline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, dict):
        raise SystemExit("protected baseline files map is missing")
    updated: list[str] = []
    for rel in SOURCE_PATHS:
        if rel not in files:
            raise SystemExit(f"protected baseline does not own {rel}")
        files[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        updated.append(rel)
    payload["file_count"] = len(files)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["generated_from"] = "git:" + product_sha
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"updated": updated, "file_count": len(files)}, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    patch_parser = sub.add_parser("patch")
    patch_parser.add_argument("--workspace", required=True)
    baseline_parser = sub.add_parser("baseline")
    baseline_parser.add_argument("--workspace", required=True)
    baseline_parser.add_argument("--product-sha", required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.command == "patch":
        patch(root)
    else:
        baseline(root, str(args.product_sha))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
