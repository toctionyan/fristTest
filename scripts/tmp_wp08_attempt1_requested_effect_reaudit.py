#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import py_compile
import subprocess

GOAL_PATH = Path('services/agent-service/src/agent_core/lifecycle/goal_planning.py')
ATTEMPT6_TEST = Path('skill-system/tests/test_wp08_attempt6_semantic_fidelity_repair.py')
NEW_TEST = Path('skill-system/tests/test_wp08_attempt1_requested_effect_reaudit.py')
BASELINE_PATH = Path('skill-system/registry/product-source-baseline.json')


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding='utf-8')


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def patch_goal_alignment(root: Path) -> None:
    path = root / GOAL_PATH
    text = read(path)
    anchor = '''            normalized_scope_reason = (\n                str(verdict.reason_code or "").strip().casefold().replace("-", "_").replace(" ", "_")\n            )\n            scope_details = verdict.details if isinstance(verdict.details, dict) else {}\n'''
    replacement = '''            normalized_semantic_reason = (\n                str(verdict.reason_code or "").strip().casefold().replace("-", "_").replace(" ", "_")\n            )\n            semantic_details = verdict.details if isinstance(verdict.details, dict) else {}\n            requested_effect_mismatch = (\n                "requested_effect" in normalized_semantic_reason\n                and any(\n                    marker in normalized_semantic_reason\n                    for marker in ("fidelity", "faithful", "business_effect")\n                )\n            )\n            if (\n                blind_dependency_audit\n                and verifier_repair_kind == "candidate_blind_dependency_reaudit"\n                and verdict.verdict == "incomplete"\n                and requested_effect_mismatch\n                and semantic_details.get("dependency_proof_complete") is True\n                and semantic_details.get("dependency_graph_match") is True\n                and bool(verdict.missing_spans)\n                and attempt < 2\n            ):\n                # Candidate-blind requested-effect audit is intentionally strict,\n                # but an open/unsupported effect has no registered capability\n                # identity to copy. Spend the already-budgeted third verifier call\n                # on the semantic mismatch claim itself instead of treating naming\n                # granularity as product evidence. Runtime still never chooses a\n                # capability or rewrites the requested effect.\n                verifier_repair_kind = "candidate_blind_dependency_requested_effect_reaudit"\n                verifier_repair = (\n                    "Re-audit only the previous requested-effect fidelity mismatch claim while preserving the complete "\n                    "candidate-blind dependency proof. requested_effect is an open semantic identity of the customer's "\n                    "user-visible business outcome, not a capability-selection result. Judge domain, operation, object_type "\n                    "and raw_description together against the literal Goal evidence_span. An unsupported/unregistered effect "\n                    "or harmless naming granularity is not itself a mismatch, and capability availability must not be used as "\n                    "evidence. Withdraw the mismatch only when the declared effect still denotes the same user-visible outcome. "\n                    "If it substitutes a different lookup, action, object or business effect, remain incomplete and copy only "\n                    "the smallest literal USER_TEXT span proving that substitution into missing_spans. Do not choose a tool, "\n                    "consult a capability registry, normalize to a nearby registered effect, or rewrite the declaration. Return "\n                    "the full candidate-blind JSON contract, including one dependency_decisions row for every unordered Goal pair."\n                )\n                continue\n            normalized_scope_reason = normalized_semantic_reason\n            scope_details = semantic_details\n'''
    text = replace_once(text, anchor, replacement, label='requested-effect re-audit insertion')
    write(path, text)


def patch_attempt6_negative(root: Path) -> None:
    path = root / ATTEMPT6_TEST
    text = read(path)
    old = '''    blind = _response({\n        "verdict": "incomplete",\n        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],\n        "missing_spans": ["快递员手机号"],\n        "dependency_decisions": [\n            {"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}\n        ],\n        "reason_code": "requested_effect_not_faithful_to_business_effect",\n    })\n    with patch("agent_core.config.get_model", return_value=object()), patch(\n        "agent_core.model_calls.invoke_model", side_effect=[first, blind]\n    ) as invoke:\n'''
    new = '''    blind = _response({\n        "verdict": "incomplete",\n        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],\n        "missing_spans": ["快递员手机号"],\n        "dependency_decisions": [\n            {"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}\n        ],\n        "reason_code": "requested_effect_not_faithful_to_business_effect",\n    })\n    confirmed = _response({\n        "verdict": "incomplete",\n        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],\n        "missing_spans": ["快递员手机号"],\n        "dependency_decisions": [\n            {"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}\n        ],\n        "reason_code": "requested_effect_fidelity",\n    })\n    with patch("agent_core.config.get_model", return_value=object()), patch(\n        "agent_core.model_calls.invoke_model", side_effect=[first, blind, confirmed]\n    ) as invoke:\n'''
    text = replace_once(text, old, new, label='attempt6 negative verifier sequence')
    text = replace_once(
        text,
        '    assert invoke.call_count == 2\n    assert verdict.verdict == "incomplete"\n    assert verdict.missing_spans == ("快递员手机号",)\n    assert verdict.reason_code == "requested_effect_not_faithful_to_business_effect"\n',
        '    assert invoke.call_count == 3\n    assert verdict.verdict == "incomplete"\n    assert verdict.missing_spans == ("快递员手机号",)\n    assert verdict.reason_code == "requested_effect_fidelity"\n    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_requested_effect_reaudit"\n',
        label='attempt6 negative assertions',
    )
    write(path, text)


def add_tests(root: Path) -> None:
    path = root / NEW_TEST
    if path.exists():
        raise SystemExit(f'test path already exists: {NEW_TEST}')
    path.write_text(r'''from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services" / "agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for value in (AGENT_ROOT, AGENT_SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))


def _response(payload: dict):
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(goal_id: str, span: str, effect: dict) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {**effect, "raw_description": span},
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": [],
    }


def _pair():
    return [{"goal_a_id": "g1", "goal_b_id": "g2", "relation": "independent"}]


def test_open_unsupported_effect_false_positive_gets_bounded_reaudit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下鼠标物流，再告诉我快递员手机号"
    goals = [
        _goal(
            "g1",
            "查一下鼠标物流",
            {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        ),
        _goal(
            "g2",
            "再告诉我快递员手机号",
            {"domain": "delivery", "operation": "get_courier_phone", "object_type": "courier"},
        ),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    false_effect_claim = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": ["快递员手机号"],
        "dependency_decisions": _pair(),
        "reason_code": "requested-effect fidelity",
    })
    corrected = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_decisions": _pair(),
        "reason_code": "open_requested_effect_preserves_user_visible_outcome",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, false_effect_claim, corrected],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.evidence_spans == ("查一下鼠标物流", "再告诉我快递员手机号")
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_requested_effect_reaudit"
    repair_message = invoke.call_args_list[2].kwargs["payload"][-1].content
    assert "open semantic identity" in repair_message
    assert "capability availability must not be used as evidence" in repair_message
    assert "remain incomplete" in repair_message


def test_real_effect_substitution_remains_fail_closed_after_reaudit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下鼠标物流，再告诉我快递员手机号"
    goals = [
        _goal(
            "g1",
            "查一下鼠标物流",
            {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        ),
        _goal(
            "g2",
            "再告诉我快递员手机号",
            {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        ),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    mismatch = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": ["快递员手机号"],
        "dependency_decisions": _pair(),
        "reason_code": "requested_effect_not_faithful_to_business_effect",
    })
    confirmed = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标物流", "再告诉我快递员手机号"],
        "missing_spans": ["快递员手机号"],
        "dependency_decisions": _pair(),
        "reason_code": "requested_effect_fidelity",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, mismatch, confirmed],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.missing_spans == ("快递员手机号",)
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_requested_effect_reaudit"


def test_unrelated_incomplete_claim_does_not_gain_requested_effect_reaudit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下还在路上的订单"
    goals = [
        _goal(
            "g1",
            text,
            {"domain": "order", "operation": "list", "object_type": "order"},
        )
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    scope = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["还在路上"],
        "dependency_decisions": [],
        "reason_code": "target-scope-constraint coverage",
    })
    confirmed_scope = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["还在路上"],
        "dependency_decisions": [],
        "reason_code": "target-scope-constraint coverage",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, scope, confirmed_scope],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_scope_constraint_reaudit"


def test_requested_effect_reaudit_policy_is_domain_neutral() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = source.index('verifier_repair_kind = "candidate_blind_dependency_requested_effect_reaudit"')
    end = source.index('normalized_scope_reason = normalized_semantic_reason', start)
    policy = source[start:end]
    assert "open semantic identity" in policy
    assert "capability availability must not be used as evidence" in policy
    assert "consult a capability registry" in policy
    for forbidden in ("快递员", "手机号", "鼠标", "物流"):
        assert forbidden not in policy
''', encoding='utf-8')


def regenerate_baseline(root: Path, product_sha: str) -> None:
    path = root / BASELINE_PATH
    payload = json.loads(read(path))
    files = payload.get('files')
    if not isinstance(files, dict) or not files:
        raise SystemExit('invalid protected source baseline')
    tracked = subprocess.check_output(
        ['git', 'ls-files', 'services', 'web', 'contracts'],
        cwd=root,
        text=True,
    ).splitlines()
    tracked_set = {row.strip() for row in tracked if row.strip()}
    baseline_set = set(files)
    if tracked_set != baseline_set:
        missing = sorted(tracked_set - baseline_set)
        stale = sorted(baseline_set - tracked_set)
        raise SystemExit(
            'protected source set drift: '
            f'missing_from_baseline={missing[:20]} stale_in_baseline={stale[:20]}'
        )
    refreshed: dict[str, str] = {}
    for relative in sorted(baseline_set):
        file_path = root / relative
        if not file_path.is_file():
            raise SystemExit(f'protected source missing: {relative}')
        refreshed[relative] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    payload['files'] = refreshed
    payload['file_count'] = len(refreshed)
    payload['generated_at'] = datetime.now(timezone.utc).isoformat()
    payload['generated_from'] = f'git:{product_sha}'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def patch(root: Path) -> None:
    patch_goal_alignment(root)
    patch_attempt6_negative(root)
    add_tests(root)
    for relative in (GOAL_PATH, ATTEMPT6_TEST, NEW_TEST):
        py_compile.compile(str(root / relative), doraise=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('patch')
    p.add_argument('--workspace', required=True)
    b = sub.add_parser('baseline')
    b.add_argument('--workspace', required=True)
    b.add_argument('--product-sha', required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.command == 'patch':
        patch(root)
    else:
        regenerate_baseline(root, str(args.product_sha))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
