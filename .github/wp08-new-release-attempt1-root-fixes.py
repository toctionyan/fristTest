#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

catalog_path = ROOT / 'services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json'
catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
case = next(row for row in catalog['cases'] if row.get('id') == 'semantic_delete_record_not_cancel')
turn = case['execution_contract']['turn_contracts'][0]
assert turn['user_text'] == '把这个订单记录删掉，不是取消订单'
assert len(turn['goal_oracle']) == 1
oracle = turn['goal_oracle'][0]
assert oracle['evidence_span'] == '订单记录删掉'
# The user-authored business effect is open when no module registers it.  The
# oracle must verify absence of a registered exact effect, not force the model
# to rename every unsupported request to a synthetic generic identity.
oracle['requested_effect'] = {
    'domain': 'order',
    'operation': 'delete_record',
    'object_type': 'order',
}
oracle['requested_effect_match'] = 'unregistered_open'

declaration = turn['model_steps'][0]['tool_calls'][0]['args']['goals'][0]
assert declaration['evidence_span'] == '订单记录删掉'
declaration['requested_effect'] = {
    'domain': 'order',
    'operation': 'delete_record',
    'object_type': 'order',
    'raw_description': '处理用户目标：订单记录删掉',
}
catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

runtime_path = ROOT / 'services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py'
text = runtime_path.read_text(encoding='utf-8')
needle = (
    '显式引用历史结果、历史轮次或展示顺序成员时必须给出 reference_expression，由 Runtime 解析并只接受 UNIQUE 证明。'
    '系统没有对应能力时仍保留原 Goal且保持原本的独立/依赖关系，后续由 Capability MatchProof 证明缺失，禁止改写成相近能力或因 unsupported 状态附加依赖。'
)
replacement = (
    '显式引用历史结果、历史轮次或展示顺序成员时必须给出 reference_expression，由 Runtime 解析并只接受 UNIQUE 证明。'
    '对于没有明确要求回到更早结果的承接式历史引用，由你根据对话语义判断是否承接最近一次客户可见结果；若是，应提出 temporal_visible_result/latest 关系。更早的可见结果仍保留给显式回看，但它们仅仅存在并不会自动让最近结果的承接变成歧义；Runtime 仍不会自动选择目标。'
    '系统没有对应能力时仍保留原 Goal且保持原本的独立/依赖关系，后续由 Capability MatchProof 证明缺失，禁止改写成相近能力或因 unsupported 状态附加依赖。'
)
if needle not in text:
    raise SystemExit('dialogue_runtime planning-rule anchor not found')
if text.count(needle) != 1:
    raise SystemExit('dialogue_runtime planning-rule anchor is not unique')
runtime_path.write_text(text.replace(needle, replacement), encoding='utf-8')

reference_path = ROOT / 'services/agent-service/src/agent_core/context/reference_resolution.py'
reference_text = reference_path.read_text(encoding='utf-8')
ref_needle = '''            (
                "Only a UNIQUE runtime proof may become a frozen resolved "
                "reference; ambiguity requires clarification."
            ),
'''
ref_replacement = '''            (
                "Only a UNIQUE runtime proof may become a frozen resolved "
                "reference; ambiguity requires clarification."
            ),
            (
                "An unqualified continuation that semantically denotes the result just discussed "
                "should be proposed as temporal_visible_result/latest. Older visible results remain "
                "available for explicit return, but their mere presence does not make the latest "
                "continuation ambiguous; Runtime never auto-selects the relation."
            ),
'''
if ref_needle not in reference_text:
    raise SystemExit('reference-resolution prompt anchor not found')
if reference_text.count(ref_needle) != 1:
    raise SystemExit('reference-resolution prompt anchor is not unique')
reference_path.write_text(reference_text.replace(ref_needle, ref_replacement), encoding='utf-8')

test_path = ROOT / 'skill-system/tests/test_wp08_new_release_attempt1_root_fixes.py'
test_path.write_text(r'''from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "services" / "agent-service"
for path in (AGENT, AGENT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agent_core.composition import get_runtime_registry
from agent_core.context.reference_resolution import (
    normalize_reference_expression,
    reference_resolution_prompt_contract,
    resolve_reference_expression,
)
from agent_core.runtime.capability_effects import capability_effect_index

CATALOG = AGENT / "tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"


def _smoke():
    path = AGENT / "scripts/verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("wp08_new_attempt1_semantic_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _delete_case() -> dict:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    return next(row for row in payload["cases"] if row["id"] == "semantic_delete_record_not_cancel")


def _registered_effects() -> set[str]:
    surface = capability_effect_index(get_runtime_registry().capabilities)
    return {
        str(row.get("requested_effect_identity") or "")
        for row in surface["effects"]
        if str(row.get("requested_effect_identity") or "")
    }


def test_delete_record_oracle_preserves_user_open_effect_and_proves_absence() -> None:
    case = _delete_case()
    turn = case["execution_contract"]["turn_contracts"][0]
    oracle = turn["goal_oracle"][0]
    assert oracle["requested_effect_match"] == "unregistered_open"
    assert oracle["requested_effect"] == {
        "domain": "order", "operation": "delete_record", "object_type": "order"
    }
    declared = turn["model_steps"][0]["tool_calls"][0]["args"]["goals"][0]
    assert declared["requested_effect"]["domain"] == "order"
    assert declared["requested_effect"]["operation"] == "delete_record"
    assert declared["requested_effect"]["object_type"] == "order"
    assert "order.delete_record:order" not in _registered_effects()


def test_real_model_oracle_accepts_any_exactly_unregistered_open_identity() -> None:
    smoke = _smoke()
    oracle = _delete_case()["execution_contract"]["turn_contracts"][0]["goal_oracle"]
    goals = [{
        "goal_id": "g-model",
        "goal_type": "",
        "evidence_span": "把这个订单记录删掉，不是取消订单",
        "required": True,
        "depends_on": [],
        "requested_effect": {
            "domain": "order", "operation": "delete_record", "object_type": "order"
        },
    }]
    smoke._match_oracle(
        case_id="semantic_delete_record_not_cancel",
        oracle=oracle,
        goals=goals,
        registered_effect_identities=_registered_effects(),
    )


def test_unregistered_open_oracle_fails_if_exact_effect_becomes_registered() -> None:
    smoke = _smoke()
    oracle = _delete_case()["execution_contract"]["turn_contracts"][0]["goal_oracle"]
    goals = [{
        "goal_id": "g-model",
        "evidence_span": "订单记录删掉",
        "required": True,
        "depends_on": [],
        "requested_effect": {
            "domain": "order", "operation": "delete_record", "object_type": "order"
        },
    }]
    with pytest.raises(RuntimeError, match="no unique model goal"):
        smoke._match_oracle(
            case_id="future-supported-delete",
            oracle=oracle,
            goals=goals,
            registered_effect_identities={*_registered_effects(), "order.delete_record:order"},
        )


def test_latest_visible_reference_resolves_latest_scope_not_older_visible_scope() -> None:
    expression = normalize_reference_expression(
        {
            "reference_type": "temporal_visible_result",
            "temporal_relation": "latest",
            "evidence_span": "其中",
            "object_type": "order",
            "expected_cardinality": "collection",
        },
        user_text="其中最贵的是哪个？",
    )
    refs = [
        {
            "result_ref": "h_result:turn3-in-transit",
            "source_turn": 3,
            "shape": "collection",
            "member_handles": ["artifact:order:10001"],
            "canonical_order": ["artifact:order:10001"],
            "resource_types": ["order"],
            "member_resource_types": ["order"],
            "discourse_recency_rank": 1,
        },
        {
            "result_ref": "h_result:turn1-all-orders",
            "source_turn": 1,
            "shape": "collection",
            "member_handles": [
                "artifact:order:10004", "artifact:order:10003",
                "artifact:order:10002", "artifact:order:10001",
            ],
            "canonical_order": [
                "artifact:order:10004", "artifact:order:10003",
                "artifact:order:10002", "artifact:order:10001",
            ],
            "resource_types": ["order"],
            "member_resource_types": ["order", "order", "order", "order"],
            "discourse_recency_rank": 2,
        },
    ]
    proof = resolve_reference_expression(expression, visible_result_refs=refs)
    assert proof["resolution_status"] == "UNIQUE"
    assert proof["resolved_result_ref"] == "h_result:turn3-in-transit"
    assert proof["resolved_member_handles"] == ["artifact:order:10001"]
    assert proof["auto_substitution_used"] is False


def test_reference_prompt_makes_recency_semantic_not_runtime_autoselection() -> None:
    rules = " ".join(reference_resolution_prompt_contract()["rules"])
    assert "temporal_visible_result/latest" in rules
    assert "Older visible results" in rules
    assert "Runtime never auto-selects" in rules
    dialogue = (AGENT / "src/agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
    assert "temporal_visible_result/latest" in dialogue
    assert "Runtime 仍不会自动选择目标" in dialogue
''', encoding='utf-8')

print(json.dumps({
    'status': 'STAGED',
    'changed': [
        str(catalog_path.relative_to(ROOT)),
        str(runtime_path.relative_to(ROOT)),
        str(reference_path.relative_to(ROOT)),
        str(test_path.relative_to(ROOT)),
    ],
}, ensure_ascii=False))
